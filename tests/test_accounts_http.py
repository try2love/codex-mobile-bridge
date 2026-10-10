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

    def test_switch_requires_auth_csrf_and_exact_payload(self):
        manager = self.manager()
        value = {'id': 'fixture', 'requestId': 'fixture', 'confirmed': True, 'tasksConfirmed': True}
        self.assertEqual(self.request('POST','/api/accounts/switch',value)[0],401)
        headers = self.login()
        self.assertEqual(self.request('POST','/api/accounts/switch',value,{'Cookie':headers['Cookie']})[0],403)
        self.assertEqual(self.request('POST','/api/accounts/switch',{**value,'apiKey':'forbidden'},headers)[0],400)
        self.assertEqual(self.request('POST','/api/accounts/switch',value,headers)[0],202)
        manager.switch.assert_called_once_with(value)

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
