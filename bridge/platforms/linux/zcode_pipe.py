"""Identify the packaged ZCode Desktop and pass Chromium its private pipe endpoints."""
import contextlib
from functools import lru_cache
import os
import re
import shlex
from pathlib import Path
import signal
import subprocess
import sys
import time

from bridge.platforms.electron import package_manifest
from bridge.platforms.linux import desktop
from bridge.platforms.linux import appimage
from bridge.platforms.linux.force_exit import Native


@lru_cache(maxsize=8)
def _version(archive, file_identity):
    # ASAR headers can be several MiB. Invalidate on replacement or modification.
    raw = package_manifest(archive)
    if raw.get('name') == '@zcode/desktop' and raw.get('productName') == 'ZCode':
        return str(raw.get('version') or '')
    return None


def _directory_installation(application):
    path = Path(application)
    try:
        if not path.is_absolute():
            return None
        path = path.resolve(strict=True)
        if path.name.casefold() != 'zcode' or not path.is_file() or not os.access(path, os.X_OK):
            return None
        archive = path.parent/'resources'/'app.asar'
        if not archive.is_file() or archive.resolve() != archive:
            return None
        with path.open('rb') as stream:
            header = stream.read(11)
            if header[:4] != b'\x7fELF' or header[8:11] == b'AI\x02':
                return None
        meta = archive.stat()
        version = _version(archive, (meta.st_dev, meta.st_ino, meta.st_size, meta.st_ctime_ns))
        if version is not None:
            return {'application': str(path), 'executable': str(path), 'version': version}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, OverflowError):
        pass
    return None


def _image_metadata(path):
    raw = appimage.metadata(path, 'zcode.desktop')
    if not raw:
        return None
    command = shlex.split(raw.get('exec', ''))
    if (raw.get('type') != 'Application' or raw.get('name', '').casefold() != 'zcode'
            or raw.get('startupwmclass', '').casefold() != 'zcode'
            or not command or command[0] not in ('AppRun', 'zcode')):
        return None
    version = raw.get('x-appimage-version', '')
    if len(version) > 128 or any(ord(char) < 32 for char in version):
        return None
    return {'version': version}


def runtime_candidates(application):
    return appimage.runtime_candidates(application, 'zcode')


def installation(application, *, pid=None):
    path = Path(application)
    try:
        if not path.is_absolute():
            return None
        path = path.resolve(strict=True)
        direct = _directory_installation(path)
        if direct:
            return direct
        if not os.access(path, os.X_OK) or not appimage.is_type2(path):
            return None
        identity = appimage.file_identity(path)
        runtimes = runtime_candidates(path)
        if pid is not None:
            runtimes = [row for row in runtimes if row['pid'] == pid]
        if len(runtimes) > 1:
            return None  # A caller must identify the instance; never take the first.
        if runtimes:
            runtime = runtimes[0]
            info = _directory_installation(runtime['executable'])
            if not info or appimage.file_identity(path) != identity:
                return None
            return {**info, 'application': str(path), 'packageType': 'appimage',
                    'installationIdentity': list(identity), 'runtimePid': runtime['pid'],
                    'runtimeVerified': True, 'coldStartVerified': _image_metadata(path) is not None}
        if pid is not None:
            return None
        metadata = _image_metadata(path)
        if metadata is not None:
            return {'application': str(path), 'executable': str(path), **metadata,
                    'packageType': 'appimage', 'installationIdentity': list(identity),
                    'runtimeVerified': False, 'coldStartVerified': True}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, OverflowError):
        pass
    return None


def runtime_owner(application, pid, *, timeout=15):
    """The launcher's PID may exec into a freshly mounted payload before CDP is ready."""
    application = Path(application).resolve(strict=True)
    deadline = time.monotonic() + timeout
    first = None
    while True:
        owner = identity(pid)
        generation = (owner['uid'], owner['birth'], owner['started'])
        if first is None:first = generation
        if generation != first:raise ValueError('原 ZCode 进程身份已变化')
        info = installation(application, pid=pid)
        if info and owner['image'] == info['executable'] and (
                info.get('packageType') != 'appimage' or info.get('runtimeVerified')):
            return owner
        if time.monotonic() >= deadline:
            raise ValueError('ZCode AppImage 运行身份尚未核验，请稍后重试连接')
        time.sleep(.1)


def is_supported(application):
    return (sys.platform == 'linux' and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')
            and installation(application) is not None)


def identity(pid):
    data = Native().identity(pid)
    if data['zombie'] or data['uid'] != os.getuid():
        raise ProcessLookupError('ZCode 进程已退出或用户已变化')
    return {'pid': pid, 'uid': data['uid'], 'image': data['image'], 'birth': data['birth'],
            'started': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def worker_title(command):
    if re.fullmatch(r'zcode-host-local-[0-9]+', command):
        return 'host'
    return {'zcode-cli': 'cli', 'zcode-node-repl-mcp': 'repl'}.get(command)


def main_processes(executable, candidates, commands):
    # All native workers replace argv. A title alone never establishes ownership.
    try:
        if appimage.is_type2(executable):
            # This is a cold launcher's path, not an authenticated GUI runtime.
            # A remaining FUSE keeper must never be mistaken for the App window.
            return [], bool(candidates)
    except (OSError, ValueError):
        pass  # Existing callers separately verify the selected executable.
    roles = {pid: role for pid in candidates if (role := worker_title(commands.get(pid, '')))}
    if not roles:
        return candidates, False
    main = [pid for pid in candidates if pid not in roles]
    if len(main) != 1:
        return main, True
    try:
        native = Native()
        identities = {pid: native.identity(pid) for pid in candidates}
        if any(item['zombie'] or item['uid'] != os.getuid() or item['image'] != str(executable)
               for item in identities.values()):
            return main, True
        for pid in roles:
            cursor, seen = pid, set()
            while cursor in roles:
                if cursor in seen:
                    return main, True
                seen.add(cursor)
                role = roles[cursor]
                cursor = identities[cursor]['parent']
                if role == 'host':
                    if cursor != main[0]:
                        return main, True
                    break
                if roles.get(cursor) not in (('cli',) if role == 'repl' else ('host', 'cli')):
                    return main, True
        return main, any(native.identity(pid) != item for pid, item in identities.items())
    except (OSError, ValueError):
        return main, True


def _launch_context(application):
    found = installation(application)
    if not found:
        raise ValueError('未找到所选的 ZCode 桌面安装')
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise ValueError('未检测到 Linux 图形会话，请在已登录的桌面中启动网关')
    exe = found['application']
    runtime_images = {exe, found['executable']}
    if desktop.processes(lambda image: str(image) in runtime_images):
        raise ValueError('ZCode 已在运行，请先在应用管理中核验并正常退出')
    env = os.environ.copy()
    env.pop('ELECTRON_RUN_AS_NODE', None)
    # A gateway may itself run from an AppImage. Its runtime identity or an
    # inherited target override must not redirect the selected Agent launch.
    for key in ('APPIMAGE', 'APPDIR', 'ARGV0', 'TARGET_APPIMAGE'):
        env.pop(key, None)
    if getattr(sys, 'frozen', False):
        # PyInstaller prepends its private runtime libraries to the gateway.
        previous = env.pop('LD_LIBRARY_PATH_ORIG', None)
        if previous:
            env['LD_LIBRARY_PATH'] = previous
        else:
            env.pop('LD_LIBRARY_PATH', None)
    return exe, env


def launch_ordinary(application, home):
    exe, env = _launch_context(application)
    return subprocess.Popen([exe], cwd=home if home.is_dir() else Path(exe).parent, env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def launch(application):
    import fcntl

    exe, env = _launch_context(application)
    with contextlib.ExitStack() as child, contextlib.ExitStack() as holder:
        source, writer = os.pipe()
        child.callback(os.close, source)
        holder.callback(os.close, writer)
        reader, sink = os.pipe()
        holder.callback(os.close, reader)
        child.callback(os.close, sink)
        # Relocate before DUP2; either pipe might have been allocated fd 3 or 4.
        source = fcntl.fcntl(source, fcntl.F_DUPFD_CLOEXEC, 5)
        child.callback(os.close, source)
        sink = fcntl.fcntl(sink, fcntl.F_DUPFD_CLOEXEC, 5)
        child.callback(os.close, sink)
        actions = [(os.POSIX_SPAWN_OPEN, fd, os.devnull, os.O_RDWR, 0) for fd in (0, 1, 2)]
        actions.extend(((os.POSIX_SPAWN_DUP2, source, 3), (os.POSIX_SPAWN_DUP2, sink, 4)))
        # The persistent pipe holder is a separate single-threaded process. Its
        # sockets and gateway descriptors must not reach the desktop or its tools.
        if hasattr(os, 'POSIX_SPAWN_CLOSEFROM'):
            actions.append((os.POSIX_SPAWN_CLOSEFROM, 5))
        else:
            for name in os.listdir('/proc/self/fd'):
                fd = int(name)
                if fd > 4:
                    try:
                        inherited = os.get_inheritable(fd)
                    except OSError:
                        continue  # The fd used by listdir itself has closed.
                    if inherited:
                        actions.append((os.POSIX_SPAWN_CLOSE, fd))
        pid = os.posix_spawn(exe, [exe, '--remote-debugging-pipe'], env, file_actions=actions,
                             setsid=True, setsigdef=(signal.SIGPIPE,))
        holder.pop_all()
        return pid, writer, reader
