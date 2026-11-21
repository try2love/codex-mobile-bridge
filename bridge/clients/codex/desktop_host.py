"""Control the App's single stdio runtime through a private Bridge endpoint.

The upstream stream is never reinitialized or resumed by a passive follower.
Bridge snapshots use its existing internal IPC representation, not App broadcasts.
"""
import copy
import hmac
import io
import json
import logging
import os
import secrets
import signal
import socket
import struct
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from .app_server import (AppServerError, AppServerRequestNotSent, AppServerUnavailable,
                         AppServerUnknownResult, response_result)
from .cli_session import CliSessions
from .desktop_stdio import DesktopStdio, MAX_LINE, METADATA_METHODS
from .ipc import DesktopIPC, IPCError, validate_stream_state
from . import desktop_runtime as runtime


CONTROL_METHODS = frozenset(('thread/loaded/list', 'thread/turns/list', 'thread/items/list',
    'turn/start', 'turn/steer', 'turn/interrupt', 'thread/settings/update', 'thread/compact/start'))


def _forward_native_output(stream, router, stopped):
    # Popen keeps stdin unbuffered; reading lines from its raw stdout would
    # issue one OS read per byte and stall all replies behind large histories.
    stream = io.BufferedReader(stream)
    while not stopped.is_set():
        line = stream.readline(MAX_LINE + 2)
        if not line:
            return
        if len(line.rstrip(b'\r\n')) > MAX_LINE:
            raise ValueError('原生桌面回执过大')
        router.from_native(json.loads(line))


class _NativeClient:
    def __init__(self, router):
        self.router = router
        self.process = object()  # Identity of this connection, never an executor.

    def request(self, method, params=None, timeout=30, replay_safe=False):
        if method == 'thread/read' and (params or {}).get('includeTurns'):
            return self.read_history(params, timeout)
        value = response_result(method, params, self.router.request_packet(method, params or {}, timeout))
        if method == 'thread/read':
            thread = value.get('thread') or {}
            if (thread.get('id') != params.get('threadId') or not thread.get('cwd') or
                    not thread.get('modelProvider') or
                    (thread.get('status') or {}).get('type') not in ('idle', 'active', 'systemError')):
                raise AppServerUnavailable('原生聊天身份或运行状态已变化')
        return value

    def read_history(self, params, timeout):
        value = self.request('thread/read', {**params, 'includeTurns': False}, timeout)
        thread = value.get('thread') or {}
        if thread.get('historyMode') != 'paginated':
            return response_result('thread/read', params, self.router.request_packet('thread/read', params, timeout))
        if thread.get('path') is None and thread.get('ephemeral') is False:
            # A newly created durable thread has no stored turns before input.
            return value

        def pages(method, extra):
            result, cursor, seen, total = [], None, set(), 0
            while True:
                page = self.request(method, {'threadId': params['threadId'], 'cursor': cursor,
                    'limit': 100, 'sortDirection': 'asc', **extra}, timeout)
                rows = page.get('data')
                if not isinstance(rows, list):
                    raise AppServerUnavailable('原生聊天历史分页格式已变化')
                total += len(json.dumps(rows, ensure_ascii=False).encode())
                if total > MAX_LINE:
                    raise AppServerUnavailable('原生聊天历史超过接入容量限制')
                result.extend(rows)
                cursor = page.get('nextCursor')
                if not cursor:
                    return result
                if cursor in seen:
                    raise AppServerUnavailable('原生聊天历史分页游标重复')
                seen.add(cursor)

        turns = pages('thread/turns/list', {'itemsView': 'notLoaded'})
        by_id = {turn['id']: {**turn, 'items': []} for turn in turns}
        for row in pages('thread/items/list', {}):
            if row.get('turnId') not in by_id or not isinstance(row.get('item'), dict):
                raise AppServerUnavailable('原生聊天历史回合归属已变化')
            by_id[row['turnId']]['items'].append(row['item'])
        return {**value, 'thread': {**thread, 'turns': list(by_id.values())}}

    def is_loaded(self, thread_id):
        cursor, seen = None, set()
        while True:
            value = self.request('thread/loaded/list', {'cursor': cursor, 'limit': 200})
            if not isinstance(value.get('data'), list):
                raise AppServerUnavailable('原生已加载聊天列表格式已变化')
            if thread_id in value['data']:
                return True
            cursor = value.get('nextCursor')
            if not cursor:
                return False
            if cursor in seen:
                raise AppServerUnavailable('原生已加载聊天列表游标重复')
            seen.add(cursor)

    def respond(self, request_id, result):
        with self.router.lock:
            request = self.router.server_requests.get(request_id)
            if request is None:
                raise AppServerRequestNotSent('原生审批已处理')
            self.router.respond(request_id, result, thread_id=request['params']['threadId'])


class HostedSessions(CliSessions):
    """Reuse native event normalization and operations, with no CLI activation."""
    def __init__(self, home, router, on_state, on_disconnect):
        super().__init__(home, on_state, on_disconnect)
        self.client = _NativeClient(router)
        self.router = router
        self.app_metadata = {}
        router.observer = self

    def attach(self, thread_id):
        # Reading saved history never grants control of a non-loaded thread.
        if not self.client.is_loaded(thread_id):
            raise AppServerRequestNotSent('no-client-found')
        value = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': False})
        thread = value.get('thread') or {}
        if (thread.get('id') != thread_id or not thread.get('cwd') or not thread.get('modelProvider') or
                (thread.get('status') or {}).get('type') not in ('idle', 'active', 'systemError')):
            raise AppServerUnavailable('无法核验原桌面聊天的配置和运行状态')
        with self.router.lock, self.lock:
            metadata = self.app_metadata.get(thread_id, {})
            metadata = {**metadata, 'cwd': thread['cwd'], 'modelProvider': thread['modelProvider'],
                        'model': thread.get('model') or metadata.get('model')}
            self.metadata[thread_id] = metadata
            self.joined[thread_id] = self.client.process
            self.requests[thread_id] = {key: value for key, value in self.router.server_requests.items()
                if value.get('params', {}).get('threadId') == thread_id}
        return self.refresh(thread_id)

    def _check_joined_identity(self, thread_id):
        super()._check_joined_identity(thread_id)
        with self.lock:
            try:
                validate_stream_state(13, self.states[thread_id])
            except (ValueError, KeyError, TypeError) as exc:
                raise AppServerRequestNotSent('原生聊天控制格式已变化，操作未发送，请更新 Bridge') from exc

    def app_response(self, request, response):
        result = response.get('result')
        if not isinstance(result, dict):
            return
        thread = result.get('thread') or {}
        thread_id = thread.get('id') if isinstance(thread, dict) else None
        if thread_id and request.get('method') in ('thread/start', 'thread/resume'):
            fields = ('model', 'reasoningEffort', 'serviceTier', 'collaborationMode')
            self.app_metadata[thread_id] = {key: copy.deepcopy(result[key]) for key in fields if key in result}
            if all(key in result for key in ('approvalPolicy', 'approvalsReviewer', 'sandbox')):
                self.app_metadata[thread_id]['permissions'] = {key: copy.deepcopy(result[key]) for key in
                    ('approvalPolicy', 'approvalsReviewer', 'sandbox', 'activePermissionProfile') if key in result}

    def native_event(self, message):
        if 'id' in message and 'method' in message:
            self._server_request(message)
        elif 'method' in message:
            self._event(message)


class _Follower:
    def __init__(self, stream, owner, token, generation, sessions, verify=lambda: None):
        self.stream, self.owner, self.token = stream, owner, token
        self.generation, self.sessions = generation, sessions
        self.verify = verify
        self.client_id = 'bridge:' + uuid.uuid4().hex
        self.following = set()
        self.pending = {}
        self.condition = threading.Condition()
        self.write_lock = threading.Lock()
        self.closed = False
        self.authenticated = False
        self.revision = 0

    def send(self, message):
        data = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode()
        if len(data) > DesktopIPC.MAX_FRAME:
            raise ValueError('桌面快照超过接入容量限制')
        with self.write_lock:
            self.stream.sendall(struct.pack('<I', len(data)) + data)

    def snapshot(self, thread_id, state):
        with self.condition:
            if thread_id in self.following and not self.closed:
                # Full snapshots can be coalesced without losing text deltas.
                self.pending[thread_id] = state
                self.condition.notify()

    def publish(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.pending)
                    if self.closed:
                        return
                    thread_id = next(iter(self.pending))
                    state = self.pending.pop(thread_id)
                    self.revision += 1
                # This is Bridge's normalized snapshot contract, independent of
                # the App version or the retired upstream desktop stream number.
                self.send({'type': 'broadcast', 'method': 'thread-stream-state-changed', 'version': 13,
                    'sourceClientId': self.owner, 'targetClientIds': [self.client_id],
                    'params': {'hostId': 'local', 'conversationId': thread_id,
                        'change': {'type': 'snapshot', 'revision': self.revision, 'conversationState': state}}})
        except (OSError, ValueError, EOFError):
            self.close()

    def handle(self, message):
        self.verify()
        method, params = message.get('method'), message.get('params') or {}
        if not self.authenticated:
            if (method != 'initialize' or message.get('type') != 'request' or
                    params.get('generation') != self.generation or
                    not isinstance(params.get('controlToken'), str) or
                    not hmac.compare_digest(params['controlToken'], self.token)):
                raise ValueError('原生桌面接入身份无效')
            self.authenticated = True
            return {'clientId': self.client_id}
        if (message.get('sourceClientId') != self.client_id or message.get('hostId', 'local') != 'local'):
            raise AppServerRequestNotSent('原生桌面接入归属不匹配')
        if method == 'native-metadata':
            native_method = params.get('method')
            if native_method not in METADATA_METHODS:
                raise AppServerRequestNotSent('原生元数据操作不受支持')
            return self.sessions.router.request_packet(native_method, params.get('params'),
                timeout=min(90, max(.01, message.get('timeoutMs', 30000) / 1000)))
        thread_id = params.get('conversationId')
        if not isinstance(thread_id, str) or not thread_id:
            raise AppServerRequestNotSent('缺少原生聊天身份')
        if method == 'thread-owner-discovery':
            if not self.sessions.client.is_loaded(thread_id):
                raise AppServerRequestNotSent('no-client-found')
            return {}
        if method == 'thread-stream-following-changed':
            if params.get('hostId') != 'local':
                raise AppServerRequestNotSent('原生桌面接入不属于此主机')
            if params.get('following') is False:
                with self.condition:
                    self.following.discard(thread_id)
                    self.pending.pop(thread_id, None)
                return {}
            targets = message.get('targetClientIds')
            if targets and targets != [self.owner]:
                raise AppServerRequestNotSent('原生桌面 owner 已变化')
            with self.condition:
                self.following.add(thread_id)
            try:
                self.sessions.attach(thread_id)
            except Exception:
                with self.condition:
                    self.following.discard(thread_id)
                raise
            return {}
        if message.get('targetClientId') != self.owner or thread_id not in self.following:
            raise AppServerRequestNotSent('原生桌面 owner 尚未核验')
        timeout = min(90, max(.01, message.get('timeoutMs', 30000) / 1000))
        if method == 'thread-follower-load-complete-history':
            self.sessions.refresh(thread_id)
            return {}
        return self.sessions.call(thread_id, method, params, timeout=timeout)

    def read(self):
        threading.Thread(target=self.publish, daemon=True).start()
        try:
            while not self.closed:
                size = struct.unpack('<I', DesktopIPC._exact(self.stream, 4))[0]
                if not 0 < size <= DesktopIPC.MAX_REQUEST:
                    raise ValueError('原生接入请求过大')
                message = json.loads(DesktopIPC._exact(self.stream, size))
                if not isinstance(message, dict):
                    raise ValueError('原生接入请求无效')
                try:
                    value = self.handle(message)
                    result = {'resultType': 'success', 'result': value}
                except (AppServerError, IPCError, ValueError, KeyError, TypeError) as exc:
                    logging.getLogger(__name__).warning('Codex hosted Desktop request rejected method=%s reason=%s',
                                                       message.get('method'), exc)
                    result = {'resultType': 'error', 'error': str(exc),
                              'notSent': isinstance(exc, AppServerRequestNotSent)}
                if message.get('type') == 'request':
                    self.send({'type': 'response', 'requestId': message['requestId'],
                               'handledByClientId': self.owner, **result})
        except (OSError, EOFError, ValueError, KeyError, TypeError):
            pass
        finally:
            self.close()

    def close(self):
        with self.condition:
            self.closed = True
            self.pending.clear()
            self.condition.notify_all()
        try:
            self.stream.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        self.stream.close()


class MetadataClient:
    def __init__(self, home):
        self.client = DesktopIPC('', codex_home=home)

    def connect(self):
        try:
            self.client.connect()
        except IPCError as exc:
            raise AppServerUnavailable(str(exc)) from exc

    def request(self, method, params, timeout=90):
        try:
            packet = self.client.request('native-metadata', {'method': method, 'params': params},
                                         timeout=timeout)['result']
        except IPCError as exc:
            if getattr(exc, 'not_sent', False):
                raise AppServerRequestNotSent(str(exc)) from exc
            raise AppServerUnknownResult(str(exc)) from exc
        return response_result(method, params, packet)

    def close(self):
        self.client.close()


def serve(config, app, host, arguments, initialize, env=None):
    """The App-owned adapter is the sole native stdin holder on both platforms."""
    home = Path(config['home'])
    root = runtime.directory(home)
    active = root / 'active.json'
    if active.exists():
        runtime.require_inactive(runtime._read(active))
    generation = str(uuid.uuid4())
    endpoint = (r'\\.\pipe\cmb-codex-desktop-' + generation if os.name == 'nt'
                else str(runtime.socket_path(home, generation)))
    token = secrets.token_hex(32)
    record = {'version': runtime.VERSION, 'ready': False, 'home': config['home'], 'cli': config['cli'],
        'app': app, 'adapter': runtime.process_identity(os.getpid()), 'runtime': None,
        'transport': 'desktop-stdio', 'socket': endpoint, 'generation': generation, 'controlToken': token}
    if host is not None:
        record['host'] = host
    runtime._write(active, json.dumps(record))
    stopped = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    output_lock, followers_lock = threading.Lock(), threading.Lock()
    followers = set()
    child = listener = None

    def emit(message):
        with output_lock:
            sys.stdout.buffer.write(json.dumps(message, ensure_ascii=False).encode() + b'\n')
            sys.stdout.buffer.flush()

    def write(data):
        remaining = memoryview(data)
        while remaining:
            count = child.stdin.write(remaining)
            if not count:
                raise EOFError('原生桌面连接已关闭')
            remaining = remaining[count:]

    def initialized(info):
        if Path(info.get('codexHome', '')).resolve() != home:
            raise ValueError('原生运行时配置目录已变化')
        # The bundled shell launcher execs the native image before initialize.
        # Its process generation must survive that image transition unchanged.
        current = runtime.process_identity(child.pid)
        keys = ('pid', 'parent', 'created') if os.name == 'nt' else ('pid', 'parent', 'started', 'uid', 'ruid')
        if (any(current.get(key) != record['runtime'].get(key) for key in keys) or
                os.name != 'nt' and current['birth'][0] != record['runtime']['birth'][0]):
            raise ValueError('原生运行时在初始化期间已替换')
        record['runtime'] = current
        runtime.verify_record(record)
        record['ready'] = True
        runtime._write(active, json.dumps(record))

    router = DesktopStdio(write, emit, initialized, control_methods=CONTROL_METHODS)

    def publish(thread_id, state):
        validate_stream_state(13, state)
        with followers_lock:
            targets = list(followers)
        for follower in targets:
            follower.snapshot(thread_id, state)

    def disconnected(thread_id=None):
        with followers_lock:
            targets = list(followers)
        for follower in targets:
            if thread_id is None or thread_id in follower.following:
                follower.close()

    sessions = HostedSessions(home, router, publish, disconnected)

    def verify():
        try:
            if runtime._read(active) != record or not router.ready:
                raise ValueError('原生桌面接入记录已变化')
            runtime.verify_record(record)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AppServerRequestNotSent('原生桌面接入归属已变化，请重新连接') from exc

    def output():
        try:
            _forward_native_output(child.stdout, router, stopped)
        except (OSError, ValueError, AppServerError) as exc:
            logging.getLogger(__name__).warning('Codex native Desktop transport stopped: %s', exc)
        finally:
            stopped.set()

    def input_loop():
        from .desktop_runtime_host import _forward_input
        from types import SimpleNamespace
        class Input:
            # Do not hold CPython's stdin buffer lock in a daemon at App EOF.
            # Native exit may happen while the App still holds the pipe open.
            pending = bytearray()

            def readline(self, limit):
                while True:
                    end = self.pending.find(b'\n')
                    if end >= 0 or len(self.pending) >= limit:
                        count = min(end + 1 if end >= 0 else limit, limit)
                        result = bytes(self.pending[:count])
                        del self.pending[:count]
                        return result
                    data = os.read(sys.stdin.fileno(), 65536)
                    if not data:
                        result = bytes(self.pending)
                        self.pending.clear()
                        return result
                    self.pending.extend(data)
        try:
            router.from_app(initialize)
            _forward_input(Input(), SimpleNamespace(send=router.from_app), emit, stopped)
        except (OSError, ValueError, AppServerError):
            pass
        finally:
            stopped.set()

    def accept():
        while not stopped.is_set():
            try:
                stream = listener.accept()
                if os.name == 'nt':
                    from bridge.platforms.windows import local_service
                    connection = stream
                    try:
                        local_service.require_peer(connection, server=True)
                        stream = local_service.byte_stream(connection, duplicate=True)
                    finally:
                        connection.close()
                else:
                    stream, _ = stream
                follower = _Follower(stream, 'desktop-runtime:' + generation, token, generation, sessions, verify)
                with followers_lock:
                    followers.difference_update([item for item in followers if item.closed])
                    if len(followers) >= 8:
                        follower.close()
                        continue
                    followers.add(follower)
                threading.Thread(target=follower.read, daemon=True).start()
            except (OSError, EOFError, ValueError):
                return

    try:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        child = subprocess.Popen([config['cli'], *arguments], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, **options)
        record['runtime'] = runtime.process_identity(child.pid)
        if record['runtime']['parent'] != os.getpid():
            raise ValueError('原生运行时不属于当前桌面适配器')
        for key in ('app', 'host', 'adapter'):
            if key in record and runtime.process_identity(record[key]['pid']) != record[key]:
                raise ValueError('原生桌面启动归属已变化')
        runtime._write(active, json.dumps(record))
        if os.name == 'nt':
            from bridge.platforms.windows.local_service import listener as open_listener
            listener = open_listener(endpoint)
        else:
            runtime.private_directory(Path(endpoint).parent)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(endpoint)
            os.chmod(endpoint, 0o600)
            listener.listen(8)
        threading.Thread(target=accept, daemon=True).start()
        threading.Thread(target=output, daemon=True).start()
        threading.Thread(target=input_loop, daemon=True).start()
        stopped.wait()
    finally:
        stopped.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        router.close()
        sessions._disconnect()
        if listener is not None:
            listener.close()
        if child is not None:
            child.stdin.close()
            try:
                child.wait(timeout=25)
            except subprocess.TimeoutExpired:
                return 1  # Preserve the record while this writer still exists.
        if active.exists() and runtime._read(active) == record:
            active.unlink()
        if os.name != 'nt':
            Path(endpoint).unlink(missing_ok=True)
    return child.returncode if child is not None else 1
