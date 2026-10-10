"""Opt-in fixture test; never targets installed client or gateway processes."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(sys.platform in ('darwin', 'linux') and
                     os.environ.get('CMB_RUN_NATIVE_FORCE_EXIT_TEST') == '1',
                     'Opt-in POSIX native test: set CMB_RUN_NATIVE_FORCE_EXIT_TEST=1')
class PosixForceNativeTests(unittest.TestCase):
    def test_isolated_sleeper_exits_and_unselected_process_survives(self):
        from bridge.platforms import desktop
        from bridge.platforms.posix.force_exit import force_stop
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as temporary:
            # Do not copy signed system binaries: AMFI may stall their exit.
            # The target set is still our sole child; another existing sleep
            # process makes force_stop refuse rather than expanding its scope.
            binaries = [Path('/bin/sleep'), Path(sys.executable)]
            children = [subprocess.Popen(['/bin/sleep', '60']),
                        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])]
            try:
                # Wait only for these self-created children to exec their copies.
                deadline = time.monotonic()+3
                native = desktop(sys.platform)
                while children[0].pid not in native.processes(lambda path: Path(path).resolve() == binaries[0].resolve()):
                    if time.monotonic() >= deadline:
                        self.fail('fixture did not exec')
                    time.sleep(.01)
                if sys.platform == 'darwin':
                    from bridge.platforms.macos.force_exit import Native
                    process = Native(); token = process.open(process.identity(children[0].pid)); token[7] += 1
                    self.assertNotEqual(process.lib.proc_signal_with_audittoken(ctypes.byref(token), 9), 0)
                    self.assertIsNone(children[0].poll())
                force_stop(binaries[0], native.commands([children[0].pid]))
                self.assertEqual(children[0].wait(timeout=3), -9)
                self.assertIsNone(children[1].poll())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait(timeout=3)
