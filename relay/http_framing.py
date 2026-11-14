"""Cloud HTTP framing; the gateway owns API authorization and capabilities."""
import re
from urllib.parse import urlsplit

from relay.protocol import MAX_BODY

MAX_PATH_LENGTH = 8192
MAX_METHOD_LENGTH = 32
HTTP_METHOD = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1," + str(MAX_METHOD_LENGTH) + r"}")
READ_METHODS = frozenset(('GET', 'HEAD'))


def validate_relative_path(value, parsed):
    if parsed.scheme or parsed.netloc or '#' in value:
        raise ValueError('Origin-relative HTTP path required')
    if '%' in parsed.path or '\\' in value:
        raise ValueError('Origin-relative HTTP path required')
    if not value.startswith('/') or '//' in parsed.path:
        raise ValueError('Origin-relative HTTP path required')
    if any(part in ('.', '..') for part in parsed.path.split('/')):
        raise ValueError('Origin-relative HTTP path required')


def validate_path(value):
    if not isinstance(value, str) or len(value) > MAX_PATH_LENGTH:
        raise ValueError('Invalid request path')
    if any(char < ' ' or char == '\x7f' for char in value):
        raise ValueError('Invalid request path')
    validate_relative_path(value, urlsplit(value))


def validate_http_request(method, path, size=0):
    validate_path(path)
    if not isinstance(method, str) or not HTTP_METHOD.fullmatch(method):
        raise ValueError('Invalid HTTP method')
    if type(size) is not int or not 0 <= size <= MAX_BODY:
        raise ValueError('Invalid body size')
    if method in READ_METHODS and size:
        raise ValueError('Read request body is not allowed')
