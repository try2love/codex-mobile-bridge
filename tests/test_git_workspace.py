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
        self.git('config', 'commit.gpgsign', 'false'); self.git('config', 'core.hooksPath', '.git/hooks')
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

    def mutate(self, action, **params):
        return GitWorkspace(self.work).mutate(action, self.status()['version'], **params)

    def test_management_staging_commit_preserves_unstaged_content(self):
        self.baseline(); path = self.root / 'hello.txt'; path.write_text('staged version\n')
        self.mutate('stage', path='hello.txt'); path.write_text('later working version\n')
        snapshot = self.status()['version']
        result = GitWorkspace(self.work).mutate('commit', snapshot, message='Commit staged content')
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(self.git('show', 'HEAD:hello.txt').stdout, b'staged version\n')
        self.assertEqual(path.read_text(), 'later working version\n')
        self.assertEqual(self.status()['entries'][0]['worktree'], 'M')
        with self.assertRaisesRegex(ValueError, '已发生变化'):
            GitWorkspace(self.work).mutate('commit', snapshot, message='Do not duplicate')
        self.assertEqual(self.git('rev-list', '--count', 'HEAD').stdout.strip(), b'2')
        self.mutate('stage-all'); self.mutate('unstage-all')
        self.assertEqual(self.git('diff', '--cached').stdout, b'')

    def test_management_unborn_unstage_and_deleted_directory(self):
        (self.root / 'new.txt').write_text('first')
        self.mutate('stage', path='new.txt'); (self.root / 'new.txt').write_text('second')
        self.mutate('unstage', path='new.txt')
        self.assertEqual((self.root / 'new.txt').read_text(), 'second')
        self.mutate('stage-all'); self.mutate('commit', message='First commit')
        (self.root / 'folder').mkdir(); (self.root / 'folder/a.txt').write_text('file')
        self.mutate('stage-all'); self.mutate('commit', message='Folder')
        (self.root / 'folder/a.txt').unlink(); (self.root / 'folder').rmdir()
        self.mutate('stage', path='folder/a.txt')
        self.assertEqual(self.status()['entries'][0]['index'], 'D')

    def test_management_rejects_stale_state_and_dirty_branch_switch(self):
        self.baseline(); self.mutate('create-branch', branch='feature/demo')
        self.assertEqual(self.status()['branch'], 'feature/demo')
        path = self.root / 'hello.txt'; path.write_text('one')
        stale = self.status()['version']; path.write_text('two changed')
        with self.assertRaisesRegex(ValueError, '已发生变化'):
            GitWorkspace(self.work).mutate('stage', stale, path='hello.txt')
        with self.assertRaisesRegex(ValueError, '未提交'):
            self.mutate('switch-branch', branch='main')
        self.assertEqual(path.read_text(), 'two changed')
        with self.assertRaises(ValueError): self.mutate('reset', branch='main')

    def test_management_merge_conflict_abort_and_resolved_commit(self):
        self.baseline(); self.mutate('create-branch', branch='feature/demo')
        (self.root / 'hello.txt').write_text('feature\n'); self.mutate('stage-all'); self.mutate('commit', message='Feature')
        self.mutate('switch-branch', branch='main')
        (self.root / 'hello.txt').write_text('main\n'); self.mutate('stage-all'); self.mutate('commit', message='Main')
        result = self.mutate('merge', branch='feature/demo')
        self.assertEqual(result['outcome'], 'conflict'); self.assertEqual(self.status()['operation'], 'merge')
        with self.assertRaisesRegex(ValueError, '冲突'): self.mutate('commit', message='Cannot commit unresolved conflict')
        self.mutate('abort-merge'); self.assertEqual((self.root / 'hello.txt').read_text(), 'main\n')
        self.assertIsNone(self.status()['operation'])
        self.mutate('merge', branch='feature/demo'); (self.root / 'hello.txt').write_text('main\n')
        self.mutate('stage-all'); self.assertEqual(self.status()['entries'], [])
        self.mutate('commit', message='Resolve merge while retaining current content')
        self.assertIsNone(self.status()['operation'])
        self.assertEqual(len(self.git('show', '-s', '--format=%P', 'HEAD').stdout.split()), 2)

    def test_history_graph_details_and_first_parent_merge_diff(self):
        self.baseline(); initial = self.git('rev-parse', 'HEAD').stdout.decode().strip()
        self.mutate('create-branch', branch='feature/demo')
        (self.root / 'feature.txt').write_text('feature\n'); self.mutate('stage-all'); self.mutate('commit', message='Feature work')
        self.mutate('switch-branch', branch='main')
        (self.root / 'main.txt').write_text('main\n'); self.mutate('stage-all'); self.mutate('commit', message='Main work')
        self.mutate('merge', branch='feature/demo'); self.git('tag', 'fixture-v1')
        work = GitWorkspace(self.work); history = work.history(limit=2)
        self.assertEqual(len(history['commits']), 2); self.assertTrue(history['hasMore'])
        tip = history['commits'][0]; self.assertEqual(len(tip['parents']), 2); self.assertIn('fixture-v1', tip['refs'])
        self.assertEqual({c['id'] for c in work.history(limit=40)['commits']}, set(self.git('rev-list', '--all').stdout.decode().split()))
        detail = work.commit_detail(tip['id']); self.assertEqual([e['path'] for e in detail['entries']], ['feature.txt'])
        self.assertIn('+feature', work.historical_diff(tip['id'], 'feature.txt')['text'])
        self.assertIn('+first', work.historical_diff(initial, 'hello.txt')['text'])
        with self.assertRaises(ValueError): work.historical_diff(tip['id'], 'hello.txt')
        with self.assertRaises(ValueError): work.commit_detail('--all')
        self.assertEqual(len(work.branches()['branches']), 2)

    def test_management_subdirectory_cannot_commit_other_project_files(self):
        self.baseline(); (self.root / 'nested').mkdir(); (self.root / 'nested/a.txt').write_text('nested')
        self.git('add', 'hello.txt'); (self.root / 'hello.txt').write_text('outside'); self.git('add', 'hello.txt')
        nested = GitWorkspace(Workspace(self.root / 'nested'))
        state = nested.status(); nested.mutate('stage-all', state['version'])
        with self.assertRaisesRegex(ValueError, '仓库根目录'):
            nested.mutate('commit', nested.status()['version'], message='Do not include outside files')
        self.assertIn(b'hello.txt', self.git('diff', '--cached', '--name-only').stdout)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD').stdout.strip(), b'1')

    def test_management_commit_hooks_are_respected_and_failures_keep_index(self):
        self.baseline(); hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o700)
        (self.root / 'hello.txt').write_text('pending'); self.mutate('stage-all')
        with self.assertRaisesRegex(ValueError, '未完成'): self.mutate('commit', message='Blocked by hook')
        self.assertEqual(self.git('rev-list', '--count', 'HEAD').stdout.strip(), b'1')
        self.assertEqual(self.status()['entries'][0]['index'], 'M')


    def test_remote_management_and_history_use_remote_repository(self):
        self.baseline(); (self.root / 'hello.txt').write_text('remote staged change\n')
        store = RemoteStore('fixture-host'); store.get = lambda _: {'cwd': str(self.root)}
        bridge = SimpleNamespace(host='remote:fixture', store=store)
        def execute(alias, source, timeout):
            self.assertEqual(alias, 'fixture-host'); output = io.StringIO()
            with contextlib.redirect_stdout(output): exec(compile(source, '<remote-git>', 'exec'), {})
            return json.loads(output.getvalue())
        with patch('bridge.service.ssh_read', side_effect=execute):
            state = Bridge.workspace(bridge, support.THREAD, 'git-status', {})
            staged = Bridge.workspace(bridge, support.THREAD, 'git-action', {'action':'stage-all', 'version':state['version']})
            committed = Bridge.workspace(bridge, support.THREAD, 'git-action', {'action':'commit', 'version':staged['state']['version'], 'message':'Remote fixture commit'})
            self.assertEqual(committed['outcome'], 'success')
            history = Bridge.workspace(bridge, support.THREAD, 'git-history', {})
            self.assertEqual(history['commits'][0]['subject'], 'Remote fixture commit')

    def test_concurrent_mobile_writes_are_serialized(self):
        self.baseline(); (self.root / 'hello.txt').write_text('pending')
        owner = GitWorkspace(self.work); state = owner.require_repository()
        with owner.mutation_lock():
            with self.assertRaisesRegex(ValueError, '另一个 Git 操作'):
                GitWorkspace(self.work).mutate('stage-all', state['version'])
        self.assertEqual(self.git('diff', '--cached').stdout, b'')


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

    def test_mutations_require_csrf_and_explicit_action(self):
        target = self.path + 'git-action'
        auth = self.login()
        self.assertEqual(self.request('GET', target, headers=auth)[0], 405)
        self.assertEqual(self.request('POST', target, {'action':'commit','version':'fixture'}, {'Cookie':auth['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', target, {'action':'commit','version':'fixture'}, {**auth,'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request('POST', target, {'action':'stage','version':'fixture','path':'a.txt'}, auth)[0], 200)
        self.assertEqual(self.calls[-1][1], 'git-action')
        self.assertEqual(self.request('POST', target, {'action':'stage','version':'fixture','root':'/outside'}, auth)[0], 400)
