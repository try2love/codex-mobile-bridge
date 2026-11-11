"""Target-side same-user Linux Codex control. Never starts a runtime."""

from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import sys
import uuid

from bridge.platforms.linux.codex_daemon_env import _identity, read_process_environment


PROC = Path('/proc')
GRACEFUL_VERSIONS = ('0.164.0', '0.167.0')
GENERATION_FIELDS = frozenset(('uid', 'user', 'home', 'homeDevice', 'homeInode', 'pid',
    'bootId', 'startTicks', 'executable', 'executableDevice', 'executableInode',
    'socket', 'socketDevice', 'socketInode', 'nativeVersion'))


class ControlError(ValueError):
    """The target or durable operation could not be verified."""


def _user():
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def _lock(descriptor):
    import fcntl
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _home(value):
    path = Path(value)
    if not path.is_absolute():
        raise ControlError('An absolute target home is required')
    path = path.resolve(strict=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ControlError('Target home ownership or permissions changed')
    return path, info


def _directory(path, *, parent=None, create=False):
    if create:
        try:
            os.mkdir(path, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ControlError('Lifecycle directory ownership or permissions changed')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _file(path, flags, *, parent=None, native=False):
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        shared_write = info.st_mode & 0o022
        if native and parent is not None:
            directory = os.fstat(parent)
            if (stat.S_ISDIR(directory.st_mode) and directory.st_uid == os.getuid()
                    and stat.S_IMODE(directory.st_mode) == 0o700):
                # Native files inherit umask; a private directory prevents
                # group traversal. Bridge-owned journals never use this branch.
                shared_write = info.st_mode & 0o002
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or shared_write or info.st_nlink != 1):
            raise ControlError('Lifecycle file ownership or permissions changed')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _json(path, *, parent=None, native=False):
    with os.fdopen(_file(path, os.O_RDONLY, parent=parent, native=native), encoding='utf-8') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ControlError('Lifecycle record is invalid')
    return value


def _native_record(home):
    state = home/'app-server-daemon'
    directory = _directory(state)
    try:
        try:
            return _json('daemon.pid', parent=directory, native=True), state/'daemon.pid'
        except FileNotFoundError:
            return _json('app-server.pid', parent=directory, native=True), state/'app-server.pid'
    finally:
        os.close(directory)


def _boot():
    return str(uuid.UUID((PROC/'sys/kernel/random/boot_id').read_text().strip()))


def _process(record, record_path, home, executable):
    normalized = {**record, 'processIdentity': record.get('processIdentity') or record.get('linuxProcessIdentity')}
    values, generation = read_process_environment(record['pid'], normalized, executable,
                                                   {'CODEX_HOME', 'HOME'}, record_path)
    selected = values.get('CODEX_HOME')
    if not selected:
        parent = values.get('HOME')
        if not parent:
            raise ControlError('Target process home is unavailable')
        selected = str(Path(parent)/'.codex')
    if not Path(selected).is_absolute() or Path(selected).resolve() != home:
        raise ControlError('Target process belongs to another home')
    arguments = (PROC/str(record['pid'])/'cmdline').read_bytes().split(b'\0')
    if executable.name != 'codex' or b'app-server' not in arguments or b'--managed-daemon' not in arguments:
        raise ControlError('Target process is not a managed Codex daemon')
    return generation, executable.stat()


def _peer(path, pid):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(2)
        channel.connect(str(path))
        peer = struct.unpack('3i', channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer[:2] != (pid, os.getuid()):
            raise ControlError('Control socket belongs to another process or user')


def _capable():
    return sys.platform == 'linux' and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')


def _socket(home, path, handles):
    advertised = home/'app-server-control/app-server-control.sock'
    directory = _directory(advertised.parent)
    handles.callback(os.close, directory)
    protected = Path('/tmp')/('codex-daemon-' + str(os.getuid()))/hashlib.sha256(
        os.fsencode(advertised.parent.resolve(strict=True)/advertised.name)).hexdigest()
    if path not in (advertised, protected):
        raise ControlError('Control socket is outside the known native layouts')
    entry = advertised.lstat()
    alias = None
    if stat.S_ISLNK(entry.st_mode):
        target = os.readlink(advertised)
        if entry.st_uid != os.getuid() or target != str(protected):
            raise ControlError('Native control socket alias changed')
        alias = (entry.st_dev, entry.st_ino, target)
        path = protected
        directory = _directory(protected.parent)
        handles.callback(os.close, directory)
        if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700:
            raise ControlError('Native private socket directory permissions changed')
    elif path != advertised:
        raise ControlError('Native control socket alias is missing')
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ControlError('Control socket ownership or permissions changed')
    return path, info, alias


def inspect(home, executable, socket_path, native_version):
    """The adapter supplies the same runtime's verified native version, without credentials."""
    if native_version is not None and not isinstance(native_version, str):
        raise ControlError('Native version metadata is invalid')
    if sys.platform != 'linux':
        raise ControlError('The lifecycle worker must run on the target Linux host')
    home, home_info = _home(home)
    executable = Path(executable)
    if not executable.is_absolute():
        raise ControlError('An absolute native executable is required')
    executable = executable.resolve(strict=True)
    if not executable.is_file():
        raise ControlError('An existing native executable is required')
    record, record_path = _native_record(home)
    pid = record.get('pid')
    identity = record.get('processIdentity') or record.get('linuxProcessIdentity') or {}
    if type(pid) is not int or pid <= 0 or not isinstance(identity, dict):
        raise ControlError('Native PID record is invalid')
    boot = _boot()
    process, binary = _process(record, record_path, home, executable)
    ticks = process[1]
    if identity.get('bootId') != boot or identity.get('startTicks') != ticks:
        raise ControlError('Native PID record belongs to another generation')
    path = Path(socket_path)
    if not path.is_absolute() or path.name in ('.', '..'):
        raise ControlError('An absolute control socket is required')
    with ExitStack() as handles:
        path, before, alias = _socket(home, path, handles)
        _peer(path, pid)
        current_path, after, current_alias = _socket(home, path, handles)
        current, current_binary = _process(record, record_path, home, executable)
        if current_path != path or alias != current_alias or (
                before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or current != process or (
                binary.st_dev, binary.st_ino) != (current_binary.st_dev, current_binary.st_ino):
            raise ControlError('Target process or control socket changed during inspection')
    generation = {'uid': os.getuid(), 'user': _user(),
        'home': str(home), 'homeDevice': home_info.st_dev, 'homeInode': home_info.st_ino,
        'pid': pid, 'bootId': boot, 'startTicks': ticks, 'executable': str(executable),
        'executableDevice': binary.st_dev, 'executableInode': binary.st_ino,
        'socket': str(path), 'socketDevice': before.st_dev, 'socketInode': before.st_ino,
        'nativeVersion': native_version}
    return {'generation': generation, 'canRelease': _capable() and native_version in GRACEFUL_VERSIONS,
            'canForce': _capable()}


def _expected(home, generation):
    if not isinstance(generation, dict) or set(generation) != GENERATION_FIELDS:
        raise ControlError('An inspected native process generation is required')
    numeric = ('uid', 'homeDevice', 'homeInode', 'pid', 'startTicks',
               'executableDevice', 'executableInode', 'socketDevice', 'socketInode')
    if (any(type(generation[key]) is not int or generation[key] < 0 for key in numeric)
            or generation['pid'] == 0 or generation['startTicks'] == 0
            or any(not isinstance(generation[key], str) or not generation[key]
                   for key in ('user', 'home', 'bootId', 'executable', 'socket'))
            or generation['nativeVersion'] is not None and not isinstance(generation['nativeVersion'], str)):
        raise ControlError('The inspected generation is malformed')
    if str(uuid.UUID(generation['bootId'])) != generation['bootId']:
        raise ControlError('The inspected boot identity is invalid')
    root, info = _home(home)
    if (generation['home'] != str(root) or generation['uid'] != os.getuid()
            or generation['user'] != _user() or
            (generation['homeDevice'], generation['homeInode']) != (info.st_dev, info.st_ino)):
        raise ControlError('The original lifecycle scope changed')
    return root


@contextmanager
def _journal(home, *, create):
    with ExitStack() as handles:
        descriptor = _directory(home)
        handles.callback(os.close, descriptor)
        for name in ('bridge-cli-maintenance', 'linux'):
            descriptor = _directory(name, parent=descriptor, create=create)
            handles.callback(os.close, descriptor)
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        lock = _file('control.lock', flags, parent=descriptor)
        handles.callback(os.close, lock)
        _lock(lock)
        try:
            data = _json('requests.json', parent=descriptor)
        except FileNotFoundError:
            data = {}
        for request_id, record in data.items():
            _request_id(request_id)
            if not isinstance(record, dict) or record.get('mode') not in ('graceful', 'force'):
                raise ControlError('Lifecycle receipt is invalid')
            _expected(home, record.get('generation'))
            canonical = data.get(record.get('canonicalRequestId'))
            if (not isinstance(canonical, dict) or canonical.get('canonicalRequestId') != record['canonicalRequestId']
                    or canonical.get('status') not in ('unknown', 'requested', 'exited')
                    or _process_key(canonical['generation']) != _process_key(record['generation'])):
                raise ControlError('Lifecycle receipt binding is invalid')
        yield descriptor, data


def _save(directory, records):
    temporary = '.requests-' + uuid.uuid4().hex
    try:
        with os.fdopen(_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, parent=directory),
                       'w', encoding='utf-8') as stream:
            json.dump(records, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, 'requests.json', src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _request_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ControlError('A canonical request UUID is required')


def _process_key(generation):
    return {key: value for key, value in generation.items()
            if key not in ('socket', 'socketDevice', 'socketInode', 'nativeVersion')}


def _observe(record, timeout):
    generation = record['generation']
    try:
        if _boot() != generation['bootId']:
            record['status'] = 'exited'
            return
        descriptor = os.pidfd_open(generation['pid'], 0)
        try:
            if select.select([descriptor], [], [], 0)[0]:
                record['status'] = 'exited'
            else:
                try:
                    current = _identity(generation['pid'])
                except FileNotFoundError:
                    current = None
                if (current is not None and current[0] != generation['startTicks']
                        or select.select([descriptor], [], [], timeout)[0]):
                    record['status'] = 'exited'
        finally:
            os.close(descriptor)
    except ProcessLookupError:
        record['status'] = 'exited'
    except OSError:
        pass  # An unreadable old process stays unknown; it never permits a signal.


def _result(record, request_id, *, duplicate=False):
    return {**record, 'requestId': request_id, 'duplicate': duplicate}


def check(home, request_id, expected, *, timeout=0):
    """Observe the original receipt. No signal, daemon startup or socket mutation."""
    _request_id(request_id)
    home = _expected(home, expected)
    if not 0 <= timeout <= 10:
        raise ControlError('Observation timeout must be between zero and ten seconds')
    try:
        with _journal(home, create=False) as (directory, records):
            record = records.get(request_id)
            if record is None:
                return {'status': 'notFound', 'requestId': request_id}
            if record['generation'] != expected:
                raise ControlError('The request belongs to another native generation')
            original = records[record['canonicalRequestId']]
            _observe(original, timeout)
            _save(directory, records)
            return _result(original, request_id)
    except FileNotFoundError:
        return {'status': 'notFound', 'requestId': request_id}


def dispatch(home, request_id, mode, expected, *, timeout=0):
    """Persist intent before one pidfd signal. Repeated intents only observe."""
    _request_id(request_id)
    home = _expected(home, expected)
    if mode not in ('graceful', 'force') or not 0 <= timeout <= 10 or not _capable():
        raise ControlError('Invalid mode, timeout, or target pidfd capability')
    with _journal(home, create=True) as (directory, records):
        if request_id in records:
            saved = records[request_id]
            if saved['mode'] != mode or saved['generation'] != expected:
                raise ControlError('The request UUID belongs to another lifecycle operation')
            record = records[saved['canonicalRequestId']]
            _observe(record, timeout)
            _save(directory, records)
            return _result(record, request_id, duplicate=True)
        for saved in records.values():
            if (_process_key(saved['generation']) == _process_key(expected)
                    and saved['mode'] in (mode, 'force')):
                record = records[saved['canonicalRequestId']]
                records[request_id] = {'mode': mode, 'generation': expected,
                                       'canonicalRequestId': record['canonicalRequestId']}
                _observe(record, timeout)
                _save(directory, records)
                return _result(record, request_id, duplicate=True)
        with ExitStack() as handles:
            native = _directory(home/'app-server-daemon')
            handles.callback(os.close, native)
            lock = _file('daemon.lock', os.O_RDWR, parent=native, native=True)
            handles.callback(os.close, lock)
            _lock(lock)
            current = inspect(home, expected['executable'], expected['socket'], expected['nativeVersion'])
            if current['generation'] != expected or mode == 'graceful' and not current['canRelease']:
                raise ControlError('The target generation or graceful capability changed')
            descriptor = os.pidfd_open(expected['pid'], 0)
            handles.callback(os.close, descriptor)
            signal.pidfd_send_signal(descriptor, 0, None, 0)
            if inspect(home, expected['executable'], expected['socket'], expected['nativeVersion']) != current:
                raise ControlError('The target changed after opening its pidfd')
            record = {'canonicalRequestId': request_id, 'mode': mode, 'generation': expected,
                      'status': 'unknown', 'signalSent': None}
            records[request_id] = record
            _save(directory, records)
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGHUP if mode == 'graceful' else signal.SIGKILL, None, 0)
                record.update(status='requested', signalSent=True)
            except OSError as error:
                record['errorType'] = type(error).__name__
            if select.select([descriptor], [], [], timeout)[0]:
                record['status'] = 'exited'
            _save(directory, records)
            return _result(record, request_id)


def run(request):
    """A single JSON command for a target-side SSH or WSL worker."""
    if not isinstance(request, dict):
        raise ControlError('A lifecycle request object is required')
    action = request.get('action')
    if action == 'inspect' and set(request) == {'action', 'home', 'executable', 'socket', 'nativeVersion'}:
        return inspect(request['home'], request['executable'], request['socket'], request['nativeVersion'])
    if action in ('dispatch', 'check'):
        fields = {'action', 'home', 'requestId', 'expected', 'timeout'} | ({'mode'} if action == 'dispatch' else set())
        if set(request) == fields:
            if action == 'dispatch':
                return dispatch(request['home'], request['requestId'], request['mode'], request['expected'], timeout=request['timeout'])
            return check(request['home'], request['requestId'], request['expected'], timeout=request['timeout'])
    raise ControlError('Invalid lifecycle worker request')


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': run(json.loads(sys.stdin.readline(65537)))}))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'errorType': type(error).__name__,
                          'error': str(error) if isinstance(error, ControlError) else 'Target lifecycle state could not be verified'}))
