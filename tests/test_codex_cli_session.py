import tempfile
import json
import os
import shlex
import subprocess
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.app_server import AppServerError, AppServerUnavailable, AppServerUnsupported, AppServerUnknownResult
from bridge.features.sessions.goal import GoalUnavailable, GoalUnsupported
from bridge.features.sessions.model import normalize_state
from bridge.clients.codex.cli_session import CliSessions, _local_cli, _managed_cli, _ssh_daemon_start_command, _ssh_proxy_command

ROOT = Path(__file__).resolve().parents[1]
MANAGED_NAME = 'codex.exe' if os.name == 'nt' else 'codex'


class FakeClient:
    def __init__(self, home):
        self.socket_path = str(home / 'app-server-control/app-server-control.sock')
        self.process = object()
        self.sent = []
        self.replies = []
        self.loaded = True
        self.thread = {'id': 'thread-a', 'cwd': '/original/project',
                       'modelProvider': 'original-provider', 'model': 'original-model',
                       'status': {'type': 'idle'}, 'turns': [], 'name': 'CLI task'}

    def connect(self):
        return {'codexHome': str(Path(self.socket_path).parents[1])}

    def is_loaded(self, thread_id):
        return self.loaded

    def request(self, method, params, **kwargs):
        self.sent.append((method, params))
        if method == 'thread/read':
            return {'thread': self.thread.copy()}
        if method == 'thread/resume':
            return {'thread': self.thread.copy(), 'modelProvider': 'original-provider',
                    'cwd': self.thread['cwd'], 'model': 'original-model'}
        if method == 'turn/start':
            return {'turn': {'id': 'turn-1'}}
        if method == 'model/list':
            return {'data': [{'model': 'native-model', 'displayName': 'Native model',
                              'supportedReasoningEfforts': [{'reasoningEffort': 'high'}],
                              'defaultReasoningEffort': 'high'}]}
        if method == 'skills/list':
            return {'data': [{'cwd': '/original/project', 'skills': [
                {'name': 'writer', 'path': '/original/skills/writer/SKILL.md',
                 'description': 'Write text', 'enabled': True, 'scope': 'user'}], 'errors': []}]}
        if method == 'thread/goal/get':
            return {'goal': None}
        if method == 'thread/goal/set':
            return {'goal': {'objective': params.get('objective', 'Existing goal'),
                             'status': params.get('status', 'active')}}
        if method == 'thread/goal/clear':
            return {'cleared': True}
        return {}

    def respond(self, request_id, result):
        self.replies.append((request_id, result))

    def close(self):
        self.process = None


class CliSessionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.home = Path(self.temp.name)
        (self.home / 'app-server-control').mkdir()
        (self.home / 'app-server-control/app-server-control.sock').touch()
        self.states = []
        self.disconnections = []
        self.sessions = CliSessions(self.home, lambda tid, state: self.states.append((tid, state)),
                                    lambda tid=None: self.disconnections.append(tid))
        self.fake = FakeClient(self.home)
        self.sessions.client = self.fake

    def tearDown(self):
        self.temp.cleanup()

    def test_passive_attach_does_not_connect_or_resume(self):
        self.sessions._start_daemon = Mock()
        Path(self.fake.socket_path).unlink()
        self.assertIsNone(self.sessions.attach('thread-a'))
        self.assertEqual(self.fake.sent, [])
        self.sessions._start_daemon.assert_not_called()

    def test_unavailable_proxy_starts_daemon_only_for_explicit_attach(self):
        info = {'codexHome': str(self.home)}
        started = False
        def connect():
            if not started:
                time.sleep(.03)
                raise AppServerUnavailable('offline')
            return info
        def start_daemon():
            nonlocal started
            started = True
        self.fake.connect = Mock(side_effect=connect)
        with patch.object(self.sessions, '_start_daemon', side_effect=start_daemon) as start:
            self.assertEqual(self.sessions.attach('thread-a', explicit=True)['id'], 'thread-a')
        start.assert_called_once_with()
        self.assertEqual(self.fake.connect.call_count, 2)

    def test_explicit_attach_resumes_saved_thread_and_preserves_identity(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli', 'cwd': '/original/project',
                 'model_provider': 'original-provider'}
        with patch.object(self.sessions.store, 'get', return_value=saved):
            state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['modelProvider'], 'original-provider')
        self.assertEqual(state['cwd'], '/original/project')
        self.assertEqual([params for method, params in self.fake.sent if method == 'thread/resume'],
                         [{'threadId': 'thread-a', 'excludeTurns': True}])
        self.assertFalse(any('model' in params or 'cwd' in params or 'sandbox' in params
                             for method, params in self.fake.sent if method == 'thread/resume'))

    def test_cold_resume_rejects_saved_identity_mismatch(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli', 'cwd': '/other/project',
                 'model_provider': 'original-provider'}
        with patch.object(self.sessions.store, 'get', return_value=saved):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent))

    def test_local_windows_cold_resume_accepts_same_directory_extended_path(self):
        self.fake.loaded = False
        self.fake.thread['cwd'] = r'C:\project'
        saved = {'id': 'thread-a', 'origin': 'cli',
                 'cwd': r'\\?\C:\project', 'model_provider': 'original-provider'}
        samefile = Mock(return_value=True)
        windows = SimpleNamespace(name='nt', path=SimpleNamespace(samefile=samefile))
        with patch.object(self.sessions.store, 'get', return_value=saved), \
                patch('bridge.clients.codex.cli_session.os', windows):
            state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['id'], 'thread-a')
        samefile.assert_called_once_with(r'\\?\C:\project', r'C:\project')

    def test_local_windows_cold_resume_rejects_different_directory(self):
        self.fake.loaded = False
        self.fake.thread['cwd'] = r'C:\project'
        saved = {'id': 'thread-a', 'origin': 'cli',
                 'cwd': r'\\?\C:\other', 'model_provider': 'original-provider'}
        windows = SimpleNamespace(name='nt', path=SimpleNamespace(samefile=Mock(return_value=False)))
        with patch.object(self.sessions.store, 'get', return_value=saved), \
                patch('bridge.clients.codex.cli_session.os', windows):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent))

    def test_local_windows_cold_resume_rejects_unverifiable_directory(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli',
                 'cwd': r'\\?\C:\project', 'model_provider': 'original-provider'}
        windows = SimpleNamespace(name='nt', path=SimpleNamespace(
            samefile=Mock(side_effect=OSError('inaccessible'))))
        with patch.object(self.sessions.store, 'get', return_value=saved), \
                patch('bridge.clients.codex.cli_session.os', windows):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent))

    def test_local_posix_cold_resume_keeps_strict_path_identity(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli',
                 'cwd': '/other/project', 'model_provider': 'original-provider'}
        samefile = Mock(return_value=True)
        posix = SimpleNamespace(name='posix', path=SimpleNamespace(samefile=samefile))
        with patch.object(self.sessions.store, 'get', return_value=saved), \
                patch('bridge.clients.codex.cli_session.os', posix):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        samefile.assert_not_called()
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent))

    def test_ssh_cold_resume_does_not_use_local_windows_path_identity(self):
        self.sessions.alias = '88server'
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli',
                 'cwd': r'\\?\C:\project', 'model_provider': 'original-provider'}
        samefile = Mock(return_value=True)
        windows = SimpleNamespace(name='nt', path=SimpleNamespace(samefile=samefile))
        with patch.object(self.sessions.store, 'get', return_value=saved), \
                patch('bridge.clients.codex.cli_session.os', windows):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        samefile.assert_not_called()
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent))

    def test_resume_response_identity_mismatch_is_not_joined(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli', 'cwd': '/original/project',
                 'model_provider': 'original-provider'}
        original = self.fake.request
        def request(method, params, **kwargs):
            result = original(method, params, **kwargs)
            if method == 'thread/resume':
                result['modelProvider'] = 'different-provider'
            return result
        self.fake.request = request
        with patch.object(self.sessions.store, 'get', return_value=saved):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        self.assertNotIn('thread-a', self.sessions.joined)

    def test_loaded_thread_preserves_identity(self):
        state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['modelProvider'], 'original-provider')
        self.assertEqual(state['cwd'], '/original/project')
        self.assertEqual([params for method, params in self.fake.sent if method == 'thread/resume'],
                         [{'threadId': 'thread-a', 'excludeTurns': True}])

    def test_core_turn_and_approval_mapping(self):
        self.sessions.attach('thread-a', explicit=True)
        attachment = str(self.home / 'a.py')
        result = self.sessions.call('thread-a', 'thread-follower-start-turn', {
            'turnStart': {'request': {'input': [{'type': 'text', 'text': 'Inspect this'}],
                                      'clientUserMessageId': 'submission-1'},
                          'context': {'fileAttachments': [{'path': attachment}]}}})
        self.assertEqual(result['turn']['id'], 'turn-1')
        params = next(params for method, params in self.fake.sent if method == 'turn/start')
        self.assertIn(attachment, params['input'][0]['text'])
        self.assertIn('Inspect this', params['input'][0]['text'])
        self.assertNotIn('model', params)
        self.sessions._server_request({'id': 9, 'method': 'item/commandExecution/requestApproval',
                                       'params': {'threadId': 'thread-a', 'command': 'echo hello'}})
        self.sessions.call('thread-a', 'thread-follower-command-approval-decision',
                           {'requestId': 9, 'decision': 'decline'})
        self.assertEqual(self.fake.replies, [(9, {'decision': 'decline'})])
        self.sessions._server_request({'id': 10, 'method': 'item/tool/requestUserInput',
                                       'params': {'threadId': 'thread-a', 'questions': []}})
        self.sessions.call('thread-a', 'thread-follower-submit-user-input',
                           {'requestId': 10, 'response': {'answers': {'q': {'answers': ['yes']}}}})
        self.assertEqual(self.fake.replies[-1][0], 10)

    def test_unsupported_operation_fails_closed(self):
        self.sessions.attach('thread-a', explicit=True)
        with self.assertRaises(AppServerUnsupported):
            self.sessions.call('thread-a', 'thread-follower-update-thread-settings', {})

    def test_native_model_settings_and_collaboration_mode_keep_thread_identity(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._queue_refresh = Mock()
        result = self.sessions.call('thread-a', 'thread-follower-update-thread-settings',
                                    {'threadSettings': {'model': 'native-model', 'effort': 'high'}})
        self.assertEqual(result, {'applied': True})
        self.assertIn(('thread/settings/update', {'threadId': 'thread-a',
                                                   'model': 'native-model', 'effort': 'high'}), self.fake.sent)
        self.assertEqual(self.states[-1][1]['latestModel'], 'native-model')
        with self.assertRaises(AppServerUnsupported):
            self.sessions.call('thread-a', 'thread-follower-update-thread-settings',
                               {'threadSettings': {'cwd': '/other'}})
        mode = {'mode': 'plan', 'settings': {'model': 'native-model',
                                           'reasoning_effort': 'high', 'developer_instructions': None}}
        self.sessions.call('thread-a', 'thread-follower-start-turn',
                           {'turnStart': {'request': {'input': [{'type': 'text', 'text': 'Plan'}],
                                                      'collaborationMode': mode}}})
        native = next(params for method, params in reversed(self.fake.sent) if method == 'turn/start')
        self.assertEqual(native['collaborationMode'], mode)
        self.assertNotIn('cwd', native)
        self.assertNotIn('modelProvider', native)
        self.assertEqual(self.states[-1][1]['latestCollaborationMode']['mode'], 'plan')

    def test_native_permission_update_waits_for_effective_settings_event(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._queue_refresh = Mock()
        self.sessions.call('thread-a', 'thread-follower-update-thread-settings',
                           {'threadSettings': {'permissions': ':workspace',
                                               'approvalPolicy': 'on-request',
                                               'approvalsReviewer': 'auto_review'}})
        self.assertIn(('thread/settings/update', {'threadId': 'thread-a',
            'permissions': ':workspace', 'approvalPolicy': 'on-request',
            'approvalsReviewer': 'auto_review'}), self.fake.sent)
        self.assertNotIn('currentPermissions', self.states[-1][1])
        self.sessions._event({'method': 'thread/settings/updated', 'params': {
            'threadId': 'thread-a', 'threadSettings': {
                'cwd': '/original/project', 'modelProvider': 'original-provider',
                'approvalPolicy': 'on-request', 'approvalsReviewer': 'auto_review',
                'sandboxPolicy': {'type': 'workspaceWrite'},
                'activePermissionProfile': {'id': ':workspace'}}}})
        self.assertEqual(self.states[-1][1]['currentPermissions']['approvalsReviewer'], 'auto_review')
        self.assertEqual(self.sessions.refresh('thread-a')['currentPermissions']['activePermissionProfile']['id'], ':workspace')
        with self.assertRaises(AppServerUnsupported):
            self.sessions.call('thread-a', 'thread-follower-update-thread-settings',
                               {'threadSettings': {'permissions': ':workspace', 'cwd': '/other'}})

    def test_native_permission_options_require_daemon_policy_evidence(self):
        self.sessions.attach('thread-a', explicit=True)
        original = self.fake.request
        def request(method, params, **kwargs):
            if method == 'config/read':
                return {'config': {'features': {'guardian_approval': True}}}
            if method == 'configRequirements/read':
                return {'requirements': {'allowedPermissionProfiles': {
                    ':workspace': True, ':danger-full-access': False}}}
            if method == 'permissionProfile/list':
                return {'data': [{'id': ':workspace', 'allowed': True},
                                 {'id': ':danger-full-access', 'allowed': True}]}
            if method == 'experimentalFeature/list':
                return {'data': [{'name': 'guardian_approval', 'enabled': True}]}
            return original(method, params, **kwargs)
        self.fake.request = request
        _, options = self.sessions.permission_options('thread-a')
        indexed = {row['preset']: row for row in options['options']}
        self.assertTrue(indexed['ask']['available'])
        self.assertTrue(indexed['auto-review']['available'])
        self.assertFalse(indexed['full-access']['available'])
        self.fake.request = lambda method, params, **kwargs: (
            original(method, params, **kwargs) if method == 'thread/read' else
            (_ for _ in ()).throw(AppServerUnsupported('old runtime')))
        _, unavailable = self.sessions.permission_options('thread-a')
        self.assertFalse(unavailable['options'][0]['available'])
        self.assertIn('CLI', unavailable['options'][0]['reason'])
        self.assertNotIn('桌面', unavailable['options'][0]['reason'])

    def test_native_catalog_and_skill_input_use_joined_daemon(self):
        import hashlib
        self.fake.thread['modelProvider'] = 'openai'
        original = self.fake.request
        def request(method, params, **kwargs):
            result = original(method, params, **kwargs)
            if method == 'thread/resume':
                result['modelProvider'] = 'openai'
            return result
        self.fake.request = request
        self.sessions.attach('thread-a', explicit=True)
        models = self.sessions.get_kind('thread-a', 'models')
        self.assertEqual(models['models'][0]['id'], 'native-model')
        skills = self.sessions.get_kind('thread-a', 'skills')
        path = '/original/skills/writer/SKILL.md'
        identifier = hashlib.sha256(path.encode()).hexdigest()
        self.assertEqual(skills['skills'][0]['id'], identifier)
        selected = self.sessions.validate_skills('thread-a', [identifier])
        self.assertEqual(selected[0]['path'], path)
        self.sessions._queue_refresh = Mock()
        self.sessions.call('thread-a', 'thread-follower-start-turn',
                           {'turnStart': {'request': {'input': [{'type': 'text', 'text': 'Use it'},
                                                                {'type': 'skill', 'name': 'writer', 'path': path}]}}})
        native = next(params for method, params in reversed(self.fake.sent) if method == 'turn/start')
        self.assertEqual(native['input'][1], {'type': 'skill', 'name': 'writer', 'path': path})
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent[4:]))

    def test_native_goal_methods_and_events(self):
        self.sessions.attach('thread-a', explicit=True)
        self.assertTrue(self.sessions.supports_goal('thread-a'))
        self.assertIsNone(self.sessions.get_goal('thread-a'))
        self.assertEqual(self.sessions.set_goal('thread-a', 'Build')['objective'], 'Build')
        self.assertEqual(self.sessions.set_goal_status('thread-a', 'paused')['status'], 'paused')
        self.assertEqual(self.sessions.edit_goal('thread-a', 'Improve', 100)['objective'], 'Improve')
        self.assertEqual(self.sessions.clear_goal('thread-a'), {'cleared': True})
        self.assertIn(('thread/goal/set', {'threadId': 'thread-a', 'objective': 'Improve',
                                             'status': 'paused', 'tokenBudget': 100}), self.fake.sent)
        self.sessions._event({'method': 'thread/goal/updated', 'params': {
            'threadId': 'thread-a', 'goal': {'objective': 'Build', 'status': 'active'}}})
        self.assertEqual(self.states[-1][1]['threadGoal']['objective'], 'Build')
        self.sessions._event({'method': 'turn/plan/updated', 'params': {
            'threadId': 'thread-a', 'turnId': 'turn-1', 'plan': [{'step': 'First', 'status': 'inProgress'}]}})
        self.assertEqual(self.states[-1][1]['turnPlan'][0]['step'], 'First')
        self.sessions._event({'method': 'thread/goal/cleared', 'params': {'threadId': 'thread-a'}})
        self.assertNotIn('threadGoal', self.states[-1][1])

    def test_completed_goal_read_timeout_keeps_live_writer(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'thread/goal/updated', 'params': {
            'threadId': 'thread-a', 'goal': {'objective': 'Build', 'status': 'complete'}}})
        self.fake.thread['turns'] = [{'id': 'turn-1', 'status': 'completed', 'items': []}]
        original = self.fake.request
        failed = threading.Event()
        failures = 0
        def request(method, params, **kwargs):
            nonlocal failures
            if method == 'thread/read' and failures < 2:
                failures += 1
                failed.set()
                raise AppServerUnavailable('Codex runtime read timed out')
            return original(method, params, **kwargs)
        self.fake.request = request
        self.sessions._queue_refresh('thread-a')
        self.assertTrue(failed.wait(2))
        deadline = time.monotonic() + 3
        while 'thread-a' in self.sessions.refreshing and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIn('thread-a', self.sessions.joined)
        self.assertEqual(self.disconnections, [])
        self.assertEqual(self.sessions.states['thread-a']['threadGoal']['status'], 'complete')
        self.assertEqual(self.sessions.states['thread-a']['turns'][-1]['status'], 'completed')

    def test_refresh_retry_stops_on_close_and_cannot_read_new_join(self):
        self.sessions.attach('thread-a', explicit=True)
        original = self.fake.request
        failed = threading.Event()
        def unavailable(method, params, **kwargs):
            if method == 'thread/read':
                failed.set()
                raise AppServerUnavailable('Codex runtime read timed out')
            return original(method, params, **kwargs)
        self.fake.request = unavailable
        self.sessions._queue_refresh('thread-a')
        self.assertTrue(failed.wait(2))
        self.sessions.close()
        deadline = time.monotonic() + .1
        while 'thread-a' in self.sessions.refreshing and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertNotIn('thread-a', self.sessions.refreshing)
        self.fake.request = original
        self.fake.process = object()
        self.sessions.attach('thread-a', explicit=True)
        reads = sum(method == 'thread/read' for method, _ in self.fake.sent)
        time.sleep(.3)
        self.assertEqual(sum(method == 'thread/read' for method, _ in self.fake.sent), reads)

    def test_old_read_cannot_replace_or_invalidate_new_join(self):
        original_request = self.fake.request
        for old_response in ('valid', 'notLoaded', 'identity_changed'):
            with self.subTest(old_response=old_response):
                self.sessions._disconnect()
                self.fake.process = object()
                self.fake.thread['model'] = 'old-model'
                self.sessions.attach('thread-a', explicit=True)
                started = threading.Event()
                release = threading.Event()
                snapshot = self.fake.thread.copy()
                if old_response == 'notLoaded':
                    snapshot['status'] = {'type': 'notLoaded'}
                elif old_response == 'identity_changed':
                    snapshot['cwd'] = '/other/project'
                def request(method, params, **kwargs):
                    if method == 'thread/read' and not started.is_set():
                        started.set()
                        release.wait(2)
                        return {'thread': snapshot}
                    return original_request(method, params, **kwargs)
                self.fake.request = request
                errors = []
                worker = threading.Thread(target=lambda: self._refresh_error(errors))
                worker.start()
                self.assertTrue(started.wait(2))
                self.sessions._disconnect()
                self.fake.process = object()
                self.fake.thread['model'] = 'new-model'
                self.sessions.attach('thread-a', explicit=True)
                disconnects = len(self.disconnections)
                release.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], AppServerUnavailable)
                self.assertEqual(self.sessions.states['thread-a']['latestModel'], 'new-model')
                self.assertIn('thread-a', self.sessions.joined)
                self.assertEqual(len(self.disconnections), disconnects)
                self.fake.request = original_request

    def _refresh_error(self, errors):
        try:
            self.sessions.refresh('thread-a')
        except Exception as exc:
            errors.append(exc)

    def test_native_settings_event_survives_thread_refresh(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.thread['model'] = 'native-model'
        self.fake.thread['reasoningEffort'] = 'high'
        self.sessions._event({'method': 'thread/settings/updated', 'params': {
            'threadId': 'thread-a', 'threadSettings': {
                'model': 'native-model', 'effort': 'high', 'serviceTier': 'fast',
                'collaborationMode': {'mode': 'default', 'settings': {}}}}})
        state = self.sessions.refresh('thread-a')
        self.assertEqual(state['latestModel'], 'native-model')
        self.assertEqual(state['latestThreadSettings']['serviceTier'], 'fast')
        self.assertEqual(state['latestCollaborationMode']['mode'], 'default')

    def test_native_read_null_model_does_not_erase_newer_settings_event(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.thread['model'] = None
        self.fake.thread['reasoningEffort'] = None
        self.sessions._event({'method': 'thread/settings/updated', 'params': {
            'threadId': 'thread-a', 'threadSettings': {
                'model': 'native-model', 'effort': None, 'serviceTier': 'fast'}}})
        state = self.sessions.refresh('thread-a')
        self.assertEqual(state['latestModel'], 'native-model')
        self.assertIsNone(state['latestReasoningEffort'])
        self.assertEqual(state['latestThreadSettings']['model'], 'native-model')
        self.assertIsNone(state['latestThreadSettings']['effort'])

    def test_old_native_read_cannot_erase_new_model_event_or_turn(self):
        self.sessions.attach('thread-a', explicit=True)
        original = self.fake.request
        started = threading.Event()
        release = threading.Event()
        old_thread = self.fake.thread.copy()
        def request(method, params, **kwargs):
            if method == 'thread/read' and not started.is_set():
                started.set()
                release.wait(2)
                return {'thread': old_thread}
            return original(method, params, **kwargs)
        self.fake.request = request
        worker = threading.Thread(target=lambda: self.sessions.refresh('thread-a'))
        worker.start()
        self.assertTrue(started.wait(2))
        self.sessions._event({'method': 'thread/settings/updated', 'params': {
            'threadId': 'thread-a', 'threadSettings': {'model': 'new-model', 'effort': 'high'}}})
        self.sessions._event({'method': 'turn/started', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'new-turn', 'status': 'inProgress'}}})
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        state = self.sessions.states['thread-a']
        self.assertEqual(state['latestModel'], 'new-model')
        self.assertEqual(state['latestReasoningEffort'], 'high')
        self.assertEqual(state['turns'][-1]['turnId'], 'new-turn')
        self.assertEqual(state['turns'][-1]['params']['model'], 'new-model')
        self.assertEqual(normalize_state(state)['status'], 'active')
        self.fake.thread['model'] = 'later-native-model'
        self.fake.thread['reasoningEffort'] = 'low'
        state = self.sessions.refresh('thread-a')
        self.assertEqual(state['latestModel'], 'later-native-model')
        self.assertEqual(state['latestReasoningEffort'], 'low')

    def test_old_native_read_cannot_reopen_completed_turn(self):
        self.fake.thread['status'] = {'type': 'active'}
        self.fake.thread['turns'] = [{'id': 'turn-1', 'status': 'inProgress', 'items': []}]
        self.sessions.attach('thread-a', explicit=True)
        original = self.fake.request
        started = threading.Event()
        release = threading.Event()
        old_thread = self.fake.thread.copy()
        def request(method, params, **kwargs):
            if method == 'thread/read' and not started.is_set():
                started.set()
                release.wait(2)
                return {'thread': old_thread}
            return original(method, params, **kwargs)
        self.fake.request = request
        worker = threading.Thread(target=lambda: self.sessions.refresh('thread-a'))
        worker.start()
        self.assertTrue(started.wait(2))
        self.sessions._event({'method': 'turn/completed', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'turn-1', 'status': 'completed'}}})
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        state = self.sessions.states['thread-a']
        self.assertEqual(normalize_state(state)['status'], 'idle')
        self.assertEqual(state['turns'][-1]['status'], 'completed')

    def test_resume_projects_persisted_mode_tier_and_goal(self):
        original = self.fake.request
        def request(method, params, **kwargs):
            if method == 'thread/resume':
                result = original(method, params, **kwargs)
                result.update({'reasoningEffort': 'high', 'serviceTier': 'fast',
                               'collaborationMode': {'mode': 'plan', 'settings': {}}})
                return result
            if method == 'thread/goal/get':
                return {'goal': {'objective': 'Existing goal', 'status': 'paused'}}
            return original(method, params, **kwargs)
        self.fake.request = request
        state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['latestThreadSettings']['serviceTier'], 'fast')
        self.assertEqual(state['latestCollaborationMode']['mode'], 'plan')
        self.assertEqual(state['threadGoal']['objective'], 'Existing goal')
        self.sessions._event({'method': 'thread/goal/cleared', 'params': {'threadId': 'thread-a'}})
        self.assertNotIn('threadGoal', self.sessions.refresh('thread-a'))

    def test_resume_projects_effective_native_permission_state(self):
        original = self.fake.request
        def request(method, params, **kwargs):
            result = original(method, params, **kwargs)
            if method == 'thread/resume':
                result.update({'approvalPolicy': 'never', 'approvalsReviewer': 'user',
                               'sandbox': {'type': 'dangerFullAccess'},
                               'activePermissionProfile': {'id': ':danger-full-access'}})
            return result
        self.fake.request = request
        state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['currentPermissions']['approvalPolicy'], 'never')
        self.assertEqual(state['currentPermissions']['activePermissionProfile']['id'], ':danger-full-access')
        self.assertEqual(self.sessions.refresh('thread-a')['currentPermissions']['sandboxPolicy']['type'], 'dangerFullAccess')

    def test_reconnect_uses_new_effective_permissions_without_stale_settings(self):
        original = self.fake.request
        resume_count = 0
        def request(method, params, **kwargs):
            nonlocal resume_count
            result = original(method, params, **kwargs)
            if method == 'thread/resume':
                resume_count += 1
                full = resume_count == 1
                result.update({'approvalPolicy': 'never' if full else 'on-request',
                               'approvalsReviewer': 'user',
                               'sandbox': {'type': 'dangerFullAccess' if full else 'workspaceWrite'},
                               'activePermissionProfile': {'id': ':danger-full-access' if full else ':workspace'}})
            return result
        self.fake.request = request
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'thread/settings/updated', 'params': {
            'threadId': 'thread-a', 'threadSettings': {
                'approvalPolicy': 'never', 'approvalsReviewer': 'user',
                'sandboxPolicy': {'type': 'dangerFullAccess'},
                'activePermissionProfile': {'id': ':danger-full-access'}}}})
        self.sessions._disconnect()
        state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['currentPermissions']['activePermissionProfile']['id'], ':workspace')
        self.assertNotIn('approvalPolicy', state.get('latestThreadSettings') or {})

    def test_goal_policy_error_does_not_block_baseline_attach(self):
        original = self.fake.request
        def request(method, params, **kwargs):
            if method == 'thread/goal/get':
                raise AppServerError('goals disabled by policy')
            return original(method, params, **kwargs)
        self.fake.request = request
        state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['id'], 'thread-a')
        self.assertNotIn('threadGoal', state)
        self.assertIn('thread-a', self.sessions.joined)

    def test_goal_transport_failure_aborts_attach(self):
        original = self.fake.request
        def request(method, params, **kwargs):
            if method == 'thread/goal/get':
                raise AppServerUnavailable('connection lost')
            return original(method, params, **kwargs)
        self.fake.request = request
        with self.assertRaises(AppServerUnavailable):
            self.sessions.attach('thread-a', explicit=True)
        self.assertNotIn('thread-a', self.sessions.joined)
        self.assertNotIn('thread-a', self.sessions.attaching)

    def test_custom_provider_catalog_never_offers_openai_models(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions.metadata['thread-a']['modelProvider'] = 'custom'
        self.fake.thread['modelProvider'] = 'custom'
        result = self.sessions.get_kind('thread-a', 'models')
        self.assertEqual(result['modelSource'], 'api')
        self.assertEqual(result['models'], [])
        self.assertFalse(any(method == 'model/list' for method, _ in self.fake.sent))

    def test_chatgpt_gateway_models_come_from_joined_daemon(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions.metadata['thread-a']['modelProvider']='openai'
        self.fake.thread['modelProvider']='openai'
        original=self.fake.request
        def request(method,params,**kwargs):
            if method=='config/read':
                return {'config':{'openai_base_url':'http://127.0.0.1:3425/backend-api/codex'}}
            if method=='account/read':
                return {'account':{'type':'chatgpt'},'requiresOpenaiAuth':True}
            return original(method,params,**kwargs)
        self.fake.request=request
        result=self.sessions.get_kind('thread-a','models')
        self.assertEqual(result['modelSource'],'codex')
        self.assertEqual(result['models'][0]['id'],'native-model')
        self.assertFalse(any(method=='turn/start' for method,_ in self.fake.sent))

    def test_native_goal_unknown_result_is_not_replayed(self):
        self.sessions.attach('thread-a', explicit=True)
        original = self.fake.request
        def fail(method, params, **kwargs):
            if method == 'thread/goal/set':
                self.fake.sent.append((method, params))
                raise AppServerUnknownResult('unknown')
            return original(method, params, **kwargs)
        self.fake.request = fail
        with self.assertRaises(GoalUnavailable):
            self.sessions.set_goal('thread-a', 'Build')
        self.assertEqual(len([method for method, _ in self.fake.sent if method == 'thread/goal/set']), 1)
        def unsupported(method, params, **kwargs):
            if method == 'thread/goal/get':
                raise AppServerUnsupported('method not found')
            return original(method, params, **kwargs)
        self.fake.request = unsupported
        self.assertFalse(self.sessions.supports_goal('thread-a'))
        with self.assertRaises(GoalUnsupported):
            self.sessions.get_goal('thread-a')

    def test_live_events_update_state_and_resolve_pending_without_refresh(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'turn/started', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'turn-1', 'status': 'inProgress', 'items': []}}})
        self.sessions._event({'method': 'item/started', 'params': {
            'threadId': 'thread-a', 'turnId': 'turn-1',
            'item': {'id': 'msg-1', 'type': 'agentMessage', 'text': ''}}})
        self.sessions._event({'method': 'item/agentMessage/delta', 'params': {
            'threadId': 'thread-a', 'turnId': 'turn-1', 'itemId': 'msg-1', 'delta': 'hello'}})
        self.assertEqual(self.states[-1][1]['turns'][0]['items'][0]['text'], 'hello')
        self.sessions._server_request({'id': 42, 'method': 'item/fileChange/requestApproval',
                                       'params': {'threadId': 'thread-a', 'turnId': 'turn-1'}})
        self.assertEqual(self.states[-1][1]['requests'][0]['id'], 42)
        self.sessions._event({'method': 'serverRequest/resolved', 'params': {
            'threadId': 'thread-a', 'requestId': 42}})
        self.assertEqual(self.states[-1][1]['requests'], [])
        self.sessions._event({'method': 'turn/completed', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'turn-1', 'status': 'completed'}}})
        self.assertEqual(self.states[-1][1]['turns'][0]['status'], 'completed')

    def test_completed_turn_keeps_user_item_omitted_from_event(self):
        self.fake.thread['turns'] = [{'id': 'turn-1', 'status': 'inProgress', 'items': [
            {'id': 'user-1', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Hello'}]},
            {'id': 'reasoning-1', 'type': 'reasoning', 'summary': 'Draft'},
            {'id': 'assistant-1', 'type': 'agentMessage', 'text': 'Partial'}]}]
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'turn/completed', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'turn-1', 'status': 'completed', 'items': [
                {'id': 'assistant-1', 'type': 'agentMessage', 'text': 'Final'}]}}})
        items = self.states[-1][1]['turns'][0]['items']
        self.assertEqual([(item['id'], item['type']) for item in items],
                         [('user-1', 'userMessage'), ('assistant-1', 'agentMessage')])
        self.assertEqual(items[1]['text'], 'Final')

    def test_unload_revokes_write_access(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'thread/closed', 'params': {'threadId': 'thread-a'}})
        self.assertEqual(self.disconnections, ['thread-a'])
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-interrupt-turn',
                               {'expectedTurnId': 'turn-1'})

    def test_unloading_one_thread_keeps_another_joined(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.thread['id'] = 'thread-b'
        self.sessions.attach('thread-b', explicit=True)
        self.sessions._event({'method': 'thread/closed', 'params': {'threadId': 'thread-a'}})
        self.assertEqual(self.disconnections, ['thread-a'])
        self.assertNotIn('thread-a', self.sessions.joined)
        self.assertIn('thread-b', self.sessions.joined)
        self.assertEqual(self.sessions.call('thread-b', 'thread-follower-interrupt-turn',
                                            {'expectedTurnId': 'turn-2'}), {})
        self.assertIn(('turn/interrupt', {'threadId': 'thread-b', 'turnId': 'turn-2'}), self.fake.sent)

    def test_provider_change_revokes_write_access(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.thread['modelProvider'] = 'another-provider'
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-interrupt-turn',
                               {'expectedTurnId': 'turn-1'})
        self.assertFalse(any(method == 'turn/interrupt' for method, _ in self.fake.sent))

    def test_unknown_approval_result_quarantines_numeric_request_id(self):
        self.sessions.attach('thread-a', explicit=True)
        request = {'id': 73, 'method': 'item/commandExecution/requestApproval',
                   'params': {'threadId': 'thread-a', 'turnId': 'turn-1'}}
        self.sessions._server_request(request)
        sends = []
        def uncertain(request_id, result):
            sends.append((request_id, result))
            raise AppServerUnknownResult('response may have arrived')
        self.fake.respond = uncertain
        with self.assertRaises(AppServerUnknownResult):
            self.sessions.call('thread-a', 'thread-follower-command-approval-decision',
                               {'requestId': 73, 'decision': 'decline'})
        self.assertEqual(self.states[-1][1]['requests'], [])
        self.sessions._server_request(request)
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-command-approval-decision',
                               {'requestId': 73, 'decision': 'decline'})
        self.assertEqual(sends, [(73, {'decision': 'decline'})])

    def test_concurrent_approval_claim_allows_only_one_send(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._server_request({'id': 74, 'method': 'item/fileChange/requestApproval',
                                       'params': {'threadId': 'thread-a', 'turnId': 'turn-1'}})
        entered = threading.Event()
        release = threading.Event()
        sends = []
        def wait_for_result(request_id, result):
            sends.append(request_id)
            entered.set()
            release.wait(2)
        self.fake.respond = wait_for_result
        worker = threading.Thread(target=lambda: self.sessions.call(
            'thread-a', 'thread-follower-file-approval-decision',
            {'requestId': 74, 'decision': 'accept'}))
        worker.start()
        self.assertTrue(entered.wait(2))
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-file-approval-decision',
                               {'requestId': 74, 'decision': 'accept'})
        release.set()
        worker.join(2)
        self.assertEqual(sends, [74])

    def test_ssh_proxy_uses_strict_existing_runtime_connection(self):
        command = _ssh_proxy_command('saved-host')
        self.assertIn('BatchMode=yes', command)
        self.assertIn('ClearAllForwardings=yes', command)
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertEqual(command[-2], 'saved-host')
        self.assertIn('codex app-server proxy', command[-1])
        self.assertTrue(command[-1].startswith('exec "$SHELL" -lc '))
        self.assertEqual(shlex.split(command[-1])[2], '-lc')
        self.assertIn('exec codex app-server proxy', shlex.split(command[-1])[3])
        with self.assertRaises(ValueError):
            _ssh_proxy_command('-unsafe')
        remote = CliSessions(self.home / 'local-only-path', lambda *_: None,
                             lambda: None, alias='saved-host')
        remote.client = FakeClient(self.home)
        self.assertTrue(remote.available())
        self.assertEqual(remote.attach('thread-a', explicit=True)['id'], 'thread-a')

    @unittest.skipIf(os.name == 'nt', 'POSIX remote shell fixture')
    def test_ssh_proxy_falls_back_to_selected_managed_package_without_path_cli(self):
        managed = self.home / 'packages/app-server-daemon/current/bin/codex'
        managed.parent.mkdir(parents=True)
        managed.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        managed.chmod(0o700)
        inner = shlex.split(_ssh_proxy_command('saved-host')[-1])[3]
        environment = os.environ.copy()
        environment.update(HOME=str(self.home), CODEX_HOME=str(self.home), PATH='')
        result = subprocess.run(['/bin/sh', '-c', inner], env=environment,
                                capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['app-server', 'proxy'])

    def test_packaged_app_finds_existing_daemon_cli_without_path(self):
        runtime = self.home / 'packages/app-server-daemon/current/bin' / MANAGED_NAME
        runtime.parent.mkdir(parents=True)
        runtime.write_text('#!/bin/sh\n')
        runtime.chmod(0o700)
        with patch('bridge.clients.codex.cli_session.shutil.which', return_value=None), \
                patch('bridge.clients.codex.cli_session.Path.home', return_value=self.home):
            self.assertEqual(_local_cli(self.home, 'codex'), str(runtime))

    def test_local_proxy_prefers_selected_daemon_package_over_another_path_cli(self):
        runtime = self.home / 'packages/app-server-daemon/current/bin' / MANAGED_NAME
        runtime.parent.mkdir(parents=True)
        runtime.write_text('#!/bin/sh\n')
        runtime.chmod(0o700)
        with patch('bridge.clients.codex.cli_session.shutil.which', return_value='/other/codex'):
            self.assertEqual(_local_cli(self.home, 'codex'), str(runtime))

    def test_local_daemon_status_uses_native_read_only_probe(self):
        report = {'status': 'running', 'socketPath': self.fake.socket_path}
        with patch('bridge.clients.codex.cli_session._local_cli', return_value='/fixture/codex'), \
                patch('bridge.clients.codex.cli_session.subprocess.run',
                      return_value=subprocess.CompletedProcess([], 0, json.dumps(report))) as run:
            self.assertEqual(self.sessions.daemon_status(), 'running')
            self.assertTrue(self.sessions.daemon_may_be_running())
        self.assertEqual(run.call_args.args[0], ['/fixture/codex', 'app-server', 'daemon', 'version'])
        self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(self.home))
        self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')
        self.assertEqual(run.call_args.kwargs['errors'], 'replace')

    def test_local_daemon_status_only_reports_stopped_without_runtime_evidence(self):
        Path(self.fake.socket_path).unlink()
        with patch('bridge.clients.codex.cli_session.subprocess.run') as run:
            self.assertEqual(self.sessions.daemon_status(), 'stopped')
            self.assertFalse(self.sessions.daemon_may_be_running())
            run.assert_not_called()
        state = self.home / 'app-server-daemon'
        state.mkdir()
        (state / 'app-server.pid').touch()
        with patch('bridge.clients.codex.cli_session._local_cli',
                   side_effect=AppServerUnavailable('no binary')):
            self.assertEqual(self.sessions.daemon_status(), 'unknown')
            self.assertTrue(self.sessions.daemon_may_be_running())

    def test_local_daemon_probe_error_fails_closed(self):
        with patch('bridge.clients.codex.cli_session._local_cli', return_value='/fixture/codex'), \
                patch('bridge.clients.codex.cli_session.subprocess.run',
                      return_value=subprocess.CompletedProcess([], 1, '')):
            self.assertEqual(self.sessions.daemon_status(), 'unknown')
            self.assertTrue(self.sessions.daemon_may_be_running())

    def test_missing_daemon_package_does_not_start_or_install(self):
        self.socket = Path(self.fake.socket_path)
        self.socket.unlink()
        with self.assertRaises(AppServerUnavailable):
            self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(self.fake.sent, [])
        self.assertIsNone(_managed_cli(self.home))

    def test_remote_start_uses_saved_login_shell_and_checks_package(self):
        command = _ssh_daemon_start_command('saved-host')
        self.assertEqual(command[-2], 'saved-host')
        inner = shlex.split(command[-1])[3]
        self.assertIn('CODEX_HOME', inner)
        self.assertIn('packages/app-server-daemon', inner)
        self.assertIn('app-server daemon start', inner)
        self.assertNotIn('daemon bootstrap', inner)

    def test_explicit_start_uses_selected_home_without_installing(self):
        managed = self.home / 'packages/app-server-daemon/current/bin' / MANAGED_NAME
        managed.parent.mkdir(parents=True)
        managed.write_text('#!/bin/sh\n')
        managed.chmod(0o700)
        self.assertTrue(self.sessions.available())
        report = {'status': 'started', 'socketPath': self.fake.socket_path}
        with patch('bridge.clients.codex.cli_session.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 0, json.dumps(report))) as run:
            self.sessions._start_daemon()
        self.assertEqual(run.call_args.args[0], [str(managed), 'app-server', 'daemon', 'start'])
        self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(self.home))

    def test_same_thread_explicit_attaches_share_one_resume(self):
        entered, release = threading.Event(), threading.Event()
        original = self.fake.request
        def request(method, params, **kwargs):
            if method == 'thread/resume':
                entered.set()
                release.wait(2)
            return original(method, params, **kwargs)
        self.fake.request = request
        results = []
        first = threading.Thread(target=lambda: results.append(self.sessions.attach('thread-a', explicit=True)))
        second = threading.Thread(target=lambda: results.append(self.sessions.attach('thread-a', explicit=True)))
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        release.set()
        first.join(2)
        second.join(2)
        self.assertEqual(len(results), 2)
        self.assertEqual(len([method for method, _ in self.fake.sent if method == 'thread/resume']), 1)


if __name__ == '__main__':
    unittest.main()
