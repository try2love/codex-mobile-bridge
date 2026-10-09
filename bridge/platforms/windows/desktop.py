"""Windows process inventory and graceful desktop lifecycle primitives."""
import json
import os
import subprocess


def _rows(commands=False):
    fields = 'ProcessId,ExecutablePath' + (',CommandLine' if commands else '')
    script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $s=(Get-Process -Id $PID).SessionId; '
              'Get-CimInstance Win32_Process | Where-Object {$_.SessionId -eq $s -and $_.ExecutablePath} | '
              'Select-Object '+fields+' | ConvertTo-Json -Compress')
    response = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                              capture_output=True, text=True, encoding='utf-8', timeout=15,
                              creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    rows = json.loads(response.stdout or '[]')
    return [rows] if isinstance(rows, dict) else rows


def inventory(result, match):
    for row in _rows(commands=True):
        state = match(row['ExecutablePath'])
        if state is not None:
            pid = int(row['ProcessId']); state['pids'].append(pid)
            state['commands'][pid] = row.get('CommandLine') or ''


def processes(matches):
    return [int(row['ProcessId']) for row in _rows() if matches(row['ExecutablePath'])]


def commands(pids):
    script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); '
              '$s=(Get-Process -Id $PID).SessionId; Get-CimInstance Win32_Process | '
              'Where-Object {$_.SessionId -eq $s} | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', check=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    rows = json.loads(result.stdout or '[]')
    if isinstance(rows, dict): rows = [rows]
    return {int(row['ProcessId']): row.get('CommandLine') or '' for row in rows if int(row['ProcessId']) in pids}


def quit_process(pid):
    script = '$p=Get-Process -Id ([int]$env:CMB_GUI_PID) -ErrorAction Stop; if (-not $p.CloseMainWindow()) {exit 2}'
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   env={**os.environ, 'CMB_GUI_PID': str(pid)}, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)


def terminate(pid):
    subprocess.run(['taskkill.exe', '/PID', str(pid)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)


def start(executable, home, environment):
    return subprocess.Popen([str(executable)], cwd=home,
                            env={**os.environ, **environment},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
