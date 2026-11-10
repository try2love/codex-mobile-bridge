"""HTTP forwarding framing. Business authorization belongs to the gateway."""
import base64
import re
from urllib.parse import urlsplit

MAX_BODY = 20 * 1024 * 1024
MAX_FRAME = (MAX_BODY + 2) // 3 * 4 + 16384


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
    """Validate one bounded origin-relative request, without choosing its API."""
    if not isinstance(path, str) or len(path) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError('Invalid request path')
    p = urlsplit(path)
    if (p.scheme or p.netloc or '#' in path or '%' in p.path or '\\' in path
            or not path.startswith('/') or '//' in p.path
            or any(part in ('.', '..') for part in p.path.split('/'))):
        raise ValueError('Origin-relative HTTP path required')
    if not isinstance(method, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,32}", method):
        raise ValueError('Invalid HTTP method')
    if type(size) is not int or not 0 <= size <= MAX_BODY:
        raise ValueError('Invalid body size')
    if method in ('GET', 'HEAD') and size:
        raise ValueError('Read request body is not allowed')


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


def request_headers(method, value):
    """Bound range requests without accepting caller credentials or HTTP framing."""
    headers = download_headers(value)
    if not headers:
        return headers
    if method != 'GET' or 'Range' not in headers:
        raise ValueError('Range negotiation requires a GET range')
    match = re.fullmatch(r'bytes=(\d{1,18})-(\d{1,18})', headers['Range'])
    if not match:
        raise ValueError('Only one bounded byte range is supported')
    start, end = map(int, match.groups())
    if end < start or end - start + 1 > MAX_BODY:
        raise ValueError('Download range exceeds channel limit')
    if 'If-Range' in headers and not re.fullmatch(r'"[^"\\]*"', headers['If-Range']):
        raise ValueError('A strong entity tag is required for resuming')
    return headers
