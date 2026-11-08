"""Permanent deletion is native, scope-confirmed, and never replayed on doubt."""
import copy
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
from bridge.api.httpd import GatewayServer
from bridge.app.lifecycle import private_json

from bridge.features.sessions.create import CreationError, DeleteRejected, native_delete_error
from bridge.features.sessions.deletion import DeleteCommands, DeleteNotSent, deletion_scope, delete_native
from bridge.features.sessions.store import SessionStore


class DeletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root, self.child, self.agent, self.other = [str(uuid.uuid4()) for _ in range(4)]
        database = sqlite3.connect(self.home / 'state_5.sqlite')
        database.execute('CREATE TABLE threads(id TEXT, title TEXT, originator TEXT, source TEXT, archived INTEGER, attached_thread_parent_id TEXT, updated_at INTEGER DEFAULT 0)')
        database.executemany('INSERT INTO threads(id,title,originator,source,archived,attached_thread_parent_id) VALUES(?,?,?,?,?,?)', [
            (self.root, 'Root', 'Codex Desktop', 'vscode', 0, None),
            (self.child, 'Archived side chat', 'Codex Desktop', 'vscode', 1, self.root),
            (self.agent, 'Subtask', 'fixture', json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': self.child}}}), 0, None),
            (self.other, 'Unrelated fork', 'Codex Desktop', 'vscode', 0, None)])
        database.commit(); database.close()
        self.store = SessionStore(self.home)

    def test_scope_includes_archived_attached_and_spawned_but_not_unrelated(self):
        scope = deletion_scope(self.store, self.root)
        self.assertEqual([row['id'] for row in scope['sessions']], [self.child, self.root])
        self.assertEqual(scope['scope'], sorted([self.root, self.child, self.agent]))
        self.assertEqual(scope['subagentCount'], 1)
        self.assertEqual(self.store.existing_ids(scope['scope']), set(scope['scope']))

    def runtime(self, snapshot, *, busy=None, error_at=None):
        calls = []
        def request(method, params):
            sid = params['threadId']; calls.append((method, sid))
            if method == 'thread/read':
                return {'thread': {'id': sid, 'status': {'type': 'active' if sid == busy else 'idle'}}}
            if method == 'thread/backgroundTerminals/list': return {'data': []}
            if method == 'thread/delete':
                if sid == error_at: raise DeleteRejected('unsupported')
                return {}
            self.fail(method)
        return request, calls

    def test_native_delete_uses_postorder_after_all_idle_checks(self):
        snapshot = deletion_scope(self.store, self.root)
        request, calls = self.runtime(snapshot)
        delete_native(request, snapshot, lambda: copy.deepcopy(snapshot))
        self.assertEqual([sid for method, sid in calls if method == 'thread/delete'], [self.child, self.root])
        self.assertEqual(calls[-2:], [('thread/delete', self.child), ('thread/delete', self.root)])

    def test_changed_scope_and_running_child_never_delete(self):
        snapshot = deletion_scope(self.store, self.root)
        request, calls = self.runtime(snapshot, busy=self.agent)
        with self.assertRaises(DeleteNotSent): delete_native(request, snapshot, lambda: snapshot)
        self.assertFalse(any(method == 'thread/delete' for method, _ in calls))
        with self.assertRaises(DeleteNotSent): delete_native(request, snapshot, lambda: {})

    def test_receipt_is_written_before_first_mutation_and_lost_response_is_not_replayed(self):
        commands = DeleteCommands(self.home, private_json)
        snapshot = deletion_scope(self.store, self.root); request_id = str(uuid.uuid4())
        def fail():
            self.assertEqual(json.loads(commands.path.read_text())[request_id]['status'], 'unknown')
            raise TimeoutError('lost acknowledgement')
        prepare = Mock(return_value=fail)
        args = (self.root, request_id, snapshot['scope'], lambda: snapshot, self.store.existing_ids, prepare)
        with self.assertRaises(CreationError): commands.perform(*args)
        with self.assertRaisesRegex(ValueError, '不会重复'): commands.perform(*args)
        self.assertEqual(prepare.call_count, 1)

    def test_partial_delete_cannot_confirm_or_replay(self):
        commands = DeleteCommands(self.home, private_json); snapshot = deletion_scope(self.store, self.root)
        remaining = {self.root}; prepare = Mock(return_value=Mock())
        args = (self.root, str(uuid.uuid4()), snapshot['scope'], lambda: snapshot, lambda ids: remaining & set(ids), prepare)
        with self.assertRaises(CreationError): commands.perform(*args)
        with self.assertRaises(ValueError): commands.perform(*args)
        remaining.clear()
        self.assertEqual(commands.perform(*args)['status'], 'confirmed')
        self.assertEqual(prepare.call_count, 1)

    def test_unsupported_before_first_delete_clears_intent_but_partial_rejection_does_not(self):
        snapshot = deletion_scope(self.store, self.root)
        request, _ = self.runtime(snapshot, error_at=self.child)
        with self.assertRaises(DeleteNotSent): delete_native(request, snapshot, lambda: snapshot)
        request, _ = self.runtime(snapshot, error_at=self.root)
        with self.assertRaises(DeleteRejected): delete_native(request, snapshot, lambda: snapshot)
        commands = DeleteCommands(self.home, private_json)
        request_id = str(uuid.uuid4())
        with self.assertRaises(DeleteNotSent):
            commands.perform(self.root, request_id, snapshot['scope'], lambda: snapshot, self.store.existing_ids,
                             lambda _: Mock(side_effect=DeleteNotSent('busy')))
        self.assertEqual(json.loads(commands.path.read_text()), {})

    def test_unknown_requires_fresh_explicit_confirmation_of_exact_receipts(self):
        snapshot = deletion_scope(self.store, self.root)
        commands = DeleteCommands(self.home, private_json)
        first, retry = str(uuid.uuid4()), str(uuid.uuid4())
        prepare = Mock(return_value=Mock(side_effect=TimeoutError('lost')))
        with self.assertRaises(CreationError):
            commands.perform(self.root, first, snapshot['scope'], lambda: snapshot, self.store.existing_ids, prepare)
        preview = commands.preview(lambda: snapshot, self.store.existing_ids)
        self.assertEqual(preview['retry'], [first])
        with self.assertRaises(ValueError):
            commands.perform(self.root, retry, snapshot['scope'], lambda: snapshot, self.store.existing_ids, prepare, retry=[])
        with self.assertRaises(ValueError):
            commands.perform(self.root, retry, snapshot['scope'], lambda: snapshot, self.store.existing_ids, prepare, retry=[str(uuid.uuid4())])
        prepare.return_value = Mock(side_effect=DeleteNotSent('owner still holds it'))
        with self.assertRaises(DeleteNotSent):
            commands.perform(self.root, retry, snapshot['scope'], lambda: snapshot, self.store.existing_ids, prepare, retry=[first])
        self.assertEqual(commands.preview(lambda: snapshot, self.store.existing_ids)['retry'], [first])
        remaining = set(snapshot['scope'])
        prepare.return_value = lambda: remaining.clear()
        result = commands.perform(self.root, retry, snapshot['scope'], lambda: snapshot,
                                  lambda ids: remaining & set(ids), prepare, retry=[first])
        self.assertEqual(result['status'], 'confirmed')
        receipt = json.loads(commands.path.read_text())
        self.assertEqual(receipt[first]['status'], 'superseded')
        self.assertEqual(receipt[first]['supersededBy'], retry)
        self.assertEqual(prepare.call_count, 3)

    def test_unrelated_or_new_unknown_receipt_invalidates_retry_confirmation(self):
        snapshot = deletion_scope(self.store, self.root)
        commands = DeleteCommands(self.home, private_json)
        a,b = str(uuid.uuid4()),str(uuid.uuid4())
        private_json(commands.path, {key:{'id':self.root,'scope':snapshot['scope'],'status':'unknown'} for key in (a,b)})
        prepare = Mock()
        with self.assertRaises(ValueError):
            commands.perform(self.root,str(uuid.uuid4()),snapshot['scope'],lambda:snapshot,self.store.existing_ids,prepare,retry=[a])
        prepare.assert_not_called()

    def test_native_error_classification_never_treats_other_errors_as_not_sent(self):
        busy = {'code': -32600, 'message': f'thread {self.root} already has an active writer'}
        self.assertIsInstance(native_delete_error(busy, self.root), DeleteRejected)
        self.assertNotIsInstance(native_delete_error(busy, self.child), DeleteRejected)
        self.assertNotIsInstance(native_delete_error({'code': -32600, 'message': 'delete failed after cleanup'}, self.root), DeleteRejected)
        self.assertIsInstance(native_delete_error({'code': -32601}, self.root), DeleteRejected)

    def test_retrying_child_does_not_resolve_unknown_parent_siblings(self):
        commands = DeleteCommands(self.home, private_json)
        prior, retry = str(uuid.uuid4()),str(uuid.uuid4())
        scope = deletion_scope(self.store, self.root)['scope']
        private_json(commands.path,{prior:{'id':self.root,'scope':scope,'status':'unknown'}})
        snapshot = deletion_scope(self.store, self.child)
        remaining = set(scope)
        commands.perform(self.child,retry,snapshot['scope'],lambda:snapshot,lambda ids:remaining & set(ids),
                         lambda _:lambda:remaining.difference_update(snapshot['scope']),retry=[prior])
        self.assertEqual(json.loads(commands.path.read_text())[prior]['status'],'unknown')

    def test_confirmation_rejects_new_child_or_wrong_request_identity(self):
        snapshot = deletion_scope(self.store, self.root); prepare = Mock()
        with self.assertRaises(ValueError):
            DeleteCommands(self.home, private_json).perform(self.root, str(uuid.uuid4()), [self.root], lambda: snapshot, self.store.existing_ids, prepare)
        prepare.assert_not_called()

    def test_http_requires_auth_csrf_and_exact_confirmation(self):
        snapshot = deletion_scope(self.store, self.root)
        bridge = SimpleNamespace(deletion=Mock(return_value=snapshot))
        bridge.for_host = lambda host: bridge
        server = GatewayServer(('127.0.0.1', 0), bridge, {'auth': {'mode': 'password'}, 'origins': []},
                               Path(__file__).resolve().parents[1] / 'web')
        origin = 'http://127.0.0.1:' + str(server.server_address[1])
        server.origins.add(origin); server.hosts.add(origin.removeprefix('http://'))
        token, session = server.auth.new_session('127.0.0.1')
        auth = {'Cookie': server.auth.cookie_name + '=' + token, 'X-CSRF-Token': session['csrf']}
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def request(method, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            connection.request(method, '/api/sessions/' + self.root + '/delete', None if body is None else json.dumps(body),
                               {'Origin': origin, 'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse(); status = response.status; response.read(); connection.close()
            return status
        try:
            body = {'id': str(uuid.uuid4()), 'scope': snapshot['scope'], 'confirmed': True}
            self.assertEqual(request('GET'), 401)
            self.assertEqual(request('POST', body, {'Cookie': auth['Cookie']}), 403)
            self.assertEqual(request('POST', {**body, 'confirmed': False}, auth), 400)
            self.assertEqual(request('POST', {**body, 'force': True}, auth), 400)
            bridge.deletion.assert_not_called()
            self.assertEqual(request('GET', headers=auth), 200)
            self.assertEqual(request('POST', body, auth), 200)
            bridge.deletion.assert_called_with(self.root, body['id'], body['scope'])
            retry = [str(uuid.uuid4())]
            self.assertEqual(request('POST', {**body, 'retry': retry}, auth), 200)
            bridge.deletion.assert_called_with(self.root, body['id'], body['scope'], retry=retry)
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
