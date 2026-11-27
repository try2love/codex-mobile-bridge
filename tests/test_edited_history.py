"""An edit keeps only the prefix named by the new rollout's history_base."""
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from bridge.features.sessions.store import SessionStore


class EditedHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        directory = self.home / 'sessions'
        directory.mkdir()
        self.store = SessionStore(self.home)
        self.thread, child = str(uuid.uuid4()), str(uuid.uuid4())
        self.child = child
        self.root = directory / ('rollout-date-' + self.thread + '.jsonl')
        self.path = directory / ('rollout-date-' + self.thread + '_' + child + '.jsonl')
        header = {'type': 'session_meta', 'payload': {'id': self.thread, 'session_id': self.thread}}
        def turn(key):
            return [{'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': key}},
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                     'content': [{'type': 'text', 'text': key}]}}]
        def lines(records):
            return b''.join(json.dumps(record).encode() + b'\n' for record in records)
        prefix = lines([header, *turn('earlier'), *turn('keep')])
        self.cut = len(prefix)
        self.root.write_bytes(prefix + lines(turn('replaced')))
        header['payload']['history_base'] = {'thread_id': self.thread, 'end_byte_offset': self.cut,
                                             'end_ordinal_exclusive': 5}
        self.path.write_bytes(lines([header, *turn('edited')]))
        self.meta = {'rollout_path': str(self.path), 'cwd': '/test'}
        self.store.get = lambda _: self.meta

    def test_rollout_backfill_stops_at_the_edit_boundary(self):
        for count, expected in ((1, ['edited']), (2, ['keep', 'edited']), (8, ['earlier', 'keep', 'edited'])):
            snapshot = self.store.history(self.thread, turn_limit=count)
            self.assertEqual([t['turnId'] for t in snapshot['turns']], expected)
            self.assertEqual(snapshot['turnsPagination']['hasLoadedOldest'], count == 8)

    def test_index_backfill_uses_physical_keys_and_excludes_the_replaced_turn(self):
        db = sqlite3.connect(self.home / 'thread_history_1.sqlite')
        self.addCleanup(db.close)
        db.executescript('''
            CREATE TABLE thread_items(thread_id,turn_id,rollout_ordinal,updated_at_ordinal,item_json);
            CREATE TABLE thread_turns(thread_id,turn_id,status,error_json,started_at,first_user_item_id,
                                      rollout_ordinal,rollout_byte_offset);
            CREATE TABLE thread_history_projection_state(thread_id,next_rollout_byte_offset);
        ''')
        with db:
            for key, size in ((self.child, self.path.stat().st_size), (self.thread, self.root.stat().st_size)):
                db.execute('INSERT INTO thread_history_projection_state VALUES(?,?)', (key, size))
            for key, turn, ordinal in ((self.thread,'earlier',1),(self.thread,'keep',3),
                                       (self.thread,'replaced',5),(self.child,'edited',6)):
                item = {'id': turn, 'type': 'userMessage', 'content': [{'type':'text','text':turn}]}
                db.execute('INSERT INTO thread_items VALUES(?,?,?,?,?)', (key,turn,ordinal,ordinal,json.dumps(item)))
                db.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?,?,?)',
                           (key,turn,'completed',None,1,turn,ordinal,self.cut if turn=='replaced' else 0))
        for size in (2, 5):
            result = self.store.history(self.thread, turn_limit=1, item_limit=size)
            self.assertEqual(result['historyReadSource'], 'native-index')
            self.assertEqual([t['turnId'] for t in result['turns']], ['earlier','keep','edited'][-size:])
            self.assertEqual(result['turnsPagination']['hasLoadedOldest'], size == 5)
        # An unrelated or stale projection cannot supply the retained prefix.
        with db:
            db.execute("UPDATE thread_turns SET rollout_byte_offset=1 WHERE turn_id='replaced'")
        result = self.store.history(self.thread, turn_limit=2, item_limit=2)
        self.assertNotIn('historyReadSource', result)
        self.assertEqual([t['turnId'] for t in result['turns']], ['keep', 'edited'])
