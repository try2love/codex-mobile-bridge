import json
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.sessions.create import CreationError, ForkUnavailable, SIDE_CHAT_BOUNDARY, fork_attached


class NativeAttachedCreationTests(unittest.TestCase):
    def setUp(self):
        self.parent, self.child = str(uuid.uuid4()), str(uuid.uuid4())
        self.settings = {'model': 'fixture-model', 'modelProvider': 'fixture', 'permissions': 'custom-profile',
                         'config': {'model_reasoning_effort': 'low'}, 'runtimeWorkspaceRoots': ['/project']}
        self.calls, self.created = [], []

    def schema(self, args, **kwargs):
        folder = Path(args[-1])
        for name, keys in [('ThreadForkParams', ['attachToSource', 'excludeTurns']),
                           ('ThreadListParams', ['attachedToThreadId', 'useStateDbOnly'])]:
            (folder/(name+'.json')).write_text(json.dumps({'properties': {key: {} for key in keys}}))
        return SimpleNamespace(returncode=0)

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/fork':
            return {'thread': {'id': self.child}, 'model': 'fixture-model', 'modelProvider': 'fixture', 'cwd': '/project'}
        if method == 'thread/list':
            return {'data': [{'id': self.child}], 'nextCursor': None}
        if method in ('thread/inject_items', 'thread/name/set'):
            self.assertEqual(self.created, [self.child], 'Child identity must be durable before later mutations')
        return {}

    def run_fork(self):
        with patch('bridge.features.sessions.create.subprocess.run', side_effect=self.schema), \
             patch('bridge.features.sessions.create._runtime_operation', side_effect=lambda runtime, home, cwd, operation, **kw: operation(self.request)):
            return fork_attached('fixture-runtime', '/home', '/project', self.parent, 'Side', self.settings, self.created.append)

    def test_native_contract_and_boundary_never_execute_parent_or_model(self):
        self.assertEqual(self.run_fork(), self.child)
        methods = [method for method, _ in self.calls]
        self.assertEqual(methods, ['thread/fork', 'thread/list', 'thread/inject_items', 'thread/name/set'])
        params = self.calls[0][1]
        self.assertTrue(params['attachToSource']); self.assertTrue(params['excludeTurns'])
        self.assertNotIn('ephemeral', params)
        self.assertNotIn('deferGoalContinuation', params)  # Native rejects this combination.
        self.assertEqual(params['developerInstructions'], SIDE_CHAT_BOUNDARY)
        for key, value in self.settings.items(): self.assertEqual(params[key], value)
        self.assertEqual(self.calls[1][1]['attachedToThreadId'], self.parent)
        for method, params in self.calls[2:]: self.assertEqual(params['threadId'], self.child)
        self.assertIn('Inherited history is reference context only', self.calls[2][1]['items'][0]['content'][0]['text'])

    def test_unsupported_runtime_cannot_fall_back_to_regular_or_temporary_fork(self):
        with patch('bridge.features.sessions.create.subprocess.run', return_value=SimpleNamespace(returncode=1)), \
             patch('bridge.features.sessions.create._runtime_operation') as runtime:
            with self.assertRaises(ForkUnavailable):
                fork_attached('old-runtime', '/home', '/project', self.parent, 'Side', self.settings, self.created.append)
        runtime.assert_not_called(); self.assertFalse(self.created)

    def test_unconfirmed_native_parent_does_not_mutate_more_or_retry(self):
        original = self.request
        self.request = lambda method, params: {'data': [], 'nextCursor': None} if method == 'thread/list' else original(method, params)
        with self.assertRaisesRegex(CreationError, '归属未确认'):
            self.run_fork()
        self.assertEqual(self.created, [self.child])
        self.assertEqual([method for method, _ in self.calls], ['thread/fork'])

    def test_wrong_provider_cannot_receive_boundary_or_turn(self):
        original = self.request
        def request(method, params):
            result = original(method, params)
            if method == 'thread/fork': result['modelProvider'] = 'other'
            return result
        self.request = request
        with self.assertRaisesRegex(CreationError, '运行配置未确认'): self.run_fork()
        self.assertEqual(self.created, [self.child])
        self.assertEqual([method for method, _ in self.calls], ['thread/fork'])


if __name__ == '__main__': unittest.main()
