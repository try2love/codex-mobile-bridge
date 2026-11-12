"""Discover installed desktops without launching them or reading credentials."""
import os
import sys
from pathlib import Path

from bridge.clients.claude.setup import claude_data_home
from bridge.platforms import discovery as native_discovery
from bridge.platforms.windows.discovery_ext import windows_installations

NAMES = {'codex': 'Codex', 'claude': 'Claude Desktop', 'deepseek': 'DeepSeek Harness', 'zcode': 'ZCode'}
BUNDLE_IDS = {'codex': 'com.openai.codex', 'claude': 'com.anthropic.claudefordesktop',
              'deepseek': 'com.deepseek.dsh', 'zcode': 'dev.zcode.app'}
APP_NAMES = {'codex': ('Codex', 'ChatGPT'), 'claude': ('Claude', 'Claude Desktop'),
             'deepseek': ('DSH Desktop', 'DeepSeek Harness', 'DeepSeekHarness', 'deepseek-harness'), 'zcode': ('ZCode',)}


def _path(value, home):
    value = str(value).strip() if value is not None else ''
    if not value:
        return None
    if value == '~' or value.startswith('~/') or value.startswith('~\\'):
        return (home/value[2:]).resolve() if len(value) > 1 else home
    return Path(value).expanduser().resolve()


def _candidate(path, provider, platform):
    if platform == 'darwin':
        return native_discovery(platform).candidate(path, BUNDLE_IDS[provider])
    if platform == 'linux':
        return native_discovery(platform).candidate(path, provider)
    if provider == 'zcode' and (path.name.lower() != 'zcode.exe' or not (path.parent/'resources/app.asar').is_file()):
        return None
    if path.is_file():
        return {'application': str(path), 'executable': str(path), 'version': ''}
    return None


def _runtime(executable, platform):
    native = native_discovery(platform)
    for candidate in native.runtime_candidates(Path(executable)):
        if candidate.is_file() and candidate.resolve() != Path(executable).resolve():
            return str(candidate)
    return ''


def discover_clients(preferences=None, *, platform=None, home=None, env=None, applications=None, windows_inventory=None):
    """Return safe installation metadata; absence never creates a new data home.

    ``applications`` overrides macOS application roots for packaged smoke tests.
    Saved application paths take precedence. A rescan performs fresh filesystem
    checks so a client installed since gateway startup is discovered immediately.
    """
    preferences = preferences or {}
    native_windows = env is None
    platform, home, env = platform or sys.platform, Path(home or Path.home()).resolve(), os.environ if env is None else env
    roots = [Path(p) for p in applications] if applications is not None else [Path('/Applications'), home/'Applications']
    homes = {'codex': _path(preferences.get('codexHome') or env.get('CODEX_HOME'), home) or home/'.codex',
             'deepseek': _path(preferences.get('deepseekHome') or env.get('DSH_HOME'), home) or home/'.dsh'}
    native = native_discovery(platform)
    if platform == 'win32' and windows_inventory is None:
        windows_inventory = windows_installations() if native_windows else {}
    homes['zcode'] = home/'.zcode/v2'
    homes['claude'] = native.claude_home(home, env)
    result = {}
    for provider, name in NAMES.items():
        if provider == 'zcode' and platform not in ('darwin', 'win32', 'linux'):
            continue
        override = preferences.get(provider+'Application') or preferences.get(provider+'Executable')
        if provider == 'codex':
            override = override or preferences.get('desktopExecutable')
        explicit = _path(override, home)
        candidates = [explicit] if explicit else []
        metadata = {}
        if platform == 'darwin':
            candidates += list(native.candidates(APP_NAMES[provider], roots))
        elif platform == 'win32':
            for row in native.registered_candidates(provider, windows_inventory or {}):
                path = Path(row['executable'])
                candidates.append(path)
                metadata.setdefault(path, {}).update({key: value for key, value in row.items() if key != 'executable'})
            candidates += list(native.candidates(provider, APP_NAMES[provider], home, env))
        elif platform == 'linux':
            candidates += list(native.candidates(provider, APP_NAMES[provider], home, env))
        found = next(({**row, **metadata.get(path, {})} for path in dict.fromkeys(candidates)
                      if (row := _candidate(path, provider, platform))), {})
        if provider == 'zcode' and not found:
            continue
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
    agy_home = _path(preferences.get('antigravityHome') or env.get('ANTIGRAVITY_HOME'), home) or home
    agy_surfaces = [agy_home/'.gemini/antigravity', agy_home/'.gemini/antigravity-ide', agy_home/'.gemini/antigravity-cli']
    from bridge.clients.antigravity.setup import discover_agy_binary
    agy_bin = discover_agy_binary(agy_home, platform=platform)
    desktop_candidates = []
    if platform == 'linux':
        desktop_candidates = [
            agy_home / 'agy/Antigravity/Antigravity-x64/antigravity',
            agy_home / '.local/share/Antigravity/antigravity',
            Path('/opt/Antigravity/antigravity'),
            Path('/usr/bin/antigravity'),
        ]
    elif platform == 'darwin':
        desktop_candidates = [
            Path('/Applications/Antigravity.app/Contents/MacOS/Antigravity'),
            agy_home / 'Applications/Antigravity.app/Contents/MacOS/Antigravity',
        ]
    elif platform == 'win32':
        local_app = Path(os.environ.get('LOCALAPPDATA', str(agy_home / 'AppData/Local')))
        desktop_candidates = [
            local_app / 'Programs/Antigravity/Antigravity.exe',
            agy_home / 'AppData/Local/Programs/Antigravity/Antigravity.exe',
        ]
    desktop_exe = next((str(p) for p in desktop_candidates if p.is_file()), '')
    chosen_exe = desktop_exe or agy_bin or ''
    launcher_script = agy_home / 'agy/Antigravity/antigravity-launcher.sh'
    launcher = str(launcher_script) if launcher_script.is_file() else ''
    if any((s/'conversation_summaries.db').is_file() or (s/'brain').is_dir() for s in agy_surfaces) or chosen_exe:
        result['antigravity'] = {
            'id': 'antigravity',
            'name': 'Antigravity',
            'installed': True,
            'application': chosen_exe,
            'executable': chosen_exe,
            'launcher': launcher,
            'dataDirectory': str(agy_surfaces[0]),
        }
    return result
