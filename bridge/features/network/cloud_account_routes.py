"""Which existing account operations an approved cloud workbench can address."""
import json
import re
from bridge.features.network.cloud_routes import cloud_target

_ACCOUNT = '/api/accounts'
_CLI = '/api/cli-access'
_WSL = '/api/wsl-accounts'
_CODEX_WRITE = frozenset({'account', 'details', 'switch', 'ssh-retry', 'reset-dynamic',
                          'reminders', 'desktop-update', 'ignore-submission'})
_DESKTOP = re.compile(r'/api/desktop-sessions/(claude|deepseek|zcode)/accounts')
_ENTRY = r'[a-f0-9]{32}'


def _account_path(path):
    return (path in (_ACCOUNT, '/api/account', '/api/account/reset', _CLI, _WSL)
            or path.startswith(_ACCOUNT + '/') or _DESKTOP.fullmatch(path) is not None)


def validate_account_route(method, path, query, size):
    if not _account_path(path):
        return False
    if (size > 512000 or method == 'POST' and not size
            or any(len(values) != 1 or not values[0] for values in query.values())):
        raise PermissionError('云账号请求参数或长度无效')
    fields = {key: values[0] for key, values in query.items()}
    desktop = _DESKTOP.fullmatch(path)
    if desktop:
        family = desktop[1]
        valid = not fields or (set(fields) == {'entry'} and family != 'zcode'
                               and re.fullmatch(family + ':' + _ENTRY, fields['entry']))
    elif path == _CLI:
        valid = not fields
    elif path == _WSL:
        provider = fields.get('provider')
        if method == 'POST':
            valid = not fields
        else:
            valid = (provider in ('codex', 'claude') and set(fields) == {'provider', 'entry'}
                     and re.fullmatch(('wsl:' if provider == 'codex' else 'wsl-claude:') + _ENTRY, fields['entry']))
    else:
        entry = fields.pop('entry', 'desktop')
        valid = entry == 'desktop' or re.fullmatch(r'(codex|wsl):' + _ENTRY, entry)
        if path == _ACCOUNT:
            valid = valid and method == 'GET' and (not fields or
                     set(fields) == {'resetForecast'} and fields['resetForecast'] in ('1', 'refresh'))
        elif path == '/api/account':
            valid = valid and method == 'GET' and (not fields or fields == {'cached': '1'})
        elif path == '/api/account/reset':
            valid = valid and method == 'POST' and not fields and not entry.startswith('wsl:')
        else:
            valid = (valid and method == 'POST' and not fields and not entry.startswith('wsl:')
                     and path[len(_ACCOUNT)+1:] in _CODEX_WRITE)
    if not valid:
        raise PermissionError('云账号请求不可用')
    return True


def enabled_cli_providers(manager):
    return tuple(family for family in ('codex', 'claude') if manager and manager.native_enabled(family))


def authorize_account_route(manager, method, target, body):
    if not _account_path(target.partition('?')[0]):
        return
    if manager is None:
        raise PermissionError('应用接入不可用')
    path, query = cloud_target(method, target, len(body))
    fields = {key: values[0] for key, values in query.items()}
    if method == 'POST' and path in (_CLI, _WSL):
        try:
            fields = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise PermissionError('云账号请求正文无效') from None
        if not isinstance(fields, dict):
            raise PermissionError('云账号请求正文无效')
    if path == _CLI:
        providers = enabled_cli_providers(manager)
        if not providers or method == 'POST' and fields.get('provider') not in providers:
            raise PermissionError('此 CLI 接入未启用')
        return
    if path == _WSL:
        provider = fields.get('provider')
        if provider not in ('codex', 'claude'):
            raise PermissionError('未知的 WSL 客户端')
        entry = fields.get('entry')
        if not isinstance(entry, str) or not re.fullmatch(('wsl:' if provider == 'codex' else 'wsl-claude:') + _ENTRY, entry):
            raise PermissionError('无效的 WSL 入口')
        if provider == 'claude':
            if not manager.claude_wsl:
                raise PermissionError('WSL Claude 接入不可用')
            try:
                manager.claude_wsl.selected(entry)
            except ValueError as exc:
                raise PermissionError(str(exc)) from None
            return
    else:
        match = _DESKTOP.fullmatch(path)
        provider = match[1] if match else 'codex'
        entry = fields.get('entry', 'desktop')
        if entry == 'desktop':
            if not manager.native_enabled(provider):
                raise PermissionError('此应用已关闭，请在应用管理中开启')
            return
    try:
        manager.require_enabled(entry)
    except ValueError as exc:
        raise PermissionError(str(exc)) from None
