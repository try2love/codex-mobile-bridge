"""Read-only Linux Desktop executor generations; never infer idle from a PID."""
import os
from pathlib import Path


def identity(pid):
    root = Path('/proc')/str(pid)
    try:
        before = (root/'stat').read_text().rpartition(')')[2].split()
        if before[0] in ('Z', 'X', 'x'):
            raise ProcessLookupError('Codex process exited')
        uids = next(line.split()[1:] for line in (root/'status').read_text().splitlines()
                    if line.startswith('Uid:'))
        image = str((root/'exe').resolve(strict=True))
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        after = (root/'stat').read_text().rpartition(')')[2].split()
    except FileNotFoundError:
        try:
            exited = (root/'stat').read_text().rpartition(')')[2].split()[0] in ('Z', 'X', 'x')
        except FileNotFoundError:
            exited = not root.exists()
        if exited:
            raise ProcessLookupError('Codex process exited') from None
        raise
    if after[0] in ('Z', 'X', 'x'):
        raise ProcessLookupError('Codex process exited')
    if (before[19] != after[19] or
            int(uids[0]) != os.getuid() or int(uids[1]) != os.geteuid()):
        raise ValueError('Codex process generation unavailable')
    ticks = int(after[19])
    return {'pid': pid, 'parent': int(after[1]), 'uid': int(uids[1]), 'ruid': int(uids[0]),
            'image': image, 'started': [boot, ticks], 'birth': [ticks], 'bootId': boot, 'zombie': False}
