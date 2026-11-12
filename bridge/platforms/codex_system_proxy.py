"""Static proxy settings for the current desktop user, without PAC evaluation."""

import re
import subprocess
import sys
from pathlib import Path

_FLYBIRD_APP = Path('/Applications/飞鸟加速.app')


def current_user_proxy_settings():
    if sys.platform == 'darwin':
        settings = _macos()
        if settings is None or settings[0]:
            return settings
        candidate = _flybird()
        return (dict.fromkeys(('http', 'https'), candidate), settings[1], settings[2]) if candidate else settings
    if sys.platform == 'win32':
        return _windows()
    return None


def _macos():
    try:
        snapshot = subprocess.run(['/usr/sbin/scutil', '--proxy'], check=True, capture_output=True,
                                  text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if not snapshot.lstrip().startswith('<dictionary>'):
        return None
    values = dict(re.findall(r'^  (\w+)[ \t]*:[ \t]*(.+)$', snapshot, re.M))
    if any(values.get(key, '0').strip() != '0' for key in ('ProxyAutoConfigEnable', 'ProxyAutoDiscoveryEnable')):
        return None
    bypass = ()
    if 'ExceptionsList' in values:
        entries = re.search(r'^  ExceptionsList : <array> \{\n(.*?)^  \}', snapshot, re.M | re.S)
        if not entries:
            return None
        bypass = re.findall(r'^\s*\d+[ \t]*:[ \t]*(.+)$', entries[1], re.M)
    proxies = {}
    for protocol in ('http', 'https'):
        prefix = protocol.upper()
        enabled = values.get(prefix + 'Enable', '0')
        if enabled not in ('0', '1'):
            return None
        if enabled == '0':
            continue
        hostname, port = values.get(prefix + 'Proxy'), values.get(prefix + 'Port')
        if not hostname or not port or not port.isascii() or not port.isdecimal() or not 0 < int(port) < 65536:
            return None
        if hostname and port:
            # The system setting stores the host and port separately. HTTPS
            # here describes the destination protocol, not proxy TLS.
            hostname = f'[{hostname}]' if ':' in hostname and not hostname.startswith('[') else hostname
            proxies[protocol] = 'http://' + hostname + ':' + port
    if not proxies and values.get('SOCKSEnable', '0').strip() != '0':
        return None
    return proxies, bypass, values.get('ExcludeSimpleHostnames') == '1'


def _flybird_processes(executable, config):
    """Read only candidate metadata/argv; never use signal-grade identity APIs."""
    import ctypes
    import os
    from bridge.platforms.macos.desktop import commands
    from bridge.platforms.macos.force_exit import Native
    result = subprocess.run(['/bin/ps', '-ww', '-axo', 'pid=,uid=,lstart=,comm='],
        check=True, capture_output=True, text=True, timeout=2, env={**os.environ, 'LC_ALL': 'en_US.UTF-8'})
    native, rows = Native(), []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 7)
        if (len(parts) != 8 or not parts[0].isdigit() or not parts[1].isdigit()
                or int(parts[1]) not in (0, os.getuid()) or parts[7] not in ('core', str(executable))):
            continue
        pid = int(parts[0])
        image = ctypes.create_string_buffer(4096)
        if native.lib.proc_pidpath(pid, image, len(image)) <= 0 or os.fsdecode(image.value) != str(executable):
            continue
        rows.append((pid, int(parts[1]), ' '.join(parts[2:7]), str(executable)))
        if len(rows) > 4:
            return []
    values = commands([row[0] for row in rows])
    target = re.escape(str(config))
    argument = r'(?:"' + target + r'"|\'' + target + r'\'|' + target + r')(?=\s+-|$)'
    flag = r'(?:^|\s)(?:-f|--config)(?:=|\s+)'
    return [row for row in rows if len(re.findall(flag, values.get(row[0], ''))) == 1
            and re.search(flag + argument, values.get(row[0], ''))]


def _flybird_listener(pid, port):
    result = subprocess.run(['/usr/sbin/netstat', '-anv', '-p', 'tcp'], check=True,
                            capture_output=True, text=True, timeout=2)
    columns, owners = None, set()
    for line in result.stdout.splitlines():
        if line.lstrip().startswith('Proto '):
            columns = line.replace('Local Address', 'LocalAddress').replace('Foreign Address', 'ForeignAddress').split()
            continue
        fields = line.split()
        if not columns or not fields or not fields[0].startswith('tcp'):
            continue
        if not all(name in columns for name in ('LocalAddress', '(state)', 'pid')):
            return False
        if len(fields) <= max(columns.index(name) for name in ('LocalAddress', '(state)', 'pid')):
            continue
        if fields[columns.index('(state)')] != 'LISTEN':
            continue
        host, _, number = fields[columns.index('LocalAddress')].rpartition('.')
        if host not in ('127.0.0.1', '::1', '*', '0.0.0.0', '::') or number != str(port):
            continue
        owner = fields[columns.index('pid')]
        if not owner.isdigit():
            return False
        owners.add(int(owner))
    return owners == {pid}


def _flybird():
    """One known current-user mixed-port source; no shell/YAML execution."""
    import os
    import plistlib
    import pwd
    import socket
    import stat
    try:
        uid = os.getuid()
        home = Path(pwd.getpwuid(uid).pw_dir)
        config = home/'Library/Application Support/com.fnjs.clash/clash/config.yaml'
        executable = _FLYBIRD_APP/'Contents/MacOS/core'
        info = executable.parent.parent/'Info.plist'
        with info.open('rb') as stream:
            package = plistlib.load(stream)
            if not isinstance(package, dict) or package.get('CFBundleIdentifier') != 'com.fnjs.clash':
                return None
        for path in (config, *config.parents):
            if path.is_symlink():
                return None
            if path == home:
                break
        with os.fdopen(os.open(config, os.O_RDONLY | os.O_NOFOLLOW), 'r', encoding='utf-8-sig') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_uid != uid or before.st_size > 2_000_000:
                return None
            port = None
            for line in stream:
                if not line.startswith('mixed-port:') and not re.match(r'mixed-port[ \t]+:', line):
                    continue
                match = re.fullmatch(r'mixed-port[ \t]*:[ \t]*([0-9]{1,5})[ \t]*(?:#[^\n]*)?\r?\n?', line)
                if not match or port is not None:
                    return None
                port = int(match[1])
            if port is None or not 0 < port < 65536:
                return None
            processes = _flybird_processes(executable, config)
            if len(processes) != 1 or not _flybird_listener(processes[0][0], port):
                return None
            with socket.create_connection(('127.0.0.1', port), timeout=.5) as connection:
                connection.sendall(b'\x05\x01\x00')
                reply = connection.recv(2)
                if len(reply) == 1:
                    reply += connection.recv(1)
                if reply != b'\x05\x00':
                    return None
            after = os.fstat(stream.fileno())
            path = config.lstat()
            signature = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_size, value.st_mtime_ns)
            if (signature(before) != signature(after) or signature(before) != signature(path)
                    or _flybird_processes(executable, config) != processes
                    or not _flybird_listener(processes[0][0], port)):
                return None
            return 'http://127.0.0.1:' + str(port)
    except (OSError, ValueError, subprocess.SubprocessError, plistlib.InvalidFileException):
        return None


def _windows():
    import ctypes
    from ctypes import wintypes

    class ProxyConfig(ctypes.Structure):
        _fields_ = [('auto_detect', wintypes.BOOL), ('pac', ctypes.c_void_p),
                    ('proxy', ctypes.c_void_p), ('bypass', ctypes.c_void_p)]

    get_config = ctypes.WinDLL('winhttp').WinHttpGetIEProxyConfigForCurrentUser
    get_config.argtypes, get_config.restype = [ctypes.POINTER(ProxyConfig)], wintypes.BOOL
    free = ctypes.WinDLL('kernel32').GlobalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    settings = ProxyConfig()
    try:
        if not get_config(ctypes.byref(settings)) or settings.auto_detect or settings.pac or not settings.proxy:
            return None
        value = ctypes.wstring_at(settings.proxy)
        bypass = re.split(r'[;,\s]+', ctypes.wstring_at(settings.bypass)) if settings.bypass else ()
        proxies = {}
        if '=' in value:
            for part in value.split(';'):
                protocol, separator, address = part.strip().partition('=')
                if not separator or protocol.lower() in proxies:
                    return None
                proxies[protocol.lower()] = _proxy_url(address.strip())
        elif value and not any(char in value for char in ';, \t'):
            proxies = dict.fromkeys(('http', 'https'), _proxy_url(value))
        return proxies, bypass, False
    finally:
        for address in (settings.pac, settings.proxy, settings.bypass):
            if address:
                free(address)


def _proxy_url(value):
    return value if '://' in value else 'http://' + value
