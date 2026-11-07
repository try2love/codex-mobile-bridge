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
            self.assertEqual(self.store.public()['accounts'][0]['usage']['limits'], [])
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


class NativeQuotaTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]/'.tmp'
        root.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.now = 1900000000
        self.original_time = self.now
        clock = patch('time.time', side_effect=lambda: self.now)
        clock.start();self.addCleanup(clock.stop)
        self.current = {'identity': 'a'*64, 'provider': 'zai', 'name': 'Fixture'}
        self.calls = []
        self.failure = None
        self.snapshot = {'authenticated': True, 'generatedAt': self.now*1000,
                         'serverTime': self.now*1000, 'context': {'scope': 'personal'},
                         'provider': {'id': 'account:zai-start-plan', 'name': 'Z.AI Start'},
                         'quota': {'level': 'Start', 'limits': [
                             {'type': 'daily-main', 'period': 'daily', 'unitType': 'token',
                              'periodStart': (self.now-600)*1000, 'periodEnd': (self.now+3600)*1000,
                              'nextResetTime': (self.now+3500)*1000, 'number': 3000000, 'remaining': 0,
                              'usageDetails': [{'modelCode': 'glm-5.3', 'displayName': 'GLM-5.3', 'usage': 3000000}]},
                             {'type': 'daily-flash', 'period': 'daily', 'unitType': 'token',
                              'periodStart': (self.now-600)*1000, 'periodEnd': (self.now+3600)*1000,
                              'nextResetTime': (self.now+3500)*1000, 'number': 5000000, 'remaining': 4000000,
                              'usageDetails': [{'modelCode': 'glm-5.3-flash', 'displayName': 'GLM-5.3-Flash', 'usage': 1000000}]},
                         ]}}
        self.store = ZCodeAccounts(self.home/'saved', self.home, lambda: dict(self.current), self.reader)

    def reader(self, refresh):
        self.calls.append(refresh)
        if self.failure:
            raise self.failure
        return {'account': dict(self.current), 'quota': self.snapshot}

    def usage(self, value):
        return next(row['usage'] for row in value['accounts'] if row['id'] == 'current')

    def test_opening_accounts_fetches_once_then_uses_five_minute_cache_and_manual_throttle(self):
        usage = self.usage(self.store.public())
        self.assertEqual(self.calls, [False])
        self.assertEqual(usage['source'], 'native')
        self.assertEqual(usage['checkedAt'], self.now)
        self.assertEqual([(row['name'], row['windows'][0]['remaining']) for row in usage['limits']],
                         [('GLM-5.3', 0), ('GLM-5.3-Flash', 4000000)])
        self.assertEqual(usage['limits'][1]['windows'][0]['resetsAt'], self.now+3500)
        self.store.public();self.store.details('current', True)
        self.assertEqual(self.calls, [False])
        self.now += 31
        self.store.details('current', True)
        self.assertEqual(self.calls, [False, True])
        self.now += 299
        self.store.public();self.assertEqual(len(self.calls), 2)
        self.now += 1
        self.store.public();self.assertEqual(self.calls, [False, True, False])

    def test_account_bound_result_does_not_depend_on_whole_credential_file_mtime(self):
        first = self.usage(self.store.public())
        (self.home/'credentials.json').write_text(json.dumps({'oauth:active_provider': 'enc:v1:active',
            'oauth:zai:access_token': 'enc:v1:private', 'oauth:zai:user_info': 'enc:v1:profile',
            'project-key-location:new': 'enc:v1:unrelated'}))
        self.now += 1
        self.assertEqual(self.usage(self.store.public()), first)
        self.assertEqual(self.calls, [False])

    def test_failed_refresh_keeps_valid_same_account_values_and_original_time_without_secret_error(self):
        first = self.usage(self.store.public())
        self.failure = ValueError('private-token-must-not-escape')
        self.now += 31
        failed = self.usage(self.store.details('current', True))
        self.assertEqual(failed['limits'], first['limits'])
        self.assertEqual(failed['checkedAt'], first['checkedAt'])
        self.assertEqual(failed['status'], 'error')
        self.assertNotIn('private-token', json.dumps(failed))
        self.store.details('current', True);self.assertEqual(len(self.calls), 2)
        self.now = self.original_time+3501
        expired = self.usage(self.store.public())
        self.assertEqual(expired['limits'], [])
        self.assertNotIn('checkedAt', expired)

    def test_account_switch_rejects_inflight_snapshot_and_discards_cached_previous_account(self):
        self.store.public()
        self.now += 31
        def switch(_refresh):
            old = dict(self.current)
            self.current['identity'] = 'b'*64
            return {'account': old, 'quota': self.snapshot}
        self.store.quota_reader = switch
        with self.assertRaisesRegex(ValueError, '登录正在变化'):
            self.store.details('current', True)
        self.assertIsNone(self.store.quota_cache)

    def test_failed_request_cannot_reuse_previous_account_balance_after_a_switch(self):
        self.store.public()
        self.now += 31
        def changed(_refresh):
            self.current['identity'] = 'b'*64
            raise ValueError('upstream failed after switch')
        self.store.quota_reader = changed
        with self.assertRaisesRegex(ValueError, '登录正在变化'):
            self.store.details('current', True)
        self.assertIsNone(self.store.quota_cache)
        self.store.quota_reader = lambda _refresh: {'account': {**self.current, 'identity': 'a'*64}, 'quota': self.snapshot}
        with self.assertRaisesRegex(ValueError, '登录正在变化'):
            self.store.public()
        self.assertIsNone(self.store.quota_cache)

    def test_native_parser_requires_selected_family_daily_tokens_and_actual_remaining(self):
        wrong = {**self.snapshot, 'provider': {'id': 'account:bigmodel-start-plan'}}
        with self.assertRaisesRegex(ValueError, '当前官方账号'):
            quota.native_snapshot(wrong, 'zai', self.now)
        self.snapshot['quota']['limits'][0]['period'] = 'one_time'
        self.snapshot['quota']['limits'][1]['remaining'] = 0
        self.snapshot['quota']['limits'][1]['available'] = 900000
        value = quota.native_snapshot(self.snapshot, 'zai', self.now)
        self.assertEqual(len(value['limits']), 1)
        self.assertEqual(value['limits'][0]['windows'][0]['remaining'], 0)
        self.assertEqual(value['limits'][0]['windows'][0]['remainingPercent'], 0)
        self.snapshot['quota']['limits'][1]['nextResetTime'] = (self.now-1)*1000
        self.assertEqual(quota.native_snapshot(self.snapshot, 'zai', self.now)['limits'], [])

    def test_missing_native_plan_is_not_fabricated_as_a_zero_balance(self):
        self.snapshot.update(unavailableReason='no_plan', quota=None)
        value = self.usage(self.store.public())
        self.assertEqual(value['status'], 'unsupported')
        self.assertEqual(value['limits'], [])
