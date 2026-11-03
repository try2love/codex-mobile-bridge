"""Private DevTools pipe transport for the isolated ZCode prototype."""

import json
import math
import os
import secrets
import select
import threading
import time

from bridge.clients.zcode.installation import renderer_url
from bridge.clients.zcode.transport import bind_workspace_registry, expression, needs_workspace_registry, operation_timeout


# The shared adapter accepts files up to 20 MiB, including their base64 encoding.
MAX_FRAME = 64 * 1024 * 1024
KEYS = frozenset(
    (
        "action",
        "anchorSessionId",
        "binding",
        "body",
        "commandId",
        "expectedRuntime",
        "expiresAt",
        "sessionId",
        "workspace",
    )
)
ACTIONS = frozenset(
    (
        "application",
        "projects",
        "target",
        "account",
        "lifecycle",
        "attach",
        "detach",
        "list",
        "create",
        "detail",
        "history",
        "receipt",
        "tasks",
        "stop-task",
        "catalog",
        "send",
        "stop",
        "respond",
        "settings",
        "access",
        "work-mode",
        "goal",
        "compact",
        "fork",
        "edit-message",
        "rename",
    )
)


def remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("ZCode pipe request timed out; check the original receipt")
    return left


class QuitUnsupported(ValueError):
    pass


class PipeTransport:
    """Single-reader transaction stream. Its caller owns both file descriptors."""

    def __init__(self, to_app, from_app, application, *, stream=None):
        self.stream = stream
        self.to_app = to_app
        self.from_app = from_app
        self.window_url = renderer_url(application)
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._next_id = secrets.randbelow(1 << 30) + 1
        self._broken = False
        if stream is None:
            os.set_blocking(to_app, False)
            os.set_blocking(from_app, False)

    @property
    def broken(self):
        return self._broken

    def _write(self, data, deadline):
        if len(data) > MAX_FRAME:
            raise ValueError("ZCode pipe request too large")
        packet = memoryview(data + b"\0")
        written = 0
        try:
            while written < len(packet):
                if self.stream is not None:
                    count = self.stream.write(packet[written:], deadline)
                else:
                    if not select.select([], [self.to_app], [], remaining(deadline))[1]:
                        raise TimeoutError("ZCode pipe write timed out")
                    try:
                        count = os.write(self.to_app, packet[written:])
                    except BlockingIOError:
                        continue
                if not count:
                    raise EOFError("ZCode pipe closed")
                written += count
        except BaseException:
            if written or self.stream is not None:
                # Another command cannot safely follow an incomplete frame.
                self._broken = True
            raise

    def _read(self, deadline):
        searched = 0
        while True:
            end = self._buffer.find(0, searched)
            if end >= 0:
                if end > MAX_FRAME:
                    self._broken = True
                    raise ValueError("ZCode pipe reply too large")
                raw = self._buffer[:end]
                del self._buffer[: end + 1]
                return json.loads(raw)
            searched = len(self._buffer)
            if searched > MAX_FRAME:
                self._broken = True
                raise ValueError("ZCode pipe reply too large")
            if self.stream is not None:
                chunk = self.stream.read(64 * 1024, deadline)
            else:
                if not select.select([self.from_app], [], [], remaining(deadline))[0]:
                    raise TimeoutError("ZCode pipe reply timed out")
                try:
                    chunk = os.read(self.from_app, 64 * 1024)
                except BlockingIOError:
                    continue
            if not chunk:
                self._broken = True
                raise EOFError("ZCode pipe closed")
            self._buffer.extend(chunk)

    def _rpc(self, method, params, deadline, session=None):
        remaining(deadline)
        if self._broken or self._next_id >= (1 << 31):
            raise ValueError("ZCode pipe must be reopened and verified")
        seq = self._next_id
        self._next_id += 1
        query = {"id": seq, "method": method, "params": params}
        if session:
            query["sessionId"] = session
        self._write(json.dumps(query, ensure_ascii=False, separators=(",", ":")).encode(), deadline)
        while True:
            remaining(deadline)
            reply = self._read(deadline)
            if not isinstance(reply, dict) or reply.get("id") != seq or reply.get("sessionId") != session:
                continue
            if reply.get("error"):
                if method == "Browser.close" and reply["error"].get("code") == -32601:
                    raise QuitUnsupported("ZCode 没有提供原生退出命令")
                raise ValueError("ZCode DevTools: " + str(reply["error"].get("message"))[:400])
            return reply["result"]

    def _with_window(self, request, deadline):
        rows = self._rpc("Target.getTargets", {}, deadline)["targetInfos"]
        matches = [
            row
            for row in rows
            if row.get("type") == "page"
            and row.get("url", "").split("?")[0].split("#")[0] == self.window_url
        ]
        if not matches and request["action"] == "application":
            return {"ready": False, "stage": "window"}
        if len(matches) != 1 or not matches[0].get("targetId"):
            raise ValueError("原 ZCode 窗口尚未就绪或无法唯一定位")
        session = self._rpc(
            "Target.attachToTarget", {"targetId": matches[0]["targetId"], "flatten": True}, deadline
        )["sessionId"]
        if not isinstance(session, str) or not session:
            raise ValueError("原 ZCode 窗口没有返回调试会话")

        def page(method, params):
            if method == "Runtime.evaluate":
                guard = json.dumps(self.window_url)
                value = params["expression"]
                params = {
                    **params,
                    "expression": '(() => { if (location.href.split("?")[0].split("#")[0] !== '
                    + guard
                    + ') throw Error("原窗口地址已变化"); return '
                    + value
                    + "; })()",
                }
            return self._rpc(method, params, deadline, session)

        try:
            if needs_workspace_registry(request):
                bind_workspace_registry(page, request)
            result = page(
                "Runtime.evaluate",
                {"expression": expression(request), "awaitPromise": True, "returnByValue": True},
            )
            if result.get("exceptionDetails"):
                detail = result["exceptionDetails"]
                raise ValueError(
                    str(detail.get("exception", {}).get("description") or detail.get("text"))[:1000]
                )
            return result["result"].get("value")
        finally:
            # This releases the temporary DevTools target only, never the native
            # conversation lease. Preserve an accepted reply if cleanup fails.
            try:
                self._rpc("Target.detachFromTarget", {"sessionId": session}, time.monotonic() + 0.5)
            except (OSError, EOFError, ValueError, KeyError):
                pass

    def call(self, request):
        if not isinstance(request, dict) or request.get("action") not in ACTIONS:
            raise ValueError("未知的 ZCode 原生操作")
        now = time.time()
        limit = operation_timeout(request["action"])
        expiry = request.get("expiresAt", int((now + limit) * 1000))
        if type(expiry) not in (int, float) or not math.isfinite(expiry):
            raise ValueError("ZCode request deadline invalid")
        deadline = time.monotonic() + min(limit, (expiry / 1000) - now)
        try:
            if not self._lock.acquire(timeout=remaining(deadline)):
                raise TimeoutError("ZCode pipe is busy")
            try:
                return self._with_window(
                    {**request, "expiresAt": min(expiry, int((now + limit) * 1000))}, deadline
                )
            finally:
                self._lock.release()
        except TimeoutError:
            if request["action"] == "application" and not self.broken:
                return {"ready": False, "stage": "waiting"}
            raise

    def request_quit(self):
        # Called only through the separately authorized lifecycle operation.
        deadline = time.monotonic() + 5
        if not self._lock.acquire(timeout=remaining(deadline)):
            raise TimeoutError("ZCode 管道正忙，未提交退出")
        try:
            self._rpc("Browser.close", {}, deadline)
        finally:
            self._lock.release()

    def probe(self):
        value = self.call({"action": "application"})
        if not isinstance(value, dict) or value.get("ready") is not True:
            raise ValueError("ZCode 原 App 会话服务尚未就绪")
        return value
