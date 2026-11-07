import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from bridge.app.lifecycle import private_json
from bridge.features.sessions.question_skips import QuestionSkips

ROOT = Path(__file__).resolve().parents[1]


class QuestionSkipTests(unittest.TestCase):
    def test_isolation_by_configuration_host_thread_turn_and_question(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            first = QuestionSkips(directory, 'local:/one/.codex', private_json)
            first.skip('thread', 'async:turn:card', 'question')
            for scope in ('local:/two/.codex', 'ssh:/one/.codex'):
                self.assertFalse(QuestionSkips(directory, scope, private_json).contains('thread', 'async:turn:card', 'question'))
            self.assertFalse(first.contains('other-thread', 'async:turn:card', 'question'))
            self.assertFalse(first.contains('thread', 'async:other-turn:card', 'question'))
            self.assertFalse(first.contains('thread', 'async:turn:card', 'other-question'))
            restored = QuestionSkips(directory, 'local:/one/.codex', private_json)
            self.assertTrue(restored.contains('thread', 'async:turn:card', 'question'))
            self.assertEqual(restored.path.stat().st_mode & 0o777, 0o600)

    def test_failed_save_does_not_hide_question_or_leave_false_receipt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            store = QuestionSkips(directory, 'fixture', Mock(side_effect=OSError('full disk')))
            request = {'id': 'card', 'method': 'bridge/requestUserInputAsync',
                       'params': {'questions': [{'id': 'one'}, {'id': 'two'}]}}
            with self.assertRaises(OSError):
                store.skip('thread', 'card', 'one')
            self.assertEqual(store.project('thread', [request]), [request])
            self.assertFalse(store.contains('thread', 'card', 'one'))

    def test_projection_preserves_approvals_and_never_mutates_native_history(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            store = QuestionSkips(directory, 'fixture', private_json)
            store.skip('thread', 'card', 'one')
            request = {'id': 'card', 'method': 'bridge/requestUserInputAsync',
                       'params': {'questions': [{'id': 'one'}, {'id': 'two'}]}}
            approval = {'id': 'card', 'method': 'item/tool/requestUserInput', 'params': request['params']}
            snapshot = json.dumps([request, approval])
            self.assertEqual(store.project('thread', [request, approval]), [
                {**request, 'params': {'questions': [{'id': 'two'}]}}, approval])
            self.assertEqual(json.dumps([request, approval]), snapshot)
