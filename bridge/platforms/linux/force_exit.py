"""Linux process generation handles; unsupported kernels fail closed."""
import os
from pathlib import Path
import signal


def _read_proc_image(exe_path, *, allow_deleted=False):
    """Read one proc image; deleted images are opt-in for their owning client only."""
    try:
        return str(exe_path.resolve(strict=True))
    except FileNotFoundError as exc:
        # AppImage 以 APPIMAGE_EXTRACT_AND_RUN=1 运行并清理临时解压目录时，
        # 内核仍会将 /proc/<pid>/exe 标记为 '<路径> (deleted)'；保留该后缀以免误匹配普通磁盘路径
        try:
            raw = os.readlink(exe_path)
        except (OSError, TypeError):
            raise exc from None
        if allow_deleted and os.path.isabs(raw) and raw.endswith(' (deleted)'):
            return raw
        raise


class Native:
    def __init__(self):
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise ValueError('当前 Linux 运行环境不支持安全强制关闭，请在电脑端退出应用')

    def identity(self, pid, *, allow_deleted=False):
        root = Path('/proc')/str(pid)
        try:
            stat = root.stat()
            before = (root/'stat').read_text().rsplit(')', 1)[1].split()
            image = '' if before[0] == 'Z' else (
                _read_proc_image(root/'exe', allow_deleted=True) if allow_deleted
                else _read_proc_image(root/'exe'))
            after = (root/'stat').read_text().rsplit(')', 1)[1].split()
            if allow_deleted and _read_proc_image(root/'exe', allow_deleted=True) != image:
                raise ProcessLookupError('process image changed')
        except FileNotFoundError:
            if not root.exists():
                raise ProcessLookupError('process exited') from None
            raise
        if before[19] != after[19]:
            raise ProcessLookupError('process changed')
        return {'pid': pid, 'parent': int(after[1]), 'uid': stat.st_uid, 'ruid': stat.st_uid,
                'image': image, 'birth': after[19], 'zombie': after[0] == 'Z'}

    def ancestor(self, pid):
        # A non-dumpable SSH/session ancestor can deny exe reads even to its
        # own user. Keep its PID in the protected chain without treating this
        # partial metadata as a signalable target identity.
        root = Path('/proc')/str(pid)
        before = (root/'stat').read_text().rsplit(')', 1)[1].split()
        try:
            image = _read_proc_image(root/'exe')
        except PermissionError:
            image = ''
        after = (root/'stat').read_text().rsplit(')', 1)[1].split()
        if before[19] != after[19]:
            raise ProcessLookupError('process changed')
        return {'parent': int(after[1]), 'image': image}

    def open(self, identity, *, allow_deleted=False):
        handle = os.pidfd_open(identity['pid'])
        try:
            current = (self.identity(identity['pid'], allow_deleted=True) if allow_deleted
                       else self.identity(identity['pid']))
            if current != identity:
                raise ProcessLookupError('process changed')
            return handle
        except BaseException:
            os.close(handle)
            raise

    def terminate(self, handle):
        signal.pidfd_send_signal(handle, signal.SIGKILL)

    def close(self, handle):
        os.close(handle)
