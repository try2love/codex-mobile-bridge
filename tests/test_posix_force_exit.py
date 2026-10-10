"""Identity and confirmation regressions; unit tests never signal live apps."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bridge.platforms.posix.force_exit import force_stop


@unittest.skipIf(os.name == 'nt', 'POSIX process identity fixture')
class PosixForceExitTests(unittest.TestCase):
    def setUp(self):
        self.executable = str(Path('/fixture/Desktop').resolve())
        self.commands = {701: self.executable, 702: self.executable + ' --type=utility'}
        self.live = {pid: {'pid': pid, 'parent': 1, 'uid': os.getuid(), 'ruid': os.getuid(),
                          'image': self.executable, 'birth': (pid, 5), 'zombie': False}
                     for pid in self.commands}
        own = {'pid': os.getpid(), 'parent': 1, 'uid': os.getuid(), 'ruid': os.getuid(),
               'image': '/fixture/gateway', 'birth': (1, 1), 'zombie': False}
        self.native = Mock()
        def identity(pid):
            if pid == os.getpid():
                return dict(own)
            if pid not in self.live:
                raise ProcessLookupError()
            return copy.deepcopy(self.live[pid])
        self.native.identity.side_effect = identity
        self.native.open.side_effect = lambda value: copy.deepcopy(value)
        self.native.terminate.side_effect = lambda handle: self.live.pop(handle['pid'])
        self.desktop = Mock()
        self.desktop.processes.side_effect = lambda predicate: list(self.live)
        self.desktop.commands.side_effect = lambda pids: {pid: self.commands[pid] for pid in pids}
        for target, value in [('bridge.platforms.posix.force_exit.sys.platform', 'darwin'),
                              ('bridge.platforms.macos.force_exit.Native', Mock(return_value=self.native)),
                              ('bridge.platforms.posix.force_exit.desktop', Mock(return_value=self.desktop))]:
            mocked = patch(target, value); mocked.start(); self.addCleanup(mocked.stop)

    def test_all_targets_are_bound_before_first_signal_and_handles_are_closed(self):
        def terminate(handle):
            self.assertEqual(self.native.open.call_count, 2)
            self.live.pop(handle['pid'])
        self.native.terminate.side_effect = terminate
        force_stop(self.executable, self.commands)
        self.assertEqual(self.native.terminate.call_count, 2)
        self.assertEqual(self.native.close.call_count, 2)
        self.assertEqual(self.live, {})

    def test_other_user_or_different_image_never_reaches_signal(self):
        for field, value in [('uid', os.getuid()+1), ('image', '/fixture/another-app')]:
            before = self.live[702][field]
            self.live[702][field] = value
            with self.assertRaisesRegex(ValueError, '进程已变化'):
                force_stop(self.executable, self.commands)
            self.native.terminate.assert_not_called()
            self.live[702][field] = before

    def test_a_new_process_is_not_added_to_confirmed_target_set(self):
        self.live[703] = {**self.live[702], 'pid': 703}
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            force_stop(self.executable, self.commands)
        self.native.terminate.assert_not_called()

    def test_pid_reuse_after_binding_stops_before_signalling(self):
        def bind(value):
            handle = copy.deepcopy(value)
            if value['pid'] == 702:
                self.live[701]['birth'] = (900, 1)
            return handle
        self.native.open.side_effect = bind
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            force_stop(self.executable, self.commands)
        self.native.terminate.assert_not_called()
        self.assertEqual(self.native.close.call_count, 2)

    def test_inventory_omission_cannot_hide_a_still_live_bound_process(self):
        self.desktop.processes.side_effect = [list(self.live), []]
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            force_stop(self.executable, self.commands)
        self.assertEqual(self.native.terminate.call_count, 1)
        self.assertIn(702, self.live)

    def test_first_inventory_omission_cannot_skip_a_known_live_target(self):
        self.desktop.processes.return_value = []
        self.desktop.processes.side_effect = None
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            force_stop(self.executable, self.commands)
        self.native.terminate.assert_not_called()
        self.assertEqual(set(self.live), set(self.commands))

    def test_inventory_omission_cannot_hide_a_reused_pid_of_the_selected_app(self):
        def bind(value):
            handle = copy.deepcopy(value)
            if value['pid'] == 702:
                self.live[701]['birth'] = (900, 1)
            return handle
        self.native.open.side_effect = bind
        self.desktop.processes.side_effect = lambda predicate: [702]
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            force_stop(self.executable, self.commands)
        self.native.terminate.assert_not_called()

    def test_known_targets_that_naturally_exited_do_not_require_a_signal(self):
        self.live.clear()
        force_stop(self.executable, self.commands)
        self.native.terminate.assert_not_called()
        self.assertTrue({701, 702}.issubset({call.args[0] for call in self.native.identity.call_args_list}))

    def test_gateway_and_ancestor_images_are_protected(self):
        with self.assertRaisesRegex(ValueError, '网关'):
            force_stop('/fixture/gateway', {})
        self.native.terminate.assert_not_called()


class ForceLifecycleBoundaryTests(unittest.TestCase):
    def test_matching_harness_hosts_can_be_explicitly_stopped_even_when_multiple(self):
        from bridge.clients.lifecycle import force_stop_client
        root = Path(__file__).resolve().parents[1]/'.tmp'
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            executable = Path(temporary)/'Harness'; executable.write_text('fixture')
            home = Path(temporary)/'profile'
            descriptor = {'id': 'deepseek', 'installed': True, 'executable': str(executable), 'dataDirectory': str(home)}
            state = {'running': True, 'pids': [701, 702], 'mainPids': [], 'runtimePids': [701, 702], 'unknown': True}
            commands = {pid: str(executable)+' --expose-internals /fixture/node_modules/@deepseek-ai/dsh-desktop-host/lib/index.js '+str(home/'profiles/desktop') for pid in state['pids']}
            with patch('bridge.clients.lifecycle.sys.platform', 'darwin'), \
                    patch('bridge.clients.lifecycle.inspect_client', return_value=state), \
                    patch('bridge.clients.lifecycle._commands', return_value=commands), \
                    patch('bridge.platforms.posix.force_exit.force_stop') as force:
                force_stop_client(descriptor, state=state)
                force.assert_called_once_with(executable.resolve(), commands)
                for dangerous in (' --eval dangerous', ' --user-data-dir=/other/profile'):
                    force.reset_mock(); commands[701] += dangerous
                    with self.assertRaisesRegex(ValueError, '进程已变化'):
                        force_stop_client(descriptor, state=state)
                    force.assert_not_called()

    def test_new_command_snapshot_cannot_reuse_old_profile_classification(self):
        from bridge.clients.lifecycle import force_stop_client
        root = Path(__file__).resolve().parents[1]/'.tmp'
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            executable = Path(temporary)/'Harness'; executable.write_text('fixture')
            home = Path(temporary)/'profile'
            descriptor = {'id': 'deepseek', 'installed': True, 'executable': str(executable), 'dataDirectory': str(home)}
            state = {'running': True, 'pids': [701], 'mainPids': [], 'runtimePids': [701], 'unknown': False}
            command = str(executable)+' --expose-internals /fixture/node_modules/@deepseek-ai/dsh-desktop-host/lib/index.js /another/profile/profiles/desktop'
            with patch('bridge.clients.lifecycle.sys.platform', 'darwin'), \
                    patch('bridge.clients.lifecycle.inspect_client', return_value=state), \
                    patch('bridge.clients.lifecycle._commands', return_value={701: command}), \
                    patch('bridge.platforms.posix.force_exit.force_stop') as force:
                with self.assertRaisesRegex(ValueError, '进程已变化'):
                    force_stop_client(descriptor, state=state)
                force.assert_not_called()

    def test_a_missing_pid_in_second_lifecycle_inventory_is_not_success(self):
        from bridge.clients.lifecycle import force_stop_client
        root = Path(__file__).resolve().parents[1]/'.tmp'
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            executable = Path(temporary)/'Harness'; executable.write_text('fixture')
            descriptor = {'id': 'deepseek', 'installed': True, 'executable': str(executable), 'dataDirectory': temporary}
            state = {'running': True, 'pids': [701], 'mainPids': [], 'runtimePids': [701], 'unknown': False}
            empty = {'running': False, 'pids': [], 'mainPids': [], 'runtimePids': [], 'unknown': False}
            with patch('bridge.clients.lifecycle.sys.platform', 'darwin'), \
                    patch('bridge.clients.lifecycle.inspect_client', return_value=empty), \
                    patch('bridge.clients.lifecycle._commands', return_value={}), \
                    patch('bridge.platforms.posix.force_exit.force_stop') as force:
                with self.assertRaisesRegex(ValueError, '进程已变化'):
                    force_stop_client(descriptor, state=state)
                force.assert_not_called()


class LinuxIdentityTests(unittest.TestCase):
    def test_an_exited_proc_entry_is_not_a_failed_force_exit(self):
        from bridge.platforms.linux.force_exit import Native
        with patch('bridge.platforms.linux.force_exit.Path') as path:
            root = path.return_value.__truediv__.return_value
            root.stat.side_effect = FileNotFoundError()
            root.exists.return_value = False
            with self.assertRaises(ProcessLookupError):
                Native.__new__(Native).identity(701)
            root.exists.return_value = True
            with self.assertRaises(FileNotFoundError):
                Native.__new__(Native).identity(701)
