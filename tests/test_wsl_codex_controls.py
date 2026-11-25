"""Focused WSL native CLI controls without a distro, model call, or daemon start."""
import copy
import hashlib
import unittest
import uuid
from unittest.mock import patch

from bridge.clients.codex.ipc import IPCError
from tests import test_wsl_entries as fixtures


class WslControlsTests(unittest.TestCase):
    setUp = fixtures.WslSessionTests.setUp
    def make_entry(self):
        entry = fixtures.WslSessionTests.make_entry(self)
        entry.store.history = lambda identifier, **kwargs: {'id': identifier, 'turns': []}
        return entry

    def skills(self):
        original = self.entry.server.request
        def request(method, params):
            if method == 'skills/list':
                self.entry.server.calls.append((method, copy.deepcopy(params)))
                return {'data': [{'cwd': '/other', 'skills': [{'name': 'wrong', 'path': '/wrong'}]},
                    {'cwd': '/home/fixture/project', 'skills': [
                        {'name': 'fixture', 'path': '/home/fixture/.agents/skills/fixture/SKILL.md'},
                        {'name': 'disabled', 'path': '/disabled', 'enabled': False}]}]}
            return original(method, params)
        self.entry.server.request = request
        return hashlib.sha256(b'/home/fixture/.agents/skills/fixture/SKILL.md').hexdigest()

    def test_skills_use_selected_cwd_and_only_trusted_native_paths(self):
        skill = self.skills()
        rows = self.entry.catalog(self.identifier, kind='skills')['skills']
        self.assertEqual([r['id'] for r in rows], [skill])
        self.entry.send(self.identifier, 'fixture', str(uuid.uuid4()), skills=[skill], work_mode='plan')
        sent = [p for m, p in self.entry.server.calls if m == 'turn/start'][0]
        self.assertEqual(sent['input'][1], {'type': 'skill', 'name': 'fixture', 'path': rows[0]['path']})
        self.assertEqual(sent['collaborationMode']['mode'], 'plan')
        self.assertFalse(any(m in ('thread/start', 'config/batchWrite') for m, _ in self.entry.server.calls))

    def test_unknown_skill_path_rejected_before_send_receipt(self):
        self.skills()
        with self.assertRaises(ValueError):
            self.entry.send(self.identifier, 'fixture', str(uuid.uuid4()), skills=['/phone/supplied/path'])
        self.assertFalse(self.entry.receipts_path.exists())
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))

    def test_queue_waits_until_idle_and_sends_once(self):
        self.entry.server.thread['status'] = {'type': 'active'}
        submission = str(uuid.uuid4())
        self.assertEqual(self.entry.send(self.identifier, 'queued', submission, mode='queue')['status'], 'queued')
        self.entry._dispatch_queued(self.identifier)
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))
        self.entry.server.thread['status'] = {'type': 'idle'}
        self.entry._dispatch_queued(self.identifier)
        self.entry._dispatch_queued(self.identifier)
        self.assertEqual(sum(m == 'turn/start' for m, _ in self.entry.server.calls), 1)
        self.assertFalse(self.entry._queue(self.identifier))
        self.assertEqual(self.entry.send(self.identifier, 'queued', submission, mode='queue')['status'], 'accepted')

    def test_queued_project_reference_keeps_identity_and_checks_target_at_dispatch(self):
        refs = [{'path': 'file.txt', 'absolutePath': '/home/fixture/project/file.txt'}]
        files = [{'path': refs[0]['absolutePath'], 'name': 'file.txt', 'image': None}]
        self.entry.server.thread['status'] = {'type': 'active'}
        identifier = str(uuid.uuid4())
        with patch.object(self.entry, 'workspace', return_value=files) as workspace:
            self.assertEqual(self.entry.send(self.identifier, '', identifier, mode='queue', references=refs)['status'], 'queued')
            self.entry.server.thread['status'] = {'type': 'idle'}
            self.entry._dispatch_queued(self.identifier)
            self.entry._dispatch_queued(self.identifier)
            self.assertGreaterEqual(workspace.call_count, 2)
            self.assertTrue(all(call.args[2] == {'references': refs} for call in workspace.call_args_list))
        sent = [params for method, params in self.entry.server.calls if method == 'turn/start']
        self.assertEqual(len(sent), 1)
        self.assertIn('## file.txt: /home/fixture/project/file.txt', sent[0]['input'][0]['text'])

    def test_queue_unknown_write_is_never_replayed_after_restart(self):
        submission = str(uuid.uuid4())
        self.entry.send(self.identifier, 'queued', submission, mode='queue')
        self.entry.server.fail_send = True
        self.entry._dispatch_queued(self.identifier)
        self.entry._dispatch_queued(self.identifier)
        restarted = self.make_entry()
        self.addCleanup(restarted.close)
        restarted._dispatch_queued(self.identifier)
        self.assertEqual(restarted.send(self.identifier, 'queued', submission, mode='queue')['status'], 'unknown')
        self.assertFalse(any(m == 'turn/start' for m, _ in restarted.server.calls))
        self.assertEqual(sum(m == 'turn/start' for m, _ in self.entry.server.calls), 1)

    def test_cancel_and_target_mismatch_prevent_queue_dispatch(self):
        submission = str(uuid.uuid4())
        self.entry.send(self.identifier, 'queued', submission, mode='queue')
        self.entry.server.thread['cwd'] = '/different'
        self.entry.store.get = lambda identifier: {'id': identifier, 'cwd': '/different', 'model_provider': 'magpie'}
        self.entry._dispatch_queued(self.identifier)
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))
        self.assertEqual(self.entry.cancel_queued(self.identifier, submission)['status'], 'cancelled')
        self.assertFalse(self.entry._queue(self.identifier))

    def test_permission_policy_uses_native_target_and_actual_settings_event(self):
        original = self.entry.server.request
        def request(method, params):
            if method == 'configRequirements/read':
                return {'requirements': {'allowedApprovalsReviewers': ['user', 'guardian_subagent']}}
            if method == 'permissionProfile/list':
                return {'data': [{'id': ':workspace', 'allowed': True}, {'id': ':danger-full-access', 'allowed': True}]}
            if method == 'experimentalFeature/list':
                return {'data': [{'name': 'guardian_approval', 'enabled': True}]}
            if method == 'thread/settings/update':
                self.entry.server.calls.append((method, copy.deepcopy(params)))
                self.entry._event({'method': 'thread/settings/updated', 'params': {'threadId': self.identifier,
                    'threadSettings': {'approvalPolicy': 'on-request', 'approvalsReviewer': params['approvalsReviewer'],
                                       'sandboxPolicy': {'type': 'workspaceWrite'}}}}, self.entry.generation)
                return {}
            return original(method, params)
        self.entry.server.request = request
        result = self.entry.permissions(self.identifier, 'auto-review', expected_runtime='cli')
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['permissionMode'], 'auto-review')
        settings = [p for m, p in self.entry.server.calls if m == 'thread/settings/update'][0]
        self.assertEqual(settings['approvalsReviewer'], 'guardian_subagent')
        self.assertFalse(any(m == 'config/batchWrite' for m, _ in self.entry.server.calls))

    def test_model_cache_persists_twelve_hours_and_manual_refresh_fetches(self):
        with patch.object(self.entry, 'read', return_value={'digest': 'auth-one'}):
            first = self.entry.catalog(self.identifier, kind='models')
            second = self.entry.catalog(self.identifier, kind='models')
            self.assertEqual(first['models'], second['models'])
            self.assertEqual(second['cache']['state'], 'fresh')
            self.assertEqual(sum(m == 'model/list' for m, _ in self.entry.server.calls), 1)
            restarted = self.make_entry()
            self.addCleanup(restarted.close)
            with patch.object(restarted, 'read', return_value={'digest': 'auth-one'}):
                self.assertEqual(restarted.catalog(self.identifier, kind='models')['models'], first['models'])
                self.assertFalse(any(m == 'model/list' for m, _ in restarted.server.calls))
            self.entry.catalog(self.identifier, refresh=True, kind='models')
            self.assertEqual(sum(m == 'model/list' for m, _ in self.entry.server.calls), 2)
            self.assertEqual(len(self.entry.model_catalog.timers), 1)
            interval = next(iter(self.entry.model_catalog.timers.values())).interval
            self.assertTrue(43198 < interval <= 43200)

    def test_model_cache_isolates_accounts_and_preserves_last_result_on_failure(self):
        with patch.object(self.entry, 'read', return_value={'digest': 'auth-one'}):
            first = self.entry.catalog(self.identifier, kind='models')
        with patch.object(self.entry, 'read', return_value={'digest': 'auth-two'}):
            second = self.entry.catalog(self.identifier, kind='models')
            self.assertNotEqual(first['cache']['identity'], second['cache']['identity'])
            original = self.entry.server.request
            def unavailable(method, params):
                if method == 'model/list':
                    raise IPCError('metadata unavailable')
                return original(method, params)
            with patch.object(self.entry.server, 'request', side_effect=unavailable):
                fallback = self.entry.catalog(self.identifier, refresh=True, kind='models')
            self.assertEqual(fallback['models'], second['models'])
            self.assertEqual(fallback['cache']['state'], 'stale')
            self.assertIn('上次结果', fallback['modelError'])

    def test_native_usage_replaces_recorded_count_and_survives_polling(self):
        session = self.entry.session(self.identifier)
        session.state.update(latestTokenUsageInfo={'last': {'totalTokens': 30}, 'modelContextWindow': 100},
                             tokenUsageFromHistory=True)
        self.entry.server.thread['name'] = 'updated fixture'
        self.entry._refresh(session)
        self.assertTrue(session.view()['contextUsage']['cached'])

        def emit(info):
            self.entry._event({'method': 'thread/tokenUsage/updated', 'params': {
                'threadId': self.identifier, 'tokenUsage': info}}, self.entry.generation)

        emit({'last': {'totalTokens': 15}, 'modelContextWindow': 100})
        emit({'total': {'totalTokens': 250}})
        self.entry.server.thread['name'] = 'updated again'
        self.entry._refresh(session)
        self.assertEqual(session.view()['contextUsage'], {'usedTokens': 15, 'contextWindow': 100, 'cached': False})

    def test_native_plan_and_settings_survive_polling(self):
        self.entry.session(self.identifier)
        mode = {'mode': 'plan', 'settings': {'model': 'group/fixture'}}
        for method, params in [('thread/settings/updated', {'threadSettings': {
            'collaborationMode': mode, 'approvalPolicy': 'on-request',
            'approvalsReviewer': 'guardian_subagent', 'sandboxPolicy': {'type': 'workspaceWrite'}}}),
            ('turn/plan/updated', {'plan': [{'step': 'fixture', 'status': 'inProgress'}]})]:
            self.entry._event({'method': method, 'params': {'threadId': self.identifier, **params}}, self.entry.generation)
        self.entry._refresh(self.entry.live[self.identifier])
        state = self.entry.live[self.identifier].state
        self.assertEqual(state['latestCollaborationMode'], mode)
        self.assertEqual(state['turnPlan'], [{'step': 'fixture', 'status': 'inProgress'}])
        self.assertEqual(self.entry.view(self.identifier, attach=False)['permissionMode'], 'auto-review')

    def test_permission_inspection_does_not_resume_and_unknown_policy_blocks_write(self):
        with patch.object(self.entry.server, 'request', wraps=self.entry.server.request):
            options = self.entry.linux_permission_options(self.identifier)
        self.assertTrue(all(not row['available'] for row in options['options']))
        with self.assertRaises(ValueError):
            self.entry.permissions(self.identifier, 'ask')
        self.assertFalse(any(m in ('thread/resume', 'thread/settings/update') for m, _ in self.entry.server.calls))
