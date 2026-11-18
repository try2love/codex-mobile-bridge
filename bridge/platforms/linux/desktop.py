"""Linux /proc inventory; only the current user's processes are observable."""
import os
from pathlib import Path

from bridge.platforms.posix.desktop import quit_process
from bridge.platforms.posix.desktop import start
from bridge.platforms.posix.desktop import terminate


def _process_rows(*, preserve_deleted_marker=False):
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            exe_link = path/'exe'
            raw = os.readlink(exe_link)
            deleted = raw.endswith(' (deleted)')
            if deleted and preserve_deleted_marker:
                # Keep the kernel marker only for Antigravity's AppImage verifier.
                executable = Path(raw)
            else:
                # Keep the shared inventory's historical absolute image path.
                executable = Path(raw.removesuffix(' (deleted)'))
            if not executable.is_absolute():
                continue
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
