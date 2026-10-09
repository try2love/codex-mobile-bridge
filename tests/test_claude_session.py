"""No real desktop input: locked/focus-failed initialization is bounded and retryable."""
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge import windows_session
from bridge.integrations import claude_setup
from bridge.integrations.claude import Claude
from bridge.integrations.manager import DesktopSessions


class ClaudeSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.adapter = Claude(Path(self.temp.name)/'adapter', auto_connect=False)
        self.adapter.discovery = {'installed': True, 'executable': 'C:/Claude.exe',
                                  'dataHome': str(Path(self.temp.name)/'profile'), 'automaticConnection': 'native-console'}
        self.desktop = patch.object(windows_session, 'status', return_value={
            'state': 'unlocked', 'interactive': True, 'reason': 'ready'})
        self.snapshot = self.desktop.start(); self.addCleanup(self.desktop.stop)
        for target in ('bridge.integrations.claude_setup.sys', 'bridge.windows_session.sys'):
            patcher = patch(target, SimpleNamespace(platform='win32'))
            patcher.start(); self.addCleanup(patcher.stop)

    def locked(self):
        self.snapshot.return_value = {'state': 'locked', 'interactive': False, 'reason': 'Windows 已锁定'}

    def test_locked_check_and_connect_do_not_start_helper(self):
        self.locked()
        with patch.object(claude_setup, 'subprocess') as processes:
            for action in ('check', 'request-permission', 'connect', 'enable-devtools', 'close-devtools', 'inspect-error'):
                result = claude_setup.native_action(action, pid=42)
                self.assertEqual(result['setupState'], 'needs-unlock')
        processes.Popen.assert_not_called()

    def test_locked_initialization_never_starts_desktop_or_prepares_injection(self):
        self.locked()
        with patch('bridge.integrations.claude.running_app') as app, patch.object(self.adapter, 'prepare') as prepare:
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
        def communicate(**kwargs):
            if not process.terminate.called:
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
        with patch('bridge.integrations.claude.native_action', side_effect=native):
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


if __name__ == '__main__':
    unittest.main()
