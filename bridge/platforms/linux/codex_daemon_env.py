"""Read selected initial variables from a verified same-user Linux daemon."""

import os
from pathlib import Path


def _identity(pid):
    root = Path('/proc') / str(pid)
    stat = (root / 'stat').read_text(encoding='ascii')
    fields = stat.rpartition(')')[2].split()
    if not stat.startswith(f'{pid} (') or len(fields) < 20:
        raise OSError('Codex daemon process identity unavailable')
    uids = next(line.split()[1:] for line in (root / 'status').read_text().splitlines()
                if line.startswith('Uid:'))
    image = (root / 'exe').resolve(strict=True)
    return (int(fields[19]), tuple(int(uid) for uid in uids), str(image))


def read_process_environment(pid, record, executable, names, record_path=None):
    before = _identity(pid)
    if (before[1][0] != os.getuid() or before[1][1] != os.geteuid() or
            before[2] != str(Path(executable).resolve())):
        raise OSError('Codex daemon process identity mismatch')
    boot = (Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    generation = record.get('processIdentity') or {}
    if generation.get('bootId') != boot or generation.get('startTicks') != before[0]:
        raise OSError('Codex daemon PID record is stale')
    wanted = {name.encode(): name for name in names}
    selected = {}
    with (Path('/proc') / str(pid) / 'environ').open('rb') as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise OSError('Codex daemon launch environment is too large')
    for entry in raw.split(b'\0'):
        key, separator, value = entry.partition(b'=')
        if separator and key in wanted:
            selected[wanted[key]] = os.fsdecode(value)
    if _identity(pid) != before:
        raise OSError('Codex daemon process changed')
    return selected, (pid, before[0], before[2])
