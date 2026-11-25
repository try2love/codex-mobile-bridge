import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.cli_session import AntigravityCli


class InterruptibleAdapter:
    concurrent_stop = True
    receipts_path = None

    def __init__(self):
        self.call = Mock(return_value={'status': 'accepted'})
        self.close = Mock()


class DesktopInterrupts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.addCleanup(temp.cleanup)
        self.manager = DesktopSessions(temp.name)
        self.addCleanup(self.manager.close)
        self.adapter = InterruptibleAdapter()
        self.manager.adapters['antigravity'] = self.adapter

    def call(self, action, rid=None):
        return self.manager.call('antigravity', action, 'one',
                                 {'id': rid or str(uuid.uuid4()), **({'text': 'hello'} if action == 'send' else {}),
                                  **({'taskId': 'bg:12'} if action == 'stop-task' else {})})

    def test_task_stop_arrives_while_send_waits(self):
        entered, stopped = threading.Event(), threading.Event()
        def native(action, *_):
            if action == 'send':
                entered.set()
                if not stopped.wait(2):
                    raise AssertionError('task stop was blocked by send')
            elif action == 'stop-task':
                stopped.set()
            return {'status': 'accepted'}
        self.adapter.call.side_effect = native
        with ThreadPoolExecutor(2) as pool:
            sent = pool.submit(self.call, 'send')
            self.assertTrue(entered.wait(1))
            rid = str(uuid.uuid4())
            stop = pool.submit(self.call, 'stop-task', rid)
            self.assertEqual(stop.result(3)['status'], 'accepted')
            self.assertEqual(sent.result(3)['status'], 'accepted')
            self.call('stop-task', rid)
        self.assertEqual([c.args[0] for c in self.adapter.call.call_args_list].count('stop-task'), 1)
        self.assertTrue(stopped.is_set())

    def test_stop_arrives_before_blocked_send_and_duplicate_receipt_is_not_replayed(self):
        entered, stopped = threading.Event(), threading.Event()
        def native(action, *_):
            if action == 'send':
                entered.set()
                if not stopped.wait(3):
                    raise AssertionError('stop was blocked by send')
            elif action == 'stop':
                stopped.set()
            return {'status': 'accepted'}
        self.adapter.call.side_effect = native
        with ThreadPoolExecutor(2) as pool:
            sent = pool.submit(self.call, 'send')
            self.assertTrue(entered.wait(2))
            rid = str(uuid.uuid4())
            stop = pool.submit(self.call, 'stop', rid)
            self.assertEqual(stop.result(2)['status'], 'accepted')
            self.assertEqual(sent.result(2)['status'], 'accepted')
            self.assertEqual(self.call('stop', rid)['status'], 'accepted')
        self.assertEqual([c.args[0] for c in self.adapter.call.call_args_list].count('stop'), 1)
        self.assertEqual(self.manager.read_changes['antigravity'], 0)

    def test_new_send_and_rebind_wait_for_stop(self):
        for rebind in (False, True):
            with self.subTest(rebind=rebind):
                entered, release, changed = threading.Event(), threading.Event(), threading.Event()
                def native(action, *_):
                    if action == 'stop':
                        entered.set()
                        release.wait(3)
                    return {'status': 'accepted'}
                def change():
                    with self.manager._changing('antigravity', rebind=rebind):
                        changed.set()
                self.adapter.call.side_effect = native
                with ThreadPoolExecutor(2) as pool:
                    stop = pool.submit(self.call, 'stop')
                    self.assertTrue(entered.wait(2))
                    other = pool.submit(change)
                    try:
                        self.assertFalse(changed.wait(.1))
                    finally:
                        release.set()
                    self.assertEqual(stop.result(2)['status'], 'accepted')
                    other.result(2)
                    self.assertTrue(changed.is_set())

    def test_changed_binding_rejects_waiting_stop_without_native_write(self):
        gate = self.manager.interrupt_locks['antigravity']
        waiting = threading.Event()
        original = self.manager._changing
        def observed(*args, **kwargs):
            if kwargs.get('interrupt'):
                waiting.set()
            return original(*args, **kwargs)
        with patch.object(self.manager, '_changing', side_effect=observed), ThreadPoolExecutor(1) as pool:
            with gate:
                pending = pool.submit(self.call, 'stop')
                self.assertTrue(waiting.wait(2))
                self.manager.binding_epochs['antigravity'] += 1
            with self.assertRaisesRegex(BridgeUnavailable, '接入已变化'):
                pending.result(2)
        self.adapter.call.assert_not_called()

    def test_completed_rebind_before_fingerprint_rejects_stop_from_old_binding(self):
        import bridge.clients.manager as module
        hashing, release = threading.Event(), threading.Event()
        original = module.hashlib.sha256
        def paused(data):
            hashing.set()
            if not release.wait(3):
                raise AssertionError('fingerprint fixture was not released')
            return original(data)
        with patch.object(module.hashlib, 'sha256', side_effect=paused), ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.call, 'stop')
            self.assertTrue(hashing.wait(2))
            try:
                with self.manager._changing('antigravity'):
                    pass
            finally:
                release.set()
            with self.assertRaisesRegex(BridgeUnavailable, '接入已变化'):
                pending.result(2)
        self.adapter.call.assert_not_called()

    def test_close_waits_for_native_stop(self):
        entered, release = threading.Event(), threading.Event()
        def native(*_):
            entered.set()
            release.wait(3)
            return {'status': 'accepted'}
        self.adapter.call.side_effect = native
        with ThreadPoolExecutor(2) as pool:
            stop = pool.submit(self.call, 'stop')
            self.assertTrue(entered.wait(2))
            closing = pool.submit(self.manager.close)
            try:
                self.assertFalse(closing.done())
                self.adapter.close.assert_not_called()
            finally:
                release.set()
            self.assertEqual(stop.result(2)['status'], 'accepted')
            closing.result(2)
        # close is not idempotent, remove its scheduled second invocation.
        self._cleanups = [row for row in self._cleanups if row[0] != self.manager.close]

    def test_non_opted_in_adapter_keeps_serial_writes(self):
        self.manager.adapters['antigravity'] = Mock()
        self.manager.adapters['antigravity'].call.return_value = {'status': 'accepted'}
        waiting = threading.Event()
        original = self.manager._changing
        def observed(*args, **kwargs):
            self.assertFalse(kwargs.get('interrupt'))
            waiting.set()
            return original(*args, **kwargs)
        with patch.object(self.manager, '_changing', side_effect=observed), ThreadPoolExecutor(1) as pool:
            with self.manager.locks['antigravity']:
                pending = pool.submit(self.call, 'stop')
                self.assertTrue(waiting.wait(2))
                self.manager.adapters['antigravity'].call.assert_not_called()
            self.assertEqual(pending.result(2)['status'], 'accepted')


class NativeInterrupts(unittest.TestCase):
    def test_task_validation_and_cancel_bypass_pending_send_transport(self):
        cli = AntigravityCli()
        entered, cancelled = threading.Event(), threading.Event()
        methods = []
        def opened(request, **kwargs):
            method = request.full_url.rsplit('/', 1)[-1]
            methods.append(method)
            if method == 'SendUserCascadeMessage':
                entered.set()
                if not cancelled.wait(2):
                    raise OSError('task validation was blocked')
            if method == 'CancelCascadeSteps':
                cancelled.set()
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b'{"tasks": []}' if method == 'ListBackgroundTasks' else b'{}'
            return response
        cli._opener.open = opened
        target = {'address': 'http://127.0.0.1:1', 'identity': 'same', 'token': 'synthetic'}
        def cancel():
            cli.list_background_tasks('cid', expected_identity='same')
            cli.stop_background_step('cid', 12, expected_identity='same')
        with patch('bridge.clients.antigravity.cli_session.endpoint', return_value=target), \
                patch('bridge.clients.antigravity.cli_session.verify_endpoint'), ThreadPoolExecutor(2) as pool:
            sent = pool.submit(cli.call_ls_rpc, 'SendUserCascadeMessage', {})
            self.assertTrue(entered.wait(1))
            stop = pool.submit(cancel)
            self.assertEqual(sent.result(3), {})
            stop.result(3)
        self.assertEqual(methods, ['SendUserCascadeMessage', 'ListBackgroundTasks', 'CancelCascadeSteps'])

    def test_adapter_task_stop_skips_session_model_hydration(self):
        adapter = Antigravity.__new__(Antigravity)
        adapter.store = Mock()
        adapter.store.resolve_session.return_value = {'id': 'one', 'nativeId': 'cid', 'surfaceDirKey': 'ide'}
        adapter.cli = Mock()
        adapter.cli.running_child_cids.return_value = {'11111111-2222-4333-8444-555555555555'}
        adapter.hooks = None
        adapter._session = Mock(side_effect=AssertionError('unnecessary model read'))
        self.assertEqual(adapter.call('stop-task', 'one', {'taskId': '11111111-2222-4333-8444-555555555555'}),
                         {'status': 'accepted'})
        adapter.cli.stop_child_cascade.assert_called_once()

    def test_cancel_transport_bypasses_busy_send_and_preserves_identity_checks(self):
        cli = AntigravityCli()
        entered, cancelled = threading.Event(), threading.Event()
        methods = []
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{}'
        def opened(request, **kwargs):
            method = request.full_url.rsplit('/', 1)[-1]
            methods.append(method)
            if method == 'SendUserCascadeMessage':
                entered.set()
                if not cancelled.wait(3):
                    raise OSError('cancel was blocked')
            if method == 'CancelCascadeInvocation':
                cancelled.set()
            return response
        cli._opener.open = opened
        target = {'address': 'http://127.0.0.1:1', 'identity': 'same', 'token': 'synthetic'}
        with patch('bridge.clients.antigravity.cli_session.endpoint', return_value=target), \
             patch('bridge.clients.antigravity.cli_session.verify_endpoint') as verify, ThreadPoolExecutor(2) as pool:
            sent = pool.submit(cli.call_ls_rpc, 'SendUserCascadeMessage', {})
            self.assertTrue(entered.wait(2))
            stop = pool.submit(cli.stop, 'cid')
            self.assertEqual(stop.result(2), {})
            self.assertEqual(sent.result(2), {})
            self.assertEqual(verify.call_count, 6)
        self.assertEqual(methods, ['SendUserCascadeMessage', 'CancelCascadeInvocation', 'ForceStopCascadeTree'])

    def test_adapter_stop_does_not_wait_for_session_model_hydration(self):
        adapter = Antigravity.__new__(Antigravity)
        adapter.store = Mock()
        adapter.store.resolve_session.return_value = {'id': 'one', 'nativeId': 'cid', 'surfaceDirKey': 'ide'}
        adapter.cli = Mock()
        adapter.cli.stop.side_effect = lambda *args, before_stop=None, **kwargs: before_stop() if before_stop else None
        adapter.hooks = Mock()
        adapter._session = Mock(side_effect=AssertionError('unnecessary model read'))
        self.assertEqual(adapter.call('stop', 'one'), {'status': 'accepted'})
        adapter.cli.stop.assert_called_once_with('cid', surface='ide', before_stop=ANY)
        adapter.hooks.request_stop.assert_called_once_with('one')
