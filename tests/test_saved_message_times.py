import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge, LiveSession
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

    def test_connected_view_enriches_terminal_turn_clocks_only_for_unique_matching_native_turns(self):
        saved = self.state()
        saved['turns'][0].update(startedAt=ISO, completedAt='2026-11-10T00:02:05Z')
        turn = self.join(saved, self.state())['turns'][0]
        self.assertEqual((turn['startedAt'], turn['completedAt']), (ISO, '2026-11-10T00:02:05Z'))
        live = self.state()
        live['turns'][0].update(turnStartedAtMs=STAMP, turnCompletedAtMs=STAMP + 1000)
        self.assertEqual(self.join(saved, live)['turns'][0]['completedAt'], STAMP + 1000)
        for status in ('inProgress', 'interrupted'):
            live = self.state()
            live['turns'][0]['status'] = status
            self.assertIsNone(self.join(saved, live)['turns'][0]['completedAt'])
        saved['turns'] *= 2
        self.assertIsNone(self.join(saved, self.state())['turns'][0]['completedAt'])

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

    def test_connected_reply_refreshes_new_native_clock_without_reconnecting(self):
        session = LiveSession('thread')
        with session.condition:
            session.set_history(self.state())
            session.state = self.state()
            session.connected = True
        native = copy.deepcopy(session.state)
        bridge = object.__new__(Bridge)
        bridge.store = SimpleNamespace()
        bridge.closed = threading.Event()
        bridge._saved_history = Mock(return_value=self.state(timestamp=STAMP))
        bridge._reconcile_submissions = Mock()
        # Execute the existing background callback deterministically.
        worker = lambda *, target, daemon: SimpleNamespace(start=target)
        with patch('bridge.app.service.threading.Thread', side_effect=worker):
            bridge._refresh_saved_async(session)
            self.assertEqual(session.timeline_view()['turns'][0]['messages'][0].get('timestamp'), STAMP)
            bridge._refresh_saved_async(session)
        self.assertEqual(bridge._saved_history.call_count, 1)
        self.assertEqual(session.state, native)
        self.assertTrue(session.connected)

    def test_missing_live_clock_retries_are_bounded_and_new_turn_can_retry(self):
        session = LiveSession('thread')
        with session.condition:
            session.set_history(self.state())
            session.state = self.state()
            session.connected = True
        bridge = object.__new__(Bridge)
        bridge.store = SimpleNamespace()
        bridge.closed = threading.Event()
        bridge._saved_history = Mock(return_value=self.state())
        bridge._reconcile_submissions = Mock()
        worker = lambda *, target, daemon: SimpleNamespace(start=target)
        with patch('bridge.app.service.threading.Thread', side_effect=worker), patch('bridge.app.service.time.monotonic', return_value=100):
            bridge._refresh_saved_async(session)
            bridge._refresh_saved_async(session)
            self.assertEqual(bridge._saved_history.call_count, 1)
            for _ in range(8):
                session.history_retry_at = 0
                bridge._refresh_saved_async(session)
            self.assertEqual(bridge._saved_history.call_count, 4)
            with session.condition:
                session.state = self.state(item_id='next-reply', turn_id='next-turn')
                session.changed()
            bridge._saved_history.return_value = self.state(item_id='next-reply', turn_id='next-turn', timestamp=STAMP)
            bridge._refresh_saved_async(session)
            self.assertEqual(bridge._saved_history.call_count, 5)
            self.assertEqual(session.view()['turns'][0]['messages'][0].get('timestamp'), STAMP)

    def test_recorded_running_model_can_arrive_after_its_message_clock(self):
        live = self.state(timestamp=STAMP); live['turns'][0]['status'] = 'inProgress'
        saved = copy.deepcopy(live)
        saved['turns'][0]['_recordedReplyLabel'] = {'model': 'recorded-running', 'effort': 'high'}
        session = LiveSession('thread')
        with session.condition:
            session.set_history(live); session.state = copy.deepcopy(live); session.connected = True
        bridge = object.__new__(Bridge); bridge.store = SimpleNamespace(); bridge.closed = threading.Event()
        bridge._saved_history = Mock(return_value=saved); bridge._reconcile_submissions = Mock()
        worker = lambda *, target, daemon: SimpleNamespace(start=target)
        with patch('bridge.app.service.threading.Thread', side_effect=worker):
            bridge._refresh_saved_async(session)
        turn = session.view()['turns'][0]
        self.assertEqual(turn['_recordedReplyLabel'], {'model': 'recorded-running', 'effort': 'high'})
        self.assertEqual(turn['messages'][0]['timestamp'], STAMP)
        self.assertEqual(session.state, live)
        self.assertEqual(bridge._saved_history.call_count, 1)


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

    def test_indexed_replies_recover_exact_turn_labels_outside_rollout_tail(self):
        source, offsets = [], {}
        for number, label in enumerate(({'model': 'first-model', 'effort': 'max'},
                                       {'model': 'second-model', 'effort': None,
                                        'collaboration_mode': {'settings': {'reasoning_effort': 'high'}}},
                                       {})):
            turn = f'turn-{number}'
            offsets[turn] = sum(map(len, source))
            events = [{'type': 'event_msg', 'payload': {'type': 'task_started', 'thread_id': 'thread', 'turn_id': turn}},
                      {'type': 'turn_context', 'payload': {'thread_id': 'thread', 'turn_id': turn, **label}},
                      {'type': 'irrelevant', 'payload': {'content': 'x' * 2048}}]
            source.extend((json.dumps(event) + '\n').encode() for event in events)
        self.path.write_bytes(b''.join(source))
        self.make_index([])
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as conn, conn:
            conn.execute('DELETE FROM thread_turns')
            conn.execute('ALTER TABLE thread_turns ADD COLUMN rollout_byte_offset')
            for number, (turn, offset) in enumerate(offsets.items()):
                conn.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?,?)',
                             ('thread', turn, 'completed', None, None, None, offset))
                conn.execute('INSERT INTO thread_items VALUES(?,?,?,?)',
                             ('thread', turn, number, json.dumps({'id': turn, 'type': 'agentMessage', 'text': turn})))
        with patch('bridge.features.sessions.store.ROLLOUT_TIME_BYTES', 128):
            state = self.store._indexed_history('thread', self.meta, 20)
        self.assertEqual(state['historyReadSource'], 'native-index')
        self.assertEqual([turn['_recordedReplyLabel'] for turn in state['turns']],
                         [{'model': 'first-model', 'effort': 'max'}, {'model': 'second-model', 'effort': None},
                          {'model': None, 'effort': None}])

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

    def test_native_index_clocks_do_not_depend_on_recent_rollout_tail(self):
        self.write(self.records() + [{'type': 'irrelevant', 'payload': {'text': 'x' * 4096}}])
        self.make_index([{'id': 'native-reply', 'type': 'agentMessage', 'text': 'same reply'}])
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as conn, conn:
            for name, kind in [('item_id', 'TEXT'), ('item_type', 'TEXT'), ('started_at_ms', 'INTEGER'), ('completed_at_ms', 'INTEGER')]:
                conn.execute(f'ALTER TABLE thread_items ADD COLUMN {name} {kind}')
            conn.execute('UPDATE thread_items SET item_id=?,item_type=?,started_at_ms=?,completed_at_ms=?',
                         ('native-reply', 'AgentMessage', STAMP - 1000, STAMP))
        with patch('bridge.features.sessions.store.ROLLOUT_TIME_BYTES', 64), \
                patch.object(self.store, '_recent_lines', side_effect=AssertionError('no full rollout scan')):
            row = self.store._indexed_history('thread', self.meta, 1)['turns'][0]['items'][0]
        self.assertEqual(row.get('timestamp'), STAMP)
        self.assertEqual(row.get('_timestampSource'), 'native-index-item-completed')
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as conn, conn:
            conn.execute('UPDATE thread_items SET item_id=?', ('different-native-id',))
        with patch('bridge.features.sessions.store.ROLLOUT_TIME_BYTES', 64):
            row = self.store._indexed_history('thread', self.meta, 1)['turns'][0]['items'][0]
        self.assertNotIn('timestamp', row, 'native clock identity must agree with the selected message')

    def test_reply_metadata_survives_a_one_item_body_window_and_running_turn(self):
        source, offsets = [], {}
        for turn, model in [('older', 'old-recorded-model'), ('running', 'running-recorded-model')]:
            offsets[turn] = sum(map(len, source))
            records = [{'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn}},
                       {'type': 'turn_context', 'payload': {'turn_id': turn, 'model': model, 'effort': 'high'}}]
            source.extend((json.dumps(row) + '\n').encode() for row in records)
        self.path.write_bytes(b''.join(source))
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as conn, conn:
            conn.execute('CREATE TABLE thread_items(thread_id,turn_id,rollout_ordinal,item_json,item_id,item_type,started_at_ms,completed_at_ms)')
            conn.execute('CREATE TABLE thread_turns(thread_id,turn_id,status,error_json,started_at,first_user_item_id,rollout_byte_offset)')
            conn.execute('CREATE TABLE thread_history_projection_state(thread_id,next_rollout_byte_offset)')
            conn.execute('INSERT INTO thread_history_projection_state VALUES(?,?)', ('thread', self.path.stat().st_size))
            for ordinal, (turn, offset) in enumerate(offsets.items()):
                status = 'inProgress' if turn == 'running' else 'completed'
                item = {'id': turn + '-reply', 'type': 'agentMessage', 'text': 'Reply ' + turn, 'phase': 'commentary'}
                conn.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?,?)', ('thread', turn, status, None, None, None, offset))
                conn.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?,?)',
                             ('thread', turn, ordinal, json.dumps(item) if turn == 'running' else '{unread older body', item['id'], 'AgentMessage', STAMP - 1000, STAMP + ordinal))
        saved = self.store._indexed_history('thread', self.meta, 1)
        self.assertEqual(len(saved['turns']), 1, 'the body remains bounded to one item')
        live = {'id': 'thread', 'turns': [
            {'turnId': turn, 'status': 'inProgress' if turn == 'running' else 'completed',
             'items': [{'id': turn + '-reply', 'type': 'agentMessage', 'text': 'Reply ' + turn, 'phase': 'commentary'}]}
            for turn in offsets]}
        session = LiveSession('thread'); session.timeline_limit = 1
        with session.condition:
            session.set_history(saved); session.state = live; session.connected = True
            view = session.view()
        from bridge.features.sessions.timeline import Timeline
        timeline = Timeline(); timeline.update({**view, 'sequence': 1})
        self.assertEqual([(row.get('model'), row.get('effort'), row.get('timestamp')) for row in timeline.rows],
                         [('old-recorded-model', 'high', STAMP), ('running-recorded-model', 'high', STAMP + 1)])
        self.assertNotIn('timestamp', live['turns'][0]['items'][0], 'presentation cannot patch the owner state')
        from bridge.features.sessions.model import normalize_state
        from bridge.features.sessions import sync
        before = Timeline(); before.update({**normalize_state(live), 'sequence': 1})
        for key in before.process_groups:
            cached = sync.process(before, key=key)
            updated = sync.process(timeline, key=key, known={row['key']: row['version'] for row in cached['index']})
            self.assertEqual(updated['rows'], [], 'metadata arrival must reuse the cached message body')
            self.assertTrue(updated['index'][0].get('timestamp'))
            self.assertTrue(updated['index'][0].get('model'))

    def test_body_page_metadata_is_not_lost_outside_adjacent_metadata_window(self):
        self.write(self.records())
        items = [{'id': f'native-{i}', 'type': 'agentMessage', 'text': str(i), 'timestamp': STAMP + i}
                 for i in range(140)]
        self.make_index(items)
        saved = self.store._indexed_history('thread', self.meta, 140)
        session = LiveSession('thread'); session.timeline_limit = 1
        with session.condition:
            session.set_history(saved)
            session.state = {'id': 'thread', 'turns': [{'turnId': 'turn', 'status': 'completed',
                'items': [{k: v for k, v in item.items() if k != 'timestamp'} for item in items]}]}
            session.connected = True
            view = session.view()
        self.assertEqual([row.get('timestamp') for row in view['turns'][0]['messages']],
                         [STAMP + i for i in range(140)])

    def test_terminal_event_clocks_survive_rollout_tail_index_and_cache_without_using_last_message(self):
        records = self.records()
        records[0]['timestamp'] = ISO
        completion = '2026-11-10T00:02:05Z'
        records.append({'type': 'event_msg', 'timestamp': completion,
                        'payload': {'type': 'task_complete', 'turn_id': 'turn', 'thread_id': 'thread'}})
        self.write(records)
        self.make_index([{'id': 'native-reply', 'type': 'agentMessage', 'text': 'same reply'}])
        for turn_limit in (None, 1):
            with self.subTest(turn_limit=turn_limit):
                self.assertEqual(self.store._history('thread', self.meta, turn_limit)['turns'][0]['completedAt'], completion)
        with patch.object(self.store, '_recent_lines', side_effect=AssertionError('unbounded rollout read')):
            for _ in range(2):
                indexed = self.store._indexed_history('thread', self.meta, 1)
                self.assertEqual(indexed['turns'][0]['completedAt'], completion)
        self.write(records[:-1])
        saved = self.store._history('thread', self.meta)
        self.assertNotIn('completedAt', saved['turns'][0])

    def test_index_converts_native_seconds_before_late_clock_enrichment(self):
        from bridge.features.sessions.model import normalize_state
        from bridge.features.sessions.timeline import Timeline
        completion = '2026-11-10T00:02:05Z'
        self.write(self.records() + [{'type': 'event_msg', 'timestamp': completion,
            'payload': {'type': 'task_complete', 'turn_id': 'turn', 'thread_id': 'thread'}}])
        self.make_index([{'id': 'native-reply', 'type': 'agentMessage', 'text': 'same reply'}])
        timeline = Timeline()
        previous = None
        for sequence, seconds in enumerate((None, 0, STAMP // 1000), 1):
            with self.subTest(seconds=seconds):
                with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as conn, conn:
                    conn.execute('UPDATE thread_turns SET started_at=?', (seconds,))
                state = self.store._indexed_history('thread', self.meta, 1)
                timeline.update({**normalize_state(state), 'sequence': sequence})
                page = timeline.page(1)
                row = page['rows'][0]
                self.assertEqual(row.get('turnStartedAt'), STAMP if seconds else None)
                self.assertEqual(row['turnCompletedAt'], completion)
                if seconds:
                    delta = timeline.changes(previous['sequence'], previous['epoch'], previous['before'])
                    self.assertEqual(delta['rows'][0]['turnStartedAt'], STAMP)
                    self.assertEqual(delta['rows'][0]['key'], previous['rows'][0]['key'])
                    self.assertEqual(delta['rows'][0]['version'], previous['rows'][0]['version'])
                previous = page

    def test_terminal_event_joins_only_its_native_turn_and_thread(self):
        from bridge.features.sessions.store import rollout_message_times
        terminal = {'type': 'event_msg', 'timestamp': ISO,
                    'payload': {'type': 'turn_aborted', 'turn_id': 'turn', 'thread_id': 'thread'}}
        for payload in ({'turn_id': 'other'}, {'thread_id': 'other'}):
            self.write(self.records() + [{**terminal, 'payload': {**terminal['payload'], **payload}}])
            self.assertNotIn('completedAt', self.store._history('thread', self.meta)['turns'][0])
        self.write(self.records() + [terminal])
        saved = self.store._history('thread', self.meta)['turns'][0]
        self.assertEqual((saved['status'], saved['completedAt']), ('interrupted', ISO))
        self.write([terminal, terminal])
        self.assertEqual(rollout_message_times(self.path.read_bytes(), 'thread', True)[1], {})
        self.write([{**terminal, 'payload': {'type': 'task_complete'}}])
        self.assertEqual(rollout_message_times(self.path.read_bytes(), 'thread', True)[1], {})

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
        terminal = {'type': 'event_msg', 'timestamp': ISO,
                    'payload': {'type': 'task_complete', 'turn_id': 'turn', 'thread_id': 'thread'}}
        line = (json.dumps(self.records()[-1]) + '\n' + json.dumps(terminal) + '\n').encode()
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
            messages, completed = self.store._indexed_message_times('thread', self.path, self.path.stat(), True)
            self.assertEqual(messages, times)
            self.assertEqual(completed, {'turn': ISO})
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
