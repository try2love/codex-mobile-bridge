"""Signal one macOS process generation, never a recycled numeric PID."""
import ctypes
import errno
import os
from pathlib import Path
import signal


class _BSD(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'parent', 'uid', 'gid', 'ruid', 'rgid',
        'svuid', 'svgid', 'reserved')] + [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid', 'nice')] + [
        ('start_sec', ctypes.c_uint64), ('start_usec', ctypes.c_uint64)]


class _Unique(ctypes.Structure):
    # Apple XNU bsd/sys/proc_info_private.h, PROC_PIDUNIQIDENTIFIERINFO (17).
    _fields_ = [('uuid', ctypes.c_ubyte * 16), ('unique', ctypes.c_uint64),
                ('parent_unique', ctypes.c_uint64), ('version', ctypes.c_int32),
                ('parent_version', ctypes.c_int32), ('reserved', ctypes.c_uint64 * 2)]


class Native:
    def __init__(self):
        self.lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                         ctypes.c_void_p, ctypes.c_int]
        self.lib.proc_pidinfo.restype = ctypes.c_int
        self.lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.lib.proc_pidpath.restype = ctypes.c_int
        self.lib.proc_signal_with_audittoken.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.proc_signal_with_audittoken.restype = ctypes.c_int

    def _info(self, pid, flavor, value):
        if self.lib.proc_pidinfo(pid, flavor, 0, ctypes.byref(value), ctypes.sizeof(value)) != ctypes.sizeof(value):
            code = ctypes.get_errno() or errno.ESRCH
            raise OSError(code, os.strerror(code))
        return value

    def identity(self, pid):
        before = self._info(pid, 17, _Unique())
        info = self._info(pid, 3, _BSD())
        path = ctypes.create_string_buffer(4096)
        if self.lib.proc_pidpath(pid, path, len(path)) <= 0:
            code = ctypes.get_errno() or errno.ESRCH
            raise OSError(code, os.strerror(code))
        after = self._info(pid, 17, _Unique())
        if (before.unique, before.version) != (after.unique, after.version):
            raise ProcessLookupError(errno.ESRCH, 'process changed')
        return {'pid': info.pid, 'parent': info.parent, 'uid': info.uid, 'ruid': info.ruid,
                'image': str(Path(os.fsdecode(path.value)).resolve()),
                'birth': (after.unique, after.version), 'zombie': info.status == 5}

    def open(self, identity):
        # proc_signal_with_audittoken uses pid and pidversion in audit_token_t.
        # The kernel also enforces normal same-user signal permissions.
        return (ctypes.c_uint32 * 8)(0, identity['uid'], 0, identity['ruid'], 0,
                                    identity['pid'], 0, identity['birth'][1])

    def terminate(self, handle):
        if self.lib.proc_signal_with_audittoken(ctypes.byref(handle), signal.SIGKILL) != 0:
            code = ctypes.get_errno() or errno.EPERM
            raise OSError(code, os.strerror(code))

    def close(self, handle):
        pass
