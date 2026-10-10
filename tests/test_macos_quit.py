import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.desktop_app import DesktopApp
from bridge.platforms.macos import desktop


class MacNormalQuitTests(unittest.TestCase):
    executable = Path('/Applications/Fixture.app/Contents/MacOS/Fixture')

    def test_exact_verified_pid_and_path_are_passed_without_appleevent_app_lookup(self):
        with patch.object(desktop.subprocess, 'run', return_value=Mock(
                returncode=0, stdout='{"state":"submitted"}')) as run:
            self.assertEqual(desktop.quit_application(self.executable, pids=[41]), 'submitted')
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ['/usr/bin/osascript', '-l', 'JavaScript'])
        self.assertEqual(args[-2:], ['41', str(self.executable)])
        self.assertIn('runningApplicationWithProcessIdentifier', args[4])
        self.assertIn('executableURL', args[4])
        for forbidden in ('forceTerminate', 'activate', 'System Events', 'tell application', 'keystroke'):
            self.assertNotIn(forbidden, args[4])

    def test_no_process_does_not_send_or_launch_anything(self):
        with patch.object(desktop, 'processes', return_value=[]), patch.object(desktop.subprocess, 'run') as run:
            self.assertEqual(desktop.quit_application(self.executable), 'exited')
        run.assert_not_called()

    def test_multiple_or_invalid_pids_rejected_before_quit(self):
        for pids in ([41, 42], [True], [-1], ['41']):
            with self.subTest(pids=pids), patch.object(desktop.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'quit: changed'):
                    desktop.quit_application(self.executable, pids=pids)
                run.assert_not_called()

    def test_changed_identity_or_rejected_quit_is_actionable(self):
        for state in ('changed', 'rejected'):
            with self.subTest(state=state), patch.object(desktop.subprocess, 'run', return_value=Mock(
                    returncode=0, stdout=json.dumps({'state': state}))):
                with self.assertRaisesRegex(ValueError, 'quit: '+state):
                    desktop.quit_application(self.executable, pids=[41])

    def test_native_error_never_leaks_the_script_or_private_stderr(self):
        result = Mock(returncode=1, stdout='', stderr='sensitive path and script')
        with patch.object(desktop.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'quit: native-error; code: 1') as error:
                desktop.quit_application(self.executable, pids=[41])
        self.assertNotIn('sensitive', str(error.exception))

    def test_timeout_is_unknown_and_is_not_retried(self):
        with patch.object(desktop.subprocess, 'run', side_effect=subprocess.TimeoutExpired('osascript', 10)) as run:
            with self.assertRaisesRegex(ValueError, 'quit: timeout'):
                desktop.quit_application(self.executable, pids=[41])
        self.assertEqual(run.call_count, 1)

    def test_missing_or_invalid_receipt_is_not_success(self):
        for receipt in ('', 'null', '[]', '{}', '{"state":"other"}'):
            with self.subTest(receipt=receipt), patch.object(desktop.subprocess, 'run', return_value=Mock(
                    returncode=0, stdout=receipt)):
                with self.assertRaisesRegex(ValueError, 'quit: invalid-response'):
                    desktop.quit_application(self.executable, pids=[41])

    def test_app_stop_waits_for_real_exit_and_does_not_force_a_cancelled_quit(self):
        app = DesktopApp(self.executable, '/fixture/home')
        with patch('bridge.clients.desktop_app.sys.platform', 'darwin'), \
                patch.object(app, 'processes', return_value=[41]), \
                patch.object(desktop, 'quit_application', return_value='submitted') as quit_, \
                patch.object(desktop, 'terminate') as signal, \
                patch('bridge.clients.desktop_app.time.monotonic', side_effect=[0, 26]):
            with self.assertRaisesRegex(ValueError, '尚未退出.*尚未强制'):
                app.stop(gui_pids=[41], provider='codex')
        quit_.assert_called_once_with(app.executable, pids=[41])
        signal.assert_not_called()


if __name__ == '__main__':
    unittest.main()
