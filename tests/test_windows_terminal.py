"""ConPTY integration tests run on Windows in the existing OS test matrix."""
import os
from pathlib import Path
import tempfile
import sys
import time
import unittest
import uuid
from unittest.mock import patch


@unittest.skipUnless(os.name == 'nt', 'ConPTY requires Windows')
class ConPtyTests(unittest.TestCase):
    def test_persistent_shell_unicode_resize_dedupe_and_close(self):
        from bridge.platforms.windows.terminal import WindowsTerminalSession
        directory = Path(__file__).resolve().parents[1] / '.tmp'; directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            root=Path(folder); (root/'nested folder').mkdir()
            runtime = os.environ.get('CMB_TEST_RUNTIME')
            with patch.object(sys, 'executable', runtime or sys.executable), patch.object(sys, 'frozen', bool(runtime), create=True):
                session=WindowsTerminalSession(root,80,24)
            try:
                deadline=time.monotonic()+10
                while '>' not in session.read()['output'] and time.monotonic()<deadline:time.sleep(.05)
                self.assertIn('>',session.read()['output'])
                command='cd "nested folder"\rset BRIDGE_TEST=retained\r'
                session.write(str(uuid.uuid4()),command)
                key=str(uuid.uuid4());session.write(key,'echo %BRIDGE_TEST%> result.txt\r');session.write(key,'echo %BRIDGE_TEST%> result.txt\r')
                deadline=time.monotonic()+10
                while not (root/'nested folder/result.txt').exists() and time.monotonic()<deadline:time.sleep(.05)
                self.assertTrue((root/'nested folder/result.txt').exists(),repr(session.read()))
                self.assertEqual((root/'nested folder/result.txt').read_text().strip(),'retained')
                # Use CMD's explicit cross-drive form; a later input must keep
                # that cwd instead of launching a fresh command from root.
                session.write(str(uuid.uuid4()), 'cd /d "' + str(root) + '"\r')
                session.write(str(uuid.uuid4()), 'cd > cwd-after.txt\r')
                deadline = time.monotonic() + 10
                while not (root/'cwd-after.txt').exists() and time.monotonic() < deadline: time.sleep(.05)
                self.assertEqual((root/'cwd-after.txt').read_text().strip().casefold(), str(root).casefold())

                self.assertTrue(session.resize(100,30)['resized'])
                with self.assertRaises(ValueError):session.write(key,'echo unexpected\r\n')
                self.assertFalse(session.done.is_set())
            finally:
                session.stop();self.assertTrue(session.done.wait(10))
