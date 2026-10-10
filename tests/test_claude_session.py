"""No real desktop input: locked/focus-failed initialization is bounded and retryable."""
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.platforms.windows import session as windows_session
from bridge.clients.claude import setup as claude_setup
from bridge.clients.claude.adapter import Claude
from bridge.clients.manager import DesktopSessions


class ClaudeSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.adapter = Claude(Path(self.temp.name)/'adapter', auto_connect=False)
        self.adapter.discovery = {'installed': True, 'executable': 'C:/Claude.exe',
                                  'dataHome': str(Path(self.temp.name)/'profile'), 'automaticConnection': 'native-console'}
        self.desktop = patch.object(windows_session, 'status', return_value={
            'state': 'unlocked', 'interactive': True, 'reason': 'ready'})
        self.snapshot = self.desktop.start(); self.addCleanup(self.desktop.stop)
        for target in ('bridge.clients.claude.setup.sys', 'bridge.clients.claude.adapter.sys',
                       'bridge.platforms.windows.session.sys', 'bridge.clients.manager.sys'):
            patcher = patch(target, SimpleNamespace(platform='win32'))
            patcher.start(); self.addCleanup(patcher.stop)

    def locked(self):
        self.snapshot.return_value = {'state': 'locked', 'interactive': False, 'reason': 'Windows 已锁定'}

    def test_locked_check_and_connect_do_not_start_helper(self):
        self.locked()
        with patch('bridge.platforms.windows.claude.subprocess') as processes:
            for action in ('check', 'request-permission', 'connect', 'enable-devtools', 'close-devtools', 'inspect-error'):
                result = claude_setup.native_action(action, pid=42)
                self.assertEqual(result['setupState'], 'needs-unlock')
        processes.Popen.assert_not_called()

    def test_locked_initialization_never_starts_desktop_or_prepares_injection(self):
        self.locked()
        with patch('bridge.clients.claude.adapter.running_app') as app, patch.object(self.adapter, 'prepare') as prepare, \
             patch.object(claude_setup, 'helper_path', return_value=Path(self.temp.name)/'missing-helper'):
            self.adapter._connect_native(False, Path(self.temp.name)/'cancel')
        self.assertEqual(self.adapter.status()['setupState'], 'needs-unlock')
        app.assert_not_called(); prepare.assert_not_called()

    def test_native_running_app_checks_desktop_again_before_launch(self):
        self.locked()
        with patch.object(claude_setup, 'DesktopApp') as app:
            with self.assertRaises(windows_session.DesktopUnavailable):
                claude_setup.running_app('C:/Claude.exe', 'C:/profile')
        app.assert_not_called()

    def test_lock_during_helper_stops_only_helper_and_reports_unlock(self):
        process = Mock(returncode=0)
        calls = 0
        def communicate(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.locked(); raise subprocess.TimeoutExpired('helper', .2)
            return '', ''
        process.communicate.side_effect = communicate
        with patch.object(claude_setup, 'helper_path', return_value=Path(__file__)), \
             patch.object(claude_setup.subprocess, 'Popen', return_value=process):
            result = claude_setup.native_action('connect', pid=42)
        self.assertEqual(result['setupState'], 'needs-unlock')
        process.terminate.assert_called_once(); process.kill.assert_not_called()

    def test_windows_connect_timeout_is_thirty_seconds_and_retryable(self):
        process = Mock(returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired('helper', .2), ('', '')]
        with patch.object(claude_setup, 'helper_path', return_value=Path(__file__)), \
             patch.object(claude_setup.subprocess, 'Popen', return_value=process), \
             patch.object(claude_setup.time, 'monotonic', side_effect=[0, 31]):
            result = claude_setup.native_action('connect', pid=42)
        self.assertEqual(result['setupState'], 'needs-retry')
        process.terminate.assert_called_once()

    def test_stale_cancelled_native_worker_cannot_overwrite_new_attempt(self):
        original = self.adapter.setup_cancel
        def native(action, **kwargs):
            original.set()
            self.adapter.setup_cancel = threading.Event()
            self.adapter._setup_state('connecting', 'new attempt')
            return {'setupState': 'needs-unlock', 'reason': 'old attempt'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=native):
            self.adapter._connect_native(False, Path(self.temp.name)/'cancel')
        self.assertEqual(self.adapter.discovery['setupState'], 'connecting')
        self.assertEqual(self.adapter.discovery['reason'], 'new attempt')

    def test_session_action_states_allow_explicit_initialization(self):
        manager = object.__new__(DesktopSessions); manager.gateway_running = True
        for state in ('needs-unlock', 'needs-desktop'):
            row = {'connected': False, 'enabled': True, 'setupStatus': state, 'reason': state}
            manager._claude_progress(row, {'running': True})
            self.assertEqual(row['connectionState'], 'needs-initialization')
            self.assertEqual(row['reason'], state)

    def test_helper_fast_failure_keeps_session_or_focus_reason(self):
        process = Mock(returncode=1)
        with patch.object(claude_setup, 'helper_path', return_value=Path(__file__)), \
             patch.object(claude_setup.subprocess, 'Popen', return_value=process):
            for action in ('connect', 'inspect-error'):
                for reason, state in [('Windows 已锁定', 'needs-unlock'), ('Windows 桌面不可用', 'needs-desktop'),
                                      ('焦点已离开 Claude', 'needs-retry'), ('已取消 Claude 连接', 'cancelled')]:
                    process.communicate.return_value = (reason, '')
                    self.assertEqual(claude_setup.native_action(action, pid=42), {'setupState': state, 'reason': reason})
            process.returncode = 0; process.communicate.return_value = ('NEEDS_TRUST', '')
            self.assertEqual(claude_setup.native_action('inspect-error', pid=42)['setupState'], 'needs-trust')

    def test_quit_does_not_require_unlocked_desktop(self):
        self.locked()
        process = Mock(returncode=0)
        process.communicate.return_value = ('{"quitState":"submitted","pid":42,"reason":"fixture"}', '')
        with patch.object(claude_setup, 'helper_path', return_value=Path(__file__)), \
             patch.object(claude_setup.subprocess, 'Popen', return_value=process):
            result = claude_setup.native_action('quit', pid=42)
        self.assertEqual(result['quitState'], 'submitted'); self.snapshot.assert_not_called()

    def test_verified_connection_recovers_while_locked_without_native_input(self):
        self.locked(); self.adapter.desktop = Mock(connected=True, capabilities={})
        with patch('bridge.clients.claude.adapter.native_action') as native:
            self.adapter._connect_native(False, Path(self.temp.name)/'cancel')
        self.assertTrue(self.adapter.status()['connected']); native.assert_not_called()
        self.snapshot.assert_not_called()

    def test_cancellation_during_successful_cleanup_cannot_publish_connected(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self.adapter.setup_cancel = threading.Event()
                original = self.adapter.setup_cancel
                self.adapter.desktop = Mock(connected=False, capabilities={})
                def native(action, **kwargs):
                    if action == 'connect':
                        self.adapter.desktop.connected = True
                        return {'setupState': 'submitted'}
                    if action == 'close-devtools':
                        self.adapter.cancel(persist=False)
                        if replacement:
                            self.adapter.setup_cancel = threading.Event()
                            self.adapter.desktop.connected = False
                            self.adapter._setup_state('connecting', 'new attempt')
                        return {'setupState': 'submitted'}
                    return {'setupState': 'ready'}
                with patch('bridge.clients.claude.adapter.native_action', side_effect=native), \
                     patch('bridge.clients.claude.adapter.running_app', return_value=42), \
                     patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
                     patch('bridge.clients.claude.adapter.claude_data_home', return_value=self.adapter.discovery['dataHome']), \
                     patch.object(self.adapter, 'prepare', return_value={'consolePath': 'fixture'}), \
                     patch.object(original, 'wait', return_value=False):
                    self.adapter._connect_native(False, Path(self.temp.name)/'cancel', original)
                self.assertFalse(self.adapter.status()['connected'])
                self.assertEqual(self.adapter.status()['setupState'], 'connecting' if replacement else 'cancelled')
                if replacement:
                    self.assertEqual(self.adapter.discovery['reason'], 'new attempt')
                    self.assertFalse(self.adapter.setup_cancel.is_set())

    def test_cached_clients_refresh_session_without_disconnecting_live_clients(self):
        manager = object.__new__(DesktopSessions); manager.client_lock = threading.RLock()
        cached = {'clients': [{'id': 'claude', 'connected': True, 'connectionState': 'connected'}]}
        manager.client_cache = (time.monotonic(), cached)
        with patch('bridge.clients.manager.sys', SimpleNamespace(platform='win32')):
            self.locked()
            locked = manager.clients()
            self.snapshot.return_value = {'state': 'unlocked', 'interactive': True, 'reason': 'ready'}
            unlocked = manager.clients()
        self.assertEqual(locked['windowsSession']['state'], 'locked')
        self.assertEqual(unlocked['windowsSession']['state'], 'unlocked')
        self.assertTrue(locked['clients'][0]['connected'])
        self.assertNotIn('windowsSession', cached)

    def test_locked_initialization_routes_through_background_adapter_and_preserves_restart_gate(self):
        self.locked()
        manager = object.__new__(DesktopSessions); manager.gateway_running = True
        manager.client_lock = threading.RLock(); manager._changing = lambda provider: nullcontext()
        manager._descriptor = Mock(); manager._stop_client = Mock()
        manager.adapters = {'claude': Mock()}; manager.clients = Mock(return_value={'clients': []})
        with patch('bridge.clients.manager.sys', SimpleNamespace(platform='win32')), \
             patch('bridge.clients.lifecycle.launch_client') as launch, \
             patch('bridge.clients.lifecycle.inspect_client') as inspect:
            for restart in (False, True):
                manager.adapters['claude'].status.return_value = {'connected': restart}
                self.assertEqual(manager.connect_claude(restart=restart), {'clients': []})
            self.assertEqual(manager._descriptor.call_count, 2)
            manager._stop_client.assert_called_once_with('claude', manager._descriptor.return_value, inspect.return_value)
            launch.assert_not_called(); inspect.assert_called_once()
            self.assertEqual(manager.adapters['claude'].connect.call_count, 2)
            manager._stop_client.side_effect = ValueError('任务仍在运行')
            with self.assertRaisesRegex(ValueError, '任务仍在运行'):
                manager.connect_claude(restart=True)
            self.assertEqual(manager.adapters['claude'].connect.call_count, 2)
            manager.adapters['claude'].connect.reset_mock()
            manager.adapters['claude'].status.return_value = {'connected': True}
            self.snapshot.reset_mock()
            self.assertEqual(manager.connect_claude(), {'clients': []})
            self.snapshot.assert_not_called(); launch.assert_not_called()
            manager.adapters['claude'].connect.assert_not_called()

    def test_stopped_gateway_initialization_only_saves_startup_preference(self):
        self.locked()
        manager = object.__new__(DesktopSessions); manager.gateway_running = False
        manager.toggle_client = Mock(return_value={'clients': []})
        self.assertEqual(manager.connect_claude(), {'clients': []})
        manager.toggle_client.assert_called_once_with({'provider': 'claude', 'enabled': True})
        self.snapshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
