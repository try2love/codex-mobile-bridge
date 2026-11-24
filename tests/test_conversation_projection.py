import json
import unittest
from unittest.mock import Mock

from bridge.features.sessions.model import normalize_state
from bridge.features.sessions.timeline import Timeline, DETAIL_CHARS, PAGE_BYTES


class ConversationProjectionTests(unittest.TestCase):
    def test_whitespace_only_assistant_items_are_not_projected_as_empty_replies(self):
        value = normalize_state({'turns': [{'turnId': 'turn', 'status': 'completed', 'items': [
            {'id': 'user', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'question'}]},
            {'id': 'blank-space', 'type': 'agentMessage', 'text': ' '},
            {'id': 'blank-lines', 'type': 'assistantMessage', 'text': '\n\t  '},
            {'id': 'reasoning', 'type': 'reasoning', 'summary': []},
            {'id': 'commentary', 'type': 'agentMessage', 'text': 'Working', 'phase': 'commentary'},
            {'id': 'answer', 'type': 'agentMessage', 'text': 'Answer', 'phase': 'final_answer'},
            {'id': 'image', 'type': 'agentMessage', 'text': ' ', 'content': [
                {'type': 'image', 'path': '/tmp/reply.png', 'name': 'reply.png'}]},
        ]}]})
        messages = value['turns'][0]['messages']
        self.assertEqual([message['id'] for message in messages], ['user', 'reasoning', 'commentary', 'answer', 'image'])
        self.assertEqual([message['text'] for message in messages if message['role'] == 'assistant'],
                         ['Working', 'Answer', ' '])
        timeline = Timeline()
        timeline.update({**value, 'sequence': 1})
        rows = timeline.page()['rows']
        self.assertEqual([row['text'] for row in rows], ['question', '', 'Working', 'Answer', ' '])

        state = {'turns': [{'turnId': 'turn', 'status': 'inProgress', 'items': [
            {'id': 'user', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'question'}]},
            {'id': 'placeholder', 'type': 'agentMessage', 'text': ' '},
        ]}]}
        timeline = Timeline()
        timeline.update({**normalize_state(state), 'sequence': 1})
        self.assertEqual([row['text'] for row in timeline.page()['rows']], ['question'])
        state['turns'][0]['items'][1]['text'] = 'streamed after placeholder'
        timeline.update({**normalize_state(state), 'sequence': 2})
        self.assertEqual([row['text'] for row in timeline.page()['rows']],
                         ['question', 'streamed after placeholder'])

    def source(self, count=1000, sequence=1):
        cache = Mock()
        cache.extract.side_effect = AssertionError('Hidden tool images must not be extracted')
        items = [{'id': 'input', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Task input'}]}]
        items += [{'id': str(i), 'type': 'commandExecution', 'command': 'PRIVATE_COMMAND',
                   'aggregatedOutput': 'PRIVATE_OUTPUT' * 4000} for i in range(count)]
        items += [{'id': 'supplement', 'type': 'steeringUserMessage', 'status': 'accepted',
                   'input': [{'type': 'text', 'text': 'Ordinary supplement'}]},
                  {'id': 'output', 'type': 'agentMessage', 'text': 'Task output', 'phase': 'final_answer'}]
        state = {'id': 'chat', 'turns': [{'turnId': 'turn', 'status': 'completed', 'items': items}]}
        value = normalize_state(state, image_cache=cache, process_bodies=False)
        value['sequence'] = sequence
        value['requests'] = [{'id': 'current-approval'}]
        return value, cache

    def test_long_task_keeps_input_supplement_output_and_no_process_body(self):
        value, cache = self.source()
        timeline = Timeline()
        timeline.update(value, limit=20, conversation=True)
        page = timeline.page(projection='conversation')
        self.assertEqual([row['text'] for row in page['rows'] if row['role'] != 'activity'],
                         ['Task input', 'Ordinary supplement', 'Task output'])
        process = next(row for row in page['rows'] if row.get('processCursor'))
        self.assertEqual(process['turnStatus'], 'completed')
        self.assertNotIn('PRIVATE_', json.dumps(page))
        self.assertEqual(page['meta']['requests'], [{'id': 'current-approval'}])
        self.assertEqual(len(timeline.detail_sources), 1000)
        cache.extract.assert_not_called()

    def test_process_directory_and_single_body_have_independent_bounded_pages(self):
        value, _ = self.source(45)
        timeline = Timeline()
        timeline.update(value, conversation=True)
        process = next(row for row in timeline.page(projection='conversation')['rows'] if row.get('processCursor'))
        first = timeline.process(process['processCursor'])
        self.assertEqual(len(first['rows']), 20)
        self.assertNotIn('PRIVATE_', json.dumps(first))
        self.assertLessEqual(len(json.dumps(first).encode()), PAGE_BYTES)
        second = timeline.process(process['processCursor'], after=first['next'])
        self.assertTrue(set(row['key'] for row in first['rows']).isdisjoint(row['key'] for row in second['rows']))
        row = first['rows'][0]
        detail = timeline.detail(row['detailCursor'], version=row['version'])
        self.assertTrue(detail['text'].startswith('PRIVATE_COMMAND\n\nPRIVATE_OUTPUT'))
        self.assertEqual(len(detail['text']), DETAIL_CHARS)
        self.assertEqual(len(timeline.detail_sources), 44)
        tail = timeline.detail(row['detailCursor'], offset=detail['next'], version=row['version'])
        self.assertEqual(tail['offset'], DETAIL_CHARS)
        self.assertEqual(tail['key'], detail['key'])

    def test_native_update_changes_process_revision_without_reclassifying_supplement(self):
        value, _ = self.source(2)
        timeline = Timeline()
        timeline.update(value, conversation=True)
        first = timeline.page(projection='conversation')
        process = next(row for row in first['rows'] if row.get('processCursor'))
        value, _ = self.source(3, sequence=2)
        timeline.update(value, conversation=True)
        delta = timeline.changes(1, first['epoch'], first['before'], paged=True, projection='conversation')
        self.assertNotIn('reset', delta)
        current = next(row for row in delta['rows'] if row.get('processCursor'))
        self.assertEqual(current['key'], process['key'])
        self.assertNotEqual(current['version'], process['version'])
        self.assertNotIn('PRIVATE_', json.dumps(delta))
        self.assertEqual(sum(row.get('kind') == 'steeringUserMessage' for row in timeline.conversation_rows), 1)

    def test_live_process_directory_returns_latest_entries_in_transcript_order_within_caps(self):
        timeline = Timeline()
        messages = [{'id': 'input', 'role': 'user', 'kind': 'userMessage', 'text': 'input'}]
        messages += [{'id': f'progress-{i}', 'role': 'assistant', 'kind': 'agentMessage',
                      'phase': 'commentary', 'text': f'Progress {i}: ' + 'x' * 10000}
                     for i in range(45)]
        timeline.update({'sequence': 1, 'turns': [{'id': 'turn', 'status': 'inProgress', 'messages': messages}]},
                        conversation=True)
        cursor = timeline.conversation_rows[1]['processCursor']
        latest = timeline.process(cursor, after='latest')
        self.assertTrue(latest['hasEarlier'])
        self.assertIsNone(latest['next'])
        self.assertLessEqual(len(latest['rows']), 20)
        self.assertLessEqual(len(json.dumps(latest).encode()), PAGE_BYTES)
        self.assertTrue(latest['rows'][-1]['text'].startswith('Progress 44:'))
        order = [int(row['text'].split(':')[0].split()[-1]) for row in latest['rows']]
        self.assertEqual(order, list(range(45 - len(order), 45)))
        self.assertTrue(timeline.process(cursor)['rows'][0]['text'].startswith('Progress 0:'))

    def test_foreign_process_cursor_cannot_read_another_timeline(self):
        value, _ = self.source(1)
        left, right = Timeline(), Timeline()
        for timeline in (left, right):
            timeline.update(value, conversation=True)
        cursor = next(row['processCursor'] for row in left.conversation_rows if row.get('processCursor'))
        with self.assertRaises(ValueError):
            right.process(cursor)

    def test_supplement_splits_process_ranges_without_moving_later_activity_before_it(self):
        timeline = Timeline()
        rows = [{'id': 'input', 'role': 'user', 'kind': 'userMessage', 'text': 'input'},
                {'id': 'first', 'role': 'activity', 'kind': 'commandExecution', 'text': 'one'},
                {'id': 'supplement', 'role': 'user', 'kind': 'steeringUserMessage', 'text': 'extra'},
                {'id': 'second', 'role': 'activity', 'kind': 'commandExecution', 'text': 'two'},
                {'id': 'explanation', 'role': 'assistant', 'kind': 'agentMessage', 'phase': 'commentary', 'text': 'Working on the second part'}]
        timeline.update({'sequence': 1, 'turns': [{'id': 'turn', 'messages': rows}]}, conversation=True)
        visible = timeline.page(projection='conversation')['rows']
        self.assertEqual([row.get('text') for row in visible], ['input', '', 'extra', ''])
        self.assertNotEqual(visible[1]['key'], visible[3]['key'])
        directory = timeline.process(visible[3]['processCursor'])
        self.assertEqual(directory['rows'][1]['text'], 'Working on the second part')

    def test_lazy_tool_images_use_the_timeline_session_cache(self):
        cache = Mock()
        cache.extract.return_value = ('sanitized result', [{'type': 'toolImage', 'desktopId': 'scoped-image'}])
        value = normalize_state({'turns': [{'turnId': 'turn', 'items': [
            {'id': 'image', 'type': 'mcpToolCall', 'tool': 'screenshot', 'result': 'private-image'}]}]}, process_bodies=False)
        timeline = Timeline(image_cache=cache)
        timeline.update({**value, 'sequence': 1}, conversation=True)
        row = timeline.rows[0]
        cache.extract.assert_not_called()
        detail = timeline.detail(timeline.cursor(row['key']), version=row['version'])
        cache.extract.assert_called_once_with('private-image')
        self.assertEqual(detail['attachments'][0]['desktopId'], 'scoped-image')


if __name__ == '__main__':
    unittest.main()
