import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bridge.platforms.windows import updates


class WindowsUpdatePreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = Path(__file__).resolve().parents[1]/'.tmp'
        temporary.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=temporary)
        self.addCleanup(self.directory.cleanup)
        self.worker = Path(self.directory.name)/'codex-mobile-gateway.exe'
        self.worker.touch()
        self.owner = {'pid': 7, 'image': os.path.normcase(str(self.worker)), 'created': 11}
        self.argv = [str(self.worker), 'zcode-pipe-host', '--data-dir', 'profile', '--application', 'ZCode.exe']

    def check(self, *, owner=None, argv=None, pids=(7,)):
        state = {self.worker: {'pids': pids, 'commands': {}}}
        with patch.object(updates, 'process_inventory', return_value=state), \
                patch.object(updates, 'identity', side_effect=owner or [self.owner, self.owner]), \
                patch.object(updates, 'process_args', return_value=argv or self.argv):
            updates.require_update_ready(self.worker)

    def test_live_same_installation_holder_blocks_without_stopping_it(self):
        with self.assertRaisesRegex(ValueError, '正常退出 ZCode'):
            self.check()

    def test_absent_exited_reused_pid_and_other_installation_do_not_block(self):
        self.check(pids=())
        self.check(owner=ProcessLookupError())
        self.check(owner=[{**self.owner, 'image': 'other-installation'}])
        with self.assertRaisesRegex(ValueError, '实例已变化'):
            self.check(owner=[self.owner, {**self.owner, 'created': 12}])

    def test_current_role_is_rechecked_and_query_failure_is_not_ignored(self):
        self.check(argv=[str(self.worker), 'snapshot-stream', '--data-dir', 'profile'])
        self.check(argv=['other-installation', *self.argv[1:]])
        with self.assertRaises(PermissionError):
            self.check(owner=PermissionError('Cannot verify process'))
