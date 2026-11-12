import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.accounts import AntigravityAccounts
from bridge.clients.discovery import discover_clients
from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]


class AntigravityAccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

        self.storage_dir = self.root / 'antigravity-accounts'
        self.config_dir = self.root / 'config' / 'Antigravity'
        self.auth_dir = self.root / 'config' / 'antigravity'

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.auth_dir.mkdir(parents=True, exist_ok=True)

        self.current_metadata = None
        # Set up initial login for user A
        self.setup_account(
            scope='ub_scope_aaa',
            email='user_a@example.com',
            name='User A',
            access_token='ya29.tokenA',
            refresh_token='1//refreshA',
        )

        self.accounts = AntigravityAccounts(
            self.storage_dir,
            config_dir=self.config_dir,
            auth_dir=self.auth_dir,
            metadata=lambda: self.current_metadata,
        )

    def setup_account(self, scope, email, name, access_token, refresh_token):
        self.current_metadata = {
            'account': {
                'name': name,
                'email': email,
                'tier': 'Google AI Pro',
                'status': 'ready',
            },
            'quota': {'limits': [{'name': 'gemini-3.8-flash', 'windows': [{'remainingPercent': 95.0}]}]},
            'credits': {'prompt': 450, 'flow': 90},
        }

        # 1. auth_dir / credential-user-scope
        (self.auth_dir / 'credential-user-scope').write_text(scope, encoding='utf-8')

        # 2. auth_dir / plugin-secrets.json
        secrets = {
            f'{scope}/oauth2:client123@1': {
                'access_token': access_token,
                'token_type': 'Bearer',
                'refresh_token': refresh_token,
                'expires_in': 3600,
            },
            f'{scope}/oauth2:client123@1_IDENTITY': email,
            f'{scope}/oauth2:client123_ACCOUNTS': '["1"]',
        }
        (self.auth_dir / 'plugin-secrets.json').write_text(json.dumps(secrets), encoding='utf-8')

        # 3. config_dir / app_storage.json
        storage = {
            'jetski.onboarding.lastLoginUsername': email,
            'antigravity-user-name': name,
        }
        (self.config_dir / 'app_storage.json').write_text(json.dumps(storage), encoding='utf-8')

        # 4. config_dir / Preferences & Cookies
        (self.config_dir / 'Preferences').write_text(json.dumps({'user': email}), encoding='utf-8')
        (self.config_dir / 'Cookies').write_bytes(f'cookies_data_{email}'.encode('utf-8'))

        # 5. config_dir / Local Storage
        ldb_dir = self.config_dir / 'Local Storage' / 'leveldb'
        ldb_dir.mkdir(parents=True, exist_ok=True)
        (ldb_dir / '000001.log').write_text(f'local_storage_log_{email}', encoding='utf-8')

    def test_public_reports_unsaved_current_login(self):
        pub = self.accounts.public()
        self.assertEqual(len(pub['accounts']), 1)
        current = pub['accounts'][0]
        self.assertEqual(current['id'], 'current')
        self.assertEqual(current['email'], 'user_a@example.com')
        self.assertFalse(current['saved'])
        self.assertEqual(pub['activeId'], 'current')

    def test_import_current_saves_snapshot_and_marks_saved(self):
        res = self.accounts.import_current('主要開發帳號')
        self.assertEqual(len(res['accounts']), 1)
        row = res['accounts'][0]
        self.assertNotEqual(row['id'], 'current')
        self.assertEqual(row['name'], '主要開發帳號')
        self.assertEqual(row['email'], 'user_a@example.com')
        self.assertTrue(row['saved'])
        self.assertEqual(res['activeId'], row['id'])

        # Verify files were actually copied into the profile snapshot
        profile_dir = self.storage_dir / 'profiles' / row['id']
        self.assertTrue((profile_dir / 'manifest.json').is_file())
        self.assertTrue((profile_dir / 'auth' / 'credential-user-scope').is_file())
        self.assertTrue((profile_dir / 'auth' / 'plugin-secrets.json').is_file())
        self.assertTrue((profile_dir / 'config' / 'app_storage.json').is_file())
        self.assertTrue((profile_dir / 'config' / 'Cookies').is_file())
        self.assertTrue((profile_dir / 'config' / 'Local Storage' / 'leveldb' / '000001.log').is_file())

    def test_rename_and_details(self):
        imported = self.accounts.import_current('帳號 A')
        acc_id = imported['accounts'][0]['id']

        renamed = self.accounts.rename(acc_id, '主力測試帳號')
        self.assertEqual(renamed['accounts'][0]['name'], '主力測試帳號')

        det = self.accounts.details(acc_id)
        self.assertEqual(det['details']['name'], '主力測試帳號')
        self.assertEqual(det['details']['email'], 'user_a@example.com')
        self.assertIn('quota', det['details'])
        self.assertEqual(det['details']['quota']['limits'][0]['name'], 'gemini-3.8-flash')

    def test_multi_account_switch_and_rollback(self):
        # 1. Save Account A
        res_a = self.accounts.import_current('帳號 A')
        id_a = res_a['accounts'][0]['id']

        # 2. Simulate User logging in with Account B on desktop
        self.setup_account(
            scope='ub_scope_bbb',
            email='user_b@example.com',
            name='User B',
            access_token='ya29.tokenB',
            refresh_token='1//refreshB',
        )

        # 3. Save Account B
        res_b = self.accounts.import_current('帳號 B')
        self.assertEqual(len(res_b['accounts']), 2)
        id_b = next(r['id'] for r in res_b['accounts'] if r['email'] == 'user_b@example.com')
        self.assertEqual(res_b['activeId'], id_b)

        # 4. Switch back to Account A
        token = self.accounts.restore(id_a)
        self.assertTrue(bool(token))

        # Verify on-disk files now reflect Account A
        self.assertEqual(
            (self.auth_dir / 'credential-user-scope').read_text(encoding='utf-8').strip(),
            'ub_scope_aaa',
        )
        self.assertIn(
            'user_a@example.com',
            (self.config_dir / 'app_storage.json').read_text(encoding='utf-8'),
        )
        self.assertEqual(
            (self.config_dir / 'Cookies').read_bytes(),
            b'cookies_data_user_a@example.com',
        )

        # Commit transaction
        self.accounts.commit(token)
        self.assertFalse((self.storage_dir / 'transactions' / token).exists())

        # Update metadata to reflect Account A after switch
        self.current_metadata['account']['email'] = 'user_a@example.com'
        pub = self.accounts.public()
        self.assertEqual(pub['activeId'], id_a)

    def test_keyring_snapshot_and_restore_cycle(self):
        mock_keyring_store = {
            'current': json.dumps({
                'token': {'access_token': 'ya29.secretA', 'refresh_token': '1//refA'},
                'id_token_email': 'user_a@example.com',
                'auth_method': 'consumer',
            })
        }

        def fake_get():
            return mock_keyring_store['current']

        def fake_set(val):
            mock_keyring_store['current'] = val
            return True

        with mock.patch('bridge.clients.antigravity.accounts.get_keyring_secret', side_effect=fake_get), \
             mock.patch('bridge.clients.antigravity.accounts.set_keyring_secret', side_effect=fake_set):

            accounts_keyring = AntigravityAccounts(
                self.storage_dir,
                config_dir=self.config_dir,
                auth_dir=self.auth_dir,
                metadata=lambda: self.current_metadata,
                keyring_enabled=True,
            )

            # 1. Save user A
            res_a = accounts_keyring.import_current('帳號 A')
            id_a = res_a['accounts'][0]['id']
            profile_a = self.storage_dir / 'profiles' / id_a
            self.assertTrue((profile_a / 'keyring' / 'secret.json').is_file())
            self.assertIn('user_a@example.com', (profile_a / 'keyring' / 'secret.json').read_text())

            # 2. Simulate User B login
            self.setup_account('ub_scope_bbb', 'user_b@example.com', 'User B', 'ya29.tokB', '1//refB')
            mock_keyring_store['current'] = json.dumps({
                'token': {'access_token': 'ya29.secretB', 'refresh_token': '1//refB'},
                'id_token_email': 'user_b@example.com',
                'auth_method': 'consumer',
            })
            res_b = accounts_keyring.import_current('帳號 B')
            id_b = next(r['id'] for r in res_b['accounts'] if r['email'] == 'user_b@example.com')

            # 3. Switch back to User A
            token = accounts_keyring.restore(id_a)
            accounts_keyring.commit(token)

            # Keyring must have been restored to User A
            self.assertIn('user_a@example.com', mock_keyring_store['current'])
            self.assertIn('ya29.secretA', mock_keyring_store['current'])

            # 4. Switch back to User B
            token = accounts_keyring.restore(id_b)
            accounts_keyring.commit(token)

            # Keyring must have been restored to User B
            self.assertIn('user_b@example.com', mock_keyring_store['current'])
            self.assertIn('ya29.secretB', mock_keyring_store['current'])

    def test_remove_account(self):
        res = self.accounts.import_current('帳號 A')
        acc_id = res['accounts'][0]['id']
        self.assertTrue((self.storage_dir / 'profiles' / acc_id).is_dir())

        removed = self.accounts.remove(acc_id)
        self.assertEqual(len(removed['accounts']), 1)  # reverts to unsaved 'current'
        self.assertEqual(removed['accounts'][0]['id'], 'current')
        self.assertFalse((self.storage_dir / 'profiles' / acc_id).exists())


class AntigravityManagerAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.fake_home = self.root / 'home'
        (self.fake_home / '.gemini' / 'antigravity').mkdir(parents=True, exist_ok=True)
        (self.fake_home / '.config' / 'Antigravity').mkdir(parents=True, exist_ok=True)
        (self.fake_home / '.config' / 'antigravity').mkdir(parents=True, exist_ok=True)

        # Setup mock login files in fake home
        scope = 'ub_scope_manager'
        email = 'manager_test@example.com'
        (self.fake_home / '.config' / 'antigravity' / 'credential-user-scope').write_text(scope)
        (self.fake_home / '.config' / 'antigravity' / 'plugin-secrets.json').write_text(
            json.dumps({f'{scope}/oauth2:c1@1_IDENTITY': email})
        )
        (self.fake_home / '.config' / 'Antigravity' / 'app_storage.json').write_text(
            json.dumps({'jetski.onboarding.lastLoginUsername': email})
        )

    def test_manager_client_accounts_flow(self):
        discovered = discover_clients(home=self.fake_home)
        self.assertIn('antigravity', discovered)

        data_dir = self.root / 'data'
        mock_account = {
            'account': {
                'name': 'Manager User',
                'email': 'manager_test@example.com',
                'tier': 'Google AI Pro',
                'status': 'ready',
            },
            'quota': {'limits': []},
            'credits': {'prompt': 500, 'flow': 100},
        }

        with mock.patch('bridge.clients.discovery.discover_clients', return_value=discovered):
            manager = DesktopSessions(data_dir)
            try:
                manager.scan()

                # Mock call('account') to return isolated test account metadata
                orig_call = manager.adapters['antigravity'].call

                def custom_call(action, *args, **kwargs):
                    if action == 'account':
                        return mock_account
                    return orig_call(action, *args, **kwargs)

                manager.adapters['antigravity'].call = custom_call
                clients = manager.clients()['clients']
                ag_client = next(c for c in clients if c['id'] == 'antigravity')
                self.assertTrue(ag_client['capabilities']['accounts'])

                # 1. List accounts
                listing = manager.client_accounts('antigravity', {'operation': 'list'})
                self.assertEqual(listing['provider'], 'antigravity')
                self.assertTrue(listing['canSwitch'])
                self.assertEqual(listing['accounts'][0]['id'], 'current')
                self.assertEqual(listing['accounts'][0]['email'], 'manager_test@example.com')

                # 2. Import current account
                imported = manager.client_accounts('antigravity', {'operation': 'import-current', 'name': '經理帳號'})
                self.assertTrue(imported['accounts'][0]['saved'])
                self.assertEqual(imported['accounts'][0]['name'], '經理帳號')
                saved_id = imported['accounts'][0]['id']

                # 3. Rename account
                renamed = manager.client_accounts(
                    'antigravity',
                    {'operation': 'rename', 'id': saved_id, 'name': '改名後帳號'},
                )
                self.assertEqual(renamed['accounts'][0]['name'], '改名後帳號')

                # 4. Details
                det = manager.client_accounts('antigravity', {'operation': 'details', 'id': saved_id})
                self.assertEqual(det['details']['name'], '改名後帳號')

                # 5. Remove account
                removed = manager.client_accounts('antigravity', {'operation': 'remove', 'id': saved_id})
                self.assertEqual(removed['accounts'][0]['id'], 'current')
            finally:
                manager.close()

    def test_switch_blocked_when_task_active(self):
        data_dir = self.root / 'sessions_busy'
        data_dir.mkdir(parents=True, exist_ok=True)
        discovered = discover_clients(home=self.fake_home)
        with mock.patch('bridge.clients.discovery.discover_clients', return_value=discovered):
            manager = DesktopSessions(data_dir)
            try:
                manager.scan()
                manager.gateway_running = True
                manager.adapters['antigravity'].call = mock.Mock(return_value={
                    'connected': True,
                    'complete': True,
                    'sessions': [{'nativeId': 's1', 'name': 'Busy Session', 'status': 'active', 'requests': []}]
                })
                with mock.patch('bridge.clients.lifecycle.inspect_client', return_value={'running': True, 'pids': [123], 'mainPids': [123], 'runtimePids': [], 'unknown': False}):
                    with self.assertRaises(ValueError) as ctx:
                        manager.client_accounts('antigravity', {'operation': 'switch', 'id': 'fake_id'})
                    self.assertIn('正在執行任務', str(ctx.exception))
            finally:
                manager.close()

    def test_switch_blocked_when_requests_pending(self):
        data_dir = self.root / 'sessions_req'
        data_dir.mkdir(parents=True, exist_ok=True)
        discovered = discover_clients(home=self.fake_home)
        with mock.patch('bridge.clients.discovery.discover_clients', return_value=discovered):
            manager = DesktopSessions(data_dir)
            try:
                manager.scan()
                manager.gateway_running = True
                manager.adapters['antigravity'].call = mock.Mock(return_value={
                    'connected': True,
                    'complete': True,
                    'sessions': [{'nativeId': 's1', 'name': 'Approval Session', 'status': 'idle', 'requests': [{'id': 'req1'}]}]
                })
                with mock.patch('bridge.clients.lifecycle.inspect_client', return_value={'running': True, 'pids': [123], 'mainPids': [123], 'runtimePids': [], 'unknown': False}):
                    with self.assertRaises(ValueError) as ctx:
                        manager.client_accounts('antigravity', {'operation': 'switch', 'id': 'fake_id'})
                    self.assertIn('正在等待確認操作', str(ctx.exception))
            finally:
                manager.close()

    def test_switch_success_when_idle(self):
        data_dir = self.root / 'sessions_idle'
        data_dir.mkdir(parents=True, exist_ok=True)
        discovered = discover_clients(home=self.fake_home)
        with mock.patch('bridge.clients.discovery.discover_clients', return_value=discovered):
            manager = DesktopSessions(data_dir)
            try:
                manager.scan()
                manager.gateway_running = True
                # First import current account
                imported = manager.client_accounts('antigravity', {'operation': 'import-current', 'name': '帳號一'})
                saved_id = imported['accounts'][0]['id']

                # Mock idle sessions
                manager.adapters['antigravity'].call = mock.Mock(return_value={
                    'connected': True,
                    'complete': True,
                    'sessions': [{'nativeId': 's1', 'name': 'Idle Session', 'status': 'idle', 'requests': []}]
                })
                manager.adapters['antigravity'].reconnect = mock.Mock()

                calls = []
                running_state = [True]
                def fake_inspect(desc):
                    return {'running': running_state[0], 'pids': [123] if running_state[0] else [], 'mainPids': [123] if running_state[0] else [], 'runtimePids': [], 'unknown': False}
                def fake_stop(desc, **kwargs):
                    calls.append('stop')
                    running_state[0] = False
                def fake_launch(desc, **kwargs):
                    calls.append('launch')
                    running_state[0] = True
                    return {'running': True, 'launched': True}

                with mock.patch('bridge.clients.lifecycle.inspect_client', side_effect=fake_inspect), \
                     mock.patch('bridge.clients.lifecycle.stop_client', side_effect=fake_stop), \
                     mock.patch('bridge.clients.lifecycle.launch_client', side_effect=fake_launch):
                    result = manager.client_accounts('antigravity', {'operation': 'switch', 'id': saved_id})
                    self.assertEqual(result['activeId'], saved_id)
                    self.assertEqual(calls, ['stop', 'launch'])
                    self.assertTrue(manager.adapters['antigravity'].reconnect.called)
            finally:
                manager.close()


if __name__ == '__main__':
    unittest.main()
