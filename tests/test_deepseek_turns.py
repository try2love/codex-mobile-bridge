"""Turn notifications require explicit native completion records, never idle."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.clients.deepseek.adapter import DeepSeek

ROOT = Path(__file__).resolve().parents[1]


def event(sequence, kind, turn, reason=None):
    data = {'turn': turn}
    if reason is not None:
        data['reason'] = reason
    return {'event': {'seq': sequence, 'type': kind, 'data': data}}


class DeepSeekTurnTests(unittest.TestCase):
    def detail(self, records):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            root = Path(folder)
            (root/'connection.json').write_text('{"token":"fixture","endpoint":"unused"}')
            (root/'endpoint.json').write_text('{"port":32123,"pid":32124}')
            adapter = DeepSeek(root, root/'home')
            class Opener:
                def open(self, request, timeout):
                    return io.BytesIO(json.dumps({'session': {'id': 'one', 'status': 'idle'},
                                                 'records': records}).encode())
            with patch('bridge.clients.deepseek.adapter.urllib.request.build_opener', return_value=Opener()):
                return adapter.call('detail', 'one')

    def test_explicit_turn_end_is_required_even_when_session_is_idle(self):
        detail = self.detail([event(1, 'turn/start', 1)])
        self.assertEqual(detail['turns'], [{'turnId': '1', 'status': 'inProgress', 'sequence': 1}])
        self.assertEqual(self.detail([])['turns'], [])

    def test_only_completed_reason_is_success_and_native_turn_id_is_stable(self):
        detail = self.detail([event(1, 'turn/start', 1), event(4, 'turn/end', 1, {'kind': 'completed'}),
                              event(5, 'turn/start', 2), event(7, 'turn/end', 2, {'kind': 'aborted', 'reason': {'kind': 'user'}}),
                              event(8, 'turn/start', 3), event(10, 'turn/end', 3, {'kind': 'error', 'error': {'private': 'do not forward'}})])
        self.assertEqual(detail['turns'], [
            {'turnId': '1', 'status': 'completed', 'sequence': 4, 'endReason': 'completed'},
            {'turnId': '2', 'status': 'failed', 'sequence': 7, 'endReason': 'aborted'},
            {'turnId': '3', 'status': 'failed', 'sequence': 10, 'endReason': 'error'}])
        self.assertNotIn('do not forward', json.dumps(detail))

    def test_completed_tail_without_start_is_explicit_terminal_evidence(self):
        self.assertEqual(self.detail([event(99, 'turn/end', 9, {'kind': 'completed'})])['turns'][0]['turnId'], '9')

    def test_out_of_order_records_cannot_turn_a_completed_turn_back_to_running(self):
        detail = self.detail([event(5, 'turn/end', 1, {'kind': 'completed'}), event(1, 'turn/start', 1)])
        self.assertEqual(detail['turns'][0]['status'], 'completed')

    def test_unknown_reason_malformed_identity_and_non_turn_records_do_not_complete(self):
        records = [event(1, 'turn/start', 0), event(2, 'turn/end', 0, {'kind': 'future-value'}),
                   event(3, 'turn/end', True, {'kind': 'completed'}), event(4, 'turn/end', '2', {'kind': 'completed'}),
                   event(5, 'assistant/message', 2), {'event': {'type': 'turn/end', 'data': {'turn': 3, 'reason': {'kind': 'completed'}}}}]
        detail = self.detail(records)
        self.assertEqual(detail['turns'], [{'turnId': '0', 'status': 'inProgress', 'sequence': 1}])


if __name__ == '__main__': unittest.main()
