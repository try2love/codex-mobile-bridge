"""Windows user, Squirrel and Store installation paths."""
from pathlib import Path


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
                for version in sorted(folder.glob('app-*'), reverse=True):
                    yield version/(name+'.exe')
            except OSError:
                continue
    if provider == 'codex':
        # The Store installation has a separate CLI; never select that as the GUI.
        root = Path(env.get('ProgramFiles') or 'C:/Program Files')/'WindowsApps'
        try:
            for folder in sorted(root.glob('OpenAI.Codex_*'), reverse=True):
                yield folder/'app/Codex.exe'
                yield folder/'app/ChatGPT.exe'
        except OSError:
            pass


def codex_candidates(runtime):
    for parent in list(runtime.parents)[:4]:
        yield parent/'Codex.exe'
        yield parent/'ChatGPT.exe'


def runtime_candidates(executable):
    for folder in (executable.parent/'resources', executable.parent):
        for name in ('codex.exe', 'codex-cli/bin/codex.exe'):
            yield folder/name


def claude_home(home, env):
    return Path(env.get('APPDATA') or home/'AppData/Roaming')/'Claude'
