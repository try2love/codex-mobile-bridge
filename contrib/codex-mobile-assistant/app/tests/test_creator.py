import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge.creator import CreationError, ThreadCreator
from bridge.runtime import RuntimeErrorDetail


PROJECT = 'fed68bb9-3ae0-4f96-bbef-657824cae519'
THREAD = 'f7d3254d-9b7a-4166-af66-930ce93ef340'
REQUEST = 'd6478f76-50dc-430c-a6bc-77c1adcf05e3'
CREATED = {'thread': {'id': THREAD}}


class CreatorCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cwd = str(Path(self.directory.name).resolve())
        self.runtime_patch = patch('bridge.creator.MetadataRuntime')
        self.runtime = self.runtime_patch.start().return_value.__enter__.return_value
        self.addCleanup(self.runtime_patch.stop)
        self.creator = ThreadCreator(self.cwd, executable='unused-test-runtime')

    def create(self, project=PROJECT, callback=None):
        return self.creator.create(self.cwd, '测试标题', [self.cwd], project,
                                   request_id=REQUEST, on_created=callback)

    def starts(self):
        return [call.args[1] for call in self.runtime.request.call_args_list
                if call.args[0] == 'thread/start']

    def test_missing_project_retries_same_workspace_and_request(self):
        self.runtime.request.side_effect = [RuntimeErrorDetail('project not found: ' + PROJECT), CREATED, {}]
        callback = MagicMock()
        result = self.create(callback=callback)
        first, second = self.starts()
        self.assertEqual(first['projectId'], PROJECT)
        self.assertNotIn('projectId', second)
        self.assertEqual(second, {k: v for k, v in first.items() if k != 'projectId'})
        self.assertEqual(second['cwd'], self.cwd)
        self.assertEqual(second['runtimeWorkspaceRoots'], [self.cwd])
        self.assertEqual(second['threadSource'], 'codex-mobile:' + REQUEST)
        self.assertEqual(result['id'], THREAD)
        callback.assert_called_once()
        self.runtime.request.assert_called_with('thread/name/set', {'threadId': THREAD, 'name': '测试标题'})

    def test_valid_project_does_not_retry(self):
        self.runtime.request.side_effect = [CREATED, {}]
        self.create()
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.starts()[0]['projectId'], PROJECT)

    def test_other_or_uncertain_errors_do_not_retry(self):
        for error in (RuntimeErrorDetail('timeout', True),
                      RuntimeErrorDetail('project not found: ' + PROJECT, True),
                      RuntimeErrorDetail('permission denied'),
                      RuntimeErrorDetail('project not found: another-project')):
            with self.subTest(error=str(error), uncertain=error.uncertain):
                self.runtime.request.reset_mock()
                self.runtime.request.side_effect = error
                with self.assertRaises(CreationError) as raised:
                    self.create()
                self.assertEqual(raised.exception.uncertain, error.uncertain)
                self.assertEqual(len(self.starts()), 1)

    def test_fallback_failure_is_not_retried_again(self):
        self.runtime.request.side_effect = [RuntimeErrorDetail('project not found: ' + PROJECT),
                                            RuntimeErrorDetail('connection lost', True)]
        with self.assertRaises(CreationError) as raised:
            self.create()
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(len(self.starts()), 2)

    def test_projectless_stays_projectless(self):
        self.runtime.request.side_effect = [CREATED, {}]
        self.create(project=None)
        self.assertEqual(len(self.starts()), 1)
        self.assertNotIn('projectId', self.starts()[0])


if __name__ == '__main__':
    unittest.main()
