"""Native result events are completion evidence; idle snapshots are not."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from bridge.integrations.claude import Claude
from bridge.integrations import claude_model

ROOT = Path(__file__).resolve().parents[1]


def result(identifier='native-result', **extra):
    return {'type': 'result', 'uuid': identifier, 'subtype': 'success',
            'is_error': False, 'num_turns': 1, 'parent_tool_use_id': None, **extra}


class ClaudeTurns(unittest.TestCase):
    def test_only_native_root_results_establish_terminal_turns(self):
        transcript = [
            {'type': 'user', 'uuid': 'prompt', 'message': {'role': 'user', 'content': 'task'}},
            {'type': 'assistant', 'uuid': 'answer', 'message': {'role': 'assistant', 'content': 'answer', 'stop_reason': 'end_turn'}},
            result('done'), result('failure', subtype='error_during_execution', is_error=True),
            result('limit', subtype='error_max_turns', is_error=False),
        ]
        self.assertEqual(claude_model.turns(transcript), [
            {'turnId': 'done', 'status': 'completed'},
            {'turnId': 'failure', 'status': 'failed'},
            {'turnId': 'limit', 'status': 'failed'},
        ])

    def test_subagents_synthetic_and_model_less_results_do_not_complete_tasks(self):
        transcript = [result('nested', parent_tool_use_id='tool-id'),
                      result('sidechain', isSidechain=True),
                      result('synthetic-result', isSyntheticResult=True),
                      result('synthetic', isSynthetic=True), result('meta', isMeta=True),
                      result('compaction', isCompactSummary=True), result('no-model', num_turns=0)]
        self.assertEqual(claude_model.turns(transcript), [])

    def test_unknown_status_missing_identity_and_assistant_end_turn_are_not_success(self):
        transcript = [result(None), result(''), result('unknown', subtype='unknown'),
                      result('uncertain', is_error=None), {'type': 'result', 'uuid': 'incomplete'},
                      {'type': 'assistant', 'uuid': 'api-error', 'isApiErrorMessage': True,
                       'message': {'role': 'assistant', 'model': '<synthetic>', 'stop_reason': 'end_turn', 'content': 'error'}}]
        self.assertEqual(claude_model.turns(transcript), [])

    def test_repeated_native_event_has_one_stable_identity(self):
        transcript = [result('first'), result('first'), result('second')]
        expected = [{'turnId': 'first', 'status': 'completed'}, {'turnId': 'second', 'status': 'completed'}]
        self.assertEqual(claude_model.turns(transcript), expected)
        self.assertEqual(claude_model.turns({'messages': transcript}), expected)

    def test_failed_result_does_not_become_success_when_no_model_turn_ran(self):
        self.assertEqual(claude_model.turns([result('failure', is_error=True, num_turns=0)]),
                         [{'turnId': 'failure', 'status': 'failed'}])


class ClaudeDetailTurns(unittest.IsolatedAsyncioTestCase):
    async def test_code_and_cowork_details_include_native_terminal_results(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            adapter = Claude(Path(directory), auto_connect=False)
            for surface in ('code', 'cowork'):
                for batched in (False, True):
                    with self.subTest(surface=surface, batched=batched):
                        raw = {'sessionId': surface+'-id', 'isRunning': False}
                        session = claude_model.session(surface, raw)
                        adapter.index = {session['id']: session}
                        transcript = [{'type': 'assistant', 'message': {'role': 'assistant', 'content': 'answer'}}, result()]
                        async def call(kind, method, *args, **kwargs):
                            self.assertEqual(kind, surface)
                            if method == 'mobileDetail': return {'session': raw, 'transcript': transcript}
                            if method == 'getSession': return raw
                            if method == 'getTranscript': return transcript
                            raise AssertionError(method)
                        adapter.desktop = Mock(connected=True, capabilities={surface: ['mobileDetail'] if batched else []})
                        adapter.desktop.call = AsyncMock(side_effect=call)
                        value = await adapter.dispatch('detail', session['id'], {})
                        self.assertEqual(value['turns'], [{'turnId': 'native-result', 'status': 'completed'}])
                        self.assertEqual([message['text'] for message in value['messages']], ['answer'])


if __name__ == '__main__':
    unittest.main()
