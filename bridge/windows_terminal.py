"""Windows 10 1809+ ConPTY transport; no third-party terminal dependency."""
import codecs
import collections
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
import queue
import subprocess
import threading
import time
import uuid

from .pty_terminal import TerminalSession, default_shell, dimensions


class COORD(C.Structure):
    _fields_ = [('X', C.c_short), ('Y', C.c_short)]


class STARTUPINFO(C.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR),
                ('lpTitle', W.LPWSTR), ('dwX', W.DWORD), ('dwY', W.DWORD),
                ('dwXSize', W.DWORD), ('dwYSize', W.DWORD), ('dwXCountChars', W.DWORD),
                ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD), ('dwFlags', W.DWORD),
                ('wShowWindow', W.WORD), ('cbReserved2', W.WORD), ('lpReserved2', C.c_void_p),
                ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class STARTUPINFOEX(C.Structure):
    _fields_ = [('StartupInfo', STARTUPINFO), ('lpAttributeList', C.c_void_p)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


def api():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreatePipe': (W.BOOL, [C.POINTER(W.HANDLE), C.POINTER(W.HANDLE), C.c_void_p, W.DWORD]),
        'CreatePseudoConsole': (C.c_long, [COORD, W.HANDLE, W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]),
        'ResizePseudoConsole': (C.c_long, [W.HANDLE, COORD]), 'ClosePseudoConsole': (None, [W.HANDLE]),
        'InitializeProcThreadAttributeList': (W.BOOL, [C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)]),
        'UpdateProcThreadAttribute': (W.BOOL, [C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p]),
        'DeleteProcThreadAttributeList': (None, [C.c_void_p]),
        'CreateProcessW': (W.BOOL, [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR, C.POINTER(STARTUPINFOEX), C.POINTER(PROCESS_INFORMATION)]),
        'ReadFile': (W.BOOL, [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]),
        'WriteFile': (W.BOOL, [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]),
        'WaitForSingleObject': (W.DWORD, [W.HANDLE, W.DWORD]),
        'GetExitCodeProcess': (W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
        'TerminateProcess': (W.BOOL, [W.HANDLE, W.UINT]), 'CloseHandle': (W.BOOL, [W.HANDLE]),
    }
    for name, (restype, argtypes) in signatures.items():
        try: function = getattr(kernel, name)
        except AttributeError: raise ValueError('连续终端需要 Windows 10 1809 或更新版本') from None
        function.restype, function.argtypes = restype, argtypes
    return kernel


def checked(result):
    if not result:
        raise C.WinError(C.get_last_error())


class WindowsTerminalSession(TerminalSession):
    def __init__(self, root, cols=80, rows=24):
        dimensions(cols, rows)
        self.kernel = k = api()
        self.lock = threading.RLock(); self.input_lock = threading.Lock(); self.close_lock = threading.Lock()
        self.text = ''; self.end = 0; self.reason = ''; self.code = None
        self.done = threading.Event(); self.stopping = threading.Event(); self.touched = time.monotonic()
        self.inputs = collections.OrderedDict(); self.writes = queue.Queue()
        self.shell = default_shell(); self.root = str(root)
        self.console = W.HANDLE(); self.process_handle = None
        input_read, self.input_write, self.output_read, output_write = (W.HANDLE() for _ in range(4))
        handles = [input_read, self.input_write, self.output_read, output_write]
        attributes = None; initialized = False
        try:
            checked(k.CreatePipe(C.byref(input_read), C.byref(self.input_write), None, 0))
            checked(k.CreatePipe(C.byref(self.output_read), C.byref(output_write), None, 0))
            if k.CreatePseudoConsole(COORD(cols, rows), input_read, output_write, 0, C.byref(self.console)) < 0:
                raise OSError('CreatePseudoConsole failed')
            size = C.c_size_t()
            k.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
            attributes = C.create_string_buffer(size.value)
            checked(k.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size))); initialized = True
            checked(k.UpdateProcThreadAttribute(attributes, 0, 0x00020016, self.console, C.sizeof(self.console), None, None))
            startup = STARTUPINFOEX(); startup.StartupInfo.cb = C.sizeof(startup)
            startup.lpAttributeList = C.cast(attributes, C.c_void_p)
            process = PROCESS_INFORMATION()
            # CreateProcess receives an argv-quoted executable, never a shell command.
            command = C.create_unicode_buffer(subprocess.list2cmdline([self.shell]))
            checked(k.CreateProcessW(self.shell, command, None, None, False, 0x00080000, None, self.root, C.byref(startup), C.byref(process)))
            self.process_handle = process.hProcess; k.CloseHandle(process.hThread)
        except Exception:
            if self.console.value: k.ClosePseudoConsole(self.console)
            for handle in handles:
                if handle.value: k.CloseHandle(handle)
            raise
        finally:
            if initialized: k.DeleteProcThreadAttributeList(attributes)
        k.CloseHandle(input_read); k.CloseHandle(output_write)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()
        threading.Thread(target=self._wait, daemon=True).start()
        threading.Thread(target=self._idle, daemon=True).start()

    def _read(self):
        decoder = codecs.getincrementaldecoder('utf-8')('replace'); buffer = C.create_string_buffer(32768)
        try:
            while True:
                count = W.DWORD()
                if not self.kernel.ReadFile(self.output_read, buffer, len(buffer), C.byref(count), None) or not count.value: break
                self.append(decoder.decode(buffer.raw[:count.value]))
            self.append(decoder.decode(b'', final=True))
        finally:
            self.kernel.CloseHandle(self.output_read)
            self.done.set()

    def _wait(self):
        self.kernel.WaitForSingleObject(self.process_handle, 0xffffffff)
        code = W.DWORD(); self.kernel.GetExitCodeProcess(self.process_handle, C.byref(code)); self.code = code.value
        self._close_console()
        with self.close_lock:
            self.kernel.CloseHandle(self.process_handle); self.process_handle = None

    def _close_console(self):
        # ClosePseudoConsole may wait for output to drain; the reader stays alive.
        with self.close_lock:
            if self.console.value:
                self.stopping.set(); self.writes.put(None)
                self.kernel.ClosePseudoConsole(self.console); self.console = W.HANDLE()

    def _writer(self):
        try:
            while True:
                job = self.writes.get()
                if job is None: break
                raw, result = job
                try:
                    if self.stopping.is_set(): raise OSError('终端已关闭，请重新打开')
                    offset = 0
                    while offset < len(raw):
                        count = W.DWORD(); data = C.create_string_buffer(raw[offset:])
                        checked(self.kernel.WriteFile(self.input_write, data, len(raw)-offset, C.byref(count), None))
                        if not count.value: raise OSError('Terminal input closed')
                        offset += count.value
                except OSError as error: result['error'] = error
                finally: result['done'].set()
        finally:
            self.kernel.CloseHandle(self.input_write)

    def write(self, identifier, data):
        uuid.UUID(str(identifier))
        if not isinstance(data, str) or not data or len(data.encode('utf-8')) > 16384: raise ValueError('无效终端输入')
        raw = data.encode('utf-8'); digest = hashlib.sha256(raw).digest()
        with self.input_lock:
            if self.done.is_set() or self.stopping.is_set(): raise ValueError('终端已关闭，请重新打开')
            result = self.inputs.get(identifier)
            if result is None:
                result = {'digest': digest, 'done': threading.Event()}; self.inputs[identifier] = result
                self.writes.put((raw, result))
            if result['digest'] != digest: raise ValueError('输入编号已用于其他内容')
            self.touched = time.monotonic()
            if not result['done'].wait(3): raise TimeoutError('终端输入拥堵，请重试')
            if result.get('error'): raise result['error']
            while len(self.inputs) > 256: self.inputs.popitem(last=False)
        return {'accepted': True}

    def resize(self, cols, rows):
        dimensions(cols, rows)
        with self.close_lock:
            if not self.console.value: return {'resized': False}
            if self.kernel.ResizePseudoConsole(self.console, COORD(cols, rows)) < 0: raise OSError('ResizePseudoConsole failed')
        return {'resized': True}

    def stop(self, reason='终端已关闭'):
        if self.done.is_set() or self.stopping.is_set(): return
        self.reason = reason
        threading.Thread(target=self._close_console, daemon=True).start()
