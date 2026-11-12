"""Discover installed desktops without launching them or reading credentials."""
import os
import sys
from copy import deepcopy
from pathlib import Path

from bridge.clients.claude.setup import claude_data_home
from bridge.platforms import discovery as native_discovery
from bridge.platforms.windows.discovery_ext import windows_installations
from bridge.platforms.antigravity import installation as antigravity_installation

DISCOVERY_REVISION = 1

NAMES = {'codex': 'Codex', 'claude': 'Claude Desktop', 'deepseek': 'DeepSeek Harness', 'zcode': 'ZCode'}
BUNDLE_IDS = {'codex': 'com.openai.codex', 'claude': 'com.anthropic.claudefordesktop',
              'deepseek': 'com.deepseek.dsh', 'zcode': 'dev.zcode.app'}
APP_NAMES = {'codex': ('Codex', 'ChatGPT'), 'claude': ('Claude', 'Claude Desktop'),
             'deepseek': ('DSH Desktop', 'DeepSeek Harness', 'DeepSeekHarness', 'deepseek-harness'), 'zcode': ('ZCode',)}


def inventory_preferences(preferences):
    """Installation inputs only; never persist credentials with the inventory."""
    return deepcopy({key: value for key, value in preferences.items()
                     if value and (key.endswith(('Home', 'Application', 'Applications', 'Executable')) or key == 'desktopExecutable')})


def inventory_current(config, preferences):
    rows = config.get('discovered')
    if (config.get('discoveryRevision') != DISCOVERY_REVISION or not isinstance(rows, dict) or
            not all(isinstance(rows.get(name), dict) for name in ('codex', 'claude', 'deepseek')) or
            config.get('discoveryPreferences') != inventory_preferences(preferences)):
        return False
    for row in rows.values():
        if not isinstance(row, dict):
            return False
        for native in [row, *row.get('installations', {}).values()]:
            executable = native.get('executable')
            if native.get('installed', True) and executable and not Path(executable).is_file():
                return False
    return True


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
    agy_installations = {}
    saved = preferences.get('antigravityApplications') or {}
    agy_candidates = [Path(path) for path in saved.values()] if isinstance(saved, dict) else []
    if preferences.get('antigravityApplication'):
        agy_candidates.append(Path(preferences['antigravityApplication']))
    if platform == 'darwin':
        agy_candidates.extend(native.candidates(('Antigravity', 'Antigravity IDE'), roots))
    elif platform == 'win32':
        agy_candidates.extend(native.candidates('antigravity', ('Antigravity', 'Antigravity IDE'), home, env))
    suffix = '.exe' if platform == 'win32' else ''
    agy_candidates.extend((agy_home/('.gemini/bin/agy'+suffix), agy_home/('.antigravity/antigravity/bin/agy'+suffix)))
    for path in dict.fromkeys(agy_candidates):
        found = antigravity_installation(path, platform=platform)
        if found:
            agy_installations.setdefault(found['surface'], found)
    history = any((s/'conversation_summaries.db').is_file() or (s/'brain').is_dir() for s in agy_surfaces)
    if history or agy_installations:
        selected = next(iter(agy_installations.values()), {})
        result['antigravity'] = {
            'id': 'antigravity', 'name': 'Antigravity', 'installed': True,
            'application': '', 'executable': '', **selected,
            'installations': agy_installations,
            'dataDirectory': str(agy_surfaces[0]),
            'historyAvailable': history,
        }
    return result
