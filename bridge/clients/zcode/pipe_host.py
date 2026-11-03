"""macOS ZCode pipe custodian. Only the active signed gateway for this profile may call it."""

import contextlib
import errno
import json
import os
from pathlib import Path
import secrets
import select
import socket
import stat
import struct
import subprocess
import sys
import threading
import tempfile
import time

from bridge.features.accounts.accounts import private_json
from bridge.clients.zcode.pipe_rpc import ACTIONS, MAX_FRAME, PipeTransport
from bridge.platforms.macos import zcode_pipe
from bridge.platforms.macos.desktop import commands
from bridge.platforms.macos.force_exit import Native
from bridge.platforms.macos.signed_peer import SignedGatewayPeer


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
OWNER_KEYS = ("pid", "uid", "image", "birth", "started")


def private_dir(path):
    path = Path(path).resolve(strict=True)
    meta = path.stat()
    if meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700 or not stat.S_ISDIR(meta.st_mode):
        raise PermissionError("ZCode pipe data directory is not private")
    return path


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_nlink != 1
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise PermissionError("Untrusted ZCode pipe record")
        value = stream.read(16 * 1024 + 1)
        if len(value) > 16 * 1024:
            raise ValueError("Oversized ZCode pipe record")
        return json.loads(value)


def profile_id(profile):
    st = profile.stat()
    return [st.st_dev, st.st_ino]


def exchange_read(sock, deadline):
    def exact(length):
        parts = bytearray()
        while len(parts) < length:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("ZCode private request expired")
            sock.settimeout(left)
            data = sock.recv(min(length - len(parts), 64 * 1024))
            if not data:
                raise EOFError("ZCode private peer disconnected")
            parts.extend(data)
        return parts

    size = struct.unpack("!I", exact(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("Invalid ZCode private packet size")
    return json.loads(exact(size))


def exchange_write(sock, value, timeout):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("ZCode private packet too large")
    sock.settimeout(timeout)
    sock.sendall(struct.pack("!I", len(data)))
    sock.sendall(data)


def owner(native, pid):
    state = native.identity(pid)
    if state["zombie"] or state["uid"] != os.getuid():
        raise ProcessLookupError(errno.ESRCH, "ZCode owner has exited")
    return {k: list(state[k]) if k in ("birth", "started") else state[k] for k in OWNER_KEYS}


def active_gateway(profile, signature, connection):
    # Protect against a signed gateway for a different running data profile.
    control = read_json(profile / "gateway-control.json")
    identity = signature.require(connection, control["pid"])
    expected = identity["image"] + " serve --data-dir " + str(profile)
    cmd = commands([identity["pid"]]).get(identity["pid"])
    if cmd not in (expected, expected + " --connection-secrets-stdin"):
        raise PermissionError("Gateway does not serve this data directory")
    return identity


def wait_original_exit(pid, expected):
    """Even a failed IPC listener must keep the App's descriptors until its exit."""
    native = Native()
    while True:
        try:
            with contextlib.closing(select.kqueue()) as kq:
                kq.control(
                    [
                        select.kevent(
                            pid,
                            filter=select.KQ_FILTER_PROC,
                            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                            fflags=select.KQ_NOTE_EXIT,
                        )
                    ],
                    0,
                    0,
                )
                if expected and owner(native, pid) != expected:
                    return
                events = kq.control([], 1, None)
                if events and not events[0].flags & select.KQ_EV_ERROR:
                    return
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return
        # An inconclusive OS failure must not turn into EOF in the target App.
        time.sleep(2)


class Holder:
    def __init__(self, application, launch, identity):
        self.application = Path(application).resolve(strict=True)
        self.launch = launch
        self.identity = identity
        self.fds = None
        self.owner = None
        self.pid = None
        self.transport = None

    def ensure_live(self):
        if not self.owner or owner(self.identity, self.pid) != self.owner:
            raise ValueError("ZCode 进程已变化")

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError("Invalid ZCode pipe request")
        command = request.get("command")
        if command == "initialize" and set(request) == {"command"}:
            if self.fds is None:
                # launch() checks tasks and/or refuses an existing App, verifies the signed
                # starter and App, and passes their disposable pipe ends exactly once.
                self.pid, write_fd, read_fd = self.launch()
                self.fds = (write_fd, read_fd)
            if self.owner is None:
                deadline = time.monotonic() + 4
                expected = str(self.application / "Contents/MacOS/ZCode")
                while True:
                    current = owner(self.identity, self.pid)
                    if current["image"] == expected:
                        self.owner = current
                        self.transport = PipeTransport(*self.fds, str(self.application))
                        break
                    starter = zcode_pipe.starter_path()
                    if (
                        starter is None
                        or current["image"] != str(starter / "Contents/MacOS/CMBZCodePipeStarter")
                        or time.monotonic() >= deadline
                    ):
                        raise PermissionError("ZCode 原 App 身份尚未核实")
                    time.sleep(0.04)
            self.ensure_live()
            if not self.transport:
                raise ValueError("ZCode 初始化尚未完成")
            return self.owner
        if command == "status" and set(request) == {"command"}:
            if self.fds is None:
                return {"running": False}
            self.ensure_live()
            return {"running": True, "owner": self.owner, "transportReady": self.transport is not None}
        if command != "operation" or set(request) != {"command", "owner", "native"}:
            raise ValueError("未知的 ZCode 本机请求")
        self.ensure_live()
        if request["owner"] != self.owner or not self.transport:
            raise ValueError("ZCode 原会话连接已变化")
        value = request["native"]
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("action"), str)
            or value["action"] not in ACTIONS
            or set(value) - KEYS
        ):
            raise ValueError("未知的 ZCode 原生操作")
        return self.transport.call(value)


def serve(profile, application, launch=None):
    """Internal, frozen-gateway entry point. No interface to choose exe, JS or CDP."""
    import fcntl

    profile = private_dir(profile)
    ident = profile_id(profile)
    signature = SignedGatewayPeer()
    application = Path(application).resolve(strict=True)
    if not zcode_pipe.is_supported(application):
        raise PermissionError("Unsupported signed ZCode starter")
    holder = Holder(
        application, launch or (lambda: zcode_pipe.launch(application, signature)), signature.native
    )
    lock = os.open(profile / "zcode-pipe-host.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    record = profile / "zcode-pipe-host.json"
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix="cmb-zp-", dir="/private/tmp") as temp, socket.socket(
            socket.AF_UNIX, socket.SOCK_STREAM
        ) as server, contextlib.closing(select.kqueue()) as kq:
            endpoint = str(private_dir(temp) / "rpc")
            server.bind(endpoint)
            os.chmod(endpoint, 0o600)
            server.listen(4)
            kq.control(
                [select.kevent(server.fileno(), filter=select.KQ_FILTER_READ, flags=select.KQ_EV_ADD)], 0, 0
            )
            session = {
                "pid": os.getpid(),
                "endpoint": endpoint,
                "profile": ident,
                "nonce": secrets.token_hex(32),
                "application": str(application),
            }
            private_json(record, session)
            registered = False
            until = time.monotonic() + 20
            try:
                while True:
                    if not holder.pid and time.monotonic() >= until:
                        break
                    if holder.pid and not registered:
                        kq.control(
                            [
                                select.kevent(
                                    holder.pid,
                                    filter=select.KQ_FILTER_PROC,
                                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                    fflags=select.KQ_NOTE_EXIT,
                                )
                            ],
                            0,
                            0,
                        )
                        registered = True
                    events = kq.control([], 2, None if registered else max(0, until - time.monotonic()))
                    if not events or any(ev.filter == select.KQ_FILTER_PROC for ev in events):
                        break
                    with server.accept()[0] as client:
                        try:
                            active_gateway(profile, signature, client)
                            value = exchange_read(client, time.monotonic() + 4)
                            if (
                                profile_id(profile) != ident
                                or not isinstance(value, dict)
                                or not secrets.compare_digest(str(value.get("nonce", "")), session["nonce"])
                            ):
                                raise PermissionError("Profile or connection changed")
                            result = holder.handle(value["request"])
                            if holder.owner and session.get("owner") != holder.owner:
                                session["owner"] = holder.owner
                                private_json(record, session)
                            reply = {"ok": True, "value": result}
                        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
                            reply = {"ok": False, "error": str(exc)[:600]}
                        try:
                            exchange_write(client, reply, 2)
                        except (OSError, ValueError, EOFError):
                            pass  # Action and native receipt keep their original state.
            finally:
                if holder.fds:
                    # Keep receipt/discovery and both pipe ends if the server fails.
                    wait_original_exit(holder.pid, holder.owner)
                    for fd in holder.fds:
                        os.close(fd)
                    holder.fds = None
                with contextlib.suppress(OSError, ValueError):
                    if read_json(record) == session:
                        record.unlink()
    finally:
        if holder.fds:
            wait_original_exit(holder.pid, holder.owner)
            for fd in holder.fds:
                os.close(fd)
        os.close(lock)
        signature.close()


class PipeHostClient:
    def __init__(self, profile, application):
        self.profile = Path(profile).resolve()
        self.application = Path(application).resolve()
        self.signature = None
        self._signature_lock = threading.Lock()
        self._verified_starter = False

    def _signing(self):
        with self._signature_lock:
            if self.signature is None:
                self.signature = SignedGatewayPeer()
            return self.signature

    def supported(self):
        if not zcode_pipe.is_supported(self.application):
            return False
        try:
            private_dir(self.profile)
            if not self._verified_starter:
                self._signing().verify_starter(zcode_pipe.starter_path())
                self._verified_starter = True
            return True
        except (ValueError, OSError, subprocess.SubprocessError):
            return False

    def close(self):
        if self.signature:
            self.signature.close()
            self.signature = None

    def _request(self, request, timeout=15):
        if not self.supported():
            raise ValueError("ZCode 私有会话组件不可用")
        record = read_json(self.profile / "zcode-pipe-host.json")
        if record.get("application") != str(self.application) or record["profile"] != profile_id(
            self.profile
        ):
            raise ValueError("Private ZCode profile changed")
        path = Path(record["endpoint"])
        if (
            path.name != "rpc"
            or path.parent.parent != Path("/private/tmp")
            or not path.parent.name.startswith("cmb-zp-")
        ):
            raise PermissionError("Unexpected ZCode endpoint")
        private_dir(path.parent)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(2)
            channel.connect(str(path))
            identity = self._signing().require(channel, record["pid"])
            expected = (
                identity["image"]
                + " zcode-pipe-host --data-dir "
                + str(self.profile)
                + " --application "
                + str(self.application)
            )
            if commands([identity["pid"]]).get(identity["pid"]) != expected:
                raise PermissionError("Wrong private ZCode service role")
            exchange_write(channel, {"nonce": record["nonce"], "request": request}, 3)
            reply = exchange_read(channel, time.monotonic() + timeout)
        if reply.get("ok") is not True:
            raise ValueError(reply.get("error", "ZCode private request failed"))
        return reply["value"]

    def status(self):
        return self._request({"command": "status"})

    def owner(self):
        if not self.supported():
            return None
        try:
            result = self.status()
        except (FileNotFoundError, ConnectionRefusedError):
            # A stale/unreachable holder with a live original App is an error.
            try:
                record = read_json(self.profile / "zcode-pipe-host.json")
            except FileNotFoundError:
                return None
            expected = record.get("owner")
            if expected:
                try:
                    live = owner(self._signing().native, expected["pid"])
                except ProcessLookupError:
                    live = None
                if live == expected:
                    raise ValueError("原 ZCode 正在运行，但其私有连接暂不可用")
            return None
        return result.get("owner") if result.get("running") else None

    def start(self):
        if not self.supported():
            raise ValueError("ZCode 私有会话组件不可用")
        self._start_service()
        expected = self._request({"command": "initialize"}, timeout=25)
        deadline = time.monotonic() + 10
        while True:
            try:
                ready = self.call({"expectedProcess": expected, "action": "application"})
                if ready != {"ready": True}:
                    raise ValueError("ZCode 原会话服务尚未就绪")
                return expected
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.3)

    def call(self, request):
        request = dict(request)
        expected = request.pop("expectedProcess", None)
        if not isinstance(expected, dict) or set(expected) != set(OWNER_KEYS):
            raise ValueError("ZCode 原进程身份未核验")
        if "application" in request and str(Path(request.pop("application")).resolve()) != str(
            self.application
        ):
            raise ValueError("ZCode 安装不匹配")
        request.pop("port", None)
        if set(request) - KEYS:
            raise ValueError("未知的 ZCode 原生字段")
        return self._request({"command": "operation", "owner": expected, "native": request})

    def _start_service(self):
        """Only on explicit initialization. A competing new holder exits on flock."""
        try:
            self.status()
            return
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        with open(os.devnull, "rb") as inp, open(os.devnull, "wb") as out:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "zcode-pipe-host",
                    "--data-dir",
                    str(self.profile),
                    "--application",
                    str(self.application),
                ],
                stdin=inp,
                stdout=out,
                stderr=out,
                start_new_session=True,
                close_fds=True,
            )
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                self.status()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
        raise OSError("ZCode 私有服务尚未就绪")
