"""Windows Claude main-window and visible Console helper operations."""
import json
import re
import subprocess
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


def helper_command(helper, action, pid, executable, script, cancel_path):
    if action in ('check', 'request-permission'):
        return None
    args = [str(helper), str(pid), str(script) if action == 'connect' else '--'+action, str(executable)]
    if cancel_path:
        args.append(str(cancel_path))
    return args, {'creationflags': subprocess.CREATE_NO_WINDOW}


def helper_result(action, stdout, returncode):
    if action == 'inspect-error':
        return {'setupState': 'needs-trust' if 'NEEDS_TRUST' in stdout else 'failed',
                'reason': '请在 Claude Code 中确认连接目录信任，然后重新连接'}
    if returncode:
        message = stdout.strip().splitlines()[-1] if stdout.strip() else 'Claude 自动连接失败'
        return {'setupState': 'cancelled' if '已取消' in message else 'failed', 'reason': message[:300]}
    return {'setupState': 'needs-developer-mode' if action == 'enable-devtools' else 'submitted',
            'reason': '请确认 Claude 的开发者模式提示，完成后会继续连接' if action == 'enable-devtools'
                      else '连接脚本已输入，正在等待 Claude 确认'}
