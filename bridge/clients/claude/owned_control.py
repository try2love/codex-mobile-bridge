"""Mac control of one explicitly spawned Claude CLI generation, never discovery."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

from bridge.clients.errors import BridgeUnavailable
from bridge.platforms.macos.force_exit import Native


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _not_sent(message):
    error = BridgeUnavailable(message)
    error.release_result = {'status': 'failed', 'notSent': True, 'error': message}
    return error


def spawn_profile(profile):
    """Only called on the already selected local profile, before its Popen."""
    if sys.platform != 'darwin':
        return None
    try:
        directory = Path(profile['directory']).resolve(strict=True)
        info = directory.stat()
        return {'executable': str(Path(profile['binary']).resolve(strict=True)),
                'directory': str(directory), 'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid}
    except (OSError, ValueError):
        return {}  # Existing launches remain usable; an unbound target cannot be forced.


def _directory(directory, create=False):
    directory = Path(directory)
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(descriptor)
        raise OSError('Claude owned-process receipt directory is not private')
    return descriptor


def _read(directory, name):
    parent = _directory(directory)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 65536):
                raise OSError('Claude owned-process receipt is not private')
            value = json.load(stream)
            if not isinstance(value, dict):
                raise ValueError('Claude owned-process receipt is invalid')
            return value
    finally:
        os.close(parent)


def _write_once(directory, name, value):
    parent = _directory(directory, create=True)
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


class OwnedControl:
    def __init__(self, binding, directory=None):
        self.binding = binding
        self.directory = Path(directory) if directory is not None else None
        self.native = Native()
        self.durable = False

    @classmethod
    def capture(cls, process, token, origin, native_id, cwd, profile, catalog, directory):
        if sys.platform != 'darwin' or not profile or type(process.pid) is not int or process.poll() is not None:
            raise ValueError('Original native Claude process is unavailable')
        identity = Native().identity(process.pid)
        if (identity['uid'] != os.getuid() or identity['ruid'] != os.getuid() or identity['zombie']
                or identity['image'] != profile['executable']
                or catalog.get('pid') is not None and catalog['pid'] != process.pid):
            raise ValueError('Original native Claude process identity changed')
        from bridge.clients.claude.model import gateway_id
        binding = {'version': 1, 'key': token, 'provider': 'claude', 'host': 'local',
                   'sessionId': gateway_id('cli:' + origin, native_id), 'nativeId': native_id,
                   'origin': origin, 'cwd': cwd, 'profile': profile, 'process': identity}
        instance = cls(binding, directory)
        if process.poll() is not None or not instance.matches():
            raise ValueError('Original Claude process changed while binding')
        if directory is not None:
            try:
                _write_once(directory, token + '.json', binding)
                instance.durable = True
            except OSError:
                pass  # Keep the kernel binding for normal EOF proof, without force capability.
        return instance

    @classmethod
    def restore(cls, directory, row):
        if sys.platform != 'darwin' or directory is None or row.get('provider') != 'claude' or row.get('host') != 'local':
            return None
        try:
            token = row['key']
            if str(uuid.UUID(token)) != token:
                return None
            binding = _read(directory, token + '.json')
            if (binding.get('version') != 1 or any(binding.get(name) != row.get(name)
                    for name in ('key', 'provider', 'host', 'sessionId'))
                    or binding['process']['uid'] != os.getuid() or binding['process']['ruid'] != os.getuid()):
                return None
            from bridge.clients.claude.model import gateway_id
            if binding['sessionId'] != gateway_id('cli:' + binding['origin'], binding['nativeId']):
                return None
            process, profile = binding['process'], binding['profile']
            if (type(process['pid']) is not int or process['pid'] <= 0
                    or process['image'] != profile['executable'] or not Path(process['image']).is_absolute()
                    or any(not isinstance(profile[name], str) for name in ('directory', 'executable'))
                    or any(len(process[name]) != 2 or any(type(item) is not int for item in process[name])
                           for name in ('birth', 'started'))):
                return None
            instance = cls(binding, directory)
            instance.durable = True
            return instance
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @property
    def generation(self):
        return _digest(self.binding)

    def configuration(self):
        profile = self.binding['profile']
        if (not isinstance(profile.get('directory'), str) or not Path(profile['directory']).is_absolute()
                or profile.get('uid') != os.getuid()
                or any(type(profile.get(name)) is not int for name in ('device', 'inode'))):
            return {}
        return {'configurationKey': _digest(['claude', 'local', profile['uid'], profile['directory'],
                                             profile['device'], profile['inode']]),
                'configurationLabel': '本机 Claude CLI · ' + profile['directory']}

    def matches(self):
        expected = self.binding['process']
        current = self.native.identity(expected['pid'])
        return (not current['zombie'] and all(current[name] == expected[name] for name in ('pid', 'uid', 'ruid', 'image'))
                and tuple(current['birth']) == tuple(expected['birth']) and tuple(current['started']) == tuple(expected['started']))

    def _gone(self):
        expected = self.binding['process']
        try:
            current = self.native.identity(expected['pid'])
        except ProcessLookupError:
            return True
        if (current['zombie'] or tuple(current['birth']) != tuple(expected['birth'])
                or tuple(current['started']) != tuple(expected['started'])):
            return True
        if any(current[name] != expected[name] for name in ('pid', 'uid', 'ruid', 'image')):
            raise OSError('Original Claude identity is no longer verifiable')
        return False

    def intent(self):
        if not self.durable:
            return None
        try:
            value = _read(self.directory, self.binding['key'] + '.force.json')
        except FileNotFoundError:
            return None
        if value.get('generation') != self.generation:
            raise ValueError('Original Claude force receipt changed')
        uuid.UUID(value['operationId'])
        return value

    def preview(self):
        try:
            available = self.durable and not self.intent() and self.matches()
        except (OSError, ValueError, KeyError, TypeError):
            available = False
        return {'canForce': bool(available), 'generation': self.generation,
                'scopeLabel': '本机 Claude CLI · ' + self.binding['profile']['directory'],
                'impact': [{'sessionId': self.binding['sessionId'], 'connectionKey': self.binding['key']}],
                'taskState': 'unknown',
                'message': '强制结束这一个 Claude CLI，未完成任务可能中断。' if available
                           else '原 CLI 已退出或身份、退出回执尚待核对；未重复强制退出。'}

    def check(self, timeout=0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                gone = self._gone()
            except OSError:
                gone = False
            if gone or time.monotonic() >= deadline:
                return {'status': 'released' if gone else 'unknown',
                        'notice': '原 Claude CLI 已退出，连接已释放。' if gone else '原 Claude CLI 退出结果待核对，不会自动重发退出。'}
            time.sleep(.05)

    def force(self, operation_id, generation):
        uuid.UUID(operation_id)
        if generation != self.generation or not self.durable:
            raise _not_sent('原 Claude CLI 连接或退出回执已变化；未强制退出')
        intent = self.intent()
        if intent:
            return {**self.check(), 'canonicalOperationId': intent['operationId']}
        try:
            if not self.matches():
                return self.check()
            handle = self.native.open(self.binding['process'])
            try:
                if not self.matches():
                    raise _not_sent('原 Claude CLI 进程已变化；未操作新的进程')
                try:
                    _write_once(self.directory, self.binding['key'] + '.force.json',
                                {'operationId': operation_id, 'generation': generation})
                except FileExistsError:
                    return self.check()
                try:
                    self.native.terminate(handle)
                except OSError:
                    return {'status': 'unknown', 'forceDispatched': True,
                            'notice': '原 Claude CLI 强制退出结果待核对；不会重复发送。'}
                return {**self.check(timeout=3), 'forceDispatched': True}
            finally:
                self.native.close(handle)
        except ProcessLookupError:
            return self.check()
