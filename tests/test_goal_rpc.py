import json
import os
import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.goal import GoalRPC, native_goal_absent, read_native_goal, sqlite_read_uri


class FakeStdin:
    def __init__(self):
        self.writes = []

    def write(self, value):
        self.writes.append(json.loads(value))

    def flush(self):
        pass

    def close(self):
        pass


class FakeStdout:
    def __init__(self):
        self.lines = [
            json.dumps({'id': 1, 'result': {'clientId': 'gateway'}}),
            json.dumps({'id': 2, 'result': {'goal': {'objective': 'Work', 'status': 'active'}}}),
        ]

    def __iter__(self):
        return iter(tuple(self.lines))

    def close(self):
        pass


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()

    def wait(self, timeout=None):
        return 0


class FakeSubprocess:
    PIPE = 'pipe'
    DEVNULL = 'devnull'
    CREATE_NO_WINDOW = 0x08000000

    def __init__(self, process):
        self.process = process

    def Popen(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return self.process


def goal_row(thread_id='tid'):
    return {'thread_id': thread_id, 'goal_id': 'gid', 'objective': 'Work', 'status': 'active',
            'token_budget': None, 'tokens_used': 1, 'time_used_seconds': 2,
            'created_at_ms': 3, 'updated_at_ms': 4}


def create_database(root, thread_id='tid', status='active'):
    path = Path(root) / 'goals # 中文' / 'goals_1.sqlite'
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute('create table thread_goals(thread_id text primary key, goal_id text, objective text, status text, token_budget integer, tokens_used integer, time_used_seconds integer, created_at_ms integer, updated_at_ms integer)')
        connection.execute('insert into thread_goals values(?,?,?,?,?,?,?,?,?)',
                           (thread_id, 'gid', 'Work', status, None, 1, 2, 3, 4))
        connection.commit()
    finally:
        connection.close()
    return path


class SQLiteURITests(unittest.TestCase):
    def test_read_uri_escapes_platform_and_unicode_paths(self):
        path = Path(tempfile.gettempdir()) / 'codex space 中文 #x' / 'goals_1.sqlite'
        uri = sqlite_read_uri(path)
        self.assertTrue(uri.startswith('file://'))
        self.assertIn('%23', uri)
        self.assertNotIn('#', uri.rsplit('?', 1)[0])

    def test_read_and_absent_use_special_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'codex space 中文 #x'
            path = create_database(root)
            row = read_native_goal(path.parent, 'tid')
            self.assertEqual(row['objective'], 'Work')
            self.assertTrue(native_goal_absent(path.parent, 'other'))

    def test_windows_uri_is_percent_encoded(self):
        from pathlib import PureWindowsPath
        uri = PureWindowsPath(r'C:\Users\Luoran 中文\goals_1.sqlite').as_uri() + '?mode=ro'
        self.assertTrue(uri.startswith('file:///C:/'))
        self.assertIn('?mode=ro', uri)


class GoalRPCProcessTests(unittest.TestCase):
    def start_rpc(self, os_name):
        process = FakeProcess()
        fake_subprocess = FakeSubprocess(process)
        home = Path(tempfile.mkdtemp(prefix='goal-rpc-'))
        rpc = GoalRPC(home, Path(__file__))
        with patch_module('bridge.goal.os.name', os_name), \
             patch_module('bridge.goal.subprocess', fake_subprocess):
            rpc.start()
        return rpc, fake_subprocess, home, process

    def test_posix_process_options(self):
        rpc, fake, home, process = self.start_rpc('posix')
        self.assertEqual(fake.kwargs['cwd'], home)
        self.assertEqual(fake.kwargs['env']['CODEX_HOME'], str(home))
        self.assertNotIn('creationflags', fake.kwargs)
        self.assertIn({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'codex_mobile_goal', 'version': '0.1'},
            'capabilities': {'experimentalApi': True}}}, process.stdin.writes)
        self.assertIn({'method': 'initialized'}, process.stdin.writes)

    def test_windows_process_options(self):
        rpc, fake, home, process = self.start_rpc('nt')
        self.assertEqual(fake.kwargs['cwd'], home)
        self.assertEqual(fake.kwargs['env']['CODEX_HOME'], str(home))
        self.assertEqual(fake.kwargs['creationflags'], FakeSubprocess.CREATE_NO_WINDOW)

    def test_new_process_queue_ignores_old_sentinel_and_serves_request(self):
        process = FakeProcess()
        fake_subprocess = FakeSubprocess(process)
        home = Path(tempfile.mkdtemp(prefix='goal-rpc-'))
        rpc = GoalRPC(home, Path(__file__))
        old_queue = queue.Queue()
        old_queue.put(None)
        rpc.messages = old_queue
        with patch_module('bridge.goal.subprocess', fake_subprocess):
            rpc.start()
        self.assertIsNot(rpc.messages, old_queue)
        self.assertEqual(rpc.get_goal('tid'), {'objective': 'Work', 'status': 'active'})
        self.assertEqual(process.stdin.writes[-1]['id'], 2)


def patch_module(name, value):
    return patch(name, value)


if __name__ == '__main__':
    unittest.main()


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_windows_runtime_discovery_reads_local_app_candidates(self):
        from bridge.catalog import Catalog
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            local=Path(directory)
            candidate=local/'OpenAI/Codex/bin/0.1/codex.exe'
            candidate.parent.mkdir(parents=True);candidate.write_text('fake')
            fake_os=SimpleNamespace(name='nt',environ={'LOCALAPPDATA':str(local)})
            fake_subprocess=SimpleNamespace(CREATE_NO_WINDOW=0x08000000,run=lambda *args,**kwargs:None)
            with patch('bridge.catalog.os',fake_os), \
                 patch('bridge.catalog.sys.platform','nt'), \
                 patch('bridge.catalog.subprocess',fake_subprocess):
                self.assertEqual(Catalog.find_runtime().resolve(),candidate.resolve())

    def test_linux_runtime_discovery_follows_bundled_launcher(self):
        from bridge.catalog import Catalog
        with tempfile.TemporaryDirectory() as directory:
            launcher=Path(directory)/'codex'
            bundled=Path(directory)/'resources/codex-cli/bin/codex'
            bundled.parent.mkdir(parents=True);bundled.write_text('#!/bin/sh\n');bundled.chmod(0o755)
            launcher.write_text('#!/bin/sh\n');launcher.chmod(0o755)
            with patch('bridge.catalog.shutil.which',return_value=str(launcher)):
                self.assertTrue(os.path.samefile(Catalog.find_linux_runtime(),bundled))


if __name__ == '__main__':
    unittest.main()
