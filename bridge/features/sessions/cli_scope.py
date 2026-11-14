"""Confirmed shared-home writer release using the existing maintenance receipt."""
from contextlib import closing
import hashlib
import json
from pathlib import Path

from bridge.clients.codex.app_server import AppServerError, CodexAppServer
from bridge.clients.codex.daemon_lifecycle import maintenance_lock
from bridge.clients.codex.daemon_network import AUTH_ENV_NAMES, PROXY_NAMES, graceful_restart_supported
from bridge.clients.codex.release import ReleaseNotSent
from bridge.features.sessions.cli_maintenance import CliTasksPending
from bridge.platforms.codex_daemon_env import daemon_generation_absent, read_daemon_environment


class CliScope:
    def __init__(self, bridge, save):
        self.bridge, self.save = bridge, save
        self.cancelled, self.dispatched = set(), set()
        self.target = None  # SSH/WSL inject target identity and operations, never another queue.

    def descriptor(self):
        if self.target is not None:
            value = self.target.control.cached()
            return self._target_descriptor(value) if value else {}
        try:
            return self.bridge.cli_force.scope_descriptor()
        except (OSError, ValueError):
            return {}

    def preview(self, thread, key):
        if self.target is not None:
            try:
                return self._target_snapshot(thread, key, self.bridge._cli_recovery_record())[0]
            except (OSError, ValueError, KeyError):
                return {'canRelease': False, 'canForce': False, 'message': '目标 CLI 进程与配置尚未核验；原逐条退订仍可单独核对。'}
        force = self.bridge.cli_force
        try:
            cli = force._scope()
            preview, executable, *_ = force._snapshot(cli, thread, key, self.bridge._cli_recovery_record())
        except (OSError, ValueError, KeyError):
            return {'canRelease': False, 'canForce': False, 'message': '当前无法核验原 CLI 配置与进程。'}
        preview.update(canRelease=False, message='将释放此配置的整个共享 CLI 服务，包含其它连接和未列出的任务；默认等待安全结束。')
        try:
            with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                        client_name='codex-mobile-bridge-target-check')) as probe:
                info = probe.connect(timeout=2)
                preview['canRelease'] = graceful_restart_supported(info) and Path(info.get('codexHome', '')).resolve() == cli.home.resolve()
        except (OSError, AppServerError):
            pass
        return preview

    def _result(self, record, status):
        return {'status': status, 'scopeKey': record['scopeKey'], 'impact': record['scopeImpact'],
                'operationId': record['scopeRequestId'], 'canCancel': status == 'waiting',
                'notice': '原生写入者已退出，此配置的原连接已释放。' if status == 'released'
                else '正在等待此配置的全部任务安全结束；尚未退出或释放原生占用。'}

    def record_released(self, result):
        if self.target is not None:
            self.target.released(result)
            return
        self.record_local_released(result)

    def record_local_released(self, result):
        bridge, cli = self.bridge, self.bridge.cli_sessions
        for row in result['impact']:
            sid, token = row['sessionId'], row.get('connectionKey')
            with cli.lock:
                current = cli.metadata.get(sid)
                if current and current.get('connectionToken') != token:
                    continue  # A receipt for the old writer cannot disconnect a new connection.
            bridge.released_cli.set(sid, True)
            with bridge.lock:
                session = bridge.live.get(sid)
            if session is not None:
                bridge._record_cli_release(session, result)

    def check(self, key):
        if self.target is not None:
            record = self.bridge._cli_recovery_record()
            if not record.get('targetGeneration') or key not in record.get('connections', ()):
                return None
            if record['phase'] == 'released':
                return self._result(record, 'released')
            if record['phase'] == 'waiting':
                return self._result(record, 'waiting')
            receipt = self.target.control.check(record['scopeRequestId'], record['targetGeneration'])
            if receipt['status'] == 'exited':
                record['phase'] = 'released'
                self.save(self.bridge.data_dir/'cli-daemon-recovery.json', record)
                return self._result(record, 'released')
            return {'status': 'unknown', 'notice': '目标原代退出仍待核验；检查未重新派发。'}
        bridge, cli = self.bridge, self.bridge.cli_sessions
        record = bridge._cli_recovery_record()
        if (not record.get('scopeRequestId') or key not in record.get('connections', ())
                or record.get('scopeKey') != self.descriptor().get('scopeKey')):
            return None
        if record['phase'] == 'released':
            return self._result(record, 'released')
        if record['phase'] == 'draining' and daemon_generation_absent(record['scopeProcessGeneration']):
            record['phase'] = 'released'
            cli.drained_connections.update(record['connections'])
            self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
            return self._result(record, 'released')
        return {'status': 'waiting' if record['phase'] == 'waiting' else 'unknown',
                'notice': '原 CLI 释放结果尚待核验；检查不会重新退出服务。'}

    def cancel(self, operation_id):
        bridge = self.bridge
        with bridge.cli_maintenance.lock:
            record = bridge._cli_recovery_record()
            own = record.get('scopeRequestId') == operation_id
            if operation_id in self.dispatched or own and record.get('phase') != 'waiting':
                raise ReleaseNotSent('释放已进入退出阶段或已经结束，不能取消；请核验结果')
            self.cancelled.add(operation_id)
            if own:
                record['phase'] = 'cancelled'
                self.save(bridge.data_dir/'cli-daemon-recovery.json', record)
            return {'status': 'cancelled', 'notice': '已取消本次等待，未为此请求退出服务。'}

    def check_cancelled(self, operation_id):
        if operation_id in self.cancelled:
            raise ReleaseNotSent('释放等待已取消；未退出服务')

    @staticmethod
    def release_environment(cli, executable, probe):
        config = probe.request('config/read', {'includeLayers': False}, replay_safe=True)['config']
        names = {*AUTH_ENV_NAMES, *PROXY_NAMES, 'CODEX_SQLITE_HOME', 'CODEX_CA_CERTIFICATE', 'NO_PROXY', 'no_proxy'}
        for provider in (config.get('model_providers') or {}):
            names.update(cli._required_model_environment(config, provider))
        values, generation = read_daemon_environment(cli.home, executable, names, require_home=True)
        return {name: values.get(name) for name in names}, generation

    def release(self, thread, key, operation_id, binding):
        if self.target is not None:
            return self._target_release(thread, key, operation_id, binding, force=False)
        from bridge.features.accounts.cli_sync import LocalCliSync
        bridge, force = self.bridge, self.bridge.cli_force
        cli = force._scope()
        with bridge.accounts.gate, cli.start_lock, maintenance_lock(cli.home):
            self.check_cancelled(operation_id)
            current = bridge._cli_recovery_record()
            own = current.get('scopeRequestId') == operation_id
            if own and current['phase'] in ('draining', 'released'):
                return self.check(key) or {'status': 'unknown'}
            if own and current['phase'] != 'waiting':
                raise ReleaseNotSent('释放等待已取消或配置已变化；未退出服务')
            previous = current['scopeBefore'] if own else current
            preview, executable, generation, configuration, identity, _ = force._snapshot(cli, thread, key, previous)
            if binding != preview['generation']:
                if own:
                    with bridge.cli_maintenance.lock:
                        current = bridge._cli_recovery_record()
                        if current.get('scopeRequestId') == operation_id and current['phase'] == 'waiting':
                            current['phase'] = 'failed'
                            self.save(bridge.data_dir/'cli-daemon-recovery.json', current)
                raise ReleaseNotSent('CLI 配置、原进程或影响范围已变化，请重新预览')
            if not own:
                if (current.get('unresolvedResume') or current.get('phase') in (
                        'waiting', 'draining', 'starting', 'resuming', 'configuringUnknown')):
                    return {'status': 'waiting', 'notice': '此配置已有未完成的维护或未知结果；可核对后继续或明确强制释放。'}
                current = {'purpose': 'release', 'phase': 'waiting', 'operationId': operation_id,
                    'scopeRequestId': operation_id, 'scopeKey': preview['scopeKey'], 'scopeBinding': binding,
                    'scopeBefore': previous, 'scopeProcessGeneration': generation, 'scopeImpact': preview['impact'],
                    'host': bridge.host, 'home': str(cli.home.resolve()), 'threadId': thread, 'targetThread': identity,
                    'releasedThread': identity, 'releaseKey': key, 'configuration': configuration,
                    'generation': hashlib.sha256(json.dumps(generation).encode()).hexdigest(),
                    'connections': sorted({row['connectionKey'] for row in preview['impact'] if row['connectionKey']} | {key}),
                    'confirmation': {'generation': binding}}
                with bridge.cli_maintenance.lock:
                    self.check_cancelled(operation_id)
                    self.save(bridge.data_dir/'cli-daemon-recovery.json', current)
            with bridge.submit_lock, cli.lock:
                busy = (cli.attaching or any(cli.requests.values()) or any(
                    row.get('status') in ('queued', 'unknown') for row in bridge.submissions.values()))
            if busy:
                return self._result(current, 'waiting')
            try:
                with closing(CodexAppServer(cli.client.socket_path, cli=executable,
                                            client_name='codex-mobile-bridge-final-idle-check')) as probe:
                    if not graceful_restart_supported(probe.connect()):
                        raise ReleaseNotSent('此原生版本尚未核验正常排空能力')
                    LocalCliSync.check_idle_daemon(probe, cli.home)
                    preserved, observed = self.release_environment(cli, executable, probe)
                    if observed != generation:
                        raise ReleaseNotSent('CLI 原生进程已变化，未退出服务')
            except (CliTasksPending, AppServerError, OSError):
                return self._result(current, 'waiting')
            try:
                bridge._drain_cli_daemon(cli, executable, generation, configuration, {}, preserved,
                    connections=current['connections'], release_operation=(operation_id, identity, key),
                    waiting_operation=operation_id)
            except ReleaseNotSent as error:
                if isinstance(error.__cause__, (CliTasksPending, AppServerError, OSError)):
                    return self._result(current, 'waiting')
                raise
            result = self._result(bridge._cli_recovery_record(), 'released')
            self.record_released(result)
            return result

    def force(self, thread, key, operation_id, binding):
        if self.target is not None:
            return self._target_release(thread, key, operation_id, binding, force=True)
        result = self.bridge.cli_force.execute(thread, operation_id, binding, connection=key)
        if result.get('forceDispatched'):
            record = self.bridge._cli_recovery_record()
            if record.get('forceRequestId') == operation_id:
                receipt = self.bridge.cli_force._receipts()[operation_id]
                record.update(scopeRequestId=operation_id, scopeKey=result['scopeKey'],
                              scopeImpact=result['impact'], scopeProcessGeneration=receipt['processGeneration'])
                self.save(self.bridge.data_dir/'cli-daemon-recovery.json', record)
        if result['status'] == 'released':
            self.record_released(result)
        return result

    def _target_descriptor(self, value):
        from bridge.features.sessions.cli_force import _digest
        generation = value['generation']
        return {'scopeKey': _digest(['codex', self.bridge.host, generation['home'],
                                     generation['uid'], generation['user']]),
                'scopeLabel': self.target.label + ' · ' + generation['user'] + ' · ' + generation['home'],
                'sharedHome': True}

    @staticmethod
    def _target_generation(value):
        # A failed version read cannot remove a verified force capability.
        return {key: item for key, item in value['generation'].items() if key != 'nativeVersion'}

    def _target_snapshot(self, thread, key, previous):
        from bridge.features.sessions.cli_force import _digest
        from pathlib import PurePosixPath
        if self.bridge.closed.is_set() or not self.target.enabled():
            raise ReleaseNotSent('目标 CLI 接入已关闭')
        value = self.target.control.inspect()
        row = self.target.saved(thread, value['generation']['home'])
        if (row.get('id') != thread or row.get('origin') != 'cli'
                or not isinstance(row.get('cwd'), str) or not PurePosixPath(row['cwd']).is_absolute()
                or not isinstance(row.get('model_provider'), str) or not row['model_provider']):
            raise ReleaseNotSent('目标不是已核验的原 CLI 会话')
        identity = {'id': thread, 'cwd': row['cwd'], 'modelProvider': row['model_provider']}
        impact = self.target.impact(value['generation']['home'])
        if not any(item['sessionId'] == thread for item in impact):
            impact.append({'sessionId': thread, 'connectionKey': key})
        impact = sorted(impact, key=lambda item: item['sessionId'])
        descriptor = self._target_descriptor(value)
        binding = _digest([descriptor['scopeKey'], self._target_generation(value), value['configuration'], identity, key, impact, previous])
        return {**descriptor, 'generation': binding, 'impact': impact, 'canForce': value['canForce'],
                'canRelease': value['canRelease'], 'taskState': 'unknown',
                'message': '将释放目标配置的整个共享 CLI 服务，可能影响未列出的任务；默认等待安全结束。'}, value, identity

    def _target_failed_wait(self, operation_id):
        with self.bridge.cli_maintenance.lock:
            record = self.bridge._cli_recovery_record()
            if record.get('scopeRequestId') == operation_id and record['phase'] == 'waiting':
                record['phase'] = 'failed'
                self.save(self.bridge.data_dir/'cli-daemon-recovery.json', record)

    def _target_release(self, thread, key, operation_id, binding, *, force):
        from bridge.features.accounts.cli_sync import LocalCliSync
        bridge, target = self.bridge, self.target
        with target.guard():
            self.check_cancelled(operation_id)
            current = bridge._cli_recovery_record()
            own = current.get('scopeRequestId') == operation_id
            if own and current['phase'] in ('draining', 'released'):
                result = self.check(key) or {'status': 'unknown'}
                if result['status'] == 'unknown' and current['phase'] == 'draining':
                    return {**result, 'status': 'waiting', 'canCancel': False} if not force else result
                if result['status'] == 'released':
                    self.record_released(result)
                return result
            if own and current['phase'] != 'waiting':
                raise ReleaseNotSent('本次释放已取消或状态已变化')
            previous = current['scopeBefore'] if own else current
            try:
                preview, value, identity = self._target_snapshot(thread, key, previous)
            except (ValueError, OSError) as error:
                if own:
                    self._target_failed_wait(operation_id)
                raise ReleaseNotSent('目标只读身份核验失败；未派发退出，请重新预览') from error
            if binding != preview['generation']:
                if own:
                    self._target_failed_wait(operation_id)
                raise ReleaseNotSent('目标主机、进程、配置或连接已变化，请重新预览')
            if not preview['canForce' if force else 'canRelease']:
                raise ReleaseNotSent('目标原生控制能力尚未核验')
            if not own:
                if not force and (current.get('unresolvedResume') or current.get('phase') in (
                        'waiting', 'draining', 'starting', 'resuming', 'configuringUnknown')):
                    return {'status': 'waiting', 'notice': '目标已有未完成维护；可取消本次等待或明确强制释放。'}
                current = {'purpose': 'release', 'phase': 'waiting', 'scopeRequestId': operation_id,
                    'operationId': operation_id, 'scopeKey': preview['scopeKey'], 'scopeImpact': preview['impact'],
                    'scopeBefore': previous, 'scopeBinding': binding, 'targetGeneration': value['generation'],
                    'host': bridge.host, 'home': value['generation']['home'], 'threadId': thread,
                    'targetThread': identity, 'configuration': value['configuration'],
                    'connections': sorted({item['connectionKey'] for item in preview['impact'] if item['connectionKey']} | {key})}
                if not force:
                    with bridge.cli_maintenance.lock:
                        self.check_cancelled(operation_id)
                        self.save(bridge.data_dir/'cli-daemon-recovery.json', current)
            if not force:
                if target.pending(value['generation']['home']):
                    return self._result(current, 'waiting')
                try:
                    with target.probe(value) as probe:
                        home = (value.get('initialize') or {}).get('codexHome') or value['generation']['home']
                        LocalCliSync.check_idle_daemon(probe, home, target_platform='linux')
                except (CliTasksPending, AppServerError, OSError, ValueError):
                    return self._result(current, 'waiting')
            # Identity/config are fresh; task uncertainty is bypassed only by force.
            fresh = target.control.inspect()
            if self._target_generation(fresh) != self._target_generation(value) or fresh['configuration'] != value['configuration']:
                if not force or own:
                    self._target_failed_wait(operation_id)
                raise ReleaseNotSent('目标原生进程或配置已变化；未派发退出')
            with bridge.cli_maintenance.lock:
                self.check_cancelled(operation_id)
                recorded = bridge._cli_recovery_record()
                valid = (recorded == previous if force and not own else
                         recorded.get('scopeRequestId') == operation_id and recorded.get('phase') == 'waiting')
                if not valid:
                    raise ReleaseNotSent('目标释放意图已变化；未派发退出')
                current['phase'] = 'draining'
                self.save(bridge.data_dir/'cli-daemon-recovery.json', current)
                self.dispatched.add(operation_id)
                queued = target.cancel_queued(value['generation']['home'], operation_id, preview['scopeKey']) if force else 0
                cancelled = queued.get('cancelledQueued', 0) if isinstance(queued, dict) else queued
                if isinstance(queued, dict) and queued.get('unresolvedQueued'):
                    current['unresolvedQueued'] = queued['unresolvedQueued']
                    self.save(bridge.data_dir/'cli-daemon-recovery.json', current)
            try:
                result = target.control.dispatch(operation_id, 'force' if force else 'graceful', value['generation'])
            except (ValueError, OSError):
                return {'status': 'unknown', 'forceDispatched': force, 'cancelledQueued': cancelled,
                        'notice': '目标退出请求结果未知；请核验原代，不会自动重发。'}
            if result['status'] == 'exited':
                current['phase'] = 'released'
                self.save(bridge.data_dir/'cli-daemon-recovery.json', current)
                result = self._result(current, 'released')
                self.record_released(result)
            else:
                result = {'status': 'unknown' if force else 'waiting', 'canCancel': False,
                          'notice': '目标退出已请求，仍在核验原代；未重放业务操作。'}
            return {**result, 'forceDispatched': force, 'cancelledQueued': cancelled}
