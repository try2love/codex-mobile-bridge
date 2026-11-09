"""Read-only native identity and loopback checks for Antigravity language servers."""
import os
from pathlib import Path
import plistlib
import re
import shlex
import sys


def profile_lease(path):
    """Keep a nonblocking cross-process lock until its returned stream closes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise ValueError('此 Antigravity 用户资料已由另一网关接入，请先停用原接入') from None
    return stream


def _trusted_image(image):
    image = Path(image).resolve()
    if not image.name.startswith('language_server'):
        return False
    if sys.platform == 'darwin':
        bundle = next((p for p in image.parents if p.suffix == '.app'), None)
        if bundle is None:
            return False
        try:
            info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        except (OSError, ValueError):
            return False
        return info.get('CFBundleIdentifier') in ('com.google.antigravity', 'com.google.antigravity-ide')
    # Packaged native servers only; never accept a process name or PATH alias.
    parts = {part.casefold() for part in image.parts}
    return ('antigravity' in parts or 'antigravity-ide' in parts) and ('resources' in parts or 'extensions' in parts)


def _identity(pid):
    if sys.platform == 'win32':
        from bridge.platforms.windows.zcode_pipe import identity
        return identity(pid)  # Same user, interactive session, birth and image.
    if sys.platform == 'darwin':
        from bridge.platforms.macos.force_exit import Native
    else:
        from bridge.platforms.linux.force_exit import Native
    row = Native().identity(pid)
    if row['uid'] != os.getuid() or row['zombie']:
        raise PermissionError('Antigravity 进程用户不匹配')
    return row


def _candidates():
    if sys.platform == 'win32':
        from bridge.platforms.windows.desktop import _rows
        from bridge.platforms.windows.zcode_pipe import process_args
        for row in _rows():
            if _trusted_image(row['ExecutablePath']):
                pid = int(row['ProcessId'])
                yield pid, process_args(pid)
    elif sys.platform == 'darwin':
        from bridge.platforms.macos.desktop import _process_rows, commands
        for pid, image in _process_rows():
            if _trusted_image(image):
                # Flags consumed below contain no spaces. The executable comes
                # from the kernel image, never from this display command line.
                yield pid, shlex.split(commands([pid]).get(pid, ''))
    elif sys.platform == 'linux':
        from bridge.platforms.linux.desktop import _process_rows
        for path, image in _process_rows():
            if _trusted_image(image):
                yield int(path.name), (path/'cmdline').read_bytes().decode().rstrip('\0').split('\0')


def _flag(args, name):
    values = []
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            values.append(args[index + 1])
        elif value.startswith(name + '='):
            values.append(value[len(name) + 1:])
    return values[0] if len(values) == 1 else None


def _listener(pid, port, image):
    if sys.platform in ('darwin', 'win32'):
        if sys.platform == 'darwin':
            from bridge.platforms.macos.loopback import listener_identity
        else:
            from bridge.platforms.windows.loopback import listener_identity
        return listener_identity(port, image)['pid'] == pid
    # Linux exposes socket ownership through /proc without running lsof.
    sockets = set()
    for fd in (Path('/proc')/str(pid)/'fd').iterdir():
        try:
            match = re.fullmatch(r'socket:\[(\d+)\]', os.readlink(fd))
            if match:
                sockets.add(match[1])
        except OSError:
            continue
    listeners = []
    for name in ('tcp', 'tcp6'):
        for line in (Path('/proc/net')/name).read_text().splitlines()[1:]:
            fields = line.split()
            address, number = fields[1].split(':')
            if int(number, 16) == port and fields[3] == '0A':
                listeners.append((address, fields[9]))
    return len(listeners) == 1 and listeners[0][0] == '0100007F' and listeners[0][1] in sockets


def endpoint(surface, home):
    """Return one same-user, same-surface server, or fail without contacting it."""
    if Path(home).resolve() != Path.home().resolve():
        raise ValueError('Antigravity 本机控制只支持当前系统用户资料')
    data_name = {'app': 'antigravity', 'ide': 'antigravity-ide', 'cli': 'antigravity-cli'}.get(surface)
    if not data_name:
        raise ValueError('Antigravity 来源无效')
    matches = []
    for pid, args in _candidates():
        if _flag(args, '--app_data_dir') != data_name:
            continue
        before = _identity(pid)
        if not _trusted_image(before['image']):
            continue
        token = _flag(args, '--csrf_token')
        if not token or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', token):
            continue
        # Read only the matching product's log. A log port alone is never
        # sufficient: the listener must belong to this exact process generation.
        if sys.platform == 'darwin':
            logs = [Path(home)/'Library/Logs'/('Antigravity' if surface == 'app' else 'Antigravity IDE')/'language_server.log']
        elif sys.platform == 'win32':
            logs = [Path(os.environ.get('APPDATA', ''))/('Antigravity' if surface == 'app' else 'Antigravity IDE')/'logs/language_server.log']
        else:
            logs = [Path(home)/'.config'/('Antigravity' if surface == 'app' else 'Antigravity IDE')/'logs/language_server.log']
        ports = set()
        for log in logs:
            if log.is_file():
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 256 * 1024))
                    text = stream.read(256 * 1024).decode('utf-8', errors='replace')
                ports.update(int(p) for p in re.findall(r'listening on \w+ port at (\d+) for HTTP\b', text))
        for port in ports:
            try:
                if 1024 <= port <= 65535 and _listener(pid, port, before['image']) and _identity(pid) == before:
                    matches.append({'address': 'http://127.0.0.1:%d' % port, 'token': token,
                                    'identity': before, 'surface': surface})
            except (OSError, ValueError):
                continue  # An old log port is not a current native endpoint.
    if len(matches) != 1:
        raise ValueError('未找到唯一、同用户且属于此来源的 Antigravity 原生服务；请在电脑打开相应应用')
    return matches[0]


def verify_endpoint(value):
    before = value['identity']
    port = int(value['address'].rsplit(':', 1)[1])
    if (_identity(before['pid']) != before or not _trusted_image(before['image']) or
            not _listener(before['pid'], port, before['image'])):
        raise ValueError('Antigravity 原生服务进程已变化，请重新连接')
