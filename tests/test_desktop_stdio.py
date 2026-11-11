import json
import io
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.desktop_stdio import DesktopStdio
from bridge.clients.codex.app_server import AppServerUnavailable, AppServerUnknownResult, AppServerRequestNotSent
from bridge.clients.codex.desktop_runtime_host import _forward_input
from bridge.clients.codex.desktop_runtime_windows import MetadataClient
from bridge.clients.local_rpc import LocalRequestNotSent
from bridge.features.sessions.create import DeleteRejected


class DesktopStdioTests(unittest.TestCase):
    def setUp(self):
        self.writes, self.app, self.initialized = [], [], []
        self.changed = threading.Event()
        def write(data):
            self.writes.append(json.loads(data)); self.changed.set()
        self.router = DesktopStdio(write, self.app.append, self.initialized.append)
        self.addCleanup(self.router.close)

    def ready(self):
        self.router.from_app({'method': 'initialize', 'id': 1, 'params': {'clientInfo': {'name': 'Codex Desktop'}}})
        self.router.from_native({'id': self.writes[-1]['id'], 'result': {'codexHome': 'home'}})
        self.assertEqual(self.app, [{'id': 1, 'result': {'codexHome': 'home'}}])
        self.assertFalse(self.router.ready)
        self.router.from_app({'method': 'initialized'})
        self.assertTrue(self.router.ready)
        self.assertEqual(self.initialized, [{'codexHome': 'home'}])

    def test_only_app_initializes_and_bridge_cannot_execute_turns(self):
        with self.assertRaises(AppServerUnavailable): self.router.request_packet('thread/read', {})
        self.ready()
        with self.assertRaises(AppServerRequestNotSent): self.router.request_packet('turn/start', {})
        with self.assertRaises(AppServerRequestNotSent): self.router.request_packet('initialize', {})
        self.assertEqual(len(self.writes), 2)

    def test_app_requests_and_native_approvals_keep_their_original_ids(self):
        self.ready()
        self.router.from_app({'id': 1, 'method': 'thread/read', 'params': {'threadId': 'app-thread'}})
        outbound = self.writes[-1]['id']
        approval = {'id': outbound, 'method': 'item/commandExecution/requestApproval', 'params': {}}
        self.router.from_native(approval)
        self.router.from_app({'id': outbound, 'result': {'decision': 'decline'}})
        self.assertEqual(self.writes[-1], {'id': outbound, 'result': {'decision': 'decline'}})
        self.router.from_native({'id': outbound, 'result': {'thread': 'app-thread'}})
        self.assertEqual(self.app[-2:], [approval, {'id': 1, 'result': {'thread': 'app-thread'}}])

    def test_bridge_reply_is_private_and_notifications_still_reach_app(self):
        self.ready(); self.changed.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.router.request_packet, 'thread/fork', {'threadId': 'parent'})
            self.assertTrue(self.changed.wait(1))
            identifier = self.writes[-1]['id']
            notification = {'method': 'thread/started', 'params': {'thread': {'id': 'child'}}}
            self.router.from_native(notification)
            self.router.from_native({'id': identifier, 'result': {'thread': {'id': 'child'}}})
            self.assertEqual(pending.result(1)['result'], {'thread': {'id': 'child'}})
        self.assertEqual(self.app[-1], notification)
        self.assertEqual(len(self.app), 2)

    def test_timeout_never_replays_or_delivers_late_reply_to_app(self):
        self.ready()
        with self.assertRaises(AppServerUnknownResult): self.router.request_packet('thread/fork', {}, timeout=.001)
        self.router.from_native({'id': self.writes[-1]['id'], 'result': {'thread': {'id': 'late'}}})
        self.assertEqual(len(self.writes), 3)
        self.assertEqual(len(self.app), 1)

    def test_close_wakes_inflight_bridge_without_replaying(self):
        self.ready(); self.changed.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.router.request_packet, 'thread/fork', {})
            self.assertTrue(self.changed.wait(1)); self.router.close()
            with self.assertRaises(AppServerUnknownResult): pending.result(1)
        self.assertEqual(len(self.writes), 3)

    def test_id_expansion_rejects_only_large_app_request_and_drops_pending(self):
        self.ready()
        too_large = {'id': 7, 'method': 'thread/read', 'params': {'threadId': 'x' * 160}}
        valid = {'id': 8, 'method': 'thread/read', 'params': {'threadId': 'short'}}
        data = b''.join(json.dumps(value).encode()+b'\n' for value in (too_large, valid))
        with patch('bridge.clients.codex.desktop_stdio.MAX_LINE', 230):
            _forward_input(io.BytesIO(data), SimpleNamespace(send=self.router.from_app), self.app.append, threading.Event())
        self.assertTrue(self.router.ready)
        self.assertEqual(self.app[-1]['id'], 7)
        self.assertIn('error', self.app[-1])
        self.assertEqual(len(self.router.pending), 1)
        self.router.from_native({'id': self.writes[-1]['id'], 'result': {'ok': True}})
        self.assertEqual(self.app[-1], {'id': 8, 'result': {'ok': True}})


class MetadataClientTests(unittest.TestCase):
    def test_explicit_native_rejection_is_distinct_from_a_missing_reply(self):
        client = MetadataClient('.', {'generation': 'fixture'})
        client.service = Mock()
        with patch('bridge.clients.codex.desktop_runtime_windows._verify'):
            client.service._exchange.return_value = {'id': 'native-id', 'error': {'code': -32601, 'message': 'unsupported'}}
            with self.assertRaises(DeleteRejected): client.request('thread/delete', {'threadId': 'fixture'})
            client.service._exchange.side_effect = LocalRequestNotSent('peer changed')
            with self.assertRaises(AppServerRequestNotSent): client.request('thread/fork', {})
            client.service._exchange.side_effect = EOFError('reply missing')
            with self.assertRaises(AppServerUnknownResult): client.request('thread/fork', {})
