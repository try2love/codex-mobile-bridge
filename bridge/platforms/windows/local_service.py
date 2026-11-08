"""Owner-only records and same-session peers for the desktop's local RPC holder."""
import contextlib
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import uuid

from .zcode_pipe import _identity, _native, identity, local_connect, local_listener, peer_pid
from .transport import WindowsPipe


class SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wintypes.BOOL)]


class FileInformation(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('times', wintypes.FILETIME * 3),
                ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD), ('size_low', wintypes.DWORD),
                ('links', wintypes.DWORD), ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


@contextlib.contextmanager
def _user_sid():
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    api.OpenProcessToken.restype = wintypes.BOOL
    api.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetTokenInformation.restype = wintypes.BOOL
    process = _native().open(os.getpid(), terminate=False)
    token, size = wintypes.HANDLE(), wintypes.DWORD()
    try:
        if not api.OpenProcessToken(process, 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        yield api, ctypes.c_void_p.from_buffer(buf)
    finally:
        if token:_native().close(token)
        _native().close(process)


@contextlib.contextmanager
def private_security(*, directory=False):
    with _user_sid() as (api, sid):
        api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        api.ConvertSidToStringSidW.restype = wintypes.BOOL
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        text, descriptor = wintypes.LPWSTR(), ctypes.c_void_p()
        kernel = _native().kernel
        kernel.LocalFree.argtypes = [ctypes.c_void_p];kernel.LocalFree.restype = ctypes.c_void_p
        try:
            if not api.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            sddl = 'O:' + text.value + 'D:P(A;' + ('OICI' if directory else '') + ';FA;;;' + text.value + ')'
            if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
                raise ctypes.WinError(ctypes.get_last_error())
            yield SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        finally:
            if descriptor:kernel.LocalFree(descriptor)
            if text:kernel.LocalFree(text)


def _check(handle, *, directory):
    kernel = _native().kernel
    kernel.LocalFree.argtypes = [ctypes.c_void_p];kernel.LocalFree.restype = ctypes.c_void_p
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    info = FileInformation()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory or not directory and info.links != 1:
        raise PermissionError('本机服务记录不能使用链接或重解析路径')
    with _user_sid() as (api, sid):
        api.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        api.GetSecurityInfo.restype = wintypes.DWORD
        api.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p];api.EqualSid.restype = wintypes.BOOL
        api.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)];api.GetAce.restype = wintypes.BOOL
        api.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)];api.GetSecurityDescriptorControl.restype = wintypes.BOOL
        owner, acl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        try:
            if api.GetSecurityInfo(handle, 1, 5, ctypes.byref(owner), None, ctypes.byref(acl), None, ctypes.byref(descriptor)):
                raise PermissionError('无法核验本机服务记录权限')
            if not owner or not api.EqualSid(owner, sid) or not acl:
                raise PermissionError('本机服务记录不属于当前用户')
            control, revision = wintypes.WORD(), wintypes.DWORD()
            if not api.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)) or not control.value & 0x1000:
                raise PermissionError('本机服务记录不能继承其他用户的访问权限')
            count = ctypes.c_ushort.from_address(acl.value + 4).value
            if not 1 <= count <= 16:
                raise PermissionError('本机服务记录权限无效')
            for index in range(count):
                ace = ctypes.c_void_p()
                if not api.GetAce(acl, index, ctypes.byref(ace)) or ctypes.c_ubyte.from_address(ace.value).value != 0 or not api.EqualSid(ace.value + 8, sid):
                    raise PermissionError('本机服务记录必须仅允许当前用户访问')
        finally:
            if descriptor:kernel.LocalFree(descriptor)


def _open(path, *, directory=False, write=False, security=None):
    kernel = _native().kernel
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x20000 | (0x40000000 if write else 0x80000000), 7 if not write else 0,
                                ctypes.byref(security) if security is not None else None,
                                1 if write else 3, 0x200000 | (0x2000000 if directory else 0), None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def directory(path, *, create=False):
    path = Path(path)
    if create:
        with private_security(directory=True) as security:
            kernel = _native().kernel
            kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
            kernel.CreateDirectoryW.restype = wintypes.BOOL
            if not kernel.CreateDirectoryW(str(path), ctypes.byref(security)) and ctypes.get_last_error() != 183:
                raise ctypes.WinError(ctypes.get_last_error())
    handle = _open(path, directory=True)
    try:_check(handle, directory=True)
    finally:_native().close(handle)
    return path


def read_record(path):
    import msvcrt
    path = Path(path);directory(path.parent.parent);directory(path.parent)
    handle = _open(path)
    try:
        _check(handle, directory=False)
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY);handle = None
        with os.fdopen(fd, 'rb') as stream:raw = stream.read(4097)
    finally:
        if handle is not None:_native().close(handle)
    if len(raw) > 4096:raise ValueError('本机服务记录无效')
    return json.loads(raw)


def write_record(path, value):
    import msvcrt
    path = Path(path);directory(path.parent.parent, create=True);directory(path.parent, create=True)
    temp = path.with_name('.' + uuid.uuid4().hex)
    try:
        with private_security() as security:
            handle = _open(temp, write=True, security=security)
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY);handle = None
                with os.fdopen(fd, 'wb') as stream:stream.write(json.dumps(value).encode());stream.flush();os.fsync(stream.fileno())
            finally:
                if handle is not None:_native().close(handle)
        os.replace(temp, path)
    finally:temp.unlink(missing_ok=True)


def require_peer(conn, *, server, expected=None):
    peer = identity(peer_pid(conn, server=server))
    if peer['image'] != identity(os.getpid())['image'] or expected is not None and any(peer.get(key) != expected.get(key) for key in ('pid', 'created', 'session', 'image')):
        raise PermissionError('本机服务与当前网关进程不匹配')
    return peer


def require_parent(pid):
    owner = _identity(os.getpid(), parent=True)
    if owner['parent'] != pid:raise PermissionError('本机服务父进程不匹配')
    identity(pid)  # The original helper enforces the same user and interactive session.
    return {key: owner[key] for key in ('pid', 'created', 'session', 'image')}


def listener(address):
    return local_listener(address, security_attributes=private_security)


def byte_stream(connection):
    import _winapi
    _winapi.SetNamedPipeHandleState(connection.fileno(), 0, None, None)  # PIPE_READMODE_BYTE
    # connection owns the handle. Each WindowsPipe operation finishes cancelled
    # overlapped I/O before returning, so its caller may then close connection.
    return WindowsPipe(connection.fileno())
