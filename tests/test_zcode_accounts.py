import json
import os
import tempfile
import unittest
from pathlib import Path
from bridge.clients.zcode.accounts import ZCodeAccounts
from bridge.clients.zcode import api_config
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
    def native_api(self):
        return {'schemaVersion':1,'config':{'providerOrder':['other'],
            'providerConfigRules':{'providerRules':[{'providerId':'other','providerName':'Other',
                'config':{'group':'standard-personal','access':{'type':'api-key','apiKey':'other-key'},
                    'api':{'type':'openai-responses','baseUrl':'https://other.example.invalid/v1'},
                    'personalModelIds':['other-model'],'modelOrder':['other-model']}}]},
            'modelConfigRules':{'providerModelRules':[{'providerId':'other','modelId':'other-model','config':{'enabled':True}}],
                                'manualProviderModelRules':[]},
            'defaultModelSelection':{'providerId':'other','modelId':'other-model'}}}
    def api_draft(self, **extra):
        return {'name':'Saved API','baseUrl':'http://127.0.0.1:9999/v1','apiKey':'fixture-secret',
                'apiFormat':'openai-responses','models':['example-model','second-model'],**extra}
    def test_api_save_persists_without_touching_native_and_applies_only_own_provider(self):
        before=self.native_api();self.write(api_config.FILE,before)
        official=self.store.import_current()['activeId']
        credentials=(self.home/'credentials.json').read_bytes();settings=(self.home/'setting.json').read_bytes()
        value=self.store.save_api(self.api_draft());row=next(row for row in value['accounts'] if row['kind']=='api')
        self.assertNotIn(row['id'],value['activeIds']);self.assertIn(official,value['activeIds'])
        self.assertEqual(json.loads((self.home/api_config.FILE).read_text()),before)
        self.assertNotIn('fixture-secret',json.dumps(value));self.assertNotIn('fixture-secret',json.dumps(self.store.edit(row['id'])))
        self.assertEqual(self.store.edit(row['id'])['api']['apiFormat'],'openai-responses')
        reloaded=ZCodeAccounts(self.root/'saved',self.home,lambda:self.current)
        token=reloaded.restore(row['id']);actual=json.loads((self.home/api_config.FILE).read_text())
        self.assertEqual(actual['config']['providerConfigRules']['providerRules'][0],before['config']['providerConfigRules']['providerRules'][0])
        self.assertEqual(actual['config']['defaultModelSelection'],before['config']['defaultModelSelection'])
        applied=actual['config']['providerConfigRules']['providerRules'][-1]
        self.assertEqual(applied['config']['api'],{'type':'openai-responses','baseUrl':'http://127.0.0.1:9999/v1'})
        self.assertEqual(applied['config']['access'],{'type':'api-key','apiKey':'fixture-secret'})
        self.assertEqual(credentials,(self.home/'credentials.json').read_bytes());self.assertEqual(settings,(self.home/'setting.json').read_bytes())
        self.assertIn(row['id'],reloaded.public()['activeIds']);self.assertIn(official,reloaded.public()['activeIds'])
        self.assertFalse(next(r for r in reloaded.public()['accounts'] if r['id']==row['id'])['canReadQuota'])
        if os.name=='posix':
            for path in (self.home/api_config.FILE,self.root/'saved'/(row['id']+'.json')):
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
        reloaded.rollback(token);self.assertEqual(json.loads((self.home/api_config.FILE).read_text()),before)
    def test_api_edit_keeps_key_and_native_model_metadata_and_rejects_unknown_fields(self):
        self.write(api_config.FILE,self.native_api())
        identifier=self.store.save_api(self.api_draft())['accounts'][0]['id']
        self.store.commit(self.store.restore(identifier))
        native=json.loads((self.home/api_config.FILE).read_text());provider=native['config']['providerConfigRules']['providerRules'][-1]
        provider['config']['api']['headers']={'X-Test':'keep'}
        custom=next(r for r in native['config']['modelConfigRules']['providerModelRules'] if r['providerId']==provider['providerId'])
        custom['config']['properties']={'contextWindow':123456}
        self.write(api_config.FILE,native)
        self.store.save_api(self.api_draft(id=identifier,apiKey='',models=['example-model','third-model']))
        self.store.commit(self.store.restore(identifier))
        after=json.loads((self.home/api_config.FILE).read_text())
        self.assertEqual(after['config']['providerConfigRules']['providerRules'][-1]['config']['api']['headers'],{'X-Test':'keep'})
        self.assertEqual(after['config']['providerConfigRules']['providerRules'][-1]['config']['access']['apiKey'],'fixture-secret')
        self.assertIn(custom,after['config']['modelConfigRules']['providerModelRules'])
        self.assertFalse(any(r['modelId']=='second-model' for r in after['config']['modelConfigRules']['providerModelRules']))
        with self.assertRaisesRegex(ValueError,'不支持的字段'):self.store.save_api(self.api_draft(authScheme='ignored'))
    def test_api_invalid_input_unknown_native_format_and_drift_do_not_overwrite(self):
        for extra in ({'baseUrl':'http://example.invalid'}, {'baseUrl':'https://x.invalid/?secret=x'},
                      {'apiKey':'bad\nkey'}, {'apiFormat':'unknown'}, {'models':[]}, {'models':['bad model']}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.store.save_api(self.api_draft(**extra))
        identifier=self.store.save_api(self.api_draft())['accounts'][0]['id']
        self.write(api_config.FILE,{'schemaVersion':999,'config':{'keep':True}})
        before=(self.home/api_config.FILE).read_bytes()
        with self.assertRaisesRegex(ValueError,'尚未识别'):self.store.restore(identifier)
        self.assertEqual((self.home/api_config.FILE).read_bytes(),before)
        self.write(api_config.FILE,self.native_api());token=self.store.restore(identifier)
        self.write(api_config.FILE,{'external':True})
        with self.assertRaisesRegex(ValueError,'外部修改'):self.store.rollback(token)
        self.assertEqual(json.loads((self.home/api_config.FILE).read_text()),{'external':True})
    def test_api_save_cannot_overwrite_official_snapshot(self):
        identifier=self.store.import_current()['activeId']
        with self.assertRaisesRegex(ValueError,'官方登录'):self.store.save_api(self.api_draft(id=identifier))
    def test_notification_provider_and_unread_are_shared(self):
        inbox=MobileEvents(self.root/'events');self.addCleanup(inbox.close)
        row={'host':'desktop:zcode','id':'sess-test'};self.assertEqual(Notifications.desktop_provider(row['host']),'zcode')
        inbox.append(['once'],row,'ZCode','done',True)
        value=inbox.read();self.assertEqual(MobileEvents.provider(value['events'][0]),'zcode')
        inbox.acknowledge('zcode',value['streamId'],value['cursor']);self.assertEqual(inbox.read()['unread']['zcode'],0)
