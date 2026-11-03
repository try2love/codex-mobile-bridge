"""Keep an original Windows or Linux ZCode alive across gateway reconnections."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
from multiprocessing.connection import Client, Listener

from bridge.clients.zcode.pipe_rpc import ACTIONS, KEYS, MAX_FRAME, PipeTransport
from bridge.clients.zcode.transport import operation_timeout


def platform_pipe():
    if sys.platform == 'win32':
        from bridge.platforms.windows import zcode_pipe
    elif sys.platform == 'linux':
        from bridge.platforms.linux import zcode_pipe
    else:
        raise ValueError('此平台没有该私有连接')
    return zcode_pipe


def gateway_command():
    command = [sys.executable]
    if not getattr(sys, 'frozen', False):
        command.append(str(Path(__file__).resolve().parents[3]/'desktop.py'))
    return command


def holder_args(profile, application):
    return ['zcode-pipe-host', '--data-dir', str(profile), '--application', str(application)]


def address(profile):
    # Linux abstract sockets disappear with their listener; bind also elects a
    # single holder. Windows uses a local-only, first-instance named pipe.
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(profile)))).hexdigest()
    return (r'\\.\pipe\cmb-zcode-' if sys.platform == 'win32' else '\0cmb-zcode-') + key


def require_peer(conn, profile, application, *, server):
    native = platform_pipe()
    if sys.platform == 'win32':
        pid = native.peer_pid(conn, server=server)
        argv = native.process_args(pid)
    else:
        with socket.fromfd(conn.fileno(), socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise PermissionError('私有连接用户不匹配')
        argv = [os.fsdecode(arg) for arg in (Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')[:-1]]
    owner = native.identity(pid)
    prefix = gateway_command()
    if owner['image'] != native.identity(os.getpid())['image']:
        raise PermissionError('私有连接的网关程序不匹配')
    # An OS-verified executable and the exact serving role are both required.
    expected = prefix[1:] + holder_args(profile, application)
    if server:
        control = json.loads((profile/'gateway-control.json').read_text(encoding='utf-8'))
        if control['pid'] != pid:
            raise PermissionError('仅允许当前资料目录的运行中网关接入')
        expected = prefix[1:] + ['serve', '--data-dir', str(profile)]
        if argv[-1:] == ['--connection-secrets-stdin']:
            expected.append('--connection-secrets-stdin')
    if argv[1:] != expected:
        raise PermissionError('私有连接的程序角色或资料目录不匹配')


def receive(conn, timeout):
    if not conn.poll(timeout):
        raise TimeoutError('ZCode 私有连接等待超时，请核对原操作回执')
    return json.loads(conn.recv_bytes(MAX_FRAME))


def send(conn, value):
    message = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    if len(message) > MAX_FRAME:
        raise ValueError('ZCode 私有请求超出大小限制')
    conn.send_bytes(message)


class LocalPipeClient:
    def __init__(self, profile, application):
        self.profile = Path(profile).resolve()
        self.application = Path(application).resolve()

    def supported(self):
        return platform_pipe().is_supported(self.application)

    def close(self):
        # The holder belongs to the original App, not this gateway.
        pass

    def _request(self, message, timeout=15):
        endpoint = address(self.profile)
        conn = (platform_pipe().local_connect(endpoint) if sys.platform == 'win32'
                else Client(endpoint, family='AF_UNIX'))
        with conn:
            require_peer(conn, self.profile, self.application, server=False)
            send(conn, message)
            reply = receive(conn, timeout)
            if reply.get('ok') is not True:
                raise ValueError(reply.get('error', 'ZCode 私有连接失败'))
            return reply['value']

    def owner(self):
        try:
            return self._request({'command': 'status'})
        except (FileNotFoundError, ConnectionRefusedError):
            return None

    def start(self):
        try:
            return self._request({'command': 'initialize'}, timeout=30)
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        options = ({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                   if sys.platform == 'win32' else {'start_new_session': True})
        proc = subprocess.Popen(gateway_command() + holder_args(self.profile, self.application),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True, **options)
        deadline = time.monotonic() + 6
        while True:
            try:
                return self._request({'command': 'initialize'}, timeout=30)
            except (ConnectionRefusedError, FileNotFoundError):
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('无法启动本机 ZCode 私有连接')
                time.sleep(.1)

    def call(self, request):
        native = dict(request)
        owner = native.pop('expectedProcess')
        return self._request({'command': 'operation', 'owner': owner, 'native': native},
                             timeout=operation_timeout(native.get('action')) + 3)


def serve(profile, application):
    native = platform_pipe()
    profile, application = Path(profile).resolve(strict=True), Path(application).resolve(strict=True)
    if not native.is_supported(application):
        raise ValueError('未找到此安装的 ZCode')
    endpoint = address(profile)
    listener = (native.local_listener(endpoint) if sys.platform == 'win32'
                else Listener(endpoint, family='AF_UNIX', backlog=4))
    started = threading.Event()
    launched = []
    initializing = threading.Lock()
    closing = threading.Event()
    state = {}

    def dispatch(message):
        if message == {'command': 'initialize'} and not launched:
            with initializing:
                if closing.is_set():
                    raise ValueError('ZCode 私有连接已关闭')
                launched.extend(native.launch(application))
                started.set()  # Keep both ends until the original App exits.
        if launched:
            pid = launched[0]
            owner = native.identity(pid)
            if os.path.normcase(owner['image']) != os.path.normcase(str(application)):
                raise ValueError('原 ZCode 进程身份不匹配')
            if not state:
                transport = (PipeTransport(None, None, str(application), stream=launched[1]) if sys.platform == 'win32'
                             else PipeTransport(launched[1], launched[2], str(application)))
                state.update(owner=owner, transport=transport)
            if state['owner'] != owner:
                raise ValueError('ZCode 原进程已变化')
        if message == {'command': 'status'}:
            return state.get('owner') if state and not state['transport'].broken else None
        if message == {'command': 'initialize'}:
            if state['transport'].broken:
                raise ValueError('ZCode 私有管道已中断，请在应用管理中重新初始化连接')
            return state['owner']
        if (not state or not isinstance(message, dict) or set(message) != {'command', 'owner', 'native'}
                or message['command'] != 'operation' or message['owner'] != state['owner']):
            raise ValueError('ZCode 原会话连接已变化')
        request = message['native']
        if not isinstance(request, dict) or set(request) - KEYS or request.get('action') not in ACTIONS:
            raise ValueError('未知的 ZCode 原生操作')
        return state['transport'].call(request)

    def handle_requests():
        while True:
            with listener.accept() as conn:
                try:
                    require_peer(conn, profile, application, server=True)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                try:
                    result = dispatch(receive(conn, 4))
                    reply = {'ok': True, 'value': result}
                except (OSError, EOFError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                    reply = {'ok': False, 'error': str(exc)[:600]}
                with contextlib.suppress(OSError, EOFError, ValueError):
                    send(conn, reply)

    with contextlib.closing(listener):
        worker = threading.Thread(target=handle_requests, daemon=True)
        worker.start()
        if not started.wait(45):
            with initializing:
                if not launched:
                    closing.set()
                    return
        # Wait on OS process exit; an idle holder consumes no polling budget.
        if sys.platform == 'win32':
            launched[1].process.wait()
            launched[1].close()
        else:
            os.waitpid(launched[0], 0)
            os.close(launched[1])
            os.close(launched[2])
