import copy
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.ipc import IPCError
from bridge.side_chat import NativeSideChat, NativeSideChats, native_candidates
import test_bridge as support


class NativeSideChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT/'.tmp')
        self.fixture = support.DesktopFixture(Path(self.temp.name))
        self.parent = str(uuid.uuid4())
        self.fixture.state.update(ephemeral=True, sideConversation=True, forkedFromId=self.parent)
        self.chat = NativeSideChat(self.fixture.path, self.parent, support.THREAD, 'local')

    def tearDown(self):
        self.chat.close()
        self.fixture.close()
        self.temp.cleanup()

    def test_attach_and_send_stay_on_original_owner_without_overrides(self):
        view = self.chat.refresh()
        self.assertTrue(view['connected'])
        self.assertEqual(view['provider'], 'custom-api')
        identifier = str(uuid.uuid4())
        self.assertEqual(self.chat.send('question', identifier)['status'], 'accepted')
        self.chat.send('question', identifier)
        sends = [r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn']
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]['targetClientId'], 'owner')
        turn = sends[0]['params']['turnStart']
        self.assertEqual(turn['request']['threadId'], support.THREAD)
        self.assertTrue(turn['context']['inheritThreadSettings'])
        self.assertFalse({'model', 'modelProvider', 'cwd', 'sandboxPolicy'} & turn['request'].keys())
        self.assertEqual({r['method'] for r in self.fixture.requests}, {'initialize', 'thread-follower-start-turn'})
        with self.assertRaises(ValueError):
            self.chat.send('different', identifier)

    def test_persisted_or_unrelated_threads_are_never_attached(self):
        for changes in ({'ephemeral': False}, {'sideConversation': False}, {'forkedFromId': str(uuid.uuid4())}):
            baseline = copy.deepcopy(self.fixture.state)
            self.fixture.state.update(changes)
            with self.assertRaises(IPCError):
                self.chat.refresh()
            self.assertFalse(self.chat.view()['connected'])
            self.fixture.state = baseline
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))

    def test_closed_desktop_cannot_receive_a_new_send(self):
        self.chat.refresh()
        self.fixture.loaded = False
        with self.assertRaises(IPCError):
            self.chat.send('must not send', str(uuid.uuid4()))
        self.assertFalse(self.chat.view()['connected'])
        self.assertIsNone(self.chat.state)
        self.assertFalse(self.chat.submissions)
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))

    def test_unknown_delivery_is_not_replayed(self):
        self.chat.refresh()
        identifier = str(uuid.uuid4())
        with patch.object(self.chat.ipc, 'request', side_effect=IPCError('timeout')) as request:
            self.assertEqual(self.chat.send('one question', identifier)['status'], 'unknown')
            self.assertEqual(self.chat.send('one question', identifier)['status'], 'unknown')
            self.assertEqual(request.call_count, 1)
        self.chat.close()
        self.assertIsNone(self.chat.state)
        self.assertFalse(self.chat.submissions)

    def test_busy_or_approval_pending_is_not_bypassed(self):
        for change in ({'threadRuntimeStatus': {'type': 'active'}},
                       {'threadRuntimeStatus': {'type': 'idle'}, 'requests': [{'id': 1}]}):
            self.fixture.state.update(change)
            with self.assertRaises(ValueError):
                self.chat.send('wait', str(uuid.uuid4()))
        self.assertFalse(self.chat.submissions)

    def test_auth_owner_scoping_and_idle_detachment(self):
        manager = NativeSideChats(self.fixture.path, 'local', candidates=lambda: [support.THREAD])
        body = {'connectionId': str(uuid.uuid4())}
        try:
            view = manager.operate(self.parent, 'device-a', 'connect', body)
            self.assertTrue(view['connected'])
            with self.assertRaises(ValueError):
                manager.operate(self.parent, 'device-b', 'read', body)
            with self.assertRaises(ValueError):
                manager.operate(self.parent, 'device-a', 'read', {'connectionId': str(uuid.uuid4())})
            chat = manager.chats['device-a', self.parent, body['connectionId']]
            manager.operate(self.parent, 'device-a', 'disconnect', {'connectionId': str(uuid.uuid4())})
            self.assertFalse(chat.closed)
            chat.touched = time.monotonic() - 301
            manager.reap()
            self.assertTrue(chat.closed)
            self.assertFalse(manager.chats)
        finally:
            manager.close()

    def test_candidate_log_is_only_a_hint(self):
        root = Path(self.temp.name)/'home'
        directory = root/'Library/Logs/com.openai.codex'/time.strftime('%Y/%m/%d')
        directory.mkdir(parents=True)
        (directory/'fixture.log').write_text('private text that must not be returned\n'
            f'info conversationId={support.THREAD} durationMs=10 method=thread/inject_items\n'
            f'info conversationId={self.parent} method=thread/fork\n')
        with patch('bridge.side_chat.sys.platform', 'darwin'), patch('bridge.side_chat.Path.home', return_value=root):
            self.assertEqual(native_candidates(), [support.THREAD])


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
        self.assertEqual(calls[-1][2], 'read')
        for body in ({'action':'connect'}, {'action':'disconnect'},
                     {'action':'send','submissionId':str(uuid.uuid4()),'text':'question'}):
            self.assertEqual(self.request('POST', path, body, {'Cookie':auth['Cookie']})[0], 403)
            self.assertEqual(self.request('POST', path, body, {**auth, 'Origin':'https://evil.example'})[0], 403)
            self.assertEqual(self.request('POST', path, body, auth)[0], 200)
        self.assertEqual(self.request('POST', path, {'action':'connect','modelProvider':'other'}, auth)[0], 400)
        self.assertEqual(self.request('POST', path, {'action':'create'}, auth)[0], 400)
