import json
import unittest
import uuid
from unittest.mock import patch

import test_accounts
from test_account import FakeRPC
from bridge.features.accounts.accounts import token_owner

class SavedResetTests(unittest.TestCase):
    setUp = test_accounts.AccountsTests.setUp
    tearDown = test_accounts.AccountsTests.tearDown

    def setup_saved(self):
        import base64
        identifier = 'a'*32
        folder = self.manager.directory(identifier);folder.mkdir()
        claims = base64.urlsafe_b64encode(json.dumps({'sub':'saved-owner','email':'test@example.test'}).encode()).decode().rstrip('=')
        auth = {'tokens':{'id_token':'e30.'+claims+'.sig','account_id':'saved-id'}}
        (folder/'auth.json').write_text(json.dumps(auth))
        self.manager.index['accounts'] = [{'id':identifier,'kind':'chatgpt','name':'Saved','email':'test@example.test','tokenOwner':token_owner(auth)}]
        self.manager.info.read_identity = lambda:{'kind':'api','key':'current','baseUrl':'https://example.test'}
        return identifier,folder

    def test_saved_reset_never_switches_or_changes_active_credentials(self):
        identifier,folder=self.setup_saved();before={n:(self.home/n).read_bytes() for n in ['auth.json','config.toml']}
        rpc=FakeRPC();native=FakeRPC()
        with patch('bridge.features.accounts.accounts.ManagedRPC',return_value=native),patch('bridge.features.accounts.account.Account.rpc',return_value=rpc):
            first=self.manager.control({'action':'account','id':identifier,'operation':'read'})
            attempt={'action':'account','operation':'consume','id':identifier,'accountKey':first['accountKey'],'creditId':'card-1','requestId':str(uuid.uuid4()),'confirmed':True}
            result=self.manager.control(attempt)
            self.assertEqual(result['outcome'],'reset')
            self.manager.control(attempt)
        self.assertEqual(len([c for c in rpc.calls if c[0].endswith('/consume')]),1)
        self.assertEqual(native.calls, [])
        self.assertEqual(before,{n:(self.home/n).read_bytes() for n in before})
        self.assertIsNone(self.manager.index['activeId']);self.assertEqual(self.manager.state['phase'],'idle')

    def test_identity_expiry_and_permission_are_checked_for_saved_account(self):
        identifier,folder=self.setup_saved();rpc=FakeRPC();native=FakeRPC()
        with patch('bridge.features.accounts.accounts.ManagedRPC',return_value=native),patch('bridge.features.accounts.account.Account.rpc',return_value=rpc):
            first=self.manager.account({'id':identifier})
            attempt={'operation':'consume','id':identifier,'accountKey':first['accountKey'],'creditId':'card-1','requestId':str(uuid.uuid4()),'confirmed':True}
            rpc.limits['rateLimitResetCredits']['credits'][0]['expiresAt']=1
            with self.assertRaises(ValueError):self.manager.account(attempt)
            rpc.auth['account']['email']='another@test'
            with self.assertRaises(ValueError):self.manager.account(attempt)
        self.assertFalse(any(m.endswith('/consume') for m,_ in rpc.calls))

    def test_saved_reset_ignores_desktop_agent_permission_without_changing_it(self):
        identifier,folder=self.setup_saved();rpc=FakeRPC();native=FakeRPC()
        native.config['desktop']['agent-usage-reset-enabled']=False
        rpc.config['desktop']['agent-usage-reset-enabled']=False
        with patch('bridge.features.accounts.accounts.ManagedRPC',return_value=native),patch('bridge.features.accounts.account.Account.rpc',return_value=rpc):
            first=self.manager.account({'id':identifier})
            self.assertTrue(first['canReset'])
            self.assertNotIn('agent-usage-reset-enabled', (folder/'config.toml').read_text())
            result=self.manager.account({'operation':'consume','id':identifier,'accountKey':first['accountKey'],
                                      'creditId':'card-1','requestId':str(uuid.uuid4()),'confirmed':True})
            self.assertEqual(result['outcome'], 'reset')
        self.assertEqual(native.calls, [])

    def test_api_missing_account_and_switch_in_progress_cannot_reset(self):
        identifier,_=self.setup_saved()
        with self.assertRaises(ValueError):self.manager.account({'id':'b'*32})
        self.manager.index['accounts'][0]['kind']='api'
        with self.assertRaises(ValueError):self.manager.account({'id':identifier})
        self.manager.state['phase']='applying'
        with self.assertRaises(ValueError):self.manager.account({'id':identifier})
