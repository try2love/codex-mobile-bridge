"""Verify a loopback development endpoint belongs to a known desktop executable."""
import os
import subprocess
from pathlib import Path

from bridge.platforms.macos.force_exit import Native


def listener_identity(port, executable):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('无效的本机调试端口')
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-iTCP:%d' % port,
                             '-sTCP:LISTEN', '-Fpnt'], capture_output=True, text=True, timeout=3)
    rows = result.stdout.splitlines()
    pids = {int(row[1:]) for row in rows if row.startswith('p')}
    names = {row[1:] for row in rows if row.startswith('n')}
    if result.returncode or len(pids) != 1 or names != {'127.0.0.1:%d' % port}:
        raise ValueError('调试端口必须仅由一个本机进程监听 127.0.0.1')
    # Reuse the kernel identity reader only; no signal/terminate operation.
    identity = Native().identity(next(iter(pids)))
    if identity['uid'] != os.getuid() or identity['zombie'] or identity['image'] != str(Path(executable).resolve()):
        raise ValueError('端口与原 App 进程身份不匹配')
    return {'pid': identity['pid'], 'birth': list(identity['birth']), 'started': list(identity['started'])}
