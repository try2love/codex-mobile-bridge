import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bridge.clients.errors import BridgeUnavailable
from bridge.clients.zcode.history import saved_detail, saved_sessions


class ZCodeSavedHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve() / 'v2'
        self.home.mkdir()
        self.index = self.home / 'tasks-index.sqlite'
        self.transcript = self.home.parent / 'cli' / 'db' / 'db.sqlite'
        self.transcript.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.index)) as db:
            db.execute('CREATE TABLE tasks(task_id, title, workspace_path, updated_at, archived, deleted, host_id, provider, workspace_identity, model)')
            db.executemany("INSERT INTO tasks VALUES (?, ?, '/work', 1, ?, ?, ?, 'glm', NULL, 'Flash')", [
                ('sess_ok', 'Saved', 0, 0, 'local'), ('sess_deleted', '', 0, 1, 'local'),
                ('sess_remote', '', 0, 0, 'ssh:host'), ('sess_mismatch', '', 0, 0, 'local'),
                ('sess_archived', 'Archived', 1, 0, 'local')])
            db.commit()
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute('CREATE TABLE session(id PRIMARY KEY, directory, time_deleted)')
            db.executemany('INSERT INTO session VALUES (?, ?, NULL)', [
                ('sess_' + sid, '/another' if sid == 'mismatch' else '/work')
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

    def test_only_matching_local_tasks_and_visible_messages_are_readable(self):
        listing = saved_sessions(self.home)
        self.assertTrue(listing['readOnly'])
        self.assertFalse(listing['connected'])
        self.assertEqual({s['id']: s['archived'] for s in listing['sessions']}, {'sess_ok': False, 'sess_archived': True})
        first = saved_detail(self.home, 'sess_ok', {'historyLimit': 1})
        self.assertFalse(first['session']['runtimeKnown'])
        self.assertFalse(first['capabilities']['send'])
        self.assertEqual(first['messages'], [{'id': '2', 'role': 'assistant', 'text': 'assistant2'}])
        older = saved_detail(self.home, 'sess_ok', {'historyBefore': first['history']['before']})
        self.assertEqual(older['messages'], [{'id': '0', 'role': 'user', 'text': 'user0'}])
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
