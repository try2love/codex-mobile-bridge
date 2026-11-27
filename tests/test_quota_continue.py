import copy
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bridge.features.sessions.quota_continue import QuotaContinuation, QuotaDeferred, candidate, quota_windows
from bridge.features.sessions.goal import plan_goal_command
from bridge.features.sessions.quota_continue import scope_stamp


def session(identifier='chat'):
    return SimpleNamespace(id=identifier, connected=True, runtime='desktop', origin='desktop', owner='owner',
        condition=threading.RLock(), state={'modelProvider': 'openai', 'cwd': '/project',
        'threadRuntimeStatus': {'type': 'idle'}, 'requests': [], 'turns': [
            {'turnId': 'failed-turn', 'status': 'failed', 'error': {'codexErrorInfo': 'usageLimitExceeded'}, 'items': []}]})


def usage(remaining, key='account'):
    return {'visible': True, 'accountKey': key, 'updatedAt': time.time(),
            'limits': [{'id': 'codex', 'windows': [{'kind': 'primary', 'remainingPercent': remaining}]}]}


class QuotaContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path('.tmp'))
        self.addCleanup(self.temp.cleanup)
        self.sessions = [session('project-a'), session('project-b')]
        self.bridge = SimpleNamespace(lock=threading.RLock(), live={s.id: s for s in self.sessions},
            host='local', account=SimpleNamespace(read=Mock(return_value=usage(0))),
            continue_after_quota=Mock(), observation_enabled=lambda: True,
            list=Mock(return_value=[{'id': s.id} for s in self.sessions]), activity=Mock())
        self.manager = SimpleNamespace(root=Path(self.temp.name), bridge=self.bridge, lock=threading.RLock(),
            info=SimpleNamespace(source_stamp=Mock(return_value=('config-hash', 'auth-hash'))))
        self.monitor = SimpleNamespace(manager=self.manager, preferences={'autoContinue': True}, closed=threading.Event(), worker=object())
        self.worker = QuotaContinuation(self.monitor)
        def dispatch(*args, before_write=None, **kwargs):
            before_write()
            return {'status': 'accepted'}
        self.bridge.continue_after_quota.side_effect = dispatch

    def scan(self):
        self.worker.next_scan = 0
        self.worker.next_discovery = 0
        self.worker.scan()

    def test_all_projects_resume_once_after_confirmed_recovery_without_notifications(self):
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)
        self.worker = QuotaContinuation(self.monitor)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)

    def test_no_replay_after_unknown_delivery_and_restart(self):
        self.scan()
        def uncertain(*args, before_write=None, **kwargs):
            before_write()
            raise TimeoutError()
        self.bridge.continue_after_quota.side_effect = uncertain
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertTrue(self.worker.error)
        self.worker = QuotaContinuation(self.monitor)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)

    def test_quota_error_plus_fresh_available_quota_resumes_without_observing_zero(self):
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)
        self.assertTrue(all(r['state'] == 'accepted' for r in self.worker.records.values()))

    def test_actual_native_system_error_workspace_credits_failure_is_registered(self):
        for row in self.sessions:
            row.state['threadRuntimeStatus'] = {'type': 'systemError'}
            row.state['turns'][-1]['error'] = {
                'message': 'Your workspace is out of credits. Add credits to continue.',
                'codexErrorInfo': 'usageLimitExceeded', 'additionalDetails': None}
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)

    def test_system_error_network_failure_is_not_quota_recovery(self):
        for row in self.sessions:
            row.state['threadRuntimeStatus'] = {'type': 'systemError'}
            row.state['turns'][-1]['error'] = {'message': 'Network connection failed', 'codexErrorInfo': 'connectionFailed'}
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()

    def test_rejected_native_stream_is_visible_even_when_other_chats_connect(self):
        self.sessions[0].connected = False
        self.sessions[0].desktop_stream_error = 'unrecognized native state'
        self.sessions[1].state['turns'][-1]['status'] = 'completed'
        self.scan()
        value = self.worker.public()
        self.assertEqual(value['incompatible'], 1)
        self.assertEqual(value['state'], 'error')
        self.assertTrue(value['error'])

    def test_quota_query_failure_still_registers_tasks_and_recovers_after_restart(self):
        self.bridge.account.read.side_effect = OSError('temporary outage')
        self.scan()
        self.assertEqual(self.worker.public()['pending'], 2)
        self.assertEqual(self.worker.public()['state'], 'error')
        self.worker = QuotaContinuation(self.monitor)
        self.bridge.account.read.side_effect = None
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)

    def test_preflight_failure_retries_same_submission_instead_of_permanently_consuming_attempt(self):
        self.bridge.account.read.return_value = usage(100)
        self.bridge.continue_after_quota.side_effect = QuotaDeferred('connection unavailable')
        self.scan()
        identifiers = [r['submissionId'] for r in self.worker.records.values()]
        self.assertTrue(all(r['state'] == 'waiting' and not r.get('attempted') for r in self.worker.records.values()))
        def dispatch(*args, before_write=None, **kwargs):
            before_write()
            return {'status': 'accepted'}
        self.bridge.continue_after_quota.side_effect = dispatch
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 4)
        self.assertEqual(identifiers, [r['submissionId'] for r in self.worker.records.values()])
        self.assertEqual(self.worker.public()['resumed'], 2)

    def test_crash_after_durable_dispatch_marker_never_replays(self):
        self.scan()
        for record in self.worker.records.values():
            record.update(state='dispatching', attempted=True)
        self.worker.save()
        self.worker = QuotaContinuation(self.monitor)
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()
        self.assertEqual(self.worker.public()['unknown'], 2)

    def test_unrelated_review_bucket_does_not_block_codex_quota(self):
        value = usage(100)
        value['limits'].append({'id': 'code-review', 'windows': [{'kind': 'primary', 'remainingPercent': 0}]})
        self.bridge.account.read.return_value = value
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)

    def test_legacy_unknown_attempt_cannot_be_replayed_by_upgrade(self):
        import hashlib
        import json
        for row in self.sessions:
            key = hashlib.sha256(json.dumps(['local', row.id, candidate(row), 'account', ('config-hash',)],
                                           sort_keys=True).encode()).hexdigest()
            self.worker.records[key] = {'attempted': True}
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()
        self.assertEqual(self.worker.public()['unknown'], 2)

    def test_newer_canonical_stream_and_quota_error_item_are_recognized(self):
        from bridge.clients.codex.ipc import validate_stream_state
        row = session()
        turn = row.state['turns'][-1]
        turn.pop('error')
        turn['items'] = [{'type': 'error', 'message': 'Usage limit reached. Try again later.'}]
        row.state.update(id='chat', turnHistory={'kind': 'canonical', 'history': {
            'entitiesByKey': {'turn': turn}, 'islands': [{'entries': [{'value': 'turn'}]}]}})
        validate_stream_state(13, row.state)
        self.assertIsNotNone(candidate(row))
        turn['items'] = [{'type': 'agentMessage', 'text': "You've hit your usage limit"}]
        self.assertIsNone(candidate(row), 'assistant text must not authorize an automatic send')

    def test_manual_interrupt_wins_even_when_goal_and_error_still_say_quota_limited(self):
        row = session()
        row.state['turns'][-1]['status'] = 'interrupted'
        self.assertIsNone(candidate(row))
        row.state['threadGoal'] = {'objective': 'Work', 'status': 'usageLimited'}
        self.assertIsNone(candidate(row))

    def test_manual_stop_survives_restart_and_global_toggle_but_new_turn_can_resume(self):
        self.scan()
        self.worker.note_manual_stop('project-a', 'failed-turn')
        self.monitor.preferences['autoContinue'] = False
        self.worker = QuotaContinuation(self.monitor)
        self.monitor.preferences['autoContinue'] = True
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 1)
        self.assertEqual(self.bridge.continue_after_quota.call_args.args[0], 'project-b')
        self.sessions[0].state['turns'][-1]['turnId'] = 'new-user-task'
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)
        self.assertEqual(self.bridge.continue_after_quota.call_args.args[0], 'project-a')

    def test_available_quota_never_restarts_network_or_context_failures(self):
        self.bridge.account.read.return_value = usage(100)
        for error in ({'message': 'network connection lost'}, {'httpStatusCode': 429},
                      {'codexErrorInfo': 'contextWindowExceeded'}):
            for row in self.sessions:
                row.state['turns'][-1]['error'] = error
            self.scan()
        self.bridge.continue_after_quota.assert_not_called()
        self.bridge.account.read.assert_not_called()

    def test_changed_account_config_or_window_cannot_resume(self):
        self.scan()
        for value, stamp in [(usage(100, 'another-account'), ('config-hash', 'auth-hash')),
                             (usage(100), ('new-config', 'auth-hash'))]:
            self.bridge.account.read.return_value = value
            self.manager.info.source_stamp.return_value = stamp
            self.scan()
        self.bridge.continue_after_quota.assert_not_called()

    def test_changed_turn_manual_pause_completion_and_archive_are_excluded(self):
        self.scan()
        self.bridge.account.read.return_value = usage(100)
        self.sessions[0].state['turns'][-1].update(turnId='manual-new-turn',status='completed')
        self.sessions[1].state['threadGoal'] = {'objective': 'Work', 'status': 'paused'}
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()
        self.sessions[0].state['turns'][-1]['status'] = 'completed'
        self.sessions[1].state.pop('threadGoal')
        self.bridge.list.return_value = [{'id': self.sessions[0].id}]
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()

    def test_disabled_does_not_discover_or_query_quota(self):
        self.monitor.preferences['autoContinue'] = False
        self.scan()
        self.bridge.list.assert_not_called()
        self.bridge.account.read.assert_not_called()

    def test_per_chat_exclusion_survives_restart_and_blocks_dispatch(self):
        sid = str(uuid.uuid4())
        self.sessions[0].id = sid
        self.bridge.live = {s.id: s for s in self.sessions}
        self.bridge.list.return_value = [{'id': s.id} for s in self.sessions]
        self.scan()
        self.worker.configure({'threadId': sid, 'excluded': True})
        self.worker = QuotaContinuation(self.monitor)
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 1)
        self.assertEqual(self.bridge.continue_after_quota.call_args.args[0], 'project-b')
        self.assertTrue(next(t for t in self.worker.public()['tasks'] if t['threadId'] == sid)['excluded'])
        self.worker.configure({'threadId': sid, 'excluded': False})
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)

    def test_exclusion_during_preflight_prevents_durable_write_marker(self):
        sid = str(uuid.uuid4())
        self.sessions[:] = [session(sid)]
        self.bridge.live = {sid: self.sessions[0]}
        self.bridge.list.return_value = [{'id': sid}]
        self.bridge.account.read.return_value = usage(100)
        def exclude_before_write(*args, before_write=None, **kwargs):
            self.worker.configure({'threadId': sid, 'excluded': True})
            before_write()
            self.fail('Excluded task must not be sent')
        self.bridge.continue_after_quota.side_effect = exclude_before_write
        self.scan()
        self.assertFalse(any(r.get('attempted') for r in self.worker.records.values()))

    def test_routine_token_refresh_does_not_cancel_same_account_recovery(self):
        self.scan()
        self.manager.info.source_stamp.return_value = ('config-hash', 'refreshed-auth-hash')
        self.bridge.account.read.return_value = usage(100)
        self.scan()
        self.assertEqual(self.bridge.continue_after_quota.call_count, 2)

    def test_toggle_changed_during_quota_query_cancels_that_scan(self):
        self.scan()
        def read(**kwargs):
            self.worker.generation += 1
            return usage(100)
        self.bridge.account.read.side_effect = read
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()

    def test_pending_request_busy_provider_and_non_quota_failures_excluded(self):
        original = session()
        variants = [dict(connected=False), dict(runtime='cli'), dict(origin='ide')]
        for changes in variants:
            row = copy.copy(original)
            for key, value in changes.items():
                setattr(row, key, value)
            self.assertIsNone(candidate(row))
        for update in [{'modelProvider': 'custom'}, {'threadRuntimeStatus': {'type': 'active'}},
                       {'turns': [{'turnId': 'x', 'status': 'failed', 'error': {'httpStatusCode': 429}}]},
                       {'threadGoal': {'objective': 'Work', 'status': 'budgetLimited'}}]:
            row = copy.copy(original)
            row.state = {**original.state, **update}
            self.assertIsNone(candidate(row))

    def test_goal_resume_only_from_usage_limited_or_paused(self):
        goal = {'objective': 'Work', 'status': 'usageLimited'}
        row = session()
        row.state['threadGoal'] = goal
        self.assertIsNotNone(candidate(row))
        self.assertEqual(plan_goal_command('resume', 'Work', goal)[0], 'execute')
        for status in ('blocked', 'budgetLimited', 'complete'):
            self.assertEqual(plan_goal_command('resume', 'Work', {**goal, 'status': status})[0], 'invalid')

    def test_stale_errors_unknown_values_and_other_exhausted_windows_block(self):
        for value in [{**usage(100), 'error': 'offline'}, {**usage(100), 'updatedAt': time.time()-360},
                      usage(None), usage(float('nan'))]:
            self.assertIsNone(quota_windows(value))
        self.scan()
        value = usage(100)
        value['limits'][0]['windows'].append({'kind': 'secondary', 'remainingPercent': 0})
        self.bridge.account.read.return_value = value
        self.scan()
        self.bridge.continue_after_quota.assert_not_called()


import test_modes as mode_support
from test_modes import THREAD, GoalStub
import uuid


class QuotaDispatchTests(unittest.TestCase):
    setUp = mode_support.ModeTests.setUp
    tearDown = mode_support.ModeTests.tearDown
    live = mode_support.ModeTests.live
    calls = mode_support.ModeTests.calls

    def prepare(self, goal=None):
        self.fixture.state.update(copy.deepcopy(session().state))
        if goal:
            self.fixture.state['threadGoal'] = goal
        live = self.live(**copy.deepcopy(self.fixture.state))
        self.bridge.accounts.monitor.preferences['autoContinue'] = True
        self.bridge.account.read = Mock(return_value=usage(100))
        def audit(**kwargs):
            kwargs['validate_state'](self.fixture.state, live.owner)
        self.bridge.assert_desktop_idle = Mock(side_effect=audit)
        return candidate(live), scope_stamp(self.bridge.accounts)

    def test_dispatch_preserves_original_owner_and_records_submission(self):
        expected, stamp = self.prepare()
        identifier = str(uuid.uuid4())
        result = self.bridge.continue_after_quota(THREAD, expected, identifier, stamp, 'account')
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]['targetClientId'], 'owner')
        self.assertEqual(self.bridge.submissions[THREAD+':'+identifier]['status'], 'accepted')

    def test_real_worker_dispatches_recovered_failure_without_ever_polling_zero(self):
        self.prepare()
        self.bridge.list = Mock(return_value=[{'id': THREAD}])
        self.bridge.activity = Mock()
        worker = self.bridge.accounts.monitor.continuation
        worker.scan()
        self.assertEqual(len(self.calls()), 1)

    def test_real_worker_dispatches_actual_system_error_shape_through_quota_preflight(self):
        self.prepare()
        self.fixture.state['threadRuntimeStatus'] = {'type': 'systemError'}
        self.live(threadRuntimeStatus={'type': 'systemError'})
        self.bridge.list = Mock(return_value=[{'id': THREAD}])
        self.bridge.activity = Mock()
        worker = self.bridge.accounts.monitor.continuation
        worker.scan()
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue(self.bridge.assert_desktop_idle.call_args.kwargs['allow_quota_error'])
        self.assertEqual([r['state'] for r in worker.records.values()], ['accepted'])
        worker.next_scan = 0
        worker.scan()
        self.assertEqual(len(self.calls()), 1)

    def test_real_worker_retries_transient_native_preflight_then_sends_once(self):
        self.prepare()
        audit = self.bridge.assert_desktop_idle.side_effect
        self.bridge.assert_desktop_idle.side_effect = TimeoutError('temporary IPC outage')
        self.bridge.list = Mock(return_value=[{'id': THREAD}])
        self.bridge.activity = Mock()
        worker = self.bridge.accounts.monitor.continuation
        worker.scan()
        self.assertEqual(self.calls(), [])
        self.assertEqual([r['state'] for r in worker.records.values()], ['waiting'])
        self.bridge.assert_desktop_idle.side_effect = audit
        worker.next_scan = 0
        worker.scan()
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual([r['state'] for r in worker.records.values()], ['accepted'])

    def test_manual_stop_intent_rejects_stale_quota_snapshot(self):
        expected, stamp = self.prepare()
        with self.assertRaises(ValueError):
            self.bridge.interrupt(THREAD)  # The quota failure already made the native turn idle.
        self.assertTrue(self.bridge.accounts.monitor.continuation.is_manually_stopped(THREAD, expected['turnId']))
        with self.assertRaises(QuotaDeferred):
            self.bridge.continue_after_quota(THREAD, expected, str(uuid.uuid4()), stamp, 'account')
        self.assertEqual(self.calls(), [])

    def test_manual_stop_during_preflight_prevents_native_write(self):
        expected, stamp = self.prepare()
        audit = self.bridge.assert_desktop_idle.side_effect
        def stop_before_write(**kwargs):
            self.bridge.accounts.monitor.continuation.note_manual_stop(THREAD, expected['turnId'])
            audit(**kwargs)
        self.bridge.assert_desktop_idle.side_effect = stop_before_write
        commit = Mock()
        with self.assertRaises(QuotaDeferred):
            self.bridge.continue_after_quota(THREAD, expected, str(uuid.uuid4()), stamp, 'account', before_write=commit)
        commit.assert_not_called()
        self.assertEqual(self.calls(), [])

    def test_fresh_native_turn_change_prevents_dispatch_even_when_cached_idle(self):
        expected, stamp = self.prepare()
        self.fixture.state['turns'][-1]['turnId'] = 'manual-new-turn'
        with self.assertRaises(ValueError):
            self.bridge.continue_after_quota(THREAD, expected, str(uuid.uuid4()), stamp, 'account')
        self.assertEqual(self.calls(), [])

    def test_quota_exhausted_again_before_dispatch_is_rejected(self):
        expected, stamp = self.prepare()
        self.bridge.account.read.return_value = usage(0)
        with self.assertRaises(ValueError):
            self.bridge.continue_after_quota(THREAD, expected, str(uuid.uuid4()), stamp, 'account')
        self.assertEqual(self.calls(), [])

    def test_usage_limited_goal_resumes_through_native_activation(self):
        goal = {'objective': 'Finish the project', 'status': 'usageLimited'}
        expected, stamp = self.prepare(goal)
        stub = GoalStub(self.bridge)
        stub.goals[THREAD] = goal
        self.bridge.goal = stub
        result = self.bridge.continue_after_quota(THREAD, expected, str(uuid.uuid4()), stamp, 'account')
        self.assertTrue(result['confirmed'])
        self.assertEqual(stub.status_calls, [(THREAD, 'active')])
        self.assertEqual(len(self.calls()), 1)

    def test_native_manual_pause_wins_over_cached_usage_limited_goal(self):
        goal = {'objective': 'Finish the project', 'status': 'usageLimited'}
        expected, stamp = self.prepare(goal)
        stub = GoalStub(self.bridge)
        stub.goals[THREAD] = {**goal, 'status': 'paused'}
        self.bridge.goal = stub
        with self.assertRaises(ValueError):
            self.bridge.continue_after_quota(THREAD, expected, str(uuid.uuid4()), stamp, 'account')
        self.assertEqual(stub.status_calls, [])
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
