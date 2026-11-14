import os
from pathlib import Path
import tempfile
import unittest

from bridge.features.workspace.workspace import operate

ROOT = Path(__file__).resolve().parents[1]


class HostFileNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root/'project'; self.project.mkdir()
        self.other = self.root/'other'; self.other.mkdir()
        (self.other/'报告.txt').write_text('host read', encoding='utf-8')
        self.history = self.root/'records'; self.history.mkdir()

    def read(self, action, path='', **params):
        return operate(str(self.project), action, {'scope': 'host', 'path': path, **params})

    def test_start_at_project_then_browse_and_preview_outside_it(self):
        start = self.read('list')
        self.assertEqual(start['path'], self.project.as_posix())
        self.assertEqual(start['project'], self.project.as_posix())
        self.assertEqual(start['projectPath'], '')
        self.assertEqual(start['parentPath'], self.root.as_posix())
        rows = self.read('list', self.other.as_posix())
        path = (self.other/'报告.txt').as_posix()
        self.assertEqual(rows['entries'][0]['path'], path)
        self.assertIsNone(rows['entries'][0]['projectPath'])
        self.assertEqual(self.read('preview', path)['text'], 'host read')
        info = self.read('info', path)
        self.assertEqual(info['absolutePath'], path)
        self.assertIsNone(info['projectPath'])
        with self.read('download-stream', path, range_header='bytes=0-3') as (meta, stream):
            self.assertEqual(meta['status'], 206)
            self.assertEqual(stream.read(), b'host')

    def test_no_project_starts_at_verified_record_directory(self):
        result = operate(None, 'list', {'scope': 'host', 'path': ''}, history_path=str(self.history/'chat.jsonl'))
        self.assertEqual(result['path'], self.history.as_posix())
        self.assertEqual(result['entries'], [])
        with self.assertRaises(ValueError):
            operate(None, 'list', {'scope': 'host', 'path': ''})

    def test_project_operations_and_sends_do_not_gain_host_scope(self):
        for action in ('upload', 'references', 'git-status', 'git-action'):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                self.read(action, (self.other/'报告.txt').as_posix())
        with self.assertRaises(PermissionError):
            operate(str(self.project), 'preview', {'path': '../other/报告.txt'})
        (self.project/'inside.txt').write_text('inside')
        self.assertEqual(self.read('info', 'inside.txt')['projectPath'], 'inside.txt')
        self.assertEqual(self.read('list')['entries'][0]['projectPath'], 'inside.txt')

    @unittest.skipIf(os.name == 'nt', 'Creating symlinks requires a separate Windows capability')
    def test_global_browsing_still_rejects_symlink_reads(self):
        (self.project/'link').symlink_to(self.other, target_is_directory=True)
        rows = self.read('list')['entries']
        self.assertEqual(rows[0]['kind'], 'blocked')
        with self.assertRaises((ValueError, PermissionError)):
            self.read('preview', (self.project/'link/报告.txt').as_posix())

    def test_parent_navigation_stops_at_native_filesystem_root(self):
        root = Path(self.root.anchor).as_posix()
        self.assertIsNone(self.read('list', root)['parentPath'])
        with self.assertRaises((ValueError, PermissionError)):
            self.read('preview', self.other.as_posix()+'/../other/报告.txt')
