import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge import windows_codex_quit as quit
from bridge.desktop_app import DesktopApp, process_inventory


class CodexQuitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.executable = self.root/'Codex 中文/ChatGPT.exe'
        self.executable.parent.mkdir()
        self.executable.write_bytes(b'fixture')
        self.helper = self.root/'codex-quit-helper.exe'
        self.helper.write_bytes(b'fixture')
        self.app = SimpleNamespace(executable=self.executable)
        self.clock = 0.0
        self.states = [self.state(41)]
        self.calls = []
        self.inventory = self.mock('process_inventory', side_effect=self.read_state)
        self.created = self.mock('_creation_time', return_value=100)
        self.mock('_windows_argv', side_effect=json.loads)
        self.mock('helper_path', return_value=self.helper)
        self.mock('sys.platform', 'win32')
        self.mock('subprocess.CREATE_NO_WINDOW', 0x08000000, create=True)
        self.mock('time.monotonic', side_effect=lambda: self.clock)
        self.mock('time.sleep', side_effect=self.sleep)
        self.runner = self.mock('subprocess.run', return_value=self.receipt('submitted'))

    def mock(self, name, *args, **kwargs):
        context = patch('bridge.windows_codex_quit.'+name, *args, **kwargs)
        result = context.start()
        self.addCleanup(context.stop)
        return result

    def sleep(self, seconds):
        self.clock += seconds

    def state(self, main=None, children=(), unknown=()):
        commands = {}
        if main is not None:
            commands[main] = json.dumps([str(self.executable)])
        for pid in children:
            commands[pid] = json.dumps([str(self.executable), '--type=renderer'])
        for pid in unknown:
            commands[pid] = ''
        return {'pids': list(commands), 'commands': commands}

    def read_state(self, paths, *, timeout):
        self.calls.append((self.clock, timeout))
        self.assertEqual(paths, [self.executable])
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 15)
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {self.executable: state}

    def receipt(self, state, pid=41, *, marker=True, code=0):
        text = (json.dumps({'quitPhase': 'dispatching', 'pid': pid})+'\n') if marker else ''
        text += json.dumps({'quitState': state, 'pid': pid, 'reason': '请在电脑端检查后重试'})
        return subprocess.CompletedProcess([], code, text, '')

    def test_already_stopped_needs_no_helper(self):
        self.states = [self.state()]
        quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_same_main_identity_receives_one_native_quit(self):
        self.states = [self.state(41), self.state(41), self.state(41), self.state()]
        quit.stop_codex(self.app, [41])
        self.runner.assert_called_once()
        self.assertEqual(self.runner.call_args.args[0],
            [str(self.helper), '41', '--quit', str(self.executable), '100'])
        self.assertLess(self.runner.call_args.kwargs['timeout'], quit.BUDGET)

    def test_renderer_alive_after_exited_receipt_is_not_success(self):
        self.runner.return_value = self.receipt('exited')
        self.states = [self.state(41, [42]), self.state(41, [42]), self.state(children=[42])]
        with self.assertRaisesRegex(ValueError, '重试'):
            quit.stop_codex(self.app)
        self.assertEqual(self.clock, quit.BUDGET)
        self.runner.assert_called_once()

    def test_renderer_drains_after_main_exit(self):
        self.states = [self.state(41, [42]), self.state(41, [42]),
                       self.state(children=[42]), self.state()]
        quit.stop_codex(self.app)
        self.runner.assert_called_once()

    def test_initial_renderer_only_waits_without_sending(self):
        self.states = [self.state(children=[42]), self.state(children=[42]), self.state()]
        quit.stop_codex(self.app, [41])
        self.runner.assert_not_called()

    def test_initial_renderer_only_does_not_report_live_process_as_success(self):
        self.states = [self.state(children=[42])]
        with self.assertRaisesRegex(ValueError, '尚未完成'):
            quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_malformed_receipt_is_observed_without_replay(self):
        self.runner.return_value = subprocess.CompletedProcess([], 0, '{bad-json', '')
        self.states = [self.state(41), self.state(41), self.state(41), self.state()]
        quit.stop_codex(self.app)
        self.runner.assert_called_once()

    def test_dispatch_marker_with_broken_receipt_is_observed_without_replay(self):
        self.runner.return_value = subprocess.CompletedProcess([], 1,
            json.dumps({'quitPhase': 'dispatching', 'pid': 41})+'\n{bad-json', '')
        self.states = [self.state(41), self.state(41), self.state(41), self.state()]
        quit.stop_codex(self.app)
        self.runner.assert_called_once()

    def test_wrong_receipt_pid_is_never_trusted_as_exited(self):
        self.runner.return_value = self.receipt('exited', pid=99)
        with self.assertRaisesRegex(ValueError, '尚未完成'):
            quit.stop_codex(self.app)
        self.runner.assert_called_once()

    def test_timeout_preserves_time_to_observe_and_never_replays(self):
        def timeout(command, **options):
            self.clock += options['timeout']
            raise subprocess.TimeoutExpired(command, options['timeout'],
                output=json.dumps({'quitPhase': 'dispatching', 'pid': 41}).encode())
        self.runner.side_effect = timeout
        self.states = [self.state(41), self.state(41), self.state(children=[42]), self.state()]
        quit.stop_codex(self.app)
        self.runner.assert_called_once()
        self.assertLessEqual(self.clock, quit.BUDGET)
        self.assertTrue(any(at >= 22 for at, _ in self.calls))

    def test_new_main_after_dispatch_stops_observation(self):
        self.states = [self.state(41), self.state(41), self.state(77)]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_called_once()
        self.assertEqual(self.clock, 0)

    def test_new_main_during_initial_renderer_drain_is_not_targeted(self):
        self.states = [self.state(children=[42]), self.state(77)]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_same_pid_reused_after_dispatch_is_rejected(self):
        self.created.side_effect = [100, 100, 101]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_called_once()

    def test_main_changes_before_helper_is_not_targeted(self):
        self.states = [self.state(41), self.state(77)]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_unknown_process_after_dispatch_stops_observation(self):
        self.states = [self.state(41), self.state(41), self.state(41, unknown=[77])]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_called_once()

    def test_initial_unknown_process_sends_nothing(self):
        self.states = [self.state(41, unknown=[77])]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_identity_read_race_accepts_only_fresh_complete_absence(self):
        self.created.side_effect = OSError('exited during query')
        self.states = [self.state(41), self.state()]
        quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_identity_unreadable_with_renderers_remaining_fails_closed(self):
        self.created.side_effect = OSError('unreadable')
        self.states = [self.state(41, [42]), self.state(children=[42])]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_gui_selection_must_match_unique_main(self):
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app, [77])
        self.runner.assert_not_called()

    def test_missing_helper_is_clear_error_and_sends_nothing(self):
        self.helper.unlink()
        with self.assertRaisesRegex(ValueError, '缺少 Codex 原生退出组件'):
            quit.stop_codex(self.app)
        self.runner.assert_not_called()

    def test_helper_start_error_is_not_raw_oserror(self):
        self.runner.side_effect = OSError('not executable')
        with self.assertRaisesRegex(ValueError, '无法启动 Codex 原生退出组件'):
            quit.stop_codex(self.app)
        self.runner.assert_called_once()

    def test_valid_pre_dispatch_failure_returns_native_reason(self):
        self.runner.return_value = self.receipt('failed', marker=False, code=1)
        with self.assertRaisesRegex(ValueError, '请在电脑端检查后重试'):
            quit.stop_codex(self.app)
        self.assertEqual(self.clock, 0)
        self.runner.assert_called_once()

    def test_explicit_pending_confirmation_returns_after_final_process_check(self):
        self.runner.return_value = self.receipt('pending')
        with self.assertRaisesRegex(ValueError, '请在电脑端检查后重试'):
            quit.stop_codex(self.app)
        self.assertEqual(self.clock, 0)
        self.assertEqual(len(self.calls), 3)
        self.runner.assert_called_once()

    def test_pending_receipt_still_succeeds_if_all_processes_already_exited(self):
        self.runner.return_value = self.receipt('pending')
        self.states = [self.state(41), self.state(41), self.state()]
        quit.stop_codex(self.app)
        self.assertEqual(self.clock, 0)
        self.runner.assert_called_once()

    def test_only_explicit_windows_codex_stop_uses_native_wrapper(self):
        app = DesktopApp(self.executable, self.root)
        with patch.object(quit, 'stop_codex') as native, patch.object(app, 'processes', return_value=[]):
            app.stop(provider='codex', gui_pids=[41], runtime_pids=[42])
            native.assert_called_once_with(app, gui_pids=[41])
            native.reset_mock()
            app.stop(provider='claude')
            app.stop()
            native.assert_not_called()

    def test_other_platform_codex_stop_keeps_existing_path(self):
        app = DesktopApp(self.executable, self.root)
        with patch.object(quit.sys, 'platform', 'linux'), patch.object(quit, 'stop_codex') as native, \
                patch.object(app, 'processes', return_value=[]):
            app.stop(provider='codex')
            native.assert_not_called()

    def test_inventory_optional_timeout_keeps_old_windows_default(self):
        self.runner.return_value = subprocess.CompletedProcess([], 0, '[]', '')
        process_inventory([self.executable])
        self.assertEqual(self.runner.call_args.kwargs['timeout'], 15)
        process_inventory([self.executable], timeout=2.5)
        self.assertEqual(self.runner.call_args.kwargs['timeout'], 2.5)

    def test_quoted_windows_type_argument_is_not_misclassified_as_main(self):
        self.states = [self.state(41)]
        self.states[0]['pids'].append(42)
        self.states[0]['commands'][42] = json.dumps([str(self.executable), '--type', 'utility'])
        self.states += [self.state(41), self.state()]
        quit.stop_codex(self.app, [41])
        self.runner.assert_called_once()

    def test_unrecognized_process_type_sends_nothing(self):
        state = self.state(41, [42])
        state['commands'][42] = json.dumps([str(self.executable), '--type=unexpected'])
        self.states = [state]
        with self.assertRaisesRegex(ValueError, '运行实例已变化'):
            quit.stop_codex(self.app)
        self.runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
