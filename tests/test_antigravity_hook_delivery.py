import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.hook_ipc import HookIPCServer, _write_frame


class HookDeliveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.addCleanup(folder.cleanup)
        with mock.patch.object(HookIPCServer, '_start_server'):
            self.server = HookIPCServer(folder.name, session_resolver=lambda payload: 'chat')
        self.addCleanup(self.server.close)

    def failed_reply(self, failure):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        _write_frame(client, {'token': self.server.token, 'event': 'post_invocation', 'payload': {}})
        client.shutdown(socket.SHUT_WR)
        with mock.patch('bridge.clients.antigravity.hook_ipc._write_frame', side_effect=failure):
            self.server._handle_client(server)

    def test_reply_failure_cannot_restore_concurrently_delivered_message(self):
        self.server.enqueue_follow_up('chat', 'delivered by another Hook', mode='steer')
        dispatch = self.server._dispatch_resolved
        def other_client_then_this(*args):
            self.assertIn('injectSteps', dispatch(*args))
            return dispatch(*args)
        with mock.patch.object(self.server, '_dispatch_resolved', side_effect=other_client_then_this):
            self.failed_reply(OSError('broken pipe'))
        self.assertEqual(self.server.get_queued_items('chat'), [])

    def test_reply_failure_cannot_restore_concurrently_cancelled_or_edited_message(self):
        for operation in ('cancel', 'edit'):
            with self.subTest(operation=operation):
                old = self.server.enqueue_follow_up('chat', operation, mode='steer')
                dispatch = self.server._dispatch_resolved
                def remove_then_dispatch(*args):
                    self.server.remove_queued_item('chat', old['id'])
                    return dispatch(*args)
                with mock.patch.object(self.server, '_dispatch_resolved', side_effect=remove_then_dispatch):
                    self.failed_reply(OSError('broken pipe'))
                self.assertEqual(self.server.get_queued_items('chat'), [])

    def test_partial_reply_does_not_replay_consumed_items_and_keeps_unselected_queue(self):
        self.server.enqueue_follow_up('chat', 'possibly delivered', mode='steer')
        queued = self.server.enqueue_follow_up('chat', 'not selected', mode='queue')
        def partial(conn, value):
            self.assertIn('injectSteps', value)
            conn.sendall(b'\0\0')
            raise OSError('reply outcome unknown')
        self.failed_reply(partial)
        self.assertEqual([x['id'] for x in self.server.get_queued_items('chat')], [queued['id']])
        self.assertEqual(json.loads(self.server.queues_file.read_text())['chat'][0]['id'], queued['id'])
        self.assertEqual(self.server.dispatch_hook_event('post_invocation', {}), {})
