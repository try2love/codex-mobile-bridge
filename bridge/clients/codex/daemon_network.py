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


def file_auth_provider(config, provider):
    definition = (config.get('model_providers') or {}).get(provider) or {}
    return (config.get('cli_auth_credentials_store') == 'file'
            and definition.get('requires_openai_auth', provider == 'openai') is True
            and not definition.get('env_key') and not definition.get('experimental_bearer_token')
            and not any(str(name).lower() == 'authorization' for name in
                        (*((definition.get('http_headers') or {}).keys()),
                         *((definition.get('env_http_headers') or {}).keys()))))


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
    version = re.match(r'^(?:Codex Desktop|codex_cli_rs|codex|codex-tui|codex-mobile-bridge(?:-(?:cli|target-check|network-check|network-setting|auth-check|final-idle-check|network-verify))?)/(\d+\.\d+\.\d+)(?:\s|$)', info.get('userAgent', ''))
    return bool(version and version.group(1) in ('0.164.0', '0.167.0'))


def native_recovery_target(home, config, source_provider, environment, layers=()):
    """Adopt this home's current native configuration without changing its files."""
    from bridge.clients.codex.cli_session import CliSessions
    profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
    effective = {**config, **profile}
    provider = effective.get('model_provider') or 'openai'
    definitions = effective.get('model_providers') or {}
    if provider != 'openai' and not definitions.get(provider):
        raise ValueError('当前原生配置未定义目标提供商，未恢复服务。')
    def route(identifier):
        row = definitions.get(identifier) or {}
        return {**{key: row.get(key) for key in ('env_key', 'experimental_bearer_token',
                'http_headers', 'env_http_headers', 'query_params')},
                'base_url': row.get('base_url') or (effective.get('openai_base_url') if identifier == 'openai' else None),
                'requires_openai_auth': row.get('requires_openai_auth', identifier == 'openai'),
                'wire_api': row.get('wire_api', 'responses')}
    aliases = [provider]
    if provider in ('openai', 'bridge_api'):
        aliases += [name for name in ('openai', 'bridge_api') if name != provider
                    and (name == 'openai' or name in definitions) and route(name) == route(provider)]
    definition = definitions.get(provider) or {}
    required = {name for name in (definition.get('env_key'),
                *(definition.get('env_http_headers') or {}).values()) if isinstance(name, str) and name}
    missing = sorted(name for name in required if not environment.get(name))
    old_names = CliSessions._required_model_environment(effective, source_provider)
    if source_provider not in aliases:
        missing += sorted(required & (old_names | {'OPENAI_API_KEY', 'OPENAI_BASE_URL'}))
    if missing:
        raise ValueError('目标接入缺少可核验的变量来源：' + '、'.join(sorted(set(missing))) +
                         '；不会借用旧接入或网关终端的值，未启动服务。')
    clear = {'OPENAI_API_KEY', 'OPENAI_BASE_URL'}
    for identifier in {*definitions, source_provider, provider}:
        clear.update(CliSessions._required_model_environment(effective, identifier))
    values = {name: None for name in clear}
    values.update({name: environment[name] for name in required})
    try:
        auth = json.loads((Path(home) / 'auth.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        auth = {}
    injected = definition.get('env_key') or definition.get('experimental_bearer_token') or any(
        str(name).lower() == 'authorization' for name in
        (*((definition.get('http_headers') or {}).keys()), *((definition.get('env_http_headers') or {}).keys())))
    file_auth = not injected and definition.get('requires_openai_auth', provider == 'openai')
    known_store = effective.get('cli_auth_credentials_store') == 'file'
    kind = ('api' if auth.get('auth_mode') in (None, 'apikey') and auth.get('OPENAI_API_KEY') else
            'chatgpt' if auth.get('tokens') else None) if file_auth and known_store else None
    if file_auth and kind is None:
        if not known_store:
            pass  # The caller can retain an existing route, but cannot adopt its unknown account source.
        elif source_provider in aliases and environment.get('OPENAI_API_KEY'):
            values['OPENAI_API_KEY'] = environment['OPENAI_API_KEY']
        else:
            raise ValueError('目标接入缺少此 home 的明确认证来源；不会沿用旧账号的 API Key。')
    if provider == source_provider == 'openai' and not effective.get('openai_base_url') and not definition.get('base_url'):
        values['OPENAI_BASE_URL'] = environment.get('OPENAI_BASE_URL')
    # File identities bind the confirmation without publishing a key verifier.
    files = []
    for name in ('config.toml', 'auth.json'):
        try:
            stat = (Path(home) / name).stat()
            files.append((name, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except FileNotFoundError:
            files.append((name, None))
    versions = [(layer.get('name'), layer.get('version')) for layer in layers]
    identity = hashlib.sha256(json.dumps([str(Path(home).resolve()), provider, files, versions], sort_keys=True).encode()).hexdigest()
    return {'accountId': None, 'provider': provider, 'aliases': aliases, 'environment': values,
            'identity': identity, 'external': True, 'kind': kind,
            'unknownAuthSource': bool(file_auth and not known_store)}


def verify_file_auth(probe, home, config, provider, *, known_factory=False):
    """Only a proven file-auth/cache mismatch blocks a new operation."""
    if not known_factory:
        return
    path = Path(home) / 'auth.json'
    try:
        before = path.stat()
        auth = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    key = auth.get('OPENAI_API_KEY')
    if auth.get('auth_mode') not in (None, 'apikey') or not isinstance(key, str) or not key:
        return  # Native same-account ChatGPT refresh remains its own responsibility.
    status = probe.request('getAuthStatus', {'includeToken': True, 'refreshToken': False}, replay_safe=True)
    after = path.stat()
    if (before.st_ino, before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (
            after.st_ino, after.st_mtime_ns, after.st_ctime_ns, after.st_size):
        return
    cached = status.get('authToken')
    if status.get('authMethod') == 'apikey' and isinstance(cached, str) and cached and cached != key:
        raise IPCError('原生 CLI 仍缓存旧 API 认证，当前 auth.json 尚未同步；请检查 CLI 网络并明确确认恢复。阅读和停止任务仍可用。')


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
