"""Byte-stream transports for desktop IPC, without changing its wire format."""
import os
import socket
import threading
import time
from pathlib import Path


def ipc_endpoint(codex_home):
    if os.name == 'nt':
        return r'\\.\pipe\codex-ipc'
    return str(Path(codex_home) / 'ipc/ipc.sock')


def connect_stream(path, timeout=5):
    if os.name == 'nt':
        return WindowsPipe.connect(str(path), timeout)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.settimeout(None)
        return sock
    except OSError:
        sock.close()
        raise


class WindowsPipe:
    """Cancellable overlapped I/O using CPython's standard-library Win32 API.

    multiprocessing.Connection adds its own framing, so use the raw byte pipe.
    Keep handles alive until all cancelled reads/writes have completed.
    """

    def __init__(self, handle):
        import _winapi
        self.api = _winapi
        self.handle = handle
        self.condition = threading.Condition()
        self.operations = set()
        self.closed = False

    @classmethod
    def connect(cls, path, timeout=5):
        import _winapi
        if not path.lower().startswith('\\\\.\\pipe\\'):
            raise OSError('Windows desktop IPC requires a local named pipe')
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle = _winapi.CreateFile(
                    path, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
                    _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, 0)
                return cls(handle)
            except OSError as exc:
                if exc.winerror != 231 or time.monotonic() >= deadline:
                    raise
                time.sleep(min(.05, max(0, deadline - time.monotonic())))

    def _io(self, value, write=False):
        api = self.api
        with self.condition:
            if self.closed:
                raise OSError('Desktop pipe is closed')
            operation, error = (api.WriteFile if write else api.ReadFile)(
                self.handle, value, overlapped=True)
            self.operations.add(operation)
        try:
            if error == api.ERROR_IO_PENDING:
                result = api.WaitForMultipleObjects([operation.event], False,
                                                    5000 if write else api.INFINITE)
                if result == api.WAIT_TIMEOUT:
                    raise TimeoutError('Desktop pipe write timed out')
            count, error = operation.GetOverlappedResult(True)
            if error:
                import ctypes
                raise ctypes.WinError(error)
            return count if write else operation.getbuffer()
        finally:
            try:
                operation.cancel()
                operation.GetOverlappedResult(True)
            finally:
                with self.condition:
                    self.operations.discard(operation)
                    self.condition.notify_all()

    def recv(self, length):
        return self._io(length)

    def sendall(self, data):
        offset = 0
        while offset < len(data):
            count = self._io(data[offset:], write=True)
            if not count:
                raise OSError('Desktop pipe disconnected during write')
            offset += count

    def shutdown(self, how=None):
        self.close()

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            for operation in self.operations:
                operation.cancel()
            self.condition.wait_for(lambda: not self.operations)
            self.api.CloseHandle(self.handle)
