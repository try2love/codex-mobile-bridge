"""CLI change detection and explicitly confirmed recovery; no real model/process."""
from contextlib import ExitStack, closing
import hashlib
import json
import os
import sqlite3
import sys
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.cli_session import CliSessions, _managed_cli
from bridge.clients.codex.daemon_network import configuration_stamp, graceful_restart_supported
from bridge.clients.codex.ipc import IPCError
from bridge.features.sessions.store import SessionStore
from tests.test_cli_bridge_integration import CliBridgeFixture


class CliNetworkRecoveryTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        (self.home / 'config.toml').write_text('model_provider="openai"\n[features]\nrespect_system_proxy=false\n')
        self.system_proxy_enabled = False
        self.auth_cache = None
        self.current_config = {'model_provider': 'openai', 'cli_auth_credentials_store': 'file'}
        self.thread_provider, self.thread_loaded = 'openai', True
        self.cli_store_provider = None
        self.cli = CliSessions(self.home, self.bridge._cli_state, self.bridge._cli_disconnected)
        self.cli.store.get = Mock(side_effect=lambda identifier: {'id': identifier, 'origin': 'cli',
            'cwd': self.native_state['cwd'], 'model_provider': self.cli_store_provider or self.thread_provider})
        self.cli.configuration_stamp = configuration_stamp(self.home)
        self.bridge.cli_sessions = self.cli
        self.cli.client.socket_path = str(self.home / 'fixture.sock')
        (self.home / 'fixture.sock').touch()
        self.cli._start_daemon = Mock(side_effect=lambda *_: setattr(self, 'proxy_updates', {}))
        self.cli.daemon_status = Mock(return_value='stopped')
        self.cli.close = Mock(side_effect=self.cli.close)
        def attach(identifier, **kwargs):
            if kwargs.get('before_resume'):
                kwargs['before_resume']()
            self.cli.metadata[identifier] = {'cwd': self.native_state['cwd'],
                                             'modelProvider': self.native_state['modelProvider']}
            self.thread_provider, self.thread_loaded = self.native_state['modelProvider'], True
            return self.native_state
        self.cli.attach = Mock(side_effect=attach)
        self.native_calls = []
        self.busy = False
        self.generation = ('fixture-pid', 'generation')
        self.proxy_updates = {'HTTPS_PROXY': 'http://127.0.0.1:15937'}
        def request(method, params, **kwargs):
            self.native_calls.append((method, params))
            if method == 'thread/read':
                return {'thread': {'id': params['threadId'], 'cwd': self.native_state['cwd'],
                                   'modelProvider': self.thread_provider, 'status':
                                   {'type': 'notLoaded' if not self.thread_loaded else 'active' if self.busy else 'idle'}}}
            if method == 'thread/backgroundTerminals/list': return {'data': []}
            if method == 'thread/goal/get': return {'goal': None}
            if method == 'config/read':
                layer = {'name': {'type': 'user', 'file': str(self.home / 'config.toml'), 'profile': None},
                         'version': 'sha256:' + hashlib.sha256((self.home / 'config.toml').read_bytes()).hexdigest(),
                         'config': {'features': {'respect_system_proxy': self.system_proxy_enabled}}}
                return {'config': {**self.current_config, 'features': layer['config']['features']},
                        'layers': [layer], 'origins': {'features.respect_system_proxy':
                            {'name': layer['name'], 'version': layer['version']}}}
            if method == 'config/value/write':
                self.system_proxy_enabled = True
                (self.home / 'config.toml').write_text('model_provider="openai"\n[features]\nrespect_system_proxy=true\n')
                return {'status': 'ok', 'filePath': str(self.home / 'config.toml'),
                        'version': 'sha256:' + hashlib.sha256((self.home / 'config.toml').read_bytes()).hexdigest()}
            if method == 'getAuthStatus':
                import json
                file = self.home / 'auth.json'
                key = json.loads(file.read_text()).get('OPENAI_API_KEY') if file.exists() else None
                return {'authMethod': 'apikey', 'authToken': self.auth_cache or key}
            raise AssertionError(method)
        self.probe = SimpleNamespace(connect=lambda: {'codexHome': str(self.home), 'userAgent': 'codex_cli_rs/0.164.0'},
                                     loaded_threads=lambda: ['unrelated-thread'], request=request, close=Mock())
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch('bridge.clients.codex.cli_session._managed_cli', return_value=self.home / 'codex'))
        self.patches.enter_context(patch('bridge.clients.codex.daemon_network.inspect_proxy',
            side_effect=lambda *_: (self.generation, self.proxy_updates, 'fixture-stamp')))
        self.patches.enter_context(patch('bridge.clients.codex.app_server.CodexAppServer', return_value=self.probe))
        self.patches.enter_context(patch('bridge.platforms.codex_daemon_env.read_daemon_environment',
            side_effect=lambda *_: ({'OPENAI_API_KEY': 'synthetic-key', 'HTTPS_PROXY': 'http://127.0.0.1:15722'}, self.generation)))
        self.stop = self.patches.enter_context(patch('bridge.platforms.codex_daemon_env.stop_daemon_gracefully'))

    def confirm(self):
        preview = self.bridge.cli_network(self.thread_id)
        self.assertTrue(preview['canRecover'])
        return self.bridge.cli_network(self.thread_id, {'confirmed': True, 'generation': preview['generation']})

    def test_change_detection_never_automatically_exits_shared_daemon(self):
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.connected, session.runtime, session.state = True, 'cli', self.native_state
        self.assertIs(self.bridge.activate(self.thread_id), session)
        self.stop.assert_not_called()
        self.assertTrue(session.connected)
        self.assertTrue(self.confirm()['connected'])
        self.stop.assert_called_once_with(self.home, self.home / 'codex', self.generation, graceful_only=True)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.assertFalse(any(method in ('turn/start', 'turn/interrupt', 'thread/resume') for method, _ in self.native_calls))

    def test_environment_change_allows_original_native_attach_without_restart(self):
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.runtime, session.desktop_owner_probe = 'cli', 'absent'
        self.bridge._attach = Mock()
        (self.home / 'auth.json').write_text('{"auth_mode":"apikey","OPENAI_API_KEY":"rotated-fixture"}')
        self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()

    def test_advisory_probe_failure_does_not_block_native_attach(self):
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.runtime, session.desktop_owner_probe = 'cli', 'absent'
        self.bridge._attach = Mock()
        with patch.object(self.bridge, 'cli_network', side_effect=OSError('proxy inspection failed')):
            self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.stop.assert_not_called()

    def test_pending_lifecycle_still_blocks_native_attach_on_probe_failure(self):
        from bridge.platforms.codex_daemon_env import DaemonEnvironmentUnavailable
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.runtime = 'cli'
        for error in (OSError('inspection failed'), DaemonEnvironmentUnavailable('identity unavailable')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(self.bridge, '_cli_recovery_record', return_value={'phase': 'draining'}), \
                    patch.object(self.bridge, 'cli_network', side_effect=error):
                with self.assertRaises(IPCError):
                    self.bridge.activate(self.thread_id)
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def test_maintenance_started_during_probe_still_blocks_native_attach(self):
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.runtime = 'cli'
        with patch.object(self.bridge, '_cli_recovery_record', side_effect=[{}, {}, {'phase': 'draining'}]):
            with self.assertRaises(IPCError):
                self.bridge.activate(self.thread_id)
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def test_maintenance_started_during_failed_probe_still_blocks_native_attach(self):
        with patch.object(self.bridge, '_cli_recovery_record', side_effect=[{}, {'phase': 'draining'}]), \
                patch.object(self.bridge, 'cli_network', side_effect=OSError('inspection failed')):
            with self.assertRaises(IPCError):
                self.bridge._recover_cli_environment(self.thread_id)
        self.cli.attach.assert_not_called()

    def test_other_active_thread_waits_before_confirmed_recovery(self):
        self.busy = True
        preview = self.bridge.cli_network(self.thread_id)
        self.assertTrue(preview['canRecover'])
        self.assertTrue(preview['willWait'])
        self.assertIn('unrelated-thread', preview['message'])
        self.stop.assert_not_called()
        self.cli.close.assert_not_called()
        body = {'confirmed': True, 'generation': preview['generation']}
        with patch('bridge.features.sessions.cli_maintenance.WAIT_INTERVAL', .01):
            pending = self.bridge.cli_network(self.thread_id, body)
            self.assertEqual(pending['state'], 'waiting')
            self.assertEqual(self.bridge.cli_network(self.thread_id, body)['operationId'], pending['operationId'])
            self.stop.assert_not_called()
            self.cli._start_daemon.assert_not_called()
            self.cli.attach.assert_not_called()
            self.busy = False
            self.bridge.cli_maintenance.worker.join(2)
            self.assertFalse(self.bridge.cli_maintenance.worker.is_alive())
        result = self.bridge.cli_network(self.thread_id, {'action': 'status', 'operationId': pending['operationId']})
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(result['connected'])
        self.stop.assert_called_once_with(self.home, self.home / 'codex', self.generation, graceful_only=True)
        self.cli._start_daemon.assert_called_once()
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True,
            expected_identity={'id': self.thread_id, 'cwd': self.native_state['cwd'], 'modelProvider': 'openai'})

    def test_refreshed_credentials_are_detected_and_confirmed_recovery_preserves_proxy(self):
        self.proxy_updates = {}
        (self.home / 'auth.json').write_text('{"auth_mode":"apikey","OPENAI_API_KEY":"rotated-fixture"}')
        detected = self.bridge.cli_network(self.thread_id, automatic=True)
        self.assertTrue(detected['requiresConfirmation'])
        self.stop.assert_not_called()
        self.confirm()
        self.assertEqual(self.cli._start_daemon.call_args.args[1]['HTTPS_PROXY'], 'http://127.0.0.1:15722')
        self.assertEqual(self.cli.configuration_stamp, configuration_stamp(self.home))

    def test_no_change_does_not_probe_or_restart_native_runtime(self):
        self.proxy_updates = {}
        self.bridge.cli_network(self.thread_id, automatic=True)
        self.assertEqual(self.native_calls, [])
        self.stop.assert_not_called()

    def test_unknown_submission_blocks_restart_without_replay(self):
        self.bridge.submissions['unrelated:submission'] = {'status': 'unknown'}
        preview = self.bridge.cli_network(self.thread_id)
        self.assertTrue(preview['willWait'])
        body = {'confirmed': True, 'generation': preview['generation']}
        pending = self.bridge.cli_network(self.thread_id, body)
        self.assertEqual(pending['state'], 'waiting')
        cancelled = self.bridge.cli_network(self.thread_id, {'action': 'cancel', 'operationId': pending['operationId']})
        self.assertEqual(cancelled['state'], 'cancelled')
        self.bridge.cli_maintenance.worker.join(2)
        self.assertFalse(self.bridge.cli_maintenance.worker.is_alive())
        self.assertEqual(self.bridge.cli_network(self.thread_id, body)['operationId'], pending['operationId'])
        self.assertNotEqual(self.bridge.cli_network(self.thread_id)['generation'], preview['generation'])
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.assertEqual(self.bridge.submissions['unrelated:submission']['status'], 'unknown')

    def test_waiting_recovery_aborts_if_process_generation_changes(self):
        self.busy = True
        with patch('bridge.features.sessions.cli_maintenance.WAIT_INTERVAL', .01):
            pending = self.confirm()
            self.generation = ('replacement-pid', 'replacement-generation')
            self.busy = False
            self.bridge.cli_maintenance.worker.join(2)
        result = self.bridge.cli_network(self.thread_id, {'action': 'status', 'operationId': pending['operationId']})
        self.assertEqual(result['state'], 'failed')
        self.assertIn('服务或配置已变化', result['message'])
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()

    def test_waiting_proxy_only_recovery_rejects_paired_thread_identity_changes(self):
        original = self.native_state.copy()
        updates = self.proxy_updates.copy()
        # Both providers are aliases of the same route: either identity alone
        # leaves the provider migration plan empty in this fixture.
        self.current_config['model_providers'] = {'bridge_api': {'requires_openai_auth': True}}
        for field in ('cwd', 'modelProvider'):
            with self.subTest(field=field), patch('sys.platform', 'linux'), \
                    patch('bridge.features.sessions.cli_maintenance.WAIT_INTERVAL', .01):
                self.native_state.update(original)
                self.thread_provider = self.cli_store_provider = original['modelProvider']
                self.proxy_updates = updates.copy()
                self.busy = True
                pending = self.confirm()
                if field == 'cwd':
                    self.native_state['cwd'] = '/private/user/moved-fixture'
                else:
                    self.native_state['modelProvider'] = 'bridge_api'
                    self.thread_provider = self.cli_store_provider = 'bridge_api'
                self.busy = False
                self.bridge.cli_maintenance.worker.join(2)
                result = self.bridge.cli_network(self.thread_id,
                    {'action': 'status', 'operationId': pending['operationId']})
                self.assertEqual(result['state'], 'failed')
                self.assertIn('恢复目标', result['message'])
                self.stop.assert_not_called()
                self.cli._start_daemon.assert_not_called()
                self.cli.attach.assert_not_called()

    def test_waiting_recovery_rechecks_target_identity_after_final_idle_audit(self):
        from bridge.features.accounts.cli_sync import LocalCliSync
        check = LocalCliSync.check_idle_daemon
        self.busy = True
        audits = 0
        def moved_after_idle(probe, home):
            nonlocal audits
            check(probe, home)
            if threading.current_thread().name == 'cli-maintenance-wait':
                audits += 1
                if audits == 2:
                    self.native_state['cwd'] = '/private/user/moved-after-audit'
        with patch('sys.platform', 'linux'), patch.object(LocalCliSync, 'check_idle_daemon', side_effect=moved_after_idle), \
                patch('bridge.features.sessions.cli_maintenance.WAIT_INTERVAL', .01):
            pending = self.confirm()
            self.busy = False
            self.bridge.cli_maintenance.worker.join(2)
        result = self.bridge.cli_network(self.thread_id, {'action': 'status', 'operationId': pending['operationId']})
        self.assertEqual(result['state'], 'failed')
        self.assertIn('恢复目标的会话身份已变化', result['message'])
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()

    def test_status_and_cancel_do_not_wait_for_an_inflight_idle_check(self):
        from bridge.features.accounts.cli_sync import LocalCliSync
        check = LocalCliSync.check_idle_daemon
        for platform in ('linux', 'darwin'):
            with self.subTest(platform=platform), patch('sys.platform', platform):
                self.busy = True
                entered, release = threading.Event(), threading.Event()
                def blocked(probe, home):
                    if threading.current_thread().name == 'cli-maintenance-wait':
                        entered.set()
                        self.assertTrue(release.wait(2))
                    return check(probe, home)
                with patch.object(LocalCliSync, 'check_idle_daemon', side_effect=blocked):
                    pending = self.confirm()
                    try:
                        self.assertTrue(entered.wait(1))
                        request = {'action': 'status', 'operationId': pending['operationId']}
                        self.assertEqual(self.bridge.cli_network(self.thread_id, request)['state'], 'waiting')
                        self.assertFalse(release.is_set())
                        self.assertEqual(self.bridge.cli_network(self.thread_id,
                            {**request, 'action': 'cancel'})['state'], 'cancelled')
                        self.busy = False
                    finally:
                        release.set()
                    self.bridge.cli_maintenance.worker.join(2)
                    self.assertFalse(self.bridge.cli_maintenance.worker.is_alive())
                self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'cancelled')
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.assertFalse(any(method == 'config/value/write' for method, _ in self.native_calls))

    def test_unknown_native_task_state_waits_until_readable(self):
        from bridge.clients.codex.app_server import AppServerUnavailable
        request = self.probe.request
        unknown = True
        def read(method, params, **kwargs):
            if unknown and method == 'thread/backgroundTerminals/list':
                raise AppServerUnavailable('fixture task list unavailable')
            return request(method, params, **kwargs)
        with patch.object(self.probe, 'request', side_effect=read), \
                patch('bridge.features.sessions.cli_maintenance.WAIT_INTERVAL', .01):
            pending = self.confirm()
            self.assertEqual(pending['state'], 'waiting')
            self.stop.assert_not_called()
            unknown = False
            self.bridge.cli_maintenance.worker.join(2)
        result = self.bridge.cli_network(self.thread_id, {'action': 'status', 'operationId': pending['operationId']})
        self.assertEqual(result['state'], 'complete')
        self.stop.assert_called_once()

    def test_last_idle_check_can_queue_instead_of_dispatching_exit(self):
        from bridge.features.accounts.cli_sync import LocalCliSync
        check = LocalCliSync.check_idle_daemon
        calls = 0
        def newly_busy(probe, home):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.busy = True
            return check(probe, home)
        with patch('sys.platform', 'linux'), patch.object(LocalCliSync, 'check_idle_daemon', side_effect=newly_busy), \
                patch('bridge.features.sessions.cli_maintenance.WAIT_INTERVAL', .01):
            pending = self.confirm()
            self.assertEqual(pending['state'], 'waiting')
            self.stop.assert_not_called()
            self.busy = False
            self.bridge.cli_maintenance.worker.join(2)
        result = self.bridge.cli_network(self.thread_id, {'action': 'status', 'operationId': pending['operationId']})
        self.assertEqual(result['state'], 'complete')
        self.stop.assert_called_once()

    def test_restart_pauses_waiting_without_status_triggering_native_work(self):
        from bridge.app.service import Bridge
        self.busy = True
        pending = self.confirm()
        old = self.bridge
        old.close()
        old.cli_maintenance.worker.join(2)
        self.assertFalse(old.cli_maintenance.worker.is_alive())
        self.bridge = Bridge(self.home, old.data_dir, cli_sessions=self.cli)
        self.native_calls.clear()
        paused = self.bridge.cli_network(self.thread_id)
        self.assertEqual(paused['operationId'], pending['operationId'])
        self.assertEqual(paused['state'], 'waiting')
        self.assertTrue(paused['canRecover'])
        self.assertIsNone(self.bridge.cli_maintenance.worker)
        self.assertEqual(self.native_calls, [])
        self.busy = False
        self.assertEqual(self.bridge.cli_network(self.thread_id,
            {'confirmed': True, 'generation': paused['generation']})['operationId'], pending['operationId'])
        self.bridge.cli_maintenance.worker.join(2)
        result = self.bridge.cli_network(self.thread_id, {'action': 'status', 'operationId': pending['operationId']})
        self.assertEqual(result['state'], 'complete')
        self.stop.assert_called_once()
        self.cli._start_daemon.assert_called_once()

    def test_waiting_recovery_keeps_unknown_write_receipt_without_replay(self):
        self.busy = True
        error = OSError('fixture lost setting receipt')
        error.system_proxy_unknown = True
        with patch('sys.platform', 'darwin'), \
                patch('bridge.features.sessions.cli_maintenance.WAIT_INTERVAL', .01), \
                patch('bridge.clients.codex.daemon_network.enable_system_proxy_following', side_effect=error) as write:
            preview = self.bridge.cli_network(self.thread_id)
            body = {'confirmed': True, 'generation': preview['generation']}
            pending = self.bridge.cli_network(self.thread_id, body)
            self.busy = False
            self.bridge.cli_maintenance.worker.join(2)
            result = self.bridge.cli_network(self.thread_id, body)
            write.assert_called_once()
        self.assertEqual(result['operationId'], pending['operationId'])
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'configuringUnknown')
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()

    def test_confirmed_recovery_does_not_compete_with_same_home_maintenance(self):
        from bridge.clients.codex.desktop_runtime import exclusive_lock
        preview = self.bridge.cli_network(self.thread_id)
        with exclusive_lock(self.home, 'cli-maintenance'):
            with self.assertRaisesRegex(ValueError, '仍在使用或退出中'):
                self.bridge.cli_network(self.thread_id, {'confirmed': True, 'generation': preview['generation']})
        self.assertEqual(self.bridge._cli_recovery_record(), {})
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()

    def test_process_generation_change_aborts_before_stop(self):
        preview = self.bridge.cli_network(self.thread_id)
        with patch('bridge.platforms.codex_daemon_env.read_daemon_environment', return_value=({}, ('replacement',))):
            with self.assertRaisesRegex(ValueError, '服务已变化'):
                self.bridge.cli_network(self.thread_id, {'confirmed': True, 'generation': preview['generation']})
        self.stop.assert_not_called()

    def test_bridge_start_after_config_change_checks_daemon_birth_without_exiting(self):
        self.proxy_updates = {}
        self.cli.configuration_stamp = None
        with patch('bridge.platforms.codex_daemon_env.configuration_after_start', return_value=True) as age:
            self.assertTrue(self.bridge.cli_network(self.thread_id, automatic=True)['requiresConfirmation'])
        age.assert_called_once_with(self.home, self.generation)
        self.stop.assert_not_called()

    def test_timed_out_drain_never_signals_twice_and_resumes_only_after_confirmation(self):
        self.cli.daemon_status.return_value = 'running'
        self.stop.side_effect = OSError('still draining')
        with self.assertRaisesRegex(ValueError, '设置已保存，CLI 服务尚未恢复'): self.confirm()
        record = self.bridge._cli_recovery_record()
        self.assertEqual(record['phase'], 'draining')
        self.bridge.cli_network(self.thread_id, automatic=True)
        self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        self.assertEqual(self.stop.call_count, 1)
        self.cli.daemon_status.return_value = 'stopped'
        self.assertTrue(self.confirm()['recovered'])
        self.assertEqual(self.stop.call_count, 1)
        self.cli._start_daemon.assert_called_once()

    def test_confirmed_macos_recovery_saves_one_setting_and_verifies_replacement(self):
        with patch('sys.platform', 'darwin'):
            preview = self.bridge.cli_network(self.thread_id)
            self.assertIn('CODEX_HOME', preview['message'])
            self.assertIn('其他客户端', preview['message'])
            self.assertIn('不修改系统代理', preview['message'])
            self.assertFalse(any(method == 'config/value/write' for method, _ in self.native_calls))
            self.bridge.cli_network(self.thread_id, {'confirmed': True, 'generation': preview['generation']})
        writes = [params for method, params in self.native_calls if method == 'config/value/write']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['keyPath'], 'features.respect_system_proxy')
        self.assertTrue(writes[0]['expectedVersion'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'complete')
        self.assertTrue(self.bridge._cli_recovery_record()['followSystemProxy'])

    def test_linux_recovery_only_updates_launch_environment(self):
        with patch('sys.platform', 'linux'):
            preview = self.bridge.cli_network(self.thread_id)
            self.assertNotIn('respect_system_proxy', preview['message'])
            self.bridge.cli_network(self.thread_id, {'confirmed': True, 'generation': preview['generation']})
        self.assertFalse(any(method == 'config/value/write' for method, _ in self.native_calls))
        self.assertNotIn('followSystemProxy', self.bridge._cli_recovery_record())

    def test_unknown_setting_write_blocks_drain_and_requires_read_only_verification(self):
        error = OSError('write response lost')
        error.system_proxy_unknown = True
        with patch('sys.platform', 'darwin'), \
                patch('bridge.clients.codex.daemon_network.enable_system_proxy_following', side_effect=error):
            with self.assertRaisesRegex(ValueError, '写入结果待核对'):
                self.confirm()
        self.stop.assert_not_called()
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'configuringUnknown')
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.runtime, session.desktop_owner_probe = 'cli', 'absent'
        self.bridge._attach = Mock()
        self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'configuringUnknown')
        self.system_proxy_enabled = True
        checked = self.bridge.cli_network(self.thread_id)
        self.assertIn('已确认保存', checked['message'])
        self.assertFalse(checked['canRecover'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'complete')
        self.assertFalse(any(method == 'config/value/write' for method, _ in self.native_calls))

    def test_process_interruption_after_setting_dispatch_keeps_durable_unknown_receipt(self):
        request = self.probe.request
        writes = []
        def interrupted(method, params, **kwargs):
            if method == 'config/value/write':
                writes.append(params)
                self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'configuringUnknown')
                raise SystemExit('simulated process interruption before acknowledgment')
            return request(method, params, **kwargs)
        self.probe.request = interrupted
        with patch('sys.platform', 'darwin'):
            with self.assertRaises(SystemExit):
                self.confirm()
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'configuringUnknown')
        self.stop.assert_not_called()
        self.probe.request = request
        self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'configuringUnknown')
        self.assertEqual(len(writes), 1)

    def test_saved_setting_followed_by_failed_native_verification_never_restarts_again(self):
        self.cli._start_daemon.side_effect = lambda *_: setattr(self, 'system_proxy_enabled', False)
        with patch('sys.platform', 'darwin'):
            with self.assertRaisesRegex(ValueError, '设置已保存，CLI 服务尚未恢复'):
                self.confirm()
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'starting')
        with self.assertRaises(IPCError):
            self.bridge._recover_cli_environment(self.thread_id)
        self.stop.assert_called_once()
        self.cli._start_daemon.assert_called_once()

    def test_start_verification_can_be_completed_by_read_only_check(self):
        self.cli._start_daemon.side_effect = lambda *_: setattr(self, 'system_proxy_enabled', False)
        with patch('sys.platform', 'darwin'):
            with self.assertRaisesRegex(ValueError, '服务尚未恢复'):
                self.confirm()
        self.system_proxy_enabled = True
        self.cli.daemon_status.return_value = 'running'
        with patch('bridge.platforms.codex_daemon_env.read_daemon_environment', return_value=({}, ('replacement',))):
            checked = self.bridge.cli_network(self.thread_id)
        self.assertTrue(checked['recovered'])
        self.assertFalse(checked['canRecover'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'complete')
        self.stop.assert_called_once()
        self.cli._start_daemon.assert_called_once()
        self.cli.attach.assert_not_called()

    def new_native_target(self):
        self.proxy_updates = {}
        self.current_config['model_provider'] = 'target-provider'
        self.current_config['model_providers'] = {
            'openai': {'env_key': 'OLD_KEY', 'env_http_headers': {'Old-Header': 'OLD_HEADER'}},
            'target-provider': {'base_url': 'http://127.0.0.1:18279/v1', 'env_key': 'NEW_KEY',
                                'env_http_headers': {'New-Header': 'NEW_HEADER'}, 'requires_openai_auth': False}}
        (self.home / 'config.toml').write_text('model_provider="target-provider"\n')
        self.native_state['modelProvider'] = 'target-provider'
        values = {'OPENAI_API_KEY': 'old-generic', 'OPENAI_BASE_URL': 'https://old.invalid/v1',
                  'OLD_KEY': 'old-key', 'OLD_HEADER': 'old-header', 'NEW_KEY': 'new-key', 'NEW_HEADER': 'new-header'}
        return patch('bridge.platforms.codex_daemon_env.read_daemon_environment',
                     side_effect=lambda _h, _e, names: ({k: values[k] for k in names if k in values}, self.generation))

    def test_external_provider_recovery_uses_only_current_target_sources(self):
        with self.new_native_target():
            preview = self.bridge.cli_network(self.thread_id)
            self.assertIn('当前原生配置', preview['message'])
            self.assertIn('不修改', preview['message'])
            self.assertIn('target-provider', preview['message'])
            self.assertTrue(self.bridge.cli_network(self.thread_id,
                {'confirmed': True, 'generation': preview['generation']})['connected'])
        environment = self.cli._start_daemon.call_args.args[1]
        self.assertEqual(environment['NEW_KEY'], 'new-key')
        self.assertEqual(environment['NEW_HEADER'], 'new-header')
        for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OLD_KEY', 'OLD_HEADER'):
            self.assertIsNone(environment[name])
        kwargs = self.cli.attach.call_args.kwargs
        self.assertEqual(kwargs['target_provider'], 'target-provider')
        self.assertEqual(kwargs['expected_identity']['modelProvider'], 'openai')
        self.assertEqual(self.bridge._cli_recovery_record()['providerPhase'], 'complete')
        self.assertFalse(any(method == 'config/value/write' for method, _ in self.native_calls))

    def test_missing_target_variable_does_not_use_old_generic_key(self):
        with self.new_native_target(), patch('bridge.platforms.codex_daemon_env.read_daemon_environment',
                return_value=({'OPENAI_API_KEY': 'old-key'}, self.generation)):
            with self.assertRaisesRegex(ValueError, 'NEW_KEY'):
                self.bridge.cli_network(self.thread_id)
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()

    def test_unknown_provider_resume_is_durable_and_check_never_replays(self):
        from bridge.clients.codex.app_server import AppServerUnknownResult
        def unknown(_identifier, **kwargs):
            kwargs['before_resume']()
            self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'resuming')
            self.thread_provider = 'target-provider'
            raise AppServerUnknownResult('resume acknowledgment lost')
        self.cli.attach.side_effect = unknown
        with self.new_native_target():
            with self.assertRaises(IPCError):
                self.confirm()
            self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'resuming')
            checked = self.bridge.cli_network(self.thread_id)
        self.assertIn('回执仍未知', checked['message'])
        self.assertEqual(self.bridge._cli_recovery_record()['providerPhase'], 'checkedUnknown')
        self.cli.attach.assert_called_once()
        self.stop.assert_called_once()
        self.assertFalse(self.cli.metadata.get(self.thread_id, {}).get('fileAuthGeneration'))

    def test_same_provider_write_ahead_receipt_does_not_prove_resume(self):
        import json
        (self.home / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'new-key'}))
        def interrupted(_identifier, **kwargs):
            kwargs['before_resume']()
            self.thread_loaded = False
            raise SystemExit('process interrupted before native resume')
        self.cli.attach.side_effect = interrupted
        with self.assertRaises(SystemExit):
            self.confirm()
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'resuming')
        self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'resuming')
        self.cli.attach.assert_called_once()
        self.assertFalse(self.cli.metadata.get(self.thread_id, {}).get('fileAuthGeneration'))

    def test_connected_early_return_cannot_complete_recovery_or_mark_factory(self):
        import json
        (self.home / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'new-key'}))
        with patch.object(self.bridge, 'activate', return_value=SimpleNamespace(connected=True, runtime='cli')):
            with self.assertRaisesRegex(IPCError, '尚未执行'):
                self.confirm()
        self.assertEqual(self.bridge._cli_recovery_record()['providerPhase'], 'authorized')
        self.cli.attach.assert_not_called()
        self.assertFalse(self.cli.metadata.get(self.thread_id, {}).get('fileAuthGeneration'))

    def test_changed_provider_persisted_while_not_loaded_can_only_resolve_receipt(self):
        from bridge.clients.codex.app_server import AppServerUnknownResult
        def unknown(_identifier, **kwargs):
            kwargs['before_resume']()
            self.thread_provider, self.thread_loaded = 'target-provider', False
            raise AppServerUnknownResult('acknowledgment lost')
        self.cli.attach.side_effect = unknown
        with self.new_native_target():
            with self.assertRaises(IPCError): self.confirm()
            result = self.bridge.cli_network(self.thread_id)
        self.assertIn('回执仍未知', result['message'])
        self.assertEqual(self.bridge._cli_recovery_record()['providerPhase'], 'checkedUnknown')
        self.cli.attach.assert_called_once()
        self.assertFalse(self.cli.metadata.get(self.thread_id, {}).get('fileAuthGeneration'))

    def test_resume_reconciliation_refuses_changed_generation(self):
        from bridge.clients.codex.app_server import AppServerUnknownResult
        def unknown(_identifier, **kwargs):
            kwargs['before_resume']()
            self.thread_provider = 'target-provider'
            raise AppServerUnknownResult('acknowledgment lost')
        self.cli.attach.side_effect = unknown
        with self.new_native_target():
            with self.assertRaises(IPCError): self.confirm()
            with patch('bridge.platforms.codex_daemon_env.read_daemon_environment', return_value=({}, ('replacement',))):
                self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'resuming')
        self.cli.attach.assert_called_once()

    def test_external_keyring_source_is_not_inferred_from_auth_file(self):
        import json
        (self.home / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'new-key'}))
        self.current_config['cli_auth_credentials_store'] = 'auto'
        with self.assertRaisesRegex(ValueError, '不是已核验的 file 来源'):
            self.bridge.cli_network(self.thread_id)
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_not_called()

    def test_known_file_factory_blocks_new_send_but_preserves_stop(self):
        import json
        (self.home / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'new-key'}))
        self.auth_cache = 'old-key'
        generation = hashlib.sha256(json.dumps(self.generation).encode()).hexdigest()
        self.cli.metadata[self.thread_id] = {'cwd': self.native_state['cwd'], 'modelProvider': 'openai',
                                             'fileAuthGeneration': generation}
        process = SimpleNamespace(poll=lambda: 0, stdin=None, stdout=None)
        self.cli.client.process = process
        self.cli.joined[self.thread_id] = process
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.runtime, session.connected = 'cli', True
        session.owner = 'cli:' + self.thread_id
        session.state = {**self.native_state, 'threadRuntimeStatus': {'type': 'active'},
                         'turns': [{'turnId': 'original-turn', 'status': 'inProgress', 'items': []}]}
        self.cli.call = Mock(return_value={'interrupted': True})
        self.assertTrue(self.bridge.interrupt(self.thread_id)['interrupted'])
        with self.assertRaisesRegex(IPCError, '仍缓存旧 API'):
            self.bridge.send(self.thread_id, 'new message', '8d7fa750-7ff4-4c4b-9c98-62e2ebbb5bfa')
        self.assertFalse(any(call.args[1] == 'turn/start' for call in self.cli.call.call_args_list))
        self.stop.assert_not_called()

    def test_unknown_loaded_factory_does_not_get_file_auth_proof_from_current_config(self):
        import json
        (self.home / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'new-key'}))
        self.auth_cache = 'old-key'
        self.bridge._verify_cli_file_auth(self.thread_id)
        self.assertFalse(any(method == 'getAuthStatus' for method, _ in self.native_calls))

    def test_explicit_native_unload_requires_confirmed_detach_and_idle_shared_service(self):
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.runtime = 'cli'
        self.cli.client.process = SimpleNamespace(poll=lambda: 0, stdin=None, stdout=None)
        self.cli.client.request = self.probe.request
        operation_id = str(uuid.uuid4())
        def before_exit(*_args, **_kwargs):
            record = self.bridge._cli_recovery_record()
            self.assertEqual((record['phase'], record['purpose'], record['operationId']),
                             ('draining', 'release', operation_id))
        self.stop.side_effect = before_exit
        result = self.bridge._unload_cli_connection(self.thread_id, 'original-token', lambda: {'status': 'detached'}, operation_id)
        self.assertEqual(result['status'], 'released')
        self.assertIn('original-token', self.cli.drained_connections)
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'released')
        self.assertEqual(self.bridge._cli_recovery_record()['purpose'], 'release')
        self.assertEqual(self.bridge._cli_recovery_record()['releasedThread']['id'], self.thread_id)
        uuid.UUID(self.bridge._cli_recovery_record()['operationId'])
        self.stop.assert_called_once()
        self.cli._start_daemon.assert_not_called()

    def released_fixture(self, *, legacy=False, notice='已正常退出此配置的共享 CLI 服务，原生占用已释放'):
        from bridge.app.lifecycle import private_json
        self.proxy_updates = {}
        identity = {key: self.native_state[key] for key in ('id', 'cwd', 'modelProvider')}
        record = {'phase': 'stopped' if legacy else 'released', 'generation': 'retired-generation',
                  'configuration': configuration_stamp(self.home), 'connections': ['released-token']}
        if not legacy:
            record.update(purpose='release', releasedThread=identity, releaseKey='released-token', operationId=str(uuid.uuid4()))
        private_json(self.bridge.data_dir/'cli-daemon-recovery.json', record)
        operation_id = str(uuid.uuid4())
        private_json(self.bridge.data_dir/'cli-release-operations.json', {
            operation_id: {'id': operation_id, 'mode': 'unload', 'connections': ['released-token'], 'results': [{'key': 'released-token',
                'sessionId': self.thread_id, 'provider': 'codex', 'host': 'local',
                'status': 'released', 'notice': notice}]}})
        self.bridge.released_cli.set(self.thread_id, True)
        self.cli.recovery_environment = None
        self.cli.configuration_stamp = None
        target = {'provider': 'openai', 'aliases': ['openai'], 'kind': 'api', 'accountId': 'selected-b',
                  'environment': {'OPENAI_API_KEY': None, 'OPENAI_BASE_URL': None}}
        return patch('bridge.features.accounts.cli_sync.LocalCliSync.recovery_target', return_value=target)

    def test_completed_release_allows_explicit_cold_continue_without_another_stop(self):
        with self.released_fixture():
            self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_called_once()
        self.assertIsNone(self.cli._start_daemon.call_args.args[1]['OPENAI_API_KEY'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'complete')
        self.assertEqual(self.bridge._cli_recovery_record()['purpose'], 'release')

    def test_native_drain_release_continue_retains_verified_unmanaged_environment(self):
        self.current_config = {'model_provider': 'openai', 'model_providers': {'openai': {
            'env_key': 'NATIVE_KEY', 'requires_openai_auth': False, 'base_url': 'https://synthetic.invalid/v1'}}}
        (self.home/'config.toml').write_text('model_provider="openai"\n[model_providers.openai]\n'
            'env_key="NATIVE_KEY"\nrequires_openai_auth=false\nbase_url="https://synthetic.invalid/v1"\n')
        session = self.bridge.session(self.thread_id, attach=False)
        session.runtime = 'cli'
        self.cli.client.process = SimpleNamespace(poll=lambda: 0, stdin=None, stdout=None)
        self.cli.client.request = self.probe.request
        with patch('bridge.platforms.codex_daemon_env.read_daemon_environment',
                side_effect=lambda _home, _exe, names: ({name: 'verified-native-value' for name in names}, self.generation)), \
                patch('bridge.features.accounts.cli_sync.LocalCliSync.recovery_target', return_value=None):
            self.bridge._unload_cli_connection(self.thread_id, 'original-token', lambda: {'status': 'detached'})
            record = self.bridge._cli_recovery_record()
            self.assertEqual(self.cli.recovery_environment['generation'], record['generation'])
            self.assertNotEqual(record['generation'], 'original-token')
            self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.assertEqual(self.cli._start_daemon.call_args.args[1]['NATIVE_KEY'], 'verified-native-value')
        self.stop.assert_called_once()

    def test_legacy_stopped_release_survives_gateway_restart_without_clearing_ledger(self):
        from bridge.app.service import Bridge
        with self.released_fixture(legacy=True):
            self.bridge.close()
            self.bridge = Bridge(self.home, self.home/'bridge-data', codex_bin=self.home/'no-codex', cli_sessions=self.cli)
            self.bridge.ipc.connect = Mock(side_effect=IPCError('desktop absent'))
            self.bridge.ipc.owner = Mock(side_effect=IPCError('no-client-found'))
            self.assertTrue(self.bridge.activate(self.thread_id).connected)
        record = self.bridge._cli_recovery_record()
        self.assertEqual(record['generation'], 'retired-generation')
        self.assertEqual(record['connections'], ['released-token'])
        self.assertEqual(record['purpose'], 'release')
        self.assertEqual(record['phase'], 'complete')
        self.stop.assert_not_called()
        self.cli._start_daemon.assert_called_once()

    def _add_cli_metadata(self, home=None):
        home = home or self.home
        home.mkdir(exist_ok=True)
        identifier = str(uuid.uuid4())
        with closing(sqlite3.connect(home/'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, '
                       'originator TEXT, source TEXT, rollout_path TEXT, model_provider TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)',
                (identifier, 'Other CLI fixture', self.native_state['cwd'], 2, 0, 'codex_cli_rs', 'cli',
                 str(home/'sessions/rollout.jsonl'), self.native_state['modelProvider']))
        return identifier

    def test_legacy_successful_home_release_allows_another_thread_after_gateway_restart(self):
        from bridge.app.service import Bridge
        other = self._add_cli_metadata()
        original = self.thread_id
        with self.released_fixture(legacy=True):
            operations_path = self.bridge.data_dir/'cli-release-operations.json'
            operations = operations_path.read_bytes()
            before = self.bridge._cli_recovery_record()
            self.cli.store = self.bridge.store  # Real SQLite in this home, including both original IDs.
            self.native_state = {**self.native_state, 'id': other}
            self.bridge.close()
            self.bridge = Bridge(self.home, self.home/'bridge-data', cli_sessions=self.cli,
                                 codex_bin=self.home/'no-codex')
            session = self.bridge.activate(other)
            self.assertTrue(session.connected)
            self.assertEqual((session.id, session.runtime), (other, 'cli'))
            self.assertEqual(operations_path.read_bytes(), operations)
        record = self.bridge._cli_recovery_record()
        self.assertEqual(record['releasedThread']['id'], original)
        self.assertEqual(record['generation'], before['generation'])
        self.assertEqual(record['connections'], before['connections'])
        self.assertEqual(record['phase'], 'complete')
        self.cli._start_daemon.assert_called_once()
        self.cli.attach.assert_called_once_with(other, explicit=True)
        self.stop.assert_not_called()

    def test_legacy_cross_thread_release_requires_exact_success_receipt(self):
        from bridge.app.lifecycle import private_json
        other = self._add_cli_metadata()
        self.cli.store = self.bridge.store
        mutations = (
            ('result', {'status': 'unknown'}), ('result', {'status': 'failed'}),
            ('result', {'status': 'detached'}), ('result', {'host': 'codex:other-home'}),
            ('result', {'provider': 'claude'}), ('result', {'key': 'different-generation-token'}),
            ('result', {'notice': '原生会话已卸载，可在电脑端继续'}),
            ('operation', {'mode': 'wait'}), ('operation', {'connections': ['different-generation-token']}),
        )
        for scope, change in mutations:
            with self.subTest(change=change), self.released_fixture(legacy=True):
                path = self.bridge.data_dir/'cli-release-operations.json'
                jobs = json.loads(path.read_text())
                job = next(iter(jobs.values()))
                (job['results'][0] if scope == 'result' else job).update(change)
                private_json(path, jobs)
                before = self.bridge._cli_recovery_record()
                with self.assertRaises(IPCError):
                    self.bridge._recover_cli_environment(other)
                self.assertEqual(self.bridge._cli_recovery_record(), before)
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def test_legacy_cross_thread_receipt_must_exist_in_this_home(self):
        other = self._add_cli_metadata()
        other_home = self.home/'another-home'
        self._add_cli_metadata(other_home)
        self.cli.store = SessionStore(other_home)
        with self.released_fixture(legacy=True):
            before = self.bridge._cli_recovery_record()
            with self.assertRaises(IPCError):
                self.bridge._recover_cli_environment(other)
            self.assertEqual(self.bridge._cli_recovery_record(), before)
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def test_legacy_cross_thread_release_cannot_reuse_a_still_running_receipted_generation(self):
        from bridge.app.lifecycle import private_json
        other = self._add_cli_metadata()
        self.cli.store = self.bridge.store
        with self.released_fixture(legacy=True):
            record = self.bridge._cli_recovery_record()
            record['generation'] = hashlib.sha256(json.dumps(self.generation).encode()).hexdigest()
            private_json(self.bridge.data_dir/'cli-daemon-recovery.json', record)
            self.cli.daemon_status.return_value = 'running'
            with self.assertRaisesRegex(IPCError, '仍在排空'):
                self.bridge._recover_cli_environment(other)
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def test_legacy_cross_thread_release_does_not_reuse_another_generation_environment(self):
        other = self._add_cli_metadata()
        self.cli.store = self.bridge.store
        with self.released_fixture(legacy=True), \
                patch('bridge.features.accounts.cli_sync.LocalCliSync.recovery_target', return_value=None):
            self.cli.recovery_environment = {'generation': 'different-generation', 'updates': {},
                                             'preserved': {'OLD_ROUTE_KEY': 'fixture-only'}}
            with self.assertRaisesRegex(IPCError, '环境已不可核验'):
                self.bridge._recover_cli_environment(other)
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def test_legacy_cross_thread_release_still_rejects_changed_home_configuration(self):
        other = self._add_cli_metadata()
        self.cli.store = self.bridge.store
        with self.released_fixture(legacy=True):
            (self.home/'config.toml').write_text('model_provider="changed-provider"\n')
            with self.assertRaisesRegex(IPCError, '配置或认证已变化'):
                self.bridge._recover_cli_environment(other)
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def _path_only_cli(self):
        from pathlib import Path
        self.cli.allow_installed_runtime = True
        Path(self.cli.client.socket_path).unlink(missing_ok=True)
        installed = self.home/'installed-codex'
        installed.write_text('#!/bin/sh\nexit 0\n')
        installed.chmod(0o700)
        patches = ExitStack()
        patches.enter_context(patch('bridge.clients.codex.cli_session._managed_cli', side_effect=_managed_cli))
        patches.enter_context(patch('bridge.clients.codex.cli_session.shutil.which', return_value=str(installed)))
        return patches

    def test_path_cli_cannot_bypass_an_unknown_legacy_release_receipt(self):
        from bridge.app.lifecycle import private_json
        other = self._add_cli_metadata()
        self.cli.store = self.bridge.store
        for status in ('unknown', 'failed', None):
            with self.subTest(receiptStatus=status), self.released_fixture(legacy=True), self._path_only_cli():
                path = self.bridge.data_dir/'cli-release-operations.json'
                if status is None:
                    path.unlink()
                else:
                    jobs = json.loads(path.read_text())
                    next(iter(jobs.values()))['results'][0]['status'] = status
                    private_json(path, jobs)
                before = self.bridge._cli_recovery_record()
                operations = path.read_bytes() if path.exists() else None
                self.native_state = {**self.native_state, 'id': other}
                self.assertTrue(self.cli.available())
                with self.assertRaises(IPCError):
                    self.bridge.activate(other)
                self.assertEqual(self.bridge._cli_recovery_record(), before)
                self.assertEqual(path.read_bytes() if path.exists() else None, operations)
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def test_path_cli_cannot_bypass_pending_or_unknown_maintenance(self):
        from bridge.app.lifecycle import private_json
        with self._path_only_cli():
            for phase, target in (('draining', self.thread_id), ('starting', self.thread_id),
                    ('resuming', self.thread_id), ('resuming', str(uuid.uuid4())),
                    ('configuringUnknown', self.thread_id)):
                with self.subTest(phase=phase, target=target):
                    record = {'phase': phase, 'generation': 'unresolved-generation',
                              'providerTarget': {'before': {'id': target}}}
                    private_json(self.bridge.data_dir/'cli-daemon-recovery.json', record)
                    with self.assertRaises(IPCError):
                        self.bridge._recover_cli_environment(self.thread_id)
                    self.assertEqual(self.bridge._cli_recovery_record(), record)
        self.cli._start_daemon.assert_not_called()
        self.cli.attach.assert_not_called()
        self.stop.assert_not_called()

    def _assert_completed_path_release(self, legacy):
        managed = self.home/'packages/app-server-daemon/current/bin/codex'
        def start(*_args):
            managed.parent.mkdir(parents=True)
            managed.write_text('#!/bin/sh\nexit 0\n')
            managed.chmod(0o700)
        self.cli._start_daemon = Mock(side_effect=start)
        with self.released_fixture(legacy=legacy), self._path_only_cli(), \
                patch('bridge.platforms.codex_daemon_env.read_daemon_environment',
                      return_value=({}, self.generation)) as environment:
            self.assertTrue(self.cli.available())
            self.assertIsNone(_managed_cli(self.home))
            self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.cli._start_daemon.assert_called_once()
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True, expected_identity={
            'id': self.thread_id, 'cwd': self.native_state['cwd'], 'modelProvider': 'openai'})
        environment.assert_called_once_with(self.cli.home, managed, ())
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'complete')
        self.stop.assert_not_called()

    def test_completed_legacy_release_can_start_from_path_and_verify_prepared_daemon(self):
        self._assert_completed_path_release(legacy=True)

    def test_completed_new_release_can_start_from_path_and_verify_prepared_daemon(self):
        self._assert_completed_path_release(legacy=False)

    def test_legacy_natural_unload_receipt_does_not_unlock_unfinished_recovery(self):
        with self.released_fixture(legacy=True, notice='原生会话已卸载，可在电脑端继续'):
            with self.assertRaisesRegex(IPCError, '排空|接续'):
                self.bridge.activate(self.thread_id)
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'stopped')
        self.cli._start_daemon.assert_not_called()
        self.stop.assert_not_called()

    def test_released_config_change_or_lost_injection_source_never_starts(self):
        with self.released_fixture():
            (self.home/'auth.json').write_text('{"OPENAI_API_KEY":"changed"}')
            with self.assertRaisesRegex(IPCError, '配置|认证'):
                self.bridge.activate(self.thread_id)
        self.cli._start_daemon.assert_not_called()
        self.stop.assert_not_called()

    def test_completed_release_joins_external_replacement_without_start_or_stop(self):
        with self.released_fixture():
            self.cli.daemon_status.return_value = 'running'
            self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.cli._start_daemon.assert_not_called()
        self.stop.assert_not_called()

    def test_completed_release_of_cli_origin_ignores_new_app_owner(self):
        from tests.test_cli_bridge_integration import CliBridgeTests
        with self.released_fixture():
            session = self.bridge.session(self.thread_id, attach=False)
            session.runtime = 'cli'
            CliBridgeTests.desktop_owner(self)
            self.assertEqual(self.bridge.activate(self.thread_id).runtime, 'cli')
        self.cli._start_daemon.assert_called_once()
        self.cli.attach.assert_called_once()
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.owner.assert_not_called()
        self.stop.assert_not_called()

    def test_released_missing_env_key_never_borrows_gateway_environment(self):
        (self.home/'config.toml').write_text('model_provider="openai"\n[model_providers.openai]\nenv_key="NEW_KEY"\n')
        with self.released_fixture(), patch.dict('os.environ', {'NEW_KEY': 'unrelated-gateway-key'}):
            with self.assertRaisesRegex(IPCError, 'NEW_KEY'):
                self.bridge.activate(self.thread_id)
        self.cli._start_daemon.assert_not_called()
        self.stop.assert_not_called()

    def test_released_unknown_daemon_status_does_not_start_or_repeat_exit(self):
        with self.released_fixture():
            self.cli.daemon_status.return_value = 'unknown'
            with self.assertRaisesRegex(IPCError, '待核对'):
                self.bridge.activate(self.thread_id)
        self.cli._start_daemon.assert_not_called()
        self.stop.assert_not_called()

    def test_released_start_unknown_is_durable_and_never_repeated(self):
        from bridge.clients.codex.app_server import AppServerUnavailable
        with self.released_fixture():
            self.cli._start_daemon.side_effect = AppServerUnavailable('start acknowledgement lost')
            with self.assertRaisesRegex(IPCError, 'lost'):
                self.bridge.activate(self.thread_id)
            self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'starting')
            with self.assertRaises(IPCError):
                self.bridge.activate(self.thread_id)
        self.cli._start_daemon.assert_called_once()
        self.stop.assert_not_called()

    def test_release_continue_rechecks_ledger_after_maintenance_lock_admission(self):
        from bridge.app.lifecycle import private_json
        with self.released_fixture():
            lock = self.cli.start_lock
            bridge = self.bridge
            injected = []
            class RacingLock:
                def __enter__(self):
                    lock.acquire()
                    if not injected:
                        record = bridge._cli_recovery_record()
                        record.update(phase='draining', generation='new-maintenance')
                        private_json(bridge.data_dir/'cli-daemon-recovery.json', record)
                        injected.append(True)
                def __exit__(self, *_args):
                    lock.release()
            self.cli.start_lock = RacingLock()
            with self.assertRaises(IPCError):
                self.bridge.activate(self.thread_id)
        self.assertEqual(self.bridge._cli_recovery_record()['generation'], 'new-maintenance')
        self.cli._start_daemon.assert_not_called()
        self.stop.assert_not_called()

    def test_legacy_network_recovery_with_unrelated_unload_receipt_stays_blocked(self):
        from bridge.app.lifecycle import private_json
        with self.released_fixture(legacy=True):
            record = self.bridge._cli_recovery_record()
            record['followSystemProxy'] = True
            private_json(self.bridge.data_dir/'cli-daemon-recovery.json', record)
            with self.assertRaises(IPCError):
                self.bridge.activate(self.thread_id)
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'stopped')
        self.cli._start_daemon.assert_not_called()
        self.stop.assert_not_called()

    def test_released_cold_start_clears_unrelated_gateway_database_and_headers(self):
        if os.name == 'nt':
            self.skipTest('uses an owned POSIX executable fixture')
        (self.home/'config.toml').write_text('model_provider="openai"\n[model_providers.old]\n'
                                           'env_key="OLD_KEY"\n[model_providers.old.env_http_headers]\nOld-Header="OLD_HEADER"\n')
        self.cli._start_daemon = Mock(side_effect=lambda *args: CliSessions._start_daemon(self.cli, *args))
        executable = self.home/'codex'
        executable.write_text('#!' + sys.executable + '\nimport json,os\n'
            'keys=("OPENAI_API_KEY","OPENAI_BASE_URL","OLD_KEY","OLD_HEADER","CODEX_SQLITE_HOME")\n'
            'with open(os.environ["CMB_RELEASE_TEST_RESULT"],"w") as stream:\n'
            ' json.dump({"keys":[key for key in keys if key in os.environ],"home":os.environ["CODEX_HOME"]},stream)\n'
            'print(json.dumps({"status":"started","socketPath":os.environ["CODEX_HOME"]+"/fixture.sock"}))\n')
        executable.chmod(0o700)
        observed = self.home/'child-environment.json'
        with self.released_fixture(), patch.dict('os.environ', {'OPENAI_API_KEY': 'unrelated-key',
                'OPENAI_BASE_URL': 'https://unrelated.invalid', 'OLD_KEY': 'old-key',
                'OLD_HEADER': 'old-header', 'CODEX_SQLITE_HOME': '/unrelated/database',
                'CMB_RELEASE_TEST_RESULT': str(observed)}):
            self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.assertEqual(json.loads(observed.read_text()), {'keys': [], 'home': str(self.home)})
        self.stop.assert_not_called()

    def test_explicit_native_unload_refuses_another_active_task(self):
        self.busy = True
        self.cli.client.process = SimpleNamespace(poll=lambda: 0, stdin=None, stdout=None)
        self.cli.client.request = self.probe.request
        with self.assertRaises(ValueError) as caught:
            self.bridge._unload_cli_connection(self.thread_id, 'original-token', lambda: {'status': 'detached'})
        self.assertTrue(caught.exception.release_result['notSent'])
        self.stop.assert_not_called()

    def test_running_turn_keeps_stop_available_while_recovery_needs_confirmation(self):
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.connected, session.runtime, session.owner = True, 'cli', 'cli:' + self.thread_id
        session.state = {**self.native_state, 'threadRuntimeStatus': {'type': 'active'},
                         'turns': [{'turnId': 'original-turn', 'status': 'inProgress', 'items': []}]}
        self.cli.call = Mock(return_value={'interrupted': True})
        self.assertTrue(self.bridge.interrupt(self.thread_id)['interrupted'])
        self.stop.assert_not_called()

    def test_graceful_capability_never_guesses_from_os_or_future_version(self):
        self.assertTrue(graceful_restart_supported({'userAgent': 'Codex Desktop/0.164.0 (Mac OS 15.6.1)'}))
        self.assertTrue(graceful_restart_supported({'userAgent': 'codex-mobile-bridge-network-check/0.167.0 (Mac OS 26.1)'}))
        self.assertFalse(graceful_restart_supported({'userAgent': 'Unknown (Mac OS 0.164.0)'}))
        self.assertFalse(graceful_restart_supported({'userAgent': 'Codex Desktop/0.165.0'}))
        self.assertTrue(graceful_restart_supported({'userAgent':
            'codex-mobile-bridge-network-verify/0.164.0 (Mac OS 15.6.1; arm64) dumb (codex-tui; 1)'}))
        self.assertFalse(graceful_restart_supported({'userAgent': 'codex-mobile-bridge-network-verify/0.165.0'}))
        self.assertFalse(graceful_restart_supported({'userAgent': 'codex-mobile-bridge-unverified/0.164.0'}))
