import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.lifecycle import inspect_client, inspect_clients, stop_client


class ClientInventoryTests(unittest.TestCase):
    def setUp(self):
        uid = patch('bridge.clients.desktop_app.os.getuid', return_value=1000, create=True)
        uid.start(); self.addCleanup(uid.stop)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.descriptors = []
        for provider, name in [('codex', 'Codex'), ('claude', 'Claude'), ('deepseek', 'DeepSeek Harness')]:
            executable = self.root/(name+'.app')/'Contents/MacOS'/name
            executable.parent.mkdir(parents=True); executable.touch()
            self.descriptors.append({'id': provider, 'installed': True, 'executable': str(executable),
                                     'dataDirectory': str(self.root/(provider+' data'))})
        self.executables = {row['id']: row['executable'] for row in self.descriptors}
        host = (self.executables['deepseek']+' --expose-internals /app/@deepseek-ai/dsh-desktop-host/lib/index.js '
                +str(self.root/'deepseek data/profiles/desktop')+' /runtime')
        self.records = [(11, self.executables['codex'], self.executables['codex']),
                        (12, self.executables['claude'], self.executables['claude']),
                        (13, self.executables['claude'], self.executables['claude']+' --type=renderer'),
                        (14, self.executables['deepseek'], self.executables['deepseek']),
                        (15, self.executables['deepseek'], host),
                        (16, self.executables['codex']+' Other', self.executables['codex']+' Other')]

    def ps(self, args, **kwargs):
        if args[-1] == 'pid=,comm=':
            return subprocess.CompletedProcess(args, 0, '\n'.join(f'{pid} {exe}' for pid, exe, _ in self.records))
        pids = {int(pid) for pid in args[args.index('-p')+1].split(',')}
        return subprocess.CompletedProcess(args, 0, '\n'.join(f'{pid} {cmd}' for pid, _, cmd in self.records if pid in pids and cmd))

    def test_mac_single_inventory_matches_four_fresh_inspections(self):
        with patch('bridge.clients.desktop_app.sys.platform', 'darwin'), patch('bridge.platforms.macos.desktop.subprocess.run', side_effect=self.ps) as run:
            expected = {row['id']: inspect_client(row) for row in [self.descriptors[0], *self.descriptors]}
            self.assertEqual(run.call_count, 8)
            run.reset_mock(); result = inspect_clients(self.descriptors)
            self.assertEqual(result, expected); self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0][3], '11,12,13,14,15')
            self.assertTrue(all('-ww' in call.args[0] for call in run.call_args_list))
        self.assertEqual(result['claude']['mainPids'], [12])
        self.assertEqual(result['deepseek']['runtimePids'], [15])
        self.assertFalse(result['deepseek']['unknown'])

    def test_invalid_descriptor_is_isolated_and_empty_batch_spawns_nothing(self):
        invalid = [None, {}, {**self.descriptors[1], 'executable': str(self.root/'missing')}]
        with patch('bridge.clients.desktop_app.sys.platform', 'darwin'), patch('bridge.platforms.macos.desktop.subprocess.run', side_effect=self.ps) as run:
            result = inspect_clients([invalid[0], self.descriptors[0], *invalid[1:], self.descriptors[2]])
            self.assertEqual(set(result), {'codex', 'deepseek'}); self.assertEqual(run.call_count, 2)
            run.reset_mock(); self.assertEqual(inspect_clients(invalid), {}); run.assert_not_called()

    def test_missing_command_and_foreign_host_profile_keep_unknown_state(self):
        self.records = [(pid, exe, '' if pid == 11 else cmd.replace('/profiles/desktop ', '/profiles/desktop-other '))
                        for pid, exe, cmd in self.records]
        with patch('bridge.clients.desktop_app.sys.platform', 'darwin'), patch('bridge.platforms.macos.desktop.subprocess.run', side_effect=self.ps):
            result = inspect_clients(self.descriptors)
        self.assertTrue(result['codex']['running']); self.assertTrue(result['codex']['unknown'])
        self.assertFalse(result['claude']['unknown']); self.assertEqual(result['deepseek']['runtimePids'], [])
        self.assertTrue(result['deepseek']['unknown'])

    def test_second_display_cycle_and_safety_inspection_are_fresh(self):
        with patch('bridge.clients.desktop_app.sys.platform', 'darwin'), patch('bridge.platforms.macos.desktop.subprocess.run', side_effect=self.ps) as run:
            first = inspect_clients(self.descriptors)
            self.records = [(99 if pid == 11 else pid, exe, cmd) for pid, exe, cmd in self.records]
            second = inspect_clients(self.descriptors)
            self.assertEqual(first['codex']['pids'], [11]); self.assertEqual(second['codex']['pids'], [99])
            self.assertEqual(run.call_count, 4)
            with patch('bridge.clients.lifecycle.process_inventory', side_effect=AssertionError('Safety must not use display snapshots')):
                self.assertEqual(inspect_client(self.descriptors[0])['pids'], [99])
                with patch('bridge.clients.desktop_app.DesktopApp.stop') as stop:
                    with self.assertRaisesRegex(ValueError, '进程已变化'):
                        stop_client(self.descriptors[0], state=first['codex'])
                    stop.assert_not_called()
            self.assertEqual(run.call_count, 8)

    def test_windows_one_cim_call_includes_commands_and_preserves_case_matching(self):
        import ntpath
        rows = [{'ProcessId': pid, 'ExecutablePath': exe.upper(), 'CommandLine': cmd} for pid, exe, cmd in self.records]
        with patch('bridge.clients.desktop_app.sys.platform', 'win32'), patch('bridge.clients.desktop_app.os.path.normcase', side_effect=ntpath.normcase), \
                patch.object(subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
                patch('bridge.platforms.windows.desktop.subprocess.run', return_value=Mock(stdout=json.dumps(rows))) as run:
            result = inspect_clients(self.descriptors)
        run.assert_called_once(); command = run.call_args.args[0][-1]
        self.assertIn('SessionId -eq $s', command); self.assertIn('ProcessId,ExecutablePath,CommandLine', command)
        self.assertEqual(result['codex']['pids'], [11]); self.assertEqual(result['claude']['mainPids'], [12])
        self.assertEqual(result['deepseek']['runtimePids'], [15])

    def test_windows_singleton_json_and_absent_command_do_not_break_other_clients(self):
        row = {'ProcessId': 11, 'ExecutablePath': self.executables['codex'], 'CommandLine': None}
        with patch('bridge.clients.desktop_app.sys.platform', 'win32'), patch.object(subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
                patch('bridge.platforms.windows.desktop.subprocess.run', return_value=Mock(stdout=json.dumps(row))):
            result = inspect_clients(self.descriptors)
        self.assertTrue(result['codex']['unknown']); self.assertFalse(result['claude']['running'])

    def test_linux_scans_proc_once_and_only_reads_matching_same_user_commands(self):
        class Entry:
            def __init__(self, pid, executable, command, uid=os.getuid()):
                self.name = str(pid); self.executable = executable; self.command = command; self.uid = uid; self.reads = 0
            def stat(self): return SimpleNamespace(st_uid=self.uid)
            def __truediv__(self, part):
                if part == 'exe': return SimpleNamespace(resolve=lambda strict: Path(self.executable))
                def read():
                    self.reads += 1
                    if isinstance(self.command, Exception): raise self.command
                    return self.command.encode().replace(b' ', b'\0')
                return SimpleNamespace(read_bytes=read)
        entries = [Entry(pid, exe, cmd) for pid, exe, cmd in self.records]
        entries += [Entry(21, self.executables['codex'], 'other user', os.getuid()+1),
                    Entry(22, self.executables['claude'], PermissionError('unreadable'))]
        proc = Mock(); proc.iterdir.return_value = entries
        with patch('bridge.clients.desktop_app.sys.platform', 'linux'), \
                patch('bridge.platforms.linux.desktop.Path', side_effect=lambda value: proc if str(value) == '/proc' else Path(value)), \
                patch('bridge.platforms.macos.desktop.subprocess.run') as run:
            with patch('bridge.clients.lifecycle._commands', side_effect=lambda app, pids: {pid: cmd for pid, _, cmd in self.records if pid in pids}):
                before = {row['id']: inspect_client(row) for row in [self.descriptors[0], *self.descriptors]}
            self.assertEqual(proc.iterdir.call_count, 4); proc.iterdir.reset_mock()
            result = inspect_clients(self.descriptors)
        proc.iterdir.assert_called_once(); run.assert_not_called()
        self.assertEqual(result, before)
        self.assertEqual(result['codex']['pids'], [11]); self.assertTrue(result['claude']['unknown'])
        self.assertEqual(result['deepseek']['runtimePids'], [15]); self.assertFalse(result['deepseek']['unknown'])
        self.assertEqual(entries[5].reads, 0); self.assertEqual(entries[6].reads, 0)

    def test_process_collection_failure_is_not_reported_as_clients_stopped(self):
        with patch('bridge.clients.desktop_app.sys.platform', 'darwin'), \
                patch('bridge.platforms.macos.desktop.subprocess.run', side_effect=subprocess.CalledProcessError(2, 'ps')):
            with self.assertRaises(subprocess.CalledProcessError): inspect_clients(self.descriptors)


if __name__ == '__main__':
    unittest.main()
