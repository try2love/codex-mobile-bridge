"""Launch the discovered Harness desktop against its existing user data."""
import os
import subprocess
import sys
import time
from pathlib import Path

from bridge.clients.desktop_app import DesktopApp, process_inventory
from bridge.platforms import desktop as native_desktop

def launch_deepseek(descriptor, restart=False):
    """Never replace a running desktop during an automatic scan.

    Restart is a separate explicit desktop action. ``DesktopApp.stop`` waits for
    the exact GUI process to exit gracefully and never force-kills a process.
    Codex and Claude are deliberately outside this helper's scope.
    """
    if descriptor.get('id') != 'deepseek' or not descriptor.get('installed'):
        raise ValueError('未找到 DeepSeek Harness 桌面应用')
    if descriptor.get('nativeProfile'):
        if restart:
            raise ValueError('请先核验任务并正常退出此独立配置，再重新开启')
        from bridge.clients.profile_process import launch_profile
        return launch_profile(descriptor)
    executable, home = descriptor.get('executable'), descriptor.get('dataDirectory')
    if not executable or not home or not Path(executable).is_file() or (Path(home).exists() and not Path(home).is_dir()):
        raise ValueError('DeepSeek Harness 应用或数据目录不可用，请重新扫描')
    app = DesktopApp(executable, home)
    running = bool(inspect_client(descriptor)['running'])
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
        stop_client(descriptor, state=state)
    working = app.home
    if sys.platform == 'darwin':
        if not any(p.suffix == '.app' for p in app.executable.parents):
            raise ValueError('DeepSeek Harness 桌面程序必须位于应用程序包中')
    else:
        # Harness initializes its own profile; launch from an existing ancestor.
        working = next((p for p in [app.home, *app.home.parents] if p.is_dir()), Path.home())
    options = {'new_instance': True} if descriptor.get('excludeProfiles') else {}
    native_desktop(sys.platform).start(app.executable, working, {'DSH_HOME': str(app.home)}, **options)
    # GUI startup is asynchronous. Report observed process state, not a claim that
    # the adapter has connected or upstream authentication has succeeded.
    deadline = time.monotonic() + 2
    while not inspect_client(descriptor)['running']:
        if time.monotonic() >= deadline:
            return {'running': False, 'launched': True, 'restarted': running, 'restartRequired': False}
        time.sleep(.1)
    return {'running': True, 'launched': True, 'restarted': running, 'restartRequired': False}


def _app(descriptor):
    if descriptor.get('id') not in ('codex', 'claude', 'deepseek', 'zcode', 'antigravity') or not descriptor.get('installed'):
        raise ValueError('未找到已配置的桌面应用，请重新扫描')
    executable, home = descriptor.get('executable'), descriptor.get('dataDirectory')
    if not executable or not home or not Path(executable).is_file():
        raise ValueError('桌面应用或数据目录不可用，请重新扫描')
    return DesktopApp(executable, home)


def _commands(app, pids):
    """Read command metadata only for same-user, exact-executable processes."""
    if not pids:
        return {}
    return native_desktop(sys.platform).commands(pids)


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
    from bridge.clients.profile_process import scope_processes
    pids, commands = scope_processes(descriptor, pids, commands)
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
        elif (descriptor['id'] == 'zcode' and re.search(r'(?:^|\s)--(?:user-data-dir|profile)(?:[=\s]|$)', command)
              or '--expose-internals' in command or '--eval' in command or ' --run' in command):
            unknown = True
        else:
            main.append(pid)
    if sys.platform in ('linux', 'win32') and descriptor['id'] == 'zcode':
        if sys.platform == 'win32':
            from bridge.platforms.windows.zcode_pipe import main_processes
        else:
            from bridge.platforms.linux.zcode_pipe import main_processes
        main, ambiguous = main_processes(app.executable, main, commands)
        unknown = unknown or ambiguous
    if len(main) > 1 or len(hosts) > 1:
        unknown = True
    return {'running': bool(pids), 'pids': pids, 'mainPids': main,
            'runtimePids': hosts, 'unknown': unknown}


def stop_client(descriptor, *, state):
    if sys.platform == 'win32' and descriptor.get('id') == 'claude':
        return stop_claude(descriptor, state=state)
    if descriptor.get('id') == 'antigravity':
        return stop_antigravity(descriptor, state=state)
    # Revalidate process identities immediately before sending native quit.
    current = inspect_client(descriptor)
    if current['unknown'] or set(current['pids']) - set(state['pids']):
        raise ValueError('客户端进程已变化，无法确认任务状态，请重新检查后再关闭')
    from bridge.clients.profile_process import require_owned
    require_owned(descriptor, current)
    app = _app(descriptor)
    app.stop(runtime_pids=current['runtimePids'], gui_pids=current['mainPids'], provider=descriptor.get('id'),
             selected_pids=current['pids'])


def force_stop_client(descriptor, *, state):
    """Explicit interruption; task evidence is bypassed, identity checks are not."""
    if descriptor.get('nativeProfile') or descriptor.get('excludeProfiles'):
        raise ValueError('多配置客户端尚未验证独立强制退出，请使用正常退出')
    if sys.platform == 'win32':
        from bridge.platforms.windows.force_exit import force_stop_client as force
        return force(descriptor, state=state)
    import re
    from bridge.platforms.posix.force_exit import force_stop, CHANGED
    app = _app(descriptor)
    current = inspect_client(descriptor)
    if set(current['pids']) != set(state['pids']) or len(current['mainPids']) > 1:
        raise ValueError(CHANGED)
    commands = _commands(app, current['pids'])
    if set(commands) != set(current['pids']):
        raise ValueError(CHANGED)
    # Do not reuse the first inventory's profile classification. The command
    # passed to the native binder must itself still name this exact profile.
    classified = _process_state(descriptor, app, current['pids'], commands)
    if len(classified['mainPids']) > 1 or descriptor['id'] == 'zcode' and classified['unknown']:
        raise ValueError(CHANGED)
    for pid, command in commands.items():
        prefix = str(app.executable)
        if descriptor['id'] == 'zcode' and sys.platform == 'linux' and len(classified['mainPids']) == 1:
            # The main and host title replace argv; the classification above verified
            # the native worker's parent. The force binder pins every exact PID below.
            from bridge.platforms.linux.zcode_pipe import worker_title
            if pid in classified['mainPids'] and command == 'ZCode' or worker_title(command):
                continue
        if descriptor['id'] == 'zcode' and sys.platform == 'darwin' and command == app.executable.name:
            # Launch Services may expose just "ZCode" as argv[0]. The native
            # binder below independently verifies image, UID, birth and command.
            continue
        if command != prefix and not command.startswith(prefix + ' '):
            raise ValueError(CHANGED)
        arguments = command[len(prefix):]
        if re.search(r'(?:^|\s)(?:--(?:eval|print|run|user-data-dir|profile)|-[ep])(?:[=\s]|$)', arguments):
            raise ValueError(CHANGED)
        if '--expose-internals' in arguments:
            # Harness's packaged native Host is eligible only for this profile.
            if (descriptor['id'] != 'deepseek' or pid not in classified['runtimePids'] or
                    '/node_modules/@deepseek-ai/dsh-desktop-host/lib/index.js' not in arguments):
                raise ValueError(CHANGED)
        elif descriptor['id'] == 'deepseek' and 'dsh-desktop-host' in arguments and pid not in classified['runtimePids']:
            raise ValueError(CHANGED)
    force_stop(app.executable, commands)


def stop_claude(descriptor, *, state):
    """Invoke Claude's normal menu once, then observe its asynchronous cleanup."""
    from bridge.clients.claude.setup import native_action
    if descriptor.get('id') != 'claude':
        raise ValueError('此退出操作仅用于 Claude 桌面端')
    changed = 'Claude 进程已变化，请重新检查后再退出'
    current = inspect_client(descriptor)
    if not current['running']:
        return
    if (current.get('unknown') or set(current['pids']) - set(state['pids']) or
            len(current.get('mainPids', [])) != 1 or current['mainPids'] != state.get('mainPids')):
        raise ValueError(changed)
    app = _app(descriptor)
    from bridge.clients.profile_process import require_owned
    require_owned(descriptor, current)
    # The connection monitor's cancellation file must not cancel native quit.
    result = native_action('quit', pid=current['mainPids'][0], executable=str(app.executable))
    if result.get('quitState') not in ('submitted', 'exited'):
        raise ValueError(result.get('reason') or '请在电脑端处理 Claude 的任务或保存提示后重试')
    deadline = time.monotonic() + 75
    while remaining := inspect_client(descriptor)['pids']:
        if set(remaining) - set(current['pids']):
            raise ValueError(changed)
        if time.monotonic() >= deadline:
            raise ValueError('Claude 尚未退出，请在电脑端处理任务或保存提示后重试')
        time.sleep(.25)


def stop_antigravity(descriptor, *, state):
    """Gracefully stop Antigravity IDE and clean up helper processes and singleton locks.

    References & Acknowledgements:
    - Step-down process termination (SIGTERM -> wait -> SIGKILL lingering helpers) and
      singleton lock cleanup patterns inspired by AntiGravity Switch (MIT License).
    """
    current = inspect_client(descriptor)
    if not current['running']:
        return

    import signal
    main_pids = current.get('mainPids', [])
    all_pids = current.get('pids', [])

    if sys.platform == 'win32':
        for pid in all_pids:
            try:
                subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
            except Exception:
                pass
    else:
        # Phase 1: Graceful stop - send SIGTERM to main process
        for pid in main_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        # Wait up to 3.5 seconds for graceful shutdown
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            st = inspect_client(descriptor)
            if not st['running']:
                break
            if not any(pid in st.get('mainPids', []) for pid in main_pids):
                break
            time.sleep(0.2)

        # Phase 2: Terminate any lingering helper processes
        remaining = inspect_client(descriptor)
        if remaining['running']:
            for pid in remaining.get('pids', []):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.3)

        # Terminate any orphaned language_server process for this user
        if sys.platform == 'linux':
            try:
                from bridge.platforms.linux.desktop import _process_rows
                for path, exe in _process_rows():
                    if 'language_server' in exe.name:
                        try:
                            os.kill(int(path.name), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
            except Exception:
                pass

    # Phase 3: Clean up stale SingletonLock and SingletonSocket symlinks
    config_dir = Path.home() / '.config' / 'Antigravity'
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        lock_file = config_dir / name
        try:
            if lock_file.is_symlink() or lock_file.exists():
                lock_file.unlink(missing_ok=True)
        except OSError:
            pass

    # Wait up to 2 seconds for process table to fully clear
    deadline = time.monotonic() + 2.0
    while inspect_client(descriptor)['running'] and time.monotonic() < deadline:
        time.sleep(0.1)


def launch_antigravity(descriptor):
    """Launch Antigravity with proper desktop environment, Wayland support and protected logs.

    References & Acknowledgements:
    - Wayland ozone flag conventions and background detached execution
      inspired by AntiGravity Switch (MIT License).
    """
    app = _app(descriptor)
    launcher = descriptor.get('launcher')
    if not launcher:
        script = app.executable.parent.parent / 'antigravity-launcher.sh'
        if script.is_file() and os.access(script, os.X_OK):
            launcher = str(script)

    env = dict(os.environ)
    if sys.platform == 'linux':
        if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
            env['DISPLAY'] = ':0'
        if not env.get('WAYLAND_DISPLAY') and Path(f'/run/user/{os.getuid()}/wayland-0').exists():
            env['WAYLAND_DISPLAY'] = 'wayland-0'
        if not env.get('XDG_RUNTIME_DIR'):
            env['XDG_RUNTIME_DIR'] = f'/run/user/{os.getuid()}'
        if not env.get('DBUS_SESSION_BUS_ADDRESS'):
            bus_path = Path(f'/run/user/{os.getuid()}/bus')
            if bus_path.exists():
                env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path={bus_path}'

    if launcher and Path(launcher).is_file():
        cmd = [str(launcher)]
    else:
        cmd = [str(app.executable)]
        if sys.platform == 'linux' and (env.get('WAYLAND_DISPLAY') or env.get('XDG_SESSION_TYPE') == 'wayland'):
            cmd.append('--ozone-platform=wayland')

    log_dir = Path.home() / '.config' / 'Antigravity' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / 'antigravity-gui.log'
    out = open(log_file, 'a', encoding='utf-8')

    subprocess.Popen(
        cmd,
        cwd=str(Path.home()),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=out,
        start_new_session=True,
    )


def stop_deepseek(descriptor, adapter, *, state):
    """Request DSH's native teardown; closing its window only hides the app."""
    import json
    from bridge.clients.deepseek.adapter import LIFECYCLE_REVISIONS
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
    from bridge.clients.profile_process import require_owned
    require_owned(descriptor, current)
    status = adapter.call('status')
    if status.get('connected') is not True:
        raise ValueError('Harness 桌面未连接，无法确认退出状态')
    try:
        endpoint = json.loads((adapter.directory/'endpoint.json').read_bytes())
        if (endpoint['pid'] != current['runtimePids'][0] or
                not isinstance(endpoint['generation'], str) or not endpoint['generation']):
            raise ValueError(changed)
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(changed) from None
    app = _app(descriptor)
    command = None
    if status.get('nativeQuit') is not True:
        from bridge.platforms.windows.dsh_quit import installer_quit_command, send_installer_quit
        command = installer_quit_command(app, current, _commands(app, current['pids']))
        runtime = adapter.call('lifecycle')
        rows = runtime.get('sessions')
        if (runtime.get('bridgeRevision') not in LIFECYCLE_REVISIONS or runtime.get('complete') is not True or
                not isinstance(rows, list) or any(not isinstance(row, dict) or
                    row.get('runtimeKnown') is not True or row.get('status') not in ('idle', 'stopped', 'completed') or
                    row.get('requests') for row in rows)):
            raise ValueError('有任务运行、等待确认或状态未知，请先在电脑端检查后再退出')
        try:
            if json.loads((adapter.directory/'endpoint.json').read_bytes()) != endpoint:
                raise ValueError(changed)
        except (OSError, ValueError):
            raise ValueError(changed) from None
    current = verify()
    if command is not None:
        send_installer_quit(command)
    else:
        result = adapter.call('quit', body={'expectedPid': endpoint['pid'],
                                          'expectedGeneration': endpoint['generation']})
        if (result.get('status') != 'accepted' or result.get('pid') != endpoint['pid'] or
                result.get('generation') != endpoint['generation']):
            raise ValueError('Harness 未确认退出请求，请在电脑端检查后重试')
    deadline = time.monotonic() + 25
    while remaining := inspect_client(descriptor)['pids']:
        if set(remaining) - set(current['pids']):
            raise ValueError(changed)
        if time.monotonic() >= deadline:
            raise ValueError('Harness 尚未退出，请在电脑端处理退出提示后重试；尚未强制结束进程')
        time.sleep(.25)


def launch_client(descriptor, *, allow_native_relaunch=False):
    if descriptor.get('nativeProfile'):
        from bridge.clients.profile_process import launch_profile
        return launch_profile(descriptor, allow_native_relaunch=allow_native_relaunch)
    if descriptor.get('id') == 'deepseek':
        return launch_deepseek(descriptor)
    app = _app(descriptor)
    if inspect_client(descriptor)['running']:
        return {'running': True, 'launched': False}
    if descriptor['id'] == 'codex':
        app.start()
    elif descriptor['id'] == 'zcode':
        from bridge.clients.zcode.installation import launch
        launch(app.executable, app.home, debug_port=descriptor.get('debugFallbackPort'))
    elif descriptor['id'] == 'antigravity':
        launch_antigravity(descriptor)
    else:
        # Claude owns its native profile selection, including third-party login.
        from bridge.clients.claude.setup import running_app
        if descriptor.get('excludeProfiles'):
            profile_running_app(descriptor)
        else:
            running_app(str(app.executable), str(app.home))
    deadline = time.monotonic() + (5.0 if descriptor.get('id') == 'antigravity' else 3.0)
    while not inspect_client(descriptor)['running']:
        if time.monotonic() >= deadline:
            return {'running': False, 'launched': True}
        time.sleep(.1)
    return {'running': True, 'launched': True}


def profile_running_app(descriptor, restart=False, cancelled=None, allow_launch=True, *, allow_native_relaunch=False):
    """Reuse Claude initialization against one selected native main process."""
    from bridge.clients.profile_process import require_owned, launch_profile
    state = inspect_client(descriptor)
    if cancelled is not None and cancelled.is_set():
        raise ValueError('已取消 Claude 连接')
    if restart:
        raise ValueError('请先核验任务并正常退出该配置，再重新连接')
    require_owned(descriptor, state, allow_native_relaunch=allow_native_relaunch, cancelled=cancelled)
    if not state['running']:
        if not allow_launch:
            raise ValueError('请先开启此 Claude 配置，再重新连接')
        if descriptor.get('nativeProfile'):
            launch_profile(descriptor, cancelled=cancelled)
        else:
            app = _app(descriptor)
            # An independent instance may already be open. Launch Services must
            # start the default rather than activating that other instance.
            native_desktop(sys.platform).start(app.executable, app.home, {}, new_instance=True)
        deadline = time.monotonic()+20
        while not (state := inspect_client(descriptor))['running']:
            if cancelled is not None and cancelled.is_set():
                raise ValueError('已取消 Claude 连接')
            if time.monotonic() >= deadline:
                raise ValueError('Claude 尚未启动，请稍后重试')
            time.sleep(.2)
    if state.get('unknown') or len(state.get('mainPids', [])) != 1:
        raise ValueError('无法确认此 Claude 配置的唯一原生进程')
    require_owned(descriptor, state)
    return state['mainPids'][0]
