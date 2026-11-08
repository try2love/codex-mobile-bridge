"""Raw Windows named-pipe byte streams; IPC framing belongs to the client."""
import threading
import time


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

    def _io(self, value, write=False, timeout=None):
        api = self.api
        with self.condition:
            if self.closed:
                raise OSError('Desktop pipe is closed')
            operation, error = (api.WriteFile if write else api.ReadFile)(
                self.handle, value, overlapped=True)
            self.operations.add(operation)
        try:
            timed_out = False
            if error == api.ERROR_IO_PENDING:
                milliseconds = (5000 if write else api.INFINITE) if timeout is None else max(0, int(timeout * 1000))
                result = api.WaitForMultipleObjects([operation.event], False, milliseconds)
                if result == api.WAIT_TIMEOUT:
                    timed_out = True
                    operation.cancel()
            count, error = operation.GetOverlappedResult(True)
            if timed_out and error == api.ERROR_OPERATION_ABORTED:
                raise TimeoutError('Desktop pipe operation timed out')
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

    def recv(self, length, *, timeout=None):
        return self._io(length, timeout=timeout)

    def send(self, data, *, timeout=None):
        return self._io(data, write=True, timeout=timeout)

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
