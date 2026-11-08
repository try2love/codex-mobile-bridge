import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge, LiveSession
from bridge.clients.codex.catalog import Catalog
from bridge.clients.codex.remote import RemoteCatalog


ROOT = Path(__file__).resolve().parents[1]


class ModelIdentityViews(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home/'auth.json').write_text('{"OPENAI_API_KEY":"fixture-key"}')
        (self.home/'config.toml').write_text('model_provider = "openai"\n')
        self.sid = str(uuid.uuid4())
        self.session = LiveSession(self.sid)
        self.session.state = {'id': self.sid, 'cwd': '/different-chat', 'modelProvider': 'openai',
                              'latestModel': 'fixture', 'threadRuntimeStatus': {'type': 'idle'},
                              'turns': [], 'requests': []}

    def bridge(self, host, reader):
        bridge = Bridge.__new__(Bridge)
        bridge.host = host
        bridge.catalog_reader = reader
        bridge.hosts = SimpleNamespace(hosts=lambda: {'ssh-host': {'displayName': 'SSH'}})
        bridge.session = Mock(return_value=self.session)
        bridge._refresh_session_goal = Mock(return_value=False)
        bridge._access = Mock(return_value={})
        bridge._draft_account_identity = Mock(return_value='')
        bridge.goal_transport = 'owner'
        bridge._goal_activation_ids = Mock(return_value=[])
        bridge._overlay_native_goal = Mock(side_effect=lambda session, sid, view: view)
        bridge._submission_meta = Mock()
        bridge._fork_origin = Mock(return_value=None)
        return bridge

    def test_remote_view_does_not_query_or_invent_model_identity(self):
        reader = RemoteCatalog('ssh-host')
        bridge = self.bridge('ssh-host', reader)
        with patch('bridge.clients.codex.remote.ssh_read', side_effect=AssertionError('remote query')):
            view = bridge.view(self.sid)
        self.assertEqual(view['host'], 'ssh-host')
        self.assertNotIn('modelCatalogIdentity', view)

    def test_remote_view_does_not_wait_for_draft_identity_ssh(self):
        bridge = self.bridge('ssh-host', RemoteCatalog('ssh-host'))
        bridge._draft_account_identity = Bridge._draft_account_identity.__get__(bridge)
        bridge.cli_sessions = SimpleNamespace(alias='ssh-alias', remote_home=Path('/remote/codex'))
        bridge._draft_remote_lock = threading.Lock()
        bridge._draft_remote_cache = {}
        bridge._draft_remote_pending = set()
        started, release = threading.Event(), threading.Event()
        def slow_read(*args, **kwargs):
            started.set()
            release.wait(2)
            return {'identity': 'remote-owner'}
        try:
            with patch('bridge.app.service.ssh_read', side_effect=slow_read) as read:
                begin = time.monotonic()
                self.assertEqual(bridge.view(self.sid)['draftAccountIdentity'], '')
                self.assertLess(time.monotonic()-begin, 0.2)
                self.assertTrue(started.wait(1))
                self.assertEqual(bridge.view(self.sid)['draftAccountIdentity'], '')
                read.assert_called_once()
                release.set()
                for _ in range(100):
                    value = bridge.view(self.sid)['draftAccountIdentity']
                    if value:
                        break
                    time.sleep(0.01)
                self.assertEqual(len(value), 64)
        finally:
            release.set()

    def test_local_view_uses_exact_project_catalog_not_global_account(self):
        reader = Catalog(self.home, 'fixture-runtime')
        bridge = self.bridge('local', reader)
        identity = reader.model_identity('/different-chat', 'openai')
        bridge.accounts = SimpleNamespace(
            current_model_identity=Mock(side_effect=AssertionError('global account identity')),
            current_models=Mock(side_effect=AssertionError('global account catalog')))
        bridge.store = SimpleNamespace(home=self.home)
        with patch('bridge.app.service.artifact_paths', return_value={}), \
             patch.object(reader, '_fetch', return_value={'models': [{'model': 'fixture'}]}):
            view = bridge.view(self.sid)
            catalog = bridge.catalog(self.sid, kind='models')
        self.assertEqual(view['modelCatalogIdentity'], identity)
        self.assertEqual(catalog['cache']['identity'], identity)
        self.assertNotEqual(identity, reader.model_identity(str(self.home), 'openai'))

    def test_unresolved_default_view_does_not_preempt_first_native_catalog_identity(self):
        reader = Catalog(self.home, 'fixture-runtime', allow_background_refresh=False)
        self.session.state.pop('modelProvider')
        bridge = self.bridge('local', reader)
        bridge.accounts = None
        bridge.store = SimpleNamespace(home=self.home)
        with patch('bridge.app.service.artifact_paths', return_value={}), \
             patch.object(reader, '_fetch', return_value={
                 'models': [{'model': 'fixture'}], '_defaultProvider': 'project-route'}) as fetch:
            self.assertNotIn('modelCatalogIdentity', bridge.view(self.sid))
            fetch.assert_not_called()
            result = bridge.catalog(self.sid, kind='models')
            self.assertEqual(result['cache']['identity'], reader.model_identity('/different-chat', 'project-route'))
            self.assertEqual(bridge.view(self.sid)['modelCatalogIdentity'], result['cache']['identity'])
            fetch.assert_called_once()

    def test_side_chat_state_uses_its_own_catalog_scope(self):
        reader = Catalog(self.home, 'fixture-runtime')
        bridge = self.bridge('local', reader)
        bridge.accounts = None
        bridge.store = SimpleNamespace(get=Mock())
        bridge._require_desktop_feature = Mock()
        child = str(uuid.uuid4())
        bridge.side_chats = SimpleNamespace(operate=Mock(return_value={
            'id': child, 'connected': True, 'cwd': '/side-project', 'provider': 'custom'}))
        state = bridge.side_chat(self.sid, None, 'read', {})
        self.assertEqual(state['modelCatalogIdentity'], reader.model_identity('/side-project', 'custom'))
        bridge.session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
