"""ConPTY integration tests run on Windows in the existing OS test matrix."""
import os
from pathlib import Path
import tempfile
import sys
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch


class TerminalCompletionTests(unittest.TestCase):
    def session(self):
        from bridge.platforms.windows.terminal import WindowsTerminalSession
        session = WindowsTerminalSession.__new__(WindowsTerminalSession)
        session.done = threading.Event(); session.output_done = threading.Event()
        session.close_lock = threading.Lock(); session.process_handle = 'process'; session.output_read = 'output'
        exited, closed = threading.Event(), threading.Event()
        def wait(handle, timeout):
            self.assertEqual(handle, 'process')
            self.assertTrue(exited.wait(1))
            return 0
        def close(handle):
            if handle == 'process': closed.set()
            return True
        session.kernel = SimpleNamespace(WaitForSingleObject=wait,
            GetExitCodeProcess=lambda handle, code: setattr(code._obj, 'value', 0),
            CloseHandle=close, ReadFile=lambda *args: False)
        session._close_console = lambda: None
        session.append = lambda text: None
        return session, exited, closed

    def test_output_eof_does_not_mark_a_running_process_complete(self):
        session, exited, _ = self.session()
        session._read()
        self.assertTrue(session.output_done.is_set())
        self.assertFalse(session.done.is_set())
        exited.set(); session._wait()
        self.assertTrue(session.done.is_set())
        self.assertIsNone(session.process_handle)

    def test_process_exit_waits_for_output_drain(self):
        session, exited, closed = self.session()
        exited.set()
        worker = threading.Thread(target=session._wait)
        worker.start()
        try:
            self.assertTrue(closed.wait(1))
            self.assertFalse(session.done.is_set())
            session._read()
        finally:
            session.output_done.set(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(session.done.is_set())

    def test_read_failure_still_allows_process_cleanup_to_complete(self):
        session, exited, _ = self.session()
        with patch.object(session.kernel, 'ReadFile', side_effect=OSError('closed pipe')):
            with self.assertRaises(OSError): session._read()
        self.assertTrue(session.output_done.is_set())
        self.assertFalse(session.done.is_set())
        exited.set(); session._wait()
        self.assertTrue(session.done.is_set())


@unittest.skipUnless(os.name == 'nt', 'ConPTY requires Windows')
class ConPtyTests(unittest.TestCase):
    def test_persistent_shell_unicode_resize_dedupe_and_close(self):
        from bridge.platforms.windows.terminal import WindowsTerminalSession
        directory = Path(__file__).resolve().parents[1] / '.tmp'; directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            root=Path(folder); (root/'nested folder').mkdir()
            runtime = os.environ.get('CMB_TEST_RUNTIME')
            with patch.object(sys, 'executable', runtime or sys.executable), patch.object(sys, 'frozen', bool(runtime), create=True):
                session=WindowsTerminalSession(root,80,24)
            try:
                deadline=time.monotonic()+10
                while '>' not in session.read()['output'] and time.monotonic()<deadline:time.sleep(.05)
                self.assertIn('>',session.read()['output'])
                command='cd "nested folder"\rset BRIDGE_TEST=retained\r'
                session.write(str(uuid.uuid4()),command)
                key=str(uuid.uuid4());session.write(key,'echo %BRIDGE_TEST%> result.txt\r');session.write(key,'echo %BRIDGE_TEST%> result.txt\r')
                deadline=time.monotonic()+10
                while not (root/'nested folder/result.txt').exists() and time.monotonic()<deadline:time.sleep(.05)
                self.assertTrue((root/'nested folder/result.txt').exists(),repr(session.read()))
                self.assertEqual((root/'nested folder/result.txt').read_text().strip(),'retained')
                # Use CMD's explicit cross-drive form; a later input must keep
                # that cwd instead of launching a fresh command from root.
                session.write(str(uuid.uuid4()), 'cd /d "' + str(root) + '"\r')
                session.write(str(uuid.uuid4()), 'cd > cwd-after.txt\r')
                deadline = time.monotonic() + 10
                while not (root/'cwd-after.txt').exists() and time.monotonic() < deadline: time.sleep(.05)
                self.assertEqual((root/'cwd-after.txt').read_text().strip().casefold(), str(root).casefold())

                self.assertTrue(session.resize(100,30)['resized'])
                with self.assertRaises(ValueError):session.write(key,'echo unexpected\r\n')
                self.assertFalse(session.done.is_set())
            finally:
                session.stop();self.assertTrue(session.done.wait(10))
                self.assertIsNone(session.process_handle)
