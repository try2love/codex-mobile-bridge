"""Local-only Windows transport for the installed ZCode desktop session."""
import contextlib
import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path
import subprocess
import time

from .desktop import process_inventory
from .executable import product_version
from .force_exit import _Native, _windows_argv
from .loopback import _same_user
from .transport import WindowsPipe


@lru_cache(maxsize=1)
def _native():
    return _Native()


def identity(pid):
    native = _native()
    try:
        process = native.open(pid, terminate=False)
    except OSError as exc:
        if exc.winerror == 87:  # ERROR_INVALID_PARAMETER for an exited PID.
            raise ProcessLookupError(pid) from exc
        raise
    with contextlib.ExitStack() as stack:
        stack.callback(native.close, process)
        own = native.open(os.getpid(), terminate=False)
        stack.callback(native.close, own)
        row = native.identity(process)
        if not native.alive(process):
            raise ProcessLookupError(pid)
        if not native.session or row['session'] != native.session or not _same_user(native, process, own):
            raise PermissionError('ZCode 连接与当前 Windows 用户或桌面会话不匹配')
        return row


def process_args(pid):
    # Query only this PID (Windows 8.1+). Avoid a machine-wide WMI scan for each RPC.
    class UnicodeString(ctypes.Structure):
        _fields_ = [('length', wintypes.USHORT), ('maximum', wintypes.USHORT), ('data', ctypes.c_void_p)]
    query = ctypes.WinDLL('ntdll').NtQueryInformationProcess
    query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    query.restype = wintypes.LONG
    native = _native()
    handle = native.open(pid, terminate=False)
    try:
        size = wintypes.ULONG()
        query(handle, 60, None, 0, ctypes.byref(size))  # ProcessCommandLineInformation
        if not ctypes.sizeof(UnicodeString) <= size.value <= 128 * 1024:
            raise PermissionError('无法核验本机进程启动参数')
        buf = ctypes.create_string_buffer(size.value)
        if query(handle, 60, buf, len(buf), ctypes.byref(size)) != 0:
            raise PermissionError('本机进程启动参数已变化')
        value = UnicodeString.from_buffer(buf)
        start = ctypes.addressof(buf)
        if (not value.data or value.length % 2 or not value.length or
                not start <= value.data <= start + len(buf) - value.length):
            raise ValueError('Windows 进程启动参数无效')
        return list(_windows_argv(ctypes.wstring_at(value.data, value.length // 2)))
    finally:
        native.close(handle)


def is_supported(application):
    path = Path(application)
    return (path.is_absolute() and path.name.casefold() == 'zcode.exe' and path.is_file() and
            (path.parent / 'resources/app.asar').is_file() and bool(product_version(path)))


def _verify_publisher(application):
    # Verify time-stamped Authenticode, accepting certificate renewal for the same legal entity.
    script = r"""
$ErrorActionPreference = 'Stop'
$s = Get-AuthenticodeSignature -LiteralPath $env:CMB_ZCODE_EXE
if ($s.Status -ne 'Valid' -or
    $s.SignerCertificate.Subject -notmatch '(?:^|,\s*)(?:SERIALNUMBER|OID\.2\.5\.4\.5)=91110108MA01KP2T5U(?:,|$)' -or
    $s.SignerCertificate.Subject -notmatch '(?:^|,\s*)C=CN(?:,|$)' -or
    (Get-Item -LiteralPath $env:CMB_ZCODE_EXE).VersionInfo.ProductName -ne 'ZCode') { exit 2 }
"""
    result = subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),
                             '-NoProfile', '-NonInteractive', '-Command', script],
                            env={**os.environ, 'CMB_ZCODE_EXE': str(application)},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise PermissionError('无法核验所选 ZCode 安装的官方发行者签名')


class _AppStream:
    def __init__(self, process, reader, writer):
        self.process = process
        self.reader = WindowsPipe(reader)
        self.writer = WindowsPipe(writer)

    def read(self, length, deadline):
        return self.reader.recv(length, timeout=deadline - time.monotonic())

    def write(self, data, deadline):
        return self.writer.send(data, timeout=deadline - time.monotonic())

    def close(self):
        self.reader.close()
        self.writer.close()
        self.process.poll()


def launch(application):
    import _winapi
    from asyncio.windows_utils import pipe

    application = Path(application).resolve(strict=True)
    if not is_supported(application):
        raise ValueError('未找到所选 ZCode 桌面安装')
    _verify_publisher(application)
    if not _native().session or process_inventory([application])[application]['pids']:
        raise ValueError('请在当前 Windows 用户桌面正常退出已运行的 ZCode，再初始化连接')
    with contextlib.ExitStack() as child, contextlib.ExitStack() as host:
        app_read, host_write = pipe(overlapped=(False, True))
        child.callback(_winapi.CloseHandle, app_read)
        host.callback(_winapi.CloseHandle, host_write)
        host_read, app_write = pipe(overlapped=(True, False))
        child.callback(_winapi.CloseHandle, app_write)
        host.callback(_winapi.CloseHandle, host_read)
        for handle in (app_read, app_write):
            os.set_handle_inheritable(handle, True)
        info = subprocess.STARTUPINFO(lpAttributeList={'handle_list': [app_read, app_write]})
        args = [str(application), '--remote-debugging-pipe',
                f'--remote-debugging-io-pipes={app_read},{app_write}']
        process = subprocess.Popen(args, executable=str(application), cwd=application.parent,
                                   startupinfo=info, close_fds=True,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        stream = _AppStream(process, host_read, host_write)
        host.pop_all()
        return process.pid, stream


def peer_pid(connection, *, server):
    name = 'GetNamedPipeClientProcessId' if server else 'GetNamedPipeServerProcessId'
    fn = getattr(_native().kernel, name)
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    fn.restype = wintypes.BOOL
    pid = wintypes.ULONG()
    if not fn(connection.fileno(), ctypes.byref(pid)) or not pid.value:
        raise PermissionError('无法核验 Windows 私有管道的对端进程')
    return pid.value


def _local_path(address):
    if not isinstance(address, str) or not address.casefold().startswith('\\\\.\\pipe\\') or '\\' in address[9:] or not address[9:]:
        raise ValueError('ZCode 只接受本机管道地址')
    return address


def local_listener(address):
    import _winapi
    from multiprocessing.connection import BUFSIZE, PipeListener

    class LocalPipeListener(PipeListener):
        def _new_handle(self, first=False):
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            mode = _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | 8  # PIPE_REJECT_REMOTE_CLIENTS
            return _winapi.CreateNamedPipe(self._address, flags, mode, _winapi.PIPE_UNLIMITED_INSTANCES,
                                           BUFSIZE, BUFSIZE, _winapi.NMPWAIT_WAIT_FOREVER, _winapi.NULL)

    return LocalPipeListener(_local_path(address))


def local_connect(address, timeout=2):
    import _winapi
    from multiprocessing.connection import PipeConnection

    address = _local_path(address)
    deadline = time.monotonic() + timeout
    while True:
        try:
            # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION. Peers can identify but cannot impersonate.
            flags = _winapi.FILE_FLAG_OVERLAPPED | 0x00110000
            handle = _winapi.CreateFile(address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                                        0, _winapi.NULL, _winapi.OPEN_EXISTING, flags, _winapi.NULL)
            break
        except OSError as exc:
            if exc.winerror != _winapi.ERROR_PIPE_BUSY or time.monotonic() >= deadline:
                raise
            try:
                _winapi.WaitNamedPipe(address, max(1, int((deadline - time.monotonic()) * 1000)))
            except OSError as waiting:
                if waiting.winerror == _winapi.ERROR_SEM_TIMEOUT:
                    raise TimeoutError('ZCode 私有管道正在使用') from waiting
                raise
    try:
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise
    return PipeConnection(handle)
