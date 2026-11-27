import copy
import http.client
import json
import os
import socket
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from bridge.auth import Auth, password_record
from bridge.files import artifact_paths
from bridge.httpd import GatewayServer
from bridge.ipc import DesktopIPC, IPCError
from bridge.model import apply_patches, normalize_state, normalize_request
from bridge.service import Bridge, LiveSession, validate_form
from bridge.catalog import Catalog

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".tmp").mkdir(exist_ok=True)
THREAD = str(uuid.uuid4())


def state():
    return {"id": THREAD, "title": "Desktop test", "modelProvider": "custom-api", "latestModel": "same-model",
            "threadRuntimeStatus": {"type": "idle"}, "turns": [], "requests": []}


class DesktopFixture:
    def __init__(self, root):
        self.root = root
        (root / "ipc").mkdir()
        # macOS limits Unix socket paths; test data is still under project .tmp.
        if os.name == 'nt':
            from pipe_fixture import PipeListener
            self.path = r'\\.\pipe\codex-mobile-test-' + uuid.uuid4().hex
            self.listener = PipeListener(self.path)
        else:
            self.path = str((root / 'ipc/ipc.sock').relative_to(ROOT))
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.listener.bind(self.path)
            self.listener.listen()
            self.listener.settimeout(.2)
        self.state = state()
        self.revision = 1
        self.host = "local"
        self.requests = []
        self.client = None
        self.write_lock = threading.Lock()
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self.serve, daemon=True)
        self.worker.start()

    def send(self, message):
        data = json.dumps(message).encode()
        with self.write_lock:
            self.client.sendall(struct.pack('<I', len(data)) + data)

    def publish(self, change):
        self.send({"type": "broadcast", "sourceClientId": "owner", "method": "thread-stream-state-changed", "version": 11,
                   "params": {"hostId": self.host, "conversationId": THREAD, "change": change}})

    def snapshot(self):
        self.publish({"type": "snapshot", "revision": self.revision, "conversationState": self.state})

    def serve(self):
        while not self.closed.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.client = client
            try:
                while not self.closed.is_set():
                    length = struct.unpack('<I', DesktopIPC._exact(client, 4))[0]
                    message = json.loads(DesktopIPC._exact(client, length))
                    method = message.get('method')
                    if message.get('type') == 'broadcast':
                        if method == 'thread-stream-following-changed' and message['params']['following']:
                            self.snapshot()
                        continue
                    self.requests.append(message)
                    if method == 'thread-follower-update-thread-settings':
                        settings = message['params']['threadSettings']
                        self.state['latestModel'] = settings['model']
                        self.state['latestReasoningEffort'] = settings['effort']
                        self.revision += 1
                        self.snapshot()
                    result = {"applied": True} if method == 'thread-follower-update-thread-settings' else {"clientId": "gateway"} if method == 'initialize' else {"ok": True}
                    self.send({"type": "response", "requestId": message['requestId'], "method": method,
                               "resultType": "success", "handledByClientId": "gateway" if method == 'initialize' else "owner", "result": result})
            except (OSError, EOFError):
                pass
            finally:
                client.close()

    def close(self):
        self.closed.set()
        self.listener.close()
        if self.client:
            try:
                self.client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.worker.join(timeout=2)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(ROOT / '.tmp'), prefix='t')
        self.root = Path(self.temp.name)
        # Path must fit macOS's 104-byte Unix socket limit; bind via relative path.
        self.original_path = self.root
        self.fixture = DesktopFixture(self.root)
        db = sqlite3.connect(str(self.root / 'state_5.sqlite'))
        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT, cwd TEXT, updated_at INT, archived INT, originator TEXT, source TEXT, rollout_path TEXT)')
        db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)', (THREAD, 'Desktop test', '/workspace', 1, 0, 'Codex Desktop', 'vscode', 'unused'))
        db.commit()
        db.close()
        self.bridge = Bridge(self.root, self.root / 'data')
        self.bridge.ipc.path = self.fixture.path

    def tearDown(self):
        self.bridge.close()
        self.fixture.close()
        self.temp.cleanup()

    def test_live_read_send_same_thread_and_idempotence(self):
        view = self.bridge.view(THREAD)
        self.assertTrue(view['connected'])
        self.assertEqual(view['provider'], 'custom-api')
        message_id = str(uuid.uuid4())
        self.bridge.send(THREAD, 'hello', message_id)
        self.bridge.send(THREAD, 'hello', message_id)
        calls = [r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn']
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call['targetClientId'], 'owner')
        self.assertEqual(call['params']['conversationId'], THREAD)
        request = call['params']['turnStart']['request']
        self.assertEqual(request['threadId'], THREAD)
        self.assertNotIn('model', request)
        self.assertNotIn('modelProvider', request)
        self.assertTrue(call['params']['turnStart']['context']['inheritThreadSettings'])

    def test_progressive_read_preserves_metadata_and_pending_requests(self):
        self.fixture.state['turns'] = [{'turnId': 'turn', 'items': [
            {'id': str(i), 'type': 'agentMessage', 'text': 'row '+str(i)} for i in range(150)]}]
        self.fixture.state['requests'] = [{'id': 7, 'method': 'item/commandExecution/requestApproval', 'params': {'command': 'pwd'}}]
        self.bridge.session(THREAD)
        page = self.bridge.timeline_read(THREAD)
        self.assertEqual(len(page['rows']), 20)
        self.assertEqual(page['meta']['provider'], 'custom-api')
        self.assertEqual(page['meta']['requests'][0]['id'], 7)
        self.assertNotIn('turns', page['meta'])
        older = self.bridge.timeline_read(THREAD, limit=80, before=page['before'])
        self.assertEqual(len(older['rows']), 80)
        detail = self.bridge.timeline_read(THREAD, 'detail', cursor=page['before'])
        self.assertEqual(detail['text'], 'row 130')
        changes = self.bridge.timeline_read(THREAD, 'changes', after=page['sequence'], epoch=page['epoch'], start=older['before'])
        self.assertEqual(changes['rows'], [])

    def test_legacy_desktop_threads_visible_but_subagents_excluded(self):
        from bridge.store import SessionStore
        legacy, untagged, child, cli = (str(uuid.uuid4()) for _ in range(4))
        with closing(sqlite3.connect(str(self.root / 'state_5.sqlite'))) as db, db:
            for tid, origin, source in [(legacy, 'codex_work_desktop', 'vscode'),
                                        (untagged, None, 'vscode'),
                                        (child, 'codex_work_desktop', '{"subagent":{}}'),
                                        (cli, 'codex_cli_rs', 'cli')]:
                db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                           (tid, 'Saved chat', '/workspace', 2, 0, origin, source, 'unused'))
        store = SessionStore(self.root)
        self.assertEqual({r['id'] for r in store.list()}, {THREAD, legacy, untagged})
        self.assertEqual(store.get(legacy)['id'], legacy)
        self.assertEqual(store.get(untagged)['id'], untagged)
        for tid in (child, cli):
            with self.assertRaises(KeyError):
                store.get(tid)

    def test_native_free_text_question_null_options_can_be_answered(self):
        self.fixture.state['turns'] = [{'turnId': 'last', 'status': 'completed', 'items': [
            {'id': 'question', 'type': 'agentMessage', 'text': 'Your preference?',
             'questions': [{'title': 'Your preference?', 'options': None}]}]}]
        request = self.bridge.view(THREAD)['requests'][0]
        question = request['params']['questions'][0]
        self.assertEqual(question['options'], [])
        self.bridge.respond(THREAD, request['id'], {'answers': {question['id']: ['Free text']}})
        self.assertEqual(self.fixture.requests[-1]['method'], 'thread-follower-start-turn')

    def test_background_read_shows_history_without_waiting_for_owner(self):
        entered, release = threading.Event(), threading.Event()
        attempts = []
        def slow_owner(*args):
            attempts.append(args)
            entered.set()
            release.wait(2)
            raise IPCError('Desktop read timed out')
        self.bridge.ipc.owner = slow_owner
        history = {**state(), 'title': 'Saved history'}
        self.bridge.store.history = lambda tid: history
        try:
            started = time.monotonic()
            self.bridge.view(THREAD, background=True)
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(entered.wait(1))
            view = self.bridge.view(THREAD, background=True)
            self.assertEqual(view['title'], 'Saved history')
            self.assertTrue(view['connecting'])
            self.assertFalse(view['connected'])
            self.assertEqual(len(attempts), 1)
        finally:
            release.set()
            session = self.bridge.live.get(THREAD)
            if session:
                with session.condition:
                    session.condition.wait_for(lambda: not session.connecting, timeout=3)

    def test_read_timeout_does_not_claim_a_write_was_submitted(self):
        from concurrent.futures import TimeoutError
        from unittest.mock import patch
        ipc = DesktopIPC('unused')
        with patch.object(ipc, '_send'), patch('bridge.ipc.Future.result', side_effect=TimeoutError):
            with self.assertRaisesRegex(IPCError, '桌面读取超时'):
                ipc.request('thread-owner-discovery', {}, timeout=0)
            with self.assertRaisesRegex(IPCError, '操作可能已提交'):
                ipc.request('thread-follower-start-turn', {}, timeout=0)
        self.assertFalse(ipc.pending)

    def test_patch_and_revision_mismatch_resnapshot(self):
        session = self.bridge.session(THREAD)
        self.fixture.publish({"type": "patches", "baseRevision": 1, "revision": 2,
                              "patches": [{"op": "replace", "path": ["title"], "value": "updated"}]})
        with session.condition:
            self.assertTrue(session.condition.wait_for(lambda: session.revision == 2, timeout=2))
        self.assertEqual(session.view()['title'], 'updated')
        self.fixture.publish({"type": "patches", "baseRevision": 999, "revision": 1000, "patches": []})
        with session.condition:
            self.assertTrue(session.condition.wait_for(lambda: not session.connected, timeout=2))
        self.assertTrue(self.bridge.view(THREAD)['connected'])
        self.assertEqual(session.revision, 1)

    def test_stale_and_pending_approval_routing(self):
        self.fixture.state['requests'] = [{"id": 7, "method": "item/commandExecution/requestApproval", "params": {"command": "pwd", "availableDecisions": ['accept', 'decline']}}]
        self.bridge.session(THREAD)
        self.bridge.respond(THREAD, 7, {"decision": "accept"})
        call = self.fixture.requests[-1]
        self.assertEqual(call['method'], 'thread-follower-command-approval-decision')
        self.assertEqual(call['params'], {"conversationId": THREAD, "requestId": 7, "decision": "accept"})
        with self.assertRaises(ValueError):
            self.bridge.respond(THREAD, 9, {"decision": "accept"})
        with self.assertRaises(ValueError):
            self.bridge.respond(THREAD, 7, {"decision": "acceptForSession"})

    def test_user_input_exact_question_ids(self):
        self.fixture.state['requests'] = [{"id": 'q', "method": "item/tool/requestUserInput", "params": {"questions": [{"id": "choice"}]}}]
        self.bridge.session(THREAD)
        self.bridge.respond(THREAD, 'q', {"answers": {"choice": ["continue"]}})
        self.assertEqual(self.fixture.requests[-1]['params']['response']['answers']['choice']['answers'], ['continue'])
        with self.assertRaises(ValueError):
            self.bridge.respond(THREAD, 'q', {"answers": {"unknown": ['continue']}})

    def test_native_async_question_uses_steer_and_exact_reply_id(self):
        self.fixture.state['threadRuntimeStatus'] = {'type': 'active'}
        self.fixture.state['turns'] = [{'turnId': 'active', 'status': 'inProgress', 'items': [
            {'id': 'question', 'type': 'agentMessage', 'text': 'Continue?', 'delivery': 'async', 'questions': [{'title': 'Continue?', 'options': ['Yes', 'No']}]}]}]
        view = self.bridge.view(THREAD)
        pending = view['requests'][0]
        question = pending['params']['questions'][0]
        self.bridge.respond(THREAD, pending['id'], {'answers': {question['id']: ['Yes']}})
        call = self.fixture.requests[-1]
        self.assertEqual(call['method'], 'thread-follower-steer-turn')
        text = call['params']['input'][0]['text']
        self.assertIn('<send_user_message_question_reply>', text)
        payload = json.loads(text.split('\n')[1])
        self.assertEqual(payload[0]['questionItemId'], '["request_user_input_async","question",0]')
        self.assertEqual(payload[0]['answer'], 'Yes')

    def test_completed_async_question_can_resume_same_thread(self):
        self.fixture.state['turns'] = [{'turnId': 'last', 'status': 'completed', 'items': [
            {'id': 'question', 'type': 'agentMessage', 'text': 'Continue?', 'delivery': 'async', 'questions': [{'title': 'Continue?', 'options': []}]}]}]
        pending = self.bridge.view(THREAD)['requests'][0]
        question = pending['params']['questions'][0]
        self.bridge.respond(THREAD, pending['id'], {'answers': {question['id']: ['Yes']}})
        self.assertEqual(self.fixture.requests[-1]['method'], 'thread-follower-start-turn')

    def test_model_settings_use_native_owner_without_turn_or_policy_change(self):
        self.bridge.catalog_reader.get = lambda *a, **k: {'models': [{'id': 'model-b', 'efforts': ['low', 'high']}], 'skills': []}
        result = self.bridge.settings(THREAD, 'model-b', 'high')
        self.assertTrue(result['confirmed'])
        call = self.fixture.requests[-1]
        self.assertEqual(call['method'], 'thread-follower-update-thread-settings')
        self.assertEqual(call['version'], 2)
        self.assertEqual(call['params']['threadSettings'], {'model': 'model-b', 'effort': 'high'})
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))
        with self.assertRaises(ValueError):
            self.bridge.settings(THREAD, 'model-b', 'ultra')

    def test_explicit_skill_input_and_dedup(self):
        path = self.root / 'SKILL.md'
        path.write_text('---\nname: sample\n---\nSample')
        self.bridge.catalog_reader.get = lambda *a, **k: {'models': [], 'skills': [{'id': 'sample-id', 'name': 'sample', 'path': str(path)}]}
        message_id = str(uuid.uuid4())
        self.bridge.send(THREAD, 'Use this skill', message_id, skills=['sample-id'])
        request = self.fixture.requests[-1]['params']['turnStart']['request']
        self.assertEqual(request['input'][1], {'type': 'skill', 'name': 'sample', 'path': str(path)})
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'Use this skill', message_id, skills=[])
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'Use this skill', str(uuid.uuid4()), skills=['/etc/passwd'])

    def test_remote_host_routes_send_settings_and_approval_to_mac_owner(self):
        host = 'remote-ssh-discovered:fixture'
        self.bridge.host = self.fixture.host = host
        self.bridge.catalog_reader.get = lambda *a, **k: {'models': [], 'skills': []}
        self.fixture.state['requests'] = [{'id': 7, 'method': 'item/commandExecution/requestApproval', 'params': {}}]
        self.assertTrue(self.bridge.view(THREAD)['connected'])
        self.bridge.send(THREAD, 'remote', str(uuid.uuid4()))
        call = self.fixture.requests[-1]
        self.assertEqual(call['hostId'], host)
        self.assertEqual(call['version'], 3)
        self.bridge.settings(THREAD, 'remote-model', 'high')
        self.assertEqual(self.fixture.requests[-1]['version'], 3)
        self.bridge.respond(THREAD, 7, {'decision': 'decline'})
        self.assertEqual(self.fixture.requests[-1]['hostId'], host)
        self.assertEqual(self.fixture.requests[-1]['version'], 2)
        self.assertEqual(self.bridge.view(THREAD)['files'], [])
        session = self.bridge.session(THREAD)
        self.bridge._event({'method': 'thread-stream-state-changed', 'version': 11, 'sourceClientId': 'owner',
                            'params': {'hostId': 'local', 'conversationId': THREAD, 'change': {'type': 'snapshot', 'revision': 90, 'conversationState': state()}}})
        self.assertNotEqual(session.revision, 90)

    def test_project_grouping_recency_and_host_identity(self):
        from bridge.remote import AppHosts
        hosts = AppHosts(self.root)
        hosts.state = lambda: {'remote-projects': [{'id': 'p', 'hostId': 'remote', 'label': 'Work', 'remotePath': '/work'}]}
        rows = hosts.decorate([{'id': THREAD, 'cwd': '/work/sub', 'recency_at': 5, 'updated_at': 20}], 'remote', 'Server')
        self.assertEqual(rows[0]['recency'], 5000)
        self.assertEqual(rows[0]['projectKey'], 'remote|p')
        self.assertEqual(rows[0]['projectName'], 'Work')
        with self.assertRaises(KeyError):
            self.bridge.for_host('unconfigured-host')

    def test_remote_sources_merge_before_pagination_and_surface_failures(self):
        from bridge.remote import RemoteUnavailable
        from unittest.mock import Mock
        remote = Mock()
        remote.host = 'remote'
        remote.lock = threading.RLock()
        remote.live = {}
        remote.store.list.return_value = [{'id': 'remote-thread', 'cwd': '/work', 'updated_at': 50}]
        self.bridge.hosts.hosts = lambda: {'remote': {'alias': 'server'}}
        self.bridge.for_host = lambda host: remote
        rows = self.bridge.list(limit=1)
        self.assertEqual(rows[0]['id'], 'remote-thread')
        self.assertEqual(self.bridge.list(limit=1, offset=1)[0]['id'], THREAD)
        remote.store.list.side_effect = RemoteUnavailable('offline')
        self.assertEqual(self.bridge.list()[0]['id'], THREAD)
        self.assertEqual(self.bridge.host_errors[0]['host'], 'remote')

    def test_queue_cancel_and_dispatch(self):
        self.fixture.state['threadRuntimeStatus'] = {"type": "active"}
        session = self.bridge.session(THREAD)
        first = str(uuid.uuid4())
        self.assertEqual(self.bridge.send(THREAD, 'later', first, 'queue')['status'], 'queued')
        self.bridge.cancel_queued(THREAD, first)
        self.assertEqual(len([r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn']), 0)
        second = str(uuid.uuid4())
        self.bridge.send(THREAD, 'next', second, 'queue')
        with session.condition:
            session.state['threadRuntimeStatus'] = {"type": "idle"}
        key = THREAD + ':' + second
        self.bridge._send_queued(session, key, self.bridge.submissions[key])
        self.assertEqual(self.bridge.submissions[key]['status'], 'accepted')

    def test_unknown_delivery_not_replayed_even_after_idle_changes(self):
        self.bridge.session(THREAD)
        self.bridge._call = lambda *a, **k: (_ for _ in ()).throw(IPCError('timeout'))
        message_id = str(uuid.uuid4())
        with self.assertRaises(IPCError):
            self.bridge.send(THREAD, 'maybe delivered', message_id)
        self.assertEqual(self.bridge.send(THREAD, 'maybe delivered', message_id)['status'], 'unknown')
        saved = json.loads((self.root / 'data/submissions.json').read_text(encoding='utf-8'))
        self.assertEqual(saved[THREAD + ':' + message_id]['status'], 'unknown')


class ModelTests(unittest.TestCase):
    def test_canonical_history(self):
        value = state()
        value['turnHistory'] = {'kind': 'canonical', 'history': {'isComplete': True, 'entitiesByKey': {
            'k': {'turnId': 'turn', 'params': {'input': [{'type': 'text', 'text': 'hello'}]}, 'items': [{'type': 'agentMessage', 'text': 'world'}]}},
            'islands': [{'entries': [{'value': 'k'}]}]}}
        messages = normalize_state(value)['turns'][0]['messages']
        self.assertEqual([x['text'] for x in messages], ['hello', 'world'])

    def test_array_patch(self):
        value = {'items': [1, 2]}
        apply_patches(value, [{'op': 'add', 'path': ['items', 1], 'value': 3}, {'op': 'remove', 'path': '/items/0'}])
        self.assertEqual(value, {'items': [3, 2]})

    def test_identity_verification_not_exposed(self):
        req = normalize_request({'id': 'x', 'method': 'mcpServer/elicitation/request', 'params': {'_meta': {'openai/userVerification': {'token': 'secret'}}}})
        self.assertFalse(req['supported'])
        self.assertNotIn('secret', json.dumps(req))

    def test_artifacts_only_referenced_workspace_files(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / '.tmp')) as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            workspace.mkdir()
            allowed = workspace / 'report.txt'
            allowed.write_text('report')
            outside = root / 'private.txt'
            outside.write_text('private')
            value = state()
            value['cwd'] = str(workspace)
            value['turns'] = [{'items': [{'type': 'agentMessage', 'text': f'[report]({allowed}) [private]({outside})'}]}]
            files = artifact_paths(value, root / '.codex')
            self.assertEqual([v['name'] for v in files.values()], ['report.txt'])

    def test_artifact_symlink_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / '.tmp')) as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            workspace.mkdir()
            outside = root / 'private.txt'
            outside.write_text('private', encoding='utf-8')
            link = workspace / 'outside.txt'
            try:
                link.symlink_to(outside)
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege is not enabled')
                raise
            value = {**state(), 'cwd': str(workspace), 'turns': [
                {'items': [{'type': 'agentMessage', 'text': f'[link]({link})'}]}]}
            self.assertEqual(artifact_paths(value, root / '.codex'), {})

    def test_file_approval_includes_actual_pending_diff(self):
        value = state()
        value['turns'] = [{'items': [{'id': 'patch', 'type': 'fileChange', 'changes': [{'path': 'a.py', 'diff': '-old\n+new'}]}]}]
        value['requests'] = [{'id': 1, 'method': 'item/fileChange/requestApproval', 'params': {'itemId': 'patch'}}]
        request = normalize_state(value)['requests'][0]
        self.assertEqual(request['params']['changes'][0]['diff'], '-old\n+new')

    def test_mcp_form_validation(self):
        schema = {'type': 'object', 'properties': {'age': {'type': 'integer', 'minimum': 0}}, 'required': ['age']}
        validate_form({'age': 12}, schema)
        for value in [{}, {'age': True}, {'age': -1}, {'age': 1.5}]:
            with self.assertRaises(ValueError):
                validate_form(value, schema)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = password_record('correct password test')

    def setUp(self):
        class EmptyBridge:
            host_errors = []
            def for_host(self, host):
                if host != "local":
                    raise KeyError(host)
                return self
            closed = threading.Event()
            def list(self, **kwargs):
                return []
        config = {'auth': {'mode': 'password', 'username': 'admin', **self.record}, 'origins': []}
        self.server = GatewayServer(('127.0.0.1', 0), EmptyBridge(), config, ROOT / 'web')
        self.port = self.server.server_address[1]
        self.origin = 'http://127.0.0.1:' + str(self.port)
        self.server.origins.add(self.origin)
        self.server.hosts.add('127.0.0.1:' + str(self.port))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        base = {'Origin': self.origin, 'Content-Type': 'application/json'}
        base.update(headers or {})
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=base)
        response = conn.getresponse()
        status, response_headers, data = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return status, response_headers, json.loads(data)

    def login(self):
        status, headers, body = self.request('POST', '/api/login', {'username': 'admin', 'password': 'correct password test'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        return {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': body['csrf']}

    def test_page_scripts_are_served_with_same_origin_csp(self):
        import re
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request('GET', '/')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            scripts = re.findall(r'<script src="([^"]+)"', response.read().decode())
            self.assertEqual(scripts, ['/vendor/markdown-it.min.js', '/vendor/katex/katex.min.js',
                                       '/vendor/texmath.js', '/markdown.js', '/timeline.js', '/app.js'])
            for script in scripts:
                conn.request('GET', script)
                response = conn.getresponse()
                self.assertEqual(response.status, 200, script)
                self.assertIn('text/javascript', response.getheader('Content-Type'))
                self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
                self.assertTrue(response.read(), script)
        finally:
            conn.close()

    def test_progressive_routes_require_auth_and_forward_page_and_detail_options(self):
        calls = []
        def timeline_read(thread_id, mode='page', **options):
            calls.append((thread_id, mode, options))
            return {'rows': []}
        self.server.bridge.timeline_read = timeline_read
        path = '/api/sessions/' + THREAD + '/timeline?limit=20&before=epoch.key'
        self.assertEqual(self.request('GET', path)[0], 401)
        self.assertEqual(calls, [])
        auth = self.login()
        self.assertEqual(self.request('GET', path, headers=auth)[0], 200)
        self.assertEqual(calls[-1], (THREAD, 'page', {'limit': 20, 'before': 'epoch.key'}))
        path = '/api/sessions/' + THREAD + '/detail?key=epoch.key&offset=16000&version=v'
        self.assertEqual(self.request('GET', path, headers=auth)[0], 200)
        self.assertEqual(calls[-1][1:], ('detail', {'cursor': 'epoch.key', 'offset': 16000, 'version': 'v'}))

    def test_formula_css_fonts_and_path_boundary(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            for path, mime in [('/vendor/katex/katex.min.css', 'text/css'),
                               ('/vendor/katex/fonts/KaTeX_Main-Regular.woff2', 'font/woff2')]:
                conn.request('GET', path)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(mime, response.getheader('Content-Type'))
                self.assertTrue(response.read())
            conn.request('GET', '/vendor/katex/fonts/../../../../.local/config.json')
            response = conn.getresponse()
            self.assertNotEqual(response.status, 200)
            response.read()
        finally:
            conn.close()

    def test_startup_does_not_require_reverse_dns(self):
        from unittest.mock import patch
        config = {'auth': {'mode': 'none'}, 'origins': []}
        with patch('socket.getfqdn', side_effect=AssertionError('Reverse DNS must not block startup')):
            server = GatewayServer(('127.0.0.1', 0), self.server.bridge, config, ROOT / 'web')
        try:
            self.assertEqual(server.server_name, '127.0.0.1')
            self.assertEqual(server.server_port, server.server_address[1])
        finally:
            server.server_close()

    def test_large_session_response_compression_and_negotiation(self):
        import gzip
        view = {'id': THREAD, 'turns': [{'messages': [{'text': '公式与历史内容 ' * 50000}]}]}
        self.server.bridge.view = lambda *args, **kwargs: view
        auth = self.login()
        for encoding, compressed in [('gzip, deflate, br', True), ('gzip;q=0', False), ('identity', False)]:
            with self.subTest(encoding=encoding):
                conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
                try:
                    conn.request('GET', '/api/sessions/'+THREAD, headers={**auth, 'Accept-Encoding': encoding})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    body = response.read()
                    self.assertEqual(len(body), int(response.getheader('Content-Length')))
                    if compressed:
                        self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
                        self.assertIn('Accept-Encoding', response.getheader('Vary'))
                        self.assertLess(len(body), 10000)
                        body = gzip.decompress(body)
                    else:
                        self.assertIsNone(response.getheader('Content-Encoding'))
                    self.assertEqual(json.loads(body), view)
                finally:
                    conn.close()

    def test_auth_csrf_origin_host_and_logout(self):
        self.assertEqual(self.request('GET', '/api/sessions')[0], 401)
        self.assertEqual(self.request('GET', '/api/sessions/'+THREAD+'/events')[0], 401)
        auth = self.login()
        self.assertEqual(self.request('GET', '/api/sessions', headers=auth)[0], 200)
        self.assertEqual(self.request('POST', '/api/logout', {}, {**auth, 'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request('POST', '/api/logout', {}, {**auth, 'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/sessions', headers={**auth, 'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/logout', {}, auth)[0], 200)
        self.assertEqual(self.request('GET', '/api/sessions', headers=auth)[0], 401)

    def test_wrong_password_and_explicit_passwordless(self):
        self.assertEqual(self.request('POST', '/api/login', {'username': 'admin', 'password': 'wrong'})[0], 403)
        self.server.auth = Auth({'mode': 'none'})
        self.assertEqual(self.request('GET', '/api/sessions')[0], 401)
        status, _, body = self.request('POST', '/api/login', {})
        self.assertEqual(status, 200)
        self.assertTrue(body['csrf'])

    def test_external_origin_has_secure_cookie_and_poll_transport(self):
        host = 'test-gateway.trycloudflare.com'
        origin = 'https://' + host
        self.server.origins.add(origin)
        self.server.hosts.add(host)
        self.server.secure_hosts.add(host)
        headers = {'Host': host, 'Origin': origin}
        status, _, body = self.request('GET', '/api/auth', headers=headers)
        self.assertEqual(body['transport'], 'poll')
        status, response_headers, body = self.request('POST', '/api/login', {'username': 'admin', 'password': 'correct password test'}, headers)
        self.assertEqual(status, 200)
        self.assertIn('; Secure', response_headers['Set-Cookie'])

    def test_remember_login_cookie_and_option_validation(self):
        status, headers, body = self.request('POST', '/api/login',
            {'username': 'admin', 'password': 'correct password test', 'remember': True})
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=2592000', headers['Set-Cookie'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertTrue(body['csrf'])
        status, _, _ = self.request('POST', '/api/login',
            {'username': 'admin', 'password': 'correct password test', 'remember': 'true'})
        self.assertEqual(status, 400)

    def test_poll_is_authenticated_and_returns_current_state(self):
        session = LiveSession(THREAD)
        session.state = state()
        session.connected = True
        self.server.bridge.session = lambda *a, **k: session
        self.server.bridge.view = lambda *a, **k: session.view()
        self.assertEqual(self.request('GET', '/api/sessions/'+THREAD+'/poll?after=-1')[0], 401)
        auth = self.login()
        status, _, value = self.request('GET', '/api/sessions/'+THREAD+'/poll?after=-1', headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(value['state']['id'], THREAD)
        self.assertEqual(session.viewers, 0)

    def test_api_cannot_execute_arbitrary_rpc_or_read_files(self):
        auth = self.login()
        self.assertEqual(self.request('POST', '/api/rpc', {'method': 'arbitrary'}, auth)[0], 404)
        self.assertEqual(self.request('GET', '/.local/config.json', headers=auth)[0], 404)


if __name__ == '__main__':
    unittest.main()
