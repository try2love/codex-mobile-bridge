"""Control only the selected GUI executable, never the bundled agent runtime."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def process_inventory(executables):
    """One uncached display snapshot for resolved GUI paths and their commands.

    Control operations keep using DesktopApp.processes and fresh command reads.
    On macOS executable names may contain spaces, so collect identities first
    and then read all matching command lines in a single second ps invocation.
    """
    result = {Path(executable): {'pids': [], 'commands': {}} for executable in executables}
    targets = {os.path.normcase(str(path)): value for path, value in result.items()}
    if not targets:
        return result

    def match(executable):
        return targets.get(os.path.normcase(str(Path(executable).resolve())))

    if sys.platform == 'win32':
        script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $s=(Get-Process -Id $PID).SessionId; '
                  'Get-CimInstance Win32_Process | Where-Object {$_.SessionId -eq $s -and $_.ExecutablePath} | '
                  'Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
        response = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                  capture_output=True, text=True, encoding='utf-8', timeout=15,
                                  creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        rows = json.loads(response.stdout or '[]')
        if isinstance(rows, dict): rows = [rows]
        for row in rows:
            state = match(row['ExecutablePath'])
            if state is not None:
                pid = int(row['ProcessId']); state['pids'].append(pid)
                state['commands'][pid] = row.get('CommandLine') or ''
        return result
    if sys.platform == 'linux':
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                state = targets.get(str((path/'exe').resolve(strict=True)))
            except (OSError, RuntimeError):
                continue
            if state is not None:
                pid = int(path.name); state['pids'].append(pid)
                try: state['commands'][pid] = (path/'cmdline').read_bytes().decode().replace('\0', ' ').strip()
                except (OSError, UnicodeError): pass  # Disappearing/unreadable commands remain unknown.
        return result
    response = subprocess.run(['ps', '-ww', '-u', str(os.getuid()), '-o', 'pid=,comm='],
                              capture_output=True, text=True, check=True, timeout=10)
    for line in response.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2:
            state = match(fields[1])
            if state is not None: state['pids'].append(int(fields[0]))
    pids = [pid for state in result.values() for pid in state['pids']]
    if pids:
        response = subprocess.run(['ps', '-ww', '-p', ','.join(map(str, pids)), '-o', 'pid=,args='],
                                  capture_output=True, text=True, timeout=10)
        if response.returncode not in (0, 1): response.check_returncode()
        commands = {int(parts[0]): parts[1] for line in response.stdout.splitlines()
                    if len(parts := line.strip().split(None, 1)) == 2}
        for state in result.values():
            state['commands'] = {pid: commands[pid] for pid in state['pids'] if pid in commands}
    return result


class DesktopApp:
    def __init__(self, executable, home):
        self.executable = Path(executable).expanduser().resolve()
        if self.executable.suffix == '.app':
            import plistlib
            try:
                info = plistlib.loads((self.executable/'Contents/Info.plist').read_bytes())
                name = info['CFBundleExecutable']
                if not isinstance(name, str) or Path(name).name != name:
                    raise ValueError('invalid executable')
                self.executable = (self.executable/'Contents/MacOS'/name).resolve()
            except (OSError, ValueError, KeyError):
                raise ValueError('请选择有效的 Codex / ChatGPT 应用程序包') from None
        self.home = Path(home).resolve()

    @staticmethod
    def discover(runtime):
        if not runtime:
            return ''
        runtime = Path(runtime).resolve()
        for parent in runtime.parents:
            if parent.suffix == '.app':
                import plistlib
                try:
                    info = plistlib.loads((parent/'Contents/Info.plist').read_bytes())
                    candidate = parent/'Contents/MacOS'/info['CFBundleExecutable']
                    return str(candidate) if candidate.is_file() else ''
                except (OSError, ValueError, KeyError):
                    return ''
        candidates = []
        for parent in list(runtime.parents)[:4]:
            if sys.platform == 'win32':
                # Store installs may keep Codex.exe as a launcher beside the GUI.
                candidates += [parent/'ChatGPT.exe', parent/'Codex.exe']
            elif sys.platform == 'linux':
                candidates += [parent/'chatgpt', parent/'ChatGPT', parent/'codex-desktop', parent/'codex']
        return next((str(p) for p in candidates if p.is_file() and p.resolve() != runtime), '')

    @staticmethod
    def scan(runtime, home):
        candidate = DesktopApp.discover(runtime)
        if not candidate and sys.platform == 'win32':
            # Updated Windows CLIs live outside the Store GUI installation. Use
            # the same registered-app discovery as the client management list.
            from .integrations.discovery import discover_clients
            desktop = discover_clients({'codexHome': str(home)})['codex']
            candidate = desktop.get('executable', '') if desktop.get('installed') else ''
        if candidate:
            app = DesktopApp(candidate, home)
            app.validate(runtime)
            return str(app.executable)
        if sys.platform == 'darwin':
            for root in (Path('/Applications'), Path.home()/'Applications'):
                for name in ('Codex.app', 'ChatGPT.app'):
                    bundle = root/name
                    if (bundle/'Contents/Resources/codex-cli/bin/codex').is_file():
                        app = DesktopApp(bundle, home)
                        app.validate(runtime)
                        return str(app.executable)
        raise ValueError('未找到 Codex 桌面程序，请选择已安装的应用程序包或可执行文件')

    def validate(self, runtime):
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise ValueError('请选择已安装的 Codex / ChatGPT 桌面程序')
        if runtime and self.executable == Path(runtime).resolve():
            raise ValueError('桌面程序不能选择内置 Codex 命令行运行时')
        # Launch scripts cannot be matched reliably to the resulting GUI process.
        with self.executable.open('rb') as stream:
            script = stream.read(2) == b'#!'
        if script:
            raise ValueError('请选择桌面程序的实际可执行文件，而不是启动脚本')

    def processes(self):
        if sys.platform == 'win32':
            script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $s=(Get-Process -Id $PID).SessionId; '
                      'Get-CimInstance Win32_Process | Where-Object {$_.SessionId -eq $s -and $_.ExecutablePath} | '
                      'Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                    capture_output=True, text=True, encoding='utf-8', timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            rows = json.loads(result.stdout or '[]')
            if isinstance(rows, dict):
                rows = [rows]
            return [int(row['ProcessId']) for row in rows
                    if os.path.normcase(str(Path(row['ExecutablePath']).resolve())) == os.path.normcase(str(self.executable))]
        if sys.platform == 'linux':
            result = []
            for path in Path('/proc').iterdir():
                if path.name.isdigit():
                    try:
                        if path.stat().st_uid == os.getuid() and (path/'exe').resolve(strict=True) == self.executable:
                            result.append(int(path.name))
                    except (OSError, RuntimeError):
                        continue
            return result
        output = subprocess.run(['ps', '-ww', '-u', str(os.getuid()), '-o', 'pid=,comm='],
                                capture_output=True, text=True, check=True, timeout=10).stdout
        result = []
        for line in output.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and Path(fields[1]).resolve() == self.executable:
                result.append(int(fields[0]))
        return result

    def stop(self, *, runtime_pids=(), gui_pids=None):
        pids = self.processes()
        main = pids if gui_pids is None else [pid for pid in gui_pids if pid in pids]
        if main and sys.platform == 'darwin':
            bundle = next((p for p in self.executable.parents if p.suffix == '.app'), None)
            if bundle is None:
                raise ValueError('macOS 桌面程序必须位于应用程序包中')
            script = 'on run argv\n tell application (item 1 of argv) to quit\nend run'
            subprocess.run(['osascript', '-e', script, str(bundle)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        for pid in main:
            if sys.platform == 'darwin':
                continue
            if pid not in self.processes():
                continue
            if sys.platform == 'win32':
                script = '$p=Get-Process -Id ([int]$env:CMB_GUI_PID) -ErrorAction Stop; if (-not $p.CloseMainWindow()) {exit 2}'
                subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                               env={**os.environ, 'CMB_GUI_PID': str(pid)}, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.kill(pid, signal.SIGTERM)
        # Only a caller that verified the native host's complete idle state may
        # include it here. Never force-kill or terminate unrelated profiles.
        for pid in runtime_pids:
            if pid not in pids or pid not in self.processes():
                continue
            if sys.platform == 'win32':
                subprocess.run(['taskkill.exe', '/PID', str(pid)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 25
        while self.processes():
            if time.monotonic() >= deadline:
                raise ValueError('桌面程序尚未退出，请在电脑上关闭后重试；尚未强制结束进程')
            time.sleep(.25)

    def start(self):
        if sys.platform == 'darwin':
            bundle = next((p for p in self.executable.parents if p.suffix == '.app'), None)
            if bundle is None:
                raise ValueError('macOS 桌面程序必须位于应用程序包中')
            # Launch as a GUI app, not as a gateway child inheriting its privacy identity.
            subprocess.run(['/usr/bin/open', '-a', str(bundle), '--env', 'CODEX_HOME='+str(self.home)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
            return
        options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP, 'close_fds': True}
                   if sys.platform == 'win32' else {'start_new_session': True})
        self.child = subprocess.Popen([str(self.executable)], cwd=self.home,
                                     env={**os.environ, 'CODEX_HOME': str(self.home)},
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, **options)
