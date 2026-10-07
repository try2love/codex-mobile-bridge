import base64
import json
import time
import unittest
from unittest.mock import Mock, patch

import test_accounts
from bridge.accounts import private_json


class InfoTests(unittest.TestCase):
    setUp = test_accounts.AccountsTests.setUp
    tearDown = test_accounts.AccountsTests.tearDown

    def official(self):
        claims=base64.urlsafe_b64encode(b'{"sub":"fixture-user","email":"fixture@example.test"}').decode().rstrip('=')
        row={'id':'a'*32,'kind':'chatgpt','name':'Fixture','email':'fixture@example.test','tokenOwner':['org','fixture-user','fixture@example.test']}
        self.manager.index['accounts'].append(row)
        auth={'auth_mode':'chatgpt','tokens':{'account_id':'org','id_token':'e30.'+claims+'.sig','access_token':'access-fixture','refresh_token':'refresh-fixture'}}
        private_json(self.manager.directory(row['id'])/'auth.json',auth)
        return row,auth

    def test_current_detects_imported_api_without_previous_switch_and_tracks_external_changes(self):
        row=self.manager.add_api({'name':'API','baseUrl':'https://fixture.test/v1','apiKey':'private-key','model':'fixture'})['accounts'][0]
        info=self.manager.info
        info.identity={'kind':'api','baseUrl':row['baseUrl'],'key':'private-key','model':'fixture','name':'API · custom'}
        info.stamp=info.source_stamp();info.checked_at=time.time()
        result=self.manager.public()
        self.assertEqual(result['activeId'],row['id']);self.assertEqual(result['current']['name'],'API')
        self.assertNotIn('private-key',json.dumps(result));self.assertIsNone(self.manager.index['activeId'])
        (self.home/'config.toml').write_text('# externally changed')
        self.assertIsNone(self.manager.public()['activeId'])

    def test_current_official_is_matched_by_identity_not_just_email(self):
        row,_=self.official();info=self.manager.info
        info.stamp=info.source_stamp();info.checked_at=time.time()
        info.identity={'kind':'chatgpt','owner':['other-org','fixture-user',row['email']],'name':row['email']}
        self.assertIsNone(self.manager.public()['activeId'])
        info.identity['owner']=row['tokenOwner']
        self.assertEqual(self.manager.public()['activeId'],row['id'])
        with self.assertRaises(ValueError):self.manager.control({'action':'delete','id':row['id']})

    def rpc(self,rate_error=False):
        def request(method,params=None):
            if method=='config/read':return {'config':{}}
            if method=='account/read':return {'requiresOpenaiAuth':True,'account':{'type':'chatgpt','email':'fixture@example.test'}}
            if method=='account/rateLimits/read':
                if rate_error:raise ValueError('raw private upstream body')
                return {'rateLimits':{'primary':{'usedPercent':25,'windowDurationMins':300}},'rateLimitResetCredits':{'availableCount':2}}
            if method=='model/list':return {'data':[{'id':'fixture-model','displayName':'Fixture model'},{'id':'hidden','hidden':True}],'nextCursor':None}
            raise AssertionError(method)
        result=Mock();result.request.side_effect=request;return result

    def test_inactive_official_usage_models_are_isolated_and_sanitized(self):
        row,_=self.official();before=self.manager.snapshot_files();info=self.manager.info
        info.read_identity=Mock(return_value={'kind':'api','name':'other','key':'unrelated','baseUrl':'https://other.test'})
        with patch('bridge.accounts.ManagedRPC') as rpc:
            rpc.return_value.__enter__.return_value=self.rpc()
            info.read(row,'usage');info.read(row,'models')
        result=self.manager.public();data=result['accounts'][0]['details']
        self.assertEqual(data['usage']['limits'][0]['windows'][0]['remainingPercent'],75)
        self.assertEqual(data['usage']['resetCredits']['availableCount'],2)
        self.assertEqual(data['models']['models'],[{'id':'fixture-model','name':'Fixture model'}])
        self.assertEqual(self.manager.snapshot_files(),before)
        self.assertNotIn('access-fixture',json.dumps(result));self.assertNotIn('refresh-fixture',json.dumps(result))

    def test_active_account_uses_live_credentials_and_syncs_back_to_saved_copy(self):
        row,auth=self.official();auth['tokens']['refresh_token']='new-refresh'
        private_json(self.home/'auth.json',auth);info=self.manager.info
        info.read_identity=Mock(return_value={'kind':'chatgpt','owner':row['tokenOwner'],'name':row['email']})
        with patch('bridge.accounts.ManagedRPC') as rpc:
            rpc.return_value.__enter__.return_value=self.rpc()
            info.read(row,'usage')
            self.assertEqual(rpc.call_args.args[0],self.home)
        saved=json.loads((self.manager.directory(row['id'])/'auth.json').read_text())
        self.assertEqual(saved['tokens']['refresh_token'],'new-refresh')

    def test_errors_do_not_expose_upstream_body_and_api_has_models_without_usage(self):
        row,_=self.official();info=self.manager.info;info.read_identity=Mock(return_value={'kind':'signedOut','name':''})
        with patch('bridge.accounts.ManagedRPC') as rpc:
            rpc.return_value.__enter__.return_value=self.rpc(rate_error=True);info.read(row,'usage')
        self.assertEqual(info.entries[row['id']]['usage']['status'],'error')
        self.assertNotIn('raw private',json.dumps(self.manager.public()))
        api=self.manager.add_api({'name':'API','baseUrl':'https://fixture.test/v1','apiKey':'key','model':'m'})['accounts'][-1]
        with self.assertRaises(ValueError):info.request({'id':api['id'],'section':'usage'})
        with patch('bridge.account_info.model_ids',return_value=['m1','m2']):info.read(api,'models')
        self.assertEqual(len(info.entries[api['id']]['models']['models']),2)

    def test_detail_cache_suppresses_duplicate_queries(self):
        row,_=self.official();info=self.manager.info
        info.entries[row['id']]={'usage':{'status':'loading'}}
        with patch('bridge.account_info.threading.Thread') as worker:
            info.request({'id':row['id'],'section':'usage'});worker.assert_not_called()
        info.entries[row['id']]['usage']={'status':'ready','checkedAt':time.time()}
        with patch('bridge.account_info.threading.Thread') as worker:
            info.request({'id':row['id'],'section':'usage'});worker.assert_not_called()
            info.request({'id':row['id'],'section':'usage','refresh':True});worker.assert_called_once()

    def test_refresh_retains_last_success_during_loading_and_failure(self):
        row,_=self.official();info=self.manager.info
        previous={'status':'ready','checkedAt':123,'limits':[{'name':'Codex','windows':[]}],
                  'resetCredits':{'availableCount':2},'updatedAt':123}
        info.entries[row['id']]={'usage':previous.copy()}
        with patch('bridge.account_info.threading.Thread'):
            info.request({'id':row['id'],'section':'usage','refresh':True})
        pending=info.entries[row['id']]['usage']
        self.assertEqual(pending['status'],'loading')
        self.assertEqual(pending['limits'],previous['limits'])
        self.assertEqual(pending['updatedAt'],123)
        info.read_identity=Mock(return_value={'kind':'signedOut','name':''})
        with patch('bridge.accounts.ManagedRPC') as rpc:
            rpc.return_value.__enter__.return_value=self.rpc(rate_error=True)
            info.read(row,'usage',True)
        failed=info.entries[row['id']]['usage']
        self.assertEqual(failed['status'],'error')
        self.assertEqual(failed['limits'],previous['limits'])
        self.assertEqual(failed['updatedAt'],123)
        self.assertNotIn('raw private',json.dumps(failed))

    def test_usage_cache_lasts_five_minutes_but_explicit_read_bypasses_it(self):
        row,_=self.official();info=self.manager.info
        info.entries[row['id']]={'usage':{'status':'ready','checkedAt':time.time()-299}}
        with patch('bridge.account_info.threading.Thread') as worker:
            info.request({'id':row['id'],'section':'usage'});worker.assert_not_called()
            info.entries[row['id']]['usage']['checkedAt']=time.time()-301
            info.request({'id':row['id'],'section':'usage'});worker.assert_called_once()
