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
from bridge.clients.codex.ipc_json import FrameReader
from bridge.clients.websocket import WebSocketMessage


class SharedServer:
    LIMIT = 64 * 1024 * 1024
    METHODS = frozenset({
        'thread/unsubscribe', 'thread/list', 'thread/read', 'thread/loaded/list', 'thread/turns/list',
        'thread/items/list', 'thread/resume', 'thread/start', 'thread/compact/start',
        'thread/backgroundTerminals/list', 'thread/backgroundTerminals/clean',
        'turn/start', 'turn/steer', 'turn/interrupt', 'thread/settings/update',
        'thread/name/set', 'thread/archive', 'thread/unarchive', 'model/list', 'config/read',
        'skills/list', 'configRequirements/read', 'permissionProfile/list',
        'experimentalFeature/list', 'account/read', 'thread/goal/get', 'thread/goal/set', 'thread/goal/clear',
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
        self.server_info = None

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
            self.server_info = self._request('initialize', {'clientInfo': {'name': 'codex_mobile_bridge_wsl', 'version': '0.2'},
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
                if method in ('thread/archive', 'thread/unarchive'):
                    from bridge.features.sessions.create import ArchiveRejected
                    raise ArchiveRejected(response['error'])
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
        try:
            while not self.closed:
                stream = WebSocketMessage(self.exact, ValueError, lambda data: self.frame(data, 10))
                value = FrameReader(stream, None, 1024 * 1024, native_rpc=True).read()
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
