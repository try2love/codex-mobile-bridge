import unittest
from bridge.clients.claude.model import transcript
from bridge.clients.deepseek.adapter import messages
from bridge.clients.zcode.adapter import project_snapshot
from bridge.features.sessions.model import normalize_state
from bridge.features.sessions.timeline import Timeline

STAMP = 1794211440000
ISO = '2026-11-09T09:24:00Z'


class NativeMessageTimes(unittest.TestCase):
    def test_normalized_subagent_messages_inherit_only_first_user_native_turn_time(self):
        state = {'turns': [{'turnId': 'turn', 'turnStartedAtMs': STAMP, 'items': [
            {'id': 'user', 'type': 'userMessage', 'content': [{'type': 'input_text', 'text': 'hi'}]},
            {'id': 'steer', 'type': 'steeringUserMessage', 'input': [{'type': 'text', 'text': 'more'}]},
            {'id': 'reply', 'type': 'agentMessage', 'text': 'answer'}]}]}
        rows = normalize_state(state)['turns'][0]['messages']
        self.assertEqual(rows[0].get('timestamp'), STAMP)
        self.assertFalse(any('timestamp' in row for row in rows[1:]))
        # A bounded page starting at a steering message must not inherit the
        # omitted original input's clock.
        bounded = normalize_state(state, item_limit=2)['turns'][0]['messages']
        self.assertFalse(any('timestamp' in row for row in bounded))
        state['turns'][0].update(_firstUserItemId='omitted', items=state['turns'][0]['items'][1:])
        paged = normalize_state(state, item_limit=2)['turns'][0]['messages']
        self.assertFalse(any('timestamp' in row for row in paged))

    def test_claude_native_event_time_once_across_split_blocks(self):
        rows = transcript([{'type': 'assistant', 'uuid': 'reply', 'timestamp': ISO,
            'message': {'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'before'}, {'type': 'tool_use', 'id': 'tool', 'name': 'read', 'input': {}},
                {'type': 'text', 'text': 'after'}]}}])
        self.assertEqual([r['timestamp'] for r in rows if 'timestamp' in r], [ISO])
        self.assertFalse(any('timestamp' in r for r in transcript([{'type': 'user', 'message': {'content': 'old'}}])))

    def test_dsh_clock_uses_event_time_not_request_or_current_time(self):
        rows = messages([{'event': {'seq': 1, 'time': STAMP, 'type': 'assistant/message',
            'data': {'message': {'content': [{'type': 'thinking', 'thinking': 'work'},
                {'type': 'text', 'text': 'answer'}, {'type': 'text', 'text': 'more'}]}}}}])
        self.assertNotIn('timestamp', rows[0])
        self.assertEqual(rows[1]['timestamp'], STAMP)
        self.assertNotIn('timestamp', rows[2])

    def test_zcode_native_row_creation_time_survives_updates(self):
        snapshot = {'sessionId': 'test', 'revision': 1, 'seq': 2, 'logEpoch': 'epoch',
            'control': {'activeWorks': []}, 'pendingInteractions': [], 'rows': {'totalCount': 2, 'window': [
                {'rowId': 1, 'kind': 'userInput', 'visibility': 'visible', 'text': 'question', 'createdAt': STAMP},
                {'rowId': 2, 'kind': 'assistantText', 'visibility': 'visible', 'text': 'reply', 'createdAt': STAMP+1000}]}}
        before = project_snapshot(snapshot, '/synthetic')['messages']
        snapshot['seq'] = 3
        snapshot['rows']['window'][1]['text'] += ' more'
        after = project_snapshot(snapshot, '/synthetic')['messages']
        self.assertEqual([r['timestamp'] for r in before], [STAMP, STAMP+1000])
        self.assertEqual([r['timestamp'] for r in before], [r['timestamp'] for r in after])

    def test_codex_native_turn_and_message_clocks_survive_pagination_and_delta(self):
        state = {'turns': [{'turnId': 'turn', 'turnStartedAtMs': STAMP, 'items': [
            {'id': 'user', 'type': 'userMessage', 'content': [{'type': 'input_text', 'text': 'hi'}]},
            {'id': 'reply', 'type': 'agentMessage', 'text': 'answer', 'timestamp': ISO}]}]}
        timeline = Timeline()
        timeline.update({**normalize_state(state), 'sequence': 1})
        page = timeline.page()
        self.assertEqual([r['timestamp'] for r in page['rows']], [STAMP, ISO])
        self.assertEqual(timeline.page(limit=1)['rows'][0]['timestamp'], ISO)
        state['turns'][0]['items'][1]['text'] += ' updated'
        timeline.update({**normalize_state(state), 'sequence': 2})
        delta = timeline.changes(1, page['epoch'], page['before'])
        self.assertEqual(delta['rows'][0]['timestamp'], ISO)
        state['turns'][0]['items'][1]['timestamp'] = ISO.replace('09:24', '09:25')
        timeline.update({**normalize_state(state), 'sequence': 3})
        changed_time = timeline.changes(2, page['epoch'], page['before'])
        self.assertEqual(changed_time['rows'][0]['timestamp'], '2026-11-09T09:25:00Z')
        self.assertEqual(changed_time['rows'][0]['version'], delta['rows'][0]['version'])
        del state['turns'][0]['turnStartedAtMs']
        del state['turns'][0]['items'][1]['timestamp']
        timeline.update({**normalize_state(state), 'sequence': 4})
        self.assertFalse(any('timestamp' in row for row in timeline.page()['rows']))
