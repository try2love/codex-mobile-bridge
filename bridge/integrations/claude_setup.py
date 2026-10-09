"""Claude discovery and visible native Console bootstrap, independent of CDP."""
import json
import plistlib
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

from ..desktop_app import DesktopApp


def _archive_members(path):
    """Read only the bounded Electron package metadata and main startup script."""
    with path.open('rb') as stream:
        header = stream.read(16)
        if len(header) != 16:
            raise ValueError('incomplete archive header')
        _, size, _, json_size = struct.unpack('<4I', header)
        if not 0 < json_size <= 8 * 1024 * 1024 or size < json_size + 8:
            raise ValueError('invalid archive header')
        tree = json.loads(stream.read(json_size))
        base = size + 8
        for name in ('package.json', '.vite/build/index.pre.js'):
            node = tree
            for part in name.split('/'):
                node = node.get('files', {}).get(part, {})
            length = node.get('size', 0)
            if ('offset' not in node or node.get('unpacked') or
                    not isinstance(length, int) or not 0 < length <= 32 * 1024 * 1024):
                continue
            offset = int(node['offset'])
            if offset < 0:
                raise ValueError('invalid archive offset')
            stream.seek(base + offset)
            yield name, stream.read(length).decode('utf-8', errors='replace')


def inspect_installation(executable):
    """Return capability evidence without launching Claude or reading accounts."""
    if not executable:
        return {'installed': False, 'automaticConnection': 'unavailable',
                'setupState': 'not-installed', 'reason': '未检测到 Claude Desktop'}
    path = Path(executable).expanduser()
    bundle = next((p for p in (path, *path.parents) if p.suffix == '.app'), None)
    version = ''
    if bundle:
        try:
            info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
            name = info['CFBundleExecutable']
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError('invalid executable')
            path = bundle/'Contents/MacOS'/name
            version = str(info.get('CFBundleShortVersionString') or '')
        except (OSError, ValueError, KeyError):
            pass
    if not path.is_file():
        return {'installed': False, 'automaticConnection': 'unavailable',
                'setupState': 'not-installed', 'reason': '未检测到 Claude Desktop'}
    archive = (bundle/'Contents/Resources/app.asar' if bundle
               else path.parent/'resources/app.asar')
    try:
        for name, content in _archive_members(archive):
            if name == 'package.json' and not version:
                version = str(json.loads(content).get('version') or '')
    except (OSError, ValueError, TypeError, AttributeError, struct.error):
        # Unknown packaging is unknown capability, never assumed compatible.
        pass
    return {'installed': True, 'executable': str(path.resolve()), 'version': version,
            'automaticConnection': 'native-console' if sys.platform in ('darwin', 'win32') else 'unavailable',
            'setupState': 'ready-to-connect' if sys.platform in ('darwin', 'win32') else 'unsupported',
            'reason': ('已发现 Claude Desktop，可以自动连接' if sys.platform in ('darwin', 'win32')
                       else '此系统暂未提供 Claude 自动连接组件')}


def helper_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]/'dist'))
    return root/'client-helpers'/('claude-bridge-helper.exe' if sys.platform == 'win32' else 'claude-bridge-helper')


def _running_profiles(executable, platform):
    """Locate the selected macOS app's open Chromium profile, without reading it.

    Claude can select Claude-3p internally, so installation paths and another
    profile's developer settings do not establish its active data directory.
    """
    if platform != 'darwin':
        return set()
    app = DesktopApp(executable, Path.home())
    pids = app.processes()
    if not pids:
        return set()
    result = subprocess.run(['/usr/sbin/lsof', '-a', '-p', ','.join(map(str, pids)), '-Fn'],
                            capture_output=True, text=True, check=True, timeout=10)
    profiles = set()
    markers = ('Local Storage/leveldb/LOCK', 'Session Storage/LOCK',
               'Network/Cookies', 'Network/Network Persistent State',
               'Cookies', 'Network Persistent State')
    for line in result.stdout.splitlines():
        if not line.startswith('n/'):
            continue
        path = Path(line[1:])
        for marker in markers:
            parts = Path(marker).parts
            if path.parts[-len(parts):] == parts:
                profiles.add(path.parents[len(parts) - 1].resolve())
                break
    return profiles


def claude_data_home(executable, fallback, platform=None):
    """Prefer unique running-profile evidence; keep the platform default otherwise."""
    try:
        profiles = _running_profiles(executable, platform or sys.platform)
        if len(profiles) == 1:
            return next(iter(profiles))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return Path(fallback)


def developer_mode_enabled(data_home):
    try:
        return json.loads((Path(data_home)/'developer_settings.json').read_text(encoding='utf-8-sig')).get('allowDevTools') is True
    except (OSError, ValueError, AttributeError):
        return False


def console_source(source, config):
    """Normalize our controlled, semicolon-terminated IIFE before adding data.

    Only standalone comment lines are removed. The connector has no template
    literals or inline line comments; parity tests run this exact transformation.
    Configuration is inserted afterwards so path/string whitespace is untouched.
    """
    lines, block = [], False
    for line in source.splitlines():
        line = line.strip()
        if line.startswith('/*'):
            block = True
        if block:
            if '*/' in line:
                block = False
            continue
        if not line or line.startswith('//'):
            continue
        if '`' in line:
            raise ValueError('连接脚本格式不支持，请重新构建应用')
        lines.append(line)
    script = '/* codex bridge connector */' + ' '.join(lines)
    return script.replace('__BRIDGE_CONFIG__', json.dumps(config, ensure_ascii=True))


def native_action(action, *, pid=None, executable=None, script=None, cancel_path=None, cancelled=None, recovered=None):
    if cancelled and cancelled.is_set():
        return {'setupState': 'cancelled', 'reason': '已取消 Claude 连接'}
    helper = helper_path()
    if not helper.is_file():
        return {'setupState': 'failed', 'reason': '缺少 Claude 自动连接组件，请重新构建或安装网关 App'}
    if sys.platform == 'win32':
        if action in ('check', 'request-permission'):
            return {'setupState': 'ready'}
        args = [str(helper), str(pid), str(script) if action == 'connect' else '--'+action, str(executable)]
        if cancel_path:
            args.append(str(cancel_path))
    else:
        args = [str(helper), '--'+action]
        if pid is not None:
            args += [str(pid), str(executable)]
        if action == 'connect':
            args += [str(script), str(cancel_path)]
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **options)
    # One read-only helper waits through screen saver/lock instead of repeatedly
    # activating Claude or launching a fresh helper on every poll.
    deadline = None if action == 'wait-desktop' else time.monotonic() + (180 if action == 'connect' else 20)
    while True:
        try:
            stdout, _ = process.communicate(timeout=1 if action == 'wait-desktop' else .2)
            break
        except subprocess.TimeoutExpired:
            connected = bool(action == 'wait-desktop' and recovered and recovered())
            if connected or (cancelled and cancelled.is_set()) or (deadline is not None and time.monotonic() >= deadline):
                # Stop only our input helper, never the Claude desktop process.
                process.terminate()
                try: process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.communicate()
                if connected and not (cancelled and cancelled.is_set()):
                    return {'setupState': 'connected', 'reason': '桌面连接可用'}
                return {'setupState': 'cancelled' if cancelled and cancelled.is_set() else 'failed',
                        'reason': '已取消 Claude 连接' if cancelled and cancelled.is_set() else 'Claude 自动连接超时，请重试'}
    if sys.platform != 'win32':
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
            return value
        except (ValueError, IndexError, AttributeError):
            return {'setupState': 'failed', 'reason': 'Claude 连接组件未返回有效状态'}
    if action == 'inspect-error':
        return {'setupState': 'needs-trust' if 'NEEDS_TRUST' in stdout else 'failed',
                'reason': '请在 Claude Code 中确认连接目录信任，然后重新连接'}
    if process.returncode:
        message = stdout.strip().splitlines()[-1] if stdout.strip() else 'Claude 自动连接失败'
        return {'setupState': 'cancelled' if '已取消' in message else 'failed', 'reason': message[:300]}
    return {'setupState': 'needs-developer-mode' if action == 'enable-devtools' else 'submitted',
            'reason': '请确认 Claude 的开发者模式提示，完成后会继续连接' if action == 'enable-devtools'
                      else '连接脚本已输入，正在等待 Claude 确认'}


def _main_pids(app):
    if sys.platform != 'win32':
        return app.processes()
    # Electron uses the same executable for renderer/GPU/utility children on
    # Windows. Only its process without --type is the native desktop owner.
    script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $s=(Get-Process -Id $PID).SessionId; '
              'Get-CimInstance Win32_Process | Where-Object {$_.SessionId -eq $s -and $_.ExecutablePath} | '
              'Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    rows = json.loads(result.stdout or '[]')
    if isinstance(rows, dict): rows = [rows]
    return [int(row['ProcessId']) for row in rows
            if str(Path(row['ExecutablePath']).resolve()).casefold() == str(app.executable).casefold()
            and not re.search(r'(?:^|\s)--type(?:=|\s)', row.get('CommandLine') or '')]


def running_app(executable, data_home, restart=False, cancelled=None, allow_launch=True):
    if cancelled and cancelled.is_set():
        raise ValueError('已取消 Claude 连接')
    app = DesktopApp(executable, data_home)
    pids = _main_pids(app)
    if cancelled and cancelled.is_set():
        raise ValueError('已取消 Claude 连接')
    if not allow_launch and (not pids or restart):
        raise ValueError('请先在电脑端打开 Claude，再重新连接')
    if pids and restart:
        if len(pids) != 1:
            raise ValueError('无法确认唯一的 Claude 主进程，请检查桌面窗口')
        if sys.platform == 'win32':
            script = '$p=Get-Process -Id '+str(pids[0])+' -ErrorAction Stop; if (-not $p.CloseMainWindow()) {exit 2}'
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 25
            while _main_pids(app):
                if cancelled and cancelled.is_set(): raise ValueError('已取消 Claude 连接')
                if time.monotonic() >= deadline:
                    raise ValueError('Claude 尚未退出，请在电脑上关闭后重试；尚未强制结束进程')
                time.sleep(.25)
        else:
            app.stop()
        pids = []
    if not pids:
        if cancelled and cancelled.is_set():
            raise ValueError('已取消 Claude 连接')
        if sys.platform == 'darwin':
            bundle = next((p for p in app.executable.parents if p.suffix == '.app'), None)
            if bundle is None:
                raise ValueError('Claude 桌面程序必须位于应用程序包中')
            subprocess.run(['/usr/bin/open', '-a', str(bundle)], check=True, timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Native startup arguments only. Claude's user profile remains its own.
            subprocess.Popen([str(app.executable)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while not pids and time.monotonic() < deadline:
            if cancelled and cancelled.is_set():
                raise ValueError('已取消 Claude 连接')
            time.sleep(.25); pids = _main_pids(app)
    if len(pids) != 1:
        raise ValueError('无法确认唯一的 Claude 主进程，请检查桌面窗口')
    return pids[0]
