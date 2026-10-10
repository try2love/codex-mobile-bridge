import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from bridge.app.desktop import Desktop
from bridge.app.lifecycle import GatewayControl
from bridge.features.notifications.channels import Notifications, read_json, save_settings, write_json
from bridge.app.service import LiveSession

ROOT = Path(__file__).resolve().parents[1]


class WatchManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.id = str(uuid.uuid4())
        self.rows = [{'id': self.id, 'host': 'local'},
                     {'id': self.id, 'host': 'remote', 'title': 'Cached remote chat', 'cwd': '/remote/project'}]
        write_json(self.data/'notification-watches.json', self.rows)
        write_json(self.data/'desktop.json', {'codexHome': str(self.data)})
        self.desktop = Desktop(self.data)
        self.desktop.status = lambda: {'running': False}
        self.manager = Notifications(None, self.data)
        self.addCleanup(self.manager.close)

    def action(self, action='update', host='remote', **extra):
        return {'action': action, 'host': host, 'id': self.id, **extra}

    def test_offline_update_remove_and_host_isolation_preserve_chat(self):
        with closing(sqlite3.connect(self.data/'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads (id TEXT, originator TEXT, title TEXT, cwd TEXT)')
            db.execute('INSERT INTO threads VALUES (?, ?, ?, ?)', (self.id, 'Codex Desktop', 'Local chat title', '/local/project'))
        before = (self.data/'state_5.sqlite').read_bytes()
        rows = self.desktop.notification_watches({'action': 'list'})['watches']
        self.assertEqual(rows[0]['title'], 'Local chat title')
        self.assertEqual(rows[1]['title'], 'Cached remote chat')
        result = self.desktop.notification_watches(self.action(notifyOnCompletion=True))
        self.assertFalse(result['watches'][0]['notifyOnCompletion'])
        self.assertTrue(result['watches'][1]['notifyOnCompletion'])
        self.desktop.notification_watches(self.action('remove'))
        self.assertEqual(read_json(self.data/'notification-watches.json', []), [self.rows[0]])
        self.assertEqual((self.data/'state_5.sqlite').read_bytes(), before)

    def test_only_existing_watches_can_be_changed_without_connecting(self):
        with patch('bridge.app.service.Bridge.for_host', side_effect=AssertionError('No activation or remote connection')):
            self.manager.control(self.action(notifyOnCompletion=True))
            self.manager.control(self.action('remove'))
            with self.assertRaisesRegex(ValueError, '已移除'):
                self.manager.control(self.action(notifyOnCompletion=True))
        for invalid in (None, [], {}, self.action('create'), self.action(notifyOnCompletion='yes'), self.action('remove', extra=True)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.manager.control(invalid)

    def test_live_control_updates_active_manager_baseline_and_cancels_retry(self):
        save_settings(self.data, {'enabled': True, 'topic': 'test'})
        session = LiveSession(self.id)
        session.connected = True
        session.state = {'title': 'Renamed remote chat', 'turns': [{'turnId': 'run', 'status': 'inProgress', 'items': []}]}
        class Source:
            def for_host(self, host): return self
            def session(self, thread, **kwargs): return session
        self.manager.bridge = Source()
        with patch('bridge.features.notifications.channels.publish'):
            self.manager.scan()
        control = GatewayControl(self.data)
        control.start(lambda: None, lambda value: {}, notifications=self.manager.control)
        self.addCleanup(control.close)
        self.desktop.status = lambda: {'running': True}
        self.desktop.notification_watches(self.action(notifyOnCompletion=True))
        self.assertTrue(self.manager.completions)
        session.state['turns'][0]['status'] = 'completed'
        with patch('bridge.features.notifications.channels.publish', side_effect=OSError('offline')) as send:
            self.manager.scan()
            self.assertEqual(send.call_count, 1)
        self.desktop.notification_watches(self.action(notifyOnCompletion=False))
        self.assertFalse(self.manager.completions)
        for row in self.manager.ledger.values(): row['next'] = 0
        with patch('bridge.features.notifications.channels.publish') as send:
            self.manager.scan()
            send.assert_not_called()
        rows = self.desktop.notification_watches(self.action('remove'))['watches']
        self.assertEqual([row['host'] for row in rows], ['local'])

    def test_missing_live_capability_does_not_edit_a_running_managers_file(self):
        before = (self.data/'notification-watches.json').read_bytes()
        write_json(self.data/'gateway-control.json', {'pid': 123, 'token': 'fixture'})
        self.desktop.status = lambda: {'running': True}
        with self.assertRaisesRegex(ValueError, '重新启动'):
            self.desktop.notification_watches(self.action('remove'))
        self.desktop.status = lambda: {'running': False}
        with self.assertRaisesRegex(ValueError, '暂不可用'):
            self.desktop.notification_watches(self.action('remove'))
        self.assertEqual((self.data/'notification-watches.json').read_bytes(), before)

    def test_scan_caches_metadata_and_never_sends_after_removal_during_attach(self):
        save_settings(self.data, {'enabled': True, 'topic': 'test'})
        write_json(self.data/'notification-watches.json', [self.rows[1]])
        session = LiveSession(self.id)
        session.connected = True
        session.state = {'title': 'New title', 'cwd': '/new/path', 'requests': [
            {'id': 'question', 'method': 'item/tool/requestUserInput', 'params': {}}]}
        manager = self.manager
        class Source:
            remove = False
            def for_host(self, host): return self
            def session(self, thread, **kwargs):
                if self.remove: manager.control({'action': 'remove', 'id': thread, 'host': 'remote'})
                return session
        source = Source()
        manager.bridge = source
        with patch('bridge.features.notifications.channels.publish'):
            manager.scan()
        self.assertEqual(manager.watches()[0]['title'], 'New title')
        self.assertEqual(manager.watches()[0]['cwd'], '/new/path')
        session.state['requests'][0]['id'] = 'new-question'
        source.remove = True
        with patch('bridge.features.notifications.channels.publish') as send:
            manager.scan()
            send.assert_not_called()
        self.assertEqual(manager.watches(), [])
