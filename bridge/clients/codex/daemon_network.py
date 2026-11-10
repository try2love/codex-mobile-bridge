"""Explicit diagnostics for the local CLI owner. No model requests or writes on inspection."""
import hashlib
import ipaddress
import json
import re
import socket
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from bridge.clients.codex.ipc import IPCError
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
    version = re.match(r'^(?:Codex Desktop|codex_cli_rs|codex|codex-tui)/(\d+\.\d+\.\d+)(?:\s|$)', info.get('userAgent', ''))
    return bool(version and version.group(1) == '0.164.0')


def system_proxy_user_layer(snapshot, home):
    """Return the versioned, unprofiled user config belonging to this native home."""
    target = (Path(home) / 'config.toml').resolve()
    layers = snapshot.get('layers') if isinstance(snapshot, dict) else None
    matches = []
    for layer in layers if isinstance(layers, list) else []:
        name = layer.get('name') if isinstance(layer, dict) else None
        if not isinstance(name, dict) or name.get('type') != 'user' or name.get('profile') is not None:
            continue
        filename = name.get('file')
        if isinstance(filename, str) and Path(filename).is_absolute() and Path(filename).resolve() == target:
            matches.append(layer)
    if (len(matches) != 1 or not isinstance(matches[0].get('version'), str)
            or not matches[0]['version'] or matches[0].get('disabledReason')
            or not isinstance(matches[0].get('config'), dict)):
        raise ValueError('无法核验此 CLI 配置的用户设置版本；未恢复服务。')
    return matches[0]


def _system_proxy_enabled(snapshot, layer):
    config = snapshot.get('config')
    if not isinstance(config, dict):
        raise ValueError('无法核验原生系统代理设置；未恢复服务。')
    features = config.get('features')
    effective = features.get('respect_system_proxy') if isinstance(features, dict) else None
    origins = snapshot.get('origins')
    origin = origins.get('features.respect_system_proxy') if isinstance(origins, dict) else None
    name = origin.get('name') if isinstance(origin, dict) else None
    if effective is True or effective is False and isinstance(name, dict) and name.get('type') == 'sessionFlags':
        return effective
    user_features = layer['config'].get('features')
    user_value = user_features.get('respect_system_proxy') if isinstance(user_features, dict) else None
    if user_value is not True:
        if effective is False and (name is None or name == layer['name']):
            return effective
        missing = features is None or isinstance(features, dict) and 'respect_system_proxy' not in features
        if effective is None and missing and name is None and user_value is None:
            return effective
    # An absent origin can mean the native default, but cannot prove that
    # a persisted true value has overcome requirements or another layer.
    raise ValueError('原生系统代理设置受其他配置或策略限制；未恢复服务。')


def enable_system_proxy_following(probe, home, expected_version=None, before_write=None):
    """Persist one native setting with CAS; never unload a thread or restart a daemon."""
    snapshot = probe.request('config/read', {'includeLayers': True})
    layer = system_proxy_user_layer(snapshot, home)
    if expected_version is not None and layer['version'] != expected_version:
        raise ValueError('CLI 配置已变化，请重新检查并确认；未恢复服务。')
    _system_proxy_enabled(snapshot, layer)
    features = layer['config'].get('features')
    if isinstance(features, dict) and features.get('respect_system_proxy') is True:
        return False
    if before_write:
        before_write()
    try:
        response = probe.request('config/value/write', {
            'keyPath': 'features.respect_system_proxy', 'value': True,
            'mergeStrategy': 'replace', 'expectedVersion': layer['version'],
        })
    except Exception as error:
        if getattr(error, 'rpc_error', None) is None:
            error.system_proxy_unknown = True
        raise
    if (not isinstance(response, dict) or response.get('status') not in ('ok', 'okOverridden')
            or not isinstance(response.get('version'), str) or not response['version']
            or response.get('filePath') != str((Path(home) / 'config.toml').resolve())):
        error = IPCError('原生系统代理设置的写入结果尚未确认；请先核验，不要重复提交。')
        error.system_proxy_unknown = True
        raise error
    try:
        updated = probe.request('config/read', {'includeLayers': True})
        current = system_proxy_user_layer(updated, home)
        features = current['config'].get('features')
        if (current['version'] != response['version'] or not isinstance(features, dict)
                or features.get('respect_system_proxy') is not True):
            raise ValueError('系统代理设置已保存，但配置核验未通过；未恢复服务。')
        _system_proxy_enabled(updated, current)
    except Exception as error:
        error.system_proxy_saved = True
        raise
    return True


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
