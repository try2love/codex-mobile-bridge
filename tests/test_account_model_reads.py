import copy
import threading
import unittest
from unittest.mock import patch

import test_account_info
from bridge.features.accounts.accounts import private_json


class ModelReadTests(unittest.TestCase):
    setUp = test_account_info.InfoTests.setUp
    tearDown = test_account_info.InfoTests.tearDown

    def active(self, kind='api'):
        if kind == 'api':
            row = self.manager.add_api({'name': 'Fixture API', 'baseUrl': 'https://fixture.test/v1',
                                        'apiKey': 'fixture-key', 'model': 'fixture'})['accounts'][-1]
        else:
            row, auth = test_account_info.InfoTests.official(self)
            private_json(self.home/'auth.json', auth)
        self.manager.mark_active(row['id'])
        return row

    def test_slow_model_reads_release_management_lock_for_api_and_official_accounts(self):
        for kind in ('api', 'chatgpt'):
            with self.subTest(kind=kind):
                self.active(kind)
                entered, release = threading.Event(), threading.Event()
                results, errors = [], []
                def query(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('query release timeout')
                    return ['fixture'] if kind == 'api' else {'models': [{'id': 'fixture'}]}
                def run():
                    try:
                        results.append(self.manager.current_models())
                    except Exception as error:
                        errors.append(error)
                target = (patch('bridge.features.accounts.accounts.model_ids', side_effect=query) if kind == 'api'
                          else patch.object(self.bridge.catalog_reader, 'get_kind', create=True, side_effect=query))
                with target:
                    worker = threading.Thread(target=run)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        acquired = self.manager.lock.acquire(timeout=.5)
                        self.assertTrue(acquired, 'model I/O held the account management lock')
                        if acquired:
                            try:
                                self.manager.public()
                            finally:
                                self.manager.lock.release()
                    finally:
                        release.set()
                        worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(results[0]['models'][0]['id'], 'fixture')

    def test_concurrent_api_model_reads_share_the_cache_without_blocking_status(self):
        self.active()
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        def query(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError('query release timeout')
            return ['fixture']
        def run():
            try:
                results.append(self.manager.current_models())
            except Exception as error:
                errors.append(error)
        with patch('bridge.features.accounts.accounts.model_ids', side_effect=query) as upstream:
            workers = [threading.Thread(target=run) for _ in range(2)]
            workers[0].start()
            try:
                self.assertTrue(entered.wait(2))
                workers[1].start()
            finally:
                release.set()
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(3)
            upstream.assert_called_once()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)

    def test_model_results_are_discarded_when_active_connection_or_phase_changes(self):
        row = self.active()
        index, config = copy.deepcopy(self.manager.index), (self.home/'config.toml').read_bytes()
        for change in ('selection', 'config', 'phase', 'profile'):
            with self.subTest(change=change):
                self.manager.index = copy.deepcopy(index)
                (self.home/'config.toml').write_bytes(config)
                self.manager.state['phase'] = 'idle'
                def query(*args):
                    if change == 'selection':
                        self.manager.index['activeId'] = None
                    elif change == 'config':
                        (self.home/'config.toml').write_text('# changed externally')
                    elif change == 'phase':
                        self.manager.state['phase'] = 'preparing'
                    else:
                        self.manager.row(row['id'])['model'] = 'changed'
                    return ['fixture']
                with patch('bridge.features.accounts.accounts.model_ids', side_effect=query):
                    with self.assertRaises(ValueError):
                        self.manager.current_models(refresh=True)

    def test_api_model_cache_rejects_deleted_profiles_and_changed_credentials(self):
        row = self.active()
        for change in ('delete', 'credentials'):
            with self.subTest(change=change):
                self.manager.index['accounts'] = [dict(row)]
                private_json(self.manager.directory(row['id'])/'api.json', {'key': 'fixture-key'})
                self.manager.info.entries.clear()
                def query(*args):
                    if change == 'delete':
                        self.manager.index['accounts'] = []
                    else:
                        private_json(self.manager.directory(row['id'])/'api.json', {'key': 'new-key'})
                    return ['fixture']
                with patch('bridge.features.accounts.accounts.model_ids', side_effect=query):
                    with self.assertRaises(ValueError):
                        self.manager.api_models(row)
                self.assertNotIn(row['id'], self.manager.info.entries)

    def test_official_model_results_verify_connection_after_query(self):
        self.active('chatgpt')
        def query(*args, **kwargs):
            (self.home/'config.toml').write_text('# changed externally')
            return {'models': [{'id': 'fixture'}]}
        with patch.object(self.bridge.catalog_reader, 'get_kind', create=True, side_effect=query):
            with self.assertRaises(ValueError):
                self.manager.current_models()

    def test_different_api_profiles_query_concurrently_and_release_the_credential_lock(self):
        first = self.active()
        second = self.manager.add_api({'name': 'Other API', 'baseUrl': 'https://other.test/v1',
                                       'apiKey': 'other-key', 'model': 'other'})['accounts'][-1]
        entered, other_entered, release = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def query(url, key):
            if url == first['baseUrl']:
                entered.set()
                if not release.wait(3):
                    raise AssertionError('query release timeout')
            else:
                other_entered.set()
            return ['fixture']
        def read_other():
            try:
                self.manager.api_models(second)
            except Exception as error:
                errors.append(error)
        with patch('bridge.features.accounts.accounts.model_ids', side_effect=query):
            first_worker = threading.Thread(target=self.manager.info.read, args=(first, 'models'))
            other_worker = threading.Thread(target=read_other)
            first_worker.start()
            try:
                self.assertTrue(entered.wait(2))
                acquired = self.bridge.account.lock.acquire(timeout=.2)
                if acquired:
                    self.bridge.account.lock.release()
                self.assertTrue(acquired, 'API model reads held the credential lock')
                other_worker.start()
                self.assertTrue(other_entered.wait(1), 'an unrelated API waited for the slow provider')
            finally:
                release.set()
                first_worker.join(3)
                if other_worker.ident is not None:
                    other_worker.join(3)
        self.assertFalse(first_worker.is_alive())
        self.assertFalse(other_worker.is_alive())
        self.assertEqual(errors, [])
