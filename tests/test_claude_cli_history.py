import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.claude import model
from bridge.clients.claude.cli_session import ClaudeCli, _saved


class ClaudeCliHistory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root/'config'
        folder = self.config/'projects'/'fixture'
        folder.mkdir(parents=True)
        self.native = str(uuid.uuid4())
        self.path = folder/(self.native + '.jsonl')
        self.origin = 'fixture:account'
        self.sid = model.gateway_id('cli:' + self.origin, self.native)
        self.profile = {'binary': '/fixture/claude', 'directory': self.config, 'origin': self.origin}
        self.cli = ClaudeCli(profile_resolver=lambda: self.profile)

    def write(self, count):
        with self.path.open('w', encoding='utf-8') as target:
            target.write(json.dumps({'type': 'user', 'uuid': 'first', 'cwd': str(self.root),
                                     'permissionMode': 'plan', 'message': {'content': 'first'}}) + '\n')
            for index in range(count):
                target.write(json.dumps({'type': 'assistant', 'uuid': f'a{index}', 'cwd': str(self.root),
                                         'message': {'role': 'assistant', 'model': 'model-a',
                                                     'content': [{'type': 'text', 'text': str(index)}]}}) + '\n')

    def test_recent_then_older_pages_and_cached_metadata(self):
        self.write(280)
        with patch('bridge.clients.claude.cli_session._saved', wraps=_saved) as scan:
            self.cli.listing()
            recent = self.cli.call('detail', self.sid, {'historyLimit': 40})
            older = self.cli.call('detail', self.sid, {'historyLimit': 40,
                                                      'historyBefore': recent['history']['before']})
            self.assertEqual(scan.call_count, 1)
        self.assertEqual(recent['session']['permissionMode'], 'plan')
        self.assertEqual(len(recent['messages']), 40)
        self.assertEqual(len(older['messages']), 40)
        self.assertEqual(recent['messages'][0]['eventId'], 'a240')
        self.assertEqual(older['messages'][-1]['eventId'], 'a239')
        self.assertTrue(recent['history']['hasMore'])

    def test_live_recent_page_does_not_reappend_persisted_stream_prefix(self):
        self.write(1000)
        events = [json.loads(line) for line in self.path.read_text().splitlines()][1:]
        owned = SimpleNamespace(origin=self.origin, process=Mock(), error=None,
            condition=threading.Condition(), events=events, pending={}, active=False,
            catalog={}, model=None, permission=None)
        owned.process.poll.return_value = None
        self.cli.owned[self.native] = owned
        recent = self.cli.call('detail', self.sid, {'historyLimit': 40})
        self.assertEqual([message['eventId'] for message in recent['messages']],
                         [f'a{index}' for index in range(960, 1000)])
        self.assertEqual(len(owned.events), 1000)
        older = self.cli.call('detail', self.sid, {'historyLimit': 40,
            'historyBefore': recent['history']['before']})
        self.assertEqual([message['eventId'] for message in older['messages']],
                         [f'a{index}' for index in range(920, 960)])

    def test_live_overlap_preserves_tools_unpersisted_tail_and_context(self):
        self.write(3)
        events = [json.loads(line) for line in self.path.read_text().splitlines()][1:]
        call = {'type': 'assistant', 'message': {'id': 'native-call', 'role': 'assistant',
            'content': [{'type': 'text', 'text': 'saved call'},
                        {'type': 'tool_use', 'id': 'create', 'name': 'TaskCreate',
                         'input': {'subject': 'work'}}]}}
        result = {'type': 'user', 'uuid': 'tool-result', 'message': {'role': 'user',
            'content': [{'type': 'tool_result', 'tool_use_id': 'create', 'content': 'Task #7 created'}]}}
        with self.path.open('a') as target:
            target.write(json.dumps(call) + '\n' + json.dumps(result) + '\n')
        call = {**call, 'message': {**call['message'], 'content': [
            {'type': 'text', 'text': 'live call'}, call['message']['content'][1]]}}
        events += [call, {'type': 'system', 'subtype': 'compact_boundary'},
            {'type': 'assistant', 'uuid': 'tail', 'message': {'role': 'assistant', 'model': 'model-a',
                'usage': {'input_tokens': 11}, 'content': [
                    {'type': 'tool_use', 'id': 'update', 'name': 'TaskUpdate',
                     'input': {'taskId': '7', 'status': 'completed'}}]}},
            {'type': 'result', 'uuid': 'finished', 'subtype': 'success', 'is_error': False,
             'num_turns': 1, 'modelUsage': {'model-a': {'contextWindow': 200}}}]
        owned = SimpleNamespace(origin=self.origin, process=Mock(), error=None,
            condition=threading.Condition(), events=events,
            pending={'approval': {'request': {'tool_name': 'Bash'}}}, active=True,
            catalog={}, model=None, permission=None)
        owned.process.poll.return_value = None
        self.cli.owned[self.native] = owned
        detail = self.cli.call('detail', self.sid, {'historyLimit': 2})
        self.assertEqual(detail['messages'][0]['text'], 'live call')
        self.assertEqual(detail['messages'][-1]['tasks'], [{'content': 'work', 'status': 'completed'}])
        self.assertEqual(detail['turns'], [{'turnId': 'finished', 'status': 'completed'}])
        self.assertEqual(detail['session']['contextUsage']['usedTokens'], 11)
        self.assertEqual(detail['session']['contextUsage']['contextWindow'], 200)
        self.assertTrue(detail['session']['contextUsage']['compacted'])
        self.assertEqual(detail['session']['requests'][0]['id'], 'approval')
        self.assertIs(owned.events, events)

    def test_live_events_without_a_saved_identity_match_are_preserved(self):
        self.write(80)
        events = [{'type': 'assistant', 'message': {'role': 'assistant', 'content': 'not saved'}}]
        owned = SimpleNamespace(origin=self.origin, process=Mock(), error=None,
            condition=threading.Condition(), events=events, pending={}, active=False,
            catalog={}, model=None, permission=None)
        owned.process.poll.return_value = None
        self.cli.owned[self.native] = owned
        detail = self.cli.call('detail', self.sid, {'historyLimit': 40})
        self.assertEqual(len(detail['messages']), 41)
        self.assertEqual(detail['messages'][-1]['text'], 'not saved')
        self.assertEqual(owned.events, events)

    def test_live_result_anchor_survives_message_page_merge(self):
        from bridge.features.notifications.channels import Notifications

        self.write(2)
        saved = [json.loads(line) for line in self.path.read_text().splitlines()][1:]
        result = lambda rid: {'type': 'result', 'uuid': rid, 'subtype': 'success',
                              'is_error': False, 'num_turns': 1}
        events = [saved[0], result('R1'), saved[1], result('R2')]
        owned = SimpleNamespace(origin=self.origin, process=Mock(), error=None,
            condition=threading.Condition(), events=events, pending={}, active=False,
            catalog={}, model=None, permission=None)
        owned.process.poll.return_value = None
        self.cli.owned[self.native] = owned
        notifications = Notifications(Mock(), self.root)
        self.addCleanup(notifications.close)
        row = {'id': self.sid, 'host': 'desktop:claude'}
        with patch.object(notifications, '_effective', return_value={'notifyOnCompletion': True}):
            self.assertEqual(notifications._completion_pending(row, model.turns([result('R1')]), 'mobile'), [])
            detail = self.cli.call('detail', self.sid, {'historyLimit': 2})
            self.assertEqual(notifications._completion_pending(row, detail['turns'], 'mobile'),
                             [notifications._delivery_key(row, 'R2', completion=True, target='mobile')])
        self.assertEqual([turn['turnId'] for turn in detail['turns']], ['R1', 'R2'])
        self.assertEqual([message['eventId'] for message in detail['messages']], ['a0', 'a1'])

    def test_append_and_rollback_invalidate_cursor(self):
        self.write(80)
        first = self.cli.call('detail', self.sid, {})
        cursor = first['history']['before']
        with self.path.open('a') as target:
            target.write(json.dumps({'type': 'assistant', 'uuid': 'new', 'cwd': str(self.root),
                                     'message': {'role': 'assistant', 'content': 'new'}}) + '\n')
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.cli.call('detail', self.sid, {'historyBefore': cursor})
        self.write(60)
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.cli.call('detail', self.sid, {'historyBefore': cursor})

    def test_unknown_permission_stays_unknown_outside_recent_page(self):
        self.write(200)
        raw = self.path.read_text().replace('"permissionMode": "plan", ', '')
        self.path.write_text(raw)
        result = self.cli.call('detail', self.sid, {})
        self.assertFalse(result['capabilities']['resume'])
        self.assertIn('权限模式', result['notice'])

    def test_old_permission_metadata_is_not_lost_when_recent_page_excludes_it(self):
        self.write(200)
        self.cli.listing()
        with patch.object(self.cli, '_ready', return_value=True):
            result = self.cli.call('detail', self.sid, {})
        self.assertTrue(result['capabilities']['resume'])
        self.assertEqual(result['session']['permissionMode'], 'plan')

    def test_history_cursor_rejects_different_session_identity(self):
        self.write(80)
        cursor = self.cli.call('detail', self.sid, {})['history']['before']
        from bridge.clients.claude.history import page
        with self.assertRaisesRegex(ValueError, '已变化'):
            page(self.path, 'different-session', cursor)

    def test_long_log_still_lists_and_recent_page_is_bounded(self):
        self.write(3)
        with self.path.open('a') as target:
            filler = json.dumps({'type': 'progress', 'data': 'x' * 900}) + '\n'
            target.write(filler * 24000)
            target.write(json.dumps({'type': 'assistant', 'uuid': 'tail', 'cwd': str(self.root),
                                     'message': {'role': 'assistant', 'content': 'tail'}}) + '\n')
        self.assertGreater(self.path.stat().st_size, 20_000_000)
        self.assertEqual(self.cli.listing()['sessions'][0]['id'], self.sid)
        result = self.cli.call('detail', self.sid, {'historyLimit': 1})
        self.assertEqual(result['messages'][0]['eventId'], 'tail')
        self.assertTrue(result['history']['hasMore'])

    def test_sidechain_display_and_compaction_context_match_native_records(self):
        self.write(2)
        with self.path.open('a') as target:
            target.write(json.dumps({'type': 'user', 'uuid': 'branch', 'isSidechain': True,
                'message': {'role': 'user', 'content': 'branch prompt'}}) + '\n')
            target.write(json.dumps({'type': 'system', 'subtype': 'compact_boundary'}) + '\n')
            target.write(json.dumps({'type': 'assistant', 'uuid': 'usage', 'cwd': str(self.root),
                'message': {'role': 'assistant', 'model': 'model-a',
                            'usage': {'input_tokens': 7}, 'content': 'after compact'}}) + '\n')
        result = self.cli.call('detail', self.sid, {})
        self.assertIn('branch', [item['eventId'] for item in result['messages']])
        self.assertEqual(result['session']['contextUsage']['usedTokens'], 7)
        self.assertTrue(result['session']['contextUsage']['compacted'])

    def test_valid_last_line_without_newline_and_saved_request_is_not_pending(self):
        self.write(1)
        with self.path.open('a') as target:
            target.write(json.dumps({'type': 'control_request', 'request_id': 'stale',
                                      'request': {'tool_name': 'Bash'}}) + '\n')
            target.write(json.dumps({'type': 'assistant', 'uuid': 'last', 'cwd': str(self.root),
                                     'message': {'role': 'assistant', 'content': 'complete'}}))
        result = self.cli.call('detail', self.sid, {})
        self.assertEqual(result['messages'][-1]['eventId'], 'last')
        self.assertEqual(result['session']['requests'], [])

    def test_same_size_mtime_rollback_invalidates_cursor(self):
        self.write(80)
        cursor = self.cli.call('detail', self.sid, {})['history']['before']
        original = self.path.stat()
        # Linux filesystems may timestamp writes within one clock tick equally.
        time.sleep(0.05)
        data = self.path.read_bytes().replace(b'a70', b'z70')
        self.path.write_bytes(data)
        os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.cli.call('detail', self.sid, {'historyBefore': cursor})

    def test_replacement_during_open_is_not_returned_as_current_history(self):
        from bridge.clients.claude.history import page
        self.write(3)
        original_open = Path.open
        target = self.path
        replaced = False
        def replace_after_open(path, *args, **kwargs):
            nonlocal replaced
            if path == target and not replaced and args and args[0] == 'rb':
                replaced = True
                old = target.with_suffix('.old')
                target.replace(old)
                target.write_bytes(old.read_bytes())
                # Return the old inode while the path identifies its replacement.
                # Rename before opening: Windows disallows renaming an open file.
                return original_open(old, *args, **kwargs)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', replace_after_open):
            with self.assertRaisesRegex(ValueError, '发生变化'):
                page(self.path, self.sid)

    def test_same_size_mtime_rollback_refreshes_cached_metadata(self):
        self.write(2)
        self.assertEqual(self.cli.listing()['sessions'][0]['model'], 'model-a')
        original = self.path.stat()
        time.sleep(0.05)
        self.path.write_bytes(self.path.read_bytes().replace(b'model-a', b'model-b'))
        os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(self.cli.listing()['sessions'][0]['model'], 'model-b')

    def test_context_capacity_survives_later_irrelevant_result(self):
        self.write(2)
        with self.path.open('a') as target:
            target.write(json.dumps({'type': 'assistant', 'uuid': 'usage', 'cwd': str(self.root),
                'message': {'role': 'assistant', 'model': 'model-a',
                            'usage': {'input_tokens': 9}, 'content': 'answer'}}) + '\n')
            target.write(json.dumps({'type': 'result', 'modelUsage': {
                'model-a': {'contextWindow': 200000}}}) + '\n')
            target.write(json.dumps({'type': 'result', 'isSidechain': True,
                                      'modelUsage': {'model-a': {'contextWindow': 10}}}) + '\n')
            target.write(json.dumps({'type': 'result', 'modelUsage': {}}) + '\n')
        result = self.cli.call('detail', self.sid, {})
        self.assertEqual(result['session']['contextUsage']['contextWindow'], 200000)


if __name__ == '__main__':
    unittest.main()
