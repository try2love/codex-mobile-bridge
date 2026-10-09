"""Read the gateway's own Windows session; never switch or unlock desktops."""
import ctypes
import os
import sys


class _SessionInfo(ctypes.Structure):
    _fields_ = [('session_id', ctypes.c_uint32), ('connection_state', ctypes.c_int32),
                ('flags', ctypes.c_int32), ('station', ctypes.c_uint16 * 33),
                ('user', ctypes.c_uint16 * 21), ('domain', ctypes.c_uint16 * 18),
                ('times', ctypes.c_int64 * 5), ('counters', ctypes.c_uint32 * 6)]


class _InfoData(ctypes.Union):
    _fields_ = [('session', _SessionInfo)]


class _InfoEx(ctypes.Structure):
    _fields_ = [('level', ctypes.c_uint32), ('data', _InfoData)]


def _query_session():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    kernel.ProcessIdToSessionId.restype = ctypes.c_int
    wts.WTSQuerySessionInformationW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                              ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    wts.WTSQuerySessionInformationW.restype = ctypes.c_int
    wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    wts.WTSFreeMemory.restype = None
    session_id, size, buffer = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_void_p()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise OSError('Windows session unavailable')
    try:
        # WTSSessionInfoEx = 25. The active console can belong to another user;
        # always inspect this process's session, including an RDP session.
        if not wts.WTSQuerySessionInformationW(None, session_id, 25, ctypes.byref(buffer), ctypes.byref(size)):
            raise OSError('Windows session information unavailable')
        if not buffer or size.value < ctypes.sizeof(_InfoEx):
            raise ValueError('Incomplete Windows session information')
        info = ctypes.cast(buffer, ctypes.POINTER(_InfoEx)).contents
        if info.level != 1 or info.data.session.session_id != session_id.value:
            raise ValueError('Unverified Windows session information')
        return info.data.session.flags, info.data.session.connection_state, session_id.value
    finally:
        if buffer:
            wts.WTSFreeMemory(buffer)


def _input_desktop_available():
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    user.OpenInputDesktop.restype = ctypes.c_void_p
    user.CloseDesktop.argtypes = [ctypes.c_void_p]
    user.CloseDesktop.restype = ctypes.c_int
    user.GetThreadDesktop.argtypes = [ctypes.c_uint32]
    user.GetThreadDesktop.restype = ctypes.c_void_p
    kernel.GetCurrentThreadId.argtypes = []
    kernel.GetCurrentThreadId.restype = ctypes.c_uint32
    user.GetUserObjectInformationW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                               ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    user.GetUserObjectInformationW.restype = ctypes.c_int
    desktop = user.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS only.
    if not desktop:
        return False  # Could also be UAC or access denial; this does not mean locked.
    try:
        def name(handle):
            value, needed = ctypes.create_unicode_buffer(256), ctypes.c_uint32()
            if not handle or not user.GetUserObjectInformationW(handle, 2, value, ctypes.sizeof(value), ctypes.byref(needed)):
                raise OSError('Input desktop identity unavailable')
            return value.value
        return name(desktop) == name(user.GetThreadDesktop(kernel.GetCurrentThreadId()))
    finally:
        user.CloseDesktop(desktop)


def status():
    """Return lock evidence separately from interactive-desktop availability."""
    if sys.platform != 'win32':
        return {'state': 'unsupported', 'interactive': None, 'reason': ''}
    state, active = 'unknown', False
    try:
        flags, connection, session_id = _query_session()
        # Win7/Server 2008 R2 had the documented lock/unlock flag inversion.
        if sys.getwindowsversion()[:2] == (6, 1) and flags in (0, 1):
            flags = 1 - flags
        state = {0: 'locked', 1: 'unlocked'}.get(flags, 'unknown')
        active = connection == 0 and session_id != 0  # WTSActive, not session 0.
    except (OSError, ValueError, AttributeError):
        pass
    interactive = False if state == 'locked' or state == 'unlocked' and not active else None
    if state == 'unlocked' and active:
        try:
            interactive = _input_desktop_available()
        except (OSError, ValueError, AttributeError):
            pass
    if state == 'locked':
        reason = 'Windows 已锁定；请解锁电脑后重试初始化连接'
    elif state == 'unknown':
        reason = '无法确认 Windows 桌面状态；请回到电脑桌面后重试初始化连接'
    elif not active:
        reason = 'Windows 桌面会话未连接；请恢复电脑桌面后重试初始化连接'
    elif interactive is not True:
        reason = 'Windows 输入桌面不可用；请关闭系统安全提示或恢复桌面后重试初始化连接'
    else:
        reason = 'Windows 桌面已解锁'
    return {'state': state, 'interactive': interactive, 'reason': reason}


class DesktopUnavailable(ValueError):
    def __init__(self, value):
        super().__init__(value['reason'])
        self.setup_state = 'needs-unlock' if value['state'] == 'locked' else 'needs-desktop'


def require_interactive():
    """Only foreground initialization needs this; normal bridge calls do not."""
    if sys.platform == 'win32':
        value = status()
        if value['state'] != 'unlocked' or value['interactive'] is not True:
            raise DesktopUnavailable(value)
