"""Shared relay boundary. No caller-selected HTTP destinations or raw IPC."""
import base64
import re
from urllib.parse import urlsplit

MAX_BODY = 20 * 1024 * 1024
MAX_FRAME = (MAX_BODY + 2) // 3 * 4 + 16384
UUID = r'[0-9a-f-]{36}'
THREAD = re.compile(r'/api/sessions/' + UUID + r'(?:/(.*))?')
READ = {'', 'catalog', 'poll', 'timeline', 'detail', 'changes', 'notifications',
        'side-chat', 'terminal', 'subagents', 'workspace', 'workspace/preview',
        'workspace/download', 'workspace/git-status', 'workspace/git-diff',
        'workspace/git-history', 'workspace/git-commit', 'workspace/git-history-diff',
        'workspace/git-branches'}
WRITE = {'send', 'stop', 'history', 'respond', 'reconnect', 'queue', 'settings',
         'permissions', 'uploads', 'message-action', 'rename', 'notifications',
         'goal/cancel', 'goal/edit', 'goal/status', 'side-chat', 'terminal',
         'workspace/upload', 'workspace/git-action'}


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
