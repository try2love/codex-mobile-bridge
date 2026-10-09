"""Bounded health probes use the existing signed file connector, never native UI."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.clients.claude.adapter import Claude
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.claude.mailbox import CONNECTOR_REVISION

ROOT = Path(__file__).resolve().parents[1]


class ClaudeConnectionProbe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.adapter = Claude(Path(self.temp.name)/'adapter')
        self.adapter.prepare(); self.addCleanup(self.adapter.close)
        self.desktop = self.adapter.desktop
        self.reply({'seq': 0})

    def reply(self, value):
        (self.adapter.directory/'response.json').write_text(self.desktop.pack({
            'generation': self.desktop.generation, 'timestamp': time.time(), 'connected': True,
            'connectorRevision': CONNECTOR_REVISION, 'surfaces': {'code': ['mobileList', 'getAll']}, **value}))

    def test_current_connector_must_answer_a_real_signed_read(self):
        seen = []
        def respond():
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                packet = json.loads((self.adapter.directory/'request.json').read_text())
                request = json.loads(packet['payload'])
                if request['type'] == 'request':
                    seen.append(request)
                    self.reply({'seq': request['seq'], 'done': True, 'result': {'code': []}})
                    return
                time.sleep(.01)
        worker = threading.Thread(target=respond); worker.start()
        with patch('bridge.clients.claude.adapter.native_action') as native, patch.object(self.adapter, 'prepare') as prepare:
            self.adapter.check_connection()
        worker.join(3)
        self.assertEqual([(row['surface'], row['method'], row['args']) for row in seen], [('code', 'mobileList', [])])
        self.assertIs(self.adapter.desktop, self.desktop)
        native.assert_not_called(); prepare.assert_not_called()

    def test_fresh_heartbeat_without_rpc_response_does_not_report_success(self):
        self.assertTrue(self.desktop.connected)
        generation = self.desktop.generation
        started = time.monotonic()
        with patch('bridge.clients.claude.adapter.native_action') as native, patch.object(self.adapter, 'prepare') as prepare:
            with self.assertRaisesRegex(BridgeUnavailable, '暂未响应'):
                self.adapter.check_connection()
        self.assertLess(time.monotonic() - started, 5.5)
        self.assertEqual(self.desktop.generation, generation)
        self.assertEqual(self.desktop.seq, 1)
        native.assert_not_called(); prepare.assert_not_called()

    def test_busy_original_operation_is_neither_cancelled_nor_overwritten(self):
        async def occupy(): await self.desktop.lock.acquire()
        asyncio.run_coroutine_threadsafe(occupy(), self.adapter.loop).result(timeout=1)
        self.desktop.write({'type': 'request', 'generation': self.desktop.generation, 'seq': 7,
                            'surface': 'code', 'method': 'sendMessage', 'args': ['fixture'],
                            'expires': time.time() + 30})
        original = (self.adapter.directory/'request.json').read_bytes()
        try:
            with self.assertRaisesRegex(BridgeUnavailable, '暂未响应'):
                self.adapter.check_connection()
            self.assertTrue(self.desktop.lock.locked())
            self.assertEqual((self.adapter.directory/'request.json').read_bytes(), original)
            self.assertEqual(self.desktop.seq, 0)
        finally:
            self.adapter.loop.call_soon_threadsafe(self.desktop.lock.release)

    def test_probe_outer_timeout_keeps_mailbox_locked_until_slow_write_finishes(self):
        entered, release, written = threading.Event(), threading.Event(), threading.Event()
        write = self.desktop.write
        def slow_write(value):
            entered.set()
            release.wait(2)
            write(value)
            written.set()
        submit = asyncio.run_coroutine_threadsafe
        submitted = []
        def shortened(operation, loop):
            future = submit(operation, loop); original_result = future.result
            future.result = lambda timeout=None: original_result(timeout=.05 if timeout == 5 else timeout)
            submitted.append(future)
            return future
        with patch.object(self.desktop, 'write', side_effect=slow_write), \
             patch('bridge.clients.claude.adapter.asyncio.run_coroutine_threadsafe', side_effect=shortened):
            try:
                with self.assertRaisesRegex(BridgeUnavailable, '暂未响应'):
                    self.adapter.check_connection()
                self.assertTrue(entered.wait(1))
                self.assertFalse(submitted[0].done())
                self.assertIn(submitted[0], self.adapter.pending_calls)
                self.assertTrue(self.desktop.lock.locked())
                with self.assertRaisesRegex(ValueError, '未确认的请求'):
                    self.adapter.connect(existing_only=True)
            finally:
                release.set()
                self.assertTrue(written.wait(1))
                self.reply({'seq': 1, 'done': True, 'result': []})
                if not submitted[0].cancelled(): submitted[0].result(timeout=1)
        self.assertFalse(self.desktop.lock.locked())

    def test_inflight_dispatch_survives_outer_timeout_and_blocks_reconnect_between_rpcs(self):
        completed = threading.Event()
        async def dispatch(*args):
            while not completed.is_set(): await asyncio.sleep(.01)
            return {'status': 'accepted'}
        submit = asyncio.run_coroutine_threadsafe
        submitted = []
        def shortened(operation, loop):
            future = submit(operation, loop); original_result = future.result
            future.result = lambda timeout=None: original_result(timeout=.02 if timeout == 100 else timeout)
            submitted.append(future)
            return future
        with patch.object(self.adapter, 'dispatch', side_effect=dispatch), \
             patch('bridge.clients.claude.adapter.asyncio.run_coroutine_threadsafe', side_effect=shortened):
            with self.assertRaises(FutureTimeoutError): self.adapter.call('send')
        try:
            self.assertFalse(self.desktop.lock.locked(), 'Fixture is between RPCs')
            self.assertFalse(submitted[0].done())
            self.assertIn(submitted[0], self.adapter.pending_calls)
            with patch.object(self.adapter, 'prepare') as prepare, patch.object(self.desktop, 'close') as close:
                with self.assertRaisesRegex(ValueError, '未确认的请求'):
                    self.adapter.connect(existing_only=True)
            prepare.assert_not_called(); close.assert_not_called()
            self.assertFalse(submitted[0].cancelled())
        finally:
            completed.set(); submitted[0].result(timeout=1)

    def test_disconnected_held_mailbox_blocks_remote_connection_before_native_ui(self):
        self.reply({'connected': False})
        async def occupy(): await self.desktop.lock.acquire()
        asyncio.run_coroutine_threadsafe(occupy(), self.adapter.loop).result(timeout=1)
        try:
            with patch.object(self.adapter, 'prepare') as prepare, \
                 patch.object(self.desktop, 'close') as close, patch('bridge.clients.claude.adapter.native_action') as native:
                with self.assertRaisesRegex(ValueError, '未确认的请求'):
                    self.adapter.connect(existing_only=True)
            prepare.assert_not_called(); close.assert_not_called(); native.assert_not_called()
        finally: self.adapter.loop.call_soon_threadsafe(self.desktop.lock.release)

    def test_pending_rpc_is_rechecked_after_screen_saver_wait(self):
        self.reply({'connected': False})
        def action(name, **kwargs):
            if name == 'check': return {'setupState': 'needs-screen-saver', 'reason': 'screen saver'}
            self.assertEqual(name, 'wait-desktop')
            async def occupy(): await self.desktop.lock.acquire()
            asyncio.run_coroutine_threadsafe(occupy(), self.adapter.loop).result(timeout=1)
            return {'setupState': 'ready'}
        try:
            with patch('bridge.clients.claude.adapter.native_action', side_effect=action), \
                 patch('bridge.clients.claude.adapter.running_app') as running, \
                 patch.object(self.adapter, 'prepare') as prepare, \
                 patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
                self.adapter._connect_native(False, self.adapter.directory/'cancel', existing_only=True)
            self.assertIn('未确认的请求', self.adapter.status()['reason'])
            running.assert_not_called(); prepare.assert_not_called()
        finally: self.adapter.loop.call_soon_threadsafe(self.desktop.lock.release)

    def test_unconfirmed_send_survives_failed_call_and_later_read_probe(self):
        self.reply({'surfaces': {'code': ['sendMessage', 'mobileList']}})
        future = asyncio.run_coroutine_threadsafe(self.desktop.call('code', 'sendMessage', 'fixture', timeout=.03), self.adapter.loop)
        with self.assertRaises(BridgeUnavailable): future.result(timeout=1)
        self.assertFalse(self.desktop.lock.locked())
        self.assertEqual(self.desktop.unconfirmed_mutations, {1})
        with self.assertRaisesRegex(ValueError, '未确认的请求'):
            self.adapter.connect(existing_only=True)
        # A later read success is not an acknowledgement of the timed-out send.
        self.reply({'seq': 2, 'done': True, 'result': []}); self.desktop.read()
        self.assertEqual(self.desktop.unconfirmed_mutations, {1})
        with self.assertRaisesRegex(ValueError, '未确认的请求'):
            self.adapter.connect(existing_only=True)
        self.reply({'seq': 1, 'done': True, 'result': True}); self.desktop.read()
        self.assertEqual(self.desktop.unconfirmed_mutations, set())

    def test_remote_reinjection_preserves_owner_and_blocks_new_calls_until_finished(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                self.adapter.discovery = {'installed': True, 'executable': '/fixture/Claude', 'dataHome': '/fixture/profile'}
                self.reply({'connected': False})
                generation = self.desktop.generation
                if stopped:
                    self.reply({'connected': True})
                    self.desktop.close()
                    self.assertFalse(self.desktop.connected, 'A fresh pre-stop heartbeat cannot undo local cancellation')
                actions = []
                def native(name, **kwargs):
                    actions.append(name)
                    if name == 'connect':
                        self.assertTrue(self.adapter.reconnecting)
                        with self.assertRaisesRegex(BridgeUnavailable, '正在重新连接'):
                            self.adapter.call('list')
                        self.assertIs(self.adapter.desktop, self.desktop)
                        self.assertEqual(self.desktop.generation, generation)
                        if stopped:
                            current = json.loads(json.loads((self.adapter.directory/'request.json').read_text())['payload'])
                            self.assertEqual(current['type'], 'idle')
                            self.assertFalse(self.desktop.stopped)
                        self.reply({'seq': 0, 'done': True, 'result': None})
                        return {'setupState': 'submitted'}
                    return {'setupState': 'connected' if name == 'close-devtools' else 'ready'}
                with patch('bridge.clients.claude.adapter.native_action', side_effect=native), \
                     patch('bridge.clients.claude.adapter.running_app', return_value=42), \
                     patch('bridge.clients.claude.adapter.claude_data_home', return_value='/fixture/profile'), \
                     patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
                     patch.object(self.desktop, 'close') as close, \
                     patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
                    self.adapter._connect_native(False, self.adapter.directory/'cancel', existing_only=True)
                close.assert_not_called()
                self.assertIn('connect', actions, 'Resume must not accept a cached heartbeat and skip actual connection')
                self.assertFalse(self.adapter.reconnecting)
                self.assertTrue(self.adapter.status()['connected'])
