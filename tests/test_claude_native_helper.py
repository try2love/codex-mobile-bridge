"""Run the real macOS Console selector regression without controlling any app."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift compiler required')
class NativeConsoleSelectors(unittest.TestCase):
    def test_observed_console_selectors_submission_and_window_guards(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            folder = Path(folder)
            helper = folder/'claude-helper'
            built = subprocess.run(['swiftc', '-module-cache-path', str(folder/'modules'),
                                    str(ROOT/'bridge/integrations/native/claude-helper.swift'), '-o', str(helper)],
                                   capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(helper), '--self-check'], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['setupState'], 'ready', result.stdout)
