import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bridge.clients.codex.catalog import CatalogIdentityChanged
from bridge.app.service import Bridge
from bridge.clients.codex.catalog import ModelStore
from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.platforms.codex_daemon_env import DaemonEnvironmentUnavailable


class NativeModelCacheTests(unittest.TestCase):
    def test_initial_read_failure_keeps_verified_manual_identity_without_caching_error(self):
        self.bridge.cli_sessions.get_kind.side_effect = AppServerUnavailable('offline')
        value = self.bridge._native_models('thread', self.temp.name, 'openai')
        self.assertEqual(value['cache'], {'state': 'unavailable', 'identity': 'identity-a'})
        self.assertEqual(value['models'], [])
        self.assertIsNone(self.bridge.native_model_store.read('identity-a'))

    def test_empty_directory_does_not_replace_last_good_models_or_timestamp(self):
        first = self.bridge._native_models('thread', self.temp.name, 'openai')
        self.bridge.cli_sessions.get_kind.return_value = {'models': []}
        value = self.bridge._native_models('thread', self.temp.name, 'openai', refresh=True)
        self.assertEqual(value['models'], first['models'])
        self.assertEqual(value['cache']['updatedAt'], first['cache']['updatedAt'])
        self.assertEqual(value['cache']['state'], 'stale')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.bridge = object.__new__(Bridge)
        self.bridge.native_model_store = ModelStore(self.temp.name)
        self.bridge.native_model_lock = threading.Lock()
        self.bridge.native_model_reads = {}
        self.bridge.native_model_timers = {}
        self.bridge.native_model_refreshing = set()
        self.bridge.closed = threading.Event()
        self.bridge.codex_home = Path(self.temp.name)
        self.bridge.host = 'local'
        self.bridge.cli_sessions = SimpleNamespace(alias=None, get_kind=Mock(return_value={
            'models': [{'id': 'gpt-fixture', 'efforts': ['high']}], 'modelSource': 'codex'}))
        self.bridge._native_model_identity = Mock(return_value='identity-a')
        self.addCleanup(lambda: [timer.cancel() for timer in self.bridge.native_model_timers.values()])

    def test_native_cli_model_list_is_reused_and_explicit_refresh_forces_read(self):
        first = self.bridge._native_models('thread', self.temp.name, 'openai')
        self.assertEqual(self.bridge._native_models('thread', self.temp.name, 'openai'), first)
        self.bridge.cli_sessions.get_kind.assert_called_once()
        self.bridge._native_models('thread', self.temp.name, 'openai', refresh=True)
        self.assertEqual(self.bridge.cli_sessions.get_kind.call_count, 2)

    def test_shared_native_catalog_never_reuses_another_threads_selection(self):
        self.bridge.cli_sessions.get_kind.return_value = {
            'models': [{'id': 'gpt-fixture'}], 'modelSource': 'codex',
            'currentModel': 'thread-a-model', 'currentEffort': 'high'}
        first = self.bridge._native_models('thread-a', self.temp.name, 'openai')
        self.assertEqual(first['currentModel'], 'thread-a-model')
        saved = self.bridge.native_model_store.read('identity-a')[1]
        self.assertNotIn('currentModel', saved)
        self.assertNotIn('currentEffort', saved)
        second = self.bridge._native_models('thread-b', self.temp.name, 'openai')
        self.assertNotIn('currentModel', second)
        self.assertNotIn('currentEffort', second)
        sessions = {
            thread: SimpleNamespace(origin='cli', runtime='cli', connected=True,
                condition=threading.Condition(), state={
                    'cwd': self.temp.name, 'latestModel': thread + '-model',
                    'latestReasoningEffort': effort}, saved_cwd=None, saved_provider='openai',
                view=lambda: {'provider': 'openai'})
            for thread, effort in (('thread-a', 'high'), ('thread-b', 'low'))}
        self.bridge.session = Mock(side_effect=lambda thread, **kwargs: sessions[thread])
        current = self.bridge.catalog('thread-b', kind='models')
        self.assertEqual((current['currentModel'], current['currentEffort']),
                         ('thread-b-model', 'low'))
        self.bridge.cli_sessions.get_kind.assert_called_once()
        self.bridge.native_model_store.write('identity-a', {**saved,
            'currentModel': 'old-schema-model', 'currentEffort': 'max'})
        old = self.bridge._native_models('thread-b', self.temp.name, 'openai')
        self.assertNotIn('currentModel', old)
        self.assertNotIn('currentEffort', old)

    def test_account_identity_change_rejects_inflight_model_result(self):
        def changed(*args, **kwargs):
            self.bridge._native_model_identity.return_value = 'identity-b'
            return {'models': [{'id': 'old-account'}]}
        self.bridge.cli_sessions.get_kind.side_effect = changed
        with self.assertRaisesRegex(CatalogIdentityChanged, '账号身份已变化'):
            self.bridge._native_models('thread', self.temp.name, 'openai')
        self.assertIsNone(self.bridge.native_model_store.read('identity-a'))

    def test_api_catalog_failure_does_not_stamp_empty_models_as_fresh(self):
        self.bridge.cli_sessions.get_kind.return_value = {
            'models': [], 'modelSource': 'api', 'modelError': '无法核验 API 凭据'}
        failed = self.bridge._native_models('thread', self.temp.name, 'custom')
        self.assertEqual(failed['models'], [])
        self.assertEqual(failed['modelError'], '无法核验 API 凭据')
        self.assertEqual(failed['cache'], {'state': 'unavailable', 'identity': 'identity-a'})
        self.assertIsNone(self.bridge.native_model_store.read('identity-a'))

        self.bridge.native_model_store.write('identity-a', {
            'models': [{'id': 'last-good'}], 'modelSource': 'api'})
        previous = self.bridge.native_model_store.read('identity-a')[0]
        retained = self.bridge._native_models('thread', self.temp.name, 'custom', refresh=True)
        self.assertEqual(retained['models'][0]['id'], 'last-good')
        self.assertEqual(retained['cache']['state'], 'stale')
        self.assertEqual(retained['cache']['updatedAt'], previous)
        self.assertEqual(self.bridge.native_model_store.read('identity-a')[0], previous)

    def test_native_identity_uses_account_and_config_without_model_list(self):
        bridge = self.bridge
        bridge.cli_sessions.client = SimpleNamespace(request=Mock(side_effect=lambda method, params, **kwargs:
            {'config': {'model_provider': 'openai'}} if method == 'config/read' else
            {'account': {'type': 'chatgpt', 'email': 'one@example.test'}}))
        first = Bridge._native_model_identity(bridge, self.temp.name, 'openai')
        self.assertEqual(bridge.cli_sessions.client.request.call_count, 2)
        bridge.cli_sessions.client.request.side_effect = lambda method, params, **kwargs: (
            {'config': {'model_provider': 'openai'}} if method == 'config/read' else
            {'account': {'type': 'chatgpt', 'email': 'two@example.test'}})
        self.assertNotEqual(first, Bridge._native_model_identity(bridge, self.temp.name, 'openai'))

    def test_unknown_cli_environment_cannot_grant_a_manual_catalog_identity(self):
        bridge = self.bridge
        bridge._native_model_identity = Bridge._native_model_identity.__get__(bridge)
        bridge.cli_sessions.client = SimpleNamespace(request=Mock(side_effect=lambda method, params, **kwargs:
            {'config': {'model_provider': 'custom'}} if method == 'config/read' else {'account': {}}))
        bridge.cli_sessions.model_environment_identity = Mock(side_effect=DaemonEnvironmentUnavailable('not verified'))
        bridge.cli_sessions.model_catalog = Mock(return_value={
            'models': [], 'modelSource': 'api', 'modelError': 'Unknown runtime environment'})
        with self.assertRaisesRegex(AppServerUnavailable, '无法核验'):
            bridge._native_models(None, self.temp.name, 'custom')
        bridge.cli_sessions.model_catalog.assert_not_called()

    def test_ssh_identity_tracks_target_credentials_and_host_without_local_auth(self):
        bridge = self.bridge
        bridge.host = 'ssh:host-a'
        bridge.cli_sessions.alias = 'host-a'
        bridge.cli_sessions.remote_home = '/remote/codex'
        bridge.cli_sessions.client = SimpleNamespace(request=Mock(side_effect=lambda method, params, **kwargs:
            {'config': {'model_provider': 'fixture'}} if method == 'config/read' else
            {'account': {'type': 'apiKey'}}))
        bridge.cli_sessions.model_environment_identity = Mock(return_value='remote-key-a')
        first = Bridge._native_model_identity(bridge, '/remote/project', 'fixture')
        bridge.cli_sessions.model_environment_identity.assert_called_once_with(
            {'model_provider': 'fixture'}, 'fixture', {'type': 'apiKey'}, cwd='/remote/project')
        (bridge.codex_home/'auth.json').write_text('{"OPENAI_API_KEY":"local-only"}')
        self.assertEqual(first, Bridge._native_model_identity(bridge, '/remote/project', 'fixture'))
        bridge.cli_sessions.model_environment_identity.return_value = 'remote-key-b'
        self.assertNotEqual(first, Bridge._native_model_identity(bridge, '/remote/project', 'fixture'))
        bridge.cli_sessions.model_environment_identity.return_value = 'remote-key-a'
        bridge.host, bridge.cli_sessions.alias = 'ssh:host-b', 'host-b'
        self.assertNotEqual(first, Bridge._native_model_identity(bridge, '/remote/project', 'fixture'))
