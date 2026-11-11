"""One native Desktop stdio connection, with bounded metadata calls from Bridge.

The App remains the sole initializer and recipient of notifications/approvals.
Only request IDs are translated; no execution settings or responses are replayed.
"""
import json
import secrets
import threading
from concurrent.futures import Future, TimeoutError

from .app_server import (AppServerUnavailable, AppServerUnknownResult, AppServerRequestNotSent,
                         AppServerMessageTooLarge)


MAX_LINE = 64 * 1024 * 1024
METADATA_METHODS = frozenset(('thread/fork', 'thread/list', 'thread/inject_items', 'thread/name/set',
                              'thread/read', 'thread/backgroundTerminals/list', 'thread/delete'))


class DesktopStdio:
    def __init__(self, write, emit, initialized):
        self.write, self.emit, self.initialized = write, emit, initialized
        self.lock = threading.RLock()
        self.pending = {}
        self.prefix, self.sequence = secrets.token_hex(16) + ':', 0
        self.closed = False
        self.ready = False
        self.handshake = None

    def _send(self, value):
        data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n'
        if len(data) > MAX_LINE + 1:
            raise AppServerMessageTooLarge('原生请求超过消息大小限制')
        self.write(data)

    def _identifier(self):
        self.sequence += 1
        return self.prefix + str(self.sequence)

    def from_app(self, message):
        with self.lock:
            if self.closed:
                raise AppServerUnavailable('原生桌面连接已关闭')
            value = message
            if 'method' in message and 'id' in message:
                identifier = self._identifier()
                self.pending[identifier] = ('app', message['id'], message.get('method'))
                value = {**message, 'id': identifier}
            try:
                self._send(value)
            except Exception:
                if value is not message:
                    self.pending.pop(value['id'], None)
                raise
            if message.get('method') == 'initialized' and self.handshake is not None:
                self.initialized(self.handshake)
                self.ready = True

    def from_native(self, message):
        # Native requests use their own ID namespace. App approval responses go
        # back unchanged and never collide with a translated outbound request.
        if 'method' in message or 'id' not in message:
            self.emit(message)
            return
        with self.lock:
            pending = self.pending.pop(message['id'], None)
            if pending is None:
                return  # A late Bridge reply must never appear in the App.
            if pending[0] == 'bridge':
                pending[1].set_result(message)
                return
            _, identifier, method = pending
            if method == 'initialize' and 'result' in message:
                self.handshake = message['result']
        self.emit({**message, 'id': identifier})

    def request_packet(self, method, params, timeout=90):
        if method not in METADATA_METHODS or not isinstance(params, dict):
            raise AppServerRequestNotSent('此连接仅支持原生聊天元数据操作')
        future = Future()
        with self.lock:
            if self.closed or not self.ready:
                raise AppServerRequestNotSent('原生桌面连接尚未就绪')
            identifier = self._identifier()
            self.pending[identifier] = ('bridge', future)
            try:
                self._send({'id': identifier, 'method': method, 'params': params})
            except AppServerMessageTooLarge as exc:
                self.pending.pop(identifier, None)
                raise AppServerRequestNotSent(str(exc)) from exc
            except Exception:
                self.pending.pop(identifier, None)
                raise
        try:
            response = future.result(timeout=timeout)
        except TimeoutError as exc:
            raise AppServerUnknownResult('原生操作结果尚未确认，不会重复提交') from exc
        finally:
            with self.lock:
                self.pending.pop(identifier, None)
        if 'result' not in response and 'error' not in response:
            raise AppServerUnknownResult('原生操作回执无效，不会重复提交')
        return response

    def close(self):
        with self.lock:
            self.closed = True
            self.ready = False
            for pending in self.pending.values():
                if pending[0] == 'bridge':
                    pending[1].set_exception(AppServerUnknownResult('原生连接已关闭，操作结果尚未确认'))
            self.pending.clear()
