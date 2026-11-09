import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from bridge.app.service import LiveSession
from bridge.features.sessions.store import SessionStore

ROOT = Path(__file__).resolve().parents[1]
STAMP = 1794268800000
ISO = '2026-11-10T00:00:00Z'


class SavedMessageTimeJoinTests(unittest.TestCase):
    def state(self, item_id='native-reply', turn_id='turn', session_id='thread', **message):
        return {'id': session_id, 'turns': [{'turnId': turn_id, 'status': 'completed', 'items': [
            {'id': item_id, 'type': 'agentMessage', 'text': 'same reply', **message}]}]}

    def join(self, saved, live):
        session = LiveSession('thread')
        with session.condition:
            session.set_history(saved)
            session.state = copy.deepcopy(live)
            session.connected = True
            result = session.view()
            self.assertEqual(session.state, live)
            return result

    def test_connected_view_preserves_unique_saved_native_message_time(self):
        saved = self.state(timestamp=ISO, _timestampSource='rollout-response-item')
        rows = self.join(saved, self.state())['turns'][0]['messages']
        self.assertEqual(rows[0].get('timestamp'), ISO)
        self.assertEqual(rows[0].get('_timestampSource'), 'rollout-response-item')
        self.assertEqual(self.join(saved, self.state(timestamp=STAMP))['turns'][0]['messages'][0]['timestamp'], STAMP)

    def test_text_position_session_turn_and_kind_cannot_substitute_for_native_identity(self):
        saved = self.state(timestamp=ISO)
        variants = [self.state(item_id='different'), self.state(turn_id='different'),
                    self.state(session_id='different'), self.state(type='userMessage'),
                    self.state(item_id=None)]
        for live in variants:
            with self.subTest(live=live):
                self.assertNotIn('timestamp', self.join(saved, live)['turns'][0]['messages'][0])
        synthetic = self.state(_syntheticId=True, timestamp=ISO)
        self.assertNotIn('timestamp', self.join(synthetic, self.state())['turns'][0]['messages'][0])

    def test_duplicate_keys_on_either_side_are_ambiguous_even_with_identical_text(self):
        for duplicate_saved in (True, False):
            saved, live = self.state(timestamp=ISO), self.state()
            target = saved if duplicate_saved else live
            target['turns'][0]['items'] *= 2
            rows = self.join(saved, live)['turns'][0]['messages']
            self.assertTrue(all('timestamp' not in row for row in rows))

    def test_native_message_time_supersedes_user_turn_start_fallback_only(self):
        saved = self.state(type='userMessage', timestamp=ISO)
        live = self.state(type='userMessage')
        live['turns'][0]['turnStartedAtMs'] = STAMP - 1000
        row = self.join(saved, live)['turns'][0]['messages'][0]
        self.assertEqual(row['timestamp'], ISO)
        self.assertNotEqual(row['_timestampSource'], 'native-turn-start')
        live['turns'][0]['items'][0]['timestamp'] = STAMP - 500
        self.assertEqual(self.join(saved, live)['turns'][0]['messages'][0]['timestamp'], STAMP - 500)


class RolloutMessageTimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'sessions').mkdir()
        self.path = self.home / 'sessions' / 'rollout.jsonl'
        self.store = SessionStore(self.home)
        self.meta = {'rollout_path': str(self.path), 'cwd': '/fixture', 'title': 'Fixture'}

    def records(self, native_id='native-reply', thread_id='thread', turn_id='turn', completed=STAMP):
        return [
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn'}},
            {'type': 'response_item', 'timestamp': ISO, 'payload': {'type': 'message', 'id': native_id,
                'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'same reply'}]}},
            {'type': 'event_msg', 'timestamp': ISO, 'payload': {'type': 'item_completed',
                'thread_id': thread_id, 'turn_id': turn_id, 'completed_at_ms': completed,
                'item': {'type': 'AgentMessage', 'id': native_id, 'content': []}}}]

    def write(self, records):
        self.path.write_text(''.join(json.dumps(record) + '\n' for record in records))

    def make_index(self, items):
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as conn, conn:
            conn.execute('CREATE TABLE thread_items(thread_id,turn_id,rollout_ordinal,item_json)')
            conn.execute('CREATE TABLE thread_turns(thread_id,turn_id,status,error_json,started_at,first_user_item_id)')
            conn.execute('CREATE TABLE thread_history_projection_state(thread_id,next_rollout_byte_offset)')
            conn.execute('INSERT INTO thread_history_projection_state VALUES(?,?)', ('thread', self.path.stat().st_size))
            conn.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?)', ('thread', 'turn', 'completed', None, None, None))
            conn.executemany('INSERT INTO thread_items VALUES(?,?,?,?)', [
                ('thread', 'turn', i, json.dumps(item)) for i, item in enumerate(items)])

    def test_rollout_preserves_native_id_source_and_exact_completed_time(self):
        self.write(self.records())
        saved = self.store._history('thread', self.meta)
        row = saved['turns'][0]['items'][0]
        self.assertEqual(row['id'], 'native-reply')
        self.assertEqual(row['timestamp'], STAMP)
        self.assertEqual(row['_timestampSource'], 'rollout-item-completed')
        self.write(self.records(native_id=None))
        self.assertTrue(self.store._history('thread', self.meta)['turns'][0]['items'][0]['_syntheticId'])

    def test_index_retains_bounded_rollout_time_evidence_and_stat_invalidates_cache(self):
        self.write(self.records())
        self.make_index([{'id': 'native-reply', 'type': 'agentMessage', 'text': 'same reply'}])
        def read():
            with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as conn, conn:
                conn.execute('UPDATE thread_history_projection_state SET next_rollout_byte_offset=?', (self.path.stat().st_size,))
            with patch.object(self.store, '_recent_lines', side_effect=AssertionError('unbounded rollout reader')):
                return self.store._indexed_history('thread', self.meta, 40)['turns'][0]['items'][0]
        self.assertEqual(read().get('timestamp'), STAMP)
        self.write(self.records(completed=STAMP + 1000))
        self.assertEqual(read().get('timestamp'), STAMP + 1000)
        self.write(self.records(thread_id='other'))
        self.assertEqual(read().get('timestamp'), ISO)
        self.write(self.records(turn_id='other'))
        self.assertEqual(read().get('timestamp'), ISO)

    def test_index_never_joins_duplicate_ids_or_same_text_with_different_ids(self):
        self.write(self.records())
        self.make_index([{'id': 'native-reply', 'type': 'agentMessage', 'text': 'same reply'},
                         {'id': 'native-reply', 'type': 'agentMessage', 'text': 'same reply'},
                         {'id': 'different', 'type': 'agentMessage', 'text': 'same reply'}])
        rows = self.store._indexed_history('thread', self.meta, 40)['turns'][0]['items']
        self.assertTrue(all('timestamp' not in row for row in rows))

    def test_index_preserves_item_native_timestamp_and_rejects_repeated_rollout_keys(self):
        self.write(self.records())
        self.make_index([{'id': 'native-reply', 'type': 'agentMessage', 'text': 'reply', 'timestamp': STAMP - 1000}])
        row = self.store._indexed_history('thread', self.meta, 40)['turns'][0]['items'][0]
        self.assertEqual(row['timestamp'], STAMP - 1000)
        from bridge.features.sessions.store import rollout_message_times
        records = self.records()
        records.append(records[-1])
        self.write(records)
        self.assertEqual(rollout_message_times(self.path.read_bytes(), 'thread'), {})

    def test_index_time_scan_is_bounded_skips_partial_first_line_and_is_thread_scoped(self):
        line = (json.dumps(self.records()[-1]) + '\n').encode()
        self.path.write_bytes(b'incomplete-record' * 1000 + b'\n' + line)
        opened, sizes = Path.open, []
        class Reader:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def seek(self, offset): return self.stream.seek(offset)
            def read(self, size):
                sizes.append(size)
                return self.stream.read(size)
        with patch('bridge.features.sessions.store.ROLLOUT_TIME_BYTES', 512), \
                patch.object(Path, 'open', autospec=True, side_effect=lambda path, *args, **kwargs: Reader(opened(path, *args, **kwargs))):
            times = self.store._indexed_message_times('thread', self.path, self.path.stat())
            self.assertEqual(times[('turn', 'native-reply', 'agentMessage', 'assistant')][0], STAMP)
            self.assertEqual(self.store._indexed_message_times('thread', self.path, self.path.stat()), times)
            self.assertEqual(sizes, [512])
            self.assertEqual(self.store._indexed_message_times('other', self.path, self.path.stat()), {})
            self.assertEqual(sizes, [512, 512])

    def test_index_rejects_rollout_mutation_during_time_scan(self):
        self.write(self.records())
        self.make_index([{'id': 'native-reply', 'type': 'agentMessage', 'text': 'reply'}])
        read = self.store._indexed_message_times
        def changed(*args):
            result = read(*args)
            with self.path.open('ab') as stream:
                stream.write(b'\n')
            return result
        with patch.object(self.store, '_indexed_message_times', side_effect=changed):
            self.assertIsNone(self.store._indexed_history('thread', self.meta, 40))
