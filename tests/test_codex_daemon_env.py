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
            with patch('bridge.platforms.codex_daemon_env.os.name','posix'), patch('bridge.platforms.codex_daemon_env.os.kill',side_effect=ProcessLookupError):
                self.assertTrue(daemon_process_absent(home))
            with patch('bridge.platforms.codex_daemon_env.os.kill',side_effect=PermissionError):
                self.assertFalse(daemon_process_absent(home))
            with patch('bridge.platforms.codex_daemon_env.os.kill',return_value=None):
                self.assertFalse(daemon_process_absent(home))
            (state/'daemon.pid').write_text('[]')
            self.assertFalse(daemon_process_absent(home))


if __name__ == '__main__':
    unittest.main()
