"""The desktop's local, length-prefixed IPC. No agent process is launched here."""
import json
import socket
import struct
import threading
import uuid
from concurrent.futures import Future, TimeoutError

from bridge.clients.codex.transport import connect_stream


class IPCError(Exception):
    pass


class DesktopIPC:
    MAX_FRAME = 64 * 1024 * 1024
    VERSIONS = {
        "initialize": 0,
        "thread-owner-discovery": 1,
        "thread-follower-start-turn": 2,
        "thread-follower-edit-last-user-turn": 2,
        "thread-follower-update-thread-settings": 2,
        "thread-follower-load-complete-history": 1,
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
        self.pending = {}
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.connect_lock = threading.Lock()

    def connect(self):
        with self.connect_lock:
            if self.client_id and self.socket:
                return
            try:
                sock = connect_stream(self.path)
            except OSError as exc:
                raise IPCError("桌面 App 未连接，请打开 Codex App。") from exc
            self.socket = sock
            threading.Thread(target=self._read_loop, args=(sock,), daemon=True).start()
            try:
                result = self.request("initialize", {"clientType": "codex-mobile-bridge"}, timeout=5)
                self.client_id = result["result"]["clientId"]
            except Exception:
                self.close()
                raise

    def _send(self, message):
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) > self.MAX_FRAME:
            raise IPCError("消息过大")
        with self.write_lock:
            sock = self.socket
            if not sock:
                raise IPCError("桌面连接已断开")
            try:
                sock.sendall(struct.pack("<I", len(data)) + data)
            except OSError as exc:
                raise IPCError("桌面连接已断开；请检查会话后再决定是否重发。") from exc

    def request(self, method, params, target=None, host="local", timeout=15):
        if method not in self.VERSIONS:
            raise IPCError("不支持的桌面操作")
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
            response = future.result(timeout=timeout + 1)
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

    @staticmethod
    def _exact(sock, length):
        chunks = bytearray()
        while len(chunks) < length:
            chunk = sock.recv(length - len(chunks))
            if not chunk:
                raise EOFError()
            chunks.extend(chunk)
        return chunks

    def _read_loop(self, sock):
        try:
            while True:
                length = struct.unpack("<I", self._exact(sock, 4))[0]
                if not 0 < length <= self.MAX_FRAME:
                    raise IPCError("桌面 IPC 帧长度不兼容")
                message = json.loads(self._exact(sock, length))
                if message.get("type") == "response":
                    with self.lock:
                        future = self.pending.get(message.get("requestId"))
                        if future and not future.done():
                            future.set_result(message)
                elif message.get("type") == "client-discovery-request":
                    self._send({"type": "client-discovery-response",
                                "requestId": message["requestId"], "response": {"canHandle": False}})
                elif message.get("type") == "broadcast":
                    targets = message.get("targetClientIds")
                    if not targets or self.client_id in targets:
                        self.on_event(message)
        except (OSError, EOFError, ValueError, IPCError):
            pass
        finally:
            disconnected = False
            with self.lock:
                if self.socket is sock:
                    disconnected = True
                    self.socket = None
                    self.client_id = None
                    for future in self.pending.values():
                        if not future.done():
                            future.set_exception(IPCError("桌面连接已断开"))
            if disconnected:
                self.on_disconnect()
            sock.close()

    def close(self):
        sock = self.socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
