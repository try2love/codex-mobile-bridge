"""Windows Claude main-window and visible Console helper operations."""
import json
import os
import re
import subprocess
import time
from . import session as windows_session
from .desktop import process_inventory
from pathlib import Path


def main_pids(app):
    # Electron renderer/GPU/utility children share the executable; only the
    # process without --type owns the native desktop window.
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


def close_main_window(pid):
    script = '$p=Get-Process -Id '+str(pid)+' -ErrorAction Stop; if (-not $p.CloseMainWindow()) {exit 2}'
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)


def native_action(action, *, helper, pid=None, executable=None, script=None, cancel_path=None, cancelled=None):
    if action not in ('check', 'request-permission', 'connect', 'connect-background', 'close-devtools', 'close-background-devtools', 'enable-devtools', 'inspect-error', 'quit'):
        raise ValueError('不支持的 Claude 本机操作')
    background = action == 'connect-background'
    def connection_result(reason, dispatched=False, state='needs-retry'):
        return {'setupState': state, 'submission': 'uncertain' if dispatched else 'none',
                'pid': pid, 'reason': reason}
    def connect_dispatched(output):
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        if not isinstance(output, str):
            return False
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (isinstance(value, dict) and value.get('connectPhase') == 'dispatching'
                    and type(value.get('pid')) is int and value['pid'] == pid):
                return True
        return False
    def quit_failed(reason):
        return {'quitState': 'failed', 'reason': reason, 'pid': pid}
    def quit_dispatched(output):
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        if not isinstance(output, str):
            return False
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (isinstance(value, dict) and value.get('quitPhase') == 'dispatching'
                    and type(value.get('pid')) is int and value['pid'] == pid):
                return True
        return False
    def quit_interrupted(reason, dispatched):
        if dispatched:
            return {'quitState': 'submitted', 'pid': pid,
                    'reason': 'Claude 退出命令回执中断，正在等待保存和退出完成'}
        return quit_failed(reason)
    if cancelled and cancelled.is_set():
        if action == 'quit':
            return quit_failed('已取消 Claude 退出')
        if background:
            return connection_result('已取消 Claude 连接', state='cancelled')
        return {'setupState': 'cancelled', 'reason': '已取消 Claude 连接'}
    def desktop_blocked():
        if action not in ('quit', 'connect-background', 'close-background-devtools'):
            try:
                windows_session.require_interactive()
            except windows_session.DesktopUnavailable as exc:
                return {'setupState': exc.setup_state, 'reason': str(exc)}
        return None
    blocked = desktop_blocked()
    if blocked:
        return blocked
    helper = Path(helper)
    if not helper.is_file():
        if action == 'quit':
            return quit_failed('缺少 Claude 原生退出组件，请重新构建或安装网关 App')
        if background:
            return connection_result('缺少 Claude 后台连接组件，请重新构建或安装网关 App', state='failed')
        return {'setupState': 'failed', 'reason': '缺少 Claude 自动连接组件，请重新构建或安装网关 App'}
    if action in ('check', 'request-permission'):
        return {'setupState': 'ready'}
    argument = '--connect-background='+str(script) if background else str(script) if action == 'connect' else '--'+action
    args = [str(helper), str(pid), argument, str(executable)]
    if cancel_path:
        args.append(str(cancel_path))
    options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **options)
    except OSError:
        if action == 'quit':
            return quit_failed('无法启动 Claude 原生退出组件，请检查网关安装')
        if background:
            return connection_result('无法启动 Claude 后台连接组件，请检查网关安装', state='failed')
        raise
    timeout = 30 if action in ('connect', 'connect-background', 'quit') else 20
    if action in ('inspect-error', 'close-devtools'):
        timeout = 8
    if action == 'close-background-devtools':
        timeout = 12
    deadline = time.monotonic() + timeout
    dispatched = False
    while True:
        try:
            stdout, _ = process.communicate(timeout=1 if action == 'wait-desktop' else .2)
            break
        except subprocess.TimeoutExpired as expired:
            if action == 'quit':
                dispatched = dispatched or quit_dispatched(expired.output)
            elif background:
                dispatched = dispatched or connect_dispatched(expired.output)
            blocked = desktop_blocked()
            if (cancelled and cancelled.is_set()) or blocked or time.monotonic() >= deadline:
                # Stop only our input helper, never the Claude desktop process.
                process.terminate()
                try: stdout, _ = process.communicate(timeout=3)
                except subprocess.TimeoutExpired as expired:
                    if action == 'quit':
                        dispatched = dispatched or quit_dispatched(expired.output)
                    elif background:
                        dispatched = dispatched or connect_dispatched(expired.output)
                    process.kill(); stdout, _ = process.communicate()
                if action == 'quit':
                    return quit_interrupted('已取消 Claude 退出' if cancelled and cancelled.is_set()
                                            else 'Claude 原生退出请求超时，请在电脑端检查退出状态',
                                            dispatched or quit_dispatched(stdout))
                if background:
                    return connection_result('已取消 Claude 连接' if cancelled and cancelled.is_set()
                                             else 'Claude 后台初始化回执超时，请等待连接状态或重试',
                                             dispatched or connect_dispatched(stdout),
                                             'cancelled' if cancelled and cancelled.is_set() else 'needs-retry')
                if blocked and not (cancelled and cancelled.is_set()):
                    return blocked
                return {'setupState': 'cancelled' if cancelled and cancelled.is_set() else 'needs-retry',
                        'reason': '已取消 Claude 连接' if cancelled and cancelled.is_set() else 'Claude 自动连接超时，请重试'}
    if background:
        dispatched = dispatched or connect_dispatched(stdout)
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
            states = ('submitted', 'needs-initialization', 'needs-developer-mode', 'needs-trust',
                      'needs-unlock', 'needs-desktop', 'needs-retry', 'failed', 'cancelled')
            if (not isinstance(value, dict) or value.get('setupState') not in states
                    or value.get('submission') not in ('none', 'submitted', 'uncertain')
                    or type(value.get('pid')) is not int or value['pid'] != pid
                    or not isinstance(value.get('reason'), str)
                    or value['setupState'] == 'submitted' and value['submission'] != 'submitted'):
                raise ValueError('invalid background connection result')
            if (dispatched and value['submission'] == 'none'
                    or process.returncode and value['submission'] != 'none'):
                return connection_result(value['reason'][:300], True,
                                         'cancelled' if value['setupState'] == 'cancelled' else 'needs-retry')
            return {key: value[key] for key in ('setupState', 'submission', 'pid')} | {'reason': value['reason'][:300]}
        except (ValueError, IndexError, AttributeError):
            # A malformed receipt cannot establish that no input was dispatched.
            return connection_result('Claude 后台初始化未返回有效回执，请等待实际连接状态', True)
    if action == 'quit':
        dispatched = dispatched or quit_dispatched(stdout)
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
            if (not isinstance(value, dict) or value.get('quitState') not in ('exited', 'pending', 'submitted', 'failed')
                    or type(value.get('pid')) is not int or value['pid'] != pid or not isinstance(value.get('reason'), str)
                    or (process.returncode and value['quitState'] != 'failed')):
                raise ValueError('invalid native quit result')
            if value['quitState'] == 'failed' and dispatched:
                return quit_interrupted(value['reason'], dispatched)
            return {'quitState': value['quitState'], 'pid': pid, 'reason': value['reason'][:300]}
        except (ValueError, IndexError, AttributeError):
            return quit_interrupted('Claude 原生退出组件未返回有效状态，请在电脑端检查退出状态', dispatched)
    if process.returncode:
        message = stdout.strip().splitlines()[-1] if stdout.strip() else 'Claude 自动连接失败'
        state = ('cancelled' if '已取消' in message else 'needs-unlock' if '锁定' in message
                 else 'needs-desktop' if '桌面不可用' in message else 'needs-retry')
        return {'setupState': state, 'reason': message[:300]}
    if action == 'inspect-error':
        return {'setupState': 'needs-trust' if 'NEEDS_TRUST' in stdout else 'failed',
                'reason': '请在 Claude Code 中确认连接目录信任，然后重新连接'}
    return {'setupState': 'needs-developer-mode' if action == 'enable-devtools' else 'submitted',
            'reason': '请确认 Claude 的开发者模式提示，完成后会继续连接' if action == 'enable-devtools'
                      else '连接脚本已输入，正在等待 Claude 确认'}



def running_profiles(executable):
    """Read the selected same-session process command lines for profile evidence."""
    selected = Path(executable).expanduser().resolve()
    state = process_inventory([selected]).get(selected, {})
    profiles = set()
    # Electron's children report their actual user-data-dir even when the
    # main process chose a Store-redirected or third-party profile itself.
    argument = re.compile(r'(?:^|\s)(?:"--user-data-dir=([^"\r\n]+)"|'
                          r'--user-data-dir(?:=|\s+)(?:"([^"\r\n]+)"|([^\s"]+)))', re.I)
    for command in state.get('commands', {}).values():
        for match in argument.finditer(command):
            path = Path(next(value for value in match.groups() if value is not None))
            if path.is_absolute():
                profiles.add(path.resolve())
    return profiles


def windows_claude_profile_paths(executable, fallback, *, env=None, package_family=None):
    """Known profiles for this user and selected installation, never arbitrary homes.

    Missing canonical paths are retained for account operations, but a custom
    fallback is not paired with another user's or installation's configuration.
    """
    env = os.environ if env is None else env
    if not env.get('APPDATA') or not env.get('LOCALAPPDATA'):
        return {}
    roaming, local = Path(env['APPDATA']).resolve(), Path(env['LOCALAPPDATA']).resolve()
    family = package_family if re.fullmatch(r'Claude_[a-z0-9]+', package_family or '', re.I) else None
    if not family and executable:
        for folder in Path(executable).resolve().parents:
            match = re.fullmatch(r'Claude_\d+(?:\.\d+){3}_(?:x64|x86|arm64|neutral)_[^_]*_([a-z0-9]+)', folder.name, re.I)
            if folder.parent.name.casefold() == 'windowsapps' and match:
                family = 'Claude_' + match[1]
                break
    official = [local/'Claude-Data']
    if family:
        official.append(local/'Packages'/family/'LocalCache/Roaming/Claude')
    official.append(roaming/'Claude')
    thirdparty = [local/'Claude-3p', roaming/'Claude-3p']
    if Path(fallback).resolve() not in (*official, *thirdparty):
        return {}
    return {'official': official, 'thirdparty': thirdparty}


def stopped_profile(executable, fallback, env, package_family):
    groups = windows_claude_profile_paths(executable, fallback, env=env, package_family=package_family)
    if not groups:
        return fallback
    candidates = groups['official'] + groups['thirdparty']
    modes = set()
    for path in candidates:
        try:
            # Only use a bounded mode selector. Never return credentials or
            # configuration values, and never create or modify native profiles.
            with (path/'claude_desktop_config.json').open('rb') as stream:
                content = stream.read(64 * 1024 + 1)
            if len(content) <= 64 * 1024:
                value = json.loads(content).get('deploymentMode')
                if value in ('1p', '3p'):
                    modes.add(value)
        except (OSError, ValueError, AttributeError):
            pass
    if len(modes) > 1:
        return fallback
    if modes:
        candidates = groups['thirdparty' if '3p' in modes else 'official']
    markers = ('Local State', 'Preferences', 'Network/Cookies', 'Cookies',
               'config.json', 'claude_desktop_config.json', 'developer_settings.json')
    populated = [path for path in candidates if any((path/marker).is_file() for marker in markers)]
    return populated[0] if len(populated) == 1 else fallback
