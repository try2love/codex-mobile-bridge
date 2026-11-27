"""File actions survive content reuse without trusting cached permissions."""
import hashlib
import unittest
from unittest.mock import patch

import test_bridge
import test_session_sync as support
from bridge.clients.manager import DesktopSessions


class DesktopFileLinkTests(unittest.TestCase):
    def test_conditional_pages_and_process_keep_current_file_references(self):
        for provider in ('claude', 'deepseek', 'zcode', 'antigravity'):
            with self.subTest(provider=provider):
                messages = [
                    {'id': 'q', 'turnId': 't', 'role': 'user', 'text': 'show report'},
                    {'id': 'note', 'turnId': 't', 'role': 'assistant', 'phase': 'commentary',
                     'text': 'Reading [notes](/audit/notes.md)'},
                    {'id': 'a', 'turnId': 't', 'role': 'assistant', 'text': 'Open [report](/audit/report.md)'},
                ]
                manager = support.DesktopProviderSyncTests().manager(provider, messages)
                detail = manager.call.side_effect
                def source(*args):
                    value = detail(*args)
                    value['session']['cwd'] = '/audit'
                    return value
                manager.call.side_effect = source
                read = lambda mode, **options: DesktopSessions.history_sync(manager, provider, 'chat', mode, options)
                cold = read('recent')
                warm = read('recent', ifVersion=cold['version'], known={r['key']: r['version'] for r in cold['index']})
                self.assertEqual(warm['rows'], [])
                self.assertEqual(warm['files'], cold['files'])
                held = read('recent', heldText=[hashlib.sha256(messages[-1]['text'].encode()).hexdigest()[:24]])
                self.assertEqual(held['files'], cold['files'])
                body = read('body', key=cold['index'][-1]['key'], version=cold['index'][-1]['version'])
                self.assertEqual(body['files'], cold['files'])
                group = cold['turns'][0]['processes'][0]['processCursor']
                process = read('process', key=group)
                self.assertEqual([f['path'] for f in process['files']], ['/audit/notes.md'])
                reused = read('process', key=group, ifVersion=process['version'])
                self.assertEqual(reused['rows'], [])
                self.assertEqual(reused['files'], process['files'])


class CodexFileLinkTests(unittest.TestCase):
    setUp = test_bridge.IntegrationTests.setUp
    tearDown = test_bridge.IntegrationTests.tearDown

    def test_conditional_native_page_keeps_file_references(self):
        turns = [{'id': 't', 'status': 'completed', 'items': [
            {'id': 'q', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'show report'}]},
            {'id': 'a', 'type': 'agentMessage', 'text': 'Open [report](/audit/report.md)'},
        ]}]
        read = lambda **options: self.bridge.timeline_read(test_bridge.THREAD, 'sync', sync_mode='recent', **options)
        with patch.object(self.bridge, '_attach') as attach, patch.object(self.bridge.store, 'history',
                return_value={'id': test_bridge.THREAD, 'cwd': '/audit', 'turns': turns, 'historyComplete': True}):
            cold = read()
            warm = read(ifVersion=cold['version'], known={r['key']: r['version'] for r in cold['index']})
            self.assertEqual(warm['rows'], [])
            self.assertEqual([f['path'] for f in cold['files']], ['/audit/report.md'])
            self.assertEqual(warm['files'], cold['files'])
            attach.assert_not_called()
