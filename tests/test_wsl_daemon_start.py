"""WSL current-package discovery with synthetic files; no native process starts."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.platforms.windows import wsl


ROOT = Path(__file__).resolve().parents[1]


class WslCurrentPackageTests(unittest.TestCase):
    def test_current_package_cold_start_without_path_or_release_tree(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            root = Path(folder).resolve()
            proc = root/'empty-proc'
            proc.mkdir()
            executable = root/'packages/app-server-daemon/current/bin/codex'
            executable.parent.mkdir(parents=True)
            executable.touch(mode=0o700)
            def path(value):
                if value == '/proc':
                    return proc
                if value == '/usr/local/bin/codex':
                    return root/'absent-global'
                return Path(value)
            run = Mock(return_value=SimpleNamespace(returncode=0))
            context = {'Path': path, 'requested_home': str(root), 'uid': root.stat().st_uid,
                       'home': root/'synthetic-user', 'inventory': {'homes': []},
                       'os': os, 'subprocess': subprocess}
            with patch('shutil.which', return_value=None), patch.object(subprocess, 'run', run):
                exec(wsl._START_DAEMON, context)
            self.assertFalse((root/'packages/app-server-daemon/releases').exists())
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                [str(executable), 'app-server', 'daemon', 'start', '--help'],
                [str(executable), 'app-server', 'daemon', 'start']])
            self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(root))
            self.assertEqual(run.call_args.kwargs['cwd'], root)
            self.assertTrue(run.call_args.kwargs['check'])
