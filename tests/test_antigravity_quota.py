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

