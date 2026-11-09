import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from bridge.features.sessions.store import SessionStore

ROOT = Path(__file__).resolve().parents[1]


class StoreTests(unittest.TestCase):
    def test_attached_children_are_grouped_only_under_accessible_native_parent(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('ALTER TABLE threads ADD COLUMN attached_thread_parent_id TEXT')
            conn.execute('ALTER TABLE threads ADD COLUMN forked_from_id TEXT')
            conn.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)', [
                ('side', 'Native side', '/project', 20, 0, 'Codex Desktop', 'vscode', 'test', None),
                ('archived-side', 'Saved side', '/project', 19, 1, 'Codex Desktop', 'vscode', 'test', None),
                ('ordinary-fork', 'Ordinary fork', '/project', 18, 0, 'Codex Desktop', 'vscode', None, 'test'),
                ('orphan', 'Orphan', '/project', 17, 0, 'Codex Desktop', 'vscode', 'missing', None),
                ('ide-parent', 'IDE parent', '/other', 16, 0, 'codex_vscode', 'vscode', None, None),
                ('other-parent-side', 'Unlisted parent', '/project', 15, 0, 'Codex Desktop', 'vscode', 'ide-parent', None),
            ])
        rows = self.store.list(exclude_sources=('ide',))
        self.assertEqual([row['id'] for row in rows], ['ordinary-fork', 'orphan', 'other-parent-side', 'test'])
        self.assertEqual(self.store.list(exclude_sources=('ide',), limit=1)[0]['id'], 'ordinary-fork')
        self.assertTrue(next(row for row in rows if row['id'] == 'orphan')['orphanedSideChat'])
        self.assertEqual({row['id'] for row in self.store.attached_children('test')}, {'side', 'archived-side'})
        self.assertEqual(self.store.attached_parent('side'), {'id': 'test', 'available': True, 'title': 'Saved chat'})
        self.assertEqual(self.store.attached_parent('orphan'), {'id': 'missing', 'available': False})
        self.assertIsNone(self.store.attached_parent('ordinary-fork'))
        self.assertEqual(SessionStore(self.root, source='desktop').attached_parent('other-parent-side'),
                         {'id': 'ide-parent', 'available': False})

    def test_attached_and_spawned_descendants_both_belong_to_lifecycle_scope(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('ALTER TABLE threads ADD COLUMN attached_thread_parent_id TEXT')
            conn.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)', [
                ('side', 'Side', '/project', 2, 1, 'Codex Desktop', 'vscode', 'test'),
                ('nested', 'Nested side', '/project', 3, 0, 'Codex Desktop', 'vscode', 'side'),
                ('worker', 'Worker', '/project', 4, 0, 'Codex Desktop', '{"subagent":{"thread_spawn":{"parent_thread_id":"nested"}}}', None),
            ])
        self.assertEqual({row['id'] for row in self.store.lifecycle_threads(root_id='test')},
                         {'test', 'side', 'nested', 'worker'})
        self.assertEqual(self.store.existing_ids({'test', 'worker', 'missing'}), {'test', 'worker'})

    def test_older_store_has_no_attached_chats(self):
        self.assertEqual(self.store.attached_children('test'), [])
        self.assertIsNone(self.store.attached_parent('test'))

    def test_native_project_assignment_and_clear_are_read_from_the_database(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('ALTER TABLE threads ADD COLUMN project_id TEXT')
            conn.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT)')
            conn.execute("INSERT INTO projects VALUES ('native','Native project')")
            conn.execute("UPDATE threads SET project_id='native'")
        self.assertEqual(self.store.list()[0]['_nativeProject'], {'id': 'native', 'name': 'Native project'})
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('UPDATE threads SET project_id=NULL')
        self.assertIsNone(self.store.list()[0]['_nativeProject'])

    def test_native_cli_metadata_controls_read_list_and_source_filters(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE threads ADD COLUMN thread_source TEXT')
            db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)', [
                ('created', 'Created in profile', '/project', 5, 0, 'Codex Desktop', 'vscode', 'cli'),
                ('ide', 'VS Code', '/project', 6, 0, 'codex_vscode', 'vscode', 'cli'),
                ('legacy', 'Old desktop', '/project', 2, 0, None, 'vscode', None),
                ('child', 'Worker', '/project', 7, 0, 'Codex Desktop', 'vscode', 'subagent'),
            ])
        self.assertEqual(self.store.get('created')['origin'], 'cli')
        self.assertEqual(self.store.get('ide')['origin'], 'ide')
        self.assertEqual([r['id'] for r in self.store.list(source='cli')], ['created'])
        self.assertEqual({r['id'] for r in self.store.list(source='desktop')}, {'test', 'legacy'})
        self.assertEqual([r['id'] for r in self.store.list(exclude_sources=('ide',), limit=1)], ['created'])
        self.assertEqual({r['id'] for r in self.store.list(exclude_sources=('ide',))}, {'test', 'legacy', 'created'})
        self.assertEqual(self.store.available_sources(), ['cli', 'desktop', 'ide'])

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
        from bridge.features.sessions.store import StoreUnavailable
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
            with patch('bridge.features.sessions.store.shutil.copyfile', side_effect=copy_then_write):
                with self.assertRaises(StoreUnavailable):
                    self.store.get('test')
            self.assertEqual(self.store.get('test')['title'], 'Concurrent update')
        finally:
            if writer:
                writer.close()

    def test_missing_database_reports_recoverable_error(self):
        from bridge.features.sessions.store import StoreUnavailable
        self.path.unlink()
        with self.assertRaises(StoreUnavailable):
            self.store.list()

    def test_corruption_does_not_use_snapshot_fallback(self):
        from bridge.features.sessions.store import StoreUnavailable
        self.path.write_bytes(b'not a database')
        with patch('bridge.features.sessions.store.shutil.copyfile') as copy:
            with self.assertRaises(StoreUnavailable):
                self.store.list()
            copy.assert_not_called()

    def test_cli_discovery_read_and_sanitized_diagnostics(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?)', [
                ('tui', 'CLI private title', '/private/user', 3, 0, 'codex-tui', 'vscode'),
                ('cli', 'CLI private title', '/private/user', 4, 0, 'codex_cli_rs', 'cli'),
                ('other', 'Other', '/private/user', 5, 0, 'unknown', 'cli'),
                ('child', 'Child', '/private/user', 6, 0, 'codex_cli_rs', '{"subagent":{}}'),
                ('archived', 'Archive', '/private/user', 7, 1, 'codex_cli_rs', 'cli'),
                ('tui-cli', 'CLI', '/private/user', 8, 0, 'codex-tui', 'cli'),
                ('rs-vscode', 'CLI', '/private/user', 9, 0, 'codex_cli_rs', 'vscode'),
                ('legacy-cli', 'CLI', '/private/user', 10, 0, None, 'cli'),
                ('bridge-cli', 'CLI', '/private/user', 11, 0, 'codex-mobile-bridge-cli', 'vscode'),
                ('bridge-other', 'Other', '/private/user', 12, 0, 'codex-mobile-bridge', 'vscode'),
            ])
        rows = self.store.list()
        self.assertEqual({r['id']: r['origin'] for r in rows},
                         {'test': 'desktop', 'tui': 'cli', 'cli': 'cli', 'tui-cli': 'cli',
                          'rs-vscode': 'cli', 'legacy-cli': 'cli', 'bridge-cli': 'cli'})
        self.assertEqual(self.store.get('tui')['origin'], 'cli')
        self.assertEqual(self.store.get('cli')['origin'], 'cli')
        self.assertEqual(self.store.get('legacy-cli')['origin'], 'cli')
        self.assertEqual({r['id'] for r in self.store.notification_changes()},
                         {'test', 'tui', 'cli', 'archived', 'tui-cli', 'rs-vscode', 'legacy-cli', 'bridge-cli'})
        self.assertEqual([r['id'] for r in self.store.list(archived=True)], ['archived'])
        for thread_id in ('other', 'child', 'bridge-other'):
            with self.assertRaises(KeyError):
                self.store.get(thread_id)
        self.assertEqual({r['id'] for r in self.store.lifecycle_threads()}, {'test'})
        report = self.store.discovery_diagnostics('cli')
        self.assertEqual(report['counts'], {'desktop': 1, 'cli': 7, 'ide': 0, 'other': 2, 'subagent': 1,
                                            'visible': 7, 'archived': 1})
        self.assertEqual(report['target'], {'found': True, 'origin': 'cli', 'archived': False,
                                            'reason': 'rollout_missing', 'read': 'unavailable'})
        self.assertTrue(self.store.discovery_diagnostics('archived')['target']['archived'])
        self.assertNotIn('/private/user', str(report))
        self.assertNotIn('CLI private title', str(report))
        self.assertEqual(self.store.discovery_diagnostics('absent')['target']['reason'], 'missing')

    def test_legacy_mobile_app_thread_is_exclusive_to_app_filter_outside_wsl_store(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?)', [
                ('mobile-app', 'App mobile thread', '/project', 2, 0, 'codex_mobile_bridge', 'vscode'),
                ('native-cli', 'CLI thread', '/project', 3, 0, 'codex-tui', 'cli')])
        desktop = self.store.list(source='desktop')
        cli = self.store.list(source='cli')
        self.assertEqual({row['id'] for row in desktop}, {'test', 'mobile-app'})
        self.assertEqual({row['id'] for row in cli}, {'native-cli'})
        self.assertTrue(all(row['origin'] == 'desktop' for row in desktop))
        self.assertTrue(all(row['origin'] == 'cli' for row in cli))
        wsl_store = SessionStore(self.root, source='cli')
        wsl_rows = wsl_store.list(source='cli')
        self.assertEqual({row['id'] for row in wsl_rows}, {'mobile-app', 'native-cli'})
        self.assertTrue(all(row['origin'] == 'cli' for row in wsl_rows))
        self.assertEqual(wsl_store.get('mobile-app')['origin'], 'cli')

    def test_source_only_schema_has_same_list_and_get_eligibility(self):
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, source TEXT)')
            db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?)', [
                ('desktop', 'Desktop', '/fixture', 1, 0, 'vscode'),
                ('cli', 'CLI', '/fixture', 2, 0, 'cli'),
                ('agent', 'Agent', '/fixture', 3, 0, '{"subagent":{}}'),
                ('unknown', 'Unknown', '/fixture', 4, 0, 'other'),
            ])
        rows = self.store.list()
        self.assertEqual({r['id']: r['origin'] for r in rows}, {'desktop': 'desktop', 'cli': 'cli'})
        self.assertEqual(self.store.available_sources(), ['cli', 'desktop'])
        self.assertEqual([r['id'] for r in self.store.list(source='desktop')], ['desktop'])
        for row in rows:
            self.assertEqual(self.store.get(row['id'])['origin'], row['origin'])
        self.assertEqual({r['id'] for r in self.store.notification_changes()}, {'desktop', 'cli'})
        self.assertEqual(self.store.discovery_diagnostics('cli')['target']['origin'], 'cli')

    def test_inventory_tolerates_schema_without_source_columns(self):
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE threads(id TEXT, title TEXT, updated_at INT, archived INT)')
        self.assertEqual(self.store.available_sources(), [])
        self.assertEqual(self.store.list(), [])

    def test_source_filter_precedes_pagination_and_inventory_ignores_search_archive_and_page(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',
                           [(f'app-{i}', 'App', '/fixture', 200 + i, 0, 'Codex Desktop', 'vscode') for i in range(120)])
            db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?)', [
                ('cli-deep', 'Find me', '/fixture', 2, 0, 'codex_cli_rs', 'cli'),
                ('cli-archived', 'Older', '/fixture', 3, 1, 'codex_cli_rs', 'cli'),
                ('legacy-desktop', 'Legacy App', '/fixture', 4, 0, None, 'vscode'),
            ])
        self.assertEqual(self.store.available_sources(), ['cli', 'desktop'])
        self.assertEqual([row['id'] for row in self.store.list(source='cli', limit=100)], ['cli-deep'])
        self.assertEqual([row['id'] for row in self.store.list(source='cli', query='Find', offset=0)], ['cli-deep'])
        self.assertEqual([row['id'] for row in self.store.list(source='cli', archived=True)], ['cli-archived'])
        self.assertEqual(len(self.store.list(source='desktop', limit=100)), 100)
        self.assertEqual(len(self.store.list(source='desktop', limit=100, offset=100)), 22)
        self.assertIn('legacy-desktop', {row['id'] for row in self.store.list(source='desktop', offset=100)})


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

    def test_native_rollout_clock_is_kept_in_full_and_tail_history(self):
        import json
        for record in self.records:
            record['timestamp'] = '2026-11-09T09:24:00Z'
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in self.records), encoding='utf-8')
        for limit in (None, 1):
            view = self.store.history('fixture', limit) if limit else self.store.history('fixture')
            self.assertEqual(view['turns'][-1]['startedAt'], '2026-11-09T09:24:00Z')
            self.assertEqual([row['timestamp'] for row in view['turns'][-1]['items']],
                             ['2026-11-09T09:24:00Z'] * 2)

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
        from bridge.features.workspace.files import artifact_paths, referenced_model_images
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
        from bridge.features.sessions.model import normalize_state
        from bridge.features.sessions.timeline import Timeline
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
