"""macOS Claude bundle, profile and visible Console helper operations."""
import json
import plistlib
import subprocess
from pathlib import Path


def installation_paths(path, bundle):
    version = ''
    try:
        info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        name = info['CFBundleExecutable']
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('invalid executable')
        path = bundle/'Contents/MacOS'/name
        version = str(info.get('CFBundleShortVersionString') or '')
    except (OSError, ValueError, KeyError):
        pass
    return path, bundle/'Contents/Resources/app.asar', version


def running_profiles(app):
    """Read open storage paths belonging only to the selected application."""
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


def start(executable):
    bundle = next((p for p in executable.parents if p.suffix == '.app'), None)
    if bundle is None:
        raise ValueError('Claude 桌面程序必须位于应用程序包中')
    subprocess.run(['/usr/bin/open', '-g', '-a', str(bundle)], check=True, timeout=20,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def helper_command(helper, action, pid, executable, script, cancel_path):
    args = [str(helper), '--'+action]
    if pid is not None:
        args += [str(pid), str(executable)]
    if action == 'connect':
        args += [str(script), str(cancel_path)]
    return args, {}


def helper_result(action, stdout, returncode):
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, AttributeError):
        return {'setupState': 'failed', 'reason': 'Claude 连接组件未返回有效状态'}
