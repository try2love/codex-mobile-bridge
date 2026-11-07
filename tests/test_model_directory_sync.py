"""Catalogue sharing is scoped to native identity; live controls never travel with it."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from bridge.clients.codex.catalog import Catalog, CatalogError, ModelStore
from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]


class CodexDirectoryIdentity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)/'codex'
        self.home.mkdir()
        self.project = Path(temporary.name)/'project'
        self.project.mkdir()
        self.reader = Catalog(self.home, 'fixture-runtime', allow_background_refresh=False)

    def raw(self, provider='project-route', model='first'):
        # Reuse the real native config resolver; no helper or model request.
        request = Mock(side_effect=lambda method, params: {'config': {
            'model_provider': provider, 'model_catalog_json': 'fixture.json'}} if method == 'config/read'
            else {'data': [{'model': model, 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}]})
        result = self.reader.read_models(request, str(self.project))
        self.assertEqual([call.args[0] for call in request.call_args_list], ['config/read', 'model/list'])
        return result

    def test_default_and_explicit_provider_share_directory_across_reader_restart_and_refresh(self):
        explicit = self.reader.model_identity(self.project, 'project-route')
        self.assertIsNone(self.reader.model_identity(self.project, None, require_verified=True))
        raw = self.raw()
        with patch.object(self.reader, '_fetch', return_value=raw) as fetch:
            first = self.reader.get_kind('models', self.project)
            other = self.reader.get_kind('models', self.project, provider='project-route')
        self.assertEqual(first, other)
        self.assertEqual(first['cache']['identity'], explicit)
        fetch.assert_called_once()
        second = Catalog(self.home, 'fixture-runtime', allow_background_refresh=False)
        with patch.object(second, '_fetch', return_value=self.raw(model='refreshed')) as fetch:
            self.assertEqual(second.model_identity(self.project, None, require_verified=True), explicit)
            self.assertEqual(second.get_kind('models', self.project), first)
            fetch.assert_not_called()
            refreshed = second.get_kind('models', self.project, provider='project-route', refresh=True)
            self.assertEqual(second.get_kind('models', self.project), refreshed)
            fetch.assert_called_once()

    def test_explicit_query_teaches_native_default_without_aliasing_other_provider(self):
        with patch.object(self.reader, '_fetch', return_value=self.raw()) as fetch:
            self.reader.get_kind('models', self.project, provider='another-provider')
            expected = self.reader.model_identity(self.project, 'project-route')
            self.assertEqual(self.reader.model_identity(self.project, None), expected)
            self.assertNotEqual(expected, self.reader.model_identity(self.project, 'another-provider'))
            self.reader.get_kind('models', self.project)
            self.assertEqual(fetch.call_count, 2)

    def test_account_and_project_changes_invalidate_native_alias_without_query(self):
        with patch.object(self.reader, '_fetch', return_value=self.raw()):
            first = self.reader.get_kind('models', self.project)
        other_project = self.project/'other'; other_project.mkdir()
        self.assertIsNone(self.reader.model_identity(other_project, None, require_verified=True))
        for path in (self.home/'auth.json', self.project/'.codex/config.toml'):
            path.parent.mkdir(exist_ok=True)
            path.write_text('changed')
            with patch.object(self.reader, '_fetch', side_effect=AssertionError('identity started helper')):
                self.assertIsNone(self.reader.model_identity(self.project, None, require_verified=True))
                self.assertNotEqual(first['cache']['identity'], self.reader.model_identity(self.project, 'project-route'))

    def test_configuration_change_during_native_query_never_stores_old_alias(self):
        raw = self.raw()
        def changed(*args):
            (self.home/'config.toml').write_text('model_provider="changed"')
            return raw
        with patch.object(self.reader, '_fetch', side_effect=changed):
            with self.assertRaisesRegex(CatalogError, '接入已变化'):
                self.reader.get_kind('models', self.project)
        self.assertIsNone(self.reader.model_identity(self.project, None, require_verified=True))

    def test_legacy_combined_catalog_sees_picker_refresh_without_refetching_skills(self):
        raw = {**self.raw(), 'skillEntries': [], 'currentModel': 'session-a', 'currentEffort': 'low'}
        with patch.object(self.reader, '_fetch', return_value=raw) as fetch:
            first = self.reader.get(self.project, provider='project-route')
            self.assertEqual(first['currentModel'], 'session-a')
            fetch.return_value = self.raw(model='changed')
            updated = self.reader.get_kind('models', self.project, provider='project-route', refresh=True)
            combined = self.reader.get(self.project, provider='project-route')
            self.assertEqual(combined['models'], updated['models'])
            self.assertNotIn('currentModel', combined)
            self.assertNotIn('currentEffort', combined)
            self.assertEqual(combined['skills'], first['skills'])
            self.assertEqual(fetch.call_count, 2)

    def test_native_cli_default_provider_uses_same_effective_config_identity(self):
        bridge = Bridge.__new__(Bridge)
        bridge.host, bridge.codex_home = 'local', self.home
        bridge.cli_sessions = SimpleNamespace(alias=None, client=SimpleNamespace(request=Mock(
            side_effect=lambda method, params, **kwargs: {'config': {
                'profile': 'chosen', 'profiles': {'chosen': {'model_provider': 'project-route'}}}}
            if method == 'config/read' else {'account': {'type': 'chatgpt'}})))
        self.assertEqual(bridge._native_model_identity(str(self.project), None),
                         bridge._native_model_identity(str(self.project), 'project-route'))


class DesktopDirectorySharing(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temporary.cleanup)
        self.manager = DesktopSessions(Path(temporary.name)); self.addCleanup(self.manager.close)
        self.manager._account_model_identity = Mock(return_value='account-one')
        self.manager._account_model_live = Mock(return_value=False)
        self.manager._model_live = Mock(return_value=False)
        self.manager._project_environment = Mock(return_value={'key': 'env', 'surface': 'desktop', 'host': 'local'})
        self.manager.directory_projects.get = Mock(return_value={'provider': 'deepseek', 'environment': 'env', 'cwd': '/fixture'})

    def test_creation_account_and_native_chat_share_refresh_without_session_selection(self):
        self.manager._read = Mock(return_value={'models': [{'id': 'a'}],
            'currentModel': 'session-only', 'currentEffort': 'high', 'capabilities': {'manualModels': True}})
        first = self.manager.creation_models('deepseek', {'environment': 'env', 'projectKey': 'project'})
        self.manager._read.assert_called_once_with('deepseek', 'account')
        chat = self.manager._catalog_models('deepseek', 'chat', {})
        self.assertEqual(chat['cache']['identity'], first['cache']['identity'])
        self.assertNotIn('currentModel', chat)
        self.assertFalse(chat['capabilities']['models'])
        self.manager._read.return_value = {'models': [{'id': 'b'}]}
        refreshed = self.manager._account_models('deepseek', refresh=True)
        self.assertEqual(self.manager.creation_models('deepseek', {'environment': 'env', 'projectKey': 'project'})['models'], refreshed['models'])
        self.assertEqual(self.manager._catalog_models('deepseek', 'another-chat', {})['models'], refreshed['models'])
        self.assertEqual(self.manager._read.call_count, 2)
        cached = self.manager.model_store.read('account-one')[1]
        self.assertNotIn('capabilities', cached)
        self.assertNotIn('currentEffort', cached)

    def test_claude_surface_projection_keeps_complete_normalized_groups(self):
        value = {'models': [{'id': 'code'}, {'id': 'cowork'}], 'groups': [
            {'surface': 'code', 'source': 'desktop-catalog', 'models': [{'id': 'code'}]},
            {'surface': 'cowork', 'source': 'session-history', 'models': [{'id': 'cowork'}]}],
            'cache': {'identity': 'account-one'}}
        self.manager._account_models = Mock(return_value=value)
        code = self.manager._desktop_model_directory('claude', 'code')
        cowork = self.manager._desktop_model_directory('claude', 'cowork')
        self.assertEqual(code['cache']['directoryIdentity'], cowork['cache']['directoryIdentity'])
        self.assertNotEqual(code['cache']['identity'], cowork['cache']['identity'])
        self.assertEqual(code['groups'], cowork['groups'])
        self.assertEqual(code['models'], code['groups'][0]['models'])
        self.assertEqual(cowork['models'], cowork['groups'][1]['models'])
        self.assertEqual(self.manager._desktop_model_directory('claude', 'missing')['models'], [])
        value['groups'] = [{'surface': 'api', 'source': 'upstream-api', 'models': [{'id': 'api'}]}]
        self.assertEqual(self.manager._desktop_model_directory('claude', 'cowork')['models'],
                         [{'id': 'api', 'source': 'upstream-api'}])
        value['groups'].append({'surface': 'unknown', 'models': [{'id': 'unknown'}]})
        self.assertEqual(self.manager._desktop_model_directory('claude', 'cowork')['models'], [])

    def test_zcode_workspace_binding_does_not_fork_the_account_directory(self):
        self.manager._account_model_identity = DesktopSessions._account_model_identity.__get__(self.manager)
        adapter = SimpleNamespace(home=Path(self.manager.directory)/'zcode-home', binding={'workspace': 'one'},
                                  account_scope=Mock(return_value='account-a'))
        self.manager.adapters['zcode'] = adapter
        self.addCleanup(self.manager.adapters.pop, 'zcode')
        self.manager._read = Mock(return_value={'models': [{'id': 'flash'}]})
        first = self.manager._catalog_models('zcode', 'session-one', {})
        adapter.binding = {'workspace': 'two'}
        second = self.manager._catalog_models('zcode', 'session-two', {})
        self.assertEqual(first['cache']['identity'], second['cache']['identity'])
        self.manager._read.assert_called_once_with('zcode', 'account')
        adapter.account_scope.return_value = 'account-b'
        third = self.manager._catalog_models('zcode', 'session-three', {})
        self.assertNotEqual(second['cache']['identity'], third['cache']['identity'])
        self.assertEqual(self.manager._read.call_count, 2)

    def test_cli_controls_are_owned_by_live_process_not_shared_catalogue(self):
        adapter = self.manager.adapters['claude']
        first, second = Mock(origin='fixture', error=None), Mock(origin='fixture', error=None)
        first.process.poll.return_value = second.process.poll.return_value = None
        for sid, owner in [('one', first), ('two', second)]:
            adapter.cli.index[sid] = {'nativeId': sid, 'origin': 'fixture'}
            adapter.cli.owned[sid] = owner
        self.manager._model_identity = Mock(return_value='same-directory')
        self.manager._read = Mock(side_effect=lambda provider, action, sid, body: {'capabilities': {
            'models': True, 'effort': sid == 'one', 'manualModels': sid == 'one', 'manualEffort': sid == 'one'}})
        self.assertTrue(self.manager._cli_model_capabilities('one', 'same-directory')['manualModels'])
        self.assertFalse(self.manager._cli_model_capabilities('two', 'same-directory')['manualModels'])
        self.manager._cli_model_capabilities('one', 'same-directory')
        self.assertEqual(self.manager._read.call_count, 2)
        adapter.cli.owned.pop('one')
        self.assertFalse(self.manager._cli_model_capabilities('one', 'same-directory')['models'])
        self.assertEqual(self.manager._read.call_count, 2)
        adapter.cli.owned.clear()  # Mock owners do not represent real cleanup targets.

    def test_directory_snapshot_removes_nested_live_state(self):
        result = ModelStore.directory({'models': [], 'currentModel': 'one', 'capabilities': {'models': True},
            'groups': [{'surface': 'code', 'models': [], 'currentModel': 'two', 'capabilities': {'effort': True}, 'notice': 'old'}]})
        self.assertEqual(result, {'models': [], 'groups': [{'surface': 'code', 'models': []}]})
