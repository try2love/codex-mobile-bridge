"""macOS bundle identities, executable metadata and installation paths."""
import plistlib
from pathlib import Path


def bundle(path, identifier):
    try:
        info = plistlib.loads((path/'Contents/Info.plist').read_bytes())
        name = info['CFBundleExecutable']
        if info.get('CFBundleIdentifier') != identifier or not isinstance(name, str) or Path(name).name != name:
            return None
        executable = path/'Contents/MacOS'/name
        if executable.is_file():
            return {'application': str(path), 'executable': str(executable),
                    'version': str(info.get('CFBundleShortVersionString', ''))}
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        pass
    return None


def candidate(path, identifier):
    application = path if path.suffix == '.app' else next((p for p in path.parents if p.suffix == '.app'), None)
    return bundle(application, identifier) if application else None


def candidates(app_names, roots):
    for root in roots:
        for name in app_names:
            yield root/(name+'.app')
        # Renamed bundles retain their signed application identifier.
        try:
            yield from sorted(root.glob('*.app'))
        except OSError:
            pass


def bundle_executable(application):
    info = plistlib.loads((application/'Contents/Info.plist').read_bytes())
    name = info['CFBundleExecutable']
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('invalid executable')
    return (application/'Contents/MacOS'/name).resolve()


def codex_from_runtime(runtime):
    for parent in runtime.parents:
        if parent.suffix == '.app':
            try:
                info = plistlib.loads((parent/'Contents/Info.plist').read_bytes())
                candidate = parent/'Contents/MacOS'/info['CFBundleExecutable']
                return str(candidate) if candidate.is_file() else ''
            except (OSError, ValueError, KeyError):
                return ''
    return None


def codex_bundles(home):
    for root in (Path('/Applications'), home/'Applications'):
        for name in ('Codex.app', 'ChatGPT.app'):
            application = root/name
            if (application/'Contents/Resources/codex-cli/bin/codex').is_file():
                yield application


def runtime_candidates(executable):
    resources = executable.parent.parent/'Resources'
    for name in ('codex-cli/bin/codex', 'codex'):
        yield resources/name


def claude_home(home, env):
    return home/'Library/Application Support/Claude'
