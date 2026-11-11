"""Read selected launch variables from the verified local Codex daemon."""

import json
import os
import stat
import sys
from pathlib import Path


class DaemonEnvironmentUnavailable(OSError):
    pass


def read_daemon_environment(home, executable, names):
    """Return (selected values, process generation); never export a full environment."""
    home = Path(home).resolve()
    expected = Path(executable).resolve()
    names = frozenset(names) | {'CODEX_HOME'}
    if (not names.issubset({name for name in names if isinstance(name, str) and
                             name and '=' not in name and '\0' not in name}) or len(names) > 32):
        raise DaemonEnvironmentUnavailable('Invalid daemon environment request')
    state = home / 'app-server-daemon'
    record = next((state / name for name in ('daemon.pid', 'app-server.pid')
                   if (state / name).is_file()), None)
    if record is None or record.is_symlink():
        raise DaemonEnvironmentUnavailable('Codex daemon PID record is unavailable')
    try:
        info = record.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or
                getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or
                os.name != 'nt' and info.st_uid != os.getuid()):
            raise DaemonEnvironmentUnavailable('Codex daemon PID record is untrusted')
        if os.name == 'nt' and state.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise DaemonEnvironmentUnavailable('Codex daemon state directory is untrusted')
        with record.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise DaemonEnvironmentUnavailable('Codex daemon PID record is too large')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DaemonEnvironmentUnavailable('Codex daemon PID record is invalid')
        pid = payload.get('pid')
        if type(pid) is not int or pid <= 0:
            raise DaemonEnvironmentUnavailable('Codex daemon PID record is invalid')
        if sys.platform == 'darwin':
            from bridge.platforms.macos.codex_daemon_env import read_process_environment
        elif sys.platform == 'linux':
            from bridge.platforms.linux.codex_daemon_env import read_process_environment
        elif os.name == 'nt':
            from bridge.platforms.windows.codex_daemon_env import read_process_environment
        else:
            raise DaemonEnvironmentUnavailable('Codex daemon environment is unsupported')
        values, identity = read_process_environment(pid, payload, expected, names, record)
        daemon_home = values.pop('CODEX_HOME', None)
        if daemon_home is not None and Path(daemon_home).resolve() != home:
            raise DaemonEnvironmentUnavailable('Codex daemon belongs to another home')
        after = record.lstat()
        if (after.st_mode, after.st_mtime_ns, after.st_ino, after.st_size) != (
                info.st_mode, info.st_mtime_ns, info.st_ino, info.st_size):
            raise DaemonEnvironmentUnavailable('Codex daemon PID record changed')
        return values, identity
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        if isinstance(exc, DaemonEnvironmentUnavailable):
            raise
        raise DaemonEnvironmentUnavailable('Codex daemon environment could not be verified') from exc


def configuration_after_start(home, generation, *, files=('config.toml', 'auth.json')):
    """Detect config/auth changed after this verified process was born."""
    try:
        if sys.platform == 'darwin':
            seconds, micros = generation[2]
            started = seconds * 1_000_000_000 + micros * 1000
        elif sys.platform == 'linux':
            boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines()
                        if line.startswith('btime '))
            # btime has one-second precision; conservatively include that second.
            started = (boot - 1) * 1_000_000_000 + generation[1] * 1_000_000_000 // os.sysconf('SC_CLK_TCK')
        elif sys.platform == 'win32':
            from bridge.platforms.windows.codex_daemon_env import FILETIME_UNIX_EPOCH
            # FILETIME is in 100 ns ticks since 1601; stat uses the Unix epoch.
            started = (generation[1] - FILETIME_UNIX_EPOCH) * 100
        else:
            raise ValueError('Unsupported process clock')
        for name in files:
            path = Path(home) / name
            if path.exists():
                info = path.stat()
                if max(info.st_mtime_ns, info.st_ctime_ns) > started:
                    return True
        return False
    except (OSError, ValueError, TypeError, IndexError, StopIteration) as error:
        raise DaemonEnvironmentUnavailable('Codex daemon configuration age could not be verified') from error


def stop_daemon_gracefully(home, executable, expected, timeout=10, *, graceful_only=False):
    """Signal one verified generation once. Never escalate to a forced exit."""
    import signal
    import time
    _, current = read_daemon_environment(home, executable, ())
    if current != expected:
        raise DaemonEnvironmentUnavailable('Codex daemon process changed')
    pid = expected[0]
    if graceful_only and sys.platform not in ('darwin', 'linux'):
        raise DaemonEnvironmentUnavailable('Graceful-only daemon shutdown is unsupported on this platform')
    requested_signal = signal.SIGHUP if graceful_only else signal.SIGTERM
    if sys.platform == 'darwin':
        from bridge.platforms.macos.force_exit import Native
        native = Native()
        identity = native.identity(pid)
        if (pid, identity['birth'], identity['started'], identity['image']) != expected:
            raise DaemonEnvironmentUnavailable('Codex daemon process changed')
        handle = native.open(identity)
        try:
            native.send_signal(handle, requested_signal)
        finally:
            native.close(handle)
        def alive():
            try:
                now = native.identity(pid)
                return not now['zombie'] and (pid, now['birth'], now['started'], now['image']) == expected
            except ProcessLookupError:
                return False
    elif sys.platform == 'linux' and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
        import select
        descriptor = os.pidfd_open(pid)
        try:
            if read_daemon_environment(home, executable, ())[1] != expected:
                raise DaemonEnvironmentUnavailable('Codex daemon process changed')
            signal.pidfd_send_signal(descriptor, requested_signal)
            if not select.select([descriptor], [], [], timeout)[0]:
                raise DaemonEnvironmentUnavailable('Codex daemon did not exit normally; no force was used')
        finally:
            os.close(descriptor)
        return
    else:
        raise DaemonEnvironmentUnavailable('Please normally stop this profile runtime before switching accounts on this platform')
    end = time.monotonic() + timeout
    while alive():
        if time.monotonic() >= end:
            raise DaemonEnvironmentUnavailable('Codex daemon did not exit normally; no force was used')
        time.sleep(.1)


def daemon_process_absent(home):
    """A stale PID file is not a running process; uncertainty stays blocking."""
    state = Path(home) / 'app-server-daemon'
    found = False
    for name in ('daemon.pid', 'app-server.pid'):
        path = state / name
        if not os.path.lexists(path):
            continue
        found = True
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or
                    getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or
                    os.name != 'nt' and info.st_uid != os.getuid()):
                return False
            with path.open('rb') as stream:
                payload = json.loads(stream.read(4097))
            pid = payload.get('pid')
            if type(pid) is not int or pid <= 0:
                return False
            if os.name == 'nt':
                from bridge.platforms.windows.codex_daemon_env import recorded_process_absent
                if (state.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                        or not recorded_process_absent(pid, payload)):
                    return False
                current = path.lstat()
                if (current.st_ino, current.st_mtime_ns, current.st_size, current.st_file_attributes) != (
                        info.st_ino, info.st_mtime_ns, info.st_size, info.st_file_attributes):
                    return False
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            return False
        except (OSError, ValueError, AttributeError):
            return False
    return found
