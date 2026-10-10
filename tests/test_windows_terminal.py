"""ConPTY integration tests run on Windows in the existing OS test matrix."""
import os
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import threading
import time
import traceback
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch


class OwnedTerminalProbe:
    """CI diagnostics and cleanup restricted to this fixture's retained process handle."""
    def __init__(self, session):
        self.session = session
        self.kernel = kernel = session.kernel
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.DuplicateHandle.restype = wintypes.BOOL
        kernel.GetProcessId.argtypes = [wintypes.HANDLE]; kernel.GetProcessId.restype = wintypes.DWORD
        self.handle = wintypes.HANDLE()
        current = wintypes.HANDLE(-1)
        if not kernel.DuplicateHandle(current, session.process_handle, current,
                                      ctypes.byref(self.handle), 0, False, 2):
            raise ctypes.WinError(ctypes.get_last_error())
        self.pid = kernel.GetProcessId(self.handle)

    def report(self):
        session = self.session
        code = wintypes.DWORD(); self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code))
        stacks = []
        for frame in sys._current_frames().values():
            cursor = frame
            while cursor is not None:
                if cursor.f_locals.get('self') is session:
                    stacks.append([f'{row.filename}:{row.lineno}:{row.name}' for row in traceback.extract_stack(frame)[-6:]])
                    break
                cursor = cursor.f_back
        value = {'pid': self.pid, 'root': session.root, 'done': session.done.is_set(),
            'outputDone': session.output_done.is_set(), 'stopping': session.stopping.is_set(),
            'console': session.console.value, 'waitResult': self.kernel.WaitForSingleObject(self.handle, 0),
            'exitCode': code.value, 'output': session.read()['output'][-512:], 'threads': stacks}
        # Filter before returning data: only our retained PID and its children.
        command = (f"Get-CimInstance Win32_Process -Filter 'ProcessId = {self.pid} OR ParentProcessId = {self.pid}' | "
                   'Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress')
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=8)
            value['processes'] = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else {'queryExit': result.returncode}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            value['processes'] = {'queryError': type(exc).__name__}
        print('Owned terminal diagnostic: ' + json.dumps(value, ensure_ascii=True), flush=True)

    def cleanup(self):
        try:
            if self.kernel.WaitForSingleObject(self.handle, 0) == 258:
                self.kernel.TerminateProcess(self.handle, 1)
                self.kernel.WaitForSingleObject(self.handle, 5000)
            self.session.done.wait(5)
        finally:
            self.kernel.CloseHandle(self.handle)


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
            probe = OwnedTerminalProbe(session)
            try:
                session.stop()
                completed = session.done.wait(5)
                if not completed: probe.report()
                self.assertTrue(completed, 'Immediate terminal close left its owned bootstrap running')
            finally:
                probe.cleanup()

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
