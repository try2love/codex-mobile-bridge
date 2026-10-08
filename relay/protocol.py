"""Shared relay boundary. No caller-selected HTTP destinations or raw IPC."""
import base64
import re
from urllib.parse import urlsplit

MAX_BODY = 20 * 1024 * 1024
MAX_FRAME = (MAX_BODY + 2) // 3 * 4 + 16384
UUID = r'[0-9a-f-]{36}'
THREAD = re.compile(r'/api/sessions/' + UUID + r'(?:/(.*))?')
DESKTOP = re.compile(r'/api/desktop-sessions/(?:deepseek|claude)/(.*)')
READ = {'', 'catalog', 'poll', 'timeline', 'detail', 'changes', 'notifications',
        'side-chat', 'terminal', 'subagents', 'workspace', 'workspace/preview',
        'workspace/download', 'workspace/git-status', 'workspace/git-diff',
        'workspace/git-history', 'workspace/git-commit', 'workspace/git-history-diff',
        'workspace/git-branches'}
WRITE = {'send', 'stop', 'history', 'respond', 'reconnect', 'queue', 'settings',
         'permissions', 'uploads', 'message-action', 'rename', 'notifications',
         'goal/cancel', 'goal/edit', 'goal/status', 'side-chat', 'terminal',
         'workspace/upload', 'workspace/git-action'}
DESKTOP_READ = {'list', 'detail', 'catalog', 'projects', 'account', 'access', 'terminal'} | {action for action in READ if action.startswith('workspace')}
DESKTOP_WRITE = {'send', 'stop', 'settings', 'respond', 'create', 'access', 'uploads', 'terminal', 'workspace/upload', 'workspace/git-action'}


def origin(value, test_http=False):
    if not isinstance(value, str):
        raise ValueError('Relay URL required')
    p = urlsplit(value)
    if (not p.hostname or p.username or p.password or p.query or p.fragment
            or p.path not in ('', '/') or p.port == 0):
        raise ValueError('Relay URL must be an HTTPS origin without a path')
    if p.scheme != 'https' and not (test_http and p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('Relay requires HTTPS')
    return value.rstrip('/')


def validate(method, path, size=0):
    if not isinstance(path, str) or len(path) > 8192 or any(ord(c) < 32 for c in path):
        raise ValueError('Invalid request path')
    p = urlsplit(path)
    if p.scheme or p.netloc or p.fragment or '%' in p.path or '\\' in path or '//' in p.path:
        raise ValueError('Relative API path required')
    if method not in ('GET', 'POST') or type(size) is not int or not 0 <= size <= MAX_BODY:
        raise ValueError('Unsupported request or body too large')
    if method == 'GET' and size:
        raise ValueError('GET body is not allowed')
    if p.path == '/api/sessions' or (p.path in ('/api/projects', '/api/mobile/events') and method == 'GET') or (p.path == '/api/activity' and method == 'POST'):
        return
    # Remote clients may toggle already configured apps. Discovery, installation,
    # account configuration and desktop lifecycle remain local desktop controls.
    if p.path == '/api/clients':
        return
    desktop = DESKTOP.fullmatch(p.path)
    if desktop:
        if desktop[1] in (DESKTOP_READ if method == 'GET' else DESKTOP_WRITE):
            return
        if re.fullmatch('uploads/' + UUID + ('/preview' if method == 'GET' else '/thumb'), desktop[1]):
            return
    match = THREAD.fullmatch(p.path)
    if match:
        action = match[1] or ''
        if action in (READ if method == 'GET' else WRITE):
            return
        if method == 'GET' and re.fullmatch(r'(files|desktop-images)/[a-f0-9]{64}', action):
            return
        if re.fullmatch('uploads/' + UUID + ('/preview' if method == 'GET' else '/thumb'), action):
            return
    raise PermissionError('This operation is not available through the shared relay')


def decode(value):
    if not isinstance(value, str) or len(value) > MAX_FRAME:
        raise ValueError('Invalid frame body')
    result = base64.b64decode(value, validate=True)
    if len(result) > MAX_BODY:
        raise ValueError('Body exceeds relay limit')
    return result


def content_type(value):
    if not isinstance(value, str) or len(value) > 160 or not re.fullmatch(r'[\w!#$&^_.+-]+/[\w!#$&^_.+-]+(?:;\s*charset=[\w-]+)?', value):
        raise ValueError('Invalid content type')
    return value


def download_headers(value, response=False):
    """Only range negotiation crosses the relay; never credentials or cookies."""
    allowed = {'ETag', 'Content-Range', 'Accept-Ranges'} if response else {'Range', 'If-Range'}
    if not isinstance(value, dict) or any(key not in allowed for key in value):
        raise ValueError('Invalid download headers')
    for key, item in value.items():
        if not isinstance(item, str) or len(item) > 256 or any(ord(c) < 32 or ord(c) > 126 for c in item):
            raise ValueError('Invalid download header')
        if key == 'Accept-Ranges' and item not in ('bytes', 'none'):
            raise ValueError('Invalid range support header')
        if key == 'Content-Range' and not re.fullmatch(r'bytes (?:\d+-\d+/\d+|\*/\d+)', item):
            raise ValueError('Invalid content range')
        if key == 'ETag' and not re.fullmatch(r'(?:W/)?"[^"\\]*"', item):
            raise ValueError('Invalid entity tag')
    return dict(value)
