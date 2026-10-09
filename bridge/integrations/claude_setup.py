"""Claude discovery and visible native Console bootstrap, independent of CDP."""
import json
import os
import plistlib
import re
import struct
import subprocess
import sys
import time
from pathlib import Path
from contextlib import nullcontext

from ..desktop_app import DesktopApp, process_inventory
from .. import windows_session


def _archive_members(path, names=('package.json', '.vite/build/index.pre.js')):
    """Read only the bounded Electron package metadata and main startup script."""
    with path.open('rb') as stream:
        header = stream.read(16)
        if len(header) != 16:
            raise ValueError('incomplete archive header')
        _, size, _, json_size = struct.unpack('<4I', header)
        if not 0 < json_size <= 8 * 1024 * 1024 or size < json_size + 8:
            raise ValueError('invalid archive header')
        tree = json.loads(stream.read(json_size))
        base = size + 8
        for name in names:
            node = tree
            for part in name.split('/'):
                node = node.get('files', {}).get(part, {})
            length = node.get('size', 0)
            if ('offset' not in node or node.get('unpacked') or
                    not isinstance(length, int) or not 0 < length <= 32 * 1024 * 1024):
                continue
            offset = int(node['offset'])
            if offset < 0:
                raise ValueError('invalid archive offset')
            stream.seek(base + offset)
            yield name, stream.read(length).decode('utf-8', errors='replace')


def inspect_installation(executable):
    """Return capability evidence without launching Claude or reading accounts."""
    if not executable:
        return {'installed': False, 'automaticConnection': 'unavailable',
                'setupState': 'not-installed', 'reason': '未检测到 Claude Desktop'}
    path = Path(executable).expanduser()
    bundle = next((p for p in (path, *path.parents) if p.suffix == '.app'), None)
    version = ''
    if bundle:
        try:
            info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
            name = info['CFBundleExecutable']
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError('invalid executable')
            path = bundle/'Contents/MacOS'/name
            version = str(info.get('CFBundleShortVersionString') or '')
        except (OSError, ValueError, KeyError):
            pass
    if not path.is_file():
        return {'installed': False, 'automaticConnection': 'unavailable',
                'setupState': 'not-installed', 'reason': '未检测到 Claude Desktop'}
    archive = (bundle/'Contents/Resources/app.asar' if bundle
               else path.parent/'resources/app.asar')
    try:
        for name, content in _archive_members(archive):
            if name == 'package.json' and not version:
                version = str(json.loads(content).get('version') or '')
    except (OSError, ValueError, TypeError, AttributeError, struct.error):
        # Unknown packaging is unknown capability, never assumed compatible.
        pass
    return {'installed': True, 'executable': str(path.resolve()), 'version': version,
            'automaticConnection': 'native-console' if sys.platform in ('darwin', 'win32') else 'unavailable',
            'setupState': 'ready-to-connect' if sys.platform in ('darwin', 'win32') else 'unsupported',
            'reason': ('已发现 Claude Desktop，可以自动连接' if sys.platform in ('darwin', 'win32')
                       else '此系统暂未提供 Claude 自动连接组件')}


def helper_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]/'dist'))
    return root/'client-helpers'/('claude-bridge-helper.exe' if sys.platform == 'win32' else 'claude-bridge-helper')


def _running_profiles(executable, platform):
    """Locate the selected app's active Chromium profile, without reading it.

    Claude can select Claude-3p internally, so installation paths and another
    profile's developer settings do not establish its active data directory.
    """
    if platform == 'win32':
        selected = Path(executable).expanduser().resolve()
        state = process_inventory([selected]).get(selected, {})
        profiles = set()
        # Electron's children report their actual user-data-dir even when the
        # main process chose a Store-redirected or third-party profile itself.
        argument = re.compile(r'(?:^|\s)(?:"--user-data-dir=([^"\r\n]+)"|'
                              r'--user-data-dir(?:=|\s+)(?:"([^"\r\n]+)"|([^\s"]+)))', re.I)
        for command in state.get('commands', {}).values():
            for match in argument.finditer(command):
                path = Path(next(value for value in match.groups() if value is not None))
                if path.is_absolute():
                    profiles.add(path.resolve())
        return profiles
    if platform != 'darwin':
        return set()
    app = DesktopApp(executable, Path.home())
    pids = app.processes()
    if not pids:
        return set()
    result = subprocess.run(['/usr/sbin/lsof', '-a', '-p', ','.join(map(str, pids)), '-Fn'],
                            capture_output=True, text=True, check=True, timeout=10)
    profiles = set()
    markers = ('Local Storage/leveldb/LOCK', 'Session Storage/LOCK',
               'Network/Cookies', 'Network/Network Persistent State',
               'Cookies', 'Network Persistent State')
    for line in result.stdout.splitlines():
        if not line.startswith('n/'):
            continue
        path = Path(line[1:])
        for marker in markers:
            parts = Path(marker).parts
            if path.parts[-len(parts):] == parts:
                profiles.add(path.parents[len(parts) - 1].resolve())
                break
    return profiles


def windows_claude_profile_paths(executable, fallback, *, env=None, package_family=None):
    """Known profiles for this user and selected installation, never arbitrary homes.

    Missing canonical paths are retained for account operations, but a custom
    fallback is not paired with another user's or installation's configuration.
    """
    env = os.environ if env is None else env
    if not env.get('APPDATA') or not env.get('LOCALAPPDATA'):
        return {}
    roaming, local = Path(env['APPDATA']).resolve(), Path(env['LOCALAPPDATA']).resolve()
    family = package_family if re.fullmatch(r'Claude_[a-z0-9]+', package_family or '', re.I) else None
    if not family and executable:
        for folder in Path(executable).resolve().parents:
            match = re.fullmatch(r'Claude_\d+(?:\.\d+){3}_(?:x64|x86|arm64|neutral)_[^_]*_([a-z0-9]+)', folder.name, re.I)
            if folder.parent.name.casefold() == 'windowsapps' and match:
                family = 'Claude_' + match[1]
                break
    official = [local/'Claude-Data']
    if family:
        official.append(local/'Packages'/family/'LocalCache/Roaming/Claude')
    official.append(roaming/'Claude')
    thirdparty = [local/'Claude-3p', roaming/'Claude-3p']
    if Path(fallback).resolve() not in (*official, *thirdparty):
        return {}
    return {'official': official, 'thirdparty': thirdparty}


def _windows_stopped_profile(executable, fallback, env, package_family):
    groups = windows_claude_profile_paths(executable, fallback, env=env, package_family=package_family)
    if not groups:
        return fallback
    candidates = groups['official'] + groups['thirdparty']
    modes = set()
    for path in candidates:
        try:
            # Only use a bounded mode selector. Never return credentials or
            # configuration values, and never create or modify native profiles.
            with (path/'claude_desktop_config.json').open('rb') as stream:
                content = stream.read(64 * 1024 + 1)
            if len(content) <= 64 * 1024:
                value = json.loads(content).get('deploymentMode')
                if value in ('1p', '3p'):
                    modes.add(value)
        except (OSError, ValueError, AttributeError):
            pass
    if len(modes) > 1:
        return fallback
    if modes:
        candidates = groups['thirdparty' if '3p' in modes else 'official']
    markers = ('Local State', 'Preferences', 'Network/Cookies', 'Cookies',
               'config.json', 'claude_desktop_config.json', 'developer_settings.json')
    populated = [path for path in candidates if any((path/marker).is_file() for marker in markers)]
    return populated[0] if len(populated) == 1 else fallback


def claude_data_home(executable, fallback, platform=None, *, env=None, package_family=None, inspect_running=True):
    """Prefer unique live evidence, then a single supported Windows profile."""
    platform, fallback = platform or sys.platform, Path(fallback)
    if inspect_running:
        try:
            profiles = _running_profiles(executable, platform)
            if profiles:
                return next(iter(profiles)) if len(profiles) == 1 else fallback
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if platform == 'win32':
        return _windows_stopped_profile(executable, fallback, env, package_family)
    return fallback


def developer_mode_enabled(data_home):
    try:
        return json.loads((Path(data_home)/'developer_settings.json').read_text(encoding='utf-8-sig')).get('allowDevTools') is True
    except (OSError, ValueError, AttributeError):
        return False


def console_source(source, config):
    """Normalize our controlled, semicolon-terminated IIFE before adding data.

    Only standalone comment lines are removed. The connector has no template
    literals or inline line comments; parity tests run this exact transformation.
    Configuration is inserted afterwards so path/string whitespace is untouched.
    """
    lines, block = [], False
    for line in source.splitlines():
        line = line.strip()
        if line.startswith('/*'):
            block = True
        if block:
            if '*/' in line:
                block = False
            continue
        if not line or line.startswith('//'):
            continue
        if '`' in line:
            raise ValueError('连接脚本格式不支持，请重新构建应用')
        lines.append(line)
    script = '/* codex bridge connector */' + ' '.join(lines)
    return script.replace('__BRIDGE_CONFIG__', json.dumps(config, ensure_ascii=True))


def native_action(action, *, pid=None, executable=None, script=None, cancel_path=None, cancelled=None):
    if action not in ('check', 'request-permission', 'connect', 'connect-background', 'close-devtools', 'close-background-devtools', 'enable-devtools', 'inspect-error', 'quit'):
        raise ValueError('不支持的 Claude 本机操作')
    background = action == 'connect-background'
    if background and sys.platform != 'win32':
        raise ValueError('此系统尚未验证 Claude 后台初始化')
    def connection_result(reason, dispatched=False, state='needs-retry'):
        return {'setupState': state, 'submission': 'uncertain' if dispatched else 'none',
                'pid': pid, 'reason': reason}
    def connect_dispatched(output):
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        if not isinstance(output, str):
            return False
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (isinstance(value, dict) and value.get('connectPhase') == 'dispatching'
                    and type(value.get('pid')) is int and value['pid'] == pid):
                return True
        return False
    def quit_failed(reason):
        return {'quitState': 'failed', 'reason': reason, 'pid': pid}
    def quit_dispatched(output):
        if sys.platform != 'win32':
            return False
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        if not isinstance(output, str):
            return False
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (isinstance(value, dict) and value.get('quitPhase') == 'dispatching'
                    and type(value.get('pid')) is int and value['pid'] == pid):
                return True
        return False
    def quit_interrupted(reason, dispatched):
        if dispatched:
            return {'quitState': 'submitted', 'pid': pid,
                    'reason': 'Claude 退出命令回执中断，正在等待保存和退出完成'}
        return quit_failed(reason)
    if cancelled and cancelled.is_set():
        if action == 'quit':
            return quit_failed('已取消 Claude 退出')
        if background:
            return connection_result('已取消 Claude 连接', state='cancelled')
        return {'setupState': 'cancelled', 'reason': '已取消 Claude 连接'}
    def desktop_blocked():
        if sys.platform == 'win32' and action not in ('quit', 'connect-background', 'close-background-devtools'):
            try:
                windows_session.require_interactive()
            except windows_session.DesktopUnavailable as exc:
                return {'setupState': exc.setup_state, 'reason': str(exc)}
        return None
    blocked = desktop_blocked()
    if blocked:
        return blocked
    helper = helper_path()
    if not helper.is_file():
        if action == 'quit':
            return quit_failed('缺少 Claude 原生退出组件，请重新构建或安装网关 App')
        if background:
            return connection_result('缺少 Claude 后台连接组件，请重新构建或安装网关 App', state='failed')
        return {'setupState': 'failed', 'reason': '缺少 Claude 自动连接组件，请重新构建或安装网关 App'}
    if sys.platform == 'win32':
        if action in ('check', 'request-permission'):
            return {'setupState': 'ready'}
        argument = '--connect-background='+str(script) if background else str(script) if action == 'connect' else '--'+action
        args = [str(helper), str(pid), argument, str(executable)]
        if cancel_path:
            args.append(str(cancel_path))
    else:
        args = [str(helper), '--'+action]
        if pid is not None:
            args += [str(pid), str(executable)]
        if action == 'connect':
            args += [str(script), str(cancel_path)]
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **options)
    except OSError:
        if action == 'quit':
            return quit_failed('无法启动 Claude 原生退出组件，请检查网关安装')
        if background:
            return connection_result('无法启动 Claude 后台连接组件，请检查网关安装', state='failed')
        raise
    timeout = (30 if sys.platform == 'win32' else 180) if action in ('connect', 'connect-background') else 30 if action == 'quit' else 20
    if sys.platform == 'win32' and action in ('inspect-error', 'close-devtools'):
        timeout = 8
    if sys.platform == 'win32' and action == 'close-background-devtools':
        timeout = 12
    deadline = time.monotonic() + timeout
    dispatched = False
    while True:
        try:
            stdout, _ = process.communicate(timeout=.2)
            break
        except subprocess.TimeoutExpired as expired:
            if action == 'quit':
                dispatched = dispatched or quit_dispatched(expired.output)
            elif background:
                dispatched = dispatched or connect_dispatched(expired.output)
            blocked = desktop_blocked()
            if (cancelled and cancelled.is_set()) or blocked or time.monotonic() >= deadline:
                # Stop only our input helper, never the Claude desktop process.
                process.terminate()
                try: stdout, _ = process.communicate(timeout=3)
                except subprocess.TimeoutExpired as expired:
                    if action == 'quit':
                        dispatched = dispatched or quit_dispatched(expired.output)
                    elif background:
                        dispatched = dispatched or connect_dispatched(expired.output)
                    process.kill(); stdout, _ = process.communicate()
                if action == 'quit':
                    return quit_interrupted('已取消 Claude 退出' if cancelled and cancelled.is_set()
                                            else 'Claude 原生退出请求超时，请在电脑端检查退出状态',
                                            dispatched or quit_dispatched(stdout))
                if background:
                    return connection_result('已取消 Claude 连接' if cancelled and cancelled.is_set()
                                             else 'Claude 后台初始化回执超时，请等待连接状态或重试',
                                             dispatched or connect_dispatched(stdout),
                                             'cancelled' if cancelled and cancelled.is_set() else 'needs-retry')
                if blocked and not (cancelled and cancelled.is_set()):
                    return blocked
                return {'setupState': 'cancelled' if cancelled and cancelled.is_set() else 'needs-retry',
                        'reason': '已取消 Claude 连接' if cancelled and cancelled.is_set() else 'Claude 自动连接超时，请重试'}
    if background:
        dispatched = dispatched or connect_dispatched(stdout)
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
            states = ('submitted', 'needs-initialization', 'needs-developer-mode', 'needs-trust',
                      'needs-unlock', 'needs-desktop', 'needs-retry', 'failed', 'cancelled')
            if (not isinstance(value, dict) or value.get('setupState') not in states
                    or value.get('submission') not in ('none', 'submitted', 'uncertain')
                    or type(value.get('pid')) is not int or value['pid'] != pid
                    or not isinstance(value.get('reason'), str)
                    or value['setupState'] == 'submitted' and value['submission'] != 'submitted'):
                raise ValueError('invalid background connection result')
            if (dispatched and value['submission'] == 'none'
                    or process.returncode and value['submission'] != 'none'):
                return connection_result(value['reason'][:300], True,
                                         'cancelled' if value['setupState'] == 'cancelled' else 'needs-retry')
            return {key: value[key] for key in ('setupState', 'submission', 'pid')} | {'reason': value['reason'][:300]}
        except (ValueError, IndexError, AttributeError):
            # A malformed receipt cannot establish that no input was dispatched.
            return connection_result('Claude 后台初始化未返回有效回执，请等待实际连接状态', True)
    if action == 'quit':
        dispatched = dispatched or quit_dispatched(stdout)
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
            if (not isinstance(value, dict) or value.get('quitState') not in ('exited', 'pending', 'submitted', 'failed')
                    or type(value.get('pid')) is not int or value['pid'] != pid or not isinstance(value.get('reason'), str)
                    or (process.returncode and value['quitState'] != 'failed')):
                raise ValueError('invalid native quit result')
            if value['quitState'] == 'failed' and dispatched:
                return quit_interrupted(value['reason'], dispatched)
            return {'quitState': value['quitState'], 'pid': pid, 'reason': value['reason'][:300]}
        except (ValueError, IndexError, AttributeError):
            return quit_interrupted('Claude 原生退出组件未返回有效状态，请在电脑端检查退出状态', dispatched)
    if sys.platform != 'win32':
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
            return value
        except (ValueError, IndexError, AttributeError):
            return {'setupState': 'failed', 'reason': 'Claude 连接组件未返回有效状态'}
    if process.returncode:
        message = stdout.strip().splitlines()[-1] if stdout.strip() else 'Claude 自动连接失败'
        state = ('cancelled' if '已取消' in message else 'needs-unlock' if '锁定' in message
                 else 'needs-desktop' if '桌面不可用' in message else 'needs-retry')
        return {'setupState': state, 'reason': message[:300]}
    if action == 'inspect-error':
        return {'setupState': 'needs-trust' if 'NEEDS_TRUST' in stdout else 'failed',
                'reason': '请在 Claude Code 中确认连接目录信任，然后重新连接'}
    return {'setupState': 'needs-developer-mode' if action == 'enable-devtools' else 'submitted',
            'reason': '请确认 Claude 的开发者模式提示，完成后会继续连接' if action == 'enable-devtools'
                      else '连接脚本已输入，正在等待 Claude 确认'}


def _main_pids(app):
    if sys.platform != 'win32':
        return app.processes()
    # Electron uses the same executable for renderer/GPU/utility children on
    # Windows. Only its process without --type is the native desktop owner.
    script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $s=(Get-Process -Id $PID).SessionId; '
              'Get-CimInstance Win32_Process | Where-Object {$_.SessionId -eq $s -and $_.ExecutablePath} | '
              'Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    rows = json.loads(result.stdout or '[]')
    if isinstance(rows, dict): rows = [rows]
    return [int(row['ProcessId']) for row in rows
            if str(Path(row['ExecutablePath']).resolve()).casefold() == str(app.executable).casefold()
            and not re.search(r'(?:^|\s)--type(?:=|\s)', row.get('CommandLine') or '')]


def _native_startup_hidden(source):
    """Recognize the verified native --startup predicate and hidden-window branch."""
    symbol = r'[A-Za-z_$][\w$]*'
    predicate = rf'({symbol})=\(\)=>{symbol}\?\?=![^;]{{1,80}}\.argv\.includes\("--startup"\)'
    for match in re.finditer(predicate, source):
        branch = source[match.end():match.end() + 1800]
        visible = re.search(rf'({symbol})=\({re.escape(match[1])}\(\)\|\|!1\)&&!{symbol};', branch)
        if (visible and '"background_launch":"os_login"' in branch
                and re.search(rf'show:{re.escape(visible[1])}&&!', branch)):
            return True
    return False


def background_start_supported(executable):
    """Unknown Claude builds must not fall back to a foreground launch."""
    archive = Path(executable).parent/'resources/app.asar'
    try:
        entry = dict(_archive_members(archive, ('package.json', '.vite/build/index.js', '.vite/build/index.pre.js')))
        if json.loads(entry.get('package.json', '{}')).get('name') != '@ant/desktop':
            return False
        sources = [entry.get('.vite/build/index.pre.js', '')]
        chunks = re.findall(r'require\("\./(index\.chunk-[A-Za-z0-9_-]+\.js)"\)', entry.get('.vite/build/index.js', ''))
        if len(chunks) > 8:
            return False
        sources.extend(source for _, source in _archive_members(archive, ['.vite/build/' + name for name in chunks]))
        return any(_native_startup_hidden(source) for source in sources)
    except (OSError, ValueError, TypeError, AttributeError, struct.error):
        return False


def _native_background_console(source):
    """Require the native environment entry and its restricted-mode guard."""
    symbol = r'[A-Za-z_$][\w$]*'
    for match in re.finditer(r'env\.CLAUDE_DEV_TOOLS&&(' + symbol + r')\(\(\(\)=>!0\)\)', source):
        gate = re.escape(match[1])
        guard = re.search(r'function ' + gate + r'\((' + symbol + r')\)\{return \1\(\)&&!(' + symbol + r')\(\)\}', source)
        if not guard:
            continue
        restricted = re.search(r'function ' + re.escape(guard[2]) + r'\(\)\{return ' + symbol + r'\(\)==="restricted"\}', source)
        branch = source[match.end():match.end()+500]
        if (restricted and '"undocked"' in branch and '.includes(' in branch
                and 'env.CLAUDE_DEV_TOOLS' in branch and '.webContents.openDevTools({mode:' in branch):
            return True
    return False


def background_console_supported(executable):
    archive = Path(executable).parent/'resources/app.asar'
    try:
        entry = dict(_archive_members(archive, ('package.json', '.vite/build/index.js', '.vite/build/index.pre.js')))
        if json.loads(entry.get('package.json', '{}')).get('name') != '@ant/desktop':
            return False
        chunks = re.findall(r'require\("\./(index\.chunk-[A-Za-z0-9_-]+\.js)"\)', entry.get('.vite/build/index.js', ''))
        if len(chunks) > 8:
            return False
        sources = [entry.get('.vite/build/index.pre.js', '')]
        sources.extend(source for _, source in _archive_members(archive, ['.vite/build/' + name for name in chunks]))
        return any(_native_background_console(source) for source in sources)
    except (OSError, ValueError, TypeError, AttributeError, struct.error):
        return False


def background_running_app(executable, data_home, cancelled=None, launch_gate=None, *, initialize_console=False):
    """Start a verified Windows Claude in its native hidden mode at most once."""
    if sys.platform != 'win32':
        raise ValueError('此系统尚未验证 Claude 后台启动入口')
    def check():
        if cancelled is not None and cancelled.is_set():
            raise ValueError('已取消 Claude 后台启动')
    check()
    app = DesktopApp(executable, data_home)
    pids = _main_pids(app)
    check()
    if len(pids) > 1:
        raise ValueError('无法确认唯一的 Claude 主进程，请在电脑端检查')
    if pids:
        return {'pid': pids[0], 'launched': False}
    if not background_start_supported(app.executable):
        raise ValueError('此 Claude 版本尚未验证后台启动，请在电脑端打开应用后重试')
    packaged = any(folder.name.casefold() == 'windowsapps' or (folder/'AppxManifest.xml').is_file()
                   for folder in app.executable.parents)
    if packaged:
        from .windows_discovery import application_execution_alias
        launcher = application_execution_alias(app.executable)
        if launcher is None:
            raise ValueError('无法确认所选 Claude 的后台启动别名，请在电脑端打开应用后重试')
    else:
        launcher = app.executable
    # No explorer fallback: it would activate the window and drop --startup.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in ('ELECTRON_RUN_AS_NODE', 'CLAUDE_DEV_TOOLS')}
    # This native switch still respects the application's restricted-mode gate.
    # Require the selected profile's prior developer choice; never edit it here.
    if initialize_console and developer_mode_enabled(data_home) and background_console_supported(app.executable):
        environment['CLAUDE_DEV_TOOLS'] = 'undocked'
    with launch_gate if launch_gate is not None else nullcontext():
        check()
        # Discovery can be slow. Never reactivate an app opened in the meantime.
        pids = _main_pids(app)
        check()
        if len(pids) > 1:
            raise ValueError('无法确认唯一的 Claude 主进程，请在电脑端检查')
        if pids:
            return {'pid': pids[0], 'launched': False}
        process = subprocess.Popen([str(launcher), '--startup'], cwd=app.executable.parent,
                                   env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 20
    while True:
        check()
        pids = _main_pids(app)
        if len(pids) == 1:
            return {'pid': pids[0], 'launched': True}
        if len(pids) > 1:
            raise ValueError('Claude 启动后出现多个主进程，请在电脑端检查')
        if process.poll() not in (None, 0) or time.monotonic() >= deadline:
            raise ValueError('Claude 后台启动未完成，请检查桌面应用后重试')
        if cancelled is None:
            time.sleep(.25)
        elif cancelled.wait(.25):
            check()


def running_app(executable, data_home, restart=False, cancelled=None):
    if cancelled and cancelled.is_set():
        raise ValueError('已取消 Claude 连接')
    windows_session.require_interactive()
    app = DesktopApp(executable, data_home)
    pids = _main_pids(app)
    if cancelled and cancelled.is_set():
        raise ValueError('已取消 Claude 连接')
    if pids and restart:
        windows_session.require_interactive()
        if len(pids) != 1:
            raise ValueError('无法确认唯一的 Claude 主进程，请检查桌面窗口')
        if sys.platform == 'win32':
            script = '$p=Get-Process -Id '+str(pids[0])+' -ErrorAction Stop; if (-not $p.CloseMainWindow()) {exit 2}'
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 25
            while _main_pids(app):
                if cancelled and cancelled.is_set(): raise ValueError('已取消 Claude 连接')
                if time.monotonic() >= deadline:
                    raise ValueError('Claude 尚未退出，请在电脑上关闭后重试；尚未强制结束进程')
                time.sleep(.25)
        else:
            app.stop()
        pids = []
    if not pids:
        if cancelled and cancelled.is_set():
            raise ValueError('已取消 Claude 连接')
        windows_session.require_interactive()
        if sys.platform == 'darwin':
            bundle = next((p for p in app.executable.parents if p.suffix == '.app'), None)
            if bundle is None:
                raise ValueError('Claude 桌面程序必须位于应用程序包中')
            subprocess.run(['/usr/bin/open', '-a', str(bundle)], check=True, timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Native startup arguments only. Claude's user profile remains its own.
            app.launch(cancelled=cancelled, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while not pids and time.monotonic() < deadline:
            if cancelled and cancelled.is_set():
                raise ValueError('已取消 Claude 连接')
            time.sleep(.25); pids = _main_pids(app)
    if len(pids) != 1:
        raise ValueError('无法确认唯一的 Claude 主进程，请检查桌面窗口')
    return pids[0]
