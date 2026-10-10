"""Exercise real Windows junctions and case-insensitive upload boundaries."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from bridge.features.workspace.workspace import Workspace


@unittest.skipUnless(os.name == 'nt', 'Windows filesystem semantics')
class WindowsWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cmb-workspace-windows-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.outside = self.root / 'outside'
        self.project.mkdir()
        self.outside.mkdir()
        (self.outside / 'fixture.txt').write_text('outside fixture', encoding='utf-8')
        self.workspace = Workspace(self.project)
        self.payload = base64.b64encode(b'upload fixture').decode()

    def junction(self):
        import _winapi
        link = self.project / 'linked'
        _winapi.CreateJunction(str(self.outside), str(link))
        # Remove only the junction, before TemporaryDirectory cleans its targets.
        self.addCleanup(os.rmdir, link)
        return link

    def test_junction_read_info_and_download_cannot_leave_project(self):
        self.junction()
        for action in (lambda: self.workspace.read('linked/fixture.txt'),
                       lambda: self.workspace.info('linked/fixture.txt')):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                action()
        with self.assertRaises(PermissionError):
            with self.workspace.download('linked/fixture.txt'):
                pass

    def test_junction_upload_cannot_write_outside_project(self):
        self.junction()
        with self.assertRaises(PermissionError):
            self.workspace.upload('linked/new.txt', self.payload)
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ['fixture.txt'])

    def test_junction_is_blocked_in_listing_and_archive(self):
        self.junction()
        entry = next(row for row in self.workspace.listing()['entries'] if row['name'] == 'linked')
        self.assertEqual(entry['kind'], 'blocked')
        with self.assertRaises(PermissionError):
            with self.workspace.archive('linked'):
                pass

    def test_git_upload_rejects_case_and_win32_name_aliases(self):
        (self.project / '.git' / 'hooks').mkdir(parents=True)
        for name in ('.git', '.GIT', '.Git', '.git.', '.GIT '):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                self.workspace.upload(name + '/hooks/fixture.txt', self.payload)
        self.assertEqual(list((self.project / '.git' / 'hooks').iterdir()), [])

    def test_nested_git_upload_is_also_blocked(self):
        (self.project / 'nested' / '.git').mkdir(parents=True)
        with self.assertRaises(PermissionError):
            self.workspace.upload('nested/.GIT/config', self.payload)
        self.assertFalse((self.project / 'nested' / '.git' / 'config').exists())

    def test_ordinary_unicode_upload_read_and_download_still_work(self):
        folder = self.project / 'folder \u4e2d\u6587'
        folder.mkdir()
        name = folder.name + '/data.txt'
        self.workspace.upload(name, self.payload)
        self.assertEqual(self.workspace.read(name)['text'], 'upload fixture')
        with self.workspace.download(name) as (_, stream):
            self.assertEqual(stream.read(), b'upload fixture')


if __name__ == '__main__':
    unittest.main()
