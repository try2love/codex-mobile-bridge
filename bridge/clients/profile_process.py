"""Select a native profile before attaching or changing its process lifecycle."""
import json
import os
from pathlib import Path
import re
import sys
import time

from bridge.clients.profiles import launch_parameters
from bridge.features.auth.auth import replace_session_file


def _path_argument(command, option, path):
    # ps does not preserve argv quoting. Match the complete known path, never
    # a prefix of another instance (including paths containing spaces).
    flags = re.IGNORECASE if os.name == 'nt' else 0
    return bool(re.search(r'(?:^|\s)["\']?' + re.escape(option) + r'(?:=|\s+)["\']?' +
        re.escape(str(path)) + r'["\']?(?=$|\s+--|\s+["\']?--)', command, flags))


def scope_processes(descriptor, pids, commands):
    """Separate same-executable processes by native argv and parentage.

    An explicit profile never inherits an unlabelled main process. The default
    entry excludes every explicit foreign profile, registered or otherwise.
    """
    profile = descriptor.get('nativeProfile')
    if not profile and not descriptor.get('excludeProfiles'):
        return pids, commands
    from bridge.clients.deepseek.recovery import _identities
    identities = _identities(pids)
    selected, excluded, children = set(), set(), set()
    expected = profile.roots['userData'] if profile else Path(descriptor['dataDirectory'])
    for pid in pids:
        command = commands.get(pid, '')
        if not command:
            selected.add(pid)  # Unknown identity blocks lifecycle operations.
        elif re.search(r'(?:^|\s)--type(?:=|\s)', command):
            children.add(pid)
        elif 'dsh-desktop-host' in command:
            home = profile.roots['runtimeHome'] if profile else Path(descriptor['dataDirectory'])
            flags = re.IGNORECASE if os.name == 'nt' else 0
            target = re.search(r'(?:^|[\s"\'])'+re.escape(str(home/'profiles/desktop'))+r'(?:$|[\s"\'])', command, flags)
            (selected if target else excluded).add(pid)
        elif _path_argument(command, '--user-data-dir', expected):
            selected.add(pid)
        elif re.search(r'(?:^|\s)["\']?--(?:user-data-dir|profile)(?:=|\s)', command):
            excluded.add(pid)
        else:
            (excluded if profile else selected).add(pid)
    pending = set(children)
    while pending:
        moved = set()
        for pid in pending:
            parent = identities.get(pid, {}).get('parent')
            if parent in selected:
                selected.add(pid); moved.add(pid)
            elif parent in excluded:
                excluded.add(pid); moved.add(pid)
        if not moved:
            break
        pending -= moved
    # An orphan utility could still own this profile. Keep it unknown rather
    # than treating the profile as stopped and starting a competing Host.
    selected.update(pending)
    return [pid for pid in pids if pid in selected], {
        pid: commands[pid] for pid in selected if pid in commands and pid not in pending}


def _identity(descriptor, pid):
    from bridge.clients.deepseek.recovery import _identities
    from bridge.clients.desktop_app import DesktopApp
    from bridge.clients.lifecycle import _commands
    app = DesktopApp(descriptor['executable'], descriptor['dataDirectory'])
    if pid not in app.processes():
        raise ValueError('独立客户端进程已变化，请重新连接')
    identity = _identities([pid]).get(pid)
    command = _commands(app, [pid]).get(pid)
    profile = descriptor['nativeProfile']
    if not identity or not command or not _path_argument(command, '--user-data-dir', profile.roots['userData']):
        raise ValueError('无法核验独立客户端的配置目录')
    if sys.platform == 'darwin':
        from bridge.platforms.macos.force_exit import Native
        native = Native().identity(pid)
        if native['image'] != str(app.executable) or native['uid'] != os.getuid() or native['zombie']:
            raise ValueError('独立客户端进程身份已变化')
        identity = {'parent': native['parent'], 'birth': list(native['birth']), 'started': list(native['started'])}
    return {'pid': pid, 'identity': identity, 'command': command,
            'executable': str(app.executable), 'home': str(profile.home)}


def require_owned(descriptor, state, *, allow_native_relaunch=False, cancelled=None):
    if not descriptor.get('nativeProfile'):
        return
    guard = descriptor.get('profileGuard')
    if guard:
        guard()
    if not state['running']:
        return
    if state.get('unknown') or len(state.get('mainPids', [])) != 1:
        raise ValueError('无法确认此独立配置的唯一原生进程')
    try:
        saved = json.loads(Path(descriptor['profileLaunchReceipt']).read_text())
    except (OSError, ValueError, KeyError):
        raise ValueError('此独立配置不是由网关启动，请正常退出该配置后从网关开启') from None
    current = _identity(descriptor, state['mainPids'][0])
    if saved != current:
        if allow_native_relaunch and sys.platform == 'darwin' and descriptor['nativeProfile'].family == 'claude':
            _recover_relaunch(descriptor, saved, current, cancelled=cancelled)
        else:
            raise ValueError('独立客户端启动身份已变化，请正常退出该配置后从网关开启')


def _recover_relaunch(descriptor, saved, current, *, cancelled=None):
    """An explicit reconnect may follow the original App's own restart."""
    from bridge.clients.lifecycle import inspect_client
    from bridge.platforms.macos.desktop import verify_relaunch
    changed = '独立客户端重启后的身份无法确认，请检查该配置后重新连接'
    profile = descriptor['nativeProfile']
    if (not isinstance(saved, dict) or type(saved.get('pid')) is not int or saved['pid'] <= 0
            or saved.get('executable') != current['executable'] or saved.get('home') != current['home']
            or not isinstance(saved.get('command'), str)
            or not _path_argument(saved['command'], '--user-data-dir', profile.roots['userData'])
            or not callable(descriptor.get('profileGuard'))):
        raise ValueError(changed)
    if cancelled is not None and cancelled.is_set():
        raise ValueError('已取消客户端连接')
    verify_relaunch(Path(current['executable']), saved, current, launch_parameters(profile)['environment'])
    # Never consume a stale display snapshot or overwrite a newer reconnect.
    descriptor['profileGuard']()
    fresh = inspect_client(descriptor)
    path = Path(descriptor['profileLaunchReceipt'])
    if (fresh.get('unknown') or fresh.get('mainPids') != [current['pid']]
            or _identity(descriptor, current['pid']) != current
            or json.loads(path.read_text()) != saved):
        raise ValueError(changed)
    if cancelled is not None and cancelled.is_set():
        raise ValueError('已取消客户端连接')
    replace_session_file(path, json.dumps(current).encode())


def launch_profile(descriptor, *, cancelled=None, allow_native_relaunch=False):
    from bridge.clients.lifecycle import inspect_client
    from bridge.platforms import desktop
    profile = descriptor['nativeProfile']
    if descriptor.get('profileGuard'):
        descriptor['profileGuard']()
    state = inspect_client(descriptor)
    if state['running']:
        require_owned(descriptor, state, allow_native_relaunch=allow_native_relaunch, cancelled=cancelled)
        return {'running': True, 'launched': False}
    params = launch_parameters(profile)
    if cancelled is not None and cancelled.is_set():
        raise ValueError('已取消客户端启动')
    desktop(sys.platform).start(Path(descriptor['executable']), profile.home,
        params['environment'], arguments=params['arguments'], new_instance=True,
        environment_unset=params['environmentUnset'])
    deadline = time.monotonic() + 20
    while True:
        if cancelled is not None and cancelled.is_set():
            raise ValueError('已取消客户端启动')
        state = inspect_client(descriptor)
        if state['running']:
            if state.get('unknown') or len(state.get('mainPids', [])) != 1:
                raise ValueError('独立客户端启动后无法确认唯一原生进程')
            proof = _identity(descriptor, state['mainPids'][0])
            path = Path(descriptor['profileLaunchReceipt'])
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            replace_session_file(path, json.dumps(proof).encode())
            require_owned(descriptor, state)
            if descriptor.get('profileInitialized'):
                descriptor['profileInitialized']()
            return {'running': True, 'launched': True}
        if time.monotonic() >= deadline:
            return {'running': False, 'launched': True}
        time.sleep(.2)
