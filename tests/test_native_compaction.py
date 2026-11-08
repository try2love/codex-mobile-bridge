import asyncio
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bridge.app.lifecycle import private_json
from bridge.app.service import Bridge
from bridge.app.service import LiveSession
from bridge.clients.claude.adapter import Claude
from bridge.clients.claude.cli_session import _compact_cli, _supports_compact
from bridge.clients.claude.model import gateway_id
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.features.sessions.compaction import CompactCommands
import test_bridge
import test_codex_cli_session
import test_wsl_entries


class CompactReceipts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=test_bridge.ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = CompactCommands(self.root, private_json)
        self.sid, self.rid = str(uuid.uuid4()), str(uuid.uuid4())

    def test_ack_is_durable_and_never_replays(self):
        dispatch = Mock()
        prepare = Mock(return_value=dispatch)
        first = self.ledger.perform(self.sid, self.rid, prepare)
        self.assertEqual(first['status'], 'accepted')
        reopened = CompactCommands(self.root, private_json)
        self.assertEqual(reopened.perform(self.sid, self.rid, prepare), first)
        prepare.assert_called_once_with(); dispatch.assert_called_once_with()
        with self.assertRaises(ValueError):
            reopened.perform(str(uuid.uuid4()), self.rid, prepare)

    def test_lost_ack_survives_restart_and_blocks_new_request_id(self):
        def dispatch():
            self.assertEqual(json.loads(self.ledger.path.read_text())[self.rid]['status'], 'unknown')
            raise TimeoutError('native may have accepted')
        first = self.ledger.perform(self.sid, self.rid, lambda: dispatch)
        self.assertEqual(first['status'], 'unknown')
        reopened = CompactCommands(self.root, private_json)
        prepare = Mock()
        self.assertEqual(reopened.perform(self.sid, self.rid, prepare), first)
        with self.assertRaises(ValueError): reopened.perform(self.sid, str(uuid.uuid4()), prepare)
        prepare.assert_not_called()

    def test_busy_preflight_is_not_recorded_as_unknown(self):
        with self.assertRaises(ValueError):
            self.ledger.perform(self.sid, self.rid, Mock(side_effect=ValueError('busy')))
        self.assertFalse(self.ledger.path.exists())


class CodexNativeCompact(unittest.TestCase):
    setUp = test_codex_cli_session.CliSessionTests.setUp
    tearDown = test_codex_cli_session.CliSessionTests.tearDown

    def test_cli_compacts_existing_identity_without_turn_start_or_resume(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.sent.clear()
        self.sessions._queue_refresh = Mock()
        self.sessions.call('thread-a', 'thread-follower-compact-thread', {})
        methods = [method for method, _ in self.fake.sent]
        self.assertIn('thread/read', methods)
        self.assertEqual(self.fake.sent[-1], ('thread/compact/start', {'threadId': 'thread-a'}))
        self.assertNotIn('thread/resume', methods); self.assertNotIn('turn/start', methods)

    def test_cli_fresh_active_state_blocks_compact(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.sent.clear()
        self.fake.thread['status'] = {'type': 'active', 'activeFlags': []}
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-compact-thread', {})
        self.assertNotIn('thread/compact/start', [method for method, _ in self.fake.sent])


class CodexOwnerGuard(unittest.TestCase):
    setUp = CompactReceipts.setUp
    def test_desktop_checks_only_selected_owner_and_never_activates(self):
        session = LiveSession(self.sid)
        session.connected, session.owner = True, 'original-owner'
        session.state = {'id': self.sid, 'threadRuntimeStatus': {'type': 'idle'}, 'turns': [], 'requests': []}
        bridge = SimpleNamespace(session=Mock(return_value=session), compact_commands=self.ledger,
                                 assert_desktop_idle=Mock(), _call=Mock(), accounts=None)
        result = Bridge.compact(bridge, self.sid, self.rid)
        self.assertEqual(result['status'], 'accepted')
        bridge.session.assert_called_once_with(self.sid, attach=False)
        bridge.assert_desktop_idle.assert_called_once_with(timeout=8, identifiers=[self.sid])
        bridge._call.assert_called_once_with(session, 'thread-follower-compact-thread', {})

    def test_owner_change_after_preflight_is_rejected_before_intent(self):
        session = LiveSession(self.sid)
        session.connected, session.owner = True, 'original-owner'
        session.state = {'id': self.sid, 'threadRuntimeStatus': {'type': 'idle'}, 'turns': [], 'requests': []}
        bridge = SimpleNamespace(session=Mock(return_value=session), compact_commands=self.ledger,
                                 assert_desktop_idle=Mock(side_effect=lambda **kw:setattr(session,'owner','changed')),
                                 _call=Mock(), accounts=None)
        with self.assertRaises(ValueError): Bridge.compact(bridge, self.sid, self.rid)
        bridge._call.assert_not_called()
        self.assertFalse(self.ledger.path.exists())


class WslCompact(unittest.TestCase):
    setUp = test_wsl_entries.WslSessionTests.setUp
    make_entry = test_wsl_entries.WslSessionTests.make_entry

    def test_native_compact_is_guarded_and_replay_safe(self):
        native = self.entry.server.request
        def request(method, params):
            if method == 'thread/compact/start':
                self.entry.server.calls.append((method, params))
                return {}
            return native(method, params)
        self.entry.server.request = request
        rid = str(uuid.uuid4())
        result = self.entry.compact(self.identifier, rid)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.entry.compact(self.identifier, rid), result)
        calls = [p for m,p in self.entry.server.calls if m == 'thread/compact/start']
        self.assertEqual(calls, [{'threadId': self.identifier}])

    def test_busy_thread_is_not_interrupted_by_compact(self):
        self.entry.server.thread['status'] = {'type': 'active', 'activeFlags': []}
        with self.assertRaises(ValueError): self.entry.compact(self.identifier, str(uuid.uuid4()))
        self.assertFalse(any(m == 'thread/compact/start' for m,p in self.entry.server.calls))


class ClaudeNativeCompact(unittest.TestCase):
    def owned(self, **changes):
        return SimpleNamespace(condition=threading.Condition(), catalog={'commands': [{'name': 'compact', 'builtin': True}]},
                               active=False, pending={}, error=None, finished=False, write=Mock(), **changes)

    def test_cli_dispatches_registered_builtin_and_marks_active(self):
        owned = self.owned(); rid = str(uuid.uuid4())
        _compact_cli(owned, rid)
        owned.write.assert_called_once_with({'type': 'user', 'uuid': rid, 'message': {'role': 'user', 'content': '/compact'}})
        self.assertTrue(owned.active)
        with self.assertRaises(BridgeUnavailable): _compact_cli(owned, str(uuid.uuid4()))
        self.assertEqual(owned.write.call_count, 1)

    def test_cli_rejects_custom_command_and_pending_approval(self):
        for commands in ([], [{'name': 'compact'}], [{'name': 'compact', 'builtin': False}]):
            owned = self.owned(); owned.catalog = {'commands': commands}
            self.assertFalse(_supports_compact(owned.catalog))
            with self.assertRaises(BridgeUnavailable): _compact_cli(owned, str(uuid.uuid4()))
            owned.write.assert_not_called()
        owned = self.owned(); owned.pending = {'approval': {}}
        with self.assertRaises(BridgeUnavailable): _compact_cli(owned, str(uuid.uuid4()))
        owned.write.assert_not_called()

    def adapter(self, kind='code', *, fresh=None, commands=None):
        adapter = Claude.__new__(Claude)
        native = 'native-independent-test'
        sid = gateway_id(kind, native)
        adapter.index = {sid: {'backend': kind, 'nativeId': native}}
        row = {'sessionId': native, 'cwd': '/original/project', 'turnRunning': False}
        calls = []
        async def call(surface, method, *args, **kwargs):
            calls.append((surface, method, args))
            if method == 'getSession':
                return fresh if fresh and len([c for c in calls if c[1] == 'getSession']) > 1 else row
            if method == 'getSupportedCommands':
                return [{'name': 'compact', 'builtin': True}] if commands is None else commands
            if method == 'sendMessage': return {'delivered': True}
            raise AssertionError(method)
        adapter.desktop = SimpleNamespace(capabilities={kind: ['getSession', 'getSupportedCommands', 'sendMessage']}, call=call)
        adapter.status = lambda: {'connected': True}
        return adapter, sid, native, calls, row

    def test_app_and_cowork_use_original_session_native_command(self):
        for kind in ('code', 'cowork'):
            adapter, sid, native, calls, _ = self.adapter(kind)
            rid = str(uuid.uuid4())
            result = asyncio.run(adapter.dispatch('compact', sid, {'id': rid}))
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual([c[1] for c in calls], ['getSession', 'getSupportedCommands', 'getSession', 'sendMessage'])
            args = calls[-1][2]
            self.assertEqual(args[:3], (native, '/compact', None))
            self.assertEqual(args[-1], rid)
            self.assertEqual(len(args), 8 if kind == 'code' else 5)

    def test_app_rechecks_idle_and_identity_after_command_discovery(self):
        for changed in ({'turnRunning': True}, {'cwd': '/another/project'}, {'isArchived': True}, {'sessionId': 'other'}):
            fresh = {'sessionId': 'native-independent-test', 'cwd': '/original/project', 'turnRunning': False, **changed}
            adapter, sid, _, calls, _ = self.adapter(fresh=fresh)
            with self.assertRaises(BridgeUnavailable): asyncio.run(adapter.dispatch('compact', sid, {'id': str(uuid.uuid4())}))
            self.assertNotIn('sendMessage', [c[1] for c in calls])

    def test_app_never_sends_unregistered_slash_text(self):
        adapter, sid, _, calls, _ = self.adapter(commands=[{'name': 'compact', 'builtin': False}])
        with self.assertRaises(BridgeUnavailable): asyncio.run(adapter.dispatch('compact', sid, {'id': str(uuid.uuid4())}))
        self.assertNotIn('sendMessage', [c[1] for c in calls])

    def test_app_accepts_native_descriptor_after_desktop_drops_builtin(self):
        command = {'name': 'compact', 'description': 'Free up context by summarizing the conversation so far',
                   'argumentHint': '<optional custom summarization instructions>'}
        self.assertFalse(_supports_compact({'commands': [command]}))
        adapter, sid, native, calls, _ = self.adapter(commands=[command])
        result = asyncio.run(adapter.dispatch('compact', sid, {'id': str(uuid.uuid4())}))
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(calls[-1][2][:2], (native, '/compact'))

    def test_app_rejects_shadowed_or_unknown_desktop_commands(self):
        native = {'name': 'compact', 'description': 'Free up context by summarizing the conversation so far',
                  'argumentHint': '<optional custom summarization instructions>'}
        for command in ({'name': 'compact'}, {**native, 'builtin': False},
                        {**native, 'description': native['description']+' (project)'},
                        {**native, 'description': 'Custom summarization skill'},
                        {**native, 'argumentHint': ''}):
            adapter, sid, _, calls, _ = self.adapter(commands=[command])
            with self.assertRaises(BridgeUnavailable):
                asyncio.run(adapter.dispatch('compact', sid, {'id': str(uuid.uuid4())}))
            self.assertNotIn('sendMessage', [c[1] for c in calls])


class CompactHTTP(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def setUp(self):
        test_bridge.HttpTests.setUp(self)
        self.server.bridge.compact = Mock(return_value={'status': 'accepted'})

    def test_authenticated_csrf_protected_exact_payload(self):
        path = '/api/sessions/'+test_bridge.THREAD+'/compact'
        body = {'id': str(uuid.uuid4())}
        self.assertEqual(self.request('POST', path, body)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, body, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, body, headers)[0], 200)
        self.server.bridge.compact.assert_called_once_with(test_bridge.THREAD, body['id'])
        self.assertEqual(self.request('POST', path, {**body, 'prompt': 'fake'}, headers)[0], 400)
