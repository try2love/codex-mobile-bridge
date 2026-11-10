"""CLI change detection and explicitly confirmed recovery; no real model/process."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.cli_session import CliSessions
from bridge.clients.codex.daemon_network import configuration_stamp, graceful_restart_supported
from bridge.clients.codex.ipc import IPCError
from tests.test_cli_bridge_integration import CliBridgeFixture


class CliNetworkRecoveryTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        self.cli = CliSessions(self.home, self.bridge._cli_state, self.bridge._cli_disconnected)
        self.cli.configuration_stamp = configuration_stamp(self.home)
        self.bridge.cli_sessions = self.cli
        self.cli.client.socket_path = str(self.home / 'fixture.sock')
        (self.home / 'fixture.sock').touch()
        self.cli._start_daemon = Mock(side_effect=lambda *_: setattr(self, 'proxy_updates', {}))
        self.cli.daemon_status = Mock(return_value='stopped')
        self.cli.close = Mock(side_effect=self.cli.close)
        self.cli.attach = Mock(return_value=self.native_state)
        self.native_calls = []
        self.busy = False
        self.generation = ('fixture-pid', 'generation')
        self.proxy_updates = {'HTTPS_PROXY': 'http://127.0.0.1:15937'}
        def request(method, params, **kwargs):
            self.native_calls.append((method, params))
            if method == 'thread/read':
                return {'thread': {'id': params['threadId'], 'status': {'type': 'active' if self.busy else 'idle'}}}
            if method == 'thread/backgroundTerminals/list': return {'data': []}
            if method == 'thread/goal/get': return {'goal': None}
            if method == 'config/read': return {'config': {'model_provider': 'openai'}}
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
        with self.assertRaisesRegex(IPCError, '确认恢复'):
            self.bridge.activate(self.thread_id)
        self.stop.assert_not_called()
        self.assertTrue(session.connected)
        self.assertTrue(self.confirm()['connected'])
        self.stop.assert_called_once_with(self.home, self.home / 'codex', self.generation, graceful_only=True)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.assertFalse(any(method in ('turn/start', 'turn/interrupt', 'thread/resume') for method, _ in self.native_calls))

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
        with self.assertRaises(OSError): self.confirm()
        record = self.bridge._cli_recovery_record()
        self.assertEqual(record['phase'], 'draining')
        self.bridge.cli_network(self.thread_id, automatic=True)
        self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        self.assertEqual(self.stop.call_count, 1)
        self.cli.daemon_status.return_value = 'stopped'
        self.assertTrue(self.confirm()['recovered'])
        self.assertEqual(self.stop.call_count, 1)
        self.cli._start_daemon.assert_called_once()

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
