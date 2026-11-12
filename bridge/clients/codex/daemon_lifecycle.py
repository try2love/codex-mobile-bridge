"""Local CLI maintenance transport, separate from ordinary session RPC."""

import sys
import time

from bridge.clients.websocket import WebSocket


def maintenance_lock(home):
    if sys.platform == 'win32':
        from bridge.platforms.windows.codex_daemon_env import bridge_maintenance_lock
        return bridge_maintenance_lock(home)
    from bridge.clients.codex.desktop_runtime import exclusive_lock
    return exclusive_lock(home, 'cli-maintenance')


class _ShutdownStream:
    def __init__(self, channel, deadline):
        self.channel, self.deadline = channel, deadline

    def _remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError('CLI shutdown socket response timed out')
        self.channel.settimeout(seconds)

    def read(self, size):
        self._remaining()
        return self.channel.recv(size)

    def write(self, data):
        self._remaining()
        self.channel.sendall(data)
        return len(data)

    def flush(self):
        pass


def request_windows_shutdown(drain, checkpoint):
    """Dispatch once; after the checkpoint, only the held process proves exit."""
    timeout = 2
    deadline = time.monotonic() + timeout
    with drain.connect(timeout) as channel:
        stream = _ShutdownStream(channel, deadline)
        connection = WebSocket(stream, stream, path='/daemon/shutdown', max_message_bytes=10,
                               error=OSError, unavailable=OSError, unsupported=OSError)
        drain.verify_peer(channel)
        drain.verify_generation()
        checkpoint()
        drain.remember_shutdown()
        stream.deadline = time.monotonic() + timeout
        try:
            connection.send(drain.pid)
            if connection.receive_text() != str(drain.pid):
                return False
            # The native listener dispatches when the acknowledged socket closes.
            return True
        except (OSError, EOFError, UnicodeError, ValueError):
            return False
