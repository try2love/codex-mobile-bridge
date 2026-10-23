"""Restricted RPC client for an already running shared local app-server.

The caller supplies a verified byte stream. This module never launches Codex,
exposes arbitrary RPC, or changes auth/config. Explicit subscription is gated by
fresh loaded-thread evidence in the entry adapter.
"""
import base64
import hashlib
import json
import os
import struct
import socket
import threading
from concurrent.futures import Future, TimeoutError

from bridge.clients.codex.ipc import IPCError


class SharedServer:
    LIMIT = 64 * 1024 * 1024
    METHODS = frozenset({
        'thread/list', 'thread/read', 'thread/loaded/list', 'thread/turns/list',
        'thread/items/list', 'thread/resume', 'thread/start',
        'turn/start', 'turn/steer', 'turn/interrupt', 'thread/settings/update',
        'thread/name/set', 'model/list', 'config/read',
    })

    def __init__(self, stream, event=lambda value: None):
        self.stream = stream
        self.event = event
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.pending = {}
        self.counter = 0
        self.closed = False
        self.reader = None

    def start(self):
        try:
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            self.stream.sendall(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                'Connection: Upgrade\r\nSec-WebSocket-Key: ' + key +
                '\r\nSec-WebSocket-Version: 13\r\n\r\n').encode('ascii'))
            header = bytearray()
            while not header.endswith(b'\r\n\r\n'):
                if len(header) >= 16384:
                    raise IPCError('WSL 控制通道握手过大')
                header.extend(self.exact(1))
            lines = bytes(header).decode('ascii').split('\r\n')
            fields = {k.lower(): v.strip() for line in lines[1:] if ':' in line for k, v in [line.split(':', 1)]}
            expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            if (lines[0] != 'HTTP/1.1 101 Switching Protocols' or fields.get('sec-websocket-accept') != expected
                    or fields.get('upgrade', '').lower() != 'websocket'
                    or 'upgrade' not in fields.get('connection', '').lower()):
                raise IPCError('WSL 控制通道身份握手失败')
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            self._request('initialize', {'clientInfo': {'name': 'codex_mobile_bridge_wsl', 'version': '0.2'},
                                        'capabilities': {'experimentalApi': True}}, 10)
            self.write({'method': 'initialized'})
            if hasattr(self.stream, 'settimeout'):
                self.stream.settimeout(None)
            return self
        except Exception:
            self.close()
            raise

    def exact(self, size):
        data = bytearray()
        while len(data) < size:
            block = self.stream.recv(size - len(data))
            if not block:
                raise EOFError('WSL 控制通道已断开')
            data.extend(block)
        return bytes(data)

    def frame(self, data, opcode=1):
        if len(data) > self.LIMIT:
            raise IPCError('WSL 控制消息过大')
        size = len(data)
        head = bytes([0x80 | opcode, 0x80 | size]) if size < 126 else (
            bytes([0x80 | opcode, 0x80 | 126]) + struct.pack('!H', size) if size < 65536
            else bytes([0x80 | opcode, 0x80 | 127]) + struct.pack('!Q', size))
        mask = os.urandom(4)
        with self.write_lock:
            if self.closed:
                raise IPCError('WSL 控制通道已断开')
            try:
                self.stream.sendall(head + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data)))
            except OSError as exc:
                raise IPCError('WSL 写入结果未确认，请检查原会话，不要重复提交') from exc

    def write(self, value):
        self.frame(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))

    def request(self, method, params, timeout=20):
        if method not in self.METHODS:
            raise ValueError('不支持的 WSL 会话操作')
        return self._request(method, params, timeout)

    def _request(self, method, params, timeout):
        with self.lock:
            if self.closed:
                raise IPCError('WSL 控制通道已断开')
            self.counter += 1
            identifier = self.counter
            future = self.pending[identifier] = Future()
        try:
            self.write({'id': identifier, 'method': method, 'params': params})
            response = future.result(timeout=timeout)
            if 'error' in response:
                error = IPCError('WSL Codex 拒绝此操作，请检查原会话状态和版本')
                error.rpc_error = response['error']
                raise error
            return response.get('result') or {}
        except TimeoutError as exc:
            raise IPCError('WSL 操作超时，写入结果未确认；请检查原会话') from exc
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    def _read(self):
        fragments = bytearray()
        fragmented = False
        try:
            while not self.closed:
                first, second = self.exact(2)
                opcode = first & 15
                size = second & 127
                if first & 0x70 or second & 128:
                    raise ValueError('Invalid WebSocket frame')
                if size == 126:
                    size = struct.unpack('!H', self.exact(2))[0]
                elif size == 127:
                    size = struct.unpack('!Q', self.exact(8))[0]
                if size > self.LIMIT or len(fragments) + size > self.LIMIT:
                    raise ValueError('Frame too large')
                if opcode >= 8 and (not first & 128 or size > 125):
                    raise ValueError('Invalid control frame')
                data = self.exact(size)
                if opcode == 8:
                    break
                if opcode == 9:
                    self.frame(data, 10)
                    continue
                if opcode == 10:
                    continue
                if opcode == 1 and not fragmented:
                    fragments = bytearray(data)
                elif opcode == 0 and fragmented:
                    fragments.extend(data)
                else:
                    raise ValueError('Invalid message fragment')
                fragmented = not bool(first & 128)
                if fragmented:
                    continue
                value = json.loads(fragments)
                fragments.clear()
                if not isinstance(value, dict):
                    raise ValueError('Invalid RPC message')
                if 'method' in value:
                    self.event(value)
                else:
                    with self.lock:
                        future = self.pending.get(value.get('id'))
                    if future is not None and not future.done():
                        future.set_result(value)
        except (EOFError, OSError, ValueError, IPCError):
            pass
        finally:
            with self.lock:
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(IPCError('WSL 控制通道已断开，操作结果待核对'))
            self.event({'method': 'bridge/disconnected'})
            self.close()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        if hasattr(self.stream, 'shutdown'):
            try:
                self.stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.stream.close()
        if self.reader is not None and threading.current_thread() is not self.reader:
            self.reader.join(timeout=3)
