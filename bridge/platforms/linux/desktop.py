"""Linux /proc inventory; only the current user's processes are observable."""
import os
from pathlib import Path

from bridge.platforms.posix.desktop import quit_process
from bridge.platforms.posix.desktop import start
from bridge.platforms.posix.desktop import terminate


def _process_rows():
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            executable = (path/'exe').resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        yield path, executable


def inventory(result, match):
    for path, executable in _process_rows():
        state = match(executable)
        if state is not None:
            pid = int(path.name); state['pids'].append(pid)
            try:
                state['commands'][pid] = (path/'cmdline').read_bytes().decode().replace('\0', ' ').strip()
            except (OSError, UnicodeError):
                pass  # Disappearing/unreadable commands remain unknown.


def processes(matches):
    return [int(path.name) for path, executable in _process_rows() if matches(executable)]


def commands(pids):
    result = {}
    for pid in pids:
        try:
            result[pid] = (Path('/proc')/str(pid)/'cmdline').read_bytes().decode().replace('\0', ' ').strip()
        except FileNotFoundError:
            pass
    return result
