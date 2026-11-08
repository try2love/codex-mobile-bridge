"""Byte-preserving local transcript archive; standalone for selected remote workers."""
import hashlib
import json
import os
import subprocess
import sys
import shutil
import re
import shlex
import threading
from collections import OrderedDict
from functools import lru_cache
import uuid
import tempfile
from pathlib import Path

# Populated only after native picker hide/restore acceptance for an exact version.
VERIFIED_PICKER_VERSIONS = {
    ('darwin', '2.1.292'): '97a01e5bc74a199e67189435d0331ea3a24eac2e07db4b76d9148c5b0386138f',
    ('linux', '2.1.308'): '356733f185fa988e6bf3aa9a555269fdd167982a2e2a80d3c8a78037bb92ad58',
}
_verified_versions = OrderedDict()
_version_lock = threading.Lock()


def _binary_stamp(path):
    stamp = path.stat()
    return (stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)


def supported(binary, environment):
    if not any(platform == sys.platform for platform, version in VERIFIED_PICKER_VERSIONS):
        return False
    try:
        path = Path(binary).resolve(strict=True)
        stamp = _binary_stamp(path)
        digest = _binary_hash(str(path), *stamp)
        if not any(platform == sys.platform and expected == digest
                   for (platform, version), expected in VERIFIED_PICKER_VERSIONS.items()):
            return False
        # Only a successful exact-binary proof is reusable. Auth, process/task
        # ownership and archive guards are still checked afresh by the caller.
        key = (sys.platform, str(path), *stamp, digest)
        with _version_lock:
            version = _verified_versions.get(key)
            if version is not None:
                return _binary_stamp(path) == stamp and VERIFIED_PICKER_VERSIONS.get((sys.platform, version)) == digest
            result = subprocess.run([str(path), '--version'], env=environment, capture_output=True,
                                    text=True, timeout=5)
            version = result.stdout.split()[0]
            if result.returncode or VERIFIED_PICKER_VERSIONS.get((sys.platform, version)) != digest or _binary_stamp(path) != stamp:
                return False
            _verified_versions[key] = version
            if len(_verified_versions) > 8:
                _verified_versions.popitem(last=False)
            return True
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return False


@lru_cache(maxsize=8)
def _binary_hash(path, device, inode, size, modified, changed):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    if _binary_stamp(Path(path)) != (device, inode, size, modified, changed):
        raise ValueError('Claude 原生程序在核验期间变化')
    return digest.hexdigest()


def _linux_executable(pid, uid, namespace):
    """Read the actual executable with same-UID/namespace/generation evidence."""
    process = Path('/proc')/str(pid)
    def proof():
        if process.stat().st_uid != uid or os.readlink(process/'ns/pid') != namespace:
            raise ValueError('Claude 进程归属或 PID 命名空间已变化')
        value = (process/'stat').read_text()
        fields = value.rpartition(')')[2].split()
        if not value.startswith(str(pid)+' (') or len(fields) < 20 or not fields[19].isdigit():
            raise ValueError('Claude 进程代际不可验证')
        return fields[19]
    try:
        before = proof()
        executable = os.readlink(process/'exe')
        if proof() != before:
            raise ValueError('Claude 进程代际已变化')
        return executable.removesuffix(' (deleted)')
    except FileNotFoundError:
        # A missing exe can also mean an exited main thread with live workers.
        # Only a vanished process directory proves there is no current writer.
        try:
            process.stat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError('无法核验 Claude 进程可执行文件') from error
        raise ValueError('无法核验 Claude 进程可执行文件') from None
    except OSError as error:
        raise ValueError('无法核验 Claude 进程可执行文件') from error


def assert_no_cli(binary):
    """Fresh process identity census; no environment or conversation reads."""
    if sys.platform not in ('darwin', 'linux'):
        raise ValueError('当前系统的 Claude 原生进程清单未验证')
    namespace = None
    if sys.platform == 'linux':
        try: namespace = os.readlink('/proc/self/ns/pid')
        except OSError as error: raise ValueError('无法核验 Claude PID 命名空间') from error
        if not re.fullmatch(r'pid:\[\d+\]', namespace):
            raise ValueError('无法核验 Claude PID 命名空间')
    result = subprocess.run(['ps', '-axo', 'uid=,pid=,comm='], capture_output=True,
                            text=True, timeout=5)
    if result.returncode:
        raise ValueError('无法核验 Claude 原生进程清单')
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError('Claude 原生进程清单不完整')
        if int(parts[0]) != os.getuid():continue
        pid, executable = int(parts[1]), parts[2]
        if sys.platform == 'linux':
            comm = Path(executable).name
            if comm in ('claude', 'claude.exe', 'claude-code'):
                raise ValueError('仍有 Claude CLI 进程运行，请先在原终端结束')
            candidate = (comm == Path(binary).name[:15] or comm in ('node', 'nodejs', 'bun')
                         or re.fullmatch(r'\d+\.\d+\.\d+(?:[-+].*)?', comm))
            if not candidate: continue
            executable = _linux_executable(pid, os.getuid(), namespace)
            if executable is None: continue
        name = Path(executable).name
        if (name in ('claude', 'claude.exe', 'claude-code')
                or '@anthropic-ai/claude-code/' in executable
                or re.search(r'/(?:\.local/share/claude/versions/[^/]+|Caskroom/claude-code/[^/]+(?:/[^/]+)?)$', executable)
                or str(Path(executable).resolve()) == str(Path(binary).resolve())):
            raise ValueError('仍有 Claude CLI 进程运行，请先在原终端结束')
        if name not in ('node', 'nodejs', 'bun'):continue
        # Inspect the entry-script operand only; never log the argument vector,
        # parse a prompt operand, or inspect process environments.
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'args='],
                                 capture_output=True, text=True, timeout=3)
        if command.returncode or not command.stdout.strip():
            raise ValueError('无法核验 Claude 脚本进程身份')
        lexer = shlex.shlex(command.stdout, posix=True)
        lexer.whitespace_split = True;lexer.commenters = ''
        next(lexer, None)
        for token in lexer:
            if token in ('-e', '--eval', '-p', '--print'):break
            if token in ('-r', '--require', '--import', '--loader'):
                next(lexer, None);continue
            if token.startswith('-'):continue
            normalized = token.replace('\\', '/')
            if ('@anthropic-ai/claude-code/' in normalized
                    or '/claude-code/' in normalized
                    or Path(normalized).name in ('claude', 'claude.js', 'claude.cjs', 'claude.mjs')):
                raise ValueError('仍有 Claude CLI 脚本进程运行，请先在原终端结束')
            break


@lru_cache(maxsize=1)
def _domain():
    if sys.platform == 'linux':
        machine = Path('/etc/machine-id').read_text().strip()
        if not re.fullmatch(r'[0-9a-fA-F]{32}', machine):
            raise ValueError('Claude 归档主机身份不可验证')
        return 'linux:'+machine
    if sys.platform == 'darwin':
        result = subprocess.run(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                                capture_output=True, text=True, timeout=5)
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]+)"', result.stdout)
        if result.returncode or not match:
            raise ValueError('Claude 归档主机身份不可验证')
        return 'darwin:'+hashlib.sha256(match[1].encode()).hexdigest()
    raise ValueError('当前系统的 Claude 文件归档未验证')


def _identity(directory):
    directory = Path(directory)
    stat = directory.stat()
    return {'configDir': str(directory.resolve()), 'uid': os.getuid() if hasattr(os, 'getuid') else None,
            'device': stat.st_dev, 'inode': stat.st_ino, 'domain': _domain()}


def _root(directory):
    directory = Path(directory)
    root = directory/'bridge-archives'
    _owned(directory)
    if root.exists():
        _owned(root)
        if root.stat().st_dev != directory.stat().st_dev:
            raise ValueError('Claude 归档必须位于原配置文件系统')
    if directory.is_symlink() or root.is_symlink():
        raise ValueError('Claude 归档路径不可验证')
    return root


def _owned(path):
    if path.is_symlink() or (hasattr(os, 'getuid') and path.stat().st_uid != os.getuid()):
        raise ValueError('Claude 归档文件归属不可验证')


def _regular_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Claude 归档文件不可验证')
    _owned(path)


def _digest(path):
    _regular_file(path)
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def _sync_directory(path):
    if os.name != 'nt':
        handle = os.open(path, os.O_RDONLY)
        try:os.fsync(handle)
        finally:os.close(handle)


def _write(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.manifest-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        try:os.unlink(temporary)
        except FileNotFoundError:pass


def _manifest(directory, native):
    native = str(uuid.UUID(native))
    bundle = _root(directory)/native
    if bundle.is_symlink():
        raise ValueError('Claude 归档目录不可验证')
    if bundle.exists():_owned(bundle)
    path = bundle/'manifest.json'
    if not path.exists():
        return bundle, None
    if path.exists():_owned(path)
    if path.is_symlink():
        raise ValueError('Claude 归档记录不可验证')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('schema') != 1 or value.get('nativeId') != native
            or value.get('identity') != _identity(directory)
            or not isinstance(value.get('project'), str)
            or value['project'] in ('', '.', '..')
            or '/' in value['project'] or '\\' in value['project']):
        raise ValueError('Claude 归档记录不可验证')
    return bundle, value


def _original(directory, project, native):
    projects = Path(directory)/'projects'
    target = projects/project
    if projects.is_symlink() or target.is_symlink() or not target.is_dir():
        raise ValueError('Claude 原项目路径不可验证')
    _owned(projects);_owned(target)
    if target.stat().st_dev != Path(directory).stat().st_dev:
        raise ValueError('Claude 会话与归档必须位于同一文件系统')
    return target/(native+'.jsonl')


def archive_paths(directory, errors=None):
    if not Path(directory).exists() and not Path(directory).is_symlink():
        return []
    root = _root(directory)
    if not root.exists():
        return []
    result = []
    for bundle in root.iterdir():
        if not bundle.is_dir() or bundle.name.startswith('.'):
            continue
        try:
            native = str(uuid.UUID(bundle.name))
        except ValueError:
            continue
        try:
            bundle, manifest = _manifest(directory, native)
            if manifest and manifest.get('phase') != 'restored':
                state(directory, bundle/(native+'.jsonl'), native)
                result.append(bundle/(native+'.jsonl'))
        except (OSError, ValueError, TypeError, KeyError):
            if errors is None:raise
            errors.append({'nativeId': native, 'notice': 'Claude Code 归档待核对'})
    return result


def state(directory, path, native, *, verify=False):
    bundle, manifest = _manifest(directory, native)
    if not manifest:
        original = _original(directory, Path(path).parent.name, native)
        if Path(path) != original:
            raise ValueError('Claude 原会话路径不可验证')
        _regular_file(original)
        return {'archived': False}
    original = _original(directory, manifest['project'], native)
    archived = bundle/(native+'.jsonl')
    phase = manifest.get('phase')
    if phase not in ('archived', 'restored'):
        raise ValueError('Claude 文件归档事务中断，请在电脑端核对；不会自动重放')
    expected, absent = (archived, original) if phase == 'archived' else (original, archived)
    if (absent.exists() or absent.is_symlink()
            or (verify and phase == 'archived' and _digest(expected) != manifest['sha256'])):
        raise ValueError('Claude 归档文件冲突或已变化，请在电脑端核对')
    _regular_file(expected)
    return {'archived': phase == 'archived'}


def _private_files(root):
    if not root.exists() and not root.is_symlink():return []
    _owned(root)
    if not root.is_dir():raise ValueError('Claude 会话附属目录不可验证')
    result = []
    for path in root.rglob('*'):
        _owned(path)
        if path.is_file():result.append(path)
        elif not path.is_dir():raise ValueError('Claude 会话附属文件不可验证')
    return result


def _snapshot(directory, original, bundle, native):
    destination = bundle/'sidecars'
    if destination.exists():
        _private_files(destination)
        shutil.rmtree(destination)
    roots = [original.parent/native, Path(directory)/'file-history'/native]
    # Validate the complete trees before creating any copy.
    for root in roots:
        if root.parent.exists() or root.parent.is_symlink():_owned(root.parent)
    files = [(root, _private_files(root)) for root in roots]
    inventory = {}
    for index, (root, items) in enumerate(files):
        for source in items:
            target = destination/str(index)/source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(source, target, follow_symlinks=False)
            with target.open('rb') as copied:os.fsync(copied.fileno())
            parent = target.parent
            while parent != bundle:
                _sync_directory(parent);parent = parent.parent
            inventory[str(target.relative_to(destination))] = _digest(target)
            if _digest(source) != _digest(target):
                raise ValueError('Claude 附属文件在备份期间变化')
    return inventory


def _restore_snapshots(directory, original, bundle, native, manifest):
    snapshot = bundle/'sidecars'
    actual = {str(path.relative_to(snapshot)): _digest(path) for path in _private_files(snapshot)}
    if actual != manifest.get('sidecars', {}):
        raise ValueError('Claude 附属文件备份已变化')
    roots = [original.parent/native, Path(directory)/'file-history'/native]
    missing = []
    for index, root in enumerate(roots):
        backup = bundle/'sidecars'/str(index)
        for source in _private_files(backup):
            target = root/source.relative_to(backup)
            parent = target.parent
            while parent != Path(directory):
                if parent.exists() or parent.is_symlink():_owned(parent)
                parent = parent.parent
            if target.exists() or target.is_symlink():
                if _digest(target) != _digest(source):
                    raise ValueError('Claude 附属文件冲突，恢复不会覆盖')
            else:missing.append((source,target))
    for source,target in missing:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Independent copy: later native edits must not mutate the backup inode.
        with source.open('rb') as reader, target.open('xb') as writer:
            shutil.copyfileobj(reader, writer);writer.flush();os.fsync(writer.fileno())
        shutil.copystat(source,target,follow_symlinks=False)


def change(directory, path, native, archived):
    """Caller must freshly verify the exact profile and stopped native writer."""
    if type(archived) is not bool:
        raise ValueError('Claude 归档设置无效')
    native = str(uuid.UUID(native))
    directory = Path(directory)
    root = _root(directory)
    root.mkdir(mode=0o700, exist_ok=True)
    lock = root/'.transaction'
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError('Claude 文件归档事务待核对，不会自动重放') from exc
    try:
        before = state(directory, path, native, verify=True)
        if before['archived'] == archived:
            return before
        bundle, manifest = _manifest(directory, native)
        if manifest is None:
            original = _original(directory, Path(path).parent.name, native)
            bundle.mkdir(mode=0o700)
            manifest = {'schema': 1, 'nativeId': native, 'project': original.parent.name,
                        'identity': _identity(directory),
                        'sha256': _digest(original)}
        original = _original(directory, manifest['project'], native)
        stored = bundle/(native+'.jsonl')
        source, target = (original, stored) if archived else (stored, original)
        if archived:
            if source.stat().st_nlink != 1:
                raise ValueError('Claude 会话已有共享硬链接，不能归档')
            manifest['sha256'] = _digest(source)
            manifest['sidecars'] = _snapshot(directory, original, bundle, native)
        else:
            _restore_snapshots(directory, original, bundle, native, manifest)
        previous = 'restored' if archived else 'archived'
        manifest['phase'] = 'preparing'
        _write(bundle/'manifest.json', manifest)
        linked = False
        try:
            # link refuses existing destinations atomically; same filesystem only.
            with source.open('rb') as original_file:os.fsync(original_file.fileno())
            os.link(source, target, follow_symlinks=False)
            linked = True
            if _digest(target) != manifest['sha256'] or _digest(source) != manifest['sha256']:
                raise ValueError('Claude 会话在归档期间变化')
            source.unlink()
            _sync_directory(source.parent);_sync_directory(target.parent)
            manifest['phase'] = 'archived' if archived else 'restored'
            _write(bundle/'manifest.json', manifest)
        except Exception:
            if linked and source.exists() and target.exists() and _digest(source) == _digest(target):
                target.unlink()
                manifest['phase'] = previous
                _write(bundle/'manifest.json', manifest)
            raise
        return state(directory, target, native, verify=True)
    finally:
        lock.rmdir()
