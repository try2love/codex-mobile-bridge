import copy
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.ipc import IPCError
from bridge.clients.errors import TaskStateUnavailable
from bridge.app.service import Bridge
from bridge.features.sessions.store import SessionStore, StoreUnavailable
from bridge.features.accounts.accounts import Accounts

ROOT = Path(__file__).resolve().parents[1]


class DesktopIdle(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temporary.cleanup)
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.host = 'local'; self.bridge.codex_home = Path(temporary.name)
        self.bridge.hosts = SimpleNamespace(lifecycle_hosts=Mock(return_value={}))
        self.bridge.lock = threading.RLock(); self.bridge.live = {}
        self.bridge.store = SimpleNamespace(lifecycle_threads=Mock(return_value=[{'id': 'desktop-only'}]), history=Mock())
        self.bridge.ipc = SimpleNamespace(path='fixture-ipc')
        self.bridge.remote_bridges = {}; self.bridge.accounts = SimpleNamespace(blockers=Mock(return_value=[]))
        self.bridge.closed = threading.Event()
        self.bridge.activate = Mock(side_effect=AssertionError('idle checks cannot activate chats'))
        self.calls = []; self.owners = {}; self.states = {}; self.instances = []
        self.after_follow = lambda sid, follower: None
        self.transform_event = lambda message: message
        self.before_request = lambda sid, options: None
        self.before_connect = lambda options: None
        self.after_close = lambda: None
        parent = self
        class Follower:
            def __init__(self, path, on_event=None, on_disconnect=None):
                self.on_event = on_event; self.on_disconnect = on_disconnect; self.closed = False
                parent.instances.append(self)
            def connect(self, **kwargs):
                parent.calls.append(('connect',))
                parent.before_connect(kwargs)
            def request(self, method, params, host='local', **kwargs):
                parent.calls.append((method, host, params['conversationId']))
                parent.before_request(params['conversationId'], kwargs)
                value = parent.owners.get((host, params['conversationId']), 'owner')
                if isinstance(value, Exception): raise value
                return {'handledByClientId': value}
            def follow(self, sid, owner, enabled=True, host='local'):
                parent.calls.append(('follow', host, sid, owner))
                state = parent.states.get((host, sid), {'id': sid, 'threadRuntimeStatus': {'type': 'idle'}, 'turns': [], 'requests': []})
                if state is None: return
                self.on_event(parent.transform_event({'method': 'thread-stream-state-changed', 'version': 11, 'sourceClientId': owner,
                               'params': {'hostId': host, 'conversationId': sid,
                                          'change': {'type': 'snapshot', 'revision': 1, 'conversationState': copy.deepcopy(state)}}}))
                parent.after_follow(sid, self)
            def close(self):
                self.closed = True
                parent.after_close()
        self.factory = patch('bridge.app.service.DesktopIPC', Follower)
        self.factory.start(); self.addCleanup(self.factory.stop)

    def test_unfollowed_desktop_busy_thread_prevents_shutdown(self):
        self.states[('local', 'desktop-only')] = {'id': 'desktop-only', 'threadRuntimeStatus': {'type': 'active'}, 'turns': [], 'requests': []}
        with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()
        self.assertIn(('follow', 'local', 'desktop-only', 'owner'), self.calls)
        self.assertEqual(self.bridge.live, {})
        self.bridge.activate.assert_not_called(); self.bridge.store.history.assert_not_called()
        self.assertTrue(all(instance.closed for instance in self.instances))

    def test_601_history_candidates_do_not_serialize_a_silent_owner_before_tail_busy(self):
        identifiers = [f'history-{index:04}' for index in range(600)] + ['tail-busy']
        self.bridge.store.lifecycle_threads.return_value = [{'id': sid} for sid in identifiers]
        self.owners = {('local', sid): IPCError('no-client-found') for sid in identifiers[:-1]}
        self.states[('local', 'tail-busy')] = {'id': 'tail-busy', 'threadRuntimeStatus': {'type': 'active'}}
        tail_seen = threading.Event()
        first_finished = threading.Event()
        overlapping = []
        def before(sid, options):
            if sid == identifiers[0]:
                overlapping.append(tail_seen.wait(1))
                first_finished.set()
            elif sid == 'tail-busy':
                tail_seen.set()
        self.before_request = before
        with self.assertRaisesRegex(ValueError, '任务运行'):
            self.bridge.assert_desktop_idle()
        self.assertTrue(first_finished.wait(1))
        self.assertEqual(overlapping, [True], 'A silent historical owner must not hold up the other candidates')
        self.assertEqual({call[2] for call in self.calls if call[0] == 'thread-owner-discovery'}, set(identifiers))
        self.bridge.activate.assert_not_called()
        self.bridge.store.history.assert_not_called()

    def test_silent_request_cannot_overrun_the_absolute_audit_deadline(self):
        release, finished = threading.Event(), threading.Event()
        def before(sid, options):
            try:
                release.wait(1)
            finally:
                finished.set()
        self.before_request = before
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(ValueError, '超时'):
                self.bridge.assert_desktop_idle(timeout=.04)
            self.assertLess(time.monotonic()-started, .4)
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
        self.assertTrue(all(instance.closed for instance in self.instances))

    def test_discovery_has_only_eight_requests_in_flight(self):
        identifiers = [f'history-{index:04}' for index in range(24)]
        self.bridge.store.lifecycle_threads.return_value = [{'id': sid} for sid in identifiers]
        self.owners = {('local', sid): IPCError('no-client-found') for sid in identifiers}
        lock, first_batch = threading.Lock(), threading.Event()
        in_flight, maximum = 0, 0
        def before(sid, options):
            nonlocal in_flight, maximum
            with lock:
                in_flight += 1
                maximum = max(maximum, in_flight)
                if in_flight == 8:
                    first_batch.set()
            first_batch.wait(1)
            with lock:
                in_flight -= 1
        self.before_request = before
        self.bridge.assert_desktop_idle()
        self.assertTrue(first_batch.is_set())
        self.assertEqual(maximum, 8)

    def test_initialization_scope_is_used_for_each_submission_recheck(self):
        self.bridge.accounts.blockers.side_effect = [[], [], [{'reason': 'unknown'}]]
        with self.assertRaisesRegex(ValueError, '操作'):
            self.bridge.assert_desktop_idle(desktop_only=True)
        self.assertEqual(self.bridge.accounts.blockers.call_count, 3)

    def native_cli(self):
        identifier = 'native-fixture'
        manager = Accounts.__new__(Accounts)
        manager.bridge = self.bridge
        self.bridge.accounts = manager
        process = Mock(poll=Mock(return_value=None))
        self.bridge.submissions = {}
        self.bridge.cli_sessions = SimpleNamespace(home=self.bridge.codex_home, alias=None,
            lock=threading.RLock(), joined={identifier: process},
            metadata={identifier: {'connectionToken': 'native-generation'}},
            client=SimpleNamespace(process=process, server_info={'codexHome': str(self.bridge.codex_home)}))
        session = SimpleNamespace(origin='cli', runtime='cli', connected=True, owner='cli:' + identifier,
            desktop_owner_probe=None, view=lambda: {'connected': True, 'status': 'active', 'title': 'Native fixture'})
        self.bridge.live[identifier] = session
        return identifier, session

    def test_native_cli_history_overlap_requires_fresh_absence_and_does_not_treat_cli_marker_as_desktop_owner(self):
        identifier, session = self.native_cli()
        self.bridge.store.lifecycle_threads.return_value = [{'id': identifier}]
        self.owners[('local', identifier)] = IPCError('no-client-found')
        for probe in (None, 'unknown', 'absent', 'found'):
            session.desktop_owner_probe = probe
            self.calls.clear()
            self.bridge.assert_desktop_idle(desktop_only=True)
            self.assertEqual([call for call in self.calls if call[0] == 'thread-owner-discovery'],
                             [('thread-owner-discovery', 'local', identifier)] * 2)
        self.assertFalse(any(call[0] == 'follow' for call in self.calls))

    def test_native_cli_missing_from_history_is_still_audited_and_unknown_owner_never_exempts_it(self):
        identifier, session = self.native_cli()
        self.bridge.store.lifecycle_threads.return_value = []
        for probe in (None, 'unknown', 'absent'):
            session.desktop_owner_probe = probe
            self.owners[('local', identifier)] = IPCError('读取超时')
            with self.assertRaises(TaskStateUnavailable):
                self.bridge.assert_desktop_idle(desktop_only=True)
        self.owners[('local', identifier)] = 'app-owner'
        self.states[('local', identifier)] = {'id': identifier, 'threadRuntimeStatus': {'type': 'active'}}
        with self.assertRaisesRegex(ValueError, '任务运行'):
            self.bridge.assert_desktop_idle(desktop_only=True)

    def test_native_cli_absence_and_proxy_identity_are_rechecked_before_finishing(self):
        identifier, session = self.native_cli()
        self.bridge.store.lifecycle_threads.return_value = [{'id': identifier}]
        self.owners[('local', identifier)] = IPCError('no-client-found')
        requests = []
        def before(sid, options):
            requests.append(sid)
            if len(requests) == 2:
                self.owners[('local', identifier)] = IPCError('读取超时')
        self.before_request = before
        with self.assertRaises(TaskStateUnavailable):
            self.bridge.assert_desktop_idle(desktop_only=True)
        self.assertEqual(requests, [identifier, identifier])
        self.owners[('local', identifier)] = IPCError('no-client-found')
        self.before_request = lambda sid, options: self.bridge.cli_sessions.metadata[identifier].update(connectionToken='changed')
        with self.assertRaises(ValueError):
            self.bridge.assert_desktop_idle(desktop_only=True)

    def test_blocked_initialize_send_is_closed_at_deadline_without_touching_existing_ipc(self):
        release, finished = threading.Event(), threading.Event()
        original = self.bridge.ipc
        original.close = Mock()
        def connect(options):
            try:
                release.wait(1)
            finally:
                finished.set()
        self.before_connect = connect
        self.after_close = release.set
        started = time.monotonic()
        with self.assertRaises(TaskStateUnavailable):
            self.bridge.assert_desktop_idle(timeout=.04)
        self.assertLess(time.monotonic()-started, .4)
        self.assertTrue(finished.wait(1))
        self.assertEqual(self.calls, [('connect',)])
        self.assertIs(self.bridge.ipc, original)
        original.close.assert_not_called()

    def test_exact_no_client_found_is_absence_not_stale_history_activity(self):
        self.owners[('local', 'desktop-only')] = IPCError('no-client-found')
        self.bridge.live['desktop-only'] = SimpleNamespace(connected=False, state={'turns': [{'status': 'inProgress'}]})
        self.bridge.assert_desktop_idle()
        self.assertFalse(any(call[0] == 'follow' for call in self.calls))
        self.bridge.store.history.assert_not_called()

    def test_previous_owner_missing_is_unknown_not_an_unloaded_historical_chat(self):
        self.owners[('local', 'desktop-only')] = IPCError('no-client-found')
        self.bridge.live['desktop-only'] = SimpleNamespace(owner='previous-owner', connected=False)
        with self.assertRaises(TaskStateUnavailable):
            self.bridge.assert_desktop_idle()

    def test_unavailable_owner_or_snapshot_does_not_hide_later_busy_owner(self):
        self.bridge.store.lifecycle_threads.return_value = [{'id': 'a-unavailable'}, {'id': 'z-busy'}]
        self.bridge.live['a-unavailable'] = SimpleNamespace(owner='previous-owner', connected=False)
        self.states[('local', 'z-busy')] = {'id': 'z-busy', 'threadRuntimeStatus': {'type': 'active'}}
        clock = [0.0]
        metadata = Mock(result=Mock(return_value=self.bridge.store.lifecycle_threads.return_value))
        executor = Mock(submit=Mock(return_value=metadata))
        class SnapshotCondition(threading.Condition):
            def wait_for(self, predicate, timeout=None):
                if predicate(): return True
                clock[0] += timeout
                return False
        for owner, state in ((IPCError('no-client-found'), None), (IPCError('timed out'), None),
                             ('', None), ('owner', None), ('owner', {'id': 'a-unavailable'})):
            with self.subTest(owner=str(owner), state=state):
                clock[0] = 0.0
                self.calls.clear()
                self.owners[('local', 'a-unavailable')] = owner
                self.states[('local', 'a-unavailable')] = state
                with patch('bridge.app.service.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                     patch('bridge.app.service.ThreadPoolExecutor', side_effect=[executor, ThreadPoolExecutor(max_workers=2)]), \
                     patch('bridge.app.service.threading', SimpleNamespace(Condition=SnapshotCondition)), \
                     self.assertRaisesRegex(ValueError, '任务运行') as failure:
                    self.bridge.assert_desktop_idle(timeout=.2)
                self.assertNotIsInstance(failure.exception, TaskStateUnavailable)
                self.assertIn(('follow', 'local', 'z-busy', 'owner'), self.calls)

    def test_slow_remote_metadata_reserves_time_for_local_busy_check(self):
        clock = [0.0]
        remote = SimpleNamespace(store=SimpleNamespace(lifecycle_threads=Mock()))
        self.bridge.hosts.lifecycle_hosts.return_value = {'remote:test': {'alias': 'test'}}
        self.bridge.remote_bridges['remote:test'] = remote
        self.states[('local', 'desktop-only')] = {'id': 'desktop-only', 'threadRuntimeStatus': {'type': 'active'}}
        local_read = Mock(result=Mock(return_value=[{'id': 'desktop-only'}]))
        def time_out(*, timeout):
            clock[0] += timeout
            raise TimeoutError('remote metadata did not arrive')
        remote_read = Mock(result=Mock(side_effect=time_out))
        executor = Mock(submit=Mock(side_effect=[local_read, remote_read]))
        # Advance the operation's clock at the remote timeout rather than
        # depending on a loaded CI runner scheduling threads within 200 ms.
        with patch('bridge.app.service.ThreadPoolExecutor', side_effect=[executor, ThreadPoolExecutor(max_workers=1)]), \
             patch('bridge.app.service.time', SimpleNamespace(monotonic=lambda: clock[0])):
            with self.assertRaisesRegex(ValueError, '任务运行') as failure:
                self.bridge.assert_desktop_idle(timeout=20)
            self.assertNotIsInstance(failure.exception, TaskStateUnavailable)
        self.assertEqual(clock[0], 10)
        self.assertIn(('follow', 'local', 'desktop-only', 'owner'), self.calls)

    def test_failed_metadata_still_checks_known_live_ids_with_fresh_state(self):
        self.bridge.store.lifecycle_threads.side_effect = OSError('temporary database failure')
        self.bridge.live['known-session'] = SimpleNamespace(owner='previous-owner', connected=False)
        self.states[('local', 'known-session')] = {'id': 'known-session', 'threadRuntimeStatus': {'type': 'active'}}
        with self.assertRaisesRegex(ValueError, '任务运行') as failure:
            self.bridge.assert_desktop_idle()
        self.assertNotIsInstance(failure.exception, TaskStateUnavailable)
        self.assertIn(('follow', 'local', 'known-session', 'owner'), self.calls)

    def test_other_owner_errors_or_missing_identity_never_mean_idle(self):
        for value in (IPCError('timed out'), IPCError('no-client-found: unavailable'), None, ''):
            with self.subTest(value=str(value)):
                self.owners[('local', 'desktop-only')] = value
                with self.assertRaisesRegex(ValueError, '无法确认'): self.bridge.assert_desktop_idle()

    def test_pending_approval_or_turn_overrides_idle_runtime(self):
        for update in ({'requests': [{'id': 1, 'method': 'item/commandExecution/requestApproval', 'params': {}}]},
                       {'turns': [{'turnId': 'turn', 'status': 'inProgress', 'items': []}]}):
            self.states[('local', 'desktop-only')] = {'id': 'desktop-only', 'threadRuntimeStatus': {'type': 'idle'}, **update}
            with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()

    def test_missing_runtime_or_snapshot_is_unknown(self):
        for state in ({'id': 'desktop-only', 'turns': [], 'requests': []}, None):
            self.states[('local', 'desktop-only')] = state
            with self.assertRaises(ValueError): self.bridge.assert_desktop_idle(timeout=.02)

    def test_metadata_failure_and_unresolved_submission_block_before_following(self):
        self.bridge.store.lifecycle_threads.side_effect = StoreUnavailable('fixture')
        with self.assertRaisesRegex(ValueError, '读取'): self.bridge.assert_desktop_idle()
        self.bridge.store.lifecycle_threads.side_effect = None
        self.bridge.accounts.blockers.return_value = [{'reason': 'unknown'}]
        with self.assertRaisesRegex(ValueError, '操作'): self.bridge.assert_desktop_idle()
        self.assertEqual(self.calls, [])

    def test_remote_and_live_only_threads_are_checked_with_their_original_host(self):
        remote = SimpleNamespace(host='remote:test', lock=threading.RLock(), live={'remote-live': object()},
                                 store=SimpleNamespace(lifecycle_threads=Mock(return_value=[{'id': 'remote-history'}])),
                                 ipc=SimpleNamespace(path='fixture-ipc'))
        self.bridge.hosts.lifecycle_hosts.return_value = {'remote:test': {'alias': 'test'}}
        self.bridge.for_host = Mock(return_value=remote)
        self.bridge.remote_bridges['remote:test'] = remote
        self.states[('remote:test', 'remote-live')] = {'id': 'remote-live', 'threadRuntimeStatus': {'type': 'active'}}
        with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()
        self.assertIn(('follow', 'remote:test', 'remote-live', 'owner'), self.calls)
        self.assertIn(('thread-owner-discovery', 'remote:test', 'remote-history'), self.calls)

    def test_offline_ssh_does_not_hide_fresh_local_busy_evidence_before_recovery_exit(self):
        remote = SimpleNamespace(store=SimpleNamespace(lifecycle_threads=Mock(side_effect=OSError('offline'))))
        self.bridge.hosts.lifecycle_hosts.return_value = {'remote:test': {'alias': 'test'}}
        self.bridge.remote_bridges['remote:test'] = remote
        self.states[('local', 'desktop-only')] = {'id': 'desktop-only', 'threadRuntimeStatus': {'type': 'active'}}
        with self.assertRaisesRegex(ValueError, '任务运行') as failure:
            self.bridge.assert_desktop_idle()
        self.assertNotIsInstance(failure.exception, TaskStateUnavailable)
        self.assertIn(('follow', 'local', 'desktop-only', 'owner'), self.calls)

    def test_all_fresh_idle_snapshots_allow_shutdown_without_activating(self):
        self.bridge.assert_desktop_idle()
        self.assertEqual(self.bridge.live, {})
        self.bridge.activate.assert_not_called()
        self.assertTrue(all(instance.closed for instance in self.instances))

    def test_task_becoming_active_while_other_sessions_are_audited_blocks_shutdown(self):
        self.bridge.store.lifecycle_threads.return_value = [{'id': 'a'}, {'id': 'b'}]
        def update(sid, follower):
            if sid == 'b':
                follower.on_event({'method': 'thread-stream-state-changed', 'version': 11, 'sourceClientId': 'owner',
                                   'params': {'hostId': 'local', 'conversationId': 'a', 'change': {
                                       'type': 'patches', 'baseRevision': 1, 'revision': 2,
                                       'patches': [{'op': 'replace', 'path': ['threadRuntimeStatus', 'type'], 'value': 'active'}]}}})
        self.after_follow = update
        with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()

    def test_owner_disconnect_after_idle_snapshot_is_unknown(self):
        self.after_follow = lambda sid, follower: follower.on_event({
            'method': 'client-status-changed', 'params': {'status': 'disconnected', 'clientId': 'owner'}})
        with self.assertRaisesRegex(ValueError, '无法确认'): self.bridge.assert_desktop_idle()

    def test_snapshot_from_wrong_owner_protocol_or_session_cannot_establish_idle(self):
        def wrong_session(message):
            message['params']['change']['conversationState']['id'] = 'other'
            return message
        for transform in (lambda message: {**message, 'version': 10},
                          lambda message: {**message, 'sourceClientId': 'other'}, wrong_session):
            with self.subTest(transform=transform):
                self.transform_event = transform
                with self.assertRaisesRegex(ValueError, '无法确认'): self.bridge.assert_desktop_idle()

    def test_unresolved_submission_arriving_during_audit_prevents_shutdown(self):
        self.bridge.accounts.blockers.side_effect = [[], [], [{'reason': 'unknown'}]]
        with self.assertRaisesRegex(ValueError, '操作'): self.bridge.assert_desktop_idle()
        self.assertTrue(all(instance.closed for instance in self.instances))

    def test_corrupted_global_metadata_does_not_silently_remove_remote_coverage(self):
        (self.bridge.codex_home/'.codex-global-state.json').write_text('{broken')
        with self.assertRaisesRegex(ValueError, '读取'): self.bridge.assert_desktop_idle()
        self.assertEqual(self.calls, [])

    def test_slow_metadata_read_is_bounded_without_following_partial_results(self):
        release = threading.Event()
        self.bridge.store.lifecycle_threads.side_effect = lambda: release.wait(2) or []
        try:
            with self.assertRaisesRegex(ValueError, '读取'): self.bridge.assert_desktop_idle(timeout=.02)
            self.assertFalse(release.is_set())
            self.assertEqual(self.calls, [])
        finally:
            release.set()

    def test_busy_nested_desktop_subagent_is_checked_without_exposing_it_in_chat_list(self):
        path = self.bridge.codex_home/'state_5.sqlite'
        database = sqlite3.connect(path)
        database.execute('CREATE TABLE threads(id TEXT, originator TEXT, source TEXT, archived INT, thread_source TEXT, updated_at INT)')
        def child(parent):
            return json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': parent}}})
        database.executemany('INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)', [
            ('parent', 'Codex Desktop', 'vscode', 0, '', 1),
            ('child', 'codex_exec', child('parent'), 1, 'subagent', 1),
            ('grandchild', 'codex_exec', child('child'), 1, 'subagent', 1),
            ('cli', 'codex_cli_rs', 'cli', 0, '', 1),
            ('cli-child', 'codex_exec', child('cli'), 0, 'subagent', 1)])
        database.commit(); database.close()
        self.bridge.store = SessionStore(self.bridge.codex_home)
        self.assertEqual({row['id'] for row in self.bridge.store.list()}, {'parent', 'cli'})
        self.assertEqual([row['id'] for row in self.bridge.store.lifecycle_threads()], ['child', 'grandchild', 'parent'])
        self.states[('local', 'grandchild')] = {'id': 'grandchild', 'threadRuntimeStatus': {'type': 'active'}}
        with patch.object(self.bridge.store, 'history', side_effect=AssertionError('Do not read history')):
            with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()
        self.assertIn(('follow', 'local', 'grandchild', 'owner'), self.calls)
