"""Additional authenticated workbench routes for locally approved Noise bindings."""
import re
from urllib.parse import parse_qs, urlsplit

from bridge.features.sessions.operations import DESKTOP_PROVIDERS, READS, WRITES
from relay.protocol import DESKTOP_READ, DESKTOP_WRITE, THREAD, UUID

_DESKTOP = re.compile(r'/api/desktop-sessions/([a-z]+)/(.+)')
_PROJECT_ROUTES = {
    '/api/projects': ('GET', 'POST'), '/api/projects/models': ('GET',),
    '/api/projects/trust': ('POST',), '/api/projects/browse': ('POST',),
}
_DESKTOP_OPTIONS = {
    'detail': {'historyLimit', 'historyBefore'},
    'catalog': {'section', 'refresh'},
    'account': {'refresh'},
    'create-models': {'environment', 'projectKey', 'path', 'refresh'},
}
_CODEX_READ = {'events', 'permissions', 'cli-network', 'message-management', 'organization'}
_CODEX_WRITE = {'cli-network', 'compact', 'message-management', 'organization'}


def cloud_target(method, target, size):
    # Validate the untouched request-target before urlsplit can strip leading
    # whitespace or aiohttp can normalize a path into a different API operation.
    if (not target.startswith('/api/') or len(target) > 32768 or
            any(ord(c) < 32 or ord(c) == 127 for c in target) or '#' in target or '\\' in target):
        raise ValueError('Relative API target required')
    parsed = urlsplit(target)
    if ('%' in parsed.path or '//' in parsed.path or
            any(part in ('.', '..') for part in parsed.path.split('/')) or
            method not in ('GET', 'POST') or (method == 'GET' and size)):
        raise ValueError('Invalid API target or method')
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                     max_num_fields=64, errors='strict')
    for key, values in query.items():
        if (any(ord(c) < 32 or ord(c) == 127 for text in (key, *values) for c in text)
                or key in ('entry', 'sessionId', 'host', 'side') and (len(values) != 1 or not values[0])):
            raise ValueError('Invalid API query')
    return parsed.path, query


def session_route(method, path, query):
    """Routing only. Shared HTTP handlers still check fields, capabilities and live ownership."""
    route = _DESKTOP.fullmatch(path)
    if route:
        family, action = route.groups()
        if family not in DESKTOP_PROVIDERS:
            return False
        if 'entry' in query and (family == 'zcode' or not re.fullmatch(family + r':[0-9a-f]{32}', query['entry'][0])):
            raise ValueError('Invalid desktop entry')
        operations = READS if method == 'GET' else WRITES
        if action in operations:
            extras = _DESKTOP_OPTIONS.get(action, set()) if method == 'GET' else set()
            if set(query) - {'entry', 'sessionId'} - extras or any(len(values) != 1 for values in query.values()):
                raise ValueError('Invalid desktop session query')
            return True
        if method == 'GET':
            return (action in DESKTOP_READ
                    or re.fullmatch(r'images/[a-f0-9]{64}|uploads/' + UUID + '/preview', action) is not None)
        return (action in DESKTOP_WRITE
                or action in ('projects/browse', 'projects/register', 'projects/trust')
                or re.fullmatch('uploads/' + UUID + '/thumb', action) is not None)
    if path == '/api/cli-connections':
        return not query
    if path in _PROJECT_ROUTES:
        return method in _PROJECT_ROUTES[path]
    thread = THREAD.fullmatch(path)
    return bool(thread and thread[1] in (_CODEX_READ if method == 'GET' else _CODEX_WRITE))
