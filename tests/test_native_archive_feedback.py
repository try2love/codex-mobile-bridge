"""Native archive rejection, receipt proof, and reversible provider fixtures."""
import http.client
import io
import threading
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bridge.api.httpd import GatewayServer
from relay.protocol import validate as validate_relay
from bridge.clients.codex.app_server import CodexAppServer
from bridge.clients.codex.shared_server import SharedServer
from bridge.clients.manager import DesktopSessions
from bridge.features.sessions.archive import ArchiveCommands
from bridge.features.sessions.create import ArchiveRejected, archive_thread
from bridge.features.sessions.store import SessionStore

ROOT = Path(__file__).resolve().parents[1]


class ArchiveFeedback(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sid = str(uuid.uuid4())

    def test_explicit_rejection_with_complete_unchanged_metadata_can_be_retried(self):
        native = {'archived': False, '_archiveFlags': {self.sid: False, 'child': False}}
        commands = ArchiveCommands(self.root)
        prepare = Mock(return_value=Mock(side_effect=ArchiveRejected({'code': -32600, 'message': 'thread fixture already has an active writer'})))
        with self.assertRaises(ArchiveRejected) as caught:
            commands.perform(self.sid, True, str(uuid.uuid4()), lambda: native, prepare)
        self.assertEqual(caught.exception.reason, 'active_writer')
        self.assertEqual(json.loads(commands.path.read_text()), {})
        def accepted(state):
            def call():
                native['archived'] = True
                native['_archiveFlags'] = {self.sid: True, 'child': True}
            return call
        self.assertEqual(commands.perform(self.sid, True, str(uuid.uuid4()), lambda: dict(native), accepted)['status'], 'confirmed')

    def test_partial_or_unverified_rejection_and_old_unknown_never_replay(self):
        for flags in ({self.sid: False, 'child': True}, None):
            folder = self.root / str(uuid.uuid4())
            native = {'archived': False, '_archiveFlags': {self.sid: False, 'child': False}}
            def read():
                return {**native, '_archiveFlags': dict(native['_archiveFlags']) if native['_archiveFlags'] else None}
            def prepare(state):
                def call():
                    native['_archiveFlags'] = flags
                    raise ArchiveRejected({'code': -32600, 'message': 'rejected after possible partial work'})
                return call
            commands = ArchiveCommands(folder)
            request_id = str(uuid.uuid4())
            with self.assertRaisesRegex(ValueError, '待核对'): commands.perform(self.sid, True, request_id, read, prepare)
            dispatch = Mock()
            with self.assertRaisesRegex(ValueError, '待核对'): commands.perform(self.sid, True, str(uuid.uuid4()), read, dispatch)
            dispatch.assert_not_called()
            self.assertEqual(json.loads(commands.path.read_text())[request_id]['status'], 'unknown')

    def test_short_runtime_preserves_explicit_archive_rpc_error_type(self):
        output = '\n'.join(json.dumps(row) for row in [
            {'id': 1, 'result': {}},
            {'id': 2, 'result': {'thread': {'id': self.sid, 'status': {'type': 'notLoaded'}}}},
            {'id': 3, 'error': {'code': -32600, 'message': 'thread fixture already has an active writer'}},
        ]) + '\n'
        process = SimpleNamespace(stdout=io.StringIO(output), stdin=io.StringIO(), wait=Mock(return_value=0))
        with patch('bridge.features.sessions.create.subprocess.Popen', return_value=process):
            with self.assertRaises(ArchiveRejected) as caught: archive_thread('fixture', self.root, self.sid, True, [self.sid])
        self.assertEqual(caught.exception.reason, 'active_writer')

    def test_daemon_and_wsl_channels_keep_explicit_error_separate_from_disconnect(self):
        client = CodexAppServer(self.root / 'control.sock')
        def send(packet):
            client.pending[packet['id']].set_result({'error': {'code': -32601, 'message': 'unsupported'}})
        client._send = send
        with self.assertRaises(ArchiveRejected) as caught: client.request('thread/archive', {'threadId': self.sid})
        self.assertEqual(caught.exception.reason, 'unsupported')
        shared = SharedServer(Mock())
        shared.write = lambda packet: shared.pending[packet['id']].set_result({'error': {'code': -32600, 'message': 'active writer'}})
        with self.assertRaises(ArchiveRejected): shared.request('thread/archive', {'threadId': self.sid})

    def test_archive_flags_cover_full_subtree_without_reading_rollouts(self):
        database = sqlite3.connect(self.root / 'state_5.sqlite')
        database.execute('CREATE TABLE threads(id TEXT, originator TEXT, source TEXT, archived INTEGER)')
        database.executemany('INSERT INTO threads VALUES(?,?,?,?)', [
            (self.sid, 'Codex Desktop', 'vscode', 0),
            ('child', 'fixture', json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': self.sid}}}), 1),
            ('unrelated', 'Codex Desktop', 'vscode', 0)])
        database.commit(); database.close()
        self.assertEqual(SessionStore(self.root).archive_flags(self.sid), {self.sid: False, 'child': True})


class ProviderArchive(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = DesktopSessions(self.root)
        self.addCleanup(self.manager.close)
        self.manager.config['enabled'] = {'claude': True, 'deepseek': True}

    def adapter(self, provider):
        adapter = self.manager.adapters[provider]
        native = {'id': 'opaque-native-session', 'archived': False, 'runtimeKnown': True, 'status': 'idle', 'requests': [],
                  'archiveSafe': True, 'capabilities': {'archive': True, 'unarchive': True}}
        calls = []
        def call(action, sid=None, body=None):
            if action == 'archive-state':
                return {**native, '_archiveFlags': {sid: native['archived']}}
            if action == 'archive':
                calls.append((sid, dict(body)))
                native['archived'] = body['archived']
                return {'status': 'accepted'}
            raise AssertionError(action)
        adapter.call = Mock(side_effect=call)
        return adapter, native, calls

    def test_native_providers_archive_restore_and_reconcile_without_send_resume_or_delete(self):
        for provider in ('claude', 'deepseek'):
            adapter, native, calls = self.adapter(provider)
            for desired in (True, False):
                body = {'id': str(uuid.uuid4()), 'archived': desired}
                self.assertEqual(self.manager.call(provider, 'archive', native['id'], body)['status'], 'confirmed')
                self.assertEqual(native['archived'], desired)
                self.assertEqual(self.manager.call(provider, 'archive', native['id'], body)['status'], 'confirmed')
            self.assertEqual(calls, [(native['id'], {'archived': True}), (native['id'], {'archived': False})])
            self.assertTrue(all(call.args[0] in ('archive', 'archive-state') for call in adapter.call.call_args_list))

    def test_unknown_busy_pending_and_unproven_native_capability_block_before_write(self):
        adapter, native, calls = self.adapter('deepseek')
        for update in ({'status': 'active'}, {'runtimeKnown': False}, {'archiveSafe': False}, {'requests': [{'id': 1}]},
                       {'capabilities': {'archive': False, 'unarchive': False}}):
            prior = dict(native); native.update(update)
            with self.assertRaises(ValueError): self.manager.call('deepseek', 'archive', native['id'], {'id': str(uuid.uuid4()), 'archived': True})
            self.assertEqual(calls, []); native.clear(); native.update(prior)
        self.manager.db.execute('INSERT INTO requests VALUES(?,?,?)', ('old', 'fixture', json.dumps({'status': 'unknown'})))
        self.manager.db.commit()
        with self.assertRaisesRegex(ValueError, '待核对'):
            self.manager.call('deepseek', 'archive', native['id'], {'id': str(uuid.uuid4()), 'archived': True})
        self.assertEqual(calls, [])

    def test_provider_archive_http_retains_login_csrf_and_relay_boundaries(self):
        server = GatewayServer(('127.0.0.1', 0), SimpleNamespace(accounts=SimpleNamespace()),
                               {'auth': {'mode': 'password'}, 'origins': []}, ROOT / 'web')
        server.desktop_sessions = self.manager
        origin = 'http://127.0.0.1:' + str(server.server_address[1])
        server.origins.add(origin); server.hosts.add(origin.removeprefix('http://'))
        token, session = server.auth.new_session('127.0.0.1')
        auth = {'Cookie': server.auth.cookie_name + '=' + token, 'X-CSRF-Token': session['csrf']}
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def request(route, body, headers):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            connection.request('POST', route, json.dumps(body), {'Origin': origin, 'Content-Type': 'application/json', **headers})
            response = connection.getresponse(); status = response.status; response.read(); connection.close()
            return status
        try:
            for provider in ('claude', 'deepseek'):
                _, native, calls = self.adapter(provider)
                route = '/api/desktop-sessions/' + provider + '/archive'
                validate_relay('POST', route)
                with self.assertRaises(PermissionError): validate_relay('GET', route)
                body = {'sessionId': native['id'], 'archived': True, 'id': str(uuid.uuid4())}
                self.assertEqual(request(route, body, {}), 401)
                self.assertEqual(request(route, body, {'Cookie': auth['Cookie']}), 403)
                self.assertEqual(calls, [])
                self.assertEqual(request(route, body, auth), 200)
                self.assertEqual(calls, [(native['id'], {'archived': True})])
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)

    def test_transport_unknown_is_durable_even_with_a_new_request_id(self):
        adapter, native, calls = self.adapter('deepseek')
        read = adapter.call.side_effect
        def timeout(action, sid=None, body=None):
            if action == 'archive':
                calls.append(action)
                raise TimeoutError('lost response')
            return read(action, sid, body)
        adapter.call.side_effect = timeout
        with self.assertRaisesRegex(ValueError, '待核对'):
            self.manager.call('deepseek', 'archive', native['id'], {'id': str(uuid.uuid4()), 'archived': True})
        with self.assertRaisesRegex(ValueError, '待核对'):
            self.manager.call('deepseek', 'archive', native['id'], {'id': str(uuid.uuid4()), 'archived': True})
        self.assertEqual(calls, ['archive'])


if __name__ == '__main__': unittest.main()
