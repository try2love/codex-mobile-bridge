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


class TerminalStartupTests(unittest.TestCase):
    def kernel(self, failure=None):
        calls, next_pipe = [], [20]
        def job(*args):
            self.assertEqual(args, (None, None))
            calls.append(('job',)); return 0 if failure == 'job' else 10
        def limits(handle, kind, value, size):
            self.assertEqual((handle, kind, value._obj.basic.flags), (10, 9, 0x2000))
            calls.append(('limits',)); return failure != 'limits'
        def pipe(read, write, *args):
            read._obj.value = next_pipe[0]; write._obj.value = next_pipe[0]+1; next_pipe[0] += 2
            return True
        def console(size, read, write, flags, handle):
            handle._obj.value = 30; return 0
        def attributes(pointer, count, flags, size):
            size._obj.value = 256; return True
        def process(*args):
            calls.append(('process', args[5]))
            self.assertFalse(args[4])
            if failure == 'process': return False
            args[-1]._obj.hProcess = 40; args[-1]._obj.hThread = 41
            return True
        def assign(job, process):
            calls.append(('assign', job, process)); return failure != 'assign'
        def resume(thread):
            calls.append(('resume', thread)); return 0xffffffff if failure == 'resume' else 1
        kernel = SimpleNamespace(CreateJobObjectW=job, SetInformationJobObject=limits,
            CreatePipe=pipe, CreatePseudoConsole=console, InitializeProcThreadAttributeList=attributes,
            UpdateProcThreadAttribute=lambda *args: True, DeleteProcThreadAttributeList=lambda *args: None,
            CreateProcessW=process, AssignProcessToJobObject=assign, ResumeThread=resume,
            CloseHandle=lambda handle: calls.append(('close', getattr(handle, 'value', handle))),
            ClosePseudoConsole=lambda handle: calls.append(('console-close', handle.value)),
            TerminateProcess=lambda handle, code: calls.append(('terminate', handle)),
            WaitForSingleObject=lambda handle, timeout: calls.append(('wait', handle)))
        return kernel, calls

    def create(self, kernel):
        from bridge.platforms.windows import terminal
        def checked(value):
            if not value: raise OSError('fixture Windows API failure')
        with patch.object(terminal, 'api', return_value=kernel), patch.object(terminal, 'checked', side_effect=checked), \
             patch.object(terminal, 'default_shell', return_value='fixture-shell'), patch.object(terminal.threading, 'Thread'):
            return terminal.WindowsTerminalSession(Path(__file__).parent)

    def test_assigns_suspended_bootstrap_before_resume_and_closes_only_its_job(self):
        kernel, calls = self.kernel()
        session = self.create(kernel)
        self.assertLess(calls.index(('process', 0x80004)), calls.index(('assign', 10, 40)))
        self.assertLess(calls.index(('assign', 10, 40)), calls.index(('resume', 41)))
        session._close_console(); session._close_console()
        self.assertEqual(calls.count(('close', 10)), 1)
        self.assertLess(calls.index(('close', 10)), calls.index(('console-close', 30)))
        self.assertIsNone(session.job)
        self.assertFalse(any(call[0] == 'terminate' for call in calls))

    def test_startup_failures_release_owned_handles_without_running_an_unassigned_child(self):
        for failure in ('job', 'limits', 'process', 'assign', 'resume'):
            with self.subTest(failure=failure):
                kernel, calls = self.kernel(failure)
                with self.assertRaises(OSError): self.create(kernel)
                self.assertEqual(calls.count(('close', 10)), 0 if failure == 'job' else 1)
                if failure in ('assign', 'resume'):
                    self.assertIn(('terminate', 40), calls)
                    self.assertIn(('wait', 40), calls)
                    self.assertEqual(calls.count(('close', 40)), 1)
                    self.assertEqual(calls.count(('close', 41)), 1)
                else:
                    self.assertNotIn(('terminate', 40), calls)
                if failure != 'resume': self.assertNotIn(('resume', 41), calls)


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
    def test_close_immediately_after_open_reaps_bootstrap(self):
        from bridge.platforms.windows.terminal import WindowsTerminalSession
        directory = Path(__file__).resolve().parents[1] / '.tmp'; directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            runtime = os.environ.get('CMB_TEST_RUNTIME')
            with patch.object(sys, 'executable', runtime or sys.executable), patch.object(sys, 'frozen', bool(runtime), create=True):
                session = WindowsTerminalSession(Path(folder), 80, 24)
            try:
                session.stop()
                self.assertTrue(session.done.wait(5), 'Immediate terminal close left its owned bootstrap running')
                self.assertIsNone(session.process_handle)
                self.assertIsNone(session.job)
            finally:
                session.stop(); session.done.wait(5)

    def test_closing_one_terminal_preserves_another_terminal(self):
        from bridge.platforms.windows.terminal import WindowsTerminalSession
        directory = Path(__file__).resolve().parents[1] / '.tmp'; directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            root = Path(folder); sessions = []
            try:
                runtime = os.environ.get('CMB_TEST_RUNTIME')
                with patch.object(sys, 'executable', runtime or sys.executable), patch.object(sys, 'frozen', bool(runtime), create=True):
                    for _ in range(2): sessions.append(WindowsTerminalSession(root, 80, 24))
                first, second = sessions
                first.stop(); self.assertTrue(first.done.wait(5))
                second.write(str(uuid.uuid4()), 'echo independent> survived.txt\r')
                deadline = time.monotonic()+10
                while not (root/'survived.txt').exists() and time.monotonic()<deadline: time.sleep(.05)
                self.assertEqual((root/'survived.txt').read_text().strip(), 'independent')
                self.assertFalse(second.done.is_set())
            finally:
                for session in sessions: session.stop()
                for session in sessions: self.assertTrue(session.done.wait(5))

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
