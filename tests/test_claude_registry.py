import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.claude import session_registry
from bridge.clients.claude.session_registry import assert_profile_idle, assert_session_closed
from bridge.clients.errors import BridgeUnavailable
from bridge.platforms.windows import claude_process


class ClaudeRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name)
        self.registry = self.config/'sessions'
        self.registry.mkdir()
        self.session_id = '11111111-1111-4111-8111-111111111111'
        self.start = 'Wed Oct 14 02:32:48 2026'

    def entry(self, session_id=None, **changes):
        value = {'sessionId': session_id or self.session_id, 'pid': 1234,
                 'pidDomain': 'darwin', 'procStart': self.start, 'cwd': '/fixture/project'}
        value.update(changes)
        (self.registry/'1234.json').write_text(json.dumps(value))

    def test_only_matching_live_session_blocks_resume(self):
        self.entry(session_id='22222222-2222-4222-8222-222222222222')
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill') as kill:
            assert_session_closed(self.config, self.session_id)
        kill.assert_not_called()
        self.entry()
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill'), \
             patch('bridge.clients.claude.session_registry.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=' '+self.start+'\n', stderr='')):
            with self.assertRaisesRegex(BridgeUnavailable, '仍在其他终端运行'):
                assert_session_closed(self.config, self.session_id)

    def test_closed_or_reused_pid_allows_resume(self):
        self.entry()
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill', side_effect=ProcessLookupError):
            assert_session_closed(self.config, self.session_id)
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill'), \
             patch('bridge.clients.claude.session_registry.subprocess.run',
                   return_value=SimpleNamespace(returncode=0,
                                                stdout='Wed Oct 14 01:00:00 2026\n', stderr='')):
            assert_session_closed(self.config, self.session_id)

    def test_profile_switch_blocks_any_verified_external_writer(self):
        self.entry(session_id='22222222-2222-4222-8222-222222222222')
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill'), \
             patch('bridge.clients.claude.session_registry.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=' '+self.start+'\n', stderr='')):
            with self.assertRaisesRegex(BridgeUnavailable, '仍在其他终端运行'):
                assert_profile_idle(self.config)
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill', side_effect=ProcessLookupError):
            assert_profile_idle(self.config)

    def test_linux_native_domain_and_start_time_guard(self):
        from bridge.platforms.linux import claude_process as linux_process
        domain = 'linux:machine-id:pid:[123]'
        self.entry(pidDomain=domain, procStart='123456')
        with patch.object(session_registry.sys, 'platform', 'linux'), \
             patch.object(session_registry.Path, 'read_text', return_value='machine-id\n'), \
             patch.object(session_registry.os, 'readlink', return_value='pid:[123]'):
            self.assertEqual(session_registry._pid_domain(), domain)
        with patch.object(session_registry.sys, 'platform', 'linux'), \
             patch.object(session_registry, '_pid_domain', return_value=domain):
            with patch.object(linux_process, 'process_start_ticks', return_value='123456'):
                with self.assertRaisesRegex(BridgeUnavailable, '仍在其他终端运行'):
                    assert_session_closed(self.config, self.session_id)
            for token in (None, '123457'):
                with patch.object(linux_process, 'process_start_ticks', return_value=token):
                    assert_session_closed(self.config, self.session_id)
            with patch.object(linux_process, 'process_start_ticks', side_effect=PermissionError):
                with self.assertRaises(BridgeUnavailable):
                    assert_session_closed(self.config, self.session_id)
            self.entry(pidDomain=domain, procStart='Wed Oct 14 01:00:00 2026')
            with self.assertRaises(BridgeUnavailable):
                assert_session_closed(self.config, self.session_id)
        with patch.object(session_registry, '_pid_domain', return_value='linux:other:pid:[123]'):
            with self.assertRaises(BridgeUnavailable):
                assert_session_closed(self.config, self.session_id)

    def test_linux_proc_stat_parser_handles_comm_and_unknown_records(self):
        from bridge.platforms.linux import claude_process as linux_process
        from bridge.clients.claude import remote_worker
        stat = '1234 (test ) process) ' + ' '.join(['S'] + ['0'] * 18 + ['123456'])
        for reader in (linux_process.process_start_ticks, remote_worker.linux_process_start):
            with patch.object(Path, 'read_text', return_value=stat):
                self.assertEqual(reader(1234), '123456')
            with patch.object(Path, 'read_text', side_effect=FileNotFoundError):
                self.assertIsNone(reader(1234))
            with patch.object(Path, 'read_text', side_effect=PermissionError):
                with self.assertRaises(OSError):
                    reader(1234)
            for malformed in ('1234 (short) S 0', stat.replace('123456', 'unknown')):
                with patch.object(Path, 'read_text', return_value=malformed):
                    with self.assertRaises((OSError, ValueError)):
                        reader(1234)

    def test_windows_native_start_ticks_guard_is_independent_of_cli_release(self):
        start = '638960923680000000'
        self.entry(procStart=start, version='2.1.168')
        entry = json.loads((self.registry/'1234.json').read_text())
        del entry['pidDomain']
        (self.registry/'1234.json').write_text(json.dumps(entry))
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(session_registry.os, 'kill', side_effect=AssertionError('Windows must not signal PID')) as kill, \
             patch.object(claude_process, 'process_start_ticks', return_value=start) as lookup:
            with self.assertRaisesRegex(BridgeUnavailable, '仍在其他终端运行'):
                assert_session_closed(self.config, self.session_id)
        kill.assert_not_called()
        lookup.assert_called_once_with(1234)
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(claude_process, 'process_start_ticks', return_value=None):
            assert_session_closed(self.config, self.session_id)
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(claude_process, 'process_start_ticks', return_value='638960923680000001'):
            assert_session_closed(self.config, self.session_id)
        for version in ('2.1.287', '2.1.999'):
            entry['version'] = version
            (self.registry/'1234.json').write_text(json.dumps(entry))
            with self.subTest(version=version), \
                 patch.object(session_registry.sys, 'platform', 'win32'), \
                 patch.object(claude_process, 'process_start_ticks', return_value=start) as lookup:
                with self.assertRaisesRegex(BridgeUnavailable, '仍在其他终端运行'):
                    assert_session_closed(self.config, self.session_id)
                lookup.assert_called_once_with(1234)
                lookup.return_value = None
                assert_session_closed(self.config, self.session_id)
                lookup.return_value = '638960923680000001'
                assert_session_closed(self.config, self.session_id)

    def test_windows_2168_native_record_without_start_only_confirms_absent(self):
        entry = {'pid': 1234, 'sessionId': self.session_id, 'cwd': '/fixture/project',
                 'startedAt': 1791984471710, 'version': '2.1.168',
                 'peerProtocol': 1, 'kind': 'interactive', 'entrypoint': 'sdk-cli'}
        (self.registry/'1234.json').write_text(json.dumps(entry))
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(session_registry.os, 'kill', side_effect=AssertionError('Windows must not signal PID')) as kill, \
             patch.object(claude_process, 'process_start_ticks', return_value=None) as lookup:
            assert_profile_idle(self.config)
        kill.assert_not_called()
        lookup.assert_called_once_with(1234)
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(claude_process, 'process_start_ticks', return_value='638960923680000000'):
            with self.assertRaisesRegex(BridgeUnavailable, '无法确认'):
                assert_profile_idle(self.config)
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(claude_process, 'process_start_ticks', side_effect=PermissionError):
            with self.assertRaisesRegex(BridgeUnavailable, '无法确认'):
                assert_profile_idle(self.config)
        entry['procStart'] = 638960923680000000
        (self.registry/'1234.json').write_text(json.dumps(entry))
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(claude_process, 'process_start_ticks') as lookup:
            with self.assertRaisesRegex(BridgeUnavailable, '无法确认'):
                assert_profile_idle(self.config)
        lookup.assert_not_called()

    def test_windows_unverified_newer_registry_fails_closed(self):
        entry = {'sessionId': self.session_id, 'pid': 1234, 'version': '2.1.287',
                 'procStartFt': '134057862268120000', 'pidDomain': 'windows:machine-guid'}
        (self.registry/'1234.json').write_text(json.dumps(entry))
        with patch.object(session_registry.sys, 'platform', 'win32'), \
             patch.object(claude_process, 'process_start_ticks') as lookup:
            with self.assertRaisesRegex(BridgeUnavailable, '无法确认'):
                assert_session_closed(self.config, self.session_id)
        lookup.assert_not_called()

    def test_windows_process_query_distinguishes_missing_from_unknown(self):
        ok = SimpleNamespace(returncode=0, stdout='638960923680000000\r\n')
        with patch.object(claude_process.subprocess, 'run', return_value=ok) as run:
            self.assertEqual(claude_process.process_start_ticks(1234), '638960923680000000')
            self.assertIn('ProcessId=1234', run.call_args.args[0][-1])
        for result in (SimpleNamespace(returncode=0, stdout='missing\r\n'),):
            with patch.object(claude_process.subprocess, 'run', return_value=result):
                self.assertIsNone(claude_process.process_start_ticks(1234))
        for result in (SimpleNamespace(returncode=1, stdout=''),
                       SimpleNamespace(returncode=0, stdout='')):
            with patch.object(claude_process.subprocess, 'run', return_value=result):
                with self.assertRaises(OSError):
                    claude_process.process_start_ticks(1234)

    def test_windows_owned_child_termination_checks_creation_on_same_handle(self):
        kernel = SimpleNamespace(OpenProcess=Mock(return_value=99),
                                 TerminateProcess=Mock(return_value=1),
                                 WaitForSingleObject=Mock(side_effect=[0x102, 0]),
                                 CloseHandle=Mock())

        def times(_handle, created, *_other):
            ctypes.cast(created, ctypes.POINTER(ctypes.c_uint64))[0] = 12345
            return 1

        kernel.GetProcessTimes = Mock(side_effect=times)
        with patch.object(claude_process, '_kernel', return_value=kernel):
            self.assertEqual(claude_process.process_start_filetime(1234), 12345)
            self.assertFalse(claude_process.terminate_matching_process(1234, 99999))
            kernel.TerminateProcess.assert_not_called()
            self.assertTrue(claude_process.terminate_matching_process(1234, 12345))
        self.assertEqual(kernel.OpenProcess.call_args.args[0], 0x101001)
        kernel.TerminateProcess.assert_called_once_with(99, 1)

    def test_windows_owned_child_already_exited_needs_no_termination(self):
        kernel = SimpleNamespace(OpenProcess=Mock(return_value=99),
                                 TerminateProcess=Mock(),
                                 WaitForSingleObject=Mock(return_value=0),
                                 CloseHandle=Mock())

        def times(_handle, created, *_other):
            ctypes.cast(created, ctypes.POINTER(ctypes.c_uint64))[0] = 12345
            return 1

        kernel.GetProcessTimes = Mock(side_effect=times)
        with patch.object(claude_process, '_kernel', return_value=kernel):
            self.assertFalse(claude_process.terminate_matching_process(1234, 12345))
        kernel.TerminateProcess.assert_not_called()

    def test_windows_owned_child_exit_during_termination_is_confirmed(self):
        kernel = SimpleNamespace(OpenProcess=Mock(return_value=99),
                                 TerminateProcess=Mock(return_value=0),
                                 WaitForSingleObject=Mock(side_effect=[0x102, 0]),
                                 CloseHandle=Mock())

        def times(_handle, created, *_other):
            ctypes.cast(created, ctypes.POINTER(ctypes.c_uint64))[0] = 12345
            return 1

        kernel.GetProcessTimes = Mock(side_effect=times)
        with patch.object(claude_process, '_kernel', return_value=kernel):
            self.assertFalse(claude_process.terminate_matching_process(1234, 12345))
        kernel.TerminateProcess.assert_called_once_with(99, 1)

    def test_windows_owned_child_binding_requires_live_shim_ancestry(self):
        identity = (5555, '638960923680000000', 'claude.exe')
        with patch.object(claude_process, '_cim_parent_start', side_effect=[identity, identity]) as query, \
             patch.object(claude_process, 'process_start_filetime', return_value=12345):
            self.assertEqual(claude_process.owned_child_start_filetime(1234, 5555), 12345)
        self.assertEqual(query.call_count, 2)
        with patch.object(claude_process, '_cim_parent_start', return_value=identity), \
             patch.object(claude_process, 'process_start_filetime') as start:
            with self.assertRaises(OSError):
                claude_process.owned_child_start_filetime(1234, 9999)
        start.assert_not_called()
        with patch.object(claude_process, '_cim_parent_start', side_effect=[identity,
             (5555, '638960923680000001', 'claude.exe')]), \
             patch.object(claude_process, 'process_start_filetime', return_value=12345):
            with self.assertRaises(OSError):
                claude_process.owned_child_start_filetime(1234, 5555)

    def test_fresh_profile_without_registry_is_idle_but_symlink_is_unknown(self):
        self.registry.rmdir()
        assert_profile_idle(self.config)
        (self.config/'sessions').symlink_to(self.config)
        with self.assertRaises(BridgeUnavailable):
            assert_profile_idle(self.config)

    def test_unknown_registry_or_process_is_not_assumed_closed(self):
        self.entry()
        with patch('bridge.clients.claude.session_registry.sys.platform', 'linux'):
            with self.assertRaises(BridgeUnavailable):
                assert_session_closed(self.config, self.session_id)
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill', side_effect=PermissionError):
            with self.assertRaises(BridgeUnavailable):
                assert_session_closed(self.config, self.session_id)
        (self.registry/'1234.json').write_text('{bad json')
        with self.assertRaises(BridgeUnavailable):
            assert_session_closed(self.config, self.session_id)
        (self.registry/'1234.json').unlink()
        assert_session_closed(self.config, self.session_id)
        self.registry.rmdir()
        with self.assertRaises(BridgeUnavailable):
            assert_session_closed(self.config, self.session_id)
