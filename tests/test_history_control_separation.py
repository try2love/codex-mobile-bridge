import json
import sqlite3
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

import test_bridge
from test_bridge import THREAD
from bridge.app.service import LiveSession
from bridge.features.sessions import model
from bridge.features.sessions.store import SessionStore
from bridge.features.workspace.files import path_identity


class HistoryControlTests(unittest.TestCase):
    setUp = test_bridge.IntegrationTests.setUp
    tearDown = test_bridge.IntegrationTests.tearDown

    def test_slow_saved_history_does_not_delay_owner_attachment(self):
        entered, release = threading.Event(), threading.Event()
        def history(*args, **kwargs):
            release.wait(3)
            return {'id': THREAD, 'turns': []}
        try:
            with patch.object(self.bridge, '_attach', side_effect=lambda session: entered.set()), \
                    patch.object(self.bridge.store, 'history', side_effect=history):
                self.bridge.session(THREAD, background=True)
                self.assertTrue(entered.wait(1))
        finally:
            release.set()

    def test_passive_catalog_and_media_share_saved_history_without_reloading(self):
        saved = {'id': THREAD, 'cwd': '/saved/project', 'turns': []}
        with patch.object(self.bridge.store, 'history', return_value=saved) as history:
            session = self.bridge.session(THREAD, attach=False)
            self.bridge.session(THREAD, attach=False)
            self.assertEqual(session.view()['cwd'], '/saved/project')
            self.assertIsNone(session.state)
            self.assertEqual(history.call_count, 1)

    def test_cached_pages_and_another_session_do_not_wait_for_owner(self):
        entered, release = threading.Event(), threading.Event()
        saved = {'id': THREAD, 'turns': [{'turnId': 'saved', 'items': [
            {'id': str(i), 'type': 'agentMessage', 'text': str(i)} for i in range(120)]}],
            'requests': [{'id': 'obsolete', 'method': 'item/commandExecution/requestApproval', 'params': {}}]}
        def attach(session):
            entered.set()
            release.wait(5)
        try:
            with patch.object(self.bridge, '_attach', side_effect=attach), patch.object(self.bridge.store, 'history', return_value=saved):
                session = self.bridge.session(THREAD, background=True)
                self.assertTrue(entered.wait(2))
                first = self.bridge.timeline_read(THREAD)
                older = self.bridge.timeline_read(THREAD, before=first['before'], limit=80)
                self.assertEqual(len(first['rows']), 20)
                self.assertEqual(len(older['rows']), 80)
                self.assertFalse(first['meta']['connected'])
                self.assertEqual(first['meta']['requests'], [])
                self.assertIsNone(session.state)
                other = LiveSession('other')
                with other.condition:
                    other.set_history({'id': 'other', 'turns': []})
                self.assertFalse(other.timeline_view()['loadingHistory'])
        finally:
            release.set()

    def test_native_snapshot_normalizes_only_loaded_bodies_and_keeps_control_base(self):
        state = self.fixture.state
        state['turns'] = [{'turnId': 'turn', 'items': [
            {'id': str(i), 'type': 'agentMessage', 'text': 'row ' + str(i), 'timestamp': 1700000000 + i} for i in range(10000)]}]
        state['requests'] = [{'id': 9, 'method': 'item/commandExecution/requestApproval', 'params': {'command': 'pwd'}}]
        session = self.bridge.session(THREAD)
        with patch.object(model, 'normalize_item', wraps=model.normalize_item) as normalize:
            first = self.bridge.timeline_read(THREAD)
            self.assertLessEqual(normalize.call_count, 40)
            again = self.bridge.timeline_read(THREAD)
            self.assertEqual(again['rows'], first['rows'])
            self.assertLessEqual(normalize.call_count, 40)
            older = self.bridge.timeline_read(THREAD, before=first['before'], limit=80)
            self.assertEqual(len(older['rows']), 80)
            self.assertLessEqual(normalize.call_count, 160)
        self.assertEqual(len(session.state['turns'][0]['items']), 10000)
        self.assertEqual(first['meta']['requests'][0]['id'], 9)
        self.assertEqual(session.owner, 'owner')

    def test_native_history_index_is_bounded_read_only_and_never_control_state(self):
        db = self.root / 'thread_history_1.sqlite'
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('CREATE TABLE thread_items(thread_id,turn_id,rollout_ordinal,item_json)')
            conn.execute('CREATE TABLE thread_turns(thread_id,turn_id,status,error_json,started_at,first_user_item_id)')
            conn.execute('CREATE TABLE thread_history_projection_state(thread_id,next_rollout_byte_offset)')
            conn.execute('INSERT INTO thread_history_projection_state VALUES(?,0)', (THREAD,))
            conn.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?)', (THREAD, 't', 'completed', None, 1, '0'))
            conn.executemany('INSERT INTO thread_items VALUES(?,?,?,?)', [
                (THREAD, 't', i, json.dumps({'id': str(i), 'type': 'agentMessage', 'text': str(i)})) for i in range(1000)])
        before = db.read_bytes()
        store = SessionStore(self.root)
        with patch.object(store, '_recent_lines', side_effect=AssertionError('full rollout read')):
            recent = store.history(THREAD, turn_limit=2, item_limit=40)
            older = store.history(THREAD, turn_limit=2, item_limit=140)
        self.assertEqual(len(recent['turns'][0]['items']), 40)
        self.assertEqual(older['turns'][0]['items'][-40:], recent['turns'][0]['items'])
        self.assertFalse(recent['turnsPagination']['hasLoadedOldest'])
        self.assertEqual(recent['threadRuntimeStatus']['type'], 'notLoaded')
        self.assertEqual(recent['requests'], [])
        self.assertEqual(before, db.read_bytes())
        # A native index for another/stale rollout must not hide new messages.
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('UPDATE thread_history_projection_state SET next_rollout_byte_offset=1')
        with patch.object(store, '_history', return_value={'fallback': True}) as fallback:
            self.assertEqual(store.history(THREAD, turn_limit=2, item_limit=40), {'fallback': True})
            fallback.assert_called_once()
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('UPDATE thread_history_projection_state SET next_rollout_byte_offset=0')
            conn.execute('UPDATE thread_items SET item_json=? WHERE rollout_ordinal=999',
                         (json.dumps({'type': 'function_call_output', 'call_id': 'outside-page', 'output': 'result'}),))
        with patch.object(store, '_history', return_value={'pairedFallback': True}):
            self.assertEqual(store.history(THREAD, turn_limit=2, item_limit=40), {'pairedFallback': True})

    def test_presentation_duplicate_keys_survive_expanding_window(self):
        session = LiveSession('test')
        session.state = {'id': 'test', 'turns': [{'turnId': 't', 'items': [
            {'type': 'agentMessage', 'text': str(i)} for i in range(100)]}]}
        session.connected = True
        with session.condition:
            session.timeline.update(session.timeline_view(), limit=session.timeline_limit)
            first = session.timeline.page()
            session.timeline_limit += 40
            session.timeline.update(session.timeline_view(), limit=session.timeline_limit)
            self.assertIsNotNone(session.timeline.position(first['before']))
            self.assertEqual(session.timeline.page()['rows'], first['rows'])

    def test_tool_result_at_boundary_retains_call_and_stable_cursor(self):
        session = LiveSession('test')
        session.connected = True
        session.state = {'id': 'test', 'turns': [{'turnId': 't', 'items': [
            {'type': 'agentMessage', 'text': 'older'} for _ in range(40)] + [
            {'type': 'function_call', 'call_id': 'call', 'name': 'read', 'arguments': 'file'},
            {'type': 'function_call_output', 'call_id': 'call', 'output': 'result'},
        ] + [{'id': str(i), 'type': 'agentMessage', 'text': str(i)} for i in range(39)]}]}
        with session.condition:
            view = session.timeline_view()
            tool = view['turns'][0]['messages'][0]
            self.assertEqual((tool['kind'], tool['output']), ('storedToolEvent', 'result'))
            session.timeline.update(view, limit=40)
            first = session.timeline.page()
            session.timeline_limit += 40
            session.timeline.update(session.timeline_view(), limit=session.timeline_limit)
            self.assertIsNotNone(session.timeline.position(first['before']))
            self.assertEqual(session.timeline.page()['rows'], first['rows'])

    def test_carried_tool_call_does_not_expand_window_on_next_live_update(self):
        session = LiveSession('test')
        session.connected = True
        session.state = {'id': 'test', 'turns': [{'turnId': 't', 'items': [
            {'type': 'function_call', 'call_id': 'call', 'name': 'read', 'arguments': 'file'}
        ] + [{'id': str(i), 'type': 'agentMessage', 'text': str(i)} for i in range(1000)] + [
            {'type': 'function_call_output', 'call_id': 'call', 'output': 'result'}]}]}
        with session.condition:
            session.timeline.update(session.timeline_view(), limit=40)
            session.changed()
            with patch.object(model, 'normalize_item', wraps=model.normalize_item) as normalize:
                session.timeline.update(session.timeline_view(), limit=session.timeline_limit)
                self.assertLessEqual(normalize.call_count, 40)
                self.assertEqual(session.timeline_limit, 40)

    def test_disconnected_media_uses_current_saved_history_not_old_runtime(self):
        import hashlib
        old, current = self.root / 'old.png', self.root / 'current.png'
        old.write_bytes(b'old');current.write_bytes(b'current')
        def state(path):
            return {'id': THREAD, 'cwd': str(self.root), 'turns': [{'turnId': 't', 'items': [
                {'id': 'image', 'type': 'agentMessage', 'text': '![image](' + str(path) + ')'}]}]}
        session = self.bridge.session(THREAD)
        with session.condition:
            session.state = state(old)
            session.connected = False
            session.set_history(state(current))
        key = lambda path: hashlib.sha256(str(path_identity(path)).encode()).hexdigest()
        self.assertEqual(self.bridge.artifact(THREAD, key(current))['path'], current)
        with self.assertRaises(KeyError):
            self.bridge.artifact(THREAD, key(old))

    def test_index_without_model_labels_does_not_erase_live_per_turn_model(self):
        session = LiveSession('test')
        turn = {'turnId': 't', 'status': 'completed', 'items': [], 'model': 'historical-model'}
        session.state = {'id': 'test', 'turns': [turn]}
        session.connected = True
        with session.condition:
            session.set_history({'id': 'test', 'historyReadSource': 'native-index',
                                 'turns': [{**turn, 'model': None}]})
            self.assertEqual(session.view()['turns'][0]['model'], 'historical-model')


if __name__ == '__main__':
    unittest.main()
