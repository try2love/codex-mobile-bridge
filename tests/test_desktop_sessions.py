import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.claude.accounts import ClaudeAccounts
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.deepseek.adapter import DeepSeek, MARKER
import test_bridge

ROOT = Path(__file__).resolve().parents[1]


class Adapters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = DesktopSessions(self.root)
        self.addCleanup(self.manager.close)

    def test_native_claude_detail_projects_verified_prompt_capabilities_for_shared_composer(self):
        from bridge.clients.claude.model import session
        adapter = self.manager.adapters['claude']
        raw = {'sessionId': 'composer-native', 'cwd': str(self.root), 'turnRunning': False}
        row = session('code', raw)
        adapter.index[row['id']] = row
        adapter.desktop = Mock(connected=True, capabilities={'code': ['mobileDetail', 'sendMessage']})
        adapter.desktop.call = AsyncMock(return_value={'session': raw, 'transcript': []})
        native = lambda action, sid=None, body=None, **kwargs: asyncio.run(adapter.dispatch(action, sid, body or {}))
        with patch.object(adapter, '_call', side_effect=native), \
             patch.object(self.manager, '_model_identity', return_value='fixture-account'), \
             patch.object(self.manager, '_draft_account_identity', return_value='fixture-draft'):
            detail = self.manager.call('claude', 'detail', row['id'])
            self.assertTrue(detail['capabilities']['send'])
            self.assertTrue(detail['capabilities']['projectReferences'])
            self.assertTrue(detail['capabilities']['queue'])
            self.assertTrue(detail['capabilities']['steer'])
            adapter.desktop.capabilities['code'].remove('sendMessage')
            unavailable = self.manager.call('claude', 'detail', row['id'])
            self.assertFalse(unavailable['capabilities']['send'])
            self.assertFalse(unavailable['capabilities']['projectReferences'])
        self.assertEqual([call.args[1] for call in adapter.desktop.call.call_args_list], ['mobileDetail', 'mobileDetail'])

    def test_detail_prompt_projection_preserves_unknown_and_explicit_native_restrictions(self):
        adapter = Mock()
        self.manager.adapters['deepseek'] = adapter
        for caps, expected in [({}, {}), ({'attachments': 'unknown'}, {'attachments': 'unknown'}),
                               ({'attachments': True, 'send': False, 'projectReferences': False},
                                {'attachments': True, 'send': False, 'projectReferences': False})]:
            adapter.call.return_value = {'session': {'id': 'composer', 'cwd': str(self.root)}, 'capabilities': caps}
            self.assertEqual(self.manager._read('deepseek', 'detail', 'composer')['capabilities'], expected)

    def test_host_references_are_resolved_once_and_left_to_native_adapters(self):
        from bridge.features.workspace.workspace import operate
        file = self.root / '中文.txt'; file.write_text('fixture')
        for provider in ['claude', 'deepseek']:
            adapter = Mock(); adapter.receipts_path = None
            adapter.call.return_value = {'status': 'accepted'}
            self.manager.adapters[provider] = adapter
            bridge = Mock(); bridge.workspace.side_effect = lambda sid, action, params: operate(str(self.root), action, params)
            body = {'id': str(uuid.uuid4()), 'text': '', 'references': [{'path': file.name, 'absolutePath': str(file)}]}
            with patch.object(self.manager, 'workspace_bridge', return_value=bridge):
                self.assertEqual(self.manager.call(provider, 'send', 'chat', body)['status'], 'accepted')
                self.manager.call(provider, 'send', 'chat', body)
            self.assertEqual(adapter.call.call_count, 1)
            dispatched = adapter.call.call_args.args[2]
            self.assertNotIn('references', dispatched)
            self.assertEqual(dispatched['text'], '')
            self.assertEqual(dispatched['resolvedReferences'], [{'path': str(file), 'name': file.name, 'kind': 'file', 'size': 7, 'image': None}])
            adapter.call.return_value = {'session': {'cwd': str(self.root)}, 'messages': [{'id': 'message', 'role': 'user', 'text': '', 'attachments': [{'type': 'file', 'path': str(file), 'name': file.name}]}]}
            detail = self.manager._read(provider, 'detail', 'chat')
            self.assertEqual(detail['messages'][0]['text'], '')
            self.assertEqual(detail['files'][0]['path'], file.as_posix())
            self.assertEqual(detail['files'][0]['scope'], 'host')

    def test_native_work_mode_routes_once_through_shared_write_receipts(self):
        adapter = Mock(); adapter.receipts_path = None
        self.manager.adapters['deepseek'] = adapter
        def native(action, sid=None, body=None):
            if action == 'detail':
                return {'capabilities': {'plan': True, 'goal': True}, 'session': {'collaborationMode': 'default'}}
            return {'status': 'accepted'}
        adapter.call.side_effect = native
        body = {'id': str(uuid.uuid4()), 'text': 'plan this', 'workMode': 'plan'}
        self.assertEqual(self.manager.call('deepseek', 'send', 'chat', body)['status'], 'accepted')
        self.manager.call('deepseek', 'send', 'chat', body)
        self.assertEqual([c.args[0] for c in adapter.call.call_args_list], ['detail', 'work-mode', 'send'])
        self.assertNotIn('workMode', adapter.call.call_args.args[2])
        adapter.call.reset_mock()
        body = {'id': str(uuid.uuid4()), 'text': 'finish this', 'workMode': 'goal'}
        self.assertEqual(self.manager.call('deepseek', 'send', 'chat', body)['status'], 'accepted')
        self.assertEqual([c.args[0] for c in adapter.call.call_args_list], ['detail', 'goal'])
        self.assertEqual(adapter.call.call_args.args[2]['objective'], 'finish this')
        adapter.call.reset_mock()
        body.update(id=str(uuid.uuid4()), mode='queue')
        with self.assertRaises(ValueError): self.manager.call('deepseek', 'send', 'chat', body)
        adapter.call.assert_not_called()

    def test_claude_preparation_is_available_without_starting_gateway(self):
        self.manager.gateway_running = False
        adapter = self.manager.adapters['claude']
        with patch.object(adapter, 'prerequisites', return_value={'workspace': '/中文', 'connected': False}) as read, \
             patch.object(adapter, 'prepare') as prepare, patch.object(adapter, 'connect') as connect:
            value = self.manager.control({'action': 'claude-prerequisites'})
            self.assertFalse(value['gatewayRunning'])
            self.assertEqual(value['workspace'], '/中文')
            read.assert_called_once_with(request_permission=False)
            self.manager.control({'action': 'claude-prerequisites', 'requestPermission': True})
            self.assertEqual(read.call_args.kwargs, {'request_permission': True})
            prepare.assert_not_called(); connect.assert_not_called()

    def test_receipts_survive_reopen_and_do_not_replay_uncertain_sends(self):
        adapter = Mock()
        adapter.call.side_effect = TimeoutError('unknown')
        self.manager.adapters['deepseek'] = adapter
        body = {'id': str(uuid.uuid4()), 'text': 'hello'}
        first = self.manager.call('deepseek', 'send', 'original-session', body)
        self.assertEqual(first['status'], 'unknown')
        self.assertEqual(self.manager.call('deepseek', 'send', 'original-session', body), first)
        self.assertEqual(adapter.call.call_count, 1)
        with self.assertRaises(ValueError):
            self.manager.call('claude', 'send', 'another-session', body)
        # A restarted gateway must retain the receipt, even if the desktop accepted
        # the first send but its acknowledgement was lost.
        second = DesktopSessions(self.root)
        try:
            other = Mock(); second.adapters['deepseek'] = other
            self.assertEqual(second.call('deepseek', 'send', 'original-session', body), first)
            other.call.assert_not_called()
        finally:
            second.close()

    def test_read_only_operations_preserve_backend_and_session(self):
        adapter = Mock(); self.manager.adapters['deepseek'] = adapter
        adapter.call.return_value = {'session': {'id': 'session-one'}}
        with patch.object(self.manager, '_model_identity', return_value='fixture-identity'):
            self.assertEqual(self.manager.call('deepseek', 'detail', 'session-one')['modelCatalogIdentity'],
                             'fixture-identity')
        adapter.call.assert_called_once_with('detail', 'session-one', {})
        with self.assertRaises(ValueError): self.manager.call('deepseek', 'eval', 'session-one')
        with self.assertRaises(ValueError): self.manager.call('deepseek', 'send', 'session-one', {'id':str(uuid.uuid4()),'text':''})

    def test_desktop_claude_uses_same_surface_projection_as_creation(self):
        adapter = self.manager.adapters['claude']
        adapter.index['session'] = {'backend': 'cowork'}
        directory = {'models': [{'id': 'code'}, {'id': 'cowork'}],
                     'groups': [{'surface': 'code', 'models': [{'id': 'code'}]},
                                {'surface': 'cowork', 'models': [{'id': 'cowork'}]}],
                     'cache': {'identity': 'same-account'}}
        with patch.object(self.manager, '_account_model_identity', return_value='same-account'), \
             patch.object(self.manager, '_account_models', return_value=directory) as account:
            result = self.manager._catalog_models('claude', 'session', {'section': 'models'})
        self.assertEqual([row['id'] for row in result['models']], ['cowork'])
        self.assertEqual(result['cache']['directoryIdentity'], 'same-account')
        self.assertEqual(result['cache']['identity'], self.manager._surface_model_identity('same-account', 'cowork'))
        account.assert_called_once_with('claude', refresh=False)

    def test_manual_settings_captured_account_is_checked_before_native_write(self):
        adapter = self.manager.adapters['deepseek']
        adapter.call = Mock(return_value={'status': 'accepted'})
        body = {'id': str(uuid.uuid4()), 'model': 'provider/manual', 'manualModel': True,
                'modelCatalogIdentity': 'old-account'}
        with patch.object(self.manager, '_model_identity', return_value='new-account'):
            with self.assertRaisesRegex(ValueError, '账号已切换'):
                self.manager.call('deepseek', 'settings', 'session', body)
        adapter.call.assert_not_called()
        self.assertEqual(self.manager.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)
        with patch.object(self.manager, '_model_identity', return_value='old-account'):
            self.assertEqual(self.manager.call('deepseek', 'settings', 'session', body)['status'], 'accepted')
        adapter.call.assert_called_once()

    def test_unavailable_catalog_still_carries_verified_account_identity(self):
        with patch.object(self.manager, '_model_identity', return_value='same-account'), \
             patch.object(self.manager, '_read', return_value={'models': [], 'capabilities': {'models': False}}):
            result = self.manager._catalog_models('claude', 'session', {'section': 'models'})
        self.assertEqual(result['cache']['identity'], 'same-account')

    def test_catalog_identity_survives_cache_write_failure(self):
        with patch.object(self.manager, '_model_identity', return_value='same-account'), \
             patch.object(self.manager, '_read', return_value={'models': [], 'capabilities': {'models': True}}), \
             patch.object(self.manager.model_store, 'write', side_effect=OSError('read-only')):
            result = self.manager._catalog_models('claude', 'session', {'section': 'models'})
        self.assertEqual(result['cache']['identity'], 'same-account')

    def test_legacy_claude_catalog_does_not_schedule_unsupported_refresh(self):
        adapter = self.manager.adapters['claude']
        row = {'nativeId': 'native', 'origin': 'fixture', 'model': 'sonnet'}
        adapter.cli.index['session'] = row
        owner = Mock(model='haiku', catalog={'models': [{'value': 'sonnet'}, {'value': 'haiku'}]})
        adapter.cli.owned['native'] = owner
        owner.model_list_unsupported = True
        self.assertTrue(self.manager._model_refresh_unsupported('claude', 'session'))
        self.manager._schedule_model_refresh('claude', 'session', 'legacy', 0)
        self.assertNotIn('legacy', self.manager.model_timers)

    def test_unsupported_legacy_refresh_keeps_original_cache_timestamp(self):
        updated_at = self.manager.model_store.write('legacy', {
            'models': [{'id': 'sonnet'}], 'capabilities': {'models': True, 'effort': False}})
        with patch.object(self.manager, '_model_identity', return_value='legacy'), \
             patch.object(self.manager, '_model_live', return_value=True), \
             patch.object(self.manager, '_read', side_effect=BridgeUnavailable('当前 Claude Code 版本不支持刷新模型目录')):
            result = self.manager._catalog_models('claude', 'session', {'section': 'models', 'refresh': True})
        self.assertEqual(result['models'], [{'id': 'sonnet'}])
        self.assertEqual(result['cache']['updatedAt'], updated_at)
        self.assertEqual(result['cache']['state'], 'stale')
        self.assertEqual(result['notice'], '当前 Claude Code 版本不支持刷新模型目录，已保留上次结果')
        self.assertEqual(result['modelError'], result['notice'])
        self.assertEqual(self.manager.model_store.read('legacy')[0], updated_at)

    def test_model_picker_uses_persisted_catalog_and_refresh_bypasses_it(self):
        self.manager._account_model_identity = Mock(return_value='deepseek-account')
        self.manager._account_model_live = Mock(return_value=False)
        adapter = self.manager.adapters['deepseek']
        adapter.call = Mock(return_value={'models': [{'id': 'provider/first', 'efforts': ['high']}],
                                          'currentModel': 'provider/first', 'capabilities': {'models': True}})
        body = {'section': 'models', 'refresh': False}
        first = self.manager.call('deepseek', 'catalog', 'session-one', body)
        self.assertEqual(first['models'][0]['id'], 'provider/first')
        self.assertEqual(self.manager.call('deepseek', 'catalog', 'session-one', body)['models'], first['models'])
        adapter.call.assert_called_once()
        second = DesktopSessions(self.root)
        try:
            second._account_model_identity = Mock(return_value='deepseek-account')
            second._account_model_live = Mock(return_value=False)
            second_adapter = second.adapters['deepseek']
            second_adapter.call = Mock(side_effect=AssertionError('opened picker queried desktop'))
            self.assertEqual(second.call('deepseek', 'catalog', 'session-one', body)['models'], first['models'])
            second_adapter.call.assert_not_called()
            second_adapter.call.side_effect = None
            second_adapter.call.return_value = {'models': [{'id': 'provider/second'}], 'capabilities': {'models': True}}
            refreshed = second.call('deepseek', 'catalog', 'session-one', {'section': 'models', 'refresh': True})
            self.assertEqual(refreshed['models'][0]['id'], 'provider/second')
            second_adapter.call.assert_called_once()
            second_adapter.call.return_value = {'models': [], 'capabilities': {'models': False}}
            retained = second.call('deepseek', 'catalog', 'session-one', {'section': 'models', 'refresh': True})
            self.assertEqual(retained['models'], refreshed['models'])
            self.assertEqual(retained['cache']['state'], 'stale')
            self.assertFalse(retained['capabilities']['models'])
            second_adapter.call.side_effect = ValueError('temporary upstream failure')
            self.assertEqual(second.call('deepseek', 'catalog', 'session-one', {'section': 'models', 'refresh': True})['models'], refreshed['models'])
            second_adapter.call.side_effect = None
            second._account_model_identity.return_value = 'different-account'
            second_adapter.call.return_value = {'models': [{'id': 'provider/new-account'}], 'capabilities': {'models': True}}
            self.assertEqual(second.call('deepseek', 'catalog', 'session-one', body)['models'][0]['id'], 'provider/new-account')
        finally:
            second.close()

    def test_account_models_share_cache_and_keep_last_success_on_refresh_failure(self):
        manager = self.manager
        manager._account_model_identity = Mock(return_value='account-one')
        manager._account_model_live = Mock(return_value=False)
        manager._read = Mock(return_value={'models':[{'id':'first'}], 'groups':[]})
        first = manager.control({'action':'account', 'provider':'deepseek'})
        self.assertEqual(first['cache']['state'], 'fresh')
        self.assertEqual(first['cache']['identity'], 'account-one')
        self.assertEqual(manager.call('deepseek', 'account')['models'], first['models'])
        manager._read.assert_called_once()
        manager._read.side_effect = ValueError('upstream unavailable')
        stale = manager.call('deepseek', 'account', body={'refresh':True})
        self.assertEqual(stale['models'], first['models'])
        self.assertEqual(stale['cache']['updatedAt'], first['cache']['updatedAt'])
        self.assertEqual(stale['cache']['state'], 'stale')
        self.assertTrue(stale['modelError'])
        manager._account_model_identity.return_value = 'account-two'
        manager._read.side_effect = None
        manager._read.return_value = {'models':[{'id':'second'}], 'groups':[]}
        self.assertEqual(manager.call('deepseek', 'account')['models'][0]['id'], 'second')

    def test_disconnected_claude_account_models_use_native_config_without_connector(self):
        store = ClaudeAccounts(self.root/'accounts', self.root/'Claude')
        store.home.mkdir()
        store._current = Mock(return_value=('api', 'fingerprint-one', {'config': {
            'inferenceGatewayBaseUrl': 'https://provider.test/v1',
            'inferenceGatewayApiKey': 'secret', 'inferenceGatewayAuthScheme': 'x-api-key',
            'inferenceModels': ['configured-model']}}))
        adapter = self.manager.adapters['claude']
        adapter.desktop = None
        with patch.object(self.manager, '_account_store', return_value=store), \
             patch('bridge.clients.claude.accounts.model_catalog', return_value=[
                 {'id': 'upstream-model', 'name': 'Upstream', 'efforts': ['high']}]) as upstream, \
             patch.object(adapter, 'call', side_effect=AssertionError('connector used')) as connector, \
             patch.object(adapter, 'connect', side_effect=AssertionError('connected')):
            public = self.manager._accounts_public('claude', {'accounts': []})
            result = self.manager.call('claude', 'account')
            self.assertEqual(result['cache']['identity'], public['modelCatalogIdentity'])
            self.assertEqual(result['groups'][0]['models'][0]['id'], 'upstream-model')
            self.assertEqual(result['groups'][0]['models'][0]['efforts'], ['high'])
            self.assertEqual(result['groups'][0]['source'], 'upstream-api')
            self.manager.call('claude', 'account')
            self.assertEqual(upstream.call_count, 1)
            self.manager.call('claude', 'account', body={'refresh': True})
            self.assertEqual(upstream.call_count, 2)
            connector.assert_not_called()

    def test_disconnected_claude_account_change_rejects_old_models(self):
        store = ClaudeAccounts(self.root/'accounts', self.root/'Claude')
        store.home.mkdir()
        store._current = Mock(return_value=('api', 'fingerprint-one', {'config': {
            'inferenceGatewayBaseUrl': 'https://provider.test/v1',
            'inferenceGatewayApiKey': 'secret', 'inferenceGatewayAuthScheme': 'bearer'}}))
        with patch.object(self.manager, '_account_store', return_value=store), \
             patch('bridge.clients.claude.accounts.model_catalog') as upstream:
            old_identity = self.manager._account_model_identity('claude')
            def changed(*args, **kwargs):
                store._current.return_value = ('api', 'fingerprint-two', store._current.return_value[2])
                return [{'id': 'old-model', 'name': 'Old', 'efforts': []}]
            upstream.side_effect = changed
            with self.assertRaisesRegex(ValueError, '账号或配置已变化'):
                self.manager.call('claude', 'account')
            self.assertIsNone(self.manager.model_store.read(old_identity))
            self.assertIsNone(self.manager.model_store.read(self.manager._account_model_identity('claude')))

    def test_official_claude_without_local_catalog_returns_matching_identity_and_no_cache(self):
        store = ClaudeAccounts(self.root/'accounts', self.root/'Claude')
        store.home.mkdir()
        store._current = Mock(return_value=('official', 'fingerprint-one', None))
        with patch.object(self.manager, '_account_store', return_value=store), \
             patch.object(self.manager.adapters['claude'], 'call', side_effect=AssertionError('connector used')):
            identity = self.manager._accounts_public('claude', {'accounts': []})['modelCatalogIdentity']
            result = self.manager.call('claude', 'account')
            self.assertEqual(result['cache']['identity'], identity)
            self.assertEqual(result['cache']['state'], 'unavailable')
            self.assertEqual(result['models'], [])
            self.assertIsNone(self.manager.model_store.read(identity))

    def test_connected_official_claude_groups_seed_cache_for_later_disconnected_reads(self):
        store = ClaudeAccounts(self.root/'accounts', self.root/'Claude')
        store.home.mkdir()
        store._current = Mock(return_value=('official', 'fingerprint-one', None))
        adapter = self.manager.adapters['claude']
        adapter.desktop = Mock(connected=True)
        adapter.call = Mock(return_value={'groups': [{'surface': 'code', 'source': 'desktop-catalog',
            'models': [{'id': 'native-model', 'name': 'Native', 'efforts': ['low', 'high']}]}]})
        with patch.object(self.manager, '_account_store', return_value=store):
            first = self.manager.call('claude', 'account')
            self.assertEqual(first['models'][0]['id'], 'native-model')
            self.assertEqual(first['groups'][0]['source'], 'desktop-catalog')
            adapter.desktop.connected = False
            cached = self.manager.call('claude', 'account')
            self.assertEqual(cached['groups'], first['groups'])
            self.assertEqual(cached['cache']['identity'], first['cache']['identity'])
            adapter.call.assert_called_once_with('account', None, {})

    def test_claude_api_refresh_failure_keeps_last_upstream_catalog(self):
        store = ClaudeAccounts(self.root/'accounts', self.root/'Claude')
        store.home.mkdir()
        store._current = Mock(return_value=('api', 'fingerprint-one', {'config': {
            'inferenceGatewayBaseUrl': 'https://provider.test/v1',
            'inferenceGatewayApiKey': 'secret', 'inferenceModels': ['configured-model']}}))
        with patch.object(self.manager, '_account_store', return_value=store), \
             patch('bridge.clients.claude.accounts.model_catalog') as upstream:
            upstream.return_value = [{'id': 'upstream-model', 'name': 'Upstream', 'efforts': ['high']}]
            first = self.manager.call('claude', 'account')
            upstream.side_effect = ValueError('upstream unavailable')
            stale = self.manager.call('claude', 'account', body={'refresh': True})
            self.assertEqual(stale['groups'], first['groups'])
            self.assertEqual(stale['cache']['state'], 'stale')
            self.assertEqual(stale['cache']['updatedAt'], first['cache']['updatedAt'])

    def test_uncached_claude_api_failure_labels_configured_models_without_persisting(self):
        store = ClaudeAccounts(self.root/'accounts', self.root/'Claude')
        store.home.mkdir()
        store._current = Mock(return_value=('api', 'fingerprint-one', {'config': {
            'inferenceGatewayBaseUrl': 'https://provider.test/v1',
            'inferenceGatewayApiKey': 'secret', 'inferenceModels': ['configured-model']}}))
        with patch.object(self.manager, '_account_store', return_value=store), \
             patch('bridge.clients.claude.accounts.model_catalog', side_effect=ValueError('upstream unavailable')):
            identity = self.manager._accounts_public('claude', {'accounts': []})['modelCatalogIdentity']
            result = self.manager.call('claude', 'account')
            self.assertEqual(result['cache']['identity'], identity)
            self.assertEqual(result['groups'][0]['source'], 'desktop-config')
            self.assertEqual(result['groups'][0]['models'][0]['efforts'], [])
            self.assertIsNone(self.manager.model_store.read(identity))

    def test_account_list_exposes_same_safe_catalog_identity(self):
        manager = self.manager
        manager._account_model_identity = Mock(return_value='account-one')
        self.assertEqual(manager._accounts_public('claude', {'accounts': []})['modelCatalogIdentity'], 'account-one')
        manager._account_model_identity.side_effect = BridgeUnavailable('账号未知')
        self.assertEqual(manager._accounts_public('claude', {'accounts': []})['modelCatalogIdentity'], '')

    def test_account_change_during_model_read_cannot_publish_old_catalog(self):
        manager = self.manager
        manager._account_model_identity = Mock(return_value='old-account')
        def changed(*args):
            manager._account_model_identity.return_value = 'new-account'
            return {'models':[{'id':'old-model'}]}
        manager._read = Mock(side_effect=changed)
        with self.assertRaisesRegex(ValueError, '账号或配置已变化'):
            manager.call('deepseek', 'account')
        self.assertIsNone(manager.model_store.read('old-account'))

    def test_cached_picker_never_waits_for_native_selection(self):
        manager = self.manager
        manager._model_identity = Mock(return_value='shared-access')
        manager._model_live = Mock(return_value=True)
        manager._read = Mock(return_value={'models':[{'id':'first'}, {'id':'second'}],
                                          'currentModel':'first', 'capabilities':{'models':True}})
        manager.call('claude', 'catalog', 'first-session', {'section':'models'})
        cached = manager.call('claude', 'catalog', 'second-session', {'section':'models'})
        self.assertNotIn('currentModel', cached)
        self.assertEqual(cached['cache']['identity'], 'shared-access')
        manager._read.assert_called_once()

    def test_cached_deepseek_catalog_does_not_read_session_list(self):
        manager = self.manager
        manager._account_model_identity = Mock(return_value='shared-access')
        manager._account_model_live = Mock(return_value=False)
        manager._model_live = Mock(return_value=True)
        calls = []
        def native(action, sid, body):
            calls.append(action)
            if action == 'account':
                return {'models': [{'id': 'provider/first'}, {'id': 'provider/second'}],
                        'capabilities': {'models': True}}
            if action == 'list':
                return {'sessions': [{'id': 'other', 'model': 'wrong'},
                                     {'id': 'session-one', 'model': 'provider/second', 'effort': 'high'}]}
            raise AssertionError(action)
        manager.adapters['deepseek'].call = Mock(side_effect=native)
        manager.call('deepseek', 'catalog', 'session-one', {'section': 'models'})
        cached = manager.call('deepseek', 'catalog', 'session-one', {'section': 'models'})
        self.assertNotIn('currentModel', cached)
        self.assertEqual(calls, ['account'])

    def test_session_account_change_after_catalog_write_rejects_old_response(self):
        manager = self.manager
        manager._account_model_identity = Mock(side_effect=['old-account', 'old-account', 'new-account'])
        manager._account_model_live = Mock(return_value=False)
        manager._read = Mock(return_value={'models':[{'id':'old-model'}], 'capabilities':{'models':True}})
        with self.assertRaisesRegex(ValueError, '账号或配置已变化'):
            manager.call('deepseek', 'catalog', 'session-one', {'section':'models'})

    def test_client_switch_requires_configuration_and_persists(self):
        self.manager.adapters['claude'].cli.status = Mock(return_value={
            'cliAvailable': False, 'cliAuthReady': False})
        adapter = Mock()
        adapter.call.side_effect = lambda action, *args: ({'connected': True, 'configured': True} if action == 'status' else
            {'sessions': [{'id': 'original', 'cwd': str(self.root)}]} if action == 'list' else {'models': [{'id': 'upstream/model'}]})
        self.manager.adapters['deepseek'] = adapter
        self.manager._start_deepseek = Mock()  # Native lifecycle has isolated coverage in test_client_lifecycle.
        self.assertFalse(self.manager.enabled('deepseek'))
        state = self.manager.toggle_client({'provider': 'deepseek', 'enabled': True})
        self.assertTrue(next(row for row in state['clients'] if row['id'] == 'deepseek')['enabled'])
        with self.assertRaises(ValueError):
            self.manager.toggle_client({'provider': 'claude', 'enabled': True})
        self.manager.toggle_client({'provider': 'claude', 'enabled': False})
        self.assertFalse(self.manager.enabled('claude'))
        self.assertTrue(json.loads(self.manager.config_path.read_text())['enabled']['deepseek'])
        adapter.call.side_effect = OSError('offline')
        state = self.manager.clients(refresh=True)
        row = next(row for row in state['clients'] if row['id'] == 'deepseek')
        self.assertTrue(row['enabled'])
        self.assertFalse(row['configured'])
        self.manager.toggle_client({'provider': 'deepseek', 'enabled': False})
        self.assertFalse(self.manager.enabled('deepseek'))

    def test_workspace_is_bound_to_provider_and_desktop_session(self):
        a, b = self.root/'one', self.root/'two'
        a.mkdir(); b.mkdir(); (a/'proof.txt').write_text('one'); (b/'proof.txt').write_text('two')
        self.manager.config['enabled'] = {'deepseek': True, 'claude': True}
        for provider, root in [('deepseek', a), ('claude', b)]:
            adapter = Mock(); adapter.call.return_value = {'sessions': [{'id': 'same-id', 'cwd': str(root)}]}
            self.manager.adapters[provider] = adapter
        one = self.manager.workspace_bridge('deepseek', 'same-id')
        two = self.manager.workspace_bridge('claude', 'same-id')
        self.assertNotEqual(one.identifier, two.identifier)
        self.assertEqual(one.workspace(None, 'preview', {'path': 'proof.txt'})['text'], 'one')
        self.assertEqual(two.workspace(None, 'preview', {'path': 'proof.txt'})['text'], 'two')
        with self.assertRaises(PermissionError):
            one.workspace(None, 'preview', {'path': '../two/proof.txt'})
        with self.assertRaises(ValueError):
            self.manager.workspace_bridge('deepseek', 'unknown')
        self.manager.config['enabled']['deepseek'] = False
        with self.assertRaises(ValueError):
            self.manager.workspace_bridge('deepseek', 'same-id')

    def test_install_remove_preserves_other_patches_and_rotates_token(self):
        home=self.root/'home'; patch=home/'profiles/desktop/cordis.patch.yml'
        patch.parent.mkdir(parents=True)
        original='- insert: []\n# user configuration\n';patch.write_text(original)
        adapter=DeepSeek(self.root/'adapter', home)
        adapter.install();adapter.install()
        self.assertEqual(patch.read_text().count(MARKER),1)
        config=adapter.directory/'connection.json';token=json.loads(config.read_text())['token']
        self.assertTrue(DeepSeek(self.root/'other-adapter',home).install()['reused'])
        patch.write_text(patch.read_text()+'# added later\n')
        adapter.uninstall()
        self.assertIn(original,patch.read_text());self.assertIn('# added later',patch.read_text())
        self.assertNotIn(MARKER,patch.read_text())
        self.assertNotEqual(json.loads(config.read_text())['token'],token)

    def test_claude_prepare_is_local_only_and_contains_both_surface_capabilities(self):
        self.assertFalse(self.manager.adapters['claude'].desktop)
        value=self.manager.control({'action':'prepare-claude'})
        self.assertIn('LocalAgentModeSessions',value['script'])
        self.assertIn('LocalSessions',value['script'])
        self.assertEqual(Path(value['scriptPath']).parent,Path(value['workspace']))
        self.assertNotIn('script', self.manager.control({'action':'status'}))


class HttpAdapters(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_client_cannot_supply_resolved_host_references(self):
        manager = self.server.desktop_sessions = Mock()
        headers = self.login()
        body = {'sessionId': 'native-id', 'text': 'test', 'resolvedReferences': [{'path': '/outside/project'}]}
        response = self.request('POST', '/api/desktop-sessions/claude/send', body, headers)
        self.assertEqual(response[0], 400)
        manager.call.assert_not_called()

    def test_client_get_uses_nonblocking_status_path(self):
        manager = self.server.desktop_sessions = Mock()
        manager.clients_status.return_value = {'clients': [], 'pending': True}
        headers = self.login()
        response = self.request('GET', '/api/clients', headers=headers)
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2], {'clients': [], 'pending': True})
        manager.clients_status.assert_called_once_with()
        manager.clients.assert_not_called()

    def test_model_catalog_http_distinguishes_open_from_explicit_refresh(self):
        manager = self.server.desktop_sessions = Mock()
        manager.call.return_value = {'models': [{'id': 'fixture'}]}
        headers = self.login()
        route = '/api/desktop-sessions/claude/catalog?sessionId=native-id&section=models'
        self.assertEqual(self.request('GET', route, headers=headers)[0], 200)
        manager.call.assert_called_with('claude', 'catalog', 'native-id', {'section': 'models', 'refresh': False})
        self.assertEqual(self.request('GET', route + '&refresh=true', headers=headers)[0], 200)
        manager.call.assert_called_with('claude', 'catalog', 'native-id', {'section': 'models', 'refresh': True})

    def test_account_model_http_distinguishes_open_from_explicit_refresh(self):
        manager = self.server.desktop_sessions = Mock()
        manager.call.return_value = {'models': [{'id':'fixture'}], 'cache': {'state':'fresh', 'updatedAt':123}}
        headers = self.login()
        route = '/api/desktop-sessions/deepseek/account'
        self.assertEqual(self.request('GET', route, headers=headers)[0], 200)
        manager.call.assert_called_with('deepseek', 'account', None, {'refresh': False})
        self.assertEqual(self.request('GET', route+'?refresh=true', headers=headers)[0], 200)
        manager.call.assert_called_with('deepseek', 'account', None, {'refresh': True})

    def test_force_desktop_choice_requires_auth_csrf_and_exact_payload(self):
        manager = self.server.desktop_sessions = Mock()
        manager.toggle_client.return_value = {'clients': []}
        body = {'provider': 'codex', 'enabled': False, 'quitDesktop': True, 'forceDesktop': True, 'forceConfirmed': True}
        self.assertEqual(self.request('POST', '/api/clients')[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/clients', headers={'Cookie': headers['Cookie']})[0], 403)
        manager.toggle_client.assert_not_called()
        self.assertEqual(self.request('POST', '/api/clients', {**body, 'pid': 123}, headers)[0], 400)
        manager.toggle_client.assert_not_called()
        self.assertEqual(self.request('POST', '/api/clients', body, headers)[0], 200)
        manager.toggle_client.assert_called_once_with(body)

    def test_explicit_claude_initialization_requires_auth_csrf_and_forwards_choice(self):
        manager = self.server.desktop_sessions = Mock()
        manager.toggle_client.return_value = {'clients': []}
        route = '/api/clients'
        self.assertEqual(self.request('POST', route)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', route, headers={'Cookie': headers['Cookie']})[0], 403)
        manager.toggle_client.assert_not_called()
        body = {'provider': 'claude', 'enabled': True, 'initializeDesktop': True}
        self.assertEqual(self.request('POST', route, body, headers)[0], 200)
        manager.toggle_client.assert_called_once_with(body)
        manager.toggle_client.reset_mock()
        for field, value in (('script', 'alert(1)'), ('force', True), ('action', 'eval')):
            with self.subTest(field=field):
                self.assertEqual(self.request('POST', route, {**body, field: value}, headers)[0], 400)
        manager.toggle_client.assert_not_called()

    def test_desktop_notifications_require_auth_csrf_and_keep_provider(self):
        self.server.desktop_sessions = Mock()
        self.server.notifications = Mock()
        self.server.notifications.policy.return_value = {'available': True, 'requests': 'inherit', 'completion': 'on'}
        route = '/api/desktop-sessions/claude/notifications?sessionId=native-id'
        self.assertEqual(self.request('GET', route)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', route, headers=headers)[0], 200)
        self.server.notifications.policy.assert_called_with('native-id', 'desktop:claude', None)
        value = {'requests': 'inherit', 'completion': 'on'}
        self.assertEqual(self.request('POST', route, value, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', route, value, headers)[0], 200)
        self.server.notifications.policy.assert_called_with('native-id', 'desktop:claude', value)
        self.server.desktop_sessions.call.assert_not_called()

    def test_desktop_accounts_web_is_read_switch_only(self):
        self.server.desktop_sessions = Mock()
        self.server.desktop_sessions.client_accounts.return_value = {'accounts': [], 'activeId': None}
        route = '/api/desktop-sessions/deepseek/accounts'
        self.assertEqual(self.request('GET', route)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', route, headers=headers)[0], 200)
        self.assertEqual(self.request('POST', route, {'action': 'import-current'}, headers)[0], 403)
        for provider in ('deepseek', 'claude'):
            for operation in ('edit', 'save-api', 'rename', 'remove'):
                with self.subTest(provider=provider, operation=operation):
                    self.assertEqual(self.request('POST', '/api/desktop-sessions/'+provider+'/accounts',
                                                  {'action': operation, 'id': 'one', 'apiKey': 'private-key'}, headers)[0], 403)
        self.assertEqual(self.request('POST', route, {'action': 'switch', 'id': 'one'}, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', route, {'action': 'details', 'id': 'one'}, headers)[0], 200)
        self.server.desktop_sessions.client_accounts.assert_called_with('deepseek', {'action': 'details', 'operation': 'details', 'id': 'one'})
    def test_desktop_routes_auth_csrf_and_no_install_or_raw_rpc(self):
        self.server.desktop_sessions = Mock()
        self.server.desktop_sessions.call.return_value = {'connected':True,'sessions':[]}
        self.server.desktop_sessions.list_with_activity.return_value = {'connected':True,'sessions':[]}
        self.assertEqual(self.request('GET','/api/desktop-sessions/deepseek/list')[0],401)
        headers=self.login()
        self.assertEqual(self.request('GET','/api/desktop-sessions/deepseek/list',headers=headers)[0],200)
        self.assertEqual(self.request('POST','/api/desktop-sessions/deepseek/send',{'sessionId':'original','id':str(uuid.uuid4()),'text':'hi'}, {'Cookie':headers['Cookie']})[0],403)
        self.assertEqual(self.request('POST','/api/desktop-sessions/deepseek/install',{},headers)[0],404)
        self.assertEqual(self.request('POST','/api/desktop-sessions/claude/eval',{},headers)[0],404)
        self.assertEqual(self.request('GET','/api/desktop-sessions/deepseek/send',headers=headers)[0],404)
        body={'sessionId':'session-original','id':str(uuid.uuid4()),'text':'hi'}
        self.assertEqual(self.request('POST','/api/desktop-sessions/deepseek/send',body,headers)[0],200)
        self.server.desktop_sessions.call.assert_called_with('deepseek','send','session-original',{'id':body['id'],'text':'hi'})

    def test_project_directory_routes_require_auth_csrf_and_exact_environment_payload(self):
        manager = self.server.desktop_sessions = Mock()
        manager.project_directories.return_value = {'path': '/fixture', 'directories': []}
        manager.register_project.return_value = {'key': 'registered-project', 'cwd': '/fixture'}
        for provider in ('claude', 'deepseek'):
            route = '/api/desktop-sessions/'+provider+'/projects/'
            self.assertEqual(self.request('POST', route+'browse')[0], 401)
            headers = self.login()
            self.assertEqual(self.request('POST', route+'browse', headers={'Cookie': headers['Cookie']})[0], 403)
            self.assertEqual(self.request('GET', route+'browse', headers=headers)[0], 405)
            body = {'environment': 'cli|local|fixture', 'path': '/fixture',
                    'hidden': False, 'name': None, 'identity': None}
            self.assertEqual(self.request('POST', route+'browse', body, headers)[0], 200)
            manager.project_directories.assert_called_with(provider, **body)
            self.assertEqual(self.request('POST', route+'browse', {**body, 'alias': 'untrusted'}, headers)[0], 400)
            registration = {'environment': body['environment'], 'path': '/fixture', 'createDirectory': False}
            self.assertEqual(self.request('POST', route+'register', registration, headers)[0], 200)
            manager.register_project.assert_called_with(provider, body['environment'], '/fixture', False)
            self.assertEqual(self.request('POST', route+'register', {**registration, 'cwd': '/other'}, headers)[0], 400)

    def test_native_process_recovery_is_not_exposed_to_authenticated_web_clients(self):
        self.server.desktop_sessions = Mock()
        self.server.desktop_sessions.toggle_client.return_value = {'clients': []}
        headers = self.login()
        for action in ('deepseek-recovery-preview', 'deepseek-recovery-confirm', 'recover-deepseek', 'quit-client'):
            route = '/api/desktop-sessions/deepseek/' + action
            self.assertEqual(self.request('GET', route, headers=headers)[0], 404)
            self.assertEqual(self.request('POST', route, {'token': 'fixture'}, headers)[0], 404)
        self.assertEqual(self.request('POST', '/api/clients', {
            'provider': 'deepseek', 'enabled': False, 'action': 'deepseek-recovery-confirm', 'token': 'fixture'
        }, headers)[0], 400)
        self.server.desktop_sessions.control.assert_not_called()
        self.server.desktop_sessions.call.assert_not_called()
        self.server.desktop_sessions.toggle_client.assert_not_called()

    def test_zcode_initialization_requires_authenticated_gateway(self):
        manager = self.server.desktop_sessions = Mock()
        manager.toggle_client.return_value = {'clients': []}
        body = {'provider': 'zcode', 'enabled': True, 'initializeDesktop': True}
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/clients', body, headers)[0], 200)
        manager.toggle_client.assert_called_once_with(body)
        manager.reset_mock()
        self.server.auth.config['mode'] = 'none'
        self.assertEqual(self.request('POST', '/api/clients', body, headers)[0], 403)
        manager.toggle_client.assert_not_called()

    def test_client_disable_choice_is_forwarded_with_auth_and_csrf(self):
        self.server.desktop_sessions = Mock()
        self.server.desktop_sessions.toggle_client.return_value = {'clients': []}
        headers = self.login()
        for quit_desktop in (False, True):
            body = {'provider': 'claude', 'enabled': False, 'quitDesktop': quit_desktop}
            # Auth rejects before reading a body. Avoid a Windows TCP reset from
            # unread request bytes obscuring the expected CSRF response.
            self.assertEqual(self.request('POST', '/api/clients', headers={'Cookie': headers['Cookie']})[0], 403)
            self.assertEqual(self.request('POST', '/api/clients', body, headers)[0], 200)
            self.server.desktop_sessions.toggle_client.assert_called_with(body)
        self.assertEqual(self.request('POST', '/api/clients', {
            'provider': 'claude', 'enabled': False, 'quitDesktop': False, 'force': True}, headers)[0], 400)


    def test_record_directory_is_available_only_to_explicit_host_read_requests(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve(); record = root/'record.json'; record.write_text('{}')
        manager = DesktopSessions(root/'gateway'); self.addCleanup(manager.close)
        manager.config['enabled'] = {'deepseek': True}; self.server.desktop_sessions = manager
        adapter = Mock(); adapter.call.return_value = {'sessions': [{'id': 'native', 'host': 'local', 'cwd': ''}]}
        adapter.record_path.return_value = str(record); manager.adapters['deepseek'] = adapter
        path = '/api/desktop-sessions/deepseek/workspace?sessionId=native'
        self.assertEqual(self.request('GET', path+'&scope=host')[0], 401)
        headers = self.login()
        result = self.request('GET', path+'&scope=host', headers=headers)
        self.assertEqual(result[0], 200); self.assertEqual(result[2]['path'], root.as_posix())
        self.assertIsNone(result[2]['projectPath'])
        self.assertNotEqual(self.request('GET', path, headers=headers)[0], 200)
        self.assertNotEqual(self.request('POST', path+'&scope=host', {}, headers)[0], 200)
        adapter.record_path.assert_called_once_with('native')

    def test_client_management_and_workspace_http_are_authenticated_and_scoped(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temp.cleanup)
        root = Path(temp.name); (root/'only-here.txt').write_text('desktop workspace')
        manager = DesktopSessions(root)
        def close_manager():
            manager.close()
            if hasattr(manager, 'terminals'):
                self.assertTrue(all(session.done.is_set() for session in manager.terminals.sessions.values()),
                                'Desktop manager did not reap its owned terminal')
        self.addCleanup(close_manager)
        self.server.desktop_sessions = manager
        self.server.bridge.for_host = lambda host: self.server.bridge
        adapter = Mock()
        adapter.call.side_effect = lambda action, *args: ({'connected': True, 'configured': True} if action == 'status' else
            {'sessions': [{'id': 'native-session', 'cwd': str(root)}]} if action == 'list' else {'models': [{'id':'upstream/model'}]})
        manager.adapters['deepseek'] = adapter
        manager._start_deepseek = Mock()
        inspector = patch('bridge.clients.lifecycle.inspect_client', return_value={'running': False})
        inspector.start(); self.addCleanup(inspector.stop)
        self.assertEqual(self.request('GET', '/api/clients')[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/clients', {'provider':'deepseek','enabled':True}, {'Cookie':headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', '/api/clients', {'provider':'deepseek','enabled':True}, headers)[0], 200)
        path = '/api/desktop-sessions/deepseek/workspace?sessionId=native-session'
        result = self.request('GET', path, headers=headers)
        self.assertEqual(result[0], 200)
        self.assertIn('only-here.txt', [row['name'] for row in result[2]['entries']])
        self.assertEqual(self.request('GET', path+'&path=..', headers=headers)[0], 403)
        self.assertNotEqual(self.request('GET', path.replace('native-session','other'), headers=headers)[0], 200)
        terminal = '/api/desktop-sessions/deepseek/terminal?sessionId=native-session'
        identifier = str(uuid.uuid4())
        result = self.request('POST', terminal, {'action':'open','id':identifier,'cols':80,'rows':24}, headers)
        self.assertEqual(result[0], 200)
        self.assertTrue(result[2]['running'])
        self.assertEqual(self.request('POST', terminal, {'action':'close','id':identifier}, headers)[0], 200)
        self.assertEqual(self.request('POST', '/api/clients', {'provider':'deepseek','enabled':False}, headers)[0], 200)
        self.assertNotEqual(self.request('GET', path, headers=headers)[0], 200)
