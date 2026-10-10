"""Desktop notices reuse delivery boundaries without sending external messages."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.features.notifications.channels import Notifications, save_settings, write_json

ROOT = Path(__file__).resolve().parents[1]


class DesktopNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [{'provider': provider, 'host': 'desktop:' + provider, 'id': 'same-native-id',
                      'title': provider + ' private title', 'status': 'idle', 'updatedAt': 1, 'requests': []}
                     for provider in ('claude', 'deepseek')]
        self.turns = {p: [{'turnId': 'old', 'status': 'completed'}] for p in ('claude', 'deepseek')}
        self.desktop = Mock()
        self.desktop.notification_rows.side_effect = lambda: self.rows
        self.desktop.enabled.return_value = True
        self.desktop.call.side_effect = lambda provider, action, sid: {'connected': True, 'turns': list(self.turns[provider])}
        self.bridge = Mock()
        # Desktop notices must never ask Codex to open or attach a chat.
        self.bridge.notification_candidates.return_value = []
        self.bridge.notification_updates.return_value = []
        self.bridge.for_host.side_effect = AssertionError('desktop notification activated Codex')
        self.manager = Notifications(self.bridge, self.root, lambda: ['https://example.com'], desktop_sessions=self.desktop)
        self.addCleanup(self.manager.close)
        save_settings(self.root, {'mobileEnabled': True})
        self.manager.defaults({'requests': True, 'completion': True})

    def scan(self):
        self.manager.desktop_poll_at = 0
        self.manager.scan()
        return self.manager.mobile.read()['events']

    def test_permissions_are_deduplicated_per_app_and_keep_the_route(self):
        for row in self.rows:
            row['requests'] = [{'id': 'permission-one'}]
        events = self.scan()
        self.assertEqual(len(events), 2)
        self.assertEqual({e['host'] for e in events}, {'desktop:claude', 'desktop:deepseek'})
        self.assertEqual({e['title'] for e in events}, {'Claude 需要你的确认', 'DSH 需要你的确认'})
        self.assertTrue(all(e['kind'] == 'request' for e in events))
        self.assertEqual(len(self.scan()), 2)
        self.assertNotIn('private title', str(events))

    def test_historical_completion_is_baselined_and_new_native_end_notifies_once(self):
        self.assertEqual(self.scan(), [])
        self.rows[0]['updatedAt'] = 2
        self.turns['claude'].append({'turnId': 'new-result-uuid', 'status': 'completed'})
        events = self.scan()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['title'], 'Claude 运行已完成')
        self.assertEqual(events[0]['kind'], 'completion')
        self.assertEqual(len(self.scan()), 1)

    def test_running_turn_finishing_is_detected_without_a_new_list_timestamp(self):
        self.rows[1]['status'] = 'active'
        self.turns['deepseek'].append({'turnId': 'turn-2', 'status': 'inProgress'})
        self.scan()
        self.rows[1]['status'] = 'idle'
        self.turns['deepseek'][-1]['status'] = 'completed'
        self.assertEqual(self.scan()[0]['title'], 'DSH 运行已完成')

    def test_failed_turn_and_idle_without_native_end_do_not_notify(self):
        self.scan()
        self.rows[0]['updatedAt'] = 2
        self.turns['claude'].append({'turnId': 'failure', 'status': 'failed'})
        self.rows[1]['status'] = 'unknown'
        self.assertEqual(self.scan(), [])

    def test_session_override_uses_same_default_policy_and_does_not_attach_codex(self):
        self.manager.policy('same-native-id', 'desktop:claude', {'requests': 'off', 'completion': 'off'})
        self.rows[0]['requests'] = [{'id': 'hidden'}]
        self.rows[1]['requests'] = [{'id': 'visible'}]
        events = self.scan()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['host'], 'desktop:deepseek')
        self.bridge.for_host.assert_not_called()

    def test_unchanged_idle_history_is_not_reloaded_each_poll(self):
        self.scan()
        self.desktop.call.reset_mock()
        self.scan()
        self.desktop.call.assert_not_called()

    def test_history_read_budget_does_not_delay_permissions(self):
        self.rows[:] = [{**self.rows[0], 'id': str(i), 'requests': [{'id': 'ask'}]} for i in range(20)]
        self.assertEqual(len(self.scan()), 20)
        self.assertEqual(self.desktop.call.call_count, 8)
        self.scan()
        self.assertEqual(len(self.manager.desktop_details), 16)

    def test_completion_survives_retry_and_gateway_reopen(self):
        save_settings(self.root, {'enabled': True, 'topic': 'fixture'})
        with patch('bridge.features.notifications.channels.publish'):
            self.scan()
        self.rows[1]['updatedAt'] = 2
        self.turns['deepseek'].append({'turnId': 'new', 'status': 'completed'})
        with patch('bridge.features.notifications.channels.publish', side_effect=OSError('offline')):
            self.scan()
        self.manager.close()
        self.manager = Notifications(self.bridge, self.root, desktop_sessions=self.desktop)
        for record in self.manager.ledger.values():
            record['next'] = 0
        with patch('bridge.features.notifications.channels.publish') as publish:
            self.scan()
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(publish.call_args.args[1], 'DSH 运行已完成')

    def test_notice_links_encode_native_ids_and_preserve_provider(self):
        sid = 'native/~ id'
        config = {'clickBase': 'https://example.com'}
        self.assertEqual(self.manager.click_url(config, sid, 'desktop:deepseek'),
                         'https://example.com/#native%2F%7E%20id~desktop%3Adeepseek')
        config['mobileAppLinks'] = True
        self.assertIn('&thread=native%2F%7E%20id&host=desktop%3Adeepseek',
                      self.manager.click_url(config, sid, 'desktop:deepseek'))

    def test_disabled_app_blocks_queued_mobile_push(self):
        self.desktop.enabled.return_value = False
        self.assertFalse(self.manager._push_allowed({'threadId': 'same-native-id', 'host': 'desktop:claude', 'kind': 'request'}))
