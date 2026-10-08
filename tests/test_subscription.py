import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from bridge.subscription import subscription_period, timestamp, request_json
from bridge.account import Account

ROOT = Path(__file__).resolve().parents[1]

class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.home = Path(self.temp.name)
        self.tokens = {'account_id':'selected', 'access_token':'opaque-test-token',
                       'id_token':self.jwt({'email':'fixture@test','https://api.openai.com/auth':{'chatgpt_subscription_active_until':'2020-01-01T00:00:00Z'}})}
        self.save()
        self.record = {'accounts':{'org':{'account':{'account_id':'selected'}, 'entitlement':{'expires_at':'2030-01-01T00:00:00Z'}}}}
    def tearDown(self):self.temp.cleanup()
    def jwt(self,value):return 'e30.'+base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')+'.sig'
    def save(self):(self.home/'auth.json').write_text(json.dumps({'tokens':self.tokens}))
    def test_live_date_replaces_stale_login_claim(self):
        with patch('bridge.subscription.request_json',return_value=self.record) as get:
            result=subscription_period(self.home,'fixture@test')
        self.assertEqual(result['periodEndsAt'],1893456000)
        self.assertEqual(result['source'],'online');self.assertEqual(get.call_count,1)
        self.assertNotIn('opaque-test-token',str(result))
    def test_missing_or_expired_entitlement_falls_back_to_selected_subscription(self):
        for value in [None,'2020-01-01T00:00:00Z']:
            self.record['accounts']['org']['entitlement']['expires_at']=value
            with patch('bridge.subscription.request_json',side_effect=[self.record,{'active_until':'2030-02-01T00:00:00Z'}]) as get:
                result=subscription_period(self.home,'fixture@test')
            self.assertEqual(result['periodEndsAt'],1896134400)
            self.assertEqual(get.call_args.args[2],{'account_id':'selected'})
    def test_other_default_paid_workspace_is_never_selected(self):
        self.record['accounts']['org']['account']['account_id']='other'
        with patch('bridge.subscription.request_json',return_value=self.record) as get:
            self.assertIsNone(subscription_period(self.home,'fixture@test')['periodEndsAt'])
        self.assertEqual(get.call_count,1)
    def test_mismatched_identity_never_sends_token(self):
        with patch('bridge.subscription.request_json') as get:
            self.assertIsNone(subscription_period(self.home,'other@test')['periodEndsAt'])
            self.tokens['access_token']=self.jwt({'https://api.openai.com/auth':{'chatgpt_account_id':'other'}});self.save()
            self.assertIsNone(subscription_period(self.home,'fixture@test')['periodEndsAt'])
            get.assert_not_called()
    def test_failed_or_restricted_query_stays_unknown_not_expired(self):
        for error in [OSError('401 secret'),ValueError('403 upstream body'),TimeoutError('secret')]:
            with patch('bridge.subscription.request_json',side_effect=error):
                result=subscription_period(self.home,'fixture@test')
            self.assertIsNone(result['periodEndsAt']);self.assertNotIn('secret',str(result));self.assertIn('error',result)
    def test_missing_live_date_does_not_use_even_a_future_login_claim(self):
        self.tokens['id_token']=self.jwt({'email':'fixture@test','https://api.openai.com/auth':{'chatgpt_subscription_active_until':'2030-01-01T00:00:00Z'}});self.save()
        with patch('bridge.subscription.request_json',return_value={}):
            self.assertIsNone(subscription_period(self.home,'fixture@test')['periodEndsAt'])
    def test_expired_live_date_is_a_date_not_an_expired_account_claim(self):
        self.record['accounts']['org']['entitlement']['expires_at']='2020-01-01T00:00:00Z'
        with patch('bridge.subscription.request_json',side_effect=[self.record,{'active_until':'2020-01-01T00:00:00Z'}]):
            result=subscription_period(self.home,'fixture@test')
        self.assertEqual(result['periodEndsAt'],1577836800);self.assertNotIn('expired',result)
    def test_cache_is_per_identity_and_manual_refresh_bypasses_it(self):
        account=Account(self.home,self.home,executable='unused')
        with patch('bridge.account.subscription_period',side_effect=lambda *args:{'observedAt':__import__('time').time(),'periodEndsAt':None}) as read:
            for key,refresh in [('one',False),('one',False),('two',False),('one',True)]:
                account.subscription(self.home,{'accountKey':key},refresh=refresh)
        self.assertEqual(read.call_count,3)
    def test_timestamp_seconds_milliseconds_iso_and_invalid(self):
        for value in [1893456000,'1893456000',1893456000000,'2030-01-01T00:00:00Z']:
            self.assertEqual(timestamp(value),1893456000)
        for value in [None,True,False,'nan',float('inf'),'bad','2030-01-01']:
            self.assertIsNone(timestamp(value))
    def test_transport_is_bounded_and_uses_only_fixed_https_origin(self):
        response=MagicMock();response.__enter__.return_value=response;response.read.return_value=b'{}'
        with patch('bridge.subscription.build_opener') as build:
            build.return_value.open.return_value=response
            self.assertEqual(request_json('subscriptions','private',{'account_id':'a&b'}),{})
        req=build.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url,'https://chatgpt.com/backend-api/subscriptions?account_id=a%26b')
        self.assertEqual(req.get_method(),'GET');self.assertEqual(req.get_header('Authorization'),'Bearer private')
        self.assertEqual(response.read.call_args.args,(1048577,))
        self.assertEqual(type(build.call_args.args[0]).__name__,'NoRedirect')
