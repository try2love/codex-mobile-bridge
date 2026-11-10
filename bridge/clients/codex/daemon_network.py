"""Explicit diagnostics for the local CLI owner. No model requests or writes on inspection."""
import hashlib
import ipaddress
import json
import re
import socket
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from bridge.platforms.codex_daemon_env import read_daemon_environment

PROXY_NAMES = ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')


def configuration_stamp(home):
    """Private change tokens only; rotating credentials are never exported."""
    result = {}
    for name in ('config.toml', 'auth.json'):
        try:
            result[name] = hashlib.sha256((Path(home) / name).read_bytes()).hexdigest()
        except OSError:
            result[name] = None
    return result


def graceful_restart_supported(info):
    version = re.match(r'^(?:Codex Desktop|codex_cli_rs|codex)/(\d+\.\d+\.\d+)(?:\s|$)', info.get('userAgent', ''))
    return bool(version and version.group(1) == '0.164.0')


def local_endpoint(value):
    try:
        url = urlsplit(value or '')
        if (url.scheme not in ('http', 'https', 'socks5', 'socks5h') or url.username or url.password
                or not url.port or not ipaddress.ip_address(url.hostname).is_loopback):
            return None
        return url.hostname, url.port
    except (TypeError, ValueError):
        return None


def reachable(endpoint):
    try:
        with socket.create_connection(endpoint, timeout=0.5):
            return True
    except OSError:
        return False


def inspect_proxy(home, executable):
    old, generation = read_daemon_environment(home, executable, PROXY_NAMES)
    old = {name.lower(): value for name, value in old.items()}
    system = getattr(urllib.request, 'getproxies_macosx_sysconf', urllib.request.getproxies)()
    previous = local_endpoint(old.get('https_proxy') or old.get('all_proxy'))
    replacement = system.get('https') or system.get('http')
    endpoint = local_endpoint(replacement)
    updates = {}
    if previous and endpoint and previous != endpoint and not reachable(previous) and reachable(endpoint):
        for name in PROXY_NAMES:
            # Use the current system HTTPS proxy for HTTPS requests; preserve
            # distinct HTTP and SOCKS settings if the system provides them.
            protocol = name.lower().split('_')[0]
            value = system.get(protocol) or (replacement if protocol == 'https' else None)
            if value and local_endpoint(value):
                updates[name] = value
    stamp = hashlib.sha256(json.dumps([generation, previous, updates], sort_keys=True).encode()).hexdigest()
    return generation, updates, stamp
