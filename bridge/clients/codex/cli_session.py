"""Explicitly resume saved CLI threads through their native app-server."""

import copy
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from bridge.features.sessions.identity import same_cwd
from bridge.clients.codex.app_server import (
    AppServerError, AppServerUnavailable, AppServerUnknownResult, AppServerUnsupported, CodexAppServer)
from bridge.clients.codex.catalog import Catalog, CatalogError, ModelStore, normalize_native_skills
from bridge.clients.codex.remote import RemoteStore, RemoteUnavailable, remote_model_catalog, remote_model_identity
from bridge.platforms.codex_daemon_env import read_daemon_environment, DaemonEnvironmentUnavailable
from bridge.features.sessions.goal import GoalError, GoalUnavailable, GoalUnsupported, normalize_goal_result
from bridge.features.sessions import linux_permissions
from bridge.features.sessions.store import SessionStore, StoreUnavailable

MAX_REASONING_PARTS = 1024


def _managed_cli(home):
    """Mirror the 0.162 daemon's selected package without installing one."""
    home = Path(home)
    dedicated = home / 'packages/app-server-daemon'
    state = home / 'app-server-daemon'
    if os.path.lexists(dedicated / 'current') or any(os.path.lexists(state / name) for name in
            ('daemon.pid', 'daemon.stderr.log', 'daemon-updater.pid', 'daemon-updater.stderr.log')):
        root = dedicated
    elif any(os.path.lexists(state / name) for name in
            ('app-server.pid', 'app-server.stderr.log', 'app-server-updater.pid', 'app-server-updater.stderr.log')):
        root = home / 'packages/standalone'
    else:
        root = dedicated
    current = root / 'current'
    name = 'codex.exe' if os.name == 'nt' else 'codex'
    packaged, legacy = current / 'bin' / name, current / name
    selected = packaged if (packaged.is_file() or
                            (not legacy.is_file() and (os.name == 'nt' or root == dedicated))) else legacy
    return selected if selected.is_file() and os.access(selected, os.X_OK) else None


def _ssh_managed_exec(*args):
    arguments = ' '.join(shlex.quote(arg) for arg in args)
    return ('home=${CODEX_HOME:-$HOME/.codex}; '
             'state="$home/app-server-daemon"; '
             'if test -e "$home/packages/app-server-daemon/current" || '
             'test -L "$home/packages/app-server-daemon/current" || '
             'test -e "$state/daemon.pid" || test -e "$state/daemon.stderr.log" || '
             'test -e "$state/daemon-updater.pid" || test -e "$state/daemon-updater.stderr.log"; '
             'then root="$home/packages/app-server-daemon"; '
             'elif test -e "$state/app-server.pid" || test -e "$state/app-server.stderr.log" || '
             'test -e "$state/app-server-updater.pid" || test -e "$state/app-server-updater.stderr.log"; '
             'then root="$home/packages/standalone"; '
             'else root="$home/packages/app-server-daemon"; fi; '
             'if test -x "$root/current/bin/codex"; then '
             'exec "$root/current/bin/codex" app-server ' + arguments + '; '
             'elif test -x "$root/current/codex"; then '
             'exec "$root/current/codex" app-server ' + arguments + '; else exit 73; fi')


def _ssh_proxy_command(alias):
    if not alias or alias.startswith('-') or any(c.isspace() for c in alias):
        raise ValueError('Invalid SSH host alias')
    # Match RemoteStore's explicit user login shell and CODEX_HOME selection.
    inner = ('if command -v codex >/dev/null 2>&1; then '
             'exec codex app-server proxy; '
             'elif test -x "$HOME/.local/bin/codex"; then '
             'exec "$HOME/.local/bin/codex" app-server proxy; else '
             + _ssh_managed_exec('proxy') + '; fi')
    remote = 'exec "$SHELL" -lc ' + shlex.quote(inner)
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UpdateHostKeys=no', alias, remote]


def _ssh_daemon_start_command(alias):
    command = _ssh_proxy_command(alias)
    inner = _ssh_managed_exec('daemon', 'start')
    command[-1] = 'exec "$SHELL" -lc ' + shlex.quote(inner)
    return command


def _local_cli(home, configured):
    if configured and configured != 'codex':
        path = Path(configured)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise AppServerUnavailable('Configured Codex CLI is unavailable')
    binary = 'codex.exe' if os.name == 'nt' else 'codex'
    candidates = (_managed_cli(home), shutil.which('codex'), Path.home()/'.local/bin'/binary)
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    candidate = Catalog.find_runtime()
    if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    raise AppServerUnavailable('Codex CLI executable is unavailable')


class CliSessions:
    def __init__(self, home, on_state, on_disconnect, cli='codex', alias=None,
                 process_factory=None, allow_installed_runtime=False):
        self.home = Path(home)
        self.allow_installed_runtime = allow_installed_runtime
        self.alias = alias
        self.remote_home = None
        command = _ssh_proxy_command(alias) if alias else None
        self.on_state = on_state
        self.on_disconnect = on_disconnect
        self.lock = threading.RLock()
        self.start_lock = threading.Lock()
        self.daemon_start_generation = 0
        self.attach_locks = {}
        self.joined = {}
        self.attaching = set()
        self.metadata = {}
        self.states = {}
        self.requests = {}
        self.consumed_requests = {}
        self.refreshing = set()
        self.refresh_pending = set()
        self.refresh_cancel = {}
        self.client = CodexAppServer(
            self.home / 'app-server-control/app-server-control.sock', cli=cli,
            on_event=self._event, on_server_request=self._server_request,
            on_disconnect=self._disconnect, process_factory=process_factory,
            command=command, check_socket=alias is None, client_name='codex-mobile-bridge-cli')
        self.configured_cli = cli
        self.store = RemoteStore(alias) if alias else SessionStore(self.home)

    def cli_connections(self):
        with self.lock:
            return [{'key': self.metadata[sid]['connectionToken'], 'sessionId': sid, 'provider': 'codex',
                     'title': self.states.get(sid, {}).get('title') or sid, 'host': self.alias or 'local',
                     'location': self.alias or '本机 CLI',
                     '_release': lambda stop, sid=sid, token=self.metadata[sid]['connectionToken']: self.release_connection(sid, token, stop)}
                    for sid, process in self.joined.items() if process is self.client.process
                    and sid in self.metadata and self.metadata[sid].get('connectionToken')]

    def release_connection(self, identifier, token, stop=False):
        from bridge.clients.codex.release import release_thread
        with self.lock:
            guard = self.attach_locks.setdefault(identifier, threading.Lock())
        with guard:
            process = self.client.process
            def request(method, params):
                with self.lock:
                    if (process is None or self.client.process is not process or self.joined.get(identifier) is not process
                            or self.metadata.get(identifier, {}).get('connectionToken') != token):
                        raise AppServerUnavailable('原 CLI 连接已变化；未操作新的连接')
                return self.client.request(method, params)
            result = release_thread(request, identifier, stop)
            if result['status'] in ('released', 'detached'):
                self._invalidate(identifier, process)
            return result

    def available(self):
        if self.alias is not None or Path(self.client.socket_path).exists() or _managed_cli(self.home) is not None:
            return True
        if self.allow_installed_runtime:
            try:
                return bool(_local_cli(self.home, self.configured_cli))
            except AppServerUnavailable:
                pass
        return False

    def daemon_status(self):
        """Return running, stopped or unknown without starting the local daemon."""
        if self.alias is not None:
            raise ValueError('Local Codex daemon status requires a local CLI')
        socket_path = Path(self.client.socket_path)
        state = self.home / 'app-server-daemon'
        if not socket_path.exists() and not any(os.path.lexists(state / name) for name in
                ('daemon.pid', 'app-server.pid')):
            return 'stopped'
        try:
            executable = _local_cli(self.home, self.configured_cli)
            environment = os.environ.copy()
            environment['CODEX_HOME'] = str(self.home)
            if self.allow_installed_runtime:
                environment.pop('CODEX_SQLITE_HOME', None)
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run([executable, 'app-server', 'daemon', 'version'],
                                    env=environment, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5, **options)
            if result.returncode == 0:
                report = json.loads(result.stdout)
                if isinstance(report, dict) and report.get('status') == 'running':
                    return 'running'
        except (AppServerUnavailable, OSError, subprocess.TimeoutExpired, ValueError):
            pass
        if not socket_path.exists():
            from bridge.platforms.codex_daemon_env import daemon_process_absent
            if daemon_process_absent(self.home):
                return 'stopped'
        return 'unknown'

    def daemon_may_be_running(self):
        """Fail closed on unknown native status before a local account change."""
        return self.daemon_status() != 'stopped'

    def create(self, cwd, title, on_start=None, on_created=None, model=None, effort=None):
        """An explicit new CLI chat; no turn is started."""
        if not isinstance(cwd, str) or not cwd or (self.alias is None and not Path(cwd).is_dir()):
            raise AppServerUnavailable('CLI 项目目录不存在')
        from bridge.features.sessions.create import model_overrides
        overrides = model_overrides(model, effort)
        info = self._connect_explicit()
        if self.alias is None and Path(info['codexHome']) != self.home:
            raise AppServerUnavailable('Codex CLI runtime belongs to another home')
        if self.alias is not None:
            remote_home = Path(info['codexHome'])
            if not remote_home.is_absolute() or (self.remote_home is not None and remote_home != self.remote_home):
                raise AppServerUnavailable('Remote Codex CLI runtime identity changed')
            self.remote_home = remote_home
        if on_start is not None:
            on_start()
        params = {'cwd': cwd, 'ephemeral': False, 'threadSource': 'cli', **overrides}
        result = self.client.request('thread/start', params)
        thread = result.get('thread') or {}
        thread_id = thread.get('id')
        if not isinstance(thread_id, str) or not same_cwd(cwd, thread.get('cwd'), self.alias is None and os.name == 'nt') or not thread.get('modelProvider'):
            raise AppServerUnsupported('CLI creation returned an unverified thread')
        uuid.UUID(thread_id)
        if on_created is not None:
            on_created(thread_id)
        if model is not None and (result.get('model') != model or effort is not None and result.get('reasoningEffort') != effort):
            raise AppServerUnsupported('新聊天已创建，但模型或思考强度未确认；请检查原会话，不要重复创建')
        with self.lock:
            self.joined[thread_id] = self.client.process
            self.metadata[thread_id] = {'connectionToken': str(uuid.uuid4()), 'cwd': cwd, 'modelProvider': thread['modelProvider'],
                                        'model': result.get('model'), 'reasoningEffort': result.get('reasoningEffort'),
                                        'serviceTier': result.get('serviceTier'),
                                        'collaborationMode': result.get('collaborationMode'),
                                        'permissions': {key: copy.deepcopy(result[key]) for key in
                                                        ('approvalPolicy', 'approvalsReviewer', 'sandbox', 'activePermissionProfile')
                                                        if key in result}, 'goal': None}
        self.client.request('thread/name/set', {'threadId': thread_id, 'name': title})
        return self.refresh(thread_id)

    def archive_call(self, thread_id, archived, identifiers, disconnect=False, check_scope=None):
        # Explicit archive may start the installed original daemon, never a thread.
        info = self._connect_explicit()
        home = Path(info.get('codexHome', ''))
        expected = self.remote_home if self.alias else self.home
        if not home.is_absolute() or (expected is not None and home != expected):
            raise AppServerUnavailable('Codex CLI runtime belongs to another home')
        from bridge.features.sessions.create import check_archive_runtime, stop_archive_runtime
        if not disconnect:
            check_archive_runtime(lambda method, params: self.client.request(method, params, replay_safe=True), identifiers)
        with self.lock:
            if not disconnect and any(identifier in self.requests and self.requests[identifier] for identifier in identifiers):
                raise ValueError('聊天有等待确认的请求，请先在原客户端处理')
        process = self.client.process
        def call():
            if self.client.process is not process:
                from bridge.features.sessions.create import ArchiveNotSent
                raise ArchiveNotSent('原运行时连接已变化，未停止或归档')
            if disconnect:
                def request(method, params):
                    if self.client.process is not process:
                        raise AppServerUnavailable('原运行时连接已变化，尚未发送归档请求')
                    return self.client.request(method, params)
                stop_archive_runtime(request, identifiers)
                if check_scope:
                    check_scope()
                return request('thread/archive', {'threadId': thread_id})
            return self.client.request('thread/archive' if archived else 'thread/unarchive', {'threadId': thread_id})
        return call

    def delete_call(self, snapshot, recheck):
        info = self._connect_explicit()
        if Path(info.get('codexHome', '')) != self.home or self.alias:
            raise AppServerUnavailable('Codex CLI runtime belongs to another home')
        from bridge.features.sessions.deletion import delete_native, DeleteNotSent
        from bridge.features.sessions.create import DeleteRejected
        process = self.client.process
        def call():
            def request(method, params):
                if self.client.process is not process:
                    raise AppServerUnavailable('原运行时连接已变化，删除结果待核对')
                try:
                    return self.client.request(method, params, replay_safe=method.endswith(('/read', '/list')))
                except AppServerUnsupported as exc:
                    if method == 'thread/delete':
                        raise DeleteRejected('当前原生运行时不支持永久删除，请在原客户端操作') from exc
                    raise
            if self.client.process is not process:
                raise DeleteNotSent('原运行时连接已变化，未发送删除')
            return delete_native(request, snapshot, recheck)
        return call

    def attach(self, thread_id, explicit=False):
        """Passive inspection never starts or resumes a runtime."""
        if not explicit:
            return None
        if not self.available():
            raise AppServerUnavailable('Codex CLI daemon and installed package are unavailable')
        with self.lock:
            attach_lock = self.attach_locks.setdefault(thread_id, threading.Lock())
        with attach_lock:
            return self._attach_explicit(thread_id)

    def _connect_explicit(self):
        if self.alias is None and isinstance(self.client, CodexAppServer):
            executable = _local_cli(self.home, self.configured_cli)
            self.client.command[0] = executable
        generation = self.daemon_start_generation
        try:
            return self.client.connect()
        except AppServerUnavailable:
            with self.start_lock:
                if self.daemon_start_generation != generation:
                    return self.client.connect()
                self._start_daemon()
                self.daemon_start_generation += 1
                return self.client.connect()

    def _start_daemon(self, proxy_updates=None, preserved=None):
        if self.alias is None:
            managed = _managed_cli(self.home)
            if managed is None and self.allow_installed_runtime:
                managed = _local_cli(self.home, self.configured_cli)
            if managed is None:
                raise AppServerUnavailable('Installed Codex daemon package is required')
            environment = os.environ.copy()
            environment['CODEX_HOME'] = str(self.home)
            if proxy_updates:
                from bridge.clients.codex.daemon_network import PROXY_NAMES
                for name in PROXY_NAMES:
                    environment.pop(name, None)
                environment.update(proxy_updates)
            command = [str(managed), 'app-server', 'daemon', 'start']
            if self.allow_installed_runtime:
                environment.pop('CODEX_SQLITE_HOME', None)
            for name, value in (preserved or {}).items():
                if value is None:
                    environment.pop(name, None)
                else:
                    environment[name] = value
        else:
            command = _ssh_daemon_start_command(self.alias)
            environment = None
        try:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25, **options)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppServerUnavailable('Could not start the existing Codex CLI daemon') from exc
        if result.returncode:
            if ('daemon executable not found' in result.stderr or
                    'this CLI has no complete local package' in result.stderr):
                raise AppServerUnavailable('此 Codex CLI 缺少完整的后台运行组件。请安装或修复完整的 Codex CLI 后重试。')
            raise AppServerUnavailable('Installed Codex CLI daemon could not start')
        try:
            report = json.loads(result.stdout)
        except ValueError as exc:
            raise AppServerUnsupported('Unexpected Codex daemon start response') from exc
        if not isinstance(report, dict) or report.get('status') not in ('started', 'alreadyRunning'):
            raise AppServerUnsupported('Unexpected Codex daemon start status')
        if self.alias is None and Path(report.get('socketPath', '')) != Path(self.client.socket_path):
            raise AppServerUnavailable('Codex daemon started in another home')

    def _attach_explicit(self, thread_id):
        info = self._connect_explicit()
        if self.alias is None and Path(info['codexHome']) != self.home:
            raise AppServerUnavailable('Codex CLI runtime belongs to another home')
        if self.alias is not None:
            remote_home = Path(info['codexHome'])
            if not remote_home.is_absolute() or (self.remote_home is not None and
                    remote_home != self.remote_home):
                raise AppServerUnavailable('Remote Codex CLI runtime identity changed')
            self.remote_home = remote_home
        with self.lock:
            if thread_id in self.joined:
                return copy.deepcopy(self.states[thread_id])
        loaded = self.client.is_loaded(thread_id)
        before = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': False},
                                     replay_safe=True)['thread']
        if before.get('id') != thread_id:
            raise AppServerUnavailable('CLI thread is unavailable in this runtime')
        if not loaded or before.get('status', {}).get('type') == 'notLoaded':
            try:
                saved = self.store.get(thread_id)
            except (KeyError, StoreUnavailable, RemoteUnavailable) as exc:
                raise AppServerUnavailable('Saved CLI thread metadata is unavailable') from exc
            if (saved.get('origin') not in ('cli', 'ide') or not saved.get('cwd') or
                    not same_cwd(saved.get('cwd'), before.get('cwd'),
                                  self.alias is None and os.name == 'nt') or
                    saved.get('model_provider') != before.get('modelProvider')):
                raise AppServerUnsupported('Saved CLI thread identity does not match this runtime')
        # Resume is explicit and sends no model, cwd, provider or permission overrides.
        with self.lock:
            self.attaching.add(thread_id)
        params = {'threadId': thread_id, 'excludeTurns': True}
        try:
            resumed = self.client.request('thread/resume', params)
        except Exception:
            with self.lock:
                self.attaching.discard(thread_id)
                self.requests.pop(thread_id, None)
            raise
        after = resumed.get('thread') or {}
        if (after.get('id') != thread_id or not same_cwd(before.get('cwd'), after.get('cwd'), self.alias is None and os.name == 'nt') or
                resumed.get('modelProvider') != before.get('modelProvider')):
            with self.lock:
                self.attaching.discard(thread_id)
                self.requests.pop(thread_id, None)
            raise AppServerUnsupported('CLI thread identity changed during join')
        try:
            goal = normalize_goal_result(self.client.request(
                'thread/goal/get', {'threadId': thread_id}, replay_safe=True))
        except AppServerUnsupported:
            goal = None
        except (AppServerUnavailable, AppServerUnknownResult):
            with self.lock:
                self.attaching.discard(thread_id)
                self.requests.pop(thread_id, None)
            raise
        except AppServerError:
            # Goal is optional; a deterministic policy error must not prevent
            # joining the thread for turns, models and Skills.
            goal = None
        with self.lock:
            self.joined[thread_id] = self.client.process
            self.attaching.discard(thread_id)
            self.metadata[thread_id] = {'connectionToken': str(uuid.uuid4()), 'model': resumed.get('model'),
                                        'modelProvider': resumed.get('modelProvider'),
                                        'cwd': resumed.get('cwd'),
                                        'reasoningEffort': resumed.get('reasoningEffort'),
                                        'serviceTier': resumed.get('serviceTier'),
                                        'collaborationMode': resumed.get('collaborationMode'),
                                        'permissions': {key: copy.deepcopy(resumed[key]) for key in
                                                        ('approvalPolicy', 'approvalsReviewer', 'sandbox', 'activePermissionProfile')
                                                        if key in resumed},
                                        'goal': goal}
        try:
            return self.refresh(thread_id)
        except Exception:
            with self.lock:
                joined = thread_id in self.joined
            if joined:
                self._invalidate(thread_id)
            raise

    def refresh(self, thread_id):
        with self.lock:
            process = self.client.process
            if process is None or self.joined.get(thread_id) is not process:
                raise AppServerUnavailable('CLI thread is not joined on this connection')
            starting = self.states.get(thread_id) or {}
            starting_model = starting.get('latestModel')
            starting_effort = starting.get('latestReasoningEffort')
            starting_turn_status = {turn.get('turnId'): turn.get('status')
                                    for turn in starting.get('turns', [])}
        result = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': True},
                                     replay_safe=True)
        thread = result.get('thread') or {}
        with self.lock:
            if self.client.process is not process or self.joined.get(thread_id) is not process:
                raise AppServerUnavailable('CLI thread connection changed during read')
            if thread.get('id') != thread_id:
                raise AppServerUnsupported('CLI thread read returned a different thread')
            invalid_reason = None
            if (thread.get('status') or {}).get('type') == 'notLoaded':
                invalid_reason = 'CLI thread unloaded'
            else:
                metadata = self.metadata.get(thread_id)
                if metadata is None:
                    raise AppServerUnavailable('CLI thread is no longer joined')
                if (not same_cwd(metadata['cwd'], thread.get('cwd'), self.alias is None and os.name == 'nt') or
                        thread.get('modelProvider') != metadata['modelProvider']):
                    invalid_reason = 'CLI thread provider or working directory changed'
            if invalid_reason is None:
                pending = list(self.requests.get(thread_id, {}).values())
                state = self._state(thread, pending, metadata)
                previous = self.states.get(thread_id) or {}
                previous_turns = {turn.get('turnId'): turn for turn in previous.get('turns', [])
                                  if turn.get('turnId')}
                read_turn_ids = {turn.get('turnId') for turn in state['turns']}
                newer_turn_event = any(turn_id not in starting_turn_status or
                                       prior.get('status') != starting_turn_status[turn_id]
                                       for turn_id, prior in previous_turns.items())
                for index, turn in enumerate(state['turns']):
                    turn_id = turn.get('turnId')
                    prior = previous_turns.get(turn_id) or {}
                    if turn_id in previous_turns and (turn_id not in starting_turn_status or
                            prior.get('status') != starting_turn_status[turn_id]):
                        state['turns'][index] = copy.deepcopy(prior)
                        continue
                    if not (turn.get('params') or {}).get('model') and (prior.get('params') or {}).get('model'):
                        turn['params'] = copy.deepcopy(prior['params'])
                for turn_id, prior in previous_turns.items():
                    if turn_id not in starting_turn_status and turn_id not in read_turn_ids:
                        state['turns'].append(copy.deepcopy(prior))
                if newer_turn_event:
                    state['threadRuntimeStatus'] = copy.deepcopy(previous['threadRuntimeStatus'])
                if previous.get('latestModel') != starting_model:
                    state['latestModel'] = previous.get('latestModel')
                elif thread.get('model') is None and previous.get('latestModel'):
                    state['latestModel'] = previous['latestModel']
                if previous.get('latestReasoningEffort') != starting_effort:
                    state['latestReasoningEffort'] = previous.get('latestReasoningEffort')
                elif 'reasoningEffort' not in thread and 'latestReasoningEffort' in previous:
                    state['latestReasoningEffort'] = previous['latestReasoningEffort']
                for key in ('latestThreadSettings', 'currentPermissions', 'latestCollaborationMode', 'threadGoal',
                            'completedThreadGoal', 'turnPlan'):
                    if key in previous:
                        state[key] = copy.deepcopy(previous[key])
                if state.get('latestThreadSettings'):
                    state['latestThreadSettings']['model'] = state['latestModel']
                    state['latestThreadSettings']['effort'] = state['latestReasoningEffort']
                self.states[thread_id] = state
        if invalid_reason is not None:
            self._invalidate(thread_id, expected_process=process)
            raise AppServerUnavailable(invalid_reason)
        self.on_state(thread_id, copy.deepcopy(state))
        return copy.deepcopy(state)

    def call(self, thread_id, desktop_method, params, timeout=30):
        self._check_joined_identity(thread_id)
        if desktop_method == 'thread-follower-compact-thread':
            state = self.refresh(thread_id)
            if (state.get('threadRuntimeStatus', {}).get('type') != 'idle' or state.get('requests') or
                    any(turn.get('status') == 'inProgress' for turn in state.get('turns', []))):
                raise AppServerUnavailable('请等待当前 Codex 任务结束后再压缩上下文')
            result = self.client.request('thread/compact/start', {'threadId': thread_id}, timeout=timeout)
            self._queue_refresh(thread_id)
            return result
        if desktop_method == 'thread-follower-start-turn':
            start = params.get('turnStart') or {}
            request = start.get('request') or {}
            context = start.get('context') or {}
            inputs = self._inputs(request.get('input', []), context)
            native = {
                'threadId': thread_id, 'input': inputs,
                'clientUserMessageId': request.get('clientUserMessageId')}
            if request.get('collaborationMode') is not None:
                mode = request['collaborationMode']
                if not isinstance(mode, dict) or mode.get('mode') not in ('plan', 'default') or not isinstance(mode.get('settings'), dict):
                    raise AppServerUnsupported('Invalid CLI collaboration mode')
                native['collaborationMode'] = mode
            result = self.client.request('turn/start', native, timeout=timeout)
            if 'collaborationMode' in native:
                with self.lock:
                    state = self.states.get(thread_id)
                    if state is not None:
                        state['latestCollaborationMode'] = copy.deepcopy(native['collaborationMode'])
                        snapshot = copy.deepcopy(state)
                    else:
                        snapshot = None
                if snapshot is not None:
                    self.on_state(thread_id, snapshot)
            self._queue_refresh(thread_id)
            return result
        if desktop_method == 'thread-follower-update-thread-settings':
            settings = params.get('threadSettings') or {}
            model_keys = {'model', 'effort', 'serviceTier'}
            permission_keys = {'permissions', 'approvalPolicy', 'approvalsReviewer'}
            allowed = model_keys | permission_keys
            if not isinstance(settings, dict) or not settings or set(settings) - allowed:
                raise AppServerUnsupported('Unsupported CLI thread settings')
            if set(settings) & permission_keys and (set(settings) != permission_keys or
                    settings['permissions'] not in (':workspace', ':danger-full-access') or
                    settings['approvalPolicy'] not in ('on-request', 'never') or
                    settings['approvalsReviewer'] not in ('user', 'auto_review', 'guardian_subagent') or
                    (settings['permissions'] == ':danger-full-access') != (settings['approvalPolicy'] == 'never') or
                    (settings['permissions'] == ':danger-full-access' and settings['approvalsReviewer'] != 'user')):
                raise AppServerUnsupported('Invalid CLI permission settings')
            native = {'threadId': thread_id, **settings}
            self.client.request('thread/settings/update', native, timeout=timeout)
            with self.lock:
                state = self.states.get(thread_id)
                if state is not None:
                    settings_state = state.setdefault('latestThreadSettings', {})
                    settings_state.update({key: value for key, value in settings.items() if key in model_keys})
                    if 'model' in settings:
                        state['latestModel'] = settings['model']
                    if 'effort' in settings:
                        state['latestReasoningEffort'] = settings['effort']
                    snapshot = copy.deepcopy(state)
                else:
                    snapshot = None
            if snapshot is not None:
                self.on_state(thread_id, snapshot)
            self._queue_refresh(thread_id)
            return {'applied': True}
        if desktop_method == 'thread-follower-steer-turn':
            if params.get('attachments') or (params.get('restoreMessage') or {}).get('context', {}).get('fileAttachments'):
                raise AppServerUnsupported('CLI steering with files is unavailable')
            with self.lock:
                active = next((t for t in reversed(self.states[thread_id]['turns'])
                               if t.get('status') == 'inProgress'), None)
            if not active:
                raise AppServerUnavailable('No active CLI turn to steer')
            result = self.client.request('turn/steer', {
                'threadId': thread_id, 'expectedTurnId': active['turnId'],
                'input': self._inputs(params.get('input', []), {}),
                'clientUserMessageId': params.get('clientUserMessageId')}, timeout=timeout)
            self._queue_refresh(thread_id)
            return result
        if desktop_method == 'thread-follower-interrupt-turn':
            turn_id = params.get('expectedTurnId')
            if not turn_id:
                raise AppServerUnsupported('Missing active turn id')
            return self.client.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id}, timeout=timeout)
        approval_methods = {
            'thread-follower-command-approval-decision': 'item/commandExecution/requestApproval',
            'thread-follower-file-approval-decision': 'item/fileChange/requestApproval',
            'thread-follower-permissions-request-approval-response': 'item/permissions/requestApproval',
            'thread-follower-submit-user-input': 'item/tool/requestUserInput',
            'thread-follower-submit-mcp-server-elicitation-response': 'mcpServer/elicitation/request',
        }
        if desktop_method in approval_methods:
            request_id = params.get('requestId')
            expected = approval_methods[desktop_method]
            if desktop_method in ('thread-follower-command-approval-decision',
                                  'thread-follower-file-approval-decision'):
                if 'decision' not in params:
                    raise AppServerUnsupported('Missing CLI approval decision')
                result = {'decision': params['decision']}
            else:
                if 'response' not in params:
                    raise AppServerUnsupported('Missing CLI approval response')
                result = params['response']
            with self.lock:
                pending = self.requests.get(thread_id, {}).get(request_id)
                if not pending or (pending['method'] != expected and not (
                        expected == 'item/tool/requestUserInput' and pending['method'] == 'tool/requestUserInput')):
                    raise AppServerUnavailable('CLI approval is no longer pending')
                # Claim the native request id before writing. A send timeout or
                # disconnect has an unknown result and must not permit replay.
                self.requests[thread_id].pop(request_id)
                self.consumed_requests.setdefault(thread_id, set()).add(request_id)
                state = self.states.get(thread_id)
                if state is not None:
                    state['requests'] = list(self.requests[thread_id].values())
                    snapshot = copy.deepcopy(state)
                else:
                    snapshot = None
            if snapshot is not None:
                self.on_state(thread_id, snapshot)
            self.client.respond(request_id, result)
            self._queue_refresh(thread_id)
            return {'submitted': True}
        raise AppServerUnsupported('CLI operation is not supported by this adapter')

    def _check_joined_identity(self, thread_id):
        self._require_joined(thread_id)
        if not self.client.is_loaded(thread_id):
            self._invalidate(thread_id)
            raise AppServerUnavailable('CLI thread unloaded before the operation')
        self._check_identity(thread_id)

    def permission_options(self, thread_id):
        self._check_joined_identity(thread_id)
        with self.lock:
            cwd = self.metadata[thread_id]['cwd']
            state = copy.deepcopy(self.states[thread_id])
        facts = linux_permissions.read_permission_facts(
            lambda method, params: self.client.request(method, params, replay_safe=True), cwd)
        # CLI support belongs to the selected daemon and its policy, not the
        # gateway computer's desktop UI preferences.
        options = linux_permissions.permission_options(facts, {'fullAccess': True},
            linux_permissions.current_mode(state), native=False)
        native_reasons = {
            linux_permissions.UNKNOWN: '无法确认当前 CLI 的权限能力，请检查 CLI 运行时与策略后刷新。',
            linux_permissions.PROFILE_UNAVAILABLE: '当前 CLI 运行时未提供此权限配置，请更新 Codex CLI 后刷新。',
        }
        for option in options['options']:
            option['reason'] = native_reasons.get(option['reason'], option['reason'])
        return facts, options

    @staticmethod
    def _required_model_environment(effective, provider, account=None):
        definition = (effective.get('model_providers') or {}).get(provider) or {}
        names = {value for value in (definition.get('env_key'),
                                     *(definition.get('env_http_headers') or {}).values())
                 if isinstance(value, str) and value}
        if provider == 'openai' and (
                not effective.get('openai_base_url') and not definition.get('base_url')):
            names.add('OPENAI_BASE_URL')
        if (not definition.get('env_key') and not definition.get('experimental_bearer_token') and
                (definition.get('requires_openai_auth') or
                 provider == 'openai' and
                 not definition.get('http_headers') and not definition.get('env_http_headers'))):
            # The native home auth file takes precedence over ambient keys.
            names.add('OPENAI_API_KEY')
        return names

    def model_environment(self, config, provider, account=None):
        """Resolve only variables used by this provider from its joined daemon."""
        if self.alias is not None:
            raise DaemonEnvironmentUnavailable('Remote daemon environment is unavailable')
        runtime = getattr(self.client, 'server_info', None)
        if isinstance(self.client, CodexAppServer) and (
                not runtime or Path(runtime.get('codexHome', '')).resolve() != self.home.resolve()):
            raise DaemonEnvironmentUnavailable('Codex daemon belongs to another home')
        profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
        effective = {**config, **profile}
        names = self._required_model_environment(effective, provider, account)
        if 'OPENAI_API_KEY' in names:
            try:
                saved_key = json.loads((self.home / 'auth.json').read_text(encoding='utf-8')).get('OPENAI_API_KEY')
            except (OSError, ValueError, AttributeError):
                saved_key = None
            if saved_key:
                names.discard('OPENAI_API_KEY')
        if not names:
            return {}, None
        try:
            executable = _local_cli(self.home, self.configured_cli)
        except AppServerUnavailable as exc:
            raise DaemonEnvironmentUnavailable('Codex daemon executable is unavailable') from exc
        return read_daemon_environment(self.home, executable, names)

    def model_environment_identity(self, config, provider, account=None, cwd=None):
        if self.alias is not None:
            return remote_model_identity(self.alias, self.remote_home, cwd, provider, config, account)
        environment, process = self.model_environment(config, provider, account)
        return ModelStore.key('native-cli-environment-v1', process, environment)

    def _optional_model_metadata(self, method, params):
        try:
            return self.client.request(method, params, timeout=2, replay_safe=True)
        except AppServerError:
            return {'data': []}

    def creation_context(self, cwd):
        info = self._connect_explicit()
        expected = self.remote_home if self.alias is not None else self.home
        actual = Path(info['codexHome'])
        if not actual.is_absolute() or expected is not None and actual != expected:
            raise AppServerUnavailable('Codex CLI runtime belongs to another home')
        if self.alias is not None:
            self.remote_home = actual
        config = self.client.request('config/read', {'cwd': cwd, 'includeLayers': False}, replay_safe=True).get('config') or {}
        profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
        return {**config, **profile}

    def model_catalog(self, cwd, provider, state=None):
        state = state or {}
        config = self.client.request('config/read', {'includeLayers': False, 'cwd': cwd},
                                     replay_safe=True).get('config') or {}
        profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
        effective = {**config, **profile}
        custom = Catalog.custom_provider(effective, provider)
        configured_catalog = (bool(effective.get('model_catalog_json')) and
                              provider == (effective.get('model_provider') or 'openai'))
        account = {}
        if not custom and not configured_catalog:
            account = self.client.request('account/read', {'refreshToken': False},
                                          replay_safe=True).get('account') or {}
            custom = account.get('type') == 'apiKey'
        if custom and not configured_catalog:
            if self.alias is not None:
                try:
                    metadata = self._optional_model_metadata('model/list', {
                        'cursor': None, 'limit': 100, 'includeHidden': False})
                    catalog = remote_model_catalog(self.alias, self.remote_home, cwd, provider,
                                                   config, account, metadata.get('data') or [])
                except CatalogError as exc:
                    catalog = {'models': [], 'modelError': str(exc)}
                return {'kind': 'models', 'models': catalog['models'], 'modelSource': 'api',
                        'fastMode': {'allowed': False}, 'modelError': catalog.get('modelError'),
                        'currentModel': state.get('latestModel'),
                        'currentEffort': state.get('latestReasoningEffort')}
            reader = Catalog(self.home, self.configured_cli, allow_background_refresh=False)
            try:
                environment, _ = self.model_environment(config, provider, account)
            except DaemonEnvironmentUnavailable:
                return {'kind': 'models', 'models': [], 'modelSource': 'api',
                        'fastMode': {'allowed': False},
                        'modelError': '无法核验当前 CLI 后台服务的启动环境，请重新连接后刷新模型列表',
                        'currentModel': state.get('latestModel'),
                        'currentEffort': state.get('latestReasoningEffort')}
            catalog = reader.read_models(
                lambda method, params: self.client.request(method, params, replay_safe=True),
                cwd, provider, metadata_request=lambda method, params: self._optional_model_metadata(method, params),
                environment=environment)
            models = [{'id': m['model'], 'name': m.get('displayName') or m['model'],
                       'description': m.get('description', ''),
                       'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                       'defaultEffort': m.get('defaultReasoningEffort'),
                       'isDefault': False, 'fastTier': None, 'defaultServiceTier': None}
                      for m in catalog['models']]
            return {'kind': 'models', 'models': models, 'modelSource': 'api',
                    'fastMode': {'allowed': False}, 'modelError': catalog.get('modelError'),
                    'currentModel': state.get('latestModel'),
                    'currentEffort': state.get('latestReasoningEffort')}
        native_models, cursor = [], None
        for _ in range(10):
            raw = self.client.request('model/list', {'cursor': cursor, 'limit': 100,
                                                      'includeHidden': False}, replay_safe=True)
            native_models.extend(raw.get('data', []))
            cursor = raw.get('nextCursor')
            if not cursor:
                break
        models = [{'id': m['model'], 'name': m.get('displayName') or m['model'],
                   'description': m.get('description', ''),
                   'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                   'defaultEffort': m.get('defaultReasoningEffort'),
                   'isDefault': m.get('isDefault', False), 'fastTier': Catalog.fast_tier(m),
                   'defaultServiceTier': m.get('defaultServiceTier')}
                  for m in native_models if not m.get('hidden') and m.get('model')]
        try:
            fast_mode = Catalog.fast_mode(lambda method, params: self.client.request(
                method, params, replay_safe=True), cwd)
        except AppServerError:
            fast_mode = {'allowed': False}
        return {'kind': 'models', 'models': models, 'modelSource': 'codex',
                'fastMode': fast_mode,
                'modelError': None, 'currentModel': state.get('latestModel'),
                'currentEffort': state.get('latestReasoningEffort')}

    def get_kind(self, thread_id, kind, refresh=False, query='', offset=0, limit=200, ids=None):
        self._check_joined_identity(thread_id)
        if kind == 'models':
            with self.lock:
                state = self.states[thread_id]
                cwd = self.metadata[thread_id]['cwd']
                provider = self.metadata[thread_id]['modelProvider']
            return self.model_catalog(cwd, provider, state)
        if kind != 'skills':
            raise ValueError('未知目录类型')
        rows, errors = self._native_skills(thread_id, force_reload=bool(refresh))
        ids = set(ids or [])
        selected = [row for row in rows if row['id'] in ids]
        query = (query or '').strip().casefold()
        filtered = [row for row in rows if query in ' '.join(str(row.get(key) or '') for key in
                    ('name', 'displayName', 'description')).casefold()]
        offset = max(0, int(offset))
        limit = min(500, max(1, int(limit)))
        return {'kind': 'skills', 'skills': filtered[offset:offset + limit],
                'selectedSkills': selected, 'errors': errors, 'total': len(filtered),
                'offset': offset, 'limit': limit,
                'cache': {'state': 'fresh', 'updatedAt': int(time.time()),
                          'bucketId': None, 'backgroundRefresh': False}}

    def _native_skills(self, thread_id, force_reload=False):
        with self.lock:
            cwd = self.metadata[thread_id]['cwd']
        raw = self.client.request('skills/list', {'cwds': [cwd], 'forceReload': force_reload},
                                  replay_safe=True)
        return normalize_native_skills(raw, cwd)

    def validate_skills(self, thread_id, ids):
        self._check_joined_identity(thread_id)
        ids = list(dict.fromkeys(ids or []))[:8]
        if not ids:
            return []
        rows, _ = self._native_skills(thread_id, force_reload=True)
        indexed = {row['id']: row for row in rows}
        if any(identifier not in indexed for identifier in ids):
            raise ValueError('Skill 不可用，请刷新列表')
        return [indexed[identifier] for identifier in ids]

    def _goal_request(self, thread_id, method, params, *, read=False):
        try:
            self._check_joined_identity(thread_id)
            return self.client.request(method, {'threadId': thread_id, **params},
                                       replay_safe=read)
        except AppServerUnsupported as exc:
            raise GoalUnsupported('当前 CLI runtime 不支持原生目标') from exc
        except (AppServerUnavailable, AppServerUnknownResult) as exc:
            raise GoalUnavailable('CLI runtime 目标操作结果未知，请刷新后确认') from exc
        except AppServerError as exc:
            raise GoalError('CLI runtime 目标操作未完成') from exc

    def supports_goal(self, thread_id):
        try:
            self._goal_request(thread_id, 'thread/goal/get', {}, read=True)
            return True
        except GoalUnsupported:
            return False

    def get_goal(self, thread_id):
        return normalize_goal_result(self._goal_request(thread_id, 'thread/goal/get', {}, read=True))

    def set_goal(self, thread_id, objective):
        return normalize_goal_result(self._goal_request(thread_id, 'thread/goal/set',
                                                       {'objective': objective, 'status': 'active'}))

    def set_goal_status(self, thread_id, status):
        if status not in ('active', 'paused'):
            raise ValueError('目标状态无效')
        return normalize_goal_result(self._goal_request(thread_id, 'thread/goal/set', {'status': status}))

    def edit_goal(self, thread_id, objective, token_budget=None):
        params = {'objective': objective, 'status': 'paused'}
        if token_budget is not None:
            params['tokenBudget'] = token_budget
        return normalize_goal_result(self._goal_request(thread_id, 'thread/goal/set', params))

    def clear_goal(self, thread_id):
        return self._goal_request(thread_id, 'thread/goal/clear', {})

    @staticmethod
    def _inputs(inputs, context):
        result = []
        for value in inputs:
            kind = value.get('type')
            if kind in ('text', 'localImage', 'skill'):
                result.append({key: value[key] for key in ('type', 'text', 'text_elements', 'path', 'name') if key in value})
            else:
                raise AppServerUnsupported('Unsupported CLI input type')
        files = context.get('fileAttachments') or []
        if files:
            for item in files:
                path = item.get('path')
                if not path or not Path(path).is_absolute():
                    raise AppServerUnsupported('Invalid CLI file attachment path')
            from bridge.features.sessions.model import attachment_prompt
            references = [{'path': item['path'], 'name': item.get('label') or Path(item['path']).name} for item in files]
            first_text = next((item for item in result if item['type'] == 'text'), None)
            if first_text is None:
                result.insert(0, {'type': 'text', 'text': attachment_prompt('', references)})
            else:
                first_text['text'] = attachment_prompt(first_text.get('text', ''), references)
        return result

    @staticmethod
    def _state(thread, requests, metadata):
        status = thread.get('status') or {'type': 'idle'}
        turns = []
        for turn in thread.get('turns') or []:
            row = {'turnId': turn.get('id'), 'status': turn.get('status'),
                          'turnStartedAtMs': (turn.get('startedAt') or 0) * 1000,
                          'items': turn.get('items') or [], 'error': turn.get('error')}
            if turn.get('model'):
                row['params'] = {'model': turn['model'], 'effort': turn.get('reasoningEffort')}
            turns.append(row)
        state = {'id': thread['id'], 'title': thread.get('name') or thread.get('preview') or '未命名聊天',
                'cwd': thread.get('cwd') or metadata['cwd'],
                'modelProvider': thread.get('modelProvider') or metadata['modelProvider'],
                'latestModel': thread.get('model') or metadata['model'],
                'latestReasoningEffort': (thread['reasoningEffort'] if 'reasoningEffort' in thread
                                          else metadata.get('reasoningEffort')),
                'threadRuntimeStatus': status, 'turns': turns, 'requests': requests}
        if metadata.get('serviceTier') is not None:
            state['latestThreadSettings'] = {'serviceTier': metadata['serviceTier']}
        if metadata.get('permissions'):
            permissions = copy.deepcopy(metadata['permissions'])
            if 'sandbox' in permissions:
                permissions['sandboxPolicy'] = permissions.pop('sandbox')
            state['currentPermissions'] = permissions
        if metadata.get('collaborationMode') is not None:
            state['latestCollaborationMode'] = copy.deepcopy(metadata['collaborationMode'])
        if metadata.get('goal') is not None:
            state['threadGoal'] = copy.deepcopy(metadata['goal'])
        return state

    def _require_joined(self, thread_id):
        with self.lock:
            if self.joined.get(thread_id) is not self.client.process or self.client.process is None:
                raise AppServerUnavailable('CLI thread is not joined on this connection')

    def _check_identity(self, thread_id):
        result = self.client.request('thread/read', {'threadId': thread_id,
                                                      'includeTurns': False}, replay_safe=True)
        thread = result.get('thread') or {}
        with self.lock:
            metadata = self.metadata.get(thread_id)
        if metadata is None:
            raise AppServerUnavailable('CLI thread is no longer joined')
        if (thread.get('id') != thread_id or thread.get('status', {}).get('type') == 'notLoaded' or
                not same_cwd(metadata['cwd'], thread.get('cwd'), self.alias is None and os.name == 'nt') or
                thread.get('modelProvider') != metadata['modelProvider']):
            self._invalidate(thread_id)
            raise AppServerUnavailable('CLI thread identity changed before the operation')

    def _event(self, event):
        method = event.get('method')
        params = event.get('params') or {}
        thread_id = params.get('threadId')
        with self.lock:
            joined = thread_id in self.joined
        if not joined:
            return
        if method == 'thread/closed' or (method == 'thread/status/changed' and
                (params.get('status') or {}).get('type') == 'notLoaded'):
            self._invalidate(thread_id)
            return
        with self.lock:
            state = self.states.get(thread_id)
            if state is None:
                self._queue_refresh(thread_id)
                return
            if method == 'serverRequest/resolved':
                self.requests.get(thread_id, {}).pop(params.get('requestId'), None)
                state['requests'] = list(self.requests.get(thread_id, {}).values())
            elif method == 'thread/settings/updated':
                settings = params.get('threadSettings') or {}
                state['latestThreadSettings'] = copy.deepcopy(settings)
                if all(key in settings for key in ('approvalPolicy', 'approvalsReviewer', 'sandboxPolicy')):
                    state['currentPermissions'] = {key: copy.deepcopy(settings[key]) for key in
                        ('approvalPolicy', 'approvalsReviewer', 'sandboxPolicy', 'activePermissionProfile') if key in settings}
                if settings.get('model'):
                    state['latestModel'] = settings['model']
                if settings.get('effort') is not None:
                    state['latestReasoningEffort'] = settings['effort']
                if settings.get('collaborationMode') is not None:
                    state['latestCollaborationMode'] = copy.deepcopy(settings['collaborationMode'])
            elif method == 'thread/goal/updated':
                goal = params.get('goal')
                state['threadGoal'] = copy.deepcopy(goal)
                self.metadata[thread_id]['goal'] = copy.deepcopy(goal)
                state.pop('completedThreadGoal', None)
            elif method == 'thread/goal/cleared':
                state.pop('threadGoal', None)
                state.pop('completedThreadGoal', None)
                self.metadata[thread_id]['goal'] = None
            elif method == 'turn/plan/updated':
                state['turnPlan'] = copy.deepcopy(params.get('plan') or [])
            elif method == 'thread/status/changed':
                state['threadRuntimeStatus'] = params.get('status') or state['threadRuntimeStatus']
            elif method == 'error':
                # The native error notification explicitly separates automatic
                # retry from a terminal failure requiring user intervention.
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                if turn is not None and type(params.get('willRetry')) is bool:
                    turn.update(error=copy.deepcopy(params.get('error')), willRetry=params['willRetry'])
                    turn['status'] = 'inProgress' if params['willRetry'] else 'failed'
            elif method in ('turn/started', 'turn/completed'):
                turn = params.get('turn') or {}
                row = next((item for item in state['turns'] if item['turnId'] == turn.get('id')), None)
                if row is None:
                    row = {'turnId': turn.get('id'), 'items': []}
                    state['turns'].append(row)
                if method == 'turn/started':
                    model = turn.get('model') or state.get('latestModel')
                    effort = turn.get('reasoningEffort') or state.get('latestReasoningEffort')
                    if model:
                        row['params'] = {'model': model, 'effort': effort}
                elif turn.get('model'):
                    row['params'] = {'model': turn['model'], 'effort': turn.get('reasoningEffort')}
                row.update(status=turn.get('status'), error=turn.get('error'))
                row.pop('willRetry', None)
                state['threadRuntimeStatus'] = {'type': 'active' if method == 'turn/started' else 'idle'}
                if turn.get('items'):
                    items = turn['items']
                    if method == 'turn/completed':
                        incoming_ids = {item.get('id') for item in items}
                        # Completed event items can omit user messages still present in thread/read.
                        items = [item for item in row['items'] if item.get('type') == 'userMessage'
                                 and item.get('id') not in incoming_ids] + items
                    row['items'] = items
                if method == 'turn/completed':
                    pending = self.requests.get(thread_id, {})
                    for request_id in list(pending):
                        if (pending[request_id].get('params') or {}).get('turnId') == turn.get('id'):
                            pending.pop(request_id)
                    state['requests'] = list(pending.values())
            elif method in ('item/started', 'item/completed'):
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                if turn and isinstance(params.get('item'), dict):
                    item = params['item']
                    items = turn['items']
                    for index, existing in enumerate(items):
                        if existing.get('id') == item.get('id'):
                            if method == 'item/started' and item.get('type') == existing.get('type') == 'reasoning':
                                # A late start must not erase deltas already received.
                                item = {**item, **{field: existing[field] for field in ('summary', 'content')
                                                  if not item.get(field) and isinstance(existing.get(field), list)}}
                            items[index] = item
                            break
                    else:
                        items.append(item)
            elif method == 'item/agentMessage/delta':
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                if turn:
                    item = next((item for item in turn['items'] if item.get('id') == params.get('itemId')), None)
                    if item is None:
                        item = {'id': params.get('itemId'), 'type': 'agentMessage', 'text': ''}
                        turn['items'].append(item)
                    if isinstance(params.get('delta'), str):
                        item['text'] = item.get('text', '') + params['delta']
            elif method == 'item/commandExecution/outputDelta':
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                if turn:
                    item = next((item for item in turn['items'] if item.get('id') == params.get('itemId')), None)
                    if item is not None and isinstance(params.get('delta'), str):
                        item['aggregatedOutput'] = item.get('aggregatedOutput', '') + params['delta']
            elif method in ('item/reasoning/textDelta', 'item/reasoning/summaryTextDelta'):
                # Third-party Responses backends stream raw chain of thought and
                # never fill `summary`; keep both streams so the phone can show
                # progress before `item/completed` replaces the item wholesale.
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                delta = params.get('delta')
                if turn and isinstance(delta, str):
                    summary = method.endswith('summaryTextDelta')
                    field = 'summary' if summary else 'content'
                    slot = params.get('summaryIndex' if summary else 'contentIndex', 0)
                    if type(slot) is not int or not 0 <= slot < MAX_REASONING_PARTS or not isinstance(params.get('itemId'), str) or not params['itemId']:
                        return  # Untrusted sparse indices must not allocate an unbounded list.
                    item = next((item for item in turn['items'] if item.get('id') == params.get('itemId')), None)
                    if item is None:
                        item = {'id': params.get('itemId'), 'type': 'reasoning', 'summary': [], 'content': []}
                        turn['items'].append(item)
                    if item.get('type') != 'reasoning':
                        return
                    parts = item.get(field)
                    if not isinstance(parts, list):
                        parts = []
                        item[field] = parts
                    while len(parts) <= slot:
                        parts.append('')
                    parts[slot] = (parts[slot] if isinstance(parts[slot], str) else '') + delta
            else:
                return
            snapshot = copy.deepcopy(state)
        self.on_state(thread_id, snapshot)

    def _server_request(self, request):
        thread_id = (request.get('params') or {}).get('threadId')
        with self.lock:
            if thread_id not in self.joined and thread_id not in self.attaching:
                return
            if request['id'] in self.consumed_requests.get(thread_id, set()):
                return
            self.requests.setdefault(thread_id, {})[request['id']] = request
            state = self.states.get(thread_id)
            if state is not None:
                state['requests'] = list(self.requests[thread_id].values())
                snapshot = copy.deepcopy(state)
            else:
                snapshot = None
        if snapshot is not None:
            self.on_state(thread_id, snapshot)

    def _queue_refresh(self, thread_id):
        with self.lock:
            if thread_id in self.refreshing:
                self.refresh_pending.add(thread_id)
                return
            self.refreshing.add(thread_id)
            process = self.joined.get(thread_id)
            cancel = threading.Event()
            self.refresh_cancel[thread_id] = cancel
        def run():
            try:
                read_retry_delay = .25
                while True:
                    if cancel.is_set():
                        return
                    try:
                        self.refresh(thread_id)
                    except AppServerUnavailable:
                        with self.lock:
                            still_joined = (process is not None and not cancel.is_set() and
                                            self.joined.get(thread_id) is process and
                                            self.client.process is process)
                        if not still_joined:
                            raise
                        # The last turn event may need this replay-safe read
                        # to converge. Keep one bounded-backoff worker until
                        # it succeeds or the native join is revoked.
                        if cancel.wait(read_retry_delay):
                            return
                        read_retry_delay = min(15, read_retry_delay * 4)
                        continue
                    read_retry_delay = .25
                    with self.lock:
                        if thread_id not in self.refresh_pending:
                            break
                        self.refresh_pending.discard(thread_id)
            except AppServerUnavailable:
                # A timed-out read does not revoke the already joined writer.
                # A real proxy disconnect invokes _disconnect independently.
                pass
            except AppServerError:
                if not cancel.is_set():
                    self._invalidate(thread_id)
            finally:
                with self.lock:
                    if self.refresh_cancel.get(thread_id) is cancel:
                        self.refresh_cancel.pop(thread_id, None)
                        self.refreshing.discard(thread_id)
                        self.refresh_pending.discard(thread_id)
        threading.Thread(target=run, daemon=True).start()

    def _disconnect(self):
        with self.lock:
            for cancel in self.refresh_cancel.values():
                cancel.set()
            self.refresh_cancel.clear()
            self.refreshing.clear()
            self.joined.clear()
            self.attaching.clear()
            self.metadata.clear()
            self.states.clear()
            self.requests.clear()
            self.consumed_requests.clear()
            self.refresh_pending.clear()
        self.on_disconnect()

    def _invalidate(self, thread_id, expected_process=None):
        with self.lock:
            if expected_process is not None and self.joined.get(thread_id) is not expected_process:
                return
            cancel = self.refresh_cancel.pop(thread_id, None)
            if cancel is not None:
                cancel.set()
            self.refreshing.discard(thread_id)
            self.refresh_pending.discard(thread_id)
            self.joined.pop(thread_id, None)
            self.attaching.discard(thread_id)
            self.metadata.pop(thread_id, None)
            self.states.pop(thread_id, None)
            self.requests.pop(thread_id, None)
            self.consumed_requests.pop(thread_id, None)
        self.on_disconnect(thread_id)

    def close(self):
        self.client.close()
        self._disconnect()
