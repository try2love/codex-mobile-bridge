"""List indicators consume native terminal evidence without delaying the list."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]


class DesktopActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.manager = DesktopSessions(self.temp.name)
        self.addCleanup(self.manager.close)
        self.adapter = Mock()
        self.manager.adapters['claude'] = self.adapter
        self.row = {'id': 'chat', 'status': 'idle', 'updatedAt': 1}
        self.turn = {'turnId': 'old', 'status': 'completed'}
        self.adapter.call.side_effect = self.read
        self.done = threading.Event()
        self.real_thread = threading.Thread
        # Observe the actual background worker completing; no timing sleeps.
        def thread(*args, **kwargs):
            target = kwargs['target']
            def run():
                try: target()
                finally: self.done.set()
            return self.real_thread(target=run, daemon=True)
        self.patch = patch('bridge.clients.manager.threading.Thread', side_effect=thread)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def read(self, action, sid=None, body=None):
        if action == 'list': return {'connected': True, 'sessions': [dict(self.row)]}
        self.assertEqual(action, 'detail')
        return {'connected': True, 'session': dict(self.row), 'turns': [dict(self.turn)], 'messages': []}

    def refresh(self):
        self.done.clear()
        value = self.manager.list_with_activity('claude')
        self.assertTrue(self.done.wait(2))
        return value

    def test_terminal_fields_reach_list_and_detail_without_reading_unchanged_history(self):
        self.refresh()
        self.adapter.call.reset_mock()
        value = self.manager.list_with_activity('claude')['sessions'][0]
        self.assertEqual((value['turnId'], value['turnStatus']), ('old', 'completed'))
        self.adapter.call.assert_called_once_with('list', None, {})
        self.row.update(status='active', updatedAt=2); self.turn = {'turnId': 'new', 'status': 'inProgress'}
        self.refresh()
        self.row['status'] = 'idle'; self.turn['status'] = 'completed'
        self.refresh()
        self.assertEqual(self.manager.list_with_activity('claude')['sessions'][0]['turnStatus'], 'completed')
        self.assertEqual(self.manager.call('claude', 'detail', 'chat')['session']['turnId'], 'new')

    def test_unknown_history_is_not_inferred_as_success(self):
        original = self.read
        self.adapter.call.side_effect = lambda action, sid=None, body=None: original(action, sid, body) if action == 'list' else {'connected': True, 'session': dict(self.row), 'turns': []}
        self.refresh()
        self.assertNotIn('turnStatus', self.manager.list_with_activity('claude')['sessions'][0])

    def test_slow_history_does_not_block_list_and_old_binding_cannot_publish(self):
        entered, release = threading.Event(), threading.Event()
        original = self.read
        def read(action, sid=None, body=None):
            if action == 'detail': entered.set(); release.wait(2)
            return original(action, sid, body)
        self.adapter.call.side_effect = read
        value = self.manager.list_with_activity('claude')
        self.assertTrue(entered.wait(1)); self.assertEqual(value['sessions'][0]['id'], 'chat')
        with self.manager._changing('claude'): pass
        release.set(); self.assertTrue(self.done.wait(2))
        self.assertEqual(self.manager.activity, {})
