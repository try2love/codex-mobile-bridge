import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.store import SessionStore

ROOT = Path(__file__).resolve().parents[1]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.root = Path(self.temp.name)
        self.path = self.root / 'state_5.sqlite'
        db = sqlite3.connect(self.path)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, originator TEXT, source TEXT)')
        db.execute("INSERT INTO threads VALUES ('test', 'Saved chat', '/project', 1, 0, 'Codex Desktop', 'vscode')")
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        db.close()
        # All writers are closed and the fixture is checkpointed. Never do this
        # to a live Codex database.
        for suffix in ('-wal', '-shm'):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        self.store = SessionStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_checkpointed_wal_without_sidecars_is_readable_without_source_writes(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(self.store.list()[0]['title'], 'Saved chat')
        self.assertEqual(self.store.get('test')['cwd'], '/project')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual([p.name for p in self.root.iterdir()], ['state_5.sqlite'])

    def test_new_wal_commits_are_visible_after_snapshot_read(self):
        self.assertEqual(self.store.get('test')['title'], 'Saved chat')
        db = sqlite3.connect(self.path)
        try:
            db.execute("UPDATE threads SET title='Live update'")
            db.commit()
            self.assertEqual(self.store.get('test')['title'], 'Live update')
        finally:
            db.close()

    def test_snapshot_rejects_writer_starting_during_copy(self):
        import shutil
        from bridge.store import StoreUnavailable
        copy = shutil.copyfile
        writer = None
        def copy_then_write(source, target):
            nonlocal writer
            result = copy(source, target)
            writer = sqlite3.connect(self.path)
            writer.execute("UPDATE threads SET title='Concurrent update'")
            writer.commit()
            return result
        try:
            with patch('bridge.store.shutil.copyfile', side_effect=copy_then_write):
                with self.assertRaises(StoreUnavailable):
                    self.store.get('test')
            self.assertEqual(self.store.get('test')['title'], 'Concurrent update')
        finally:
            if writer:
                writer.close()

    def test_missing_database_reports_recoverable_error(self):
        from bridge.store import StoreUnavailable
        self.path.unlink()
        with self.assertRaises(StoreUnavailable):
            self.store.list()

    def test_corruption_does_not_use_snapshot_fallback(self):
        from bridge.store import StoreUnavailable
        self.path.write_bytes(b'not a database')
        with patch('bridge.store.shutil.copyfile') as copy:
            with self.assertRaises(StoreUnavailable):
                self.store.list()
            copy.assert_not_called()


class RecentHistoryTests(unittest.TestCase):
    def setUp(self):
        import json
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.home = Path(self.temp.name)
        (self.home/'sessions').mkdir()
        self.path = self.home/'sessions/rollout.jsonl'
        self.records = []
        for index in range(120):
            self.records.extend([
                {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': str(index)}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'text', 'text': '问题'+str(index)}]}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': '答'*24000}]}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete'}}])
        self.path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in self.records), encoding='utf-8')
        self.store = SessionStore(self.home)
        self.get = patch.object(self.store, 'get', return_value={'rollout_path': str(self.path), 'cwd': '/fixture'})
        self.get.start()

    def tearDown(self):
        self.get.stop(); self.temp.cleanup()

    def test_tail_matches_full_turns_across_unicode_and_chunk_boundaries(self):
        full = self.store.history('fixture')
        for limit in (1, 20, 70, 150):
            tail = self.store.history('fixture', limit)
            self.assertEqual(tail['turns'], full['turns'][-limit:])
            self.assertEqual(tail['turnsPagination']['hasLoadedOldest'], limit >= 120)

    def test_turn_context_labels_do_not_follow_later_thread_settings_or_cache(self):
        import json
        records=[]
        for index,model,effort in [('one','gpt-6-astra','max'),('two','gemini-pro','high')]:
            records.extend([
                {'type':'event_msg','payload':{'type':'task_started','turn_id':index}},
                {'type':'turn_context','payload':{'turn_id':index,'model':model,'effort':effort}},
                {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'text':index}]}},
                {'type':'event_msg','payload':{'type':'task_complete','turn_id':index}}])
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in records))
        meta={'rollout_path':str(self.path),'cwd':'/fixture','model':'next-model','model_provider':'next-provider'}
        first=self.store._history('fixture',meta)
        meta.update(model='changed-without-turn',model_provider='changed-provider')
        cached=self.store._history('fixture',meta)
        self.assertEqual(first['latestModel'],'next-model')
        self.assertEqual(cached['latestModel'],'changed-without-turn')
        self.assertEqual(cached['modelProvider'],'changed-provider')
        self.assertEqual([t['params'] for t in cached['turns']],
                         [{'model':'gpt-6-astra','effort':'max'},{'model':'gemini-pro','effort':'high'}])

    def test_saved_imageview_event_is_preserved_for_history_rendering(self):
        import json
        self.path.write_text('\n'.join(json.dumps(record, ensure_ascii=False) for record in [
            {'type':'event_msg','payload':{'type':'task_started','turn_id':'image-turn'}},
            {'type':'event_msg','payload':{'type':'item_completed','item':{'type':'imageView','id':'view','path':'file:///C:/fixture-preview.png'}}},
            {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'![preview](file:///C:/fixture-preview.png)'}]}},
            {'type':'event_msg','payload':{'type':'task_complete'}},
        ])+'\n', encoding='utf-8')
        state=self.store.history('fixture')
        kinds=[item.get('type') for item in state['turns'][0]['items']]
        self.assertIn('ImageView',kinds)
        from bridge.files import artifact_paths, referenced_model_images
        referenced=list(referenced_model_images(state))
        self.assertEqual(len(referenced),1)
        image=self.home/'preview.png';image.write_bytes(b'PNG')
        uri=image.as_uri()
        state['turns'][0]['items'][0]['path']=uri
        state['turns'][0]['items'][1]['text']=f'![preview]({image})'
        files=artifact_paths(state,self.home)
        self.assertIn(str(image),{item['reference'] for item in files.values()})

    def test_cache_does_not_share_mutations_and_invalidates_after_append(self):
        import json
        tail = self.store.history('fixture', 20)
        tail['turns'].clear()
        with patch.object(self.store, '_recent_lines', side_effect=AssertionError('reread')):
            self.assertEqual(len(self.store.history('fixture', 20)['turns']), 20)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type':'event_msg','payload':{'type':'task_started','turn_id':'new'}})+'\n')
        self.assertEqual(self.store.history('fixture', 20)['turns'][-1]['turnId'], 'new')

    def test_prepend_preserves_cursor_and_existing_order(self):
        from bridge.model import normalize_state
        from bridge.timeline import Timeline
        timeline = Timeline()
        tail = normalize_state(self.store.history('fixture', 20));tail['sequence'] = 1
        timeline.update(tail);page = timeline.page(limit=20)
        before = page['before'];orders = {row['key']: row['order'] for row in timeline.rows}
        expanded = normalize_state(self.store.history('fixture', 70));expanded['sequence'] = 2
        timeline.update(expanded)
        older = timeline.page(limit=100, before=before)
        self.assertFalse(older.get('reset', False))
        self.assertEqual({row['key']:row['order'] for row in timeline.rows if row['key'] in orders}, orders)
        self.assertLess(older['rows'][0]['order'], page['rows'][0]['order'])
