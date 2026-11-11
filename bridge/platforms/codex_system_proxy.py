"""Static proxy settings for the current desktop user, without PAC evaluation."""

import re
import subprocess
import sys


def current_user_proxy_settings():
    if sys.platform == 'darwin':
        return _macos()
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
        if values.get(prefix + 'Enable') != '1':
            continue
        hostname, port = values.get(prefix + 'Proxy'), values.get(prefix + 'Port')
        if hostname and port and port.isascii() and port.isdecimal():
            # The system setting stores the host and port separately. HTTPS
            # here describes the destination protocol, not proxy TLS.
            hostname = f'[{hostname}]' if ':' in hostname and not hostname.startswith('[') else hostname
            proxies[protocol] = 'http://' + hostname + ':' + port
    return proxies, bypass, values.get('ExcludeSimpleHostnames') == '1'


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
