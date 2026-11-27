"""A byte-mode named-pipe desktop fixture; no third-party Windows dependencies."""
import socket
import threading

from bridge.transport import WindowsPipe


class PipeListener:
    def __init__(self, path):
        import _winapi
        self.api = _winapi
        self.path = path
        self.lock = threading.Lock()
        self.closed = False
        self.handle = None
        self.operation = None
        self._listen()

    def _listen(self):
        api = self.api
        self.handle = api.CreateNamedPipe(
            self.path, api.PIPE_ACCESS_DUPLEX | api.FILE_FLAG_OVERLAPPED,
            0, 2, 65536, 65536, 0, api.NULL)
        self.operation = api.ConnectNamedPipe(self.handle, overlapped=True)

    def accept(self):
        with self.lock:
            if self.closed:
                raise OSError('Listener closed')
            api = self.api
            if api.WaitForMultipleObjects([self.operation.event], False, 200) == api.WAIT_TIMEOUT:
                raise socket.timeout()
            _, error = self.operation.GetOverlappedResult(True)
            if error:
                raise OSError('Pipe accept failed: ' + str(error))
            stream = WindowsPipe(self.handle)
            self.handle = self.operation = None
            self._listen()
            return stream, None

    def close(self):
        with self.lock:
            self.closed = True
            if self.operation:
                self.operation.cancel()
                self.operation.GetOverlappedResult(True)
            if self.handle is not None:
                self.api.CloseHandle(self.handle)
                self.handle = None
