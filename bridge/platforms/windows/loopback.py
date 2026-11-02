"""Read-only identity checks for a local desktop development endpoint."""
import ctypes
import os
import socket
from pathlib import Path

from .force_exit import _Native


class _Tcp4(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ('state', 'address', 'port', 'remote_address', 'remote_port', 'pid')]


class _Tcp6(ctypes.Structure):
    _fields_ = [('address', ctypes.c_ubyte * 16), ('scope', ctypes.c_uint32),
                ('port', ctypes.c_uint32), ('remote_address', ctypes.c_ubyte * 16),
                ('remote_scope', ctypes.c_uint32), ('remote_port', ctypes.c_uint32),
                ('state', ctypes.c_uint32), ('pid', ctypes.c_uint32)]


def _listeners(port):
    api = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
                    ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.restype = ctypes.c_uint32
    rows = []
    for family, row_type in ((socket.AF_INET, _Tcp4), (socket.AF_INET6, _Tcp6)):
        size = ctypes.c_uint32()
        code = api(None, ctypes.byref(size), False, family, 3, 0)  # OWNER_PID_LISTENER
        if code not in (0, 122) or size.value < 4:
            raise OSError('无法读取 Windows 监听端口')
        buffer = ctypes.create_string_buffer(size.value)
        code = api(buffer, ctypes.byref(size), False, family, 3, 0)
        if code:
            raise OSError('Windows 监听端口在核验期间变化，请重试')
        count = ctypes.c_uint32.from_buffer(buffer).value
        if 4 + count * ctypes.sizeof(row_type) > len(buffer):
            raise ValueError('Windows 监听端口信息不完整')
        for index in range(count):
            row = row_type.from_buffer(buffer, 4 + index * ctypes.sizeof(row_type))
            if socket.ntohs(row.port & 0xffff) == port:
                address = (socket.inet_ntop(family, row.address.to_bytes(4, 'little'))
                           if family == socket.AF_INET else socket.inet_ntop(family, bytes(row.address)))
                rows.append((row.pid, address))
    return rows


def _same_user(native, first, second):
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    security.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    security.OpenProcessToken.restype = ctypes.c_int
    security.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    security.GetTokenInformation.restype = ctypes.c_int
    security.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    security.EqualSid.restype = ctypes.c_int
    buffers = []
    for process in (first, second):
        token, size = ctypes.c_void_p(), ctypes.c_uint32()
        if not security.OpenProcessToken(process, 8, ctypes.byref(token)):  # TOKEN_QUERY
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(size.value)
            if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            buffers.append(buffer)
        finally:
            native.close(token)
    return bool(security.EqualSid(*(ctypes.c_void_p.from_buffer(buffer) for buffer in buffers)))


def listener_identity(port, executable):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('无效的本机调试端口')
    rows = _listeners(port)
    if len(rows) != 1 or rows[0][1] != '127.0.0.1':
        raise ValueError('调试端口必须仅由一个本机进程监听 127.0.0.1')
    native = _Native()
    handle = native.open(rows[0][0], terminate=False)
    try:
        identity = native.identity(handle)
        own = native.open(os.getpid(), terminate=False)
        try:
            same_user = _same_user(native, handle, own)
        finally:
            native.close(own)
        if (not native.alive(handle) or not same_user or identity['session'] == 0
                or identity['session'] != native.session
                or identity['image'] != os.path.normcase(str(Path(executable).resolve()))
                or _listeners(port) != rows):
            raise ValueError('端口与原 App 用户、桌面会话或进程身份不匹配')
        return {key: identity[key] for key in ('pid', 'created', 'session')}
    finally:
        native.close(handle)
