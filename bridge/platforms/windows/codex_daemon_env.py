"""Read only requested variables from a verified x64 Windows Codex daemon.

The PEB layout is an internal Windows interface. Fail closed when it differs.
"""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import hashlib
import json
import os
import socket
import stat
import struct
from pathlib import Path
from select import select


QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
VM_READ = 0x0010
SYNCHRONIZE = 0x00100000
TOKEN_QUERY = 0x0008
MAX_ENV = 1_048_576
FILETIME_UNIX_EPOCH = 116_444_736_000_000_000
WAIT_EXITED, WAIT_RUNNING = 0, 258
SHUTDOWN_FLAG = 'CODEX_DAEMON_SHUTDOWN_SOCKET'
AF_UNIX = 1  # Winsock; CPython's Windows build does not expose sockaddr_un.


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(ctypes.c_uint64)] * 4)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def _read(kernel, handle, address, size):
    if not address or size <= 0 or size > MAX_ENV:
        raise OSError('Codex daemon memory address invalid')
    buffer = ctypes.create_string_buffer(size)
    count = ctypes.c_size_t()
    if not kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size,
                                    ctypes.byref(count)) or count.value != size:
        raise OSError('Codex daemon launch data unavailable')
    return buffer.raw


def _owner_sid(handle):
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel = _kernel()
    target = wintypes.HANDLE()
    current = wintypes.HANDLE()
    if not advapi.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(target)):
        raise OSError('Codex daemon owner unavailable')
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(current)):
            raise OSError('Codex daemon owner unavailable')
        def token_sid(token):
            length = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))  # TokenUser
            if not 0 < length.value <= 4096:
                raise OSError('Codex daemon owner invalid')
            data = ctypes.create_string_buffer(length.value)
            if not advapi.GetTokenInformation(token, 1, data, length, ctypes.byref(length)):
                raise OSError('Codex daemon owner unavailable')
            return data, ctypes.c_void_p.from_buffer(data).value
        target_data, target_sid = token_sid(target)
        current_data, current_sid = token_sid(current)
        if not advapi.EqualSid(target_sid, current_sid):
            raise OSError('Codex daemon belongs to another user')
    finally:
        kernel.CloseHandle(target)
        if current:
            kernel.CloseHandle(current)


def _birth(kernel, handle):
    times = [ctypes.c_uint64() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise OSError('Codex daemon start time unavailable')
    return times[0].value


def _identity(kernel, handle, expected):
    birth = _birth(kernel, handle)
    path = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(path))
    if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
        raise OSError('Codex daemon executable unavailable')
    def normalized(value):
        return os.path.normcase(os.path.normpath(str(value).removeprefix('\\\\?\\')))
    if normalized(path.value) != normalized(Path(expected).resolve()):
        raise OSError('Codex daemon executable mismatch')
    return birth, normalized(path.value)


def _environment(kernel, handle, names):
    ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    basic = ctypes.create_string_buffer(48)  # PROCESS_BASIC_INFORMATION, x64.
    returned = wintypes.ULONG()
    if ntdll.NtQueryInformationProcess(handle, 0, basic, len(basic), ctypes.byref(returned)) < 0:
        raise OSError('Codex daemon PEB unavailable')
    peb = struct.unpack_from('<Q', basic.raw, 8)[0]
    params = struct.unpack('<Q', _read(kernel, handle, peb + 0x20, 8))[0]
    address = struct.unpack('<Q', _read(kernel, handle, params + 0x80, 8))[0]
    if not address:
        raise OSError('Codex daemon environment unavailable')
    # The UTF-16 block ends in two NUL characters. Read only bounded pages.
    raw = bytearray()
    offset = 0
    while offset < MAX_ENV:
        size = min(4096 - ((address + offset) % 4096), MAX_ENV - offset)
        previous = len(raw)
        raw.extend(_read(kernel, handle, address + offset, size))
        end = next((index for index in range(max(0, previous - 2) // 2 * 2, len(raw) - 3, 2)
                    if raw[index:index + 4] == b'\0\0\0\0'), -1)
        if end >= 0:
            raw = raw[:end + 4]
            break
        offset += size
    else:
        raise OSError('Codex daemon environment exceeds limit')
    selected = {}
    wanted = {name.casefold(): name for name in names}
    for entry in bytes(raw).decode('utf-16-le').split('\0'):
        if not entry:
            break
        key, separator, value = entry.partition('=')
        if separator and key.casefold() in wanted:
            selected[wanted[key.casefold()]] = value
    return selected


def read_process_environment(pid, record, executable, names, record_path=None):
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError('Codex daemon environment needs Windows x64')
    kernel = _kernel()
    handle = kernel.OpenProcess(QUERY | VM_READ, False, pid)
    if not handle:
        raise OSError('Codex daemon process read unavailable')
    try:
        _owner_sid(handle)
        before = _identity(kernel, handle, executable)
        if str(before[0]) != str(record.get('processStartTime')):
            raise OSError('Codex daemon PID record is stale')
        values = _environment(kernel, handle, names)
        if _identity(kernel, handle, executable) != before:
            raise OSError('Codex daemon process changed')
        return values, (pid, *before)
    finally:
        kernel.CloseHandle(handle)


def recorded_process_absent(pid, record):
    """Only a missing PID, a verified replacement, or an exited handle is proof."""
    start = record.get('processStartTime')
    if not isinstance(start, str) or not start.isascii() or not start.isdecimal() or len(start) > 20:
        return False
    birth = int(start)
    if not 0 < pid <= 0xffffffff or not FILETIME_UNIX_EPOCH < birth < 2 ** 64:
        return False
    kernel = _kernel()
    handle = kernel.OpenProcess(QUERY | SYNCHRONIZE, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such nonzero PID.
    try:
        return _birth(kernel, handle) != birth or kernel.WaitForSingleObject(handle, 0) == WAIT_EXITED
    finally:
        kernel.CloseHandle(handle)


class _Overlapped(ctypes.Structure):
    _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                ('offset', wintypes.DWORD), ('offset_high', wintypes.DWORD), ('event', wintypes.HANDLE)]


@contextmanager
def _operation_lock(path, *, create=False):
    from .local_service import FileInformation
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or
            getattr(parent, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise OSError('CLI daemon lock directory is not trusted')
    kernel = _kernel()
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    # Keep the file handle open without FILE_SHARE_DELETE. Match Codex's
    # exclusive, nonblocking lock of byte 0 even when the file is empty.
    handle = kernel.CreateFileW(str(path), 0x40000000, 3, None, 4 if create else 3, 0x200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError('CLI daemon lock could not be opened')
    lock = _Overlapped()
    acquired = False
    try:
        info = FileInformation()
        if (not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)) or info.links != 1 or
                info.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY)):
            raise OSError('CLI daemon lock could not be verified')
        acquired = bool(kernel.LockFileEx(handle, 3, 0, 1, 0, ctypes.byref(lock)))
        if not acquired and ctypes.get_last_error() != 33:  # ERROR_LOCK_VIOLATION
            raise OSError('CLI daemon lock could not be verified')
        yield acquired
    finally:
        if acquired:
            kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(lock))
        kernel.CloseHandle(handle)


def _private_maintenance(home):
    return home / 'bridge-cli-maintenance'


@contextmanager
def bridge_maintenance_lock(home):
    from .local_service import directory
    private = directory(_private_maintenance(Path(home).resolve()), create=True)
    with _operation_lock(private / 'maintenance.lock', create=True) as acquired:
        if not acquired:
            raise ValueError('共享接入仍在使用或退出中，请稍后重试')
        yield


def _peer_pid(channel):
    winsock = ctypes.WinDLL('ws2_32')
    winsock.WSAIoctl.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_void_p]
    winsock.WSAIoctl.restype = ctypes.c_int
    pid, size = ctypes.c_uint32(), ctypes.c_uint32()
    # SDK afunix.h: SIO_AF_UNIX_GETPEERPID = _WSAIOR(IOC_VENDOR, 256).
    if winsock.WSAIoctl(channel.fileno(), 0x58000100, None, 0, ctypes.byref(pid),
                       ctypes.sizeof(pid), ctypes.byref(size), None, None):
        raise OSError('CLI shutdown socket peer PID could not be verified')
    # Older Windows fills the ULONG but reports zero bytes, as handled by
    # Codex's own Windows AF_UNIX peer validation.
    if not pid.value or size.value not in (0, ctypes.sizeof(pid)):
        raise OSError('CLI shutdown socket peer PID could not be verified')
    return pid.value


def _connect_control_socket(channel, endpoint, timeout):
    path = os.fsencode(endpoint)
    if b'\0' in path or len(path) >= 108:
        raise OSError('CLI shutdown socket address is invalid')
    winsock = ctypes.WinDLL('ws2_32')
    winsock.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    winsock.connect.restype = ctypes.c_int
    address = ctypes.create_string_buffer(struct.pack('=H108s', AF_UNIX, path))
    channel.setblocking(False)
    if winsock.connect(channel.fileno(), address, 110):
        if winsock.WSAGetLastError() != 10035:  # WSAEWOULDBLOCK
            raise OSError('CLI shutdown socket connection failed')
        _, ready, failed = select([], [channel], [channel], timeout)
        if failed or not ready or channel.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
            raise OSError('CLI shutdown socket connection could not be verified')
    channel.settimeout(timeout)


class WindowsDaemonDrain:
    def __init__(self, home, executable, kernel, handle, generation, acquired):
        self.home, self.executable, self.generation = home, executable, generation
        self.kernel, self.handle = kernel, handle
        self.pid = generation[0]
        self.key = hashlib.sha256(json.dumps(generation).encode()).hexdigest()
        self.marker = _private_maintenance(home) / 'windows' / 'shutdown.json'
        self.observe_only = not acquired or self._requested()

    def _requested(self):
        from .local_service import read_record
        try:
            saved = read_record(self.marker)
        except FileNotFoundError:
            return False
        key = saved.get('generation') if isinstance(saved, dict) else None
        if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise OSError('CLI shutdown receipt could not be verified')
        return key == self.key

    @contextmanager
    def connect(self, timeout):
        endpoint = self.home / 'app-server-control' / 'app-server-control.sock'
        parent = endpoint.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or
                getattr(parent, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise OSError('CLI shutdown socket directory is not trusted')
        with socket.socket(AF_UNIX) as channel:
            _connect_control_socket(channel, endpoint, timeout)
            self.verify_peer(channel)
            yield channel

    def verify_peer(self, channel):
        if _peer_pid(channel) != self.pid or self.kernel.WaitForSingleObject(self.handle, 0) != WAIT_RUNNING:
            raise OSError('CLI shutdown socket belongs to another process generation')

    def verify_generation(self):
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        values, generation = read_daemon_environment(self.home, self.executable, {SHUTDOWN_FLAG})
        if generation != self.generation or values.get(SHUTDOWN_FLAG) != '1':
            raise OSError('CLI daemon shutdown capability or generation changed')

    def remember_shutdown(self):
        from .local_service import write_record
        if self.observe_only:
            raise OSError('CLI shutdown is already being observed')
        # Persist a known or newly authorized shutdown so other gateways using
        # this home only observe the process, including after uncertain I/O.
        write_record(self.marker, {'generation': self.key})
        self.observe_only = True

    def wait(self, timeout):
        if self.kernel.WaitForSingleObject(self.handle, int(timeout * 1000)) != WAIT_EXITED:
            raise OSError('原 CLI 的退出尚未确认；不会重发关闭请求或强制退出')


@contextmanager
def windows_daemon_drain(home, executable, generation):
    from bridge.platforms.codex_daemon_env import read_daemon_environment
    home = Path(home).resolve()
    values, observed = read_daemon_environment(home, executable, {SHUTDOWN_FLAG})
    if observed != generation or values.get(SHUTDOWN_FLAG) != '1':
        raise OSError('CLI daemon shutdown capability or generation changed')
    kernel = _kernel()
    handle = kernel.OpenProcess(QUERY | SYNCHRONIZE | VM_READ, False, generation[0])
    if not handle:
        raise OSError('CLI daemon process handle unavailable')
    try:
        _owner_sid(handle)
        if (generation[0], *_identity(kernel, handle, executable)) != generation:
            raise OSError('CLI daemon process generation changed')
        values = _environment(kernel, handle, {SHUTDOWN_FLAG, 'CODEX_HOME'})
        actual_home = Path(values['CODEX_HOME']) if values.get('CODEX_HOME') else Path.home() / '.codex'
        if actual_home.resolve() != home or values.get(SHUTDOWN_FLAG) != '1':
            raise OSError('CLI daemon shutdown belongs to another home')
        with _operation_lock(home / 'app-server-daemon' / 'daemon.lock') as acquired:
            yield WindowsDaemonDrain(home, executable, kernel, handle, generation, acquired)
    finally:
        kernel.CloseHandle(handle)
