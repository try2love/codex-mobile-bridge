import json
import os
import tempfile
import unittest
from pathlib import Path
from bridge.clients.zcode.accounts import ZCodeAccounts
from bridge.features.notifications.events import MobileEvents
from bridge.features.notifications.channels import Notifications

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'home';self.home.mkdir()
        self.current={'identity':'a'*64,'name':'A','email':'a@example.invalid'}
        self.store=ZCodeAccounts(self.root/'saved',self.home,lambda:self.current)
        self.write('credentials.json',{'oauth:active_provider':'enc:v1:provider','oauth:test:access_token':'enc:v1:A','unrelated':'private-API','project-key-location:old':'derived'})
        self.write('setting.json',{'providerFamilyDomain':'a','locale':'zh','recentProjects':['project']})
    def write(self,name,value): (self.home/name).write_text(json.dumps(value))
    def test_two_accounts_restore_only_login_and_rollback_without_public_secrets(self):
        a=self.store.import_current()['activeId']
        self.current={'identity':'b'*64,'name':'B'}
        self.write('credentials.json',{'oauth:active_provider':'enc:v1:provider','oauth:test:access_token':'enc:v1:B','unrelated':'other-API','project-key-location:new':'derived'})
        self.write('setting.json',{'providerFamilyDomain':'b','locale':'en','recentProjects':['new']})
        b=self.store.import_current()['activeId'];self.assertNotEqual(a,b)
        public=json.dumps(self.store.public());self.assertNotIn('enc:v1:',public);self.assertNotIn('other-API',public)
        token=self.store.restore(a)
        credentials=json.loads((self.home/'credentials.json').read_text());self.assertEqual(credentials['oauth:test:access_token'],'enc:v1:A');self.assertEqual(credentials['unrelated'],'other-API');self.assertFalse(any(k.startswith('project-key-location:') for k in credentials))
        settings=json.loads((self.home/'setting.json').read_text());self.assertEqual(settings,{'providerFamilyDomain':'a','locale':'en','recentProjects':['new']})
        if os.name == 'posix':
            self.assertEqual((self.home/'credentials.json').stat().st_mode & 0o777,0o600)
        self.store.rollback(token);self.assertEqual(json.loads((self.home/'credentials.json').read_text())['oauth:test:access_token'],'enc:v1:B')
    def test_drift_and_symlink_fail_without_overwriting(self):
        a=self.store.import_current()['activeId'];token=self.store.restore(a)
        self.write('setting.json',{'external':True})
        with self.assertRaisesRegex(ValueError,'外部修改'):self.store.rollback(token)
        self.assertTrue((self.root/'saved'/('rollback-'+token+'.json')).exists())
        (self.home/'credentials.json').unlink();(self.home/'credentials.json').symlink_to(self.home/'setting.json')
        with self.assertRaisesRegex(ValueError,'符号链接'):self.store.snapshot()
    def test_import_requires_stable_native_identity(self):
        self.current={'name':'unknown'}
        with self.assertRaises(ValueError):self.store.import_current()
        self.assertFalse((self.root/'saved/index.json').exists())
    def test_notification_provider_and_unread_are_shared(self):
        inbox=MobileEvents(self.root/'events');self.addCleanup(inbox.close)
        row={'host':'desktop:zcode','id':'sess-test'};self.assertEqual(Notifications.desktop_provider(row['host']),'zcode')
        inbox.append(['once'],row,'ZCode','done',True)
        value=inbox.read();self.assertEqual(MobileEvents.provider(value['events'][0]),'zcode')
        inbox.acknowledge('zcode',value['streamId'],value['cursor']);self.assertEqual(inbox.read()['unread']['zcode'],0)
