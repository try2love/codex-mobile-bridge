"""Read PE product metadata without launching the application."""
import ctypes


def product_version(path):
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    version.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = ctypes.c_int
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    version.VerQueryValueW.restype = ctypes.c_int
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    value, length = ctypes.c_void_p(), ctypes.c_uint32()
    if (not version.GetFileVersionInfoW(str(path), 0, size, buffer)
            or not version.VerQueryValueW(buffer, '\\', ctypes.byref(value), ctypes.byref(length))
            or length.value < 52):
        return None
    fields = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
    if fields[0] != 0xfeef04bd:
        return None
    return '.'.join(str(number) for number in (fields[4] >> 16, fields[4] & 0xffff,
                                              fields[5] >> 16, fields[5] & 0xffff))
