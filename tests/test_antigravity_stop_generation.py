import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.hook_ipc import HookIPCServer
from bridge.clients.errors import BridgeUnavailable


ROOT = Path(__file__).resolve().parents[1]
CID = '11111111-2222-4333-8444-555555555555'
SID = 'agy-app:' + CID


class AntigravityStopGenerationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.adapter = Antigravity(temporary.name, home=temporary.name)
        self.addCleanup(self.adapter.close)
        with mock.patch.object(HookIPCServer, '_start_server'):
            self.adapter.hooks = HookIPCServer(Path(temporary.name) / 'hooks')
        self.adapter.hooks.set_permission_mode(SID, 'auto-allow')
        self.session = {'model': None, 'effort': ''}
        self.commands = {name: {'name': name, 'info': {'name': name}} for name in ('inspect', 'goal', 'compact')}
        self.row = row = {'id': SID, 'nativeId': CID, 'surfaceDirKey': 'app', 'surfaceRoot': temporary.name}
        self.calls = []

        def rpc(method, payload=None, *, surface='app'):
            self.calls.append((method, payload, surface))
            return {}

        for patcher in (
            mock.patch.object(self.adapter.store, 'resolve_session', return_value=row),
            mock.patch.object(self.adapter.store, 'is_native_busy', return_value=False),
            mock.patch.object(self.adapter.cli, 'available', return_value=True),
            mock.patch.object(self.adapter.cli, 'waiting_state_by_cid', return_value=({}, set())),
            mock.patch.object(self.adapter.cli, 'call_ls_rpc', side_effect=rpc),
            mock.patch.object(self.adapter, '_session', return_value=self.session),
            mock.patch.object(self.adapter, '_commands_by_name', return_value=self.commands),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stop_during_preparation_rejects_old_turn_but_allows_explicit_next_turn(self):
        for action, body in [('send', {'text': '/inspect work'}),
                             ('goal', {'operation': 'set', 'objective': 'finish'}), ('compact', {})]:
            for stage in ('resolve', 'session', 'commands', 'model'):
                with self.subTest(action=action, stage=stage):
                    self.calls.clear()
                    queued = self.adapter.hooks.enqueue_follow_up(SID, 'keep queued')
                    ready, release = threading.Event(), threading.Event()
                    owner = threading.get_ident()
                    target, name, value = {
                        'resolve': (self.adapter.store, 'resolve_session', self.row),
                        'session': (self.adapter, '_session', self.session),
                        'commands': (self.adapter, '_commands_by_name', self.commands),
                        'model': (self.adapter.cli, '_model', None),
                    }[stage]

                    def prepare(*args, **kwargs):
                        if threading.get_ident() == owner:
                            return value
                        ready.set()
                        if not release.wait(3):
                            raise AssertionError('preparation was not released')
                        return value

                    with mock.patch.object(target, name, side_effect=prepare), ThreadPoolExecutor(1) as pool:
                        sending = pool.submit(self.adapter.call, action, SID, body)
                        try:
                            self.assertTrue(ready.wait(2))
                            self.assertEqual(self.adapter.call('stop', SID), {'status': 'accepted'})
                            self.assertEqual([call[0] for call in self.calls], ['CancelCascadeInvocation', 'ForceStopCascadeTree'])
                        finally:
                            release.set()
                        with self.assertRaisesRegex(BridgeUnavailable, '已停止'):
                            sending.result(2)
                    self.assertFalse(any(method == 'SendUserCascadeMessage' for method, _, _ in self.calls))
                    self.assertIn(SID, self.adapter.hooks._stop_requests)
                    self.assertIn(queued['id'], [row['id'] for row in self.adapter.hooks.get_queued_items(SID)])
                    self.assertFalse(self.adapter.hooks.has_recent_heartbeat(SID))

                    self.assertEqual(self.adapter.call(action, SID, body)['status'], 'accepted')
                    self.assertNotIn(SID, self.adapter.hooks._stop_requests)
                    self.assertEqual(sum(method == 'SendUserCascadeMessage' for method, _, _ in self.calls), 1)
                    self.assertIn(queued['id'], [row['id'] for row in self.adapter.hooks.get_queued_items(SID)])
                    self.assertEqual(self.adapter.hooks.dispatch_hook_event('pre_tool_use', {'conversationId': SID}), {'decision': 'allow'})

    def test_stop_generation_is_scoped_to_native_session_and_surface(self):
        cli = self.adapter.cli
        generation = cli.turn_generation(CID, surface='app')
        cli.stop(CID, surface='ide')
        cli.stop('other-native-cid', surface='app')
        cli.send_user_message(CID, 'keep original scope', surface='app', turn_generation=generation)
        self.assertEqual(self.calls[-1][0], 'SendUserCascadeMessage')
        self.assertEqual(self.calls[-1][2], 'app')
        cli.stop(CID, surface='app')
        callback = mock.Mock()
        with self.assertRaisesRegex(BridgeUnavailable, '已停止'):
            cli.send_user_message(CID, 'stale', surface='app', turn_generation=generation, before_send=callback)
        callback.assert_not_called()
        cli.stop('another-cid', surface='ide')
        snapshot = cli.turn_generation()
        cli.send_user_message(CID, 'explicit next turn', turn_generation=snapshot)
        self.assertEqual(self.calls[-1][0], 'SendUserCascadeMessage')

    def test_stop_while_prepared_send_waits_for_rpc_lock_rejects_submission(self):
        cli = self.adapter.cli
        rpc_lock = threading.RLock()
        owner = threading.get_ident()
        waiting = threading.Event()

        class ObservedLock:
            def __enter__(self):
                if threading.get_ident() != owner:
                    waiting.set()
                rpc_lock.acquire()

            def __exit__(self, *args):
                rpc_lock.release()

        cli._lock = ObservedLock()
        with ThreadPoolExecutor(1) as pool:
            with cli._lock:
                sending = pool.submit(self.adapter.call, 'send', SID, {'text': 'prepared message'})
                self.assertTrue(waiting.wait(2))
                self.assertEqual(self.adapter.call('stop', SID), {'status': 'accepted'})
            with self.assertRaisesRegex(BridgeUnavailable, '已停止'):
                sending.result(2)
        self.assertFalse(any(method == 'SendUserCascadeMessage' for method, _, _ in self.calls))
        self.assertIn(SID, self.adapter.hooks._stop_requests)

    def test_stop_marker_and_generation_advance_together_before_cancel(self):
        cli = self.adapter.cli
        generation = cli.turn_generation(CID, surface='app')
        callback_called = []

        def before_stop():
            callback_called.append(True)
            self.assertEqual(cli._stop_generations[('app', CID)], generation + 1)
            self.assertEqual(self.calls, [])
            self.adapter.hooks.request_stop(SID)

        cli.stop(CID, surface='app', before_stop=before_stop)
        self.assertEqual(callback_called, [True])
        self.assertIn(SID, self.adapter.hooks._stop_requests)
        callback = mock.Mock()
        with self.assertRaisesRegex(BridgeUnavailable, '已停止'):
            cli.send_user_message(CID, 'stale', turn_generation=generation, before_send=callback)
        callback.assert_not_called()

    def test_generation_lock_is_released_while_native_send_waits_for_response(self):
        entered, cancelled = threading.Event(), threading.Event()

        def rpc(method, payload=None, *, surface='app'):
            if method == 'SendUserCascadeMessage':
                entered.set()
                if not cancelled.wait(3):
                    raise AssertionError('stop could not acquire the generation lock')
            if method == 'CancelCascadeInvocation':
                cancelled.set()
            return {}

        self.adapter.cli.call_ls_rpc.side_effect = rpc
        with ThreadPoolExecutor(1) as pool:
            sending = pool.submit(self.adapter.cli.send_user_message, CID, 'native request')
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.adapter.call('stop', SID), {'status': 'accepted'})
            self.assertEqual(sending.result(2), {})
