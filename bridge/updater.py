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
import zipfile

APP = 'Codex Mobile Bridge'
APP_ID = 'io.github.try2love.codexmobilebridge'
RESULT = 'desktop-update-result.json'
PENDING_TRANSACTION_TIMEOUT = 15*60
STOP_RETRY_SECONDS = 8


def write_json(path, value):
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
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
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
    return target / (APP + '.exe'), target / 'resources/gateway/codex-mobile-gateway.exe', target / 'resources'


def prepare(data_dir, payload):
    from .desktop import Desktop
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
        if metadata != {'version': payload['version'], 'platform': payload['platform'], 'arch': payload['arch']}:
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
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.2)
        raise TimeoutError('应用尚未退出，更新已取消。')


def launch_app(target, transaction, plan, acknowledge=True):
    executable, _, _ = locations(target)
    env = {**os.environ, 'CMB_UPDATE_DATA_DIR': plan['dataDir'], 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
    for key in ('ELECTRON_RUN_AS_NODE', 'CMB_UPDATE_TRANSACTION', 'CMB_UPDATE_TOKEN'):
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
    from .desktop import Desktop
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
    record_update(plan['dataDir'], result, plan.get('target'))
    write_json(Path(plan['dataDir']) / RESULT, result)
    write_json(transaction / 'result.json', result)
    return result
