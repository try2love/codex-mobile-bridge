"""Synthetic daemon environment checks; no real account or model access."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.platforms.codex_daemon_env import read_daemon_environment, DaemonEnvironmentUnavailable


ROOT = Path(__file__).resolve().parents[1]


class DaemonEnvironmentTests(unittest.TestCase):
    def test_selected_values_come_from_verified_process_not_gateway(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as temporary:
            home = Path(temporary)
            state = home / 'app-server-daemon'
            state.mkdir()
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],
                env={**os.environ, 'CMB_SYNTHETIC_ENV_KEY': 'child-value', 'CODEX_HOME': str(home)},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                record = {'pid': process.pid}
                executable = sys.executable
                if sys.platform == 'darwin':
                    from bridge.platforms.macos.force_exit import Native
                    time.sleep(.1)  # Wait for the Python launcher to exec Python.app.
                    identity = Native().identity(process.pid)
                    executable = identity['image']
                    record['processIdentity'] = {'uniqueId': identity['birth'][0],
                        'startSeconds': identity['started'][0],
                        'startMicroseconds': identity['started'][1]}
                elif sys.platform == 'linux':
                    from bridge.platforms.linux.codex_daemon_env import _identity
                    record['processIdentity'] = {
                        'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                        'startTicks': _identity(process.pid)[0]}
                elif os.name == 'nt':
                    from bridge.platforms.windows.codex_daemon_env import _kernel, _identity, QUERY
                    kernel = _kernel()
                    handle = kernel.OpenProcess(QUERY, False, process.pid)
                    try:
                        record['processStartTime'] = str(_identity(kernel, handle, sys.executable)[0])
                    finally:
                        kernel.CloseHandle(handle)
                else:
                    self.skipTest('unsupported OS')
                path = state / 'daemon.pid'
                path.write_text(json.dumps(record))
                with patch.dict(os.environ, {'CMB_SYNTHETIC_ENV_KEY': 'gateway-value'}):
                    values, identity = read_daemon_environment(home, executable,
                        {'CMB_SYNTHETIC_ENV_KEY'})
                self.assertEqual(values, {'CMB_SYNTHETIC_ENV_KEY': 'child-value'})
                self.assertTrue(identity)
                self.assertNotIn('child-value', repr(identity))
                with self.assertRaises(DaemonEnvironmentUnavailable):
                    read_daemon_environment(home, __file__, {'CMB_SYNTHETIC_ENV_KEY'})
                if os.name == 'nt':
                    # An adjacent Windows PID is not guaranteed to be unused.
                    record['processStartTime'] = '1'
                else:
                    record['pid'] = os.getpid()
                path.write_text(json.dumps(record))
                with self.assertRaises(DaemonEnvironmentUnavailable):
                    read_daemon_environment(home, executable, {'CMB_SYNTHETIC_ENV_KEY'})
                path.write_text('[]')
                with self.assertRaises(DaemonEnvironmentUnavailable):
                    read_daemon_environment(home, executable, {'CMB_SYNTHETIC_ENV_KEY'})
            finally:
                process.terminate()
                process.wait(timeout=3)


class DaemonAbsenceTests(unittest.TestCase):
    def test_pid_records_require_proven_absence_and_reject_unknown(self):
        from bridge.platforms.codex_daemon_env import daemon_process_absent
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            home=Path(directory);state=home/'app-server-daemon';state.mkdir()
            (state/'daemon.pid').write_text(json.dumps({'pid':12345}))
            with patch('bridge.platforms.codex_daemon_env.os') as native:
                native.name = 'posix'
                native.path = os.path
                native.getuid.return_value = (state/'daemon.pid').stat().st_uid
                native.kill.side_effect = ProcessLookupError
                self.assertTrue(daemon_process_absent(home))
                native.kill.side_effect = PermissionError
                self.assertFalse(daemon_process_absent(home))
                native.kill.return_value = None
                native.kill.side_effect = None
                self.assertFalse(daemon_process_absent(home))
                (state/'daemon.pid').write_text('[]')
                self.assertFalse(daemon_process_absent(home))


class DaemonRecoveryBoundaryTests(unittest.TestCase):
    def test_configuration_age_uses_verified_process_birth(self):
        from bridge.platforms.codex_daemon_env import configuration_after_start
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            home = Path(directory)
            (home / 'auth.json').write_text('{"fixture":true}')
            now = int(time.time())
            with patch('bridge.platforms.codex_daemon_env.sys.platform', 'darwin'):
                self.assertTrue(configuration_after_start(home, (1, (1,), (now - 10, 0), 'fixture')))
                self.assertFalse(configuration_after_start(home, (1, (1,), (now + 10, 0), 'fixture')))

    def test_windows_configuration_age_converts_filetime_to_stat_epoch(self):
        from bridge.platforms.codex_daemon_env import configuration_after_start
        from bridge.platforms.windows.codex_daemon_env import FILETIME_UNIX_EPOCH
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            home = Path(directory)
            auth = home / 'auth.json'
            auth.write_text('{"fixture":true}')
            changed = max(auth.stat().st_mtime_ns, auth.stat().st_ctime_ns)
            with patch('bridge.platforms.codex_daemon_env.sys.platform', 'win32'):
                self.assertTrue(configuration_after_start(home, (1, FILETIME_UNIX_EPOCH + changed // 100 - 1, 'fixture')))
                self.assertFalse(configuration_after_start(home, (1, FILETIME_UNIX_EPOCH + changed // 100 + 1, 'fixture')))

    def test_posix_signal_entry_point_is_unsupported_on_windows(self):
        from bridge.platforms.codex_daemon_env import stop_daemon_gracefully
        generation = (1, 'fixture')
        with patch('bridge.platforms.codex_daemon_env.sys.platform', 'win32'), \
                patch('bridge.platforms.codex_daemon_env.read_daemon_environment', return_value=({}, generation)):
            with self.assertRaisesRegex(DaemonEnvironmentUnavailable, 'unsupported'):
                stop_daemon_gracefully(ROOT / '.tmp', sys.executable, generation, graceful_only=True)


if __name__ == '__main__':
    unittest.main()
