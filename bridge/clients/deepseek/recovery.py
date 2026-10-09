"""Desktop-only recovery of verified Harness processes, with an explicit preview."""
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from bridge.clients.lifecycle import _app, _commands, inspect_client
from bridge.clients.deepseek.adapter import BRIDGE_REVISION
from bridge.features.accounts.models import NoRedirect

CHANGED = 'Harness 进程已变化，请重新检查后再确认恢复'
BUSY = '有任务运行或等待确认，请先结束任务再关闭或重启客户端'


def _identities(pids):
    if not pids:
        return {}
    if sys.platform == 'win32':
        script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); '
                  'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate | ConvertTo-Json -Compress')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, encoding='utf-8', check=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        rows = json.loads(result.stdout or '[]')
        if isinstance(rows, dict): rows = [rows]
        return {int(row['ProcessId']): {'parent': int(row['ParentProcessId']), 'start': str(row['CreationDate'])}
                for row in rows if int(row['ProcessId']) in pids and row.get('CreationDate')}
    if sys.platform == 'linux':
        rows = {}
        for pid in pids:
            try:
                fields = (Path('/proc')/str(pid)/'stat').read_text().rsplit(')', 1)[1].split()
                rows[pid] = {'parent': int(fields[1]), 'start': fields[19]}
            except FileNotFoundError:
                continue
        return rows
    result = subprocess.run(['ps', '-ww', '-p', ','.join(map(str, pids)), '-o', 'pid=,ppid=,lstart='],
                            capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1): result.check_returncode()
    return {int(parts[0]): {'parent': int(parts[1]), 'start': ' '.join(parts[2:])}
            for line in result.stdout.splitlines() if len(parts := line.split()) == 7}


def _snapshot(descriptor):
    state = inspect_client(descriptor)
    app = _app(descriptor)
    identities = _identities(state['pids'])
    commands = _commands(app, state['pids'])
    if set(identities) != set(state['pids']) or set(commands) != set(state['pids']):
        raise ValueError(CHANGED)
    for pid, row in identities.items():
        row['command'] = hashlib.sha256(commands[pid].encode()).hexdigest()
    # A profile-mismatched Host or unknown executable mode must never be killed.
    allowed = set(state['mainPids']) | set(state['runtimePids'])
    unknown = [pid for pid in state['pids'] if pid not in allowed and
               not re.search(r'(?:^|\s)--type(?:=|\s)', commands[pid])]
    return {'executable': str(app.executable), 'home': str(app.home), 'state': state,
            'identities': identities, 'unrecognized': unknown}


def _ports(pid):
    if sys.platform == 'win32':
        script = ('Get-NetTCPConnection -State Listen -OwningProcess '+str(int(pid))+
                  ' -ErrorAction SilentlyContinue | Where-Object {$_.LocalAddress -eq "127.0.0.1"} | Select-Object -ExpandProperty LocalPort')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        return [int(port) for port in result.stdout.split() if port.isdigit() and 0 < int(port) < 65536]
    if sys.platform == 'linux':
        inodes = set()
        for fd in (Path('/proc')/str(pid)/'fd').iterdir():
            try:
                match = re.fullmatch(r'socket:\[(\d+)\]', os.readlink(fd))
                if match: inodes.add(match[1])
            except FileNotFoundError:
                pass
        ports = []
        for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 9 and fields[3] == '0A' and fields[9] in inodes:
                host, port = fields[1].split(':')
                if host == '0100007F': ports.append(int(port, 16))
        return ports
    result = subprocess.run(['lsof', '-nP', '-a', '-p', str(pid), '-iTCP', '-sTCP:LISTEN', '-Fn'],
                            capture_output=True, text=True, timeout=10)
    return [int(line.rsplit(':', 1)[1]) for line in result.stdout.splitlines()
            if re.fullmatch(r'n127\.0\.0\.1:\d+', line)]


def _query(adapter, port, action):
    config = json.loads((adapter.directory/'connection.json').read_bytes())
    request = urllib.request.Request('http://127.0.0.1:'+str(port)+'/mobile',
        data=json.dumps({'action': action, 'sid': None, 'body': {}}).encode(),
        headers={'Authorization': 'Bearer '+config['token'], 'Content-Type': 'application/json'})
    # The process owning this port was verified locally. Never use an HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=3) as response:
        raw = response.read(1024*1024+1)
    if len(raw) > 1024*1024: raise ValueError('oversized lifecycle response')
    value = json.loads(raw)
    if not isinstance(value, dict): raise ValueError('invalid lifecycle response')
    return value


def _evidence(adapter, snapshot):
    state = snapshot['state']
    busy, unknown, verified = False, False, []
    for pid in state['runtimePids']:
        found = False
        try:
            ports = _ports(pid)
        except (OSError, ValueError, subprocess.SubprocessError):
            ports = []
        for port in ports:
            try:
                status = _query(adapter, port, 'status')
                if status.get('connected') is not True: continue
                found = True
                try:
                    detail = _query(adapter, port, 'lifecycle')
                except (OSError, ValueError):
                    # Legacy lists can prove work is active, but cannot prove idle:
                    # they may omit child agents. Inactive contexts stay unknown.
                    detail = _query(adapter, port, 'list')
                rows = detail.get('sessions')
                if not isinstance(rows, list):
                    unknown = True
                    continue
                busy = busy or any(not isinstance(row, dict) or row.get('requests') or
                    row.get('status') in ('active', 'running', 'waiting', 'busy') for row in rows)
                complete = (detail.get('bridgeRevision') == BRIDGE_REVISION and detail.get('complete') is True and
                    all(isinstance(row, dict) and row.get('runtimeKnown') is True and
                        row.get('status') in ('idle', 'stopped', 'completed') and not row.get('requests') for row in rows))
                unknown = unknown or not complete
                if complete: verified.append(pid)
                break
            except (OSError, ValueError, KeyError):
                if found: unknown = True
                continue
        if not found: unknown = True
    if state['mainPids'] and not state['runtimePids']: unknown = True
    return {'busy': bool(busy), 'unknown': bool(unknown), 'verifiedIdle': verified}


def _same_processes(descriptor, expected):
    current = _snapshot(descriptor)
    if (current['executable'], current['home'], current['identities']) != (
            expected['executable'], expected['home'], expected['identities']):
        raise ValueError(CHANGED)
    return current


def _remaining_processes(current, expected, *, gui_allowed=False):
    if current['state']['mainPids'] and not gui_allowed:
        raise ValueError(CHANGED)
    for pid, identity in current['identities'].items():
        old = expected['identities'].get(pid)
        if old is None or any(identity[key] != old[key] for key in ('start', 'command')):
            raise ValueError(CHANGED)


def _quitting_snapshot(descriptor, expected, *, gui_allowed=False):
    """Allow only confirmed exits while sampling the already-approved processes."""
    app = _app(descriptor)
    if (str(app.executable), str(app.home)) != (expected['executable'], expected['home']):
        raise ValueError(CHANGED)
    remaining = set(expected['identities'])
    state = inspect_client(descriptor)
    # Every retry must lose at least one approved PID, so this is bounded even
    # when several children exit between inventory, identity and command reads.
    for _ in range(len(remaining)+1):
        pids = set(state['pids'])
        if not pids <= remaining or (state['mainPids'] and not gui_allowed):
            raise ValueError(CHANGED)
        identities = _identities(state['pids'])
        commands = _commands(app, state['pids'])
        if not set(identities) <= pids or not set(commands) <= pids:
            raise ValueError(CHANGED)
        for pid, identity in identities.items():
            if identity.get('start') != expected['identities'][pid]['start']:
                raise ValueError(CHANGED)
        for pid, command in commands.items():
            if command and hashlib.sha256(command.encode()).hexdigest() != expected['identities'][pid]['command']:
                raise ValueError(CHANGED)
        missing = pids - (set(identities) & {pid for pid, command in commands.items() if command})
        if not missing:
            for pid, identity in identities.items():
                identity['command'] = expected['identities'][pid]['command']
            allowed = set(state['mainPids']) | set(state['runtimePids'])
            unknown = [pid for pid in state['pids'] if pid not in allowed and
                       not re.search(r'(?:^|\s)--type(?:=|\s)', commands[pid])]
            return {'executable': str(app.executable), 'home': str(app.home), 'state': state,
                    'identities': identities, 'unrecognized': unknown}
        state = inspect_client(descriptor)
        fresh = set(state['pids'])
        # Missing metadata is not proof of exit. Refuse a still-live PID, new
        # PID, or any identity change observed before the next coherent sample.
        if missing & fresh or not fresh < pids:
            raise ValueError(CHANGED)
        remaining = fresh
    raise ValueError(CHANGED)


def _quit(descriptor, expected):
    """Quit the GUI first, then terminate only still-matching approved Hosts."""
    current = _same_processes(descriptor, expected)
    app = _app(descriptor)
    main = current['state']['mainPids']
    if main and sys.platform == 'darwin':
        bundle = next((p for p in app.executable.parents if p.suffix == '.app'), None)
        if bundle is None: raise ValueError('DeepSeek Harness 桌面程序必须位于应用程序包中')
        subprocess.run(['osascript', '-e', 'on run argv\n tell application (item 1 of argv) to quit\nend run', str(bundle)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
    else:
        for pid in main:
            if sys.platform == 'win32':
                script = '$p=Get-Process -Id ([int]$env:CMB_GUI_PID) -ErrorAction Stop; if (-not $p.CloseMainWindow()) {exit 2}'
                subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                    env={**os.environ, 'CMB_GUI_PID': str(pid)}, check=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic()+15
    while True:
        current = _quitting_snapshot(descriptor, expected, gui_allowed=True)
        # Parent IDs can change when the GUI exits; PID/start/command must not.
        _remaining_processes(current, expected, gui_allowed=True)
        if not current['state']['mainPids']: break
        if time.monotonic() >= deadline:
            raise ValueError('Harness 尚未退出，请在客户端处理退出提示后重试；未终止后台任务')
        time.sleep(.2)
    for pid in current['state']['runtimePids']:
        check = _quitting_snapshot(descriptor, expected)
        _remaining_processes(check, expected)
        identity = check['identities'].get(pid)
        if identity is None: continue
        old = expected['identities'][pid]
        if any(identity[key] != old[key] for key in ('start', 'command')): raise ValueError(CHANGED)
        if sys.platform == 'win32':
            subprocess.run(['taskkill.exe', '/PID', str(pid)], check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try: os.kill(pid, signal.SIGTERM)
            except ProcessLookupError: pass
    deadline = time.monotonic()+15
    while inspect_client(descriptor)['running']:
        if time.monotonic() >= deadline:
            raise ValueError('Harness 后台尚未退出，请在电脑端检查；未强制结束进程')
        time.sleep(.2)


class DeepSeekRecovery:
    def __init__(self):
        self.pending = None

    def preview(self, descriptor, adapter, restart=True):
        if type(restart) is not bool: raise ValueError('Harness 恢复选项无效')
        # This validates the native profile and allowlisted connector before any probe.
        adapter.discover_existing()
        snapshot = _snapshot(descriptor)
        evidence = _evidence(adapter, snapshot)
        state = snapshot['state']
        allowed = not snapshot['unrecognized'] and len(state['mainPids']) <= 1
        token = secrets.token_hex(24)
        self.pending = {'token': token, 'expires': time.monotonic()+300, 'snapshot': snapshot,
                        'restart': restart, 'allowed': allowed, 'unknown': evidence['unknown']}
        return {'token': token, 'expiresAt': time.time()+300, 'restart': restart,
                'guiCount': len(state['mainPids']), 'backgroundCount': len(state['runtimePids']),
                'busy': evidence['busy'], 'unknown': evidence['unknown'],
                'requiresUnknownConfirmation': evidence['unknown'], 'canRecover': allowed and not evidence['busy']}

    def confirm(self, descriptor, adapter, value):
        pending = self.pending
        if (not pending or not isinstance(value.get('token'), str) or
                not secrets.compare_digest(value['token'], pending['token']) or time.monotonic() >= pending['expires']):
            raise ValueError('Harness 恢复确认已过期，请重新检查')
        if value.get('confirmed') is not True: raise ValueError('请确认完整退出 Harness')
        snapshot = _same_processes(descriptor, pending['snapshot'])
        adapter.discover_existing()
        evidence = _evidence(adapter, snapshot)
        if evidence['busy']: raise ValueError(BUSY)
        if not pending['allowed']: raise ValueError('检测到其他或无法识别的 Harness 实例，请先在电脑端退出这些实例')
        if evidence['unknown'] and (not pending['unknown'] or value.get('acknowledgeUnknown') is not True):
            raise ValueError('无法核验 Harness 任务状态，请重新检查并明确确认可能中断任务')
        self.pending = None
        _quit(descriptor, snapshot)
        return pending['restart']
