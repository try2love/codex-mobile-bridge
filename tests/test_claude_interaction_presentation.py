import unittest
from bridge.clients.claude.model import transcript


class ClaudeInteractionPresentationTests(unittest.TestCase):
    def test_tool_receipt_text_is_folded_without_hiding_human_supplements(self):
        rows = transcript([
            {'uuid': 'receipt', 'type': 'user', 'sourceToolAssistantUUID': 'tool', 'message': {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Approved'}, {'type': 'tool_result', 'content': 'done'}]}},
            {'uuid': 'human', 'type': 'user', 'message': {'role': 'user', 'content': 'Approved, and add a check'}},
        ])
        self.assertEqual(rows[0].get('interaction'), 'response')
        self.assertEqual(rows[0]['role'], 'user')
        self.assertEqual(rows[1]['role'], 'activity')
        self.assertNotIn('interaction', rows[2])
        self.assertEqual(rows[2]['role'], 'user')

    def test_native_stop_reason_separates_progress_and_final(self):
        rows = transcript([{'uuid': reason, 'type': 'assistant', 'message': {
            'role': 'assistant', 'stop_reason': reason, 'content': [{'type': 'text', 'text': reason}]}}
            for reason in ['tool_use', 'end_turn', None]])
        self.assertEqual(rows[0].get('phase'), 'commentary')
        self.assertEqual(rows[1].get('phase'), 'final_answer')
        self.assertNotIn('phase', rows[2])

if __name__ == '__main__': unittest.main()
