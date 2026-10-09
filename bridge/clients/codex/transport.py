"""Select a byte-stream transport without changing desktop IPC framing."""
import os
from pathlib import Path

from bridge.platforms.posix.transport import connect_stream as connect_unix_stream
from bridge.platforms.windows.transport import WindowsPipe


def ipc_endpoint(codex_home):
    if os.name == 'nt':
        return r'\\.\pipe\codex-ipc'
    return str(Path(codex_home) / 'ipc/ipc.sock')


def connect_stream(path, timeout=5):
    if os.name == 'nt':
        return WindowsPipe.connect(str(path), timeout)
    return connect_unix_stream(path, timeout)
