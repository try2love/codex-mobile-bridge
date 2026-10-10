"""Linux process generation handles; unsupported kernels fail closed."""
import os
from pathlib import Path
import signal


class Native:
    def __init__(self):
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise ValueError('当前 Linux 运行环境不支持安全强制关闭，请在电脑端退出应用')

    def identity(self, pid):
        root = Path('/proc')/str(pid)
        try:
            stat = root.stat()
            before = (root/'stat').read_text().rsplit(')', 1)[1].split()
            image = '' if before[0] == 'Z' else str((root/'exe').resolve(strict=True))
            after = (root/'stat').read_text().rsplit(')', 1)[1].split()
        except FileNotFoundError:
            if not root.exists():
                raise ProcessLookupError('process exited') from None
            raise
        if before[19] != after[19]:
            raise ProcessLookupError('process changed')
        return {'pid': pid, 'parent': int(after[1]), 'uid': stat.st_uid, 'ruid': stat.st_uid,
                'image': image, 'birth': after[19], 'zombie': after[0] == 'Z'}

    def open(self, identity):
        handle = os.pidfd_open(identity['pid'])
        try:
            if self.identity(identity['pid']) != identity:
                raise ProcessLookupError('process changed')
            return handle
        except BaseException:
            os.close(handle)
            raise

    def terminate(self, handle):
        signal.pidfd_send_signal(handle, signal.SIGKILL)

    def close(self, handle):
        os.close(handle)
