"""macOS process inventory and Launch Services operations."""
import json
import os
import subprocess
from pathlib import Path

from bridge.platforms.posix.desktop import terminate


def _process_rows():
    output = subprocess.run(['ps', '-ww', '-u', str(os.getuid()), '-o', 'pid=,comm='],
                            capture_output=True, text=True, check=True, timeout=10).stdout
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2:
            yield int(fields[0]), fields[1]


def inventory(result, match):
    for pid, executable in _process_rows():
        state = match(executable)
        if state is not None:
            state['pids'].append(pid)
    pids = [pid for state in result.values() for pid in state['pids']]
    if pids:
        values = commands(pids)
        for state in result.values():
            state['commands'] = {pid: values[pid] for pid in state['pids'] if pid in values}


def processes(matches):
    return [pid for pid, executable in _process_rows() if matches(executable)]


def commands(pids):
    result = subprocess.run(['ps', '-ww', '-p', ','.join(map(str, pids)), '-o', 'pid=,args='],
                            capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return {int(parts[0]): parts[1] for line in result.stdout.splitlines()
            if len(parts := line.strip().split(None, 1)) == 2}


def _bundle(executable):
    bundle = next((p for p in executable.parents if p.suffix == '.app'), None)
    if bundle is None:
        raise ValueError('macOS 桌面程序必须位于应用程序包中')
    return bundle


_QUIT_SCRIPT = '''ObjC.import('AppKit');
function run(argv) {
  const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(Number(argv[0]));
  if (!app || app.isNil() || app.terminated) return JSON.stringify({state:'exited'});
  const url = app.executableURL;
  if (!url || url.isNil() || ObjC.unwrap(url.URLByResolvingSymlinksInPath.path) !== argv[1])
    return JSON.stringify({state:'changed'});
  return JSON.stringify({state:app.terminate ? 'submitted' : 'rejected'});
}'''


def quit_application(executable, *, pids=None):
    """Request normal exit from the verified running instance, never by app name.

    AppKit's terminate is cooperative and asynchronous, not a process signal.
    The shared caller still waits for actual exit and respects cancellation.
    """
    executable = Path(executable).resolve()
    _bundle(executable)
    if pids is None:
        pids = processes(lambda path: Path(path).resolve() == executable)
    if not pids:
        return 'exited'
    if len(pids) != 1 or type(pids[0]) is not int or pids[0] <= 0:
        raise ValueError('客户端进程已变化，请重新检查后再关闭（quit: changed）')
    try:
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', _QUIT_SCRIPT,
                                 str(pids[0]), str(executable)],
                                capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        raise ValueError('macOS 退出请求结果未知，请检查客户端状态；尚未强制结束进程（quit: timeout）') from None
    except OSError:
        raise ValueError('无法发送 macOS 退出请求，请检查系统运行环境（quit: unavailable）') from None
    if result.returncode:
        # Do not return the native script, arguments or private stderr over HTTP.
        raise ValueError('macOS 退出请求失败，请检查客户端状态（quit: native-error; code: '+str(result.returncode)+'）')
    try:
        receipt = json.loads(result.stdout)
        state = receipt.get('state') if isinstance(receipt, dict) else None
    except (ValueError, TypeError):
        state = None
    if state in ('submitted', 'exited'):
        return state
    if state == 'changed':
        raise ValueError('客户端进程已变化，请重新检查后再关闭（quit: changed）')
    if state == 'rejected':
        raise ValueError('客户端未接受退出请求，请检查客户端提示；尚未强制结束进程（quit: rejected）')
    raise ValueError('无法核验 macOS 退出请求结果，请检查客户端状态（quit: invalid-response）')


def start(executable, home, environment):
    # Keep Launch Services' GUI privacy identity without activating the app or
    # stealing focus from the user's current app / screen saver. The client may
    # still present its own first-run, login or system permission prompts.
    args = ['/usr/bin/open', '-g', '-a', str(_bundle(executable))]
    for name, value in environment.items():
        args += ['--env', name+'='+str(value)]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
