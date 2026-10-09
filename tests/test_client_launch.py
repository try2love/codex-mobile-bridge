import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.integrations.client_launch import launch_deepseek


class ClientLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.executable = self.root/'DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'app')
        self.home = self.root/'existing-user-data'
        self.home.mkdir()
        self.descriptor = {'id': 'deepseek', 'installed': True, 'executable': str(self.executable),
                           'dataDirectory': str(self.home), 'restartRequired': True}
        self.app = Mock(executable=self.executable, home=self.home)
        self.factory = patch('bridge.integrations.client_launch.DesktopApp', return_value=self.app).start()
        self.addCleanup(patch.stopall)
        self.commands = patch('bridge.integrations.client_launch._commands', side_effect=lambda app, pids: {pid: str(app.executable) for pid in pids}).start()

    def test_scan_does_not_restart_running_desktop(self):
        self.app.processes.return_value = [123]
        with patch('bridge.integrations.client_launch.subprocess.run') as run:
            result = launch_deepseek(self.descriptor)
        self.assertEqual(result, {'running': True, 'launched': False, 'restarted': False, 'restartRequired': True})
        self.app.stop.assert_not_called()
        run.assert_not_called()

    def test_orphan_host_is_not_reported_as_an_open_desktop(self):
        self.app.processes.return_value = [123]
        self.commands.side_effect = lambda app, pids: {123: str(app.executable)+' --expose-internals /app/@deepseek-ai/dsh-desktop-host/lib/index.js /dsh '+str(app.home/'profiles/desktop')+' /runtime'}
        with patch('bridge.integrations.client_launch.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, '残留.*后台实例'):
                launch_deepseek(self.descriptor)
        self.app.stop.assert_not_called(); run.assert_not_called()

    def test_mac_launch_preserves_selected_home_and_does_not_start_a_cli_runtime(self):
        self.app.processes.side_effect = [[], [123]]
        with patch('bridge.integrations.client_launch.sys.platform', 'darwin'), patch('bridge.integrations.client_launch.subprocess.run') as run:
            result = launch_deepseek(self.descriptor)
        command = run.call_args.args[0]
        self.assertEqual(command, ['/usr/bin/open', '-a', str(self.root/'DeepSeek Harness.app'), '--env', 'DSH_HOME='+str(self.home)])
        self.assertTrue(result['running'])
        self.assertTrue(result['launched'])
        self.assertFalse(result['restarted'])
        self.app.start.assert_not_called()

    def test_explicit_restart_waits_for_graceful_stop(self):
        self.app.processes.return_value = [123]
        self.app.stop.side_effect = TimeoutError('desktop is still closing')
        with patch('bridge.integrations.client_launch.subprocess.run') as run:
            with self.assertRaises(TimeoutError):
                launch_deepseek(self.descriptor, restart=True)
        self.app.stop.assert_called_once()
        run.assert_not_called()

    def test_linux_launch_uses_gui_and_selected_existing_home(self):
        self.app.processes.side_effect = [[], [123]]
        with patch('bridge.integrations.client_launch.sys.platform', 'linux'):
            result = launch_deepseek(self.descriptor)
        args, kwargs = self.app.launch.call_args
        self.assertEqual(args, ())
        self.factory.assert_called_with(str(self.executable), str(self.home))
        self.assertEqual(kwargs['env']['DSH_HOME'], str(self.home))
        self.assertEqual(kwargs['cwd'], self.home)
        self.assertTrue(kwargs['start_new_session'])
        self.assertTrue(result['running'])

    def test_windows_launch_detaches_only_the_selected_gui(self):
        self.app.processes.side_effect = [[], [123]]
        with patch('bridge.integrations.client_launch.sys.platform', 'win32'), patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, create=True):
            result = launch_deepseek(self.descriptor)
        self.assertEqual(self.app.launch.call_args.kwargs['creationflags'], 512)
        self.assertTrue(self.app.launch.call_args.kwargs['close_fds'])
        self.assertTrue(result['launched'])

    def test_never_launches_codex(self):
        with self.assertRaises(ValueError):
            launch_deepseek({**self.descriptor, 'id': 'codex'})
        self.factory.assert_not_called()

    def test_first_launch_lets_actual_desktop_initialize_its_home(self):
        self.home.rmdir()
        self.app.processes.side_effect = [[], [123]]
        with patch('bridge.integrations.client_launch.sys.platform', 'linux'):
            result = launch_deepseek(self.descriptor)
        self.assertTrue(result['launched'])
        self.assertEqual(self.app.launch.call_args.kwargs['cwd'], self.root)
        self.assertEqual(self.app.launch.call_args.kwargs['env']['DSH_HOME'], str(self.home))
        self.assertFalse(self.home.exists())


class ProcessLifecycleTests(unittest.TestCase):
    def test_windows_claude_cannot_be_quit_by_closing_its_window(self):
        from bridge.integrations.client_launch import stop_client
        with patch('bridge.integrations.client_launch.sys.platform', 'win32'), \
                patch('bridge.integrations.client_launch._app') as factory, \
                patch('bridge.integrations.client_launch.inspect_client') as inspect:
            with self.assertRaisesRegex(ValueError, 'Claude Desktop 不支持后台退出'):
                stop_client({'id': 'claude'}, state={})
        factory.assert_not_called()
        inspect.assert_not_called()

    def test_dsh_multiple_or_wrong_profile_hosts_fail_closed(self):
        from bridge.integrations.client_launch import inspect_client
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder)/'Harness'; executable.touch()
            descriptor = {'id': 'deepseek', 'installed': True, 'executable': str(executable), 'dataDirectory': folder}
            command = str(executable)+' --expose-internals /app/@deepseek-ai/dsh-desktop-host/lib/index.js /dsh '+str(Path(folder)/'profiles/desktop')+' /runtime'
            app = Mock(executable=executable, home=Path(folder))
            app.processes.return_value = [11, 12, 13]
            with patch('bridge.integrations.client_launch.DesktopApp', return_value=app), patch('bridge.integrations.client_launch._commands', return_value={11: str(executable), 12: command, 13: command}):
                result = inspect_client(descriptor)
                self.assertTrue(result['unknown'])
                self.assertEqual(result['runtimePids'], [12, 13])
            app.processes.return_value = [11, 12]
            with patch('bridge.integrations.client_launch.DesktopApp', return_value=app), patch('bridge.integrations.client_launch._commands', return_value={11: str(executable), 12: command.replace(str(Path(folder)/'profiles/desktop')+' ', str(Path(folder)/'profiles/desktop-other')+' ')}):
                self.assertTrue(inspect_client(descriptor)['unknown'])

    def test_windows_electron_children_are_not_additional_main_apps(self):
        from bridge.integrations.client_launch import inspect_client
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder)/'Claude'; executable.touch()
            descriptor = {'id': 'claude', 'installed': True, 'executable': str(executable), 'dataDirectory': folder}
            app = Mock(executable=executable, home=Path(folder)); app.processes.return_value = [11, 12]
            with patch('bridge.integrations.client_launch.DesktopApp', return_value=app), patch('bridge.integrations.client_launch._commands', return_value={11: str(executable), 12: str(executable)+' --type=renderer'}):
                result = inspect_client(descriptor)
                self.assertFalse(result['unknown']); self.assertEqual(result['mainPids'], [11])

    def test_process_changed_after_idle_check_never_sends_quit(self):
        from bridge.integrations.client_launch import stop_client
        state = {'running': True, 'pids': [11], 'mainPids': [11], 'runtimePids': [], 'unknown': False}
        with patch('bridge.integrations.client_launch.inspect_client', return_value={**state, 'pids': [11, 22]}), patch('bridge.integrations.client_launch._app') as factory:
            with self.assertRaisesRegex(ValueError, '进程已变化'):
                stop_client({}, state=state)
            factory.assert_not_called()

    def test_mac_quit_includes_only_preverified_idle_native_host(self):
        import signal
        from bridge.desktop_app import DesktopApp
        app = DesktopApp('/Applications/Fixture.app/Contents/MacOS/Fixture', '/fixture/home')
        with patch('bridge.desktop_app.sys.platform', 'darwin'), patch.object(app, 'processes', side_effect=[[11, 12], [12], []]), patch('bridge.desktop_app.subprocess.run') as run, patch('bridge.desktop_app.os.kill') as kill:
            app.stop(runtime_pids=[12, 999], gui_pids=[11])
            run.assert_called_once(); self.assertEqual(run.call_args.args[0][0], 'osascript')
            kill.assert_called_once_with(12, signal.SIGTERM)

    def test_mac_process_paths_are_never_truncated_at_terminal_width(self):
        from bridge.desktop_app import DesktopApp
        app = DesktopApp('/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness', '/fixture/home')
        with patch('bridge.desktop_app.sys.platform', 'darwin'), patch('bridge.desktop_app.os.getuid', return_value=1000, create=True), patch('bridge.desktop_app.subprocess.run', return_value=Mock(stdout='42 '+str(app.executable))) as run:
            self.assertEqual(app.processes(), [42])
            self.assertIn('-ww', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
