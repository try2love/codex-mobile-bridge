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

    def test_retrieve_user_quota_summary_attaches_5h_and_weekly_windows_and_groups(self):
        cli = AntigravityCli()
        status = {
            'userTier': {'name': 'Google AI Pro'},
            'planStatus': {'availablePromptCredits': 500, 'availableFlowCredits': 100},
            'cascadeModelConfigData': {
                'clientModelConfigs': [
                    {
                        'modelId': 'gemini-4-neon-high',
                        'label': 'Gemini 4 Neon (High)',
                        'modelOrAlias': {'model': 'MODEL_M532'},
                        'quotaInfo': {'remainingFraction': 0.70, 'resetTime': '2026-11-21T15:50:25Z'},
                    },
                    {
                        'modelId': 'gemini-4-argon-medium',
                        'label': 'Gemini 4 Argon (Medium)',
                        'modelOrAlias': {'model': 'MODEL_M418'},
                        'quotaInfo': {'remainingFraction': 0.32, 'resetTime': '2026-11-28T10:50:45Z'},
                    },
                    {
                        'modelId': 'claude-opus-5-5-high',
                        'label': 'Claude Opus 5.5 (High)',
                        'modelOrAlias': {'model': 'MODEL_M402'},
                        'quotaInfo': {'remainingFraction': 0.37, 'resetTime': '2026-11-25T10:27:56Z'},
                    },
                ],
            },
        }
        qsummary = {
            'groups': [
                {
                    'displayName': 'Gemini Models',
                    'description': 'Models within this group: Gemini 4 Neon, Gemini 4 Hydrogen',
                    'buckets': [
                        {'bucketId': 'gemini-weekly', 'window': 'weekly', 'remainingFraction': 0.91, 'resetTime': '2026-11-28T10:50:25Z'},
                        {'bucketId': 'gemini-5h', 'window': '5h', 'remainingFraction': 0.70, 'resetTime': '2026-11-21T15:50:25Z'},
                    ],
                },
                {
                    'displayName': 'Claude and GPT models',
                    'description': 'Models within this group: Claude Opus 5.5, GPT-OSS 120B',
                    'buckets': [
                        {'bucketId': '3p-weekly', 'window': 'weekly', 'remainingFraction': 0.372, 'resetTime': '2026-11-25T10:27:56Z'},
                        {'bucketId': '3p-5h', 'window': '5h', 'remainingFraction': 1.0, 'resetTime': '2026-11-21T16:07:59Z'},
                    ],
                },
                {
                    'displayName': 'Gemini 4 Argon',
                    'description': 'Separate Argon quota.',
                    'buckets': [
                        {'bucketId': 'argon-weekly', 'window': 'weekly', 'remainingFraction': 0.326, 'resetTime': '2026-11-28T10:50:45Z'},
                    ],
                },
            ],
        }
        with mock.patch.object(cli, 'user_status', return_value=status), \
                mock.patch.object(cli, 'quota_summary', return_value=qsummary):
            cat = cli.catalog_models()
        by_id = {m['id']: m for m in cat['models']}
        self.assertEqual([w['shortLabel'] for w in by_id['gemini-4-neon']['quotaWindows']], ['5h', '7d'])
        self.assertEqual([w['remainingPercent'] for w in by_id['gemini-4-neon']['quotaWindows']], [70.0, 91.0])
        self.assertEqual([w['shortLabel'] for w in by_id['claude-opus-5-5']['quotaWindows']], ['5h', '7d'])
        self.assertEqual([w['remainingPercent'] for w in by_id['claude-opus-5-5']['quotaWindows']], [100.0, 37.2])
        self.assertEqual([w['shortLabel'] for w in by_id['gemini-4-argon']['quotaWindows']], ['7d'])
        self.assertEqual([g['name'] for g in cat['usageSummary']['groups']], ['Gemini Models', 'Claude and GPT models', 'Gemini 4 Argon'])

    def test_overlay_live_quota_and_current_model_windows_refresh_cached_catalog(self):
        cli = AntigravityCli()
        stale_catalog = {
            'cache': {'state': 'fresh', 'updatedAt': 1000.0},
            'groups': [{
                'surface': 'app',
                'label': 'Antigravity 2.0',
                'models': [{'id': 'gemini-4-neon', 'name': 'Gemini 4 Neon', 'quotaTag': '剩余 100%'}],
                'usageSummary': {'tier': 'Google AI Pro', 'credits': '可用 Prompt 点数: 500'},
            }],
        }
        live_summary = {
            'groups': [{
                'displayName': 'Gemini Models',
                'description': 'Models within this group: Gemini 4 Neon',
                'buckets': [
                    {'bucketId': 'gemini-5h', 'window': '5h', 'remainingFraction': 0.59, 'resetTime': '2026-11-21T15:50:25Z'},
                    {'bucketId': 'gemini-weekly', 'window': 'weekly', 'remainingFraction': 0.88, 'resetTime': '2026-11-28T10:50:25Z'},
                ],
            }],
        }
        with mock.patch.object(cli, 'call_ls_rpc', return_value={'response': live_summary}):
            refreshed = cli.overlay_live_quota(None, stale_catalog)
            m0 = refreshed['groups'][0]['models'][0]
            self.assertEqual([(w['shortLabel'], w['remainingPercent']) for w in m0['quotaWindows']], [('5h', 59.0), ('7d', 88.0)])
            self.assertEqual(refreshed['groups'][0]['usageSummary']['groups'][0]['name'], 'Gemini Models')
            self.assertGreater(refreshed['cache']['updatedAt'], 1000.0)
            wins = cli.current_model_quota_windows('app', 'gemini-4-neon', 'Gemini 4 Neon')
            self.assertEqual([(w['shortLabel'], w['remainingPercent']) for w in wins], [('5h', 59.0), ('7d', 88.0)])



