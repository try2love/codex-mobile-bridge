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

# Revision 4 before bounded native history pages, LF and Windows CRLF.
PREVIOUS_HISTORY_SOURCES = ('7ff9ce99e26c3b1b9e54ade4db4a634976096f3a429b6f4d81e7401d3e067f8f',
                            '2bd8e0711db16fe5c2d2a39f473f8e0f747e5f9067dae64572129c56ff0e7136')

# Revision 4 before explicit new project directories, LF and Windows CRLF.
PREVIOUS_PROJECT_SOURCES = ('2f71a8c64cdf798843386310c359e41b57c3b2b26130f448f97c6603b4fa5762',
                            '2ad4a7923d1d124d2c2c94a9f46e15ab45ef4e2b91a937abe45e0bdf0616831a')

# Revision 4 shipped with the WSL/project preview, before native workspace
# registration and forks. Keep both source encodings during an explicit upgrade.
PREVIOUS_WORKSPACE_SOURCES = ('b7f3c05ec967e736897f068b9da2820f7ce98a151a12051691e641540174e640',
                              '0d673a9d13fb7f32da07f36ff34e45d1fd48de555cb20661e4ab8192b1427582')

# Revision 5 before reversible native archive and guarded activity semantics.
PREVIOUS_ARCHIVE_SOURCES = ('0b7d749e831f181c7b0ea9ad9d4bc6fd5858355c8facb52e1a5e3e6a8c0fce8d',
                            '0f3e7b0962a8f1a9de1a9333afaa74316c83ab6cad18b820a132b8179d3e592e')


# Revision 6 before explicitly confirmed stop-before-archive, LF and CRLF.
PREVIOUS_DISCONNECT_SOURCES = ('b8b05cd4d93af60913b505c842dc6b74d2860199980bab079fe2b4e17e153752',
                               '95451e5ff26ba7e0e5f139a1109ceeaaf3dba1a51b169d3e5d5207d5ef29873b')


# Revision 7 before per-message model metadata, LF and Windows CRLF.
PREVIOUS_MESSAGE_MODEL_SOURCES = ('74c6a26a7a8eca9a6dfe5e6256f595a174c58b3645d3bd15497c9877d1422938',
                                  '740533827d3817803605c490bd95f3ffbff1bc841cf14f99820ebcf0db14ab4b')

# Revision 8 before guarded native rename and project membership, LF and CRLF.
PREVIOUS_SESSION_METADATA_SOURCES = ('78b87c8f6f5b4633067d4c75fd50b8f006042bc1dc0aba26130e662748006911',
                                     '87ab2a8dedbde1661c1f325b9371711704caaa140e3e4209ae71801d1c78bf6d')

# Revision 9 predecessors remain usable; history-image reads require the newer host.
COMPATIBLE_OFFICIAL_SOURCES = ('1608bf0b7b4c1f6e2927a1103467b3c56dfaef4d80c6821309dca03fe2e2d9f5',
                               'bb6a664b33c21d6195ec158a029fed76a121e989bc2ea46168e6e3c9130035c1',
                               '19a45ae394f380e69c8b0a8b31e9014efb435276520f33bc871c410164942839',
                               'eb507bba209d203b392249d022d69fe875a59514234df43552c70839ea1038ad',
                               '31ee82718e131d43c60202261d531195af9257e9a375ce0c6b9ff4edf5de8d5a',
                               '5479ae9f1e78bd7bf7ce61478a6427025b47964593dbc830b0845a1ef4f0da5a',
                               '32fd056cd0840884610a223a41bb690c4f3e244c3af7bdb7fc9c65c84d19b3db',
                               'f3ce3350a32ece209415418e798e2a5d348276081bd15c671552456c18576bb5',
                               '14ca377eecc7b714e9c059b026181164c3cb21166d315a6d9ef9d16327bda815',
                               '9c8da5a761588f5f91d5696e938ff2bd927412a2e56367e08aeabeabc6a42ec2',
                               '80cc10b76e18621b90dbdd5675ce8dd3932eb381a3380f0a538a76b4bcc77ebf',
                               '2ffbe404b5251cd5fb26da5ffa44a8f3ba4ed2c3c91f80c57ad9f299d62c0741',
                               # Last revision 9 shipped before creation model selection (d75c61e).
                               '7c10ac8e3b2849ef6cb72dd7db7a32ef2755572cd1978a5f7eaaa0cf05680aad',
                               '2714358b35f126daed913e0f73bd52bf585038d831f68e1b09642050f7f923ef',
                               # Revision 10 Test predecessor, before optional service lookup was corrected.
                               '992442ed888adaf535b6893ec2f8672d7a281569d0e4778e511c392d93571a8f',
                               '5bd8ece728f7cc4aeb96d775dbc6d6d79631e48d8aae4d70cfeea36906e69e2f',
                               # Revision 11 before credential provenance (c057e6b), LF and CRLF.
                               '9dfb9fe8eaba3f5f162b9177f6a9d551f2899f6316f9ee94bf82fbc5c59089d6',
                               'd9b394e55b8436209ead3a953da0d374cb08f2454848718db0eb6a59e7ee9a1f')

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
        if quoted and not escaped and char in '\r\n':
            folded = re.match(r'(?:\r?\n[ \t]*)+', text[index:])
            if folded:
                while chars[-1] in ' \t':
                    chars.pop()
                # YAML folds one break to a space; empty lines retain line feeds.
                breaks = folded[0].count('\n')
                chars.append(' ' if breaks == 1 else '\\n'*(breaks-1))
                index += folded.end()
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
        current_source = Path(__file__).with_name('host.mjs').read_bytes()
        current_lf = current_source.replace(b'\r\n', b'\n')
        current_sources = {hashlib.sha256(content).hexdigest() for content in
                           (current_lf, current_lf.replace(b'\n', b'\r\n'))}
        current_sources.update(COMPATIBLE_OFFICIAL_SOURCES)
        if digest not in current_sources and digest not in (
                LEGACY_SOURCE, PREVIOUS_SOURCE, PREVIOUS_CLEANUP_SOURCE, PREVIOUS_CATALOG_SOURCE,
                *PREVIOUS_QUIT_SOURCES, *PREVIOUS_CONTEXT_SOURCES, *PREVIOUS_HISTORY_SOURCES,
                *PREVIOUS_PROJECT_SOURCES, *PREVIOUS_WORKSPACE_SOURCES, *PREVIOUS_ARCHIVE_SOURCES,
                *PREVIOUS_DISCONNECT_SOURCES, *PREVIOUS_MESSAGE_MODEL_SOURCES, *PREVIOUS_SESSION_METADATA_SOURCES):
            return None
        return directory, digest not in current_sources
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
