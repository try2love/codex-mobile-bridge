"""WSL session workflow on the original daemon, with isolated write receipts."""
import copy
import hashlib
import json
import posixpath
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from bridge.features.sessions.message_management import snapshot as management_snapshot, mutate as management_mutate
from pathlib import Path

from bridge.app.lifecycle import private_json
from bridge.clients.codex.ipc import IPCError
from bridge.clients.codex.remote import RemoteStore, RemoteUnavailable, payload, upload_file
from bridge.clients.codex.shared_server import SharedServer
from bridge.clients.codex.configuration import magpie_managed
from bridge.clients.codex.catalog import Catalog, normalize_native_skills
from bridge.features.sessions import linux_permissions
from bridge.features.accounts.draft_identity import draft_identity
from bridge.features.sessions.model import normalize_state, normalize_request, computer_use_approval
from bridge.features.sessions.timeline import Timeline
from bridge.features.sessions.store import StoreUnavailable
from bridge.features.sessions.archive import ArchiveCommands
from bridge.features.sessions.organization_commands import OrganizationCommands
from bridge.features.sessions.compaction import CompactCommands
from bridge.features.sessions.create import check_archive_runtime
from bridge.features.sessions.pins import SessionPins
from bridge.features.sessions.organization import organization as native_organization
from bridge.features.sessions.projects import Projects
from bridge.features.endpoints.wsl_features import WslFeatures
from bridge.features.endpoints.wsl_models import WslModelCatalog
from bridge.features.workspace.uploads import Uploads
from bridge.platforms.windows import wsl
from bridge.resources import source_text


CAPABILITIES = {'send': True, 'stop': True, 'settings': True, 'approvals': True, 'queue': True,
                'workspace': True, 'attachments': True, 'skills': True, 'permissions': True,
                'plan': True, 'goal': True, 'terminal': True, 'sideChat': False,
                'notifications': True, 'messageActions': False}


def _entry_write(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        # Disabling waits for accepted writes; stale HTTP targets cannot start
        # another write after the entry is closed.
        with self.write_lock:
            self._require_open()
            return method(self, *args, **kwargs)
    return guarded


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
        self.watched = False
        self.notification_probe_until = 0

    def changed(self):
        self.sequence += 1
        self.condition.notify_all()

    def view(self):
        with self.condition:
            return {**normalize_state(self.state, self.connected), 'permissionMode': linux_permissions.current_mode(self.state), 'sequence': self.sequence,
                    'loadingHistory': self.loading, 'activating': self.activating, 'error': self.error}


class WslEntry(WslFeatures):
    capabilities = CAPABILITIES
    def __init__(self, row, directory):
        self.row = dict(row)
        self.host = row['id']  # Gateway route only; never sent as a native hostId.
        self.data_dir = Path(directory)
        self.session_pins = SessionPins(directory, self.host + ":" + row["user"] + ":" + row["home"])
        self.archive_commands = ArchiveCommands(directory)
        self.organization_commands = OrganizationCommands(directory, private_json)
        self.compact_commands = CompactCommands(directory, private_json)
        self.lock = threading.RLock()
        self.connection_lock = threading.RLock()
        self.closed = threading.Event()
        self.live = {}
        self.listed = set()
        self.server = None
        self.generation = None
        self.subscriptions = {}
        self.subscription_tokens = {}
        from bridge.features.sessions.cli_release import ReleasedSessions
        self.released_cli = ReleasedSessions(directory, private_json)
        self._managed_routes = {}
        self.write_lock = threading.RLock()
        self._draft_identity_cache = {}
        self._draft_identity_pending = set()
        self.receipts_path = self.data_dir / 'operations.json'
        self.receipts = json.loads(self.receipts_path.read_text(encoding='utf-8')) if self.receipts_path.exists() else {}
        for key, receipt in self.receipts.items():
            if ':send:' in key and receipt.get('result', {}).get('status') == 'queued':
                identifier = key.split(':send:', 1)[0]
                uuid.UUID(identifier)
                self.live.setdefault(identifier, Session(identifier))
        self.create_lock = threading.RLock()
        self.creations_path = self.data_dir / 'creations.json'
        self.creations = json.loads(self.creations_path.read_text(encoding='utf-8')) if self.creations_path.exists() else {}
        self.store = WslStore(self)
        self.directory_projects = Projects(self.data_dir)
        self.uploads = Uploads(self.data_dir, lambda *args: upload_file(self.row['distribution'], *args, reader=self.read))
        self._init_features()
        self.model_catalog = WslModelCatalog(self)
        self.worker = threading.Thread(target=self._maintain, daemon=True)
        self.worker.start()

    def read(self, alias, source, timeout=20):
        self._require_open()
        prefix = 'import os\nos.environ["CODEX_HOME"]=' + repr(self.row['home']) + '\n'
        try:
            return wsl.read(self.row['distribution'], prefix + source, self.row['user'], timeout)
        except (OSError, ValueError, TimeoutError) as exc:
            raise RemoteUnavailable('WSL 入口暂不可用，请检查发行版和原 Codex') from exc

    def _require_open(self):
        if self.closed.is_set():
            raise IPCError('WSL 入口已关闭')

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
                self.daemon_identity = dict(daemon)
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
        with self.lock:
            self.notification_dirty.add(session.id)
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
            elif method == 'thread/settings/updated':
                settings = params.get('threadSettings') or {}
                session.state['latestThreadSettings'] = copy.deepcopy(settings)
                if all(k in settings for k in ('approvalPolicy', 'approvalsReviewer', 'sandboxPolicy')):
                    session.state['currentPermissions'] = {k: copy.deepcopy(settings[k]) for k in
                        ('approvalPolicy', 'approvalsReviewer', 'sandboxPolicy', 'activePermissionProfile') if k in settings}
                if settings.get('model'):
                    session.state['latestModel'] = settings['model']
                if settings.get('effort') is not None:
                    session.state['latestReasoningEffort'] = settings['effort']
                if settings.get('collaborationMode') is not None:
                    session.state['latestCollaborationMode'] = copy.deepcopy(settings['collaborationMode'])
            elif method == 'turn/plan/updated':
                session.state['turnPlan'] = copy.deepcopy(params.get('plan') or [])
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
                self._verify_thread(session.id, metadata, thread)
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
                         'cwd': thread.get('cwd'), 'latestModel': thread.get('model') or session.state.get('latestModel'),
                         'latestReasoningEffort': thread.get('reasoningEffort') or session.state.get('latestReasoningEffort'), 'modelProvider': thread.get('modelProvider'),
                         'threadRuntimeStatus': thread.get('status') or {},
                         'turns': [{**t, 'turnId': t['id'], 'turnStartedAtMs': (t.get('startedAt') or 0) * 1000} for t in turns],
                         'turnHistory': {'history': {'isComplete': not bool(cursor)}}}
                connected = not self.released_cli.contains(session.id) and thread.get('status', {}).get('type') in ('idle', 'active') and thread.get('canAcceptDirectInput') is True
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
                for key in ('turnPlan', 'latestCollaborationMode', 'latestTokenUsageInfo'):
                    if key in session.state:
                        state[key] = copy.deepcopy(session.state[key])
                state['latestThreadSettings'] = copy.deepcopy(session.state.get('latestThreadSettings') or {})
                if session.state.get('currentPermissions'):
                    state['currentPermissions'] = copy.deepcopy(session.state['currentPermissions'])
                state['requests'] = requests
                if stamp != session.stamp or session.loading:
                    session.state = state
                    session.connected, session.error, session.loading = connected, error, False
                    session.stamp = stamp
                    session.changed()
            self._refresh_features(session)

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
                if session.viewers or session.watched or time.monotonic() - session.touched < 20:
                    with session.condition:
                        if session.refreshing:
                            continue
                        session.refreshing = True
                    self._refresh_background(session)
                try:
                    self._dispatch_queued(session.id)
                except IPCError:
                    if self.closed.is_set():
                        return

    def list(self, query='', limit=100, offset=0, archived=False, source='all', include_sources=False, pin_order=False):
        available = ['wsl'] if include_sources and self.store.available_sources() else []
        pins = set(self.session_pins.identifiers())
        rows = [] if source == 'desktop' else self.store.list(query=query, limit=limit, offset=offset,
            archived=archived, source='cli' if source == 'wsl' else source,
            **({'pinned_ids': sorted(pins)} if pin_order else {}))
        for row in rows:
            cwd = row.get('cwd') or ''
            row.update(host=self.host, endpointId=self.host, hostLabel=self.row['label'],
                       title=row.get('name') or row.get('display_title') or row.get('title') or '未命名聊天',
                       projectKey=self.host + '|' + cwd, projectName=cwd.rstrip('/').rsplit('/', 1)[-1] or '未归类',
                       recency=row.get('recency_at_ms') or (row.get('recency_at') or 0) * 1000 or row.get('updated_at_ms') or (row.get('updated_at') or 0) * 1000)
            with self.lock:
                session = self.live.get(row['id'])
                self.listed.add(row['id'])
            row['bridgePinned'] = row['id'] in pins
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
            'canChangePermissions': connected,
            'canChangeModel': connected,
            'canSelectSkills': connected,
            'canUseModes': connected,
            'canUseGoal': False,
            'canUseSideChat': False,
            'canEditMessages': False,
            'reason': None if connected else '点击重新连接以恢复 WSL 会话',
        }

    def _account_draft_state(self, provider):
        """Fresh credential fingerprints only; no runtime or model access."""
        from bridge.features.accounts.wsl_account_payload import worker_source
        source = worker_source()+'\n'+source_text('features/accounts/draft_identity.py')
        source += '\nstore=AccountStore("codex", '+repr(self.row['home'])+')\nfiles=store.files()\n'
        source += 'config=_toml_loads((files["config.toml"] or b"").decode())\n'
        source += 'owner=codex_account_identity(store.directory,'+payload(provider)+',os.environ,shared_auth=True,toml_loads=_toml_loads)\n'
        source += 'print(json.dumps({"identity":_identity(files),"provider":config.get("model_provider","openai"),"owner":owner}))\n'
        value = self.read(self.row['distribution'], source)
        if not value.get('identity') or value.get('provider') != provider:
            raise IPCError('WSL 草稿账号身份无法核验，请刷新后重新选择')
        return {**value, 'accountIdentity': draft_identity('codex', self.host, provider, value['owner']) if value.get('owner') else ''}

    def _account_draft_catalog(self, provider):
        sessions, visited = {}, set()
        for archived in (False, True):
            offset = 0
            while True:
                rows = self.store.list(query='', limit=200, offset=offset, archived=archived, source='cli')
                if not isinstance(rows, list):
                    raise IPCError('WSL 历史会话目录无法完整核验，账号尚未切换')
                for row in rows:
                    sid = row.get('id')
                    if not isinstance(sid, str) or not sid or sid in visited:
                        raise IPCError('WSL 历史会话目录已变化，账号尚未切换')
                    visited.add(sid)
                    if (row.get('model_provider') or 'openai') != provider:
                        continue
                    sid, cwd = row.get('id'), row.get('cwd')
                    if not isinstance(sid, str) or not sid or not isinstance(cwd, str) or not cwd or sid in sessions:
                        raise IPCError('WSL 历史会话目录已变化，账号尚未切换')
                    sessions[sid] = {'id': sid, 'cwd': cwd, 'provider': provider}
                if len(rows) < 200:
                    break
                offset += len(rows)
        return sessions

    def capture_account_drafts(self, provider):
        """Snapshot catalog tuples only; never load thread contents or runtimes."""
        state = self._account_draft_state(provider)
        sessions = self._account_draft_catalog(provider)
        if self._account_draft_state(provider) != state:
            raise IPCError('WSL 草稿账号身份已变化，账号尚未切换')
        return {'version': 1, 'entry': self.host, 'provider': provider, 'identity': state['identity'],
                'accountIdentity': state['accountIdentity'], 'sessions': list(sessions.values())}

    def _account_draft_records(self):
        path = self.data_dir/'account-drafts.json'
        if not path.exists():
            return {'version': 1, 'entry': self.host, 'sessions': {}}
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('version') != 1 or value.get('entry') != self.host or not isinstance(value.get('sessions'), dict):
            raise IPCError('WSL 草稿续接记录无法核验')
        return value

    def complete_account_drafts(self, snapshot, appliedIdentity):
        if (snapshot.get('version') != 1 or snapshot.get('entry') != self.host
                or not isinstance(snapshot.get('sessions'), list)):
            raise IPCError('WSL 草稿续接归属无法核验')
        provider = snapshot.get('provider')
        state = self._account_draft_state(provider)
        if state['identity'] != appliedIdentity:
            raise IPCError('WSL 草稿续接配置身份已变化')
        if not snapshot.get('accountIdentity') or not state['accountIdentity']:
            # A first login has no proven old draft owner. It still permits
            # account setup, without inventing a cross-account authorization.
            with self.lock:
                self._draft_identity_cache[provider] = (time.monotonic(), state['accountIdentity'])
            return []
        value = self._account_draft_records()
        updated = copy.deepcopy(value)
        drafts = []
        catalog = self._account_draft_catalog(provider)
        for saved in snapshot['sessions']:
            sid = saved['id']
            current = catalog.get(sid)
            if current != saved or saved['provider'] != provider:
                raise IPCError('原 WSL 草稿会话身份或目录已变化')
            previous = value['sessions'].get(sid) or {}
            same_tuple = previous.get('cwd') == saved['cwd'] and previous.get('provider') == provider
            preceding = (previous.get('appliedIdentity') == snapshot['identity'] and
                         previous.get('accountIdentity') == snapshot['accountIdentity'])
            repeated = (previous.get('appliedIdentity') == appliedIdentity and
                        previous.get('accountIdentity') == state['accountIdentity'])
            prior = (previous.get('previousAccountIdentities') or []) if same_tuple and (preceding or repeated) else []
            lineage = list(dict.fromkeys([*prior, snapshot['accountIdentity']]))
            lineage = [item for item in lineage if isinstance(item, str) and len(item) == 64
                       and item != state['accountIdentity']][-32:]
            updated['sessions'][sid] = {**saved, 'appliedIdentity': appliedIdentity,
                'accountIdentity': state['accountIdentity'], 'previousAccountIdentities': lineage}
            drafts.append({'id': sid, 'previousAccountIdentity': snapshot['accountIdentity'],
                           'accountIdentity': state['accountIdentity']})
        if self._account_draft_state(provider) != state:
            raise IPCError('WSL 草稿续接配置身份已变化')
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_json(self.data_dir/'account-drafts.json', updated)
        with self.lock:
            self._draft_identity_cache[provider] = (time.monotonic(), state['accountIdentity'])
        return drafts

    def _account_draft_continuation(self, identifier):
        try:
            record = self._account_draft_records()['sessions'].get(identifier)
            if not record:
                return None
            metadata = self.store.get(identifier)
            provider = metadata.get('model_provider') or 'openai'
            if metadata.get('id') != identifier or metadata.get('cwd') != record['cwd'] or provider != record['provider']:
                return None
            state = self._account_draft_state(provider)
            if state['identity'] != record['appliedIdentity'] or state['accountIdentity'] != record['accountIdentity']:
                return None
            return {'id': identifier, 'entry': self.host, 'accountIdentity': state['accountIdentity'],
                    'previousAccountIdentities': list(record['previousAccountIdentities'])}
        except (OSError, ValueError, KeyError, TypeError, IPCError, RemoteUnavailable):
            return None

    def view(self, identifier, **options):
        session = self.session(identifier, **options)
        result = session.view()
        result['draftAccountIdentity'] = self._management_identity(identifier, refresh=True)
        continuation = self._account_draft_continuation(identifier)
        if continuation is not None:
            result['draftAccountIdentity'] = continuation['accountIdentity']
            result['draftContinuation'] = continuation
        result.update(host=self.host, endpointId=self.host, hostLabel=self.row['label'],
                      files=[], queue=self._queue(identifier), submissions=self._submissions(identifier), goalRuntimeAvailable=False,
                      capabilities=CAPABILITIES, access=self._access(session),
                      accessManagement='external', forkedFrom=None)
        for turn in result['turns']:
            turn['actionable'] = False
        result['capabilities'] = {**result['capabilities'], 'compact': result['connected'] and
                                  result['status'] == 'idle' and not result['requests']}
        if not result['connected']:
            result['readOnlyReason'] = result.get('error') or '点击重新连接以恢复 WSL 会话'
        return self._view_features(session, result)

    def timeline_read(self, identifier, mode='page', **options):
        session = self.session(identifier, background=True)
        if options.get('before') and not session.view()['historyComplete']:
            with session.action_lock:
                session.history_limit += 50
            self._refresh(session)
        # view() reads write receipts; do not take the entry write gate while
        # holding the session condition (writers acquire them in reverse order).
        snapshot = self.view(identifier, attach=False)
        with session.condition:
            if snapshot['sequence'] >= session.timeline.sequence:
                session.timeline.update(snapshot)
            if mode == 'detail':
                return session.timeline.detail(**options)
            result = session.timeline.changes(**options) if mode == 'changes' else session.timeline.page(**options)
            return {**result, 'files': []}

    def full_history(self, identifier):
        session = self.session(identifier, attach=False)
        session.history_limit += 50
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

    @staticmethod
    def _verify_thread(identifier, metadata, thread):
        if (thread.get('id') != identifier or thread.get('parentThreadId') or
                thread.get('threadSource') == 'subagent' or
                any(metadata.get(saved) is not None and thread.get(native) != metadata[saved]
                    for saved, native in (('cwd', 'cwd'), ('model_provider', 'modelProvider')))):
            raise IPCError('原 WSL 会话身份或接入已变化，操作尚未发送，请重新查看原会话')

    def _account_route(self):
        from bridge.features.accounts.wsl_account_payload import worker_source
        # Unmanaged homes retain their existing path/ownership semantics. Only
        # an existing management store activates the stricter account worker.
        summary = worker_source()
        summary += '\nstore=AccountStore("codex", '+repr(self.row['home'])+')\nprint(json.dumps(store.route_summary()))\n'
        path = posixpath.join(self.row['home'], '.codex-mobile-bridge-accounts', 'store.json')
        source = 'import os,json\nif not os.path.lexists('+repr(path)+'):\n print(json.dumps({"managed":False}))\nelse:\n exec('+repr(summary)+')\n'
        return self.read(self.row['distribution'], source)

    def _check_account_route(self, runtime, thread, *, new=False):
        route = self._account_route()
        if route.get('managed') is False:
            previous = self._managed_routes.get(thread.get('id'))
            if previous is not None and previous[0] == self.generation:
                raise IPCError('WSL 账号管理状态已变化，请退出原 WSL 服务后重新连接')
            return None
        if not route.get('valid'):
            raise IPCError('WSL 账号配置已变化，请在账号页面核验后重新连接')
        config = runtime.request('config/read', {'includeLayers': False, 'cwd': thread.get('cwd')}).get('config') or {}
        provider = config.get('model_provider') or 'openai'
        definition = (config.get('model_providers') or {}).get(provider) or {}
        if ((thread.get('modelProvider') and thread['modelProvider'] != provider) or provider != route['provider']):
            raise IPCError('此历史会话的提供商与当前接入不同；请先选择原接入')
        if (definition.get('base_url') != route['baseUrl']
                or definition.get('wire_api', 'responses') != 'responses'
                or definition.get('requires_openai_auth') is not True
                or config.get('cli_auth_credentials_store', 'file') != 'file'
                or config.get('openai_base_url') not in (None, route['baseUrl'])
                or config.get('forced_login_method') not in (None, 'api')
                or any(config.get(key) for key in ('profile', 'profiles', 'api_key_helper', 'credential_store'))
                or any(definition.get(key) for key in ('env_key', 'experimental_bearer_token', 'http_headers',
                    'env_http_headers', 'auth', 'aws', 'query_params', 'gateway_oauth'))):
            raise IPCError('此项目覆盖了已选择的 WSL API 地址或认证，请在原 CLI 修正项目配置后重新连接')
        proof = (self.generation, route['appliedIdentity'], route['accountIdentity'],
                 thread.get('cwd'), provider, thread.get('model'))
        previous = self._managed_routes.get(thread.get('id'))
        # A changed model does not change the verified endpoint. Preserve the
        # latest observed model while retaining the original route provenance.
        if (not new and thread.get('status', {}).get('type') != 'notLoaded'
                and (previous is None or previous[:-1] != proof[:-1])):
            raise IPCError('此 WSL 会话已由其他连接加载，无法核验原 API 路由；请退出原 WSL 服务后重新连接')
        return proof

    def _target(self, identifier, activate=False, managed_control=False):
        if not activate and self.released_cli.contains(identifier):
            raise IPCError('CLI 连接已释放，请点击继续会话重新连接')
        metadata = self.store.get(identifier)
        runtime = self.runtime(allow_start=activate)
        # Fresh native evidence, never a display cache. Explicit subscription is
        # allowed only to this already loaded thread on this original daemon.
        thread = runtime.request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        self._verify_thread(identifier, metadata, thread)
        previous = self._managed_routes.get(identifier)
        defer_route = (managed_control and thread.get('status', {}).get('type') != 'notLoaded'
                       and previous is not None and previous[0] == self.generation)
        proof = (self._check_account_route(runtime, thread)
                 if (activate or managed_control) and not defer_route else None)
        if (activate and thread.get('id') == identifier and not thread.get('parentThreadId')
                and thread.get('threadSource') != 'subagent' and thread.get('status', {}).get('type') == 'notLoaded'):
            self.subscriptions.pop(identifier, None)
            self._subscribe(identifier, runtime, thread, route_proof=proof)
            thread = runtime.request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
            self._verify_thread(identifier, metadata, thread)
        if (thread.get('id') != identifier or thread.get('parentThreadId') or
                thread.get('threadSource') == 'subagent' or
                thread.get('canAcceptDirectInput') is not True or thread.get('status', {}).get('type') not in ('idle', 'active')):
            raise IPCError('WSL 会话尚未就绪或由其他执行器独占；操作未发送，请重新连接')
        if proof is not None and thread.get('status', {}).get('type') != 'notLoaded':
            self._managed_routes[identifier] = (*proof[:-1], thread.get('model'))
        return runtime, thread

    def _subscribe(self, identifier, runtime, thread, route_proof=None):
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
        session = self.session(identifier, attach=False)
        with session.condition:
            permissions = {k: copy.deepcopy(result[k]) for k in ('approvalPolicy', 'approvalsReviewer', 'activePermissionProfile') if k in result}
            if 'sandbox' in result:
                permissions['sandboxPolicy'] = copy.deepcopy(result['sandbox'])
            if result.get('model'):
                session.state['latestModel'] = result['model']
            if result.get('reasoningEffort') is not None:
                session.state['latestReasoningEffort'] = result['reasoningEffort']
            if result.get('collaborationMode') is not None:
                session.state['latestCollaborationMode'] = copy.deepcopy(result['collaborationMode'])
            if permissions:
                session.state['currentPermissions'] = permissions
                session.changed()
        self.subscriptions[identifier] = generation
        self.subscription_tokens[identifier] = str(uuid.uuid4())
        if route_proof is not None:
            self._managed_routes[identifier] = (*route_proof[:-1], current.get('model'))

    def cli_connections(self):
        with self.lock:
            return [{'key': self.subscription_tokens[sid], 'provider': 'codex', 'sessionId': sid,
                     'host': self.host, 'location': 'WSL · ' + self.row['distribution'],
                     'title': self.live[sid].state.get('title') or sid,
                     '_release': lambda stop, sid=sid, token=self.subscription_tokens[sid]: self.release_connection(sid, token, stop)}
                    for sid, generation in self.subscriptions.items() if generation == self.generation
                    and sid in self.subscription_tokens and sid in self.live]

    @_entry_write
    def release_connection(self, identifier, token, stop=False):
        from bridge.clients.codex.release import release_thread
        session = self.session(identifier, attach=False)
        with session.action_lock:
            generation, runtime = self.generation, self.server
            def request(method, params):
                if (runtime is None or runtime.closed or self.server is not runtime or self.generation != generation
                        or self.subscriptions.get(identifier) != generation or self.subscription_tokens.get(identifier) != token):
                    raise IPCError('原 WSL CLI 连接已变化；未操作新的连接')
                return runtime.request(method, params)
            queued = [entry for key, entry in self.receipts.items() if key.startswith(identifier + ':send:')
                      and entry.get('result', {}).get('status') in ('queued', 'unknown')]
            if queued:
                if not stop:
                    return {'status': 'waiting'}
                raise ValueError('此会话还有待处理的发送记录，请先在发送管理中处理')
            result = release_thread(request, identifier, stop)
            if result['status'] in ('released', 'detached'):
                self.released_cli.set(identifier, True)
                self.subscriptions.pop(identifier, None)
                self.subscription_tokens.pop(identifier, None)
                with session.condition:
                    session.connected = False
                    session.state['requests'] = []
                    session.error = result.get('notice')
                    session.changed()
            return result

    @_entry_write
    def activate(self, identifier):
        uuid.UUID(identifier)
        self.runtime(allow_start=True)
        self.released_cli.set(identifier, False)
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

    @_entry_write
    def connect(self):
        self.runtime(allow_start=True)
        return {'ok': True, 'host': self.host}

    def project_environments(self):
        self._require_open()
        return [{'key': 'cli|' + self.host, 'type': 'cli', 'surface': 'cli', 'host': self.host,
                 'hostLabel': self.row['label'], 'name': self.row['label']}]

    def projects(self):
        """History workspaces plus explicitly validated directory selections."""
        try:
            rows = self.store.list(limit=1000)
        except (StoreUnavailable, RemoteUnavailable):
            rows = []
        roots = sorted({row['cwd'] for row in rows if isinstance(row.get('cwd'), str) and row['cwd'].startswith('/')})
        registered = self.directory_projects.list({self.host})
        known = {row['cwd'] for row in registered}
        projects = registered + [{'key': self.host + '|' + cwd, 'host': self.host, 'hostLabel': self.row['label'],
                    'cwd': cwd, 'name': cwd.rstrip('/').rsplit('/', 1)[-1] or cwd} for cwd in roots if cwd not in known]
        return [{**project, 'type': 'cli', 'environment': 'cli|' + self.host} for project in projects]

    @_entry_write
    def register_project(self, path, create=False, host=None, kind='desktop'):
        if host not in (None, self.host): raise ValueError('请选择当前 WSL 应用的目录')
        if kind not in ('desktop', 'cli'): raise ValueError('请选择 WSL CLI 项目环境')
        # Explicit action can wake only this already registered distribution.
        self.runtime(allow_start=True)
        source = source_text('features/sessions/create.py')
        source += '\ntry:\n result=project_directory(**' + payload({'value': path, 'create': create}) + ')\nexcept ValueError as error: result={"error":str(error)}\nprint(json.dumps(result))\n'
        identity = self.read(self.row['distribution'], source)
        if identity.get('error'): raise ValueError(identity['error'])
        project = self.directory_projects.save(self.host, self.row['label'], identity)
        return {**project, 'type': 'cli', 'environment': 'cli|' + self.host}

    @_entry_write
    def project_directories(self, host, path=None, hidden=False, name=None, identity=None):
        if host != self.host: raise ValueError('请选择当前 WSL 应用')
        source = source_text('features/sessions/create.py')
        source += '\ntry:\n result=project_directory_listing(**' + payload({'value':path,'hidden':hidden,'name':name,'identity':identity}) + ')\nexcept ValueError as error: result={"error":str(error)}\nprint(json.dumps(result))\n'
        result = self.read(self.row['distribution'], source)
        if result.get('error'): raise ValueError(result['error'])
        return result

    @_entry_write
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
                proof = self._check_account_route(runtime, {'cwd': cwd}, new=True)
                receipt = self.creations[request_id] = {'project': project_key, 'title': title, 'cwd': cwd}
                self.data_dir.mkdir(parents=True, exist_ok=True)
                private_json(self.creations_path, self.creations)
                try:
                    created = runtime.request('thread/start', {'cwd': cwd, 'ephemeral': False})['thread']
                    identifier = created['id']
                    uuid.UUID(identifier)
                    if created.get('cwd') != cwd or created.get('parentThreadId'):
                        raise IPCError('新会话身份未确认')
                    if proof is not None:
                        if created.get('modelProvider') != proof[4]:
                            raise IPCError('新会话提供商与已选择的 WSL API 不一致')
                        self._managed_routes[identifier] = (*proof[:-1], created.get('model'))
                    receipt['id'] = identifier
                    private_json(self.creations_path, self.creations)
                    runtime.request('thread/name/set', {'threadId': identifier, 'name': title})
                    # Force durable empty-thread history before exposing it in lists.
                    runtime.request('thread/read', {'threadId': identifier, 'includeTurns': True})
                except Exception:
                    raise IPCError('创建结果待核对，请先刷新聊天列表；不会重复创建') from None
            self.activate(identifier)
            return {'id': identifier, 'host': self.host, 'opened': True, 'message': '聊天已创建，可以直接发送消息'}

    @_entry_write
    def _mutation(self, identifier, key, value, call):
        fingerprint = hashlib.sha256(json.dumps([identifier, value], sort_keys=True).encode()).hexdigest()
        with self.write_lock:
            old = self.receipts.get(key)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('相同请求标识不能用于不同操作')
                if old['result'].get('status') != 'queued':
                    return dict(old['result'])
            self.data_dir.mkdir(parents=True, exist_ok=True)
            unknown = {'status': 'unknown', 'id': key, 'message': '结果待核对，请检查原 WSL 会话；不会重复发送'}
            self.receipts[key] = {'fingerprint': fingerprint, 'result': unknown}
            if ':send:' in key:
                self.receipts[key]['entry'] = {**copy.deepcopy((old or {}).get('entry') or {}), **copy.deepcopy(value)}
                self.receipts[key]['entry'].setdefault('at', time.time())
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

    @_entry_write
    def send(self, identifier, text, submission_id, mode='send', skills=None, work_mode=None, attachments=None, **options):
        if work_mode == 'goal':
            if options.get('references'):
                raise ValueError('目标模式暂不支持附件')
            return self.send_goal(identifier, text, submission_id, mode=mode, skills=skills,
                                  attachments=attachments, ui_locale=options.get('ui_locale'))
        return self._send(identifier, text, submission_id, mode, skills, work_mode, attachments, references=options.get('references'))

    def _send(self, identifier, text, submission_id, mode='send', skills=None, work_mode=None, attachments=None, *, references=None, queued=False, goal_activation=None):
        uuid.UUID(identifier); uuid.UUID(submission_id)
        skills = skills or []
        if not isinstance(skills, list) or len(skills) > 8 or any(not isinstance(s, str) for s in skills):
            raise ValueError('最多选择 8 个 Skill')
        if work_mode not in (None, 'default', 'plan'):
            raise ValueError('工作模式无效')
        if mode not in ('send', 'steer', 'queue'):
            raise ValueError('发送方式无效')
        files = self.uploads.resolve(identifier, attachments or [])
        if references is not None:
            files += self.workspace(identifier, 'references', {'references': references})
        if len(files) > 10:
            raise ValueError('每条消息最多添加 10 个不同附件')
        if not isinstance(text, str) or (not text.strip() and not files) or len(text) > 100000:
            raise ValueError('请输入 1–100000 字的消息')
        with self.session(identifier, attach=False).action_lock:
            value = {'text': text, 'mode': mode, 'attachments': attachments or []}
            if references:
                value['references'] = references
            if skills:
                value['skills'] = sorted(set(skills))
            if work_mode is not None:
                value['workMode'] = work_mode
            if goal_activation is not None:
                value['goalActivation'] = copy.deepcopy(goal_activation)
            key = identifier + ':send:' + submission_id
            previous = self._previous_send(identifier, key, value)
            if previous is not None and not (queued and previous.get('status') == 'queued'):
                return previous
            runtime, thread = self._target(identifier, activate=mode != 'queue', managed_control=True)
            if (mode == 'send' or queued) and thread['status']['type'] == 'active':
                raise ValueError('任务正在运行，请选择补充当前任务，或等待完成')
            if goal_activation is not None:
                from bridge.features.sessions.goal import goal_identity
                current_goal = self._read_goal(identifier, runtime)
                if not current_goal or current_goal.get('status') != 'active' or goal_identity(current_goal) != goal_activation:
                    if queued:
                        return self.cancel_queued(identifier, submission_id)
                    raise ValueError('目标已暂停或发生变化，启动消息未发送')
            selected = self._resolve_skills(identifier, runtime, thread, skills)
            if mode == 'queue' and not queued:
                result = {'status': 'queued', 'id': submission_id}
                self.receipts[key] = {'fingerprint': hashlib.sha256(json.dumps([identifier, value], sort_keys=True).encode()).hexdigest(), 'result': result, 'entry': {**value, 'at': time.time(), 'attachmentNames': [f['name'] for f in files], 'target': {k: thread.get(k) for k in ('cwd', 'modelProvider')}}}
                self.data_dir.mkdir(parents=True, exist_ok=True)
                private_json(self.receipts_path, self.receipts)
                return result
            self._subscribe(identifier, runtime, thread)
            from bridge.features.sessions.model import attachment_prompt
            items = [{'type': 'text', 'text': attachment_prompt(text, files) if references else text}]
            for file in files:
                if file.get('image'):
                    items.append({'type': 'localImage', 'path': file['path']})
                elif not references:
                    items[0]['text'] += '\n\n用户提供的文件：' + file['path']
            items.extend({'type': 'skill', 'name': s['name'], 'path': s['path']} for s in selected)
            collaboration = None
            if work_mode is not None:
                state = self.session(identifier, attach=False).state
                model = thread.get('model') or state.get('latestModel')
                effort = thread.get('reasoningEffort') or state.get('latestReasoningEffort')
                if not model:
                    raise ValueError('尚未取得模型设置，请重新连接后再切换模式')
                collaboration = {'mode': work_mode, 'settings': {'model': model, 'reasoning_effort': effort, 'developer_instructions': None}}
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
                    if collaboration is not None:
                        params['collaborationMode'] = collaboration
                    result = runtime.request('turn/start', params)
                return {'status': 'accepted', 'id': submission_id, 'turnId': (result.get('turn') or {}).get('id') or result.get('turnId')}
            # Check immediately before creating an uncertain write receipt. A
            # known route rejection has not sent model bytes and stays retryable.
            self._check_account_route(runtime, thread)
            result = self._mutation(identifier, key, value, dispatch)
            self.session(identifier, background=True)
            return result

    @staticmethod
    def _skills(runtime, thread, refresh=False):
        cwd = thread['cwd']
        raw = runtime.request('skills/list', {'cwds': [cwd], 'forceReload': bool(refresh)})
        return normalize_native_skills(raw, cwd)

    def _resolve_skills(self, identifier, runtime, thread, identifiers):
        if not identifiers:
            return []
        rows, _ = self._skills(runtime, thread, True)
        indexed = {r['id']: r for r in rows}
        if any(key not in indexed for key in identifiers):
            raise ValueError('Skill 不可用，请刷新列表')
        return [indexed[key] for key in sorted(set(identifiers))]

    def _submissions(self, identifier):
        with self.write_lock:
            return [{**row['result'], 'text': (row.get('entry') or {}).get('text', '')} for key, row in self.receipts.items()
                    if key.startswith(identifier + ':send:') and row['result'].get('status') in ('queued', 'unknown')]

    def _queue(self, identifier):
        with self.write_lock:
            return [{**copy.deepcopy(row['entry']), **row['result']} for key, row in self.receipts.items()
                    if key.startswith(identifier + ':send:') and row['result'].get('status') == 'queued']

    def _management_identity(self, identifier, refresh=False):
        with self.lock:
            session = self.live.get(identifier)
        if session is None:
            return ''
        with session.condition:
            provider = session.state.get('modelProvider')
        if not provider:
            return ''
        with self.lock:
            cached = self._draft_identity_cache.get(provider)
            fresh = cached and time.monotonic() - cached[0] < 30
            if refresh and not fresh and provider not in self._draft_identity_pending:
                self._draft_identity_pending.add(provider)
                threading.Thread(target=self._probe_draft_identity, args=(provider,), daemon=True,
                                 name='wsl-draft-identity').start()
            return cached[1] if fresh else ''

    def _probe_draft_identity(self, provider):
        identity = ''
        try:
            from bridge.features.accounts.wsl_account_payload import worker_source
            source = worker_source()+'\n'+source_text('features/accounts/draft_identity.py')
            source += '\nimport os,json\nfrom pathlib import Path\n'
            source += 'home=os.environ.get("CODEX_HOME") or str(Path.home()/".codex")\n'
            source += 'print(json.dumps({"identity":codex_account_identity(home,' + payload(provider) + ',os.environ,shared_auth=True,toml_loads=_toml_loads)}))\n'
            owner = self.read(self.row['distribution'], source).get('identity')
            if owner:
                identity = draft_identity('codex', self.host, provider, owner)
        except (OSError, ValueError, RemoteUnavailable, IPCError):
            pass
        with self.lock:
            self._draft_identity_cache[provider] = (time.monotonic(), identity)
            self._draft_identity_pending.discard(provider)
            sessions = list(self.live.values())
        for session in sessions:
            with session.condition:
                if session.state.get('modelProvider') == provider:
                    session.changed()

    def message_management(self, identifier, receipt_ids=()):
        uuid.UUID(identifier)
        with self.write_lock:
            self._require_open()
            rows = [(k.split(':send:', 1)[1], {**copy.deepcopy(v.get('entry') or {}), **v['result']})
                    for k, v in self.receipts.items() if k.startswith(identifier + ':send:')]
            identity = self._management_identity(identifier, refresh=True)
            result = management_snapshot(rows, receipt_ids, scope=[self.host, identifier, identity])
            result['draftAccountIdentity'] = identity
            result['identityPending'] = not identity and (bool(self._draft_identity_pending) or
                                                        bool(self._management_identity(identifier)))
            if not identity:
                result['capabilities'] = dict.fromkeys(result['capabilities'], False)
            return result

    @_entry_write
    def manage_messages(self, identifier, body):
        uuid.UUID(identifier)
        with self.lock:
            session = self.live.setdefault(identifier, Session(identifier))
        with session.action_lock:
            identity = self._management_identity(identifier)
            if not identity or body.get('expectedAccount') != identity:
                raise ValueError('账号接入已变化，请刷新会话后重试')
            body = {k: v for k, v in body.items() if k != 'expectedAccount'}
            prefix = identifier + ':send:'
            rows = [(k[len(prefix):], {**copy.deepcopy(v.get('entry') or {}), **v['result']})
                    for k, v in self.receipts.items() if k.startswith(prefix)]
            updated, result = management_mutate(rows, body, scope=[self.host, identifier, identity])
            old = dict(self.receipts); ordered = []
            for key, value in updated:
                item = copy.deepcopy(old[prefix + key]); item['result']['status'] = value['status']
                item.setdefault('entry', {})['_managementRevision'] = value['_managementRevision']
                if body['action'] == 'edit' and key == body['id']:
                    item['entry']['text'] = value['text']
                    item['entry']['_editId'] = value['_editId']
                    original = item['entry']
                    send_value = {'text': original['text'], 'mode': original['mode'], 'attachments': original.get('attachments', [])}
                    if original.get('skills'):
                        send_value['skills'] = sorted(set(original['skills']))
                    for field in ('workMode', 'goalActivation'):
                        if original.get(field) is not None:
                            send_value[field] = original[field]
                    item['fingerprint'] = hashlib.sha256(json.dumps([identifier, send_value], sort_keys=True).encode()).hexdigest()
                ordered.append((prefix + key, item))
            values = iter(ordered)
            self.receipts = dict(next(values) if k.startswith(prefix) else (k, v) for k, v in old.items())
            try:
                private_json(self.receipts_path, self.receipts)
            except Exception:
                self.receipts = old
                raise
            result.update(self.message_management(identifier))
            return result

    @_entry_write
    def cancel_queued(self, identifier, submission_id):
        uuid.UUID(identifier); uuid.UUID(submission_id)
        with self.session(identifier, attach=False).action_lock:
            row = self.receipts.get(identifier + ':send:' + submission_id)
            if row is None or row['result'].get('status') != 'queued':
                raise ValueError('排队消息已发送或不存在')
            row['result'] = {'status': 'cancelled', 'id': submission_id}
            private_json(self.receipts_path, self.receipts)
            return dict(row['result'])

    @_entry_write
    def _dispatch_queued(self, identifier):
        with self.session(identifier, attach=False).action_lock:
            rows = self._queue(identifier)
            if not rows:
                return
            try:
                _, thread = self._target(identifier)
                if thread.get('status', {}).get('type') != 'idle':
                    return
                row = rows[0]
                if any(thread.get(k) != v for k, v in row.get('target', {}).items()):
                    return
                self._send(identifier, row['text'], row['id'], mode='queue', skills=row.get('skills'),
                           work_mode=row.get('workMode'), attachments=row.get('attachments'), references=row.get('references'), queued=True,
                           goal_activation=row.get('goalActivation'))
            except (IPCError, RemoteUnavailable, ValueError):
                # A queued operation may wait for the original target to return.
                # Once dispatch writes bytes its durable status is already unknown.
                return

    def _permission_options(self, identifier):
        runtime, thread = self._target(identifier)
        facts = linux_permissions.read_permission_facts(runtime.request, thread['cwd'])
        session = self.session(identifier, attach=False)
        with session.condition:
            current = linux_permissions.current_mode(session.state)
        return facts, linux_permissions.permission_options(facts, {'fullAccess': True}, current, native=False)

    def linux_permission_options(self, identifier):
        return self._permission_options(identifier)[1]

    @_entry_write
    def permissions(self, identifier, preset, confirmed=False, *, expected_runtime=None):
        if preset not in ('ask', 'auto-review', 'full-access'):
            raise ValueError('权限设置无效')
        if expected_runtime not in (None, 'cli', 'wsl'):
            raise ValueError('会话运行时已变化，请重新打开权限面板')
        if preset == 'full-access' and confirmed is not True:
            raise ValueError('请确认完全访问权限')
        session = self.session(identifier, attach=False)
        with session.action_lock:
            facts, options = self._permission_options(identifier)
            linux_permissions.require_permission(options, preset)
            runtime, thread = self._target(identifier)
            self._subscribe(identifier, runtime, thread)
            settings = linux_permissions.permission_settings(preset, facts)
            with session.condition:
                before = session.sequence
            runtime.request('thread/settings/update', {'threadId': identifier, **settings})
            with session.condition:
                applied = session.condition.wait_for(lambda: session.sequence > before and linux_permissions.current_mode(session.state) == preset, timeout=5)
                current = linux_permissions.current_mode(session.state)
            return {'applied': True, 'confirmed': applied, 'permissionMode': current}

    @_entry_write
    def compact(self, identifier, request_id):
        session = self.session(identifier, attach=False)
        with session.action_lock:
            def prepare():
                runtime, thread = self._target(identifier)
                if thread['status']['type'] != 'idle' or session.view()['requests']:
                    raise ValueError('请等待当前 Codex 任务结束后再压缩上下文')
                return lambda: runtime.request('thread/compact/start', {'threadId': identifier})
            return self.compact_commands.perform(identifier, request_id, prepare)

    @_entry_write
    def interrupt(self, identifier):
        with self.session(identifier, attach=False).action_lock:
            runtime, _ = self._target(identifier)
            turns = runtime.request('thread/turns/list', {'threadId': identifier, 'limit': 1, 'sortDirection': 'desc'}).get('data', [])
            if not turns or turns[0].get('status') != 'inProgress':
                return {'status': 'idle'}
            turn = turns[0]['id']
            return self._mutation(identifier, identifier + ':stop:' + turn, {'turnId': turn},
                lambda: runtime.request('turn/interrupt', {'threadId': identifier, 'turnId': turn}))

    @_entry_write
    def settings(self, identifier, model, effort, *, fast_mode=None):
        if (not isinstance(model, str) or not model or len(model) > 200 or any(c in model for c in '\r\n\0')
                or effort not in (None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')):
            raise ValueError('模型或推理强度无效')
        if fast_mode is not None and not isinstance(fast_mode, bool):
            raise ValueError('Fast 模式开关无效')
        with self.session(identifier, attach=False).action_lock:
            runtime, thread = self._target(identifier)
            settings = {'model': model, 'effort': effort}
            if fast_mode is not None:
                catalog = self.catalog(identifier, refresh=True, kind='models')
                known = next((m for m in catalog['models'] if m['id'] == model), None)
                if not catalog.get('fastMode', {}).get('allowed') or not known or not known.get('fastTier'):
                    raise ValueError('当前账号、模型或工作区不支持 Fast 模式，请刷新模型设置')
                settings['serviceTier'] = known['fastTier'] if fast_mode else 'default'
            self._subscribe(identifier, runtime, thread)
            runtime.request('thread/settings/update', {'threadId': identifier, **settings})
            self._refresh(self.live[identifier])
            view = self.live[identifier].view()
            return {'applied': True, 'confirmed': view.get('model') == model and view.get('effort') == effort and
                    (fast_mode is None or (self.live[identifier].state.get('latestThreadSettings') or {}).get('serviceTier') == settings['serviceTier'])}

    def catalog(self, identifier, refresh=False, kind=None, **options):
        metadata = self.store.get(identifier)
        if kind is None:
            return {**self.catalog(identifier, refresh, 'models'), **self.catalog(identifier, refresh, 'skills', **options)}
        if kind == 'skills':
            runtime, thread = self._target(identifier)
            rows, errors = self._skills(runtime, thread, refresh)
            ids = set(options.get('ids') or [])
            selected = [r for r in rows if r['id'] in ids]
            query = (options.get('query') or '').strip().casefold()
            rows = [r for r in rows if query in ' '.join(str(r.get(k) or '') for k in ('name', 'displayName', 'description')).casefold()]
            offset, limit = max(0, int(options.get('offset', 0))), min(500, max(1, int(options.get('limit', 200))))
            return {'kind': 'skills', 'skills': rows[offset:offset + limit], 'selectedSkills': selected, 'errors': errors, 'total': len(rows), 'offset': offset, 'limit': limit}
        runtime = self.runtime()
        current = runtime.request('thread/read', {'threadId': identifier, 'includeTurns': False}).get('thread') or {}
        config = runtime.request('config/read', {'includeLayers': False, 'cwd': metadata['cwd']}).get('config') or {}
        provider = config.get('model_provider') or 'openai'
        if (current.get('modelProvider') or metadata.get('model_provider')) != provider:
            return {'kind': 'models', 'models': [], 'modelSource': 'codex',
                    'modelError': '此聊天的提供商与当前默认接入不同，请手动填写原提供商支持的模型 ID',
                    'fastMode': {'allowed': False}}
        value = self.model_catalog.get(identifier, metadata, current, config, refresh,
            lambda: self._models(runtime, metadata, current, config))
        return {**value, 'currentModel': current.get('model') or metadata.get('model') or config.get('model'),
                'currentEffort': current.get('reasoningEffort')}

    def _models(self, runtime, metadata, current, config):
        provider = config.get('model_provider') or 'openai'
        models, cursor = [], None
        for _ in range(10):
            page = runtime.request('model/list', {'limit': 100, 'cursor': cursor, 'includeHidden': False})
            models.extend(page.get('data', [])); cursor = page.get('nextCursor')
            if not cursor:
                break
        try:
            fast_mode = Catalog.fast_mode(runtime.request, metadata['cwd'], provider)
        except IPCError:
            fast_mode = {'allowed': False}
        return {'kind': 'models', 'models': Catalog.normalize_models(models), 'modelSource': 'codex', 'skills': [],
                'currentModel': current.get('model') or metadata.get('model') or config.get('model'),
                'currentEffort': current.get('reasoningEffort'),
                'fastMode': fast_mode, 'accessManagement': 'external'}

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

    @_entry_write
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

    @_entry_write
    def archive(self, identifier, archived, request_id, disconnect=False):
        if type(disconnect) is not bool or (disconnect and archived is not True):
            raise ValueError('聊天归档设置无效')
        def read():
            with self.store.lock:
                self.store.cache.clear()
            metadata = self.store.get(identifier)
            metadata['_archiveFlags'] = self.store.archive_flags(identifier)
            return metadata
        def prepare(metadata):
            identifiers = [row['id'] for row in self.store.lifecycle_threads(root_id=identifier)]
            if any(key.split(':')[0] in identifiers and row['result'].get('status') in ('queued', 'unknown')
                   for key, row in self.receipts.items()):
                raise ValueError('聊天有排队或待核对操作，请先在原 WSL 处理')
            runtime = self.runtime(allow_start=True)
            if not disconnect:
                check_archive_runtime(runtime.request, identifiers)
            with self.lock:
                sessions = [self.live[sid] for sid in identifiers if sid in self.live]
            for session in sessions:
                with session.condition:
                    if session.activating or (not disconnect and session.state.get('requests')):
                        raise ValueError('聊天有运行或等待确认的请求，请先在原 WSL 处理')
            def call():
                if disconnect:
                    from bridge.features.sessions.create import stop_archive_runtime
                    stop_archive_runtime(runtime.request, identifiers)
                    if set(identifiers) != {row['id'] for row in self.store.lifecycle_threads(root_id=identifier)}:
                        raise ValueError('子会话范围已变化，尚未发送归档请求')
                return runtime.request('thread/archive' if archived else 'thread/unarchive', {'threadId': identifier})
            return call
        return {**self.archive_commands.perform(identifier, archived, request_id, read, prepare), 'host': self.host}

    @_entry_write
    def pin(self, identifier, pinned, client_id=None, revision=None):
        uuid.UUID(identifier)
        return {**self.session_pins.set(identifier, pinned, client_id, revision), 'host': self.host}

    @_entry_write
    def rename(self, identifier, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or any(ord(c) < 32 for c in title):
            raise ValueError('聊天名称需为 1–120 个字符，不能包含控制字符')
        self.store.get(identifier)
        self.runtime().request('thread/name/set', {'threadId': identifier, 'name': title.strip()})
        return {'id': identifier, 'title': title.strip(), 'host': self.host}

    @_entry_write
    def organization(self, identifier, change=None):
        uuid.UUID(identifier)
        metadata = self.store.get(identifier)
        runtime = self.runtime(allow_start=False)
        def execute(change):
            return native_organization(runtime.request, identifier, change, expected_cwd=metadata.get('cwd'))
        result = execute(None) if change is None else self.organization_commands.perform(
            identifier, change, lambda: execute(None), execute)
        return {**result, 'host': self.host}

    @_entry_write
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

    @_entry_write
    def upload(self, identifier, upload_id, name, data):
        self.store.get(identifier)
        return self.uploads.put(identifier, upload_id, name, data)

    @_entry_write
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

    side_chat = side_attachment = unsupported
    message_action = unsupported
    desktop_image_preview = unsupported

    def close(self):
        with self.write_lock:
            self.closed.set()
            self._close_features()
            self.model_catalog.close()
            server = self.server
            if server is not None:
                server.close()
        self.worker.join(timeout=3)
