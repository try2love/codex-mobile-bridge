import json
import sqlite3
import tempfile
import threading
import os
import subprocess
import sys
from unittest.mock import patch
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock

from bridge.features.sessions.store import SessionStore
from bridge.features.sessions.history_transfer import HistorySources

ROOT = Path(__file__).resolve().parents[1]
THREAD = '12345678-1234-4234-8234-123456789abc'


class RetainedIndexHistoryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'sessions').mkdir()
        self.rollout = self.home / 'sessions' / ('rollout-' + THREAD + '.jsonl')
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(id TEXT,originator TEXT,source TEXT,archived INT,cwd TEXT,rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)', (THREAD, 'Codex Desktop', 'vscode', 0, str(self.home), str(self.rollout)))
            db.commit()
        self.index = self.home / 'thread_history_1.sqlite'
        with closing(sqlite3.connect(self.index)) as db:
            db.execute('CREATE TABLE thread_items(thread_id,turn_id,item_id,rollout_ordinal,updated_at_ordinal,item_json)')
            db.execute('CREATE INDEX page ON thread_items(thread_id,rollout_ordinal)')
            db.execute('CREATE TABLE thread_turns(thread_id,turn_id,status,error_json,started_at,completed_at,first_user_item_id)')
            db.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?,?)', (THREAD, 'turn', 'completed', None, 100, 101, 'u'))
            for i in range(65):
                item = {'id': 'u' if i == 0 else str(i), 'type': 'userMessage' if i == 0 else 'agentMessage', 'role': 'user' if i == 0 else 'assistant', 'text': 'saved-' + str(i),
                        'content': [{'type': 'input_text' if i == 0 else 'output_text', 'text': 'saved-' + str(i)}]}
                db.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?)', (THREAD, 'turn', item['id'], i, i, json.dumps(item)))
            db.commit()
        self.store = SessionStore(self.home)

    def test_missing_rollout_still_reads_bounded_native_saved_messages(self):
        page = self.store.history(THREAD, item_limit=10)
        self.assertEqual(page['historyReadSource'], 'native-index-retained')
        self.assertEqual(len(page['turns'][0]['items']), 11)  # Ten recent items plus the initial user message.
        self.assertEqual(page['turns'][0]['items'][0]['id'], 'u')
        self.assertFalse(page['turnsPagination']['hasLoadedOldest'])
        self.assertEqual(page['requests'], [])
        self.assertEqual(page['threadRuntimeStatus']['type'], 'notLoaded')
        self.assertTrue(self.store.history_revision(THREAD))

    def test_transfer_uses_saved_index_and_explains_missing_raw_history(self):
        host = Mock(store=self.store, entries=None, live={}, lock=threading.RLock())
        host.for_host.return_value = host
        host.uploads = None
        sources = HistorySources(host, None)
        session = {'client': 'codex', 'entry': 'local', 'host': 'local', 'sessionId': THREAD}
        snapshot = sources.pin(session)
        rows = list(sources.records(session, snapshot))
        self.assertTrue(rows)
        self.assertTrue(any(row[0].get('text') == 'saved-64' for row in rows))
        self.assertTrue(snapshot['warnings'])

    def test_fork_parent_physical_segment_may_have_its_original_logical_identity(self):
        original = '23456789-2345-4345-8345-23456789abcd'
        segment = '3456789a-3456-4456-8456-3456789abcde'
        parent = self.home / 'sessions' / ('rollout-2026-11-24-' + original + '_' + segment + '.jsonl')
        records = [{'type': 'session_meta', 'payload': {'id': original, 'session_id': original}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Inherited question'}]}}]
        parent.write_text(''.join(json.dumps(row) + '\n' for row in records))
        self.rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': THREAD, 'session_id': THREAD,
            'forked_from_id': original, 'history_base': {'thread_id': segment, 'end_byte_offset': parent.stat().st_size}}}) + '\n')
        snapshot = self.store.transfer_snapshot(THREAD)
        self.assertEqual(len(snapshot['segments']), 2)
        self.assertEqual(snapshot['segments'][0][0], str(parent))

    def test_missing_ancestor_keeps_available_child_records_with_a_warning(self):
        parent = '3456789a-3456-4456-8456-3456789abcde'
        self.rollout.write_text(''.join(json.dumps(row) + '\n' for row in [
            {'type': 'session_meta', 'payload': {'id': THREAD, 'history_base': {'thread_id': parent, 'end_byte_offset': 100}}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'child-turn'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'id': 'child-question', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Available child question'}]}},
        ]))
        host = Mock(store=self.store, entries=None, live={}, lock=threading.RLock(), uploads=None)
        host.for_host.return_value = host
        sources = HistorySources(host, None)
        session = {'client': 'codex', 'entry': 'local', 'host': 'local', 'sessionId': THREAD}
        snapshot = sources.pin(session)
        self.assertTrue(snapshot['warnings'])
        self.assertTrue(any(row[0].get('text') == 'Available child question' for row in sources.records(session, snapshot)))

    def test_pinned_running_turn_does_not_adopt_a_later_completion(self):
        from bridge.features.sessions import index_archive
        with closing(sqlite3.connect(self.index)) as db:
            db.execute("UPDATE thread_turns SET status='inProgress',completed_at=NULL")
            db.commit()
        snapshot = index_archive.pin(self.home, THREAD)
        revision = self.store.history_revision(THREAD)
        with closing(sqlite3.connect(self.index)) as db:
            db.execute("UPDATE thread_turns SET status='completed',completed_at=102")
            db.commit()
        saved = list(index_archive.turns(self.home, snapshot))
        self.assertEqual(saved[0]['status'], 'inProgress')
        self.assertIsNone(saved[0]['turnCompletedAtMs'])
        self.assertNotEqual(revision, self.store.history_revision(THREAD))

    def test_remote_injection_reads_the_same_saved_index_without_installed_bridge(self):
        from bridge.clients.codex.remote import RemoteStore
        def read(_alias, script, **_kwargs):
            injected = self.home/'injected-remote.py'
            injected.write_text(script, encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', str(injected)], cwd=self.home,
                env={**os.environ, 'PYTHONPATH': '', 'CODEX_HOME': str(self.home), 'PYTHONIOENCODING': 'utf-8'},
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0,
                             f'Injected remote failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}')
            return json.loads(result.stdout)
        with patch('bridge.clients.codex.remote.ssh_read', side_effect=read):
            remote = RemoteStore('fixture')
            snapshot = remote.transfer_snapshot(THREAD)
            turns = list(remote.transfer_index_turns(snapshot['nativeIndex']))
        self.assertEqual(snapshot['warnings'], self.store.transfer_snapshot(THREAD)['warnings'])
        self.assertEqual(len(turns[0]['items']), 65)

    def test_saved_prefix_allows_appends_but_rejects_changes_to_pinned_items(self):
        from bridge.features.sessions import index_archive
        snapshot = index_archive.pin(self.home, THREAD)
        with closing(sqlite3.connect(self.index)) as db:
            db.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?)', (THREAD, 'turn', 'later', 65, 65, json.dumps({'type': 'message', 'role': 'assistant', 'content': [{'text': 'later'}]})))
            db.commit()
        saved = list(index_archive.turns(self.home, snapshot))
        self.assertNotIn('later', str(saved))
        with closing(sqlite3.connect(self.index)) as db:
            db.execute('UPDATE thread_items SET updated_at_ordinal=66 WHERE item_id=?', ('u',))
            db.commit()
        with self.assertRaises(ValueError):
            list(index_archive.turns(self.home, snapshot))
