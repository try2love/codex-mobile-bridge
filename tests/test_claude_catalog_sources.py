import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.errors import BridgeUnavailable

ROOT = Path(__file__).resolve().parents[1]
HISTORY = '客户端尚未提供当前可选模型，历史模型仅供查看'


class ClaudeCatalogSources(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temp.cleanup)
        self.manager = DesktopSessions(Path(temp.name))
        self.addCleanup(self.manager.close)
        adapter = self.manager.adapters['claude']
        adapter.index['session'] = {'backend': 'cowork'}
        adapter.desktop = SimpleNamespace(connected=True, capabilities={'cowork': ['setModel', 'setEffort']})
        self.addCleanup(setattr, adapter, 'desktop', None)
        self.store = Mock(lock=threading.RLock())
        self.store._current.return_value = ('api', 'current', {})
        for name, value in [('_account_store', self.store), ('_account_model_identity', 'api-account'),
                            ('_account_model_live', False), ('_model_live', True)]:
            mocked = patch.object(self.manager, name, return_value=value)
            mocked.start(); self.addCleanup(mocked.stop)
        self.account = {'models': [{'id': 'shared', 'efforts': []}, {'id': 'upstream'}],
                        'groups': [{'surface': 'api', 'source': 'upstream-api',
                                    'models': [{'id': 'shared'}, {'id': 'upstream'}]}]}
        self.native = {'surface': 'cowork',
                       'models': [{'id': 'shared', 'efforts': ['high']}, {'id': 'historical'}],
                       'source': 'session-history', 'notice': HISTORY}
        self.store.models.side_effect = lambda: self.account
        self.manager._read = Mock(side_effect=lambda *args, **kwargs: {'groups': [self.native]})

    def catalog(self, refresh=False):
        return self.manager._catalog_models('claude', 'session', {'refresh': refresh})

    def test_fresh_merge_tracks_sources_and_replaces_history_only_notice(self):
        result = self.catalog(True)
        rows = {row['id']: row for row in result['models']}
        self.assertEqual(rows['shared']['source'], 'upstream-api')
        self.assertEqual(rows['shared']['efforts'], ['high'])
        self.assertEqual(rows['historical']['source'], 'session-history')
        self.assertNotEqual(result.get('notice'), HISTORY)
        self.assertIn('历史', result['notice'])
        cached = self.manager.model_store.read('api-account')[1]
        self.assertEqual(cached['groups'][0]['models'], result['models'])
        self.assertEqual(cached['modelSourcesVersion'], 1)

    def test_cached_directory_keeps_provenance_and_timestamp_without_native_queries(self):
        first = self.catalog(True)
        stamp = first['cache']['updatedAt']
        self.manager._read.side_effect = AssertionError('native query')
        self.store.models.side_effect = AssertionError('upstream query')
        result = self.catalog()
        self.assertEqual(result['cache']['updatedAt'], stamp)
        self.assertEqual(result['models'], first['models'])
        self.assertNotEqual(result.get('notice'), HISTORY)
        self.assertTrue(result['capabilities']['manualModels'])
        self.assertNotIn('capabilities', self.manager.model_store.read('api-account')[1])

    def test_previous_session_cache_is_not_shared_without_account_evidence(self):
        identity = self.manager._surface_model_identity('api-account', 'cowork')
        self.manager.model_store.write(identity, {'models': [{'id': 'wrong-account'}]})
        result = self.catalog()
        self.assertNotIn('wrong-account', [row['id'] for row in result['models']])
        self.store.models.assert_called_once()

    def test_genuine_history_only_result_keeps_warning_when_discovery_fails(self):
        self.account = {'models': [], 'groups': []}
        result = self.catalog(True)
        self.assertEqual(result['notice'], HISTORY)
        self.assertEqual({row['source'] for row in result['models']}, {'session-history'})

    def test_configured_models_are_not_claimed_as_upstream_results(self):
        self.account['groups'][0].update(source='desktop-config', notice='配置来源提示')
        result = self.catalog(True)
        self.assertEqual(result['models'][0]['source'], 'desktop-config')
        self.assertIn('配置来源提示', result['notice'])

    def test_all_models_confirmed_by_upstream_remove_history_warning_and_survive_cache(self):
        self.native['models'] = [self.native['models'][0]]
        fresh = self.catalog(True)
        self.manager._read.side_effect = AssertionError('native query')
        self.store.models.side_effect = AssertionError('upstream query')
        cached = self.catalog()
        self.assertNotIn('notice', fresh)
        self.assertNotIn('notice', cached)
        self.assertEqual(cached['source'], 'upstream-api')
        self.assertEqual(cached['models'], fresh['models'])
        self.assertTrue(cached['capabilities']['manualEffort'])

    def test_account_change_during_directory_read_is_rejected(self):
        with patch.object(self.manager, '_account_model_identity', side_effect=['api-account', 'api-account', 'changed']):
            with self.assertRaises(BridgeUnavailable):
                self.catalog(True)

    def test_official_account_never_merges_api_catalog(self):
        self.store._current.return_value = ('official', 'current', None)
        result = self.catalog(True)
        self.assertEqual(result['notice'], HISTORY)
        self.assertEqual([row['id'] for row in result['models']], ['shared', 'historical'])
