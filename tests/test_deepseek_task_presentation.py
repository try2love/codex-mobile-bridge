import unittest
from bridge.clients.deepseek.adapter import messages


def event(seq, kind, data):
    return {'event': {'seq': seq, 'type': kind, 'time': 1700000000000 + seq*1000, 'data': data}}


class DeepSeekTaskPresentationTests(unittest.TestCase):
    def test_native_turn_contains_supplements_and_only_terminal_reply_is_final(self):
        rows = messages([
            event(1, 'turn/start', {'turn': 3}),
            event(2, 'user/message', {'source': {'kind': 'user'}, 'content': 'prompt'}),
            event(3, 'assistant/message', {'turn': 3, 'message': {'content': 'progress'}}),
            event(4, 'user/message', {'source': {'kind': 'user'}, 'content': 'supplement'}),
            event(5, 'tool/call', {'name': 'Read'}),
            event(6, 'assistant/message', {'turn': 3, 'message': {'content': 'final'}}),
            event(7, 'turn/end', {'turn': 3, 'reason': {'kind': 'completed'}}),
        ])
        self.assertEqual({row.get('turnId') for row in rows}, {'3'})
        self.assertEqual({row.get('turnStatus') for row in rows}, {'completed'})
        self.assertEqual(rows[1]['phase'], 'commentary')
        self.assertEqual(rows[-1]['phase'], 'final_answer')
        self.assertEqual(rows[2]['role'], 'user')
        self.assertNotIn('interaction', rows[2])
        self.assertEqual(rows[-1]['turnStartedAt'], 1700000001000)
        self.assertEqual(rows[-1]['turnCompletedAt'], 1700000007000)

    def test_partial_page_preserves_explicit_turn_and_never_fabricates_terminal(self):
        rows = messages([event(10, 'assistant/message', {'turn': 4, 'message': {'content': 'progress'}})])
        self.assertEqual(rows[0]['turnId'], '4')
        self.assertNotIn('turnStatus', rows[0])
        self.assertNotEqual(rows[0].get('phase'), 'final_answer')
        self.assertNotIn('turnStartedAt', rows[0])

if __name__ == '__main__':
    unittest.main()
