import os
import time
import unittest
from unittest import mock

from bridge.clients.antigravity.cli_session import AntigravityCli


class QuotaTests(unittest.TestCase):
    def test_proto_default_zero_requires_quota_message_presence(self):
        self.assertIsNone(AntigravityCli._quota_info({}))
        self.assertEqual(AntigravityCli._quota_info({'quotaInfo': {}})['quotaStatus'], 'exhausted')
        self.assertEqual(AntigravityCli._quota_info({'quotaInfo': {'remainingFraction': 0}})['quotaTag'], '已用尽')

    @unittest.skipUnless(hasattr(time, 'tzset'), 'requires POSIX timezone selection')
    def test_reset_time_retains_the_timezone_meaning(self):
        original = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'Asia/Shanghai'
            time.tzset()
            self.assertEqual(AntigravityCli._format_reset_note('2026-11-14T12:30:00Z'), '将于 11/14 20:30 CST 重置')
        finally:
            if original is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = original
            time.tzset()

    def test_missing_plan_does_not_invent_credits_but_present_zero_plan_does(self):
        cli = AntigravityCli()
        with mock.patch.object(cli, 'user_status', return_value={}) as read:
            self.assertNotIn('usageSummary', cli.catalog_models())
            read.return_value = {'planStatus': {}}
            self.assertEqual(cli.catalog_models()['usageSummary']['credits'], '可用 Prompt 点数: 0 · Flow 点数: 0')


class SharedQuotaTests(unittest.TestCase):
    @staticmethod
    def raw(fraction=.7):
        return {'groups': [{'displayName': 'Gemini Models',
            'description': 'Models within this group: Gemini Example, Other Model',
            'buckets': [{'bucketId': 'short', 'window': '5h', 'remainingFraction': fraction,
                         'resetTime': '2026-11-22T10:00:00Z'},
                        {'bucketId': 'week', 'window': 'weekly', 'remainingFraction': 0}]}]}

    def test_explicit_membership_dual_windows_and_unknown_do_not_guess_a_pool(self):
        from bridge.clients.antigravity.quota import quota_groups, model_windows
        groups = quota_groups(self.raw())
        windows = model_windows(groups, 'gemini-example', 'Gemini Example')
        self.assertEqual([row['windowDurationMins'] for row in windows], [300, 10080])
        self.assertEqual([row['remainingPercent'] for row in windows], [70, 0])
        self.assertEqual(model_windows(groups, 'gemini-new', 'Gemini New'), [])
        self.assertEqual(model_windows(groups * 2, 'gemini-example', 'Gemini Example'), [])
        for value in (None, True, float('nan'), float('inf'), -1, 2, '0.5'):
            self.assertIsNone(quota_groups(self.raw(value))[0]['windows'][0]['remainingPercent'])

    def test_cache_is_bound_to_account_and_process_and_failed_reads_keep_time(self):
        from bridge.clients.errors import BridgeUnavailable
        cli = AntigravityCli()
        with mock.patch.object(cli, 'source_identity', return_value='process-one') as identity, \
             mock.patch.object(cli, 'user_status', return_value={'email': 'one@example.test'}) as user, \
             mock.patch.object(cli, 'call_ls_rpc', return_value={'response': self.raw()}) as rpc:
            first = cli.quota_snapshot()
            again = cli.quota_snapshot()
            self.assertEqual(rpc.call_count, 1)
            again['groups'].clear()
            self.assertTrue(cli.quota_snapshot()['groups'])
            rpc.side_effect = BridgeUnavailable('unavailable')
            stale = cli.quota_snapshot(refresh=True)
            self.assertEqual(stale['status'], 'stale')
            self.assertEqual(stale['checkedAt'], first['checkedAt'])
            before = rpc.call_count
            cli.quota_snapshot()
            self.assertEqual(rpc.call_count, before)
            user.return_value = {'email': 'two@example.test'}
            self.assertEqual(cli.quota_snapshot()['groups'], [])
            rpc.side_effect = None
            identity.return_value = 'process-two'
            self.assertEqual(cli.quota_snapshot()['status'], 'ready')
            self.assertEqual(rpc.call_args.kwargs['expected_identity'], 'process-two')

    def test_inflight_account_switch_discards_reply_and_old_rpc_failure_is_throttled(self):
        cli = AntigravityCli()
        with mock.patch.object(cli, 'source_identity', return_value='process'), \
             mock.patch.object(cli, 'user_status', side_effect=[{'email': 'one'}, {'email': 'two'}]), \
             mock.patch.object(cli, 'call_ls_rpc', return_value={'response': self.raw()}):
            self.assertEqual(cli.quota_snapshot()['groups'], [])
            self.assertFalse(cli._quota_cache)
        with mock.patch.object(cli, 'source_identity', return_value='process'), \
             mock.patch.object(cli, 'user_status', return_value={'email': 'one'}), \
             mock.patch.object(cli, 'call_ls_rpc', return_value={}) as rpc:
            cli.quota_snapshot();cli.quota_snapshot()
            self.assertEqual(rpc.call_count, 1)

    def test_overlay_preserves_model_directory_time_and_persisted_group_summary(self):
        from bridge.clients.antigravity.quota import overlay, quota_groups
        from bridge.clients.codex.catalog import ModelStore
        catalog = {'cache': {'updatedAt': 100}, 'models': [{'id': 'example', 'name': 'Gemini Example'}]}
        value = overlay(catalog, {'groups': quota_groups(self.raw()), 'checkedAt': 200, 'status': 'ready'})
        self.assertEqual(value['cache']['updatedAt'], 100)
        self.assertEqual(value['usageSummary']['checkedAt'], 200)
        self.assertNotIn('quotaWindows', catalog['models'][0])
        directory = ModelStore.directory({'groups': [{'surface': 'app', **value}]})
        self.assertEqual(directory['groups'][0]['usageSummary']['checkedAt'], 200)
