import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bridge.clients.zcode import quota
from bridge.clients.zcode.accounts import ZCodeAccounts


class QuotaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.credentials = self.home / 'credentials.json'
        self.login = {'oauth:active_provider': 'enc:v1:active', 'oauth:zai:access_token': 'enc:v1:a',
                      'oauth:zai:user_info': 'enc:v1:profile'}
        self.credentials.write_text(json.dumps(self.login))
        self.store = ZCodeAccounts(self.home/'saved', self.home, lambda: {})
        self.now = int(time.time())+30
        stamp = datetime.fromtimestamp(self.now)
        self.date_text = stamp.strftime('%Y-%m-%d %H:%M:%S')+'.000'
        self.path = self.home/'logs'/(stamp.date().isoformat()+'.log')
        self.path.parent.mkdir()
        self.plans = [{'user_plan_id': 'mine', 'plan_id': 'free', 'status': 'active',
                       'entitlements': [{'entitlement_id': 'a', 'period': 'daily'},
                                        {'entitlement_id': 'b', 'period': 'daily'},
                                        {'entitlement_id': 'promo', 'period': 'one_time'}]}]
        self.buckets = [{'user_plan_id': 'mine', 'plan_id': 'free', 'entitlement_id': token,
                         'unit_type': 'token', 'show_name': model, 'capabilities': ['model:'+model.lower()],
                         'remaining_units': amt, 'total_units': 1000, 'period_start': self.now-100,
                         'period_end': self.now+100} for token, model, amt in
                        [('a', 'GLM-5.3', 0), ('b', 'GLM-5.3-Flash', 750), ('promo', 'GLM-5.3', 111)]]

    def write_event(self, host='local-1', provider='zai', when=None):
        data = {'server_time': self.now if when is None else when, 'plans': self.plans, 'balances': self.buckets}
        event = {'providerId': 'account:'+provider+'-start-plan', 'success': True, 'payload': {'data': data, 'code': 0},
                 'url': 'https://zcode.z.ai/api/v1/zcode-plan/billing/balance?app_version=3.17.2'}
        text = (f'[{self.date_text}] [info] [pid:123] [main] [host-log] ({host}) [host] '
                f'[{self.date_text}] [pid:234] [usage-stats] billing/balance 请求完成 ')
        self.path.write_text(text+json.dumps(event)+'\n', encoding='utf-8')

    def details(self):
        with patch('time.time', return_value=self.now):
            return self.store.details('current', True)['accounts'][0]['usage']

    def test_current_official_login_reads_daily_buckets_even_without_chat_transport(self):
        self.write_event()
        self.assertTrue(self.store.public()['capabilities']['quota'])
        usage = self.details()
        self.assertEqual(usage['checkedAt'], self.now)
        self.assertEqual([(b['name'], b['windows'][0]['remaining']) for b in usage['limits']],
                         [('GLM-5.3', 0), ('GLM-5.3-Flash', 750)])
        self.assertNotIn('enc:v1', json.dumps(self.store.public()))
        with self.assertRaisesRegex(ValueError, '仅支持查看当前'):
            self.store.details('inactive', True)

    def test_new_login_remote_host_wrong_family_and_expired_bucket_hide_receipts(self):
        self.write_event(when=self.now-29)
        self.assertEqual(self.details()['limits'], [])
        self.write_event(host='remote-1')
        self.assertEqual(self.details()['limits'], [])
        self.write_event(provider='bigmodel')
        self.assertEqual(self.details()['limits'], [])
        self.write_event()
        self.assertEqual(len(self.details()['limits']), 2)
        with patch('time.time', return_value=self.now+101):
            self.assertNotIn('usage', self.store.public()['accounts'][0])
            self.assertEqual(self.store.details('current', True)['accounts'][0]['usage']['limits'], [])
        self.login['oauth:bigmodel:user_info'] = 'enc:v1:b'
        self.credentials.write_text(json.dumps(self.login))
        self.assertFalse(self.store.public()['capabilities']['quota'])

    def test_switch_during_read_invalidates_result_and_cache(self):
        self.write_event()
        original = quota.latest
        # Two in-place writes can have the same timestamps on a coarse filesystem.
        regular, identity = quota.regular, self.credentials.stat()
        def same_tick(path, **kwargs):
            info = regular(path, **kwargs)
            return identity if path == self.credentials else info
        def switched(home, generation, now):
            result = original(home, generation, now)
            self.credentials.write_text(json.dumps({**self.login, 'oauth:zai:user_info': 'enc:v1:another'}))
            return result
        with patch.object(quota, 'regular', same_tick), patch.object(quota, 'latest', switched), \
                self.assertRaisesRegex(ValueError, '正在变化'):
            self.details()
        self.assertIsNone(self.store.quota_cache)

    def test_never_reads_external_log_links(self):
        self.write_event()
        original = self.home/'other.log'
        self.path.rename(original)
        try:
            self.path.symlink_to(original)
        except OSError as error:
            self.skipTest(str(error))
        self.assertEqual(self.details()['limits'], [])
