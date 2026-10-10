import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.features.sessions.create import CreationError, ForkUnavailable, fork_copy
from bridge.clients.codex.ipc import IPCError, DesktopIPC
from bridge.app.service import Bridge, LiveSession
from bridge.clients.codex.remote import RemoteStore

ROOT = Path(__file__).resolve().parents[1]


class MessageActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.root = Path(self.temp.name)
        self.source, self.child = str(uuid.uuid4()), str(uuid.uuid4())
        self.bridge = Bridge(self.root, self.root / 'data')
        self.session = LiveSession(self.source)
        self.session.connected = True
        self.session.owner = 'original-owner'
        self.session.state = {'id': self.source, 'title': 'Original', 'cwd': str(self.root), 'modelProvider': 'custom-api',
                              'latestModel': 'local-long-model', 'latestReasoningEffort': 'high',
                              'latestThreadSettings': {'serviceTier': 'default'}, 'threadRuntimeStatus': {'type': 'idle'},
                              'turns': [self.turn('first'), self.turn('last')], 'requests': []}
        self.bridge.live[self.source] = self.session
        self.target = patch.object(self.bridge, '_target', return_value=self.session).start()
        self.call = patch.object(self.bridge, '_call', return_value={'ok': True}).start()
        self.fork = patch('bridge.app.service.fork_copy', return_value=self.child).start()

    def tearDown(self):
        patch.stopall()
        self.bridge.close()
        self.temp.cleanup()

    @staticmethod
    def turn(identifier):
        return {'turnId': identifier, 'status': 'completed', 'params': {'input': [{'type': 'text', 'text': 'User ' + identifier}]},
                'items': [{'id': identifier + '-u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'User ' + identifier}]},
                          {'id': identifier + '-a', 'type': 'agentMessage', 'text': 'Answer ' + identifier, 'phase': 'final'}]}

    def body(self, turn='last', action='edit', role='user'):
        self.session.timeline.update(self.session.view())
        row = next(r for r in self.session.timeline.rows if r['turnId'] == turn and r['role'] == role)
        return {'id': str(uuid.uuid4()), 'action': action, 'key': self.session.timeline.cursor(row['key']), 'version': row['version'], 'text': 'Edited'}

    def test_latest_edit_native_owner_and_durable_deduplication(self):
        body = self.body()
        result = self.bridge.message_action(self.source, body)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.call.call_args.args[1], 'thread-follower-edit-last-user-turn')
        params = self.call.call_args.args[2]
        self.assertEqual(params, {'turnId': 'last', 'message': 'Edited', 'shouldSendPermissionOverrides': False, 'serviceTier': 'default'})
        self.assertEqual(DesktopIPC.VERSIONS['thread-follower-edit-last-user-turn'], 2)
        self.bridge.message_action(self.source, body)
        self.assertEqual(self.call.call_count, 1)
        self.bridge.close()
        self.bridge = Bridge(self.root, self.root / 'data')
        self.assertEqual(self.bridge.message_action(self.source, body)['status'], 'accepted')
        with self.assertRaises(ValueError):
            self.bridge.message_action(self.source, {**body, 'text': 'Different'})

    def test_active_stale_and_older_edit_are_rejected_before_mutation(self):
        for mode in ('active', 'stale', 'older'):
            body = self.body('first' if mode == 'older' else 'last')
            if mode == 'active': self.session.state['threadRuntimeStatus']['type'] = 'active'
            if mode == 'stale': body['version'] = 'old'
            with self.assertRaises(ValueError): self.bridge.message_action(self.source, body)
            self.session.state['threadRuntimeStatus']['type'] = 'idle'
        self.call.assert_not_called()
        self.fork.assert_not_called()
        self.assertEqual(self.bridge.message_actions, {})

    def test_fork_keeps_source_untouched_and_retains_provider_settings(self):
        before = copy.deepcopy(self.session.state)
        body = self.body('first', 'fork', 'assistant')
        result = self.bridge.message_action(self.source, body)
        self.assertEqual(result['id'], self.child)
        self.assertEqual(result['status'], 'created')
        self.assertEqual(self.session.state, before)
        self.call.assert_not_called()
        args = self.fork.call_args.kwargs
        self.assertEqual(args['turn_id'], 'first')
        self.assertEqual(args['settings'], {'modelProvider': 'custom-api', 'model': 'local-long-model', 'config': {'model_reasoning_effort': 'high'}, 'serviceTier': 'default'})
        self.assertEqual(self.bridge._fork_origin(self.child)['id'], self.source)
        self.bridge.message_action(self.source, body)
        self.assertEqual(self.fork.call_count, 1)

    def test_historical_edit_targets_only_new_child(self):
        child = LiveSession(self.child)
        child.connected = True
        child.owner = 'child-owner'
        self.target.side_effect = [self.session, child]
        result = self.bridge.message_action(self.source, self.body('first', 'edit-fork'))
        self.assertEqual(result['status'], 'accepted')
        self.assertIs(self.call.call_args.args[0], child)
        self.assertEqual(self.call.call_args.args[2]['turnId'], 'first')

    def test_activation_failure_retains_branch_and_unsent_edit(self):
        self.target.side_effect = [self.session, IPCError('offline')]
        body = self.body('first', 'edit-fork')
        result = self.bridge.message_action(self.source, body)
        self.assertEqual((result['id'], result['status'], result['draft']), (self.child, 'created', 'Edited'))
        self.assertEqual(self.bridge.message_action(self.source, body), result)
        self.fork.assert_called_once()
        self.call.assert_not_called()

    def test_unknown_native_edit_or_fork_never_replays(self):
        body = self.body()
        self.call.side_effect = IPCError('lost acknowledgement')
        with self.assertRaises(IPCError): self.bridge.message_action(self.source, body)
        self.assertEqual(self.bridge.message_action(self.source, body)['status'], 'unknown')
        self.call.assert_called_once()
        fork_body = self.body('first', 'fork', 'assistant')
        self.fork.side_effect = CreationError('unknown')
        with self.assertRaises(CreationError): self.bridge.message_action(self.source, fork_body)
        self.assertEqual(self.bridge.message_action(self.source, fork_body)['status'], 'unknown')
        self.fork.assert_called_once()

    def test_unsupported_runtime_can_retry_same_intent_after_update(self):
        body = self.body('first', 'fork', 'assistant')
        self.fork.side_effect = [ForkUnavailable('update required'), self.child]
        with self.assertRaises(ForkUnavailable):
            self.bridge.message_action(self.source, body)
        self.assertNotIn(body['id'], self.bridge.message_actions)
        self.assertEqual(self.bridge.message_action(self.source, body)['id'], self.child)

    def test_remote_capability_failure_can_retry_without_replaying_a_fork(self):
        self.bridge.host = 'remote-ssh-discovered:fixture'
        self.bridge.hosts.hosts = lambda: {self.bridge.host: {'alias': 'fixture'}}
        body = self.body('first', 'fork', 'assistant')
        with patch('bridge.app.service.ssh_read', side_effect=[{'unavailable': 'update required'}, {'id': self.child}]):
            with self.assertRaises(ForkUnavailable):
                self.bridge.message_action(self.source, body)
            self.assertEqual(self.bridge.message_action(self.source, body)['id'], self.child)

    def test_remote_fork_executes_on_original_host_with_encoded_payload(self):
        self.bridge.host = 'remote-ssh-discovered:fixture'
        self.bridge.hosts.hosts = lambda: {self.bridge.host: {'alias': 'fixture'}}
        self.bridge.store = RemoteStore('fixture')
        with patch('bridge.app.service.ssh_read', return_value={'id': self.child}) as ssh:
            result = self.bridge.message_action(self.source, self.body('first', 'fork', 'assistant'))
        self.assertEqual(result['host'], self.bridge.host)
        self.assertEqual(ssh.call_args.args[0], 'fixture')
        self.assertIn('fork_copy(runtime, home', ssh.call_args.args[1])
        self.fork.assert_not_called()

    def test_streaming_answer_and_steering_message_have_no_branch_or_edit(self):
        self.session.state['turns'][-1]['status'] = 'inProgress'
        self.session.state['turns'][-1]['items'].append({'id': 'steer', 'type': 'steeringUserMessage', 'input': [{'type': 'text', 'text': 'extra'}]})
        self.session.timeline.update(self.session.view())
        self.assertFalse(any(r['forkable'] for r in self.session.timeline.rows if r['turnId'] == 'last'))
        self.assertFalse(self.session.timeline.rows[-1]['editable'])


class ForkRuntimeTests(unittest.TestCase):
    def test_runtime_forks_with_goal_continuation_disabled_and_never_sends_a_turn(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            root = Path(folder); child = str(uuid.uuid4()); source = str(uuid.uuid4())
            script = root / 'runtime.py'; log = root / 'calls.json'
            script.write_text('''import json,sys
from pathlib import Path
calls=[]
for line in sys.stdin:
 r=json.loads(line);calls.append(r)
 if 'id' not in r:continue
 result={'thread':{'id':''' + repr(child) + ''','turns':[{'id':'selected'}]},'modelProvider':'custom'} if r['method']=='thread/fork' else {}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
Path(''' + repr(str(log)) + ''').write_text(json.dumps(calls))
''', encoding='utf-8')
            def schema(argv, **kwargs):
                Path(argv[-1], 'ThreadForkParams.json').write_text(json.dumps({'properties': {'lastTurnId': {}, 'deferGoalContinuation': {}}}))
                return subprocess.CompletedProcess(argv, 0)
            real_popen = subprocess.Popen
            with patch('bridge.features.sessions.create.subprocess.run', side_effect=schema), patch('bridge.features.sessions.create.subprocess.Popen', side_effect=lambda argv, **kwargs: real_popen([sys.executable, str(script)], **kwargs)):
                self.assertEqual(fork_copy('runtime', root, str(root), source, 'selected', 'Branch', {'modelProvider': 'custom'}), child)
            calls = json.loads(log.read_text())
            self.assertEqual([c['method'] for c in calls], ['initialize', 'initialized', 'thread/fork', 'thread/name/set'])
            self.assertEqual(calls[2]['params']['lastTurnId'], 'selected')
            self.assertTrue(calls[2]['params']['deferGoalContinuation'])

    def test_runtime_without_goal_deferral_fails_before_starting_an_agent(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            def schema(argv, **kwargs):
                Path(argv[-1], 'ThreadForkParams.json').write_text(json.dumps({'properties': {'lastTurnId': {}}}))
                return subprocess.CompletedProcess(argv, 0)
            with patch('bridge.features.sessions.create.subprocess.run', side_effect=schema), patch('bridge.features.sessions.create._runtime_operation') as operation:
                with self.assertRaises(CreationError): fork_copy('runtime', Path(folder), folder, str(uuid.uuid4()), 'turn', 'Branch', {'modelProvider': 'custom'})
                operation.assert_not_called()


class MessageActionHttpTests(unittest.TestCase):
    import test_bridge as support
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    setUp = support.HttpTests.setUp
    tearDown = support.HttpTests.tearDown
    request = support.HttpTests.request
    login = support.HttpTests.login

    def test_action_requires_login_csrf_origin_and_routes_to_selected_host(self):
        from unittest.mock import Mock
        bridge = self.server.bridge
        bridge.message_action = Mock(return_value={'status': 'accepted'})
        tid = str(uuid.uuid4()); path = '/api/sessions/' + tid + '/message-action'
        body = {'action': 'edit', 'id': str(uuid.uuid4()), 'key': 'k', 'version': 'v', 'text': 'changed'}
        self.assertEqual(self.request('POST', path, body)[0], 401)
        auth = self.login()
        self.assertEqual(self.request('POST', path, body, {'Cookie': auth['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, body, {**auth, 'Origin': 'https://other.test'})[0], 403)
        bridge.message_action.assert_not_called()
        self.assertEqual(self.request('POST', path, body, auth)[2]['status'], 'accepted')
        bridge.message_action.assert_called_once_with(tid, body)
