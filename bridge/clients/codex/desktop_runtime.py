"""Opt-in native Desktop runtime sharing; never take over an existing writer.

The original App launches the CLI adapter and owns its lifetime. A Bridge only
opens a separate connection to that same runtime for explicit metadata writes.
"""
import json
import os
import shlex
import stat
import sys
import hashlib
import subprocess
import time
import uuid
import shutil
from contextlib import contextmanager
from pathlib import Path

from bridge.clients.codex.app_server import CodexAppServer, AppServerUnavailable
from bridge.resources import project_root


VERSION = 1


def directory(home):
    root = Path(home).resolve() / 'bridge-desktop-runtime'
    return root / 'records' if sys.platform == 'win32' else root


def private_directory(root):
    if sys.platform == 'win32':
        from bridge.platforms.windows import local_service
        local_service.directory(root.parent, create=True)
        return local_service.directory(root, create=True)
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('共享接入目录权限无法确认')
    return root


def socket_path(home, generation):
    uuid.UUID(generation)
    # Darwin limits Unix socket paths to 104 bytes. Independent test homes and
    # non-ASCII home paths can exceed that before the filename is even added.
    digest = hashlib.sha256(str(Path(home).resolve()).encode()).hexdigest()[:16]
    root = Path('/tmp') / f'cmb-codex-{os.getuid()}-{digest}'
    return root / (generation + '.sock')


def _daemon_directory():
    # Matches Codex's private UDS root; TMPDIR and CODEX_HOME do not select it.
    return Path('/tmp').resolve() / f'codex-daemon-{os.geteuid()}'


def verified_socket(endpoint):
    """Verify either a private socket or Codex's exact protected-socket alias."""
    endpoint = Path(endpoint)
    parent = endpoint.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or
            stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError('Shared runtime socket directory is not private')
    advertised = endpoint.lstat()
    if advertised.st_uid != os.geteuid():
        raise ValueError('Shared runtime socket owner changed')
    target = endpoint
    if stat.S_ISLNK(advertised.st_mode):
        # Canonicalize only the parent, not the final symlink. The native CLI
        # hashes these OS path bytes and creates the alias itself.
        digest = hashlib.sha256(os.fsencode(endpoint.parent.resolve() / endpoint.name)).hexdigest()
        root = _daemon_directory()
        target = root / digest
        if os.readlink(endpoint) != str(target):
            raise ValueError('Shared runtime socket alias changed')
        protected = root.lstat()
        if (not stat.S_ISDIR(protected.st_mode) or protected.st_uid != os.geteuid() or
                stat.S_IMODE(protected.st_mode) != 0o700):
            raise ValueError('Native runtime socket directory is not private')
    info = target.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError('Shared runtime socket is not private')
    return (str(target), info.st_dev, info.st_ino, advertised.st_dev, advertised.st_ino)


class DesktopRuntimeBusy(ValueError):
    """Another verified lifecycle operation still owns this runtime lock."""


class DesktopRuntimeOutdated(ValueError):
    """Valid shared settings refer to a replaced App or gateway launcher."""


@contextmanager
def exclusive_lock(home, name, *, timeout=0):
    if sys.platform == 'win32':
        from bridge.platforms.windows.codex_daemon_env import _operation_lock
        root = private_directory(directory(home))
        deadline = time.monotonic() + timeout
        while True:
            with _operation_lock(root / (name + '.lock'), create=True) as acquired:
                if acquired:
                    yield
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DesktopRuntimeBusy('共享接入仍在使用或退出中，请稍后重试')
            time.sleep(min(.25, remaining))
    import fcntl
    root = private_directory(directory(home))
    fd = os.open(root / (name + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('共享接入锁身份无法确认')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DesktopRuntimeBusy('共享接入仍在使用或退出中，请稍后重试') from exc
                time.sleep(min(.25, remaining))
        yield
    finally:
        os.close(fd)


def initialization_lock(home):
    return exclusive_lock(home, 'initialize')


def _read(path):
    if sys.platform == 'win32':
        from bridge.platforms.windows.local_service import read_record
        return read_record(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o077 or info.st_size > 65536):
        raise ValueError('共享接入文件身份无法确认')
    return json.loads(path.read_text(encoding='utf-8'))


def _write(path, value, mode=0o600):
    if sys.platform == 'win32':
        from bridge.platforms.windows.local_service import write_record
        return write_record(path, json.loads(value))
    # Same-directory atomic publication; never follow a preexisting temporary file.
    import tempfile
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def settings(home):
    path = directory(home) / 'settings.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _read(path)
    if value.get('version') != VERSION or value.get('home') != str(Path(home).resolve()):
        raise ValueError('共享接入配置不匹配，请重新初始化')
    return value


def preparation(home):
    """Caller holds initialization_lock; all gateways share this write receipt."""
    path = directory(home) / 'preparation.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _read(path)
    if (value.get('version') != VERSION or value.get('home') != str(Path(home).resolve())
            or value.get('state') not in ('pending', 'complete') or not isinstance(value.get('requestId'), str)):
        raise ValueError('原生连接准备记录无法核验，请在账号管理中检查')
    return value


def record_preparation(home, request_id, *, complete=False):
    root = private_directory(directory(home))
    _write(root / 'preparation.json', json.dumps({'version': VERSION, 'home': str(Path(home).resolve()),
        'requestId': request_id, 'state': 'complete' if complete else 'pending'}))


def configure(home, application, cli):
    """Called only after the lifecycle owner has verified idle and normal exit."""
    if sys.platform not in ('darwin', 'win32', 'linux'):
        raise ValueError('此系统尚未验证 Codex 共享接入')
    # GUI exit precedes the adapter's cooperative proxy/native shutdown.
    with exclusive_lock(home, 'runtime', timeout=35):
        _configure(home, application, cli)


def wait_stopped(home, *, timeout=35):
    """Wait only during an explicit lifecycle operation; never terminate a process."""
    if not directory(home).exists():
        return
    with exclusive_lock(home, 'runtime', timeout=timeout):
        active = directory(home) / 'active.json'
        if active.exists() or active.is_symlink():
            require_inactive(_read(active))


def _configure(home, application, cli):
    root = private_directory(directory(home))
    active = root / 'active.json'
    if active.exists():
        old = _read(active)
        require_inactive(old)
        active.unlink()
    value = {'version': VERSION, 'home': str(Path(home).resolve()),
             'application': os.path.normcase(str(Path(application).resolve())), 'cli': str(Path(cli).resolve())}
    if sys.platform == 'win32':
        value.update(launcher_settings(application))
        _write(root / 'settings.json', json.dumps(value))
        return
    launcher = root / 'codex-cli'
    command = ([sys.executable] if getattr(sys, 'frozen', False) else
               [sys.executable, str(project_root() / 'desktop.py')])
    command += ['codex-desktop-runtime', '--configuration', str(root / 'settings.json'), '--']
    script = '#!/bin/sh\n'
    if sys.platform == 'linux':
        script += 'if [ ! -x '+shlex.quote(command[0])+' ]; then exec '+shlex.quote(value['cli'])+' "$@"; fi\n'
    _write(launcher, script+'exec ' + shlex.join(command) + ' "$@"\n', 0o700)
    _write(root / 'settings.json', json.dumps(value))


def launcher_settings(application):
    """Check Windows launch prerequisites before quitting the original App."""
    if sys.platform == 'linux':
        from bridge.platforms.linux.codex_launch import supported
        if not supported(application):
            raise ValueError('此 Linux Codex 安装布局尚未核验原生初始化，请继续使用原有接入')
        return {}
    if sys.platform != 'win32':
        return {}
    if not getattr(sys, 'frozen', False):
        raise ValueError('请使用已编译的网关 App 初始化 Windows 原生连接')
    from bridge.platforms.windows.discovery_ext import application_user_model_id, application_execution_alias
    identity = application_user_model_id(application)
    packaged = any((parent / 'AppxManifest.xml').is_file() for parent in Path(application).parents)
    if packaged and not identity:
        raise ValueError('无法核验 Codex 安装包身份；尚未改变原生连接设置')
    if packaged and application_execution_alias(application) is None:
        raise ValueError('无法核验 Codex 应用启动别名；尚未改变原生连接设置')
    return {'launcher': str(Path(sys.executable).resolve()), 'sandboxFamily': identity.split('!')[0]}


def validate_runtime(cli):
    """Read native capabilities before any App exit; no model or daemon starts."""
    try:
        for arguments, required in ((['app-server', '--help'], 'unix://'),
                                    (['app-server', 'proxy', '--help'], '--sock')):
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
            result = subprocess.run([str(cli), *arguments], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=10, check=True, **options)
            if required not in result.stdout:
                raise ValueError('此 Codex 版本不支持共享接入，请先更新原 App')
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法核验 Codex 共享接入能力；原 App 保持不变') from exc


def launch_environment(home, application):
    value = settings(home) if sys.platform in ('darwin', 'win32', 'linux') else None
    if value is None:
        return {}
    if value['application'] != os.path.normcase(str(Path(application).resolve())):
        raise DesktopRuntimeOutdated('Codex 程序已变化，请重新初始化共享接入')
    if sys.platform == 'win32':
        launcher = Path(value['launcher'])
        if not launcher.is_file() or launcher.resolve() != Path(sys.executable).resolve():
            raise DesktopRuntimeOutdated('网关程序已变化，请重新初始化 Codex 原生连接')
        return {'CODEX_CLI_PATH': str(launcher), 'CMB_CODEX_DESKTOP_CONFIGURATION': str(directory(home) / 'settings.json'),
                'CODEX_APP_SERVER_WS_URL': '', 'CODEX_APP_SERVER_FORCE_CLI': '0'}
    launcher = directory(home) / 'codex-cli'
    info = launcher.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('共享接入启动器身份无法确认')
    return {'CODEX_CLI_PATH': str(launcher), 'CODEX_APP_SERVER_WS_URL': '',
            'CODEX_APP_SERVER_FORCE_CLI': '0'}


def ordinary_launch_helper():
    root = project_root()
    if not getattr(sys, 'frozen', False):
        root = root / 'dist'
    return root / 'client-helpers' / 'codex-launcher'


def enable_ordinary_launch(home, application):
    """Prepare normal GUI opening without changing the App or restarting it."""
    if sys.platform == 'linux':
        from bridge.platforms.linux.codex_launch import install
        install(application, home, directory(home)/'codex-cli')
        return True
    if sys.platform != 'darwin':
        return False
    value = settings(home)
    if value is None:
        return False
    if value['application'] != str(Path(application).resolve()):
        raise DesktopRuntimeOutdated('Codex 程序已变化，请重新初始化共享接入')
    from bridge.platforms.macos import codex_launch
    root = private_directory(directory(home))
    helper = ordinary_launch_helper()
    if not helper.is_file():
        raise ValueError('普通启动接入组件尚未编译')
    with exclusive_lock(home, 'ordinary-launch'):
        previous = codex_launch.get_environment()
        gui = private_directory(root / 'gui')
        if previous and not (Path(previous).parent.parent == gui and Path(previous).name == 'codex-launcher'):
            raise ValueError('已有其他 Codex 启动配置，保留原配置；当前连接不受影响')
        digest = hashlib.sha256(helper.read_bytes()).hexdigest()
        generation = private_directory(gui / digest)
        launcher = generation / 'codex-launcher'
        if launcher.is_symlink():
            raise ValueError('普通启动接入组件身份已变化')
        if not launcher.exists():
            shutil.copyfile(helper, launcher, follow_symlinks=False)
            launcher.chmod(0o700)
        elif launcher.is_symlink() or hashlib.sha256(launcher.read_bytes()).hexdigest() != digest:
            raise ValueError('普通启动接入组件身份已变化')
        command = ([sys.executable] if getattr(sys, 'frozen', False) else
                   [sys.executable, str(project_root() / 'desktop.py')])
        command += ['codex-desktop-runtime', '--configuration', str(root / 'settings.json'), '--']
        record = {**value, 'command': command}
        target = root / 'ordinary-launch.json'
        if not target.exists() or _read(target) != record:
            _write(target, json.dumps(record))
        if previous != str(launcher):
            codex_launch.set_environment(launcher)
        codex_launch.install_login_environment(launcher)
        return True


def process_identity(pid):
    # Keep the protocol independent of the platform identity implementation.
    if sys.platform == 'win32':
        from bridge.platforms.windows.zcode_pipe import _identity
        return _identity(pid, parent=True)
    if sys.platform == 'linux':
        from bridge.platforms.linux.codex_runtime import identity
        return identity(pid)
    from bridge.platforms.macos.force_exit import Native
    value = Native().identity(pid)
    if value['uid'] != os.getuid() or value['ruid'] != os.getuid() or value['zombie']:
        raise ValueError('共享运行时进程身份无法确认')
    return {key: (list(item) if isinstance(item, tuple) else item) for key, item in value.items()}


def desktop_owner(parent, application):
    """Bind either the original App or its bundled utility host to that App."""
    if parent['image'] == application:
        if sys.platform not in ('win32', 'linux'):
            return parent, None
        try:
            ancestor = process_identity(parent['parent'])
        except (OSError, ValueError):
            return parent, None  # A verified main App need not retain its launcher.
        if ancestor['image'] != application:
            return parent, None
        if process_identity(parent['pid']) != parent:
            raise ValueError('Desktop host generation changed')
        return ancestor, parent
    if sys.platform != 'darwin':
        raise ValueError('Desktop runtime parent is not the selected App')
    frameworks = Path(application).parent.parent / 'Frameworks'
    if frameworks not in Path(parent['image']).parents:
        raise ValueError('Desktop runtime host is outside the selected App')
    app = process_identity(parent['parent'])
    if app['image'] != application or process_identity(parent['pid']) != parent:
        raise ValueError('Desktop runtime host owner changed')
    return app, parent


def verify_record(record):
    """Recheck every process generation, including the optional utility host."""
    parent = record.get('host', record['app'])
    if (record['adapter']['parent'] != parent['pid'] or
            record['runtime']['parent'] != record['adapter']['pid']):
        raise ValueError('Shared runtime process ancestry changed')
    if 'host' in record and desktop_owner(record['host'], record['app']['image']) != (record['app'], record['host']):
        raise ValueError('Shared runtime host changed')
    for kind in ('app', 'host', 'adapter', 'runtime'):
        if kind in record and process_identity(record[kind]['pid']) != record[kind]:
            raise ValueError('Shared runtime generation changed')


def runtime_alive(identity):
    if sys.platform == 'win32':
        from bridge.platforms.windows.codex_daemon_env import recorded_process_absent
        # A terminated Windows process can retain a PID/handle while image
        # queries already fail. The recorded birth and wait state prove exit.
        if recorded_process_absent(identity['pid'], {'processStartTime': str(identity['created'])}):
            return False
    try:
        current = process_identity(identity['pid'])
    except ProcessLookupError:
        return False
    # If an adapter exits before native shutdown completes, macOS reparents the
    # child. A changed parent alone never permits starting a second writer.
    keys = ('pid', 'created', 'session', 'image') if sys.platform == 'win32' else ('pid', 'birth', 'started', 'image', 'uid', 'ruid')
    return all(current[key] == identity[key] for key in keys)


def require_inactive(record):
    """A failed launch is not permission to create an untracked second writer."""
    identity = record.get('runtime')
    if not identity:
        raise ValueError('原共享运行时启动结果待核对，请在电脑端检查；不会重复启动')
    if record.get('ready') is True or sys.platform == 'win32':
        alive = runtime_alive(identity)
    else:
        try:
            current = process_identity(identity['pid'])
        except ProcessLookupError:
            alive = False
        else:
            # A native shell launcher may exec its bundled binary before the
            # handshake; the process generation remains the same across exec.
            alive = (current['birth'][0] == identity['birth'][0] and
                     all(current[key] == identity[key]
                         for key in ('pid', 'started', 'uid', 'ruid')))
    if alive:
        raise ValueError('原共享运行时尚未退出，请稍后重新检查')


def connect(home, cli):
    """None means never configured; configured but unavailable must fail closed."""
    try:
        value = settings(home) if sys.platform in ('darwin', 'win32', 'linux') else None
        if value is None:
            return None
        record = _read(directory(home) / 'active.json')
        if (record.get('version') != VERSION or record.get('ready') is not True or
                record.get('home') != value['home'] or
                record.get('cli') != value['cli'] or value['cli'] != str(Path(cli).resolve()) or
                record['app']['image'] != value['application'] or
                record['adapter']['parent'] != record.get('host', record['app'])['pid'] or
                record['runtime']['parent'] != record['adapter']['pid']):
            raise ValueError('Shared runtime owner mismatch')
        verify_record(record)
        if record.get('transport') == 'desktop-stdio':
            from bridge.clients.codex.desktop_host import MetadataClient
            client = MetadataClient(home)
            client.connect()
            return client
        if sys.platform == 'win32':
            from bridge.clients.codex.desktop_runtime_windows import MetadataClient
            client = MetadataClient(directory(home), record)
            client.connect()
            return client
        expected = socket_path(home, record['generation'])
        if record['socket'] != str(expected):
            raise ValueError('Shared runtime socket mismatch')
        endpoint_identity = verified_socket(expected)
        client = CodexAppServer(expected, cli=value['cli'])
        try:
            result = client.connect()
            if Path(result['codexHome']).resolve() != Path(home).resolve():
                raise ValueError('Shared runtime configuration changed')
            for kind in ('app', 'adapter', 'runtime'):
                if process_identity(record[kind]['pid']) != record[kind]:
                    raise ValueError('Shared runtime generation changed during handshake')
            if verified_socket(expected) != endpoint_identity:
                raise ValueError('Shared runtime socket changed during handshake')
            return client
        except Exception:
            client.close()
            raise
    except (OSError, KeyError, TypeError, ValueError, AppServerUnavailable) as exc:
        raise AppServerUnavailable('Codex 原生共享连接尚未就绪，可在账号管理中查看自动准备状态；不会另起运行时重试写入') from exc


def hosted_endpoint(home):
    """An authenticated, live hosted record selects the new control transport."""
    value = settings(home) if sys.platform in ('darwin', 'win32', 'linux') else None
    if value is None:
        return None
    active = directory(home) / 'active.json'
    if not active.exists():
        return None
    record = _read(active)
    if record.get('transport') != 'desktop-stdio':
        return None
    if (record.get('version') != VERSION or record.get('ready') is not True or
            record.get('home') != value['home'] or record.get('cli') != value['cli'] or
            record['app']['image'] != value['application']):
        raise ValueError('原生桌面接入记录尚未就绪或归属已变化')
    verify_record(record)
    expected = (r'\\.\pipe\cmb-codex-desktop-' + record['generation'] if sys.platform == 'win32'
                else str(socket_path(home, record['generation'])))
    if record.get('socket') != expected or not isinstance(record.get('controlToken'), str) or len(record['controlToken']) != 64:
        raise ValueError('原生桌面接入端点身份不匹配')
    identity = verified_socket(expected) if sys.platform != 'win32' else None
    return {'path': expected, 'generation': record['generation'], 'controlToken': record['controlToken'],
            'record': record, 'identity': identity}


def server_arguments(arguments, socket_path):
    """Select only the Desktop stdio server, preserving every native override."""
    args = list(arguments)
    try:
        i = args.index('app-server')
    except ValueError:
        return None
    # Keep global feature/config additions before the unchanged subcommand.
    # One-shot native invocations never consume a Desktop initialize message.
    if any(token in ('--help', '-h', '--version', '-V') for token in args[:i]):
        return None
    result = args[:i + 1]
    i += 1
    while i < len(args):
        token = args[i]
        if token in ('-c', '--config') and i + 1 < len(args):
            result.extend(args[i:i + 2]); i += 2
        elif token in ('--plugin-mcp-launch-cwd', '--mcp-launch-cwd'):
            if i + 3 >= len(args):
                return None
            result.extend(args[i:i + 4]); i += 4
        elif token == '--stdio':
            i += 1
        elif token == '--listen' and i + 1 < len(args) and args[i + 1] == 'stdio://':
            i += 2
        elif token == '--listen=stdio://':
            i += 1
        elif (token == '--listen' or token.startswith('--listen=') or
              token in ('--print-startup-config', '--help', '-h', '--version', '-V',
                        'proxy', 'daemon', 'generate-ts', 'generate-json-schema')):
            return None  # Auxiliary commands and explicit other transports stay native.
        else:
            # Feature additions may add startup flags while keeping the same
            # stdio/initialize contract. Preserve them for the native parser;
            # the App's primary initialize and owner proof still gate sharing.
            result.append(token); i += 1
    return [*result, '--listen', 'unix://' + str(socket_path)]
