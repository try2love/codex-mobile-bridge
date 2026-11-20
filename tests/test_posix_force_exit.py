"""Identity and confirmation regressions; unit tests never signal live apps."""
import copy
import os
from pathlib import Path
import posixpath
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

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

    def test_exit_confirmation_never_reads_commands_for_an_empty_inventory(self):
        def commands(pids):
            self.assertTrue(pids, 'empty inventory must not reach the native command reader')
            return {pid: self.commands[pid] for pid in pids}
        self.desktop.commands.side_effect = commands
        force_stop(self.executable, self.commands)
        self.assertEqual(self.live, {})
        self.native.terminate.reset_mock()
        force_stop(self.executable, self.commands)
        self.native.terminate.assert_not_called()

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

    def test_deleted_executable_identity_requires_explicit_opt_in(self):
        from bridge.platforms.linux.force_exit import Native
        fields = ['S', '12', *(['0']*17), '99']
        stat = Mock()
        stat.read_text.return_value = '701 (language_server) '+' '.join(fields)
        exe = Mock()
        exe.resolve.side_effect = FileNotFoundError()
        root = MagicMock()
        root.stat.return_value = SimpleNamespace(st_uid=1000)
        root.__truediv__.side_effect = lambda name: stat if name == 'stat' else exe
        readlink = Mock(return_value='/tmp/extracted/language_server (deleted)')
        with patch('bridge.platforms.linux.force_exit.Path') as path, \
             patch('bridge.platforms.linux.force_exit.os', SimpleNamespace(path=posixpath, readlink=readlink)):
            path.return_value.__truediv__.return_value = root
            native = Native.__new__(Native)
            with self.assertRaises(FileNotFoundError):
                native.identity(701)
            identity = native.identity(701, allow_deleted=True)
            for image in ('tmp/extracted/language_server (deleted)', '/tmp/extracted/language_server'):
                readlink.return_value = image
                with self.assertRaises(FileNotFoundError):
                    native.identity(701, allow_deleted=True)
        self.assertEqual(identity['pid'], 701)
        self.assertEqual(identity['image'], '/tmp/extracted/language_server (deleted)')

    def test_open_propagates_deleted_identity_opt_in_only_when_requested(self):
        from bridge.platforms.linux.force_exit import Native
        native = Native.__new__(Native)
        identity = {'pid': 701, 'image': '/tmp/extracted/language_server (deleted)'}
        with patch('bridge.platforms.linux.force_exit.os.pidfd_open', return_value=23, create=True), \
             patch('bridge.platforms.linux.force_exit.os.close') as close, \
             patch.object(native, 'identity', return_value=identity) as check:
            self.assertEqual(native.open(identity, allow_deleted=True), 23)
        check.assert_called_once_with(701, allow_deleted=True)
        close.assert_not_called()


class LinuxAncestorTests(unittest.TestCase):
    def test_unreadable_ancestor_image_keeps_parent_but_target_identity_stays_strict(self):
        from bridge.platforms.linux.force_exit import Native
        fields = ['S', '12', *(['0']*17), '99']
        with patch('bridge.platforms.linux.force_exit.Path') as path:
            root = path.return_value.__truediv__.return_value
            stat, image = Mock(), Mock()
            root.__truediv__.side_effect = lambda name: stat if name == 'stat' else image
            stat.read_text.return_value = '701 (sshd) '+ ' '.join(fields)
            image.resolve.side_effect = PermissionError(13, 'Permission denied')
            native = Native.__new__(Native)
            self.assertEqual(native.ancestor(701), {'parent': 12, 'image': ''})
            with self.assertRaises(PermissionError):
                native.identity(701)
            fields[-1] = '100'
            stat.read_text.side_effect = ['701 (sshd) '+' '.join(fields),
                                          '701 (sshd) '+' '.join([*fields[:-1], '101'])]
            with self.assertRaises(ProcessLookupError):
                native.ancestor(701)

    def test_deleted_ancestor_image_keeps_the_existing_fail_closed_contract(self):
        from bridge.platforms.linux.force_exit import Native
        fields = ['S', '12', *(['0']*17), '99']
        stat, image = Mock(), Mock()
        stat.read_text.return_value = '701 (appimage) '+' '.join(fields)
        image.resolve.side_effect = FileNotFoundError()
        root = MagicMock()
        root.__truediv__.side_effect = lambda name: stat if name == 'stat' else image
        with patch('bridge.platforms.linux.force_exit.Path') as path:
            path.return_value.__truediv__.return_value = root
            with self.assertRaises(FileNotFoundError):
                Native.__new__(Native).ancestor(701)

    def test_linux_protection_walk_does_not_use_partial_ancestor_data_for_targets(self):
        from bridge.platforms.posix.force_exit import force_stop
        native = Mock()
        native.ancestor.return_value = {'parent': 1, 'image': ''}
        native.identity.side_effect = PermissionError(13, 'Target image unavailable')
        with patch('bridge.platforms.posix.force_exit.sys.platform', 'linux'), \
                patch('bridge.platforms.linux.force_exit.Native', return_value=native):
            with self.assertRaisesRegex(ValueError, '权限'):
                force_stop('/fixture/client', {701: '/fixture/client'})
        native.ancestor.assert_called_once_with(os.getpid())
        native.identity.assert_called_once_with(701)
        native.open.assert_not_called()
        native.terminate.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux', 'Linux /proc deleted symlink fixture')
    def test_deleted_appimage_exe_symlink_is_opt_in_and_missing_non_deleted_fails(self):
        from bridge.platforms.linux.force_exit import Native
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)/'proc'
            deleted_root = proc/'701'
            deleted_root.mkdir(parents=True)
            (deleted_root/'stat').write_text('701 (language_server) S 700 ' + ' '.join(['0'] * 17) + ' 99 0')
            (deleted_root/'exe').symlink_to('/tmp/appimage_extracted_abc/resources/bin/language_server (deleted)')

            missing_root = proc/'702'
            missing_root.mkdir(parents=True)
            (missing_root/'stat').write_text('702 (broken) S 1 ' + ' '.join(['0'] * 17) + ' 99 0')
            (missing_root/'exe').symlink_to('/tmp/missing/binary')

            native = Native.__new__(Native)
            with patch('bridge.platforms.linux.force_exit.Path', side_effect=lambda value: proc if value == '/proc' else Path(value)):
                with self.assertRaises(FileNotFoundError):
                    native.identity(701)
                row = native.identity(701, allow_deleted=True)
                self.assertEqual(row['image'], '/tmp/appimage_extracted_abc/resources/bin/language_server (deleted)')
                with self.assertRaises(FileNotFoundError):
                    native.identity(702)
