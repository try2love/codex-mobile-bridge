import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.ipc import IPCError
from bridge.app.service import Bridge
from bridge.features.sessions.store import SessionStore, StoreUnavailable

ROOT = Path(__file__).resolve().parents[1]


class DesktopIdle(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temporary.cleanup)
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.host = 'local'; self.bridge.codex_home = Path(temporary.name)
        self.bridge.hosts = SimpleNamespace(hosts=Mock(return_value={}))
        self.bridge.lock = threading.RLock(); self.bridge.live = {}
        self.bridge.store = SimpleNamespace(lifecycle_threads=Mock(return_value=[{'id': 'desktop-only'}]), history=Mock())
        self.bridge.ipc = SimpleNamespace(path='fixture-ipc')
        self.bridge.remote_bridges = {}; self.bridge.accounts = SimpleNamespace(blockers=Mock(return_value=[]))
        self.bridge.closed = threading.Event()
        self.bridge.activate = Mock(side_effect=AssertionError('idle checks cannot activate chats'))
        self.calls = []; self.owners = {}; self.states = {}; self.instances = []
        self.after_follow = lambda sid, follower: None
        self.transform_event = lambda message: message
        parent = self
        class Follower:
            def __init__(self, path, on_event=None, on_disconnect=None):
                self.on_event = on_event; self.on_disconnect = on_disconnect; self.closed = False
                parent.instances.append(self)
            def connect(self): parent.calls.append(('connect',))
            def request(self, method, params, host='local', **kwargs):
                parent.calls.append((method, host, params['conversationId']))
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
            def close(self): self.closed = True
        self.factory = patch('bridge.app.service.DesktopIPC', Follower)
        self.factory.start(); self.addCleanup(self.factory.stop)

    def test_unfollowed_desktop_busy_thread_prevents_shutdown(self):
        self.states[('local', 'desktop-only')] = {'id': 'desktop-only', 'threadRuntimeStatus': {'type': 'active'}, 'turns': [], 'requests': []}
        with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()
        self.assertIn(('follow', 'local', 'desktop-only', 'owner'), self.calls)
        self.assertEqual(self.bridge.live, {})
        self.bridge.activate.assert_not_called(); self.bridge.store.history.assert_not_called()
        self.assertTrue(all(instance.closed for instance in self.instances))

    def test_exact_no_client_found_is_absence_not_stale_history_activity(self):
        self.owners[('local', 'desktop-only')] = IPCError('no-client-found')
        self.bridge.live['desktop-only'] = SimpleNamespace(connected=False, state={'turns': [{'status': 'inProgress'}]})
        self.bridge.assert_desktop_idle()
        self.assertFalse(any(call[0] == 'follow' for call in self.calls))
        self.bridge.store.history.assert_not_called()

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
        self.bridge.hosts.hosts.return_value = {'remote:test': {'alias': 'test'}}
        self.bridge.for_host = Mock(return_value=remote)
        self.bridge.remote_bridges['remote:test'] = remote
        self.states[('remote:test', 'remote-live')] = {'id': 'remote-live', 'threadRuntimeStatus': {'type': 'active'}}
        with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()
        self.assertIn(('follow', 'remote:test', 'remote-live', 'owner'), self.calls)
        self.assertIn(('thread-owner-discovery', 'remote:test', 'remote-history'), self.calls)

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
        self.assertEqual([row['id'] for row in self.bridge.store.list()], ['parent'])
        self.assertEqual([row['id'] for row in self.bridge.store.lifecycle_threads()], ['child', 'grandchild', 'parent'])
        self.states[('local', 'grandchild')] = {'id': 'grandchild', 'threadRuntimeStatus': {'type': 'active'}}
        with patch.object(self.bridge.store, 'history', side_effect=AssertionError('Do not read history')):
            with self.assertRaisesRegex(ValueError, '任务运行'): self.bridge.assert_desktop_idle()
        self.assertIn(('follow', 'local', 'grandchild', 'owner'), self.calls)
