"""Local desktop update transaction. Never exposed through the HTTP gateway.

The Electron controller authenticates the release manifest and passes a verified
archive. Preparation rechecks its digest before extracting into a sibling of the
installed app. A copied runtime survives replacement and can restore the backup.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

APP = 'Codex Mobile Bridge'
APP_ID = 'io.github.try2love.codexmobilebridge'
RESULT = 'desktop-update-result.json'
PENDING_TRANSACTION_TIMEOUT = 15*60
STOP_RETRY_SECONDS = 8
DATA_FORMAT = 'bridge-data-v1'
# Only this source family has been reviewed against the pre-format stable app.
RETURN_STABLE_SOURCE = re.compile(r'2\.2\.2-preview\.\d+')
CONFIG_FILES = ('config.json', 'desktop.json', 'auth-sessions.json', 'connection-vault.json',
                'notifications.json', 'notification-watches.json', 'notification-policies.json',
                'network-adaptation.json', 'desktop-sessions/settings.json', 'desktop-sessions/settings.user.json',
                'client-instances/entries.json', 'codex-entries/entries.json', 'cli-access/index.json',
                'account-drafts.json')
ACCOUNT_FILES = ('index.json', 'switch.json', 'shared-daemon-stop.json', 'reset-dynamic.json',
                 'reminders.json', 'global-reset-watch.json')


def version_key(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?', value) if isinstance(value, str) else None
    if not match:
        raise ValueError('更新版本格式无效。')
    pre = match[4]
    return (*map(int, match.group(1, 2, 3)), pre is None,
            tuple((0, int(part)) if part.isdigit() else (1, part) for part in (pre or '').split('.')))


def validate_transition(target, payload):
    transition = payload.get('transition')
    if transition is None:
        return None  # Existing, upgrade-only controllers keep their wire contract.
    if (not isinstance(transition, dict) or set(transition) != {'intent', 'from', 'to', 'dataFormat'}
            or transition['dataFormat'] != DATA_FORMAT or transition['to'] != payload.get('version')):
        raise ValueError('此版本的数据兼容性尚未核验，已取消安装。')
    installed = read_record(locations(Path(target))[2] / 'update-version.json')
    if not installed or installed.get('version') != transition['from']:
        raise ValueError('当前安装版本已变化，请重新检查更新。')
    switching = transition['intent'] == 'switch-version'
    default_format = None if switching and installed['version'] != '2.2.1' else DATA_FORMAT
    source_format = installed.get('dataFormat', default_format)
    if source_format != DATA_FORMAT:
        raise ValueError('当前资料格式尚未核验，已取消安装。')
    before, after = version_key(transition['from']), version_key(transition['to'])
    if switching:
        return transition  # The selected archive must independently prove its data format in prepare().
    if after < before:
        if (transition['intent'] != 'return-stable' or transition['to'] != '2.2.1'
                or not RETURN_STABLE_SOURCE.fullmatch(transition['from'])):
            raise ValueError('此回退版本尚未核验资料兼容性。')
    elif (after <= before or transition['intent'] not in ('update', 'opt-in-preview', 'return-stable')
          or transition['intent'] == 'return-stable' and '-' in transition['to']):
        raise ValueError('更新方向与已确认的操作不一致。')
    return transition


def configuration_files(data):
    """Only Bridge settings and saved credentials; never walk native chat stores."""
    roots = [data]
    for folder in ('client-instances', 'codex-entries'):
        parent = data / folder
        if parent.is_symlink():
            raise ValueError('资料登记目录不能使用符号链接。')
        if parent.is_dir():
            roots.extend(child for child in parent.iterdir() if child.is_dir())
    for root in roots:
        if root.is_symlink():
            raise ValueError('资料登记目录不能使用符号链接。')
        yield from (root / name for name in CONFIG_FILES)
        accounts = root / 'accounts'
        if accounts.is_symlink():
            raise ValueError('账号目录不能使用符号链接。')
        yield from (accounts / name for name in ACCOUNT_FILES)
        yield accounts / 'rollback.json'
        if accounts.is_dir():
            for folder in accounts.iterdir():
                if re.fullmatch(r'[0-9a-f]{32}', folder.name) and folder.is_dir():
                    yield from (folder / name for name in ('auth.json', 'api.json', 'config.toml'))
        for family in ('claude', 'deepseek', 'zcode', 'antigravity'):
            directory = root / 'desktop-sessions' / ('antigravity/accounts' if family == 'antigravity' else family + '-accounts')
            if directory.is_symlink():
                raise ValueError('账号目录不能使用符号链接。')
            if directory.is_dir():
                yield from directory.glob('*.json')
                if family == 'claude':
                    yield from directory.glob('profiles/*/manifest.json')
                    yield from directory.glob('profiles/*/auth.json')
                    yield from directory.glob('profiles/*/gateway.json')
        yield from (root / 'cli-access').glob('claude/*/api.json')


def configuration_snapshot(data, *, rollback=False):
    files, total = {}, 0
    for path in configuration_files(data):
        relative = path.relative_to(data)
        try:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if any(data.joinpath(*relative.parts[:index]).is_symlink()
                   for index in range(1, len(relative.parts) + 1)):
                raise ValueError('更新备份的配置路径不能使用符号链接。')
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16*1024**2 or len(files) >= 10000:
                raise ValueError('更新配置备份超出限制。')
            raw = path.read_bytes()
        except PermissionError:
            if relative.parts[-2:] in (('desktop-sessions', 'settings.json'),
                                        ('desktop-sessions', 'settings.user.json')):
                continue
            raise
        total += len(raw)
        if total > 64*1024**2:
            raise ValueError('更新配置备份超出限制。')
        try:
            value = json.loads(raw.decode('utf-8-sig')) if path.suffix == '.json' else None
        except (UnicodeError, ValueError):
            raise ValueError('现有配置无法读取，已取消安装；请先检查本机配置。') from None
        if path.suffix == '.json' and not isinstance(value, (dict, list)):
            raise ValueError('现有配置格式无法识别，已取消安装。')
        if rollback and path.name in ('config.json', 'desktop.json', 'settings.json') and not isinstance(value, dict):
            raise ValueError('现有配置格式不兼容，已取消回退。')
        if rollback and path.name == 'config.json':
            auth = value.get('auth')
            if (not isinstance(auth, dict) or auth.get('mode') not in ('none', 'password')
                    or auth['mode'] == 'password' and (not isinstance(auth.get('username'), str)
                        or not isinstance(auth.get('salt'), str) or not isinstance(auth.get('hash'), str)
                        or type(auth.get('iterations')) is not int)):
                raise ValueError('网关认证格式不兼容，已取消回退。')
        if rollback and path.name == 'entries.json' and (not isinstance(value, dict)
                or value.get('version') != 1 or not isinstance(value.get('entries'), list)):
            raise ValueError('接入登记格式不兼容，已取消回退。')
        if rollback and path.name == 'entries.json' and path.parent.name == 'client-instances':
            from bridge.features.clients.instances import Instances
            Instances(path.parent.parent)._load()  # Existing structural reader; no native profile activation.
        if rollback and path.name == 'entries.json' and path.parent.name == 'codex-entries':
            from bridge.features.endpoints.registry import entry_id, local_entry_id
            try:
                for row in value['entries']:
                    expected = (local_entry_id(row['home']) if row.get('kind') == 'codex-local'
                                else entry_id(row['distribution'], row['user'], row['home']))
                    if row.get('id') != expected:
                        raise ValueError()
            except (AttributeError, KeyError, TypeError, ValueError):
                raise ValueError('Codex 接入登记格式不兼容，已取消回退。') from None
        if rollback and path.name == 'switch.json' and (not isinstance(value, dict)
                or value.get('phase') not in ('idle', 'complete', 'restored')):
            raise ValueError('账号操作尚未完成，请恢复原接入后再回退。')
        if rollback and path.name == 'rollback.json':
            raise ValueError('账号恢复尚未完成，请恢复原接入后再回退。')
        if rollback and path.name == 'index.json' and path.parent.name == 'accounts' and 'desktop-sessions' not in relative.parts:
            if (not isinstance(value, dict) or not isinstance(value.get('accounts'), list)
                    or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                           or not re.fullmatch(r'[0-9a-f]{32}', row['id'])
                           or row.get('kind') not in ('api', 'chatgpt') for row in value['accounts'])):
                raise ValueError('账号登记格式不兼容，已取消回退。')
        if rollback and path.name == 'auth-sessions.json':
            if (not isinstance(value, dict) or not isinstance(value.get('identity'), str)
                    or not isinstance(value.get('cookieName'), str) or not isinstance(value.get('policy'), dict)
                    or not isinstance(value.get('sessions'), dict)
                    or any(not isinstance(row, dict) or type(row.get('expires')) not in (int, float)
                           for row in value['sessions'].values())):
                raise ValueError('设备授权格式不兼容，已取消回退。')
            from bridge.features.auth.auth import access_policy
            try:
                access_policy(value['policy'])
            except (TypeError, ValueError):
                raise ValueError('设备授权规则不兼容，已取消回退。') from None
        files[path.relative_to(data).as_posix()] = raw
    return files


def restore_legacy_client_settings(data, transition):
    """Make client settings readable by stable builds that predate the sidecar."""
    if transition.get('intent') not in ('return-stable', 'switch-version') or transition.get('to') != '2.2.1':
        return False
    directory = data/'desktop-sessions'
    primary, fallback = directory/'settings.json', directory/'settings.user.json'
    if directory.is_symlink() or primary.is_symlink() or fallback.is_symlink():
        raise ValueError('无法返回稳定版：客户端设置路径不能使用符号链接。')
    try:
        raw = fallback.read_bytes()
    except FileNotFoundError:
        try:
            with primary.open('rb'):
                return False
        except FileNotFoundError:
            return False
        except PermissionError:
            raise ValueError('无法返回稳定版：客户端设置不可读，且没有可供旧版使用的恢复副本。') from None
    except PermissionError:
        raise ValueError('无法返回稳定版：新版客户端设置副本不可读。') from None
    try:
        value = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        raise ValueError('无法返回稳定版：新版客户端设置副本格式无效。') from None
    if not isinstance(value, dict):
        raise ValueError('无法返回稳定版：新版客户端设置副本格式无效。')
    try:
        if os.name == 'nt':
            from bridge.platforms.windows.local_service import write_private
            write_private(primary, json.dumps(value, ensure_ascii=False).encode('utf-8'))
        else:
            descriptor, name = tempfile.mkstemp(prefix='.' + primary.name + '.', suffix='.tmp', dir=directory)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, primary)
            finally:
                temporary.unlink(missing_ok=True)
        fallback.unlink()
    except OSError as error:
        raise ValueError('无法返回稳定版：客户端设置无法恢复到旧版路径。') from error
    return True


def private_directory(path):
    if os.name == 'nt':
        from bridge.platforms.windows.local_service import directory
        return directory(path, create=True)  # Protected owner-only Windows DACL.
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError('更新备份目录不属于当前用户。')
    path.chmod(0o700)
    return path


def backup_configuration(data_dir, payload):
    """Copy current live data; this does not establish a manual target's compatibility."""
    data = Path(data_dir).resolve()
    transition = validate_transition(payload['target'], payload)
    if transition is None:
        raise ValueError('备份缺少已确认的版本操作。')
    files = configuration_snapshot(data, rollback=transition['intent'] in ('return-stable', 'switch-version'))
    parent = private_directory(data / 'update-backups')
    backup = private_directory(parent / uuid.uuid4().hex)
    try:
        records = {}
        for name, raw in files.items():
            destination = backup / name
            for folder in reversed(destination.parent.relative_to(backup).parents):
                private_directory(backup / folder)
            private_directory(destination.parent)
            with destination.open('xb') as stream:
                os.chmod(destination, 0o600)
                stream.write(raw)
            records[name] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        write_json(backup / 'backup.json', {'schema': 1, 'transition': transition, 'files': records})
    except Exception:
        shutil.rmtree(backup)
        raise
    # A manual legacy installer never reaches apply(). Restore its settings only
    # after the complete backup, and retain that backup if compatibility fails.
    if payload.get('manualInstall') is True and transition['to'] == '2.2.1':
        restore_legacy_client_settings(data, transition)
    return {'backup': str(backup), 'files': len(files)}


def write_json(path, value):
    if os.name == 'nt':
        from bridge.platforms.windows.local_service import write_private
        write_private(path, json.dumps(value, ensure_ascii=False).encode('utf-8'))
        return
    temporary = Path(str(path) + '.tmp')
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_record(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def record_update(data_dir, result, target=None):
    """Keep a durable diagnosis line after the transaction is cleaned."""
    value = {'at': time.time(), 'pid': os.getpid(), 'target': target, **result}
    with (Path(data_dir) / 'desktop-update.log').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')


def child_log(transaction, limit=8000):
    path = Path(transaction) / 'new-app.log'
    try:
        data = path.read_bytes()
    except OSError:
        return ''
    text = data[-limit:].decode('utf-8', errors='replace').strip()
    return text


def process_exists(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        # kill(pid, 0) also succeeds for an exited process awaiting reaping.
        # Such a zombie holds no application files and must not block handoff.
        try:
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                                   capture_output=True, text=True, timeout=2)
            if state.returncode == 0 and state.stdout.strip().startswith('Z'):
                return False
        except (OSError, subprocess.TimeoutExpired):
            pass  # Failure to inspect state is not proof that the process exited.
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
    finally:
        kernel.CloseHandle(handle)


def transactions_for(target):
    """Only inspect transactions belonging to this installation."""
    target = Path(target).resolve()
    for transaction in Path(target).parent.glob('.cmb-update-*'):
        if not transaction.is_dir() or transaction.is_symlink():
            continue
        plan = read_record(transaction / 'plan.json')
        if not plan or not isinstance(plan.get('target'), str):
            continue
        if Path(plan['target']).resolve() == target:
            yield transaction


def clean_transactions(target):
    """Remove confirmed finished transactions; preserve unknown recovery state."""
    for transaction in transactions_for(target):
        result = read_record(transaction / 'result.json')
        if not result:
            continue
        if result.get('state') == 'updated' or (result.get('state') == 'failed' and result.get('recovered') is True):
            shutil.rmtree(transaction, ignore_errors=True)


def reject_pending_transaction(target):
    """Two helpers must never swap the same installation concurrently."""
    for transaction in transactions_for(target):
        if read_record(transaction / 'result.json'):
            continue
        # A dead helper or an old timestamp does not prove a swap was recovered.
        # Keep the backup and copied helper after crashes or interrupted writes.
        if any((transaction / name).exists() for name in ('ready.json', 'previous', 'failed')):
            raise ValueError('上一次更新的恢复状态尚未确认，已保留备份；请检查更新日志并恢复后再试。')
        try:
            age = time.time() - transaction.stat().st_mtime
        except OSError:
            continue
        if age > PENDING_TRANSACTION_TIMEOUT:
            shutil.rmtree(transaction, ignore_errors=True)
            continue
        helper = read_record(transaction / 'helper.json')
        if helper and isinstance(helper.get('pid'), int):
            if process_exists(helper['pid']):
                raise ValueError('上一次更新事务尚未完成；请等待几分钟后再试。')
            shutil.rmtree(transaction, ignore_errors=True)
            continue
        raise ValueError('上一次更新事务尚未完成；请等待几分钟或重启电脑后再试。')


def extract(archive, destination):
    """Validate all entries first; links are created after regular files."""
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 50000 or sum(e.file_size for e in entries) > 4*1024**3:
            raise ValueError('更新包解压大小超出限制。')
        seen, links = set(), {}
        for entry in entries:
            # ZipInfo normalizes Windows separators and truncates NULs. Validate
            # the wire name as well, before that normalization can hide it.
            if entry.orig_filename != entry.filename:
                raise ValueError('更新包包含无效路径。')
            name = entry.filename.rstrip('/')
            parts = PurePosixPath(name).parts
            if (not name or '\\' in name or ':' in name or name.startswith('/')
                    or any(p in ('.', '..') for p in name.split('/'))):
                raise ValueError('更新包包含无效路径。')
            # Both supported filesystems commonly compare names case-insensitively.
            key = name.casefold()
            if key in seen:
                raise ValueError('更新包包含重复路径。')
            seen.add(key)
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                raise ValueError('更新包包含不支持的文件。')
            if stat.S_ISLNK(mode):
                if sys.platform == 'win32' or entry.file_size > 4096:
                    raise ValueError('更新包包含无效链接。')
                target = source.read(entry).decode('utf-8')
                if not target or '\\' in target or Path(target).is_absolute():
                    raise ValueError('更新包链接指向安装目录外。')
                resolved = (destination / name).parent.joinpath(target).resolve()
                if destination.resolve() not in resolved.parents:
                    raise ValueError('更新包链接指向安装目录外。')
                links[name] = target
        link_keys = {name.casefold() for name in links}
        for entry in entries:
            name = entry.filename.rstrip('/')
            if any(str(parent).casefold() in link_keys for parent in PurePosixPath(name).parents):
                raise ValueError('更新包通过链接写入文件。')
        for entry in entries:
            name = entry.filename.rstrip('/')
            if name in links:
                continue
            target = destination / name
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as src, open(target, 'xb') as dst:
                    shutil.copyfileobj(src, dst)
                mode = (entry.external_attr >> 16) & 0o777
                os.chmod(target, mode or 0o644)
        for name, target in links.items():
            link = destination / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
        # Detect chained links escaping the bundle as well.
        for name in links:
            if destination.resolve() not in (destination / name).resolve().parents:
                raise ValueError('更新包链接指向安装目录外。')


def locations(target):
    if sys.platform == 'darwin':
        return (target / 'Contents/MacOS' / APP,
                target / 'Contents/Resources/gateway/codex-mobile-gateway',
                target / 'Contents/Resources')
    if sys.platform == 'linux':
        return target / 'codex-mobile-bridge-desktop', target / 'resources/gateway/codex-mobile-gateway', target / 'resources'
    return target / (APP + '.exe'), target / 'resources/gateway/codex-mobile-gateway.exe', target / 'resources'


def preflight(target):
    if sys.platform == 'win32':
        from bridge.platforms.windows.updates import require_update_ready
        require_update_ready(locations(Path(target))[1])
    return {'ready': True}


def prepare(data_dir, payload):
    from bridge.app.desktop import Desktop
    target = Path(payload['target']).absolute()
    data = Path(data_dir).resolve()
    if sys.platform not in ('darwin', 'win32'):
        raise ValueError('此系统暂无应用内更新包。')
    if not target.is_dir() or target.is_symlink() or target.resolve() != target:
        raise ValueError('请将应用安装到可写目录后再更新。')
    if target == data or target in data.parents:
        raise ValueError('请先将网关数据目录移到应用安装目录之外。')
    old_app, old_worker, _ = locations(target)
    if not old_app.is_file() or not old_worker.is_file():
        raise ValueError('未找到完整的应用安装目录。')
    preflight(target)
    transition = validate_transition(target, payload)
    if transition:
        configuration_snapshot(data, rollback=transition['intent'] in ('return-stable', 'switch-version'))
    digest = hashlib.sha256()
    with open(payload['archive'], 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != payload['sha256']:
        raise ValueError('更新包校验失败，已取消安装。')
    reject_pending_transaction(target)
    clean_transactions(target)
    transaction = Path(tempfile.mkdtemp(prefix='.cmb-update-', dir=target.parent))
    os.chmod(transaction, 0o700)
    try:
        # Record ownership before preparation, including for interrupted builds.
        write_json(transaction / 'plan.json', {'target': str(target)})
        stage = transaction / 'unpacked'
        stage.mkdir()
        extract(Path(payload['archive']), stage)
        staged = stage / (APP + '.app') if sys.platform == 'darwin' else stage
        new_app, new_worker, resources = locations(staged)
        metadata = json.loads((resources / 'update-version.json').read_text(encoding='utf-8'))
        expected = {'version': payload['version'], 'platform': payload['platform'], 'arch': payload['arch']}
        default_format = (None if transition and transition['intent'] == 'switch-version'
                          and payload['version'] != '2.2.1' else DATA_FORMAT)
        if (any(metadata.get(key) != value for key, value in expected.items())
                or set(metadata) - {*expected, 'dataFormat'} or metadata.get('dataFormat', default_format) != DATA_FORMAT):
            raise ValueError('更新包版本或平台不匹配。')
        if not new_app.is_file() or not new_worker.is_file():
            raise ValueError('更新包缺少应用程序。')
        if sys.platform == 'darwin':
            with open(staged / 'Contents/Info.plist', 'rb') as stream:
                info = plistlib.load(stream)
            if info.get('CFBundleIdentifier') != APP_ID or info.get('CFBundleShortVersionString') != payload['version']:
                raise ValueError('更新包的应用标识或版本不匹配。')
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(staged)], check=True, capture_output=True, timeout=60)
        else:
            # The ZIP is also used by NSIS installations. Preserve the registered
            # uninstaller and shortcuts; neither contains gateway configuration.
            for uninstaller in [*target.glob('Uninstall *.exe'), target / 'uninstallerIcon.ico']:
                if uninstaller.is_file():
                    shutil.copy2(uninstaller, staged / uninstaller.name)
        subprocess.run([str(new_worker), '--help'], check=True, capture_output=True, timeout=20,
                       env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
        helper = transaction / 'helper'
        shutil.copytree(old_worker.parent, helper, symlinks=True)
        runtime = Desktop(data_dir).status()
        if runtime['portOccupied']:
            raise ValueError('端口被其他网关占用，请先检查运行配置。')
        plan = {'target': str(target), 'staged': str(staged), 'backup': str(transaction / 'previous'),
                'dataDir': str(data), 'version': payload['version'], 'parentPid': payload['parentPid'],
                'token': payload['token'], 'runtime': runtime}
        if transition:
            plan['transition'] = transition
        write_json(transaction / 'plan.json', plan)
        return {'plan': str(transaction / 'plan.json'), 'helper': str(helper / old_worker.name)}
    except Exception:
        shutil.rmtree(transaction)
        raise


def wait_parent(pid, timeout=45):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return
        try:
            if kernel.WaitForSingleObject(handle, int(timeout*1000)) != 0:
                raise TimeoutError('应用尚未退出，更新已取消。')
        finally:
            kernel.CloseHandle(handle)
    else:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not process_exists(pid):
                return
            time.sleep(.2)
        raise TimeoutError('应用尚未退出，更新已取消。')


def launch_app(target, transaction, plan, acknowledge=True):
    executable, _, _ = locations(target)
    env = {**os.environ, 'CMB_UPDATE_DATA_DIR': plan['dataDir'], 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
    for key in ('ELECTRON_RUN_AS_NODE', 'CMB_UPDATE_TRANSACTION', 'CMB_UPDATE_TOKEN', 'CMB_UPDATE_LAUNCHER_PID'):
        env.pop(key, None)
    if acknowledge:
        env.update(CMB_UPDATE_TRANSACTION=str(transaction), CMB_UPDATE_TOKEN=plan['token'])
    with (transaction / 'new-app.log').open('ab') as log:
        child = subprocess.Popen([str(executable)], env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 **({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}))
    write_json(transaction / 'launched.json', {'pid': child.pid})
    return child


def start_gateway(target, data_dir, desktop):
    _, worker, _ = locations(target)
    try:
        command = subprocess.run([str(worker), 'start', '--data-dir', data_dir], capture_output=True,
                                 timeout=35, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
    except subprocess.TimeoutExpired as error:
        detail = (error.stderr or b'').decode('utf-8', errors='replace').strip() or 'control process timed out'
        raise RuntimeError('更新后的网关启动失败：' + detail) from error
    try:
        response = json.loads(command.stdout.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        response = {}
    if command.returncode or not response.get('ok'):
        detail = response.get('error') or command.stderr.decode('utf-8', errors='replace').strip() or (
            command.stdout.decode('utf-8', errors='replace').strip() or f'exit {command.returncode}')
        raise RuntimeError('更新后的网关启动失败：' + detail)
    end = time.monotonic() + 40
    while time.monotonic() < end:
        if desktop.status()['running']:
            return
        time.sleep(.3)
    raise TimeoutError('更新后的网关未就绪。')


def check_app(child, transaction, plan):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if child.poll() is not None:
            logs = child_log(transaction)
            raise RuntimeError(f'更新后的应用未能启动（exit {child.poll()}）：' + (logs or '没有应用输出'))
        try:
            ack = json.loads((transaction / 'ack.json').read_text(encoding='utf-8'))
            if ack == {'token': plan['token'], 'version': plan['version'], 'dataDir': plan['dataDir']}:
                return
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    logs = child_log(transaction)
    raise TimeoutError('更新后的应用未就绪：' + (logs or '没有应用输出，也未写入确认'))


def registry_version(target, version):
    if os.name != 'nt':
        return
    import winreg
    # Only update an existing per-user registration for this exact installation.
    base = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                with winreg.OpenKey(root, winreg.EnumKey(root, index), 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                    try:
                        uninstall = winreg.QueryValueEx(key, 'UninstallString')[0]
                        match = re.match(r'^"([^"]+)"', uninstall)
                        name = winreg.QueryValueEx(key, 'DisplayName')[0]
                        if name == APP and match and Path(match[1]).parent.resolve() == target.resolve():
                            winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, version)
                    except OSError:
                        continue
    except OSError:
        pass  # Portable installations have no registration.


def move_app(source, destination):
    # Windows may release Electron child-process DLL handles slightly after the
    # controller exits. Retry only sharing/access failures, with a bounded wait.
    end = time.monotonic() + 10
    while True:
        try:
            source.rename(destination)
            return
        except PermissionError:
            if os.name != 'nt' or time.monotonic() >= end:
                raise
            time.sleep(.2)


def apply(plan_file, *, desktop=None, wait=wait_parent, launch=launch_app, health=check_app, start=start_gateway):
    from bridge.app.desktop import Desktop
    transaction = Path(plan_file).parent
    plan = json.loads(Path(plan_file).read_text(encoding='utf-8'))
    target, staged, backup = (Path(plan[k]) for k in ('target', 'staged', 'backup'))
    desktop = desktop or Desktop(plan['dataDir'])
    swapped = False
    stopped = False
    child = None
    write_json(transaction / 'ready.json', {'token': plan['token']})
    try:
        wait(plan['parentPid'])
        preflight(target)
        # A one-second local health probe can miss just after wake or during a
        # brief event-loop stall. Retry only ambiguous shutdown states; foreign
        # gateway instances still fail immediately.
        deadline = time.monotonic() + 5
        while True:
            state = desktop.status()
            if state.get('portOccupied'):
                raise RuntimeError('网关状态发生变化，更新已取消。')
            expected_id = plan['runtime'].get('instanceId')
            actual_id = state.get('instanceId')
            if expected_id and actual_id and actual_id != expected_id:
                raise RuntimeError('网关状态发生变化，更新已取消。')
            if state.get('running') == plan['runtime'].get('running'):
                if not plan['runtime']['running'] or actual_id == expected_id:
                    break
            if time.monotonic() >= deadline:
                raise RuntimeError('网关状态发生变化，更新已取消。')
            time.sleep(.2)
        if plan['runtime']['running']:
            # Stop can return while the listener is finishing its final socket
            # cleanup, and a single health probe can therefore be ambiguous.
            # Retry against the recorded instance; never terminate a foreign PID.
            stop_error = None
            verified_pid = state.get('pid') or plan['runtime'].get('pid')
            acknowledged = False
            stop_deadline = time.monotonic() + STOP_RETRY_SECONDS
            while True:
                try:
                    desktop.stop()
                    acknowledged = True
                except Exception as error:
                    stop_error = error
                state = desktop.status()
                if state.get('portOccupied') or (state.get('instanceId') and state['instanceId'] != expected_id):
                    raise RuntimeError('网关状态发生变化，更新已取消。')
                if acknowledged or time.monotonic() >= stop_deadline:
                    break
                time.sleep(.2)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = desktop.status()
                if state.get('portOccupied') or (state.get('instanceId') and state['instanceId'] != expected_id):
                    raise RuntimeError('网关状态发生变化，更新已取消。')
                if acknowledged and not state.get('running') and (not verified_pid or not process_exists(verified_pid)):
                    break
                time.sleep(.2)
            else:
                detail = str(stop_error) if stop_error else '实例仍在运行'
                raise RuntimeError('网关尚未停止，更新已取消：' + detail)
            stopped = True
            if os.name == 'nt' and verified_pid:
                wait_parent(verified_pid, timeout=20)
        preflight(target)
        if plan.get('transition'):
            # Take the final copy after the old controller and managed gateway
            # have stopped. It is independent of replaceable app transactions.
            plan['configurationBackup'] = backup_configuration(plan['dataDir'], plan)['backup']
            restore_legacy_client_settings(Path(plan['dataDir']).resolve(), plan['transition'])
            write_json(Path(plan_file), plan)
        move_app(target, backup)
        try:
            move_app(staged, target)
        except Exception:
            move_app(backup, target)
            raise
        swapped = True
        child = launch(target, transaction, plan)
        health(child, transaction, plan)
        if plan['runtime']['running']:
            start(target, plan['dataDir'], desktop)
        registry_version(target, plan['version'])
        result = {'state': 'updated', 'version': plan['version'], 'backup': str(backup)}
    except Exception as error:
        recovery_error = None
        try:
            if swapped:
                if desktop.status()['running']:
                    desktop.stop()
                if child and child.poll() is None:
                    child.terminate()
                    child.wait(timeout=15)
                move_app(target, transaction / 'failed')
                move_app(backup, target)
            if stopped:
                start(target, plan['dataDir'], desktop)
            # A wait timeout means the original controller is still running.
            if not isinstance(error, TimeoutError) or stopped or swapped:
                launch(target, transaction, plan, acknowledge=False)
        except Exception as recovery:
            recovery_error = str(recovery)
        result = {'state': 'failed', 'version': plan['version'], 'message': str(error),
                  'recovered': recovery_error is None, 'backup': str(backup)}
        if recovery_error:
            result['recoveryError'] = recovery_error
    if plan.get('configurationBackup'):
        result['configurationBackup'] = plan['configurationBackup']
    record_update(plan['dataDir'], result, plan.get('target'))
    write_json(Path(plan['dataDir']) / RESULT, result)
    write_json(transaction / 'result.json', result)
    return result
