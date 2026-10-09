"""Discover installed desktops without launching them or reading credentials."""
import configparser
import os
import plistlib
import shlex
import shutil
import sys
from pathlib import Path

from .claude_setup import claude_data_home
from .windows_discovery import registered_candidates, windows_installations

NAMES = {'codex': 'Codex', 'claude': 'Claude Desktop', 'deepseek': 'DeepSeek Harness'}
BUNDLE_IDS = {'codex': 'com.openai.codex', 'claude': 'com.anthropic.claudefordesktop',
              'deepseek': 'com.deepseek.dsh'}
APP_NAMES = {'codex': ('Codex', 'ChatGPT'), 'claude': ('Claude', 'Claude Desktop'),
             'deepseek': ('DSH Desktop', 'DeepSeek Harness', 'DeepSeekHarness', 'deepseek-harness')}


def _path(value, home):
    value = str(value).strip() if value is not None else ''
    if not value:
        return None
    if value == '~' or value.startswith('~/') or value.startswith('~\\'):
        return (home/value[2:]).resolve() if len(value) > 1 else home
    return Path(value).expanduser().resolve()


def _bundle(path, provider):
    try:
        info = plistlib.loads((path/'Contents/Info.plist').read_bytes())
        name = info['CFBundleExecutable']
        if info.get('CFBundleIdentifier') != BUNDLE_IDS[provider] or not isinstance(name, str) or Path(name).name != name:
            return None
        executable = path/'Contents/MacOS'/name
        if executable.is_file():
            return {'application': str(path), 'executable': str(executable),
                    'version': str(info.get('CFBundleShortVersionString', ''))}
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        pass
    return None


def _candidate(path, provider, platform):
    if platform == 'darwin':
        bundle = path if path.suffix == '.app' else next((p for p in path.parents if p.suffix == '.app'), None)
        return _bundle(bundle, provider) if bundle else None
    if path.is_file():
        return {'application': str(path), 'executable': str(path), 'version': ''}
    return None


def _windows_candidates(provider, home, env):
    local = Path(env.get('LOCALAPPDATA') or home/'AppData/Local')
    roots = [local/'Programs', local]
    roots += [Path(env[key]) for key in ('ProgramFiles', 'ProgramFiles(x86)') if env.get(key)]
    roots += [Path(env['ProgramFiles'])/'WindowsApps'] if env.get('ProgramFiles') else []
    for root in roots:
        for name in APP_NAMES[provider]:
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


def _linux_candidates(provider, home, env):
    names = {'codex': ('codex-desktop', 'ChatGPT', 'chatgpt'),
             'claude': ('claude-desktop', 'Claude'),
             'deepseek': ('deepseek-harness', 'DeepSeek Harness', 'dsh-desktop')}[provider]
    for folder in ('/opt', '/usr/lib', '/usr/local/lib', str(home/'.local/share')):
        for name in (*APP_NAMES[provider], *names):
            for binary in names:
                yield Path(folder)/name/binary
    for name in names:
        executable = shutil.which(name, path=env.get('PATH', ''))
        if executable:
            yield Path(executable).resolve()
    # Desktop entries also cover AppImages and nonstandard install locations.
    directories = [Path(env.get('XDG_DATA_HOME') or home/'.local/share')]
    directories += [Path(p) for p in env.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if p]
    accepted = {name.casefold() for name in (*APP_NAMES[provider], *names)}
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


def _runtime(executable, platform):
    root = Path(executable).parent
    roots = [root.parent/'Resources'] if platform == 'darwin' else [root/'resources', root]
    names = ('codex-cli/bin/codex', 'codex') if platform != 'win32' else ('codex.exe', 'codex-cli/bin/codex.exe')
    for folder in roots:
        for name in names:
            candidate = folder/name
            if candidate.is_file() and candidate.resolve() != Path(executable).resolve():
                return str(candidate)
    return ''


def discover_clients(preferences=None, *, platform=None, home=None, env=None, applications=None, windows_inventory=None):
    """Return safe installation metadata; absence never creates a new data home.

    ``applications`` overrides macOS application roots for packaged smoke tests.
    An explicit ``env`` isolates filesystem discovery from the host; tests can
    supply ``windows_inventory`` separately to model native registrations.
    Saved application paths take precedence. A rescan performs fresh filesystem
    checks so a client installed since gateway startup is discovered immediately.
    """
    preferences = preferences or {}
    native_windows = env is None
    platform, home, env = platform or sys.platform, Path(home or Path.home()).resolve(), os.environ if env is None else env
    if platform == 'win32' and windows_inventory is None:
        windows_inventory = windows_installations() if native_windows else {}
    roots = [Path(p) for p in applications] if applications is not None else [Path('/Applications'), home/'Applications']
    homes = {'codex': _path(preferences.get('codexHome') or env.get('CODEX_HOME'), home) or home/'.codex',
             'deepseek': _path(preferences.get('deepseekHome') or env.get('DSH_HOME'), home) or home/'.dsh'}
    if platform == 'darwin':
        homes['claude'] = home/'Library/Application Support/Claude'
    elif platform == 'win32':
        homes['claude'] = Path(env.get('APPDATA') or home/'AppData/Roaming')/'Claude'
    else:
        homes['claude'] = Path(env.get('XDG_CONFIG_HOME') or home/'.config')/'Claude'
    result = {}
    for provider, name in NAMES.items():
        override = preferences.get(provider+'Application') or preferences.get(provider+'Executable')
        if provider == 'codex':
            override = override or preferences.get('desktopExecutable')
        explicit = _path(override, home)
        candidates = [explicit] if explicit else []
        metadata = {}
        if platform == 'darwin':
            for root in roots:
                candidates += [root/(name+'.app') for name in APP_NAMES[provider]]
                # Renamed bundles retain their signed application identifier.
                try:
                    candidates += sorted(root.glob('*.app'))
                except OSError:
                    pass
        elif platform == 'win32':
            for row in registered_candidates(provider, windows_inventory or {}):
                path = Path(row['executable'])
                candidates.append(path)
                metadata.setdefault(path, {}).update({key: value for key, value in row.items() if key != 'executable'})
            candidates += list(_windows_candidates(provider, home, env))
        elif platform == 'linux':
            candidates += list(_linux_candidates(provider, home, env))
        found = next(({**row, **metadata.get(path, {})} for path in dict.fromkeys(candidates)
                      if (row := _candidate(path, provider, platform))), {})
        if provider == 'claude':
            explicit_home = _path(preferences.get('claudeHome'), home)
            homes['claude'] = explicit_home or (claude_data_home(found['executable'], homes['claude'], platform,
                                                env=env, package_family=found.get('packageFamilyName'),
                                                inspect_running=native_windows or platform != 'win32')
                                                if found else homes['claude'])
        row = {'id': provider, 'name': name, 'installed': bool(found), 'application': '', 'executable': '',
               'dataDirectory': str(homes[provider]), **found}
        if provider == 'codex':
            row['runtime'] = _runtime(row['executable'], platform) if found else ''
        elif provider == 'deepseek':
            row['desktopProfileReady'] = (homes[provider]/'profiles/desktop/cordis.patch.yml').is_file()
        elif provider == 'claude':
            row['dataDirectoryExplicit'] = explicit_home is not None
        result[provider] = row
    return result
