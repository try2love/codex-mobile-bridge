"""AGY read recovery must never turn unavailable state into write permission."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import uuid

from bridge.clients.antigravity.cli_session import AntigravityCli
from bridge.clients.antigravity.model import gateway_id
from bridge.clients.antigravity.store import AntigravityStore
from bridge.clients.errors import BridgeUnavailable
from test_antigravity_adapter import CID, ROOT, _create_surface_db, _write_transcript


class AntigravityReadingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / '.gemini/antigravity'
        self.store = AntigravityStore(self.home / 'scratch', home=self.home)

    def row(self, cid=CID, time='2026-01-01', parent=''):
        return (cid, 'native title', '', 2, time, json.dumps([(self.home / 'project').as_uri()]),
                'CASCADE_RUN_STATUS_RUNNING', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
                '', parent, int(bool(parent)), 1, 0, '')

    def test_transient_open_and_query_failures_reopen_then_recover(self):
        _create_surface_db(self.root, [self.row()])
        connect = sqlite3.connect
        db = self.root / 'conversation_summaries.db'
        broken = mock.Mock()
        broken.execute.side_effect = sqlite3.OperationalError('database is locked')
        for failure in (sqlite3.OperationalError('unable to open database file'), broken):
            calls = [failure]
            def open_db(*args, **kwargs):
                if calls:
                    result = calls.pop()
                    if isinstance(result, Exception):
                        raise result
                    return result
                return connect(*args, **kwargs)
            with mock.patch('bridge.clients.antigravity.store.sqlite3.connect', side_effect=open_db) as opened, \
                    mock.patch('bridge.clients.antigravity.store.time.sleep'):
                self.assertTrue(self.store.is_native_busy(self.root, CID))
                self.assertEqual(opened.call_count, 2)
                self.assertIn('mode=ro', opened.call_args.args[0])
        broken.close.assert_called_once()

    def test_exhausted_or_permanent_failure_does_not_become_idle(self):
        _create_surface_db(self.root, [self.row()])
        for error, attempts in ((sqlite3.OperationalError('database is locked'), 3),
                                (sqlite3.DatabaseError('file is not a database'), 1),
                                (sqlite3.OperationalError('no such column: status'), 1)):
            with mock.patch('bridge.clients.antigravity.store.sqlite3.connect', side_effect=error) as opened, \
                    mock.patch('bridge.clients.antigravity.store.time.sleep'):
                with self.assertRaises(sqlite3.DatabaseError):
                    self.store.is_native_busy(self.root, CID)
                self.assertEqual(opened.call_count, attempts)

    def test_old_summary_retains_native_identity_workspace_state_and_archive(self):
        rows = [self.row(str(uuid.uuid4()), '2026-11-01') for _ in range(301)]
        child = str(uuid.uuid4())
        _create_surface_db(self.root, rows + [self.row(), self.row(child, parent=CID)])
        conn = sqlite3.connect(str(self.root / 'conversation_summaries.db'))
        conn.execute('CREATE TABLE conversations_fts (conversation_id TEXT, archived INTEGER)')
        conn.execute('INSERT INTO conversations_fts VALUES (?, 1)', (CID,))
        conn.commit()
        conn.close()
        summary = self.store.resolve_session(gateway_id('app', CID))
        self.assertEqual(summary['title'], 'native title')
        self.assertEqual(summary['cwd'], str(self.home / 'project'))
        self.assertEqual(summary['nativeId'], CID)
        self.assertEqual(summary['status'], 'active')
        self.assertTrue(summary['runtimeKnown'])
        self.assertTrue(summary['archived'])
        self.assertIsNone(self.store.resolve_session(gateway_id('ide', CID)))
        self.assertIsNone(self.store.resolve_session(gateway_id('app', child)))

    def test_page_cache_copies_values_and_keys_activity_cursor_and_limit(self):
        _write_transcript(self.root, CID, [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'question'},
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'content': 'answer'}])
        with mock.patch.object(self.store, '_load_reconciled_steps', wraps=self.store._load_reconciled_steps) as read:
            first = self.store.read_transcript_page(self.root, CID)
            first['messages'].clear()
            self.assertTrue(self.store.read_transcript_page(self.root, CID)['messages'])
            self.assertEqual(read.call_count, 1)
            self.store.read_transcript_page(self.root, CID, session_active=True)
            self.store.read_transcript_page(self.root, CID, limit=200)
            self.assertFalse(self.store.read_transcript_page(self.root, CID, before=0)['messages'])
            self.assertEqual(read.call_count, 4)
            for before in range(20):
                self.store.read_transcript_page(self.root, CID, before=before)
            self.assertLessEqual(len(self.store._transcript_cache), 16)

    def test_page_cache_invalidates_full_hydration_replacement_rewind_and_deletion(self):
        compact = [{'step_index': 0, 'type': 'PLANNER_RESPONSE', 'content': 'short', 'truncated_fields': ['content']}]
        full = [{'step_index': 0, 'type': 'PLANNER_RESPONSE', 'content': 'answer one'}]
        logs = _write_transcript(self.root, CID, compact, full)
        def content():
            return json.dumps(self.store.read_transcript_page(self.root, CID), ensure_ascii=False)
        self.assertIn('answer one', content())
        full_path = logs / 'transcript_full.jsonl'
        full_stamp = full_path.stat()
        full[0]['content'] = 'answer two'
        _write_transcript(self.root, CID, compact, full)
        os.utime(full_path, ns=(full_stamp.st_atime_ns, full_stamp.st_mtime_ns + 1_000_000_000))
        self.assertIn('answer two', content())
        path = logs / 'transcript.jsonl'
        before = path.stat()
        replacement = logs / 'replacement'
        replacement.write_text(json.dumps({'step_index': 0, 'type': 'USER_INPUT', 'content': 'rewound'}))
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(path)
        self.assertIn('rewound', content())
        path.write_text('')
        self.assertFalse(self.store.read_transcript_page(self.root, CID)['messages'])
        path.unlink()
        self.assertFalse(self.store.read_transcript_page(self.root, CID)['messages'])
        self.assertFalse(self.store._transcript_cache)

    def test_page_cache_expires_same_signature_rewrite_without_renewing_on_hits(self):
        compact = [{'step_index': 0, 'type': 'PLANNER_RESPONSE', 'content': 'short', 'truncated_fields': ['content']}]
        full = [{'step_index': 0, 'type': 'PLANNER_RESPONSE', 'content': 'answer one'}]
        logs = _write_transcript(self.root, CID, compact, full)
        stamps = {str(logs / name): (logs / name).stat()
                  for name in ('transcript.jsonl', 'transcript_full.jsonl')}
        stat = Path.stat
        def unchanged(path, *args, **kwargs):
            return stamps.get(str(path)) or stat(path, *args, **kwargs)
        def content():
            return json.dumps(self.store.read_transcript_page(self.root, CID), ensure_ascii=False)
        with mock.patch.object(Path, 'stat', unchanged), \
                mock.patch('bridge.clients.antigravity.store.time.monotonic', return_value=0) as clock:
            self.assertIn('answer one', content())
            full[0]['content'] = 'answer two'
            _write_transcript(self.root, CID, compact, full)
            for now in (0.25, 0.75, 0.999):
                clock.return_value = now
                self.assertIn('answer one', content())
            clock.return_value = 1.0
            self.assertIn('answer two', content())

    def test_page_cache_lifetime_starts_before_a_slow_read(self):
        _write_transcript(self.root, CID, [{'type': 'USER_INPUT', 'content': 'question'}])
        read = self.store._read_transcript_page
        with mock.patch('bridge.clients.antigravity.store.time.monotonic', return_value=0) as clock:
            def slow(*args, **kwargs):
                result = read(*args, **kwargs)
                clock.return_value = 2.0
                return result
            with mock.patch.object(self.store, '_read_transcript_page', side_effect=slow) as reads:
                self.store.read_transcript_page(self.root, CID)
                self.store.read_transcript_page(self.root, CID)
            self.assertEqual(reads.call_count, 2)

    def test_changed_during_read_is_not_cached(self):
        logs = _write_transcript(self.root, CID, [{'type': 'USER_INPUT', 'content': 'one'}])
        read = self.store._read_transcript_page
        def changing(*args, **kwargs):
            result = read(*args, **kwargs)
            with (logs / 'transcript.jsonl').open('a') as stream:
                stream.write(json.dumps({'type': 'PLANNER_RESPONSE', 'content': 'two'}) + '\n')
            return result
        with mock.patch.object(self.store, '_read_transcript_page', side_effect=changing):
            self.store.read_transcript_page(self.root, CID)
        self.assertFalse(self.store._transcript_cache)
        self.assertIn('two', json.dumps(self.store.read_transcript_page(self.root, CID)))

    def test_rpc_write_budget_stop_budget_and_redacted_failure_without_replay(self):
        cli = AntigravityCli(self.home)
        target = {'address': 'http://127.0.0.1:12345', 'token': 'secret-token'}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with mock.patch('bridge.clients.antigravity.cli_session.endpoint', return_value=target), \
                mock.patch('bridge.clients.antigravity.cli_session.verify_endpoint'), \
                mock.patch.object(cli._opener, 'open', return_value=response) as opened:
            for method, timeout in (('SendUserCascadeMessage', 30), ('StartCascade', 30),
                                    ('GetUserStatus', 8), ('CancelCascadeInvocation', 8)):
                cli.call_ls_rpc(method)
                self.assertEqual(opened.call_args.kwargs['timeout'], timeout)
            opened.reset_mock()
            opened.side_effect = TimeoutError('secret-token private-text')
            with self.assertLogs('bridge.clients.antigravity.cli_session', level='WARNING') as logs:
                with self.assertRaises(BridgeUnavailable):
                    cli.call_ls_rpc('SendUserCascadeMessage', {'text': 'private-text'})
            opened.assert_called_once()
            self.assertIn('TimeoutError', logs.output[0])
            self.assertNotIn('secret-token', logs.output[0])
            self.assertNotIn('private-text', logs.output[0])
