"""SSH homes retain target POSIX semantics on a Windows gateway."""
import tempfile
import unittest
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import Mock, patch

from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.clients.codex.cli_session import CliSessions
from bridge.clients.codex.remote import RemoteCliModels
from tests.test_codex_cli_session import FakeClient


class RemoteHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.sessions = CliSessions(Path(self.temp.name), lambda *_: None, lambda *_: None, alias='fixture-host')
        self.fake = FakeClient(Path(self.temp.name))
        self.fake.server_info['codexHome'] = '/home/fixture/.codex'
        self.sessions.client = self.fake
        self.sessions.store.get = Mock(return_value={'origin': 'cli', 'cwd': self.fake.thread['cwd'],
                                                    'model_provider': self.fake.thread['modelProvider']})

    def test_windows_gateway_resumes_and_reads_context_in_same_remote_home(self):
        self.fake.loaded = False
        with patch('bridge.clients.codex.cli_session.Path', PureWindowsPath):
            self.assertEqual(self.sessions.attach('thread-a', explicit=True)['id'], 'thread-a')
            self.assertEqual(self.sessions.creation_context('/work/project'), {})
            self.sessions.archive_call('thread-a', False, [], disconnect=False)()
        self.assertEqual(self.sessions.remote_home, PurePosixPath('/home/fixture/.codex'))
        self.assertEqual([method for method, _ in self.fake.sent].count('thread/resume'), 1)
        self.assertEqual(self.fake.sent[-1][0], 'thread/unarchive')

    def test_windows_gateway_creates_in_remote_posix_home(self):
        identifier = str(uuid.uuid4())
        original = self.fake.request
        def request(method, params, **kwargs):
            if method == 'thread/start':
                self.fake.thread.update(id=identifier, cwd=params['cwd'])
                return {'thread': dict(self.fake.thread), 'model': 'original-model'}
            return original(method, params, **kwargs)
        self.fake.request = request
        with patch('bridge.clients.codex.cli_session.Path', PureWindowsPath):
            result = self.sessions.create('/work/new-project', 'fixture')
        self.assertEqual((result['id'], result['cwd']), (identifier, '/work/new-project'))

    def test_changed_relative_or_windows_remote_home_cannot_mutate(self):
        self.sessions._runtime_home(self.fake.server_info)
        for home in ('relative/.codex', 'C:\\Users\\fixture\\.codex', '/home/other/.codex', ''):
            with self.subTest(home=home), patch('bridge.clients.codex.cli_session.Path', PureWindowsPath):
                self.fake.server_info['codexHome'] = home
                with self.assertRaises(AppServerUnavailable):
                    self.sessions.archive_call('thread-a', True, [])
        self.assertEqual(self.fake.sent, [])

    def test_model_helper_serializes_pure_remote_home_without_local_mapping(self):
        with patch('bridge.clients.codex.remote.ssh_read', return_value={'identity': 'a' * 64}) as read:
            RemoteCliModels('fixture-host').identity(PurePosixPath('/home/fixture/.codex'),
                '/work/project', 'openai', {})
        self.assertIn('/home/fixture/.codex', __import__('base64').b64decode(
            read.call_args.args[1].split('b64decode(')[-1].split(')')[0].strip("'\"")).decode())


if __name__ == '__main__':
    unittest.main()
