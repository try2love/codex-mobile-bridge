"""CLI change detection and explicitly confirmed recovery; no real model/process."""
from contextlib import ExitStack
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.cli_session import CliSessions
from bridge.clients.codex.daemon_network import configuration_stamp, graceful_restart_supported
from bridge.clients.codex.ipc import IPCError
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

    def test_other_active_thread_blocks_even_explicit_recovery(self):
        self.busy = True
        preview = self.bridge.cli_network(self.thread_id)
        self.assertFalse(preview['canRecover'])
        self.assertIn('unrelated-thread', preview['message'])
        self.stop.assert_not_called()
        self.cli.close.assert_not_called()

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
        self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        self.stop.assert_not_called()
        self.assertEqual(self.bridge.submissions['unrelated:submission']['status'], 'unknown')

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
        result = self.bridge._unload_cli_connection(self.thread_id, 'original-token', lambda: {'status': 'detached'})
        self.assertEqual(result['status'], 'released')
        self.assertIn('original-token', self.cli.drained_connections)
        self.stop.assert_called_once()
        self.cli._start_daemon.assert_not_called()

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
        self.assertFalse(graceful_restart_supported({'userAgent': 'Unknown (Mac OS 0.164.0)'}))
        self.assertFalse(graceful_restart_supported({'userAgent': 'Codex Desktop/0.165.0'}))
        self.assertTrue(graceful_restart_supported({'userAgent':
            'codex-mobile-bridge-network-verify/0.164.0 (Mac OS 15.6.1; arm64) dumb (codex-tui; 1)'}))
        self.assertFalse(graceful_restart_supported({'userAgent': 'codex-mobile-bridge-network-verify/0.165.0'}))
        self.assertFalse(graceful_restart_supported({'userAgent': 'codex-mobile-bridge-unverified/0.164.0'}))
