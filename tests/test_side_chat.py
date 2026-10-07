import copy
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.side_chat import SideChat, SideChats, SideChatError, BOUNDARY
import test_bridge as support


class RuntimeFixture:
    def __init__(self, executable, home, cwd, event):
        self.cwd, self.event = cwd, event
        self.child = str(uuid.uuid4())
        self.calls, self.responses = [], []
        self.closed = False
        self.fail_send = False

    def start(self):
        pass

    def request(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'thread/fork':
            return {'thread': {'id': self.child, 'ephemeral': True}, 'modelProvider': params['modelProvider'], 'model': params['model'], 'cwd': self.cwd}
        if method == 'turn/start':
            if self.fail_send:
                raise SideChatError('timeout')
            self.emit('turn/started', turn={'id': 'turn', 'status': 'inProgress'})
            self.emit('item/started', turnId='turn', item={'id': 'user', 'type': 'userMessage', 'content': params['input']})
            self.emit('item/started', turnId='turn', item={'id': 'reply', 'type': 'agentMessage', 'text': ''})
            return {'turn': {'id': 'turn'}}
        if method == 'turn/interrupt':
            self.emit('turn/completed', turn={'id': 'turn', 'status': 'interrupted'})
        return {}

    def emit(self, method, **params):
        self.event({'method': method, 'params': {'threadId': self.child, **params}})

    def write(self, value):
        self.responses.append(value)

    def close(self):
        self.closed = True


class SideChatTests(unittest.TestCase):
    def setUp(self):
        self.parent = str(uuid.uuid4())
        self.chat = SideChat('runtime', '/home', self.parent, '/project',
            {'model': 'fixture', 'modelProvider': 'custom', 'approvalPolicy': 'on-request'}, RuntimeFixture)
        self.addCleanup(self.chat.close)
        self.runtime = self.chat.runtime

    def test_fork_boundary_goal_and_no_parent_execution(self):
        calls = self.runtime.calls
        self.assertEqual([m for m, _ in calls], ['thread/fork', 'thread/inject_items'])
        self.assertEqual(calls[0][1]['threadId'], self.parent)
        self.assertTrue(calls[0][1]['ephemeral'])
        self.assertNotIn('deferGoalContinuation', calls[0][1])
        self.assertEqual(calls[1][1]['threadId'], self.chat.id)
        self.assertEqual(BOUNDARY, calls[1][1]['items'][0]['content'][0]['text'])
        self.assertFalse(self.chat.view()['turns'])

    def test_streaming_dedupe_stop_and_close(self):
        submission = str(uuid.uuid4())
        self.assertEqual(self.chat.send('question', submission)['status'], 'accepted')
        self.runtime.emit('item/agentMessage/delta', turnId='turn', itemId='reply', delta='answer')
        view = self.chat.view()
        self.assertEqual(view['turns'][0]['messages'][1]['text'], 'answer')
        self.assertEqual(view['status'], 'active')
        self.chat.send('question', submission)
        self.assertEqual(sum(m == 'turn/start' for m, _ in self.runtime.calls), 1)
        with self.assertRaises(ValueError):
            self.chat.send('changed', submission)
        with self.assertRaises(ValueError):
            self.chat.send('another', str(uuid.uuid4()))
        self.assertEqual(self.chat.stop()['status'], 'idle')
        self.chat.close()
        self.assertTrue(self.runtime.closed)
        self.assertFalse(self.chat.view()['turns'])
        self.assertFalse(self.chat.view()['connected'])
        for method, params in self.runtime.calls[1:]:
            self.assertEqual(params['threadId'], self.chat.id)

    def test_unknown_send_never_replays(self):
        self.runtime.fail_send = True
        submission = str(uuid.uuid4())
        self.assertEqual(self.chat.send('question', submission)['status'], 'unknown')
        self.chat.send('question', submission)
        self.assertEqual(sum(m == 'turn/start' for m, _ in self.runtime.calls), 1)

    def test_approval_scoped_to_child_and_single_use(self):
        self.chat._event({'id': 70, 'method': 'item/commandExecution/requestApproval', 'params': {
            'threadId': self.parent, 'command': 'must not approve'}})
        self.assertFalse(self.chat.view()['requests'])
        self.chat._event({'id': 71, 'method': 'item/commandExecution/requestApproval', 'params': {
            'threadId': self.chat.id, 'command': 'echo test', 'availableDecisions': ['accept', 'decline']}})
        with self.assertRaises(ValueError):
            self.chat.respond(71, {'decision': 'acceptForSession'})
        self.chat.respond(71, {'decision': 'accept'})
        self.assertEqual(self.runtime.responses[-1], {'id': 71, 'result': {'decision': 'accept'}})
        with self.assertRaises(ValueError):
            self.chat.respond(71, {'decision': 'accept'})

    def test_question_and_permission_responses(self):
        self.chat._event({'id': 72, 'method': 'item/tool/requestUserInput', 'params': {
            'threadId': self.chat.id, 'questions': [{'id': 'q', 'question': 'Which?'}]}})
        with self.assertRaises(ValueError):
            self.chat.respond(72, {'answers': {'other': ['x']}})
        self.chat.respond(72, {'answers': {'q': ['choice']}})
        self.assertEqual(self.runtime.responses[-1]['result'], {'answers': {'q': {'answers': ['choice']}}})
        self.chat._event({'id': 73, 'method': 'item/permissions/requestApproval', 'params': {
            'threadId': self.chat.id, 'permissions': {'network': {'enabled': True}}}})
        self.chat.respond(73, {'decision': 'decline'})
        self.assertEqual(self.runtime.responses[-1]['result'], {'permissions': {}, 'scope': 'turn'})

    def test_desktop_only_async_question_is_not_offered_as_rpc_approval(self):
        self.chat.state['turns'] = [{'turnId': 'one', 'status': 'completed', 'items': [
            {'id': 'q', 'type': 'agentMessage', 'text': 'Question', 'questions': [{'title': 'Continue?'}]}]}]
        request = self.chat.view()['requests'][0]
        self.assertFalse(request['supported'])
        self.assertNotIn('桌面', request['params']['message'])

    def test_runtime_death_is_not_resumed(self):
        self.runtime.emit('bridge/disconnected')
        with self.assertRaises(ValueError):
            self.chat.send('no resurrection', str(uuid.uuid4()))
        self.assertFalse(self.chat.view()['connected'])
        self.assertFalse(any(m == 'thread/resume' for m, _ in self.runtime.calls))

    def test_model_mismatch_destroys_child_before_turn(self):
        runtime = self.runtime
        def factory(*args):
            other = RuntimeFixture(*args)
            original = other.request
            def request(method, params):
                result = original(method, params)
                if method == 'thread/fork': result['modelProvider'] = 'wrong'
                return result
            other.request = request
            factory.runtime = other
            return other
        with self.assertRaises(ValueError):
            SideChat('runtime', '/home', self.parent, '/project', {'model': 'x', 'modelProvider': 'y'}, factory)
        self.assertTrue(factory.runtime.closed)
        self.assertEqual(len(factory.runtime.calls), 1)

    def test_shared_views_creation_retry_and_stale_close(self):
        with tempfile.TemporaryDirectory(dir=support.ROOT/'.tmp') as directory:
            manager = SideChats('runtime', '/home', directory, 'local',
                factory=lambda *args: SideChat(*args, runtime_factory=RuntimeFixture))
            self.addCleanup(manager.close)
            create = {'creationId': str(uuid.uuid4())}
            args = ({'cwd': '/project'}, {'model': 'fixture', 'modelProvider': 'custom'})
            with patch('bridge.side_chat.check_runtime'):
                view = manager.operate(self.parent, 'create', create, *args)
            # A fresh browser/device has no connection identifier; it finds the same child.
            self.assertEqual(manager.operate(self.parent, 'read', {})['id'], view['id'])
            self.assertEqual(manager.operate(self.parent, 'create', create, *args)['id'], view['id'])
            manager.operate(self.parent, 'close', {'id': view['id']})
            self.assertIsNone(manager.operate(self.parent, 'read', {})['id'])
            with self.assertRaises(ValueError): manager.operate(self.parent, 'create', create, *args)
            new = manager.operate(self.parent, 'create', {'creationId': str(uuid.uuid4())}, *args)
            with self.assertRaises(ValueError): manager.operate(self.parent, 'close', {'id': view['id']})
            self.assertEqual(manager.operate(self.parent, 'read', {})['id'], new['id'])


class SideChatServiceTests(unittest.TestCase):
    setUp = support.IntegrationTests.setUp
    tearDown = support.IntegrationTests.tearDown

    def test_service_creation_preserves_parent_and_shared_identity(self):
        bridge = self.bridge
        bridge.side_chats.factory = lambda *args: SideChat(*args, runtime_factory=RuntimeFixture)
        self.fixture.state['cwd'] = str(self.root)
        before = copy.deepcopy(self.fixture.state)
        with patch('bridge.side_chat.check_runtime'), patch.object(bridge, 'activate', side_effect=AssertionError('Parent must not activate')):
            view = bridge.side_chat(support.THREAD, 'phone', 'create', {'creationId': str(uuid.uuid4())})
        self.assertTrue(view['connected'])
        self.assertEqual(bridge.side_chat(support.THREAD, 'browser', 'read', {})['id'], view['id'])
        self.assertEqual(self.fixture.state, before)
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))
        bridge.side_chat(support.THREAD, 'browser', 'close', {'id': view['id']})


class SideChatHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    setUp = support.HttpTests.setUp
    tearDown = support.HttpTests.tearDown
    request = support.HttpTests.request
    login = support.HttpTests.login

    def test_auth_csrf_and_closed_schema(self):
        path = '/api/sessions/'+support.THREAD+'/side-chat'
        self.assertEqual(self.request('GET', path)[0], 401)
        auth = self.login()
        calls = []
        self.server.bridge.side_chat = lambda *args: calls.append(args) or {'connected': True}
        self.assertEqual(self.request('GET', path, headers=auth)[0], 200)
        for body in ({'action': 'create', 'creationId': str(uuid.uuid4())},
                     {'action': 'close', 'id': support.THREAD}, {'action': 'stop', 'id': support.THREAD},
                     {'action': 'respond', 'id': support.THREAD, 'requestId': 1, 'response': {'decision': 'decline'}},
                     {'action': 'send', 'id': support.THREAD, 'submissionId': str(uuid.uuid4()), 'text': 'question'}):
            self.assertEqual(self.request('POST', path, body, {'Cookie': auth['Cookie']})[0], 403)
            self.assertEqual(self.request('POST', path, body, {**auth, 'Origin': 'https://evil.example'})[0], 403)
            self.assertEqual(self.request('POST', path, body, auth)[0], 200)
        self.assertEqual(self.request('POST', path, {'action': 'create', 'modelProvider': 'other'}, auth)[0], 400)
        self.assertEqual(self.request('POST', path, {'action': 'connect'}, auth)[0], 400)
