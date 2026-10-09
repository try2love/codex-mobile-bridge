"""Linux executable, shell-launcher and desktop-entry discovery."""
import configparser
import os
import shlex
import shutil
from pathlib import Path

BINARIES = {'codex': ('ChatGPT', 'chatgpt', 'Codex', 'codex-desktop', 'codex'),
            'claude': ('claude-desktop', 'Claude', 'claude'),
            'deepseek': ('deepseek-harness', 'DeepSeek Harness', 'DeepSeekHarness', 'dsh-desktop')}


def candidate(path, provider):
    # PATH and desktop entries often point at a shell launcher. Match the
    # actual GUI binary so /proc process detection and lifecycle agree.
    try:
        path = path.resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            return None
        with path.open('rb') as stream:
            script = stream.read(2) == b'#!'
        if script:
            for name in BINARIES[provider]:
                sibling = path.parent/name
                if sibling.resolve() == path or not sibling.is_file() or not os.access(sibling, os.X_OK):
                    continue
                with sibling.open('rb') as stream:
                    if stream.read(4) == b'\x7fELF':
                        return candidate(sibling, provider)
            return None
        # A PATH codex/claude command can be a standalone CLI. Only accept
        # these ambiguous names when an Electron desktop package is present.
        if path.name in ('codex', 'claude') and not (path.parent/'resources/app.asar').is_file():
            return None
    except (OSError, RuntimeError):
        return None
    if path.is_file():
        return {'application': str(path), 'executable': str(path), 'version': ''}
    return None


def candidates(provider, app_names, home, env):
    names = BINARIES[provider]
    for folder in ('/opt', '/usr/lib', '/usr/local/lib', str(home/'.local/share')):
        for name in (*app_names, *names):
            for binary in names:
                yield Path(folder)/name/binary
    for name in names:
        executable = shutil.which(name, path=env.get('PATH', ''))
        if executable:
            yield Path(executable).resolve()
    # Desktop entries also cover AppImages and nonstandard install locations.
    directories = [Path(env.get('XDG_DATA_HOME') or home/'.local/share')]
    directories += [Path(p) for p in env.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if p]
    accepted = {name.casefold() for name in (*app_names, *names)}
    for directory in directories:
        try:
            entries = (directory/'applications').glob('*.desktop')
            for path in entries:
                config = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    config.read(path, encoding='utf-8')
                    entry = config['Desktop Entry']
                    if entry.get('Name', '').casefold() not in accepted or entry.get('Type') != 'Application':
                        continue
                    parts = shlex.split(entry.get('Exec', ''))
                    if not parts or parts[0] in ('env', 'sh', 'bash'):
                        continue
                    executable = parts[0] if Path(parts[0]).is_absolute() else shutil.which(parts[0], path=env.get('PATH', ''))
                    if executable:
                        yield Path(executable).resolve()
                except (OSError, ValueError, KeyError, configparser.Error):
                    continue
        except OSError:
            continue


def codex_candidates(runtime):
    for parent in list(runtime.parents)[:4]:
        for name in ('chatgpt', 'ChatGPT', 'codex-desktop', 'codex'):
            yield parent/name


def runtime_candidates(executable):
    for folder in (executable.parent/'resources', executable.parent):
        for name in ('codex-cli/bin/codex', 'codex'):
            yield folder/name


def claude_home(home, env):
    return Path(env.get('XDG_CONFIG_HOME') or home/'.config')/'Claude'
