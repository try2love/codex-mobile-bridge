import json
import time
import unittest
import uuid
import copy
import threading
from unittest.mock import Mock, patch

import test_account
import test_account_info
from bridge.features.accounts.account import Account
from bridge.features.accounts.info import CURRENT_LOGIN_ID
from bridge.features.accounts.monitor import AccountMonitor
from bridge.features.accounts.accounts import operation, private_json
from bridge.features.notifications.channels import save_settings


def immediate_worker(*, target, args, **kwargs):
    thread = Mock()
    thread.start.side_effect = lambda: target(*args)
    return thread


class CurrentLoginTests(unittest.TestCase):
    setUp = test_account_info.InfoTests.setUp
    tearDown = test_account_info.InfoTests.tearDown

    def login(self):
        rpc = test_account.FakeRPC()
        rpc.config = {'model_provider': 'custom'}
        rpc.auth['workspaceRouting'] = {'backendOrigin': 'https://chatgpt.com', 'chatgptAccountId': 'fixture'}
        rpc.auth['account']['email'] = 'fixture@example.test'
        context = Account.login_context(rpc)
        info = self.manager.info
        info.identity = {'kind': 'api', 'name': 'API · custom', 'login': {'context': context, 'owner': None}}
        info.stamp = info.source_stamp()
        info.checked_at = time.time()
        return rpc, context

    def saved_login(self):
        rpc, context = self.login()
        row, auth = test_account_info.InfoTests.official(self)
        private_json(self.home / 'auth.json', auth)
        self.manager.info.identity['login']['owner'] = row['tokenOwner']
        self.manager.info.stamp = self.manager.info.source_stamp()
        self.bridge.account.subscription = lambda *args, **kwargs: {'source': 'login', 'observedAt': time.time()}
        subscription = patch('bridge.features.accounts.account.subscription_period',
                             return_value={'source': 'login', 'observedAt': time.time()})
        subscription.start()
        self.addCleanup(subscription.stop)
        return row, auth, rpc, context

    def test_importing_logged_in_account_twice_merges_identity_and_preserves_current_connection(self):
        row, _, rpc, _ = self.saved_login()
        self.manager.index['accounts'] = []
        before = self.manager.snapshot_files()
        with patch('bridge.features.accounts.accounts.ManagedRPC', return_value=rpc):
            candidates = self.manager.scan({})['discovery']['candidates']
        candidate = next(row for row in candidates if row['kind'] == 'chatgpt')
        for _ in range(2):
            self.manager.import_account({'candidateId': candidate['id'], 'name': 'My account'})
        value = self.manager.public()
        self.assertEqual(len(value['accounts']), 1)
        self.assertEqual(value['loginAccount']['id'], value['accounts'][0]['id'])
        self.assertEqual(value['loginAccount']['name'], 'My account')
        self.assertEqual(value['current']['name'], 'API · custom')
        self.assertIsNone(self.manager.index['activeId'])
        self.assertEqual(self.manager.snapshot_files(), before)

    def test_imported_current_account_uses_fresh_native_credentials_for_usage_and_reset(self):
        row, _, rpc, context = self.saved_login()
        identity = copy.deepcopy(self.manager.info.identity)
        private_json(self.manager.directory(row['id']) / 'auth.json', {'tokens': {'account_id': 'expired-import'}})
        before = self.manager.snapshot_files()
        homes = []
        def native_rpc(target):
            homes.append(target.home)
            self.assertEqual(target.home, self.home)
            return rpc
        with patch.object(self.manager.info, 'read_identity', return_value=identity), \
                patch('bridge.features.accounts.account.Account.rpc', native_rpc), \
                patch('bridge.features.accounts.accounts.ManagedRPC', return_value=rpc) as managed:
            self.manager.account({'id': row['id'], 'operation': 'read', 'refresh': False})
            self.manager.account({'id': row['id'], 'operation': 'consume', 'confirmed': True,
                                  'requestId': str(uuid.uuid4()), 'accountKey': context['accountKey'], 'creditId': 'card-1'})
            self.manager.info.read(row, 'usage')
            self.assertEqual(managed.call_args.args[0], self.home)
        self.assertEqual(homes, [self.home, self.home])
        self.assertEqual(self.manager.info.entries[row['id']]['usage']['status'], 'ready')
        self.assertEqual(self.manager.snapshot_files(), before)
        self.assertIsNone(self.manager.index['activeId'])

    def test_same_email_different_workspace_uses_saved_credentials_and_keeps_login_separate(self):
        row, auth, _, _ = self.saved_login()
        other = {**row, 'id': 'b' * 32, 'name': 'Other workspace', 'tokenOwner': ['other-org', *row['tokenOwner'][1:]]}
        other_auth = copy.deepcopy(auth)
        other_auth['tokens']['account_id'] = 'other-org'
        self.manager.index['accounts'].append(other)
        private_json(self.manager.directory(other['id']) / 'auth.json', other_auth)
        identity = copy.deepcopy(self.manager.info.identity)
        before = self.manager.snapshot_files()
        rpc = test_account.FakeRPC()
        rpc.auth['account']['email'] = row['email']
        def saved_rpc(target):
            self.assertEqual(target.home, self.manager.directory(other['id']))
            return rpc
        with patch.object(self.manager.info, 'read_identity', return_value=identity), \
                patch('bridge.features.accounts.account.Account.rpc', saved_rpc):
            result = self.manager.account({'id': other['id'], 'operation': 'read'})
        self.assertTrue(result['visible'])
        self.assertEqual(self.manager.public()['loginAccount']['id'], row['id'])
        self.assertEqual(len(self.manager.public()['accounts']), 2)
        self.assertEqual(self.manager.snapshot_files(), before)

    def test_deleting_imported_login_keeps_native_card_and_login_credentials(self):
        row, _, _, _ = self.saved_login()
        before = self.manager.snapshot_files()
        value = self.manager.control({'action': 'delete', 'id': row['id']})
        self.assertEqual(value['accounts'], [])
        self.assertEqual(value['loginAccount']['id'], CURRENT_LOGIN_ID)
        self.assertTrue(value['loginAccount']['transient'])
        self.assertEqual(self.manager.snapshot_files(), before)

    def test_official_model_query_keeps_saved_official_catalog_separate_from_aggregation(self):
        row, _, _, _ = self.saved_login()
        identity = copy.deepcopy(self.manager.info.identity)
        with patch.object(self.manager.info, 'read_identity', return_value=identity), \
                patch('bridge.features.accounts.accounts.ManagedRPC') as managed:
            managed.return_value.__enter__.return_value = test_account_info.InfoTests.rpc(self)
            self.manager.info.read(row, 'models')
            self.assertEqual(managed.call_args.args[0], self.manager.directory(row['id']))
        self.assertEqual(self.manager.info.entries[row['id']]['models']['status'], 'ready')

    def test_proxy_connection_and_native_login_are_displayed_without_enrollment(self):
        _, context = self.login()
        value = self.manager.public()
        self.assertEqual(value['accounts'], [])
        self.assertEqual(value['current']['kind'], 'api')
        self.assertEqual(value['loginAccount']['email'], 'fixture@example.test')
        self.assertTrue(value['loginAccount']['transient'])
        self.assertEqual(value['loginAccount']['accountKey'], context['accountKey'])
        self.assertFalse(self.manager.directory('a' * 32).exists())
        self.assertNotIn('owner', json.dumps(value))

    def check_native_reminders(self, saved_api=False, imported=False):
        if imported:
            _, _, rpc, _ = self.saved_login()
        else:
            rpc, _ = self.login()
        if saved_api:
            row = self.manager.add_api({'name': 'API', 'baseUrl': 'https://fixture.test/v1',
                                        'apiKey': 'fixture-key', 'model': 'fixture'})['accounts'][-1]
            self.manager.info.identity.update(baseUrl=row['baseUrl'], key='fixture-key', model=row['model'])
            self.assertEqual(self.manager.info.current()['id'], row['id'])
        rpc.limits['rateLimits']['primary']['usedPercent'] = 95
        before = self.manager.snapshot_files()
        save_settings(self.root, {'barkEnabled': True, 'barkKey': 'fixture'})
        self.manager.monitor.configure({'lowQuota': True})
        identity = copy.deepcopy(self.manager.info.identity)
        with patch('bridge.features.accounts.info.threading.Thread', side_effect=immediate_worker), \
                patch.object(self.manager.info, 'read_identity', return_value=identity), \
                patch('bridge.features.accounts.accounts.ManagedRPC', return_value=rpc), \
                patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch('bridge.features.accounts.account.subscription_period', return_value={}), \
                patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.manager.monitor.scan()
            self.manager.monitor.scan()
            send.assert_called_once()
        self.assertEqual(sum(method == 'account/rateLimits/read' for method, _ in rpc.calls), 1)
        self.assertEqual(self.manager.snapshot_files(), before)

    def test_unimported_native_login_reminders_work_with_aggregation_and_share_quota_cache(self):
        self.check_native_reminders()

    def test_saved_api_connection_keeps_unimported_native_login_reminders(self):
        self.check_native_reminders(saved_api=True)

    def test_imported_native_login_is_checked_and_notified_once(self):
        self.check_native_reminders(imported=True)

    def test_reminder_history_survives_import_delete_reimport_and_restart(self):
        _, _, rpc, _ = self.saved_login()
        self.manager.index['accounts'] = []
        now = time.time()
        rpc.limits['rateLimits']['primary'].update(usedPercent=95, resetsAt=now+60)
        save_settings(self.root, {'barkEnabled': True, 'barkKey': 'fixture'})
        self.manager.monitor.configure({'lowQuota': True, 'quotaReset': True})
        identity = copy.deepcopy(self.manager.info.identity)
        with patch('bridge.features.accounts.info.threading.Thread', side_effect=immediate_worker), \
                patch.object(self.manager.info, 'read_identity', return_value=identity), \
                patch('bridge.features.accounts.accounts.ManagedRPC', return_value=rpc), \
                patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.manager.monitor.scan()
            self.assertEqual(send.call_count, 1)
            candidate = next(row for row in self.manager.scan({})['discovery']['candidates'] if row['kind'] == 'chatgpt')
            imported = self.manager.import_account({'candidateId': candidate['id'], 'name': 'Native login'})['loginAccount']
            self.manager.monitor.scan()
            self.assertEqual(send.call_count, 1)
            self.manager.control({'action': 'delete', 'id': imported['id']})
            self.manager.monitor.scan()
            self.assertEqual(send.call_count, 1)
            self.manager.import_account({'candidateId': candidate['id'], 'name': 'Native login'})
            self.manager.monitor.scan()
            self.assertEqual(send.call_count, 1)
            rpc.limits['rateLimits']['primary'].update(usedPercent=0, resetsAt=now+18000)
            with patch('bridge.features.accounts.monitor.time.time', return_value=now+301):
                self.manager.monitor.scan()
                self.assertEqual([call.args[1] for call in send.call_args_list], ['Codex 额度不足', 'Codex 额度已恢复'])
                AccountMonitor(self.manager).scan()
                self.assertEqual(send.call_count, 2)

    def test_legacy_native_login_and_saved_profile_share_reminder_identity(self):
        row, _, rpc, _ = self.saved_login()
        self.manager.index['accounts'] = []
        self.manager.info.identity = {'kind': 'chatgpt', 'name': row['email'], 'owner': row['tokenOwner']}
        rpc.config = {'model_provider': 'openai'}
        rpc.limits['rateLimits']['primary']['usedPercent'] = 95
        save_settings(self.root, {'barkEnabled': True, 'barkKey': 'fixture'})
        self.manager.monitor.configure({'lowQuota': True})
        identity = copy.deepcopy(self.manager.info.identity)
        with patch('bridge.features.accounts.info.threading.Thread', side_effect=immediate_worker), \
                patch.object(self.manager.info, 'read_identity', return_value=identity), \
                patch('bridge.features.accounts.accounts.ManagedRPC', return_value=rpc), \
                patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.manager.monitor.scan()
            self.assertEqual(send.call_count, 1)
            candidate = next(row for row in self.manager.scan({})['discovery']['candidates'] if row['kind'] == 'chatgpt')
            self.manager.import_account({'candidateId': candidate['id'], 'name': 'Native login'})
            self.manager.monitor.scan()
            send.assert_called_once()

    def test_reminders_discard_a_native_login_snapshot_after_identity_changes(self):
        _, context = self.login()
        self.manager.info.entries[CURRENT_LOGIN_ID] = {'usage': {
            'status': 'ready', 'accountKey': context['accountKey'], 'updatedAt': time.time(),
            'limits': [{'id': 'codex', 'windows': [{'kind': 'primary', 'remainingPercent': 5,
                                                  'resetsAt': time.time() + 3600}]}]}}
        save_settings(self.root, {'barkEnabled': True, 'barkKey': 'fixture'})
        self.manager.monitor.configure({'lowQuota': True})
        def change_login(value):
            self.manager.info.identity['login']['context'] = {**context, 'accountKey': 'b' * 64}
        with patch.object(self.manager.info, 'request', side_effect=change_login), \
                patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.manager.monitor.scan()
            send.assert_not_called()

    def test_slow_account_reads_release_status_and_chat_gates_but_keep_credential_lock(self):
        for imported, stage in [(saved, phase) for saved in (False, True) for phase in ('quota', 'subscription')]:
            with self.subTest(imported=imported, stage=stage):
                if imported:
                    self.manager.index['accounts'] = []
                    row, _, rpc, _ = self.saved_login()
                    account_id = row['id']
                else:
                    rpc, _ = self.login()
                    account_id = CURRENT_LOGIN_ID
                identity = copy.deepcopy(self.manager.info.identity)
                entered, release = threading.Event(), threading.Event()
                status_ready, operation_ready = threading.Event(), threading.Event()
                errors = []
                def slow_read(*args):
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Fixture timeout')
                    return {}
                rpc.on_limits = slow_read if stage == 'quota' else None
                @operation
                def harmless_operation(bridge):
                    operation_ready.set()
                def read():
                    try:
                        self.manager.account({'id': account_id, 'operation': 'read', 'refresh': True})
                    except Exception as error:
                        errors.append(error)
                def status():
                    self.manager.public()
                    status_ready.set()
                with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                        patch.object(self.manager.info, 'read_identity', return_value=identity), \
                        patch('bridge.features.accounts.account.subscription_period', side_effect=slow_read if stage == 'subscription' else lambda *args: {}):
                    worker = threading.Thread(target=read)
                    worker.start()
                    other = []
                    try:
                        self.assertTrue(entered.wait(2))
                        other = [threading.Thread(target=status), threading.Thread(target=lambda: harmless_operation(self.bridge))]
                        for thread in other:
                            thread.start()
                        self.assertTrue(status_ready.wait(1), 'Status waits for quota I/O')
                        self.assertTrue(operation_ready.wait(1), 'Chat gate waits for quota I/O')
                        acquired = self.bridge.account.lock.acquire(blocking=False)
                        if acquired:
                            self.bridge.account.lock.release()
                        self.assertFalse(acquired, 'Credential reads must remain serialized')
                    finally:
                        release.set()
                        worker.join(3)
                        for thread in other:
                            thread.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])

    def test_saved_read_discards_results_after_its_profile_is_deleted(self):
        row, _, rpc, _ = self.saved_login()
        identity = copy.deepcopy(self.manager.info.identity)
        rpc.on_limits = lambda: self.manager.control({'action': 'delete', 'id': row['id']})
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch.object(self.manager.info, 'read_identity', return_value=identity):
            with self.assertRaises(ValueError):
                self.manager.account({'id': row['id'], 'operation': 'read', 'refresh': True})

    def test_native_read_rechecks_owner_after_subscription(self):
        _, _, rpc, _ = self.saved_login()
        self.manager.index['accounts'] = []
        def replace_owner(*args):
            private_json(self.home/'auth.json', {'tokens': {'account_id': 'other'}})
            return {}
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch('bridge.features.accounts.account.subscription_period', side_effect=replace_owner):
            with self.assertRaises(ValueError):
                self.manager.account({'id': CURRENT_LOGIN_ID, 'operation': 'read', 'refresh': True})

    def test_native_read_discards_results_if_switching_starts_during_subscription(self):
        rpc, _ = self.login()
        def begin_switch(*args):
            self.manager.state['phase'] = 'preparing'
            return {}
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch('bridge.features.accounts.account.subscription_period', side_effect=begin_switch):
            with self.assertRaises(ValueError):
                self.manager.account({'id': CURRENT_LOGIN_ID, 'operation': 'read', 'refresh': True})

    def test_usage_reads_native_login_and_preserves_configuration_and_saved_list(self):
        rpc, _ = self.login()
        before = self.manager.snapshot_files()
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc):
            self.manager.info.read(self.manager.info.current_login(), 'usage')
        value = self.manager.public()
        usage = value['loginAccount']['details']['usage']
        self.assertEqual(usage['limits'][0]['windows'][0]['remainingPercent'], 75)
        self.assertEqual(usage['resetCredits']['availableCount'], 3)
        self.assertEqual(self.manager.snapshot_files(), before)
        self.assertEqual(value['accounts'], [])
        self.assertFalse(any(method.endswith('/consume') for method, _ in rpc.calls))

    def test_discarded_native_query_releases_its_slot_and_can_refresh_after_configuration_returns(self):
        rpc, _ = self.login()
        info = self.manager.info
        original = (self.home/'config.toml').read_bytes()
        with patch('bridge.features.accounts.info.threading.Thread') as worker:
            info.request({'id': CURRENT_LOGIN_ID, 'section': 'usage'})
            args = worker.call_args.kwargs['args']
        rpc.on_limits = lambda: (self.home/'config.toml').write_text('# external change')
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch('bridge.features.accounts.account.subscription_period', return_value={}):
            info.read(*args)
        self.assertNotIn('usage', info.entries[CURRENT_LOGIN_ID])
        (self.home/'config.toml').write_bytes(original)
        rpc.on_limits = None
        with patch('bridge.features.accounts.info.threading.Thread') as worker:
            info.request({'id': CURRENT_LOGIN_ID, 'section': 'usage', 'refresh': True})
            worker.assert_called_once()
            args = worker.call_args.kwargs['args']
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc), \
                patch('bridge.features.accounts.account.subscription_period', return_value={}):
            info.read(*args)
        self.assertEqual(info.entries[CURRENT_LOGIN_ID]['usage']['status'], 'ready')

    def test_old_native_worker_preserves_new_loading_and_completed_slots(self):
        _, original = self.login()
        info = self.manager.info
        for same_login in (False, True):
            for completed in (False, True):
                with self.subTest(same_login=same_login, completed=completed):
                    info.entries.clear()
                    info.identity['login']['context'] = original
                    with patch('bridge.features.accounts.info.threading.Thread') as worker:
                        info.request({'id': CURRENT_LOGIN_ID, 'section': 'usage'})
                        old_args = worker.call_args.kwargs['args']
                        info.entries[CURRENT_LOGIN_ID].pop('usage')
                        context = original if same_login else {**original, 'accountKey': 'b'*64}
                        info.identity['login']['context'] = context
                        info.request({'id': CURRENT_LOGIN_ID, 'section': 'usage'})
                        new_args = worker.call_args.kwargs['args']
                    if completed:
                        with patch.object(self.manager, 'account', return_value={'visible': True, **context}):
                            info.read(*new_args)
                    newest = info.entries[CURRENT_LOGIN_ID]['usage']
                    with patch.object(self.manager, 'account', return_value={'visible': True, **original}):
                        info.read(*old_args)
                    self.assertIs(info.entries[CURRENT_LOGIN_ID]['usage'], newest)
                    self.assertEqual(newest['status'], 'ready' if completed else 'loading')

    def test_proxy_shaped_metadata_and_changed_identity_are_rejected(self):
        rpc, _ = self.login()
        for origin in ('https://proxy.test', None):
            rpc.auth['workspaceRouting']['backendOrigin'] = origin
            self.assertIsNone(Account.login_context(rpc))
        rpc.auth['workspaceRouting']['backendOrigin'] = 'https://chatgpt.com'
        rpc.auth['account']['email'] = 'other@example.test'
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc):
            with self.assertRaises(ValueError):
                self.manager.account({'id': CURRENT_LOGIN_ID, 'operation': 'read'})

    def test_reset_is_confirmed_identity_bound_and_idempotent_on_proxy_connection(self):
        rpc, context = self.login()
        value = {'id': CURRENT_LOGIN_ID, 'operation': 'consume', 'confirmed': True,
                 'requestId': str(uuid.uuid4()), 'accountKey': context['accountKey'], 'creditId': 'card-1'}
        with patch('bridge.features.accounts.account.Account.rpc', return_value=rpc):
            with self.assertRaises(ValueError):
                self.manager.account({**value, 'confirmed': False})
            self.manager.account(value)
            self.manager.account(value)
            rpc.auth['account']['email'] = 'changed@example.test'
            with self.assertRaises(ValueError):
                self.manager.account({**value, 'requestId': str(uuid.uuid4())})
        self.assertEqual(sum(method.endswith('/consume') for method, _ in rpc.calls), 1)
        self.assertEqual(self.manager.index['accounts'], [])

    def test_changed_login_cannot_display_previous_quota_or_pending_reset(self):
        _, context = self.login()
        self.manager.info.entries[CURRENT_LOGIN_ID] = {'usage': {'accountKey': context['accountKey'], 'limits': ['old']}}
        self.assertTrue(self.manager.public()['loginAccount']['details'])
        self.manager.info.identity['login']['context'] = {**context, 'accountKey': 'b' * 64}
        self.assertEqual(self.manager.public()['loginAccount']['details'], {})
        (self.home / 'auth.json').write_text('{}')
        self.assertIsNone(self.manager.public()['loginAccount'])


if __name__ == '__main__':
    unittest.main()
