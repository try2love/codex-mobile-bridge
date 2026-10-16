import base64
import json
import time
import unittest
from unittest.mock import patch

import test_accounts
from bridge.features.accounts.account import subscription_period, normalize_limits
from bridge.features.accounts.monitor import AccountMonitor, account_identity, events, DEFAULTS
from bridge.features.notifications.channels import save_settings


class InsightsTests(unittest.TestCase):
    setUp = test_accounts.AccountsTests.setUp
    tearDown = test_accounts.AccountsTests.tearDown

    def test_credits_are_separate_from_reset_cards_and_unknown_is_not_zero(self):
        result=normalize_limits({'rateLimits':{'credits':{'balance':'62500','unlimited':False,'private':'secret'}},
                                 'rateLimitResetCredits':{'availableCount':2}})
        self.assertEqual(result['limits'][0]['credits']['balance'],'62500')
        self.assertEqual(result['resetCredits']['availableCount'],2)
        self.assertNotIn('secret',str(result))
        self.assertIsNone(normalize_limits({'rateLimits':{}})['limits'][0]['credits'])

    def test_subscription_reminder_is_explicitly_a_nonlive_login_record(self):
        now = 1791400000
        reminders = events({'subscription':{'periodEndsAt':now+86400,'source':'login'}}, {}, now,
                           {**DEFAULTS, 'subscriptionExpiry':True})
        self.assertEqual(len(reminders), 1)
        self.assertIn('并非实时账单信息', reminders[0][3])
        self.assertNotIn('当前订阅周期', reminders[0][3])
        self.assertEqual(events({'subscription':{'periodEndsAt':now-1,'source':'login'}}, {}, now,
                                {**DEFAULTS, 'subscriptionExpiry':True}), [])

    def test_desktop_update_reminder_points_to_client_management(self):
        monitor = self.manager.monitor
        monitor.configure({'desktopUpdate': True})
        save_settings(self.root, {'barkEnabled': True, 'barkKey': 'fixture'})
        with patch.object(self.manager.updates, 'check'), patch.object(
                self.manager.updates, 'status', return_value={'state': 'available', 'targetBuild': '101'}), patch.object(
                monitor, 'deliver') as deliver:
            monitor.scan()
        self.assertIn('客户端管理的 Codex 版本与更新', deliver.call_args.args[1][3])

    def test_reminders_do_not_guess_recovery_from_a_passed_timestamp(self):
        now=2000000000
        value={'limits':[{'id':'codex','windows':[{'kind':'primary','remainingPercent':5,'resetsAt':now-1}]}]}
        prefs={k:True for k in DEFAULTS}
        self.assertEqual(events(value,{},now,prefs),[])
        previous={'codex:primary':value['limits'][0]['windows'][0].copy()}
        value['limits'][0]['windows'][0].update(remainingPercent=100,resetsAt=now+18000)
        self.assertEqual(events(value,previous,now,prefs)[0][0],'quotaReset')

    def test_reminder_delivery_deduplicates_across_restart_and_disabled_channels(self):
        monitor=self.manager.monitor
        save_settings(self.root,{'barkEnabled':True,'barkKey':'fixture'})
        monitor.configure({'lowQuota':True})
        event=('lowQuota','window:123','title','body')
        with patch('bridge.features.accounts.monitor.publish_bark') as send:
            monitor.deliver('account1',event);monitor.deliver('account1',event)
            from bridge.features.accounts.monitor import AccountMonitor
            restored=AccountMonitor(self.manager);restored.deliver('account1',event)
            self.assertEqual(send.call_count,1)
            restored.deliver('account2',event);self.assertEqual(send.call_count,2)
            restored.configure({'lowQuota':False});restored.deliver('account3',event)
            self.assertEqual(send.call_count,2)

    def test_disabled_reminders_never_query_accounts(self):
        with patch.object(self.manager.info,'request') as query:
            self.manager.monitor.scan();query.assert_not_called()

    def test_reminder_identity_aliases_preserve_delivery_and_recovery_history(self):
        monitor = self.manager.monitor
        save_settings(self.root, {'barkEnabled': True, 'barkKey': 'fixture'})
        monitor.configure({'lowQuota': True, 'quotaReset': True})
        now = time.time()
        value = {'updatedAt': now, 'limits': [{'id': 'codex', 'windows': [
            {'kind': 'primary', 'remainingPercent': 5, 'resetsAt': now+60}]}]}
        with patch('bridge.features.accounts.monitor.publish_bark') as send:
            monitor.observe('saved-profile', value)
            monitor.observe('account', value, aliases=('saved-profile',))
            send.assert_called_once()
            self.assertNotIn('saved-profile', monitor.previous)
            self.assertEqual(monitor.previous['account']['codex:primary']['remainingPercent'], 5)
            self.assertTrue(all(key.startswith('account|') for key in AccountMonitor(self.manager).ledger))
            AccountMonitor(self.manager).observe('account', value)
            send.assert_called_once()
            with patch('bridge.features.accounts.monitor.time.time', return_value=now+61):
                recovered = {'updatedAt': now+61, 'limits': [{'id': 'codex', 'windows': [
                    {'kind': 'primary', 'remainingPercent': 100, 'resetsAt': now+3600}]}]}
                monitor.observe('account', recovered)
            self.assertEqual(send.call_args.args[1], 'Codex 额度已恢复')
            self.assertEqual(send.call_count, 2)

    def test_reminder_identity_uses_workspace_ownership_and_keeps_keyring_fallback(self):
        owner = ['workspace-one', 'user', 'fixture@example.test']
        key = account_identity(owner, 'profile-one')
        self.assertEqual(key, account_identity(owner, 'profile-two'))
        self.assertNotEqual(key, account_identity(['workspace-two', *owner[1:]], 'profile-one'))
        self.assertNotIn(owner[2], key)
        self.assertEqual(account_identity(None, 'native-account-key'), 'native-account-key')

    def test_reminder_identity_alias_merge_keeps_sent_records_and_retry_backoff(self):
        for old_sent, current_sent in ((True, False), (False, True), (False, False)):
            with self.subTest(old_sent=old_sent, current_sent=current_sent):
                monitor = self.manager.monitor
                monitor.ledger = {
                    'profile|lowQuota|window|device': {'sent': old_sent, 'time': 200, 'next': 500},
                    'account|lowQuota|window|device': {'sent': current_sent, 'time': 100, 'next': 400}}
                monitor.observe('account', {'updatedAt': time.time(), 'limits': []}, aliases=('profile',))
                self.assertNotIn('profile|lowQuota|window|device', monitor.ledger)
                merged = monitor.ledger['account|lowQuota|window|device']
                self.assertEqual(merged['sent'], old_sent or current_sent)
                if not merged['sent']:
                    self.assertEqual(merged['next'], 500)
                self.assertEqual(AccountMonitor(self.manager).ledger, monitor.ledger)

    def test_reminder_failure_is_sanitized_and_retried_without_claiming_delivery(self):
        monitor=self.manager.monitor;monitor.configure({'resetExpiry':True})
        save_settings(self.root,{'barkEnabled':True,'barkKey':'fixture'})
        event=('resetExpiry','card','title','body')
        with patch('bridge.features.accounts.monitor.publish_bark',side_effect=ValueError('private-key')) as send:
            monitor.deliver('account',event);monitor.deliver('account',event)
            send.assert_called_once()
        self.assertNotIn('private-key',monitor.last_error)
        self.assertFalse(next(iter(monitor.ledger.values()))['sent'])

    def test_reminder_settings_reject_unknown_or_non_boolean_values(self):
        for value in ({'lowQuota':1},{'consumeReset':True},None):
            with self.assertRaises(ValueError):self.manager.monitor.configure(value)



    def test_early_quota_recovery_is_confirmed_by_a_new_window_and_increased_balance(self):
        now = 2000000000
        previous = {'codex:primary': {'remainingPercent': 1, 'resetsAt': now+100}}
        window = {'kind': 'primary', 'remainingPercent': 100, 'resetsAt': now+18000}
        value = {'limits': [{'id': 'codex', 'windows': [window]}]}
        preferences = {**DEFAULTS, 'quotaReset': True}
        self.assertEqual(events(value, previous, now, preferences)[0][0], 'quotaReset')
        window['remainingPercent'] = 1
        self.assertEqual(events(value, previous, now, preferences), [])


if __name__=='__main__':unittest.main()
