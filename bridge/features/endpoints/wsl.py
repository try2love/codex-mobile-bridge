"""WSL session workflow on the original daemon, with isolated write receipts."""
import copy
import hashlib
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from bridge.app.lifecycle import private_json
from bridge.clients.codex.ipc import IPCError
from bridge.clients.codex.remote import RemoteStore, RemoteUnavailable, payload, upload_file
from bridge.clients.codex.shared_server import SharedServer
from bridge.clients.codex.configuration import magpie_managed
from bridge.clients.codex.catalog import Catalog
from bridge.features.sessions.model import normalize_state, normalize_request, computer_use_approval
from bridge.features.sessions.timeline import Timeline
from bridge.features.sessions.store import StoreUnavailable
from bridge.features.sessions.projects import Projects
from bridge.features.workspace.uploads import Uploads
from bridge.platforms.windows import wsl
from bridge.resources import source_text


CAPABILITIES = {'send': True, 'stop': True, 'settings': True, 'approvals': True, 'queue': False,
                'workspace': True, 'attachments': True, 'skills': False, 'permissions': False,
                'plan': False, 'goal': False, 'terminal': False, 'sideChat': False,
                'notifications': False, 'messageActions': False}


class WslStore(RemoteStore):
    def __init__(self, entry):
        super().__init__(entry.row['distribution'])
        self.entry = entry

    def call(self, method, args):
        source = source_text('features/sessions/store.py')
        source += '\ns=SessionStore(Path(' + repr(self.entry.row['home']) + '),source="cli")\n'
        source += 'print(json.dumps(s.' + method + '(**' + payload(args) + '),ensure_ascii=False))\n'
        return self.entry.read(self.alias, source, timeout=30)


class Session:
    def __init__(self, identifier):
        self.id = identifier
        self.condition = threading.Condition(threading.RLock())
        self.action_lock = threading.RLock()
        self.timeline = Timeline()
        self.sequence = 0
        self.state = {'id': identifier, 'turns': [], 'threadRuntimeStatus': {'type': 'notLoaded'}}
        self.connected = False
        self.activating = False
        self.loading = True
        self.error = None
        self.viewers = 0
        self.touched = time.monotonic()
        self.history_limit = 20
        self.refreshing = False
        self.stamp = None

    def changed(self):
        self.sequence += 1
        self.condition.notify_all()

    def view(self):
        with self.condition:
            return {**normalize_state(self.state, self.connected), 'sequence': self.sequence,
                    'loadingHistory': self.loading, 'activating': self.activating, 'error': self.error}


class WslEntry:
    capabilities = CAPABILITIES
    def __init__(self, row, directory):
        self.row = dict(row)
        self.host = row['id']  # Gateway route only; never sent as a native hostId.
        self.data_dir = Path(directory)
        self.lock = threading.RLock()
        self.connection_lock = threading.RLock()
        self.closed = threading.Event()
        self.live = {}
        self.listed = set()
        self.server = None
        self.generation = None
        self.subscriptions = {}
        self.write_lock = threading.RLock()
        self.receipts_path = self.data_dir / 'operations.json'
        self.receipts = json.loads(self.receipts_path.read_text(encoding='utf-8')) if self.receipts_path.exists() else {}
        self.create_lock = threading.RLock()
        self.creations_path = self.data_dir / 'creations.json'
        self.creations = json.loads(self.creations_path.read_text(encoding='utf-8')) if self.creations_path.exists() else {}
        self.store = WslStore(self)
        self.directory_projects = Projects(self.data_dir)
        self.uploads = Uploads(self.data_dir, lambda *args: upload_file(self.row['distribution'], *args, reader=self.read))
        self.worker = threading.Thread(target=self._maintain, daemon=True)
        self.worker.start()

    def read(self, alias, source, timeout=20):
        prefix = 'import os\nos.environ["CODEX_HOME"]=' + repr(self.row['home']) + '\n'
        try:
            return wsl.read(self.row['distribution'], prefix + source, self.row['user'], timeout)
        except (OSError, ValueError, TimeoutError) as exc:
            raise RemoteUnavailable('WSL 入口暂不可用，请检查发行版和原 Codex') from exc

    def runtime(self, allow_start=False):
        with self.connection_lock:
            if self.closed.is_set():
                raise IPCError('WSL 入口已关闭')
            if self.server is not None and not self.server.closed:
                return self.server
            try:
                info = (wsl.ensure_daemon(self.row['distribution'], self.row['user'], self.row['home']) if allow_start
                        else wsl.probe(self.row['distribution'], self.row['user']))
                item = next((h for h in info['homes'] if h['home'] == self.row['home']), None)
                daemon = item.get('daemon') if item else None
                if info['user'] != self.row['user'] or not daemon:
                    raise IPCError('WSL 共享服务暂不可用；打开聊天时会尝试启动已安装的 Codex 服务')
                stream = wsl.Stream(self.row['distribution'], self.row['user'], self.row['home'], daemon)
                if self.closed.is_set():
                    stream.close()
                    raise IPCError('WSL 入口已关闭')
                generation = uuid.uuid4().hex
                server = SharedServer(stream, lambda event: self._event(event, generation))
                self.generation = generation
                self.subscriptions.clear()
                with self.lock:
                    sessions = list(self.live.values())
                for session in sessions:
                    with session.condition:
                        session.connected = False
                        session.state['requests'] = []
                        session.changed()
                self.server = server
                if self.closed.is_set():
                    server.close()
                    raise IPCError('WSL 入口已关闭')
                return server.start()
            except (OSError, ValueError, TimeoutError) as exc:
                raise IPCError('无法连接 WSL Codex 共享服务，请检查版本、发行版状态或其他独立运行的 Codex') from exc

    def _event(self, message, generation):
        if generation != self.generation:
            return
        method, params = message.get('method'), message.get('params') or {}
        if method == 'bridge/disconnected':
            with self.lock:
                sessions = list(self.live.values())
            for session in sessions:
                with session.condition:
                    session.connected = False
                    session.state['requests'] = []
                    session.error = '原 WSL 连接已断开；未确认操作不会自动重发'
                    session.changed()
            return
        with self.lock:
            session = self.live.get(params.get('threadId'))
        if session is None:
            return
        with session.condition:
            if 'id' in message:
                if normalize_request(message).get('supported'):
                    request = {**copy.deepcopy(message), '_generation': generation}
                    session.state.setdefault('requests', []).append(request)
                else:
                    session.error = '此类请求请在原 WSL Codex 中处理'
            elif method == 'serverRequest/resolved':
                session.state['requests'] = [r for r in session.state.get('requests', [])
                                             if r['id'] != params.get('requestId')]
            elif method in ('item/agentMessage/delta', 'item/commandExecution/outputDelta'):
                for turn in session.state.get('turns', []):
                    for item in turn.get('items', []):
                        if item.get('id') == params.get('itemId'):
                            key = 'text' if method == 'item/agentMessage/delta' else 'aggregatedOutput'
                            item[key] = item.get(key, '') + params.get('delta', '')
            elif method == 'thread/status/changed':
                session.state['threadRuntimeStatus'] = copy.deepcopy(params.get('status') or {})
            elif method == 'thread/closed':
                self.subscriptions.pop(session.id, None)
                session.connected = False
                session.state['requests'] = []
                session.error = 'WSL 会话已卸载，点击重新连接即可恢复'
            session.changed()

    def _refresh(self, session):
        with session.action_lock:
            metadata = self.store.get(session.id)
            try:
                runtime = self.runtime()
                generation = self.generation
                thread = runtime.request('thread/read', {'threadId': session.id, 'includeTurns': False})['thread']
                if thread.get('parentThreadId') or thread.get('threadSource') == 'subagent':
                    raise KeyError('不是 CLI 根会话')
                turns, cursor = [], None
                while len(turns) < session.history_limit:
                    page = runtime.request('thread/turns/list', {'threadId': session.id, 'cursor': cursor,
                        'limit': min(50, session.history_limit - len(turns)), 'sortDirection': 'desc', 'itemsView': 'full'})
                    turns.extend(page.get('data', []))
                    cursor = page.get('nextCursor')
                    if not cursor:
                        break
                turns.reverse()
                state = {'id': session.id, 'title': thread.get('name') or metadata.get('name') or metadata.get('display_title') or metadata.get('title'),
                         'cwd': thread.get('cwd'), 'latestModel': thread.get('model'),
                         'latestReasoningEffort': thread.get('reasoningEffort'), 'modelProvider': thread.get('modelProvider'),
                         'threadRuntimeStatus': thread.get('status') or {},
                         'turns': [{**t, 'turnId': t['id'], 'turnStartedAtMs': (t.get('startedAt') or 0) * 1000} for t in turns],
                         'turnHistory': {'history': {'isComplete': not bool(cursor)}}}
                connected = thread.get('status', {}).get('type') in ('idle', 'active') and thread.get('canAcceptDirectInput') is True
                error = None if connected else '点击重新连接即可在此设备恢复会话并继续聊天'
                if runtime.closed or generation != self.generation:
                    raise IPCError('WSL 原连接已变化')
            except IPCError as exc:
                state = self.store.history(session.id, turn_limit=session.history_limit)
                state['title'] = metadata.get('display_title') or state.get('title')
                connected, error = False, str(exc)
            with session.condition:
                requests = session.state.get('requests', [])
                stamp = hashlib.sha256(json.dumps([state, connected, error], sort_keys=True).encode()).hexdigest()
                state['requests'] = requests
                if stamp != session.stamp or session.loading:
                    session.state = state
                    session.connected, session.error, session.loading = connected, error, False
                    session.stamp = stamp
                    session.changed()

    def session(self, identifier, attach=True, background=False, force=False):
        uuid.UUID(identifier)
        with self.lock:
            session = self.live.get(identifier)
        if session is None:
            self.store.get(identifier)
            with self.lock:
                session = self.live.setdefault(identifier, Session(identifier))
        session.touched = time.monotonic()
        if attach and background:
            with session.condition:
                if not session.refreshing:
                    session.refreshing = True
                    threading.Thread(target=self._refresh_background, args=(session,), daemon=True).start()
        elif attach:
            self._refresh(session)
        return session

    def _refresh_background(self, session):
        try:
            self._refresh(session)
        except (OSError, ValueError, KeyError, RemoteUnavailable, IPCError) as exc:
            with session.condition:
                session.error = str(exc)
                session.loading = False
                session.connected = False
                session.changed()
        finally:
            with session.condition:
                session.refreshing = False

    def _maintain(self):
        while not self.closed.wait(2):
            with self.lock:
                sessions = list(self.live.values())
            for session in sessions:
                if self.closed.is_set():
                    break
                if session.viewers or time.monotonic() - session.touched < 20:
                    with session.condition:
                        if session.refreshing:
                            continue
                        session.refreshing = True
                    self._refresh_background(session)

    def list(self, query='', limit=100, offset=0, archived=False, source='all', include_sources=False):
        available = self.store.available_sources() if include_sources else []
        rows = self.store.list(query=query, limit=limit, offset=offset, archived=archived, source=source)
        for row in rows:
            cwd = row.get('cwd') or ''
            row.update(host=self.host, endpointId=self.host, hostLabel=self.row['label'],
                       title=row.get('name') or row.get('display_title') or row.get('title') or '未命名聊天',
                       projectKey=self.host + '|' + cwd, projectName=cwd.rstrip('/').rsplit('/', 1)[-1] or '未归类',
                       recency=row.get('recency_at_ms') or (row.get('recency_at') or 0) * 1000 or row.get('updated_at_ms') or (row.get('updated_at') or 0) * 1000)
            with self.lock:
                session = self.live.get(row['id'])
                self.listed.add(row['id'])
            row['connected'] = bool(session and session.connected)
            row['access'] = self._access(session)
        return (rows, available) if include_sources else rows

    def _access(self, session=None):
        connected = bool(session and session.connected)
        return {
            'source': 'cli',
            'runtime': 'wsl',
            'readOnly': not connected,
            'canSend': connected,
            'canInterrupt': connected,
            'canApprove': connected,
            'canReconnect': True,
            'canConfigure': connected,
            'canChangePermissions': False,
            'canChangeModel': connected,
            'canSelectSkills': False,
            'canUseModes': False,
            'canUseGoal': False,
            'canUseSideChat': False,
            'canEditMessages': False,
            'reason': None if connected else '点击重新连接以恢复 WSL 会话',
        }

    def view(self, identifier, **options):
        session = self.session(identifier, **options)
        result = session.view()
        result.update(host=self.host, endpointId=self.host, hostLabel=self.row['label'],
                      files=[], queue=[], submissions=[], goalRuntimeAvailable=False,
                      capabilities=CAPABILITIES, access=self._access(session),
                      accessManagement='external', forkedFrom=None)
        for turn in result['turns']:
            turn['actionable'] = False
        if not result['connected']:
            result['readOnlyReason'] = result.get('error') or '点击重新连接以恢复 WSL 会话'
        return result

    def timeline_read(self, identifier, mode='page', **options):
        session = self.session(identifier, background=True)
        if options.get('before') and not session.view()['historyComplete']:
            with session.action_lock:
                session.history_limit = min(2000, session.history_limit + 50)
            self._refresh(session)
        with session.condition:
            session.timeline.update(self.view(identifier, attach=False))
            if mode == 'detail':
                return session.timeline.detail(**options)
            result = session.timeline.changes(**options) if mode == 'changes' else session.timeline.page(**options)
            return {**result, 'files': []}

    def full_history(self, identifier):
        session = self.session(identifier, attach=False)
        session.history_limit = min(2000, session.history_limit + 50)
        self._refresh(session)
        return self.view(identifier, attach=False)

    def activity(self, identifiers):
        if not isinstance(identifiers, list) or len(identifiers) > 500 or any(not isinstance(i, str) for i in identifiers):
            raise ValueError('会话状态请求无效')
        rows = []
        for identifier in dict.fromkeys(identifiers):
            if identifier not in self.listed:
                continue
            with self.lock:
                session = self.live.get(identifier)
            if session is None:
                continue
            view = session.view()
            last = view['turns'][-1] if view['turns'] else {}
            rows.append({'id': identifier, 'host': self.host, 'connected': view['connected'],
                         'status': view['status'] if view['connected'] else 'unknown',
                         'turnId': last.get('id'), 'turnStatus': last.get('status')})
        return rows

    def _target(self, identifier, activate=False):
        self.store.get(identifier)
        runtime = self.runtime(allow_start=activate)
        # Fresh native evidence, never a display cache. Explicit subscription is
        # allowed only to this already loaded thread on this original daemon.
        thread = runtime.request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        if (activate and thread.get('id') == identifier and not thread.get('parentThreadId')
                and thread.get('threadSource') != 'subagent' and thread.get('status', {}).get('type') == 'notLoaded'):
            self.subscriptions.pop(identifier, None)
            self._subscribe(identifier, runtime, thread)
            thread = runtime.request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        if (thread.get('id') != identifier or thread.get('parentThreadId') or
                thread.get('threadSource') == 'subagent' or
                thread.get('canAcceptDirectInput') is not True or thread.get('status', {}).get('type') not in ('idle', 'active')):
            raise IPCError('WSL 会话尚未就绪或由其他执行器独占；操作未发送，请重新连接')
        return runtime, thread

    def _subscribe(self, identifier, runtime, thread):
        generation = self.generation
        if self.subscriptions.get(identifier) == generation:
            return
        if thread.get('status', {}).get('type') == 'notLoaded':
            config = runtime.request('config/read', {'includeLayers': False, 'cwd': thread.get('cwd')}).get('config') or {}
            if thread.get('modelProvider') and thread['modelProvider'] != (config.get('model_provider') or 'openai'):
                raise IPCError('此历史会话的提供商与当前接入不同；已保留原设置，请先在接入管理工具中选择原接入')
        # A second connection can send, but only subscribers receive approvals.
        # This reuses the SAME daemon/thread; no runtime, config overrides,
        # auth, cwd, model, permission policy, or new task is supplied.
        result = runtime.request('thread/resume', {'threadId': identifier, 'excludeTurns': True})
        current = result.get('thread') or {}
        if (runtime.closed or generation != self.generation or current.get('id') != identifier or
                any(thread.get(k) is not None and current.get(k) != thread[k] for k in ('cwd', 'modelProvider', 'model'))):
            raise IPCError('原 WSL 会话设置或连接已变化，操作尚未发送，请重新查看原会话')
        self.subscriptions[identifier] = generation

    def activate(self, identifier):
        uuid.UUID(identifier)
        self.runtime(allow_start=True)
        session = self.session(identifier, attach=False)
        with session.action_lock:
            with session.condition:
                session.activating = True
                session.changed()
            try:
                runtime, thread = self._target(identifier, activate=True)
                self._subscribe(identifier, runtime, thread)
                self._refresh(session)
                return session
            except IPCError as exc:
                with session.condition:
                    session.error = str(exc)
                raise
            finally:
                with session.condition:
                    session.activating = False
                    session.changed()

    def connect(self):
        self.runtime(allow_start=True)
        return {'ok': True, 'host': self.host}

    def projects(self):
        """History workspaces plus explicitly validated directory selections."""
        try:
            rows = self.store.list(limit=1000)
        except StoreUnavailable:
            rows = []
        roots = sorted({row['cwd'] for row in rows if isinstance(row.get('cwd'), str) and row['cwd'].startswith('/')})
        registered = self.directory_projects.list({self.host})
        known = {row['cwd'] for row in registered}
        return registered + [{'key': self.host + '|' + cwd, 'host': self.host, 'hostLabel': self.row['label'],
                 'cwd': cwd, 'name': cwd.rstrip('/').rsplit('/', 1)[-1] or cwd} for cwd in roots if cwd not in known]

    def register_project(self, path, create=False, host=None):
        if host not in (None, self.host): raise ValueError('请选择当前 WSL 应用的目录')
        # Explicit action can wake only this already registered distribution.
        self.runtime(allow_start=True)
        source = source_text('features/sessions/create.py')
        source += '\ntry:\n result=project_directory(**' + payload({'value': path, 'create': create}) + ')\nexcept ValueError as error: result={"error":str(error)}\nprint(json.dumps(result))\n'
        identity = self.read(self.row['distribution'], source)
        if identity.get('error'): raise ValueError(identity['error'])
        return self.directory_projects.save(self.host, self.row['label'], identity)

    def project_directories(self, host, path=None, hidden=False, name=None, identity=None):
        if host != self.host: raise ValueError('请选择当前 WSL 应用')
        source = source_text('features/sessions/create.py')
        source += '\ntry:\n result=project_directory_listing(**' + payload({'value':path,'hidden':hidden,'name':name,'identity':identity}) + ')\nexcept ValueError as error: result={"error":str(error)}\nprint(json.dumps(result))\n'
        result = self.read(self.row['distribution'], source)
        if result.get('error'): raise ValueError(result['error'])
        return result

    def create_chat(self, project_key, title, request_id):
        if not isinstance(request_id, str):
            raise ValueError('创建请求标识无效')
        uuid.UUID(request_id)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError('请输入 1–120 字的聊天名称')
        if not isinstance(project_key, str) or not project_key.startswith(self.host + '|'):
            raise ValueError('请选择此 WSL 应用的已有项目或独立聊天')
        independent = project_key == self.host + '|independent'
        title = title.strip()
        with self.create_lock:
            receipt = self.creations.get(request_id)
            if receipt:
                if receipt['project'] != project_key or receipt['title'] != title:
                    raise ValueError('同一创建请求不能用于不同内容')
                if not receipt.get('id'):
                    raise IPCError('创建结果待核对，请先刷新聊天列表；不会重复创建')
                identifier = receipt['id']
            else:
                runtime = self.runtime(allow_start=True)
                project = None if independent else next((p for p in self.projects() if p['key'] == project_key), None)
                if not independent and project is None:
                    raise ValueError('请选择此 WSL 应用的已有项目或独立聊天')
                source = 'import json,uuid\nfrom pathlib import Path\n'
                if independent:
                    source += source_text('features/sessions/create.py')
                    source += '\nprint(json.dumps({"cwd":independent_directory(' + repr(self.row['home'] + '/mobile-bridge') + ',' + repr(request_id) + ')}))\n'
                else:
                    source += source_text('features/sessions/create.py')
                    source += '\nprint(json.dumps(project_directory(**' + payload({'value': project['cwd'], 'expected': project.get('directoryIdentity')}) + ')))\n'
                cwd = self.read(self.row['distribution'], source)['cwd']
                receipt = self.creations[request_id] = {'project': project_key, 'title': title, 'cwd': cwd}
                self.data_dir.mkdir(parents=True, exist_ok=True)
                private_json(self.creations_path, self.creations)
                try:
                    created = runtime.request('thread/start', {'cwd': cwd, 'ephemeral': False})['thread']
                    identifier = created['id']
                    uuid.UUID(identifier)
                    if created.get('cwd') != cwd or created.get('parentThreadId'):
                        raise IPCError('新会话身份未确认')
                    receipt['id'] = identifier
                    private_json(self.creations_path, self.creations)
                    runtime.request('thread/name/set', {'threadId': identifier, 'name': title})
                    # Force durable empty-thread history before exposing it in lists.
                    runtime.request('thread/read', {'threadId': identifier, 'includeTurns': True})
                except Exception:
                    raise IPCError('创建结果待核对，请先刷新聊天列表；不会重复创建') from None
            self.activate(identifier)
            return {'id': identifier, 'host': self.host, 'opened': True, 'message': '聊天已创建，可以直接发送消息'}

    def _mutation(self, identifier, key, value, call):
        fingerprint = hashlib.sha256(json.dumps([identifier, value], sort_keys=True).encode()).hexdigest()
        with self.write_lock:
            old = self.receipts.get(key)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('相同请求标识不能用于不同操作')
                return dict(old['result'])
            self.data_dir.mkdir(parents=True, exist_ok=True)
            unknown = {'status': 'unknown', 'id': key, 'message': '结果待核对，请检查原 WSL 会话；不会重复发送'}
            self.receipts[key] = {'fingerprint': fingerprint, 'result': unknown}
            private_json(self.receipts_path, self.receipts)
            try:
                result = call()
            except Exception:
                # Persistence precedes any bytes. Neither disconnect nor a new
                # daemon generation is permission to replay this mutation.
                raise IPCError(unknown['message']) from None
            self.receipts[key]['result'] = result
            private_json(self.receipts_path, self.receipts)
            return result

    def _previous_send(self, identifier, key, value):
        with self.write_lock:
            old = self.receipts.get(key)
            if old is None:
                return None
            fingerprint = hashlib.sha256(json.dumps([identifier, value], sort_keys=True).encode()).hexdigest()
            if old['fingerprint'] != fingerprint:
                raise ValueError('相同请求标识不能用于不同操作')
            return dict(old['result'])

    def send(self, identifier, text, submission_id, mode='send', skills=None, work_mode=None, attachments=None, **options):
        uuid.UUID(identifier); uuid.UUID(submission_id)
        if skills:
            raise ValueError('WSL Skill 选择尚未开放，请在原终端设置')
        if work_mode not in (None, 'default'):
            raise ValueError('此 WSL 入口暂不支持切换计划或目标模式')
        if mode not in ('send', 'steer'):
            raise ValueError('WSL 暂不支持网关排队，请直接发送或补充当前任务')
        files = self.uploads.resolve(identifier, attachments or [])
        if not isinstance(text, str) or (not text.strip() and not files) or len(text) > 100000:
            raise ValueError('请输入 1–100000 字的消息')
        with self.session(identifier, attach=False).action_lock:
            value = {'text': text, 'mode': mode, 'attachments': attachments or []}
            key = identifier + ':send:' + submission_id
            previous = self._previous_send(identifier, key, value)
            if previous is not None:
                return previous
            runtime, thread = self._target(identifier, activate=True)
            if mode == 'send' and thread['status']['type'] == 'active':
                raise ValueError('任务正在运行，请选择补充当前任务，或等待完成')
            self._subscribe(identifier, runtime, thread)
            items = [{'type': 'text', 'text': text}]
            for file in files:
                if file.get('image'):
                    items.append({'type': 'localImage', 'path': file['path']})
                else:
                    items[0]['text'] += '\n\n用户提供的文件：' + file['path']
            def dispatch():
                params = {'threadId': identifier, 'input': items}
                if mode == 'steer':
                    turns = runtime.request('thread/turns/list', {'threadId': identifier, 'limit': 1, 'sortDirection': 'desc'}).get('data', [])
                    if not turns or turns[0].get('status') != 'inProgress':
                        raise ValueError('当前任务已结束，请重新查看会话')
                    params['expectedTurnId'] = turns[0]['id']
                    result = runtime.request('turn/steer', params)
                else:
                    params['clientUserMessageId'] = submission_id
                    result = runtime.request('turn/start', params)
                return {'status': 'accepted', 'id': submission_id, 'turnId': (result.get('turn') or {}).get('id') or result.get('turnId')}
            result = self._mutation(identifier, key, value, dispatch)
            self.session(identifier, background=True)
            return result

    def interrupt(self, identifier):
        with self.session(identifier, attach=False).action_lock:
            runtime, _ = self._target(identifier)
            turns = runtime.request('thread/turns/list', {'threadId': identifier, 'limit': 1, 'sortDirection': 'desc'}).get('data', [])
            if not turns or turns[0].get('status') != 'inProgress':
                return {'status': 'idle'}
            turn = turns[0]['id']
            return self._mutation(identifier, identifier + ':stop:' + turn, {'turnId': turn},
                lambda: runtime.request('turn/interrupt', {'threadId': identifier, 'turnId': turn}))

    def settings(self, identifier, model, effort, *, fast_mode=None):
        if (not isinstance(model, str) or not model or len(model) > 200 or any(c in model for c in '\r\n\0')
                or effort not in (None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')):
            raise ValueError('模型或推理强度无效')
        if fast_mode is not None:
            raise ValueError('此 WSL 入口不提供 Fast 设置')
        with self.session(identifier, attach=False).action_lock:
            runtime, _ = self._target(identifier)
            runtime.request('thread/settings/update', {'threadId': identifier, 'model': model, 'effort': effort})
            self._refresh(self.live[identifier])
            view = self.live[identifier].view()
            return {'applied': True, 'confirmed': view.get('model') == model and view.get('effort') == effort}

    def catalog(self, identifier, refresh=False, kind=None, **options):
        metadata = self.store.get(identifier)
        if kind == 'skills':
            return {'kind': 'skills', 'skills': [], 'total': 0, 'hasMore': False}
        runtime = self.runtime()
        current = runtime.request('thread/read', {'threadId': identifier, 'includeTurns': False}).get('thread') or {}
        config = runtime.request('config/read', {'includeLayers': False, 'cwd': metadata['cwd']}).get('config') or {}
        provider = config.get('model_provider') or 'openai'
        if (current.get('modelProvider') or metadata.get('model_provider')) != provider:
            return {'kind': 'models', 'models': [], 'modelSource': 'codex',
                    'modelError': '此聊天的提供商与当前默认接入不同，请手动填写原提供商支持的模型 ID',
                    'fastMode': {'allowed': False}}
        models, cursor = [], None
        for _ in range(10):
            page = runtime.request('model/list', {'limit': 100, 'cursor': cursor, 'includeHidden': False})
            models.extend(page.get('data', [])); cursor = page.get('nextCursor')
            if not cursor:
                break
        return {'kind': kind or 'models', 'models': Catalog.normalize_models(models), 'modelSource': 'codex', 'skills': [],
                'currentModel': current.get('model') or metadata.get('model') or config.get('model'),
                'currentEffort': current.get('reasoningEffort'),
                'fastMode': {'allowed': False}, 'accessManagement': 'external'}

    def access_summary(self):
        current = {'status': 'error', 'kind': 'external', 'id': None, 'name': 'WSL 外部管理'}
        try:
            config = self.runtime().request('config/read', {'includeLayers': False}).get('config') or {}
            current.update(status='ready', name='Magpie' if magpie_managed(config=config) else '原 WSL 终端接入')
        except (IPCError, RemoteUnavailable):
            pass
        return {'scope': 'wsl', 'scopeLabel': self.row['label'], 'accounts': [], 'current': current,
                'activeId': None, 'canSwitch': False, 'canManageDesktop': False, 'blockers': [],
                'switch': {'phase': 'idle'}, 'management': {'mode': 'external', 'external': True,
                    'reason': 'WSL 接入由原终端或 Magpie 管理，请在原环境中切换'},
                'desktopUpdate': {'state': 'unsupported'}, 'reminders': {}}

    def respond(self, identifier, request_id, response):
        session = self.session(identifier, attach=False)
        with session.action_lock:
            runtime, _ = self._target(identifier)
            with session.condition:
                pending = next((r for r in session.state.get('requests', []) if str(r['id']) == str(request_id)), None)
                if not pending or pending.get('_generation') != self.generation:
                    raise ValueError('此请求已处理、已过期或属于此前连接，请在原终端检查')
                pending = copy.deepcopy(pending)
            method, params = pending['method'], pending.get('params') or {}
            if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
                decision = response.get('decision')
                if set(response) != {'decision'} or decision not in ('accept', 'decline', 'cancel') or (params.get('availableDecisions') and decision not in params['availableDecisions']):
                    raise ValueError('此请求不支持所选授权范围')
                result = {'decision': decision}
            elif method == 'item/permissions/requestApproval':
                if set(response) != {'decision'} or response['decision'] not in ('accept', 'decline'):
                    raise ValueError('请选择本次批准或拒绝')
                result = {'permissions': params.get('permissions', {}) if response['decision'] == 'accept' else {}, 'scope': 'turn'}
            elif method in ('item/tool/requestUserInput', 'tool/requestUserInput'):
                answers = response.get('answers')
                keys = {q['id'] for q in params.get('questions', [])}
                if set(response) != {'answers'} or not isinstance(answers, dict) or not keys or set(answers) != keys or any(not isinstance(v, list) or not v or any(not isinstance(s, str) or not s.strip() or len(s) > 20000 for s in v) for v in answers.values()):
                    raise ValueError('请完整回答问题')
                result = {'answers': {k: {'answers': v} for k, v in answers.items()}}
            elif method == 'mcpServer/elicitation/request':
                action = response.get('action')
                if action not in ('accept', 'decline', 'cancel'):
                    raise ValueError('未知确认操作')
                computer = computer_use_approval(params)
                if computer:
                    if set(response) - {'action', 'persist'} or ('persist' in response and (action != 'accept' or response['persist'] not in computer['persistModes'])):
                        raise ValueError('此请求不支持所选授权范围')
                    result = {'action': action, 'content': {} if action == 'accept' else None}
                    if 'persist' in response:
                        result['_meta'] = {'persist': response['persist']}
                else:
                    from bridge.app.service import validate_form
                    if set(response) - {'action', 'content'}:
                        raise ValueError('此请求不支持所选授权范围')
                    if action == 'accept':
                        validate_form(response.get('content'), params.get('requestedSchema', {}))
                    result = {'action': action, 'content': response.get('content') if action == 'accept' else None}
            else:
                raise ValueError('此类请求请在原 WSL 终端处理')
            def dispatch():
                runtime.write({'id': pending['id'], 'result': result})
                with session.condition:
                    resolved = session.condition.wait_for(lambda: self.generation != pending['_generation'] or
                        not any(r['id'] == pending['id'] for r in session.state.get('requests', [])), timeout=8)
                    if not resolved or self.generation != pending['_generation'] or runtime.closed:
                        return {'status': 'unknown', 'message': '审批结果待核对；请检查原会话，不会重复发送'}
                return {'status': 'submitted'}
            receipt = self._mutation(identifier, identifier + ':approval:' + self.generation + ':' + str(pending['id']), result, dispatch)
            return receipt

    def rename(self, identifier, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or any(ord(c) < 32 for c in title):
            raise ValueError('聊天名称需为 1–120 个字符，不能包含控制字符')
        self.store.get(identifier)
        self.runtime().request('thread/name/set', {'threadId': identifier, 'name': title.strip()})
        return {'id': identifier, 'title': title.strip(), 'host': self.host}

    def workspace(self, identifier, action, params):
        root = self.store.get(identifier)['cwd']
        source = source_text('features/workspace/workspace.py')
        args = payload({'root': root, 'action': action, 'params': params})
        if action in ('download-stream', 'archive-stream'):
            source += '\nimport json,sys\ntry:\n with operate(**' + args + ') as (metadata,stream):\n  sys.stdout.buffer.write(json.dumps(metadata).encode()+b"\\n")\n  while True:\n   b=stream.read(65536)\n   if not b:break\n   sys.stdout.buffer.write(b)\nexcept (ValueError,PermissionError,OSError) as error:\n sys.stdout.buffer.write(json.dumps({"error":str(error),"permission":isinstance(error,PermissionError)}).encode()+b"\\n")\n'
            return self._download(source)
        source += '\nimport json\ntry:\n result={"value":operate(**' + args + ')}\nexcept (ValueError,PermissionError) as e:\n result={"error":str(e)}\nprint(json.dumps(result,ensure_ascii=False))\n'
        value = self.read(self.row['distribution'], source, timeout=60)
        if 'error' in value:
            raise ValueError(value['error'])
        return value['value']

    @contextmanager
    def _download(self, source):
        try:
            with wsl.download(self.row['distribution'], source, self.row['user']) as result:
                yield result
        except PermissionError:
            raise
        except OSError as exc:
            raise RemoteUnavailable('WSL 下载失败，请检查原环境后重试') from exc

    def upload(self, identifier, upload_id, name, data):
        self.store.get(identifier)
        return self.uploads.put(identifier, upload_id, name, data)

    def upload_thumb(self, identifier, upload_id, data, width, height):
        self.store.get(identifier)
        return self.uploads.set_thumb(identifier, upload_id, data, width, height)

    def upload_preview(self, identifier, upload_id, variant='thumb'):
        self.store.get(identifier)
        return self.uploads.preview(identifier, upload_id, variant)

    def artifact(self, identifier, artifact_id):
        self.store.get(identifier)
        raise KeyError('请在项目文件面板访问 WSL 文件')

    def subagents(self, identifier, agent_id=None):
        self.store.get(identifier)
        if agent_id is None:
            return self.store.subagents(identifier)
        uuid.UUID(agent_id)
        view = normalize_state(self.store.subagent_history(identifier, agent_id), False)
        return {'id': agent_id, 'title': view['title'], 'turns': view['turns'],
                'historyComplete': view['historyComplete'], 'saved': True}

    @staticmethod
    def unsupported(*args, **kwargs):
        raise ValueError('此功能暂不支持 WSL CLI 入口，请在原终端操作')

    terminal = side_chat = side_attachment = permissions = linux_permission_options = unsupported
    cancel_goal = edit_goal = set_goal_status = cancel_queued = message_action = unsupported
    desktop_image_preview = unsupported

    def close(self):
        self.closed.set()
        server = self.server
        if server is not None:
            server.close()
        self.worker.join(timeout=3)
