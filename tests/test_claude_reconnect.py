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
        self.adapter.connection_probe_failed = True
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
        self.assertTrue(self.adapter.status()['connected'])
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

    def test_failed_probe_reaches_console_in_one_explicit_reconnect_despite_fresh_heartbeat(self):
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': '/fixture/Claude', 'dataHome': '/fixture/profile'}
        with self.assertRaises(BridgeUnavailable):
            self.adapter.check_connection()
        # A renderer can continue heartbeats while its IPC reads are stuck.
        self.reply({'seq': 1})
        self.assertFalse(self.adapter.status()['connected'])
        generation, actions = self.desktop.generation, []
        requests = []
        def respond():
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                packet = json.loads((self.adapter.directory/'request.json').read_text())
                request = json.loads(packet['payload'])
                if request['type'] == 'request' and request['seq'] > 1:
                    requests.append(request)
                    self.reply({'seq': request['seq'], 'done': True, 'result': []})
                    return
                time.sleep(.01)
        worker = threading.Thread(target=respond); worker.start()
        def native(name, **kwargs):
            actions.append(name)
            if name == 'connect':
                self.assertIs(self.adapter.desktop, self.desktop)
                self.assertEqual(self.desktop.generation, generation)
                self.reply({'seq': 1})
                return {'setupState': 'submitted', 'submission': 'submitted', 'consoleCleanupPending': False}
            return {'setupState': 'connected' if name == 'close-devtools' else 'ready'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=native), \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.adapter.claude_data_home', return_value='/fixture/profile'), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter.connect(existing_only=True)
            self.adapter.setup_thread.join(timeout=2)
        worker.join(3)
        self.assertFalse(self.adapter.setup_thread.is_alive())
        self.assertEqual(actions.count('connect'), 1)
        self.assertEqual([(row['surface'], row['method'], row['args']) for row in requests], [('code', 'mobileList', [])])
        self.assertTrue(self.adapter.status()['connected'])

    def test_reconnect_heartbeat_cannot_clear_failed_probe_or_trigger_replay(self):
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': '/fixture/Claude', 'dataHome': '/fixture/profile',
                                  'dataHomeExplicit': True}
        self.adapter.connection_probe_failed = True
        actions = []
        def native(name, **kwargs):
            actions.append(name)
            if name == 'check': return {'setupState': 'ready'}
            self.assertEqual(name, 'connect')
            self.reply({'seq': 0})  # Old renderer heartbeat, not a bootstrap or RPC receipt.
            return {'setupState': 'submitted'}
        call = self.desktop.call
        async def bounded_call(surface, method, *args, **kwargs):
            return await call(surface, method, *args, timeout=.03)
        with patch('bridge.clients.claude.adapter.native_action', side_effect=native), \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
             patch.object(self.desktop, 'call', side_effect=bounded_call), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter._connect_native(False, self.adapter.directory/'cancel', existing_only=True)
        self.assertEqual(actions, ['check', 'connect'])
        self.assertFalse(self.adapter.status()['connected'])
        self.assertTrue(self.adapter.connection_probe_failed)
        self.assertEqual(self.adapter.status()['setupState'], 'needs-retry')
        request = json.loads(json.loads((self.adapter.directory/'request.json').read_text())['payload'])
        self.assertEqual((request['seq'], request['surface'], request['method'], request['args']), (1, 'code', 'mobileList', []))

    def test_macos_only_explicit_initialization_selects_foreground_path(self):
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': '/fixture/Claude', 'dataHome': '/fixture/profile'}
        self.adapter.connection_probe_failed = True
        for explicit in (False, True):
            with self.subTest(explicit=explicit), \
                 patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
                 patch('bridge.clients.claude.adapter.threading.Thread') as thread:
                thread.return_value.is_alive.return_value = False
                if explicit: self.adapter.connect(existing_only=True)
                else: self.adapter.reconnect()
            self.assertEqual(thread.call_args.kwargs['target'], self.adapter._connect_native)
            self.assertEqual(thread.call_args.kwargs['kwargs'], {'existing_only': True, 'foreground': explicit})

    def test_macos_silent_reconnect_never_checks_permissions_or_opens_native_ui(self):
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': '/fixture/Claude', 'dataHome': '/fixture/profile'}
        self.reply({'connected': False})
        generation = self.desktop.generation
        with patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.adapter.native_action') as native, \
             patch('bridge.clients.claude.adapter.running_app') as running:
            self.adapter.reconnect()
            self.adapter.setup_thread.join(timeout=2)
        self.assertFalse(self.adapter.setup_thread.is_alive())
        self.assertEqual(self.adapter.status()['setupState'], 'needs-initialization')
        self.assertFalse(self.adapter.status()['connected'])
        self.assertEqual(self.desktop.generation, generation)
        native.assert_not_called(); running.assert_not_called()

    def test_macos_initial_auto_recovery_creates_mailbox_without_native_ui(self):
        directory = self.adapter.directory/'auto'
        directory.mkdir()
        (directory/'discovery.json').write_text(json.dumps({'installed': True,
            'automaticConnection': 'native-console', 'autoConnect': True,
            'executable': '/fixture/Claude', 'dataHome': '/fixture/profile'}))
        with patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.adapter.native_action') as native, \
             patch('bridge.clients.claude.adapter.running_app') as running:
            adapter = Claude(directory)
            self.addCleanup(adapter.close)
            adapter.setup_thread.join(timeout=2)
        self.assertFalse(adapter.setup_thread.is_alive())
        self.assertTrue((directory/'connection.json').is_file())
        self.assertEqual(adapter.status()['setupState'], 'needs-initialization')
        native.assert_not_called(); running.assert_not_called()

    def test_macos_silent_recovery_requires_actual_rpc_after_failed_probe(self):
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': '/fixture/Claude', 'dataHome': '/fixture/profile'}
        self.adapter.connection_probe_failed = True
        seen = []
        def respond():
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                request = json.loads(json.loads((self.adapter.directory/'request.json').read_text())['payload'])
                if request['type'] == 'request':
                    seen.append(request)
                    self.reply({'seq': request['seq'], 'done': True, 'result': []})
                    return
                time.sleep(.01)
        worker = threading.Thread(target=respond); worker.start()
        with patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.adapter.native_action') as native, \
             patch('bridge.clients.claude.adapter.running_app') as running:
            self.adapter.reconnect()
            self.adapter.setup_thread.join(timeout=2)
        worker.join(3)
        self.assertFalse(self.adapter.setup_thread.is_alive())
        self.assertEqual([(row['surface'], row['method'], row['args']) for row in seen], [('code', 'mobileList', [])])
        self.assertTrue(self.adapter.status()['connected'])
        self.assertFalse(self.adapter.connection_probe_failed)
        native.assert_not_called(); running.assert_not_called()

    def test_macos_silent_heartbeat_cannot_clear_failed_rpc_or_inject_console(self):
        self.adapter.connection_probe_failed = True
        call = self.desktop.call
        async def bounded_call(surface, method, *args, **kwargs):
            return await call(surface, method, *args, timeout=.03)
        with patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.adapter.native_action') as native, \
             patch('bridge.clients.claude.adapter.running_app') as running, \
             patch.object(self.desktop, 'call', side_effect=bounded_call):
            self.adapter._connect_native(False, self.adapter.directory/'cancel', existing_only=True, foreground=False)
        self.assertEqual(self.adapter.status()['setupState'], 'needs-initialization')
        self.assertFalse(self.adapter.status()['connected'])
        self.assertTrue(self.adapter.connection_probe_failed)
        self.assertEqual(self.desktop.seq, 1)
        native.assert_not_called(); running.assert_not_called()

    def test_macos_silent_reconnect_cannot_resume_from_a_pre_stop_heartbeat(self):
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': '/fixture/Claude', 'dataHome': '/fixture/profile'}
        self.desktop.close()
        generation = self.desktop.generation
        with patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.adapter.native_action') as native:
            self.adapter.reconnect()
            self.adapter.setup_thread.join(timeout=2)
        self.assertEqual(self.adapter.status()['setupState'], 'needs-initialization')
        self.assertFalse(self.adapter.status()['connected'])
        self.assertEqual(self.desktop.generation, generation)
        request = json.loads(json.loads((self.adapter.directory/'request.json').read_text())['payload'])
        self.assertEqual(request['type'], 'idle')
        native.assert_not_called()
        self.reply({'seq': 0})
        self.assertTrue(self.adapter.status()['connected'])

    def test_macos_silent_reconnect_keeps_unconfirmed_mutation_gate(self):
        self.desktop.unconfirmed_mutations.add(1)
        request = (self.adapter.directory/'request.json').read_bytes()
        with patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.adapter.native_action') as native:
            with self.assertRaisesRegex(ValueError, '未确认的请求'):
                self.adapter.reconnect()
        self.assertEqual((self.adapter.directory/'request.json').read_bytes(), request)
        native.assert_not_called()

    def test_macos_explicit_initialization_cannot_bypass_unconfirmed_mutation_gate(self):
        self.desktop.unconfirmed_mutations.add(1)
        request = (self.adapter.directory/'request.json').read_bytes()
        with patch('bridge.clients.claude.adapter.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.adapter.native_action') as native, \
             patch.object(self.adapter, 'prepare') as prepare:
            with self.assertRaisesRegex(ValueError, '未确认的请求'):
                self.adapter.connect()
        self.assertEqual((self.adapter.directory/'request.json').read_bytes(), request)
        native.assert_not_called(); prepare.assert_not_called()

    def test_recovery_read_probe_does_not_bypass_normal_calls_or_accept_cancelled_owner(self):
        self.adapter.reconnecting = True
        with self.assertRaisesRegex(BridgeUnavailable, '正在重新连接'):
            self.adapter.call('send')
        with self.assertRaisesRegex(BridgeUnavailable, '正在重新连接'):
            self.adapter.check_connection()
        self.assertEqual(self.desktop.seq, 0)
        for invalidate in ('cancel', 'owner'):
            with self.subTest(invalidate=invalidate):
                self.adapter.desktop = self.desktop
                self.adapter.setup_cancel.clear()
                async def invalidate_after_reply(*args, **kwargs):
                    if invalidate == 'cancel': self.adapter.setup_cancel.set()
                    else: self.adapter.desktop = None
                    return []
                with patch.object(self.desktop, 'call', side_effect=invalidate_after_reply):
                    with self.assertRaisesRegex(BridgeUnavailable, '暂未响应'):
                        self.adapter.check_connection(_reconnect_probe=True)
                self.assertTrue(self.adapter.connection_probe_failed)
        self.adapter.desktop = self.desktop
        self.adapter.setup_cancel.clear()
        self.adapter.reconnecting = False

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

    @patch('bridge.clients.claude.adapter.sys.platform', 'darwin')
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

    def test_failed_probe_cannot_treat_heartbeat_as_recovery_during_screen_saver_wait(self):
        self.adapter.connection_probe_failed = True
        def native(name, **kwargs):
            self.assertEqual(name, 'wait-desktop')
            self.assertFalse(kwargs['recovered']())
            return {'setupState': 'ready'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=native):
            self.assertTrue(self.adapter._await_desktop({'setupState': 'needs-screen-saver'}, self.adapter.setup_cancel))

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
