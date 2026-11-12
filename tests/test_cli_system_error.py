"""Terminal native errors must not strand account selection or subscriptions."""
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock

from bridge.clients.codex.release import check_release, release_thread
from bridge.features.accounts.cli_sync import LocalCliSync
from bridge.features.sessions.create import check_archive_runtime


class SystemErrorTests(unittest.TestCase):
    def setUp(self):
        self.thread = {'id': 'failed-a', 'status': {'type': 'systemError'}}
        self.turns = [{'id': 'a-turn', 'status': 'failed', 'completedAt': 1794332820}]
        self.background = {'data': []}
        self.goal = None
        self.loaded = ['failed-a']
        self.client = Mock()
        self.client.connect.return_value = {'codexHome': str(Path('.tmp').resolve()),
                                           'userAgent': 'codex-mobile-bridge-cli/' + ('0.167.0-alpha.3.2' if sys.platform == 'win32' else '0.164.0') + ' (fixture)'}
        self.client.loaded_threads.side_effect = lambda: list(self.loaded)
        self.client.request.side_effect = self.request

    def request(self, method, params, **_kwargs):
        if method == 'thread/read':
            if params['threadId'] != 'failed-a':
                return {'thread': {'id': params['threadId'], 'status': {'type': 'active',
                                  'activeFlags': ['waitingOnApproval']}}}
            return {'thread': self.thread}
        if method == 'thread/turns/list':
            self.assertEqual(params, {'threadId': 'failed-a', 'limit': 1, 'sortDirection': 'desc'})
            return {'data': self.turns}
        if method == 'thread/backgroundTerminals/list':
            return self.background
        if method == 'thread/goal/get':
            return {'goal': self.goal}
        if method == 'thread/unsubscribe':
            return {'status': 'unsubscribed'}
        self.fail(method)

    def check_idle(self):
        LocalCliSync.check_idle_daemon(self.client, Path('.tmp').resolve())

    def release(self):
        return release_thread(self.client.request, 'failed-a', allow_terminal_error=True)

    def assert_no_write(self):
        self.assertTrue(all(call.args[0] in ('thread/read', 'thread/turns/list',
            'thread/backgroundTerminals/list', 'thread/goal/get') for call in self.client.request.call_args_list))

    def test_failed_a_with_completed_timestamp_does_not_block_account_b_preflight(self):
        self.check_idle()
        self.assertEqual(self.thread['status'], {'type': 'systemError'})
        self.assert_no_write()

    def test_terminal_error_can_unsubscribe_but_loaded_thread_remains_detached(self):
        result = self.release()
        self.assertEqual(result['status'], 'detached')
        self.assertTrue(result['unsubscribed'])
        self.assertEqual(sum(call.args[0] == 'thread/unsubscribe' for call in self.client.request.call_args_list), 1)
        self.assertEqual(self.thread['status'], {'type': 'systemError'})

    def test_read_only_check_preserves_confirmed_detach_on_system_error(self):
        self.assertEqual(check_release(self.client.request, 'failed-a', True)['status'], 'detached')
        self.assertEqual(check_release(self.client.request, 'failed-a', False)['status'], 'unknown')
        self.assert_no_write()

    def test_default_archive_guard_still_rejects_system_error(self):
        with self.assertRaises(ValueError):
            check_archive_runtime(self.client.request, ['failed-a'])
        self.assert_no_write()

    def test_unverified_native_version_cannot_enable_error_terminal_release_or_switch(self):
        self.client.connect.return_value['userAgent'] = 'codex-mobile-bridge-cli/0.165.0 (fixture)'
        with self.assertRaises(ValueError):
            self.check_idle()
        with self.assertRaisesRegex(ValueError, '版本'):
            release_thread(self.client.request, 'failed-a')
        self.assert_no_write()

    def test_state_change_after_terminal_turn_is_read_stays_blocked(self):
        original = self.request
        def request(method, params, **kwargs):
            if method == 'thread/turns/list':
                self.thread = {**self.thread, 'status': {'type': 'active', 'activeFlags': []}}
            return original(method, params, **kwargs)
        self.client.request.side_effect = request
        with self.assertRaises(ValueError):
            self.release()
        self.assert_no_write()

    def test_unknown_goal_or_background_does_not_enable_release(self):
        self.goal = {'status': 'unrecognized'}
        with self.assertRaises(ValueError):
            self.check_idle()
        with self.assertRaises(ValueError):
            self.release()
        self.goal = None
        self.background = {}
        with self.assertRaises(ValueError):
            self.release()
        self.assert_no_write()

    def test_error_before_turn_complete_or_missing_turn_metadata_stays_blocked(self):
        for rows in ([], None, [{'id': 'a-turn', 'status': 'inProgress', 'completedAt': None}],
                     [{'id': 'a-turn', 'status': 'failed'}],
                     [{'id': 'a-turn', 'status': 'failed', 'completedAt': None}]):
            with self.subTest(rows=rows):
                self.turns = rows
                with self.assertRaises(ValueError):
                    self.check_idle()
                with self.assertRaises(ValueError) as caught:
                    self.release()
                self.assertTrue(caught.exception.release_result['notSent'])
        self.assert_no_write()

    def test_pending_requests_or_active_flags_contradict_terminal_error(self):
        for field, value in (('requests', [{'id': 3}]), ('requests', None)):
            with self.subTest(field=field, value=value):
                self.thread[field] = value
                with self.assertRaises(ValueError):
                    self.check_idle()
                with self.assertRaises(ValueError):
                    self.release()
        self.thread.pop('requests')
        self.thread['status']['activeFlags'] = ['waitingOnApproval']
        with self.assertRaises(ValueError):
            self.check_idle()
        with self.assertRaises(ValueError):
            self.release()
        self.assert_no_write()

    def test_background_goal_and_other_loaded_agent_still_block_account_switch(self):
        for background, goal, loaded in (({'data': [{'id': 'background'}]}, None, ['failed-a']),
                ({}, None, ['failed-a']), ({'data': []}, {'status': 'active'}, ['failed-a']),
                ({'data': []}, None, ['failed-a', 'child-agent'])):
            with self.subTest(background=background, goal=goal, loaded=loaded):
                self.background, self.goal, self.loaded = background, goal, loaded
                with self.assertRaises(ValueError):
                    self.check_idle()
        self.assert_no_write()

    def test_terminal_error_with_background_or_goal_does_not_release_early(self):
        for background, goal in (({'data': [{'id': 'background'}]}, None),
                                 ({'data': []}, {'status': 'active'})):
            with self.subTest(background=background, goal=goal):
                self.background, self.goal = background, goal
                self.assertEqual(self.release(), {'status': 'waiting'})
        self.assert_no_write()
