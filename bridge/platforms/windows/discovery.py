"""Windows user, Squirrel and Store installation paths."""
import os
import re
from pathlib import Path
from .discovery_ext import (registered_candidates, windows_installations, launch_windows_desktop,
                            package_registration_fingerprint)


def _version_key(path):
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in re.split(r'(\d+)', path.name))


def candidates(provider, app_names, home, env):
    local = Path(env.get('LOCALAPPDATA') or home/'AppData/Local')
    roots = [local/'Programs', local]
    roots += [Path(env[key]) for key in ('ProgramFiles', 'ProgramFiles(x86)') if env.get(key)]
    roots += [Path(env['ProgramFiles'])/'WindowsApps'] if env.get('ProgramFiles') else []
    for root in roots:
        for name in app_names:
            folder = root/name
            yield folder/(name+'.exe')
            if provider == 'deepseek':
                yield folder/'DeepSeek Harness.exe'
            if provider == 'claude':
                yield folder/'claude.exe'
            try:
                # Squirrel keeps the current executable in a versioned directory.
                for version in sorted(folder.glob('app-*'), key=_version_key, reverse=True):
                    yield version/(name+'.exe')
            except OSError:
                continue
    if provider == 'codex':
        # The Store installation has a separate CLI; never select that as the GUI.
        root = Path(env.get('ProgramFiles') or 'C:/Program Files')/'WindowsApps'
        try:
            for folder in sorted(root.glob('OpenAI.Codex_*'), key=_version_key, reverse=True):
                yield folder/'app/ChatGPT.exe'
                yield folder/'app/Codex.exe'
        except OSError:
            pass


def codex_candidates(runtime):
    for parent in list(runtime.parents)[:4]:
        yield parent/'ChatGPT.exe'
    for parent in list(runtime.parents)[:4]:
        yield parent/'Codex.exe'


def runtime_candidates(executable):
    for folder in (executable.parent/'resources', executable.parent):
        for name in ('codex.exe', 'codex-cli/bin/codex.exe'):
            yield folder/name


def cached_runtime_candidates(selected=None, *, env=None):
    """The App's downloaded native runtimes, preserving explicit outside CLIs."""
    env=os.environ if env is None else env
    local=Path(env.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))
    root=local/'OpenAI/Codex/bin'
    if selected is not None:
        selected=Path(selected)
        if selected.parent.parent!=root or selected.name.casefold()!='codex.exe':
            return []
    versions=[]
    for path in root.glob('*/codex.exe'):
        try:
            if path.is_file():versions.append((path.stat().st_mtime,path))
        except OSError:continue
    return [path for _,path in sorted(versions,reverse=True)]


def codex_runtime_stamp(executable, previous=None, env=None):
    """Watch cached CLI versions, keeping the previous stamp on query failure."""
    if not executable:
        return None
    executable = Path(executable)
    env = os.environ if env is None else env
    local = Path(env.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))
    root = local/'OpenAI/Codex/bin'
    if executable.parent.parent != root or executable.name.casefold() != 'codex.exe':
        return None
    try:
        value = root.stat()
        directory = value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
        versions = []
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            try:
                value = (folder/'codex.exe').stat()
                identity = value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
            except FileNotFoundError:
                identity = None
            versions.append((folder.name, identity))
        return directory, tuple(versions)
    except OSError:
        return previous


def claude_home(home, env):
    return Path(env.get('APPDATA') or home/'AppData/Roaming')/'Claude'
