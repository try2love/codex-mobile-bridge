"""A live but retired router must not trap the gateway after explicit recovery."""
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import patch

from bridge.clients.codex.ipc import DesktopIPC, IPCError
import test_bridge
from test_bridge import DesktopFixture, THREAD


class RecoveryTests(unittest.TestCase):
    setUp = test_bridge.IntegrationTests.setUp
    tearDown = test_bridge.IntegrationTests.tearDown

    def test_explicit_connect_replaces_retired_socket_even_if_snapshot_was_connected(self):
        if os.name == 'nt':
            self.skipTest('Unix pathname replacement')
        session = self.bridge.session(THREAD)
        self.assertTrue(session.connected)
        old = self.fixture
        old.loaded = False
        Path(old.path).unlink()
        alternate = self.root / 'replacement'
        alternate.mkdir()
        fresh = DesktopFixture(alternate)
        fresh.state['title'] = 'Current router'
        Path(fresh.path).rename(old.path)
        self.addCleanup(fresh.close)
        original_socket = self.bridge.ipc.socket
        with patch('bridge.app.service.open_in_desktop') as navigate:
            result = self.bridge.activate(THREAD)
        self.assertIsNot(self.bridge.ipc.socket, original_socket)
        self.assertTrue(result.connected)
        self.assertEqual(result.state['title'], 'Current router')
        self.assertTrue(old.worker.is_alive())
        navigate.assert_not_called()
        self.assertFalse(any(r['method'].startswith('thread-follower-') for r in fresh.requests))

    def test_explicit_connect_rebuilds_once_after_owner_disconnect(self):
        session = self.bridge.session(THREAD)
        self.bridge._disconnected()
        with patch.object(self.bridge, '_refresh_async'), patch.object(
                self.bridge.ipc, 'reconnect', wraps=self.bridge.ipc.reconnect) as reconnect, patch(
                'bridge.app.service.open_in_desktop') as navigate:
            result = self.bridge.activate(THREAD)
        self.assertTrue(result.connected)
        reconnect.assert_called_once_with()
        navigate.assert_not_called()

    def test_replaced_path_does_not_detach_an_owner_still_answering_on_old_router(self):
        if os.name == 'nt':
            self.skipTest('Unix pathname replacement')
        session = self.bridge.session(THREAD)
        original_socket = self.bridge.ipc.socket
        Path(self.fixture.path).unlink()
        alternate = self.root / 'replacement'
        alternate.mkdir()
        fresh = DesktopFixture(alternate)
        fresh.loaded = False
        Path(fresh.path).rename(self.fixture.path)
        self.addCleanup(fresh.close)
        with patch('bridge.app.service.open_in_desktop') as navigate:
            result = self.bridge.activate(THREAD)
        self.assertTrue(result.connected)
        self.assertEqual(result.state['title'], 'Desktop test')
        self.assertIs(self.bridge.ipc.socket, original_socket)
        self.assertEqual(fresh.requests, [])
        navigate.assert_not_called()

    def test_recovery_waits_for_inflight_host_operation_before_replacing_follower(self):
        session = self.bridge.session(THREAD)
        self.bridge._disconnected()
        started, replaced = threading.Event(), threading.Event()
        original_socket = self.bridge.ipc.socket
        rebuild = self.bridge._rebuild_desktop_transport
        def replace():
            replaced.set()
            rebuild()
        def reconnect():
            started.set()
            return self.bridge.reconnect_session(THREAD)
        with patch.object(self.bridge, '_refresh_async'), patch.object(
                self.bridge, '_rebuild_desktop_transport', side_effect=replace):
            with ThreadPoolExecutor(max_workers=1) as executor:
                with self.bridge.accounts.gate.host('local'):
                    pending = executor.submit(reconnect)
                    self.assertTrue(started.wait(1))
                    self.assertFalse(replaced.wait(.05))
                    self.assertIs(self.bridge.ipc.socket, original_socket)
                self.assertTrue(pending.result(timeout=3).connected)
        self.assertTrue(replaced.is_set())

    def test_duplicate_explicit_reconnects_share_one_result_before_gate_queue(self):
        started, release = threading.Event(), threading.Event()
        joined = threading.Barrier(3)
        future = Future()
        original_result = future.result
        def wait_for_result():
            joined.wait(timeout=2)
            return original_result()
        def activate(thread_id):
            started.set()
            release.wait(2)
            raise IPCError('owner unavailable')
        with patch.object(self.bridge, 'activate', side_effect=activate) as activate, patch(
                'bridge.app.service.Future', return_value=future), patch.object(
                future, 'result', side_effect=wait_for_result):
            with ThreadPoolExecutor(max_workers=3) as executor:
                first = executor.submit(self.bridge.reconnect_session, THREAD)
                self.assertTrue(started.wait(1))
                second = executor.submit(self.bridge.reconnect_session, THREAD)
                third = executor.submit(self.bridge.reconnect_session, THREAD)
                joined.wait(timeout=2)
                release.set()
                for result in (first, second, third):
                    with self.assertRaisesRegex(IPCError, 'owner unavailable'):
                        result.result(timeout=2)
                self.assertEqual(activate.call_count, 1)
        self.assertFalse(self.bridge.reconnect_pending)

    def test_close_interrupts_blocked_write_even_while_reader_holds_event_lock(self):
        started, released, finished = threading.Event(), threading.Event(), threading.Event()
        class BlockedStream:
            def sendall(self, data):
                started.set()
                released.wait(3)
                raise OSError('closed')
            def shutdown(self, how):
                released.set()
            def close(self):
                released.set()
        ipc = DesktopIPC('unused')
        ipc.socket = BlockedStream()
        def write():
            with ipc.event_lock:
                try:
                    ipc._send({'text': 'blocked discovery reply'})
                except IPCError:
                    pass
                finally:
                    finished.set()
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        self.assertTrue(started.wait(1))
        self.assertFalse(finished.is_set())
        closer = threading.Thread(target=ipc.close, daemon=True)
        closer.start()
        try:
            closer.join(1)
            self.assertFalse(closer.is_alive(), 'Closing must interrupt the blocked send before waiting on its locks')
            writer.join(1)
            self.assertTrue(finished.is_set())
            self.assertIsNone(ipc.socket)
        finally:
            released.set()
            writer.join(1)
            closer.join(1)

    def test_rebuild_leaves_native_running_and_uses_replacement_router(self):
        if os.name == 'nt':
            self.skipTest('Unix pathname replacement; Windows reconnect covered separately')
        session = self.bridge.session(THREAD)
        old = self.fixture
        old.loaded = False
        self.bridge._disconnected()
        Path(old.path).unlink()  # Old server and its accepted connection stay alive.
        alternate = self.root / 'replacement'
        alternate.mkdir()
        fresh = DesktopFixture(alternate)
        fresh.state['title'] = 'Current router'
        Path(fresh.path).rename(old.path)
        self.addCleanup(fresh.close)
        original_socket = self.bridge.ipc.socket
        self.bridge._attach(session, timeout=.01)
        self.assertFalse(session.connected, 'Existing connection still reaches retired router')
        with patch('bridge.app.service.open_in_desktop') as navigate:
            result = self.bridge.rebuild_connection(THREAD)
        self.assertTrue(result.connected)
        self.assertIsNot(self.bridge.ipc.socket, original_socket)
        self.assertEqual(result.state['title'], 'Current router')
        self.assertTrue(old.worker.is_alive(), 'No native shutdown')
        self.assertFalse(navigate.called, 'Available owner needs no navigation')
        self.assertFalse(any(r['method'].startswith('thread-follower-') for r in fresh.requests))

    def test_disconnection_recommends_remote_rebuild_and_snapshot_clears_it(self):
        session = self.bridge.session(THREAD)
        self.bridge._disconnected()
        self.assertTrue(session.view()['rebuildRecommended'])
        session.desktop_restart_recommended = True
        self.fixture.snapshot()
        with session.condition:
            self.assertTrue(session.condition.wait_for(lambda: session.connected, timeout=2))
        self.assertFalse(session.view()['rebuildRecommended'])
        self.assertFalse(self.bridge.desktop_restart_recommended())

    def test_reconnect_clears_old_transport_synchronously(self):
        ipc = self.bridge.ipc
        ipc.connect()
        old = ipc.socket
        ipc.reconnect()
        self.assertIsNot(ipc.socket, old)
        self.assertTrue(ipc.client_id)
        client_id = ipc.client_id
        ipc._disconnect(old)  # A delayed old reader must not invalidate the replacement.
        self.assertEqual(ipc.client_id, client_id)

    def test_unreachable_owner_finishes_with_remote_rebuild_available(self):
        session = self.bridge.session(THREAD)
        self.bridge._disconnected()
        with patch.object(self.bridge, '_attach'), patch('bridge.app.service.open_in_desktop', side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(IPCError, '应用管理'):
                self.bridge.rebuild_connection(THREAD)
        self.assertTrue(session.view()['desktopRestartRecommended'])
        self.assertTrue(self.bridge.desktop_restart_recommended())
        self.assertFalse(session.activating)
        self.assertFalse(session.connected)
        self.assertTrue(session.view()['rebuildRecommended'])

    def test_retry_after_transport_replacement_error_reattaches_without_restart(self):
        session = self.bridge.session(THREAD)
        self.bridge._disconnected()
        session.rebuild_recommended = False
        with patch.object(self.bridge, '_refresh_async'), patch.object(
                self.bridge.ipc, 'reconnect', side_effect=IPCError('interrupted')):
            with self.assertRaisesRegex(IPCError, '再次重建'):
                self.bridge.rebuild_connection(THREAD)
        self.assertTrue(session.view()['desktopRestartRecommended'])
        self.assertTrue(session.view()['rebuildRecommended'])
        with patch('bridge.app.service.open_in_desktop') as navigate:
            result = self.bridge.rebuild_connection(THREAD)
        self.assertTrue(result.connected)
        self.assertFalse(result.view()['desktopRestartRecommended'])
        self.assertFalse(result.view()['rebuildRecommended'])
        navigate.assert_not_called()

    def test_missing_owner_invalidates_control_without_replaying_write(self):
        session = self.bridge.session(THREAD)
        with patch.object(self.bridge.ipc, 'request', side_effect=IPCError('no-client-found')) as request:
            with self.assertRaises(IPCError):
                self.bridge._call(session, 'thread-follower-start-turn', {'text': 'only once'})
        self.assertEqual(request.call_count, 1)
        self.assertFalse(session.connected)
        self.assertTrue(session.view()['rebuildRecommended'])

if __name__ == '__main__':
    unittest.main()
