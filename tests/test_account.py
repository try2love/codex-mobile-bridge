import copy
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from bridge.account import Account, AccountError, AccountRPC, normalize_limits
from bridge.lifecycle import GatewayControl, request_pairing

ROOT = Path(__file__).resolve().parents[1]


class FakeRPC:
    def __init__(self):
        self.config = {'model_provider': 'openai', 'desktop': {'agent-usage-reset-enabled': True}}
        self.auth = {'account': {'type': 'chatgpt', 'email': 'test@example.test', 'planType': 'pro'},
                     'requiresOpenaiAuth': True}
        self.limits = {'rateLimits': {'primary': {'usedPercent': 25, 'windowDurationMins': 300, 'resetsAt': 2000000000}},
                       'rateLimitResetCredits': {'availableCount': 3, 'credits': [
                           {'id': 'card-1', 'resetType': 'codexRateLimits', 'status': 'available', 'expiresAt': None}]}}
        self.calls = []
        self.outcome = 'reset'
        self.fail_read = False
        self.fail_consume = False
        self.on_limits = None

    def __enter__(self): return self
    def __exit__(self, *_): pass

    def request(self, method, params=None):
        self.calls.append((method, params))
        if method == 'config/read': return {'config': copy.deepcopy(self.config)}
        if method == 'account/read': return copy.deepcopy(self.auth)
        if method == 'account/rateLimits/read':
            if self.on_limits: self.on_limits()
            if self.fail_read: raise AccountError('read failed')
            return copy.deepcopy(self.limits)
        if method == 'account/rateLimitResetCredit/consume':
            if self.fail_consume: raise AccountError('connection lost')
            return {'outcome': self.outcome}
        raise AssertionError(method)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.account = Account(self.tmp.name, self.tmp.name, 'unused')
        self.rpc = FakeRPC()
        self.account.rpc = lambda: self.rpc

    def tearDown(self): self.tmp.cleanup()

    def attempt(self, **overrides):
        return {'requestId': str(uuid.uuid4()), 'accountKey': self.account.read()['accountKey'],
                'creditId': 'card-1', 'confirmed': True, **overrides}

    def consumes(self): return [p for method, p in self.rpc.calls if method.endswith('/consume')]

    def test_automatic_reads_share_five_minute_cache_manual_reads_and_resets_bypass_it(self):
        self.account.read(refresh=False);self.account.read(refresh=False)
        count=lambda:len([m for m,_ in self.rpc.calls if m=='account/rateLimits/read'])
        self.assertEqual(count(),1)
        self.account.read();self.assertEqual(count(),2)
        for cached in self.account.usage_cache.values():cached['checkedAt']-=301
        self.account.read(refresh=False);self.assertEqual(count(),3)
        request=self.rpc.request
        def consume(method,params=None):
            value=request(method,params)
            if method=='account/rateLimitResetCredit/consume':
                self.rpc.limits['rateLimitResetCredits']['availableCount']=2
            return value
        with patch.object(self.rpc,'request',side_effect=consume):
            result=self.account.consume(self.attempt())
        self.assertEqual(result['account']['resetCredits']['availableCount'],2)

    def test_native_login_reads_only_sanitized_data(self):
        self.rpc.limits['secret'] = 'must not leave runtime'
        result = self.account.read()
        self.assertTrue(result['visible'])
        self.assertEqual(result['limits'][0]['windows'][0]['remainingPercent'], 75)
        self.assertEqual(result['resetCredits']['availableCount'], 3)
        self.assertNotIn('secret', str(result))
        self.assertEqual(self.consumes(), [])

    def test_failed_refresh_keeps_timestamp_and_never_reuses_another_account_snapshot(self):
        first = self.account.read()
        self.rpc.fail_read = True
        failed = self.account.read()
        self.assertEqual(failed['limits'], first['limits'])
        self.assertEqual(failed['updatedAt'], first['updatedAt'])
        self.assertTrue(failed['error'])
        self.rpc.auth['account']['email'] = 'different@example.test'
        changed = self.account.read()
        self.assertEqual(changed['limits'], [])
        self.assertIsNone(changed['updatedAt'])

    def test_api_key_signed_out_custom_provider_and_profile_are_hidden(self):
        for auth, login_type in (({'type': 'apiKey'}, 'api'), (None, 'signedOut'), ({'type': 'unknown'}, 'unknown')):
            self.rpc.auth['account'] = auth
            self.rpc.calls.clear()
            self.assertEqual(self.account.read(), {'visible': False, 'loginType': login_type})
            self.assertFalse(any('rateLimit' in method for method, _ in self.rpc.calls))
        self.rpc = FakeRPC()
        for config in ({'model_provider': 'proxy'},
                       {'profile': 'api', 'profiles': {'api': {'model_provider': 'proxy'}}},
                       {'model_providers': {'openai': {'base_url': 'https://proxy.test'}}}):
            self.rpc.config = config
            self.rpc.calls.clear()
            self.assertEqual(self.account.read(), {'visible': False, 'loginType': 'api'})
            self.assertEqual([m for m, _ in self.rpc.calls], ['config/read'])

    def test_unknown_quota_and_card_count_are_not_zero(self):
        result = normalize_limits({'rateLimits': {'primary': {}}})
        self.assertIsNone(result['limits'][0]['windows'][0]['remainingPercent'])
        self.assertIsNone(result['resetCredits'])
        self.assertIsNone(normalize_limits({'rateLimitResetCredits': {'credits': []}})['resetCredits']['availableCount'])

    def test_multiple_buckets_and_invalid_percentages(self):
        raw = {'rateLimitsByLimitId': {'one': {'primary': {'usedPercent': -20}},
                                     'two': {'secondary': {'usedPercent': 140}},
                                     'unknown': {'primary': {'usedPercent': float('nan')}}},
               'rateLimits': {'primary': {'usedPercent': 90}}}
        self.assertEqual([b['windows'][0]['remainingPercent'] for b in normalize_limits(raw)['limits']], [100, 0, None])

    def test_permission_confirmation_and_account_switch_block_consumption(self):
        body = self.attempt()
        with self.assertRaises(ValueError): self.account.consume({**body, 'confirmed': False})
        self.rpc.config['desktop']['agent-usage-reset-enabled'] = False
        self.assertTrue(self.account.read()['canReset'])
        self.rpc.config['desktop']['agent-usage-reset-enabled'] = True
        self.rpc.auth['account']['email'] = 'other@example.test'
        with self.assertRaises(PermissionError): self.account.consume(body)
        self.rpc.auth['account'] = {'type': 'apiKey'}
        with self.assertRaises(PermissionError): self.account.consume(body)
        self.assertEqual(self.consumes(), [])

    def test_account_changed_during_read_hides_old_data(self):
        self.rpc.on_limits = lambda: self.rpc.auth.update(account=None)
        self.assertEqual(self.account.read(), {'visible': False, 'loginType': 'signedOut'})

    def test_desktop_agent_permission_does_not_block_manual_reset(self):
        body = self.attempt()
        self.rpc.on_limits = lambda: self.rpc.config['desktop'].update({'agent-usage-reset-enabled': False})
        self.assertEqual(self.account.consume(body)['outcome'], 'reset')
        self.assertEqual(len(self.consumes()), 1)

    def test_account_changed_while_validating_card_cannot_consume(self):
        body = self.attempt()
        self.rpc.on_limits = lambda: self.rpc.auth.update(account=None)
        with self.assertRaises(PermissionError): self.account.consume(body)
        self.assertFalse(self.account.path.exists())
        self.assertEqual(self.consumes(), [])

    def test_duplicate_concurrent_requests_consume_once(self):
        body = self.attempt()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.account.consume(body), range(4)))
        self.assertTrue(all(r['outcome'] == 'reset' for r in results))
        self.assertEqual(self.consumes(), [{'creditId': 'card-1', 'idempotencyKey': body['requestId']}])
        self.assertEqual(self.rpc.calls[-2][0], 'config/read')  # reread limits and auth after success

    def test_uncertain_reset_survives_restart_and_reuses_key(self):
        body = self.attempt()
        self.rpc.fail_consume = True
        with self.assertRaises(AccountError): self.account.consume(body)
        fresh = Account(self.tmp.name, self.tmp.name, 'unused');fresh.rpc = lambda: self.rpc
        self.assertEqual(fresh.read()['pendingReset']['requestId'], body['requestId'])
        with self.assertRaises(AccountError): fresh.consume({**body, 'requestId': str(uuid.uuid4())})
        self.rpc.fail_consume = False;self.rpc.outcome = 'alreadyRedeemed'
        self.rpc.limits['rateLimitResetCredits'] = {'availableCount': 0, 'credits': []}
        self.assertEqual(fresh.consume(body)['outcome'], 'alreadyRedeemed')
        self.assertEqual([p['idempotencyKey'] for p in self.consumes()], [body['requestId']]*2)
        self.assertIsNone(fresh.read()['pendingReset'])

    def test_key_is_bound_to_account_and_card(self):
        body = self.attempt();self.account.consume(body)
        with self.assertRaises(ValueError): self.account.consume({**body, 'creditId': None})
        self.assertEqual(len(self.consumes()), 1)

    def test_count_only_and_noop_outcomes(self):
        self.rpc.limits['rateLimitResetCredits']['credits'] = None
        for outcome in ('nothingToReset', 'noCredit'):
            self.rpc.outcome = outcome
            body = self.attempt(creditId=None)
            result = self.account.consume(body)
            self.assertEqual(result['outcome'], outcome)
            self.assertNotIn('creditId', self.consumes()[-1])
            self.assertIsNone(result['account']['pendingReset'])

    def test_unavailable_expired_and_other_reset_types_cannot_be_used(self):
        body = self.attempt()
        for key, value in [('status', 'redeemed'), ('expiresAt', 1), ('resetType', 'unknown')]:
            row = self.rpc.limits['rateLimitResetCredits']['credits'][0]
            original = row[key];row[key] = value
            with self.assertRaises(ValueError): self.account.consume(body)
            row[key] = original
        self.rpc.fail_read = True
        with self.assertRaises(AccountError): self.account.consume(body)
        self.assertEqual(self.consumes(), [])

    def test_rpc_has_no_login_turn_or_config_write_capability(self):
        rpc = AccountRPC(self.tmp.name, 'unused')
        for method in ('turn/start', 'account/login/start', 'getAuthStatus', 'config/value/write'):
            with self.assertRaises(ValueError): rpc.request(method)

    def test_desktop_control_uses_same_account_service(self):
        control = GatewayControl(self.tmp.name)
        control.start(lambda: None, lambda _: {}, account=self.account.control)
        try:
            result = request_pairing(self.tmp.name, {'action': 'account', 'value': {'action': 'read'}})
            self.assertTrue(result['visible'])
            body = self.attempt()
            result = request_pairing(self.tmp.name, {'action': 'account', 'value': {'action': 'consume', **body}})
            self.assertEqual(result['outcome'], 'reset')
            self.account.consume(body)
            self.assertEqual(len(self.consumes()), 1)
        finally: control.close()


if __name__ == '__main__': unittest.main()
