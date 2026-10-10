"""Verify DSH's own Windows installer handoff before requesting native quit."""
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path, PurePosixPath

UNSUPPORTED = '当前 Harness 未提供可验证的后台退出通道，请更新桌面端后重试'
PROFILE_CHANGED = 'Harness 运行实例的数据目录无法核对，请重新扫描后重试'
FLAG = '--dsh-installer-quit'
PRODUCTS = {'dsh-plugin-desktop': 'DSH Desktop', 'dsh-plugin-desktop-beta': 'DSH Desktop Beta'}


def _member(resources, name):
    parts = PurePosixPath(name.replace('\\', '/')).parts
    if not parts or any(part in ('..', '/') or ':' in part for part in parts):
        raise ValueError(UNSUPPORTED)
    folder = resources/'app'
    if (folder/'package.json').is_file():
        path = folder.joinpath(*parts).resolve()
        if not path.is_relative_to(folder.resolve()) or not 0 < path.stat().st_size <= 16*1024*1024:
            raise ValueError(UNSUPPORTED)
        return path.read_text('utf-8')
    with (resources/'app.asar').open('rb') as stream:
        header = stream.read(16)
        if len(header) != 16: raise ValueError(UNSUPPORTED)
        _, size, _, json_size = struct.unpack('<4I', header)
        if not 0 < json_size <= 8*1024*1024 or size < json_size+8:
            raise ValueError(UNSUPPORTED)
        node = json.loads(stream.read(json_size))
        for part in parts: node = node.get('files', {}).get(part, {})
        length, offset = node.get('size'), int(node.get('offset', -1))
        if node.get('unpacked') or node.get('link') or type(length) is not int or not 0 < length <= 16*1024*1024 or offset < 0:
            raise ValueError(UNSUPPORTED)
        stream.seek(size+8+offset)
        data = stream.read(length)
        if len(data) != length: raise ValueError(UNSUPPORTED)
        return data.decode('utf-8')


def installer_quit_product(executable):
    """Require the installed implementation, not a guessed version threshold.

    Both branches must return before showing a window or booting a profile.
    Unknown/minified implementations remain unsupported rather than receiving
    a flag they may ignore and interpret as a request to show the desktop.
    """
    try:
        resources = Path(executable).parent/'resources'
        package = json.loads(_member(resources, 'package.json'))
        product = PRODUCTS[package['name']]
        source = _member(resources, package['main'])
        predicate = re.search(r'''function\s+(\w+)\(\s*argv\s*,\s*platform\s*\)\s*\{\s*return\s+platform\s*===\s*["']win32["']\s*&&\s*argv\.includes\(["']--dsh-installer-quit["']\)\s*;?\s*\}''', source)
        if predicate is None: raise ValueError(UNSUPPORTED)
        check = re.escape(predicate[1])
        early = (r'async\s+function\s+start\(\)\s*\{\s*if\s*\(!app\.requestSingleInstanceLock\(\)\)\s*\{\s*app\.quit\(\);\s*return;\s*\}\s*'
                 r'if\s*\('+check+r'\(process\.argv,\s*process\.platform\)\)\s*\{\s*app\.quit\(\);\s*return;\s*\}')
        handoff = (r'''app\.on\(["']second-instance["'],\s*\(_event,\s*argv\)\s*=>\s*\{\s*if\s*\('''+check+
                   r'\(argv,\s*process\.platform\)\)\s*\{\s*requestQuit\(0\);\s*return;\s*\}')
        shutdown = r'const\s+requestQuit\s*=\s*\(code\)\s*=>\s*\{\s*shutdown\.request\(code\);\s*\}'
        if not all(re.search(pattern, source) for pattern in (early, handoff, shutdown)):
            raise ValueError(UNSUPPORTED)
        return product
    except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error):
        raise ValueError(UNSUPPORTED) from None


def _windows_argv(command):
    import ctypes
    if not command or sys.platform != 'win32': raise ValueError(PROFILE_CHANGED)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    arguments = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not arguments: raise ValueError(PROFILE_CHANGED)
    try: return [arguments[index] for index in range(count.value)]
    finally: kernel.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))


def _profile(arguments):
    profiles = []
    for index, argument in enumerate(arguments):
        if argument == '--user-data-dir':
            profiles.append(arguments[index+1] if index+1 < len(arguments) else '')
        elif argument.startswith('--user-data-dir='):
            profiles.append(argument.partition('=')[2])
    if len(profiles) > 1 or profiles and (not profiles[0] or not Path(profiles[0]).is_absolute()):
        raise ValueError(PROFILE_CHANGED)
    return Path(profiles[0]).resolve() if profiles else None


def installer_quit_command(app, state, commands):
    product = installer_quit_product(app.executable)
    main, hosts = state.get('mainPids', []), state.get('runtimePids', [])
    if len(main) != 1 or len(hosts) != 1: raise ValueError(PROFILE_CHANGED)
    arguments = [_windows_argv(commands.get(pid, '')) for pid in (main[0], hosts[0])]
    if any(not row or os.path.normcase(str(Path(row[0]).resolve())) != os.path.normcase(str(app.executable)) for row in arguments):
        raise ValueError(PROFILE_CHANGED)
    explicit, actual = map(_profile, arguments)
    roaming = os.environ.get('APPDATA')
    expected = explicit or (Path(roaming)/product).resolve() if roaming or explicit else None
    if actual is None or expected is None or os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
        raise ValueError(PROFILE_CHANGED)
    return [str(app.executable), *(['--user-data-dir='+str(explicit)] if explicit else []), FLAG]


def send_installer_quit(command):
    if sys.platform != 'win32': raise ValueError(UNSUPPORTED)
    environment = {key: value for key, value in os.environ.items() if key.upper() != 'ELECTRON_RUN_AS_NODE'}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    child = subprocess.Popen(command, cwd=Path(command[0]).parent, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup, close_fds=True)
    try:
        result = child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # This helper is itself a desktop executable; never force-kill it.
        raise ValueError('Harness 正常退出请求尚未完成，请稍后重试') from None
    if result != 0: raise ValueError('Harness 拒绝了正常退出请求，请在电脑端检查后重试')
