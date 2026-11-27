"""Per-user ordinary GUI dispatch; no App files, accounts or tasks are changed."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path

from . import local_service
from .discovery_ext import application_user_model_id


KEY = 'CODEX_CLI_PATH'


def get_environment():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
        try:
            value, kind = winreg.QueryValueEx(key, KEY)
        except FileNotFoundError:
            return None
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
        raise ValueError('已有 Codex 启动环境无法核验')
    return os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value


def set_environment(value):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, KEY)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, KEY, 0, winreg.REG_SZ, str(value))
    send = ctypes.WinDLL('user32', use_last_error=True).SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    send.restype = ctypes.c_ssize_t
    result = ctypes.c_size_t()
    # Shell reloads user environment; do not restart Explorer or other apps.
    send(0xffff, 0x1a, 0, 'Environment', 2, 2000, ctypes.byref(result))


def owned_launcher(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    return path.name == 'codex-launcher.exe' and path.parent.parent == root/'gui'


def install(root, value, helper, command):
    root = local_service.directory(root)
    for previous in (get_environment(), os.environ.get(KEY)):
        if previous and not owned_launcher(previous, root):
            raise ValueError('已有其他 Codex 启动配置，保留原配置；可使用手动初始化连接')
    helper = Path(helper)
    raw = helper.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    gui = local_service.directory(root/'gui', create=True)
    generation = local_service.directory(gui/digest, create=True)
    launcher = generation/'codex-launcher.exe'
    if launcher.exists():
        handle = local_service._open(launcher)
        try:
            local_service._check(handle, directory=False)
        finally:
            local_service._native().close(handle)
        if launcher.read_bytes() != raw:
            raise ValueError('普通启动组件身份已变化')
    else:
        local_service.write_private(launcher, raw)
    application, cli = Path(value['application']), Path(value['cli'])
    try:
        relative = str(cli.relative_to(application.parent))
    except ValueError:
        relative = None  # The native App also uses its downloaded CLI cache.
    record = {**value, 'command': list(command), 'dispatcher': str(launcher),
              'cliRelative': relative, 'applicationUserModelId': application_user_model_id(application)}
    local_service.write_record(root/'ordinary-launch.json', record)
    set_environment(launcher)
    return str(launcher)


def bundled_cli(application, relative=None):
    root = Path(application).resolve().parent
    candidates = ([root/relative] if relative else []) + [
        root/'resources/codex.exe', root/'resources/codex-cli/bin/codex.exe']
    for path in candidates:
        resolved = path.resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            return str(resolved)
    raise ValueError('调用 App 的原生 Codex CLI 不可用')


def selected_application(application, record):
    application = Path(application).resolve()
    if os.path.normcase(str(application)) == record['application']:
        return True
    identity = record.get('applicationUserModelId')
    return bool(identity and application_user_model_id(application) == identity)
