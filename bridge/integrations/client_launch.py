"""Launch the discovered Harness desktop against its existing user data."""
import os
import subprocess
import sys
import time
from pathlib import Path

from ..desktop_app import DesktopApp, process_inventory


def launch_deepseek(descriptor, restart=False):
    """Never replace a running desktop during an automatic scan.

    Restart is a separate explicit desktop action. ``DesktopApp.stop`` waits for
    the exact GUI process to exit gracefully and never force-kills a process.
    Codex and Claude are deliberately outside this helper's scope.
    """
    if descriptor.get('id') != 'deepseek' or not descriptor.get('installed'):
        raise ValueError('未找到 DeepSeek Harness 桌面应用')
    executable, home = descriptor.get('executable'), descriptor.get('dataDirectory')
    if not executable or not home or not Path(executable).is_file() or (Path(home).exists() and not Path(home).is_dir()):
        raise ValueError('DeepSeek Harness 应用或数据目录不可用，请重新扫描')
    app = DesktopApp(executable, home)
    running = bool(app.processes())
    if running:
        state = inspect_client(descriptor)
        running = state['running']
        if running and not state['mainPids']:
            raise ValueError('检测到残留 Harness 后台实例且无法核对任务状态，请在电脑端退出 Harness 后重试')
        if state['unknown']:
            raise ValueError('检测到多个或无法识别的 Harness 实例，请在电脑端检查后重试')
    if running and not restart:
        return {'running': True, 'launched': False, 'restarted': False,
                'restartRequired': bool(descriptor.get('restartRequired'))}
    if running:
        app.stop()
    if sys.platform == 'darwin':
        bundle = next((p for p in app.executable.parents if p.suffix == '.app'), None)
        if bundle is None:
            raise ValueError('DeepSeek Harness 桌面程序必须位于应用程序包中')
        subprocess.run(['/usr/bin/open', '-a', str(bundle), '--env', 'DSH_HOME='+str(app.home)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
    else:
        options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP, 'close_fds': True}
                   if sys.platform == 'win32' else {'start_new_session': True})
        # On first launch Harness itself initializes its home and desktop profile.
        # Do not manufacture a second profile merely to give Popen a working dir.
        working = next((p for p in [app.home, *app.home.parents] if p.is_dir()), Path.home())
        app.launch(cwd=working, env={**os.environ, 'DSH_HOME': str(app.home)},
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    # GUI startup is asynchronous. Report observed process state, not a claim that
    # the adapter has connected or upstream authentication has succeeded.
    deadline = time.monotonic() + 2
    while not app.processes():
        if time.monotonic() >= deadline:
            return {'running': False, 'launched': True, 'restarted': running, 'restartRequired': False}
        time.sleep(.1)
    return {'running': True, 'launched': True, 'restarted': running, 'restartRequired': False}


def _app(descriptor):
    if descriptor.get('id') not in ('codex', 'claude', 'deepseek') or not descriptor.get('installed'):
        raise ValueError('未找到已配置的桌面应用，请重新扫描')
    executable, home = descriptor.get('executable'), descriptor.get('dataDirectory')
    if not executable or not home or not Path(executable).is_file():
        raise ValueError('桌面应用或数据目录不可用，请重新扫描')
    return DesktopApp(executable, home)


def _commands(app, pids):
    """Read command metadata only for same-user, exact-executable processes."""
    import json
    if not pids:
        return {}
    if sys.platform == 'win32':
        script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); '
                  '$s=(Get-Process -Id $PID).SessionId; Get-CimInstance Win32_Process | '
                  'Where-Object {$_.SessionId -eq $s} | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, encoding='utf-8', check=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        rows = json.loads(result.stdout or '[]')
        if isinstance(rows, dict): rows = [rows]
        return {int(row['ProcessId']): row.get('CommandLine') or '' for row in rows if int(row['ProcessId']) in pids}
    if sys.platform == 'linux':
        result = {}
        for pid in pids:
            try: result[pid] = (Path('/proc')/str(pid)/'cmdline').read_bytes().decode().replace('\0', ' ').strip()
            except FileNotFoundError: pass
        return result
    result = subprocess.run(['ps', '-ww', '-p', ','.join(map(str, pids)), '-o', 'pid=,args='],
                            capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return {int(parts[0]): parts[1] for line in result.stdout.splitlines()
            if len(parts := line.strip().split(None, 1)) == 2}


def inspect_client(descriptor):
    app = _app(descriptor)
    pids = app.processes()
    commands = _commands(app, pids)
    return _process_state(descriptor, app, pids, commands)


def inspect_clients(descriptors):
    """Batch display state only; no snapshot is retained for a later operation."""
    apps = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            continue
        try:
            app = _app(descriptor)
        except (ValueError, OSError):
            continue
        apps[descriptor['id']] = (descriptor, app)
    if not apps:
        return {}
    inventory = process_inventory(app.executable for _, app in apps.values())
    return {provider: _process_state(descriptor, app, **inventory[app.executable])
            for provider, (descriptor, app) in apps.items()}


def _process_state(descriptor, app, pids, commands):
    import re
    main, hosts = [], []
    unknown = any(not commands.get(pid) for pid in pids)
    for pid, command in commands.items():
        if (descriptor['id'] == 'deepseek' and sys.platform == 'win32' and
                re.search(r'(?:^|\s)--type(?:=|\s+)utility(?:$|\s)', command) and
                re.search(r'(?:^|\s)--utility-sub-type(?:=|\s+)node\.mojom\.NodeService(?:$|\s)', command)):
            # New DSH releases run the Host in Electron's Node utility process.
            # Its profile is passed over IPC, not on the command line. This is
            # only a candidate: quit still requires one Host, matching endpoint
            # PID and a complete idle snapshot from the selected profile.
            hosts.append(pid)
            continue
        if re.search(r'(?:^|\s)--type(?:=|\s)', command):
            continue  # Electron renderer/GPU/utility child, closed by its owner.
        if descriptor['id'] == 'deepseek' and '@deepseek-ai' in command and 'dsh-desktop-host' in command:
            profile = str(app.home/'profiles/desktop')
            # ps keeps spaces in arguments; require the whole expected path and
            # a path boundary rather than a substring of another user's profile.
            flags = re.IGNORECASE if sys.platform == 'win32' else 0
            if re.search(r'(?:^|[\s\"])'+re.escape(profile)+r'(?:$|[\s\"])', command, flags):
                hosts.append(pid)
            else:
                unknown = True
        elif '--expose-internals' in command or '--eval' in command or ' --run' in command:
            unknown = True
        else:
            main.append(pid)
    if len(main) > 1 or len(hosts) > 1:
        unknown = True
    return {'running': bool(pids), 'pids': pids, 'mainPids': main,
            'runtimePids': hosts, 'unknown': unknown}


def stop_client(descriptor, *, state):
    # Revalidate process identities immediately before sending native quit.
    current = inspect_client(descriptor)
    if current['unknown'] or set(current['pids']) - set(state['pids']):
        raise ValueError('客户端进程已变化，无法确认任务状态，请重新检查后再关闭')
    app = _app(descriptor)
    app.stop(runtime_pids=current['runtimePids'], gui_pids=current['mainPids'])


def stop_deepseek(descriptor, adapter, *, state):
    """Request DSH's native teardown; closing its window only hides the app."""
    import json
    if descriptor.get('id') != 'deepseek':
        raise ValueError('此退出操作仅用于 Harness 桌面端')
    changed = 'Harness 进程已变化，请重新检查后再退出'

    def verify():
        current = inspect_client(descriptor)
        if (current.get('unknown') or set(current['pids']) - set(state['pids']) or
                len(current.get('runtimePids', [])) != 1 or
                current['runtimePids'] != state.get('runtimePids')):
            raise ValueError(changed)
        return current

    current = verify()
    status = adapter.call('status')
    if status.get('connected') is not True:
        raise ValueError('Harness 桌面未连接，无法确认退出状态')
    if status.get('nativeQuit') is not True:
        raise ValueError('Harness 接入需要更新才能正常退出；请先在电脑端退出 Harness，再重新连接')
    try:
        endpoint = json.loads((adapter.directory/'endpoint.json').read_bytes())
        if (endpoint['pid'] != current['runtimePids'][0] or
                not isinstance(endpoint['generation'], str) or not endpoint['generation']):
            raise ValueError(changed)
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(changed) from None
    app = _app(descriptor)
    current = verify()
    result = adapter.call('quit', body={'expectedPid': endpoint['pid'],
                                      'expectedGeneration': endpoint['generation']})
    if (result.get('status') != 'accepted' or result.get('pid') != endpoint['pid'] or
            result.get('generation') != endpoint['generation']):
        raise ValueError('Harness 未确认退出请求，请在电脑端检查后重试')
    deadline = time.monotonic() + 25
    while remaining := app.processes():
        if set(remaining) - set(current['pids']):
            raise ValueError(changed)
        if time.monotonic() >= deadline:
            raise ValueError('Harness 尚未退出，请在电脑端处理退出提示后重试；尚未强制结束进程')
        time.sleep(.25)


def launch_client(descriptor):
    if descriptor.get('id') == 'deepseek':
        return launch_deepseek(descriptor)
    app = _app(descriptor)
    if app.processes():
        return {'running': True, 'launched': False}
    if descriptor['id'] == 'codex':
        app.start()
    else:
        # Claude owns its native profile selection, including third-party login.
        from .claude_setup import running_app
        running_app(str(app.executable), str(app.home))
    deadline = time.monotonic() + 3
    while not app.processes():
        if time.monotonic() >= deadline:
            return {'running': False, 'launched': True}
        time.sleep(.1)
    return {'running': True, 'launched': True}
