"""macOS process inventory and Launch Services operations."""
import os
import subprocess

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


def quit_application(executable):
    script = 'on run argv\n tell application (item 1 of argv) to quit\nend run'
    subprocess.run(['osascript', '-e', script, str(_bundle(executable))], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)


def start(executable, home, environment):
    # Launch as a GUI app, not as a child inheriting the gateway's privacy identity.
    args = ['/usr/bin/open', '-a', str(_bundle(executable))]
    for name, value in environment.items():
        args += ['--env', name+'='+str(value)]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
