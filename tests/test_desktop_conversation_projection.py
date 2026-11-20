import json
import threading
from collections import OrderedDict
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bridge.clients.manager import DesktopSessions


class DesktopConversationProjectionTests(unittest.TestCase):
    def manager(self):
        value = {'session': {'id': 'chat', 'cwd': '/project', 'status': 'active', 'requests': [{'id': 'current'}]},
                 'messages': [{'id': 'input', 'role': 'user', 'text': 'Task input'},
                              {'id': 'operation', 'role': 'activity', 'kind': 'commandExecution', 'title': 'Run command', 'text': 'PRIVATE_OUTPUT' * 4000},
                              {'id': 'supplement', 'role': 'user', 'kind': 'steeringUserMessage', 'text': 'Ordinary supplement'},
                              {'id': 'output', 'role': 'assistant', 'text': 'Task output'}],
                 'requests': [{'id': 'current'}], 'history': {'before': 'native-cursor', 'hasMore': True}}
        manager = SimpleNamespace(read_lock=threading.Lock(), binding_epochs={'claude': 1}, history_timelines=OrderedDict(),
                                  call=Mock(return_value=value), require_enabled=Mock(), history_cursor_secret=b'fixture secret')
        return manager, value

    def test_shared_native_projection_does_not_transfer_process_or_lose_current_controls(self):
        manager, value = self.manager()
        page = DesktopSessions._conversation(manager, 'claude', 'chat', {})
        self.assertNotIn('PRIVATE_', json.dumps(page))
        self.assertEqual(page['history'], value['history'])
        self.assertEqual(page['requests'], [{'id': 'current'}])
        self.assertEqual(page['session']['requests'], [{'id': 'current'}])
        process = next(row for row in page['messages'] if row.get('processCursor'))
        directory = DesktopSessions._conversation(manager, 'claude', 'chat', {'historyProcess': process['processCursor']})
        self.assertNotIn('PRIVATE_', json.dumps(directory))
        row = directory['rows'][0]
        body = DesktopSessions._conversation(manager, 'claude', 'chat', {'historyDetail': row['detailCursor'], 'historyVersion': row['version']})
        self.assertTrue(body['text'].startswith('PRIVATE_OUTPUT'))
        self.assertEqual(manager.call.call_count, 3)
        self.assertTrue(all(not timeline.details for timeline in manager.history_timelines.values()))

    def test_native_cursor_and_binding_scope_are_preserved_without_resuming(self):
        manager, _ = self.manager()
        page = DesktopSessions._conversation(manager, 'claude', 'chat', {'historyBefore': 'native-before', 'historyLimit': 100})
        manager.call.assert_called_once_with('claude', 'detail', 'chat', {'historyBefore': 'native-before', 'historyLimit': 100})
        cursor = next(row['processCursor'] for row in page['messages'] if row.get('processCursor'))
        for provider, sid in [('claude', 'other')]:
            with self.assertRaises(ValueError):
                DesktopSessions._conversation(manager, provider, sid, {'historyProcess': cursor})
        manager.binding_epochs['claude'] = 2
        with self.assertRaises(ValueError):
            DesktopSessions._conversation(manager, 'claude', 'chat', {'historyProcess': cursor})

    def test_evicted_projection_reloads_from_its_scoped_source_without_losing_history(self):
        manager, _ = self.manager()
        page = DesktopSessions._conversation(manager, 'claude', 'chat', {})
        cursor = next(row['processCursor'] for row in page['messages'] if row.get('processCursor'))
        manager.history_timelines.clear()
        directory = DesktopSessions._conversation(manager, 'claude', 'chat', {'historyProcess': cursor})
        self.assertEqual(len(directory['rows']), 1)
        with self.assertRaises(ValueError):
            DesktopSessions._conversation(manager, 'claude', 'chat', {'historyProcess': cursor.replace('timeline.', 'timeline.x', 1)})

    def test_evicted_reordered_source_rejects_continuation_instead_of_skipping_rows(self):
        manager, value = self.manager()
        value['messages'] = [{'id': str(i), 'role': 'activity', 'kind': 'commandExecution', 'text': str(i)} for i in range(30)]
        page = DesktopSessions._conversation(manager, 'claude', 'chat', {})
        cursor = page['messages'][0]['processCursor']
        first = DesktopSessions._conversation(manager, 'claude', 'chat', {'historyProcess': cursor})
        manager.history_timelines.clear()
        value['messages'].reverse()
        with self.assertRaisesRegex(ValueError, '过程记录已变化'):
            DesktopSessions._conversation(manager, 'claude', 'chat', {'historyProcess': cursor, 'historyAfter': first['next']})

    def test_large_native_page_uses_local_continuation_before_advancing_native_cursor(self):
        manager, value = self.manager()
        value['messages'] = [{'id': str(i), 'role': 'assistant', 'text': str(i)} for i in range(240)]
        page = DesktopSessions._conversation(manager, 'claude', 'chat', {})
        seen = {row['id'] for row in page['messages']}
        while page['history']['before'].startswith('timeline.'):
            page = DesktopSessions._conversation(manager, 'claude', 'chat', {'historyBefore': page['history']['before']})
            self.assertTrue(seen.isdisjoint(row['id'] for row in page['messages']))
            seen.update(row['id'] for row in page['messages'])
        self.assertEqual(len(seen), 240)
        self.assertEqual(page['history']['before'], 'native-cursor')

    def test_disabled_or_unknown_read_parameters_cannot_use_retained_projection(self):
        manager, _ = self.manager()
        with self.assertRaises(ValueError):
            DesktopSessions._conversation(manager, 'claude', 'chat', {'unexpected': True})
        manager.require_enabled.side_effect = ValueError('disabled')
        with self.assertRaisesRegex(ValueError, 'disabled'):
            DesktopSessions._conversation(manager, 'claude', 'chat', {})

    def test_display_row_keys_preserve_native_edit_and_branch_action_identities(self):
        manager, value = self.manager()
        value['messages'] = [{'id': 'display-user', 'role': 'user', 'text': 'Question', 'editMessageId': 'native-edit-token'},
                             {'id': 'display-answer', 'role': 'assistant', 'text': 'Answer', 'forkMessageId': 'native-fork-token'}]
        value['capabilities'] = {'editMessage': True, 'fork': True}
        page = DesktopSessions._conversation(manager, 'claude', 'chat', {})
        self.assertNotEqual(page['messages'][0]['id'], 'native-edit-token')
        self.assertEqual(page['messages'][0]['editMessageId'], 'native-edit-token')
        self.assertEqual(page['messages'][1]['forkMessageId'], 'native-fork-token')
        self.assertEqual(page['capabilities'], {'editMessage': True, 'fork': True})


if __name__ == '__main__':
    unittest.main()
