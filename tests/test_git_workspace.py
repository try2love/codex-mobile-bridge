import contextlib
import io
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bridge.workspace import Workspace, GitWorkspace, operate, MAX_PREVIEW
from bridge.service import Bridge
from bridge.remote import RemoteStore
import test_bridge as support


class GitWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp')
        self.root = Path(self.temp.name) / 'project'; self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.invalid')
        self.work = Workspace(self.root)

    def tearDown(self): self.temp.cleanup()
    def git(self, *args, check=True):
        return subprocess.run(['git', *args], cwd=self.root, env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}, capture_output=True, check=check)
    def status(self): return GitWorkspace(self.work).status()
    def diff(self, path, section): return GitWorkspace(self.work).diff(path, section)
    def baseline(self):
        (self.root / 'hello.txt').write_text('first\nsecond\n')
        self.git('add', '.'); self.git('commit', '-qm', 'fixture baseline')

    def test_unborn_clean_and_detached(self):
        self.assertEqual(self.status()['commit'], '(initial)')
        (self.root / 'new.txt').write_text('new\n'); self.git('add', '.')
        self.assertIn('+new', self.diff('new.txt', 'staged')['text'])
        self.git('commit', '-qm', 'first')
        self.assertEqual(self.status()['entries'], [])
        self.git('checkout', '-q', '--detach')
        self.assertEqual(self.status()['branch'], '(detached)')

    def test_staged_unstaged_and_read_only_index(self):
        self.baseline(); target = self.root / 'hello.txt'
        target.write_text('staged\nsecond\n'); self.git('add', '.')
        target.write_text('working\nsecond\n')
        before = (self.root / '.git/index').read_bytes()
        self.assertEqual(self.status()['entries'][0]['index'], 'M')
        self.assertEqual(self.status()['entries'][0]['worktree'], 'M')
        self.assertIn('+staged', self.diff('hello.txt', 'staged')['text'])
        self.assertNotIn('+working', self.diff('hello.txt', 'staged')['text'])
        self.assertIn('-staged', self.diff('hello.txt', 'unstaged')['text'])
        self.assertIn('+working', self.diff('hello.txt', 'unstaged')['text'])
        self.assertEqual(before, (self.root / '.git/index').read_bytes())

    def test_untracked_ignored_binary_large_and_no_newline(self):
        self.baseline(); (self.root / '.gitignore').write_text('ignored.txt\n'); (self.root / 'ignored.txt').write_text('private')
        (self.root / '中文 [x]*.txt').write_text('<img src=x>')
        self.assertIn('+<img src=x>\n\\ No newline', self.diff('中文 [x]*.txt', 'untracked')['text'])
        self.assertNotIn('ignored.txt', [e['path'] for e in self.status()['entries']])
        with self.assertRaises(ValueError): self.diff('ignored.txt', 'untracked')
        (self.root / 'binary').write_bytes(b'\x00\xff')
        self.assertEqual(self.diff('binary', 'untracked')['kind'], 'binary')
        (self.root / 'big').write_bytes(b'a' * (MAX_PREVIEW + 1))
        self.assertEqual(self.diff('big', 'untracked')['kind'], 'large')
        self.git('add', 'big')
        self.assertEqual(self.diff('big', 'staged')['kind'], 'large')

    def test_rename_delete_and_intent_to_add(self):
        self.baseline(); self.git('mv', 'hello.txt', 'renamed.txt')
        entry = self.status()['entries'][0]; self.assertEqual(entry['oldPath'], 'hello.txt')
        self.assertTrue(self.diff('renamed.txt', 'staged')['metadataOnly'])
        (self.root / 'renamed.txt').unlink()
        self.assertIn('-first', self.diff('renamed.txt', 'unstaged')['text'])
        (self.root / 'intent.txt').write_text('intent\n'); self.git('add', '-N', 'intent.txt')
        self.assertIn('+intent', self.diff('intent.txt', 'unstaged')['text'])

    def test_project_subdirectory_scope_and_inherited_git_environment(self):
        self.baseline(); (self.root / 'nested').mkdir(); (self.root / 'nested/file.txt').write_text('nested\n')
        self.git('add', '.'); self.git('commit', '-qm', 'nested')
        (self.root / 'hello.txt').write_text('outside\n'); (self.root / 'nested/file.txt').write_text('inside\n')
        nested = GitWorkspace(Workspace(self.root / 'nested'))
        state = nested.status(); self.assertEqual([e['path'] for e in state['entries']], ['file.txt'])
        self.assertIn('+inside', nested.diff('file.txt', 'unstaged')['text'])
        for path in ('../hello.txt', '/etc/passwd', 'file.txt/../../hello.txt'):
            with self.assertRaises((ValueError, PermissionError)): nested.diff(path, 'unstaged')
        with patch.dict(os.environ, {'GIT_DIR': '/nonexistent', 'GIT_WORK_TREE': '/nonexistent'}):
            self.assertTrue(self.status()['available'])

    def test_conflicts_and_symlink_boundary(self):
        self.baseline(); self.git('checkout', '-qb', 'other'); (self.root / 'hello.txt').write_text('other\n'); self.git('commit', '-qam', 'other')
        self.git('checkout', '-q', 'main'); (self.root / 'hello.txt').write_text('main\n'); self.git('commit', '-qam', 'main')
        self.git('merge', 'other', check=False)
        self.assertTrue(self.status()['entries'][0]['conflict'])
        self.assertIn('<<<<<<<', self.diff('hello.txt', 'conflict')['text'])
        with self.assertRaises(ValueError): self.diff('hello.txt', 'staged')
        (self.root / 'external').symlink_to(self.root.parent / 'private.txt')
        (self.root.parent / 'private.txt').write_text('secret')
        with self.assertRaises((ValueError, OSError)): self.diff('external', 'untracked')

    def test_configured_filters_are_not_executed(self):
        self.baseline(); marker = self.root / 'filter-ran'
        (self.root / '.gitattributes').write_text('hello.txt filter=fixture\n')
        self.git('config', 'filter.fixture.clean', 'touch filter-ran; cat')
        self.git('config', 'filter.fixture.required', 'true')
        (self.root / 'hello.txt').write_text('changed content\n')
        self.status(); self.diff('hello.txt', 'unstaged')
        self.assertFalse(marker.exists())
        self.assertEqual(self.git('config', 'filter.fixture.required').stdout.strip(), b'true')

    def test_nonrepo_and_missing_git(self):
        plain = Path(self.temp.name) / 'plain'; plain.mkdir()
        # Stop discovery at this fixture rather than finding the enclosing source repo.
        subprocess.run(['git', 'init', '--bare', '-q', str(plain)], check=True)
        self.assertFalse(GitWorkspace(Workspace(plain)).status()['available'])
        with patch('bridge.workspace.subprocess.Popen', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, '未安装 Git'): self.status()

    def test_remote_routing_does_not_activate_owner(self):
        self.baseline(); (self.root / 'hello.txt').write_text('remote change\n')
        store = RemoteStore('fixture-host'); store.get = lambda _: {'cwd': str(self.root)}
        bridge = SimpleNamespace(host='remote:fixture', store=store)
        def execute(alias, source, timeout):
            self.assertEqual(alias, 'fixture-host'); output = io.StringIO()
            with contextlib.redirect_stdout(output): exec(compile(source, '<remote-git>', 'exec'), {})
            return json.loads(output.getvalue())
        with patch('bridge.service.ssh_read', side_effect=execute):
            self.assertEqual(Bridge.workspace(bridge, support.THREAD, 'git-status', {})['branch'], 'main')
            self.assertIn('+remote change', Bridge.workspace(bridge, support.THREAD, 'git-diff', {'path': 'hello.txt', 'section': 'unstaged'})['text'])


class GitHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    request = support.HttpTests.request
    login = support.HttpTests.login
    def setUp(self):
        support.HttpTests.setUp(self)
        self.calls = []
        def workspace(thread, action, params):
            self.calls.append((thread, action, params)); return {'available': True, 'entries': []}
        self.server.bridge.workspace = workspace
        self.path = '/api/sessions/' + support.THREAD + '/workspace/'
    def tearDown(self): support.HttpTests.tearDown(self)
    def test_auth_routing_and_no_write_endpoint(self):
        self.assertEqual(self.request('GET', self.path + 'git-status')[0], 401)
        auth = self.login()
        self.assertEqual(self.request('GET', self.path + 'git-status', headers=auth)[0], 200)
        self.assertEqual(self.calls[-1][1:], ('git-status', {}))
        self.assertEqual(self.request('GET', self.path + 'git-diff?path=a.txt&section=staged', headers=auth)[0], 200)
        self.assertEqual(self.calls[-1][2], {'path': 'a.txt', 'section': 'staged'})
        self.assertEqual(self.request('POST', self.path + 'git-diff', {}, auth)[0], 405)
        self.assertEqual(self.request('GET', self.path + 'git-status?host=wrong', headers=auth)[0], 404)
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        connection.request('GET', '/git-panel.js'); response = connection.getresponse()
        self.assertEqual(response.status, 200); self.assertIn(b'class GitPanel', response.read()); connection.close()
