"""Prepare empty native profiles with the installed App's own initializer."""
import json
import os
from pathlib import Path
import subprocess
import sys

from bridge.clients.profiles import launch_parameters


def _stamp(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def initializer(executable):
    """Discover the package's exported profile helper, without version pinning."""
    executable = Path(executable).resolve()
    package = executable.parent/'resources/app'
    manifest = package/'package.json'
    if not executable.is_file() or not manifest.is_file():
        return None
    if manifest.stat().st_size > 1024*1024:
        raise ValueError('DSH 原生初始化声明过大，请重新扫描应用')
    value = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or value.get('name') not in ('dsh-plugin-desktop', '@deepseek-ai/dsh-desktop'):
        return None
    exports = value.get('exports')
    exported = exports.get('./profile') if isinstance(exports, dict) else None
    if isinstance(exported, dict):
        exported = exported.get('default') or exported.get('import')
    if not isinstance(exported, str) or not exported.startswith('./'):
        return None
    helper = (package/exported).resolve()
    if package.resolve() not in helper.parents or not helper.is_file():
        return None
    return executable, manifest, helper


_PREPARE = """import {pathToFileURL} from 'node:url';
import {existsSync} from 'node:fs';
import {join,resolve} from 'node:path';
const native=await import(pathToFileURL(process.argv[1]).href);
if(typeof native.ensureDesktopProfile!=='function') throw Error('Unsupported native profile initializer');
const home=resolve(process.argv[2]);
const directory=native.ensureDesktopProfile(home);
if(resolve(directory)!==join(home,'profiles','desktop')||!existsSync(join(directory,'package.json')))
  throw Error('Native profile preparation not confirmed');
console.log(JSON.stringify({directory,prepared:true}));
"""


def prepare(profile, descriptor):
    """No GUI, network install, account import, or model call is requested."""
    if profile.family != 'deepseek':
        raise ValueError('此配置不属于 DSH')
    directory = profile.home/'profiles/desktop'
    manifest = directory/'package.json'
    if manifest.exists():
        return False  # Existing native profiles remain authoritative.
    found = initializer(descriptor.get('executable', ''))
    if found is None:
        if sys.platform == 'win32':
            raise ValueError('此 DSH 应用未提供独立配置初始化入口，请更新应用或选择已经初始化的独立目录')
        return False  # Existing verified macOS first-start path remains unchanged.
    executable, package, helper = found
    guard = descriptor.get('profileGuard')
    if guard:
        guard()
    before = {path: _stamp(path) for path in found}
    preserved = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in
                 (directory/'cordis.patch.yml', directory/'pnpm-workspace.yaml') if path.is_file()}
    parameters = launch_parameters(profile)
    removed = {name.upper() for name in parameters['environmentUnset']} | {'NODE_OPTIONS', 'NODE_PATH', 'ELECTRON_RUN_AS_NODE'}
    environment = {key: value for key, value in os.environ.items() if key.upper() not in removed}
    environment.update(parameters['environment'], ELECTRON_RUN_AS_NODE='1')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        result = subprocess.run([str(executable), '--input-type=module', '--eval', _PREPARE,
                                 str(helper), str(profile.home)], cwd=executable.parent,
                                env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=25, **options)
    except subprocess.TimeoutExpired:
        # subprocess owns the exact helper handle; no GUI process was launched.
        raise ValueError('DSH 独立配置初始化超时，尚未启动桌面应用，请稍后重试') from None
    if result.returncode:
        raise ValueError('DSH 原生独立配置初始化失败，尚未启动桌面应用')
    if guard:
        guard()
    if any(_stamp(path) != stamp for path, stamp in before.items()):
        raise ValueError('DSH 应用在初始化期间发生变化，请重新扫描')
    if any((path.read_bytes(), path.stat().st_mtime_ns) != saved for path, saved in preserved.items()):
        raise ValueError('原生初始化改变了已有配置，尚未启动桌面应用，请检查所选独立目录')
    try:
        receipt = json.loads(result.stdout.decode('utf-8').strip().splitlines()[-1])
        if receipt.get('prepared') is not True or Path(receipt['directory']).resolve() != directory.resolve():
            raise ValueError()
        value = json.loads(manifest.read_text(encoding='utf-8-sig'))
        if (not isinstance(value, dict) or not isinstance(value.get('dsh', {}).get('profile', {}).get('bundles'), list)
                or not (directory/'cordis.patch.yml').is_file() or not (directory/'pnpm-workspace.yaml').is_file()):
            raise ValueError()
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ValueError('DSH 未确认独立配置准备完成，尚未启动桌面应用') from None
    return True
