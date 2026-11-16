import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity import hook_ipc
from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.cli_session import AntigravityCli
from bridge.clients.errors import BridgeUnavailable


ROOT = Path(__file__).resolve().parents[1]
CID = 'agy-app:11111111-2222-4333-8444-555555555555'


class AntigravityStopHookTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(hook_ipc.HookIPCServer, '_start_server'):
            self.server = hook_ipc.HookIPCServer(temporary.name)
        self.addCleanup(self.server.close)

    def dispatch(self, event, **payload):
        return self.server.dispatch_hook_event(event, {'conversationId': CID, **payload})

    def start_hook(self):
        replies = []
        thread = threading.Thread(target=lambda: replies.append(self.dispatch('pre_tool_use')))
        thread.start()
        self.addCleanup(thread.join, 2)
        return thread, replies

    def adapter(self):
        adapter = Antigravity.__new__(Antigravity)
        adapter.hooks = self.server
        adapter.cli = AntigravityCli()
        adapter.cli.available = mock.Mock(return_value=True)
        adapter.cli._model = mock.Mock(return_value=None)
        adapter.cli.call_ls_rpc = mock.Mock(return_value={})
        adapter.cli.send_user_message = mock.Mock(wraps=adapter.cli.send_user_message)
        adapter.store = mock.Mock()
        adapter.store.is_native_busy.return_value = False
        adapter.store.resolve_session.return_value = {
            'id': CID, 'nativeId': 'native-cid', 'surfaceDirKey': 'app', 'surfaceRoot': 'root',
        }
        adapter._session = mock.Mock(return_value={'model': '', 'effort': ''})
        adapter._waiting_state = mock.Mock(return_value=({}, set()))
        adapter._commands_by_name = mock.Mock(return_value={
            name: {'name': name, 'info': {'name': name}} for name in ('goal', 'compact')
        })
        return adapter

    def test_explicit_idle_turn_clears_unacknowledged_stop_without_replaying_queue(self):
        self.server.set_permission_mode(CID, 'auto-allow')
        steer = self.server.enqueue_follow_up(CID, 'old steer', mode='steer')
        queued = self.server.enqueue_follow_up(CID, 'old queued')
        for action, body in [('send', {'text': 'continue now'}),
                             ('goal', {'operation': 'set', 'objective': 'continue goal'}),
                             ('compact', {})]:
            with self.subTest(action=action):
                adapter = self.adapter()
                self.assertEqual(adapter.call('stop', CID), {'status': 'accepted'})
                # The original service reports success but never emits a terminal stop Hook.
                self.assertIn(CID, self.server._stop_requests)
                self.assertEqual(self.dispatch('pre_tool_use').get('decision'), 'deny')
                result = adapter.call(action, CID, body)
                self.assertEqual(result['status'], 'accepted')
                adapter._waiting_state.assert_called_with('app', live=True, fresh=True)
                self.assertNotIn(CID, self.server._stop_requests)
                self.assertFalse(self.server.has_recent_heartbeat(CID))
                self.assertEqual([row['id'] for row in self.server.get_queued_items(CID)], [steer['id'], queued['id']])
                self.assertEqual(adapter.cli.send_user_message.call_count, 1)
                self.assertEqual(self.dispatch('pre_tool_use'), {'decision': 'allow'})

    def test_busy_or_waiting_turn_cannot_clear_stop_marker(self):
        for action, body in [('send', {'text': 'continue now'}),
                             ('goal', {'operation': 'set', 'objective': 'continue goal'}),
                             ('compact', {})]:
            for busy, waiting in [(True, ({}, set())), (False, ({'native-cid': [{}]}, set()))]:
                with self.subTest(action=action, busy=busy):
                    adapter = self.adapter()
                    adapter.store.is_native_busy.return_value = busy
                    adapter._waiting_state.return_value = waiting
                    self.server.request_stop(CID)
                    with self.assertRaises(BridgeUnavailable):
                        adapter.call(action, CID, body)
                    self.assertIn(CID, self.server._stop_requests)
                    adapter.cli.send_user_message.assert_not_called()

    def test_waiting_queue_request_does_not_clear_stop_marker(self):
        adapter = self.adapter()
        adapter._waiting_state.return_value = ({'native-cid': [{}]}, set())
        self.server.request_stop(CID)
        adapter.call('send', CID, {'text': 'queued followup', 'mode': 'queue'})
        self.assertIn(CID, self.server._stop_requests)

    def test_late_tool_is_denied_in_every_permission_mode_without_heartbeat(self):
        for mode in ('desktop', 'auto-allow', 'mobile-ask'):
            with self.subTest(mode=mode):
                self.server.set_permission_mode(CID, mode)
                self.server.request_stop(CID)
                # A timeout keeps the broken mobile-ask path deterministic.
                with mock.patch.object(hook_ipc, 'APPROVAL_TIMEOUT', 0):
                    reply = self.dispatch('pre_tool_use')
                self.assertEqual(reply.get('decision'), 'deny')
                self.assertFalse(self.server.has_recent_heartbeat(CID))
                self.assertFalse(self.server.has_pending_approvals(CID))

    def test_stop_between_tool_dispatch_and_approval_registration(self):
        self.server.set_permission_mode(CID, 'mobile-ask')
        constructing, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = hook_ipc._PendingApproval

        def pending(*args):
            constructing.set()
            if not release.wait(2):
                raise AssertionError('approval construction was not released')
            return original(*args)

        with mock.patch.object(hook_ipc, '_PendingApproval', side_effect=pending), \
                mock.patch.object(hook_ipc, 'APPROVAL_TIMEOUT', 0):
            thread, replies = self.start_hook()
            self.assertTrue(constructing.wait(2))
            self.server.request_stop(CID)
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(replies[0].get('decision'), 'deny')
        self.assertFalse(self.server.has_recent_heartbeat(CID))
        self.assertFalse(self.server.has_pending_approvals(CID))

    def test_stopped_approval_cannot_revive_after_explicit_next_turn_clears_marker(self):
        self.server.set_permission_mode(CID, 'mobile-ask')
        waiting, awakened, release = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = hook_ipc._PendingApproval

        def pending(*args):
            item = original(*args)
            wait = item.event.wait

            def gated_wait(timeout):
                waiting.set()
                result = wait(timeout)
                awakened.set()
                if not release.wait(2):
                    raise AssertionError('approval response was not released')
                return result

            item.event.wait = gated_wait
            return item

        with mock.patch.object(hook_ipc, '_PendingApproval', side_effect=pending):
            thread, replies = self.start_hook()
            self.assertTrue(waiting.wait(2))
            approval = self.server.pending_approvals_for(CID)[0]
            self.server.request_stop(CID)
            self.assertTrue(awakened.wait(2))
            self.server.resolve_approval(CID, approval['id'], 'accept')
            self.assertEqual(self.dispatch('stop', fullyIdle=True, terminationReason='model_stop'), {})
            self.assertIn(CID, self.server._stop_requests)
            self.server.begin_user_turn(CID)
            self.assertNotIn(CID, self.server._stop_requests)
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(replies[0].get('decision'), 'deny')
        self.assertFalse(self.server.has_recent_heartbeat(CID))

    def test_stop_acknowledgements_preserve_followups_until_user_continues(self):
        steer = self.server.enqueue_follow_up(CID, 'steer later', mode='steer')
        queued = self.server.enqueue_follow_up(CID, 'queue later')
        self.server.request_stop(CID)
        for _ in range(2):
            self.assertEqual(self.dispatch('post_invocation'), {'terminationBehavior': 'terminate'})
            self.assertIn(CID, self.server._stop_requests)
            self.assertFalse(self.server.has_recent_heartbeat(CID))
        self.server.set_permission_mode(CID, 'auto-allow')
        for _ in range(2):
            self.assertEqual(self.dispatch('stop', fullyIdle=True, terminationReason='model_stop'), {})
            self.assertIn(CID, self.server._stop_requests)
            self.assertFalse(self.server.has_recent_heartbeat(CID))
            self.assertEqual(self.dispatch('pre_tool_use').get('decision'), 'deny')
            self.assertFalse(self.server.has_recent_heartbeat(CID))
            self.assertEqual([row['id'] for row in self.server.get_queued_items(CID)], [steer['id'], queued['id']])

        # A later user invocation has normal Hook behavior and can consume the preserved queue.
        self.server.begin_user_turn(CID)
        self.assertEqual(self.dispatch('pre_tool_use'), {'decision': 'allow'})
        self.assertTrue(self.server.has_recent_heartbeat(CID))
        self.assertEqual(self.dispatch('post_invocation'), {
            'injectSteps': [{'userMessage': 'steer later'}], 'terminationBehavior': 'force_continue',
        })
        self.assertEqual(self.dispatch('stop', fullyIdle=True, terminationReason='model_stop'), {
            'decision': 'continue', 'reason': '手机端发来新追问，请直接继续处理并回复用户：\n\nqueue later',
        })
        self.assertEqual(self.server.get_queued_items(CID), [])

    def test_normal_pending_approval_still_allows_and_denies(self):
        self.assertEqual(self.dispatch('pre_tool_use'), {'decision': 'ask'})
        self.server.set_permission_mode(CID, 'mobile-ask')
        original = hook_ipc._PendingApproval
        for decision, expected in [('accept', 'allow'), ('decline', 'deny')]:
            with self.subTest(decision=decision):
                waiting = threading.Event()

                def pending(*args):
                    item = original(*args)
                    wait = item.event.wait

                    def observed_wait(timeout):
                        waiting.set()
                        return wait(timeout)

                    item.event.wait = observed_wait
                    return item

                with mock.patch.object(hook_ipc, '_PendingApproval', side_effect=pending):
                    thread, replies = self.start_hook()
                    self.assertTrue(waiting.wait(2))
                    approval = self.server.pending_approvals_for(CID)[0]
                    self.server.resolve_approval(CID, approval['id'], decision)
                    thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(replies[0].get('decision'), expected)
                self.assertTrue(self.server.has_recent_heartbeat(CID))
                self.assertFalse(self.server.has_pending_approvals(CID))
