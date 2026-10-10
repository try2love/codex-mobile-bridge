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
from pathlib import Path, PureWindowsPath

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
        self.loaded = True
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
                        if method == 'thread-stream-following-changed' and message['params']['following'] and self.loaded:
                            self.snapshot()
                        continue
                    self.requests.append(message)
                    if method == 'thread-follower-update-thread-settings':
                        settings = message['params']['threadSettings']
                        if 'model' in settings: self.state['latestModel'] = settings['model']
                        if 'effort' in settings: self.state['latestReasoningEffort'] = settings['effort']
                        self.state['latestThreadSettings'] = {**self.state.get('latestThreadSettings', {}), **settings}
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

    def test_external_provider_change_does_not_use_stale_managed_account_to_block_send(self):
        from bridge.accounts import Accounts
        manager = Accounts(self.bridge)
        self.bridge.accounts = manager
        config = self.root / 'config.toml'
        config.write_text('model_provider="bridge_api"\n')
        manager.index['accounts'] = [{'id': 'a' * 32, 'kind': 'api'}]
        manager.mark_active('a' * 32)
        config.write_text('model_provider="custom-api"\n')
        self.assertFalse(manager.active_matches())
        self.bridge.send(THREAD, 'hello from the existing provider', str(uuid.uuid4()))
        calls = [r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['targetClientId'], 'owner')
        self.assertNotIn('modelProvider', calls[0]['params']['turnStart']['request'])
        self.assertEqual(self.bridge.view(THREAD)['provider'], 'custom-api')

    def test_local_database_failure_keeps_other_hosts_in_list(self):
        from types import SimpleNamespace
        from bridge.store import StoreUnavailable
        def unavailable(**kwargs):
            raise StoreUnavailable('Database temporarily unavailable')
        self.bridge.store.list = unavailable
        remote = SimpleNamespace(host='remote:test', lock=threading.RLock(), live={}, listed=set(),
                                 store=SimpleNamespace(list=lambda **kw: [{'id': THREAD, 'title': 'Remote chat', 'cwd': '/remote', 'updated_at': 1}]))
        self.bridge.hosts.hosts = lambda: {'remote:test': {'alias': 'test'}}
        self.bridge.for_host = lambda host: remote
        rows = self.bridge.list()
        self.assertEqual(rows[0]['host'], 'remote:test')
        self.assertEqual(self.bridge.host_errors, [{'host': 'local', 'label': '此电脑', 'error': 'Database temporarily unavailable'}])

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
        legacy, untagged, child, cli, mobile = (str(uuid.uuid4()) for _ in range(5))
        with closing(sqlite3.connect(str(self.root / 'state_5.sqlite'))) as db, db:
            for tid, origin, source in [(legacy, 'codex_work_desktop', 'vscode'),
                                        (untagged, None, 'vscode'),
                                        (child, 'codex_work_desktop', '{"subagent":{}}'),
                                        (cli, 'codex_cli_rs', 'cli'),
                                        (mobile, 'codex_mobile_bridge', 'vscode')]:
                db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                           (tid, 'Saved chat', '/workspace', 2, 0, origin, source, 'unused'))
        store = SessionStore(self.root)
        self.assertEqual({r['id'] for r in store.list()}, {THREAD, legacy, untagged, mobile})
        self.assertEqual(store.get(legacy)['id'], legacy)
        self.assertEqual(store.get(untagged)['id'], untagged)
        self.assertEqual(store.get(mobile)['id'], mobile)
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
        def slow_attach(*args):
            attempts.append(args)
            entered.set()
            release.wait(2)
        self.bridge._attach = slow_attach
        history = {**state(), 'title': 'Saved history'}
        self.bridge.store.history = lambda tid, **kwargs: history
        try:
            started = time.monotonic()
            self.bridge.view(THREAD, background=True)
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(entered.wait(1))
            session = self.bridge.live[THREAD]
            with session.condition:
                self.assertTrue(session.condition.wait_for(lambda: session.saved_view is not None, timeout=1))
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

    def test_fast_owner_snapshot_does_not_skip_saved_history(self):
        from unittest.mock import patch, Mock
        session = LiveSession(THREAD)
        saved = {**state(), 'turns': [{'turnId':'old','status':'completed','items':[]}]}
        class InlineThread:
            def __init__(self, target, args=(), **kwargs):self.target,self.args=target,args
            def start(self):self.target(*self.args)
            def join(self):pass
        def attach(target):
            target.state = {**state(), 'turnsPagination': {'hasLoadedOldest':False}}
            target.connected = True
        with patch('bridge.service.threading.Thread', InlineThread), patch.object(self.bridge, '_attach', side_effect=attach), patch.object(self.bridge.store, 'history', return_value=saved) as read:
            self.bridge._refresh_async(session)
        read.assert_called_once_with(THREAD, turn_limit=20)
        self.assertEqual(session.view()['turns'][0]['id'], 'old')
        self.assertTrue(session.connected)

    def test_cold_read_never_opens_desktop_and_late_snapshot_can_connect(self):
        from unittest.mock import patch
        self.fixture.loaded = False
        self.bridge.store.history = lambda tid, **kwargs: {**state(), 'title': 'Saved history'}
        with patch('bridge.service.open_in_desktop') as opened:
            session = self.bridge.session(THREAD, background=True)
            with session.condition:
                self.assertTrue(session.condition.wait_for(lambda: not session.connecting, timeout=3))
            self.assertEqual(session.view()['title'], 'Saved history')
            self.assertFalse(session.connected)
            self.assertIsNone(session.error)
            opened.assert_not_called()
            self.fixture.snapshot()
            with session.condition:
                self.assertTrue(session.condition.wait_for(lambda: session.connected, timeout=2))

    def test_explicit_activation_recovers_cold_chat_without_a_model_request(self):
        from unittest.mock import patch
        self.fixture.loaded = False
        self.bridge.store.history = lambda tid, **kwargs: state()
        with patch('bridge.service.open_in_desktop', side_effect=lambda *args: setattr(self.fixture, 'loaded', True)) as opened:
            session = self.bridge.activate(THREAD)
            self.assertTrue(session.connected)
            opened.assert_called_once_with(THREAD, 'local')
            self.bridge.activate(THREAD)
            self.assertEqual(opened.call_count, 1)
        self.assertFalse(any(r['method'].startswith('thread-follower-') for r in self.fixture.requests))

    def test_existing_owner_needs_no_desktop_navigation(self):
        from unittest.mock import patch
        with patch('bridge.service.open_in_desktop') as opened:
            self.assertTrue(self.bridge.activate(THREAD).connected)
            opened.assert_not_called()

    def test_cold_send_inherits_original_provider_after_activation(self):
        from unittest.mock import patch
        self.fixture.loaded = False
        self.bridge.host = self.fixture.host = 'remote:test'
        self.bridge.store.history = lambda tid, **kwargs: state()
        with patch('bridge.service.open_in_desktop', side_effect=lambda *args: setattr(self.fixture, 'loaded', True)) as opened:
            self.bridge.send(THREAD, 'hello', str(uuid.uuid4()))
            opened.assert_called_once_with(THREAD, 'remote:test')
        call = next(r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn')
        self.assertEqual(call['hostId'], 'remote:test')
        self.assertEqual(call['targetClientId'], 'owner')
        self.assertTrue(call['params']['turnStart']['context']['inheritThreadSettings'])
        self.assertNotIn('model', call['params']['turnStart']['request'])

    def test_failed_activation_never_sends_or_marks_message_submitted(self):
        from unittest.mock import patch
        self.fixture.loaded = False
        self.bridge.store.history = lambda tid, **kwargs: state()
        with patch('bridge.service.open_in_desktop', side_effect=OSError('handler unavailable')):
            with self.assertRaisesRegex(IPCError, '未发送'):
                self.bridge.send(THREAD, 'hello', str(uuid.uuid4()))
        self.assertFalse(self.bridge.submissions)
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))

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

    def test_computer_use_persistence_is_validated_and_forwarded(self):
        params = {'serverName':'codex_apps','mode':'form','message':'Use fixture app',
                  'requestedSchema':{'type':'object','properties':{}},
                  '_meta':{'codex_approval_kind':'mcp_tool_call','connector_id':'computer-use',
                           'tool_params':{'app':'Fixture App'},'persist':['session','always'], 'private':'omit'}}
        self.fixture.state['requests'] = [{'id':'computer','method':'mcpServer/elicitation/request','params':params}]
        view = self.bridge.view(THREAD)
        self.assertEqual(view['requests'][0]['params']['computerUse'], {'app':'Fixture App','persistModes':['session','always']})
        self.assertNotIn('private', json.dumps(view['requests']))
        for mode in ('session','always'):
            self.bridge.respond(THREAD,'computer',{'action':'accept','persist':mode})
            reply = self.fixture.requests[-1]['params']['response']
            self.assertEqual(reply, {'action':'accept','content':{},'_meta':{'persist':mode}})
        self.bridge.respond(THREAD,'computer',{'action':'decline'})
        self.assertEqual(self.fixture.requests[-1]['params']['response'], {'action':'decline','content':None})
        for response in ({'action':'accept','persist':'forever'}, {'action':'decline','persist':'always'},
                         {'action':'accept','_meta':{'persist':'always'}}):
            with self.assertRaises(ValueError): self.bridge.respond(THREAD,'computer',response)

    def test_computer_scope_cannot_be_added_to_other_or_session_only_requests(self):
        params = {'mode':'form','requestedSchema':{'type':'object','properties':{}},
                  '_meta':{'codex_approval_kind':'mcp_tool_call','connector_id':'computer-use',
                           'tool_params':{'app':'Fixture'},'persist':['session']}}
        self.fixture.state['requests']=[{'id':'computer','method':'mcpServer/elicitation/request','params':params}]
        self.bridge.view(THREAD)
        with self.assertRaises(ValueError):self.bridge.respond(THREAD,'computer',{'action':'accept','persist':'always'})
        self.bridge.respond(THREAD,'computer',{'action':'accept','persist':'session'})
        session=self.bridge.session(THREAD)
        with session.condition:
            session.state['requests'][0]['params']['_meta']['connector_id']='unrelated'
        with self.assertRaises(ValueError):self.bridge.respond(THREAD,'computer',{'action':'accept','persist':'session'})
        self.bridge.respond(THREAD,'computer',{'action':'accept','content':{}})
        self.assertNotIn('_meta',self.fixture.requests[-1]['params']['response'])
        with session.condition:session.state['requests']=[]
        with self.assertRaisesRegex(ValueError,'已处理或已过期'):
            self.bridge.respond(THREAD,'computer',{'action':'accept','persist':'always'})

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
        self.bridge.catalog_reader.get_kind = lambda *a, **k: {'models': [{'id': 'model-b', 'efforts': ['low', 'high']}], 'skills': []}
        result = self.bridge.settings(THREAD, 'model-b', 'high')
        self.assertTrue(result['confirmed'])
        call = self.fixture.requests[-1]
        self.assertEqual(call['method'], 'thread-follower-update-thread-settings')
        self.assertEqual(call['version'], 2)
        self.assertEqual(call['params']['threadSettings'], {'model': 'model-b', 'effort': 'high'})
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))
        with self.assertRaises(ValueError):
            self.bridge.settings(THREAD, 'model-b', 'ultra')

    def test_official_model_without_capabilities_cannot_save_guessed_effort(self):
        self.bridge.catalog_reader.get_kind = lambda *a, **k: {'models': [{'id':'official-missing','efforts':[]}], 'modelSource':'codex'}
        with self.assertRaisesRegex(ValueError, '推理能力'):
            self.bridge.settings(THREAD, 'official-missing', 'minimal')
        self.assertFalse(any(r['method'] == 'thread-follower-update-thread-settings' for r in self.fixture.requests))

    def test_managed_catalog_uses_selected_access_instead_of_old_chat_provider(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        selected={'models':[{'id':'gemini-fixture','efforts':[]}],'modelSource':'api','modelAccessName':'Gemini access'}
        self.bridge.accounts=SimpleNamespace(current_models=Mock(return_value=selected))
        self.bridge.catalog_reader.get=Mock(side_effect=AssertionError('must not query old provider models'))
        self.bridge.catalog_reader.get_kind=Mock(return_value={'skills':[],'errors':[]})
        for kind in ('models',None):
            result=self.bridge.catalog(THREAD,refresh=True,kind=kind)
            self.assertEqual(result['models'],selected['models'])
            self.assertEqual(result['modelAccessName'],'Gemini access')
        self.assertEqual([c.args[0] for c in self.bridge.catalog_reader.get_kind.call_args_list],['skills'])
        self.bridge.accounts=None

    def test_upstream_model_without_effort_metadata_uses_original_provider_and_owner(self):
        reads = []
        def catalog(kind, cwd, **kwargs):
            reads.append((cwd, kwargs))
            return {'models': [{'id': 'gemini-fixture', 'efforts': []}], 'skills': [], 'modelSource': 'api'}
        self.bridge.catalog_reader.get_kind = catalog
        before = self.bridge.view(THREAD)
        result = self.bridge.settings(THREAD, 'gemini-fixture', 'high')
        self.assertTrue(result['confirmed'])
        self.assertEqual(reads, [(before['cwd'], {'refresh': False, 'provider': 'custom-api'})])
        call = self.fixture.requests[-1]
        self.assertEqual(call['method'], 'thread-follower-update-thread-settings')
        self.assertEqual(call['params']['threadSettings'], {'model': 'gemini-fixture', 'effort': 'high'})
        after = self.bridge.view(THREAD)
        for key in ('provider', 'cwd', 'owner'):
            self.assertEqual(after.get(key), before.get(key))
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))

    def test_explicit_skill_input_and_dedup(self):
        path = self.root / 'SKILL.md'
        path.write_text('---\nname: sample\n---\nSample')
        skill = {'id': 'sample-id', 'name': 'sample', 'path': str(path)}
        self.bridge.catalog_reader.get_kind = lambda kind, *a, **k: {'skills': [skill]}
        self.bridge.catalog_reader.validate_skills = lambda cwd, ids, refresh=False: ([skill] if set(ids) == {'sample-id'} else [])
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
        self.bridge.catalog_reader.get = lambda *a, **k: {'models': [], 'skills': [], 'fastMode': {'allowed': False}}
        self.bridge.catalog_reader.get_kind = lambda kind, *a, **k: {kind: []}
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

    def test_ignore_unknown_is_durable_and_never_replays_or_cancels_other_records(self):
        message_id = str(uuid.uuid4())
        key = THREAD + ':' + message_id
        self.bridge.submissions[key] = {'text': 'uncertain', 'mode': 'send', 'status': 'unknown', 'at': 1}
        queued = str(uuid.uuid4())
        self.bridge.submissions[THREAD + ':' + queued] = {'text': 'queued', 'mode': 'queue', 'status': 'queued', 'at': 2}
        self.assertEqual(self.bridge.ignore_submission(THREAD, message_id)['status'], 'ignored')
        self.assertEqual(self.bridge.ignore_submission(THREAD, message_id)['status'], 'ignored')
        self.assertFalse(self.fixture.requests)
        saved = json.loads(self.bridge.ledger_path.read_text())
        self.assertEqual(saved[key]['status'], 'ignored')
        with self.assertRaises(ValueError): self.bridge.ignore_submission(THREAD, queued)
        with self.assertRaises(ValueError): self.bridge.ignore_submission(str(uuid.uuid4()), message_id)
        self.assertEqual(self.bridge.send(THREAD, 'uncertain', message_id)['status'], 'ignored')
        self.assertFalse(any(r['method'] == 'thread-follower-start-turn' for r in self.fixture.requests))

    def test_account_switch_keeps_old_custom_chat_on_its_desktop_owner(self):
        from bridge.accounts import Accounts
        manager = Accounts(self.bridge)
        self.bridge.accounts = manager
        (self.root / 'config.toml').write_text('model_provider="bridge_api"\n')
        manager.index['accounts'] = [{'id': 'a' * 32, 'kind': 'api'}]
        manager.mark_active('a' * 32)
        self.bridge.send(THREAD, 'continue old chat', str(uuid.uuid4()))
        call = next(r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn')
        self.assertEqual(call['targetClientId'], 'owner')
        self.assertTrue(call['params']['turnStart']['context']['inheritThreadSettings'])
        self.assertNotIn('modelProvider', call['params']['turnStart']['request'])
        self.assertEqual(self.fixture.state['modelProvider'], 'custom-api')



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

    @unittest.skipUnless(os.name == 'nt', 'Windows drive path syntax')
    def test_reference_path_accepts_windows_drive_paths(self):
        from bridge.files import reference_path
        for reference in ('D:\\workspace\\report.txt', 'D:/workspace/report.txt',
                          '/D:/workspace/report.txt', '/D:\\workspace\\report.txt',
                          'file:///D:/workspace/report.txt'):
            with self.subTest(reference=reference):
                path = reference_path(reference)
                self.assertTrue(path.is_absolute())
                self.assertEqual(path, PureWindowsPath('D:/workspace/report.txt'))

    @unittest.skipUnless(os.name == 'nt', 'Windows drive path syntax')
    def test_slash_prefixed_windows_artifacts_remain_in_workspace(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / '.tmp')) as directory:
            root = Path(directory)
            workspace = root / 'workspace';workspace.mkdir()
            outside = root / 'private.md';outside.write_text('private', encoding='utf-8')
            for name in ('report.md', 'app.apk'):
                allowed = workspace / name;allowed.write_bytes(b'test')
                reference = '/' + allowed.as_posix()
                escape = '/' + workspace.as_posix() + '/../private.md'
                value = {**state(), 'cwd': str(workspace), 'turns': [{'items': [
                    {'type': 'agentMessage', 'text': f'[file]({reference}) [private](/{outside.as_posix()}) [escape]({escape})'},
                ]}]}
                with self.subTest(name=name):
                    files = artifact_paths(value, root / '.codex')
                    self.assertEqual(len(files), 1)
                    artifact = next(iter(files.values()))
                    self.assertEqual(artifact['path'], allowed.resolve())
                    self.assertEqual(artifact['reference'], reference)

    @unittest.skipIf(os.name == 'nt', 'POSIX path syntax')
    def test_slash_prefixed_drive_is_preserved_on_posix(self):
        from bridge.files import reference_path
        self.assertEqual(reference_path('/D:/workspace/report.md'), Path('/D:/workspace/report.md'))

    @unittest.skipUnless(os.name == 'nt', 'Windows drive path syntax')
    def test_windows_drive_markdown_reference_remains_in_workspace(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / '.tmp')) as directory:
            root = Path(directory)
            workspace = root / 'workspace';workspace.mkdir()
            allowed = workspace / 'report.txt';allowed.write_text('report', encoding='utf-8')
            outside = root / 'private.txt';outside.write_text('private', encoding='utf-8')
            value = state();value['cwd'] = str(workspace)
            value['turns'] = [{'items': [{'type': 'agentMessage', 'text': f'[report]({allowed}) [private]({outside})'}]}]
            files = artifact_paths(value, root / '.codex')
            self.assertEqual([item['name'] for item in files.values()], ['report.txt'])

    def test_file_uri_image_view_links_plain_markdown_reference(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / '.tmp')) as directory:
            root = Path(directory)
            image = root / 'preview.png';image.write_bytes(b'PNG')
            value = {**state(), 'cwd': str(root), 'turns': [{'items': [
                {'type': 'ImageView', 'path': image.as_uri()},
                {'type': 'agentMessage', 'text': f'![preview]({image})'},
            ]}]}
            files = artifact_paths(value, root / '.codex')
            self.assertEqual(len(files), 1)
            self.assertEqual(next(iter(files.values()))['reference'], str(image))
            self.assertEqual(set(next(iter(files.values()))['references']), {str(image), image.as_uri()})

    def test_artifact_retains_all_references_to_same_file(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / '.tmp')) as directory:
            root = Path(directory)
            for name in ('report.md', 'app.apk'):
                path = root / name;path.write_bytes(b'test')
                references = [str(path), path.as_uri(), str(path) + ':12']
                if os.name == 'nt':
                    references.extend([path.as_posix(), '/' + path.as_posix()])
                value = {**state(), 'cwd': str(root), 'turns': [{'items': [
                    {'type': 'agentMessage', 'text': ' '.join(f'[file]({reference})' for reference in references)},
                ]}]}
                with self.subTest(name=name):
                    files = artifact_paths(value, root / '.codex')
                    self.assertEqual(len(files), 1)
                    artifact = next(iter(files.values()))
                    self.assertEqual(set(artifact['references']), set(references))
                    self.assertIn(artifact['reference'], references)

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

    def test_gateway_language_updates_without_restart_and_before_login(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            self.server.ui_language_path = Path(folder) / 'ui-language.json'
            self.assertEqual(self.request('GET', '/api/auth')[2]['uiLanguage'], 'zh')
            for value, expected in [('{"language":"en"}', 'en'), ('{"language":"zh"}', 'zh'), ('broken', 'zh')]:
                self.server.ui_language_path.write_text(value)
                status, _, data = self.request('GET', '/api/auth')
                self.assertEqual(status, 200)
                self.assertFalse(data['authenticated'])
                self.assertEqual(data['uiLanguage'], expected)

    def test_computer_identity_is_only_available_after_login(self):
        from unittest.mock import patch
        with patch('bridge.httpd.socket.gethostname', return_value='demo-workstation'):
            status, _, public = self.request('GET', '/api/auth')
            self.assertEqual(status, 200)
            self.assertNotIn('computer', public)
            self.assertIsInstance(public['instanceId'], str)
            before = public['loginStatus']
            auth = self.login()
            status, _, private = self.request('GET', '/api/auth', headers=auth)
            self.assertEqual(private['computer']['name'], 'demo-workstation')
            self.assertIn(private['computer']['platform'], ['Darwin', 'Linux', 'Windows'])
            self.assertEqual(private['loginStatus'], before)

    def test_ignore_submission_route_requires_login_csrf_and_exact_payload(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        ignore = Mock(return_value={'accounts': [], 'blockers': []})
        self.server.bridge.accounts = SimpleNamespace(ignore_submission=ignore)
        payload = {'threadId': THREAD, 'submissionId': str(uuid.uuid4()), 'host': 'local'}
        path = '/api/accounts/ignore-submission'
        self.assertEqual(self.request('POST', path, payload)[0], 401)
        auth = self.login()
        self.assertEqual(self.request('POST', path, payload, {'Cookie': auth['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, {**payload, 'extra': True}, auth)[0], 400)
        ignore.assert_not_called()
        self.assertEqual(self.request('POST', path, payload, auth)[0], 200)
        ignore.assert_called_once_with(payload)

    def test_cross_site_link_can_open_homepage_only(self):
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                      'Sec-Fetch-Dest': 'document', 'Sec-Fetch-User': '?1'}
        for path in ['/', '/?source=link']:
            with self.subTest(path=path):
                conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
                self.addCleanup(conn.close)
                # A link from another site has Fetch Metadata but no Origin.
                conn.request('GET', path, headers=navigation)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader('Content-Type').startswith('text/html'))
                self.assertEqual(response.read(), (ROOT / 'web' / 'index.html').read_bytes())

    def test_cross_site_navigation_keeps_api_embedding_origin_and_host_guards(self):
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                      'Sec-Fetch-Dest': 'document'}
        cases = [
            ('GET', '/api/auth', navigation),
            ('GET', '/api/sessions', navigation),
            ('GET', '/app.js', navigation),
            ('GET', '/', {**navigation, 'Sec-Fetch-Dest': 'iframe'}),
            ('GET', '/', {**navigation, 'Sec-Fetch-Mode': 'cors'}),
            ('GET', '/', {'Sec-Fetch-Site': 'cross-site'}),
            ('GET', '/', {**navigation, 'Origin': 'https://other.example'}),
            ('GET', '/', {**navigation, 'Host': 'other.example'}),
            ('POST', '/', navigation),
            ('POST', '/api/login', navigation),
        ]
        for method, path, headers in cases:
            with self.subTest(method=method, path=path, headers=headers):
                self.assertEqual(self.request(method, path, headers=headers)[0], 403)

    def test_pushplus_configuration_requires_login_and_csrf_and_hides_token(self):
        from bridge.notifications import Notifications, settings
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            self.server.notifications = Notifications(None, directory)
            path = '/api/notifications/pushplus'
            self.assertEqual(self.request('GET', path)[0], 401)
            headers = self.login()
            body = {'pushplusEnabled': True, 'pushplusToken': 'private-pushplus'}
            self.assertEqual(self.request('POST', path, body, {'Cookie': headers['Cookie']})[0], 403)
            status, _, result = self.request('POST', path, body, headers)
            self.assertEqual(status, 200)
            self.assertEqual(result, {'pushplusEnabled': True, 'hasPushplusToken': True})
            self.assertNotIn('private-pushplus', json.dumps(self.request('GET', path, headers=headers)))
            self.assertEqual(settings(directory)['pushplusToken'], 'private-pushplus')
            with patch('bridge.httpd.publish_pushplus') as send:
                self.assertEqual(self.request('POST', path+'/test', {}, headers)[0], 200)
                send.assert_called_once()
            self.assertEqual(self.request('POST', path, {'server':'https://other.example'}, headers)[0], 400)

    def test_rename_route_requires_csrf_and_routes_to_selected_host(self):
        from unittest.mock import Mock
        headers = self.login()
        remote = Mock()
        remote.rename.return_value = {'id': THREAD, 'host':'remote', 'title':'New'}
        self.server.bridge.for_host = Mock(return_value=remote)
        path = '/api/sessions/'+THREAD+'/rename?host=remote'
        self.assertEqual(self.request('POST', path, {'title':'New'}, {'Cookie':headers['Cookie']})[0], 403)
        remote.rename.assert_not_called()
        self.assertEqual(self.request('POST', path, {'title':'New'}, headers)[0], 200)
        self.server.bridge.for_host.assert_called_once_with('remote')
        remote.rename.assert_called_once_with(THREAD, 'New')

    def test_skill_catalog_route_paginates_searches_and_hydrates_selection(self):
        from unittest.mock import Mock
        headers = self.login()
        catalog = self.server.bridge.catalog = Mock()
        catalog.return_value = {'kind': 'skills', 'skills': [], 'selectedSkills': [], 'total': 0}
        path = ('/api/sessions/' + THREAD + '/catalog?kind=skills&q=fixture&limit=5&offset=10'
                '&id=selected-1&id=selected-2&id=selected-1&host=local')
        status, _, body = self.request('GET', path, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(catalog.call_args.args, (THREAD,))
        self.assertEqual(catalog.call_args.kwargs, {'refresh': False, 'kind': 'skills', 'query': 'fixture', 'offset': 10, 'limit': 5, 'ids': ['selected-1', 'selected-2']})
        self.assertEqual(self.request('GET', '/api/sessions/'+THREAD+'/catalog?kind=skills&limit=999', headers=headers)[0], 200)
        self.assertEqual(catalog.call_args.kwargs['limit'], 500)
        self.assertEqual(self.request('GET', '/api/sessions/'+THREAD+'/catalog?kind=skills&offset=-1', headers=headers)[0], 200)
        self.assertEqual(catalog.call_args.kwargs['offset'], 0)
        self.assertEqual(self.request('GET', '/api/sessions/'+THREAD+'/catalog?kind=skills&offset=bad', headers=headers)[0], 400)

    def test_web_appearance_assets_are_served_with_correct_types(self):
        for name, content_type in [('presentation.js', 'text/javascript'), ('presentation.css', 'text/css')]:
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
            self.addCleanup(conn.close)
            conn.request('GET', '/' + name, headers={'Origin': self.origin})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.getheader('Content-Type').startswith(content_type))
            self.assertEqual(response.read(), (ROOT / 'web' / name).read_bytes())

    def test_saved_account_reset_route_requires_login_csrf_and_same_origin(self):
        from unittest.mock import Mock
        manager = Mock();manager.account.return_value = {'outcome':'reset','account':{'visible':True}}
        self.server.bridge.accounts = manager
        path = '/api/accounts/account'
        body = {'id':'a'*32,'operation':'consume','confirmed':True}
        self.assertEqual(self.request('POST', path, body)[0], 401)
        auth = self.login()
        self.assertEqual(self.request('POST', path, body, {'Cookie':auth['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, body, {**auth,'Origin':'https://other.test'})[0], 403)
        manager.account.assert_not_called()
        self.assertEqual(self.request('POST', path, body, auth)[2]['outcome'], 'reset')
        manager.account.assert_called_once_with(body)

    def test_account_routes_require_login_csrf_and_same_origin(self):
        from unittest.mock import Mock
        account = self.server.bridge.account = Mock()
        account.read.return_value = {'visible': False}
        account.consume.return_value = {'outcome': 'reset', 'account': {'visible': True}}
        self.assertEqual(self.request('GET', '/api/account')[0], 401)
        self.assertEqual(self.request('POST', '/api/account/reset', {})[0], 401)
        account.read.assert_not_called();account.consume.assert_not_called()
        auth = self.login()
        self.assertEqual(self.request('GET', '/api/account', headers=auth)[2], {'visible': False})
        self.assertEqual(self.request('POST', '/api/account/reset', {}, {'Cookie': auth['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', '/api/account/reset', {}, {**auth, 'Origin': 'https://other.test'})[0], 403)
        account.consume.assert_not_called()
        body = {'confirmed': True, 'requestId': str(uuid.uuid4()), 'creditId': 'card-1', 'accountKey': 'a'*64}
        self.assertEqual(self.request('POST', '/api/account/reset', body, auth)[2]['outcome'], 'reset')
        account.consume.assert_called_once_with(body)
        account.consume.side_effect = PermissionError('Only native login')
        self.assertEqual(self.request('POST', '/api/account/reset', body, auth)[0], 403)

    def test_mobile_pairing_persists_as_revocable_device_across_restart(self):
        from unittest.mock import patch
        from bridge.pairing import Pairing
        for platform in ('Android', 'iOS'):
            with self.subTest(platform=platform):
                config = {'mode': 'none', 'sessionHours': 1}
                temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
                self.addCleanup(temporary.cleanup)
                directory = Path(temporary.name)
                self.server.auth = Auth(config, directory)
                # Local test uses an approved non-loopback alias for QR issuance.
                origin = 'http://phone.example.test'
                self.server.auth.new_session('127.0.0.1')
                pairing = Pairing(self.server.auth, {origin})
                grant = pairing.control({'action': 'create', 'url': origin})
                ua = 'Mozilla/5.0 BridgeMobile/0.1-' + platform
                token, row = pairing.exchange(grant['url'].split('#pair=')[1], origin, '127.0.0.1', ua)
                headers = {'Cookie': Auth.COOKIE + '=' + token, 'User-Agent': ua}
                with patch('bridge.auth.time.time', return_value=row['created'] + 30 * 86400):
                    self.server.auth = Auth(config, directory)
                    status, response, body = self.request('GET', '/api/auth', headers=headers)
                    self.assertEqual(status, 200)
                    self.assertTrue(body['authenticated'])
                    self.assertTrue(body['trustedDevice'])
                    self.assertIn('HttpOnly', response['Set-Cookie'])
                    self.assertIn('Max-Age=34560000', response['Set-Cookie'])
                    self.server.auth.manage({'action': 'revoke', 'id': Auth.key(token)})
                    self.server.auth = Auth(config, directory)
                    self.assertFalse(self.request('GET', '/api/auth', headers=headers)[2]['authenticated'])

    def test_login_cookie_lifetime_and_device_revocation(self):
        self.server.auth = Auth({'mode': 'none', 'sessionHours': 0})
        status, headers, body = self.request('POST', '/api/login', {}, {'User-Agent': 'iPhone Test'})
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=34560000', headers['Set-Cookie'])
        auth = {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': body['csrf']}
        row = self.server.auth.manage({})['sessions'][0]
        self.assertEqual(row['ip'], '127.0.0.1')
        self.assertEqual(row['userAgent'], 'iPhone Test')
        status, refreshed, body = self.request('GET', '/api/auth', headers=auth)
        self.assertTrue(body['authenticated'])
        self.assertIn('Max-Age=34560000', refreshed['Set-Cookie'])
        self.server.auth.manage({'action': 'revoke', 'id': row['id']})
        self.assertEqual(self.request('GET', '/api/sessions', headers=auth)[0], 401)

    def test_ip_rules_reject_login_pairing_and_existing_sessions(self):
        auth = self.login()
        self.server.auth.manage({'action': 'save', 'policy': {'blocklist': ['127.0.0.1']}})
        self.assertEqual(self.request('GET', '/api/sessions', headers=auth)[0], 403)
        self.assertEqual(self.request('POST', '/api/login', {})[0], 403)
        self.assertEqual(self.request('POST', '/api/pair', {'token': 'invalid'})[0], 403)
        # Desktop health discovery must still work while access is blocked.
        status, _, body = self.request('GET', '/api/auth', headers=auth)
        self.assertEqual(status, 200)
        self.assertFalse(body['authenticated'])
        self.server.auth.manage({'action': 'save', 'policy': {}})
        self.assertEqual(self.request('GET', '/api/sessions', headers=auth)[0], 401)
        self.assertEqual(self.request('POST', '/api/devices', {}, self.login())[0], 404)

    def test_forged_ip_headers_do_not_bypass_direct_access_rules(self):
        self.server.auth.manage({'action': 'save', 'policy': {'blocklist': ['127.0.0.1']}})
        headers = {'X-Forwarded-For': '192.0.2.7', 'CF-Connecting-IP': '192.0.2.7'}
        self.assertEqual(self.request('POST', '/api/login', {}, headers)[0], 403)

    def test_create_chat_requires_login_csrf_and_saved_project(self):
        from types import SimpleNamespace
        calls=[]
        self.server.bridge.hosts=SimpleNamespace(projects=lambda:[{'key':'local|p','name':'Test'}])
        self.server.bridge.create_chat=lambda *args: calls.append(args) or {'id':THREAD,'host':'local'}
        body={'project':'local|p','title':'New chat','id':str(uuid.uuid4())}
        self.assertEqual(self.request('GET','/api/projects')[0],401)
        self.assertEqual(self.request('POST','/api/sessions',body)[0],401)
        headers=self.login()
        self.assertEqual(self.request('POST','/api/sessions',body,{'Cookie':headers['Cookie']})[0],403)
        self.assertEqual(calls,[])
        self.assertEqual(self.request('GET','/api/projects',headers=headers)[2]['projects'][0]['key'],'local|p')
        status,_,result=self.request('POST','/api/sessions',body,headers)
        self.assertEqual(status,200)
        self.assertEqual(result['id'],THREAD)
        self.assertEqual(calls,[(body['project'],body['title'],body['id'])])

    def test_explicit_reconnect_requires_auth_csrf_and_activation_flag(self):
        from types import SimpleNamespace
        calls = []
        self.server.bridge.activate = lambda tid: calls.append(tid) or SimpleNamespace(connected=True)
        path = '/api/sessions/' + THREAD + '/reconnect'
        self.assertEqual(self.request('POST', path, {'activate': True})[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, {'activate': True}, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(calls, [])
        self.assertEqual(self.request('GET', path, headers=headers)[0], 404)
        status, _, body = self.request('POST', path, {'activate': True}, headers)
        self.assertEqual(status, 200)
        self.assertTrue(body['connected'])
        self.assertEqual(calls, [THREAD])

    def test_database_read_failure_has_actionable_http_error(self):
        from bridge.store import StoreUnavailable
        def unavailable(*args, **kwargs):
            raise StoreUnavailable('Database temporarily unavailable; retry')
        self.server.bridge.timeline_read = unavailable
        status, _, body = self.request('GET', '/api/sessions/' + THREAD + '/timeline', headers=self.login())
        self.assertEqual(status, 409)
        self.assertIn('retry', body['error'])
        self.assertNotIn('日志', body['error'])

    def test_reverse_proxy_preserved_host_keeps_secure_login(self):
        origin='https://codex.example.test:9443'
        host='codex.example.test:9443'
        self.server.origins.add(origin);self.server.hosts.add(host);self.server.secure_hosts.add(host)
        headers={'Host':host,'Origin':origin}
        status,response,body=self.request('POST','/api/login',{'username':'admin','password':'correct password test'},headers)
        self.assertEqual(status,200)
        self.assertIn('; Secure',response['Set-Cookie'])
        headers['Cookie']=response['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('GET','/api/sessions',headers=headers)[0],200)
        self.assertEqual(self.request('GET','/api/sessions',headers={**headers,'Host':'unconfigured.example','X-Forwarded-Host':host})[0],403)

    def test_page_scripts_are_served_with_same_origin_csp(self):
        import re
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request('GET', '/')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            scripts = re.findall(r'<script src="([^"]+)"', response.read().decode())
            self.assertEqual([script.split('?', 1)[0] for script in scripts], ['/vendor/markdown-it.min.js', '/vendor/katex/katex.min.js',
                                       '/vendor/texmath.js', '/message-actions.js', '/markdown.js', '/i18n.js', '/downloads.js', '/image-viewer.js', '/timeline.js', '/account.js', '/modes.js', '/attachments.js', '/activity.js', '/fast-mode.js', '/accounts.js', '/client-accounts.js', '/floating-panel.js', '/git-panel.js', '/vendor/xterm/xterm.js', '/vendor/xterm/addon-fit.js', '/command-terminal-panel.js', '/terminal-panel.js', '/permissions.js', '/side-chat.js', '/agents-panel.js', '/workbench.js', '/list-sync.js', '/desktop-sessions.js', '/client-lifecycle.js', '/client-navigation.js', '/app.js', '/presentation.js'])
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

    def test_notification_route_is_authenticated_and_csrf_protected(self):
        class NotificationsFixture:
            def policy(self, thread, host, value):
                return {'available': True, 'watching': value['requests']=='on', 'notifyOnCompletion': value['completion']=='on'}
            def defaults(self, value=None):
                return value or {'requests':True,'completion':False}
        self.server.notifications = NotificationsFixture()
        self.server.bridge.host = 'local'
        path = '/api/sessions/'+THREAD+'/notifications'
        self.assertEqual(self.request('GET', path)[0], 401)
        auth = self.login()
        self.assertEqual(self.request('POST', path, {'requests':'on','completion':'off'}, {'Cookie': auth['Cookie']})[0], 403)
        status, _, value = self.request('POST', path, {'requests':'on','completion':'off'}, auth)
        self.assertEqual(status, 200)
        self.assertTrue(value['watching'])
        self.assertFalse(value['notifyOnCompletion'])
        status, _, value = self.request('POST', path, {'requests':'on','completion':'on'}, auth)
        self.assertEqual(status, 200)
        self.assertTrue(value['notifyOnCompletion'])

        defaults = '/api/notifications/defaults'
        self.assertEqual(self.request('GET', defaults)[0], 401)
        self.assertEqual(self.request('POST', defaults, {'requests':False,'completion':True}, {'Cookie':auth['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', defaults, {'requests':False,'completion':True}, auth)[0], 200)

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

    def test_poll_is_authenticated_and_returns_current_state(self):
        session = LiveSession(THREAD)
        session.state = state()
        session.connected = True
        self.server.bridge.session = lambda *a, **k: session
        self.server.bridge.view = lambda *a, **k: session.view()
        self.assertEqual(self.request('GET', '/api/sessions/'+THREAD+'/poll?after=-1')[0], 401)
        auth = self.login()
        finished = threading.Event()
        process_request = self.server.process_request_thread
        def complete_request(*args):
            try:
                process_request(*args)
            finally:
                finished.set()
        self.server.process_request_thread = complete_request
        status, _, value = self.request('GET', '/api/sessions/'+THREAD+'/poll?after=-1', headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(value['state']['id'], THREAD)
        self.assertTrue(finished.wait(3), 'Poll request did not finish cleanup')
        self.assertEqual(session.viewers, 0)

    def test_api_cannot_execute_arbitrary_rpc_or_read_files(self):
        auth = self.login()
        self.assertEqual(self.request('POST', '/api/rpc', {'method': 'arbitrary'}, auth)[0], 404)
        self.assertEqual(self.request('GET', '/.local/config.json', headers=auth)[0], 404)


if __name__ == '__main__':
    unittest.main()
