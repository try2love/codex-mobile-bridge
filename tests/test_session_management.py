"""Bridge preferences and native archive safety, using synthetic sessions only."""
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.api.httpd import GatewayServer
from bridge.features.auth.auth import password_record
from relay.protocol import validate as validate_relay
from bridge.app.service import Bridge
from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.clients.codex.ipc import IPCError
from bridge.clients.codex.cli_session import CliSessions
from bridge.clients.codex.shared_server import SharedServer
from bridge.features.endpoints.wsl import WslEntry
from bridge.features.sessions.archive import ArchiveCommands
from bridge.features.sessions.create import ArchiveNotSent, ArchiveRejected, archive_thread, check_archive_runtime
from bridge.features.sessions.pins import SessionPins
from bridge.features.sessions.store import SessionStore

ROOT = Path(__file__).resolve().parents[1]


class SessionManagement(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.sid = str(uuid.uuid4())

    def test_bridge_pins_shared_between_clients_and_isolated_by_environment(self):
        web = SessionPins(self.home, 'local:/fixture/.codex')
        app = SessionPins(self.home, 'local:/fixture/.codex')
        wsl = SessionPins(self.home, 'wsl:fixture:user:/home/user/.codex')
        web.set(self.sid, True)
        self.assertEqual(app.identifiers(), (self.sid,))
        self.assertEqual(wsl.identifiers(), ())
        app.set(self.sid, False)
        self.assertEqual(web.identifiers(), ())
        with self.assertRaises(ValueError): web.set(self.sid, 'true')

    def test_pin_revisions_reject_late_writes_after_reload_with_opaque_ids(self):
        client = str(uuid.uuid4())
        pins = SessionPins(self.home, 'claude')
        sid = 'ssh:fixture:opaque/session-id'
        pins.set(sid, True)
        pins.set(sid, False, client, 2)
        reloaded = SessionPins(self.home, 'claude')
        before = reloaded.path.read_bytes()
        stale = reloaded.set(sid, True, client, 1)
        self.assertFalse(stale['bridgePinned'])
        self.assertEqual(stale['revision'], 2)
        self.assertEqual(reloaded.path.read_bytes(), before)
        self.assertEqual(reloaded.identifiers(), ())
        reloaded.set(sid, True, str(uuid.uuid4()), 1)
        self.assertEqual(reloaded.identifiers(), (sid,))
        for revision in (True, 0, -1, 1.5, 9007199254740992):
            with self.assertRaises(ValueError): pins.set(sid, True, client, revision)
        with self.assertRaises(ValueError): pins.set(sid, True, client)

    def test_provider_pins_are_native_free_and_provider_isolated(self):
        from bridge.clients.manager import DesktopSessions
        with closing(DesktopSessions(self.home)) as manager:
            sid = 'opaque/native-session'
            with patch.object(manager, 'require_enabled'), patch.object(manager, '_read', side_effect=AssertionError('native read')):
                result = manager.call('deepseek', 'pin', sid, {'pinned': True})
                self.assertTrue(result['bridgePinned'])
                self.assertEqual(manager.session_pins['claude'].identifiers(), ())
                self.assertEqual(manager.session_pins['deepseek'].identifiers(), (sid,))
            manager.call = Mock(return_value={'sessions': [{'id': sid}]})
            with patch.object(manager, '_list_activity'):
                self.assertTrue(manager.list_with_activity('deepseek')['sessions'][0]['bridgePinned'])
                self.assertFalse(manager.list_with_activity('claude')['sessions'][0]['bridgePinned'])
            validate_relay('POST', '/api/desktop-sessions/deepseek/pin')
            with self.assertRaises(PermissionError): validate_relay('GET', '/api/desktop-sessions/deepseek/pin')

    def test_old_pin_is_sorted_before_sql_pagination_but_default_order_stays_recent(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as database, database:
            database.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, originator TEXT, source TEXT, is_pinned INT)')
            database.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)', [
                (str(i), 'chat ' + str(i), '/project', i, 0, 'Codex Desktop', 'vscode', int(i == 149)) for i in range(150)])
        store = SessionStore(self.home)
        before = (self.home / 'state_5.sqlite').read_bytes()
        self.assertEqual([row['id'] for row in store.list(limit=2)], ['149', '148'])
        self.assertEqual([row['id'] for row in store.list(limit=2, pinned_ids=['0'])], ['0', '149'])
        self.assertEqual([row['id'] for row in store.list(limit=2, offset=1, pinned_ids=['0'])], ['149', '148'])
        self.assertEqual([row['id'] for row in store.list(query='chat 0', pinned_ids=['0'])], ['0'])
        self.assertEqual((self.home / 'state_5.sqlite').read_bytes(), before)

    def test_subtree_inventory_has_no_ui_limit_and_excludes_unrelated_threads(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as database, database:
            database.execute('CREATE TABLE threads(id TEXT, originator TEXT, source TEXT)')
            database.execute('INSERT INTO threads VALUES(?,?,?)', (self.sid, 'Codex Desktop', 'vscode'))
            database.execute('INSERT INTO threads VALUES(?,?,?)', ('unrelated', 'Codex Desktop', 'vscode'))
            database.executemany('INSERT INTO threads VALUES(?,?,?)', [(str(i), 'fixture', json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': self.sid}}})) for i in range(150)])
        identifiers = {row['id'] for row in SessionStore(self.home).lifecycle_threads(root_id=self.sid)}
        self.assertEqual(len(identifiers), 151)
        self.assertNotIn('unrelated', identifiers)

    def test_native_safety_refuses_busy_approval_unknown_and_running_descendants(self):
        for update in ({'status': {'type': 'active'}}, {'requests': [{'id': 1}]},
                       {'status': {'type': 'missing'}}, {'turns': [{'status': 'inProgress'}]}):
            request = Mock(return_value={'thread': {'id': self.sid, 'status': {'type': 'idle'}, **update}})
            with self.subTest(update=update), self.assertRaises(ValueError): check_archive_runtime(request, [self.sid])
        request = Mock(side_effect=lambda method, params: {'thread': {'id': params['threadId'], 'status': {'type': 'active' if params['threadId'] == 'child' else 'idle'}}})
        with self.assertRaises(ValueError): check_archive_runtime(request, [self.sid, 'child'])
        self.assertTrue(all(call.args[0] == 'thread/read' and call.args[1]['includeTurns'] is False for call in request.call_args_list))

    def test_archive_receipt_reconciles_lost_response_without_replaying_and_can_unarchive(self):
        native = {'archived': False}
        commands = ArchiveCommands(self.home)
        calls = []
        def prepare(metadata):
            def send():
                calls.append('archive'); native['archived'] = True
                raise ConnectionError('lost reply')
            return send
        request_id = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, '待核对'):
            commands.perform(self.sid, True, request_id, lambda: native, prepare)
        result = ArchiveCommands(self.home).perform(self.sid, True, request_id, lambda: native, prepare)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(calls, ['archive'])
        def restore(metadata):
            return lambda: native.update(archived=False)
        self.assertFalse(commands.perform(self.sid, False, str(uuid.uuid4()), lambda: native, restore)['archived'])

    def test_unknown_result_blocks_new_write_and_validation_does_not_record_intent(self):
        commands = ArchiveCommands(self.home)
        read = lambda: {'archived': False}
        dispatch = Mock(return_value=Mock(side_effect=ConnectionError('lost')))
        with self.assertRaises(ValueError): commands.perform(self.sid, True, str(uuid.uuid4()), read, dispatch)
        with self.assertRaises(ValueError): commands.perform(self.sid, True, str(uuid.uuid4()), read, dispatch)
        self.assertEqual(dispatch.call_count, 1)
        other = ArchiveCommands(self.home / 'other')
        with self.assertRaises(ValueError): other.perform(self.sid, True, str(uuid.uuid4()), read, Mock(side_effect=ValueError('busy')))
        self.assertFalse(other.path.exists())

    def test_native_preflight_failure_is_not_an_unknown_write(self):
        commands = ArchiveCommands(self.home)
        read = lambda: {'archived': False}
        def prepare(metadata):
            return lambda: archive_thread('fixture', self.home, self.sid, True, [self.sid])
        def operation(executable, home, cwd, call):
            return call(lambda method, params: {'thread': {'id': self.sid, 'status': {'type': 'active'}}})
        with patch('bridge.features.sessions.create._runtime_operation', side_effect=operation):
            with self.assertRaises(ArchiveNotSent): commands.perform(self.sid, True, str(uuid.uuid4()), read, prepare)
        self.assertEqual(json.loads(commands.path.read_text()), {})

    def test_cli_archive_uses_same_home_control_runtime_and_never_resumes(self):
        cli = CliSessions(self.home, lambda *_: None, lambda: None)
        cli.client = Mock()
        cli.client.connect.return_value = {'codexHome': str(self.home)}
        cli._start_daemon = Mock()
        cli.client.request.return_value = {'thread': {'id': self.sid, 'status': {'type': 'idle'}}}
        call = cli.archive_call(self.sid, True, [self.sid])
        call()
        self.assertEqual([c.args[0] for c in cli.client.request.call_args_list], ['thread/read', 'thread/archive'])
        cli._start_daemon.assert_not_called()
        cli.client.connect.return_value = {'codexHome': '/different/home'}
        with self.assertRaises(Exception): cli.archive_call(self.sid, False, [self.sid])

    def test_confirmed_cli_archive_refuses_connection_change_after_stopping(self):
        cli = CliSessions(self.home, lambda *_: None, lambda: None)
        cli.client = Mock(); cli.client.connect.return_value = {'codexHome': str(self.home)}
        def changed():cli.client.process = object()
        with patch('bridge.features.sessions.create.stop_archive_runtime') as stop:
            call = cli.archive_call(self.sid, True, [self.sid], disconnect=True, check_scope=changed)
            with self.assertRaises(AppServerUnavailable):call()
            stop.assert_called_once()
            cli.client.request.assert_not_called()

    def test_explicit_cli_archive_starts_only_original_control_daemon_if_stopped(self):
        cli = CliSessions(self.home, lambda *_: None, lambda: None)
        cli.client = Mock()
        cli.client.connect.side_effect = [AppServerUnavailable('stopped'), {'codexHome': str(self.home)}]
        cli.client.request.return_value = {'thread': {'id': self.sid, 'status': {'type': 'notLoaded'}}}
        cli._start_daemon = Mock()
        cli.archive_call(self.sid, True, [self.sid])()
        cli._start_daemon.assert_called_once_with()
        self.assertEqual([call.args[0] for call in cli.client.request.call_args_list], ['thread/read', 'thread/archive'])

    def test_only_ipc_connect_file_absence_can_bypass_desktop_probe(self):
        bridge = Bridge.__new__(Bridge)
        bridge.host = 'local'; bridge.codex_home = self.home
        bridge.accounts = SimpleNamespace(blockers=lambda: [])
        bridge.remote_bridges = {}; bridge.lock = threading.RLock(); bridge.live = {}
        bridge.ipc = SimpleNamespace(path='fixture')
        bridge.store = SimpleNamespace()
        missing = IPCError('desktop absent'); missing.__cause__ = FileNotFoundError('endpoint')
        follower = Mock(); follower.connect.side_effect = missing
        with patch('bridge.app.service.DesktopIPC', return_value=follower):
            bridge.assert_desktop_idle(identifiers=[self.sid], allow_missing_ipc=True)
        follower.close.assert_called_once()
        (self.home / '.codex-global-state.json').write_text('{}')
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError('metadata changed')):
            with self.assertRaisesRegex(ValueError, '读取'):
                bridge.assert_desktop_idle(identifiers=[self.sid], allow_missing_ipc=True)

    def test_stopped_app_requires_missing_ipc_and_fresh_verified_gui_absence(self):
        bridge = Bridge.__new__(Bridge)
        bridge.host = 'local'; bridge.codex_home = self.home
        bridge.accounts = SimpleNamespace(blockers=lambda: [], index={'desktopExecutable': '/fixture/Codex'})
        bridge.catalog_reader = SimpleNamespace(executable='/fixture/codex')
        bridge.remote_bridges = {}; bridge.lock = threading.RLock(); bridge.live = {}
        bridge.ipc = SimpleNamespace(path='fixture'); bridge.store = SimpleNamespace()
        missing = IPCError('desktop absent'); missing.__cause__ = FileNotFoundError('endpoint')
        follower = Mock(); follower.connect.side_effect = missing
        app = Mock(); app.processes.return_value = []
        with patch('bridge.app.service.DesktopIPC', return_value=follower), patch('bridge.app.service.DesktopApp', return_value=app) as desktop:
            bridge.assert_desktop_idle(identifiers=[self.sid], allow_stopped_app=True)
            desktop.assert_called_once_with('/fixture/Codex', self.home)
            app.validate.assert_called_once_with('/fixture/codex')
            app.processes.return_value = [123]
            with self.assertRaisesRegex(ValueError, '无法确认'):
                bridge.assert_desktop_idle(identifiers=[self.sid], allow_stopped_app=True)
            app.processes.side_effect = PermissionError('process visibility denied')
            with self.assertRaisesRegex(ValueError, '无法确认'):
                bridge.assert_desktop_idle(identifiers=[self.sid], allow_stopped_app=True)
            follower.connect.side_effect = IPCError('timeout')
            with self.assertRaisesRegex(ValueError, '无法确认'):
                bridge.assert_desktop_idle(identifiers=[self.sid], allow_stopped_app=True)

    def test_wsl_archive_and_unarchive_preserve_entry_runtime_and_guard_receipts(self):
        entry = WslEntry.__new__(WslEntry)
        entry.host = 'wsl:fixture'; entry.write_lock = threading.RLock(); entry.closed = threading.Event()
        entry.lock = threading.RLock(); entry.live = {}; entry.receipts = {}
        native = {'archived': False, 'cwd': '/home/user/project', 'model_provider': 'fixture', 'status': 'idle'}
        entry.store = SimpleNamespace(lock=threading.RLock(), cache={}, get=lambda sid: dict(native),
                                     lifecycle_threads=lambda **_: [{'id': self.sid}],
                                     archive_flags=lambda sid: {sid: bool(native['archived'])})
        entry.archive_commands = ArchiveCommands(self.home)
        runtime = Mock()
        def request(method, params):
            if method == 'thread/read': return {'thread': {'id': self.sid, 'status': {'type': native['status']}}}
            if method == 'thread/turns/list':return {'data': [{'id': 'turn-fixture', 'status': 'inProgress'}]}
            if method == 'thread/backgroundTerminals/list':return {'data': []}
            if method == 'turn/interrupt':
                native['status'] = 'idle'
                return {}
            native['archived'] = method == 'thread/archive'
            return {}
        runtime.request.side_effect = request
        entry.runtime = Mock(return_value=runtime)
        self.assertTrue(entry.archive(self.sid, True, str(uuid.uuid4()))['archived'])
        self.assertFalse(entry.archive(self.sid, False, str(uuid.uuid4()))['archived'])
        entry.runtime.assert_called_with(allow_start=True)
        self.assertEqual(native['cwd'], '/home/user/project')
        self.assertEqual(native['model_provider'], 'fixture')
        self.assertTrue({'thread/archive', 'thread/unarchive'} <= SharedServer.METHODS)
        self.assertFalse(any(call.args[0] in ('thread/resume', 'turn/start') for call in runtime.request.call_args_list))
        native['status'] = 'active'
        self.assertTrue(entry.archive(self.sid, True, str(uuid.uuid4()), disconnect=True)['archived'])
        self.assertEqual(native['status'], 'idle')
        writes = [call.args for call in runtime.request.call_args_list if call.args[0] == 'turn/interrupt']
        self.assertEqual(writes, [('turn/interrupt', {'threadId': self.sid, 'turnId': 'turn-fixture'})])
        self.assertFalse(entry.archive(self.sid, False, str(uuid.uuid4()))['archived'])
        entry.receipts = {self.sid + ':send:x': {'result': {'status': 'unknown'}}}
        with self.assertRaisesRegex(ValueError, '待核对'): entry.archive(self.sid, True, str(uuid.uuid4()))


class SessionManagementHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = password_record('fixture password')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.sid = str(uuid.uuid4())
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as database, database:
            database.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, originator TEXT, source TEXT)')
            database.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?)', [
                (self.sid if i == 0 else str(uuid.uuid4()), 'chat ' + str(i), str(self.home), i, 0, 'Codex Desktop', 'vscode') for i in range(150)])
        self.bridge = Bridge(self.home, self.home / 'bridge', codex_bin=self.home / 'missing-runtime')
        self.addCleanup(self.bridge.close)
        config = {'auth': {'mode': 'password', 'username': 'admin', **self.password}, 'origins': []}
        self.server = GatewayServer(('127.0.0.1', 0), self.bridge, config, ROOT / 'web')
        self.addCleanup(self.server.server_close)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.server.origins.add(self.origin)
        self.server.hosts.add(self.origin.removeprefix('http://'))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={'Origin': self.origin, 'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), json.loads(response.read())
        connection.close()
        return result

    def login(self):
        status, headers, body = self.request('POST', '/api/login', {'username': 'admin', 'password': 'fixture password'})
        self.assertEqual(status, 200)
        return {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': body['csrf']}

    def test_pin_and_archive_routes_keep_login_csrf_and_relay_boundaries(self):
        headers = self.login()
        for action, body in (('pin', {'pinned': True}), ('archive', {'archived': True, 'id': str(uuid.uuid4())})):
            path = '/api/sessions/' + self.sid + '/' + action
            self.assertEqual(self.request('POST', path, body)[0], 401)
            self.assertEqual(self.request('POST', path, body, {'Cookie': headers['Cookie']})[0], 403)
            validate_relay('POST', path)
            with self.assertRaises(PermissionError): validate_relay('GET', path)
        path = '/api/sessions/' + self.sid + '/pin'
        self.assertEqual(self.request('POST', path, {'pinned': True}, headers)[0], 200)
        self.assertEqual(self.request('POST', path, {'pinned': 'true'}, headers)[0], 400)
        recent = self.request('GET', '/api/sessions?scope=host&host=local&order=recent', headers=headers)[2]['sessions']
        self.assertEqual(recent[0]['id'], self.sid)
        self.assertTrue(recent[0]['bridgePinned'])
        project = self.request('GET', '/api/sessions?scope=host&host=local&order=project', headers=headers)[2]['sessions']
        self.assertNotIn(self.sid, [row['id'] for row in project])

    def test_disconnect_flag_requires_confirmation_shape_and_reaches_original_route(self):
        headers = self.login()
        route = '/api/sessions/' + self.sid + '/archive'
        rid = str(uuid.uuid4())
        body = {'archived': True, 'id': rid, 'disconnect': True}
        with patch.object(self.bridge, 'archive', return_value={'status': 'confirmed'}) as archive:
            self.assertEqual(self.request('POST', route, body)[0], 401)
            self.assertEqual(self.request('POST', route, body, {'Cookie': headers['Cookie']})[0], 403)
            archive.assert_not_called()
            self.assertEqual(self.request('POST', route, body, headers)[0], 200)
            archive.assert_called_once_with(self.sid, True, rid, disconnect=True)
        for invalid in ({**body, 'disconnect': 'yes'}, {**body, 'archived': False}, {**body, 'extra': True}):
            self.assertEqual(self.request('POST', route, invalid, headers)[0], 400)

    def test_pin_metadata_preserves_auth_and_rejects_delayed_older_intent(self):
        headers = self.login()
        path = '/api/sessions/' + self.sid + '/pin'
        client = str(uuid.uuid4())
        latest = {'pinned': False, 'clientId': client, 'revision': 2}
        self.assertEqual(self.request('POST', path, latest)[0], 401)
        self.assertEqual(self.request('POST', path, latest, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, latest, headers)[0], 200)
        status, _, stale = self.request('POST', path, {**latest, 'pinned': True, 'revision': 1}, headers)
        self.assertEqual(status, 200)
        self.assertFalse(stale['bridgePinned'])
        self.assertEqual(stale['revision'], 2)
        for body in ({'pinned': True, 'revision': 3}, {**latest, 'revision': True}, {**latest, 'extra': 1}):
            self.assertEqual(self.request('POST', path, body, headers)[0], 400)

    def test_explicit_native_writer_rejection_is_actionable_and_not_persisted_as_unknown(self):
        headers = self.login()
        route = '/api/sessions/' + self.sid + '/archive'
        rejection = ArchiveRejected({'code': -32600, 'message': 'thread fixture already has an active writer'})
        with patch.object(self.bridge, 'assert_desktop_idle'), patch('bridge.app.service.archive_thread', side_effect=rejection):
            status, _, result = self.request('POST', route, {'archived': True, 'id': str(uuid.uuid4())}, headers)
        self.assertEqual(status, 409)
        self.assertEqual(result['code'], 'archive_rejected')
        self.assertEqual(result['reason'], 'active_writer')
        self.assertNotIn('结果待核对', result['error'])
        self.assertEqual(json.loads(self.bridge.archive_commands.path.read_text()), {})
        self.assertFalse(self.bridge.store.get(self.sid)['archived'])

    def test_app_archive_and_unarchive_dispatch_native_metadata_only_and_confirm_store(self):
        headers = self.login()
        route = '/api/sessions/' + self.sid + '/archive'
        calls = []
        def native(executable, home, sid, archived, identifiers):
            calls.append((Path(home), sid, archived, identifiers))
            with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as database, database:
                database.execute('UPDATE threads SET archived=? WHERE id=?', (int(archived), sid))
        with patch.object(self.bridge, 'assert_desktop_idle') as idle, patch('bridge.app.service.archive_thread', side_effect=native):
            for archived in (True, False):
                status, _, result = self.request('POST', route, {'archived': archived, 'id': str(uuid.uuid4())}, headers)
                self.assertEqual(status, 200)
                self.assertEqual(result['status'], 'confirmed')
                self.assertEqual(result['archived'], archived)
            self.assertEqual(idle.call_count, 2)
        self.assertEqual(calls, [(self.home, self.sid, True, [self.sid]), (self.home, self.sid, False, [self.sid])])


if __name__ == '__main__':
    unittest.main()
