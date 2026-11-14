"""Windows generic credentials as raw bytes, matching the native Go client."""
import ctypes
from ctypes import wintypes


class Credential(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]


def api():
    native = ctypes.WinDLL('Advapi32', use_last_error=True)
    native.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.POINTER(ctypes.POINTER(Credential))]
    native.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    native.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    for name in ('CredReadW', 'CredWriteW', 'CredDeleteW'):
        getattr(native, name).restype = wintypes.BOOL
    native.CredFree.argtypes = [ctypes.c_void_p]
    native.CredFree.restype = None
    return native


def read(service, user):
    native, pointer = api(), ctypes.POINTER(Credential)()
    if not native.CredReadW(service+':'+user, 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ValueError('无法读取 Windows 原生登录凭据')
    try:
        return ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize)
    finally:
        native.CredFree(pointer)


def write(service, user, value):
    native, target = api(), service+':'+user
    if value is None:
        if not native.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != 1168:
            raise ValueError('无法移除 Windows 原生登录凭据')
        return
    if len(value) > 2560:
        raise ValueError('Windows 原生凭据超过支持长度')
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    credential = Credential(Type=1, TargetName=target, CredentialBlobSize=len(value),
                            CredentialBlob=buffer, Persist=2, UserName=user)
    if not native.CredWriteW(ctypes.byref(credential), 0):
        raise ValueError('Windows 原生登录凭据写入失败')
