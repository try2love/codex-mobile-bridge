"""Isolated Antigravity plugin & hooks installer for Codex Mobile Bridge."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PLUGIN_DIR_NAME = 'codex-mobile-bridge'
HOOK_NAME = 'codex-mobile-bridge'
PRE_TOOL_MATCHER = (
    'run_command|edit_file|write_file|replace_file_content|'
    'multi_replace_file_content|write_to_file'
)


def discover_agy_binary(home=None, platform=None):
    """Locate the local agy CLI binary across PATH and standard installation roots."""
    target_platform = platform or sys.platform
    base_home = Path(home).expanduser().resolve() if home else Path.home().resolve()
    if target_platform == sys.platform:
        env_bin = os.environ.get('AGY_BIN', '').strip()
        if env_bin and Path(env_bin).is_file():
            return str(Path(env_bin).resolve())
        try:
            which_bin = shutil.which('agy')
            if which_bin:
                return str(Path(which_bin).resolve())
        except Exception:
            pass

    candidates = [
        base_home / '.antigravity/antigravity/bin/agy',
        base_home / '.local/bin/agy',
    ]
    if target_platform in ('win32', 'nt'):
        local_app = Path(os.environ.get('LOCALAPPDATA', str(base_home / 'AppData/Local')))
        candidates.extend([
            local_app / 'Programs/Antigravity/bin/agy.cmd',
            local_app / 'Programs/Antigravity/bin/agy.exe',
            base_home / 'AppData/Local/Programs/Antigravity/bin/agy.cmd',
            base_home / 'AppData/Local/Programs/Antigravity/bin/agy.exe',
        ])
    elif target_platform == 'darwin':
        candidates.extend([
            Path('/Applications/Antigravity.app/Contents/Resources/app/bin/agy'),
            base_home / 'Applications/Antigravity.app/Contents/Resources/app/bin/agy',
        ])
    elif target_platform == 'linux':
        candidates.extend([
            Path('/usr/local/bin/agy'),
            Path('/usr/bin/agy'),
        ])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return ''


def _build_hook_command(python_executable, hook_client_path, event, state_dir):
    args = [str(python_executable), str(hook_client_path), event, '--state-dir', str(state_dir)]
    if os.name == 'nt':
        return subprocess.list2cmdline(args)
    return ' '.join(shlex.quote(part) for part in args)


def build_hooks_manifest(state_dir, *, enabled=True, python_executable=None, hook_client_path=None):
    """Build the Antigravity hooks.json object for the isolated bridge plugin."""
    py_bin = python_executable or sys.executable or 'python3'
    script = Path(hook_client_path or Path(__file__).with_name('hook_client.py')).resolve()
    state_root = Path(state_dir).resolve()

    def cmd(event):
        return _build_hook_command(py_bin, script, event, state_root)

    return {
        HOOK_NAME: {
            'enabled': bool(enabled),
            'SessionStart': [
                {'type': 'command', 'command': cmd('session_start'), 'timeout': 5},
            ],
            'PreToolUse': [
                {
                    'matcher': PRE_TOOL_MATCHER,
                    'hooks': [
                        {'type': 'command', 'command': cmd('pre_tool_use'), 'timeout': 290},
                    ],
                },
            ],
            'PostToolUse': [
                {
                    'matcher': '*',
                    'hooks': [
                        {'type': 'command', 'command': cmd('post_tool_use'), 'timeout': 5},
                    ],
                },
            ],
            'PostInvocation': [
                {'type': 'command', 'command': cmd('post_invocation'), 'timeout': 5},
            ],
            'Stop': [
                {'type': 'command', 'command': cmd('stop'), 'timeout': 5},
            ],
        }
    }


def plugin_directory(home=None):
    base_home = Path(home).expanduser().resolve() if home else Path.home().resolve()
    return base_home / '.gemini' / 'config' / 'plugins' / PLUGIN_DIR_NAME


def inspect_plugin(state_dir, home=None):
    """Inspect whether the isolated bridge plugin is installed and up to date."""
    pdir = plugin_directory(home)
    manifest_path = pdir / 'plugin.json'
    hooks_path = pdir / 'hooks.json'
    if not manifest_path.is_file() or not hooks_path.is_file():
        return {'installed': False, 'enabled': False, 'upToDate': False, 'pluginDir': str(pdir)}
    try:
        hooks_data = json.loads(hooks_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'installed': True, 'enabled': False, 'upToDate': False, 'pluginDir': str(pdir)}

    spec = hooks_data.get(HOOK_NAME) if isinstance(hooks_data, dict) else None
    enabled = bool(isinstance(spec, dict) and spec.get('enabled', True) is not False)
    expected = build_hooks_manifest(state_dir, enabled=enabled)
    return {
        'installed': True,
        'enabled': enabled,
        'upToDate': hooks_data == expected,
        'pluginDir': str(pdir),
    }


def _atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def ensure_plugin(state_dir, home=None, *, enabled=True):
    """Write or update ~/.gemini/config/plugins/codex-mobile-bridge/{plugin.json,hooks.json}."""
    pdir = plugin_directory(home)
    was_present = (pdir / 'plugin.json').is_file() and (pdir / 'hooks.json').is_file()
    plugin_manifest = {
        'name': PLUGIN_DIR_NAME,
        'displayName': 'Codex Mobile Bridge',
        'description': 'Antigravity lifecycle bridge for mobile tool approvals and mid-turn follow-up injection.',
        'version': '1.0.0',
    }
    hooks_manifest = build_hooks_manifest(state_dir, enabled=enabled)

    changed = False
    for target, expected in ((pdir / 'plugin.json', plugin_manifest), (pdir / 'hooks.json', hooks_manifest)):
        current = None
        if target.is_file():
            try:
                current = json.loads(target.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                current = None
        if current != expected:
            _atomic_write_json(target, expected)
            changed = True

    return {
        'installed': True,
        'enabled': bool(enabled),
        'changed': changed,
        'firstInstall': not was_present,
        'pluginDir': str(pdir),
    }


def disable_plugin(state_dir, home=None):
    """Disable hook handlers in-place without deleting the plugin directory."""
    pdir = plugin_directory(home)
    if not (pdir / 'hooks.json').is_file():
        return {'installed': False, 'enabled': False, 'changed': False, 'pluginDir': str(pdir)}
    return ensure_plugin(state_dir, home=home, enabled=False)
