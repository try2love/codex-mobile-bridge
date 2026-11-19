import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity import hook_ipc
from bridge.clients.antigravity.setup import build_hooks_manifest, ensure_plugin, inspect_plugin

ROOT = Path(__file__).resolve().parents[1]
CID = 'agy-app:11111111-2222-4333-8444-555555555555'


class AntigravityPreInvocationHookTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        with mock.patch.object(hook_ipc.HookIPCServer, '_start_server'):
            self.server = hook_ipc.HookIPCServer(Path(self.temp.name) / 'state')
        self.addCleanup(self.server.close)

    def dispatch(self, event, **payload):
        return self.server.dispatch_hook_event(event, {'conversationId': CID, **payload})

    def test_hooks_manifest_registers_pre_invocation_and_flags_older_manifest_stale(self):
        home = Path(self.temp.name) / 'home'
        state_dir = Path(self.temp.name) / 'state'
        manifest = build_hooks_manifest(state_dir)
        spec = manifest['codex-mobile-bridge']
        self.assertIn('PreInvocation', spec)
        self.assertIn('pre_invocation', spec['PreInvocation'][0]['command'])

        # Simulate legacy hooks.json without PreInvocation -> inspect_plugin detects stale
        ensure_plugin(state_dir, home)
        hooks_path = home / '.gemini' / 'config' / 'plugins' / 'codex-mobile-bridge' / 'hooks.json'
        legacy = json.loads(hooks_path.read_text(encoding='utf-8'))
        legacy['codex-mobile-bridge'].pop('PreInvocation')
        hooks_path.write_text(json.dumps(legacy), encoding='utf-8')
        self.assertFalse(inspect_plugin(state_dir, home)['upToDate'])

        info = ensure_plugin(state_dir, home)
        self.assertTrue(info['changed'])
        self.assertTrue(inspect_plugin(state_dir, home)['upToDate'])

    def test_stale_invocations_in_stopped_turn_terminate_but_new_desktop_turn_unlocks(self):
        self.server.set_permission_mode(CID, 'auto-allow')
        # Normal turn starts at initialNumSteps=10
        self.assertEqual(self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=10), {})
        self.assertTrue(self.server.has_recent_heartbeat(CID))

        # Mobile presses Stop mid-turn
        self.server.request_stop(CID)
        self.assertIn(CID, self.server._stop_requests)

        # Late invocation #2 of the SAME stopped turn is immediately terminated
        self.assertEqual(
            self.dispatch('pre_invocation', invocationNum=2, initialNumSteps=10),
            {'terminationBehavior': 'terminate'},
        )
        self.assertIn(CID, self.server._stop_requests)
        self.assertFalse(self.server.has_recent_heartbeat(CID))
        self.assertEqual(self.dispatch('pre_tool_use').get('decision'), 'deny')

        # Terminal Stop fires for the stopped turn (still does NOT immediately clear before next turn)
        self.assertEqual(self.dispatch('stop', fullyIdle=True, terminationReason='TERMINAL_CUSTOM_HOOK'), {})
        self.assertIn(CID, self.server._stop_requests)

        # Even after stop_ack, a replayed/stale invocation with the same initialNumSteps=10 stays blocked
        self.assertEqual(
            self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=10),
            {'terminationBehavior': 'terminate'},
        )
        self.assertIn(CID, self.server._stop_requests)

        # User returns to desktop and sends a new message -> new turn invocationNum=1, initialNumSteps=13
        self.assertEqual(self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=13), {})
        self.assertNotIn(CID, self.server._stop_requests)
        self.assertTrue(self.server.has_recent_heartbeat(CID))
        # Tools and normal invocations work again without requiring a mobile send or restart
        self.assertEqual(self.dispatch('pre_tool_use'), {'decision': 'allow'})

    def test_new_desktop_turn_unlocks_even_when_stop_hook_was_never_emitted(self):
        self.server.set_permission_mode(CID, 'auto-allow')
        # Turn 1 observed at initialNumSteps=6
        self.assertEqual(self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=6), {})
        self.server.request_stop(CID)

        # Same-turn late invocation #1 or #2 with initialNumSteps=6 must NOT unlock
        self.assertEqual(
            self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=6),
            {'terminationBehavior': 'terminate'},
        )
        self.assertEqual(
            self.dispatch('pre_invocation', invocationNum=2, initialNumSteps=8),
            {'terminationBehavior': 'terminate'},
        )
        self.assertIn(CID, self.server._stop_requests)

        # Brand new desktop turn starts at invocationNum=1 with strictly greater initialNumSteps=9
        self.assertEqual(self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=9), {})
        self.assertNotIn(CID, self.server._stop_requests)
        self.assertEqual(self.dispatch('pre_tool_use'), {'decision': 'allow'})

    def test_stop_before_first_pre_invocation_never_falsely_unlocks_stopped_turn(self):
        self.server.set_permission_mode(CID, 'auto-allow')
        # Stop arrives before the stopped turn's own invocationNum=1 hits the IPC server
        self.server.request_stop(CID)
        self.assertEqual(
            self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=5),
            {'terminationBehavior': 'terminate'},
        )
        self.assertIn(CID, self.server._stop_requests)

        # Subsequent desktop turn at initialNumSteps=7 cleanly unlocks
        self.assertEqual(self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=7), {})
        self.assertNotIn(CID, self.server._stop_requests)

    def test_stop_hook_accepts_native_no_tool_call_termination_reason(self):
        self.server.enqueue_follow_up(CID, 'follow up question')
        reply = self.dispatch('stop', fullyIdle=True, terminationReason='NO_TOOL_CALL')
        self.assertEqual(reply.get('decision'), 'continue')
        self.assertIn('follow up question', reply.get('reason', ''))
        self.assertEqual(self.server.get_queued_items(CID), [])

    def test_stop_ack_without_strict_counter_progress_never_unlocks(self):
        variants = [
            (None, {'invocationNum': 1}),
            (10, {'invocationNum': 1}),
            (None, {'invocationNum': 1, 'initialNumSteps': 20}),
            (10, {'invocationNum': 0, 'initialNumSteps': 20}),
            (10, {'invocationNum': 1, 'initialNumSteps': 20, 'initial_num_steps': 10}),
            (10, {'invocationNum': 1, 'invocation_num': 2, 'initialNumSteps': 20}),
        ]
        for idx, (baseline, payload) in enumerate(variants):
            with self.subTest(baseline=baseline, payload=payload), \
                    mock.patch.object(hook_ipc.HookIPCServer, '_start_server'):
                server = hook_ipc.HookIPCServer(Path(self.temp.name) / f'case-{idx}')
                self.addCleanup(server.close)
                dispatch = lambda event, **fields: server.dispatch_hook_event(event, {'conversationId': CID, **fields})
                if baseline is not None:
                    dispatch('pre_invocation', invocationNum=1, initialNumSteps=baseline)
                queued = server.enqueue_follow_up(CID, 'keep queued')
                server.request_stop(CID)
                dispatch('stop', fullyIdle=True, terminationReason='NO_TOOL_CALL')
                self.assertEqual(dispatch('pre_invocation', **payload), {'terminationBehavior': 'terminate'})
                self.assertFalse(server.has_recent_heartbeat(CID))
                self.assertEqual(dispatch('pre_tool_use')['decision'], 'deny')
                self.assertEqual([item['id'] for item in server.get_queued_items(CID)], [queued['id']])

    def test_late_old_hook_cannot_lower_counter_baseline(self):
        self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=10)
        self.dispatch('post_tool_use', initialNumSteps=6)
        self.server.request_stop(CID)
        self.assertEqual(self.dispatch('pre_invocation', invocationNum=1, initialNumSteps=8),
                         {'terminationBehavior': 'terminate'})
        self.assertIn(CID, self.server._stop_requests)

    def test_no_tool_call_does_not_deliver_queue_without_fully_idle(self):
        self.server.enqueue_follow_up(CID, 'keep queued')
        self.assertEqual(self.dispatch('stop', fullyIdle=False, terminationReason='NO_TOOL_CALL'), {})
        self.assertEqual(len(self.server.get_queued_items(CID)), 1)


if __name__ == '__main__':
    unittest.main()
