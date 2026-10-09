"""Session detection is read-only and scoped to the gateway process's session."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge import windows_session as session


class WindowsSessionTests(unittest.TestCase):
    def setUp(self):
        platform = patch.object(session, 'sys', SimpleNamespace(platform='win32', getwindowsversion=lambda: (10, 0)))
        platform.start(); self.addCleanup(platform.stop)

    def snapshot(self, flags, connection=0, session_id=3, desktop=True):
        with patch.object(session, '_query_session', return_value=(flags, connection, session_id)), \
             patch.object(session, '_input_desktop_available', return_value=desktop) as query:
            result = session.status()
        return result, query

    def test_locked_session_never_probes_or_switches_input_desktop(self):
        value, probe = self.snapshot(0)
        self.assertEqual(value['state'], 'locked'); self.assertFalse(value['interactive'])
        probe.assert_not_called()
        self.assertEqual(set(value), {'state', 'interactive', 'reason'})

    def test_unlocked_requires_active_interactive_own_session(self):
        value, _ = self.snapshot(1)
        self.assertEqual(value['state'], 'unlocked'); self.assertTrue(value['interactive'])
        for connection, session_id in [(4, 3), (0, 0)]:
            value, probe = self.snapshot(1, connection, session_id)
            self.assertFalse(value['interactive']); probe.assert_not_called()

    def test_secure_desktop_or_access_denial_does_not_claim_locked(self):
        value, _ = self.snapshot(1, desktop=False)
        self.assertEqual(value['state'], 'unlocked'); self.assertFalse(value['interactive'])
        self.assertNotIn('已锁定', value['reason'])

    def test_query_failure_or_unknown_flags_stay_unknown(self):
        for error in (OSError('denied'), ValueError('bad buffer')):
            with patch.object(session, '_query_session', side_effect=error):
                value = session.status()
            self.assertEqual(value['state'], 'unknown'); self.assertIsNone(value['interactive'])
        self.assertEqual(self.snapshot(-1)[0]['state'], 'unknown')

    def test_windows7_inverted_flag_is_supported(self):
        session.sys.getwindowsversion = lambda: (6, 1)
        self.assertEqual(self.snapshot(1)[0]['state'], 'locked')
        self.assertEqual(self.snapshot(0)[0]['state'], 'unlocked')

    def test_only_verified_interactive_session_allows_foreground_setup(self):
        for state, interactive, expected in [('locked', False, 'needs-unlock'),
                ('unknown', None, 'needs-desktop'), ('unlocked', False, 'needs-desktop')]:
            with patch.object(session, 'status', return_value={'state': state, 'interactive': interactive, 'reason': 'fixture'}):
                with self.assertRaises(session.DesktopUnavailable) as caught:
                    session.require_interactive()
                self.assertEqual(caught.exception.setup_state, expected)
        with patch.object(session, 'status', return_value={'state': 'unlocked', 'interactive': True}):
            session.require_interactive()

    def test_wts_reads_process_session_and_frees_valid_or_invalid_buffer(self):
        info = session._InfoEx(); info.level = 1
        info.data.session.session_id = 31; info.data.session.flags = 1
        kernel, wts = Mock(), Mock()
        def process_session(pid, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_uint32))[0] = 31
            return True
        def query(server, sid, kind, output, length):
            self.assertEqual(sid.value, 31); self.assertEqual(kind, 25)
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(info)
            ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = ctypes.sizeof(info)
            return True
        kernel.ProcessIdToSessionId.side_effect = process_session
        wts.WTSQuerySessionInformationW.side_effect = query
        with patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kwargs: kernel if name == 'kernel32' else wts, create=True):
            self.assertEqual(session._query_session(), (1, 0, 31))
            info.data.session.session_id = 32
            with self.assertRaises(ValueError): session._query_session()
            info.level = 2
            with self.assertRaises(ValueError): session._query_session()
        self.assertEqual(wts.WTSFreeMemory.call_count, 3)

    def test_other_platform_never_calls_windows_apis(self):
        session.sys.platform = 'darwin'
        with patch.object(session, '_query_session') as query:
            self.assertEqual(session.status()['state'], 'unsupported')
            session.require_interactive()
        query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
