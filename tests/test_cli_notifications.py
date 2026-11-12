"""Read-only native CLI observations use the shared companion inbox."""
import copy
from contextlib import closing
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from bridge.clients.codex.app_server import AppServerUnavailable, AppServerUnsupported
from bridge.clients.codex.cli_session import CliSessions
from bridge.features.notifications.channels import Notifications, save_settings
from bridge.features.sessions.store import SessionStore
from tests.test_cli_bridge_integration import CliBridgeFixture
from tests.test_codex_cli_session import FakeClient


class CliNotificationsTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        self.native, self.fake = self.install_native(self.bridge, self.home)
        self.directory = self.home/'notifications'
        save_settings(self.directory, {'mobileEnabled': True})
        self.manager = Notifications(self.bridge, self.directory)
        self.addCleanup(self.manager.close)
        self.manager.defaults({'requests': True, 'completion': True})

    def install_native(self, bridge, home, alias=None):
        native = CliSessions(home, bridge._cli_state, bridge._cli_disconnected, alias=alias)
        fake = FakeClient(home)
        if alias:
            fake.server_info['codexHome'] = '/remote/codex-home'
        fake.thread.update(id=self.thread_id, cwd=self.native_state['cwd'], modelProvider='openai')
        native.client, native.store = fake, SessionStore(home)
        native._start_daemon = Mock(side_effect=AssertionError('notifications started a daemon'))
        native.attach = Mock(side_effect=AssertionError('notifications resumed a thread'))
        bridge.cli_sessions = native
        bridge.origins[self.thread_id] = 'cli'
        return native, fake

    def observe(self, bridge=None):
        bridge = bridge or self.bridge
        previous = bridge.cli_notifications.get(self.thread_id)
        if previous:
            previous.retry_at = 0
        session = bridge.notification_session(self.thread_id)
        with session.condition:
            self.assertTrue(session.condition.wait_for(lambda: not session.notification_connecting, timeout=2))
        return session

    def scan(self):
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET updated_at=updated_at+1')
        self.observe()
        self.manager.discovery_at = 0
        self.manager.scan()
        return self.manager.mobile.read()['events']

    def turn(self, identifier, status, flag=None, item='item-1'):
        self.fake.thread['status'] = {'type': 'active', 'activeFlags': [flag] if flag else []} if status == 'inProgress' else {'type': 'idle' if status == 'completed' else 'systemError'}
        turn = {'id': identifier, 'status': status,
            'items': [{'id': item, 'type': 'commandExecution', 'status': status,
                       'command': 'private command text'}]}
        turns = self.fake.thread.setdefault('turns', [])
        index = next((index for index, row in enumerate(turns) if row['id'] == identifier), None)
        if index is None:
            turns.append(turn)
        else:
            turns[index] = turn

    def test_passive_cli_read_is_separate_from_ui_and_never_mutates_native(self):
        session = self.observe()
        self.assertTrue(session.connected)
        self.assertEqual(session.runtime, 'cli-observer')
        self.assertTrue(session.access['readOnly'])
        self.assertFalse(session.access['canSend'])
        self.assertNotIn(self.thread_id, self.bridge.live)
        self.assertFalse(self.bridge.view(self.thread_id, attach=False)['access']['canSend'])
        self.assertEqual(self.native.joined, {})
        self.assertEqual([method for method, _ in self.fake.sent], ['thread/read'])
        self.native.attach.assert_not_called()
        self.native._start_daemon.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.owner.assert_not_called()

    def test_completion_failure_and_status_approvals_share_inbox_and_deduplicate(self):
        self.turn('historical', 'failed')
        self.assertEqual(self.scan(), [])
        self.turn('completion', 'inProgress'); self.scan()
        self.turn('completion', 'completed')
        self.assertEqual([event['kind'] for event in self.scan()], ['completion'])
        self.scan()
        self.turn('failure', 'inProgress'); self.scan()
        self.turn('failure', 'failed')
        self.assertEqual([event['kind'] for event in self.scan()], ['completion', 'attention'])
        self.turn('approval', 'inProgress', 'waitingOnApproval')
        self.assertEqual([event['kind'] for event in self.scan()], ['completion', 'attention', 'request'])
        observer = self.bridge.cli_notifications[self.thread_id]
        self.assertEqual(observer.state['requests'], [])
        self.assertNotIn('method', observer.pending_requests()[0])
        self.native.requests[self.thread_id] = {77: {'id': 77, 'method': 'item/commandExecution/requestApproval'}}
        self.assertEqual(len(self.scan()), 3, 'joining must not duplicate a status-only request notice')
        self.turn('approval', 'inProgress', 'waitingOnApproval', item='item-2')
        self.assertEqual(len(self.scan()), 3, 'item changes do not prove a new waiting episode')
        self.turn('approval', 'inProgress', item='item-2'); self.scan()
        self.turn('approval', 'inProgress', 'waitingOnApproval', item='item-2')
        self.assertEqual(len(self.scan()), 4)
        self.turn('approval', 'inProgress', 'waitingOnUserInput', item='item-3')
        events = self.scan()
        self.assertEqual(len(events), 5)
        self.assertTrue(all(event['host'] == 'local' and event['threadId'] == self.thread_id for event in events))
        self.assertNotIn('private command text', str(events))
        self.native.attach.assert_not_called()
        self.native._start_daemon.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()

    def test_waiting_identity_tracks_observed_boundaries_and_survives_restart(self):
        from bridge.app.lifecycle import private_json
        from bridge.features.notifications.cli import CliWaits
        self.turn('approval', 'inProgress', 'waitingOnApproval')
        first = self.observe().pending_requests()[0]['id']
        self.fake.thread['turns'][-1]['items'].append({'id': 'unrelated', 'type': 'reasoning'})
        self.assertEqual(self.observe().pending_requests()[0]['id'], first)
        self.fake.thread['status']['activeFlags'] = []
        self.assertEqual(self.observe().pending_requests(), [])
        self.fake.thread['status']['activeFlags'] = ['waitingOnApproval']
        second = self.observe().pending_requests()[0]['id']
        self.assertNotEqual(second, first)
        reopened = CliWaits(self.bridge.data_dir, private_json)
        self.assertEqual(reopened.project(self.thread_id, self.native.observe(self.thread_id))[
            'notificationRequests'][0]['id'], second)

    def _assert_refresh_does_not_send_old_request(self, new_target):
        self.turn('approval', 'inProgress', 'waitingOnApproval')
        with patch.object(self.manager.mobile, 'append', side_effect=OSError('fixture inbox failure')):
            self.assertEqual(self.scan(), [])
        for row in self.manager.ledger.values():
            row['next'] = 0
        if new_target:
            self.manager.mobile.value['streamId'] = 'replacement-fixture-inbox'
        self.fake.thread['status']['activeFlags'] = []
        original = self.native.observe
        entered, release = threading.Event(), threading.Event()
        def blocked(identifier):
            entered.set()
            self.assertTrue(release.wait(2))
            return original(identifier)
        observer = self.bridge.cli_notifications[self.thread_id]
        observer.retry_at = 0
        with patch.object(self.native, 'observe', side_effect=blocked):
            try:
                self.bridge.notification_session(self.thread_id)
                self.assertTrue(entered.wait(1))
                with patch.object(self.manager.mobile, 'append', wraps=self.manager.mobile.append) as append:
                    self.manager.scan()
                    append.assert_not_called()
                self.assertFalse(observer.connected)
            finally:
                release.set()
                with observer.condition:
                    self.assertTrue(observer.condition.wait_for(lambda: not observer.notification_connecting, timeout=2))
        self.manager.scan()
        self.assertEqual(self.manager.mobile.read()['events'], [])

    def test_request_retry_waits_for_the_refresh_that_clears_the_request(self):
        self._assert_refresh_does_not_send_old_request(new_target=False)

    def test_new_inbox_target_waits_for_the_refresh_that_clears_the_request(self):
        self._assert_refresh_does_not_send_old_request(new_target=True)

    def test_queued_request_delivery_rechecks_observation_freshness(self):
        from bridge.features.notifications.channels import settings
        self.turn('approval', 'inProgress', 'waitingOnApproval')
        self.scan()
        observed = self.bridge.cli_notifications[self.thread_id]
        config = save_settings(self.directory, {'enabled': True, 'topic': 'isolated-unit-fixture'})
        target = self.manager._targets(config)['ntfy']
        row = {'id': self.thread_id, 'host': 'local'}
        keys = [self.manager._delivery_key(row, request['id'], target=target) for request in observed.pending_requests()]
        self.manager._dispatch(settings(self.directory), row, 'fixture', 'ntfy', target, keys, False)
        with observed.condition:
            observed.notification_connecting = True
            observed.connected = False
        try:
            with patch('bridge.features.notifications.channels.publish') as send:
                self.manager.deliver_pending()
                send.assert_not_called()
        finally:
            observed.notification_connecting = False

    def test_dequeued_request_does_not_send_after_observer_release(self):
        from bridge.features.notifications.channels import settings
        self.turn('approval', 'inProgress', 'waitingOnApproval')
        self.scan()
        observed = self.bridge.cli_notifications[self.thread_id]
        config = save_settings(self.directory, {'enabled': True, 'topic': 'isolated-unit-fixture'})
        target = self.manager._targets(config)['ntfy']
        row = {'id': self.thread_id, 'host': 'local'}
        keys = [self.manager._delivery_key(row, request['id'], target=target) for request in observed.pending_requests()]
        state = observed.state
        original = self.manager._send_keys
        for reattached in (False, True):
            with self.subTest(reattached=reattached):
                observed.state, observed.connected = state, True
                self.manager.attached[('local', self.thread_id)] = observed
                self.manager._dispatch(settings(self.directory), row, 'fixture', 'ntfy', target, keys, False)
                def released_after_dequeue(*args, **kwargs):
                    self.assertFalse(self.manager.delivery_queue)
                    self.bridge.release_notification_session(observed)
                    self.manager.attached.pop(('local', self.thread_id))
                    if reattached:
                        observed.state, observed.connected = state, True
                        self.manager.attached[('local', self.thread_id)] = observed
                    return original(*args, **kwargs)
                with patch.object(self.manager, '_send_keys', side_effect=released_after_dequeue), \
                        patch('bridge.features.notifications.channels.publish') as send:
                    self.manager.deliver_pending()
                    send.assert_not_called()

    def test_release_or_disable_discards_an_inflight_observation(self):
        for action in ('unwatch', 'released', 'disabled'):
            self.bridge.released_cli.set(self.thread_id, False)
            self.bridge.observation_enabled = lambda: True
            self.assertTrue(self.observe().connected)
            entered, release = threading.Event(), threading.Event()
            def blocked(_identifier):
                entered.set()
                self.assertTrue(release.wait(2))
                return copy.deepcopy(self.native_state)
            with self.subTest(action=action), patch.object(self.native, 'observe', side_effect=blocked):
                previous = self.bridge.cli_notifications.get(self.thread_id)
                if previous:
                    previous.retry_at = 0
                session = self.bridge.notification_session(self.thread_id)
                try:
                    self.assertTrue(entered.wait(1))
                    if action == 'released':
                        live = self.bridge.session(self.thread_id, attach=False)
                        self.bridge._record_cli_release(live, {'status': 'detached'})
                    elif action == 'disabled':
                        self.bridge.observation_enabled = lambda: False
                    else:
                        self.bridge.release_notification_session(session)
                finally:
                    release.set()
                    with session.condition:
                        self.assertTrue(session.condition.wait_for(lambda: not session.notification_connecting, timeout=2))
                self.assertFalse(session.connected)
                self.assertIsNone(session.state)
        self.bridge.observation_enabled = lambda: True
        self.bridge.released_cli.set(self.thread_id, True)
        with patch.object(self.native, 'observe') as observe:
            self.bridge.notification_session(self.thread_id)
            observe.assert_not_called()
        self.bridge.ipc.follow.assert_not_called()

    def test_unloaded_offline_or_wrong_home_never_resumes_for_notifications(self):
        self.fake.loaded = False
        self.assertIsNone(self.native.observe(self.thread_id))
        self.assertEqual(self.fake.sent, [])
        self.fake.loaded = True
        self.fake.server_info['codexHome'] = str(self.home/'wrong-home')
        with self.assertRaises(AppServerUnavailable):
            self.native.observe(self.thread_id)
        self.fake.connect = Mock(side_effect=AppServerUnavailable('offline'))
        self.assertFalse(self.observe().connected)
        self.native._start_daemon.assert_not_called()
        self.native.attach.assert_not_called()

    def test_unknown_origin_metadata_is_resolved_off_the_scanner(self):
        entered, release = threading.Event(), threading.Event()
        original = self.bridge.store.get
        def blocked(identifier):
            entered.set()
            self.assertTrue(release.wait(2))
            return original(identifier)
        self.bridge.origins.clear()
        with patch.object(self.bridge.store, 'get', side_effect=blocked):
            started = time.monotonic()
            session = self.bridge.notification_session(self.thread_id)
            try:
                self.assertLess(time.monotonic() - started, .2)
                self.assertTrue(entered.wait(1))
            finally:
                release.set()
                with session.condition:
                    self.assertTrue(session.condition.wait_for(lambda: not session.notification_connecting, timeout=2))
        self.assertEqual(session.origin, 'cli')
        self.assertTrue(self.observe().connected)
        self.assertFalse(session.connected)
        self.bridge.ipc.connect.assert_not_called()

    def test_cli_terminal_event_wakes_a_dormant_notification_without_metadata_change(self):
        self.scan()
        self.assertIn(('local', self.thread_id), self.manager.dormant)
        live = self.bridge.session(self.thread_id, attach=False)
        live.origin, live.runtime, live.connected = 'cli', 'cli', True
        self.bridge.notification_dirty.clear()
        state = {**self.native_state, 'turns': [{'turnId': 'new', 'status': 'completed'}]}
        self.bridge._cli_state(self.thread_id, state)
        self.assertEqual(self.bridge.notification_updates(), [{'id': self.thread_id, 'host': 'local'}])

    def test_read_rejects_a_changed_process_or_saved_working_directory(self):
        original = self.fake.request
        def changed(method, params, **kwargs):
            result = original(method, params, **kwargs)
            self.fake.process = object()
            return result
        self.fake.request = changed
        with self.assertRaises(AppServerUnavailable):
            self.native.observe(self.thread_id)
        self.fake.request = original
        self.fake.thread['cwd'] = '/wrong/project'
        with self.assertRaises(AppServerUnsupported):
            self.native.observe(self.thread_id)

    def test_unloaded_observation_retires_without_renewing_the_probe_window(self):
        self.fake.loaded = False
        session = self.observe()
        deadline = session.notification_probe_until
        self.bridge.notification_session(self.thread_id)
        self.assertEqual(session.notification_probe_until, deadline)
        session.notification_probe_until = time.monotonic() - 1
        self.manager.scan()
        self.assertIn(('local', self.thread_id), self.manager.dormant)
        self.assertFalse(session.watched)

    def test_same_thread_id_in_local_instance_and_ssh_has_distinct_event_identity(self):
        sources = [self.bridge]
        for index, host in enumerate(('codex:' + '1'*32, 'remote-ssh:fixture')):
            home = self.home/('source-' + str(index)); home.mkdir()
            with closing(sqlite3.connect(self.home/'state_5.sqlite')) as original, \
                    closing(sqlite3.connect(home/'state_5.sqlite')) as target:
                original.backup(target)
            source = Bridge(home, home/'data', host=host, alias='saved-host' if host.startswith('remote') else None,
                            codex_bin=home/'no-app')
            self.addCleanup(source.close)
            self.install_native(source, home, alias='saved-host' if host.startswith('remote') else None)
            sources.append(source)
        mapping = {source.host: source for source in sources}
        aggregate = SimpleNamespace(notification_event=threading.Event(), for_host=mapping.__getitem__,
            notification_candidates=lambda: [{'id': self.thread_id, 'host': host} for host in mapping],
            notification_updates=lambda: [])
        self.manager.bridge = aggregate
        for source in sources:
            self.observe(source)
        self.manager.scan()
        for source in sources:
            source.cli_sessions.client.thread['turns'] = [{'id': 'same-turn', 'status': 'completed', 'items': []}]
            self.observe(source)
        self.manager.discovery_at = 0
        self.manager.scan()
        events = self.manager.mobile.read()['events']
        self.assertEqual({event['host'] for event in events}, set(mapping))
        self.assertEqual(len({event['id'] for event in events}), 3)
        self.assertTrue(all(event['threadId'] == self.thread_id for event in events))
