import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bridge import windows_force_exit as force


class FakeNative:
    def __init__(self, executable):
        self.executable = force._path(executable)
        self.session, self.protected_pids, self.protected_paths = 3, {999}, {'gateway.exe'}
        self.rows, self.handles, self.events, self.closed = {}, {}, [], []
        self.sequence, self.on_open, self.on_terminate = 0, None, None

    def add(self, pid, arguments=(), **values):
        self.rows[pid] = {'pid': pid, 'image': self.executable, 'created': pid * 100,
                          'session': self.session, 'alive': True,
                          'command': subprocess.list2cmdline([self.executable, *arguments]), **values}

    def open(self, pid, *, terminate):
        self.events.append(('open', pid, terminate))
        if self.on_open:
            self.on_open(pid, terminate)
        if pid not in self.rows or not self.rows[pid]['alive']:
            error = OSError('gone')
            error.winerror = 87
            raise error
        self.sequence += 1
        self.handles[self.sequence] = self.rows[pid]
        return self.sequence

    def identity(self, handle):
        return {key: self.handles[handle][key] for key in ('pid', 'image', 'created', 'session')}

    def close(self, handle):
        self.closed.append(handle)

    def alive(self, handle):
        return self.handles[handle]['alive']

    def terminate(self, handle):
        row = self.handles[handle]
        self.events.append(('terminate', row['pid'], handle))
        if self.on_terminate:
            self.on_terminate(row)
        row['alive'] = False


class ForceExitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.executable = Path(self.directory.name)/'Fixture.exe'
        self.executable.write_bytes(b'MZ')
        self.descriptor = {'id': 'codex', 'installed': True, 'executable': str(self.executable)}
        self.native = FakeNative(self.executable)
        self.native.add(10)
        self.native.add(20, ['--type=renderer'])
        self.state = {'pids': [10, 20], 'mainPids': [10], 'unknown': False}
        self.inventory_calls, self.inventory_hook = 0, None
        for name, value in [('_Native', lambda: self.native), ('process_inventory', self.inventory)]:
            mock = patch.object(force, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(force.sys, 'platform', 'win32')
        mock.start()
        self.addCleanup(mock.stop)
        # Native parser exercised separately on Windows; deterministic argv in
        # mocks permits running this orchestration suite on other platforms.
        if os.name != 'nt':
            import shlex
            mock = patch.object(force, '_windows_argv', lambda value: tuple(shlex.split(value)))
            mock.start()
            self.addCleanup(mock.stop)

    def inventory(self, paths, *, timeout):
        self.assertEqual(list(paths), [self.executable])
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 15)
        self.inventory_calls += 1
        if self.inventory_hook:
            self.inventory_hook(self.inventory_calls)
        rows = {pid: row for pid, row in self.native.rows.items() if row['alive']}
        return {self.executable: {'pids': list(rows), 'commands': {pid: row['command'] for pid, row in rows.items()}}}

    def run_force(self):
        return force.force_stop_client(self.descriptor, state=self.state)

    def stopped(self):
        return [event[1] for event in self.native.events if event[0] == 'terminate']

    def rejected(self, message=force.CHANGED):
        with self.assertRaisesRegex(ValueError, message):
            self.run_force()
        self.assertEqual(self.stopped(), [])

    def test_opens_all_targets_before_main_then_children(self):
        self.run_force()
        self.assertEqual(self.stopped(), [10, 20])
        first = next(index for index, event in enumerate(self.native.events) if event[0] == 'terminate')
        self.assertEqual([event[1] for event in self.native.events[:first] if event[0] == 'open' and event[2]], [10, 20])
        self.assertEqual(sorted(self.native.closed), sorted(self.native.handles))

    def test_all_terminate_calls_use_original_handles(self):
        self.run_force()
        self.assertEqual([event[2] for event in self.native.events if event[0] == 'terminate'], [1, 2])

    def test_no_processes_is_success_without_write_handle(self):
        self.native.rows = {}
        self.run_force()
        self.assertFalse(any(event[0] == 'open' and event[2] for event in self.native.events))

    def test_children_only_accepted(self):
        del self.native.rows[10]
        self.state['mainPids'] = []
        self.run_force()
        self.assertEqual(self.stopped(), [20])

    def test_natural_main_exit_during_validation_does_not_add_targets(self):
        def hook(count):
            if count == 2:
                self.native.rows[10]['alive'] = False
        self.inventory_hook = hook
        self.run_force()
        self.assertEqual(self.stopped(), [20])

    def test_natural_exit_between_inventory_and_query_open(self):
        def hook(pid, terminate):
            if pid == 10 and not terminate:
                self.native.rows[10]['alive'] = False
        self.native.on_open = hook
        self.run_force()
        self.assertEqual(self.stopped(), [20])

    def test_natural_exit_between_inventory_and_first_write_open(self):
        def hook(pid, terminate):
            if pid == 10 and terminate:
                self.native.rows[10]['alive'] = False
        self.native.on_open = hook
        self.run_force()
        self.assertEqual(self.stopped(), [20])

    def test_natural_exit_during_terminate_not_failure(self):
        def hook(row):
            if row['pid'] == 10:
                row['alive'] = False
                raise PermissionError('already exited')
        self.native.on_terminate = hook
        self.run_force()
        self.assertFalse(self.native.rows[20]['alive'])

    def test_empty_inventory_with_held_live_processes_never_succeeds(self):
        def hook(count):
            if count == 2:
                self.native.rows = {}
        self.inventory_hook = hook
        self.rejected()
        self.assertTrue(self.native.handles[1]['alive'])

    def test_initial_empty_inventory_rechecks_known_live_pid(self):
        with patch.object(force, 'process_inventory', return_value={self.executable: {'pids': [], 'commands': {}}}):
            self.rejected()

    def test_initial_empty_inventory_access_denied_is_not_absence(self):
        self.native.on_open = lambda pid, terminate: (_ for _ in ()).throw(PermissionError('denied'))
        with patch.object(force, 'process_inventory', return_value={self.executable: {'pids': [], 'commands': {}}}):
            self.rejected(force.FAILED)

    def test_initial_missing_pid_reused_by_other_image_is_not_targeted(self):
        self.native.rows[10]['image'] = force._path(self.executable.parent/'neighbor/Fixture.exe')
        self.native.rows[20]['image'] = force._path(self.executable.parent/'neighbor/Fixture.exe')
        with patch.object(force, 'process_inventory', return_value={self.executable: {'pids': [], 'commands': {}}}):
            self.run_force()
        self.assertEqual(self.stopped(), [])

    def test_access_denied_then_inventory_omission_is_not_success(self):
        def on_open(pid, terminate):
            if pid == 10 and terminate:
                self.native.rows = {}
                raise PermissionError('denied')
        self.native.on_open = on_open
        self.rejected(force.FAILED)

    def test_multiple_main_processes_rejected(self):
        self.native.rows[20]['command'] = self.native.rows[10]['command']
        self.rejected()

    def test_unknown_state_rejected(self):
        self.state['unknown'] = True
        self.rejected()

    def test_new_initial_main_rejected(self):
        self.state['pids'] = [20]
        self.state['mainPids'] = []
        self.rejected()

    def test_new_main_before_first_termination_rejected(self):
        def hook(count):
            if count == 2:
                self.native.rows[10]['alive'] = False
                self.native.add(11)
        self.inventory_hook = hook
        self.rejected()

    def test_new_main_after_partial_exit_stops_without_killing_it(self):
        def hook(row):
            if row['pid'] == 10:
                self.native.add(11)
        self.native.on_terminate = hook
        with self.assertRaisesRegex(ValueError, force.CHANGED):
            self.run_force()
        self.assertEqual(self.stopped(), [10])
        self.assertTrue(self.native.rows[11]['alive'])
        self.assertTrue(self.native.rows[20]['alive'])

    def test_new_child_is_never_appended(self):
        self.native.on_terminate = lambda row: self.native.add(21, ['--type=renderer'])
        with self.assertRaisesRegex(ValueError, force.CHANGED):
            self.run_force()
        self.assertEqual(self.stopped(), [10])

    def test_pid_reuse_does_not_retarget_held_handle(self):
        old = self.native.rows[20]
        def hook(count):
            if count == 2:
                self.native.add(20, ['--type=renderer'], created=999999)
        self.inventory_hook = hook
        self.rejected()
        self.assertIs(self.native.handles[2], old)
        self.assertTrue(self.native.rows[20]['alive'])

    def test_same_basename_different_image_rejected(self):
        self.native.rows[20]['image'] = force._path(self.executable.parent/'other'/'Fixture.exe')
        self.rejected()

    def test_other_session_rejected(self):
        self.native.rows[20]['session'] = 77
        self.rejected()

    def test_self_executable_rejected(self):
        self.native.protected_paths.add(force._path(self.executable))
        self.rejected(force.PROTECTED)

    def test_ancestor_pid_rejected(self):
        self.native.protected_pids.add(20)
        self.rejected(force.PROTECTED)

    def test_access_denied_opening_second_target_kills_none(self):
        def hook(pid, terminate):
            if pid == 20 and terminate:
                raise PermissionError('denied')
        self.native.on_open = hook
        self.rejected(force.FAILED)
        self.assertEqual(self.native.closed, [1])

    def test_partial_termination_failure_is_not_success(self):
        def hook(row):
            if row['pid'] == 20:
                raise PermissionError('denied')
        self.native.on_terminate = hook
        with self.assertRaisesRegex(ValueError, force.FAILED):
            self.run_force()
        self.assertFalse(self.native.rows[10]['alive'])
        self.assertTrue(self.native.rows[20]['alive'])
        self.assertEqual(sorted(self.native.closed), sorted(self.native.handles))

    def test_missing_command_rejected(self):
        self.native.rows[20]['command'] = ''
        self.rejected()

    def test_execution_modes_rejected(self):
        for argument in ('--eval=alert(1)', '--run', '--print=1', '--expose-internals', '-e'):
            with self.subTest(argument=argument):
                self.native.add(20, ['--type=utility', argument])
                self.rejected()

    def test_unknown_and_duplicate_child_types_rejected(self):
        for arguments in (['--type=weird'], ['--type'], ['--type=renderer', '--type=gpu-process']):
            with self.subTest(arguments=arguments):
                self.native.add(20, arguments)
                self.rejected()

    def test_explicit_spaced_type_argument_accepted(self):
        self.native.add(20, ['--type', 'utility', '--utility-sub-type=node.mojom.NodeService'])
        self.run_force()
        self.assertEqual(self.stopped(), [10, 20])

    def test_profile_argument_is_parsed_without_substring_matching(self):
        self.native.add(10, ['--user-data-dir=C:\\A Profile\\Example', '--hidden'])
        self.run_force()

    def test_changed_command_on_same_pid_rejected(self):
        def hook(count):
            if count == 2:
                self.native.add(20, ['--type=utility'])
        self.inventory_hook = hook
        self.rejected()

    def test_non_absolute_argv_zero_rejected(self):
        self.native.rows[20]['command'] = 'Fixture.exe --type=renderer'
        self.rejected()

    def test_snapshot_oserror_translated(self):
        self.inventory_hook = lambda count: (_ for _ in ()).throw(OSError('native detail'))
        self.rejected(force.FAILED)

    def test_timeout_remains_bounded(self):
        with patch.object(force.time, 'monotonic', side_effect=[0, 0, 26]):
            self.rejected(force.TIMEOUT)

    def test_invalid_descriptor_and_platform(self):
        self.descriptor['id'] = 'other'
        self.rejected(force.INVALID)
        with patch.object(force.sys, 'platform', 'darwin'):
            self.rejected(force.UNSUPPORTED)


if __name__ == '__main__':
    unittest.main()
