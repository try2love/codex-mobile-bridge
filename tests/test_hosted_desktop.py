"""Desktop host compatibility without starting a second native executor."""
import json
import copy
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock

from bridge.clients.codex import desktop_runtime as runtime
from bridge.clients.codex.desktop_stdio import DesktopStdio
from bridge.clients.codex.app_server import AppServerRequestNotSent, AppServerUnavailable, AppServerUnknownResult
from bridge.clients.codex.desktop_host import HostedSessions, CONTROL_METHODS, _Follower, MetadataClient
from bridge.clients.codex.ipc import DesktopIPC, IPCError, validate_stream_state


class HostedOwnerTests(unittest.TestCase):
    def test_primary_app_and_its_bundled_host_have_the_same_verified_owner(self):
        app = {'pid': 1, 'parent': 99, 'image': '/Applications/Codex.app/Contents/MacOS/Codex'}
        host = {'pid': 2, 'parent': 1, 'image': '/Applications/Codex.app/Contents/Frameworks/Runtime.framework/Helpers/Host.app/Contents/MacOS/Host'}
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'process_identity', side_effect=lambda pid: dict({1: app, 2: host}[pid])):
            self.assertEqual(runtime.desktop_owner(app, app['image']), (app, None))
            self.assertEqual(runtime.desktop_owner(host, app['image']), (app, host))

    def test_unrelated_host_and_replaced_ancestor_are_rejected(self):
        app = {'pid': 1, 'parent': 99, 'image': '/Applications/Codex.app/Contents/MacOS/Codex'}
        host = {'pid': 2, 'parent': 1, 'image': '/tmp/Host'}
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'process_identity', return_value=app):
            with self.assertRaises(ValueError): runtime.desktop_owner(host, app['image'])
        host['image'] = '/Applications/Codex.app/Contents/Frameworks/Host'
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'process_identity', return_value={**app, 'image': '/Applications/Other.app/Contents/MacOS/Other'}):
            with self.assertRaises(ValueError): runtime.desktop_owner(host, app['image'])

    def test_native_host_launch_scopes_are_preserved(self):
        args = ['-c', 'features.code_mode_host=true', 'app-server', '--analytics-default-enabled',
                '--plugin-mcp-launch-cwd', 'plugin@source', 'cua_repl', '/project',
                '--mcp-launch-cwd', 'node_repl', '/runtime/node_repl', '/project']
        self.assertEqual(runtime.server_arguments(args, '/private/runtime.sock'),
                         args + ['--listen', 'unix:///private/runtime.sock'])
        self.assertIsNone(runtime.server_arguments(args[:-1], '/private/runtime.sock'))

    def test_windows_utility_host_and_legacy_main_are_distinguished_by_ancestry(self):
        main = {'pid': 1, 'parent': 99, 'image': 'C:/Codex/Codex.exe'}
        host = {'pid': 2, 'parent': 1, 'image': main['image']}
        identities = {1: main, 2: host, 99: {'image': 'C:/Windows/explorer.exe'}}
        with patch.object(runtime.sys, 'platform', 'win32'), \
             patch.object(runtime, 'process_identity', side_effect=lambda pid: identities[pid]):
            self.assertEqual(runtime.desktop_owner(main, main['image']), (main, None))
            self.assertEqual(runtime.desktop_owner(host, main['image']), (main, host))

    def test_invalid_hosted_record_never_falls_back_to_the_legacy_socket(self):
        ipc = DesktopIPC('/legacy.sock', codex_home='/fixture')
        with patch.object(runtime, 'hosted_endpoint', side_effect=ValueError('generation changed')), \
             patch('bridge.clients.codex.ipc.connect_stream') as connect:
            with self.assertRaises(IPCError): ipc.connect()
            connect.assert_not_called()

    def test_windows_follower_owns_a_duplicate_and_closes_it_on_setup_failure(self):
        from bridge.platforms.windows import local_service
        api = Mock(); api.GetCurrentProcess.return_value = 1
        api.DuplicateHandle.return_value = 12; api.DUPLICATE_SAME_ACCESS = 2
        connection = Mock(); connection.fileno.return_value = 11
        with patch.dict('sys.modules', {'_winapi': api}), patch.object(local_service, 'WindowsPipe') as pipe:
            local_service.byte_stream(connection, duplicate=True)
            api.DuplicateHandle.assert_called_once_with(1, 11, 1, 0, False, 2)
            pipe.assert_called_once_with(12)
            connection.close.assert_not_called()
            api.SetNamedPipeHandleState.side_effect = OSError('pipe mode')
            with self.assertRaises(OSError): local_service.byte_stream(connection, duplicate=True)
            api.CloseHandle.assert_called_once_with(12)


class HostedStdioTests(unittest.TestCase):
    def setUp(self):
        self.writes, self.app = [], []
        self.written = threading.Event()
        def write(data):
            self.writes.append(json.loads(data)); self.written.set()
        self.router = DesktopStdio(write, self.app.append, lambda info: None,
                                   control_methods={'thread/loaded/list', 'turn/start'})
        self.addCleanup(self.router.close)
        self.router.from_app({'id': 1, 'method': 'initialize', 'params': {}})
        self.router.from_native({'id': self.writes[-1]['id'], 'result': {'codexHome': '/fixture'}})
        self.written.clear()

    def test_native_control_reply_is_private_and_the_app_keeps_receiving_events(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            call = pool.submit(self.router.request_packet, 'turn/start', {'threadId': 't'})
            self.assertTrue(self.written.wait(2))
            request = self.writes[-1]
            self.router.from_native({'id': request['id'], 'result': {'turn': {'id': 'turn'}}})
            self.assertEqual(call.result(2)['result']['turn']['id'], 'turn')
        event = {'method': 'turn/started', 'params': {'threadId': 't', 'turn': {'id': 'turn'}}}
        self.router.from_native(event)
        self.assertEqual(self.app[-1], event)
        self.assertEqual(len(self.app), 2)
        with self.assertRaises(AppServerRequestNotSent):
            self.router.request_packet('thread/resume', {'threadId': 't'})

    def test_desktop_and_phone_cannot_answer_the_same_approval_twice(self):
        approval = {'id': 7, 'method': 'item/commandExecution/requestApproval',
                    'params': {'threadId': 't', 'turnId': 'turn'}}
        self.router.from_native(approval)
        self.router.respond(7, {'decision': 'decline'}, thread_id='t')
        count = len(self.writes)
        self.router.from_app({'id': 7, 'result': {'decision': 'accept'}})
        self.assertEqual(len(self.writes), count)
        with self.assertRaises(AppServerRequestNotSent):
            self.router.respond(7, {'decision': 'accept'}, thread_id='t')
        self.router.from_native({**approval, 'id': 8})
        self.router.from_app({'id': 8, 'result': {'decision': 'decline'}})
        with self.assertRaises(AppServerRequestNotSent):
            self.router.respond(8, {'decision': 'accept'}, thread_id='t')

    def test_approval_cannot_cross_thread_boundaries(self):
        self.router.from_native({'id': 7, 'method': 'item/fileChange/requestApproval',
                                 'params': {'threadId': 'other'}})
        count = len(self.writes)
        with self.assertRaises(AppServerRequestNotSent):
            self.router.respond(7, {'decision': 'accept'}, thread_id='t')
        self.assertEqual(len(self.writes), count)

    def test_resolved_approval_cannot_be_replayed_by_either_view(self):
        self.router.from_native({'id': 7, 'method': 'item/fileChange/requestApproval',
                                 'params': {'threadId': 't'}})
        self.router.from_native({'method': 'serverRequest/resolved',
                                 'params': {'threadId': 't', 'requestId': 7}})
        count = len(self.writes)
        self.router.from_app({'id': 7, 'result': {'decision': 'accept'}})
        with self.assertRaises(AppServerRequestNotSent):
            self.router.respond(7, {'decision': 'accept'}, thread_id='t')
        self.assertEqual(len(self.writes), count)


class HostedSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.thread = {'id': 't', 'cwd': self.temp.name, 'modelProvider': 'original-provider',
                       'model': 'original-model', 'status': {'type': 'idle'}, 'historyMode': 'paginated',
                       'path': '/fixture/rollout.jsonl', 'ephemeral': False, 'turns': []}
        self.turns = [{'id': 'turn', 'status': 'completed', 'items': []}]
        self.items = [{'turnId': 'turn', 'item': {'type': 'agentMessage', 'id': 'answer', 'text': 'history'}}]
        self.writes, self.app, self.states = [], [], []
        self.drop_start = False
        def write(data):
            message = json.loads(data); self.writes.append(message)
            method = message.get('method')
            if method is None:
                return
            params = message.get('params') or {}
            if method == 'initialize': value = {'codexHome': self.temp.name}
            elif method == 'thread/loaded/list': value = {'data': ['t'], 'nextCursor': None}
            elif method == 'thread/read': value = {'thread': copy.deepcopy(self.thread)}
            elif method == 'thread/turns/list': value = {'data': copy.deepcopy(self.turns[-1:]), 'nextCursor': 'older' if len(self.turns) > 1 else None}
            elif method == 'thread/items/list': value = {'data': [copy.deepcopy(row) for row in self.items
                if params.get('turnId', row['turnId']) == row['turnId']], 'nextCursor': None}
            elif method == 'turn/start':
                if self.drop_start: return
                value = {'turn': {'id': 'new-turn', 'status': 'inProgress'}}
            else: raise AssertionError(method)
            self.router.from_native({'id': message['id'], 'result': value})
        self.router = DesktopStdio(write, self.app.append, lambda value: None, control_methods=CONTROL_METHODS)
        self.sessions = HostedSessions(self.temp.name, self.router,
            lambda tid, state: self.states.append(state), lambda *args: None)
        self.addCleanup(self.sessions._disconnect)
        self.addCleanup(self.router.close)
        self.router.from_app({'id': 1, 'method': 'initialize', 'params': {}})

    def test_passive_attach_reads_paged_history_without_resuming(self):
        state = self.sessions.attach('t')
        validate_stream_state(99, state)
        self.assertEqual(state['turns'][0]['items'][0]['text'], 'history')
        self.assertEqual(state['modelProvider'], 'original-provider')
        methods = [m.get('method') for m in self.writes]
        self.assertNotIn('thread/resume', methods)
        self.assertNotIn('thread/start', methods)
        self.assertNotIn('turn/start', methods)
        with self.assertRaises(AppServerRequestNotSent): self.sessions.attach('saved-only')

    def test_attach_with_an_oversized_older_turn_only_reads_current_turn(self):
        self.turns.insert(0, {'id': 'old', 'status': 'completed', 'items': []})
        self.items.insert(0, {'turnId': 'old', 'item': {'type': 'agentMessage', 'text': 'x' * 8192}})
        with patch('bridge.clients.codex.desktop_host.MAX_LINE', 4096):
            state = self.sessions.attach('t')
        self.assertEqual(state['turnsPagination'], {'hasLoadedOldest': False})
        self.assertEqual([turn['turnId'] for turn in state['turns']], ['turn'])
        queries = [m['params'] for m in self.writes if m.get('method') == 'thread/items/list']
        self.assertEqual([p.get('turnId') for p in queries], ['turn'])

    def test_paginated_empty_thread_is_readable_before_materialization(self):
        self.thread['path'] = None
        state = self.sessions.attach('t')
        self.assertEqual(state['turns'], [])
        self.assertFalse(any(m.get('method') == 'thread/turns/list' for m in self.writes))

    def test_unknown_control_state_refuses_attachment_and_mutations(self):
        self.sessions.attach('t')
        self.thread['status'] = {'type': 'future-state'}
        with self.assertRaises(AppServerUnavailable): self.sessions.attach('t')
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('t', 'thread-follower-start-turn', {'turnStart': {'request': {'input': []}}})
        self.assertFalse(any(m.get('method') == 'turn/start' for m in self.writes))

    def test_unknown_turn_shape_also_refuses_mutations_before_native_dispatch(self):
        self.sessions.attach('t')
        self.sessions.states['t']['turns'][0]['status'] = 'future-status'
        with self.assertRaises(AppServerRequestNotSent):
            self.sessions.call('t', 'thread-follower-start-turn', {'turnStart': {'request': {'input': []}}})
        self.assertFalse(any(m.get('method') == 'turn/start' for m in self.writes))

    def test_unknown_send_result_is_not_replayed(self):
        self.sessions.attach('t'); self.drop_start = True
        with self.assertRaises(AppServerUnknownResult):
            self.sessions.call('t', 'thread-follower-start-turn',
                {'turnStart': {'request': {'input': [{'type': 'text', 'text': 'hello'}],
                                           'clientUserMessageId': 'submission'}}}, timeout=.01)
        sent = [m for m in self.writes if m.get('method') == 'turn/start']
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['params']['threadId'], 't')
        self.assertNotIn('modelProvider', sent[0]['params'])
        self.assertNotIn('cwd', sent[0]['params'])

    def test_native_events_and_approvals_reach_both_views(self):
        self.sessions.attach('t')
        event = {'method': 'turn/started', 'params': {'threadId': 't',
                 'turn': {'id': 'active', 'status': 'inProgress', 'items': []}}}
        self.router.from_native(event)
        self.router.from_native({'method': 'item/agentMessage/delta',
            'params': {'threadId': 't', 'turnId': 'active', 'itemId': 'output', 'delta': 'live text'}})
        self.assertEqual(self.states[-1]['turns'][-1]['items'][0]['text'], 'live text')
        self.assertIn(event, self.app)
        approval = {'id': 41, 'method': 'item/commandExecution/requestApproval',
                    'params': {'threadId': 't', 'turnId': 'active'}}
        self.router.from_native(approval)
        self.assertEqual(self.states[-1]['requests'], [approval])
        self.sessions.call('t', 'thread-follower-command-approval-decision', {'requestId': 41, 'decision': 'decline'})
        self.assertEqual([m for m in self.writes if m.get('id') == 41],
                         [{'id': 41, 'result': {'decision': 'decline'}}])

    def test_production_ipc_handshake_follow_and_owner_use_the_same_runtime(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close); self.addCleanup(right.close)
        token, generation, owner = 'a' * 64, 'fixture-generation', 'desktop-runtime:fixture-generation'
        follower = _Follower(right, owner, token, generation, self.sessions)
        self.addCleanup(follower.close)
        self.sessions.on_state = follower.snapshot
        threading.Thread(target=follower.read, daemon=True).start()
        delivered = threading.Event(); snapshots = []
        def event(value):
            snapshots.append(value); delivered.set()
        endpoint = {'path': '/fixture/host.sock', 'generation': generation, 'controlToken': token}
        ipc = DesktopIPC('/fixture/old.sock', event, codex_home=self.temp.name)
        self.addCleanup(ipc.close)
        with patch('bridge.clients.codex.ipc.connect_stream', return_value=left), \
             patch.object(runtime, 'hosted_endpoint', return_value=endpoint):
            ipc.connect()
            self.assertEqual(ipc.owner('t'), owner)
            ipc.follow('t', owner)
            self.assertTrue(delivered.wait(2))
            self.assertEqual(snapshots[-1]['params']['change']['conversationState']['id'], 't')
            ipc.follow('t', owner, False)
            legacy = Mock()
            legacy.request.return_value = {'resultType': 'success', 'handledByClientId': 'original-ide-owner',
                                           'result': {'ok': True}}
            with patch.object(ipc, '_legacy_connection', return_value=legacy):
                self.assertEqual(ipc.owner('ide-thread'), 'original-ide-owner')
                ipc.follow('ide-thread', 'original-ide-owner')
                legacy.follow.assert_called_once_with('ide-thread', 'original-ide-owner', True, 'local')
                ipc.request('thread-follower-interrupt-turn', {'conversationId': 'ide-thread'},
                            target='original-ide-owner')
                self.assertEqual(legacy.request.call_args.args[0], 'thread-follower-interrupt-turn')
        self.assertFalse(any(m.get('method') in ('thread/resume', 'turn/start') for m in self.writes))

    def test_quota_preflight_reads_fresh_native_failure_through_hosted_ipc(self):
        from bridge.app.service import Bridge
        from bridge.features.sessions.quota_continue import candidate
        self.thread.update(modelProvider='openai', status={'type': 'systemError'})
        self.turns = [{'id': 'quota-turn', 'status': 'failed', 'items': [],
                       'error': {'codexErrorInfo': 'usageLimitExceeded'}}]
        left, right = socket.socketpair()
        self.addCleanup(left.close); self.addCleanup(right.close)
        owner = 'desktop-runtime:fixture-generation'
        follower = _Follower(right, owner, 'a' * 64, 'fixture-generation', self.sessions)
        self.addCleanup(follower.close)
        self.sessions.on_state = follower.snapshot
        threading.Thread(target=follower.read, daemon=True).start()
        bridge = Bridge.__new__(Bridge)
        bridge.host = 'local'; bridge.codex_home = Path(self.temp.name)
        bridge.ipc = SimpleNamespace(path='/fixture/old.sock', codex_home=self.temp.name)
        bridge.accounts = Mock(); bridge.accounts.blockers.return_value = []
        bridge.live = {}; bridge.remote_bridges = {}; bridge.lock = threading.RLock()
        observed = []
        def validate(state, actual_owner):
            observed.append(candidate(SimpleNamespace(connected=True, runtime='desktop', origin='desktop',
                state=state, owner=actual_owner)))
        endpoint = {'path': '/fixture/host.sock', 'generation': 'fixture-generation', 'controlToken': 'a' * 64}
        with patch('bridge.clients.codex.ipc.connect_stream', return_value=left), \
                patch.object(runtime, 'hosted_endpoint', return_value=endpoint) as endpoint_lookup:
            bridge.assert_desktop_idle(timeout=3, identifiers={'t'}, validate_state=validate, allow_quota_error=True)
        self.assertTrue(endpoint_lookup.called)
        self.assertTrue(observed)
        self.assertTrue(all(row and row['turnId'] == 'quota-turn' and row['owner'] == owner for row in observed))
        self.assertFalse(any(m.get('method') in ('thread/resume', 'thread/start', 'turn/start') for m in self.writes))

    def test_metadata_preserves_native_errors_and_explicit_not_sent_results(self):
        client = MetadataClient(self.temp.name)
        with patch.object(client.client, 'request', return_value={'result': {
                'id': 'native', 'error': {'code': -32600, 'message': 'already has an active writer'}}}):
            with self.assertRaisesRegex(Exception, 'active writer') as error:
                client.request('thread/fork', {'threadId': 't'})
            self.assertEqual(error.exception.rpc_error['code'], -32600)
        failure = IPCError('generation changed'); failure.not_sent = True
        with patch.object(client.client, 'request', side_effect=failure):
            with self.assertRaises(AppServerRequestNotSent): client.request('thread/fork', {'threadId': 't'})


if __name__ == '__main__':
    unittest.main()
