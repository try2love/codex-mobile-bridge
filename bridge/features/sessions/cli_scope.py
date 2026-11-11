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

    def descriptor(self):
        try:
            return self.bridge.cli_force.scope_descriptor()
        except (OSError, ValueError):
            return {}

    def preview(self, thread, key):
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
