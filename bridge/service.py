import copy
from contextlib import nullcontext
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
import unicodedata
from pathlib import Path

from .terminal import TerminalManager
from .ipc import DesktopIPC, IPCError
from .transport import ipc_endpoint
from .model import apply_patches, computer_use_approval, items_array, normalize_state, normalize_request, ordered_turns, pending_requests, async_requests, request_id, user_display_text
from .store import SessionStore, StoreUnavailable
from .files import artifact_paths, referenced_model_images
from .workspace import operate as workspace_operation
from .catalog import Catalog
from .remote import AppHosts, RemoteStore, RemoteCatalog, RemoteUnavailable, ssh_read, payload
from .create import rename_thread, create_empty, fork_copy, open_in_desktop, CreationError, ForkUnavailable
from .timeline import Timeline
from .account import Account
from .accounts import Accounts, operation
from .goal import GoalRPC, GoalError, GoalUnavailable, GoalUnsupported, goal_activation, goal_identity, normalize_goal_result, normalize_ui_locale, goal_command_fingerprint, goal_state_confirms_command, native_goal_absent, normalize_goal, plan_goal_command, read_native_goal
from .uploads import Uploads, image_type
from .remote import upload_file


class LiveSession:
    def __init__(self, thread_id):
        self.id = thread_id
        self.owner = None
        self.discovering = False
        self.state = None
        self.saved_view = None
        self.history_limit = 20
        self.revision = None
        self.sequence = 0
        self.connected = False
        self.connecting = False
        self.activating = False
        self.last_activation = None
        self.retry_at = 0
        self.error = None
        self.viewers = 0
        self.watched = False
        self.notification_probe_until = 0
        self.activity_only = False
        self.touched = time.monotonic()
        self.condition = threading.Condition(threading.RLock())
        self.attach_lock = threading.Lock()
        self.activation_lock = threading.Lock()
        self.action_lock = threading.RLock()
        self.timeline = Timeline()

    def changed(self):
        self.sequence += 1
        self.condition.notify_all()

    def set_history(self, state):
        self.saved_view = normalize_state(state, False)
        if self.state is None or not self.connected:
            self.state = state
        self.changed()

    def view(self):
        with self.condition:
            result = normalize_state(self.state or {"id": self.id}, self.connected)
            # Native paginated snapshots only contain the loaded tail. Keep the
            # saved prefix in the presentation, never in the IPC patch base.
            if self.saved_view and not result['historyComplete']:
                saved = self.saved_view['turns']
                turns = result['turns']
                first = next((i for i, turn in enumerate(saved) if turns and turn['id'] == turns[0]['id']), None)
                if not turns or first is not None:
                    result['turns'] = saved[:first] + turns if turns else saved
                    result['historyComplete'] = self.saved_view['historyComplete']
            result["savedHistoryMore"] = bool(self.saved_view and not result["historyComplete"] and not self.saved_view["historyComplete"])
            result["sequence"] = self.sequence
            result["connectionError"] = self.error
            result["connecting"] = self.connecting
            result["activating"] = self.activating
            result["loadingHistory"] = self.state is None
            return result


class Bridge:
    def __init__(self, codex_home, data_dir, host="local", alias=None, ipc_path=None, codex_bin=None, goal_rpc=None, owner_goal=None):
        self.host = host
        self.codex_home = Path(codex_home)
        self.data_dir = Path(data_dir)
        self.hosts = AppHosts(codex_home)
        self.remote_bridges = {}
        self.host_errors = []
        self.listed = set()
        self.activity_following = False
        self.notification_cursor = 0
        self.notification_client_id = None
        self.notification_dirty = set()
        self.notification_boundary = set()
        self.notification_event = threading.Event()
        self.store = RemoteStore(alias) if alias else SessionStore(codex_home)
        self.catalog_reader = RemoteCatalog(alias) if alias else Catalog(codex_home, codex_bin)
        self.terminals = TerminalManager()
        self.uploads = Uploads(data_dir, (lambda *args: upload_file(alias, *args)) if alias else None)
        self.account = Account(codex_home, data_dir, codex_bin) if host == 'local' else None
        self.accounts = Accounts(self) if host == "local" else None
        self.goal = goal_rpc or (GoalRPC(codex_home, codex_bin or Catalog.find_runtime()) if host == 'local' else None)
        self.owner_goal = owner_goal
        self.goal_transport = 'owner' if owner_goal else 'sidecar'
        self._open_desktop = open_in_desktop
        self.live = {}
        self.lock = threading.RLock()
        self.ipc = DesktopIPC(ipc_path or ipc_endpoint(codex_home), self._event, self._disconnected)
        self.closed = threading.Event()
        Path(data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ledger_path = Path(data_dir) / "submissions.json"
        self.submit_lock = threading.Lock()
        self.create_lock = threading.Lock()
        self.message_lock = threading.RLock()
        self.actions_path = self.data_dir / 'message-actions.json'
        self.message_actions = json.loads(self.actions_path.read_text(encoding='utf-8')) if self.actions_path.exists() else {}
        self.creations_path = self.data_dir / 'creations.json'
        self.creations = json.loads(self.creations_path.read_text(encoding='utf-8')) if self.creations_path.exists() else {}
        self.submissions = json.loads(self.ledger_path.read_text(encoding='utf-8')) if self.ledger_path.exists() else {}
        self.goal_commands_path = self.data_dir / 'goal-commands.json'
        self.goal_commands = json.loads(self.goal_commands_path.read_text(encoding='utf-8')) if self.goal_commands_path.exists() else {}
        self.goal_lock = threading.RLock()
        for command in self.goal_commands.values():
            if command.get('state') in ('pending', 'sent'):
                command['state'] = 'unknown'

        for key, value in self.submissions.items():
            if value.get("status") == "queued":
                self.live.setdefault(key.split(":")[0], LiveSession(key.split(":")[0]))
        threading.Thread(target=self._maintain, daemon=True).start()
        threading.Thread(target=self._upload_gc, daemon=True).start()

    def _goal_runtime_available(self):
        if self.host != "local":
            return False
        if self.owner_goal is not None:
            return True
        executable = getattr(self.goal, "executable", None)
        return bool(executable and Path(executable).is_file())

    def _save_goal_commands(self):
        target = self.goal_commands_path.with_suffix('.tmp')
        target.write_text(json.dumps(self.goal_commands, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.goal_commands_path)

    def _native_goal_state(self, thread_id, *, strict=False):
        if self.goal:
            try:
                native = self._goal_call('get_goal', thread_id)
                # A successful empty answer is authoritative. Do not let a stale
                # desktop snapshot resurrect a goal that native state has cleared.
                return normalize_goal_result(native)
            except GoalUnsupported:
                pass
            except GoalError:
                if strict:
                    raise
        native = normalize_goal(read_native_goal(self.codex_home, thread_id))
        if strict and native is None and not native_goal_absent(self.codex_home, thread_id):
            raise GoalUnavailable('无法确认原生目标状态，请稍后刷新')
        return native

    def _reconcile_goal_commands(self, thread_id):
        """Promote unknown commands that authoritative native state now proves.

        A goal RPC can succeed while the post-write snapshot read races the
        desktop owner. That outcome is durable and unknown, but it must not be
        replayed. Later reads may safely close the ledger when SQLite or
        GoalRPC proves the requested state.
        """
        with self.submit_lock:
            pending = [(key, copy.deepcopy(value)) for key, value in self.goal_commands.items()
                       if key.startswith(thread_id + ':') and value.get('state') in ('unknown', 'sent', 'pending')]
        if not pending:
            return
        try:
            native = self._native_goal_state(thread_id, strict=True)
        except GoalError:
            return
        confirmed_keys = []
        for key, command in pending:
            objective = (command.get('objective') or '').strip()
            action = command.get('action')
            proved = goal_state_confirms_command(command, native)
            if proved:
                confirmed_keys.append((key, command))
        if not confirmed_keys:
            return
        with self.submit_lock:
            for key, command in confirmed_keys:
                stored = self.goal_commands.get(key)
                if not stored or stored.get('state') not in ('unknown', 'sent', 'pending'):
                    continue
                action = command.get('action')
                status = {'create': 'active', 'pause': 'paused', 'resume': 'active',
                          'cancel': 'cancelled', 'edit': 'paused'}.get(action)
                stored['state'] = 'confirmed'; stored['sent'] = True
                stored['confirmedAt'] = time.time()
                stored['response'] = {'status': status, 'confirmed': True,
                                      'reconciled': True, 'result': native, 'transitionId': key.split(':')[-1],
                                      'uiLocale': stored.get('uiLocale')}
                if action == 'cancel':
                    objective = (command.get('objective') or '').strip()
                    for old_key, old in self.goal_commands.items():
                        if (old_key.startswith(thread_id + ':') and old.get('action') == 'create' and
                                old.get('objective', '').strip() == objective):
                            old['goalCancelled'] = True
                self._save_goal_commands()

    def _goal_command(self, session, thread_id, request_id, action, *, objective=None, status=None, ui_locale=None, expected=None):
        uuid.UUID(request_id)
        if action not in ('create', 'pause', 'resume', 'cancel', 'edit'):
            raise ValueError('目标操作无效')
        key = thread_id + ':' + request_id
        if expected is not None and not isinstance(expected, dict):
            raise ValueError('目标状态无效')
        objective = (objective or '').strip()
        if action in ('create', 'pause', 'resume', 'edit') and not objective:
            raise ValueError('当前聊天没有目标')
        if len(objective) > 4000:
            raise ValueError('目标内容不能超过 4000 字')
        fingerprint = goal_command_fingerprint(action, objective, status)
        expected_status = {'create': 'active', 'resume': 'active', 'pause': 'paused', 'edit': 'paused'}.get(action)
        # Goal commands mutate one native object and are observed asynchronously,
        # so serialize every bridge-side transition before checking the ledger.
        with self.goal_lock:
            self._reconcile_goal_commands(thread_id)
            native = self._native_goal_state(thread_id, strict=True)
            with self.submit_lock:
                command = self.goal_commands.get(key)
                if command:
                    if command.get('fingerprint') != fingerprint:
                        raise ValueError('同一目标操作标识不能用于不同内容')
                    if command.get('state') == 'confirmed':
                        response = copy.deepcopy(command.get('response') or {})
                        response.update({'status': response.get('status') or expected_status or 'cancelled',
                                         'confirmed': True, 'duplicate': True})
                        return response
                    if command.get('state') == 'failed':
                        raise IPCError(command.get('error') or '目标操作失败')
                    # A durable sent/pending command has an unknown delivery result
                    # after restart. The caller may inspect state, but replaying it
                    # automatically could create or clear the wrong goal.
                    return {'status': 'unknown', 'confirmed': False, 'duplicate': True,
                            'error': command.get('error') or '目标操作结果尚未确认'}
                if expected is not None and (goal_identity(expected) != goal_identity(native) or expected.get('status') != (native or {}).get('status')):
                    raise ValueError('目标已发生变化，请刷新后重试')
                command = {'before': native, 'action': action, 'workMode': 'goal', 'objective': objective, 'status': status,
                           'uiLocale': normalize_ui_locale(ui_locale), 'state': 'pending', 'sent': False,
                           'at': time.time(), 'fingerprint': fingerprint}
                self.goal_commands[key] = command
                self._save_goal_commands()

            def confirm(response, *, duplicate=False):
                with self.submit_lock:
                    command['state'] = 'confirmed'; command['sent'] = True
                    if not duplicate:
                        response['transitionId'] = request_id
                    command['confirmedAt'] = time.time(); command['response'] = response
                    if duplicate:
                        response['duplicate'] = True
                    self._save_goal_commands()
                with session.condition:
                    session.changed()
                return response

            def fail(message, *, unknown=False):
                with self.submit_lock:
                    command['state'] = 'unknown' if unknown else 'failed'
                    command['error'] = message; command['sent'] = command.get('sent', False)
                    command['response'] = {'status': 'unknown' if unknown else (expected_status or 'cancelled'),
                                           'confirmed': False, 'error': message}
                    self._save_goal_commands()
                with session.condition:
                    session.changed()

            decision, decision_error, _expected = plan_goal_command(action, objective, native, status=status)
            if decision == 'duplicate':
                return confirm({'status': expected_status or 'cancelled', 'confirmed': True,
                                'result': native, 'uiLocale': command['uiLocale']}, duplicate=True)
            if decision == 'invalid':
                fail(decision_error)
                raise ValueError(decision_error)

            rpc_methods = {'create': ('set_goal', objective), 'pause': ('set_goal_status', 'paused'),
                           'resume': ('set_goal_status', 'active'), 'cancel': ('clear_goal', None), 'edit': ('edit_goal', objective)}
            rpc_method, rpc_arg = rpc_methods[action]
            with self.submit_lock:
                command['sent'] = True; command['state'] = 'sent'; command['attemptedAt'] = time.time()
                self._save_goal_commands()
            with session.condition:
                session.changed()
            try:
                if action == 'edit':
                    result = self._goal_call('edit_goal', thread_id, objective, (native or {}).get('tokenBudget'))
                elif rpc_arg is None:
                    result = self._goal_call(rpc_method, thread_id)
                else:
                    result = self._goal_call(rpc_method, thread_id, rpc_arg)
            except GoalUnsupported as exc:
                fail('桌面 Codex 版本不支持原生目标模式')
                raise IPCError('桌面 Codex 版本不支持原生目标模式') from exc
            except GoalError as exc:
                # Timeout and disconnect leave delivery unknown; ordinary native
                # errors are deterministic failures. Neither is replayed by id.
                unknown = isinstance(exc, GoalUnavailable) or 'timeout' in str(exc).lower() or '超时' in str(exc)
                fail(str(exc), unknown=unknown)
                raise IPCError(str(exc)) from exc

            try:
                native = self._native_goal_state(thread_id, strict=True)
            except GoalError as exc:
                fail(str(exc), unknown=True)
                raise IPCError('目标操作已发送，但状态尚未确认') from exc
            confirmed = goal_state_confirms_command(command, native)
            if action == 'cancel':
                if confirmed:
                    with self.submit_lock:
                        for old_key, old in self.goal_commands.items():
                            if old_key.startswith(thread_id + ':') and old.get('action') == 'create' and old.get('objective', '').strip() == objective:
                                old['goalCancelled'] = True
                        self._save_goal_commands()
                    with session.condition:
                        if session.state is not None:
                            session.state['threadGoal'] = None
                        session.changed()
                    return confirm({'status': 'cancelled', 'confirmed': True, 'result': result})
            elif confirmed:
                return confirm({'status': expected_status, 'confirmed': True, 'result': native,
                                'uiLocale': command['uiLocale']})
            fail('目标操作已发送，但状态尚未确认', unknown=True)
            raise IPCError('目标操作已发送，但状态尚未确认')

    def _upload_gc(self):
        while not self.closed.wait(60):
            try:
                with self.submit_lock:
                    protected=set()
                    for value in self.submissions.values():
                        if value.get("status") in ("queued", "unknown", "accepted"):
                            protected.update(value.get("attachments") or [])
                removed=self.uploads.collect(protected=protected)
                if removed:
                    logging.getLogger(__name__).info("Removed %d expired upload(s)", removed)
            except Exception:
                logging.getLogger(__name__).exception("Upload cleanup failed")

    def _save_creations(self):
        target = self.creations_path.with_suffix('.tmp')
        target.write_text(json.dumps(self.creations, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.creations_path)

    @operation
    def create_chat(self, project_key, title, request_id):
        if not isinstance(request_id, str):
            raise ValueError('创建请求标识无效')
        uuid.UUID(request_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise ValueError('请输入 1–120 字的聊天名称')
        project = next((p for p in self.hosts.projects() if p['key'] == project_key), None)
        if project is None:
            raise ValueError('请选择电脑 App 中已保存的项目')
        title = title.strip()
        with self.create_lock:
            entry = self.creations.get(request_id)
            if entry:
                if entry['project'] != project_key or entry['title'] != title:
                    raise ValueError('同一创建请求不能用于不同内容')
                if not entry.get('id'):
                    raise CreationError('上次创建结果尚不确定，请先刷新聊天列表并检查电脑 App，避免重复创建')
            else:
                self.ipc.connect()
                entry = {'project': project_key, 'title': title, 'host': project['host'], 'at': time.time()}
                self.creations[request_id] = entry
                self._save_creations()
                if project['host'] == 'local':
                    thread_id = create_empty(self.catalog_reader.executable, self.codex_home, project['cwd'], title)
                else:
                    host = self.hosts.hosts()[project['host']]
                    source = Path(__file__).with_name('create.py').read_text(encoding='utf-8')
                    source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                    source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                    source += 'print(json.dumps({"id":create_empty(runtime, home, **' + payload({'cwd': project['cwd'], 'title': title}) + ')}))\n'
                    thread_id = ssh_read(host['alias'], source, timeout=90)['id']
                uuid.UUID(thread_id)
                entry['id'] = thread_id
                self._save_creations()
            bridge = self.for_host(entry['host'])
            if isinstance(bridge.store, RemoteStore):
                with bridge.store.lock:
                    bridge.store.cache.clear()
            try:
                open_in_desktop(entry['id'], entry['host'])
                opened = True
            except (OSError, CreationError, subprocess.SubprocessError):
                opened = False
            return {'id': entry['id'], 'host': entry['host'], 'opened': opened,
                    'message': '已创建，正在连接桌面 App' if opened else '聊天已创建，请在电脑 App 打开后重新连接'}

    def _save_actions(self):
        target = self.actions_path.with_suffix('.tmp')
        target.write_text(json.dumps(self.message_actions, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.actions_path)

    def _fork_origin(self, thread_id):
        with self.message_lock:
            entry = next((v for v in self.message_actions.values() if v.get('id') == thread_id and v['action'] != 'edit'), None)
            return {'id': entry['source'], 'title': entry['sourceTitle'], 'turnId': entry['turnId'], 'host': self.host} if entry else None

    @staticmethod
    def _fork_settings(state):
        result = {'modelProvider': state.get('modelProvider'), 'model': state.get('latestModel')}
        if not all(result.values()):
            raise ValueError('尚未取得原会话的模型配置，请重新连接')
        effort = state.get('latestReasoningEffort') or (state.get('latestThreadSettings') or {}).get('effort')
        if effort:
            result['config'] = {'model_reasoning_effort': effort}
        latest = state.get('latestThreadSettings') or {}
        if 'serviceTier' in latest:
            result['serviceTier'] = latest['serviceTier']
        permissions = state.get('currentPermissions') or {}
        profile = latest.get('activePermissionProfile', permissions.get('activePermissionProfile'))
        if profile and profile.get('id'):
            result['permissions'] = profile['id']
        for key in ('approvalPolicy', 'approvalsReviewer', 'runtimeWorkspaceRoots'):
            if key in permissions:
                result[key] = permissions[key]
        return result

    @operation
    def message_action(self, thread_id, body):
        action, identifier = body.get('action'), body.get('id')
        if action not in ('edit', 'fork', 'edit-fork') or not isinstance(identifier, str):
            raise ValueError('消息操作无效')
        uuid.UUID(identifier)
        text = body.get('text') if action != 'fork' else None
        if action != 'fork' and (not isinstance(text, str) or not text.strip() or len(text) > 100000):
            raise ValueError('请输入 1–100000 字的消息')
        identity = {k: body.get(k) for k in ('action', 'key', 'version')}
        identity.update(source=thread_id, text=text)
        # Durable intent precedes every mutation. An uncertain native edit has no
        # idempotency key, so neither refresh nor a repeated POST may replay it.
        with self.message_lock:
            entry = self.message_actions.get(identifier)
            if entry:
                if any(entry.get(k) != v for k, v in identity.items()):
                    raise ValueError('同一操作标识不能用于不同内容')
                return self._action_result(entry)
            session = self._target(thread_id)
            with session.action_lock:
                with session.condition:
                    session.timeline.update(session.view())
                    position = session.timeline.position(body.get('key', ''))
                    if position is None:
                        raise ValueError('消息位置已变化，请刷新后重试')
                    row = session.timeline.rows[position]
                    if row['version'] != body.get('version'):
                        raise ValueError('消息内容已变化，请重新打开编辑')
                    if action == 'fork' and not row['forkable'] or action != 'fork' and not row['editable']:
                        raise ValueError('此消息不支持该操作')
                    if row.get('turnStatus') == 'inProgress':
                        raise ValueError('请先停止当前任务，再编辑或分支')
                    if action == 'edit':
                        if session.timeline.meta['latestUserTurnId'] != row['turnId']:
                            raise ValueError('只有最近一条消息可原地编辑，请使用编辑并新建分支')
                        if session.state.get('threadRuntimeStatus', {}).get('type') == 'active':
                            raise ValueError('请先停止当前任务，再编辑或分支')
                    snapshot = copy.deepcopy(session.state)
                    entry = {**identity, 'turnId': row['turnId'], 'sourceTitle': snapshot.get('title') or '未命名聊天',
                             'status': 'unknown', 'at': time.time()}
                    settings = self._fork_settings(snapshot) if action != 'edit' else None
                self.message_actions[identifier] = entry
                self._save_actions()
                if action != 'edit':
                    title = (entry['sourceTitle'][:90] + ' · 分支')
                    args = dict(cwd=snapshot['cwd'], source_id=thread_id, turn_id=entry['turnId'], title=title, settings=settings)
                    try:
                        if self.host == 'local':
                            child = fork_copy(self.catalog_reader.executable, self.codex_home, **args)
                        else:
                            host = self.hosts.hosts()[self.host]
                            source = Path(__file__).with_name('create.py').read_text(encoding='utf-8')
                            source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                            source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                            source += 'try:\n print(json.dumps({"id":fork_copy(runtime, home, **' + payload(args) + ')}))\n'
                            source += 'except ForkUnavailable as exc:\n print(json.dumps({"unavailable":str(exc)}))\n'
                            result = ssh_read(host['alias'], source, timeout=120)
                            if 'unavailable' in result:
                                raise ForkUnavailable(result['unavailable'])
                            child = result['id']
                    except ForkUnavailable:
                        del self.message_actions[identifier]
                        self._save_actions()
                        raise
                    uuid.UUID(child)
                    if child == thread_id:
                        raise CreationError('分支结果无效，请检查桌面聊天列表')
                    entry.update(id=child, status='created')
                    self._save_actions()
                    if isinstance(self.store, RemoteStore):
                        with self.store.lock:
                            self.store.cache.clear()
                    if action == 'fork':
                        return self._action_result(entry)
                    try:
                        target = self._target(child)
                    except (IPCError, OSError, CreationError, RemoteUnavailable):
                        # Creation succeeded; return the child and the unsent draft.
                        # A new edit operation on that child is safe after activation.
                        return self._action_result(entry)
                else:
                    target = session
                entry['status'] = 'unknown'
                self._save_actions()
                params = {'turnId': entry['turnId'], 'message': text, 'shouldSendPermissionOverrides': False}
                tier = (snapshot.get('latestThreadSettings') or {})
                if 'serviceTier' in tier:
                    params['serviceTier'] = tier['serviceTier']
                self._call(target, 'thread-follower-edit-last-user-turn', params, timeout=90)
                entry['status'] = 'accepted'
                self._save_actions()
                return self._action_result(entry)

    def _action_result(self, entry):
        return {'status': entry['status'], 'id': entry.get('id', entry['source']), 'host': self.host,
                'action': entry['action'], 'draft': entry['text'] if entry['status'] == 'created' and entry['action'] == 'edit-fork' else None,
                'source': {'id': entry['source'], 'title': entry['sourceTitle'], 'turnId': entry['turnId']}}

    def for_host(self, host):
        if host == self.host:
            return self
        available = self.hosts.hosts()
        if self.host != "local" or host not in available:
            raise KeyError("未知 SSH 主机")
        with self.lock:
            if host not in self.remote_bridges:
                folder = self.data_dir / 'hosts' / hashlib.sha256(host.encode()).hexdigest()[:16]
                self.remote_bridges[host] = Bridge(self.codex_home, folder, host, available[host]['alias'], ipc_path=self.ipc.path)
                self.remote_bridges[host].accounts = self.accounts
            return self.remote_bridges[host]

    def list(self, query="", limit=100, offset=0, archived=False):
        sources = [(self, "此电脑")]
        if self.host == 'local':
            sources.extend((self.for_host(host), info.get('displayName') or info['alias']) for host, info in self.hosts.hosts().items())
        def read(source):
            bridge, label = source
            try:
                rows = bridge.store.list(limit=limit + offset, archived=archived, query=query)
                rows = self.hosts.decorate(rows, bridge.host, label)
                with bridge.lock:
                    bridge.listed.update(row['id'] for row in rows)
                    for row in rows:
                        session = bridge.live.get(row['id'])
                        row['connected'] = bool(session and session.connected)
                        row['title'] = row.get('name') or row.get('title') or '未命名聊天'
                return rows, None
            except (RemoteUnavailable, StoreUnavailable) as exc:
                return [], {'host': bridge.host, 'label': label, 'error': str(exc)}
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
            results = list(pool.map(read, sources))
        self.host_errors = [error for _, error in results if error]
        rows = [row for group, _ in results for row in group]
        rows.sort(key=lambda row: (row['recency'], row['id'], row['host']), reverse=True)
        return rows[offset:offset + limit]

    def activity(self, identifiers):
        if not isinstance(identifiers, list) or len(identifiers) > 500 or any(not isinstance(v, str) for v in identifiers):
            raise ValueError('会话状态请求无效')
        with self.lock:
            sessions = []
            for identifier in dict.fromkeys(identifiers):
                if identifier not in self.listed:
                    continue
                session = self.live.get(identifier)
                if session is None:
                    session = self.live[identifier] = LiveSession(identifier)
                    session.activity_only = True
                session.touched = time.monotonic()
                sessions.append(session)
            pending = [s for s in sessions if not s.connected and time.monotonic() >= s.retry_at]
            if pending and not self.activity_following:
                self.activity_following = True
                def follow():
                    try:
                        for session in pending:
                            if self.closed.is_set(): break
                            session.retry_at = time.monotonic() + 15
                            self._attach(session, timeout=0)
                    finally:
                        with self.lock: self.activity_following = False
                threading.Thread(target=follow, daemon=True).start()
        try:
            recencies = self.store.recencies([s.id for s in sessions])
        except (OSError, ValueError, StoreUnavailable, RemoteUnavailable):
            recencies = {}
        rows = []
        for session in sessions:
            with session.condition:
                state = session.state or {}
                turns = ordered_turns(state)
                last = next((t for t in reversed(turns) if t.get('turnId')), {})
                rows.append({'id': session.id, 'host': self.host, 'recency': recencies.get(session.id, 0), 'connected': session.connected,
                             'status': state.get('threadRuntimeStatus', {}).get('type') if session.connected else 'unknown',
                             'turnId': last.get('turnId'), 'turnStatus': last.get('status')})
        return rows

    def notification_candidates(self):
        sources = [self]
        if self.host == 'local':
            sources.extend(self.for_host(host) for host in self.hosts.hosts())
        rows = []
        for source in sources:
            source.notification_event = self.notification_event
            try:
                # A new IPC connection needs one fresh discovery, even if the
                # desktop resumed a task without changing its saved metadata.
                source.ipc.connect()
                if source.notification_client_id != source.ipc.client_id:
                    source.notification_cursor = 0
                    source.notification_boundary.clear()
                    source.notification_client_id = source.ipc.client_id
                changes = source.store.notification_changes(source.notification_cursor)
                cursor = max([source.notification_cursor] + [r['changed_at'] for r in changes])
                for row in changes:
                    if row['changed_at'] > source.notification_cursor or row['id'] not in source.notification_boundary:
                        rows.append({'id': row['id'], 'host': source.host})
                source.notification_boundary = {r['id'] for r in changes if r['changed_at'] == cursor}
                source.notification_cursor = cursor
            except (IPCError, RemoteUnavailable, StoreUnavailable):
                pass  # Preserve the cursor so the next discovery can recover.
        return rows

    def notification_updates(self):
        sources = [self] + list(self.remote_bridges.values()) if self.host == 'local' else [self]
        rows = []
        for source in sources:
            with source.lock:
                identifiers = source.notification_dirty
                source.notification_dirty = set()
                sessions = [source.live[i] for i in identifiers if i in source.live]
            for session in sessions:
                with session.condition:
                    state = session.state or {}
                    if session.connected and (session.watched or state.get('threadRuntimeStatus', {}).get('type') == 'active' or
                            any(t.get('status') == 'inProgress' for t in ordered_turns(state)) or pending_requests(state)):
                        rows.append({'id': session.id, 'host': source.host})
        return rows

    def release_notification_session(self, session):
        # A follow may be waiting for a snapshot. Never invert its lock order
        # against the notification worker holding session.condition.
        if not session.attach_lock.acquire(blocking=False):
            return False
        try:
            session.watched = False
            with self.submit_lock:
                queued = any(k.startswith(session.id + ':') and v['status'] == 'queued' for k, v in self.submissions.items())
            with session.condition:
                if session.viewers or queued or not session.activity_only:
                    return True
                if session.connected or session.discovering:
                    try:
                        self.ipc.follow(session.id, session.owner, False, host=self.host)
                    except IPCError:
                        pass
                session.connected = False
                session.discovering = False
                session.revision = None
            return True
        finally:
            session.attach_lock.release()

    def notification_session(self, thread_id):
        # Follow only; never parse saved history or activate an unloaded owner.
        with self.lock:
            session = self.live.get(thread_id)
            if session is None:
                session = self.live[thread_id] = LiveSession(thread_id)
                session.activity_only = True
        if not session.watched:
            session.notification_probe_until = time.monotonic() + 10
        session.watched = True
        if not session.connected and time.monotonic() >= session.retry_at:
            session.retry_at = time.monotonic() + 15
            self._attach(session, timeout=0)
        return session

    def upload(self, thread_id, identifier, name, data):
        self.store.get(thread_id)  # Uploads do not activate a desktop chat.
        return self.uploads.put(thread_id, identifier, name, data)

    def upload_thumb(self, thread_id, identifier, data, width, height):
        self.store.get(thread_id)
        return self.uploads.set_thumb(thread_id, identifier, data, width, height)

    def upload_preview(self, thread_id, identifier, variant='thumb'):
        self.store.get(thread_id)
        return self.uploads.preview(thread_id, identifier, variant)

    def session(self, thread_id, attach=True, background=False, force=False):
        uuid.UUID(thread_id)
        with self.lock:
            session = self.live.get(thread_id)
        if session is None:
            # SSH/SQLite reads must not block IPC events for other chats.
            self.store.get(thread_id)
            with self.lock:
                session = self.live.setdefault(thread_id, LiveSession(thread_id))
        session.touched = time.monotonic()
        if background:
            if attach:
                self._refresh_async(session, force)
            return session
        if attach and not session.connected:
            self._attach(session)
        if session.state is None or session.activity_only:
            fallback = self._saved_history(session)
            with session.condition:
                if session.state is None or session.activity_only:
                    session.set_history(fallback)
                    session.activity_only = False
        return session

    def _saved_history(self, session):
        return self.store.history(session.id, turn_limit=session.history_limit)

    def _refresh_async(self, session, force=False):
        with session.condition:
            if (session.connected and not session.activity_only) or session.connecting or self.closed.is_set():
                return
            if not force and time.monotonic() < session.retry_at and not session.activity_only:
                return
            needs_history = session.state is None or session.activity_only
            session.connecting = True
            session.changed()
        def refresh():
            # Owner discovery and saved-history IO progress independently.
            attachment = threading.Thread(target=self._attach, args=(session,), daemon=True)
            attachment.start()
            try:
                if needs_history:
                    try:
                        fallback = self._saved_history(session)
                        with session.condition:
                            session.set_history(fallback)
                    except (OSError, ValueError, KeyError, RemoteUnavailable, StoreUnavailable):
                        # A missing saved rollout must not prevent a live snapshot.
                        logging.getLogger(__name__).warning("Saved history unavailable; trying desktop snapshot")
                    finally:
                        session.activity_only = False
            except Exception:
                logging.getLogger(__name__).exception("Background session read failed")
                with session.condition:
                    session.error = "读取会话失败，请重新连接或查看网关日志。"
            finally:
                attachment.join()
                with session.condition:
                    session.connecting = False
                    session.retry_at = time.monotonic() + 15
                    session.changed()
        threading.Thread(target=refresh, daemon=True).start()

    def _attach(self, session, timeout=1.5):
        with session.attach_lock:
            if session.connected or self.closed.is_set():
                return
            try:
                # Owners answer a follow with a fresh snapshot. This also works
                # for remote/background owners absent from owner-discovery.
                self.ipc.connect()
                with session.condition:
                    session.owner = None
                    session.revision = None
                    session.discovering = True
                self.ipc.follow(session.id, None, host=self.host)
                with session.condition:
                    session.condition.wait_for(lambda: session.connected or self.closed.is_set(), timeout=timeout)
                    # Keep following: a cold chat can publish after this short
                    # wait. Missing live state does not make saved history fail.
            except IPCError as exc:
                with session.condition:
                    session.discovering = False
                    session.connected = False
                    session.error = str(exc)
                    session.changed()

    @operation
    def activate(self, thread_id):
        """Explicit phone operation only; reads and notification watches never navigate."""
        session = self.session(thread_id, background=True)
        with session.activation_lock:
            if session.connected:
                return session
            with session.condition:
                session.activating = True
                session.changed()
            try:
                # Reuse an in-flight passive attach before considering navigation.
                self._attach(session)
                if session.connected:
                    return session
                if self.closed.is_set():
                    raise IPCError('网关正在停止；操作未发送，请稍后重试。')
                if session.last_activation is not None and time.monotonic() - session.last_activation < 20:
                    raise IPCError(session.error or '桌面仍在加载此聊天；操作未发送，请稍后重试。')
                session.last_activation = time.monotonic()
                try:
                    open_in_desktop(session.id, self.host)
                except (OSError, CreationError, subprocess.SubprocessError) as exc:
                    raise IPCError('无法在电脑 Codex 中加载此聊天；操作未发送，请检查 Codex 是否已安装并运行。') from exc
                deadline = time.monotonic() + 20
                while not session.connected and not self.closed.is_set() and time.monotonic() < deadline:
                    self._attach(session, timeout=.75)
                    if not session.connected:
                        self.closed.wait(.25)
                if not session.connected:
                    raise IPCError('桌面仍在加载此聊天；操作未发送，请稍后重试。')
                return session
            except IPCError as exc:
                with session.condition:
                    session.error = str(exc)
                raise
            finally:
                with session.condition:
                    session.activating = False
                    session.changed()

    def _event(self, message):
        if message.get("method") == "client-status-changed":
            params = message.get("params", {})
            if params.get("status") == "disconnected":
                with self.lock:
                    sessions = list(self.live.values())
                for session in sessions:
                    if session.owner == params.get("clientId"):
                        self._invalidate(session, "桌面会话连接已断开，正在等待重连")
            # Owner changes can make previously unanswered probes available.
            self.notification_cursor = 0
            self.notification_boundary.clear()
            self.notification_event.set()
            return
        if message.get("method") != "thread-stream-state-changed":
            return
        if message.get("version") != 11:
            self._disconnected()
            return
        params = message.get("params", {})
        if params.get("hostId") != self.host:
            return
        with self.lock:
            session = self.live.get(params.get("conversationId"))
        if session is None:
            return
        change = params.get("change", {})
        with session.condition:
            if session.discovering and change.get("type") == "snapshot" and (change.get("conversationState") or {}).get("id") == session.id:
                session.owner = message.get("sourceClientId")
                session.discovering = False
            if not session.owner or message.get("sourceClientId") != session.owner:
                return
            try:
                if change.get("type") == "snapshot":
                    state = change["conversationState"]
                    if state.get("id") != session.id:
                        raise ValueError("Session mismatch")
                    session.state = state
                elif change.get("type") == "patches":
                    if session.revision is None or change.get("baseRevision") != session.revision:
                        raise ValueError("Revision mismatch")
                    session.state = apply_patches(session.state, change["patches"])
                else:
                    return
                session.revision = change["revision"]
                session.connected = True
                session.error = None
                session.changed()
                with self.lock:
                    self.notification_dirty.add(session.id)
                self.notification_event.set()
            except (ValueError, KeyError, IndexError, TypeError):
                session.connected = False
                session.revision = None
                session.error = "同步状态发生变化，正在重新读取桌面快照"
                session.changed()

    def _invalidate(self, session, message):
        with session.condition:
            session.connected = False
            session.revision = None
            session.error = message
            session.changed()

    def _disconnected(self):
        self.notification_cursor = 0
        self.notification_boundary.clear()
        self.notification_event.set()
        with self.lock:
            sessions = list(self.live.values())
        for session in sessions:
            self._invalidate(session, "桌面 App 连接已断开，正在等待重连")

    def _maintain(self):
        delay = 3
        while not self.closed.wait(delay):
            if self.accounts is not None:
                try:
                    self.accounts.check_ready()
                except ValueError:
                    continue
            with self.lock:
                sessions = list(self.live.values())
            for session in sessions:
                with self.submit_lock:
                    queued = [(k, dict(v)) for k, v in self.submissions.items() if k.startswith(session.id + ":") and v["status"] == "queued"]
                if queued and session.connected and session.view().get("status") == "idle":
                    key, entry = queued[0]
                    try:
                        self._send_queued(session, key, entry)
                    except Exception:
                        pass  # Unknown outcomes stay recorded and are never automatically replayed.
                if (session.viewers > 0 or queued or session.watched) and not session.connected:
                    if session.activity_only and session.viewers == 0 and not queued:
                        if time.monotonic() >= session.retry_at:
                            session.retry_at = time.monotonic() + 15
                            self._attach(session, timeout=0)
                    else:
                        self._refresh_async(session)
                elif session.viewers == 0 and not queued and not session.watched and time.monotonic() - session.touched > 300:
                    with self.lock:
                        if session.viewers != 0 or session.watched:
                            continue
                        self.live.pop(session.id, None)
                    if session.connected or session.discovering:
                        try:
                            self.ipc.follow(session.id, session.owner, False, host=self.host)
                        except IPCError:
                            pass
            delay = 3 if self.ipc.client_id else min(30, delay * 2)

    def _target(self, thread_id):
        session = self.activate(thread_id)
        with session.condition:
            if not session.connected or not session.owner:
                raise IPCError(session.error or "请先在桌面 App 打开此聊天")
        return session

    def _check_provider(self, session):
        if self.host == 'local' and self.accounts and self.accounts.active_matches():
            row = self.accounts.row(self.accounts.index['activeId'])
            expected = ('openai',) if row['kind'] == 'chatgpt' else ('openai', 'bridge_api')
            if session.view().get('provider') not in expected:
                raise ValueError('此聊天保留了原提供商，请切回对应接入或新建聊天')

    @operation
    def _call(self, session, method, params, timeout=30):
        self._check_provider(session)
        return self.ipc.request(method, {"conversationId": session.id, **params}, target=session.owner, host=self.host, timeout=timeout)["result"]

    def _save_ledger(self):
        # Keep accepted or uncertain submissions durable across process restarts.
        target = self.ledger_path.with_suffix(".tmp")
        target.write_text(json.dumps(self.submissions, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.ledger_path)

    def view(self, thread_id, attach=True, background=False, force=False):
        session = self.session(thread_id, attach=attach, background=background, force=force)
        view = session.view()
        view["host"] = self.host
        view["hostLabel"] = "此电脑" if self.host == "local" else self.hosts.hosts().get(self.host, {}).get("displayName", self.host)
        view["goalTransport"] = self.goal_transport
        view["goalRuntimeAvailable"] = self._goal_runtime_available()
        view["goalActivationIds"] = sorted(self._goal_activation_ids(thread_id))
        with session.condition:
            artifacts = artifact_paths(session.state or {}, self.store.home) if self.host == "local" else {}
        view["files"] = [{"id": k, "name": v["name"], "reference": v["reference"], "image": v["image"]} for k, v in artifacts.items()]
        self._overlay_native_goal(session, thread_id, view)
        self._submission_meta(session, view)
        view["forkedFrom"] = self._fork_origin(thread_id)
        return view

    def _latest_created_goal_objective(self, thread_id):
        with self.submit_lock:
            commands = [v for k, v in self.goal_commands.items()
                        if k.startswith(thread_id + ":") and v.get("action") == "create"]
        latest = max(commands, key=lambda v: v.get("at", 0), default=None)
        return (latest or {}).get("objective", "")

    def _cancelled_goal_objective(self, thread_id, objective=None):
        """Return true when the newest ledger entry for this objective was cancelled."""
        objective_key = (objective or "").strip()
        if not objective_key:
            return False
        with self.submit_lock:
            entries = []
            for key, value in self.goal_commands.items():
                if key.startswith(thread_id + ":") and (value.get("workMode") == "goal" or value.get("action") == "cancel") and value.get("objective", "").strip() == objective_key:
                    entries.append({**value, 'goalCancelled': value.get('goalCancelled', value.get('action') == 'cancel')})
            for key, value in self.submissions.items():
                if key.startswith(thread_id + ":") and value.get("workMode") == "goal" and value.get("text", "").strip() == objective_key:
                    entries.append(value)
        latest = max(entries, key=lambda v: v.get("at", 0), default=None)
        return bool(latest and latest.get("goalCancelled"))

    def _submission_meta(self, session, view):
        with self.submit_lock:
            entries = [(k.split(":")[1], copy.deepcopy(v)) for k, v in self.submissions.items()
                       if k.startswith(session.id + ":")]
            # Native goal creates live in the goal-command ledger, while older
            # prompt-based goals remain in submissions. Merge both by request id
            # so a newly created native goal cannot be shadowed by a stale entry.
            merged = {key: value for key, value in entries}
            for key, value in self.goal_commands.items():
                if not key.startswith(session.id + ":") or value.get('action') != 'create':
                    continue
                command_id = key.split(':', 2)[-1]
                command_entry = {
                    'text': value.get('objective', ''), 'status': 'accepted', 'at': value.get('at', 0),
                    'workMode': 'goal', 'goalConfirmed': bool((value.get('response') or {}).get('confirmed')),
                    'goalCancelled': bool(value.get('goalCancelled')), 'goalSource': 'command',
                    'goalState': value.get('state'),
                }
                old = merged.get(command_id)
                if not old or command_entry['at'] >= old.get('at', 0):
                    merged[command_id] = command_entry
        activation_ids = self._goal_activation_ids(session.id)
        view["submissions"] = [{"id": k, "text": v["text"], "status": v["status"], **({'attachments': v['attachmentNames']} if v.get('attachmentNames') else {})}
                               for k, v in entries if v["status"] in ("queued", "unknown")
                               and k not in activation_ids]
        view["goalSubmission"] = None
        goal = view.get('goal')
        goal_commands = [(k.split(':',2)[-1], copy.deepcopy(v)) for k, v in self.goal_commands.items()
                         if k.startswith(session.id + ':') and (v.get('workMode') == 'goal' or v.get('action') == 'cancel')]
        latest_goal_command = max(goal_commands, key=lambda pair: pair[1].get('at', 0), default=None)
        if latest_goal_command and latest_goal_command[1].get('action') == 'cancel':
            return
        # The direct GoalRPC connection can leave the desktop owner's in-memory
        # thread goal behind after the owner deletes or completes it. Once
        # native storage is readable and proves absence, do not turn that stale
        # snapshot into a mobile-only "unconfirmed" dead end.
        if (latest_goal_command and latest_goal_command[1].get('action') == 'create' and
                (latest_goal_command[1].get('response') or {}).get('confirmed') and
                time.time() - latest_goal_command[1].get('confirmedAt', latest_goal_command[1].get('at', 0)) > 1 and
                native_goal_absent(self.codex_home, session.id)):
            view['goal'] = None
            return
        goals = [(k, v) for k, v in merged.items() if v.get("workMode") == "goal"]
        if not goals:
            return
        key, entry = max(goals, key=lambda pair: pair[1]["at"])
        objective = entry["text"].strip()
        goal = view.get("goal")

        # Once the authoritative native goal is cleared, hide a stale desktop
        # snapshot instead of offering a second cancel.
        if entry.get("goalCancelled"):
            return

        goal_statuses = {"active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"}
        if entry.get("goalConfirmed") and (not goal or goal.get("objective", "").strip() != objective or goal.get("status") not in goal_statuses):
            # A later clear/replace happened elsewhere; expose the stale request only briefly as unconfirmed.
            view["goalSubmission"] = {"id": key, "objective": entry["text"], "status": "unconfirmed"}
            return
        if goal and goal.get("objective", "").strip() == objective and goal.get("status") in goal_statuses:
            if not entry.get("goalConfirmed") and entry.get("goalSource") != 'command':
                with self.submit_lock:
                    self.submissions[session.id + ":" + key]["goalConfirmed"] = True
                    self._save_ledger()
            return
        status = "unknown" if entry["status"] == "unknown" or entry.get("goalState") == "unknown" else "pending"
        # Legacy prompt-based requests created a real turn. Once that turn ends
        # without a native goal, the request is unconfirmed rather than blocking
        # the newer native Goal API forever.
        with session.condition:
            for turn in ordered_turns(session.state or {}):
                params = turn.get("params", {})
                if params.get("clientUserMessageId") == key and turn.get("status") in ("completed", "failed", "interrupted"):
                    status = "unconfirmed"
                    break
        view["goalSubmission"] = {"id": key, "objective": entry["text"], "status": status}

    def workspace(self, thread_id, action, params):
        uuid.UUID(thread_id)
        # Read project metadata only: file browsing never activates a Codex owner.
        root = self.store.get(thread_id).get('cwd')
        if self.host == 'local':
            return workspace_operation(root, action, params)
        if not isinstance(self.store, RemoteStore):
            raise RemoteUnavailable('此主机暂不支持文件浏览')
        source = Path(__file__).with_name('workspace.py').read_text(encoding='utf-8')
        source += '\nimport json\ntry:\n result={"value": operate(**' + payload({'root': root, 'action': action, 'params': params}) + ')}\nexcept (ValueError, PermissionError) as error:\n result={"error": str(error)}\nprint(json.dumps(result, ensure_ascii=False))\n'
        result = ssh_read(self.store.alias, source, timeout=60)
        if 'error' in result: raise ValueError(result['error'])
        return result['value']

    def subagents(self, thread_id, agent_id=None):
        uuid.UUID(thread_id)
        if not agent_id: return self.store.subagents(thread_id)
        uuid.UUID(agent_id)
        state = self.store.subagent_history(thread_id, agent_id)
        view = normalize_state(state, False)
        # Saved logs are evidence of the last recorded state, not a live heartbeat.
        return {'id': agent_id, 'title': view['title'], 'turns': view['turns'],
                'historyComplete': view['historyComplete'], 'saved': True}

    def terminal(self, thread_id, owner, action, params):
        uuid.UUID(thread_id)
        root = self.store.get(thread_id).get('cwd')
        if action == 'info':
            return {'cwd': root, 'host': self.host, 'shell': 'sh' if self.host != 'local' or os.name != 'nt' else 'cmd'}
        identifier = params.get('id', '')
        if action == 'start':
            return self.terminals.start(owner, thread_id, identifier, root, params.get('command'),
                                        self.store.alias if isinstance(self.store, RemoteStore) else None)
        job = self.terminals.get(owner, thread_id, identifier)
        if action == 'stop': job.stop()
        elif action != 'read': raise ValueError('未知终端操作')
        after = params.get('after', 0)
        if not isinstance(after, int) or not 0 <= after <= 10**12: raise ValueError('无效输出游标')
        return job.read(after)

    def artifact(self, thread_id, artifact_id):
        session = self.session(thread_id, attach=False)
        with session.condition:
            files = artifact_paths(session.state or {}, self.store.home) if self.host == "local" else {}
        if artifact_id not in files:
            raise KeyError("文件不属于此聊天的工作目录")
        return files[artifact_id]

    def _annotate_upload_attachments(self, session, rows):
        """Add preview ids only for images recorded in this chat's upload ledger."""
        paths = {a['path'] for row in rows for a in row.get('attachments', [])
                 if a.get('type') in ('localImage', 'image') and isinstance(a.get('path'), str)}
        if not paths:
            return
        by_path = self.uploads.by_path(session.id, paths)
        for row in rows:
            for attachment in row.get('attachments', []):
                path = attachment.get('path')
                if attachment.get('type') not in ('localImage', 'image') or not isinstance(path, str):
                    continue
                resolved = str(Path(path).resolve())
                upload = by_path.get(resolved)
                if upload and upload.get('image'):
                    attachment['uploadId'] = upload['id']
                    attachment['mime'] = upload['image']
                    attachment['thumb'] = upload.get('thumb')
                    attachment['name'] = upload.get('name') or attachment.get('name')
                elif self.host == 'local':
                    attachment['desktopId'] = hashlib.sha256(resolved.encode('utf-8')).hexdigest()
                if not attachment.get('name'):
                    attachment['name'] = Path(path).name
                # Preview metadata is enough for the browser. The private upload
                # path must never cross the authenticated web API boundary.
                attachment.pop('path', None)

    def desktop_image_preview(self, thread_id, image_id):
        """Serve a desktop-side image that is referenced by the saved thread."""
        if not re.fullmatch(r'[0-9a-f]{64}', image_id):
            raise ValueError('桌面图片标识无效')
        session = self.session(thread_id, attach=False)
        paths = []
        with session.condition:
            for turn in ordered_turns(session.state or {}):
                for item in items_array(turn.get('items', [])):
                    if item.get('type') not in ('userMessage', 'steeringUserMessage'):
                        continue
                    content = item.get('content') or item.get('input') or []
                    if not isinstance(content, list):
                        continue
                    for attachment in content:
                        if not isinstance(attachment, dict) or attachment.get('type') != 'localImage':
                            continue
                        path = attachment.get('path')
                        if isinstance(path, str) and path:
                            paths.append(Path(path))
            paths.extend(referenced_model_images(session.state or {}, image_views_only=True))
        for path in paths:
            try:
                resolved = path.resolve()
                if hashlib.sha256(str(resolved).encode('utf-8')).hexdigest() != image_id or not resolved.is_file():
                    continue
                data = resolved.read_bytes()
            except OSError:
                continue
            mime = image_type(data)
            if not mime:
                continue
            return {'previewPath': str(resolved), 'previewMime': mime,
                    'previewSha256': hashlib.sha256(data).hexdigest(),
                    'name': resolved.name}, 'desktop'
        raise KeyError('桌面图片不存在或已不可用')

    def _merge_submission_attachments(self, session, rows):
        """Show every submitted attachment, including files passed only as context."""
        with self.submit_lock:
            submissions = {key.rsplit(':', 1)[-1]: copy.deepcopy(value) for key, value in self.submissions.items()
                           if key.startswith(session.id + ':') and value.get('attachmentNames')}
        for row in rows:
            request_id = row.get('requestId')
            if not request_id:
                continue
            names = []
            entry = submissions.get(request_id)
            if not entry:
                continue
            for name in entry.get('attachmentNames') or []:
                if name not in names:
                    names.append(name)
            if row.get('role') != 'user' or not names:
                continue
            attachments = row.setdefault('attachments', [])
            existing = {item.get('name') for item in attachments}
            for name in names:
                if name not in existing:
                    attachments.append({'type': 'file', 'name': name})

    def _overlay_native_goal(self, session, thread_id, view):
        if self.host != "local":
            return view
        native_goal = normalize_goal(read_native_goal(self.codex_home, thread_id))
        if native_goal:
            view["goal"] = native_goal
        elif native_goal_absent(self.codex_home, thread_id):
            view["goal"] = None
        elif view.get("goal") and self._cancelled_goal_objective(thread_id, view["goal"].get("objective", "")):
            view["goal"] = None
        return view

    def timeline_read(self, thread_id, mode='page', **options):
        session = self.session(thread_id, background=True)
        # Expand saved history only when the reader reaches its loaded edge.
        if mode == 'page' and options.get('before'):
            with session.condition:
                position = session.timeline.position(options['before'])
                expand = position is not None and position < options.get('limit', 20) and session.saved_view and not session.saved_view['historyComplete']
                if expand:
                    session.history_limit += 50
            if expand:
                saved = self._saved_history(session)
                with session.condition:
                    session.set_history(saved)
        with session.condition:
            projection = session.timeline
            authoritative_view = self._overlay_native_goal(session, thread_id, session.view())
            if projection.sequence != session.sequence or projection.meta.get("goal") != authoritative_view.get("goal"):
                projection.update(authoritative_view)
            # Annotate the full projection, not only the returned page. Otherwise
            # older pages briefly render raw localImage paths after an upgrade.
            self._annotate_upload_attachments(session, projection.rows)
            self._merge_submission_attachments(session, projection.rows)
            if mode == 'detail':
                result = projection.detail(**options)
                texts = [projection.details[result['key']]]
            else:
                result = projection.changes(**options) if mode == 'changes' else projection.page(**options)
                result['meta'] = dict(result['meta'], host=self.host,
                                      hostLabel='此电脑' if self.host == 'local' else self.hosts.hosts().get(self.host, {}).get('displayName', self.host))
                texts = [row['text'] for row in result['rows'] if row['role'] == 'assistant']
            cwd = (session.state or {}).get('cwd')
            media_items = [item for turn in ordered_turns(session.state or {})
                           for item in items_array(turn.get('items')) if item.get('type') in ('ImageView', 'imageView')]
        # Resolve links in the delivered page, plus ImageView evidence needed by
        # embedded model screenshots that can live outside workspace roots.
        file_state = {'cwd': cwd, 'turns': [
            {'items': [{'type': 'agentMessage', 'text': text} for text in texts]},
            {'items': media_items},
        ]}
        artifacts = artifact_paths(file_state, self.store.home) if self.host == 'local' else {}
        result['files'] = [{'id': k, 'name': v['name'], 'reference': v['reference'], 'image': v['image']} for k, v in artifacts.items()]
        if mode != 'detail':
            self._submission_meta(session, result['meta'])
            result['meta']['forkedFrom'] = self._fork_origin(thread_id)
        return result

    def catalog(self, thread_id, refresh=False, kind=None, query='', offset=0, limit=200, ids=None):
        session = self.session(thread_id)
        with session.condition:
            cwd = session.state.get("cwd")
            model = session.state.get("latestModel")
            effort = session.state.get("latestReasoningEffort") or (session.state.get("latestThreadSettings") or {}).get("effort")
            provider = session.view().get('provider')
        if kind in ('models', 'skills'):
            value = self.catalog_reader.get_kind(kind, cwd, refresh=refresh, provider=provider,
                                                 query=query, offset=offset, limit=limit, ids=ids)
            if kind == 'models':
                with session.condition:
                    view = session.view()
                return {**value, 'kind': 'models', 'currentModel': model or value.get('currentModel'),
                        'currentEffort': effort or value.get('currentEffort'),
                        'fastMode': {**value.get('fastMode', {}), 'allowed': view.get('provider') == 'openai' and value.get('fastMode', {}).get('allowed') is True},
                        **({'currentServiceTier': view['serviceTier']} if 'serviceTier' in view else {})}
            return {**value, 'kind': 'skills'}
        catalog = self.catalog_reader.get(cwd, refresh=refresh, provider=provider)
        with session.condition:
            view = session.view()
        return {**catalog, "currentModel": model or catalog.get("currentModel"),
                "currentEffort": effort or catalog.get("currentEffort"),
                'fastMode': {**catalog.get('fastMode', {}), 'allowed': view.get('provider') == 'openai' and catalog.get('fastMode', {}).get('allowed') is True},
                **({'currentServiceTier': view['serviceTier']} if 'serviceTier' in view else {})}

    def _resolve_skills(self, session, skills):
        if not isinstance(skills, list) or len(skills) > 8 or not all(isinstance(s, str) for s in skills):
            raise ValueError("最多选择 8 个 Skill")
        if not skills:
            return []
        with session.condition:
            cwd = session.state.get("cwd") or str(self.codex_home)
        identifiers = sorted(set(skills))
        rows = self.catalog_reader.validate_skills(cwd, identifiers)
        by_id = {row["id"]: row for row in rows}
        selected = []
        for key in identifiers:
            skill = by_id.get(key)
            if not skill:
                raise ValueError("Skill 不可用，请刷新列表")
            selected.append({"id": key, "name": skill["name"], "path": skill["path"]})
        return selected

    @operation
    def rename(self, thread_id, title):
        uuid.UUID(thread_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or any(unicodedata.category(c) in ('Cc', 'Zl', 'Zp') for c in title):
            raise ValueError('聊天名称需为 1–120 个字符，不能包含换行或控制字符')
        title = title.strip()
        self.store.get(thread_id)  # Only existing desktop chats may be renamed.
        session = self.session(thread_id, attach=False, background=True)
        with session.action_lock:
            if isinstance(self.store, RemoteStore):
                source = Path(__file__).with_name('create.py').read_text(encoding='utf-8')
                source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                source += 'rename_thread(runtime, home, **' + payload({'thread_id': thread_id, 'title': title}) + ')\nprint(json.dumps({"ok":True}))\n'
                ssh_read(self.store.alias, source, timeout=90)
                with self.store.lock:
                    self.store.cache.clear()
            else:
                rename_thread(self.catalog_reader.executable, self.codex_home, thread_id, title)
            with session.condition:
                if session.state is not None:
                    session.state['title'] = title
                if session.saved_view is not None:
                    session.saved_view['title'] = title
                session.changed()
        return {'id': thread_id, 'host': self.host, 'title': title}

    @operation
    def settings(self, thread_id, model, effort, *, fast_mode=None):
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,199}", model):
            raise ValueError("模型 ID 格式不正确")
        if effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            raise ValueError("请选择有效的推理强度")
        if fast_mode is not None and not isinstance(fast_mode, bool):
            raise ValueError('Fast 模式开关无效')
        session = self._target(thread_id)
        with session.action_lock:
            # Recheck login/model/policy on the execution host before a speed change.
            catalog = self.catalog(thread_id, refresh=fast_mode is not None)
            known = next((m for m in catalog["models"] if m["id"] == model), None)
            if known and known["efforts"] and effort not in known["efforts"]:
                raise ValueError("这个模型不支持所选推理强度")
            settings = {'model': model, 'effort': effort}
            if fast_mode is not None:
                if not catalog.get('fastMode', {}).get('allowed') or not known or not known.get('fastTier'):
                    raise ValueError('当前账号、模型或工作区不支持 Fast 模式，请刷新模型设置')
                # null can inherit a default; "default" explicitly opts out of Fast.
                settings['serviceTier'] = known['fastTier'] if fast_mode else 'default'
            result = self._call(session, "thread-follower-update-thread-settings", {"threadSettings": settings})
        if not result.get("applied"):
            raise IPCError("桌面未应用模型设置，请刷新后重试")
        with session.condition:
            confirmed = session.condition.wait_for(lambda: session.state.get("latestModel") == model and session.view().get("effort") == effort and
                (fast_mode is None or (session.state.get('latestThreadSettings') or {}).get('serviceTier') == settings['serviceTier']), timeout=5)
        return {"applied": True, "confirmed": confirmed, "model": model, "effort": effort,
                **({'serviceTier': settings['serviceTier']} if fast_mode is not None else {})}

    def _goal_activation_ids(self, thread_id):
        """Stable request ids used by goal activation turns."""
        ids = set()
        for key, command in self.goal_commands.items():
            if not key.startswith(thread_id + ':') or command.get('action') not in ('create', 'resume'):
                continue
            response = command.get('response') or {}
            goal = normalize_goal_result(response.get('result'))
            if response.get('confirmed') and goal:
                ids.add(goal_activation(thread_id, goal, command['action'], transition_id=response.get('transitionId'))[0])
        return ids

    def _cancel_goal_activation_queues(self, thread_id):
        """Cancel queued start turns after their goal command is confirmed cancelled."""
        ids = self._goal_activation_ids(thread_id)
        changed = False
        with self.submit_lock:
            for activation_id in ids:
                key = thread_id + ':' + activation_id
                entry = self.submissions.get(key)
                if entry and entry.get('status') == 'queued':
                    entry['status'] = 'cancelled'
                    changed = True
            if changed:
                self._save_ledger()
        if changed:
            session = self.live.get(thread_id)
            if session:
                with session.condition:
                    session.changed()

    def _send_goal_activation(self, session, response, action, ui_locale=None):
        """Serially start a turn after authoritative state proves the goal."""
        if not response.get("confirmed") or response.get("status") not in ("active",):
            return None
        goal = normalize_goal_result(response.get("result"))
        if not goal:
            return None
        # Prefer the authoritative row when the RPC result omits a stable goal id.
        goal = normalize_goal(self._native_goal_state(session.id, strict=True))
        if goal is None:
            return None
        ui_locale = normalize_ui_locale(response.get("uiLocale") or ui_locale)
        if goal.get('status') != 'active' or goal_identity(goal) != goal_identity(normalize_goal_result(response.get('result'))):
            return None
        if response.get('duplicate') and not response.get('transitionId'):
            return None
        activation_id, text = goal_activation(session.id, goal, action, ui_locale, response.get('transitionId'))
        with session.condition:
            active = (session.state or {}).get("threadRuntimeStatus", {}).get("type") == "active"
        try:
            activation = self.send(session.id, text, activation_id, "queue" if active else "send",
                                   activation={"action": action, "objective": goal["objective"],
                                               "uiLocale": ui_locale, "goal": goal_identity(goal)})
        except Exception as exc:
            raise IPCError(f"目标状态已确认，但启动消息发送失败：{exc}") from exc
        if activation.get("status") not in ("accepted", "queued"):
            raise IPCError("目标状态已确认，但启动消息结果尚未确认；请不要自动重发")
        return {"id": activation_id, "status": activation["status"]}

    def _goal_call(self, method, *args):
        """Use the owner when available, otherwise the isolated sidecar."""
        if self.owner_goal is not None:
            # A configured owner transport is authoritative. Never silently
            # fall back after the owner path was selected: that could create a
            # second writer and reintroduce split-brain state.
            return getattr(self.owner_goal, method)(*args)
        last = None
        for attempt in range(2 if method == 'get_goal' else 1):
            try:
                return getattr(self.goal, method)(*args)
            except GoalUnavailable as exc:
                last = exc
                self.goal.close()
                if method == 'get_goal' and attempt == 0 and not self.closed.is_set():
                    self.closed.wait(.25)
                    continue
                raise
        raise last

    @operation
    def _goal_control(self, thread_id, request_id, action, *, status=None, objective=None, ui_locale=None, expected=None):
        uuid.UUID(request_id)
        if self.host != 'local' or self.goal is None:
            raise ValueError('目标模式暂不支持 SSH 主机')
        session = self.session(thread_id, attach=False, background=True)
        # Match the queue dispatcher lock order and serialize state + activation.
        with session.action_lock, self.goal_lock:
            prior = self.goal_commands.get(thread_id + ':' + request_id)
            native = self._native_goal_state(thread_id, strict=True)
            if objective is None:
                objective = (prior or {}).get('objective', (native or {}).get('objective', ''))
            response = self._goal_command(session, thread_id, request_id, action,
                objective=objective, status=status, ui_locale=ui_locale, expected=expected)
            if response.get('confirmed') and action in ('pause', 'cancel', 'edit'):
                self._cancel_goal_activation_queues(thread_id)
            if action == 'resume':
                response['activation'] = self._send_goal_activation(session, response, action)
            with session.condition:
                session.changed()
            return response

    def set_goal_status(self, thread_id, status, request_id, ui_locale=None, expected=None):
        if status not in ('active', 'paused'):
            raise ValueError('目标状态无效')
        return self._goal_control(thread_id, request_id, 'pause' if status == 'paused' else 'resume',
                                  status=status, ui_locale=ui_locale, expected=expected)

    def cancel_goal(self, thread_id, request_id, expected=None):
        return self._goal_control(thread_id, request_id, 'cancel', expected=expected)

    def edit_goal(self, thread_id, objective, request_id, expected=None):
        if not isinstance(objective, str) or not 0 < len(objective.strip()) <= 4000:
            raise ValueError('请输入 1–4000 字的目标内容')
        return self._goal_control(thread_id, request_id, 'edit', objective=objective, expected=expected)

    def cancel_queued(self, thread_id, submission_id):
        uuid.UUID(submission_id)
        session = self.session(thread_id, attach=False)
        with session.action_lock:
            with self.submit_lock:
                key = thread_id + ":" + submission_id
                prior = self.submissions.get(key)
                if not prior or prior["status"] != "queued":
                    raise ValueError("此消息已离开队列，请查看会话")
                prior["status"] = "cancelled"
                self._save_ledger()
            with session.condition:
                session.changed()
        return {"status": "cancelled"}

    @operation
    def ignore_submission(self, thread_id, submission_id):
        uuid.UUID(thread_id)
        uuid.UUID(submission_id)
        with self.lock:
            session = self.live.get(thread_id)
        with session.action_lock if session else nullcontext():
            with self.submit_lock:
                entry = self.submissions.get(thread_id + ':' + submission_id)
                if not entry or entry['status'] not in ('unknown', 'ignored'):
                    raise ValueError('只能忽略发送结果未确认的记录')
                entry['status'] = 'ignored'
                self._save_ledger()
            if session:
                with session.condition:
                    session.changed()
        return {'status': 'ignored', 'id': submission_id}

    @operation
    def send(self, thread_id, text, submission_id, mode="send", skills=None, *, work_mode=None,
             plan_response=None, attachments=None, ui_locale=None, activation=None):
        uuid.UUID(submission_id)
        identifiers = [] if attachments is None else attachments
        files = self.uploads.resolve(thread_id, identifiers)
        if not isinstance(text, str) or (not text.strip() and not files) or len(text) > 100000:
            raise ValueError("请输入 1–100000 字的消息")
        if mode not in ("send", "steer", "queue"):
            raise ValueError("未知发送方式")
        if work_mode not in (None, "default", "plan", "goal"):
            raise ValueError("未知工作模式")
        if mode == "steer" and work_mode is not None:
            raise ValueError("补充当前任务时不能切换工作模式")
        if work_mode == "goal" and (mode != "send" or not text.strip() or len(text) > 4000):
            raise ValueError("目标需在当前任务结束后直接发送，且不超过 4000 字")
        if work_mode == "goal":
            if self.host != "local" or self.goal is None:
                raise ValueError("目标模式暂不支持 SSH 主机")
            if identifiers:
                raise ValueError("目标模式暂不支持附件")
            if skills:
                raise ValueError("目标模式只接受纯文本目标")
            session = self._target(thread_id)
            with session.action_lock, self.goal_lock:
                response = self._goal_command(session, thread_id, submission_id, "create", objective=text,
                                              ui_locale=ui_locale or activation.get("uiLocale") if activation else ui_locale)
                result = {"status": "unknown" if response.get("status") == "unknown" else "accepted",
                          "confirmed": bool(response.get("confirmed")), "duplicate": bool(response.get("duplicate")),
                          "id": submission_id, "result": response.get("result")}
                result["activation"] = self._send_goal_activation(
                    session, response, "create", ui_locale or activation.get("uiLocale") if activation else ui_locale)
                return result
        session = self._target(thread_id)
        selected = self._resolve_skills(session, [] if skills is None else skills)
        key = thread_id + ":" + submission_id
        with session.action_lock:
            with self.submit_lock:
                prior = self.submissions.get(key)
                if prior:
                    if (prior["text"] != text or prior["mode"] != mode or prior.get("skills", []) != selected or
                            prior.get("workMode") != work_mode or prior.get("planResponse") != plan_response or prior.get('attachments', []) != identifiers or
                            prior.get("goalActivation") != activation):
                        raise ValueError("同一消息标识不能用于不同内容")
                    return {"status": prior["status"], "duplicate": True, "id": submission_id}
            self._check_provider(session)
            with session.condition:
                active = (session.state or {}).get("threadRuntimeStatus", {}).get("type") == "active"
                if active and mode == "send":
                    raise ValueError("Codex 正在执行。请选择「排队发送」或「补充当前任务」。")
                if not active and mode == "steer":
                    raise ValueError("当前任务已经结束，请使用普通发送")
                if work_mode is not None and not (session.state.get("latestModel") or
                        (session.state.get("latestCollaborationMode") or {}).get("settings", {}).get("model")):
                    raise ValueError("尚未取得桌面模型设置，请重新连接后再切换模式")
                if plan_response is not None:
                    pending = next((r for r in session.state.get("requests", [])
                                    if r.get("id") == plan_response["requestId"] and r.get("method") == "item/plan/requestImplementation"), None)
                    if pending is None:
                        raise ValueError("此计划已处理或已过期，请刷新后查看最新计划")
                    if plan_response["action"] == "implement":
                        if text != "Implement the following plan:\n\n" + pending.get("params", {}).get("planContent", ""):
                            raise ValueError("计划已更新，请刷新后重试")
            with self.submit_lock:
                entry = {"text": text, "mode": mode, "skills": selected, "status": "queued" if mode == "queue" else "unknown", "at": time.time()}
                if identifiers:
                    entry['attachments'] = identifiers
                    entry['attachmentNames'] = [f['name'] for f in files]
                if work_mode is not None:
                    entry["workMode"] = work_mode
                if plan_response is not None:
                    entry["planResponse"] = plan_response
                if activation:
                    entry["goalActivation"] = activation
                self.submissions[key] = entry
                self._save_ledger()
            with session.condition:
                session.changed()
            if mode == "queue":
                return {"status": "queued", "id": submission_id}
            return self._dispatch(session, key, entry)

    @operation
    def _send_queued(self, session, key, entry):
        with session.action_lock:
            with session.condition:
                if not session.connected or session.state.get("threadRuntimeStatus", {}).get("type") != "idle":
                    return
            with self.goal_lock:
                with self.submit_lock:
                    if self.submissions[key]["status"] != "queued":
                        return
                    activation = entry.get("goalActivation")
                    cancelled = False
                    if activation:
                        goal = self._native_goal_state(session.id, strict=True)
                        objective = (activation.get("objective") or "").strip()
                        goal_objective = (goal or {}).get("objective", "").strip()
                        if goal is None or goal.get("status") != "active" or goal_objective != objective or (activation.get("goal") and activation["goal"] != goal_identity(goal)):
                            self.submissions[key]["status"] = "cancelled"
                            self._save_ledger()
                            cancelled = True
                    if not cancelled:
                        self.submissions[key]["status"] = "unknown"
                        self._save_ledger()
                try:
                    if cancelled:
                        return
                    self._dispatch(session, key, entry)
                finally:
                    with session.condition:
                        session.changed()

    def _dispatch(self, session, key, entry):
        submission_id = key.split(":")[1]
        text = entry["text"]
        files = self.uploads.resolve(session.id, entry.get('attachments', []))
        request = {"threadId": session.id, "input": [{"type": "text", "text": text, "text_elements": []}], "clientUserMessageId": submission_id}
        request['input'].extend({'type': 'localImage', 'path': f['path']} for f in files if f['image'])
        context = {'attachments': [], 'commentAttachments': []}
        if files:
            context['fileAttachments'] = [{'path': f['path'], 'label': f['name']} for f in files]
        if entry.get("workMode") is not None:
            with session.condition:
                current = session.state
                model = current.get("latestModel") or (current.get("latestCollaborationMode") or {}).get("settings", {}).get("model")
                effort = current.get("latestReasoningEffort") or (current.get("latestThreadSettings") or {}).get("effort")
            if not model:
                raise ValueError("尚未取得桌面模型设置，请重新连接后再切换模式")
            request["collaborationMode"] = {"mode": "plan" if entry["workMode"] == "plan" else "default",
                                            "settings": {"model": model, "reasoning_effort": effort, "developer_instructions": None}}
        request["input"].extend({"type": "skill", "name": s["name"], "path": s["path"]} for s in entry.get("skills", []))
        if entry["mode"] != "steer":
            response = self._call(session, "thread-follower-start-turn", {
                "turnStart": {"request": request, "context": {"inheritThreadSettings": True, **context}}}, timeout=90)
        else:
            response = self._call(session, "thread-follower-steer-turn", {
                "input": request["input"], "clientUserMessageId": submission_id,
                "restoreMessage": {"request": request, "context": context},
                "attachments": []}, timeout=30)
        with self.submit_lock:
            self.submissions[key]["status"] = "accepted"
            self._save_ledger()
        with session.condition:
            session.changed()
        return {"status": "accepted", "id": submission_id, "result": response}

    def interrupt(self, thread_id):
        session = self._target(thread_id)
        with session.condition:
            active = next((t for t in reversed(ordered_turns(session.state)) if t.get("status") == "inProgress"), None)
            if not active or not active.get("turnId"):
                raise ValueError("当前没有可停止的任务")
            turn_id = active["turnId"]
        return self._call(session, "thread-follower-interrupt-turn", {"mode": "user-stop", "expectedTurnId": turn_id})

    def full_history(self, thread_id):
        session = self._target(thread_id)
        return self._call(session, "thread-follower-load-complete-history", {}, timeout=180)

    def respond(self, thread_id, request_id, response):
        session = self._target(thread_id)
        plan_id = str(uuid.uuid5(uuid.UUID(thread_id), "plan:" + str(request_id)))
        with self.submit_lock:
            prior = copy.deepcopy(self.submissions.get(thread_id + ":" + plan_id))
        if prior and prior.get("planResponse"):
            if prior["planResponse"] != {"requestId": request_id, **response}:
                raise ValueError("此计划已经提交了不同操作，请查看会话结果")
            return {"status": prior["status"], "duplicate": True, "id": plan_id}
        with session.condition:
            async_pending = next((r for r in async_requests(session.state) if r["id"] == request_id), None)
        if async_pending:
            questions = async_pending["params"]["questions"]
            answers = response.get("answers", {})
            if set(answers) != {q["id"] for q in questions} or any(not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], str) or not v[0].strip() or len(v[0]) > 20000 for v in answers.values()):
                raise ValueError("请回答所有问题")
            replies = [{"questionItemId": q["id"], "question": q["question"], "answer": answers[q["id"]][0]} for q in questions]
            text = "<send_user_message_question_reply>\n" + json.dumps(replies, ensure_ascii=False, separators=(",", ":")) + "\n</send_user_message_question_reply>"
            submission = str(uuid.uuid5(uuid.UUID(thread_id), request_id + text))
            with session.condition:
                active = (session.state or {}).get("threadRuntimeStatus", {}).get("type") == "active"
            return self.send(thread_id, text, submission, "steer" if active else "send")
        with session.condition:
            pending = next((r for r in session.state.get("requests", []) if str(r.get("id")) == str(request_id)), None)
            if not pending:
                raise ValueError("此请求已处理或已过期")
            pending = copy.deepcopy(pending)
        if not normalize_request(pending)["supported"]:
            raise ValueError("此类请求请在桌面 App 中处理")
        method = pending.get("method", "")
        params = pending.get("params", {})
        if method == "item/plan/requestImplementation":
            action = response.get("action")
            if action == "implement" and set(response) == {"action"}:
                plan = params.get("planContent")
                if not isinstance(plan, str) or not plan.strip():
                    raise ValueError("计划内容尚未加载，请刷新后重试")
                text, work_mode = "Implement the following plan:\n\n" + plan, "default"
            elif action == "revise" and set(response) == {"action", "text"} and isinstance(response.get("text"), str) and response["text"].strip():
                text, work_mode = response["text"], "plan"
            else:
                raise ValueError("请选择执行计划或填写修改意见")
            return self.send(thread_id, text, plan_id, work_mode=work_mode,
                             plan_response={"requestId": request_id, **response})
        mapping = {
            "item/commandExecution/requestApproval": "thread-follower-command-approval-decision",
            "item/fileChange/requestApproval": "thread-follower-file-approval-decision",
            "item/permissions/requestApproval": "thread-follower-permissions-request-approval-response",
            "item/tool/requestUserInput": "thread-follower-submit-user-input",
            "tool/requestUserInput": "thread-follower-submit-user-input",
            "mcpServer/elicitation/request": "thread-follower-submit-mcp-server-elicitation-response",
        }
        if method not in mapping:
            raise ValueError("此类请求请在桌面 App 中处理")
        payload = {"requestId": pending["id"]}
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            decision = response.get("decision")
            if decision not in ("accept", "decline", "cancel"):
                raise ValueError("只支持本次批准、拒绝或取消")
            available = params.get("availableDecisions")
            if available and decision not in available:
                raise ValueError("该审批不支持所选操作")
            payload["decision"] = decision
        elif method == "item/permissions/requestApproval":
            if response.get("decision") not in ("accept", "decline"):
                raise ValueError("请选择批准或拒绝")
            payload["response"] = {"permissions": params.get("permissions", {}) if response["decision"] == "accept" else {}, "scope": "turn"}
        elif method in ("item/tool/requestUserInput", "tool/requestUserInput"):
            answers = response.get("answers")
            if not isinstance(answers, dict):
                raise ValueError("请填写问题答案")
            allowed_ids = {q["id"] for q in params.get("questions", [])}
            if not allowed_ids or set(answers) != allowed_ids or any(not isinstance(v, list) or not v or not all(isinstance(s, str) and len(s) <= 20000 for s in v) for v in answers.values()):
                raise ValueError("答案不完整或格式不正确")
            payload["response"] = {"answers": {k: {"answers": v} for k, v in answers.items()}}
        else:
            action = response.get("action")
            if action not in ("accept", "decline", "cancel"):
                raise ValueError("未知确认操作")
            computer = computer_use_approval(params)
            if computer:
                if set(response) - {'action', 'persist'} or ('persist' in response and
                        (action != 'accept' or response['persist'] not in computer['persistModes'])):
                    raise ValueError('此请求不支持所选授权范围')
                payload['response'] = {'action': action, 'content': {} if action == 'accept' else None}
                if 'persist' in response:
                    payload['response']['_meta'] = {'persist': response['persist']}
            else:
                if set(response) - {'action', 'content'}:
                    raise ValueError('此请求不支持所选授权范围')
                content = response.get("content")
                if action == "accept":
                    validate_form(content, params.get("requestedSchema", {}))
                payload["response"] = {"action": action, "content": content if action == "accept" else None}
        return self._call(session, mapping[method], payload)

    def close(self):
        self.terminals.close()
        self.closed.set()
        if self.host == "local" and self.accounts:
            self.accounts.login_cancel.set()
        for bridge in list(self.remote_bridges.values()):
            bridge.close()
        with self.lock:
            sessions = list(self.live.values())
        for session in sessions:
            try:
                if session.connected or session.discovering:
                    self.ipc.follow(session.id, session.owner, False, host=self.host)
            except IPCError:
                pass
        self.ipc.close()
        if self.goal:
            self.goal.close()


def validate_form(value, schema):
    """Validate the small JSON-schema form subset that the phone can render."""
    from .model import form_supported
    if not form_supported(schema):
        raise ValueError("此表单需要在桌面填写")
    if not isinstance(value, dict):
        raise ValueError("请填写表单")
    properties = schema.get("properties", {})
    if set(value) - set(properties) or set(schema.get("required", [])) - set(value):
        raise ValueError("表单字段不完整")
    for key, item in value.items():
        field = properties[key]
        kind = field.get("type", "string")
        valid = ((kind == "string" and isinstance(item, str)) or
                 (kind == "boolean" and isinstance(item, bool)) or
                 (kind in ("number", "integer") and type(item) in (int, float) and (kind != "integer" or int(item) == item)))
        if not valid or ("enum" in field and item not in field["enum"]):
            raise ValueError("表单字段格式不正确：" + key)
        if isinstance(item, str) and not field.get("minLength", 0) <= len(item) <= field.get("maxLength", 20000):
            raise ValueError("表单文本长度不正确：" + key)
        if type(item) in (int, float) and not field.get("minimum", float("-inf")) <= item <= field.get("maximum", float("inf")):
            raise ValueError("表单数值超出范围：" + key)
