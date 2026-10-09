"""Isolated Windows background bootstrap receipts and heartbeat regressions."""
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.integrations import claude_setup
from bridge.integrations.claude import Claude
from bridge.integrations.mailbox import CONNECTOR_REVISION
from bridge.integrations.manager import DesktopSessions


class BackgroundHelperContract(unittest.TestCase):
    def setUp(self):
        self.process = Mock(returncode=0)
        self.process.communicate.return_value = (self.receipt(), '')
        for target, value in (
                ('bridge.integrations.claude_setup.sys', SimpleNamespace(platform='win32')),
                ('bridge.integrations.claude_setup.helper_path', Mock(return_value=Path(__file__))),
                ('bridge.integrations.claude_setup.subprocess.Popen', Mock(return_value=self.process)),
                ('bridge.integrations.claude_setup.windows_session.require_interactive', Mock(side_effect=AssertionError('foreground gate')))):
            fixture = patch(target, value); fixture.start(); self.addCleanup(fixture.stop)

    def receipt(self, state='submitted', submission='submitted', pid=42):
        return json.dumps({'setupState': state, 'submission': submission, 'pid': pid, 'reason': 'fixture'})

    def call(self, **kwargs):
        return claude_setup.native_action('connect-background', pid=42, executable='fixture.exe',
                                        script='fixture.js', cancel_path='cancel', **kwargs)

    def test_locked_background_action_preserves_positional_helper_contract(self):
        result = self.call()
        self.assertEqual(result['submission'], 'submitted')
        self.assertEqual(claude_setup.subprocess.Popen.call_args.args[0],
                         [str(Path(__file__)), '42', '--connect-background=fixture.js', 'fixture.exe', 'cancel'])
        claude_setup.windows_session.require_interactive.assert_not_called()

    def test_locked_cleanup_uses_same_native_contract_and_twelve_second_timeout(self):
        self.process.communicate.side_effect = [subprocess.TimeoutExpired('helper', .2), ('', '')]
        with patch.object(claude_setup.time, 'monotonic', side_effect=[0, 13]):
            result = claude_setup.native_action('close-background-devtools', pid=42,
                                               executable='fixture.exe', cancel_path='cancel')
        self.assertEqual(result['setupState'], 'needs-retry')
        self.assertEqual(claude_setup.subprocess.Popen.call_args.args[0],
                         [str(Path(__file__)), '42', '--close-background-devtools', 'fixture.exe', 'cancel'])
        claude_setup.windows_session.require_interactive.assert_not_called()
        self.process.terminate.assert_called_once()

    def test_timeout_requires_matching_dispatch_pid_to_be_uncertain(self):
        for marker_pid, submission in ((42, 'uncertain'), (43, 'none')):
            with self.subTest(pid=marker_pid):
                marker = json.dumps({'connectPhase': 'dispatching', 'pid': marker_pid}).encode()
                self.process.communicate.side_effect = [subprocess.TimeoutExpired('helper', .2, output=marker), ('', '')]
                with patch.object(claude_setup.time, 'monotonic', side_effect=[0, 31]):
                    result = self.call()
                self.assertEqual(result['submission'], submission)
                self.assertEqual(result['setupState'], 'needs-retry')
        self.assertEqual(self.process.terminate.call_count, 2)
        self.process.kill.assert_not_called()

    def test_cancel_after_dispatch_stops_only_helper_and_preserves_uncertainty(self):
        cancelled = threading.Event()
        def interrupted(**kwargs):
            cancelled.set()
            raise subprocess.TimeoutExpired('helper', .2, output=b'{"connectPhase":"dispatching","pid":42}')
        calls = iter((True, False))
        self.process.communicate.side_effect = lambda **kwargs: interrupted(**kwargs) if next(calls) else ('', '')
        result = self.call(cancelled=cancelled)
        self.assertEqual(result['submission'], 'uncertain')
        self.assertEqual(result['setupState'], 'cancelled')
        self.process.terminate.assert_called_once()

    def test_malformed_receipt_never_proves_no_submission(self):
        for output in ('', 'not json', self.receipt(pid=43), self.receipt(state='connected'),
                       self.receipt(state='submitted', submission='none')):
            with self.subTest(output=output):
                self.process.communicate.return_value = (output, '')
                self.assertEqual(self.call()['submission'], 'uncertain')

    def test_nonzero_and_contradictory_receipts_remain_uncertain(self):
        self.process.returncode = 1
        self.assertEqual(self.call()['submission'], 'uncertain')
        self.process.returncode = 0
        self.process.communicate.return_value = ('{"connectPhase":"dispatching","pid":42}\n' +
                                                self.receipt('failed', 'none'), '')
        self.assertEqual(self.call()['submission'], 'uncertain')
        self.process.communicate.return_value = (self.receipt('failed', 'none'), '')
        self.assertEqual(self.call()['submission'], 'none')

    def test_cancel_before_helper_has_no_side_effects(self):
        cancelled = threading.Event(); cancelled.set()
        self.assertEqual(self.call(cancelled=cancelled)['submission'], 'none')
        claude_setup.subprocess.Popen.assert_not_called()


class BackgroundConsoleCapability(unittest.TestCase):
    source = ('r.default.env.CLAUDE_DEV_TOOLS&&GU((()=>!0))){'
              'let e=["detach","bottom","left","right","undocked"].includes(r.default.env.CLAUDE_DEV_TOOLS)'
              '?r.default.env.CLAUDE_DEV_TOOLS:KU.mode;B?.webContents.openDevTools({mode:e})}'
              'function GU(e){return e()&&!_v()}function _v(){return gv()==="restricted"}')

    def test_console_capability_requires_native_restricted_mode_guard(self):
        self.assertTrue(claude_setup._native_background_console(self.source))
        for source in (self.source.replace('&&!_v()', ''), self.source.replace('"restricted"', '"other"'),
                       self.source.replace('"undocked"', '"other"'), 'CLAUDE_DEV_TOOLS openDevTools'):
            self.assertFalse(claude_setup._native_background_console(source))

    def test_environment_requires_explicit_initialization_developer_choice_and_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); executable = root/'Claude.exe'; executable.write_bytes(b'fixture')
            for initialize, developer, supported in ((True, True, True), (False, True, True),
                                                     (True, False, True), (True, True, False)):
                with self.subTest(initialize=initialize, developer=developer, supported=supported), \
                     patch('bridge.integrations.claude_setup.sys', SimpleNamespace(platform='win32')), \
                     patch.object(claude_setup, '_main_pids', side_effect=[[], [], [42]]), \
                     patch.object(claude_setup, 'background_start_supported', return_value=True), \
                     patch.object(claude_setup, 'developer_mode_enabled', return_value=developer), \
                     patch.object(claude_setup, 'background_console_supported', return_value=supported), \
                     patch.dict(os.environ, {'ELECTRON_RUN_AS_NODE': '1', 'CLAUDE_DEV_TOOLS': 'right'}), \
                     patch.object(claude_setup.subprocess, 'Popen') as spawn:
                    claude_setup.background_running_app(executable, root, initialize_console=initialize)
                    environment = spawn.call_args.kwargs['env']
                    self.assertNotIn('ELECTRON_RUN_AS_NODE', environment)
                    self.assertEqual(environment.get('CLAUDE_DEV_TOOLS'), 'undocked' if initialize and developer and supported else None)


class BackgroundAccountRestart(unittest.TestCase):
    def test_account_restart_waits_for_confirmed_main_process_before_reconnect(self):
        manager = object.__new__(DesktopSessions)
        manager.gateway_running = True; manager.enabled = Mock(return_value=True)
        manager.adapters = {'claude': Mock()}
        descriptor = {'executable': 'fixture.exe', 'dataDirectory': 'fixture-profile'}
        order = []
        manager.adapters['claude'].reconnect.side_effect = lambda **kwargs: order.append('reconnect')
        with patch('bridge.integrations.manager.sys', SimpleNamespace(platform='win32')), \
             patch('bridge.integrations.client_launch.launch_client') as foreground, \
             patch.object(claude_setup, 'background_running_app', side_effect=lambda *args, **kwargs: order.append('running')) as launch:
            manager._launch_account_client('claude', descriptor)
            self.assertEqual(order, ['running', 'reconnect'])
            launch.assert_called_once_with('fixture.exe', 'fixture-profile', initialize_console=True)
            manager.adapters['claude'].reconnect.assert_called_once_with(launch=True)
            foreground.assert_not_called()
            launch.side_effect = ValueError('startup failed')
            with self.assertRaisesRegex(ValueError, 'startup failed'):
                manager._launch_account_client('claude', descriptor)
            self.assertEqual(order, ['running', 'reconnect'])

    def test_offline_or_disabled_account_launch_does_not_open_console(self):
        manager = object.__new__(DesktopSessions)
        manager.adapters = {'claude': Mock()}
        for gateway, enabled in ((False, True), (True, False)):
            manager.gateway_running = gateway; manager.enabled = Mock(return_value=enabled)
            with patch('bridge.integrations.manager.sys', SimpleNamespace(platform='win32')), \
                 patch.object(claude_setup, 'background_running_app') as launch:
                manager._launch_account_client('claude', {'executable': 'fixture.exe', 'dataDirectory': 'fixture-profile'})
                launch.assert_called_once_with('fixture.exe', 'fixture-profile', initialize_console=False)
                manager.adapters['claude'].reconnect.assert_not_called()


class BackgroundInitializationWorker(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.adapter = Claude(Path(temporary.name)/'adapter', auto_connect=False)
        self.addCleanup(self.adapter.close)
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': 'fixture.exe', 'dataHome': temporary.name, 'dataHomeExplicit': True}
        self.adapter.prepare()
        self.cancel = self.adapter.setup_cancel
        fixtures = {
            'bridge.integrations.claude.background_running_app': Mock(return_value={'pid': 42, 'launched': True}),
            'bridge.integrations.claude.developer_mode_enabled': Mock(return_value=True),
            'bridge.integrations.claude.native_action': Mock(return_value={'setupState': 'submitted', 'submission': 'submitted'}),
            'bridge.integrations.claude.windows_session.status': Mock(return_value={'interactive': True}),
        }
        self.mocks = {}
        for target, value in fixtures.items():
            fixture = patch(target, value); fixture.start(); self.addCleanup(fixture.stop)
            self.mocks[target.rsplit('.', 1)[-1]] = value
        fixture = patch.object(self.cancel, 'wait', return_value=False)
        self.wait = fixture.start(); self.addCleanup(fixture.stop)
        fixture = patch.object(self.adapter, '_connect_native')
        self.foreground = fixture.start(); self.addCleanup(fixture.stop)

    def run_worker(self, foreground=False):
        self.adapter._connect_background(self.cancel, 'fixture.exe', self.adapter.discovery['dataHome'], foreground=foreground)

    def heartbeat(self, *, valid=True):
        desktop = self.adapter.desktop
        payload = json.dumps({'generation': desktop.generation, 'timestamp': time.time(), 'connected': True,
                              'connectorRevision': CONNECTOR_REVISION, 'surfaces': {}})
        signature = hmac.new(desktop.token.encode(), payload.encode(), hashlib.sha256).hexdigest() if valid else 'invalid'
        (self.adapter.directory/'response.json').write_text(json.dumps({'payload': payload, 'signature': signature}), encoding='utf-8')

    def test_submitted_uncertain_or_missing_receipt_never_means_connected_or_foreground_retry(self):
        for submission in ('submitted', 'uncertain', None):
            with self.subTest(submission=submission):
                self.mocks['native_action'].return_value = {'setupState': 'submitted', 'submission': submission}
                self.run_worker(foreground=True)
                self.assertFalse(self.adapter.status()['connected'])
                self.assertEqual(self.adapter.status()['setupState'], 'needs-retry')
                self.foreground.assert_not_called()
        self.assertEqual(self.mocks['native_action'].call_count, 3)

    def test_real_signed_current_heartbeat_is_required(self):
        for valid in (False, True):
            with self.subTest(valid=valid):
                def native(*args, **kwargs):
                    self.heartbeat(valid=valid)
                    return {'setupState': 'submitted', 'submission': 'submitted'}
                self.mocks['native_action'].side_effect = native
                self.run_worker()
                self.assertEqual(self.adapter.status()['connected'], valid)
                self.assertEqual(self.adapter.status()['setupState'], 'connected' if valid else 'needs-retry')
        self.foreground.assert_not_called()

    def test_cleanup_only_follows_current_submission_and_verified_heartbeat(self):
        for submission in ('submitted', 'uncertain', 'none'):
            with self.subTest(submission=submission):
                self.adapter.prepare(reset=True)
                self.mocks['native_action'].reset_mock()
                def native(action, **kwargs):
                    if action == 'connect-background':
                        self.heartbeat()
                        return {'setupState': 'submitted', 'submission': submission}
                    return {'setupState': 'submitted'}
                self.mocks['native_action'].side_effect = native
                self.run_worker()
                actions = [call.args[0] for call in self.mocks['native_action'].call_args_list]
                self.assertEqual(actions, ['connect-background', 'close-background-devtools']
                                 if submission != 'none' else ['connect-background'])
                self.assertTrue(self.adapter.status()['connected'])
        self.mocks['native_action'].reset_mock()
        self.run_worker()
        self.mocks['native_action'].assert_not_called()

    def test_cleanup_failure_keeps_connection_and_cancellation_cannot_publish_success(self):
        for outcome in ('failed', 'exception', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.adapter.prepare(reset=True)
                def native(action, **kwargs):
                    if action == 'connect-background':
                        self.heartbeat()
                        return {'setupState': 'submitted', 'submission': 'submitted'}
                    if outcome == 'exception':
                        raise OSError('cleanup failed')
                    if outcome == 'cancelled':
                        self.adapter.cancel(persist=False)
                        return {'setupState': 'submitted'}
                    return {'setupState': 'needs-retry'}
                self.mocks['native_action'].side_effect = native
                self.run_worker()
                if outcome == 'cancelled':
                    self.assertFalse(self.adapter.status()['connected'])
                    self.assertEqual(self.adapter.discovery['setupState'], 'cancelled')
                else:
                    self.assertTrue(self.adapter.status()['connected'])
                    self.assertTrue(self.adapter.discovery['consoleCleanupPending'])
                    self.assertIn('开发者工具未自动关闭', self.adapter.discovery['reason'])
        self.foreground.assert_not_called()

    def test_none_falls_back_only_for_explicit_interactive_attempt(self):
        self.mocks['native_action'].return_value = {'setupState': 'failed', 'submission': 'none', 'reason': 'no prompt'}
        for explicit, interactive in ((False, True), (True, False), (True, True)):
            with self.subTest(explicit=explicit, interactive=interactive):
                self.foreground.reset_mock(); self.mocks['status'].return_value = {'interactive': interactive}
                self.run_worker(foreground=explicit)
                self.assertEqual(self.foreground.call_count, int(explicit and interactive))

    def test_cancelled_none_receipt_never_falls_back(self):
        self.mocks['native_action'].return_value = {'setupState': 'cancelled', 'submission': 'none', 'reason': 'cancelled'}
        self.run_worker(foreground=True)
        self.foreground.assert_not_called()
        self.assertEqual(self.adapter.status()['setupState'], 'cancelled')

    def test_pre_helper_failure_can_fall_back_but_helper_exception_cannot(self):
        self.mocks['background_running_app'].side_effect = ValueError('unverified startup')
        self.run_worker(foreground=True)
        self.foreground.assert_called_once()
        self.foreground.reset_mock(); self.mocks['background_running_app'].side_effect = None
        self.mocks['native_action'].side_effect = OSError('helper interrupted')
        self.run_worker(foreground=True)
        self.foreground.assert_not_called()

    def test_cancelled_or_replaced_worker_cannot_launch_or_publish(self):
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                if cancelled: self.cancel.set()
                else: self.cancel.clear(); self.adapter.setup_cancel = threading.Event()
                self.adapter._setup_state('connecting', 'current attempt')
                self.run_worker(foreground=True)
                self.assertEqual(self.adapter.discovery['reason'], 'current attempt')
                self.mocks['background_running_app'].assert_not_called()
                self.mocks['native_action'].assert_not_called()
                self.foreground.assert_not_called()

    def test_replaced_after_native_submission_cannot_publish_old_result(self):
        def native(*args, **kwargs):
            self.adapter.setup_cancel = threading.Event()
            self.adapter._setup_state('connecting', 'new attempt')
            return {'setupState': 'submitted', 'submission': 'submitted'}
        self.mocks['native_action'].side_effect = native
        self.run_worker(foreground=True)
        self.assertEqual(self.adapter.discovery['reason'], 'new attempt')
        self.foreground.assert_not_called()


if __name__ == '__main__':
    unittest.main()
