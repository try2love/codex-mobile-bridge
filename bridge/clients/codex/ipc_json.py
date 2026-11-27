"""Incremental decoding for Codex's length-prefixed JSON stream."""
import codecs
import hashlib
import json
import re
import tempfile
import threading
from bisect import bisect_right


_STRING = re.compile(rb'["\\\x00-\x1f]')
_NUMBER = re.compile(rb'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?\Z')
_SPACE = b' \r\n\t'
# Fields read as identifiers, routing/permission metadata or file evidence
# retain their original values. Other history leaves are presentation bodies.
_ITEM_METADATA = frozenset({
    'type', 'kind', 'id', 'turnId', 'itemId', 'call_id', 'clientId', 'client_id',
    'clientUserMessageId', 'status', 'phase', 'timestamp', 'path', 'mimeType',
    'tool', 'toolName', 'server', 'name', 'model', 'effort', 'project_id',
    'attached_thread_parent_id', 'diff', 'unified_diff',
})


class StoredString(str):
    """A private history leaf. The ordinary JSON representation is a preview."""

    def __new__(cls, preview, storage, offset, size, chars, digest, index):
        value = super().__new__(cls, preview)
        value.storage = storage
        value.offset = offset
        value.size = size
        value.chars = chars
        value.digest = digest
        value.index = index
        return value

    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self

    def read(self, limit):
        if self.size > limit:
            return None
        return self.slice(0, self.chars)

    def slice(self, start, end):
        result = []
        character, pos = self.index[bisect_right(self.index, (start, self.size)) - 1]
        decoder = codecs.getincrementaldecoder('utf-8')('surrogatepass')
        while pos < self.size and character < end:
            with self.storage.lock:
                self.storage.file.seek(self.offset + pos)
                raw = self.storage.file.read(min(64 * 1024, self.size - pos))
            pos += len(raw)
            data = decoder.decode(raw, final=pos == self.size)
            if character + len(data) > start:
                result.append(data[max(0, start - character):end - character])
            character += len(data)
        return ''.join(result)


class _Storage:
    def __init__(self):
        self.lock = threading.Lock()
        self.file = tempfile.TemporaryFile()
        self.end = 0

    def __del__(self):
        if hasattr(self, 'file'):
            self.file.close()


class FrameReader:
    """Retain the original JSON container paths for revisions and patches."""

    def __init__(self, sock, length, chunk, *, native_rpc=False):
        self.sock, self.remaining, self.chunk = sock, length, chunk
        self.buffer, self.pos = b'', 0
        self.storage = None
        self.native_rpc = native_rpc

    def peek(self):
        if self.pos == len(self.buffer) and self.remaining != 0:
            limit = self.chunk if self.remaining is None else min(self.remaining, self.chunk)
            self.buffer = self.sock.recv(limit)
            if self.remaining is not None:
                if not self.buffer:
                    raise EOFError()
                self.remaining -= len(self.buffer)
            elif not self.buffer:
                self.remaining = 0
            self.pos = 0
        return self.buffer[self.pos:self.pos + 1]

    def take(self):
        byte = self.peek()
        self.pos += bool(byte)
        return byte

    def space(self):
        byte = self.peek()
        while byte and byte in _SPACE:
            self.pos += 1
            byte = self.peek()
        return byte

    def string(self, retain=False):
        parts = []
        offset, size, chars = None, 0, 0
        digest = None
        index = [(0, 0)]
        decoder = codecs.getincrementaldecoder('utf-8')()
        high_surrogate = None

        def consume(text):
            nonlocal offset, size, chars, digest
            if not text:
                return
            previous_chars = chars
            chars += len(text)
            if offset is None:
                parts.append(text)
                if not retain or chars <= 4096:
                    return
                if retain == 'data-url' and not ''.join(part[:5] for part in parts[:5]).startswith('data:'):
                    return
                if self.storage is None:
                    self.storage = _Storage()
                offset = self.storage.end
                digest = hashlib.sha256()
                text = ''.join(parts)
                parts[:] = [text[:1024]]
            if size - index[-1][1] >= 64 * 1024:
                index.append((previous_chars, size))
            chunk = text.encode('utf-8', errors='surrogatepass')
            with self.storage.lock:
                self.storage.file.seek(offset + size)
                self.storage.file.write(chunk)
                self.storage.end = offset + size + len(chunk)
            size += len(chunk)
            digest.update(chunk)

        def flush_high():
            nonlocal high_surrogate
            if high_surrogate is not None:
                consume(chr(high_surrogate))
                high_surrogate = None

        while self.peek():
            match = _STRING.search(self.buffer, self.pos)
            end = match.start() if match else len(self.buffer)
            if end != self.pos:
                flush_high()
                consume(decoder.decode(self.buffer[self.pos:end]))
            self.pos = end
            if not match:
                continue
            consume(decoder.decode(b'', final=True))
            marker = self.take()
            if marker == b'"':
                flush_high()
                if offset is None:
                    return ''.join(parts)
                return StoredString(parts[0], self.storage, offset, size, chars, digest.hexdigest(), index)
            if marker != b'\\':
                raise ValueError('Unescaped JSON control character')
            escape = self.take()
            if escape == b'u':
                digits = b''.join(self.take() for _ in range(4))
                if len(digits) != 4 or any(c not in b'0123456789abcdefABCDEF' for c in digits):
                    raise ValueError('Invalid JSON Unicode escape')
                code = int(digits, 16)
                if high_surrogate is not None and 0xdc00 <= code <= 0xdfff:
                    consume(chr(0x10000 + ((high_surrogate - 0xd800) << 10) + code - 0xdc00))
                    high_surrogate = None
                else:
                    flush_high()
                    if 0xd800 <= code <= 0xdbff:
                        high_surrogate = code
                    else:
                        consume(chr(code))
            elif escape and escape in b'"\\/bfnrt':
                flush_high()
                consume(json.loads(b'"' + marker + escape + b'"'))
            else:
                raise ValueError('Invalid JSON escape')
        raise ValueError('Unterminated JSON string')

    def retain_decoded(self, value, path=()):
        """Apply history-leaf storage to an already bounded decoded record."""
        if isinstance(value, dict):
            return {key: self.retain_decoded(part, (*path, key)) for key, part in value.items()}
        if isinstance(value, list):
            return [self.retain_decoded(part, (*path, index)) for index, part in enumerate(value)]
        if not isinstance(value, str) or len(value) <= 4096:
            return value
        retain = self.history_leaf(path)
        if not retain or retain == 'data-url' and not value.startswith('data:'):
            return value
        if self.storage is None:
            self.storage = _Storage()
        raw = value.encode('utf-8', errors='surrogatepass')
        with self.storage.lock:
            offset = self.storage.end
            self.storage.file.seek(offset)
            self.storage.file.write(raw)
            self.storage.end += len(raw)
        return StoredString(value[:1024], self.storage, offset, len(raw), len(value),
                            hashlib.sha256(raw).hexdigest(), [(0, 0)])

    def history_leaf(self, path):
        if path[:1] == ('pending-patch-value',):
            return True
        snapshot = ('params', 'change', 'conversationState')
        if self.native_rpc:
            if path[:2] == ('result', 'thread'):
                path = (*snapshot, *path[2:])
            elif path[:2] == ('result', 'turn'):
                path = (*snapshot, 'turns', 0, *path[2:])
            elif path[:2] == ('result', 'data') and len(path) >= 4 and isinstance(path[2], int):
                if path[3] == 'item':
                    path = (*snapshot, 'turns', 0, 'items', path[2], *path[4:])
                elif path[3] == 'items':
                    path = (*snapshot, 'turns', path[2], *path[3:])
            elif path[:2] == ('params', 'item'):
                path = (*snapshot, 'turns', 0, 'items', 0, *path[2:])
            elif path[:2] == ('params', 'turn'):
                path = (*snapshot, 'turns', 0, *path[2:])
        if path[:3] != snapshot or path[3:4] not in (('turns',), ('turnHistory',)):
            return False
        if 'items' not in path and 'input' not in path:
            return False
        if path[-1] in ('url', 'imageUrl', 'image_url'):
            return 'data-url'
        return path[-1] not in _ITEM_METADATA

    @staticmethod
    def patch_target(value):
        if isinstance(value, str):
            value = [part.replace('~1', '/').replace('~0', '~') for part in value.split('/')[1:]]
        if isinstance(value, (list, tuple)) and all(type(part) in (str, int) for part in value):
            return ('params', 'change', 'conversationState', *value)
        return ()

    def resolve_patch_value(self, value, path):
        if isinstance(value, StoredString):
            retain = self.history_leaf(path)
            if retain and (retain != 'data-url' or value.startswith('data:')):
                return value
            return value.read(value.size)
        if isinstance(value, dict):
            return {key: self.resolve_patch_value(part, path + (key,)) for key, part in value.items()}
        if isinstance(value, list):
            return [self.resolve_patch_value(part, path + (i,)) for i, part in enumerate(value)]
        return value

    def value(self, path=(), depth=0):
        if depth > 128:
            raise ValueError('JSON nesting exceeds IPC decoder budget')
        marker = self.space()
        if marker == b'"':
            self.take()
            return self.string(self.history_leaf(path))
        if marker in (b'{', b'['):
            obj = marker == b'{'
            result = {} if obj else []
            patch = obj and path[:3] == ('params', 'change', 'patches') and len(path) == 4
            self.take()
            close = b'}' if obj else b']'
            if self.space() == close:
                self.take()
                return result
            while True:
                if obj:
                    if self.space() != b'"':
                        raise ValueError('Expected a JSON property')
                    self.take()
                    key = self.string()
                    if self.space() != b':':
                        raise ValueError('Expected a JSON colon')
                    self.take()
                    child = path + (key,)
                    if patch and key == 'value':
                        child = ('pending-patch-value',)
                    result[key] = self.value(child, depth + 1)
                else:
                    result.append(self.value(path + (len(result),), depth + 1))
                self.space()
                separator = self.take()
                if separator == close:
                    if patch and 'value' in result:
                        result['value'] = self.resolve_patch_value(result['value'], self.patch_target(result.get('path')))
                    return result
                if separator != b',':
                    raise ValueError('Expected a JSON separator')
        atom = bytearray()
        while marker and marker not in b',]}' + _SPACE and len(atom) <= 128:
            atom.extend(self.take())
            marker = self.peek()
        if atom not in (b'true', b'false', b'null') and not _NUMBER.fullmatch(atom):
            raise ValueError('Invalid JSON scalar')
        return json.loads(atom)

    def read(self, *, allow_eof=False):
        if allow_eof and not self.peek():
            return None
        message = self.value()
        if not isinstance(message, dict) or self.space():
            raise ValueError('Invalid JSON frame')
        return message


class JsonLine:
    """Expose exactly one JSONL record to the incremental reader."""

    def __init__(self, stream):
        self.stream = stream
        self.finished = False

    def recv(self, size):
        if self.finished:
            return b''
        data = self.stream.readline(size)
        self.finished = not data or data.endswith(b'\n')
        return data


def has_stored_text(value):
    if isinstance(value, StoredString):
        return True
    if isinstance(value, dict):
        return any(has_stored_text(part) for part in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_stored_text(part) for part in value)
    return False


def iter_wire_json(value):
    """Serialize native frames without creating a frame-sized bytes object."""
    if isinstance(value, str):
        yield b'"'
        size = value.chars if isinstance(value, StoredString) else len(value)
        for offset in range(0, size, 64 * 1024):
            end = min(size, offset + 64 * 1024)
            part = value.slice(offset, end) if isinstance(value, StoredString) else value[offset:end]
            yield json.dumps(part, ensure_ascii=False)[1:-1].encode(errors='backslashreplace')
        yield b'"'
    elif isinstance(value, dict):
        yield b'{'
        for index, (key, part) in enumerate(value.items()):
            if index:
                yield b','
            yield from iter_wire_json(key)
            yield b':'
            yield from iter_wire_json(part)
        yield b'}'
    elif isinstance(value, (list, tuple)):
        yield b'['
        for index, part in enumerate(value):
            if index:
                yield b','
            yield from iter_wire_json(part)
        yield b']'
    else:
        yield json.dumps(value).encode()


def json_body(value):
    """Keep exact projected JSON bodies retrievable by the bounded detail API."""
    if not has_stored_text(value):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return _store_text(chunk.decode('utf-8') for chunk in iter_wire_json(value))


def join_text(parts):
    """Join native text blocks without collapsing stored leaves into previews."""
    if len(parts) == 1:
        return parts[0]
    if not any(isinstance(part, StoredString) for part in parts):
        return '\n'.join(parts)

    def chunks():
        for index, part in enumerate(parts):
            if index:
                yield '\n'
            stored = isinstance(part, StoredString)
            size = part.chars if stored else len(part)
            for offset in range(0, size, 64 * 1024):
                end = min(size, offset + 64 * 1024)
                yield part.slice(offset, end) if stored else part[offset:end]

    return _store_text(chunks())


def _store_text(chunks):
    storage = _Storage()
    index, preview = [(0, 0)], ''
    digest = hashlib.sha256()
    chars = size = 0
    for text in chunks:
        chunk = text.encode('utf-8', errors='surrogatepass')
        if len(preview) < 1024:
            preview += text[:1024 - len(preview)]
        if size - index[-1][1] >= 64 * 1024:
            index.append((chars, size))
        storage.file.write(chunk)
        digest.update(chunk)
        chars += len(text)
        size += len(chunk)
    storage.end = size
    return StoredString(preview, storage, 0, size, chars, digest.hexdigest(), index)
