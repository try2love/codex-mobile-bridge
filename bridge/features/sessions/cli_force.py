"""Explicitly terminate one verified local CLI daemon generation."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import uuid

from bridge.clients.codex.app_server import AppServerError, CodexAppServer
from bridge.clients.codex.cli_session import CliSessions, _local_cli, _managed_cli
from bridge.clients.codex.daemon_lifecycle import maintenance_lock
from bridge.clients.codex.daemon_network import (
    AUTH_ENV_NAMES, PROXY_NAMES, automatic_proxy_update, configuration_stamp,
    native_recovery_target, proxy_update_stamp)
from bridge.clients.codex.ipc import IPCError
from bridge.clients.codex.release import ReleaseNotSent
from bridge.features.sessions.store import StoreUnavailable
from bridge.platforms.codex_daemon_env import (
    DaemonEnvironmentUnavailable, daemon_generation_absent, daemon_process_absent, force_daemon_exit,
    daemon_scope_user, force_daemon_supported, read_daemon_environment)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _socket_identity(path):
    path = Path(path)
    try:
        link = path.lstat()
    except FileNotFoundError:
        return None
    try:
        target = path.stat()
    except FileNotFoundError:
        target = None
    return [[value.st_dev, value.st_ino, value.st_mode, value.st_ctime_ns] if value else None
            for value in (link, target)]


def maintenance_daemon_status(cli, record):
    status = cli.daemon_status()
    if (status == 'unknown' and record.get('forceExitObserved') and record.get('oldSocket')
            and _socket_identity(cli.client.socket_path) == record['oldSocket']
            and daemon_process_absent(cli.home)):
        # A forced process may leave the original socket on disk. Do not
        # remove it here: native start validates and replaces stale sockets.
        return 'stopped'
    return status


class CliForce:
    def __init__(self, bridge, save):
        self.bridge, self.save = bridge, save
        self.path = bridge.data_dir/'cli-force-receipts.json'

    def _scope(self):
        bridge, cli = self.bridge, self.bridge.cli_sessions
        if (not force_daemon_supported() or not bridge.local or not isinstance(cli, CliSessions)
                or cli.alias is not None or cli.home.resolve() != bridge.codex_home.resolve()
                or bridge.closed.is_set() or not bridge.observation_enabled()):
            raise ReleaseNotSent('此入口不能核验本机 CLI 的强制退出范围')
        return cli

    def available(self):
        try:
            self._scope()
            return True
        except ReleaseNotSent:
            return False

    def scope_descriptor(self):
        cli = self._scope()
        home = str(cli.home.resolve())
        return {'scopeKey': _digest(['codex', self.bridge.host, home, daemon_scope_user()]),
                'scopeLabel': self.bridge.host_label + ' · ' + home, 'sharedHome': True}

    def _receipts(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def status(self, thread_id, operation_id):
        receipts = self._receipts()
        if not isinstance(operation_id, str):
            return None
        row = receipts.get(operation_id)
        if row and row['threadId'] == thread_id and not row['connection']:
            return {**row['result'], 'threadId': thread_id,
                    'message': row['result']['notice'],
                    'state': 'unknown' if row['result']['status'] == 'unknown' else 'complete'}
        for newer, row in reversed(list(receipts.items())):
            previous = row['previousMaintenance']
            if previous.get('operationId') == operation_id and previous.get('threadId') == thread_id:
                return {'operationId': operation_id, 'threadId': thread_id, 'state': 'superseded',
                        'replacedBy': newer, 'message': '已改为明确强制退出，请查看新的请求。'}
        return None

    def _reconcile(self, cli, request_id, receipt, receipts):
        bridge = self.bridge
        result = receipt['result']
        if receipt.get('intent') == 'reconnect':
            return result
        record = bridge._cli_recovery_record()
        if result['status'] != 'unknown' or record.get('forceRequestId') != request_id:
            return result
        if record.get('phase') == 'starting' and not receipt['connection']:
            executable = _managed_cli(cli.home)
            if executable is None and cli.allow_installed_runtime:
                executable = _local_cli(cli.home, cli.configured_cli)
            try:
                if executable is not None:
                    bridge._resume_cli_recovery(cli, executable, record, check_only=True)
            except (ValueError, OSError, AppServerError, IPCError):
                return result
            if record['phase'] == 'complete':
                result.update(status='started', notice='已核验新 CLI 服务；未重放会话接续或消息。')
                self.save(self.path, receipts)
            return result
        if record.get('phase') != 'draining':
            return result
        if not record.get('forceExitObserved'):
            if not daemon_generation_absent(receipt['processGeneration']):
                return result
            record['forceExitObserved'] = True
        if maintenance_daemon_status(cli, record) != 'stopped':
            return result
        cli.close()
        cli.drained_connections.update(record['connections'])
        record['phase'] = 'released' if receipt['connection'] else 'stopped'
        result.update(status=record['phase'], notice='原 CLI 进程已退出；未重放原操作。')
        self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
        self.save(self.path, receipts)
        return result

    def check_connection(self, thread_id, connection):
        bridge, cli = self.bridge, self._scope()
        with bridge.accounts.gate, cli.start_lock, maintenance_lock(cli.home):
            receipts = self._receipts()
            for request_id, row in reversed(list(receipts.items())):
                if row['threadId'] == thread_id and row['connection'] == connection:
                    return self._reconcile(cli, request_id, row, receipts)
        return None

    @staticmethod
    def _identity(cli, thread_id):
        row = cli.store.get(thread_id)
        if (row.get('id') != thread_id or row.get('origin') != 'cli'
                or not isinstance(row.get('cwd'), str) or not Path(row['cwd']).is_absolute()
                or not isinstance(row.get('model_provider'), str) or not row['model_provider']):
            raise ReleaseNotSent('不能核验当前配置的原 CLI 会话')
        return {'id': thread_id, 'cwd': row['cwd'], 'modelProvider': row['model_provider']}

    def _impact(self, cli, thread_id):
        with cli.lock:
            known = {identifier: row.get('connectionToken') for identifier, row in cli.metadata.items()}
        with self.bridge.submit_lock:
            queued = {key.split(':', 1)[0] for key, row in self.bridge.submissions.items() if row.get('status') == 'queued'}
        for identifier in queued - known.keys():
            try:
                self._identity(cli, identifier)
                known[identifier] = None
            except (ValueError, KeyError, OSError, StoreUnavailable):
                continue
        known.setdefault(thread_id, None)
        return [{'sessionId': identifier, 'connectionKey': known[identifier]} for identifier in sorted(known)]

    def _snapshot(self, cli, thread_id, connection, previous):
        bridge = self.bridge
        home = str(cli.home.resolve())
        if (previous.get('host', bridge.host) != bridge.host or previous.get('home', home) != home):
            raise ReleaseNotSent('旧维护记录不属于此主机或配置，未强制退出')
        identity = self._identity(cli, thread_id)
        executable = _managed_cli(cli.home)
        if executable is None and cli.allow_installed_runtime:
            executable = _local_cli(cli.home, cli.configured_cli)
        if executable is None:
            raise ReleaseNotSent('无法核验原 CLI 运行组件')
        generation = read_daemon_environment(cli.home, executable, (), require_home=True)[1]
        configuration = configuration_stamp(cli.home)
        old_socket = _socket_identity(cli.client.socket_path)
        impact = self._impact(cli, thread_id)
        operation = previous.get('operationId') if previous.get('phase') in (
            'waiting', 'draining', 'starting', 'resuming', 'configuringUnknown') else None
        scope = self.scope_descriptor()
        binding = _digest(['cli-force-v1', scope['scopeKey'], generation, configuration, old_socket, identity,
                           connection, impact, previous])
        preview = {**scope, 'canForce': True, 'generation': binding, 'impact': impact,
                   'operationId': operation, 'taskState': 'unknown',
                   'message': '将强制退出此配置的共享 CLI 服务，可能中断其中未列出的任务；已保存的聊天会保留。'}
        return preview, executable, generation, configuration, identity, old_socket

    def preview(self, thread_id, *, connection=None):
        try:
            cli = self._scope()
            return self._snapshot(cli, thread_id, connection, self.bridge._cli_recovery_record())[0]
        except (ValueError, KeyError, OSError, AppServerError, StoreUnavailable):
            return {'canForce': False, 'message': '当前无法核验原 CLI 进程或配置；未强制退出。'}

    def _reconnect_snapshot(self, cli, thread_id, record):
        home = str(cli.home.resolve())
        unknown = record.get('unresolvedResume')
        receipt = self._receipts().get(record.get('forceRequestId'), {})
        generation = unknown.get('processGeneration') if unknown else None
        generation = generation or receipt.get('processGeneration')
        if (not unknown or record.get('host') != self.bridge.host or record.get('home') != home
                or not generation or not daemon_generation_absent(generation)):
            raise ReleaseNotSent('以前接续的原进程尚未确认退出；不会重新接续')
        identity = self._identity(cli, thread_id)
        executable = _managed_cli(cli.home)
        if executable is None and cli.allow_installed_runtime:
            executable = _local_cli(cli.home, cli.configured_cli)
        if executable is None:
            raise ReleaseNotSent('当前 CLI 运行组件尚未核验')
        status = maintenance_daemon_status(cli, record)
        current = (read_daemon_environment(cli.home, executable, (), require_home=True)[1]
                   if status == 'running' else None)
        if status not in ('running', 'stopped'):
            raise ReleaseNotSent('当前 CLI 进程身份尚未核验；不会重新接续')
        binding = _digest(['cli-reconnect-v1', self.bridge.host, home, record, identity,
                           configuration_stamp(cli.home), current, _socket_identity(cli.client.socket_path)])
        return {'canRecover': True, 'requiresConfirmation': True, 'action': 'reconnect', 'generation': binding,
                'message': '原服务已退出。确认从当前保存的聊天重新连接；以前的接续回执仍未知，不重放旧提供商设置或消息。'}, executable, identity

    def reconnect_preview(self, thread_id):
        try:
            return self._reconnect_snapshot(self._scope(), thread_id, self.bridge._cli_recovery_record())[0]
        except (ValueError, KeyError, OSError, StoreUnavailable):
            return {'canRecover': False, 'requiresConfirmation': True,
                    'message': '以前的 CLI 接续结果仍未知，原进程或当前配置尚未核验；不会重新接续。'}

    def reconnect(self, thread_id, request_id, confirmation):
        bridge, cli = self.bridge, self._scope()
        uuid.UUID(request_id)
        with bridge.accounts.gate:
            return self._reconnect_locked(cli, thread_id, request_id, confirmation)

    def _reconnect_locked(self, cli, thread_id, request_id, confirmation):
        bridge = self.bridge
        with cli.start_lock, maintenance_lock(cli.home):
            receipts = self._receipts()
            key = _digest(['reconnect', thread_id, confirmation])
            if request_id in receipts:
                if receipts[request_id]['confirmation'] != key:
                    raise ReleaseNotSent('此请求标识已用于不同的 CLI 操作')
                return dict(receipts[request_id]['result'], duplicate=True)
            bridge.accounts.check_ready()
            previous = bridge._cli_recovery_record()
            preview, executable, identity = self._reconnect_snapshot(cli, thread_id, previous)
            if confirmation != preview['generation']:
                raise ReleaseNotSent('CLI 配置、进程或保存的会话已变化，请重新检查')
            record = {name: value for name, value in previous.items() if name in (
                'generation', 'configuration', 'connections', 'host', 'home', 'forceExitObserved',
                'forceRequestId', 'oldSocket')}
            record.update(purpose='recovery', phase='stopped', operationId=request_id,
                          threadId=thread_id, targetThread=identity, releasedThread=identity,
                          confirmation={'action': 'reconnect', 'generation': confirmation, 'requestId': request_id})
            result = {'status': 'unknown', 'operationId': request_id, 'connected': False,
                      'notice': '新连接结果待核对；不会重放以前的操作。'}
            receipt = {'confirmation': key, 'result': result, 'previousMaintenance': previous,
                       'threadId': thread_id, 'connection': None, 'intent': 'reconnect'}
            receipts[request_id] = receipt
            self.save(self.path, receipts)
            # Keep the blocker through startup; only the new explicit intent
            # may establish a fresh subscription without the old override.
            record['unresolvedResume'] = previous['unresolvedResume']
            self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
            try:
                bridge._continue_released_cli(cli, executable, record, thread_id, fresh_continuation=True)
                record = bridge._cli_recovery_record()
                current = read_daemon_environment(cli.home, executable, (), require_home=True)[1]
                record['unresolvedResume'] = {'operationId': request_id, 'targetThread': identity,
                                              'processGeneration': current}
                self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
            except (ValueError, OSError, AppServerError, IPCError):
                self.save(self.path, receipts)
                return result

        # _connect_explicit takes start_lock again after a failed proxy
        # connection. Keep home/account serialization, not that process lock.
        with maintenance_lock(cli.home):
            def verify_intent():
                active = bridge._cli_recovery_record()
                if (active.get('operationId') != request_id or not active.get('unresolvedResume')
                        or self._identity(self._scope(), thread_id) != identity
                        or configuration_stamp(cli.home) != record['configuration']
                        or read_daemon_environment(cli.home, executable, (), require_home=True)[1] != current):
                    raise ReleaseNotSent('明确重新连接的进程、配置或会话已变化；未接续原操作')
            try:
                verify_intent()
                plan = {'provider': identity['modelProvider'], 'before': identity}
                session = bridge.activate(thread_id, _cli_recovery=(plan, verify_intent))
                if not session.connected:
                    raise ValueError('新会话连接尚未确认')
                verify_intent()
                record.pop('unresolvedResume')
                record['phase'] = 'complete'
                self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
                result.update(status='recovered', connected=True, notice='已从当前保存状态重新连接；以前的回执仍未知。')
            except (ValueError, OSError, AppServerError, IPCError):
                pass
            self.save(self.path, receipts)
            return result

    def _restart_environment(self, cli, executable, generation, identity, previous):
        """Read configuration, never query or wait for native task state."""
        from bridge.platforms.codex_daemon_env import configuration_after_start
        try:
            with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                        client_name='codex-mobile-bridge-target-check')) as probe:
                if Path(probe.connect(timeout=2)['codexHome']).resolve() != cli.home.resolve():
                    raise ReleaseNotSent('原生服务不属于此配置')
                snapshot = probe.request('config/read', {'cwd': identity['cwd'], 'includeLayers': True},
                                         timeout=2, replay_safe=True)
        except AppServerError:
            # If even config is unavailable, force can still release the
            # verified writer; it cannot guess an injected account to restart.
            return None
        config = snapshot.get('config')
        if not isinstance(config, dict):
            return None
        names = {*AUTH_ENV_NAMES, *PROXY_NAMES, 'NO_PROXY', 'no_proxy', 'CODEX_SQLITE_HOME', 'CODEX_CA_CERTIFICATE'}
        for provider in {identity['modelProvider'], config.get('model_provider') or 'openai',
                         *((config.get('model_providers') or {}).keys())}:
            names.update(cli._required_model_environment(config, provider))
        try:
            old, current = read_daemon_environment(cli.home, executable, names, require_home=True)
            changed = configuration_after_start(cli.home, generation)
        except DaemonEnvironmentUnavailable:
            return None
        if current != generation:
            raise ReleaseNotSent('原 CLI 进程已变化，未强制退出')
        preserved = {name: old.get(name) for name in names}
        provider_plan = None
        if (changed or cli.configuration_stamp is not None and
                cli.configuration_stamp != configuration_stamp(cli.home)
                or previous.get('followAccount') and not previous.get('followProxy')):
            try:
                target = native_recovery_target(cli.home, config, identity['modelProvider'], old,
                                                snapshot.get('layers') or [])
            except (ValueError, OSError):
                return None
            if target.get('unknownAuthSource') or target['provider'] != identity['modelProvider']:
                return None
            preserved.update(target['environment'])
            provider_plan = {key: target.get(key) for key in ('accountId', 'identity', 'external', 'kind', 'provider')}
            provider_plan['before'] = identity
        auto_proxy, updates = (automatic_proxy_update(config, identity['modelProvider'], old, preserved)
                               if self.bridge.host == 'local' and previous.get('followProxy') else (None, {}))
        for name in updates:
            preserved.pop(name, None)
        return {'preserved': preserved, 'updates': updates, 'autoProxySpec': auto_proxy, 'providerPlan': provider_plan}

    def _cancel_queued(self, cli, known):
        bridge = self.bridge
        cancelled = 0
        with bridge.submit_lock:
            for key, row in bridge.submissions.items():
                if row.get('status') != 'queued':
                    continue
                identifier = key.split(':', 1)[0]
                if identifier not in known:
                    try:
                        self._identity(cli, identifier)
                    except (ValueError, KeyError, OSError, StoreUnavailable):
                        continue
                row['status'] = 'cancelled'
                cancelled += 1
            if cancelled:
                bridge._save_ledger()
        return cancelled

    def execute(self, thread_id, request_id, confirmation, *, operation=None, connection=None):
        bridge, cli = self.bridge, self._scope()
        uuid.UUID(request_id)
        with bridge.accounts.gate, cli.start_lock, maintenance_lock(cli.home):
            receipts = self._receipts()
            key = _digest([thread_id, confirmation, operation, connection])
            if request_id in receipts:
                prior = receipts[request_id]
                if prior['confirmation'] != key:
                    raise ReleaseNotSent('此请求标识已用于不同的 CLI 操作')
                return dict(self._reconcile(cli, request_id, prior, receipts), duplicate=True)
            bridge.accounts.check_ready()
            previous = bridge._cli_recovery_record()
            preview, executable, generation, configuration, identity, old_socket = self._snapshot(
                cli, thread_id, connection, previous)
            if preview['generation'] != confirmation or connection is None and preview['operationId'] != operation:
                raise ReleaseNotSent('CLI 配置、进程或影响范围已变化，请重新预览')
            # An earlier unknown resume may already have changed persistent
            # thread state. A new force intent does not replay that request.
            unresolved_resume = (previous if previous.get('phase') == 'resuming'
                                 else previous.get('unresolvedResume'))
            can_resume = not unresolved_resume
            environment = (self._restart_environment(cli, executable, generation, identity, previous)
                           if connection is None else None)
            generation_key = hashlib.sha256(json.dumps(generation).encode()).hexdigest()
            record = {'purpose': 'release' if connection else 'recovery', 'phase': 'draining',
                      'operationId': request_id, 'forceRequestId': request_id,
                      'generation': generation_key, 'configuration': configuration,
                      'connections': sorted({row['connectionKey'] for row in preview['impact'] if row['connectionKey']}
                                            | ({connection} if connection else set())),
                      'host': bridge.host, 'home': str(cli.home.resolve()), 'oldSocket': old_socket}
            if unresolved_resume:
                record['unresolvedResume'] = unresolved_resume
            if connection:
                record.update(releasedThread=identity, releaseKey=connection)
            else:
                record.update(threadId=thread_id, targetThread=identity,
                              confirmation={'confirmed': True, 'generation': confirmation})
                if environment and environment['providerPlan']:
                    record['providerTarget'] = environment['providerPlan']
                if environment and environment['autoProxySpec']:
                    scheme = environment['autoProxySpec']['scheme']
                    record['automaticProxy'] = {'scheme': scheme, 'stamp': proxy_update_stamp(environment['updates'], scheme)}
            dispatched = False
            result = {'status': 'unknown', 'operationId': request_id, 'connected': False,
                      'scopeKey': preview['scopeKey'], 'impact': preview['impact'],
                      'notice': 'CLI 强制退出结果待核对；不会重放原操作。'}
            receipt = {'confirmation': key, 'result': result, 'previousMaintenance': previous,
                       'threadId': thread_id, 'connection': connection, 'processGeneration': generation}

            def checkpoint():
                nonlocal dispatched
                if (self._snapshot(self._scope(), thread_id, connection, bridge._cli_recovery_record())[0]['generation']
                        != confirmation):
                    raise ReleaseNotSent('CLI 配置、进程或影响范围已变化，未强制退出')
                with bridge.cli_maintenance.lock:
                    receipts[request_id] = receipt
                    self.save(self.path, receipts)
                    self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
                    bridge.cli_maintenance.cancel()
                    result['cancelledQueued'] = self._cancel_queued(cli, {row['sessionId'] for row in preview['impact']})
                    result['forceDispatched'] = True
                    self.save(self.path, receipts)
                    dispatched = True

            try:
                force_daemon_exit(cli.home, executable, generation, checkpoint)
            except (OSError, ValueError) as error:
                if not dispatched:
                    raise ReleaseNotSent('未能核验并强制退出原 CLI 进程') from error
                return result
            cli.close()
            record['forceExitObserved'] = True
            self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
            if maintenance_daemon_status(cli, record) != 'stopped':
                return result
            cli.drained_connections.update(record['connections'])
            record['phase'] = 'released' if connection else 'stopped'
            self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
            if connection:
                result.update(status='released', notice='已退出此配置的原生 CLI 服务并释放原生占用。')
            else:
                result.update(status='stopped', notice='原 CLI 已退出；先在原工具核对以前未确认的接续结果。'
                              if unresolved_resume else '原 CLI 已退出，尚未确认重新连接。')
                if environment is not None:
                    cli.recovery_environment = {'generation': generation_key, **environment}
                    if can_resume:
                        try:
                            bridge._resume_cli_recovery(cli, executable, record)
                            result.update(status='started', notice='新 CLI 已启动，尚未确认原会话连接。')
                        except (ValueError, OSError, AppServerError, IPCError):
                            result.update(status='unknown', notice='新 CLI 启动结果待核对；不会重复退出或启动。')
                    else:
                        result['notice'] = '原 CLI 已退出；先核对以前未确认的接续结果。'
            self.save(self.path, receipts)
        if not connection and environment is not None and can_resume and record['phase'] == 'complete':
            try:
                session = bridge.activate(thread_id, _expected_cli=identity)
                result.update(status='recovered' if session.connected else 'started', connected=session.connected,
                              notice='已连接原 CLI 会话。' if session.connected else 'CLI 已启动，原会话尚未连接。')
            except (ValueError, OSError, AppServerError, IPCError) as error:
                result.update(status='started', notice='CLI 已启动，原生会话未确认接续；请检查原工具。')
                details = getattr(error, 'details', None) or getattr(error.__cause__, 'details', None)
                if details and details.get('reason') == 'modelProviderMismatch':
                    result['reason'] = details
            with bridge.accounts.gate, cli.start_lock:
                receipts = self._receipts()
                receipts[request_id]['result'] = result
                self.save(self.path, receipts)
        return result
