import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import test_bridge
from bridge.app.service import Bridge


class WslAccountsHttpTests(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def manager(self):
        manager = SimpleNamespace(
            read=Mock(return_value={'accounts': [], 'capabilities': {'connect': True, 'save': True, 'import': True, 'capture': True, 'switch': True, 'restore': True, 'attemptSwitch': True, 'attemptRestore': True, 'recoverRestart': True, 'models': True}}),
            environments=Mock(return_value={'environments': []}),
            change=Mock(return_value={'accounts': []}))
        self.server.bridge.wsl_accounts = manager
        self.server.bridge.accounts = SimpleNamespace(public=Mock())
        self.server.bridge.cli_access = SimpleNamespace(change=Mock())
        return manager

    def test_reads_are_authenticated_and_explicitly_targeted(self):
        manager = self.manager()
        url = '/api/wsl-accounts?provider=codex&entry=wsl:' + 'a' * 32
        self.assertEqual(self.request('GET', url)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', url, headers=headers)[0], 200)
        manager.read.assert_called_once_with('codex', 'wsl:' + 'a' * 32)
        self.assertEqual(self.request('GET', '/api/wsl-accounts/environments?provider=claude', headers=headers)[0], 200)
        manager.environments.assert_called_once_with('claude')
        for query in ('', '?provider=codex', '?provider=codex&entry=',
                      '?provider=codex&entry=wsl:a&entry=wsl:b',
                      '?provider=codex&entry=wsl:a&host=local'):
            self.assertEqual(self.request('GET', '/api/wsl-accounts' + query, headers=headers)[0], 400)
        self.server.bridge.accounts.public.assert_not_called()

    def test_authenticated_web_can_manage_explicitly_selected_wsl_profiles(self):
        manager = self.manager()
        headers = self.login()
        base = {'provider': 'codex', 'entry': 'wsl:' + 'a' * 32}
        operations = ({'action': 'save-api', 'name': 'Fixture', 'baseUrl': 'https://fixture.invalid',
                       'model': 'fixture', 'apiKey': 'write-only-private'},
                      {'action': 'save-api', 'id': 'saved', 'name': 'Updated', 'baseUrl': 'https://fixture.invalid',
                       'model': 'fixture'},
                      {'action': 'import-api', 'sourceId': 'saved-local', 'sourceIdentity': 'source-digest'},
                      {'action': 'connect'}, {'action': 'import-sources'}, {'action': 'import-current'},
                      {'action': 'delete', 'id': 'saved'}, {'action': 'rename', 'id': 'saved', 'name': 'Renamed'})
        for operation in operations:
            value = {**base, **operation}
            with self.subTest(action=operation['action']):
                self.assertEqual(self.request('POST', '/api/wsl-accounts', value, headers)[0], 200)
                manager.change.assert_called_with(value, desktop=False)
        self.server.bridge.cli_access.change.assert_not_called()

    def test_management_rejects_invalid_fields_no_auth_no_csrf_and_passwordless_access(self):
        manager = self.manager()
        base = {'provider': 'codex', 'entry': 'wsl:' + 'a' * 32}
        value = {**base, 'action': 'save-api', 'name': 'Fixture', 'model': 'fixture',
                 'baseUrl': 'https://fixture.invalid', 'apiKey': 'write-only-private'}
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value, {'Cookie': headers['Cookie']})[0], 403)
        for invalid in ({**base, 'action': 'wslAccounts'}, {**value, 'desktop': True},
                        {**base, 'action': 'models', 'id': 'native', 'apiKey': 'never-read'},
                        {**base, 'action': 'import-api', 'sourceId': 'local-missing-identity'}):
            self.assertEqual(self.request('POST', '/api/wsl-accounts', invalid, headers)[0], 400)
        self.server.auth.config['mode'] = 'none'
        for operation in ({'action': 'save-api', 'name': 'Fixture', 'model': 'fixture', 'baseUrl': 'https://fixture.invalid'},
                          {'action': 'connect'}, {'action': 'import-sources'}, {'action': 'import-current'},
                          {'action': 'delete', 'id': 'saved'}, {'action': 'rename', 'id': 'saved', 'name': 'Fixture'}):
            self.assertEqual(self.request('POST', '/api/wsl-accounts', {**base, **operation}, headers)[0], 403)
        manager.change.assert_not_called()

    def test_codex_recovery_binds_only_applied_identity_and_keeps_write_security(self):
        manager = self.manager()
        value = {'provider': 'codex', 'entry': 'wsl:'+'a'*32,
                 'action': 'recover-restart', 'expectedIdentity': 'already-applied'}
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value, {'Cookie': headers['Cookie']})[0], 403)
        for invalid in ({**value, 'id': 'another-account'}, {**value, 'apiKey': 'never-forward'},
                        {**value, 'provider': 'claude', 'entry': 'wsl-claude:'+'a'*32}):
            self.assertEqual(self.request('POST', '/api/wsl-accounts', invalid, headers)[0], 400)
        self.server.auth.config['mode'] = 'none'
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value, headers)[0], 403)
        manager.change.assert_not_called()
        self.server.auth.config['mode'] = 'password'
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value, headers)[0], 200)
        manager.change.assert_called_once_with(value, desktop=False)

    def test_switch_requires_auth_csrf_and_password_protection(self):
        manager = self.manager()
        value = {'provider': 'claude', 'entry': 'wsl-claude:' + 'b' * 32,
                 'action': 'switch', 'id': 'native', 'expectedIdentity': 'current'}
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value, {'Cookie': headers['Cookie']})[0], 403)
        self.server.auth.config['mode'] = 'none'
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value, headers)[0], 403)
        manager.change.assert_not_called()
        status, _, result = self.request('GET', '/api/wsl-accounts?provider=claude&entry=' + value['entry'], headers=headers)
        self.assertEqual(status, 200)
        for capability in ('connect', 'save', 'import', 'capture', 'switch', 'restore', 'attemptSwitch', 'attemptRestore', 'recoverRestart'):
            self.assertFalse(result['capabilities'][capability])
        self.assertTrue(result['capabilities']['models'])
        self.server.auth.config['mode'] = 'password'
        self.assertEqual(self.request('POST', '/api/wsl-accounts', value, headers)[0], 200)
        manager.change.assert_called_once_with(value, desktop=False)


class WslDesktopControlTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.cli_access = SimpleNamespace(control=Mock(return_value={'local': True}))
        self.bridge.wsl_accounts = SimpleNamespace(change=Mock(), environments=Mock())

    def test_existing_cli_control_keeps_original_request_and_response(self):
        value = {'action': 'list'}
        self.assertEqual(self.bridge.cli_access_control(value), {'local': True})
        self.bridge.cli_access.control.assert_called_once_with(value)
        self.bridge.wsl_accounts.change.assert_not_called()

    def test_explicit_desktop_envelope_cannot_fall_back_to_local_accounts(self):
        entry = 'wsl:' + 'a' * 32
        self.bridge.cli_access_control({'action': 'wslAccounts', 'operation': 'list', 'provider': 'codex', 'entry': entry})
        self.bridge.wsl_accounts.change.assert_called_once_with({'action': 'read', 'provider': 'codex', 'entry': entry}, desktop=True)
        self.bridge.cli_access_control({'action': 'wslAccounts', 'operation': 'environments', 'provider': 'claude'})
        self.bridge.wsl_accounts.environments.assert_called_once_with('claude')
        with self.assertRaises(ValueError):
            self.bridge.cli_access_control({'action': 'wslAccounts', 'operation': 'environments', 'provider': 'codex', 'entry': entry})
        self.bridge.cli_access.control.assert_not_called()
