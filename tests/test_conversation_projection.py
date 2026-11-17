import json
import unittest
from unittest.mock import Mock

from bridge.features.sessions.model import normalize_state
from bridge.features.sessions.timeline import Timeline, DETAIL_CHARS, PAGE_BYTES


class ConversationProjectionTests(unittest.TestCase):
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

    def test_foreign_process_cursor_cannot_read_another_timeline(self):
        value, _ = self.source(1)
        left, right = Timeline(), Timeline()
        for timeline in (left, right):
            timeline.update(value, conversation=True)
        cursor = next(row['processCursor'] for row in left.conversation_rows if row.get('processCursor'))
        with self.assertRaises(ValueError):
            right.process(cursor)


if __name__ == '__main__':
    unittest.main()
