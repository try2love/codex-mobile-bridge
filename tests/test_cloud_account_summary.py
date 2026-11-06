import json
import http.client
from pathlib import Path
from threading import BoundedSemaphore, RLock, Thread
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock

from relay.protocol import validate
from bridge.app.cloud import create_cloud_server
from bridge.features.accounts.cloud_summary import current_summary, SUMMARY_PATH
from bridge.features.network.cloud_connector import validate_request

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 'codex:' + 'a'*32


def account():
    manager = Mock(lock=RLock(), independent=False)
    manager.management.return_value = {'external': False}
    manager.info.current.return_value = {'status':'ready','kind':'chatgpt','id':None,'name':'logged-in'}
    manager.info.current_login.return_value = {
        'id':'current-login','transient':True,'kind':'chatgpt','name':'ChatGPT','email':'fixture@example.test',
        'planType':'pro','accountKey':'secret-key','owner':'secret-owner','details':{
            'usage': {'status':'ready','planType':'pro','updatedAt':123,'checkedAt':123,'accountKey':'secret-key',
                      'limits':[{'name':'one','windows':[{'remainingPercent':63,'resetsAt':999,'windowDurationMins':100,
                                                           'raw':'secret'}],'credits':{'balance':'12.50','jwt':'secret'}}],
                      'resetCredits':{'availableCount':2,'credits':[{'id':'secret-reset-id'}]},
                      'subscription':{'periodEndsAt':500,'source':'online','serverResponse':'secret'}},
            'models': {'status':'ready','cache':{'updatedAt':113,'cacheIdentity':'secret'},
                       'models':[{'id':'fixture-model','name':'Fixture','efforts':['low'],'key':'secret'}]}}}
    return manager


def envelope(path, method='GET', length=0, headers=None):
    return {'kind':'request','id':str(uuid.uuid4()),'path':path,'method':method,'content_type':'application/json',
            'headers':headers or {},'length':length}


class CloudAccountSummaryTests(unittest.TestCase):
    def test_projection_and_explicit_metadata_fetch_are_current_only(self):
        manager = account()
        result = current_summary(manager)
        login = result['loginAccount']
        self.assertEqual(login['id'],'cloud-current')
        self.assertEqual(login['email'],'fixture@example.test')
        usage=login['details']['usage']
        self.assertEqual(usage['resetCredits'],{'availableCount':2})
        self.assertEqual(usage['subscription'],{'periodEndsAt':500,'source':'online'})
        self.assertEqual(usage['limits'][0]['windows'][0]['remainingPercent'],63)
        self.assertEqual(login['details']['models']['models'],[{'id':'fixture-model','name':'Fixture','efforts':['low']}])
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('sshHosts',result)
        manager.public.assert_not_called()
        manager.info.request.assert_not_called()
        current_summary(manager,'models',True)
        manager.info.request.assert_called_once_with({'id':'current-login','section':'models','refresh':True},snapshot=False)

    def test_api_uses_only_verified_active_model_and_unknown_identity_does_not_read_saved_accounts(self):
        manager=account()
        manager.info.current.return_value={'status':'ready','kind':'api','id':'saved','name':'Local provider'}
        manager.row.return_value={'id':'saved','kind':'api','name':'Local provider','model':'current-model',
                                  'baseUrl':'https://private.example','apiKey':'secret'}
        manager.info.public_details.return_value={'models':{'status':'error','error':'secret'}}
        result=current_summary(manager)
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('private.example',json.dumps(result))
        self.assertEqual(result['accounts'][0]['model'],'current-model')
        self.assertIsNone(result['loginAccount'])
        with self.assertRaises(PermissionError): current_summary(manager,'usage')
        manager.info.current_login.assert_not_called()
        manager.row.reset_mock()
        manager.info.current.return_value={'status':'checking'}
        result=current_summary(manager)
        self.assertEqual(result['accounts'],[])
        manager.row.assert_not_called()

    def test_legacy_summary_schema_remains_specific_to_cloud(self):
        for path in (SUMMARY_PATH,SUMMARY_PATH+'?section=models&refresh=1',SUMMARY_PATH+'?entry=codex%3A'+'a'*32):
            validate_request(envelope(path))
            with self.assertRaises(PermissionError): validate('GET',path,0)
        for path in ('/api/accounts/details','/api/accounts/switch',
                     SUMMARY_PATH+'?section=models&id=other',SUMMARY_PATH+'?refresh=1',SUMMARY_PATH+'?entry=wsl%3A'+'a'*32,
                     SUMMARY_PATH+'?entry=local',SUMMARY_PATH+'?section=usage&section=models',SUMMARY_PATH+'#fragment',
                     '//elsewhere'+SUMMARY_PATH,'https://elsewhere'+SUMMARY_PATH,SUMMARY_PATH+'?section=unknown'):
            with self.subTest(path=path),self.assertRaises((PermissionError,ValueError)):
                validate_request(envelope(path))
        for packet in (envelope(SUMMARY_PATH,'POST'),envelope(SUMMARY_PATH,length=1),
                       envelope(SUMMARY_PATH,headers={'Range':'bytes=0-1'})):
            with self.assertRaises((PermissionError,ValueError)):validate_request(packet)
        validate('GET','/api/cli-access',0) # older shared transport keeps its own policy.

    def test_private_listener_requires_session_and_enabled_native_entry(self):
        manager=account()
        bridge=Mock()
        bridge.account_target.return_value=SimpleNamespace(accounts=manager)
        sessions=Mock()
        gateway=SimpleNamespace(bridge=bridge,desktop_sessions=sessions,web_dir=ROOT/'web',
                                slots=BoundedSemaphore(8),terminal_sockets=BoundedSemaphore(1))
        server=create_cloud_server(gateway)
        th=Thread(target=server.serve_forever,daemon=True);th.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        token,_=server.auth.new_session('127.0.0.1','Noise-approved mobile')
        def get(path,approved=True):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            try:
                conn.request('GET',path,headers={'Cookie':server.auth.cookie_name+'='+token} if approved else {})
                response=conn.getresponse()
                return response.status,json.loads(response.read())
            finally:conn.close()
        status,_=get(SUMMARY_PATH,False)
        self.assertEqual(status,401)
        status,value=get(SUMMARY_PATH+'?entry=codex%3A'+'a'*32)
        self.assertEqual(status,200);self.assertTrue(value['cloudReadOnly'])
        sessions.require_enabled.assert_called_once_with(ENTRY)
        bridge.account_target.assert_called_once_with(ENTRY)
        sessions.require_enabled.side_effect=PermissionError('disabled')
        self.assertEqual(get(SUMMARY_PATH)[0],403)
        self.assertEqual(bridge.account_target.call_count,1)
