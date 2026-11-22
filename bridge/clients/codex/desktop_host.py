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
import tempfile
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from .app_server import (AppServerError, AppServerRequestNotSent, AppServerUnavailable,
                         AppServerUnknownResult, response_result)
from .cli_session import CliSessions
from .desktop_stdio import DesktopStdio, MAX_LINE, METADATA_METHODS
from .ipc_json import FrameReader, JsonLine, iter_wire_json
from .ipc import DesktopIPC, IPCError, validate_stream_state
from . import desktop_runtime as runtime


CONTROL_METHODS = frozenset(('thread/loaded/list', 'thread/turns/list', 'thread/items/list',
    'turn/start', 'turn/steer', 'turn/interrupt', 'thread/settings/update', 'thread/compact/start',
    'thread/revert', 'thread/rollback'))


def _forward_native_output(stream, router, stopped):
    # Popen keeps stdin unbuffered; reading lines from its raw stdout would
    # issue one OS read per byte and stall all replies behind large histories.
    with io.BufferedReader(stream) as buffered:
        while not stopped.is_set():
            reader = FrameReader(JsonLine(buffered), None, DesktopIPC.READ_CHUNK, native_rpc=True)
            message = reader.read(allow_eof=True)
            if message is None:
                return
            router.from_native(message)


HISTORY_TURN_LIMIT = 40


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

        # Retain only previously observed turns confirmed by native headers.
        page = self.request('thread/turns/list', {'threadId': params['threadId'],
            'cursor': None, 'limit': HISTORY_TURN_LIMIT, 'sortDirection': 'desc', 'itemsView': 'notLoaded'}, timeout)
        turns = page.get('data')
        if not isinstance(turns, list) or len(turns) > HISTORY_TURN_LIMIT:
            raise AppServerUnavailable('原生聊天历史分页格式已变化')
        headers = turns
        more = bool(page.get('nextCursor'))
        if turns:
            turn = turns[0]
            page = self.request('thread/items/list', {'threadId': params['threadId'],
                'turnId': turn['id'], 'cursor': None, 'limit': 100, 'sortDirection': 'desc'}, timeout)
            if not isinstance(page.get('data'), list) or len(page['data']) > 100:
                raise AppServerUnavailable('原生聊天历史分页格式已变化')
            items = []
            for row in reversed(page['data']):
                if row.get('turnId') != turn['id'] or not isinstance(row.get('item'), dict):
                    raise AppServerUnavailable('原生聊天历史回合归属已变化')
                items.append(row['item'])
            more = more or bool(page.get('nextCursor'))
            turns = [{**turn, 'items': items, '_itemsComplete': not page.get('nextCursor')}]
        return {**value, 'thread': {**thread, 'turns': turns, '_historyHeaders': headers,
            'turnsPagination': {'hasLoadedOldest': not more}}}

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
        super().__init__(home, self._mark_dirty, on_disconnect)
        self.publish_state = on_state
        self.client = _NativeClient(router)
        self.router = router
        self.app_metadata = {}
        self.subscribers = {}
        self.editing = set()
        self.event_condition = threading.Condition()
        self.dirty = set()
        self.event_closed = False
        router.observer = self
        self.event_worker = threading.Thread(target=self._process_events, daemon=True,
                                             name='desktop-observation')
        self.event_worker.start()

    def retain(self, thread_id, subscriber):
        with self.lock:
            subscribers = self.subscribers.setdefault(thread_id, set())
            subscribers.add(subscriber)
        try:
            return self.attach(thread_id, _subscribers=subscribers)
        except Exception:
            self.release(thread_id, subscriber)
            raise

    def release(self, thread_id, subscriber):
        with self.lock:
            subscribers = self.subscribers.get(thread_id, set())
            subscribers.discard(subscriber)
            if not subscribers:
                self.subscribers.pop(thread_id, None)
                # Local observation cleanup is not a native disconnect. In
                # particular a failed cold-chat follow shares its socket with
                # already connected chats and must not close that transport.
                self._invalidate(thread_id, notify=False)

    def attach(self, thread_id, *, _subscribers=None):
        # Reading saved history never grants control of a non-loaded thread.
        if not self.client.is_loaded(thread_id):
            raise AppServerRequestNotSent('no-client-found')
        value = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': False})
        thread = value.get('thread') or {}
        if (thread.get('id') != thread_id or not thread.get('cwd') or not thread.get('modelProvider') or
                (thread.get('status') or {}).get('type') not in ('idle', 'active', 'systemError')):
            raise AppServerUnavailable('无法核验原桌面聊天的配置和运行状态')
        with self.router.lock, self.lock:
            if _subscribers is not None and self.subscribers.get(thread_id) is not _subscribers:
                raise AppServerRequestNotSent('原生聊天订阅已关闭')
            metadata = self.app_metadata.get(thread_id, {})
            metadata = {**metadata, 'cwd': thread['cwd'], 'modelProvider': thread['modelProvider'],
                        'model': thread.get('model') or metadata.get('model')}
            self.metadata[thread_id] = metadata
            self.joined[thread_id] = self.client.process
            self.requests[thread_id] = {key: value for key, value in self.router.server_requests.items()
                if value.get('params', {}).get('threadId') == thread_id}
        return self.refresh(thread_id)

    def _state(self, thread, requests, metadata):
        state = CliSessions._state(thread, requests, metadata)
        if 'turnsPagination' in thread:
            state['turnsPagination'] = dict(thread['turnsPagination'])
        for original, turn in zip(thread.get('turns', []), state['turns']):
            if original.get('_itemsComplete') is False:
                turn['_itemsComplete'] = False
        if '_historyHeaders' in thread:
            previous = {turn['turnId']: turn for turn in
                        (self.states.get(thread['id']) or {}).get('turns', [])}
            retained = []
            size = len(json.dumps(state, ensure_ascii=False).encode())
            # Reuse only the contiguous, native-confirmed tail on this connection.
            # Saved history never supplies items, runtime status or approvals here.
            for header in thread['_historyHeaders'][1:]:
                prior = previous.get(header['id'])
                if prior is None:
                    break
                row = {**copy.deepcopy(prior), 'status': header.get('status'), 'error': header.get('error')}
                if header.get('startedAt') is not None:
                    row['turnStartedAtMs'] = header['startedAt'] * 1000
                if header.get('completedAt') is not None:
                    row['turnCompletedAtMs'] = header['completedAt'] * 1000
                if header.get('model'):
                    row['params'] = {'model': header['model'], 'effort': header.get('reasoningEffort')}
                size += len(json.dumps(row, ensure_ascii=False).encode()) + 1
                if size > MAX_LINE:
                    break
                retained.append(row)
            state['turns'] = list(reversed(retained)) + state['turns']
            state['turnsPagination']['hasLoadedOldest'] &= (
                len(state['turns']) == len(thread['_historyHeaders']) and
                all(turn.get('_itemsComplete') is not False for turn in state['turns']))
        return state

    def _check_joined_identity(self, thread_id):
        super()._check_joined_identity(thread_id)
        with self.lock:
            try:
                validate_stream_state(13, self.states[thread_id])
            except (ValueError, KeyError, TypeError) as exc:
                raise AppServerRequestNotSent('原生聊天控制格式已变化，操作未发送，请更新 Bridge') from exc

    def call(self, thread_id, desktop_method, params, timeout=30):
        if desktop_method != 'thread-follower-edit-last-user-turn':
            return super().call(thread_id, desktop_method, params, timeout)
        self._check_joined_identity(thread_id)
        thread = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': False})['thread']
        if thread['status']['type'] != 'idle' or self.requests.get(thread_id):
            raise AppServerRequestNotSent('请先停止当前任务，再编辑消息')
        paginated = thread.get('historyMode') == 'paginated'
        if paginated:
            turns = self.client.request('thread/turns/list', {'threadId': thread_id,
                'cursor': None, 'limit': 1, 'sortDirection': 'desc', 'itemsView': 'notLoaded'})['data']
        else:
            turns = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': True})['thread']['turns'][-1:]
        if (len(turns) != 1 or turns[0].get('id') != params.get('turnId') or
                turns[0].get('status') == 'inProgress' or not isinstance(params.get('message'), str)):
            raise AppServerRequestNotSent('最近一条消息已变化，请刷新后重新编辑')
        if paginated:
            rows = self.client.request('thread/items/list', {'threadId': thread_id, 'turnId': params['turnId'],
                'cursor': None, 'limit': 100, 'sortDirection': 'asc'})['data']
            if any(row.get('turnId') != params['turnId'] for row in rows):
                raise AppServerRequestNotSent('原生消息归属已变化')
            items = [row['item'] for row in rows]
        else:
            items = turns[0].get('items') or []
        users = [item for item in items if item.get('type') == 'userMessage']
        if len(users) != 1 or not isinstance(users[0].get('content'), list):
            raise AppServerRequestNotSent('无法确认原始输入，请在桌面编辑此消息')
        inputs = copy.deepcopy(users[0]['content'])
        text = next((item for item in inputs if item.get('type') == 'text'), None)
        if text is None:
            inputs.insert(0, {'type': 'text', 'text': params['message'], 'text_elements': []})
        else:
            from bridge.features.sessions.model import _desktop_wrapper_bounds
            bounds = _desktop_wrapper_bounds(text.get('text', ''))
            prefix = text['text'][:bounds[2]] if bounds else ''
            text.update(text=prefix + params['message'], text_elements=[])
        with self.lock:
            self.editing.add(thread_id)
        reverted = False
        try:
            result = self.client.request('thread/revert' if paginated else 'thread/rollback',
                {'threadId': thread_id, **({'beforeTurnId': params['turnId']} if paginated else {'numTurns': 1})}, timeout=timeout)
            reverted = True
            updated = result.get('thread') or {}
            if (updated.get('id') != thread_id or updated.get('cwd') != thread['cwd'] or
                    updated.get('modelProvider') != thread['modelProvider']):
                raise AppServerUnavailable('编辑后的原生聊天身份无法核验')
            result = self.client.request('turn/start', {'threadId': thread_id, 'input': inputs,
                'turnTrigger': 'edit_user_message'}, timeout=timeout)
        except Exception as exc:
            # Revert already succeeded. The whole edit is a single durable
            # unknown write until verified; never replay its first half.
            if reverted:
                raise AppServerUnknownResult('历史已更新，但编辑重发结果尚未确认；请核对原会话，不会自动重发') from exc
            raise
        finally:
            with self.lock:
                self.editing.discard(thread_id)
            self._queue_refresh(thread_id)
        return result

    def app_response(self, request, response):
        result = response.get('result')
        if not isinstance(result, dict):
            return
        thread = result.get('thread') or {}
        thread_id = thread.get('id') if isinstance(thread, dict) else None
        if thread_id and request.get('method') in ('thread/revert', 'thread/rollback'):
            self._queue_refresh(thread_id)
        if thread_id and request.get('method') in ('thread/start', 'thread/resume'):
            fields = ('model', 'reasoningEffort', 'serviceTier', 'collaborationMode')
            self.app_metadata[thread_id] = {key: copy.deepcopy(result[key]) for key in fields if key in result}
            if all(key in result for key in ('approvalPolicy', 'approvalsReviewer', 'sandbox')):
                self.app_metadata[thread_id]['permissions'] = {key: copy.deepcopy(result[key]) for key in
                    ('approvalPolicy', 'approvalsReviewer', 'sandbox', 'activePermissionProfile') if key in result}

    def native_event(self, message):
        thread_id = (message.get('params') or {}).get('threadId')
        # Apply ordered deltas to a bounded state before the next native reply.
        # Copying, validation and serialization run on the publisher instead.
        with self.lock:
            if thread_id not in self.joined:
                return
            if (message.get('method') == 'thread/status/changed' and
                    (message.get('params', {}).get('status') or {}).get('type') == 'notLoaded' and
                    (thread_id in self.editing or any(p[0] == 'app' and p[2] in ('thread/revert', 'thread/rollback')
                        and p[3].get('params', {}).get('threadId') == thread_id for p in self.router.pending.values()))):
                # Revert replaces the loaded history in the same native runtime.
                # Its transient unload precedes the verified replacement reply.
                return
            if message.get('method') in ('thread/reverted', 'thread/rolledBack'):
                self._queue_refresh(thread_id)
            if 'id' in message:
                self._server_request(message, publish=False)
            else:
                self._event(message, publish=False)
            state = self.states.get(thread_id)
            if state is not None:
                turns = state['turns']
                if len(turns) > HISTORY_TURN_LIMIT:
                    del turns[:-HISTORY_TURN_LIMIT]
                    revisions = self.turn_revisions.get(thread_id, {})
                    self.turn_revisions[thread_id] = {turn['turnId']: revisions.get(turn['turnId'], 0)
                                                       for turn in turns}
                    state['turnsPagination'] = {'hasLoadedOldest': False}
                for turn in turns:
                    if len(turn['items']) > 100:
                        del turn['items'][:-100]
                        turn['_itemsComplete'] = False
                        state['turnsPagination'] = {'hasLoadedOldest': False}
        self._mark_dirty(thread_id)

    def _mark_dirty(self, thread_id, state=None):
        with self.event_condition:
            if not self.event_closed:
                self.dirty.add(thread_id)
                self.event_condition.notify_all()

    def _process_events(self):
        while True:
            with self.event_condition:
                self.event_condition.wait_for(lambda: self.event_closed or self.dirty)
                # Coalesce streamed text without queuing every token or snapshot.
                self.event_condition.wait_for(lambda: self.event_closed, timeout=.025)
                if self.event_closed:
                    return
                changed = self.dirty
                self.dirty = set()
            for thread_id in changed:
                try:
                    with self.lock:
                        state = self.states.get(thread_id)
                        if state is None:
                            continue
                        snapshot = copy.deepcopy(state)
                    self.publish_state(thread_id, snapshot)
                except Exception:
                    logging.getLogger(__name__).warning('Desktop observation could not synchronize')
                    self._invalidate(thread_id)

    def _disconnect(self):
        with self.event_condition:
            self.event_closed = True
            self.dirty.clear()
            self.event_condition.notify_all()
        super()._disconnect()
        if threading.current_thread() is not self.event_worker:
            self.event_worker.join(timeout=2)


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
        with tempfile.SpooledTemporaryFile(max_size=DesktopIPC.READ_CHUNK) as encoded:
            for chunk in iter_wire_json(message):
                encoded.write(chunk)
                if encoded.tell() > DesktopIPC.MAX_FRAME:
                    raise ValueError('桌面快照超过原生帧长度')
            length = encoded.tell()
            encoded.seek(0)
            with self.write_lock:
                self.stream.sendall(struct.pack('<I', length))
                while chunk := encoded.read(DesktopIPC.READ_CHUNK):
                    self.stream.sendall(chunk)

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
                if state is None:
                    self.send({'type': 'broadcast', 'method': 'client-status-changed', 'version': 0,
                        'sourceClientId': self.owner, 'targetClientIds': [self.client_id],
                        'params': {'hostId': 'local', 'conversationId': thread_id,
                                   'clientId': self.owner, 'status': 'disconnected'}})
                    continue
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
                self.sessions.release(thread_id, self.client_id)
                return {}
            targets = message.get('targetClientIds')
            if targets and targets != [self.owner]:
                raise AppServerRequestNotSent('原生桌面 owner 已变化')
            with self.condition:
                self.following.add(thread_id)
            try:
                self.sessions.retain(thread_id, self.client_id)
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
            if self.closed:
                return
            self.closed = True
            following = list(self.following)
            self.following.clear()
            self.pending.clear()
            self.condition.notify_all()
        for thread_id in following:
            self.sessions.release(thread_id, self.client_id)
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


def serve(config, app, host, arguments, initialize, env=None, dispatcher=None):
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
    if dispatcher is not None:
        record['dispatcher'] = dispatcher
    runtime._write(active, json.dumps(record))
    stopped = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    output_lock, followers_lock = threading.Lock(), threading.Lock()
    followers = set()
    child = listener = None

    def emit(message):
        with output_lock:
            # The App still receives the complete native reply. Hold the output
            # lock across all chunks to preserve the original JSONL boundaries.
            output = sys.stdout.buffer
            for chunk in iter_wire_json(message):
                output.write(chunk)
            output.write(b'\n')
            output.flush()

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
            if thread_id is None:
                follower.close()
            else:
                follower.snapshot(thread_id, None)

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
