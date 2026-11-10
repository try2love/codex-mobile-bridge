import copy
from contextlib import ExitStack, nullcontext
import hashlib
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import unicodedata
from pathlib import Path

from bridge.features.endpoints.registry import codex_list_hosts
from bridge.app.lifecycle import private_json
from bridge.resources import source_text
from bridge.features.sessions.message_management import snapshot as management_snapshot, mutate as management_mutate

from bridge.features.terminals.manager import TerminalManager
from bridge.features.sessions.side_chat import SideChats
from bridge.features.sessions import linux_permissions
from bridge.clients.codex.ipc import DesktopIPC, IPCError
from bridge.clients.errors import TaskStateUnavailable
from bridge.clients.codex.app_server import AppServerError, AppServerUnavailable
from bridge.clients.codex.cli_session import CliSessions
from bridge.features.sessions.identity import same_cwd
from bridge.clients.desktop_app import DesktopApp
from bridge.clients.codex.transport import ipc_endpoint
from bridge.features.sessions.model import apply_patches, attachment_prompt, computer_use_approval, items_array, normalize_state, normalize_request, ordered_turns, pending_requests, async_requests, request_id, user_display_text, merge_saved_message_times
from bridge.features.sessions.store import SessionStore, StoreUnavailable, persisted_submission_ids
from bridge.features.workspace.files import artifact_paths, referenced_model_images
from bridge.features.workspace.workspace import operate as workspace_operation
from bridge.clients.codex.catalog import Catalog, CatalogError, ModelStore, MODEL_REFRESH_SECONDS
from bridge.platforms.codex_daemon_env import DaemonEnvironmentUnavailable
from bridge.clients.codex.remote import AppHosts, RemoteStore, RemoteCatalog, RemoteUnavailable, ssh_read, ssh_download, payload, project_path_name, ssh_connection, retry_ssh
from bridge.features.sessions.create import (
    archive_thread, rename_thread, independent_directory, create_empty, fork_copy, fork_attached, open_in_desktop, open_project_in_desktop,
    CreationError, CreationUnavailable, ForkUnavailable, project_directory,
    project_directory_listing, project_trust, apply_desktop_config,
)
from bridge.features.sessions.archive import ArchiveCommands
from bridge.features.sessions.deletion import DeleteCommands, deletion_scope, delete_native
from bridge.features.sessions.compaction import CompactCommands
from bridge.features.sessions.cli_release import ReleasedSessions
from bridge.features.sessions.cli_maintenance import CliMaintenance, CliTasksPending, maintenance_receipt
from bridge.features.sessions.organization_commands import OrganizationCommands
from bridge.features.sessions.organization import OrganizationNotSent
from bridge.features.sessions.pins import SessionPins
from bridge.features.sessions.question_skips import QuestionSkips
from bridge.features.sessions.organization import organization as native_organization
from bridge.features.sessions.projects import Projects
from bridge.features.sessions.timeline import Timeline
from bridge.features.sessions.tool_images import TOOL_IMAGE_CACHE, public_turns
from bridge.features.accounts.account import Account
from bridge.features.accounts.accounts import Accounts, operation
from bridge.features.accounts.operation_gate import OperationGate
from bridge.features.accounts.draft_identity import codex_account_identity, draft_identity
from bridge.features.accounts.cli_access import CliAccess
from bridge.features.accounts.models import valid_model_id
from bridge.features.sessions.goal import GoalRPC, GoalError, GoalUnavailable, GoalUnsupported, goal_activation, goal_identity, normalize_goal_result, normalize_ui_locale, goal_command_fingerprint, goal_state_confirms_command, native_goal_absent, normalize_goal, plan_goal_command, read_native_goal
from bridge.features.workspace.uploads import Uploads, image_type
from bridge.clients.codex.remote import upload_file


class SubmissionRejected(ValueError):
    """The request was rejected before any submission was recorded or sent."""


class LiveSession:
    def __init__(self, thread_id, *, image_cache=None, question_skips=None):
        self.id = thread_id
        self.question_skips = question_skips
        self.owner = None
        self.origin = "desktop"
        self.origin_verified = False
        self.runtime = "desktop"
        self.saved_cwd = None
        self.saved_provider = None
        self.attached_parent = None
        self.desktop_owner_probe = None
        self.native_attaching = False
        self.native_goal_supported = None
        self.native_goal_refresh_at = 0
        self.access = None
        self.discovering = False
        self.state = None
        self.history_state = None
        self.saved_view = None
        self.saved_file_changes = {}
        self.history_limit = 2
        self.timeline_limit = 40
        self.timeline_source = None
        self.timeline_source_limit = None
        self.revision = None
        self.sequence = 0
        self.connected = False
        self.connecting = False
        self.owner_probe_inflight = False
        self.owner_probe_sequence = 0
        self.owner_probe_epoch = None
        self.activating = False
        self.last_activation = None
        self.retry_at = 0
        self.connection_epoch = 0
        self.desktop_recovery_until = 0
        self.history_retry_at = 0
        self.history_refreshing = False
        self.history_receipt_key = None
        self.history_receipt_attempts = 0
        self.error = None
        self.cli_recovery = None
        self.rebuild_recommended = False
        self.desktop_restart_recommended = False
        self.viewers = 0
        self.watched = False
        self.notification_probe_until = 0
        self.notification_connecting = False
        self.activity_only = False
        self.touched = time.monotonic()
        self.condition = threading.Condition(threading.RLock())
        self.attach_lock = threading.Lock()
        self.activation_lock = threading.Lock()
        self.action_lock = threading.RLock()
        self.timeline = Timeline()
        self.tool_image_cache = image_cache if image_cache is not None else TOOL_IMAGE_CACHE
        self.tool_images = {}
        self.tool_images_sequence = -1

    def changed(self):
        self.sequence += 1
        self.timeline_source = None
        self.condition.notify_all()

    def timeline_view(self):
        # Presentation only. Actions still call view() against current state.
        with self.condition:
            source = self.state if self.connected else self.history_state or self.state
            # Preserve the actual loaded anchor across appends, rather than
            # treating older Desktop hydration as newly appended messages.
            anchor = self.timeline.source_anchor
            if anchor is not None:
                after, found = 0, False
                for turn in ordered_turns(source or {}):
                    occurrences = {}
                    calls = set()
                    for item in items_array(turn.get('items')):
                        kind = item.get('type', 'unknown')
                        if kind in ('function_call_output', 'custom_tool_call_output') and item.get('call_id') in calls:
                            continue
                        if kind in ('function_call', 'custom_tool_call'):
                            calls.add(item.get('call_id', item.get('id')))
                        key = ((turn.get('turnId'), item.get('call_id', item.get('id')), 'storedToolEvent')
                               if kind in ('function_call', 'custom_tool_call') else
                               (turn.get('turnId'), item.get('id'), kind))
                        ordinal = occurrences.get(key, 0)
                        occurrences[key] = ordinal + 1
                        found = found or key + (ordinal,) == anchor
                        after += int(found)
                if found:
                    self.timeline_limit = max(self.timeline_limit, after)
            if self.timeline_source is None or self.timeline_source_limit != self.timeline_limit:
                self.timeline_source = self.view(item_limit=self.timeline_limit)
                self.timeline_source_limit = self.timeline_limit
            return dict(self.timeline_source)

    def set_history(self, state):
        self.history_state = state
        self.saved_view = normalize_state(state, False, image_cache=self.tool_image_cache, item_limit=self.timeline_limit)
        # Complete native patch evidence is retained by the saved reader even
        # when its messages are outside the current presentation window.
        self.saved_file_changes = {
            turn.get('turnId'): (turn['_nativeFileChanges'], turn.get('_nativeFileChangesBasis', 'recorded'))
            for turn in ordered_turns(state) if turn.get('status') == 'completed' and turn.get('_nativeFileChanges')}
        for turn in self.saved_view['turns']:
            if turn.get('status') == 'completed' and turn.get('fileChanges'):
                self.saved_file_changes.setdefault(turn['id'], (turn['fileChanges'], turn.get('fileChangesBasis', 'recorded')))
        self.changed()

    def pending_requests(self):
        if self.runtime == 'cli-observer':
            return (self.state or {}).get('notificationRequests', [])
        requests = pending_requests(self.state or {})
        return self.question_skips.project(self.id, requests) if self.question_skips else requests

    def view(self, *, item_limit=None):
        with self.condition:
            source = (self.state if self.connected else self.history_state or self.state) or {"id": self.id}
            result = normalize_state(source, self.connected,
                                     image_cache=self.tool_image_cache, item_limit=item_limit)
            if self.question_skips:
                result['requests'] = self.question_skips.project(self.id, result['requests'])
            if not self.connected:
                # Persisted history never grants authority to answer a request.
                result['requests'] = []
                result['status'] = 'notLoaded'
            if self.runtime == 'cli' and self.connected:
                result['permissionMode'] = linux_permissions.current_mode(self.state)
            for turn in result['turns']:
                saved_changes = self.saved_file_changes.get(turn['id'])
                if turn.get('status') == 'completed' and not turn.get('fileChanges') and saved_changes:
                    turn['fileChanges'], turn['fileChangesBasis'] = saved_changes
            if self.saved_view and result['contextUsage'] is None:
                saved_usage = self.saved_view.get('contextUsage')
                if saved_usage:
                    result['contextUsage'] = {**saved_usage, 'cached': True}
            if self.saved_view:
                if self.connected and result.get('id') == self.id:
                    merge_saved_message_times(result, self.saved_view)
                saved_labels = {turn['id']: turn for turn in self.saved_view['turns'] if turn.get('status') != 'inProgress'}
                for turn in result['turns']:
                    saved = saved_labels.get(turn['id'])
                    label_known = (self.history_state or {}).get('historyReadSource') != 'native-index' or saved and saved.get('model')
                    if saved is not None and label_known and (self.runtime != 'cli' or saved.get('model')):
                        # Desktop history hydration can substitute today's model.
                        # Recorded turn_context is authoritative for past replies.
                        # Only native CLI per-turn fields can fill an unknown label.
                        turn['model'], turn['effort'] = saved.get('model'), saved.get('effort')
            # Native paginated snapshots only contain the loaded tail. Keep the
            # saved prefix in the presentation, never in the IPC patch base.
            if self.saved_view and (not result['historyComplete'] or self.origin in ('cli', 'ide') and self.runtime == 'desktop'):
                saved = self.saved_view['turns']
                turns = result['turns']
                first = next((i for i, turn in enumerate(saved) if turns and turn['id'] == turns[0]['id']), None)
                if not turns or first is not None:
                    result['turns'] = saved[:first] + turns if turns else saved
                    result['historyComplete'] = self.saved_view['historyComplete']
            result["savedHistoryMore"] = bool(self.saved_view and not result["historyComplete"] and not self.saved_view["historyComplete"])
            result["sequence"] = self.sequence
            result["connectionError"] = self.error
            result['attachedParent'] = self.attached_parent
            result["rebuildRecommended"] = self.rebuild_recommended and not self.connected
            result["desktopRestartRecommended"] = self.desktop_restart_recommended and not self.connected
            result["connecting"] = self.connecting
            result['capabilities'] = {'compact': self.connected and result['status'] == 'idle' and not result['requests']}
            result["activating"] = self.activating
            result["loadingHistory"] = self.state is None and self.history_state is None
            if self.access is not None:
                result["access"] = dict(self.access)
            if self.cli_recovery is not None:
                result['cliRecovery'] = dict(self.cli_recovery)
                if self.cli_recovery.get('reason') == 'modelProviderMismatch' and 'access' in result:
                    result['access'].update(readOnly=True, canSend=False)
            self.tool_images = {
                attachment['desktopId']: dict(attachment)
                for turn in result['turns'] for message in turn['messages']
                for attachment in message.get('attachments') or []
                if attachment.get('type') == 'toolImage' and attachment.get('desktopId')
            }
            self.tool_images_sequence = self.sequence
            # Base64 stays in the private index; state, polls and SSE carry ids only.
            result['turns'] = public_turns(result['turns'])
            return result

    def tool_image(self, image_id):
        with self.condition:
            if self.tool_images_sequence != self.sequence:
                self.view()
            return self.tool_images.get(image_id)


def _runtime_scope(host):
    return {'isolated_home': True} if host.startswith('codex:') else {}


class Bridge:
    def observation_enabled(self):
        return True

    def __init__(self, codex_home, data_dir, host="local", alias=None, ipc_path=None, codex_bin=None, goal_rpc=None, owner_goal=None, cli_sessions=None, entry_label=None, owner_guard=None, observation_enabled=None):
        self.host = host
        if observation_enabled is not None:
            self.observation_enabled = observation_enabled
        self.entry_label = entry_label
        self.owner_guard = owner_guard
        self.entry_gate = OperationGate() if host.startswith('codex:') else None
        self.codex_home = Path(codex_home)
        self._draft_remote_lock = threading.Lock()
        self._draft_remote_cache = {}
        self._draft_remote_pending = set()
        self.data_dir = Path(data_dir)
        self.session_pins = SessionPins(data_dir, host + ":" + str(self.codex_home))
        self.question_skips = QuestionSkips(data_dir, host + ":" + str(self.codex_home.resolve()), private_json)
        self.archive_commands = ArchiveCommands(data_dir)
        self.delete_commands = DeleteCommands(data_dir, private_json)
        self.compact_commands = CompactCommands(data_dir, private_json)
        self.released_cli = ReleasedSessions(data_dir, private_json)
        self.cli_maintenance = CliMaintenance()
        self.organization_commands = OrganizationCommands(data_dir, private_json)
        self.hosts = AppHosts(codex_home)
        self.directory_projects = Projects(data_dir)
        if host == 'local':
            from bridge.features.endpoints.registry import Entries
            self.entries = Entries(data_dir, codex_home, local_factory=lambda row, directory:
                self._local_entry(row, directory, codex_bin))
        else:
            self.entries = None
        self.remote_bridges = {}
        self.host_errors = []
        self.listed = set()
        self.activity_following = False
        self.notification_cursor = 0
        self.notification_client_id = None
        self.notification_dirty = set()
        self.cli_notifications = {}
        from bridge.features.notifications.cli import CliWaits
        self.cli_notification_waits = CliWaits(data_dir, private_json)
        self.notification_boundary = set()
        self.notification_event = threading.Event()
        self.notification_slots = threading.BoundedSemaphore(4)
        self.store = RemoteStore(alias) if alias else SessionStore(codex_home)
        self.catalog_reader = RemoteCatalog(alias) if alias else Catalog(codex_home, codex_bin,
            observation_enabled=lambda: self.observation_enabled())
        self.terminals = TerminalManager()
        self.uploads = Uploads(data_dir, (lambda *args: upload_file(alias, *args)) if alias else None)
        executable_getter = (lambda: self.catalog_reader.executable) if self.local else None
        self.account = Account(codex_home, data_dir, codex_bin, executable_getter=executable_getter) if self.local else None
        self.accounts = Accounts(self) if self.local else None
        self.cli_access = CliAccess(self) if host == "local" else None
        self.wsl_accounts = None
        if host == 'local' and sys.platform == 'win32':
            from bridge.features.accounts.wsl_accounts import WslAccounts
            self.wsl_accounts = WslAccounts(self)
        self.goal = goal_rpc or (GoalRPC(codex_home, codex_bin, executable_getter=executable_getter, **_runtime_scope(host)) if self.local else None)
        self.owner_goal = owner_goal
        self.goal_transport = 'owner' if owner_goal else 'sidecar'
        self._open_desktop = open_in_desktop
        self.live = {}
        self.origins = {}
        self.cli_sessions = cli_sessions or CliSessions(self.codex_home, self._cli_state, self._cli_disconnected,
            alias=alias, allow_installed_runtime=self.local, clear_sqlite_home=host.startswith('codex:'))
        self.lock = threading.RLock()
        self.reconnect_pending = {}
        self.ipc = DesktopIPC(ipc_path or ipc_endpoint(codex_home), self._event, self._disconnected)
        self.side_chats = SideChats(codex_bin, self.codex_home, self.data_dir, self.native_host,
                                   executable_getter=executable_getter, **_runtime_scope(host))
        self.closed = threading.Event()
        self.native_model_store = ModelStore(self.data_dir)
        self.native_model_lock = threading.Lock()
        self.native_model_reads = {}
        self.native_model_timers = {}
        self.native_model_refreshing = set()
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
                self.live.setdefault(key.split(":")[0], LiveSession(key.split(":")[0], question_skips=self.question_skips))
        threading.Thread(target=self._maintain, daemon=True).start()
        threading.Thread(target=self._upload_gc, daemon=True).start()

    def _local_entry(self, row, directory, codex_bin):
        target = Bridge(row['home'], directory, host=row['id'], entry_label=row['label'],
                        codex_bin=codex_bin, owner_guard=lambda sid: self.entries.check_ipc_scope(row['id'], sid, self.store),
                        observation_enabled=lambda: self.entries.enabled(row['id']))
        target.accounts.monitor.notification_data_dir = self.data_dir
        if self.accounts.monitor.worker is not None:
            target.accounts.monitor.start()
        return target

    def account_target(self, entry='desktop'):
        if entry in ('desktop', 'local'):
            return self
        if not isinstance(entry, str) or not entry.startswith('codex:') or self.entries is None:
            raise ValueError('此入口不支持本机账号管理')
        return self.entries.get(entry, management=True)

    def accounts_control(self, value):
        value = dict(value)
        target = self.account_target(value.pop('entry', 'desktop'))
        with target.entry_gate if target.entry_gate is not None else nullcontext():
            if target.closed.is_set():
                raise ValueError('Codex 入口已关闭')
            return target.accounts.control(value)

    @property
    def local(self):
        return self.host == 'local' or self.host.startswith('codex:')

    @property
    def native_host(self):
        return 'local' if self.local else self.host

    @property
    def host_label(self):
        return getattr(self, 'entry_label', None) or ('此电脑' if self.host == 'local' else
            self.hosts.hosts().get(self.host, {}).get('displayName', self.host))

    def cli_access_control(self, value):
        """Local desktop control; credential mutations stay off the web route."""
        if not isinstance(value, dict):
            raise ValueError('CLI 接入请求无效')
        if value.get('action') != 'wslAccounts':
            return self.cli_access.control(value)
        manager = self.wsl_accounts
        if manager is None:
            raise ValueError('此网关不支持 WSL 账号')
        operation = value.get('operation')
        if operation == 'environments':
            if set(value) != {'action', 'operation', 'provider'}:
                raise ValueError('WSL 环境查询参数无效')
            return manager.environments(value['provider'])
        request = {key: item for key, item in value.items() if key not in ('action', 'operation')}
        request['action'] = 'read' if operation == 'list' else operation
        return manager.change(request, desktop=True)

    def wsl_account_sources(self, provider):
        from bridge.features.accounts.wsl_account_sources import sources
        return sources(self, provider)

    def wsl_account_import(self, provider, value):
        from bridge.features.accounts.wsl_account_sources import import_api
        return import_api(self, provider, value)

    def wsl_account_guard(self, provider, row, *, read_only=False, release_idle=False):
        from bridge.features.accounts.wsl_account_guard import account_guard
        return account_guard(self, provider, row, read_only=read_only, release_idle=release_idle)

    def _goal_runtime_available(self):
        if not self.local:
            return False
        if self.owner_goal is not None:
            return True
        executable = getattr(self.goal, "executable", None)
        return bool(executable and Path(executable).is_file())

    def _session_goal_available(self, session):
        with session.condition:
            native = session.connected and session.runtime == 'cli'
            known = session.native_goal_supported
        if not native:
            return self._goal_runtime_available()
        if known is None:
            try:
                known = self.cli_sessions.supports_goal(session.id)
            except GoalError:
                return False
            with session.condition:
                if session.connected and session.runtime == 'cli':
                    session.native_goal_supported = known
                    session.access = self._access(session.origin, session)
                    session.changed()
        return known is True

    def _refresh_session_goal(self, session):
        if not self._session_goal_available(session):
            return False
        with session.condition:
            native = session.connected and session.runtime == 'cli'
            due = time.monotonic() >= session.native_goal_refresh_at
        if native and due:
            try:
                goal = normalize_goal_result(self.cli_sessions.get_goal(session.id))
            except GoalError:
                return True
            with session.condition:
                if session.connected and session.runtime == 'cli' and session.state is not None:
                    if session.state.get('threadGoal') != goal:
                        session.state['threadGoal'] = goal
                        session.changed()
                    session.native_goal_refresh_at = time.monotonic() + 2
        return True

    def _save_goal_commands(self):
        target = self.goal_commands_path.with_suffix('.tmp')
        target.write_text(json.dumps(self.goal_commands, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.goal_commands_path)

    def _native_goal_state(self, thread_id, *, strict=False):
        session = self.live.get(thread_id)
        if not self.local and not (session and session.origin in ('cli', 'ide') and session.connected and session.runtime == 'cli'):
            raise GoalUnsupported('目标模式暂不支持 SSH 桌面会话')
        if self.goal or session and session.origin in ('cli', 'ide') and session.runtime == 'cli':
            try:
                native = self._goal_call('get_goal', thread_id)
                # A successful empty answer is authoritative. Do not let a stale
                # desktop snapshot resurrect a goal that native state has cleared.
                return normalize_goal_result(native)
            except GoalUnsupported:
                if session and session.runtime == 'cli':
                    raise
            except GoalError:
                if strict or session and session.runtime == 'cli':
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
                fail('当前 Codex 版本不支持原生目标模式')
                raise IPCError('当前 Codex 版本不支持原生目标模式') from exc
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
            if session.runtime == 'cli':
                with session.condition:
                    if session.state is not None:
                        session.state['threadGoal'] = native
                        session.native_goal_refresh_at = time.monotonic() + 2
                        session.changed()
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

    def project_host(self, key):
        if not isinstance(key, str):
            raise ValueError('请选择电脑 App 中已保存的项目')
        if key in ('local|independent', self.host+'|independent') and self.local:
            return self.host
        hosts = {self.host} if self.host.startswith('codex:') else {'local', *self.hosts.hosts()}
        project = self.directory_projects.get(key)
        if project is None:
            project = next((row for row in self.hosts.projects() if row['key'] == key), None)
        if project and project['host'] in hosts:
            return project['host']
        if key.startswith('cli|'):
            host, _, digest = key[4:].rpartition('|')
            if host in hosts and re.fullmatch('[0-9a-f]{20}', digest):
                return host
        raise ValueError('请选择电脑 App 中已保存的项目')

    def projects(self, host=None):
        if host is not None and host not in ({self.host} if self.host.startswith('codex:') else {'local', *self.hosts.hosts()}):
            raise ValueError('项目环境不存在')
        hosts = ({self.host} if self.host.startswith('codex:') else {'local', *self.hosts.hosts()}) if host is None else {host}
        projects = [dict(project, type=project.get('type', 'desktop'))
                    for project in self.directory_projects.list(hosts)]
        def identity(project):
            cwd = project.get('cwd') or ''
            cwd = os.path.normcase(os.path.realpath(cwd)) if project['host'] == 'local' else cwd.rstrip('/')
            return project['host'], cwd, project.get('type', 'desktop')
        known = {identity(project): project for project in projects}
        for project in self.hosts.projects():
            if project['host'] not in hosts:
                continue
            key = identity(project)
            if key in known:
                known[key]['nativeProject'] = True
            else:
                row = dict(project, type='desktop', nativeProject=True)
                projects.append(row)
                known[key] = row
        candidates = [(self.host, self)] if self.host in hosts else []
        if self.host == 'local':
            for remote_host in self.hosts.hosts():
                if remote_host in hosts:
                    candidates.append((remote_host, self.for_host(remote_host)))
        def read(candidate):
            try:
                if host is None and isinstance(candidate[1].store, RemoteStore):
                    return candidate[1].store.poll('list', {'limit': 500})
                return candidate[1].store.list(limit=500)
            except (StoreUnavailable, RemoteUnavailable):
                return []
        with ThreadPoolExecutor(max_workers=max(1, min(4, len(candidates)))) as pool:
            groups = list(pool.map(read, candidates))
        seen = {(project['host'], project['cwd']) for project in projects if project.get('type') == 'cli'}
        for (host, bridge), rows in zip(candidates, groups):
            for row in rows:
                cwd = row.get('cwd')
                if row.get('origin') not in ('cli', 'ide') or not isinstance(cwd, str) or not cwd or (host, cwd) in seen:
                    continue
                seen.add((host, cwd))
                label = self.host_label if host == self.host else self.hosts.hosts()[host].get('displayName', host)
                projects.append({'key': 'cli|' + host + '|' + hashlib.sha256(cwd.encode()).hexdigest()[:20],
                                 'type': 'cli', 'host': host, 'hostLabel': label,
                                 'name': project_path_name(cwd) or cwd, 'cwd': cwd})
        for project in projects:
            project['environment'] = project.get('type', 'desktop') + '|' + project['host']
        return projects

    def project_environments(self):
        hosts = {self.host: {'displayName': self.host_label}} if self.host.startswith('codex:') else {'local': {'displayName': '此电脑'}, **self.hosts.hosts()}
        return [{'key': kind + '|' + host, 'type': kind, 'surface': kind, 'host': host,
                 'hostLabel': info.get('displayName') or info.get('alias') or host,
                 'name': (info.get('displayName') or info.get('alias') or host) +
                         (' · Codex App' if kind == 'desktop' else ' · Codex CLI')}
                for host, info in hosts.items() for kind in (('cli',) if self.host.startswith('codex:') else ('desktop', 'cli'))]

    @operation(host='host')
    def register_project(self, path, create=False, host='local', kind='desktop'):
        if kind not in ('desktop', 'cli'):
            raise ValueError('未知聊天类型')
        if host == self.host and self.local:
            identity = project_directory(path, create)
            label = '此电脑'
        else:
            info = self.hosts.hosts().get(host)
            if info is None: raise ValueError('请选择已登记的运行主机')
            source = source_text('features/sessions/create.py')
            source += '\nprint(json.dumps(project_directory(**' + payload({'value': path, 'create': create}) + ')))\n'
            identity = ssh_read(info['alias'], source)
            label = info.get('displayName') or info['alias']
        return self.directory_projects.save(host, label, identity,
            scope='cli' if kind == 'cli' else None, type=kind, environment=kind+'|'+host,
            hostIdentity=None if host == self.host and self.local else info['alias'])

    @operation(host='host')
    def project_directories(self, host, path=None, hidden=False, name=None, identity=None):
        if host == self.host and self.local: return project_directory_listing(path, hidden, name, identity)
        info = self.hosts.hosts().get(host)
        if info is None: raise ValueError('请选择已登记的运行主机')
        source = source_text('features/sessions/create.py')
        source += '\nprint(json.dumps(project_directory_listing(**' + payload({'value':path,'hidden':hidden,'name':name,'identity':identity}) + ')))\n'
        return ssh_read(info['alias'], source)

    @operation(project='key')
    def project_trust(self, key, confirmed=False, confirmation=None):
        if type(confirmed) is not bool or not isinstance(key, str):
            raise ValueError('目录信任请求无效')
        project = self.directory_projects.get(key)
        if not project:
            if not confirmed and any(row['key'] == key for row in self.hosts.projects()):
                return {'required': False}
            raise ValueError('请重新选择项目目录')
        if project.get('type') != 'desktop':
            raise ValueError('请选择 Codex App 项目')
        token = hashlib.sha256(json.dumps(project, sort_keys=True).encode()).hexdigest()
        if confirmed and confirmation != token:
            raise ValueError('所选项目已变化，请重新确认')
        if project['host'] == 'local':
            result = project_trust(self.catalog_reader.executable, self.codex_home,
                                   project['directoryIdentity'], confirmed)
        else:
            info = self.hosts.hosts().get(project['host'])
            if not info or info['alias'] != project.get('hostIdentity'):
                raise ValueError('原 SSH 主机配置已变化，请重新选择项目')
            source = source_text('features/sessions/create.py')
            source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
            source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
            source += 'print(json.dumps(project_trust(runtime, home, **' + payload({
                'identity': project['directoryIdentity'], 'confirmed': confirmed}) + ')))\n'
            result = ssh_read(info['alias'], source, timeout=90)
        return dict(result, required=not result['trusted'], confirmation=token,
                    environment=project['hostLabel'] + ' · Codex App')

    def _saved_desktop_project(self, cwd, host='local'):
        if host != 'local':
            return next((row for row in self.hosts.projects() if row['host'] == host and
                         row['cwd'].rstrip('/') == cwd.rstrip('/')), None)
        canonical = os.path.normcase(os.path.realpath(cwd))
        return next((row for row in self.hosts.projects() if row['host'] == 'local' and
                     os.path.normcase(os.path.realpath(row['cwd'])) == canonical), None)

    def _confirm_desktop_project(self, project, entry):
        host = project['host']
        cwd = project['cwd']
        if host == 'local':
            cwd = project_directory(cwd, expected=project['directoryIdentity'])['cwd']
        else:
            info = self.hosts.hosts().get(host)
            if not info or info['alias'] != project.get('hostIdentity'):
                raise ValueError('原 SSH 主机配置已变化，请重新选择项目')
            source = source_text('features/sessions/create.py')
            source += '\nprint(json.dumps(project_directory(**' + payload({
                'value': cwd, 'expected': project['directoryIdentity']}) + ')))\n'
            ssh_read(info['alias'], source)
        native = self._saved_desktop_project(cwd, host)
        if native is None:
            if entry.get('phase') in ('registering-project', 'project-ready'):
                raise CreationError('原生项目登记结果尚未确认，请在电脑 App 完成目录确认后重试同一请求；尚未创建聊天')
            # Validate and prepare only this host's import before recording a launch.
            config = self.hosts.project_import(project) if host != 'local' else None
            entry['phase'] = 'registering-project'
            self._save_creations()
            try:
                if config is None:
                    open_project_in_desktop(cwd)
                else:
                    self.hosts.write_project_import(config, private_json)
                    apply_desktop_config()
            except (OSError, CreationError, subprocess.SubprocessError) as exc:
                raise CreationError('原生项目打开结果尚未确认，请检查电脑 App；尚未创建聊天，不会重复登记') from exc
            deadline = time.monotonic() + 12
            while native is None and time.monotonic() < deadline:
                time.sleep(.25)
                native = self._saved_desktop_project(cwd, host)
            if native is None:
                raise CreationError('请在电脑 App 完成项目目录确认后重试同一请求；尚未创建聊天，不会重复登记')
        entry['phase'] = 'project-ready'
        self._save_creations()
        return dict(project, nativeProject=True, name=native.get('name') or project.get('name'))

    def creation_models(self, project_key=None, kind='desktop', refresh=False, host=None, path=None):
        if self.host.startswith('codex:'):
            kind = 'cli'
        if kind not in ('desktop', 'cli'):
            raise ValueError('未知聊天类型')
        if project_key in ('local|independent', self.host+'|independent') and self.local:
            root = self.data_dir/'independent-chats'
            project = {'host': self.host, 'cwd': str((root if root.is_dir() else self.data_dir).resolve())}
        elif project_key:
            project = next((row for row in self.projects(host=self.project_host(project_key))
                            if row['key'] == project_key and (row.get('type') or 'desktop') == kind), None)
        elif path and host:
            environment = next((row for row in self.project_environments() if row['host'] == host and
                                (row.get('type') or row.get('kind') or row.get('surface') or 'desktop') == kind), None)
            if environment is None:
                raise ValueError('运行环境已变化，请重新选择')
            if host == self.host and self.local:
                cwd = project_directory(path)['cwd']
            else:
                source = source_text('features/sessions/create.py')+'\nprint(json.dumps(project_directory(**'+payload({'value': path})+')))\n'
                cwd = ssh_read(self.hosts.hosts()[host]['alias'], source)['cwd']
            project = {'host': host, 'cwd': cwd}
        else:
            project = None
        if project is None:
            raise ValueError('请选择电脑 App 中已保存的项目')
        target = self.for_host(project['host'])
        if kind == 'cli':
            config = target.cli_sessions.creation_context(project['cwd'])
            provider = config.get('model_provider') or 'openai'
            value = target._native_models(None, project['cwd'], provider, refresh=refresh)
            value = {**value, 'currentModel': config.get('model'), 'currentEffort': config.get('model_reasoning_effort')}
        else:
            value = target.catalog_reader.get_kind('models', project['cwd'], refresh=refresh)
        api = value.get('modelSource') == 'api'
        return {**value, 'capabilities': {'models': True, 'effort': True, 'manualModels': api, 'manualEffort': api}}

    @operation(project='project_key')
    def create_chat(self, project_key, title, request_id, kind='desktop', model_options=None):
        from bridge.features.sessions.creation_models import intent
        model_options = model_options or {}
        if self.host.startswith('codex:'):
            kind = 'cli'
        if not isinstance(request_id, str):
            raise ValueError('创建请求标识无效')
        uuid.UUID(request_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise ValueError('请输入 1–120 字的聊天名称')
        if kind not in ('desktop', 'cli'):
            raise ValueError('未知聊天类型')
        available = [project for project in self.projects(host=self.project_host(project_key))
                     if (project.get('type') or 'desktop') == kind]
        project = next((p for p in available if p['key'] == project_key), None)
        if self.local and project_key in ('local|independent', self.host+'|independent'):
            project = {'host': self.host, 'cwd': None}
        if project is None:
            raise ValueError('请选择电脑 App 中已保存的项目')
        title = title.strip()
        with self.create_lock:
            entry = self.creations.get(request_id)
            if entry:
                if (entry['project'] != project_key or entry['title'] != title or
                        entry.get('kind', 'desktop') != kind or
                        intent(entry.get('modelOptions', {})) != intent(model_options) or
                        entry.get('cwd', project['cwd']) != project['cwd']):
                    raise ValueError('同一创建请求不能用于不同内容')
                if not entry.get('id') and entry.get('phase') not in ('registering-project', 'project-ready'):
                    raise CreationError('上次创建结果尚不确定，请先刷新聊天列表并检查电脑 App，避免重复创建')
            else:
                if model_options:
                    from bridge.features.sessions.creation_models import validate
                    validate(model_options, self.creation_models(project_key, kind))
                if project.get('hostIdentity') and self.hosts.hosts().get(project['host'], {}).get('alias') != project['hostIdentity']:
                    raise ValueError('原 SSH 主机配置已变化，请重新选择项目')
                if kind == 'cli':
                    cli_bridge = self.for_host(project['host'])
                    if (not cli_bridge.local and
                            cli_bridge.cli_sessions.alias != self.hosts.hosts()[project['host']]['alias']):
                        raise ValueError('原 SSH 主机配置已变化，请重启网关后重新选择项目')
                if kind == 'desktop':
                    self.ipc.connect()
                entry = {'project': project_key, 'title': title, 'kind': kind,
                         'cwd': project['cwd'], 'host': project['host'],
                         'directoryIdentity': project.get('directoryIdentity'), 'at': time.time()}
                if model_options:
                    entry['modelOptions'] = model_options
                self.creations[request_id] = entry
                self._save_creations()
            if not entry.get('id'):
                if (kind == 'desktop' and project.get('directoryIdentity') and
                        (not project.get('nativeProject') or entry.get('phase') in ('registering-project', 'project-ready'))):
                    project = self._confirm_desktop_project(project, entry)
                if kind == 'cli':
                    def mark_started():
                        entry['started'] = True
                        self._save_creations()

                    def mark_created(created_id):
                        entry['id'] = created_id
                        self._save_creations()

                    try:
                        if project.get('directoryIdentity'):
                            if cli_bridge.local:
                                project_directory(project['cwd'], expected=project['directoryIdentity'])
                            else:
                                source = source_text('features/sessions/create.py')
                                source += '\nprint(json.dumps(project_directory(**' + payload({
                                    'value': project['cwd'], 'expected': project['directoryIdentity']}) + ')))\n'
                                ssh_read(cli_bridge.cli_sessions.alias, source)
                        if (not cli_bridge.local and
                                cli_bridge.cli_sessions.alias != self.hosts.hosts().get(project['host'], {}).get('alias')):
                            raise ValueError('原 SSH 主机配置已变化，请重启网关后重新选择项目')
                        runtime_cwd = project['cwd'] or independent_directory(self.data_dir, request_id)
                        state = cli_bridge.cli_sessions.create(
                            runtime_cwd, title, on_start=mark_started, on_created=mark_created,
                            **{key: model_options[key] for key in ('model', 'effort') if key in model_options})
                        thread_id = state['id']
                    except AppServerUnavailable:
                        if not entry.get('started'):
                            del self.creations[request_id]
                            self._save_creations()
                        raise
                elif project['host'] == 'local':
                    try:
                        cwd = (project_directory(project['cwd'], expected=project['directoryIdentity'])['cwd']
                               if project.get('directoryIdentity') else project['cwd']) or independent_directory(self.data_dir, request_id)
                        native_project = ({'name': project.get('name'),
                            'required': bool(project.get('directoryIdentity') and not project.get('nativeProject'))} if project['cwd'] else None)
                        if entry.get('phase') == 'project-ready':
                            entry['phase'] = 'creating-thread'
                            self._save_creations()
                        thread_id = create_empty(self.catalog_reader.executable, self.codex_home, cwd, title,
                                                 project=native_project, **{key: model_options[key] for key in ('model', 'effort') if key in model_options})
                        entry['runtimeCwd'] = cwd
                    except CreationUnavailable:
                        # No thread mutation happened: retrying this request cannot
                        # create a duplicate. Keep uncertain post-start results recorded.
                        if entry.get('phase') == 'creating-thread':
                            entry['phase'] = 'project-ready'
                        else:
                            del self.creations[request_id]
                        self._save_creations()
                        raise
                else:
                    host = self.hosts.hosts()[project['host']]
                    source = source_text('features/sessions/create.py')
                    source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                    source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                    if project.get('directoryIdentity'):
                        source += 'project_directory(**' + payload({'value': project['cwd'], 'expected': project['directoryIdentity']}) + ')\n'
                    source += 'print(json.dumps({"id":create_empty(runtime, home, **' + payload({'cwd': project['cwd'], 'title': title,
                        **{key: model_options[key] for key in ('model', 'effort') if key in model_options}}) + ')}))\n'
                    if entry.get('phase') == 'project-ready':
                        entry['phase'] = 'creating-thread'
                        self._save_creations()
                    thread_id = ssh_read(host['alias'], source, timeout=90)['id']
                uuid.UUID(thread_id)
                entry['id'] = thread_id
                if kind == 'desktop' and model_options:
                    entry['modelSettingsPhase'] = 'pending'
                self._save_creations()
                if kind == 'cli':
                    bridge = cli_bridge
                    with bridge.lock:
                        session = bridge.live.setdefault(thread_id, LiveSession(thread_id, question_skips=bridge.question_skips))
                    with session.condition:
                        session.origin = 'cli'
                        session.origin_verified = True
                        session.saved_cwd = runtime_cwd
                        session.saved_provider = state['modelProvider']
                        session.runtime = 'cli'
                        session.owner = 'cli:' + thread_id
                        session.connected = True
                        session.state = state
                        session.access = bridge._access('cli', session)
                        session.changed()
            bridge = self.for_host(entry['host'])
            if isinstance(bridge.store, RemoteStore):
                with bridge.store.lock:
                    bridge.store.cache.clear()
            if kind == 'cli':
                return {'id': entry['id'], 'host': entry['host'], 'opened': False,
                        'message': 'CLI 聊天已创建，可在此继续'}
            try:
                open_in_desktop(entry['id'], entry['host'])
                opened = True
            except (OSError, CreationError, subprocess.SubprocessError):
                opened = False
            self._complete_creation_settings(entry, bridge)
            return {'id': entry['id'], 'host': entry['host'], 'opened': opened,
                    'message': '已创建，正在连接桌面 App' if opened else '聊天已创建，请在电脑 App 打开后重新连接'}

    def _complete_creation_settings(self, entry, bridge):
        # An empty thread's temporary creator does not own Desktop's composer.
        # Complete the explicit creation through that original owner, never from
        # passive history/attachment or by overriding the account's defaults.
        phase = entry.get('modelSettingsPhase')
        if phase not in ('pending', 'unknown'):
            return
        try:
            session = bridge._target(entry['id'])
        except (IPCError, StoreUnavailable) as exc:
            raise CreationError('聊天已创建，所选模型尚未应用；请重试本次创建以继续连接同一聊天，不会重复创建') from exc
        choice = entry['modelOptions']
        effort = choice.get('effort')
        with session.action_lock:
            with session.condition:
                state = session.state or {}
                expected_cwd = entry.get('runtimeCwd') or entry.get('cwd')
                if (session.runtime != 'desktop' or state.get('id') != entry['id'] or
                        not same_cwd(expected_cwd, state.get('cwd'), bridge.local and os.name == 'nt') or
                        not state.get('modelProvider') or
                        session.saved_provider and session.saved_provider != state['modelProvider']):
                    raise CreationError('聊天已创建，但原桌面会话身份尚未确认；请重新连接同一聊天，不要重复创建')
                confirmed = state.get('latestModel') == choice['model'] and (effort is None or session.view().get('effort') == effort)
                if not confirmed and phase == 'unknown':
                    raise CreationError('聊天已创建，但此前模型设置结果仍待核对；请在该聊天确认模型，不会重复提交')
                if not confirmed and (ordered_turns(state) or
                        state.get('threadRuntimeStatus', {}).get('type') != 'idle'):
                    raise CreationError('聊天已有内容或正在运行，未覆盖原生模型设置；请在该聊天确认模型')
            if not confirmed:
                entry['modelSettingsPhase'] = 'unknown'
                self._save_creations()
                try:
                    if effort is not None:
                        result = bridge._apply_settings(session, choice['model'], effort)
                    else:
                        # API catalogs can explicitly omit reasoning controls.
                        # Preserve native effort rather than inventing a default.
                        result = bridge._call(session, 'thread-follower-update-thread-settings',
                                              {'threadSettings': {'model': choice['model']}})
                        with session.condition:
                            result['confirmed'] = bool(result.get('applied')) and session.condition.wait_for(
                                lambda: session.state.get('latestModel') == choice['model'], timeout=5)
                except ValueError as exc:
                    # Model/catalog validation rejects before dispatching a write.
                    entry['modelSettingsPhase'] = 'pending'
                    self._save_creations()
                    raise CreationError('聊天已创建，所选模型尚未应用：' + str(exc)) from exc
                except IPCError as exc:
                    raise CreationError('聊天已创建，但模型设置结果仍待核对；请在该聊天确认模型，不会重复提交') from exc
                if not result.get('confirmed'):
                    raise CreationError('聊天已创建，但模型设置尚未确认；请在该聊天确认模型，不会重复提交')
            entry['modelSettingsPhase'] = 'applied'
            self._save_creations()

    def _save_actions(self):
        target = self.actions_path.with_suffix('.tmp')
        target.write_text(json.dumps(self.message_actions, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.actions_path)

    def _fork_origin(self, thread_id):
        with self.message_lock:
            entry = next((v for v in self.message_actions.values() if v.get('id') == thread_id and v['action'] in ('fork', 'edit-fork')), None)
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
            if key in latest or key in permissions:
                result[key] = latest[key] if key in latest else permissions[key]
        return result

    @operation
    def message_action(self, thread_id, body):
        self._require_desktop_feature(thread_id)
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
                    session.timeline.update(session.view(), limit=session.timeline_limit)
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
                        if self.local:
                            if self.host.startswith('codex:'):
                                args.update(thread_source='cli', isolated_home=True)
                            child = fork_copy(self.catalog_reader.executable, self.codex_home, **args)
                        else:
                            host = self.hosts.hosts()[self.host]
                            source = source_text('features/sessions/create.py')
                            source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                            source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                            source += 'try:\n print(json.dumps({"id":fork_copy(runtime, home, **' + payload(args) + ')}))\n'
                            source += 'except CreationUnavailable as exc:\n print(json.dumps({"unavailable":str(exc)}))\n'
                            result = ssh_read(host['alias'], source, timeout=120)
                            if 'unavailable' in result:
                                raise ForkUnavailable(result['unavailable'])
                            child = result['id']
                    except CreationUnavailable:
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

    def for_host(self, host, *, cached=False):
        if host == self.host:
            return self
        if isinstance(host, str) and host.startswith(('wsl:', 'codex:')) and self.entries is not None:
            return self.entries.get(host)
        available = self.hosts.hosts(cached=True) if cached else self.hosts.hosts()
        if self.host != "local" or host not in available:
            raise KeyError("未知 SSH 主机")
        with self.lock:
            if host not in self.remote_bridges:
                folder = self.data_dir / 'hosts' / hashlib.sha256(host.encode()).hexdigest()[:16]
                self.remote_bridges[host] = Bridge(self.codex_home, folder, host, available[host]['alias'], ipc_path=self.ipc.path,
                                                  observation_enabled=lambda: self.observation_enabled())
                self.remote_bridges[host].accounts = self.accounts
            return self.remote_bridges[host]

    def ssh_status(self):
        return [{'host': host, 'label': info.get('displayName') or info['alias'],
                 **ssh_connection(info['alias']).status()}
                for host, info in self.hosts.hosts().items()]

    def retry_ssh(self, host):
        info = self.hosts.hosts().get(host) if isinstance(host, str) else None
        if not info:
            raise ValueError('请选择已登记的 SSH 主机')
        target = self.for_host(host)
        if target.store.alias != info['alias']:
            raise ValueError('SSH 主机配置已变化，请重启网关后重试')
        retry_ssh(info['alias'])
        with target.store.poll_lock:
            target.store.poll_results.clear()

    def assert_desktop_idle(self, timeout=20, identifiers=None, allow_missing_ipc=False, allow_stopped_app=False,
                            *, desktop_only=False):
        """Passively check every desktop owner before an application shutdown.

        The caller holds the account operation gate until shutdown completes.
        Saved rollouts and the gateway's existing follow cache are not evidence
        that a desktop-only or remote task is idle.
        """
        deadline = time.monotonic() + timeout
        unknown = '无法确认 Codex 桌面任务状态，请检查桌面后重试'
        busy = 'Codex 有任务运行或等待确认，请先结束任务再关闭'
        desktop_absent = {}

        def native_connection(host, identifier):
            source = self if host == self.host else self.remote_bridges.get(host)
            if source is None:
                return None
            with source.lock:
                session = source.live.get(identifier)
            return Accounts._native_cli_connection(source, identifier, session)

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                # An incomplete audit cannot use the unavailable-state quit override.
                raise ValueError('Codex 任务核验超时，仍有会话未检查，请稍后重试')
            return value

        def check_submissions(*, final=False):
            blockers = self.accounts.blockers()
            if desktop_only:
                affected = []
                for row in blockers:
                    key = (row.get('host'), row.get('id'))
                    connection = native_connection(*key) if row['reason'] in ('running', 'approval') else None
                    # Defer native routes to this operation's owner audit. Only
                    # its exact no-client-found response can exempt the task.
                    if connection is not None:
                        if not final or desktop_absent.get(key) == connection:
                            continue
                        raise TaskStateUnavailable(unknown)
                    affected.append(row)
                blockers = affected
            if identifiers is not None:
                blocked = [row for row in blockers if row['host'] == self.host and row['id'] in identifiers]
            else:
                blocked = blockers
            if blocked:
                raise ValueError('Codex 有任务或待确认操作，请先处理后再关闭')

        check_submissions()
        try:
            # AppHosts normally tolerates damaged metadata for list rendering.
            # A lifecycle decision must not silently lose its remote coverage.
            metadata = self.codex_home / '.codex-global-state.json'
            if metadata.exists() and not isinstance(json.loads(metadata.read_text(encoding='utf-8')), dict):
                raise ValueError('Invalid desktop metadata')
            hosts = self.hosts.lifecycle_hosts() if identifiers is None else {}
            sources = {self.host: self, **{key: value for key, value in self.remote_bridges.items() if key in hosts}}
            if desktop_only:
                for host, source in self.remote_bridges.items():
                    with source.lock:
                        if any(getattr(live, 'runtime', None) == 'cli' and getattr(live, 'connected', False)
                               for live in source.live.values()):
                            sources[host] = source
            for host in hosts:
                remaining()
                if host not in sources:
                    sources[host] = self.for_host(host)
        except Exception as exc:
            raise ValueError('无法读取 Codex 桌面和 SSH 会话，请检查连接后重试') from exc
        check_submissions()
        # SSH metadata already has its own finite transport timeout. Waiting on
        # these read-only jobs is also bounded by this operation's deadline.
        executor = ThreadPoolExecutor(max_workers=min(4, len(sources)))
        reads = {host: executor.submit(source.store.lifecycle_threads) for host, source in sources.items()} if identifiers is None else {}
        threads = []
        metadata_error = None
        metadata_deadline = time.monotonic() + remaining() / 2
        try:
            for host, source in sources.items():
                source_identifiers = set()
                try:
                    rows = reads[host].result(timeout=max(0, metadata_deadline - time.monotonic())) if identifiers is None else [{'id': sid} for sid in identifiers]
                    source_identifiers = {row['id'] for row in rows}
                    if any(not isinstance(sid, str) or not sid for sid in source_identifiers):
                        source_identifiers = set()
                        raise ValueError('Invalid desktop session metadata')
                except Exception as exc:
                    # Unavailable SSH metadata must not hide a busy local owner.
                    # Retain the gap and inspect every available source first.
                    metadata_error = exc
                if identifiers is None and getattr(source, 'live', None):
                    with source.lock:
                        source_identifiers.update(sid for sid, live in source.live.items() if (
                                           getattr(live, "origin", "desktop") == "desktop" or
                                           (getattr(live, "runtime", "desktop") == "desktop" and getattr(live, "connected", False)) or
                                           (desktop_only and getattr(live, 'runtime', None) == 'cli' and getattr(live, 'connected', False))))
                threads.extend((host, sid) for sid in sorted(source_identifiers))
        finally:
            for read in reads.values():
                read.cancel()
            executor.shutdown(wait=False)
        if metadata_error and not threads:
            raise TaskStateUnavailable('无法读取 Codex 桌面和 SSH 会话，请检查连接后重试') from metadata_error

        condition = threading.Condition()
        followed = {}
        disconnected = False

        def disconnect():
            nonlocal disconnected
            with condition:
                disconnected = True
                condition.notify_all()

        def event(message):
            params = message.get('params', {})
            with condition:
                if message.get('method') == 'client-status-changed':
                    if params.get('status') == 'disconnected':
                        for record in followed.values():
                            if record['owner'] == params.get('clientId'):
                                record['invalid'] = True
                        condition.notify_all()
                    return
                if message.get('method') != 'thread-stream-state-changed':
                    return
                key = (params.get('hostId'), params.get('conversationId'))
                record = followed.get(key)
                if record is None:
                    return
                try:
                    if message.get('version') != 11 or message.get('sourceClientId') != record['owner']:
                        raise ValueError('Desktop owner changed')
                    change = params['change']
                    if type(change.get('revision')) is not int:
                        raise ValueError('Missing revision')
                    if record['revision'] is not None and change['revision'] < record['revision']:
                        raise ValueError('Stale revision')
                    if change.get('type') == 'snapshot':
                        state = change['conversationState']
                    elif change.get('type') == 'patches' and record['revision'] is not None:
                        if change.get('baseRevision') != record['revision']:
                            raise ValueError('Revision mismatch')
                        state = apply_patches(record['state'], change['patches'])
                    else:
                        raise ValueError('Missing snapshot')
                    if not isinstance(state, dict) or state.get('id') != key[1]:
                        raise ValueError('Session mismatch')
                    record.update(state=state, revision=change['revision'])
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    record['invalid'] = True
                condition.notify_all()

        def check_state(record):
            if disconnected or record['invalid'] or record['state'] is None:
                raise TaskStateUnavailable(unknown)
            state = record['state']
            try:
                runtime = state.get('threadRuntimeStatus', {}).get('type')
                if (runtime in ('active', 'running', 'waiting', 'busy') or pending_requests(state)
                        or any(turn.get('status') == 'inProgress' for turn in ordered_turns(state))):
                    raise ValueError(busy)
                if runtime != 'idle':
                    raise TaskStateUnavailable(unknown)
            except (TypeError, AttributeError, KeyError) as exc:
                raise TaskStateUnavailable(unknown) from exc

        ipc = DesktopIPC(self.ipc.path, on_event=event, on_disconnect=disconnect)
        audits = ThreadPoolExecutor(max_workers=min(8, max(1, len(threads))))
        try:
            remaining()
            try:
                handshake = audits.submit(ipc.connect, deadline=deadline)
                try:
                    handshake.result(timeout=remaining())
                except TimeoutError as exc:
                    raise TaskStateUnavailable(unknown) from exc
            except IPCError as exc:
                if isinstance(exc.__cause__, FileNotFoundError):
                    if allow_missing_ipc:
                        return
                    if allow_stopped_app and self.host == 'local':
                        try:
                            executable = self.accounts.index.get('desktopExecutable') or DesktopApp.discover(self.catalog_reader.executable)
                            if not executable:
                                raise TaskStateUnavailable(unknown)
                            app = DesktopApp(executable, self.codex_home)
                            app.validate(self.catalog_reader.executable)
                            if app.processes():
                                raise TaskStateUnavailable(unknown)
                        except (OSError, ValueError, subprocess.SubprocessError) as error:
                            raise TaskStateUnavailable(unknown) from error
                        return
                raise
            def inspect_thread(host, sid, budget):
                if time.monotonic() >= deadline or disconnected:
                    return True
                native = native_connection(host, sid) if desktop_only else None
                try:
                    response = ipc.request('thread-owner-discovery', {'hostId': host, 'conversationId': sid},
                                           host=host, timeout=min(6, budget),
                                           deadline=min(deadline, time.monotonic() + budget))
                except IPCError as exc:
                    if str(exc) == 'no-client-found':
                        if native is not None and native_connection(host, sid) == native:
                            with condition:
                                if disconnected or time.monotonic() >= deadline:
                                    return True
                                desktop_absent[(host, sid)] = native
                            return False
                        live = sources[host].live.get(sid)
                        return bool(live and (getattr(live, 'owner', None) or
                                              getattr(live, 'desktop_restart_recommended', False)))
                    return True
                owner = response.get('handledByClientId')
                if not isinstance(owner, str) or not owner:
                    return True
                record = {'owner': owner, 'state': None, 'revision': None, 'invalid': False}
                with condition:
                    if disconnected or time.monotonic() >= deadline:
                        return True
                    followed[(host, sid)] = record
                try:
                    ipc.follow(sid, owner, host=host)
                    with condition:
                        condition.wait_for(lambda: record['state'] is not None or record['invalid'] or disconnected,
                                           timeout=min(4, budget, max(0, deadline - time.monotonic())))
                        check_state(record)
                except (IPCError, TaskStateUnavailable):
                    return True
                return False

            def inspect_threads(candidates):
                unavailable = False
                workers = min(8, len(candidates))
                if not workers:
                    return False
                pending = set()
                submitted = 0

                def submit_next():
                    nonlocal submitted
                    # Reserve both discovery and snapshot time, scaled by the
                    # bounded parallelism rather than dividing 20s by all history.
                    budget = remaining() * workers / max(workers, len(candidates) - submitted) / 2
                    host, sid = candidates[submitted]
                    submitted += 1
                    pending.add(audits.submit(inspect_thread, host, sid, budget))

                for _ in range(workers):
                    submit_next()
                while pending:
                    done, _ = wait(pending, timeout=remaining(), return_when=FIRST_COMPLETED)
                    if not done:
                        raise ValueError('Codex 任务核验超时，仍有会话未检查，请稍后重试')
                    pending.difference_update(done)
                    for future in done:
                        unavailable = future.result() or unavailable
                    for _ in done:
                        if submitted < len(candidates):
                            submit_next()
                return unavailable

            unavailable = inspect_threads(threads)
            if desktop_only and desktop_absent:
                # Absence has no live snapshot subscription. Recheck native
                # candidates immediately before the final lifecycle decision.
                native_threads = list(desktop_absent)
                desktop_absent.clear()
                unavailable = inspect_threads(native_threads) or unavailable
            remaining()
            check_submissions(final=True)
            with condition:
                if disconnected:
                    raise TaskStateUnavailable(unknown)
                for record in followed.values():
                    try:
                        check_state(record)
                    except TaskStateUnavailable:
                        unavailable = True
            if unavailable:
                raise TaskStateUnavailable(unknown)
            if metadata_error:
                raise TaskStateUnavailable('无法读取 Codex 桌面和 SSH 会话，请检查连接后重试') from metadata_error
        except IPCError as exc:
            raise TaskStateUnavailable(unknown) from exc
        finally:
            disconnect()
            ipc.close()
            audits.shutdown(wait=False, cancel_futures=True)

    def _access(self, origin, session=None):
        desktop = origin not in ('cli', 'ide') and not self.host.startswith('codex:')
        connected = bool(session and session.connected)
        desktop_controls = desktop or bool(connected and origin == 'ide' and session.runtime == 'desktop' and self.local)
        writable = desktop or connected
        reconnect = desktop or origin == 'ide' or origin == 'cli' and self.cli_sessions.available()
        cli_controls = connected and origin in ('cli', 'ide')
        goal = (self._goal_runtime_available()
                if not session or session.runtime != 'cli' else session.native_goal_supported is True)
        return {'source': origin, 'runtime': (session.runtime if connected else None),
                'readOnly': not writable, 'canSend': writable, 'canInterrupt': writable,
                'canApprove': writable, 'canReconnect': reconnect,
                'canConfigure': desktop_controls, 'canChangePermissions': desktop or cli_controls,
                'canChangeModel': desktop or cli_controls,
                'canSelectSkills': desktop or cli_controls,
                'canUseModes': desktop or cli_controls,
                'canUseGoal': (desktop and self.host == 'local') or cli_controls and goal,
                'canUseSideChat': desktop_controls, 'canEditMessages': desktop_controls,
                'reason': None if reconnect else '未找到可用的 Codex CLI 后台服务或已安装的服务包；请先在电脑上安装或更新 Codex CLI，并运行一次。'}

    def _require_desktop_feature(self, thread_id):
        metadata = self.store.get(thread_id)
        if metadata.get('origin') == 'ide':
            session = self.session(thread_id, attach=False, background=True)
            if not self.local or not session.connected or session.runtime != 'desktop':
                raise ValueError('此功能需要已连接的本机 VS Code 原会话')
        elif metadata.get('origin') == 'cli':
            raise ValueError('此功能请在原 CLI 中操作。')

    def cli_network(self, thread_id, body=None, *, automatic=False, _waiting=None, _follow_account=False):
        from bridge.clients.codex.cli_session import _managed_cli
        from bridge.clients.codex.daemon_network import (inspect_proxy, configuration_stamp,
            graceful_restart_supported, system_proxy_user_layer, enable_system_proxy_following,
            native_recovery_target, daemon_version, CliProviderMismatch, file_auth_provider, AUTH_ENV_NAMES)
        from bridge.features.accounts.cli_sync import LocalCliSync
        from bridge.clients.codex.desktop_runtime import exclusive_lock
        from bridge.clients.codex.release import ReleaseNotSent
        from bridge.platforms.codex_daemon_env import read_daemon_environment, configuration_after_start
        from contextlib import closing
        from bridge.clients.codex.app_server import CodexAppServer
        import sys
        origin = self.store.get(thread_id).get('origin')
        if (self.host != 'local' and not self.host.startswith('codex:') or origin not in ('cli', 'ide')
                or _follow_account and origin != 'cli'):
            raise ValueError('只支持本机 Codex CLI')
        if sys.platform not in ('darwin', 'linux'):
            if body is not None:
                raise ValueError('此平台请在电脑上正常重启 CLI 服务')
            return {'canRecover': False, 'message': '此平台请在电脑上正常重启 CLI 服务'}
        cli = self.cli_sessions
        if _follow_account and (cli.alias is not None or cli.home.resolve() != self.accounts.home.resolve()):
            raise IPCError('当前 CLI 账号不属于此配置；操作未发送。')
        if isinstance(body, dict) and set(body) == {'action', 'operationId'} and body['action'] in ('status', 'cancel'):
            with self.cli_maintenance.lock:
                record = self._cli_recovery_record()
                if (record.get('purpose') != 'recovery' or record.get('operationId') != body['operationId']
                        or record.get('threadId') != thread_id or record.get('host') != self.host
                        or record.get('home') != str(cli.home.resolve())):
                    raise ValueError('恢复请求已变化，请重新检查')
                if body['action'] == 'cancel':
                    if record['phase'] != 'waiting':
                        raise ValueError('恢复已开始或结束，不能取消等待')
                    record['phase'] = 'cancelled'
                    private_json(self.data_dir/'cli-daemon-recovery.json', record)
                    self.cli_maintenance.cancel()
                return self.cli_maintenance.status(record)
        if body is None and not automatic:
            record = self._cli_recovery_record()
            if (record.get('purpose') == 'recovery' and record.get('phase') == 'waiting'
                    and record.get('host') == self.host and record.get('home') == str(cli.home.resolve())):
                return self.cli_maintenance.status(record)
        confirmation = isinstance(body, dict) and body.get('confirmed') is True
        home_lock = exclusive_lock(cli.home, 'cli-maintenance') if confirmation else nullcontext()
        with self.accounts.gate, cli.start_lock, home_lock:
            recovery = self._cli_recovery_record()
            maintenance = recovery.get('purpose') == 'recovery'
            waiting = maintenance and recovery.get('phase') == 'waiting'
            if maintenance and (recovery['host'] != self.host or recovery['home'] != str(cli.home.resolve())):
                raise ValueError('CLI 恢复记录不属于此配置，未退出服务')
            if (_waiting is None and maintenance and body == recovery['confirmation']
                    and thread_id == recovery['threadId']):
                with self.cli_maintenance.lock:
                    recovery = self._cli_recovery_record()
                    if recovery['phase'] == 'waiting':
                        if self.closed.is_set() or not self.observation_enabled():
                            raise ValueError('CLI 接入已停用或网关正在关闭')
                        self._start_cli_maintenance(recovery)
                    return self.cli_maintenance.status(recovery)
            if waiting and _waiting is None:
                if body is not None:
                    raise ValueError('此配置已有等待中的 CLI 恢复，请先查看该请求')
                return self.cli_maintenance.status(recovery)
            if _waiting is not None and (not waiting or recovery['operationId'] != _waiting
                    or recovery['threadId'] != thread_id or recovery['confirmation'] != body
                    or self.released_cli.contains(thread_id) != recovery['wasReleased']
                    or self.closed.is_set() or not self.observation_enabled()):
                raise ValueError('等待期间 CLI 恢复请求或配置已变化，未退出服务')
            executable = _managed_cli(cli.home)
            if executable is None:
                raise ValueError('无法核对当前 CLI 服务')
            if recovery.get('phase') == 'released':
                return {'canRecover': False, 'message': '此配置已正常释放；请继续原会话以核验并重新连接。'}
            if recovery.get('phase') == 'resuming':
                target = recovery['providerTarget']
                message = '已确认的接入恢复结果待核对；不会重复 resume 或退出共享服务。'
                if target['before']['id'] == thread_id and not automatic:
                    identity = read_daemon_environment(cli.home, executable, ())[1]
                    if (hashlib.sha256(json.dumps(identity).encode()).hexdigest() != recovery.get('resumedGeneration')
                            or configuration_stamp(cli.home) != recovery['configuration']):
                        return {'canRecover': False, 'message': '原生服务或目标配置已变化，原接入恢复结果仍待核对；不会重复 resume。'}
                    with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                                client_name='codex-mobile-bridge-target-check')) as probe:
                        if Path(probe.connect()['codexHome']).resolve() != cli.home.resolve():
                            raise ValueError('原生服务不属于此配置')
                        thread = probe.request('thread/read', {'threadId': thread_id, 'includeTurns': False}, replay_safe=True)['thread']
                        config = probe.request('config/read', {'includeLayers': False}, replay_safe=True)['config']
                        self._verify_cli_target(probe, config, target, cli, executable)
                    if (read_daemon_environment(cli.home, executable, ())[1] != identity
                            or configuration_stamp(cli.home) != recovery['configuration']):
                        raise ValueError('原生服务已变化，原恢复结果仍待核对；不会重复 resume。')
                    from bridge.features.sessions.identity import same_cwd
                    if thread.get('modelProvider') not in {
                            target['provider'], target['before']['modelProvider'], target.get('historicalProvider')}:
                        raise ValueError('原会话历史提供商身份已变化，恢复结果仍待核对')
                    persisted = cli.persisted_identity(thread)
                    applied = (target['provider'] != target['before']['modelProvider'] or
                               recovery.get('resumeWasCold') and thread.get('status', {}).get('type') in ('idle', 'active'))
                    if (applied and thread.get('id') == thread_id and persisted['modelProvider'] == target['provider']
                            and same_cwd(thread.get('cwd'), target['before']['cwd'], os.name == 'nt')):
                        recovery.update(phase='complete', providerPhase='checkedUnknown', resumeReceipt='unknown')
                        private_json(self.data_dir / 'cli-daemon-recovery.json', recovery)
                        message = '当前线程状态和目标配置已核验，原 resume 回执仍未知；不会重放。若连接订阅待核对，请先从 CLI 面板核验或释放。'
                return {'canRecover': False, 'requiresConfirmation': True,
                        'maintenancePending': automatic and target['before']['id'] == thread_id,
                        'message': message}
            if recovery.get('phase') == 'configuringUnknown':
                message = '系统代理跟随设置的写入结果待核对；不会重放设置或退出 CLI 服务。'
                if not automatic:
                    with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                                client_name='codex-mobile-bridge-network-check')) as probe:
                        probe.connect()
                        snapshot = probe.request('config/read', {'includeLayers': True}, replay_safe=True)
                        layer = system_proxy_user_layer(snapshot, cli.home)
                        if (layer['config'].get('features') or {}).get('respect_system_proxy') is True:
                            recovery['phase'] = 'complete'
                            private_json(self.data_dir / 'cli-daemon-recovery.json', recovery)
                            message = '系统代理跟随设置已确认保存，CLI 服务尚未恢复；请重新检查网络并确认恢复。'
                return {'canRecover': False,
                        'requiresConfirmation': True, 'message': message}
            if recovery.get('phase') in ('draining', 'stopped', 'starting'):
                pending_message = '此前 CLI 服务正在排空或等待接续，请检查 CLI 网络；不会重复请求退出'
                if automatic:
                    return {'canRecover': False, 'requiresConfirmation': True,
                            'maintenancePending': True, 'message': pending_message}
                resume_stamp = hashlib.sha256(json.dumps(recovery, sort_keys=True).encode()).hexdigest()
                if body is not None:
                    if body != {'confirmed': True, 'generation': resume_stamp}:
                        raise ValueError('恢复状态已变化，请重新检查')
                    if self._resume_cli_recovery(cli, executable, recovery):
                        return {'recovered': True, 'connected': False}
                else:
                    status = cli.daemon_status()
                    if status == 'running':
                        try:
                            self._resume_cli_recovery(cli, executable, recovery, check_only=True)
                        except (ValueError, OSError, AppServerError) as error:
                            return {'canRecover': False, 'message': str(error)}
                        if recovery['phase'] == 'complete':
                            return {'canRecover': False, 'recovered': True,
                                    'message': 'CLI 服务恢复已核验，请继续原会话；检查未启动会话或重放操作。'}
                    can_resume = (status == 'stopped' and recovery['phase'] != 'starting'
                                  and bool(cli.recovery_environment))
                    return {'canRecover': can_resume, 'generation': resume_stamp, 'message':
                            '原 CLI 已退出。请确认恢复期间其他客户端不再发起任务，再恢复此共享服务。' if can_resume else pending_message}
            if _follow_account:
                generation = read_daemon_environment(cli.home, executable, ())[1]
                updates, stamp = {}, 'account'
            else:
                generation, updates, stamp = inspect_proxy(cli.home, executable)
            configuration = configuration_stamp(cli.home)
            generation_key = hashlib.sha256(json.dumps(generation).encode()).hexdigest()
            if _waiting is not None and (recovery['generation'] != generation_key or recovery['configuration'] != configuration):
                raise ValueError('等待期间原生服务或配置已变化，未退出服务')
            follow_system_proxy = bool(updates) and sys.platform == 'darwin'
            baseline = cli.configuration_stamp
            if _follow_account and cli.configuration_generation not in (None, generation_key):
                baseline = None
            configuration_changed = (configuration != baseline
                if baseline is not None else configuration_after_start(cli.home, generation))
            plan, target, original, target_thread = None, None, None, None
            if not automatic or _follow_account:
                try:
                    with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                                client_name='codex-mobile-bridge-target-check')) as probe:
                        runtime_info = probe.connect()
                        if Path(runtime_info['codexHome']).resolve() != cli.home.resolve():
                            raise ValueError('原生服务不属于此配置，未恢复服务')
                        before = probe.request('thread/read', {'threadId': thread_id, 'includeTurns': False}, replay_safe=True)['thread']
                        snapshot = probe.request('config/read', {'includeLayers': True, 'cwd': before.get('cwd')}, replay_safe=True)
                        config = snapshot['config']
                    if (not isinstance(config, dict) or before.get('id') != thread_id
                            or not before.get('cwd') or not before.get('modelProvider')):
                        raise ValueError('无法核验原会话身份，未恢复服务')
                    historical_provider = before['modelProvider']
                    target_thread = cli.persisted_identity(before)
                except (AppServerError, ValueError, OSError, KeyError, TypeError) as error:
                    if _follow_account and configuration_changed:
                        raise IPCError('CLI 接入已变化，但无法核验当前原生配置和会话；操作未发送。') from error
                    raise
                if _waiting is not None and target_thread != recovery['targetThread']:
                    raise ValueError('恢复目标的会话身份已变化，未恢复服务')
                before = {**before, **target_thread}
                names = {*AUTH_ENV_NAMES, 'CODEX_SQLITE_HOME', 'NO_PROXY', 'no_proxy'}
                if not updates:
                    from bridge.clients.codex.daemon_network import PROXY_NAMES
                    names.update(PROXY_NAMES)
                for provider in {*((config.get('model_providers') or {}).keys()), before['modelProvider'], config.get('model_provider') or 'openai'}:
                    names.update(cli._required_model_environment(config, provider))
                try:
                    original, identity = read_daemon_environment(cli.home, executable, names)
                    if identity != generation:
                        raise ValueError('原生服务已变化，请重新检查')
                except (ValueError, OSError) as error:
                    if _follow_account and configuration_changed:
                        raise IPCError('CLI 接入已变化，但无法核验同代原生服务；操作未发送。') from error
                    raise
                sync = LocalCliSync(self)
                try:
                    target = sync.recovery_target(config)
                    if target is None:
                        target = native_recovery_target(cli.home, config, before['modelProvider'], original, snapshot.get('layers') or [])
                except (ValueError, OSError) as error:
                    if _follow_account and configuration_changed:
                        raise IPCError('当前 CLI 接入变化尚未核验；本次操作未发送。' + str(error)) from error
                    raise
                provider = before['modelProvider'] if before['modelProvider'] in target['aliases'] else target['provider']
                if daemon_version(runtime_info) == '0.167.0' and target['provider'] != before['modelProvider']:
                    error = CliProviderMismatch(before['modelProvider'], target['provider'])
                    if _follow_account or body is not None:
                        raise error
                    return {'canRecover': False, **error.details, 'message': str(error)}
                if _follow_account and configuration_changed and provider == before['modelProvider']:
                    try:
                        route_changed = (configuration['config.toml'] != baseline['config.toml']
                            if baseline is not None else configuration_after_start(cli.home, generation, files=('config.toml',)))
                        if not route_changed and not target.get('unknownAuthSource'):
                            with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                                        client_name='codex-mobile-bridge-auth-check')) as probe:
                                if Path(probe.connect()['codexHome']).resolve() != cli.home.resolve():
                                    raise ValueError('原生服务不属于此配置')
                                matched = sync.current_file_auth_matches(probe, target)
                            if (matched is True or matched is None and not file_auth_provider(config, provider)):
                                if (configuration_stamp(cli.home) != configuration
                                        or read_daemon_environment(cli.home, executable, ())[1] != generation):
                                    raise ValueError('核验账号期间原生配置已变化，未接续会话')
                                cli.configuration_stamp = configuration
                                cli.configuration_generation = generation_key
                                return {'canRecover': False, 'followed': True}
                    except (ValueError, OSError, AppServerError, KeyError, TypeError) as error:
                        # File age is sufficient evidence of drift on a fresh gateway.
                        # A failed native probe must not fall back to sending with the old cache.
                        raise IPCError('无法核验此配置的原生认证缓存；操作未发送。') from error
                if configuration_changed or provider != before['modelProvider']:
                    if target.get('unknownAuthSource'):
                        error = '当前原生认证存储不是已核验的 file 来源，无法确认新账号；请在原工具核验认证来源。'
                        raise (IPCError if _follow_account else ValueError)(error)
                    if (_follow_account and not target.get('accountId') and file_auth_provider(config, provider)
                            and (target.get('kind') is None or any(original.get(name) for name in ('CODEX_API_KEY', 'CODEX_ACCESS_TOKEN')))):
                        raise IPCError('此 CLI 的文件认证或原生注入来源尚未核验，请先在原客户端同步；本次操作未发送。')
                    if (_follow_account and target.get('kind') in ('api', 'chatgpt')
                            and sync.file_auth_identity(target['kind']) is None):
                        raise IPCError('此配置的原生认证文件尚未核验；本次操作未发送。')
                    plan = {key: target.get(key) for key in ('accountId', 'identity', 'external', 'kind')}
                    plan.update(provider=provider, before=target_thread)
                    plan['historicalProvider'] = historical_provider
                    follow_system_proxy = False  # Adopting native auth/config never rewrites those files.
            preview_salt = (recovery['previewSalt'] if _waiting is not None else recovery['operationId']
                            if maintenance and recovery['phase'] in ('cancelled', 'failed') else None)
            stamp = hashlib.sha256(json.dumps([stamp, generation_key, configuration, follow_system_proxy, target_thread, plan,
                target.get('identity') if target else None, preview_salt], sort_keys=True).encode()).hexdigest()
            if not updates and not configuration_changed and plan is None:
                if automatic:
                    cli.configuration_stamp = configuration
                    cli.configuration_generation = generation_key
                if _follow_account and _waiting is not None:
                    return {'followed': True}
                if body is not None:
                    raise ValueError('网络状态已变化，请重新检查')
                return {'canRecover': False, 'message': '未检测到可恢复的失效本机代理'}
            message = ('原 CLI 使用的本机代理已失效，当前系统代理可用。' if updates else
                       'CLI 认证或配置已变化，需要核对共享服务后恢复。')
            if plan:
                message += ('按此 home 当前原生配置继续本会话；不修改其 auth/config。' if plan.get('external') else
                            '使用此 home 当前已选择并核验的 CLI 接入继续本会话。')
                if plan['provider'] != plan['before']['modelProvider']:
                    message += ('本次只将此会话从 ' + plan['before']['modelProvider'] + ' 改为 ' + plan['provider'] +
                                '，保留会话 ID、历史和工作目录；之后的历史会由目标接入处理。')
            if follow_system_proxy:
                message += ('恢复时会在此 CODEX_HOME 的 CLI 用户配置中保存 features.respect_system_proxy=true，'
                            '使使用此配置的其他客户端今后加载的会话也跟随系统代理；不修改系统代理。')
            if _follow_account and body is None:
                if not graceful_restart_supported(runtime_info):
                    raise IPCError('当前 CLI 版本的正常重建能力尚未核验；请在原客户端同步账号。')
                confirmation = {'confirmed': True, 'generation': stamp}
                return self._queue_cli_maintenance(target_thread, confirmation, generation_key, configuration,
                    preview_salt, '正在等待此配置的任务安全结束，以同步当前原生 CLI 接入。', follow_account=True)
            if automatic and not _follow_account:
                return {'canRecover': False, 'requiresConfirmation': True,
                        'message': message + '请从会话菜单检查 CLI 网络并确认恢复；不会自动退出共享服务。'}
            if body is not None and body != {'confirmed': True, 'generation': stamp}:
                raise ValueError('恢复目标或网络状态已变化，请重新检查')
            try:
                if sys.platform not in ('darwin', 'linux'):
                    raise ValueError('此平台请在电脑上正常重启 CLI 服务')
                live = self.live.get(thread_id)
                if live and live.desktop_owner_probe == 'found':
                    raise ValueError('当前会话由 Codex App 持有')
                with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                            client_name='codex-mobile-bridge-network-check')) as probe:
                    info = probe.connect()
                    if not graceful_restart_supported(info):
                        raise ValueError('当前 CLI 版本尚未核验安全排空能力，请在电脑端正常重启后继续')
                    LocalCliSync.check_idle_daemon(probe, cli.home)
                    if body is not None or follow_system_proxy:
                        snapshot = probe.request('config/read', {'includeLayers': follow_system_proxy}, replay_safe=True)
                        config = snapshot['config']
                        if follow_system_proxy:
                            layer = system_proxy_user_layer(snapshot, cli.home)
                if (cli.attaching or any(cli.requests.values()) or
                        any(row.get('cleanupOnly') for row in cli.metadata.values()) or
                        any(row.get('status') in ('queued', 'unknown') for row in self.submissions.values())):
                    raise CliTasksPending('请先处理当前配置的待确认或待发送操作')
            except CliTasksPending as error:
                if _waiting is not None:
                    raise
                message += ' 将等待此配置的全部任务安全结束。' + str(error)
                if body is None:
                    return {'canRecover': True, 'willWait': True, 'generation': stamp, 'message': message}
                return self._queue_cli_maintenance(target_thread, body, generation_key, configuration, preview_salt, message)
            except ValueError as error:
                if body is not None:
                    raise
                return {'canRecover': False, 'message': message + str(error)}
            if body is None:
                return {'canRecover': True, 'generation': stamp, 'message': message}
            # Preserve credentials and provider routing from this exact process, even
            # when the gateway was launched with a different shell environment.
            original, identity = read_daemon_environment(cli.home, executable, names)
            if identity != generation:
                raise ValueError('原生服务已变化，请重新检查')
            if configuration_stamp(cli.home) != configuration:
                raise ValueError('CLI 认证或配置正在更新，请稍后继续；未重启服务')
            if self.closed.is_set() or not self.observation_enabled():
                raise ValueError('CLI 接入已停用或网关正在关闭，未退出服务')
            preserved = {name: original.get(name) for name in names}
            if target:
                preserved.update(target['environment'])
            setting_saved = False
            setting_dispatched = False
            receipt = maintenance_receipt(recovery) if _waiting is not None else {}
            def before_setting_write():
                nonlocal setting_dispatched
                if _waiting is not None:
                    self._verify_cli_recovery_thread(probe, cli, target_thread)
                with self.cli_maintenance.lock:
                    self._verify_cli_waiting_dispatch(_waiting)
                    if (read_daemon_environment(cli.home, executable, ())[1] != generation
                            or configuration_stamp(cli.home) != configuration):
                        raise ValueError('原生服务或配置已变化，未保存代理设置')
                    private_json(self.data_dir / 'cli-daemon-recovery.json', {
                        **receipt, 'phase': 'configuringUnknown', 'generation': generation_key})
                    setting_dispatched = True
            try:
                if follow_system_proxy:
                    with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                                client_name='codex-mobile-bridge-network-setting')) as probe:
                        if Path(probe.connect()['codexHome']).resolve() != cli.home.resolve():
                            raise ValueError('原生服务不属于此配置，未保存代理设置')
                        setting_saved = enable_system_proxy_following(probe, cli.home,
                            expected_version=layer['version'], before_write=before_setting_write)
                    if setting_dispatched and _waiting is None:
                        private_json(self.data_dir / 'cli-daemon-recovery.json', recovery)
                    after = configuration_stamp(cli.home)
                    if after['auth.json'] != configuration['auth.json']:
                        raise ValueError('CLI 认证正在更新，请稍后重新检查；未重启服务')
                    configuration = after
                self._drain_cli_daemon(cli, executable, generation, configuration, updates,
                                       preserved, follow_system_proxy=follow_system_proxy, provider_plan=plan,
                                       waiting_operation=_waiting, setting_verified=setting_saved)
                if self.closed.is_set() or not self.observation_enabled():
                    raise ValueError('CLI 已正常退出；接入已停用或关闭，未重新启动服务')
                self._resume_cli_recovery(cli, executable, self._cli_recovery_record())
            except Exception as error:
                if (isinstance(error, ReleaseNotSent) and isinstance(error.__cause__, CliTasksPending)
                        and not setting_dispatched and not setting_saved):
                    if _waiting is not None:
                        raise error.__cause__
                    message += ' 将等待此配置的全部任务安全结束。' + str(error)
                    return self._queue_cli_maintenance(target_thread, body, generation_key, configuration, preview_salt, message)
                if getattr(error, 'system_proxy_unknown', False):
                    private_json(self.data_dir / 'cli-daemon-recovery.json', {
                        **receipt, 'phase': 'configuringUnknown', 'generation': generation_key})
                    raise ValueError('系统代理跟随设置的写入结果待核对；未请求退出服务，不会自动重放设置。') from error
                if setting_saved or getattr(error, 'system_proxy_saved', False):
                    raise ValueError('系统代理跟随设置已保存，CLI 服务尚未恢复：' + str(error)) from error
                if setting_dispatched:
                    private_json(self.data_dir / 'cli-daemon-recovery.json', recovery)
                raise
        if self.closed.is_set() or not self.observation_enabled():
            raise ValueError('接入已停用或关闭，未重新连接会话')
        if plan:
            session = self._activate_cli_recovery(thread_id, plan)
        elif _waiting is not None:
            session = self.activate(thread_id, _expected_cli=target_thread)
        else:
            session = self.activate(thread_id)
        return {'connected': session.connected}

    def _queue_cli_maintenance(self, target_thread, body, generation, configuration, preview_salt, message, *, follow_account=False):
        if self.closed.is_set() or not self.observation_enabled():
            raise ValueError('CLI 接入已停用或网关正在关闭')
        record = {'purpose': 'recovery', 'phase': 'waiting', 'operationId': str(uuid.uuid4()),
                  'threadId': target_thread['id'], 'targetThread': target_thread,
                  'host': self.host, 'home': str(self.cli_sessions.home.resolve()),
                  'confirmation': body, 'generation': generation, 'configuration': configuration,
                  'previewSalt': preview_salt, 'message': message,
                  'wasReleased': self.released_cli.contains(target_thread['id']), 'followAccount': follow_account}
        with self.cli_maintenance.lock:
            private_json(self.data_dir/'cli-daemon-recovery.json', record)
            self._start_cli_maintenance(record)
            return self.cli_maintenance.status(record)

    def _verify_cli_waiting_dispatch(self, operation_id, *, setting_verified=False):
        if operation_id is None:
            return
        current = self._cli_recovery_record()
        phase = 'configuringUnknown' if setting_verified else 'waiting'
        if (current.get('operationId') != operation_id or current.get('phase') != phase
                or self.closed.is_set() or not self.observation_enabled()):
            raise ValueError('恢复已取消或接入已停用，未发起下一步操作')

    @staticmethod
    def _verify_cli_recovery_thread(probe, cli, target):
        thread = probe.request('thread/read', {'threadId': target['id'], 'includeTurns': False}, replay_safe=True)['thread']
        if cli.persisted_identity(thread) != target:
            raise ValueError('恢复目标的会话身份已变化，未派发维护操作')

    def _start_cli_maintenance(self, record):
        identifier, thread = record['operationId'], record['threadId']
        def finish(result, error):
            with self.accounts.gate, self.cli_sessions.start_lock, self.cli_maintenance.lock:
                current = self._cli_recovery_record()
                if (current.get('operationId') != identifier or current.get('phase') == 'cancelled'
                        or self.closed.is_set()):
                    return
                if error is not None:
                    if current['phase'] == 'waiting':
                        current['phase'] = 'failed'
                    current['maintenanceError'] = (str(error) if isinstance(error, (ValueError, IPCError))
                                                   else 'CLI 恢复未能完成，请重新检查；不会自动重放操作。')
                else:
                    current['maintenanceResult'] = result
                private_json(self.data_dir/'cli-daemon-recovery.json', current)
        self.cli_maintenance.start(identifier,
            lambda: self._attempt_cli_maintenance(thread, record['confirmation'], identifier, record.get('followAccount', False)), finish)

    def _attempt_cli_maintenance(self, thread, confirmation, identifier, follow_account):
        with self.entry_gate if self.entry_gate is not None else nullcontext(), self.accounts.gate:
            self.accounts.check_ready()
            result = self.cli_network(thread, confirmation, _waiting=identifier, _follow_account=follow_account)
            if not follow_account or not result.get('followed'):
                return result
            with self.cli_maintenance.lock:
                self._verify_cli_waiting_dispatch(identifier)
                record = self._cli_recovery_record()
                record['phase'] = 'complete'
                private_json(self.data_dir/'cli-daemon-recovery.json', record)
            return {'connected': self.activate(thread, _expected_cli=record['targetThread']).connected}

    def _completed_cli_release(self, thread_id, record):
        """Recognize a completed release, never an unfinished network recovery."""
        if record.get('purpose') == 'release':
            try:
                uuid.UUID(record['operationId'])
                identity = record['releasedThread']
                return (record.get('phase') == 'released' and bool(identity.get('id'))
                        and bool(identity.get('cwd')) and bool(identity.get('modelProvider'))
                        and record['releaseKey'] in record['connections'])
            except (ValueError, KeyError, TypeError):
                return False
        # Exact legacy .8 single-connection format. A natural thread unload has
        # a different receipt and cannot prove this shared daemon was stopped.
        if (set(record) != {'generation', 'phase', 'configuration', 'connections'} or
                record['phase'] != 'stopped' or len(record['connections']) != 1):
            return False
        directory = self.data_dir
        if self.host.startswith('codex:') and directory.parent.name == 'codex-entries':
            directory = directory.parent.parent
        try:
            jobs = json.loads((directory/'cli-release-operations.json').read_text())
            for identifier, job in jobs.items():
                uuid.UUID(identifier)
                if (job.get('id') != identifier or job.get('mode') != 'unload' or
                        job.get('connections') != record['connections'] or len(job.get('results', [])) != 1):
                    continue
                row = job['results'][0]
                if (row.get('key') == record['connections'][0] and row.get('sessionId')
                        and row.get('host') == self.host and row.get('provider') == 'codex'
                        and row.get('status') == 'released'
                        and row.get('notice') == '已正常退出此配置的共享 CLI 服务，原生占用已释放'):
                    saved = self.cli_sessions.store.get(row['sessionId'])
                    record.update(purpose='release', phase='released', operationId=identifier,
                        releaseKey=row['key'], releasedThread={'id': row['sessionId'], 'cwd': saved['cwd'],
                                                             'modelProvider': saved['model_provider']})
                    private_json(self.data_dir/'cli-daemon-recovery.json', record)
                    return True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return False

    def _continue_released_cli(self, cli, executable, record, thread_id):
        from bridge.clients.codex.daemon_network import configuration_stamp, native_recovery_target, file_auth_provider, AUTH_ENV_NAMES, PROXY_NAMES
        from bridge.features.accounts.cli_sync import LocalCliSync
        from bridge.features.endpoints.registry import load_toml
        configuration = configuration_stamp(cli.home)
        changed = configuration != record['configuration']
        saved = cli.store.get(record['releasedThread']['id'])
        if {'id': saved['id'], 'cwd': saved['cwd'], 'modelProvider': saved['model_provider']} != record['releasedThread']:
            raise ValueError('释放后的原会话身份已变化，请重新核验；未启动 CLI')
        saved = cli.store.get(thread_id)
        status = cli.daemon_status()
        if status == 'stopped' or changed:
            config = load_toml((cli.home/'config.toml').read_text(encoding='utf-8'))
            previous = cli.recovery_environment
            known = previous and previous['generation'] == record['generation']
            values = dict(previous['preserved']) if known else {}
            native = native_recovery_target(cli.home, config, saved['model_provider'], values)
            sync = LocalCliSync(self)
            selected = sync.recovery_target(config)
            target = selected or native
            if changed:
                effective = {**config, **((config.get('profiles') or {}).get(config.get('profile')) or {})}
                if target['provider'] != saved['model_provider']:
                    raise ValueError('释放后的当前提供商与原会话不同，未启动服务')
                if (target.get('unknownAuthSource') or target.get('kind') not in ('api', 'chatgpt')
                        or not file_auth_provider(effective, target['provider'])
                        or sync.file_auth_identity(target['kind']) is None):
                    raise ValueError('释放后的当前 CLI 配置或认证尚未核验；未沿用旧环境，未启动服务')
                record = {**record, 'releasedConfiguration': record['configuration'], 'configuration': configuration,
                          'releasedAuth': {'provider': target['provider'], 'kind': target['kind'], 'cwd': saved['cwd']}}
                if not known:
                    values = dict.fromkeys((*AUTH_ENV_NAMES, *PROXY_NAMES, 'NO_PROXY', 'no_proxy'))
                values.update(native['environment'])
            elif not known and selected is None:
                raise ValueError('此前 CLI 进程环境已不可核验，请在原客户端核验接入并正常启动；不会借用网关环境')
            if saved['model_provider'] not in target['aliases']:
                raise ValueError('当前所选 CLI 接入与原会话提供商不同，请检查 CLI 网络并明确确认；未迁移会话')
            if selected:
                values.update(native['environment'])
                values.update(selected['environment'])
            if not known:
                values['CODEX_SQLITE_HOME'] = None
            if status == 'stopped':
                cli.recovery_environment = {'generation': record['generation'], 'updates': {}, 'preserved': values}
        self._resume_cli_recovery(cli, executable, record)

    def _recover_cli_environment(self, thread_id, *, defer_start=False, follow_account=False):
        """Keep environment diagnostics advisory; an unfinished drain stays blocked."""
        from bridge.clients.codex.cli_session import CliSessions, _managed_cli, _local_cli
        from bridge.clients.codex.desktop_runtime import exclusive_lock
        from bridge.clients.codex.daemon_network import configuration_stamp
        from bridge.platforms.codex_daemon_env import DaemonEnvironmentUnavailable
        cli = self.cli_sessions
        record = self._cli_recovery_record()
        release_candidate = (isinstance(cli, CliSessions) and cli.alias is None and
                (self.host == 'local' or self.host.startswith('codex:')) and
                record.get('phase') in ('released', 'stopped'))
        if release_candidate:
            try:
                with self.accounts.gate, cli.start_lock:
                    record = self._cli_recovery_record()
                    if self._completed_cli_release(thread_id, record):
                        if defer_start:
                            return False
                        executable = _managed_cli(cli.home)
                        if executable is None and cli.allow_installed_runtime:
                            executable = _local_cli(cli.home, cli.configured_cli)
                        if executable is None:
                            raise ValueError('无法核对已释放的 CLI 运行组件')
                        with exclusive_lock(cli.home, 'cli-maintenance'):
                            self._continue_released_cli(cli, executable, record, thread_id)
                        return
            except (ValueError, OSError, KeyError, AppServerError) as error:
                raise IPCError('已释放的 CLI 尚未重新连接：' + str(error)) from error
        if record.get('phase') == 'released':
            raise IPCError('已释放的 CLI 回执尚未匹配当前会话，请先检查原释放结果；未启动服务')
        pending_phases = ('waiting', 'draining', 'stopped', 'starting')
        pending = (record.get('phase') in pending_phases or
                   record.get('phase') == 'resuming' and record['providerTarget']['before']['id'] == thread_id)
        if (not isinstance(cli, CliSessions) or cli.alias is not None or
                not (self.host == 'local' or self.host.startswith('codex:'))):
            return
        if _managed_cli(cli.home) is None:
            if pending or record.get('phase') in ('resuming', 'configuringUnknown'):
                raise IPCError('CLI 服务恢复结果尚未核对且运行组件不可用；操作未发送，不会跳过待核验回执。')
            return
        if not Path(cli.client.socket_path).exists() and not pending:
            return
        follow_account = (follow_account and sys.platform in ('darwin', 'linux')
                          and self.store.get(thread_id).get('origin') == 'cli')
        known_drift = (follow_account and cli.configuration_stamp is not None
                       and configuration_stamp(cli.home) != cli.configuration_stamp)
        if not pending and not follow_account:
            self._verify_cli_file_auth(thread_id)
        try:
            result = (self.cli_network(thread_id, automatic=True, _follow_account=True) if follow_account
                      else self.cli_network(thread_id, automatic=True))
            pending = pending or bool(result.get('maintenancePending'))
            if pending and result.get('requiresConfirmation'):
                if result.get('operationId'):
                    error = IPCError(result['message'] + '；本次操作未发送。')
                    error.details = {'reason': 'cliMaintenancePending', 'operationId': result['operationId'],
                                     'threadId': result['threadId']}
                    raise error
                raise ValueError(result['message'])
            if follow_account and not pending:
                self._verify_cli_file_auth(thread_id)
        except DaemonEnvironmentUnavailable as error:
            # Native identity could not be established. Keep the original
            # runtime; native admission still protects every following request.
            if pending or self._cli_recovery_record().get('phase') in pending_phases:
                raise IPCError('CLI 服务恢复结果尚未核对，请先检查 CLI 网络；操作未发送。') from error
            if known_drift:
                raise IPCError('CLI 接入已变化，但无法核验原生服务；操作未发送。') from error
            return
        except (ValueError, OSError, AppServerError) as error:
            if pending or self._cli_recovery_record().get('phase') in pending_phases:
                raise IPCError('CLI 连接恢复暂受阻：' + str(error) + '；原任务和未确认操作保持不变。') from error
            if known_drift:
                raise IPCError('CLI 接入已变化，尚未核验；操作未发送。') from error
            # A changed credential file or an unreachable inherited proxy does
            # not prove this thread needs it (NO_PROXY, local providers, pooled
            # connections and native token refresh can still work).

    def _verify_cli_file_auth(self, thread_id):
        from contextlib import closing
        from bridge.clients.codex.app_server import CodexAppServer
        from bridge.clients.codex.cli_session import CliSessions, _managed_cli
        from bridge.clients.codex.daemon_network import verify_file_auth
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        cli = self.cli_sessions
        if not isinstance(cli, CliSessions) or cli.alias is not None:
            return
        with cli.lock:
            binding = ((cli.metadata.get(thread_id) or {}).get('fileAuthGeneration')
                       if cli.client.process is not None and cli.joined.get(thread_id) is cli.client.process else None)
        if not binding:
            return
        executable = _managed_cli(cli.home)
        if executable is None or not Path(cli.client.socket_path).exists():
            return
        try:
            generation = read_daemon_environment(cli.home, executable, ())[1]
            if hashlib.sha256(json.dumps(generation).encode()).hexdigest() != binding:
                return
            with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                        client_name='codex-mobile-bridge-auth-check')) as probe:
                if Path(probe.connect()['codexHome']).resolve() != cli.home.resolve():
                    return
                thread = probe.request('thread/read', {'threadId': thread_id, 'includeTurns': False}, replay_safe=True)['thread']
                if thread.get('id') != thread_id or not thread.get('modelProvider'):
                    return
                config = probe.request('config/read', {'includeLayers': False}, replay_safe=True)['config']
                mismatch = None
                try:
                    verify_file_auth(probe, cli.home, config, thread['modelProvider'], known_factory=True)
                except IPCError as error:
                    mismatch = error
            if mismatch and read_daemon_environment(cli.home, executable, ())[1] == generation:
                raise mismatch
        except (AppServerError, OSError, ValueError):
            # Inspection failure cannot prove the cached credential is wrong.
            return

    def _cli_recovery_record(self):
        path = self.data_dir / 'cli-daemon-recovery.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def _activate_cli_recovery(self, thread_id, plan):
        from bridge.clients.codex.cli_session import _managed_cli
        from bridge.clients.codex.daemon_network import configuration_stamp
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        cli = self.cli_sessions
        record = self._cli_recovery_record()
        if (record.get('phase') != 'complete' or record.get('providerPhase') != 'authorized'
                or record.get('providerTarget') != plan or plan['before']['id'] != thread_id
                or configuration_stamp(cli.home) != record['configuration']):
            raise IPCError('已确认的目标接入或会话已变化，请重新检查；未迁移会话。')
        generation = read_daemon_environment(cli.home, _managed_cli(cli.home), ())[1]
        if hashlib.sha256(json.dumps(generation).encode()).hexdigest() != record.get('resumedGeneration'):
            raise IPCError('原生服务已变化，请重新检查并确认；未迁移会话。')
        dispatched = False
        def before_resume():
            nonlocal dispatched
            current = read_daemon_environment(cli.home, _managed_cli(cli.home), ())[1]
            if (hashlib.sha256(json.dumps(current).encode()).hexdigest() != record.get('resumedGeneration')
                    or configuration_stamp(cli.home) != record['configuration']):
                raise IPCError('原生服务或已确认目标已变化；未迁移会话。')
            record['phase'] = 'resuming'
            record['resumeWasCold'] = True  # CliSessions checked notLoaded before invoking this callback.
            private_json(self.data_dir / 'cli-daemon-recovery.json', record)
            dispatched = True
        try:
            session = self.live.get(thread_id)
            with cli.lock:
                joined = cli.joined.get(thread_id) is not None and cli.joined.get(thread_id) is cli.client.process
            if session and session.runtime == 'cli' and not joined:
                with session.condition:
                    session.connected = False
                    session.changed()
            session = self.activate(thread_id, _cli_recovery=(plan, before_resume))
            if not dispatched or session.runtime != 'cli':
                raise IPCError('已确认的原生接续尚未执行，请重新核对连接；未确认目标接入生效。')
        except Exception as error:
            native = error
            while getattr(native, '__cause__', None) is not None:
                native = native.__cause__
            if dispatched and getattr(native, 'rpc_error', None) is not None:
                record.update(phase='complete', providerPhase='failed')
                private_json(self.data_dir / 'cli-daemon-recovery.json', record)
            raise
        record.update(phase='complete', providerPhase='complete')
        private_json(self.data_dir / 'cli-daemon-recovery.json', record)
        if plan.get('kind') == 'api':
            with cli.lock:
                metadata = cli.metadata.get(thread_id)
                if (metadata and metadata.get('modelProvider') == plan['provider'] and not metadata.get('cleanupOnly')
                        and cli.client.process is not None and cli.joined.get(thread_id) is cli.client.process):
                    metadata['fileAuthGeneration'] = record['resumedGeneration']
        return session

    def _verify_cli_target(self, probe, config, plan, cli, executable):
        from bridge.features.accounts.cli_sync import LocalCliSync
        from bridge.clients.codex.daemon_network import native_recovery_target, AUTH_ENV_NAMES
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        snapshot = probe.request('config/read', {'includeLayers': True, 'cwd': plan['before']['cwd']}, replay_safe=True)
        config = snapshot['config']
        sync = LocalCliSync(self)
        target = sync.recovery_target(config)
        if target is None:
            names = set(AUTH_ENV_NAMES) | cli._required_model_environment(config, plan['provider'])
            environment, _ = read_daemon_environment(cli.home, executable, names)
            target = native_recovery_target(cli.home, config, plan['before']['modelProvider'], environment, snapshot.get('layers') or [])
        if target['identity'] != plan['identity'] or plan['provider'] not in target['aliases']:
            raise ValueError('目标接入已变化，未迁移会话；请重新检查并确认')
        if target.get('accountId'):
            sync.verify_runtime_auth(probe, target)
        elif sync.current_file_auth_matches(probe, target) is False:
            raise ValueError('新服务尚未使用已核验的原生认证，未接续会话')

    def _drain_cli_daemon(self, cli, executable, generation, configuration, updates, preserved, connections=(), *,
                          follow_system_proxy=False, provider_plan=None, release_operation=None,
                          waiting_operation=None, setting_verified=False):
        from contextlib import closing
        from bridge.clients.codex.app_server import CodexAppServer
        from bridge.clients.codex.daemon_network import graceful_restart_supported
        from bridge.clients.codex.release import ReleaseNotSent
        from bridge.features.accounts.cli_sync import LocalCliSync
        from bridge.platforms.codex_daemon_env import stop_daemon_gracefully
        key = hashlib.sha256(json.dumps(generation).encode()).hexdigest()
        previous = self._cli_recovery_record()
        if previous.get('phase') == 'waiting' and release_operation:
            raise ReleaseNotSent('此配置已有等待中的 CLI 恢复，请先取消恢复等待')
        own_setting = (waiting_operation is not None and setting_verified
                       and previous.get('operationId') == waiting_operation and previous.get('phase') == 'configuringUnknown')
        if previous.get('phase') == 'resuming' or (not own_setting and previous.get('generation') == key
                and previous.get('phase') in ('configuringUnknown', 'draining', 'starting')):
            raise ValueError('原 CLI 正在排空或退出结果待核对，不会重复发送退出信号')
        with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                    client_name='codex-mobile-bridge-final-idle-check')) as probe:
            try:
                if not graceful_restart_supported(probe.connect()):
                    raise ValueError('当前 CLI 版本尚未核验安全排空能力，请在电脑端正常重启')
                LocalCliSync.check_idle_daemon(probe, cli.home)
                if waiting_operation is not None:
                    self._verify_cli_recovery_thread(probe, cli, previous['targetThread'])
            except Exception as error:
                raise ReleaseNotSent(error) from error
            from bridge.clients.codex.daemon_network import configuration_stamp
            if configuration_stamp(cli.home) != configuration or self.closed.is_set() or not self.observation_enabled():
                raise ReleaseNotSent('配置正在变化或接入已关闭，未退出服务')
            receipt = maintenance_receipt(previous) if waiting_operation is not None else {}
            record = {**receipt, 'generation': key, 'phase': 'draining', 'configuration': configuration,
                      'connections': sorted(set(connections) |
                          {row['connectionToken'] for row in cli.metadata.values() if row.get('connectionToken')} |
                          {token for token, process in cli.release_receipts.items() if process is cli.client.process})}
            if follow_system_proxy:
                record['followSystemProxy'] = True
            if provider_plan:
                record['providerTarget'] = provider_plan
                record['providerPhase'] = 'authorized'
            if release_operation:
                operation_id, identity, release_key = release_operation
                record.update(purpose='release', operationId=operation_id, releasedThread=identity, releaseKey=release_key)
            with self.cli_maintenance.lock:
                self._verify_cli_waiting_dispatch(waiting_operation, setting_verified=own_setting)
                cli.recovery_environment = {'generation': key, 'updates': updates, 'preserved': preserved}
                private_json(self.data_dir / 'cli-daemon-recovery.json', record)
            stop_daemon_gracefully(cli.home, executable, generation, graceful_only=True)
        cli.close()
        if cli.daemon_status() != 'stopped':
            raise ValueError('原 CLI 退出仍待核对，不会重复发送退出信号')
        cli.drained_connections.update(record['connections'])
        record['phase'] = 'released' if record.get('purpose') == 'release' else 'stopped'
        private_json(self.data_dir / 'cli-daemon-recovery.json', record)

    def _resume_cli_recovery(self, cli, executable, record, *, check_only=False):
        from contextlib import closing
        from bridge.clients.codex.app_server import CodexAppServer
        from bridge.clients.codex.cli_session import _managed_cli
        from bridge.clients.codex.daemon_network import configuration_stamp, file_auth_provider
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        def verify_proxy_setting():
            released_auth = record.get('releasedAuth')
            if record.get('followSystemProxy') or record.get('providerTarget') or released_auth:
                with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                            client_name='codex-mobile-bridge-network-verify')) as probe:
                    info = probe.connect()
                    if Path(info['codexHome']).resolve() != cli.home.resolve():
                        raise ValueError('CLI 服务不属于此配置，恢复结果待核对')
                    params = {'includeLayers': False}
                    if released_auth:
                        params['cwd'] = released_auth['cwd']
                    config = probe.request('config/read', params, replay_safe=True)['config']
                    if record.get('followSystemProxy') and (config.get('features') or {}).get('respect_system_proxy') is not True:
                        raise ValueError('新 CLI 服务尚未启用系统代理跟随，请核对配置；不会再次退出服务')
                    plan = record.get('providerTarget')
                    if plan:
                        self._verify_cli_target(probe, config, plan, cli, executable)
                    if released_auth:
                        from bridge.features.accounts.cli_sync import LocalCliSync
                        provider = released_auth['provider']
                        if ((config.get('model_provider') or 'openai') != provider or not file_auth_provider(config, provider)
                                or LocalCliSync(self).current_file_auth_matches(probe, released_auth) is not True
                                or configuration_stamp(cli.home) != record['configuration']):
                            raise ValueError('释放后的原生服务未确认当前认证；未接续会话')
        status = cli.daemon_status()
        if status == 'running':
            generation = read_daemon_environment(cli.home, executable, ())[1]
            if hashlib.sha256(json.dumps(generation).encode()).hexdigest() == record['generation']:
                raise ValueError('原 CLI 仍在排空任务，请等待完成；不会重复发送退出信号')
            verify_proxy_setting()
            record['resumedGeneration'] = hashlib.sha256(json.dumps(generation).encode()).hexdigest()
            if record.get('releasedAuth') or record.get('providerTarget'):
                cli.configuration_stamp = record['configuration']
                cli.configuration_generation = record['resumedGeneration']
            record['phase'] = 'complete'
            private_json(self.data_dir / 'cli-daemon-recovery.json', record)
            return False
        if check_only:
            return False
        if status != 'stopped' or record['phase'] == 'starting':
            raise ValueError('CLI 服务启停结果待核对，请在电脑端检查；不会重放操作')
        environment = cli.recovery_environment
        if not environment or environment['generation'] != record['generation']:
            raise ValueError('此前服务环境已无法重新核验，请在电脑端正常启动此配置后继续')
        if configuration_stamp(cli.home) != record['configuration']:
            raise ValueError('退出期间认证或配置已变化，请在电脑端正常启动此配置后继续')
        cli.drained_connections.update(record['connections'])
        record['phase'] = 'starting'
        private_json(self.data_dir / 'cli-daemon-recovery.json', record)
        cli._start_daemon(environment['updates'], environment['preserved'])
        cli.daemon_start_generation += 1
        executable = _managed_cli(cli.home) or executable
        verify_proxy_setting()
        record['resumedGeneration'] = hashlib.sha256(json.dumps(
            read_daemon_environment(cli.home, executable, ())[1]).encode()).hexdigest()
        cli.configuration_stamp = record['configuration']
        cli.configuration_generation = record['resumedGeneration']
        record['phase'] = 'complete'
        private_json(self.data_dir / 'cli-daemon-recovery.json', record)
        return True

    def _cli_state(self, thread_id, state):
        if not isinstance(state, dict) or state.get('id') != thread_id:
            return
        with self.lock:
            session = self.live.get(thread_id)
        if session is None or session.origin not in ('cli', 'ide') or self.closed.is_set():
            return
        with session.condition:
            if (session.origin == 'ide' and session.desktop_owner_probe == 'found' or
                    not session.native_attaching and not (session.runtime == 'cli' and session.connected)):
                return
            newly_connected = not session.connected or session.runtime != 'cli'
            session.state = state
            if state.get('modelProvider'):
                session.saved_provider = state['modelProvider']
            session.runtime = 'cli'
            session.owner = 'cli:' + thread_id
            session.connected = True
            session.discovering = False
            session.error = None
            if newly_connected:
                session.cli_recovery = None
            session.access = self._access(session.origin, session)
            session.changed()
        if newly_connected:
            logging.getLogger(__name__).info("Codex connection transition thread=%s cause=native_attached", thread_id)
        with self.lock:
            self.notification_dirty.add(thread_id)
        self.notification_event.set()

    def _cli_disconnected(self, thread_id=None):
        if not self.closed.is_set():
            logging.getLogger(__name__).info("Codex connection transition thread=%s cause=%s",
                                            thread_id or 'all', 'native_thread_revoked' if thread_id else 'native_proxy_disconnected')
        with self.lock:
            sessions = list(self.live.values())
        for session in sessions:
            if thread_id is None or session.id == thread_id:
                with session.condition:
                    session.native_goal_supported = None
                self._invalidate(session, 'CLI 连接已断开，请重新连接；未自动重发任何操作。', runtime='cli')
        with self.lock:
            observers = list(self.cli_notifications.values())
            self.notification_cursor = 0
            self.notification_boundary.clear()
        for observer in observers:
            if thread_id is None or observer.id == thread_id:
                self.release_notification_session(observer)
        self.notification_event.set()

    def cli_connections(self):
        rows = []
        with self.lock:
            followers = [session for session in self.live.values() if session.origin == 'ide'
                         and session.connected and session.runtime == 'desktop' and session.owner]
        for session in followers:
            owner, generation = session.owner, self.ipc.client_id
            callback = lambda stop, sid=session.id, owner=owner, generation=generation: self._release_ide_follower(sid, owner, generation, stop)
            release = lambda stop, callback=callback, sid=session.id: self._release_cli_connection(sid, callback, stop)
            rows.append({'key': ':'.join([self.host, session.id, owner, generation]), 'sessionId': session.id,
                         'provider': 'codex', 'title': session.view().get('title') or session.id,
                         'host': self.host, 'location': self.host_label + ' · VS Code', '_release': release})
        for row in self.cli_sessions.cli_connections():
            callback, sid = row['_release'], row['sessionId']
            release = lambda stop, callback=callback, sid=sid: self._release_cli_connection(sid, callback, stop)
            callbacks = {}
            if '_check' in row:
                callbacks['_check'] = lambda callback=row['_check'], sid=sid: self._check_cli_connection(sid, callback)
                from bridge.clients.codex.daemon_network import graceful_restart_supported
                if (self.cli_sessions.alias is None and sys.platform in ('darwin', 'linux') and
                        graceful_restart_supported(getattr(self.cli_sessions.client, 'server_info', None) or {})):
                    callbacks['canUnload'] = True
                    callbacks['_unload'] = lambda operation_id, callback=row['_check'], sid=sid, key=row['key']: self._unload_cli_connection(sid, key, callback, operation_id)
            rows.append({**row, 'host': self.host, '_release': release, **callbacks})
        if self.host == 'local':
            with self.lock:
                sources = list(self.remote_bridges.values())
            if self.entries is not None:
                with self.entries.lock:
                    sources += list(self.entries.bridges.values())
            for source in sources:
                rows.extend(source.cli_connections())
        return rows

    def _release_ide_follower(self, sid, owner, generation, stop):
        session = self.live[sid]
        if (not session.connected or session.runtime != 'desktop' or session.owner != owner
                or self.ipc.client_id != generation):
            raise ValueError('原 VS Code 连接已变化，未释放新的连接')
        if session.view().get('status') != 'idle':
            if stop:
                self.interrupt(sid)
            return {'status': 'waiting'}
        self.ipc.follow(sid, owner, False, host=self.native_host)
        return {'status': 'detached', 'notice': '网关连接已释放，可在原 VS Code 会话中继续。'}

    @operation
    def _release_cli_connection(self, sid, callback, stop):
        session = self.session(sid, attach=False)
        with session.activation_lock, session.action_lock, session.attach_lock:
            with self.submit_lock:
                entries = [value for key, value in self.submissions.items() if key.startswith(sid + ':')]
                if any(value.get('status') == 'unknown' for value in entries):
                    raise ValueError('发送结果尚未确认，请先核对；未释放 CLI 连接')
                queued = [value for value in entries if value.get('status') == 'queued']
                if queued and not stop:
                    return {'status': 'waiting'}
                if queued:
                    for value in queued:
                        value['status'] = 'cancelled'
                    self._save_ledger()
            result = callback(stop)
            self._record_cli_release(session, result)
            return result

    @operation
    def _unload_cli_connection(self, sid, key, check, operation_id=None):
        """Explicit maintenance confirmation; never called by reading or reconnecting."""
        from bridge.clients.codex.cli_session import _managed_cli
        from bridge.clients.codex.daemon_network import AUTH_ENV_NAMES, PROXY_NAMES, configuration_stamp
        from bridge.clients.codex.desktop_runtime import exclusive_lock
        from bridge.clients.codex.release import ReleaseNotSent
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        session = self.session(sid, attach=False, background=True)
        cli = self.cli_sessions
        with (session.activation_lock, session.action_lock, session.attach_lock, self.accounts.gate, cli.start_lock,
              exclusive_lock(cli.home, 'cli-maintenance')):
            try:
                verified = check()
                if verified['status'] == 'released':
                    self._record_cli_release(session, verified)
                    return verified
                if (verified['status'] != 'detached' or cli.alias is not None or
                        sys.platform not in ('darwin', 'linux') or cli.attaching or any(cli.requests.values()) or
                        any(row.get('cleanupOnly') for row in cli.metadata.values()) or
                        any(row.get('status') in ('queued', 'unknown') for row in self.submissions.values())):
                    raise ValueError('原生占用尚不能安全释放，请先处理此配置的任务和待确认操作')
                executable = _managed_cli(cli.home)
                if executable is None:
                    raise ValueError('无法核对当前 CLI 服务')
                config = cli.client.request('config/read', {'includeLayers': False}, replay_safe=True)['config']
                before = cli.client.request('thread/read', {'threadId': sid, 'includeTurns': False}, replay_safe=True)['thread']
                identity = cli.persisted_identity(before)
                names = {*AUTH_ENV_NAMES, *PROXY_NAMES, 'CODEX_SQLITE_HOME', 'NO_PROXY', 'no_proxy'}
                for provider in (config.get('model_providers') or {}):
                    names.update(cli._required_model_environment(config, provider))
                original, generation = read_daemon_environment(cli.home, executable, names)
            except Exception as error:
                raise ReleaseNotSent(error) from error
            self._drain_cli_daemon(cli, executable, generation, configuration_stamp(cli.home), {},
                                   {name: original.get(name) for name in names}, connections=(key,),
                                   release_operation=(operation_id or str(uuid.uuid4()), identity, key))
            result = {'status': 'released', 'notice': '已正常退出此配置的共享 CLI 服务，原生占用已释放'}
            self._record_cli_release(session, result)
            return result

    @operation
    def _check_cli_connection(self, sid, callback):
        session = self.session(sid, attach=False, background=True)
        with session.activation_lock, session.action_lock, session.attach_lock:
            record = self._cli_recovery_record()
            if (record.get('phase') in ('draining', 'stopped') and isinstance(self.cli_sessions, CliSessions)
                    and self.cli_sessions.daemon_status() == 'stopped'):
                self.cli_sessions.drained_connections.update(record['connections'])
                record['phase'] = 'released' if record.get('purpose') == 'release' else 'stopped'
                private_json(self.data_dir / 'cli-daemon-recovery.json', record)
            result = callback()
            self._record_cli_release(session, result)
            return result

    def _record_cli_release(self, session, result):
        if result['status'] in ('released', 'detached'):
            self.released_cli.set(session.id, True)
            with self.lock:
                observer = self.cli_notifications.get(session.id)
            if observer is not None:
                self.release_notification_session(observer)
            with session.condition:
                session.connected = False
                session.error = result.get('notice', 'CLI 连接已释放，请点击继续会话重新连接')
                session.changed()

    def discovery_diagnostics(self, thread_id=None):
        if thread_id is not None:
            uuid.UUID(thread_id)
        sources = [(self, 'local')]
        if self.host == 'local':
            sources.extend((self.for_host(host), 'remote-' + str(index))
                           for index, host in enumerate(self.hosts.hosts(), 1))
        def inspect(source):
            bridge, label = source
            try:
                result = bridge.store.discovery_diagnostics(thread_id)
            except RemoteUnavailable:
                result = {'status': 'unavailable', 'phase': 'ssh', 'reason': 'remote_read_failed'}
            except (StoreUnavailable, OSError, ValueError):
                result = {'status': 'unavailable', 'phase': 'store', 'reason': 'store_read_failed'}
            return {'host': label, **result}
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
            reports = list(pool.map(inspect, sources))
        return {'schemaVersion': 1, 'generatedAt': int(time.time()),
                'remoteHostCount': len(sources) - 1, 'hosts': reports}

    def list(self, query="", limit=100, offset=0, archived=False, source='all', include_sources=False,
             local_only=False, host_label=None, entry=None, pin_order=False):
        if source not in ('all', 'desktop', 'cli', 'ide', 'wsl'):
            raise ValueError('不支持的会话来源')
        if entry and entry not in ('desktop', 'all'):
            self.host_errors = []
            target = self.for_host(entry)
            try:
                return target.list(query=query, limit=limit, offset=offset, archived=archived,
                                   source=source, include_sources=include_sources, pin_order=pin_order)
            except (RemoteUnavailable, StoreUnavailable) as exc:
                self.host_errors = [{'host': entry, 'label': host_label or 'WSL', 'error': str(exc)}]
                return ([], []) if include_sources else []
        entry_errors = []
        sources = [(self, host_label or self.host_label)]
        if self.host == 'local' and not local_only:
            entries = self.entries.hosts() if self.entries is not None and entry != 'desktop' else {}
            for host, info in codex_list_hosts(self.hosts.hosts(), entries).items():
                label = info.get('displayName') or info.get('label') or info.get('alias') or host
                try:
                    sources.append((self.for_host(host), label))
                except (OSError, ValueError, KeyError) as exc:
                    entry_errors.append({'host': host, 'label': label, 'error': str(exc)})
        def read(host_source):
            bridge, label = host_source
            try:
                if bridge.host.startswith('wsl:'):
                    # WSL is a display category; its native source remains CLI.
                    wsl_source = 'wsl' if source in ('all', 'wsl') else 'desktop'
                    value = bridge.list(query=query, limit=limit + offset, archived=archived,
                                        source=wsl_source, include_sources=include_sources, pin_order=pin_order)
                    rows, available = value if include_sources else (value, [])
                    return rows, None, available
                # A host-scoped HTTP read can wait for that host. Aggregated
                # readers must never make local results wait for SSH.
                remote = self.host == 'local' and isinstance(bridge.store, RemoteStore)
                available = ((bridge.store.poll('available_sources', {}) if remote else
                              bridge.store.available_sources()) if include_sources else [])
                excluded = () if bridge.host.startswith('codex:') else ('ide',)
                available = [item for item in available if item not in excluded]
                pins = set(bridge.session_pins.identifiers())
                options = dict(limit=limit + offset, archived=archived, query=query, source=source,
                               **({'exclude_sources': excluded} if excluded else {}),
                               **({"pinned_ids": sorted(pins)} if pin_order else {}))
                rows = ([] if source == 'wsl' else bridge.store.poll('list', options) if remote else
                        bridge.store.list(**options))
                rows = self.hosts.decorate(rows, bridge.host, label, bridge.organization_commands.native_memberships())
                with bridge.lock:
                    bridge.listed.update(row['id'] for row in rows)
                    for row in rows:
                        session = bridge.live.get(row['id'])
                        bridge.origins[row['id']] = row.get('origin', 'desktop')
                        row['bridgePinned'] = row['id'] in pins
                        row['connected'] = bool(session and session.connected)
                        row['access'] = bridge._access(row.get('origin', 'desktop'), session)
                        row['title'] = row.get('name') or row.get('title') or '未命名聊天'
                return rows, None, available
            except (RemoteUnavailable, StoreUnavailable) as exc:
                return [], {'host': bridge.host, 'label': label, 'error': str(exc)}, []
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
            results = list(pool.map(read, sources))
        self.host_errors = entry_errors + [error for _, error, _ in results if error]
        rows = [row for group, _, _ in results for row in group]
        available_sources = {source for _, _, available in results for source in available}
        rows.sort(key=lambda row: (bool(pin_order and row.get('bridgePinned')), row['recency'], row['id'], row['host']), reverse=True)
        page = rows[offset:offset + limit]
        return (page, sorted(available_sources)) if include_sources else page

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
                    session = self.live[identifier] = LiveSession(identifier, question_skips=self.question_skips)
                    session.activity_only = True
                    session.origin = self.origins.get(identifier, "desktop")
                    session.access = self._access(session.origin, session)
                session.touched = time.monotonic()
                sessions.append(session)
            pending = [s for s in sessions if not s.connected and time.monotonic() >= s.retry_at]
            if pending and not self.activity_following:
                self.activity_following = True
                def follow():
                    try:
                        for session in pending:
                            if self.closed.is_set(): break
                            with session.condition:
                                now = time.monotonic()
                                session.retry_at = now + (.5 if now < session.desktop_recovery_until else 15)
                            self._attach(session, timeout=0)
                    finally:
                        with self.lock: self.activity_following = False
                threading.Thread(target=follow, daemon=True).start()
        try:
            identifiers = [s.id for s in sessions]
            recencies = (self.store.poll('recencies', {'identifiers': identifiers}) if isinstance(self.store, RemoteStore)
                         else self.store.recencies(identifiers))
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
        sources = [self] if self.observation_enabled() else []
        if self.host == 'local' and sources:
            sources.extend(self.for_host(host, cached=True) for host in self.hosts.hosts(cached=True))
        rows = []
        for source in sources:
            if not source.observation_enabled():
                continue
            source.notification_event = self.notification_event
            try:
                # Metadata discovery also works when no App IPC is available.
                if not source.observation_enabled():
                    source.suspend_observation()
                    continue
                if source.notification_client_id != source.ipc.client_id:
                    source.notification_cursor = 0
                    source.notification_boundary.clear()
                    source.notification_client_id = source.ipc.client_id
                changes = (source.store.poll('notification_changes', {'since': source.notification_cursor})
                           if isinstance(source.store, RemoteStore) else
                           source.store.notification_changes(source.notification_cursor))
                cursor = max([source.notification_cursor] + [r['changed_at'] for r in changes])
                for row in changes:
                    if row['changed_at'] > source.notification_cursor or row['id'] not in source.notification_boundary:
                        rows.append({'id': row['id'], 'host': source.host})
                source.notification_boundary = {r['id'] for r in changes if r['changed_at'] == cursor}
                source.notification_cursor = cursor
            except (IPCError, RemoteUnavailable, StoreUnavailable):
                pass  # Preserve the cursor so the next discovery can recover.
        if self.host == 'local' and self.entries is not None:
            for identifier in self.entries.hosts():
                try:
                    rows.extend(self.entries.get(identifier).notification_candidates())
                except (IPCError, RemoteUnavailable, StoreUnavailable, KeyError, OSError, ValueError):
                    pass
        return rows

    def notification_updates(self):
        sources = [self] + list(self.remote_bridges.values()) if self.host == 'local' else [self]
        rows = []
        for source in sources:
            if not source.observation_enabled():
                continue
            with source.lock:
                identifiers = source.notification_dirty
                source.notification_dirty = set()
                sessions = [source.live[i] for i in identifiers if i in source.live]
            for session in sessions:
                with session.condition:
                    state = session.state or {}
                    if session.connected and (session.origin == 'cli' or session.watched or state.get('threadRuntimeStatus', {}).get('type') == 'active' or
                            any(t.get('status') == 'inProgress' for t in ordered_turns(state)) or pending_requests(state)):
                        rows.append({'id': session.id, 'host': source.host})
        if self.host == 'local' and self.entries is not None:
            # Existing instances only: a UI poll never registers or starts WSL.
            with self.entries.lock:
                entries = list(self.entries.bridges.values())
            for entry in entries:
                rows.extend(entry.notification_updates())
        return rows

    def release_notification_session(self, session):
        if session.runtime == 'cli-observer':
            with session.condition:
                session.watched = False
                session.connection_epoch += 1
                session.connected = False
                session.state = None
                session.retry_at = 0
                session.changed()
            return True
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
                if (session.connected or session.discovering) and session.runtime != "cli":
                    try:
                        self.ipc.follow(session.id, session.owner, False, host=self.native_host)
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
        if not self.observation_enabled():
            raise IPCError('此应用已停用手机接入')
        with self.lock:
            current = self.live.get(thread_id)
            origin = current.origin if current and current.origin_verified else self.origins.get(thread_id)
        if origin == 'cli':
            return self._cli_notification_session(thread_id)
        with self.lock:
            session = self.live.get(thread_id)
            if session is None:
                session = self.live[thread_id] = LiveSession(thread_id, question_skips=self.question_skips)
                session.activity_only = True
        if not session.watched:
            session.notification_probe_until = time.monotonic() + 10
        session.watched = True
        with session.condition:
            if not session.connected and not session.notification_connecting and not self.closed.is_set() and time.monotonic() >= session.retry_at:
                if not self.notification_slots.acquire(blocking=False):
                    # Wait for a probe slot, not for an owner RPC on the scanner.
                    session.notification_probe_until = time.monotonic() + 10
                    return session
                session.notification_connecting = True
                now = time.monotonic()
                session.retry_at = now + (.5 if now < session.desktop_recovery_until else 15)
                session.notification_probe_until = time.monotonic() + 10
                def follow():
                    started = time.monotonic()
                    try:
                        self._attach(session, timeout=0)
                    except (OSError, ValueError, KeyError, IPCError, RemoteUnavailable, StoreUnavailable):
                        pass  # An unavailable passive probe must never activate a chat.
                    finally:
                        with session.condition:
                            session.notification_connecting = False
                            session.condition.notify_all()
                        self.notification_slots.release()
                        self.notification_event.set()
                        logging.debug('notification stage=probe source=%s work_ms=%.1f',
                                     'local' if self.host == 'local' else 'remote', (time.monotonic() - started) * 1000)
                threading.Thread(target=follow, daemon=True).start()
        return session

    def _cli_notification_session(self, thread_id):
        # Keep observer state out of live execution sessions: a read-only native
        # status must never grant write capabilities or satisfy activate().
        with self.lock:
            session = self.cli_notifications.setdefault(thread_id, LiveSession(thread_id))
        with session.condition:
            session.runtime, session.origin = 'cli-observer', 'cli'
            session.access = {'source': 'cli', 'runtime': None, 'readOnly': True,
                              'canSend': False, 'canInterrupt': False, 'canApprove': False,
                              'canReconnect': False}
            if self.closed.is_set() or not self.observation_enabled() or self.released_cli.contains(thread_id):
                session.connected = False
                session.state = None
                return session
            if not session.watched:
                session.notification_probe_until = time.monotonic() + 10
            session.watched = True
            if session.notification_connecting or time.monotonic() < session.retry_at:
                return session
            if not self.notification_slots.acquire(blocking=False):
                session.notification_probe_until = time.monotonic() + 10
                return session
            session.notification_connecting = True
            session.notification_probe_until = time.monotonic() + 10
            epoch = session.connection_epoch
            session.retry_at = time.monotonic() + 5
            session.state, session.connected = None, False
        def observe():
            try:
                state = self.cli_sessions.observe(thread_id)
                with session.condition:
                    if (session.connection_epoch == epoch and session.watched and not self.closed.is_set()
                            and self.observation_enabled() and not self.released_cli.contains(thread_id)):
                        session.state = self.cli_notification_waits.project(thread_id, state) if state is not None else None
                        session.connected = state is not None
                        if state is None:
                            session.retry_at = time.monotonic() + 15
                        session.changed()
                    elif session.connection_epoch == epoch:
                        session.state, session.connected = None, False
                        session.changed()
            except (AppServerError, RemoteUnavailable, OSError, ValueError, KeyError):
                with session.condition:
                    if session.connection_epoch == epoch:
                        session.state, session.connected = None, False
                        session.retry_at = time.monotonic() + 15
                        session.changed()
            finally:
                with session.condition:
                    session.notification_connecting = False
                    session.condition.notify_all()
                self.notification_slots.release()
                self.notification_event.set()
        threading.Thread(target=observe, daemon=True, name='cli-notification-read').start()
        return session

    @operation(entry_only=True)
    def upload(self, thread_id, identifier, name, data):
        self.store.get(thread_id)  # Uploads do not activate a desktop chat.
        return self.uploads.put(thread_id, identifier, name, data)

    def _desktop_metadata_operation(self, cwd, operation, failure_message, *, require_shared=False):
        from bridge.clients.codex.desktop_runtime import connect, initialization_lock
        from bridge.clients.codex.app_server import AppServerError
        from bridge.features.sessions.create import _runtime_operation
        with ExitStack() as locks:
            try:
                if self.host == 'local' and sys.platform == 'darwin':
                    # Every gateway using this home must finish its metadata
                    # transaction before initialization may quit the original App.
                    locks.enter_context(initialization_lock(self.codex_home))
                client = connect(self.codex_home, self.catalog_reader.executable) if self.host == 'local' else None
            except (AppServerError, OSError, ValueError) as exc:
                # Only connection/identity checks have run; no metadata write was sent.
                raise CreationUnavailable(str(exc)) from exc
            if client is None:
                if require_shared and self.host == 'local' and sys.platform == 'darwin':
                    raise CreationUnavailable('请先打开账号管理 → Codex 原生会话连接 → 初始化连接，在所有桌面任务结束后确认正常重启 Codex App；尚未创建侧边聊天')
                return _runtime_operation(self.catalog_reader.executable, self.codex_home, cwd, operation,
                                          failure_message=failure_message, **_runtime_scope(self.host))
            try:
                return operation(client.request)
            except AppServerError as exc:
                # A shared-runtime operation is never retried through a new writer.
                raise CreationError(failure_message) from exc
            finally:
                client.close()  # Disconnect this Bridge only; the original App owns the runtime.

    def _invalidate_attached_threads(self, parent_id):
        if self.host != 'local' or not parent_id:
            return
        try:
            self.ipc.invalidate_attached_threads(parent_id)
        except (IPCError, OSError, ValueError):
            # A native UI refresh failure must not turn a completed mutation into
            # an unknown write or cause the durable operation to be submitted again.
            logging.debug('Codex attached thread cache invalidation unavailable')

    @operation
    def side_chat(self, thread_id, owner, action, body):
        self.store.get(thread_id)
        if action == 'list':
            if not self.local:
                raise ValueError('此接入暂不支持读取原生侧边聊天')
            rows = self.store.attached_children(thread_id)
            for row in rows:
                row.update(host=self.host, title=row.get('name') or row.get('title') or '未命名聊天')
            return {'parentId': thread_id, 'chats': rows}
        if action == 'create':
            self._require_desktop_feature(thread_id)
            if not self.local:
                raise ValueError('此预览暂支持网关电脑上的聊天，SSH 远程工作区尚未接入侧边聊天')
            # Follow only: creating a child must never resume or execute the parent.
            session = self.session(thread_id, attach=True)
            with session.condition:
                if not session.connected or not session.state:
                    raise ValueError('尚未取得主聊天的实时配置，请等待连接完成后重试')
                snapshot = copy.deepcopy(session.state)
                settings = self._fork_settings(snapshot)
            permissions = snapshot.get('currentPermissions') or {}
            policy = permissions.get('sandboxPolicy') or {}
            if not settings.get('permissions') and policy.get('type'):
                modes = {'readOnly': 'read-only', 'workspaceWrite': 'workspace-write', 'dangerFullAccess': 'danger-full-access'}
                if policy['type'] not in modes:
                    raise ValueError('当前权限配置暂不支持原生侧边聊天')
                settings['sandbox'] = modes[policy['type']]
            identifier = body.get('creationId')
            if not isinstance(identifier, str):
                raise ValueError('创建请求标识无效')
            uuid.UUID(identifier)
            # Share the existing durable mutation ledger. A lost fork response
            # must never turn a retry/reload into another persisted child.
            with self.message_lock:
                entry = self.message_actions.get(identifier)
                if entry:
                    if entry.get('action') != 'side-chat' or entry.get('source') != thread_id:
                        raise ValueError('同一操作标识不能用于不同聊天')
                    if entry.get('status') != 'created':
                        raise CreationError('侧边聊天创建结果尚未确认，请查看父聊天；不会重复创建')
                    return {'id': entry['id'], 'host': self.host, 'parentId': thread_id}
                entry = {'action': 'side-chat', 'source': thread_id, 'status': 'unknown', 'at': time.time()}
                self.message_actions[identifier] = entry
                self._save_actions()
                def created(child):
                    entry['id'] = child
                    self._save_actions()
                try:
                    child = fork_attached(self.catalog_reader.executable, self.codex_home,
                                          snapshot['cwd'], thread_id,
                                          (snapshot.get('title') or '未命名聊天')[:90] + ' · 侧边聊天',
                                          settings, created, isolated_home=self.host.startswith('codex:'),
                                          runtime_operation=lambda call: self._desktop_metadata_operation(
                                              snapshot['cwd'], call, '侧边聊天创建结果尚未确认，请查看父聊天；不会重复创建',
                                              require_shared=True))
                except CreationUnavailable:
                    del self.message_actions[identifier]
                    self._save_actions()
                    raise
                entry.update(id=child, status='created')
                self._save_actions()
                self._invalidate_attached_threads(thread_id)
                return {'id': child, 'host': self.host, 'parentId': thread_id}
        # Existing temporary views may finish their current lifecycle, but no
        # endpoint creates another temporary runtime after the native migration.
        result = self.side_chats.operate(thread_id, action, body, None, None, self.catalog_reader)
        if (self.local and isinstance(result, dict) and result.get('id') and
                result.get('cwd') and result.get('provider')):
            result['modelCatalogIdentity'] = self.catalog_reader.model_identity(result['cwd'], result['provider'])
        return result

    def side_attachment(self, thread_id, child, operation, *args):
        self.store.get(thread_id)
        return self.side_chats.attachment(thread_id, child, operation, *args)

    @operation(entry_only=True)
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
            with self.lock:
                session = self.live.setdefault(thread_id, LiveSession(thread_id, question_skips=self.question_skips))
        self._load_session_origin(session)
        session.touched = time.monotonic()
        if background:
            if attach:
                self._refresh_async(session, force)
                self._refresh_saved_async(session)
            return session
        if attach and not session.connected:
            self._attach(session)
        if (session.state is None and session.history_state is None) or session.activity_only or (session.origin in ('cli', 'ide') and session.runtime == 'desktop' and session.saved_view is None):
            fallback = self._saved_history(session)
            with session.condition:
                if (session.state is None and session.history_state is None) or session.activity_only or (session.origin in ('cli', 'ide') and session.runtime == 'desktop' and session.saved_view is None):
                    session.set_history(fallback)
                    session.activity_only = False
        return session

    def _load_session_origin(self, session):
        if session.origin_verified:
            return
        # Restored queued entries have no verified source until this read.
        # Keep SSH/SQLite IO outside the shared live-session lock.
        metadata = self.store.get(session.id)
        parent = self.store.attached_parent(session.id) if self.local and hasattr(self.store, 'attached_parent') else None
        with session.condition:
            session.origin = metadata.get("origin", "desktop")
            session.saved_cwd = metadata.get('cwd')
            session.saved_provider = metadata.get('model_provider')
            session.attached_parent = dict(parent, host=self.host) if parent else None
            session.origin_verified = True
            session.access = self._access(session.origin, session)

    def _saved_history(self, session):
        return self.store.history(session.id, turn_limit=session.history_limit, item_limit=session.timeline_limit)

    def _refresh_saved_async(self, session, receipts=False):
        # Local rollout reads are cheap enough to follow terminal writes without
        # repeating the slower owner discovery (or SSH) on every poll.
        if receipts:
            with self.submit_lock:
                unknown = tuple(sorted(k for k, v in self.submissions.items()
                                       if k.startswith(session.id + ':') and v.get('status') == 'unknown'))
            if not unknown:
                return
        if isinstance(self.store, RemoteStore):
            return
        with session.condition:
            if session.history_refreshing or self.closed.is_set():
                return
            if receipts:
                # Coalesce by unknown set / completed turn, with four reads
                # at most for delayed persistence. Never trust prepared turns.
                completed = tuple(t.get('turnId') for t in ordered_turns(session.state or {})
                                  if t.get('turnId') and t.get('status') in ('completed', 'interrupted', 'failed'))
                receipt_key = (unknown, completed[-1:])
                if receipt_key != session.history_receipt_key:
                    session.history_receipt_key = receipt_key
                    session.history_receipt_attempts = 0
                elif session.history_receipt_attempts >= 4 or time.monotonic() < session.history_retry_at:
                    return
                session.history_receipt_attempts += 1
            elif (session.connected or session.connecting or session.saved_view is None or
                  time.monotonic() < session.history_retry_at):
                return
            session.history_refreshing = True
            session.history_retry_at = time.monotonic() + (2 ** session.history_receipt_attempts if receipts else 2)
        def refresh():
            try:
                fallback = self._saved_history(session)
                normalized = normalize_state(fallback, False, image_cache=session.tool_image_cache, item_limit=session.timeline_limit)
                with session.condition:
                    if receipts or not session.connected and session.saved_view != normalized:
                        session.set_history(fallback)
                self._reconcile_submissions(session)
            except (OSError, ValueError, KeyError, RemoteUnavailable, StoreUnavailable):
                pass
            finally:
                with session.condition:
                    session.history_refreshing = False
        threading.Thread(target=refresh, daemon=True).start()

    def _refresh_async(self, session, force=False):
        with session.condition:
            if (session.connected and not session.activity_only) or session.connecting or self.closed.is_set() or not self.observation_enabled():
                return
            if not force and time.monotonic() < session.retry_at and not session.activity_only:
                return
            # A CLI origin may keep writing its rollout after the first passive
            # read. Refresh saved turns while no runtime owns this viewer.
            needs_history = session.history_state is None or session.activity_only or (session.origin in ('cli', 'ide') and not session.connected)
            session.connecting = True
            session.owner_probe_inflight = True
            epoch = session.connection_epoch
            session.changed()
        def refresh():
            # Give a fast saved read priority, without making a slow/missing
            # remote history file a prerequisite for the original owner.
            history_ready = threading.Event()
            def attach_owner():
                history_ready.wait(.1)
                try:
                    self._attach(session)
                finally:
                    with session.condition:
                        session.owner_probe_inflight = False
                        session.changed()
            owner_worker = threading.Thread(target=attach_owner, daemon=True)
            owner_worker.start()
            try:
                if needs_history:
                    try:
                        fallback = self._saved_history(session)
                        with session.condition:
                            session.set_history(fallback)
                            session.history_retry_at = time.monotonic() + 2
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
                history_ready.set()
                owner_worker.join()
                with session.condition:
                    session.connecting = False
                    # A retired probe must not reinstate its cooldown after an
                    # observed disconnect has invalidated this connection.
                    if session.connection_epoch == epoch:
                        now = time.monotonic()
                        session.retry_at = (0 if session.connected else now +
                                            (.5 if now < session.desktop_recovery_until else 15))
                    session.changed()
        threading.Thread(target=refresh, daemon=True).start()

    def _owner_absent(self, error, connected):
        if str(error) == 'no-client-found':
            return True
        if connected or not isinstance(error.__cause__, OSError):
            return False
        if isinstance(error.__cause__, (FileNotFoundError, ConnectionRefusedError)):
            return True
        if sys.platform != 'win32':
            try:
                Path(self.ipc.path).stat()
            except FileNotFoundError:
                return True
            except OSError:
                pass
        return False

    def _attach(self, session, timeout=1.5):
        with session.condition:
            probe_sequence = session.owner_probe_sequence
            epoch = session.connection_epoch
        with session.attach_lock:
            if self.released_cli.contains(session.id):
                return
            if session.connected or self.closed.is_set() or not self.observation_enabled():
                return
            if isinstance(self.store, RemoteStore):
                try:
                    ssh_connection(self.store.alias).check()
                except RemoteUnavailable as exc:
                    with session.condition:
                        session.error = str(exc)
                        session.changed()
                    return
            self._load_session_origin(session)
            if self.host.startswith('codex:') and session.origin == 'desktop':
                with session.condition:
                    session.error = '此独立入口只接入原 VS Code / CLI 会话；桌面会话请使用默认入口。'
                    session.changed()
                return
            if session.origin == 'cli':
                # Source is the execution route. Passive reads never probe App
                # owners or resume a native thread; explicit activation does.
                with session.condition:
                    session.desktop_owner_probe = 'absent'
                    session.access = self._access(session.origin, session)
                return
            if session.origin in ('cli', 'ide'):
                with session.condition:
                    # A concurrent read may have completed this same discovery
                    # while we waited for attach_lock. Reuse only that overlap.
                    if (session.connection_epoch == epoch == session.owner_probe_epoch and
                            session.owner_probe_sequence != probe_sequence):
                        return
                    probe_epoch = session.connection_epoch
                ipc_connected = False
                try:
                    if sys.platform == 'win32' and self.owner_guard:
                        self.owner_guard(session.id)
                    self.ipc.connect()
                    if not self.observation_enabled():
                        self.suspend_observation()
                        return
                    ipc_connected = True
                    owner = self.ipc.owner(session.id, host=self.native_host)
                    if not isinstance(owner, str) or not owner:
                        raise IPCError('现有 Codex App 连接与此会话不匹配，未连接')
                    with session.condition:
                        if session.connection_epoch != probe_epoch:
                            return
                        session.owner = owner
                        session.revision = None
                        session.desktop_owner_probe = 'found'
                        session.discovering = True
                        session.access = self._access(session.origin, session)
                    self.ipc.follow(session.id, owner, host=self.native_host)
                    with session.condition:
                        session.condition.wait_for(lambda: session.connected or self.closed.is_set(), timeout=timeout)
                except IPCError as exc:
                    with session.condition:
                        if session.connection_epoch != probe_epoch:
                            return
                        session.discovering = False
                        absent = self._owner_absent(exc, ipc_connected)
                        if absent or session.origin == 'cli' and (not ipc_connected or
                                str(exc) == '桌面读取超时；尚未取得实时会话状态。'):
                            session.owner = None
                            session.desktop_owner_probe = 'absent'
                            session.error = None
                        else:
                            session.desktop_owner_probe = 'unknown'
                            session.error = ('VS Code 原会话未响应，尚未确认执行归属。请重新连接；如使用旧版 Codex 扩展，请先更新扩展。'
                                             if session.origin == 'ide' and str(exc) == '桌面读取超时；尚未取得实时会话状态。' else str(exc))
                        session.access = self._access(session.origin, session)
                        session.changed()
                with session.condition:
                    if session.connection_epoch == probe_epoch:
                        session.owner_probe_epoch = probe_epoch
                        session.owner_probe_sequence += 1
                return
            try:
                # Owners answer a follow with a fresh snapshot. This also works
                # for remote/background owners absent from owner-discovery.
                self.ipc.connect()
                if not self.observation_enabled():
                    self.suspend_observation()
                    return
                with session.condition:
                    session.owner = None
                    session.revision = None
                    session.discovering = True
                self.ipc.follow(session.id, None, host=self.native_host)
                with session.condition:
                    session.condition.wait_for(lambda: session.connected or self.closed.is_set(), timeout=timeout)
                    # Keep following: a cold chat can publish after this short
                    # wait. Missing live state does not make saved history fail.
            except IPCError as exc:
                with session.condition:
                    session.discovering = False
                    session.connected = False
                    session.error = None if session.origin in ('cli', 'ide') else str(exc)
                    session.access = self._access(session.origin, session)
                    session.changed()

    def desktop_restart_recommended(self):
        with self.lock:
            return any(session.desktop_restart_recommended and not session.connected
                       for session in self.live.values())

    def reconnect_session(self, thread_id, *, rebuild=False):
        # Join duplicate phone reconnects before they queue behind the host gate.
        # Only connection reads are shared; writes keep their existing receipts.
        key = (thread_id, rebuild)
        with self.lock:
            pending = self.reconnect_pending.get(key)
            if pending is None:
                pending = self.reconnect_pending[key] = Future()
                leader = True
            else:
                leader = False
        if not leader:
            return pending.result()
        try:
            result = self.rebuild_connection(thread_id) if rebuild else self.activate(thread_id)
            pending.set_result(result)
            return result
        except Exception as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.reconnect_pending.pop(key, None)

    def _rebuild_desktop_transport(self):
        # Caller holds the host operation gate: wait for writes before replacing
        # this follower, and never replay an operation whose result is unknown.
        self.ipc.reconnect()
        with self.lock:
            sessions = list(self.live.values())
        for followed in sessions:
            if followed.runtime == 'desktop':
                with followed.condition:
                    followed.last_activation = None
                    followed.retry_at = 0

    @operation
    def rebuild_connection(self, thread_id):
        session = self.session(thread_id, background=True)
        self._load_session_origin(session)
        if session.origin != 'desktop' or self.host != 'local':
            raise IPCError('此会话不使用桌面连接，请使用继续会话。')
        # The host operation gate drains pending writes before this read-only
        # follower connection is replaced. Native tasks keep their original owner.
        rebuilt = False
        try:
            self._rebuild_desktop_transport()
            rebuilt = True
            return self.activate(thread_id, _transport_rebuilt=True)
        except IPCError as exc:
            with session.condition:
                if session.connected:
                    return session
                session.rebuild_recommended = True
                session.desktop_restart_recommended = True
                session.error = ('已重建网关连接，但原桌面会话仍未恢复。可在应用管理中核对任务后重启 Codex；操作未发送。'
                                 if rebuilt else '尚未连接原桌面会话。可再次重建连接；若仍失败，可在应用管理中核对任务后重启 Codex。')
                session.changed()
                raise IPCError(session.error) from exc

    @staticmethod
    def _record_cli_follow_error(session, error):
        with session.condition:
            session.error = str(error)
            session.cli_recovery = getattr(error, 'details', None)
            session.changed()

    def _verify_cli_submission_account(self, session):
        # Recheck at dispatch admission, regardless of a turn ending after
        # connection lookup. Existing task answers take their own guarded path.
        try:
            self._recover_cli_environment(session.id, follow_account=True)
        except IPCError as error:
            self._record_cli_follow_error(session, error)
            raise
        with session.condition:
            if session.cli_recovery is not None:
                session.cli_recovery = session.error = None
                session.changed()

    @operation
    def activate(self, thread_id, *, allow_ide_resume=True, _transport_rebuilt=False, _cli_recovery=None, _expected_cli=None,
                 _defer_cli_account=False):
        """Explicit phone operation only; reads and notification watches never navigate."""
        from bridge.clients.codex.daemon_network import CliProviderMismatch
        if _cli_recovery is None:
            record = self._cli_recovery_record()
            plan = record.get('providerTarget')
            if (plan and plan['before']['id'] == thread_id and record.get('phase') == 'complete'
                    and record.get('providerPhase') == 'authorized'):
                return self._activate_cli_recovery(thread_id, plan)
        session = self.session(thread_id, background=True)
        if self.host.startswith('codex:') and self.store.get(thread_id).get('origin') == 'desktop':
            raise ValueError('此独立入口只接入原 VS Code / CLI 会话')
        with session.activation_lock:
            self.released_cli.set(thread_id, False)
            follow_account = _cli_recovery is None and _expected_cli is None
            environment_checked = not follow_account or session.runtime == 'cli'
            with session.condition:
                state = session.state or {}
                idle_cli = ((state.get('threadRuntimeStatus') or {}).get('type') == 'idle'
                            and not session.pending_requests() and (state.get('goal') or {}).get('status') != 'active'
                            and not any(turn.get('status') == 'inProgress' for turn in ordered_turns(state)))
            # A running task must retain its stop/approval/Goal controls even
            # while connection configuration is waiting for a safe recovery.
            if follow_account and environment_checked and (not session.connected or idle_cli and not _defer_cli_account):
                try:
                    environment_checked = self._recover_cli_environment(thread_id, defer_start=True, follow_account=follow_account) is not False
                except IPCError as error:
                    self._record_cli_follow_error(session, error)
                    raise
                with session.condition:
                    if session.cli_recovery is not None:
                        session.cli_recovery = session.error = None
                        session.changed()
            endpoint_changed = not _transport_rebuilt and session.runtime == 'desktop' and self.ipc.endpoint_changed()
            replace_transport = endpoint_changed or (not _transport_rebuilt and session.runtime == 'desktop' and
                not session.connected and session.rebuild_recommended)
            if session.connected and not replace_transport:
                return session
            with session.condition:
                session.activating = True
                session.changed()
            try:
                # VS Code can replace the public router without closing an old
                # follower socket. Re-entering a chat must not trust that socket's
                # cached connected snapshot. A prior owner disconnect also gets
                # one fresh connection, including on Windows named pipes.
                if replace_transport:
                    if endpoint_changed and session.connected:
                        # Another router can coexist with a still-working owner.
                        # Ask for a current snapshot before abandoning that route.
                        with session.condition:
                            session.connected = False
                        self._attach(session)
                        if session.connected:
                            return session
                    self._rebuild_desktop_transport()
                    _transport_rebuilt = True
                if session.connected:
                    return session
                # Reuse this in-flight passive owner probe once. If none is
                # running, the explicit action performs its own fresh probe.
                with session.condition:
                    probe_inflight = session.owner_probe_inflight
                if probe_inflight:
                    with session.condition:
                        # One cold probe can use socket 5s + initialize 6s +
                        # owner 7s + snapshot 1.5s. Bound this wait if queued too.
                        ready = session.condition.wait_for(
                            lambda: not session.owner_probe_inflight or session.connected or self.closed.is_set(),
                            timeout=20 if session.origin in ('cli', 'ide') else 8)
                    if not ready:
                        raise IPCError('桌面连接检查未完成，请稍后重试。')
                else:
                    self._attach(session)
                if session.connected:
                    return session
                if session.origin == 'ide' and not allow_ide_resume:
                    raise IPCError(session.error or 'VS Code 原会话尚未连接。若编辑器已关闭，请主动继续此会话以启用后台接续。')
                if session.origin == 'ide' and session.desktop_owner_probe != 'absent':
                    raise IPCError(session.error or '正在等待原 Codex App 会话快照；未启动 CLI 后台服务。')
                if self.closed.is_set():
                    raise IPCError('网关正在停止；操作未发送，请稍后重试。')
                if session.origin in ('cli', 'ide'):
                    with session.attach_lock:
                        with session.condition:
                            if session.connected:
                                return session
                            if session.origin == 'ide' and session.desktop_owner_probe != 'absent':
                                raise IPCError(session.error or '正在等待原 Codex App 会话快照；未启动 CLI 后台服务。')
                            session.native_attaching = True
                        try:
                            try:
                                if not environment_checked:
                                    self._recover_cli_environment(session.id, follow_account=follow_account)
                                if _cli_recovery:
                                    plan, before_resume = _cli_recovery
                                    state = self.cli_sessions.attach(session.id, explicit=True,
                                        target_provider=plan['provider'] if plan['provider'] != plan['before']['modelProvider'] else None,
                                        expected_identity=plan['before'], before_resume=before_resume)
                                else:
                                    released = self._cli_recovery_record()
                                    expected = _expected_cli or (released.get('releasedThread') if released.get('purpose') == 'release'
                                                and released.get('phase') == 'complete'
                                                and (released.get('releasedThread') or {}).get('id') == session.id else None)
                                    state = (self.cli_sessions.attach(session.id, explicit=True, expected_identity=expected)
                                             if expected else self.cli_sessions.attach(session.id, explicit=True))
                            except AppServerError as exc:
                                raise (CliProviderMismatch.from_native(exc) or IPCError(str(exc))) from exc
                            self._cli_state(session.id, state)
                            if not session.connected:
                                raise IPCError('CLI 会话尚未恢复；操作未发送，请重试继续会话。')
                            return session
                        finally:
                            with session.condition:
                                session.native_attaching = False
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
                    session.rebuild_recommended = session.origin == 'desktop' and self.host == 'local'
                    if session.rebuild_recommended and _transport_rebuilt:
                        session.desktop_restart_recommended = True
                        session.error = '已重建网关连接，但原桌面会话仍未恢复。可在应用管理中核对任务后重启 Codex；操作未发送。'
                    else:
                        session.error = ('桌面执行连接尚未恢复，建议点击“重建连接”。操作未发送。'
                                         if session.rebuild_recommended else str(exc))
                    session.cli_recovery = getattr(exc, 'details', None)
                    error = session.error
                raise IPCError(error) from exc
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
                        self._invalidate(session, "桌面会话连接已断开，正在等待重连", runtime='desktop',
                                         owner=params.get("clientId"), reason='desktop_client_disconnected')
            # Owner changes can make previously unanswered probes available.
            self.notification_cursor = 0
            self.notification_boundary.clear()
            self.notification_event.set()
            return
        if message.get("method") != "thread-stream-state-changed":
            return
        params = message.get("params", {})
        if params.get("hostId") != self.native_host:
            return
        with self.lock:
            session = self.live.get(params.get("conversationId"))
        if session is None or self.released_cli.contains(session.id):
            return
        change = params.get("change", {})
        with session.condition:
            if session.origin in ('cli', 'ide') and (session.native_attaching or session.runtime == 'cli' and session.connected):
                return
            if session.origin in ('cli', 'ide') and session.desktop_owner_probe != 'found':
                return
            if message.get('version') != 11:
                if session.owner and session.owner == message.get('sourceClientId'):
                    self._invalidate(session, '原客户端的会话同步协议与网关不兼容，请更新 Codex 客户端后重连。',
                                     runtime='desktop', owner=session.owner, reason='desktop_stream_version')
                return
            if session.discovering and session.origin not in ('cli', 'ide') and change.get("type") == "snapshot" and (change.get("conversationState") or {}).get("id") == session.id:
                session.owner = message.get("sourceClientId")
                session.discovering = False
            if not session.owner or message.get("sourceClientId") != session.owner:
                return
            reason = 'snapshot_invalid'
            try:
                if change.get("type") == "snapshot":
                    state = change["conversationState"]
                elif change.get("type") == "patches":
                    if session.revision is None or change.get("baseRevision") != session.revision:
                        reason = 'revision_mismatch'
                        raise ValueError("Revision mismatch")
                    state = apply_patches(session.state, change["patches"])
                else:
                    return
                if state.get('id') != session.id:
                    reason = 'thread_id_mismatch'
                    raise ValueError("Session mismatch")
                previous_cwd = session.state.get('cwd') if session.connected and session.state else None
                if session.origin in ('cli', 'ide') and (state.get('hostId', self.native_host) != self.native_host or
                        not session.saved_cwd or not session.saved_provider or
                        state.get('modelProvider') != session.saved_provider or
                        not (previous_cwd and previous_cwd == state.get('cwd') or
                             same_cwd(session.saved_cwd, state.get('cwd'), self.local and sys.platform == 'win32'))):
                    reason = 'owner_identity_mismatch'
                    raise ValueError("CLI owner identity mismatch")
                newly_connected = not session.connected or session.runtime != 'desktop'
                session.state = state
                session.revision = change["revision"]
                session.runtime = 'desktop'
                session.discovering = False
                session.connected = True
                session.rebuild_recommended = False
                session.desktop_restart_recommended = False
                session.error = None
                session.access = self._access(session.origin, session)
                session.changed()
                if newly_connected:
                    logging.getLogger(__name__).info(
                        "Codex connection transition thread=%s cause=desktop_owner_attached", session.id)
                with self.lock:
                    self.notification_dirty.add(session.id)
                self.notification_event.set()
            except (ValueError, KeyError, IndexError, TypeError):
                logging.getLogger(__name__).info(
                    "Codex connection transition thread=%s cause=%s", session.id, reason)
                session.connected = False
                session.revision = None
                session.error = "现有 Codex App 连接与此会话不匹配，未连接" if session.origin in ('cli', 'ide') else "同步状态发生变化，正在重新读取桌面快照"
                session.changed()

    def _invalidate(self, session, message, runtime=None, owner=None, reason=None):
        with session.condition:
            if (runtime is not None and session.runtime != runtime) or (owner is not None and session.owner != owner):
                return
            if session.connected and reason:
                logging.getLogger(__name__).info(
                    "Codex connection transition thread=%s cause=%s", session.id, reason)
            session.connected = False
            session.connection_epoch += 1
            if reason in ('desktop_ipc_disconnected', 'desktop_client_disconnected'):
                # The old route's successful read is no longer a reason to
                # delay recovery. Allow two ordinary maintenance passes while
                # the native router and original owners register again.
                session.retry_at = 0
                session.desktop_recovery_until = time.monotonic() + 6
            session.rebuild_recommended = session.origin == 'desktop' and self.host == 'local'
            session.revision = None
            if session.origin in ('cli', 'ide') and session.runtime == 'desktop':
                session.owner = None
                session.desktop_owner_probe = None
            session.error = message
            session.access = self._access(session.origin, session)
            session.changed()

    def _disconnected(self):
        self.notification_cursor = 0
        self.notification_boundary.clear()
        self.notification_event.set()
        with self.lock:
            sessions = list(self.live.values())
        for session in sessions:
            if session.origin not in ('cli', 'ide') or session.desktop_owner_probe == 'found':
                self._invalidate(session, "桌面 App 连接已断开，正在等待重连", runtime='desktop',
                                 reason='desktop_ipc_disconnected')

    def suspend_observation(self):
        """Release only desktop followers; native/CLI tasks and saved state survive."""
        with self.ipc.connect_lock:
            self.ipc.close()
        for source in list(self.remote_bridges.values()):
            source.suspend_observation()

    def _maintain(self):
        delay = 3
        while not self.closed.wait(delay):
            if not self.observation_enabled():
                continue
            if isinstance(self.store, RemoteStore):
                current = self.hosts.hosts(cached=True).get(self.host)
                if current and current['alias'] == self.store.alias:
                    retry_ssh(self.store.alias, manual=False)
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
                            with session.condition:
                                now = time.monotonic()
                                session.retry_at = now + (.5 if now < session.desktop_recovery_until else 15)
                            self._attach(session, timeout=0)
                    else:
                        self._refresh_async(session)
                elif session.viewers == 0 and not queued and not session.watched and time.monotonic() - session.touched > 300:
                    with self.lock:
                        if session.viewers != 0 or session.watched:
                            continue
                        self.live.pop(session.id, None)
                    if (session.connected or session.discovering) and session.runtime != "cli":
                        try:
                            self.ipc.follow(session.id, session.owner, False, host=self.native_host)
                        except IPCError:
                            pass
            recovering = any(not session.connected and time.monotonic() < session.desktop_recovery_until
                             for session in sessions)
            delay = 3 if self.ipc.client_id or recovering else min(30, delay * 2)

    def _target(self, thread_id, *, defer_cli_account=False):
        if self.released_cli.contains(thread_id):
            raise IPCError('CLI 连接已释放，请点击继续会话重新连接')
        options = {'_defer_cli_account': True} if defer_cli_account else {}
        session = self.activate(thread_id, allow_ide_resume=False, **options)
        with session.condition:
            if not session.connected or not session.owner:
                raise IPCError(session.error or "请先在桌面 App 打开此聊天")
        return session

    @operation
    def _call(self, session, method, params, timeout=30):
        # The existing desktop owner retains this thread's provider and credentials.
        # A newly selected default account must not gate unrelated existing providers.
        if session.runtime == "cli":
            try:
                return self.cli_sessions.call(session.id, method, params, timeout=timeout)
            except AppServerError as exc:
                raise IPCError(str(exc)) from exc
        try:
            return self.ipc.request(method, {"conversationId": session.id, **params}, target=session.owner, host=self.native_host, timeout=timeout)["result"]
        except IPCError as exc:
            if str(exc) in ('no-client-found', 'client-disconnected', 'server-closed', 'request-timeout') or str(exc).startswith(('桌面连接已断开', '桌面响应超时')):
                self._invalidate(session, '桌面执行连接不可用，可点击“重建连接”恢复；未确认操作不会自动重发。',
                                 runtime='desktop', reason='desktop_owner_unavailable')
            raise

    def _save_ledger(self):
        # Keep accepted or uncertain submissions durable across process restarts.
        target = self.ledger_path.with_suffix(".tmp")
        target.write_text(json.dumps(self.submissions, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.ledger_path)

    def _draft_account_identity(self, view):
        provider = view.get('provider')
        if not isinstance(provider, str) or not provider:
            return ''
        if not self.local:
            alias = self.cli_sessions.alias
            if not alias:
                return ''
            home = str(self.cli_sessions.remote_home) if self.cli_sessions.remote_home else None
            key = (self.host, alias, home, provider)
            with self._draft_remote_lock:
                cached = self._draft_remote_cache.get(key)
                if cached and time.monotonic() - cached[0] < 30:
                    return cached[1]
                if key in self._draft_remote_pending:
                    return cached[1] if cached else ''
                self._draft_remote_pending.add(key)
            threading.Thread(target=self._probe_remote_draft_identity,
                             args=(key, alias, home, provider), daemon=True,
                             name='remote-draft-identity').start()
            return cached[1] if cached else ''
        try:
            if provider in ('openai', 'bridge_api') and self.accounts:
                with self.accounts.lock:
                    if self.accounts.active_matches():
                        row = self.accounts.row(self.accounts.index['activeId'])
                        if row['kind'] == 'api':
                            key = json.loads((self.accounts.directory(row['id'])/'api.json').read_text(encoding='utf-8')).get('key')
                            return draft_identity('codex', self.host, provider, row['id'], row['baseUrl'], key) if key else ''
                        return draft_identity('codex', self.host, provider, 'chatgpt', row['id'])
            owner = codex_account_identity(self.codex_home, provider)
            return draft_identity('codex', self.host, provider, owner) if owner else ''
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return ''

    def _probe_remote_draft_identity(self, key, alias, home, provider):
        try:
            source = source_text('features/accounts/draft_identity.py')
            source += '\nimport json, os\nfrom pathlib import Path\n'
            source += 'request = ' + payload({'home': home, 'provider': provider}) + '\n'
            source += 'home = request["home"] or os.environ.get("CODEX_HOME") or str(Path.home()/".codex")\n'
            source += 'print(json.dumps({"identity": codex_account_identity(home, request["provider"])}))\n'
            owner = ssh_read(alias, source, timeout=12).get('identity')
            identity = draft_identity('codex', key[0], provider, owner) if owner else ''
        except (OSError, ValueError, RemoteUnavailable, KeyError, TypeError):
            identity = ''
        with self._draft_remote_lock:
            self._draft_remote_cache[key] = (time.monotonic(), identity)
            self._draft_remote_pending.discard(key)

    def view(self, thread_id, attach=True, background=False, force=False):
        session = self.session(thread_id, attach=attach, background=background, force=force)
        goal_available = self._refresh_session_goal(session)
        with session.condition:
            session.access = self._access(session.origin, session)
        view = session.view()
        if linux_permissions.enabled(self.native_host):
            view["linuxPermissionChecks"] = True
            with session.condition:
                if session.connected:
                    view["permissionMode"] = linux_permissions.current_mode(session.state)
        view["host"] = self.host
        view['draftAccountIdentity'] = self._draft_account_identity(view)
        view["hostLabel"] = self.host_label
        if self.host == 'local' and session.origin not in ('cli', 'ide'):
            cwd = view.get('cwd')
            provider = view.get('provider')
            if cwd:
                try:
                    identity = (self.catalog_reader.model_identity(cwd, provider) if provider else
                                self.catalog_reader.model_identity(cwd, provider, require_verified=True))
                    if identity:
                        view['modelCatalogIdentity'] = identity
                except (OSError, ValueError, KeyError):
                    pass
        view["goalTransport"] = 'cli' if session.connected and session.runtime == 'cli' else self.goal_transport
        view["goalRuntimeAvailable"] = goal_available
        view["goalActivationIds"] = sorted(self._goal_activation_ids(thread_id))
        with session.condition:
            source = session.state if session.connected else session.history_state or session.state
            artifacts = artifact_paths(source or {}, self.store.home) if self.local else {}
        view["files"] = [{"id": k, "name": v["name"], "reference": v["reference"], "references": v["references"], "image": v["image"]} for k, v in artifacts.items()]
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

    def _reconcile_submissions(self, session):
        with self.submit_lock:
            if not any(k.startswith(session.id + ':') and v.get('status') == 'unknown'
                       for k, v in self.submissions.items()):
                return
        with session.condition:
            saved = session.history_state
            if saved and saved.get('hostId', self.native_host) != self.native_host:
                return
            identifiers = persisted_submission_ids(saved, session.id)
        if not identifiers:
            return
        with self.submit_lock:
            changed = [self.submissions[session.id + ':' + identifier] for identifier in identifiers
                       if self.submissions.get(session.id + ':' + identifier, {}).get('status') == 'unknown']
            if not changed:
                return
            for entry in changed:
                entry['status'] = 'accepted'
            try:
                self._save_ledger()
            except Exception:
                for entry in changed:
                    entry['status'] = 'unknown'
                raise
        with session.condition:
            session.changed()

    def _submission_meta(self, session, view):
        self._reconcile_submissions(session)
        self._refresh_saved_async(session, receipts=True)
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
                self.local and native_goal_absent(self.codex_home, session.id)):
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

    @operation(entry_only=True)
    def workspace(self, thread_id, action, params):
        uuid.UUID(thread_id)
        # Read project metadata only: file browsing never activates a Codex owner.
        root = self.store.get(thread_id).get('cwd')
        if self.local:
            return workspace_operation(root, action, params)
        if not isinstance(self.store, RemoteStore):
            raise RemoteUnavailable('此主机暂不支持文件浏览')
        source = source_text('features/workspace/workspace.py')
        if action in ('download-stream', 'archive-stream'):
            source += '\nimport json, sys\ntry:\n with operate(**' + payload({'root': root, 'action': action, 'params': params}) + ') as (metadata, stream):\n  sys.stdout.buffer.write(json.dumps(metadata).encode()+b"\\n")\n  while True:\n   chunk=stream.read(65536)\n   if not chunk: break\n   sys.stdout.buffer.write(chunk)\nexcept (ValueError, PermissionError, OSError) as error:\n sys.stdout.buffer.write(json.dumps({"error": str(error), "permission": isinstance(error, PermissionError)}).encode()+b"\\n")\n'
            return ssh_download(self.store.alias, source)
        source += '\nimport json\ntry:\n result={"value": operate(**' + payload({'root': root, 'action': action, 'params': params}) + ')}\nexcept (ValueError, PermissionError) as error:\n result={"error": str(error)}\nprint(json.dumps(result, ensure_ascii=False))\n'
        result = ssh_read(self.store.alias, source, timeout=60)
        if 'error' in result: raise ValueError(result['error'])
        return result['value']

    def subagents(self, thread_id, agent_id=None):
        uuid.UUID(thread_id)
        if not agent_id: return self.store.subagents(thread_id)
        uuid.UUID(agent_id)
        state = self.store.subagent_history(thread_id, agent_id)
        view = normalize_state(state, False, image_cache=TOOL_IMAGE_CACHE)
        # Saved logs are evidence of the last recorded state, not a live heartbeat.
        # Subagent screenshots have no preview route; omit their descriptors.
        return {'id': agent_id, 'title': view['title'], 'turns': public_turns(view['turns'], image_previews=False),
                'historyComplete': view['historyComplete'], 'saved': True}

    @operation(entry_only=True)
    def terminal(self, thread_id, owner, action, params):
        uuid.UUID(thread_id)
        if action == 'info':
            root = self.store.get(thread_id).get('cwd')
            from bridge.platforms.posix.terminal import default_shell
            return {'cwd': root, 'host': self.host, 'mode': 'pty',
                    'shell': default_shell() if self.local else None}
        identifier = params.get('id', '')
        if action == 'open':
            root = self.store.get(thread_id).get('cwd')
            return self.terminals.open_session(owner, thread_id, identifier, root, params.get('cols'), params.get('rows'),
                                                self.store.alias if isinstance(self.store, RemoteStore) else None)
        if action in ('input', 'resize', 'close', 'poll', 'complete'):
            session = self.terminals.session(owner, thread_id, identifier)
            if action == 'input': return session.write(params.get('inputId'), params.get('data'))
            if action == 'resize': return session.resize(params.get('cols'), params.get('rows'))
            if action == 'close': session.stop(); return {'closed': True}
            if action == 'complete':
                from bridge.features.terminals.completion import complete_path
                return (session.complete(params.get('text'), params.get('selectionStart'))
                        if hasattr(session, 'complete') else complete_path(session, params.get('text'), params.get('selectionStart')))
            after = params.get('after', 0)
            if type(after) is not int or not 0 <= after <= 10**12: raise ValueError('无效输出游标')
            wait_ms = params.get('waitMs', 0)
            if type(wait_ms) is not int or not 0 <= wait_ms <= 1000: raise ValueError('无效等待时间')
            return session.read(after, wait_ms)
        if action == 'start':
            root = self.store.get(thread_id).get('cwd')
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
            source = session.state if session.connected else session.history_state or session.state
            files = artifact_paths(source or {}, self.store.home) if self.local else {}
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
                elif self.local:
                    attachment['desktopId'] = hashlib.sha256(resolved.encode('utf-8')).hexdigest()
                if not attachment.get('name'):
                    attachment['name'] = Path(path).name
                # Preview metadata is enough for the browser. The private upload
                # path must never cross the authenticated web API boundary.
                attachment.pop('path', None)

    def desktop_image_preview(self, thread_id, image_id):
        """Serve an image evidenced by this thread, without activating its owner."""
        if not re.fullmatch(r'[0-9a-f]{64}', image_id):
            raise ValueError('桌面图片标识无效')
        session = self.session(thread_id, attach=False)
        attachment = session.tool_image(image_id)
        if attachment is not None:
            raw = session.tool_image_cache.preview(attachment)
            # A patch may have removed/replaced the image while decoding outside
            # the session lock. Cached bytes alone never authorize a preview.
            if raw is not None and session.tool_image(image_id) == attachment:
                return {'previewBytes': raw, 'previewMime': attachment['mime'],
                        'previewSha256': image_id, 'name': attachment['name']}, 'tool'
        paths = []
        with session.condition:
            source = (session.state if session.connected else session.history_state or session.state) or {}
            for turn in ordered_turns(source):
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
            paths.extend(referenced_model_images(source, image_views_only=True))
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
        if not self.local or session.runtime == "cli":
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
        goal_available = self._refresh_session_goal(session)
        # Expand saved history only when the reader reaches its loaded edge.
        if mode == 'page' and options.get('before'):
            with session.condition:
                position = session.timeline.position(options['before'])
                near_edge = position is not None and position < options.get('limit', 20)
                expand = near_edge and not session.timeline.window_more and session.saved_view and not session.saved_view['historyComplete']
                if near_edge:
                    session.timeline_limit += max(1, min(100, options.get('limit', 20)))
                if expand:
                    session.history_limit += 5
            if expand:
                saved = self._saved_history(session)
                with session.condition:
                    session.set_history(saved)
        with session.condition:
            session.access = self._access(session.origin, session)
            projection = session.timeline
            authoritative_view = self._overlay_native_goal(session, thread_id, session.timeline_view())
            authoritative_view['access'] = dict(session.access)
            projection.update(authoritative_view, limit=session.timeline_limit)
            # Goals/access may change independently of the history revision.
            projection.meta.update(goal=authoritative_view.get('goal'), access=authoritative_view['access'])
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
                                      hostLabel=self.host_label,
                                      goalRuntimeAvailable=goal_available)
                if linux_permissions.enabled(self.native_host):
                    result['meta']['linuxPermissionChecks'] = True
                    if session.connected:
                        result['meta']['permissionMode'] = linux_permissions.current_mode(session.state)
                texts = [row['text'] for row in result['rows'] if row['role'] == 'assistant']
            cwd = authoritative_view.get('cwd')
            # Resolve only evidence in the displayed window, not the entire
            # native snapshot on every poll.
            media_items = [{'type': 'ImageView', 'path': attachment['path']}
                           for turn in authoritative_view['turns'] for row in turn['messages']
                           for attachment in row.get('attachments', [])
                           if attachment.get('type') == 'localImage' and attachment.get('path')]
        # Resolve links in the delivered page, plus runtime ImageView evidence
        # retained separately from the paged message text.
        if mode != 'detail':
            result['meta']['draftAccountIdentity'] = self._draft_account_identity(authoritative_view)
        file_state = {'cwd': cwd, 'turns': [
            {'items': [{'type': 'agentMessage', 'text': text} for text in texts]},
            {'items': media_items},
        ]}
        artifacts = artifact_paths(file_state, self.store.home) if self.local else {}
        result['files'] = [{'id': k, 'name': v['name'], 'reference': v['reference'], 'references': v['references'], 'image': v['image']} for k, v in artifacts.items()]
        if mode != 'detail':
            self._submission_meta(session, result['meta'])
            result['meta']['forkedFrom'] = self._fork_origin(thread_id)
        return result

    def _native_model_identity(self, cwd, provider):
        request = self.cli_sessions.client.request
        config = request('config/read', {'includeLayers': False, 'cwd': cwd}, replay_safe=True).get('config') or {}
        if not isinstance(config, dict):
            config = {}
        try:
            account = request('account/read', {'refreshToken': False}, replay_safe=True).get('account') or {}
        except AppServerError:
            account = {}
        if not isinstance(account, dict):
            account = {}
        try:
            local_auth = hashlib.sha256((self.codex_home/'auth.json').read_bytes()).hexdigest() if self.local else None
        except OSError:
            local_auth = None
        runtime_home = (self.cli_sessions.remote_home if self.cli_sessions.alias is not None
                        else self.codex_home)
        profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
        if not isinstance(profile, dict):
            profile = {}
        effective = {**config, **profile}
        provider = provider or effective.get('model_provider') or 'openai'
        configured_catalog = (bool(effective.get('model_catalog_json')) and
                              provider == (effective.get('model_provider') or 'openai'))
        environment_identity = None
        if (hasattr(self.cli_sessions, 'model_environment_identity') and
                not configured_catalog and (Catalog.custom_provider(effective, provider) or
                                            account.get('type') == 'apiKey')):
            try:
                environment_identity = self.cli_sessions.model_environment_identity(config, provider, account, cwd=cwd)
            except (DaemonEnvironmentUnavailable, CatalogError):
                environment_identity = 'unavailable'
        return ModelStore.key('codex-cli-models-v3', self.host, self.cli_sessions.alias,
                              str(runtime_home), cwd, provider, config, account, local_auth,
                              environment_identity)

    def _schedule_native_model_refresh(self, key, thread_id, cwd, provider, updated_at, replace=False):
        if thread_id is None or not self.observation_enabled():
            return
        with self.native_model_lock:
            if replace and key in self.native_model_timers:
                self.native_model_timers.pop(key).cancel()
            if self.closed.is_set() or key in self.native_model_timers:
                return
            delay = max(1, updated_at + MODEL_REFRESH_SECONDS - time.time())
            def due():
                with self.native_model_lock:
                    self.native_model_timers.pop(key, None)
                    if key in self.native_model_refreshing or self.closed.is_set() or not self.observation_enabled():
                        return
                    self.native_model_refreshing.add(key)
                try:
                    if self._native_model_identity(cwd, provider) == key:
                        self._native_models(thread_id, cwd, provider, refresh=True)
                except (OSError, ValueError, AppServerError):
                    pass
                finally:
                    with self.native_model_lock:
                        self.native_model_refreshing.discard(key)
                    if not self.closed.is_set() and self.observation_enabled():
                        try:
                            record = self.native_model_store.read(key)
                        except (OSError, sqlite3.DatabaseError, ValueError):
                            record = None
                        if record:
                            self._schedule_native_model_refresh(key, thread_id, cwd, provider,
                                                                max(record[0], time.time()-MODEL_REFRESH_SECONDS+300))
            timer = threading.Timer(delay, due)
            timer.daemon = True
            self.native_model_timers[key] = timer
            timer.start()

    def _native_models(self, thread_id, cwd, provider, refresh=False):
        stable = ModelStore.directory

        identity = self._native_model_identity(cwd, provider)
        try:
            persisted = self.native_model_store.read(identity)
        except (OSError, sqlite3.DatabaseError, ValueError):
            persisted = None
        if persisted and not refresh:
            self._schedule_native_model_refresh(identity, thread_id, cwd, provider, persisted[0])
            return {**stable(persisted[1]), 'cache': {'state': 'fresh' if time.time()-persisted[0] < MODEL_REFRESH_SECONDS else 'stale',
                                                    'updatedAt': persisted[0], 'identity': identity}}
        with self.native_model_lock:
            lock = self.native_model_reads.setdefault(identity, threading.Lock())
        with lock:
            if not refresh:
                try:
                    persisted = self.native_model_store.read(identity)
                except (OSError, sqlite3.DatabaseError, ValueError):
                    persisted = None
                if persisted:
                    return {**stable(persisted[1]), 'cache': {'state': 'fresh', 'updatedAt': persisted[0], 'identity': identity}}
            try:
                value = (self.cli_sessions.model_catalog(cwd, provider) if thread_id is None else
                         self.cli_sessions.get_kind(thread_id, 'models', refresh=refresh,
                                                    query='', offset=0, limit=200, ids=None))
            except (OSError, ValueError, AppServerError):
                if persisted:
                    return {**stable(persisted[1]), 'modelError': '模型列表刷新失败，已保留上次结果',
                            'cache': {'state': 'stale', 'updatedAt': persisted[0], 'identity': identity}}
                raise
            if self._native_model_identity(cwd, provider) != identity:
                raise ValueError('账号身份已变化，请重新读取模型列表')
            if not value.get('modelError'):
                try:
                    updated_at = self.native_model_store.write(identity, stable(value))
                    self._schedule_native_model_refresh(identity, thread_id, cwd, provider, updated_at, replace=True)
                except (OSError, sqlite3.DatabaseError):
                    updated_at = time.time()
            elif persisted:
                return {**stable(persisted[1]), 'modelError': value['modelError'],
                        'cache': {'state': 'stale', 'updatedAt': persisted[0], 'identity': identity}}
            if self._native_model_identity(cwd, provider) != identity:
                raise ValueError('账号身份已变化，请重新读取模型列表')
            return {**value, 'cache': {'state': 'fresh', 'updatedAt': updated_at, 'identity': identity}} if not value.get('modelError') else value

    def catalog(self, thread_id, refresh=False, kind=None, query='', offset=0, limit=200, ids=None):
        session = self.session(thread_id, attach=False, background=True)
        if session.origin not in ('cli', 'ide'):
            session = self.session(thread_id, attach=False)
        with session.condition:
            state = session.state if session.connected else session.history_state or session.state or {}
            native = session.origin in ('cli', 'ide') and session.runtime == 'cli' and session.connected
            if session.origin in ('cli', 'ide') and not session.connected:
                raise ValueError('请先继续此会话，再读取模型或 Skill')
            cwd = state.get("cwd") if session.origin not in ('cli', 'ide') else state.get("cwd") or session.saved_cwd
            model = state.get("latestModel")
            effort = state.get("latestReasoningEffort") or (state.get("latestThreadSettings") or {}).get("effort")
            provider = state.get('modelProvider')
        if native:
            if kind in ('models', 'skills'):
                value = (self._native_models(thread_id, cwd, provider, refresh=refresh) if kind == 'models' else
                         self.cli_sessions.get_kind(thread_id, kind, refresh=refresh, query=query,
                                                    offset=offset, limit=limit, ids=ids))
                return {**value, 'kind': kind, **({'currentModel': model or value.get('currentModel'),
                        'currentEffort': effort or value.get('currentEffort')} if kind == 'models' else {})}
            models = self._native_models(thread_id, cwd, provider, refresh=refresh)
            skills = self.cli_sessions.get_kind(thread_id, 'skills', refresh=refresh)
            return {**models, **skills, 'currentModel': model or models.get('currentModel'),
                    'currentEffort': effort or models.get('currentEffort')}
        if kind in ('models', 'skills'):
            value = self.catalog_reader.get_kind(
                kind, cwd, refresh=refresh, provider=provider,
                **({'query':query, 'offset':offset, 'limit':limit, 'ids':ids} if kind == 'skills' else {}))
            if kind == 'models':
                with session.condition:
                    view = session.view()
                return {**value, 'kind': 'models', 'currentModel': model or value.get('currentModel'),
                        'currentEffort': effort or value.get('currentEffort'),
                        'fastMode': {**value.get('fastMode', {}), 'allowed': view.get('provider') == value.get('fastMode', {}).get('provider', 'openai') and value.get('fastMode', {}).get('allowed') is True},
                        **({'currentServiceTier': view['serviceTier']} if 'serviceTier' in view else {})}
            return {**value, 'kind': 'skills'}
        catalog = self.catalog_reader.get(cwd, refresh=refresh, provider=provider)
        with session.condition:
            view = session.view()
        return {**catalog, "currentModel": model or catalog.get("currentModel"),
                "currentEffort": effort or catalog.get("currentEffort"),
                'fastMode': {**catalog.get('fastMode', {}), 'allowed': view.get('provider') == catalog.get('fastMode', {}).get('provider', 'openai') and catalog.get('fastMode', {}).get('allowed') is True},
                **({'currentServiceTier': view['serviceTier']} if 'serviceTier' in view else {})}

    def _resolve_skills(self, session, skills):
        if not isinstance(skills, list) or len(skills) > 8 or not all(isinstance(s, str) for s in skills):
            raise ValueError("最多选择 8 个 Skill")
        if not skills:
            return []
        with session.condition:
            cwd = session.state.get("cwd") or str(self.codex_home)
        identifiers = sorted(set(skills))
        rows = (self.cli_sessions.validate_skills(session.id, identifiers)
                if session.origin in ('cli', 'ide') and session.runtime == 'cli'
                else self.catalog_reader.validate_skills(cwd, identifiers))
        by_id = {row["id"]: row for row in rows}
        selected = []
        for key in identifiers:
            skill = by_id.get(key)
            if not skill:
                raise ValueError("Skill 不可用，请刷新列表")
            selected.append({"id": key, "name": skill["name"], "path": skill["path"]})
        return selected

    @operation
    def archive(self, thread_id, archived, request_id, disconnect=False):
        if type(disconnect) is not bool or (disconnect and archived is not True):
            raise ValueError('聊天归档设置无效')
        def read():
            if isinstance(self.store, RemoteStore):
                with self.store.lock:
                    self.store.cache.clear()
            metadata = self.store.get(thread_id)
            metadata['_archiveFlags'] = self.store.archive_flags(thread_id)
            return metadata
        def prepare(metadata):
            identifiers = [row['id'] for row in self.store.lifecycle_threads(root_id=thread_id)]
            if not (disconnect and metadata.get('origin') in ('cli', 'ide')):
                self.assert_desktop_idle(identifiers=identifiers, allow_missing_ipc=metadata.get('origin') in ('cli', 'ide'),
                                         allow_stopped_app=metadata.get('origin') == 'desktop')
            with self.submit_lock:
                ledgers = (self.submissions, self.goal_commands, self.message_actions)
                if any((key.split(':')[0] in identifiers or row.get('source') in identifiers or row.get('id') in identifiers) and row.get('status', row.get('state')) in
                       ('queued', 'unknown', 'pending', 'sent') for ledger in ledgers for key, row in ledger.items()):
                    raise ValueError('聊天有排队或待核对操作，请先在原客户端处理')
            if metadata.get('origin') in ('cli', 'ide'):
                def check_scope():
                    if set(identifiers) != {row['id'] for row in self.store.lifecycle_threads(root_id=thread_id)}:
                        raise ValueError('子会话范围已变化，尚未发送归档请求')
                return self.cli_sessions.archive_call(thread_id, archived, identifiers,
                    **({'disconnect': True, 'check_scope': check_scope} if disconnect else {}))
            def call():
                if isinstance(self.store, RemoteStore):
                    source = source_text('features/sessions/create.py')
                    source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                    source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                    source += 'archive_thread(runtime, home, **' + payload({'thread_id': thread_id, 'archived': archived, 'identifiers': identifiers}) + ')\nprint(json.dumps({"ok":True}))\n'
                    ssh_read(self.store.alias, source, timeout=90)
                else:
                    archive_thread(self.catalog_reader.executable, self.codex_home, thread_id, archived, identifiers, **_runtime_scope(self.host))
            return call
        result = self.archive_commands.perform(thread_id, archived, request_id, read, prepare)
        return {**result, 'host': self.host}

    @operation
    def deletion(self, thread_id, request_id=None, expected=None, retry=None):
        uuid.UUID(thread_id)
        if isinstance(self.store, RemoteStore):
            raise ValueError('此 SSH 接入暂不支持永久删除，请在原客户端操作')
        read = lambda: deletion_scope(self.store, thread_id)
        if request_id is None:
            return self.delete_commands.preview(read, self.store.existing_ids)

        try:
            parent = (self.store.attached_parent(thread_id) or {}).get('id')
        except KeyError:
            parent = None  # A retry may only be reading an already confirmed receipt.

        def prepare(snapshot):
            identifiers = snapshot['scope']
            metadata = self.store.get(thread_id)
            with self.submit_lock:
                ledgers = (self.submissions, self.goal_commands, self.message_actions)
                if any((key.split(':')[0] in identifiers or row.get('source') in identifiers or row.get('id') in identifiers)
                       and row.get('status', row.get('state')) in ('queued', 'unknown', 'pending', 'sent')
                       for ledger in ledgers for key, row in ledger.items()):
                    raise ValueError('聊天有排队或待核对操作，请先处理后再删除')
            if metadata.get('origin') in ('cli', 'ide'):
                return self.cli_sessions.delete_call(snapshot, read)
            self.assert_desktop_idle(identifiers=identifiers, allow_stopped_app=True)
            def call():
                from bridge.features.sessions.deletion import DeleteNotSent
                try:
                    return self._desktop_metadata_operation(str(self.codex_home),
                        lambda request: delete_native(request, snapshot, read),
                        '删除结果待核对，请检查原聊天；不会重复提交')
                except CreationUnavailable as exc:
                    raise DeleteNotSent(str(exc)) from exc
            return call

        result = self.delete_commands.perform(thread_id, request_id, expected, read, self.store.existing_ids, prepare, retry=retry)
        if result.get('status') == 'confirmed':
            self._invalidate_attached_threads(parent)
        return {**result, 'host': self.host}

    @operation
    def pin(self, thread_id, pinned, client_id=None, revision=None):
        uuid.UUID(thread_id)
        return {**self.session_pins.set(thread_id, pinned, client_id, revision), 'host': self.host}

    @operation
    def rename(self, thread_id, title):
        uuid.UUID(thread_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or any(unicodedata.category(c) in ('Cc', 'Zl', 'Zp') for c in title):
            raise ValueError('聊天名称需为 1–120 个字符，不能包含换行或控制字符')
        title = title.strip()
        self._require_desktop_feature(thread_id)
        session = self.session(thread_id, attach=False, background=True)
        with session.action_lock:
            if isinstance(self.store, RemoteStore):
                source = source_text('features/sessions/create.py')
                source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                source += 'rename_thread(runtime, home, **' + payload({'thread_id': thread_id, 'title': title}) + ')\nprint(json.dumps({"ok":True}))\n'
                ssh_read(self.store.alias, source, timeout=90)
                with self.store.lock:
                    self.store.cache.clear()
            else:
                rename_thread(self.catalog_reader.executable, self.codex_home, thread_id, title, **_runtime_scope(self.host))
            with session.condition:
                if session.state is not None:
                    session.state['title'] = title
                if session.saved_view is not None:
                    session.saved_view['title'] = title
                session.changed()
        return {'id': thread_id, 'host': self.host, 'title': title}

    @operation
    def organization(self, thread_id, change=None):
        from bridge.features.sessions.create import _runtime_operation
        uuid.UUID(thread_id)
        metadata = self.store.get(thread_id)
        session = self.session(thread_id, attach=False, background=True)
        def execute(change):
            if isinstance(self.store, RemoteStore):
                source = source_text('features/sessions/create.py') + '\n' + source_text('features/sessions/organization.py')
                source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
                source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
                arguments = payload({'identifier': thread_id, 'change': change, 'expected_cwd': metadata.get('cwd')})
                source += 'try:\n result=_runtime_operation(runtime,home,str(home),lambda request:organization(request,**' + arguments + '),failure_message="原生分类结果待核对，请刷新会话列表")\nexcept (CreationError,ValueError) as error:\n result={"error":str(error),"notSent":isinstance(error,(OrganizationNotSent,CreationUnavailable))}\nprint(json.dumps(result))\n'
                result = ssh_read(self.store.alias, source, timeout=90)
                if result.get('error'):
                    raise (OrganizationNotSent if result.get('notSent') else ValueError)(result['error'])
                if change is not None:
                    with self.store.lock:
                        self.store.cache.clear()
            else:
                result = _runtime_operation(self.catalog_reader.executable, self.codex_home, str(self.codex_home),
                    lambda request: native_organization(request, thread_id, change, expected_cwd=metadata.get('cwd')),
                    failure_message='原生分类结果待核对，请刷新会话列表', **_runtime_scope(self.host))
            return result
        with session.action_lock:
            result = execute(None) if change is None else self.organization_commands.perform(
                thread_id, change, lambda: execute(None), execute)
        return {**result, 'host': self.host}

    def _linux_permission_options(self, session):
        with session.condition:
            cwd = (session.state or {}).get('cwd') or str(self.codex_home)
            current = linux_permissions.current_mode(session.state)
        try:
            facts = self.catalog_reader.linux_permission_capabilities(cwd, linux_permissions.read_permission_facts)
        except Exception:
            facts = {}
        preferences = linux_permissions.desktop_permission_preferences(self.codex_home)
        return facts, linux_permissions.permission_options(facts, preferences, current)

    @operation
    def linux_permission_options(self, thread_id):
        session = self.session(thread_id, attach=False, background=True)
        if session.connected and session.runtime == 'cli':
            return self.cli_sessions.permission_options(thread_id)[1]
        if not linux_permissions.enabled(self.native_host):
            raise ValueError('此主机未启用 Linux 权限检查')
        self.store.get(thread_id)
        # Picker inspection must not activate the desktop or change the owner.
        return self._linux_permission_options(session)[1]

    @operation
    def permissions(self, thread_id, preset, confirmed=False, *, expected_runtime=None):
        if preset not in ('ask', 'auto-review', 'full-access'):
            raise ValueError('权限设置无效')
        if expected_runtime is not None and expected_runtime not in ('cli', 'desktop'):
            raise ValueError('会话运行时无效')
        if preset == 'full-access' and confirmed is not True:
            raise ValueError('请确认完全访问权限')
        session = self._target(thread_id)
        if not self._access(session.origin, session)['canChangePermissions']:
            raise ValueError('此会话暂不能修改权限')
        with session.action_lock:
            if expected_runtime is not None and (not session.connected or session.runtime != expected_runtime):
                raise ValueError('会话运行时已变化，请重新打开权限面板')
            if session.runtime == 'cli':
                facts, options = self.cli_sessions.permission_options(thread_id)
                linux_permissions.require_permission(options, preset)
                settings = linux_permissions.permission_settings(preset, facts)
                with session.condition:
                    before = session.sequence
                self._call(session, 'thread-follower-update-thread-settings', {'threadSettings': settings})
                with session.condition:
                    applied = session.condition.wait_for(lambda:
                        session.sequence > before and
                        linux_permissions.current_mode(session.state) == preset, timeout=5)
                return {'applied': True, 'confirmed': applied, 'permissionMode':
                        linux_permissions.current_mode(session.state)}
            profile = ':danger-full-access' if preset == 'full-access' else ':workspace'
            settings = {'approvalPolicy': 'never' if preset == 'full-access' else 'on-request',
                        'approvalsReviewer': 'auto_review' if preset == 'auto-review' else 'user',
                        'permissions': profile, 'activePermissionProfile': {'id': profile, 'extends': None}}
            if linux_permissions.enabled(self.native_host):
                facts, options = self._linux_permission_options(session)
                linux_permissions.require_permission(options, preset)
                settings = linux_permissions.permission_settings(preset, facts)
                settings['activePermissionProfile'] = {'id': settings['permissions'], 'extends': None}
            # Desktop owner validates feature/policy support. Never silently fall back.
            result = self._call(session, 'thread-follower-update-thread-settings', {'threadSettings': settings})
            if not result.get('applied'):
                raise IPCError('桌面未应用权限设置，请刷新后重试')
        with session.condition:
            confirmed = session.condition.wait_for(lambda: (linux_permissions.current_mode(session.state)
                if linux_permissions.enabled(self.native_host) else session.view().get('permissionMode')) == preset, timeout=5)
        return {'applied': True, 'confirmed': confirmed, 'permissionMode': preset}

    @operation
    def settings(self, thread_id, model, effort, *, fast_mode=None):
        if not valid_model_id(model):
            raise ValueError("模型 ID 格式不正确")
        if effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            raise ValueError("请选择有效的推理强度")
        if fast_mode is not None and not isinstance(fast_mode, bool):
            raise ValueError('Fast 模式开关无效')
        session = self._target(thread_id)
        return self._apply_settings(session, model, effort, fast_mode=fast_mode)

    def _apply_settings(self, session, model, effort, *, fast_mode=None):
        # Callers already hold the host gate and resolved/activated this session.
        # Re-entering _target under action_lock reverses release's lock order.
        if not self._access(session.origin, session)['canChangeModel']:
            raise ValueError('此会话暂不能修改模型设置')
        with session.action_lock:
            # Recheck login/model/policy on the execution host before a speed change.
            catalog = self.catalog(session.id, refresh=fast_mode is not None, kind='models')
            known = next((m for m in catalog["models"] if m["id"] == model), None)
            if catalog.get('modelSource') == 'codex' and (not known or not known.get('efforts')):
                raise ValueError('未能读取此模型的推理能力，请重新打开模型设置')
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
            raise IPCError("Codex 未应用模型设置，请刷新后重试")
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
                                   work_mode="default",
                                   activation={"action": action, "objective": goal["objective"],
                                               "uiLocale": ui_locale, "goal": goal_identity(goal)})
        except Exception as exc:
            raise IPCError(f"目标状态已确认，但启动消息发送失败：{exc}") from exc
        if activation.get("status") not in ("accepted", "queued"):
            raise IPCError("目标状态已确认，但启动消息结果尚未确认；请不要自动重发")
        return {"id": activation_id, "status": activation["status"]}

    def _goal_call(self, method, *args):
        """Use the owner when available, otherwise the isolated sidecar."""
        session = self.live.get(args[0]) if args else None
        if session and session.origin in ('cli', 'ide') and session.runtime == 'cli':
            return getattr(self.cli_sessions, method)(*args)
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
        session = self.session(thread_id, attach=False, background=True)
        if not self.local and not (session.origin in ('cli', 'ide') and session.connected and session.runtime == 'cli'):
            raise ValueError('目标模式暂不支持 SSH 主机')
        if session.origin not in ('cli', 'ide') and self.goal is None:
            raise ValueError('目标模式暂不支持 SSH 主机')
        if session.origin in ('cli', 'ide') and not session.connected:
            raise ValueError('请先继续此会话，再操作目标')
        if session.origin in ('cli', 'ide') and not self._session_goal_available(session):
            raise ValueError('当前 Codex 会话暂不支持目标模式')
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

    def _management_identity(self, thread_id, refresh=False):
        with self.lock:
            session = self.live.get(thread_id)
        if session is None:
            return ''
        with session.condition:
            provider = (session.state or {}).get('modelProvider') or session.saved_provider
        if not provider:
            return ''
        if self.local:
            return self._draft_account_identity({'provider': provider})
        alias = self.cli_sessions.alias
        home = str(self.cli_sessions.remote_home) if self.cli_sessions.remote_home else None
        with self._draft_remote_lock:
            cached = self._draft_remote_cache.get((self.host, alias, home, provider))
            fresh = cached and time.monotonic() - cached[0] < 30
            identity = cached[1] if fresh else ''
        if refresh and not fresh:
            # Probe only account files, never native history or attachment.
            self._draft_account_identity({'provider': provider})
        return identity

    def message_management(self, thread_id, receipt_ids=()):
        uuid.UUID(thread_id)
        identity = self._management_identity(thread_id, refresh=True)
        with self.goal_lock, self.submit_lock:
            rows = [(k.split(':', 1)[1], copy.deepcopy(v)) for k, v in self.submissions.items() if k.startswith(thread_id + ':')]
            result = management_snapshot(rows, receipt_ids, self._goal_activation_ids(thread_id), scope=[self.host, thread_id, identity])
            for receipt in result['receipts']:
                command = self.goal_commands.get(thread_id + ':' + receipt['id'])
                if command and command.get('action') == 'create':
                    state = command.get('state')
                    receipt['status'] = 'accepted' if state == 'confirmed' else 'failed' if state == 'failed' else 'unknown'
        result['draftAccountIdentity'] = identity
        result['identityPending'] = not identity and (bool(getattr(self, '_draft_remote_pending', ())) or
                                                    (self.host != 'local' and bool(self._management_identity(thread_id))))
        if not identity:
            result['capabilities'] = dict.fromkeys(result['capabilities'], False)
        return result

    @operation
    def manage_messages(self, thread_id, body):
        uuid.UUID(thread_id)
        # Do not load history or attach merely to edit the durable queue.
        with self.lock:
            session = self.live.setdefault(thread_id, LiveSession(thread_id, question_skips=self.question_skips))
        with session.action_lock, self.goal_lock, self.submit_lock:
            identity = self._management_identity(thread_id)
            if not identity or body.get('expectedAccount') != identity:
                raise ValueError('账号接入已变化，请刷新会话后重试')
            body = {k: v for k, v in body.items() if k != 'expectedAccount'}
            rows = [(k.split(':', 1)[1], copy.deepcopy(v)) for k, v in self.submissions.items() if k.startswith(thread_id + ':')]
            updated, result = management_mutate(rows, body, self._goal_activation_ids(thread_id), scope=[self.host, thread_id, identity])
            keys = [k for k in self.submissions if k.startswith(thread_id + ':')]
            values = iter((thread_id + ':' + k, v) for k, v in updated)
            previous = self.submissions
            self.submissions = dict(next(values) if k in keys else (k, v) for k, v in previous.items())
            try:
                self._save_ledger()
            except Exception:
                self.submissions = previous
                raise
            result.update(management_snapshot(updated, hidden=self._goal_activation_ids(thread_id), scope=[self.host, thread_id, identity]))
            result['draftAccountIdentity'] = identity
        with session.condition:
            session.changed()
        return result

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
             plan_response=None, attachments=None, references=None, ui_locale=None, activation=None,
             _async_response=None):
        uuid.UUID(submission_id)
        identifiers = [] if attachments is None else attachments
        files = self.uploads.resolve(thread_id, identifiers)
        references = [] if references is None else references
        if references:
            files += self.workspace(thread_id, 'references', {'references': references})
        elif not isinstance(references, list):
            raise ValueError('项目文件引用无效，请重新选择')
        if len(files) > 10:
            raise ValueError('每条消息最多添加 10 个不同附件')
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
        with self.lock:
            current = self.live.get(thread_id)
        target_options = {'defer_cli_account': True} if current and current.runtime == 'cli' and current.connected else {}
        if work_mode == "goal":
            if identifiers or references:
                raise ValueError("目标模式暂不支持附件")
            if skills:
                raise ValueError("目标模式只接受纯文本目标")
            if not self.local:
                session = self.session(thread_id, attach=False, background=True)
                if not (session.origin in ('cli', 'ide') and session.connected and session.runtime == 'cli'):
                    raise ValueError('目标模式暂不支持 SSH 主机')
            session = self._target(thread_id, **target_options)
            if not self.local and not (session.origin in ('cli', 'ide') and session.connected and session.runtime == 'cli'):
                raise ValueError('目标模式暂不支持 SSH 主机')
            if session.origin not in ('cli', 'ide') and self.goal is None:
                raise ValueError('目标模式暂不支持 SSH 主机')
            if session.origin in ('cli', 'ide') and not self._session_goal_available(session):
                raise ValueError('当前 Codex 会话暂不支持目标模式')
            with session.action_lock, self.goal_lock:
                if session.runtime == 'cli':
                    self._verify_cli_submission_account(session)
                response = self._goal_command(session, thread_id, submission_id, "create", objective=text,
                                              ui_locale=ui_locale or activation.get("uiLocale") if activation else ui_locale)
                result = {"status": "unknown" if response.get("status") == "unknown" else "accepted",
                          "confirmed": bool(response.get("confirmed")), "duplicate": bool(response.get("duplicate")),
                          "id": submission_id, "result": response.get("result")}
                result["activation"] = self._send_goal_activation(
                    session, response, "create", ui_locale or activation.get("uiLocale") if activation else ui_locale)
                return result
        session = self._target(thread_id, **target_options)
        access = self._access(session.origin, session)
        if work_mode is not None and not access['canUseModes']:
            raise ValueError('此会话暂不能切换工作模式')
        if skills and not access['canSelectSkills']:
            raise ValueError('此会话暂不能选择 Skill')
        selected = self._resolve_skills(session, [] if skills is None else skills)
        key = thread_id + ":" + submission_id
        with session.action_lock:
            with self.submit_lock:
                prior = self.submissions.get(key)
                if prior:
                    if (prior["text"] != text or prior["mode"] != mode or prior.get("skills", []) != selected or
                            prior.get("workMode") != work_mode or prior.get("planResponse") != plan_response or prior.get('attachments', []) != identifiers or
                            prior.get('references', []) != references or
                            prior.get("goalActivation") != activation):
                        raise ValueError("同一消息标识不能用于不同内容")
                    return {"status": prior["status"], "duplicate": True, "id": submission_id}
            with session.condition:
                if _async_response is not None:
                    request_id, response = _async_response
                    pending = next((r for r in session.pending_requests()
                                    if r['id'] == request_id and r['method'] == 'bridge/requestUserInputAsync'), None)
                    if not pending or self._async_reply_text(pending, response) != text:
                        raise ValueError('此请求已处理或已过期')
                active = (session.state or {}).get("threadRuntimeStatus", {}).get("type") == "active"
                if active and mode == "send":
                    raise SubmissionRejected("Codex 正在执行。请选择「排队发送」或「补充当前任务」。")
                if not active and mode == "steer":
                    raise SubmissionRejected("当前任务已经结束，请使用普通发送")
                if work_mode is not None and not (session.state.get("latestModel") or
                        (session.state.get("latestCollaborationMode") or {}).get("settings", {}).get("model")):
                    raise ValueError("尚未取得模型设置，请重新连接后再切换模式")
                if plan_response is not None:
                    pending = next((r for r in session.state.get("requests", [])
                                    if r.get("id") == plan_response["requestId"] and r.get("method") == "item/plan/requestImplementation"), None)
                    if pending is None:
                        raise ValueError("此计划已处理或已过期，请刷新后查看最新计划")
                    if plan_response["action"] == "implement":
                        if text != "Implement the following plan:\n\n" + pending.get("params", {}).get("planContent", ""):
                            raise ValueError("计划已更新，请刷新后重试")
            if session.runtime == 'cli' and _async_response is None:
                self._verify_cli_submission_account(session)
            with self.submit_lock:
                entry = {"text": text, "mode": mode, "skills": selected, "status": "queued" if mode == "queue" else "unknown", "at": time.time()}
                if identifiers:
                    entry['attachments'] = identifiers
                if references:
                    entry['references'] = references
                if files:
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
                    queued = [(k, v) for k, v in self.submissions.items() if k.startswith(session.id + ':') and v.get('status') == 'queued']
                    if not queued or queued[0][0] != key:
                        return
                    entry = copy.deepcopy(queued[0][1])
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
        if entry.get('references'):
            files += self.workspace(session.id, 'references', {'references': entry['references']})
        # CLI start already adds context.fileAttachments to its prompt.
        if session.runtime != 'cli':
            text = attachment_prompt(text, files)
        request = {"threadId": session.id, "input": [{"type": "text", "text": text, "text_elements": []}], "clientUserMessageId": submission_id}
        request['input'].extend({'type': 'localImage', 'path': f['path']} for f in files if f['image'])
        context = {'attachments': [], 'commentAttachments': []}
        if files:
            context['fileAttachments'] = [{'path': f['path'], 'label': f['name']} for f in files]
            # Prepared native turns render this metadata directly; fileAttachments
            # alone is composer input and does not populate the desktop message.
            context['attachments'] = [dict(path=f['path'], fsPath=f['path'], label=f['name'],
                                           **({'isImageAttachment': True} if f['image'] else {})) for f in files]
            context['localTurnMetadata'] = {'fileAttachmentCount': sum(not f['image'] for f in files)}
        if entry.get("workMode") is not None:
            with session.condition:
                current = session.state
                model = current.get("latestModel") or (current.get("latestCollaborationMode") or {}).get("settings", {}).get("model")
                effort = current.get("latestReasoningEffort") or (current.get("latestThreadSettings") or {}).get("effort")
            if not model:
                raise ValueError("尚未取得模型设置，请重新连接后再切换模式")
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
                "attachments": context['attachments']}, timeout=30)
        with self.submit_lock:
            self.submissions[key]["status"] = "accepted"
            self._save_ledger()
        with session.condition:
            session.changed()
        return {"status": "accepted", "id": submission_id, "result": response}

    @operation
    def compact(self, thread_id, request_id):
        session = self.session(thread_id, attach=False)
        with session.action_lock:
            def prepare():
                with session.condition:
                    identity = (session.runtime, session.owner)
                    if not session.connected or not session.owner:
                        raise ValueError('请先连接原会话后再压缩上下文')
                if session.runtime == 'cli':
                    self.cli_sessions.refresh(thread_id)
                else:
                    try:
                        self.assert_desktop_idle(timeout=8, identifiers=[thread_id])
                    except ValueError as exc:
                        raise ValueError('原生会话忙碌或状态暂不可确认，请稍后再压缩上下文') from exc
                with session.condition:
                    view = session.view()
                    if (not session.connected or identity != (session.runtime, session.owner) or
                            view['status'] != 'idle' or view['requests'] or
                            any(turn.get('status') == 'inProgress' for turn in ordered_turns(session.state))):
                        raise ValueError('请等待当前任务结束并核对原会话连接后再压缩上下文')
                return lambda: self._call(session, 'thread-follower-compact-thread', {})
            return self.compact_commands.perform(thread_id, request_id, prepare)

    def interrupt(self, thread_id):
        session = self._target(thread_id)
        with session.condition:
            active = next((t for t in reversed(ordered_turns(session.state)) if t.get("status") == "inProgress"), None)
            if not active or not active.get("turnId"):
                raise ValueError("当前没有可停止的任务")
            turn_id = active["turnId"]
        return self._call(session, "thread-follower-interrupt-turn", {"mode": "user-stop", "expectedTurnId": turn_id})

    def full_history(self, thread_id):
        session = self.session(thread_id, attach=False)
        if session.origin in ('cli', 'ide') and not (session.origin == 'ide' and session.connected and session.runtime == 'desktop'):
            fallback = self.store.history(thread_id, turn_limit=None)
            with session.condition:
                session.set_history(fallback)
            return {"loaded": True}
        session = self._target(thread_id)
        return self._call(session, "thread-follower-load-complete-history", {}, timeout=180)

    def _skip_question(self, thread_id, request_id, response):
        if (set(response) != {'action', 'questionId'} or not isinstance(request_id, str)
                or not isinstance(response.get('questionId'), str)):
            raise ValueError('跳过问题的请求无效')
        question_id = response['questionId']
        # This only changes gateway presentation; it never activates a native owner.
        session = self.session(thread_id, attach=False)
        with session.action_lock, session.condition:
            if not self.question_skips.contains(thread_id, request_id, question_id):
                if not session.connected:
                    raise ValueError('请先连接原会话')
                pending = next((r for r in async_requests(session.state) if r['id'] == request_id), None)
                if not pending or question_id not in {q['id'] for q in pending['params']['questions']}:
                    raise ValueError('此请求已处理或已过期')
                self.question_skips.skip(thread_id, request_id, question_id)
                session.changed()
            return {'status': 'skipped', 'questionId': question_id}

    @staticmethod
    def _async_reply_text(pending, response):
        questions = pending['params']['questions']
        answers = response.get('answers', {})
        if (set(response) != {'answers'} or not isinstance(answers, dict)
                or set(answers) != {q['id'] for q in questions}
                or any(not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], str)
                       or not v[0].strip() or len(v[0]) > 20000 for v in answers.values())):
            raise ValueError('请回答所有问题')
        replies = [{'questionItemId': q['id'], 'question': q['question'], 'answer': answers[q['id']][0]}
                   for q in questions]
        return '<send_user_message_question_reply>\n' + json.dumps(replies, ensure_ascii=False, separators=(',', ':')) + '\n</send_user_message_question_reply>'

    def respond(self, thread_id, request_id, response):
        if response.get('action') == 'skip':
            return self._skip_question(thread_id, request_id, response)
        session = self._target(thread_id)
        plan_id = str(uuid.uuid5(uuid.UUID(thread_id), "plan:" + str(request_id)))
        with self.submit_lock:
            prior = copy.deepcopy(self.submissions.get(thread_id + ":" + plan_id))
        if prior and prior.get("planResponse"):
            if prior["planResponse"] != {"requestId": request_id, **response}:
                raise ValueError("此计划已经提交了不同操作，请查看会话结果")
            return {"status": prior["status"], "duplicate": True, "id": plan_id}
        with session.condition:
            async_pending = next((r for r in session.pending_requests() if r["id"] == request_id and r["method"] == "bridge/requestUserInputAsync"), None)
            active = (session.state or {}).get("threadRuntimeStatus", {}).get("type") == "active"
        if async_pending:
            text = self._async_reply_text(async_pending, response)
            submission = str(uuid.uuid5(uuid.UUID(thread_id), request_id + text))
            # Resolve the owner before taking action_lock, then recheck questions
            # inside send's lock so a concurrent skip cannot submit a stale answer.
            return self.send(thread_id, text, submission, "steer" if active else "send",
                             _async_response=(request_id, response))
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

    def assert_detachable(self):
        """Check gateway-owned work before removing an already disabled entry."""
        with self.submit_lock, self.create_lock, self.message_lock:
            for ledger in (self.submissions, self.creations, self.message_actions, self.goal_commands):
                if any(row.get('status', row.get('state')) in ('queued', 'unknown', 'pending', 'sent')
                       for row in ledger.values()):
                    raise ValueError('此客户端有排队或待核对操作，请先处理后再移除')
            if any(not row.get('id') for row in self.creations.values()):
                raise ValueError('此客户端有待核对的新建聊天，请先处理后再移除')
        if self.accounts:
            with self.accounts.lock:
                if (self.accounts.enrollment or {}).get('phase') in ('starting', 'waiting'):
                    raise ValueError('此客户端正在登录，请完成或取消登录后再移除')
        with self.terminals.lock:
            if any(not job.done.is_set() for job in
                   (*self.terminals.jobs.values(), *self.terminals.sessions.values())):
                raise ValueError('此客户端有未关闭的终端，请先关闭后再移除')
        if self.cli_sessions.cli_connections():
            raise ValueError('请先释放此客户端的 CLI 连接，再移除配置')
        with self.side_chats.lock:
            if any(not chat.closed for chat in self.side_chats.chats.values()):
                raise ValueError('请先结束此客户端的旧侧边聊天，再移除配置')

    def close(self):
        with self.entry_gate if self.entry_gate is not None else nullcontext():
            self._close()

    def _close(self):
        self.terminals.close()
        self.side_chats.close()
        if self.cli_access:
            self.cli_access.close()
        if getattr(self, 'wsl_accounts', None):
            self.wsl_accounts.close()
        self.closed.set()
        self.cli_maintenance.cancel()
        if hasattr(self.catalog_reader, 'stop_model_refresh'):
            self.catalog_reader.stop_model_refresh()
        with self.native_model_lock:
            for timer in self.native_model_timers.values():
                timer.cancel()
            self.native_model_timers.clear()
        if self.entries is not None:
            self.entries.close()
        if self.accounts:
            self.accounts.monitor.close()
            self.accounts.login_cancel.set()
            self.accounts.reset_forecast.close()
            with self.accounts.lock:
                for timer in self.accounts.api_model_timers.values():
                    timer.cancel()
                self.accounts.api_model_timers.clear()
                for catalog in self.accounts.saved_model_catalogs.values():
                    catalog.stop_model_refresh()
        for bridge in list(self.remote_bridges.values()):
            bridge.close()
        with self.lock:
            sessions = list(self.live.values())
        for session in sessions:
            with session.condition:
                session.condition.notify_all()
            try:
                if (session.connected or session.discovering) and session.runtime != "cli":
                    self.ipc.follow(session.id, session.owner, False, host=self.native_host)
            except IPCError:
                pass
        self.ipc.close()
        self.cli_sessions.close()
        if self.goal:
            self.goal.close()


def validate_form(value, schema):
    """Validate the small JSON-schema form subset that the phone can render."""
    from bridge.features.sessions.model import form_supported
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
