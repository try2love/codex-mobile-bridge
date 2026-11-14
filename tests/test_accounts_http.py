import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import test_bridge


class AccountsHttpTests(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def manager(self):
        manager = SimpleNamespace(public=Mock(return_value={'accounts': [], 'activeId': None, 'switch': {'phase': 'idle'}}),
                                  switch=Mock(return_value={'accounts': [], 'switch': {'phase': 'preparing'}}))
        manager.info = SimpleNamespace(request=Mock(return_value={'accounts': [], 'switch': {'phase': 'idle'}}))
        self.server.bridge.accounts = manager
        return manager

    def test_reset_dynamic_setting_and_requests_require_auth_and_enabled_feature(self):
        manager = self.manager()
        manager.reset_dynamic_enabled = False
        manager.reset_forecast = SimpleNamespace(request=Mock())
        manager.set_reset_dynamic = Mock()
        path = '/api/accounts/reset-dynamic'
        self.assertEqual(self.request('POST', path, {'enabled': True})[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, {'enabled': True}, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, {'enabled': True, 'extra': True}, headers)[0], 400)
        self.assertEqual(self.request('POST', path, {'enabled': True}, headers)[0], 200)
        manager.set_reset_dynamic.assert_called_once_with(True)
        self.assertEqual(self.request('GET', '/api/accounts?resetForecast=1', headers=headers)[0], 200)
        manager.reset_forecast.request.assert_not_called()
        manager.reset_dynamic_enabled = True
        self.assertEqual(self.request('GET', '/api/accounts?resetForecast=1', headers=headers)[0], 200)
        manager.reset_forecast.request.assert_called_once_with(refresh=False)

    def test_switch_requires_auth_csrf_and_exact_payload(self):
        manager = self.manager()
        value = {'id': 'fixture', 'requestId': 'fixture', 'confirmed': True, 'tasksConfirmed': True}
        self.assertEqual(self.request('POST','/api/accounts/switch',value)[0],401)
        headers = self.login()
        self.assertEqual(self.request('POST','/api/accounts/switch',value,{'Cookie':headers['Cookie']})[0],403)
        self.assertEqual(self.request('POST','/api/accounts/switch',{**value,'apiKey':'forbidden'},headers)[0],400)
        self.assertEqual(self.request('POST','/api/accounts/switch',value,headers)[0],202)
        manager.switch.assert_called_once_with(value)

    def test_ssh_retry_requires_auth_csrf_and_registered_host(self):
        self.manager()
        self.server.bridge.retry_ssh = Mock()
        path = '/api/accounts/ssh-retry'
        value = {'host': 'remote:fixture'}
        self.assertEqual(self.request('POST', path, value)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, value, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, {**value, 'alias': 'arbitrary'}, headers)[0], 400)
        self.assertEqual(self.request('POST', path, value, headers)[0], 202)
        self.server.bridge.retry_ssh.assert_called_once_with('remote:fixture')
        self.server.bridge.retry_ssh.side_effect = ValueError('请选择已登记的 SSH 主机')
        self.assertEqual(self.request('POST', path, {'host': 'unknown'}, headers)[0], 400)

    def test_runtime_initialization_requires_authenticated_default_desktop_scope(self):
        manager = self.manager(); manager.control = Mock(return_value={'switch': {'phase': 'preparing'}})
        path = '/api/accounts/desktop-runtime'
        value = {'requestId': 'fixture', 'confirmed': True, 'tasksConfirmed': True}
        self.assertEqual(self.request('POST', path, value)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, value, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, {**value, 'home': '/different'}, headers)[0], 400)
        self.assertEqual(self.request('POST', path, value, headers)[0], 202)
        manager.control.assert_called_once_with({'action': 'initializeDesktopRuntime', **value})
        manager.control.reset_mock()
        self.server.bridge.account_target = Mock(return_value=SimpleNamespace(accounts=manager))
        self.assertEqual(self.request('POST', path + '?entry=codex:fixture', value, headers)[0], 400)
        self.server.auth.config['mode'] = 'none'
        self.assertEqual(self.request('POST', path, value, headers)[0], 403)
        manager.control.assert_not_called()

    def test_runtime_initialization_forwards_explicit_unavailable_acknowledgement(self):
        manager = self.manager(); manager.control = Mock(return_value={'switch': {'phase': 'preparing'}})
        path = '/api/accounts/desktop-runtime'
        value = {'requestId': 'fixture', 'confirmed': True, 'tasksConfirmed': True,
                 'acknowledgeUnavailable': True}
        self.assertEqual(self.request('POST', path, value)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, value, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, value, headers)[0], 202)
        manager.control.assert_called_once_with({'action': 'initializeDesktopRuntime', **value})
        manager.control.reset_mock()
        for invalid in (False, 'true', 1, None):
            self.assertEqual(self.request('POST', path, {**value, 'acknowledgeUnavailable': invalid}, headers)[0], 400)
        self.assertEqual(self.request('POST', path, {**value, 'home': '/different'}, headers)[0], 400)
        self.server.bridge.account_target = Mock(return_value=SimpleNamespace(accounts=manager))
        self.assertEqual(self.request('POST', path + '?entry=codex:fixture', value, headers)[0], 400)
        self.server.auth.config['mode'] = 'none'
        self.assertEqual(self.request('POST', path, value, headers)[0], 403)
        manager.control.assert_not_called()

    def test_enrollment_and_mutation_routes_do_not_exist_on_web(self):
        manager = self.manager(); headers = self.login()
        for path in ['/api/accounts/add','/api/accounts/login','/api/accounts/delete','/api/accounts/configure','/api/accounts/recover','/api/accounts/scan','/api/accounts/import','/api/accounts/models']:
            self.assertEqual(self.request('POST',path,{'action':'addApi','apiKey':'secret'},headers)[0],404)
        manager.switch.assert_not_called()
        self.assertEqual(self.request('GET','/api/accounts',headers=headers)[0],200)

    def test_passwordless_switch_is_denied_even_with_valid_session(self):
        manager = self.manager(); headers = self.login()
        self.server.auth.config['mode']='none'
        self.assertEqual(self.request('POST','/api/accounts/switch',{'id':'fixture'},headers)[0],403)
        manager.switch.assert_not_called()

    def test_details_are_authenticated_and_accept_only_saved_account_queries(self):
        manager=self.manager();value={'id':'fixture','section':'models'}
        self.assertEqual(self.request('POST','/api/accounts/details',value)[0],401)
        headers=self.login()
        self.assertEqual(self.request('POST','/api/accounts/details',value,{'Cookie':headers['Cookie']})[0],403)
        self.assertEqual(self.request('POST','/api/accounts/details',{**value,'apiKey':'forbidden'},headers)[0],400)
        self.assertEqual(self.request('POST','/api/accounts/details',value,headers)[0],202)
        manager.info.request.assert_called_once_with(value)

    def test_reminders_and_desktop_updates_require_authenticated_csrf_requests(self):
        manager=self.manager();manager.monitor=SimpleNamespace(configure=Mock());manager.updates=SimpleNamespace(check=Mock(),request=Mock())
        paths=[('/api/accounts/reminders',{'preferences':{'lowQuota':True}}),
               ('/api/accounts/desktop-update',{'action':'checkDesktopUpdate'})]
        for path,value in paths:self.assertEqual(self.request('POST',path,value)[0],401)
        headers=self.login()
        for path,value in paths:
            self.assertEqual(self.request('POST',path,value,{'Cookie':headers['Cookie']})[0],403)
            self.assertIn(self.request('POST',path,value,headers)[0],(200,202))
        manager.monitor.configure.assert_called_once_with({'lowQuota':True});manager.updates.check.assert_called_once()
        self.server.auth.config['mode']='none'
        self.assertEqual(self.request('POST','/api/accounts/desktop-update',{'action':'checkDesktopUpdate'},headers)[0],403)
        manager.updates.request.assert_not_called()

    def test_independent_scope_routes_reads_and_writes_without_default_fallback(self):
        default=self.manager();headers=self.login();entry='codex:'+'a'*32
        scoped=SimpleNamespace(public=Mock(return_value={'accounts':[],'scope':'profile','canSwitch':True}),
            switch=Mock(return_value={'scope':'profile'}),reset_dynamic_enabled=False)
        target=SimpleNamespace(host=entry,accounts=scoped)
        self.server.bridge.account_target=Mock(return_value=target)
        value={'id':'fixture','requestId':'fixture','confirmed':True,'tasksConfirmed':True}
        self.assertEqual(self.request('GET','/api/accounts?entry='+entry,headers=headers)[0],200)
        self.assertEqual(self.request('POST','/api/accounts/switch?entry='+entry,value,headers)[0],202)
        scoped.switch.assert_called_once_with(value);default.switch.assert_not_called();default.public.assert_not_called()
        self.assertEqual(self.request('POST','/api/accounts/desktop-update?entry='+entry,{'action':'checkDesktopUpdate'},headers)[0],400)
        self.server.bridge.account_target.side_effect=ValueError('Unknown profile')
        self.assertEqual(self.request('POST','/api/accounts/switch?entry='+entry,value,headers)[0],400)
        self.assertEqual(scoped.switch.call_count,1);default.switch.assert_not_called()
