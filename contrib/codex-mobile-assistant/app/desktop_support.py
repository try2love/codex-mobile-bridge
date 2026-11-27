"""Windows tray, optional current-user startup and explicit update checks."""
import ctypes
from ctypes import wintypes as W
import json
import re
import subprocess
import threading
import urllib.request
from urllib.parse import urlsplit

VERSION = '1.6.27'


def https_url(value):
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('请输入不含账号密码的 HTTPS 地址')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('更新源端口无效')
    return value


def version_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', value):
        raise ValueError('更新信息中的版本号格式不正确')
    return tuple(int(x) for x in value.split('.')) + (0,) * (4 - len(value.split('.')))


def check_update(url):
    if not url.strip():
        return {'status': 'unconfigured', 'message': '尚未配置更新源，请在应用设置中填写 HTTPS 更新信息地址。'}
    class SecureRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            https_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(SecureRedirect())
    request = urllib.request.Request(https_url(url), headers={'Accept': 'application/json', 'User-Agent': 'CodexMobile/' + VERSION})
    with opener.open(request, timeout=8) as response:
        data = response.read(131073)
    if len(data) > 131072:
        raise ValueError('更新信息过大')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('更新信息必须是 JSON 对象')
    newer = version_parts(value.get('version')) > version_parts(VERSION)
    result = {'status': 'available' if newer else 'current', 'version': value['version'],
              'message': '发现新版本 ' + value['version'] if newer else '当前版本已是最新（此更新源）'}
    if newer:
        result['url'] = https_url(value.get('downloadUrl', ''))
        result['notes'] = str(value.get('notes', ''))[:2000]
    return result


def startup_command(key, executable=None, enabled=None):
    import winreg
    path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    name = 'CodexMobileAssistant-' + key
    if enabled is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as registry:
                return winreg.QueryValueEx(registry, name)[0]
        except FileNotFoundError:
            return None
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as registry:
        if enabled:
            winreg.SetValueEx(registry, name, 0, winreg.REG_SZ, subprocess.list2cmdline([str(executable), '--minimized']))
        else:
            try:
                winreg.DeleteValue(registry, name)
            except FileNotFoundError:
                pass


def restore_existing(key):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
    user.FindWindowW.restype = W.HWND
    user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    handle = user.FindWindowW('CodexMobileTray-' + key, None)
    return bool(handle and user.PostMessageW(handle, 0x8002, 0, 0))


class TrayIcon:
    def __init__(self, key, icon_path, emit):
        self.key, self.icon_path, self.emit = key, str(icon_path), emit
        self.hwnd = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, 0x0010, 0, 0)
            if self.thread is not threading.current_thread():
                self.thread.join(timeout=2)

    def _run(self):
        try:
            self._message_loop()
        except Exception as exc:
            self.emit('tray-error', str(exc))

    def _message_loop(self):
        user = self.user = ctypes.WinDLL('user32', use_last_error=True)
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        class WindowClass(ctypes.Structure):
            _fields_ = [('style', W.UINT), ('proc', callback), ('classExtra', ctypes.c_int), ('windowExtra', ctypes.c_int),
                        ('instance', W.HINSTANCE), ('icon', W.HICON), ('cursor', W.HANDLE), ('background', W.HBRUSH),
                        ('menu', W.LPCWSTR), ('name', W.LPCWSTR)]
        class IconData(ctypes.Structure):
            _fields_ = [('size', W.DWORD), ('window', W.HWND), ('id', W.UINT), ('flags', W.UINT),
                        ('message', W.UINT), ('icon', W.HICON), ('tip', W.WCHAR * 128),
                        ('state', W.DWORD), ('stateMask', W.DWORD), ('info', W.WCHAR * 256),
                        ('version', W.UINT), ('title', W.WCHAR * 64), ('infoFlags', W.DWORD),
                        ('guid', ctypes.c_byte * 16), ('balloonIcon', W.HICON)]
        api = {
            'DefWindowProcW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], ctypes.c_ssize_t),
            'RegisterClassW': ([ctypes.POINTER(WindowClass)], W.ATOM),
            'CreateWindowExW': ([W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, ctypes.c_void_p], W.HWND),
            'LoadImageW': ([W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT], W.HANDLE),
            'PostMessageW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            'GetMessageW': ([ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], ctypes.c_int),
            'TranslateMessage': ([ctypes.POINTER(W.MSG)], W.BOOL), 'DispatchMessageW': ([ctypes.POINTER(W.MSG)], ctypes.c_ssize_t),
            'CreatePopupMenu': ([], W.HMENU), 'AppendMenuW': ([W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR], W.BOOL),
            'TrackPopupMenu': ([W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p], W.UINT),
            'GetCursorPos': ([ctypes.POINTER(W.POINT)], W.BOOL), 'SetForegroundWindow': ([W.HWND], W.BOOL),
            'DestroyMenu': ([W.HMENU], W.BOOL), 'DestroyIcon': ([W.HICON], W.BOOL),
            'DestroyWindow': ([W.HWND], W.BOOL), 'RegisterWindowMessageW': ([W.LPCWSTR], W.UINT),
            'UnregisterClassW': ([W.LPCWSTR, W.HINSTANCE], W.BOOL), 'PostQuitMessage': ([ctypes.c_int], None),
        }
        for name, (args, result) in api.items():
            getattr(user, name).argtypes, getattr(user, name).restype = args, result
        kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [W.LPCWSTR], W.HMODULE
        shell.Shell_NotifyIconW.argtypes, shell.Shell_NotifyIconW.restype = [W.DWORD, ctypes.POINTER(IconData)], W.BOOL
        instance = kernel.GetModuleHandleW(None)
        restart = user.RegisterWindowMessageW('TaskbarCreated')
        data = IconData()
        def popup():
            menu = user.CreatePopupMenu()
            try:
                for index, label in ((1, '显示窗口'), (2, '连接'), (3, '停止连接'), (4, '退出')):
                    user.AppendMenuW(menu, 0, index, label)
                point = W.POINT()
                user.GetCursorPos(ctypes.byref(point))
                user.SetForegroundWindow(self.hwnd)
                selected = user.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, self.hwnd, None)
                if selected:
                    self.emit({1: 'tray-show', 2: 'tray-connect', 3: 'tray-stop', 4: 'tray-exit'}[selected])
                user.PostMessageW(self.hwnd, 0, 0, 0)
            finally:
                user.DestroyMenu(menu)
        @callback
        def handle(hwnd, message, wparam, lparam):
            if message == 0x8002:
                self.emit('tray-show')
                return 0
            if message == 0x8001:
                if lparam in (0x0202, 0x0203):
                    self.emit('tray-show')
                elif lparam == 0x0205:
                    popup()
                return 0
            if message == restart:
                shell.Shell_NotifyIconW(0, ctypes.byref(data))
                return 0
            if message == 0x0010:
                user.DestroyWindow(hwnd)
                return 0
            if message == 0x0002:
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(hwnd, message, wparam, lparam)
        name = 'CodexMobileTray-' + self.key
        window_class = WindowClass(0, handle, 0, 0, instance, None, None, None, None, name)
        if not user.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        icon = user.LoadImageW(None, self.icon_path, 1, 0, 0, 0x10 | 0x40)
        try:
            self.hwnd = user.CreateWindowExW(0, name, '', 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd or not icon:
                raise ctypes.WinError(ctypes.get_last_error())
            data.size, data.window, data.id, data.flags, data.message, data.icon = ctypes.sizeof(data), self.hwnd, 1, 7, 0x8001, icon
            data.tip = 'Codex 手机助手 · 点击打开'
            if not shell.Shell_NotifyIconW(0, ctypes.byref(data)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            self.emit('tray-ready')
            message = W.MSG()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            self.ready.clear()
            shell.Shell_NotifyIconW(2, ctypes.byref(data))
            if self.hwnd:
                user.DestroyWindow(self.hwnd)
                self.hwnd = None
            if icon:
                user.DestroyIcon(icon)
            user.UnregisterClassW(name, instance)
