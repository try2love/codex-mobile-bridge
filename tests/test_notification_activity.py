import json
from contextlib import closing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.features.notifications.channels import Notifications, save_settings
from bridge.app.service import LiveSession
from bridge.features.sessions.store import SessionStore
import test_bridge as support

ROOT = Path(__file__).resolve().parents[1]
THREAD = '11111111-1111-4111-8111-111111111111'


class ActiveNotificationsTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.session = LiveSession(THREAD)
        self.session.connected = True
        self.session.state = {'requests': [], 'turns': [{'turnId': 'old', 'status': 'completed'}]}
        session = self.session
        class Source:
            changes = [{'host': 'local', 'id': THREAD}]
            calls = 0
            releases = 0
            def notification_candidates(self):
                result, self.changes = self.changes, []
                return result
            def for_host(self, host):return self
            def notification_session(self, identifier):
                self.calls += 1
                session.connected = True
                return session
            def release_notification_session(self, value):
                self.releases += 1
                value.watched = False
                value.connected = False
                return True
        self.source = Source()
        save_settings(self.directory, {'enabled': True, 'topic': 'test'})
        self.manager = Notifications(self.source, self.directory)
        self.manager.defaults({'requests': True, 'completion': True})

    def rediscover(self):
        self.source.changes = [{'host': 'local', 'id': THREAD}]
        self.manager.discovery_at = 0

    def test_idle_retires_and_new_run_rejoins_then_completes_once(self):
        with patch('bridge.features.notifications.channels.publish') as send:
            self.manager.scan()
            self.assertEqual(self.source.releases, 1)
            for _ in range(20):self.manager.scan()
            self.assertEqual(self.source.calls, 1)
            send.assert_not_called()
            self.session.state['turns'].append({'turnId': 'new', 'status': 'inProgress'})
            self.rediscover();self.manager.scan()
            self.assertTrue(self.session.watched)
            self.session.state['turns'][-1]['status'] = 'completed'
            self.manager.scan();send.assert_called_once()
            self.assertEqual(self.source.releases, 2)
            self.manager.scan();send.assert_called_once()

    def test_idle_runtime_patch_before_turn_completion_does_not_detach_early(self):
        self.session.state['threadRuntimeStatus'] = {'type': 'active'}
        self.session.state['turns'][-1]['status'] = 'inProgress'
        self.manager.scan()
        self.session.state['threadRuntimeStatus']['type'] = 'idle'
        with patch('bridge.features.notifications.channels.publish') as send:
            self.manager.scan()
            self.assertEqual(self.source.releases, 0)
            self.session.state['turns'][-1]['status'] = 'completed'
            self.manager.scan()
            send.assert_called_once()
            self.assertEqual(self.source.releases, 1)

    def test_pending_approval_stays_attached_when_runtime_is_idle(self):
        self.session.state['threadRuntimeStatus'] = {'type': 'idle'}
        self.session.state['requests'] = [{'id': 'approval', 'method': 'item/commandExecution/requestApproval', 'params': {}}]
        with patch('bridge.features.notifications.channels.publish') as send:
            self.manager.scan();send.assert_called_once()
            self.assertEqual(self.source.releases, 0)
            self.session.state['requests'] = []
            self.manager.scan();self.assertEqual(self.source.releases, 1)

    def test_completion_retry_does_not_reattach_and_survives_restart(self):
        self.session.state['turns'][-1]['status'] = 'inProgress'
        self.manager.scan()
        self.session.state['turns'][-1]['status'] = 'completed'
        with patch('bridge.features.notifications.channels.publish', side_effect=OSError()):self.manager.scan()
        calls = self.source.calls
        self.assertEqual(self.source.releases, 1)
        for value in self.manager.ledger.values():value['next'] = 0
        with patch('bridge.features.notifications.channels.publish') as send:
            self.manager.scan();send.assert_called_once()
        self.assertEqual(self.source.calls, calls)
        # A failed completion is retained independently of subscriptions.
        for value in self.manager.ledger.values():value.update(delivered=False, next=0)
        from bridge.features.notifications.channels import write_json
        write_json(self.directory/'notification-delivery.json', self.manager.ledger)
        restarted = Notifications(self.source, self.directory)
        with patch('bridge.features.notifications.channels.publish') as send:
            restarted.scan();send.assert_called_once()
        self.assertEqual(self.source.calls, calls)

    def test_live_event_rejoins_without_waiting_for_metadata_discovery(self):
        with patch('bridge.features.notifications.channels.publish') as send:
            self.manager.scan()
            self.session.state['turns'].append({'turnId': 'event', 'status': 'inProgress'})
            self.source.notification_updates = lambda: [{'host': 'local', 'id': THREAD}]
            self.manager.scan()
            self.assertTrue(self.session.watched)
            self.session.state['turns'][-1]['status'] = 'completed'
            self.manager.scan()
            send.assert_called_once()

    def test_reenabled_defaults_probe_idle_chat_again(self):
        self.manager.scan()
        self.session.state['turns'].append({'turnId': 'later', 'status': 'inProgress'})
        self.manager.defaults({'requests': False, 'completion': False})
        self.manager.scan()
        self.manager.defaults({'requests': True, 'completion': True})
        self.manager.scan()
        self.assertTrue(self.session.watched)

    def test_disabled_completion_cancels_detached_retry(self):
        self.session.state['turns'][-1]['status'] = 'inProgress'
        self.manager.scan()
        self.session.state['turns'][-1]['status'] = 'completed'
        with patch('bridge.features.notifications.channels.publish', side_effect=OSError()):self.manager.scan()
        self.manager.defaults({'requests': True, 'completion': False})
        for value in self.manager.ledger.values():value['next'] = 0
        with patch('bridge.features.notifications.channels.publish') as send:
            self.manager.scan();send.assert_not_called()


class DiscoveryTests(unittest.TestCase):
    setUp = support.IntegrationTests.setUp
    tearDown = support.IntegrationTests.tearDown

    def test_incremental_discovery_and_equal_timestamp_new_thread(self):
        first = self.bridge.notification_candidates()
        self.assertIn(support.THREAD, [r['id'] for r in first])
        self.assertEqual(self.bridge.notification_candidates(), [])
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET updated_at=updated_at+1 WHERE id=?', (support.THREAD,))
        self.assertEqual(len(self.bridge.notification_candidates()), 1)
        self.assertEqual(self.bridge.notification_candidates(), [])
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:
            db.execute('INSERT INTO threads SELECT ?,title,cwd,updated_at,1,originator,source,rollout_path FROM threads WHERE id=?', (THREAD, support.THREAD))
        self.assertEqual(self.bridge.notification_candidates(), [{'id': THREAD, 'host': 'local'}])

    def test_reconnect_rediscovers_without_metadata_change(self):
        self.assertTrue(self.bridge.notification_candidates())
        self.assertEqual(self.bridge.notification_candidates(), [])
        self.bridge._disconnected()
        self.assertTrue(self.bridge.notification_candidates())

    def test_stream_updates_do_not_rejoin_idle_chat_but_wake_active_chat(self):
        session = self.bridge.notification_session(support.THREAD)
        until = time.monotonic()+2
        while not session.connected and time.monotonic()<until:time.sleep(.01)
        self.assertTrue(self.bridge.notification_updates())
        session.watched = False
        self.bridge.notification_dirty.add(session.id)
        self.assertEqual(self.bridge.notification_updates(), [])
        session.state['threadRuntimeStatus'] = {'type': 'active'}
        self.bridge.notification_dirty.add(session.id)
        self.assertTrue(self.bridge.notification_updates())

    def test_release_only_notification_subscription_preserves_phone_reader(self):
        session = self.bridge.notification_session(support.THREAD)
        until = time.monotonic()+2
        while not session.connected and time.monotonic()<until:time.sleep(.01)
        self.assertTrue(session.connected)
        session.viewers = 1
        self.bridge.release_notification_session(session)
        self.assertTrue(session.connected)
        session.viewers = 0
        self.bridge.release_notification_session(session)
        self.assertFalse(session.connected)
        self.assertFalse(session.watched)
