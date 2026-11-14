"""The desktop's local, length-prefixed IPC. No agent process is launched here."""
import json
import logging
import socket
import struct
import threading
import time
import uuid
from concurrent.futures import Future, TimeoutError

from bridge.clients.codex.transport import connect_stream, endpoint_identity


class IPCError(Exception):
    pass


class DesktopIPC:
    # Desktop snapshots include the loaded history and can exceed 64 MiB.
    # Keep a bounded receive budget without enlarging outbound operations.
    MAX_FRAME = 256 * 1024 * 1024
    MAX_REQUEST = 64 * 1024 * 1024
    READ_CHUNK = 1024 * 1024
    VERSIONS = {
        "initialize": 0,
        "thread-owner-discovery": 1,
        "thread-follower-start-turn": 2,
        "thread-follower-edit-last-user-turn": 2,
        "thread-follower-update-thread-settings": 2,
        "thread-follower-load-complete-history": 1,
        "thread-follower-compact-thread": 1,
        "thread-follower-steer-turn": 1,
        "thread-follower-interrupt-turn": 4,
        "thread-follower-command-approval-decision": 1,
        "thread-follower-file-approval-decision": 1,
        "thread-follower-permissions-request-approval-response": 1,
        "thread-follower-submit-user-input": 1,
        "thread-follower-submit-mcp-server-elicitation-response": 1,
        "thread-stream-following-changed": 1,
    }

    def __init__(self, path, on_event=None, on_disconnect=None):
        self.path = str(path)
        self.on_event = on_event or (lambda event: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.socket = None
        self.client_id = None
        self.endpoint_identity = None
        self.pending = {}
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.connect_lock = threading.Lock()
        self.event_lock = threading.RLock()
        self.generation = 0

    def reconnect(self):
        """Replace only this follower's transport; never stop the native owner."""
        with self.connect_lock:
            self.close()
        self.connect()

    def endpoint_changed(self):
        """Report a replacement, without interrupting any pending operation."""
        current = endpoint_identity(self.path)
        with self.lock:
            return bool(self.socket and self.endpoint_identity is not None and
                        current is not None and current != self.endpoint_identity)

    def connect(self, *, deadline=None):
        with self.connect_lock:
            if self.client_id and self.socket:
                return
            with self.lock:
                generation = self.generation
            try:
                identity = endpoint_identity(self.path)
                if deadline is None:
                    sock = connect_stream(self.path)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise IPCError("桌面读取超时；尚未取得实时会话状态。")
                    sock = connect_stream(self.path, timeout=min(5, remaining))
            except OSError as exc:
                raise IPCError("桌面 App 未连接，请打开 Codex App。") from exc
            with self.lock:
                if generation != self.generation or deadline is not None and time.monotonic() >= deadline:
                    sock.close()
                    raise IPCError("桌面读取超时；尚未取得实时会话状态。")
                self.socket = sock
                self.endpoint_identity = identity
            threading.Thread(target=self._read_loop, args=(sock,), daemon=True).start()
            try:
                result = self.request("initialize", {"clientType": "codex-mobile-bridge"}, timeout=5, deadline=deadline)
                with self.lock:
                    if (generation != self.generation or self.socket is not sock or
                            deadline is not None and time.monotonic() >= deadline):
                        raise IPCError("桌面读取超时；尚未取得实时会话状态。")
                    self.client_id = result["result"]["clientId"]
            except Exception:
                self.close()
                raise

    def _send(self, message):
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) > self.MAX_REQUEST:
            raise IPCError("消息过大")
        with self.write_lock:
            sock = self.socket
            if not sock:
                raise IPCError("桌面连接已断开")
            try:
                sock.sendall(struct.pack("<I", len(data)) + data)
            except OSError as exc:
                raise IPCError("桌面连接已断开；请检查会话后再决定是否重发。") from exc

    def request(self, method, params, target=None, host="local", timeout=15, *, deadline=None):
        if method not in self.VERSIONS:
            raise IPCError("不支持的桌面操作")
        if deadline is not None:
            timeout = min(timeout, max(0, deadline - time.monotonic()))
            if timeout <= 0:
                raise IPCError("桌面读取超时；尚未取得实时会话状态。")
        request_id = str(uuid.uuid4())
        future = Future()
        with self.lock:
            self.pending[request_id] = future
        version = self.VERSIONS[method]
        message = {"type": "request", "requestId": request_id,
                   "sourceClientId": self.client_id, "method": method,
                   "version": version, "params": params, "timeoutMs": int(timeout * 1000)}
        if target:
            message["targetClientId"] = target
        if host != "local":
            message["hostId"] = host
            if method.startswith("thread-follower-"):
                message["version"] += 1
        try:
            self._send(message)
            # Lifecycle audits share an absolute deadline. The ordinary mutation
            # path retains its acknowledgement grace and unknown-result handling.
            wait = timeout + 1 if deadline is None else min(timeout, max(0, deadline - time.monotonic()))
            response = future.result(timeout=wait)
            if response.get("resultType") != "success":
                raise IPCError(response.get("error", "桌面请求未完成"))
            return response
        except TimeoutError as exc:
            if method in ("initialize", "thread-owner-discovery", "thread-follower-load-complete-history"):
                raise IPCError("桌面读取超时；尚未取得实时会话状态。") from exc
            raise IPCError("桌面响应超时；操作可能已提交，请查看会话后再决定是否重发。") from exc
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def owner(self, session_id, host="local"):
        self.connect()
        response = self.request("thread-owner-discovery", {
            "hostId": host, "conversationId": session_id}, timeout=6)
        return response["handledByClientId"]

    def follow(self, session_id, owner, enabled=True, host="local"):
        message = {"type": "broadcast", "sourceClientId": self.client_id,
                   "method": "thread-stream-following-changed", "version": 1,
                   "params": {"hostId": host, "conversationId": session_id, "following": enabled}}
        if owner:
            message["targetClientIds"] = [owner]
        self._send(message)

    def invalidate_attached_threads(self, parent_id):
        """Refresh native metadata after a confirmed mutation, using our identity."""
        uuid.UUID(parent_id)
        self.connect()
        self._send({'type': 'broadcast', 'sourceClientId': self.client_id,
                    'method': 'query-cache-invalidate', 'version': 0,
                    'params': {'queryKey': ['attached-side-chats', 'local', parent_id], 'reset': False}})

    @staticmethod
    def _exact(sock, length):
        chunks = bytearray()
        while len(chunks) < length:
            chunk = sock.recv(min(length - len(chunks), DesktopIPC.READ_CHUNK))
            if not chunk:
                raise EOFError()
            chunks.extend(chunk)
        return chunks

    def _read_loop(self, sock):
        try:
            while True:
                length = struct.unpack("<I", self._exact(sock, 4))[0]
                if not 0 < length <= self.MAX_FRAME:
                    logging.getLogger(__name__).warning(
                        "Codex desktop IPC frame rejected: bytes=%s limit=%s", length, self.MAX_FRAME)
                    raise IPCError("桌面 IPC 帧长度不兼容")
                message = json.loads(self._exact(sock, length))
                if message.get("type") == "response":
                    with self.lock:
                        if self.socket is not sock:
                            return
                        future = self.pending.get(message.get("requestId"))
                        if future and not future.done():
                            future.set_result(message)
                elif message.get("type") == "client-discovery-request":
                    with self.event_lock:
                        if self.socket is sock:
                            self._send({"type": "client-discovery-response",
                                        "requestId": message["requestId"], "response": {"canHandle": False}})
                elif message.get("type") == "broadcast":
                    with self.event_lock:
                        targets = message.get("targetClientIds")
                        if self.socket is sock and (not targets or self.client_id in targets):
                            self.on_event(message)
        except (OSError, EOFError, ValueError, IPCError):
            pass
        finally:
            self._disconnect(sock)

    def close(self):
        with self.lock:
            self.generation += 1
            sock = self.socket
        self._disconnect(sock)

    def _disconnect(self, sock):
        if sock is None:
            return
        # Interrupt sendall before taking locks a blocked writer may hold.
        # Socket identity below still prevents an old reader clearing a replacement.
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        with self.event_lock:
            with self.lock:
                disconnected = self.socket is sock
                if disconnected:
                    self.socket = None
                    self.client_id = None
                    self.endpoint_identity = None
                    for future in self.pending.values():
                        if not future.done():
                            future.set_exception(IPCError("桌面连接已断开"))
            if disconnected:
                self.on_disconnect()
