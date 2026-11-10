"""Lifecycle reads share one absolute deadline, without the mutation grace wait."""
import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.ipc import DesktopIPC, IPCError


class DeadlineTests(unittest.TestCase):
    def test_close_cancels_a_late_connect_before_it_can_initialize(self):
        entered, release = threading.Event(), threading.Event()
        stream = Mock()
        ipc = DesktopIPC('fixture')
        ipc.request = Mock()
        def connect(*args, **kwargs):
            entered.set()
            release.wait(1)
            return stream
        with patch('bridge.clients.codex.ipc.connect_stream', side_effect=connect), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ipc.connect, deadline=time.monotonic()+1)
            self.assertTrue(entered.wait(1))
            ipc.close()
            release.set()
            with self.assertRaises(IPCError):
                future.result(timeout=1)
        self.assertIsNone(ipc.socket)
        self.assertIsNone(ipc.client_id)
        ipc.request.assert_not_called()
        stream.close.assert_called_once()

    def test_late_initialize_response_cannot_restore_a_closed_connection(self):
        entered, release = threading.Event(), threading.Event()
        stream = Mock()
        ipc = DesktopIPC('fixture')
        ipc._read_loop = Mock()
        def initialize(*args, **kwargs):
            entered.set()
            release.wait(1)
            return {'result': {'clientId': 'late-client'}}
        ipc.request = Mock(side_effect=initialize)
        with patch('bridge.clients.codex.ipc.connect_stream', return_value=stream), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ipc.connect, deadline=time.monotonic()+1)
            self.assertTrue(entered.wait(1))
            ipc.close()
            release.set()
            with self.assertRaises(IPCError):
                future.result(timeout=1)
        self.assertIsNone(ipc.socket)
        self.assertIsNone(ipc.client_id)
        self.assertEqual(ipc.request.call_count, 1)

    def test_send_time_is_deducted_and_read_has_no_extra_second(self):
        clock = [10.0]
        future = Mock(result=Mock(side_effect=TimeoutError))
        ipc = DesktopIPC('fixture')
        ipc._send = Mock(side_effect=lambda message: clock.__setitem__(0, 10.25))
        with patch('bridge.clients.codex.ipc.time', SimpleNamespace(monotonic=lambda: clock[0])), \
             patch('bridge.clients.codex.ipc.Future', return_value=future):
            with self.assertRaisesRegex(IPCError, '读取超时'):
                ipc.request('thread-owner-discovery', {'conversationId': 'fixture'}, timeout=6, deadline=10.5)
        self.assertEqual(ipc._send.call_args.args[0]['timeoutMs'], 500)
        future.result.assert_called_once_with(timeout=.25)
        self.assertEqual(ipc.pending, {})

    def test_expired_deadline_does_not_send_or_register_a_pending_request(self):
        ipc = DesktopIPC('fixture')
        ipc._send = Mock()
        with patch('bridge.clients.codex.ipc.time', SimpleNamespace(monotonic=lambda: 10)):
            with self.assertRaisesRegex(IPCError, '读取超时'):
                ipc.request('thread-owner-discovery', {}, deadline=10)
        ipc._send.assert_not_called()
        self.assertEqual(ipc.pending, {})

    def test_default_mutation_wait_and_uncertain_result_are_preserved(self):
        ipc = DesktopIPC('fixture')
        ipc._send = Mock()
        future = Mock(result=Mock(side_effect=TimeoutError))
        with patch('bridge.clients.codex.ipc.Future', return_value=future):
            with self.assertRaisesRegex(IPCError, '操作可能已提交'):
                ipc.request('thread-follower-start-turn', {}, timeout=3)
        future.result.assert_called_once_with(timeout=4)
        self.assertEqual(ipc._send.call_count, 1)
