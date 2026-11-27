"""Shared bounded JSON WebSocket framing over caller-owned byte streams."""
import base64
import hashlib
import json
import os
import struct
import threading


class WebSocketMessage:
    """Read one server text message without buffering its frames or fragments."""

    def __init__(self, exact, reject, pong, *, limit=None, skip_binary=False):
        self.exact, self.reject, self.pong = exact, reject, pong
        self.limit, self.skip_binary = limit, skip_binary
        self.remaining = self.total = 0
        self.final, self.open, self.ignoring, self.done = False, False, False, False

    def recv(self, size):
        while not self.done:
            if self.remaining:
                amount = min(size, self.remaining)
                self.remaining -= amount
                data = bytes(self.exact(amount))
                self.done = self.final and self.remaining == 0
                return data
            first, second = self.exact(2)
            opcode, fin = first & 15, bool(first & 128)
            length = second & 127
            if length == 126:
                length = struct.unpack('!H', self.exact(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self.exact(8))[0]
            if first & 0x70 or second & 128 or length >= 1 << 63:
                raise self.reject('Invalid Native WebSocket frame')
            if self.limit is not None and length > self.limit:
                raise self.reject('Native WebSocket frame is too large')
            if opcode in (8, 9, 10):
                if not fin or length > 125:
                    raise self.reject('Invalid Native WebSocket control frame')
                payload = self.exact(length)
                if opcode == 8:
                    raise EOFError()
                if opcode == 9:
                    self.pong(payload)
                continue
            if opcode == 2 and self.skip_binary or opcode == 0 and self.ignoring:
                if self.open or opcode == 2 and self.ignoring:
                    raise self.reject('Interleaved Native WebSocket frame')
                self.ignoring = not fin
                while length:
                    amount = min(size, length)
                    self.exact(amount)
                    length -= amount
                continue
            if opcode not in (0, 1) or opcode == 0 and not self.open or opcode == 1 and (self.open or self.ignoring):
                raise self.reject('Invalid Native WebSocket text fragment')
            self.total += length
            if self.limit is not None and self.total > self.limit:
                raise self.reject('Native WebSocket message is too large')
            self.open, self.final, self.remaining = not fin, fin, length
            if fin and not length:
                self.done = True
        return b''


class WebSocket:
    """Minimal client-side WebSocket over the proxy's byte streams."""

    def __init__(self, reader, writer, *, path="/", host="localhost",
                 error=RuntimeError, unavailable=RuntimeError, unsupported=RuntimeError,
                 max_message_bytes=16 * 1024 * 1024, oversized=None, message_reader=None):
        self.error, self.unavailable, self.unsupported = error, unavailable, unsupported
        self.max_message_bytes = max_message_bytes
        self.message_reader = message_reader
        self.oversized = oversized or error
        if any(c in path + host for c in "\r\n") or not path.startswith("/"):
            raise ValueError("Invalid WebSocket endpoint")
        self.reader = reader
        self.writer = writer
        self.write_lock = threading.Lock()
        self.send_lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        request = (f'GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Key: ' + key + '\r\n'
                   'Sec-WebSocket-Version: 13\r\n\r\n').encode('ascii')
        self._write(request)
        header = bytearray()
        while not header.endswith(b'\r\n\r\n'):
            byte = self.reader.read(1)
            if not byte or len(header) > 8192:
                raise self.unavailable('Native control socket WebSocket handshake failed')
            header.extend(byte)
        lines = header.decode('ascii').split('\r\n')
        expected = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest()).decode('ascii')
        fields = {name.lower(): value.strip() for line in lines[1:] if ':' in line
                  for name, value in (line.split(':', 1),)}
        if ' 101 ' not in lines[0] or fields.get('sec-websocket-accept') != expected:
            raise self.unavailable('Native control socket rejected WebSocket handshake')
        # Native Unix transports advertise their effective single-frame cap.
        # Older versions use Tungstenite's 16 MiB frame / 64 MiB message defaults.
        self.max_frame_bytes = 16 * 1024 * 1024
        advertised = fields.get('x-codex-websocket-max-unfragmented-message-bytes')
        if advertised is not None:
            try:
                limit = int(advertised)
                if limit <= 0:
                    raise ValueError()
                self.max_frame_bytes = min(self.max_frame_bytes, limit)
            except ValueError as exc:
                raise self.unsupported('Invalid Native WebSocket frame limit') from exc

    def send(self, value):
        data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(data) > self.max_message_bytes:
            raise self.oversized('App-server message is too large')
        with self.send_lock:
            for offset in range(0, len(data), self.max_frame_bytes):
                fragment = data[offset:offset + self.max_frame_bytes]
                opcode = 1 if offset == 0 else 0
                final = offset + len(fragment) == len(data)
                self._data_frame(fragment, opcode, final)

    def _data_frame(self, data, opcode, final):
        mask = os.urandom(4)
        first = opcode | (0x80 if final else 0)
        if len(data) < 126:
            header = bytes((first, 0x80 | len(data)))
        elif len(data) <= 65535:
            header = bytes((first, 0xfe)) + struct.pack('!H', len(data))
        else:
            header = bytes((first, 0xff)) + struct.pack('!Q', len(data))
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self._write(header + mask + payload)

    def receive(self):
        if self.message_reader is not None:
            return self.message_reader(self.message_stream())
        return json.loads(self._receive_bytes())

    def receive_text(self):
        return self._receive_bytes().decode('utf-8')

    def message_stream(self, *, limit=None):
        return WebSocketMessage(self._exact, self.unsupported, lambda data: self._control(10, data), limit=limit,
                                skip_binary=True)

    def _receive_bytes(self):
        stream = self.message_stream(limit=64 * 1024 * 1024)
        chunks = []
        while chunk := stream.recv(1024 * 1024):
            chunks.append(chunk)
        return b''.join(chunks)

    def _exact(self, length):
        chunks = bytearray()
        while len(chunks) < length:
            data = self.reader.read(length - len(chunks))
            if not data:
                raise EOFError()
            chunks.extend(data)
        return chunks

    def _write(self, data):
        with self.write_lock:
            view = memoryview(data)
            while view:
                count = self.writer.write(view)
                if not count:
                    raise OSError('Native transport closed')
                view = view[count:]
            self.writer.flush()

    def _control(self, opcode, payload):
        if len(payload) > 125:
            raise self.unsupported('Invalid Native WebSocket control frame')
        mask = os.urandom(4)
        self._write(bytes((0x80 | opcode, 0x80 | len(payload))) + mask +
                    bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))

