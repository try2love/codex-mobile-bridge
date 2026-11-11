import json
import os
import sqlite3
import tempfile
import time
import uuid
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from bridge.clients.errors import BridgeUnavailable
from bridge.clients.zcode.history import saved_detail, saved_sessions


class ZCodeSavedHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve() / 'v2'
        self.home.mkdir()
        self.workspace = str(self.home.parent / 'work')
        Path(self.workspace).mkdir()
        self.index = self.home / 'tasks-index.sqlite'
        self.transcript = self.home.parent / 'cli' / 'db' / 'db.sqlite'
        self.transcript.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.index)) as db:
            db.execute('CREATE TABLE tasks(task_id, title, workspace_path, updated_at, archived, deleted, host_id, provider, workspace_identity, model)')
            db.executemany("INSERT INTO tasks VALUES (?, ?, ?, 1, ?, ?, ?, 'glm', NULL, 'Flash')", [
                ('sess_ok', 'Saved', self.workspace, 0, 0, 'local'), ('sess_deleted', '', self.workspace, 0, 1, 'local'),
                ('sess_remote', '', self.workspace, 0, 0, 'ssh:host'), ('sess_mismatch', '', self.workspace, 0, 0, 'local'),
                ('sess_archived', 'Archived', self.workspace, 1, 0, 'local')])
            db.commit()
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute('CREATE TABLE session(id PRIMARY KEY, directory, time_deleted)')
            db.executemany('INSERT INTO session VALUES (?, ?, NULL)', [
                ('sess_' + sid, str(self.home.parent / 'another') if sid == 'mismatch' else self.workspace)
                for sid in ('ok', 'deleted', 'remote', 'mismatch', 'archived')])
            db.execute('CREATE TABLE message(id PRIMARY KEY, session_id, sequence INTEGER, time_created INTEGER, data)')
            db.execute('CREATE TABLE part(id PRIMARY KEY, session_id, message_id, sequence INTEGER, time_created INTEGER, data)')
            for i, (sid, role, visibility, synthetic) in enumerate([
                    ('sess_ok', 'user', 'visible', False), ('sess_ok', 'user', 'hidden', True),
                    ('sess_ok', 'assistant', 'visible', False), ('sess_remote', 'assistant', 'visible', False)]):
                msg = {'role': role, 'semantics': {'uiVisibility': visibility}, 'synthetic': synthetic}
                db.execute('INSERT INTO message VALUES (?, ?, ?, ?, ?)', (str(i), sid, i, i, json.dumps(msg)))
                db.execute('INSERT INTO part VALUES (?, ?, ?, 0, 0, ?)',
                           (sid + str(i), sid, str(i), json.dumps({'type': 'text', 'text': role + str(i)})))
            db.execute("""INSERT INTO part VALUES ('internal', 'sess_ok', '0', 1, 0,
                       '{"type":"text","text":"private runtime context","synthetic":true}')""")
            db.commit()

    def test_saved_composer_labels_preserve_routing_ids_without_live_permissions(self):
        for model, display, effort in [
                ('account:zai-start-plan/GLM-5.3-Flash$low', 'GLM-5.3-Flash', 'low'),
                ('api:fixture/team/model$high', 'team/model', 'high'),
                ('custom:api%3Afixture:team%2Fmodel', 'team/model', None),
                ('Flash', 'Flash', None)]:
            with self.subTest(model=model), closing(sqlite3.connect(self.index)) as db:
                db.execute('UPDATE tasks SET model=? WHERE task_id=?', (model, 'sess_ok'))
                db.commit()
                view = saved_detail(self.home, 'sess_ok')
                row = view['session']
                self.assertEqual(row['model'], model)
                self.assertEqual(row['modelDisplayName'], display)
                self.assertEqual(row.get('effort'), effort)
                self.assertTrue(row['composerCapabilities']['skills'])
                self.assertTrue(row['composerCapabilities']['plan'])
                self.assertFalse(view['capabilities']['send'])
                self.assertNotIn('skills', view['capabilities'])
                self.assertTrue(view['readOnly'])

    def test_indexed_chat_without_cli_transcript_can_open_but_not_authorize_a_write(self):
        from bridge.clients.zcode.history import resume_workspace
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute("DELETE FROM session WHERE id='sess_ok'")
            db.commit()
        # Orphaned message rows still exist. They must not be read through the index alone.
        result = saved_detail(self.home, 'sess_ok')
        self.assertEqual(result['messages'], [])
        self.assertTrue(result['readOnly'] and result['session']['indexOnly'])
        self.assertFalse(result['capabilities']['send'])
        self.assertEqual(resume_workspace(self.home, 'sess_ok'), self.workspace)
        for sid in ('sess_mismatch', 'sess_deleted', 'sess_remote', 'sess_missing'):
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                saved_detail(self.home, sid)

    def test_record_location_follows_verified_native_storage_and_rejects_foreign_sessions(self):
        from types import SimpleNamespace
        from bridge.clients.zcode.adapter import ZCodeDesktop
        adapter = SimpleNamespace(home=self.home)
        self.assertEqual(ZCodeDesktop.record_path(adapter, 'sess_ok'), str(self.transcript))
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute("DELETE FROM session WHERE id='sess_ok'"); db.commit()
        self.assertEqual(ZCodeDesktop.record_path(adapter, 'sess_ok'), str(self.index))
        for sid in ('sess_remote', 'sess_deleted', 'sess_mismatch', 'sess_missing'):
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                ZCodeDesktop.record_path(adapter, sid)

    def test_historical_reply_uses_its_own_completed_model_call(self):
        from bridge.clients.zcode.reply_metadata import native_reply_models
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute("UPDATE message SET data=json_set(data,'$.modelId','previous','$.parentID','0','$.anchor.turnId','turn-a') WHERE id='2'")
            db.execute("UPDATE message SET data=json_set(data,'$.modelSelection.modelId','requested','$.modelSelection.options.reasoningLevel','high') WHERE id='0'")
            db.execute('CREATE TABLE model_usage(session_id, turn_id, assistant_message_id, model_id, variant, status, completed_at, attempt_index)')
            db.executemany('INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?)',[
                ('sess_ok','turn-a','2','older','medium','completed',1,0),
                ('sess_ok','turn-a','2','Flash','low','completed',2,1),
                ('sess_remote','turn-a','2','other','high','completed',3,1)])
            db.commit()
        models = native_reply_models(self.home,'sess_ok',self.workspace,['2'])
        self.assertEqual(models, {'2': {'model': 'Flash', 'effort': 'low'}})
        self.assertEqual(native_reply_models(self.home,'sess_ok',str(self.home.parent / 'wrong'),['2']), {})
        reply = next(m for m in saved_detail(self.home,'sess_ok')['messages'] if m['role']=='assistant')
        self.assertEqual((reply['model'],reply['effort']), ('Flash','low'))
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute('DROP TABLE model_usage')
            db.commit()
        self.assertEqual(native_reply_models(self.home,'sess_ok',self.workspace,['2']), {'2': {'model':'previous'}})

    def test_resume_checks_local_project_when_cold_transcript_cannot_be_attached(self):
        from bridge.clients.zcode.history import resume_workspace, _TranscriptUnavailable
        with patch('bridge.clients.zcode.history.saved_target', side_effect=_TranscriptUnavailable('cold')):
            self.assertEqual(resume_workspace(self.home, 'sess_ok'), self.workspace)
            for sid in ('sess_archived', 'sess_remote', 'sess_deleted', 'sess_missing'):
                with self.subTest(sid=sid), self.assertRaises(ValueError):
                    resume_workspace(self.home, sid)
        with self.assertRaises(ValueError):
            resume_workspace(self.home, 'sess_mismatch')

    def test_legacy_sqlite_json_index_does_not_block_verified_native_resume(self):
        from bridge.clients.zcode.history import resume_workspace
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute("CREATE INDEX dwf_event_artifact_idx ON message(json_extract(data,'$.artifact'))")

        connect = sqlite3.connect
        def legacy_connection(*args, **kwargs):
            db = connect(*args, **kwargs)
            # Deterministic but not tagged SQLITE_INNOCUOUS, as on older SQLite.
            db.create_function('json_extract', -1, lambda *_: None, deterministic=True)
            return db

        with patch.object(sqlite3, 'connect', side_effect=legacy_connection):
            self.assertEqual(resume_workspace(self.home, 'sess_ok'), self.workspace)
            with self.assertRaises(ValueError):
                resume_workspace(self.home, 'sess_remote')
            with self.assertRaises(BridgeUnavailable):
                saved_detail(self.home, 'sess_ok')

    def test_debug_reconnect_from_saved_chat_validates_process_and_workspace_before_binding(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        app = {'application': 'fixture.app', 'dataDirectory': self.home, 'debugFallbackPort': 17742}
        adapter = ZCodePrototype(self.home.parent, app)
        process = {'pid': 7, 'birth': [1, 0], 'started': [1, 0]}
        binding = {'runtime': {'workspaceKey': self.workspace, 'identity': 'runtime'}, 'clientId': 'client', 'connectionId': 'owner'}
        calls = []
        def native(request):
            calls.append(request['action'])
            if request['action'] == 'target':
                return {'workspace': self.workspace}
            if request['action'] == 'attach':
                return {'binding': binding, 'subscriptionId': 'lease'}
            if request['action'] == 'detail':
                return {'snapshot': {'sessionId': 'sess_ok', 'revision': 1, 'seq': 0, 'logEpoch': 'a',
                                     'rows': {'window': [], 'totalCount': 0}, 'control': {},
                                     'inputRouting': {'mode': 'startNow'}, 'pendingInteractions': []}}
            raise AssertionError(request['action'])
        with patch.object(adapter, '_window_app', return_value=None), patch.object(adapter, '_live_process', return_value=process), \
                patch('bridge.clients.zcode.transport.request_native', side_effect=native):
            before = adapter.call('detail', 'sess_ok')
            self.assertTrue(before['readOnly'] and before['capabilities']['resume'])
            self.assertFalse(adapter.call('detail', 'sess_archived')['capabilities']['resume'])
            self.assertFalse(adapter.status()['connected'])
            self.assertEqual(calls, [])
            receipt = adapter.call('resume', 'sess_ok', {'id': str(uuid.uuid4())})
            self.assertEqual(receipt['status'], 'ready')
            self.assertEqual(calls, ['target', 'attach'])
            self.assertEqual(adapter.sid, 'sess_ok')
            self.assertEqual(adapter.config, app)
            live = adapter.call('detail', 'sess_ok')
            self.assertTrue(live['capabilities']['send'])
            self.assertEqual(live['session']['connectionState'], 'ready')
            self.assertEqual(live['session']['controlScope'], 'session')
            # Both target lookup and attach must verify the newly observed, exact PID.
            replacement = {**process, 'birth': [2, 0]}
            with patch.object(adapter, '_live_process', side_effect=[replacement, replacement, process]):
                with self.assertRaisesRegex(BridgeUnavailable, '进程已变化'):
                    adapter.call('resume', 'sess_ok', {'id': str(uuid.uuid4())})
            self.assertEqual(calls, ['target', 'attach', 'detail', 'target'])

    def test_reconnect_refuses_different_native_workspace_and_archived_rows(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        adapter = ZCodePrototype(self.home.parent, {'dataDirectory': self.home, 'debugFallbackPort': 17742})
        with patch.object(adapter, '_live_process', return_value={'pid': 29}), \
                patch.object(adapter, 'transport', return_value={'workspace': str(self.home.parent / 'unrelated')}) as native:
            with self.assertRaisesRegex(BridgeUnavailable, '项目已变化'):
                adapter.call('resume', 'sess_ok', {'id': str(uuid.uuid4())})
            with self.assertRaisesRegex(BridgeUnavailable, '归档'):
                adapter.call('resume', 'sess_archived', {'id': str(uuid.uuid4())})
            self.assertEqual([call.args[0]['action'] for call in native.call_args_list], ['target'])
            self.assertFalse(adapter.sid)

    def _window_manager(self):
        from bridge.clients.manager import DesktopSessions
        from bridge.clients.zcode.adapter import ZCodeDesktop
        manager = DesktopSessions(self.home.parent)
        self.addCleanup(manager.close)
        adapter = ZCodeDesktop(manager.directory/'zcode', {'dataDirectory': self.home, 'application': 'fixture.app'})
        manager.adapters['zcode'] = adapter
        manager.config.setdefault('enabled', {})['zcode'] = True
        window = patch.object(adapter, '_window_app', return_value='/fixture/ZCode')
        window.start()
        self.addCleanup(window.stop)
        return manager, adapter

    def _append_user(self, mid, text, seq=10, sid='sess_ok', visible=True, at=None):
        at = int(time.time() * 1000) if at is None else at
        with closing(sqlite3.connect(self.transcript)) as db:
            msg = {'role': 'user', 'semantics': {'uiVisibility': 'visible' if visible else 'hidden'}}
            db.execute('INSERT INTO message VALUES (?, ?, ?, ?, ?)', (mid, sid, seq, at, json.dumps(msg)))
            db.execute('INSERT INTO part VALUES (?, ?, ?, 0, ?, ?)', (
                'part-'+mid, sid, mid, at, json.dumps({'type':'text','text':text})))
            db.commit()

    def test_window_resume_is_explicit_and_send_waits_for_original_user_message(self):
        manager, adapter = self._window_manager()
        rid = str(uuid.uuid4())
        with patch('bridge.platforms.macos.zcode.operation') as operate:
            listing = adapter.call('list')
            self.assertFalse(listing['connected'])
            view = manager.call('zcode', 'detail', 'sess_ok')
            self.assertTrue(view['readOnly'] and view['capabilities']['resume'])
            self.assertFalse(view['capabilities']['send'])
            operate.assert_not_called()
            operate.return_value = {'status': 'ready', 'pid': 21}
            self.assertEqual(manager.call('zcode','resume','sess_ok',{'id':str(uuid.uuid4())})['status'], 'ready')
            self.assertFalse(manager.call('zcode', 'detail', 'sess_ok')['readOnly'])
            self.assertFalse(manager.call('zcode', 'detail', 'sess_archived')['capabilities']['resume'])
            def send(action, executable, sid, title, text, *, expected_pid):
                self.assertEqual((action, sid, title, expected_pid), ('send', 'sess_ok', 'Saved', 21))
                self._append_user('new', text)
                return {'status': 'submitted', 'pid': 21}
            operate.side_effect = send
            body = {'id':rid,'text':'same text','mode':'send'}
            self.assertEqual(manager.call('zcode','send','sess_ok',body), {'id':rid,'status':'accepted','nativeMessageId':'new'})
            self.assertEqual(manager.call('zcode','send','sess_ok',body)['nativeMessageId'], 'new')
            self.assertEqual(operate.call_count, 2)
            manager.toggle_client({'provider':'zcode','enabled':False,'quitDesktop':False})
            self.assertIsNone(adapter.window_binding)

    def test_late_receipt_only_reads_native_history_and_cannot_match_old_hidden_or_foreign_text(self):
        manager, adapter = self._window_manager()
        adapter.window_binding = ('sess_ok', 22)
        rid = str(uuid.uuid4())
        text = 'same native text'
        self._append_user('old', text, 8)
        body = {'id':rid,'mode':'send','text':text}
        with patch('bridge.platforms.macos.zcode.operation', return_value={'status':'submitted','pid':22}) as operate, \
                patch('bridge.clients.zcode.adapter.time.sleep'):
            pending=manager.call('zcode','send','sess_ok',body)
            self.assertEqual(pending['status'],'unknown')
            self._append_user('hidden',text,10,visible=False)
            self._append_user('foreign',text,11,sid='sess_remote')
            self._append_user('too-late',text,12,at=pending['windowReceiptSince']+30001)
            self.assertEqual(manager.call('zcode','send','sess_ok',body)['status'],'unknown')
            self._append_user('visible',text,11)
            self.assertEqual(manager.call('zcode','send','sess_ok',body)['nativeMessageId'],'visible')
            operate.assert_called_once()

    def test_uncertain_or_wrong_window_receipt_never_triggers_second_send(self):
        manager, adapter = self._window_manager()
        adapter.window_binding = ('sess_ok', 23)
        body={'id':str(uuid.uuid4()),'text':'match'}
        with patch('bridge.platforms.macos.zcode.operation',return_value={'status':'submitted','pid':24}) as operate:
            for _ in range(2):
                self.assertEqual(manager.call('zcode','send','sess_ok',body)['status'],'unknown')
            operate.assert_called_once()
            self.assertIsNone(adapter.window_binding)
        with patch('bridge.platforms.macos.zcode.operation') as operate:
            for sid, extra in [('sess_archived',{}),('sess_ok',{'mode':'queue'}),('sess_ok',{'plugins':['x']}),
                               ('sess_ok',{'resolvedFiles':[{'path':'untouched'}]})]:
                adapter.window_binding=('sess_ok',23)
                result=manager.call('zcode','send',sid,{'id':str(uuid.uuid4()),'text':'x',**extra})
                self.assertEqual(result['status'],'blocked')
            operate.assert_not_called()

    def test_two_new_visible_user_messages_never_confirm_a_window_send(self):
        manager, adapter = self._window_manager()
        adapter.window_binding = ('sess_ok', 26)
        body={'id':str(uuid.uuid4()),'text':'overlap'}
        def send(*args, **kwargs):
            self._append_user('one','overlap',10)
            self._append_user('two','overlap',11)
            return {'status':'submitted','pid':26}
        with patch('bridge.platforms.macos.zcode.operation',side_effect=send) as operate, \
                patch('bridge.clients.zcode.adapter.time.sleep'):
            self.assertEqual(manager.call('zcode','send','sess_ok',body)['status'],'unknown')
            self.assertEqual(manager.call('zcode','send','sess_ok',body)['status'],'unknown')
            operate.assert_called_once()

    def test_only_matching_local_tasks_and_visible_messages_are_readable(self):
        listing = saved_sessions(self.home)
        self.assertTrue(listing['readOnly'])
        self.assertFalse(listing['connected'])
        self.assertEqual({s['id']: s['archived'] for s in listing['sessions']}, {'sess_ok': False, 'sess_archived': True})
        first = saved_detail(self.home, 'sess_ok', {'historyLimit': 1})
        self.assertFalse(first['session']['runtimeKnown'])
        self.assertFalse(first['capabilities']['send'])
        self.assertEqual(first['messages'], [{'id': '2', 'role': 'assistant', 'text': 'assistant2', 'timestamp': 2}])
        older = saved_detail(self.home, 'sess_ok', {'historyBefore': first['history']['before']})
        self.assertEqual(older['messages'], [{'id': '0', 'role': 'user', 'text': 'user0', 'timestamp': 0}])
        self.assertFalse(older['history']['hasMore'])
        for sid in ('sess_mismatch', 'sess_deleted', 'sess_remote'):
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                saved_detail(self.home, sid)
        with self.assertRaises(ValueError):
            saved_detail(self.home, 'sess_ok', {'historyBefore': '3'})

    @unittest.skipUnless(os.name == 'nt', 'Requires the Windows filesystem')
    def test_local_windows_extended_path_matches_the_same_native_directory(self):
        with closing(sqlite3.connect(self.index)) as db:
            db.execute("UPDATE tasks SET workspace_path = ? WHERE task_id = 'sess_ok'", (str(self.home),))
            db.commit()
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute("UPDATE session SET directory = ? WHERE id = 'sess_ok'", ('\\\\?\\' + str(self.home),))
            db.commit()
        self.assertEqual(saved_detail(self.home, 'sess_ok')['session']['cwd'], str(self.home))

    def test_missing_profile_does_not_create_database(self):
        self.index.unlink()
        self.assertEqual(saved_sessions(self.home)['sessions'], [])
        self.assertFalse(self.index.exists())

    def test_rejects_link_to_another_profile(self):
        real = self.transcript.rename(self.transcript.with_suffix('.other'))
        try:
            self.transcript.symlink_to(real)
        except OSError:
            self.skipTest('symbolic links unavailable')
        with self.assertRaises(BridgeUnavailable):
            saved_detail(self.home, 'sess_ok')
