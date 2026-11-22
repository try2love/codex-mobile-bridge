import copy
import tempfile
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.features.sessions import sync


class DesktopProjectionReuseTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {'id': 'question', 'turnId': 'long', 'role': 'user', 'text': 'original'},
            *({'id': str(i), 'turnId': 'long', 'role': 'activity',
               'kind': 'commandExecution', 'text': 'command ' + str(i)}
              for i in range(1000)),
            {'id': 'answer', 'turnId': 'long', 'role': 'assistant', 'text': 'answer'},
        ]
        self.revision = 'r1'
        self.revision_on_tail_only = False

    def manager(self, provider='claude', adapter=None):
        def native(_provider, action, sid, options):
            self.assertEqual((_provider, action, sid), (provider, 'detail', 'test'))
            end = int(options.get('historyBefore', len(self.entries)))
            start = max(0, end - options['historyLimit'])
            history = {'before': str(start) if start else None, 'hasMore': bool(start)}
            if self.revision and (not self.revision_on_tail_only or 'historyBefore' not in options):
                history['revision'] = self.revision
            return {'session': {'status': 'idle'}, 'messages': copy.deepcopy(self.entries[start:end]),
                    'history': history}

        return SimpleNamespace(read_lock=threading.Lock(), history_scans=OrderedDict(), history_views=OrderedDict(),
                               binding_epochs={provider: 1}, require_enabled=Mock(), adapters={provider: adapter},
                               call=Mock(side_effect=native))

    def read(self, manager, mode, provider='claude', **options):
        value = DesktopSessions.history_sync(manager, provider, 'test', mode, options)
        while value.get('scan'):
            value = DesktopSessions.history_sync(manager, provider, 'test', mode,
                                                 {**options, 'scan': value['scan']})
        self.assertFalse(value.get('pending'))
        return value

    def test_versioned_scan_projects_at_sparse_checkpoints_and_hot_pages_reuse(self):
        manager = self.manager()
        with patch.object(DesktopSessions, '_project_history', wraps=DesktopSessions._project_history) as project:
            page = self.read(manager, 'recent')
            group = page['turns'][0]['processes'][0]['key']
            self.assertLess(project.call_count, 8)
            self.assertEqual([r['text'] for r in page['rows'] if sync.primary(r)], ['original', 'answer'])
            project.reset_mock()
            manager.call.reset_mock()
            first = self.read(manager, 'process', key=group, limit=100)
            next_page = self.read(manager, 'process', key=group, after=first['next'], limit=100)
            self.assertEqual(manager.call.call_count, 2)
            project.assert_not_called()
            self.assertEqual(next_page['rows'][0]['order'], 100)
            unchanged = self.read(manager, 'process', key=group, limit=100, ifVersion=first['version'])
            self.assertTrue(unchanged['notModified'])
            self.assertEqual(unchanged['rows'], [])

    def test_unversioned_reuse_checks_every_contributing_page_and_detects_older_edit(self):
        self.revision = None
        manager = self.manager('deepseek')
        main = self.read(manager, 'recent', provider='deepseek')
        key = main['turns'][0]['processes'][0]['key']
        with patch.object(DesktopSessions, '_project_history', wraps=DesktopSessions._project_history) as project:
            manager.call.reset_mock()
            first = self.read(manager, 'process', provider='deepseek', key=key)
            self.assertGreater(manager.call.call_count, 10)
            project.assert_not_called()
            version = first['version']
            self.entries[1]['text'] = 'older process amended'
            changed = self.read(manager, 'process', provider='deepseek', key=key, ifVersion=version)
            self.assertFalse(changed['notModified'])
            self.assertEqual(changed['rows'][0]['text'], 'older process amended')
            self.assertGreater(project.call_count, 0)

    def test_revision_on_tail_only_does_not_authorize_older_cached_bodies(self):
        self.revision_on_tail_only = True
        manager = self.manager()
        main = self.read(manager, 'recent')
        key = main['turns'][0]['processes'][0]['key']
        first = self.read(manager, 'process', key=key)
        self.entries[1]['text'] = 'source page without a version was edited'
        changed = self.read(manager, 'process', key=key, ifVersion=first['version'])
        self.assertFalse(changed['notModified'])
        self.assertEqual(changed['rows'][0]['text'], 'source page without a version was edited')

    def test_new_revision_or_binding_never_uses_previous_projection(self):
        manager = self.manager()
        main = self.read(manager, 'recent')
        key = main['turns'][0]['processes'][0]['key']
        initial = self.read(manager, 'process', key=key)
        self.entries[1]['text'] = 'first revision edit'
        self.revision = 'r2'
        second = self.read(manager, 'process', key=key, ifVersion=initial['version'])
        self.assertFalse(second['notModified'])
        self.assertEqual(second['rows'][0]['text'], 'first revision edit')
        self.entries[1]['text'] = 'change of binding'
        manager.binding_epochs['claude'] += 1
        rebound = self.read(manager, 'process', key=key, ifVersion=second['version'])
        self.assertFalse(rebound['notModified'])
        self.assertEqual(rebound['rows'][0]['text'], 'change of binding')

    def test_renderer_only_session_without_saved_record_keeps_watch_fallback(self):
        adapter = SimpleNamespace(record_path=Mock(side_effect=KeyError('no saved record')))
        manager = self.manager('zcode', adapter)
        source = DesktopSessions.sync_watch_source(manager, 'zcode', 'test')
        self.assertIsNone(source)
        value = self.read(manager, 'watch', provider='zcode')
        self.assertRegex(value['version'], r'^[0-9a-f]{24}$')
        adapter.record_path.assert_called_once_with('test')
        manager.adapters.clear()
        self.assertIsNone(DesktopSessions.sync_watch_source(manager, 'zcode', 'test'))
        self.assertEqual(self.read(manager, 'watch', provider='zcode')['version'], value['version'])

    def test_first_native_read_can_construct_a_deferred_adapter(self):
        manager = self.manager()
        source = manager.call.side_effect
        adapter = object()

        def first_read(*args):
            manager.adapters['claude'] = adapter
            return source(*args)

        manager.call.side_effect = first_read
        value = self.read(manager, 'recent')
        self.assertTrue(value['index'])
        self.assertEqual(next(iter(manager.history_views))[-1], id(adapter))

    def test_antigravity_reuses_only_the_same_verified_pair_of_log_files(self):
        self.revision = None
        with tempfile.TemporaryDirectory(dir='.tmp') as area:
            compact = Path(area).resolve() / 'transcript.jsonl'
            compact.write_text('{}\n', encoding='utf8')
            full = compact.with_name('transcript_full.jsonl')
            full.write_text('{}\n', encoding='utf8')
            manager = self.manager('antigravity', SimpleNamespace(record_path=lambda sid: str(compact)))
            main = self.read(manager, 'recent', provider='antigravity')
            key = main['turns'][0]['processes'][0]['key']
            manager.call.reset_mock()
            first = self.read(manager, 'process', provider='antigravity', key=key)
            manager.call.assert_called_once()
            full.write_text('{"amended":true}\n', encoding='utf8')
            self.entries[1]['text'] = 'amended full transcript'
            newer = self.read(manager, 'process', provider='antigravity', key=key,
                              ifVersion=first['version'])
            self.assertFalse(newer['notModified'])
            self.assertEqual(newer['rows'][0]['text'], 'amended full transcript')


if __name__ == '__main__':
    unittest.main()
