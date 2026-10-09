import json
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.integrations import manager as manager_module
from bridge.integrations.manager import DesktopSessions
from bridge.integrations.errors import BridgeUnavailable


class Reads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.manager = DesktopSessions(self.temp.name)
        self.addCleanup(self.manager.close)
        self.adapter = Mock()
        self.manager.adapters['deepseek'] = self.adapter

    def concurrent(self, action, body=None, error=None):
        entered, release, joined = threading.Event(), threading.Event(), threading.Event()
        count, lock = [0], threading.Lock()
        class ObservedFuture(Future):
            def result(self, *args, **kwargs):
                with lock:
                    count[0] += 1
                    if count[0] >= 5:
                        joined.set()
                return super().result(*args, **kwargs)
        def native(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('fixture was not released')
            if error:
                raise error
            return {'items': [{'text': 'original'}]}
        self.adapter.call.side_effect = native
        with patch('bridge.integrations.manager.Future', ObservedFuture), ThreadPoolExecutor(6) as pool:
            first = pool.submit(self.manager.call, 'deepseek', action, 'one', body)
            self.assertTrue(entered.wait(2))
            rest = [pool.submit(self.manager.call, 'deepseek', action, 'one', body) for _ in range(5)]
            try:
                self.assertTrue(joined.wait(2), 'concurrent readers did not join')
            finally:
                release.set()
            if error:
                for pending in [first, *rest]:
                    with self.assertRaises(type(error)):
                        pending.result(2)
                return
            return [pending.result(2) for pending in [first, *rest]]

    def test_identical_reads_join_only_while_pending_and_copy_results(self):
        values = self.concurrent('detail', {'after': 12})
        self.assertEqual(self.adapter.call.call_count, 1)
        values[0]['items'][0]['text'] = 'changed'
        self.assertEqual(values[1]['items'][0]['text'], 'original')
        self.manager.call('deepseek', 'detail', 'one', {'after': 12})
        self.assertEqual(self.adapter.call.call_count, 2)
        self.assertEqual(self.manager.reads, {})

    def test_failure_is_shared_but_next_call_retries(self):
        self.concurrent('catalog', error=BridgeUnavailable('offline'))
        self.assertEqual(self.adapter.call.call_count, 1)
        self.adapter.call.side_effect = None
        self.adapter.call.return_value = {'models': []}
        self.manager.call('deepseek', 'catalog', 'one')
        self.assertEqual(self.adapter.call.call_count, 2)

    def test_provider_session_and_parameters_are_distinct(self):
        gate = threading.Barrier(5)
        self.manager.adapters['claude'].call = self.adapter.call
        self.adapter.call.side_effect = lambda *args: (gate.wait(3), {'ok': True})[1]
        cases = [('deepseek', 'detail', 'one', {}), ('claude', 'detail', 'one', {}),
                 ('deepseek', 'detail', 'two', {}), ('deepseek', 'detail', 'one', {'after': 1}),
                 ('deepseek', 'catalog', 'one', {})]
        with ThreadPoolExecutor(5) as pool:
            futures = [pool.submit(self.manager.call, *case) for case in cases]
            for future in futures:
                self.assertEqual(future.result(5), {'ok': True})
        self.assertEqual(self.adapter.call.call_count, 5)

    def test_account_or_connector_change_rejects_old_response(self):
        entered, release = threading.Event(), threading.Event()
        def old(*args):
            entered.set()
            release.wait(3)
            return {'account': 'old'}
        self.adapter.call.side_effect = old
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.manager.call, 'deepseek', 'detail', 'one')
            self.assertTrue(entered.wait(2))
            with self.manager._changing('deepseek'):
                fresh = Mock()
                fresh.call.return_value = {'account': 'new'}
                self.manager.adapters['deepseek'] = fresh
            self.assertEqual(self.manager.call('deepseek', 'detail', 'one'), {'account': 'new'})
            release.set()
            with self.assertRaises(BridgeUnavailable):
                pending.result(2)

    def test_mutations_never_join_read_requests(self):
        self.adapter.call.return_value = {'ok': True}
        for action, extra in [('send', {'text': 'test'}), ('stop', {}), ('respond', {}), ('access', {'mode': 'test'})]:
            for _ in range(2):
                self.manager.call('deepseek', action, 'one', {'id': str(uuid.uuid4()), **extra})
        self.assertEqual(self.adapter.call.call_count, 8)
        self.assertEqual(self.manager.reads, {})

    def test_reads_during_mutation_are_not_shared(self):
        gate = threading.Barrier(2)
        self.adapter.call.side_effect = lambda *args: (gate.wait(2), {'ok': True})[1]
        with self.manager._changing('deepseek'), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.manager.call, 'deepseek', 'detail', 'one') for _ in range(2)]
            for future in futures:
                self.assertEqual(future.result(3), {'ok': True})
        self.assertEqual(self.adapter.call.call_count, 2)

    def test_chat_write_keeps_progress_reads_valid_without_joining_old_read(self):
        entered, release = threading.Event(), threading.Event()
        reads = []
        def native(action, *args):
            if action == 'detail':
                reads.append(len(reads))
                if len(reads) == 1:
                    entered.set()
                    release.wait(3)
                return {'status': 'running'}
            return {'status': 'accepted'}
        self.adapter.call.side_effect = native
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.manager.call, 'deepseek', 'detail', 'one')
            self.assertTrue(entered.wait(2))
            try:
                self.manager.call('deepseek', 'send', 'one', {'id': str(uuid.uuid4()), 'text': 'test'})
                self.assertEqual(self.manager.call('deepseek', 'detail', 'one'), {'status': 'running'})
            finally:
                release.set()
            self.assertEqual(pending.result(2), {'status': 'running'})
        self.assertEqual(len(reads), 2)

    def switch_account(self):
        store = Mock()
        store.restore.return_value = {'fixture': True}
        store.public.return_value = {'activeIds': ['new-account']}
        with patch.object(self.manager, '_account_store', return_value=store), \
             patch.object(self.manager, '_descriptor', return_value={}), \
             patch.object(self.manager, '_stop_client'), \
             patch('bridge.integrations.client_launch.inspect_client', return_value={'running': False}):
            self.manager.client_accounts('deepseek', {'operation': 'switch', 'id': 'new-account'})

    def test_account_change_during_result_copy_rejects_old_read(self):
        entered, release = threading.Event(), threading.Event()
        self.adapter.call.return_value = {'account': 'old'}
        copy = manager_module._copy_read
        def delayed_copy(value):
            if isinstance(value, dict) and value.get('account') == 'old':
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('fixture was not released')
            return copy(value)
        with patch.object(manager_module, '_copy_read', delayed_copy), ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.manager.call, 'deepseek', 'account')
            try:
                self.assertTrue(entered.wait(2))
                self.switch_account()
            finally:
                release.set()
            with self.assertRaises(BridgeUnavailable):
                pending.result(2)

    def test_account_change_after_list_returns_cannot_repopulate_workspace_cache(self):
        self.manager.config['enabled'] = {'deepseek': True}
        old_root, new_root = Path(self.temp.name)/'old', Path(self.temp.name)/'new'
        old_root.mkdir()
        new_root.mkdir()
        self.adapter.call.return_value = {'sessions': [{'id': 'same-id', 'cwd': str(old_root)}]}
        entered, release = threading.Event(), threading.Event()
        call = self.manager.call
        def delayed_call(*args, **kwargs):
            result = call(*args, **kwargs)
            entered.set()
            if not release.wait(3):
                raise TimeoutError('fixture was not released')
            return result
        with patch.object(self.manager, 'call', delayed_call), ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.manager.workspace_bridge, 'deepseek', 'same-id')
            try:
                self.assertTrue(entered.wait(2))
                self.switch_account()
                self.assertNotIn(('deepseek', 'same-id'), self.manager.workspace_roots)
                self.adapter.call.return_value = {'sessions': [{'id': 'same-id', 'cwd': str(new_root)}]}
            finally:
                release.set()
            with self.assertRaises(BridgeUnavailable):
                pending.result(2)
        self.assertNotIn(('deepseek', 'same-id'), self.manager.workspace_roots)
        self.assertEqual(self.manager.workspace_bridge('deepseek', 'same-id').root, str(new_root))
        self.assertEqual(self.adapter.call.call_count, 2)

    def test_workspace_cache_does_not_survive_connector_generation_change(self):
        self.manager.config['enabled'] = {'deepseek': True}
        old_root, new_root = Path(self.temp.name)/'old', Path(self.temp.name)/'new'
        old_root.mkdir()
        new_root.mkdir()
        self.adapter.call.return_value = {'sessions': [{'id': 'same-id', 'cwd': str(old_root)}]}
        self.assertEqual(self.manager.workspace_bridge('deepseek', 'same-id').root, str(old_root))
        with self.manager._changing('deepseek'):
            fresh = Mock()
            fresh.call.return_value = {'sessions': [{'id': 'same-id', 'cwd': str(new_root)}]}
            self.manager.adapters['deepseek'] = fresh
        self.assertEqual(self.manager.workspace_bridge('deepseek', 'same-id').root, str(new_root))
        fresh.call.assert_called_once_with('list', None, {})
