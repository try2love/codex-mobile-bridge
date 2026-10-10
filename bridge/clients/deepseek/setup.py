"""Recognize our existing Harness insertion without executing YAML expressions."""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

MARKER = '# codex-mobile-bridge desktop adapter'
LEGACY_SOURCE = '374720c7f1340c98e10515c5e52ca9f20526f23d164515d2fc4ea148c2e09401'

# Last packaged revision 2, before explicit runtime-state evidence was added.
PREVIOUS_SOURCE = '72578883751d175d30c56d8693c978f4e5afea2fea4e6700d47d1533813453e5'

# Revision 3 before native fiber cleanup was corrected; upgrade keeps its credentials.
PREVIOUS_CLEANUP_SOURCE = 'cda63d5cd12daa9b4e67c708aeff57a2e8c0161f7d56eb988482234249d9fbbb'

# Revision 3 before account model catalogs were available without a chat.
PREVIOUS_CATALOG_SOURCE = 'b2fb9136b07f0734702e9bf26c880aa7af46da5aa340735e39616487ce1d8f19'

# Revision 3 before native quit support (Windows CRLF and release LF sources).
PREVIOUS_QUIT_SOURCES = ('2a9926ace79180be27c248bc68c6c6d9367c58c318c8dd8a8402a9aeac83177c',
                         '68e4f5712d4658bbc1da3767b86c45f6d01c7e34257383cac6f4229a27083192')

# Revision 3 shipped by the Windows integration (3ad8f0a), LF and CRLF.
# Keep this verified predecessor when changing host.mjs: source equality alone
# must not strand an existing, otherwise fully owned desktop connection.
PREVIOUS_CONTEXT_SOURCES = ('c60b404ff5efcadc4329b125ff8524293bc4e4c2e29224393ea9cec5753ea2a1',
                            '4fdfe35712002fc368a2db8fd4e18ab2cc851bacb31473f583ffde817d564068')


def insertion(text):
    """Read only our JSON flow insertion, including Harness's line wrapping.

    The rest of a user's patch can contain arbitrary YAML, including !!js. It
    is never parsed or executed. Unknown forms are left untouched for review.
    """
    markers = list(re.finditer(r'(?m)^'+re.escape(MARKER)+r'[ \t]*\r?$', text))
    if not markers:
        return None
    if len(markers) != 1:
        raise ValueError('Harness 接入配置包含重复标记，尚未修改配置')
    marker = markers[0]
    prefix = re.match(r'\s*-\s*(?=\{)', text[marker.end():])
    if not prefix:
        raise ValueError('Harness 接入配置格式无法识别，尚未修改配置')
    start = marker.end()+prefix.end()
    chars, depth, quoted, escaped = [], 0, False, False
    index = start
    while index < len(text):
        char = text[index]
        if quoted and not escaped and char == '\\':
            continuation = re.match(r'\\\r?\n[ \t]*', text[index:])
            if continuation:
                index += continuation.end()
                continue
        chars.append(char)
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            depth += 1
        elif char in '}]':
            depth -= 1
            if depth == 0:
                try:
                    entry = json.loads(''.join(chars), object_pairs_hook=_unique)
                except (ValueError, TypeError):
                    raise ValueError('Harness 接入配置格式无法识别，尚未修改配置') from None
                end = index+1
                if text[end:end+2] == '\r\n':
                    end += 2
                elif text[end:end+1] == '\n':
                    end += 1
                return entry, marker.start(), end
        index += 1
    raise ValueError('Harness 接入配置不完整，尚未修改配置')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def _owned_file(path, private=False):
    stat = path.stat()
    if not path.is_file() or stat.st_size > 1024*1024:
        raise ValueError('invalid connector file')
    if os.name != 'nt' and (stat.st_uid != os.getuid() or (private and stat.st_mode & 0o077)):
        raise ValueError('invalid connector owner')


def verified_directory(entry, home):
    """Return an existing connector only when its own durable record agrees."""
    try:
        if not isinstance(entry, dict) or set(entry) != {'insert'} or len(entry['insert']) != 1:
            return None
        plugin = entry['insert'][0]
        if set(plugin) != {'id', 'name', 'config'} or plugin['id'] != 'codex-mobile-desktop' or set(plugin['config']) != {'configPath'}:
            return None
        uri = urlsplit(plugin['name'])
        if uri.scheme != 'file' or uri.netloc or uri.query or uri.fragment:
            return None
        source = Path(url2pathname(uri.path)).resolve()
        config = Path(plugin['config']['configPath'])
        if not config.is_absolute() or config.name != 'connection.json':
            return None
        config = config.resolve()
        directory = config.parent
        if source != directory/'mobile-host.mjs':
            return None
        record = directory/'installation.json'
        for path in (source, config, record):
            _owned_file(path, path != source)
        saved = json.loads(record.read_text('utf-8'))
        if Path(saved['home']).resolve() != home.resolve() or json.loads(saved['line'][2:], object_pairs_hook=_unique) != entry:
            return None
        credentials = json.loads(config.read_text('utf-8'))
        if not isinstance(credentials.get('token'), str) or not credentials['token'] or credentials.get('endpoint') != str(directory/'endpoint.json'):
            return None
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        current = hashlib.sha256(Path(__file__).with_name('host.mjs').read_bytes()).hexdigest()
        if digest not in (current, LEGACY_SOURCE, PREVIOUS_SOURCE, PREVIOUS_CLEANUP_SOURCE, PREVIOUS_CATALOG_SOURCE,
                          *PREVIOUS_QUIT_SOURCES, *PREVIOUS_CONTEXT_SOURCES):
            return None
        return directory, digest != current
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def _mapping(text):
    """Read the scalar/mapping subset emitted by the local credential store.

    Unknown YAML features fail closed; no aliases, tags or code constructors.
    Values remain local and must never be included in an exception or response.
    """
    if text.lstrip().startswith('{'):
        return json.loads(text, object_pairs_hook=_unique)
    root = {}
    stack = [(-1, root)]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'( *)([^:]+):(?: +(.*))?', line)
        if not match:
            raise ValueError('unsupported credential format')
        indent, key, raw = len(match[1]), match[2], match[3]
        key = _scalar(key)
        if not isinstance(key, str):
            raise ValueError('invalid credential key')
        while indent <= stack[-1][0]:
            stack.pop()
        if indent != (0 if stack[-1][0] == -1 else stack[-1][0]+2):
            raise ValueError('unsupported credential indentation')
        mapping = stack[-1][1]
        if key in mapping:
            raise ValueError('duplicate credential key')
        value = {} if raw is None else _scalar(raw)
        mapping[key] = value
        if isinstance(value, dict):
            stack.append((indent, value))
    return root


def _scalar(value):
    value = value.strip()
    if not value or value[0] in '&*!|>{[':
        raise ValueError('unsupported credential scalar')
    if value.startswith('"'):
        parsed = json.loads(value)
        if not isinstance(parsed, str):
            raise ValueError('invalid credential scalar')
        return parsed
    if value.startswith("'"):
        if not value.endswith("'"):
            raise ValueError('invalid credential scalar')
        return value[1:-1].replace("''", "'")
    if value in ('null', '~', 'true', 'false'):
        return {'null': None, '~': None, 'true': True, 'false': False}[value]
    if value.isdigit():
        return int(value)
    return value


def legacy_account_configured(home):
    """Match the shipped platform provider's stored grant check, without output."""
    try:
        path = home/'.credentials.yaml'
        _owned_file(path, private=True)
        document = _mapping(path.read_text('utf-8'))
        if document.get('version') != 1 or set(document)-{'version', 'refs', 'records'}:
            return False
        grant = document.get('records', {}).get('deepseek-account-platform/default', {})
        payload = grant.get('payload', {})
        return (grant.get('kind') == 'grant' and payload.get('version') == 1
                and isinstance(payload.get('token'), str) and bool(payload['token'])
                and payload.get('issuer') == 'https://platform.deepseek.com')
    except (OSError, ValueError, TypeError, AttributeError):
        return False
