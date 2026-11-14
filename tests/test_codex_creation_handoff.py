"""Creation intent must reach the native Desktop owner before the first turn."""
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.app.service import Bridge, LiveSession
from bridge.clients.codex.ipc import IPCError
from bridge.clients.codex.cli_session import CliSessions
from bridge.features.sessions.create import CreationError

ROOT = Path(__file__).resolve().parents[1]


class CodexCreationHandoff(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.bridge = Bridge(self.home, self.home / 'bridge')
        self.addCleanup(self.bridge.close)
        self.bridge.hosts.state = lambda: {'local-projects': {
            'project': {'id': 'project', 'name': 'Fixture', 'rootPaths': [str(self.home)]}}}
        self.bridge.ipc.connect = Mock()
        self.thread_id = str(uuid.uuid4())
        self.request_id = str(uuid.uuid4())
        self.selected = {'model': 'gpt-6-astra', 'effort': 'low', 'modelCatalogIdentity': 'fixture-account'}
        self.catalog = {'models': [{'id': 'gpt-6-astra', 'efforts': ['low', 'high']}],
                        'cache': {'identity': 'fixture-account'}, 'modelSource': 'codex'}
        self.bridge.creation_models = Mock(return_value=self.catalog)
        self.bridge.catalog = Mock(return_value=self.catalog)
        self.session = LiveSession(self.thread_id)
        self.session.connected = True
        self.session.owner = 'native-owner'
        self.session.saved_cwd = str(self.home)
        self.session.saved_provider = 'original-provider'
        self.session.state = {'id': self.thread_id, 'cwd': str(self.home), 'modelProvider': 'original-provider',
                              'latestModel': 'gpt-6-sol', 'latestReasoningEffort': 'medium',
                              'threadRuntimeStatus': {'type': 'idle'}, 'turns': [], 'requests': []}
        self.bridge.live[self.thread_id] = self.session
        self.bridge._target = Mock(return_value=self.session)
        self.turns = []
        self.bridge.ipc.request = Mock(side_effect=self.native_owner)
        for name, options in (('create_empty', {'return_value': self.thread_id}), ('open_in_desktop', {})):
            mocking = patch('bridge.app.service.' + name, **options)
            setattr(self, name, mocking.start())
            self.addCleanup(mocking.stop)

    def native_owner(self, method, params, **options):
        self.assertEqual(options['target'], 'native-owner')
        self.assertEqual(params['conversationId'], self.thread_id)
        if method == 'thread-follower-update-thread-settings':
            settings = params['threadSettings']
            with self.session.condition:
                self.session.state['latestModel'] = settings['model']
                if 'effort' in settings:
                    self.session.state['latestReasoningEffort'] = settings['effort']
                self.session.changed()
            return {'result': {'applied': True}}
        if method == 'thread-follower-start-turn':
            self.assertTrue(params['turnStart']['context']['inheritThreadSettings'])
            self.turns.append((self.session.state['latestModel'], self.session.state['latestReasoningEffort']))
            return {'result': {}}
        self.fail('Unexpected native operation: ' + method)

    def create(self):
        return self.bridge.create_chat('local|project', 'Fixture', self.request_id, model_options=self.selected)

    def test_desktop_reopened_empty_thread_keeps_creation_choice_for_first_send(self):
        result = self.create()
        self.assertEqual(result['id'], self.thread_id)
        self.assertEqual(self.session.view()['model'], 'gpt-6-astra')
        self.assertEqual(self.session.view()['effort'], 'low')
        self.bridge.send(self.thread_id, 'Fixture input', str(uuid.uuid4()))
        self.assertEqual(self.turns, [('gpt-6-astra', 'low')])
        self.assertEqual(self.session.state['modelProvider'], 'original-provider')
        self.assertEqual(self.session.state['cwd'], str(self.home))

    def test_repeating_completed_creation_never_overwrites_a_later_choice(self):
        self.create()
        self.session.state['latestModel'] = 'gpt-6-sol'
        self.session.state['latestReasoningEffort'] = 'high'
        self.bridge.ipc.request.reset_mock()
        self.create()
        self.assertEqual(self.session.view()['model'], 'gpt-6-sol')
        self.assertEqual(self.session.view()['effort'], 'high')
        self.bridge.ipc.request.assert_not_called()
        self.create_empty.assert_called_once()
        self.assertEqual(self.bridge.creations[self.request_id]['modelSettingsPhase'], 'applied')

    def test_selection_is_not_tied_to_the_native_default_model_family(self):
        self.selected['model'] = 'gpt-6-sol'
        self.catalog['models'] = [{'id': 'gpt-6-sol', 'efforts': ['low']}]
        self.session.state['latestModel'] = 'gpt-6-astra'
        self.create()
        self.bridge.send(self.thread_id, 'Fixture input', str(uuid.uuid4()))
        self.assertEqual(self.turns, [('gpt-6-sol', 'low')])

    def test_model_without_effort_controls_never_invents_a_reasoning_choice(self):
        self.selected.pop('effort')
        self.catalog['models'][0].update(efforts=[], supportsEffort=False)
        self.catalog['modelSource'] = 'api'
        self.create()
        self.assertEqual(self.session.view()['model'], self.selected['model'])
        self.assertEqual(self.session.view()['effort'], 'medium')
        settings = self.bridge.ipc.request.call_args.args[1]['threadSettings']
        self.assertEqual(settings, {'model': self.selected['model']})

    def test_creation_never_reenters_activation_while_holding_action_lock(self):
        def target(thread_id):
            self.assertFalse(self.session.action_lock._is_owned(),
                             'Activation must precede the session action lock')
            with self.session.activation_lock:
                return self.session
        self.bridge._target.side_effect = target
        self.create()
        self.bridge._target.assert_called_once_with(self.thread_id)

    def test_owner_not_ready_retains_created_id_and_retries_only_handoff(self):
        self.bridge._target.side_effect = IPCError('Owner not ready')
        with self.assertRaisesRegex(CreationError, '聊天已创建，所选模型尚未应用'):
            self.create()
        self.assertEqual(self.bridge.creations[self.request_id]['id'], self.thread_id)
        self.assertEqual(self.bridge.creations[self.request_id]['modelSettingsPhase'], 'pending')
        self.bridge.ipc.request.assert_not_called()
        self.bridge._target.side_effect = None
        self.create()
        self.create_empty.assert_called_once()
        self.assertEqual(self.session.view()['model'], 'gpt-6-astra')

    def test_unknown_settings_are_reconciled_without_replaying_a_write(self):
        self.bridge.ipc.request.side_effect = IPCError('lost response')
        with self.assertRaisesRegex(CreationError, '模型设置结果仍待核对'):
            self.create()
        self.assertEqual(self.bridge.creations[self.request_id]['modelSettingsPhase'], 'unknown')
        with self.assertRaisesRegex(CreationError, '不会重复提交'):
            self.create()
        self.bridge.ipc.request.assert_called_once()
        self.session.state['latestModel'] = self.selected['model']
        self.session.state['latestReasoningEffort'] = self.selected['effort']
        self.create()
        self.bridge.ipc.request.assert_called_once()
        self.create_empty.assert_called_once()
        self.assertEqual(self.bridge.creations[self.request_id]['modelSettingsPhase'], 'applied')

    def test_existing_turn_or_changed_native_identity_never_receives_settings(self):
        for state in (
                {'turns': [{'turnId': 'already-used', 'items': []}]},
                {'threadRuntimeStatus': {'type': 'active'}},
                {'cwd': '/another-project'},
                {'modelProvider': 'another-provider'}):
            with self.subTest(state=state):
                self.request_id = str(uuid.uuid4())
                saved = dict(self.session.state)
                self.session.state.update(state)
                with self.assertRaises(CreationError):
                    self.create()
                self.session.state = saved
                self.bridge.ipc.request.assert_not_called()

    def test_catalog_rejection_before_settings_write_can_be_explicitly_retried(self):
        self.bridge.catalog.return_value = {**self.catalog, 'models': [{'id': self.selected['model'], 'efforts': ['high']}]}
        with self.assertRaisesRegex(CreationError, '不支持所选推理强度'):
            self.create()
        self.assertEqual(self.bridge.creations[self.request_id]['modelSettingsPhase'], 'pending')
        self.bridge.ipc.request.assert_not_called()
        self.bridge.catalog.return_value = self.catalog
        self.create()
        self.create_empty.assert_called_once()

    def test_legacy_create_without_model_options_keeps_existing_behavior(self):
        self.bridge.create_chat('local|project', 'Fixture', self.request_id)
        self.bridge._target.assert_not_called()
        self.bridge.ipc.request.assert_not_called()


class CodexCliCreationChoice(unittest.TestCase):
    def test_cli_retains_its_original_runtime_and_choice_for_first_send(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            home = Path(directory)
            thread_id = str(uuid.uuid4())
            cli = CliSessions(home, lambda *_: None, lambda: None)
            cli.client.process = object()
            cli.client.is_loaded = Mock(return_value=True)
            cli._connect_explicit = Mock(return_value={'codexHome': str(home)})
            cli._queue_refresh = Mock()
            thread = {'id': thread_id, 'cwd': directory, 'modelProvider': 'fixture-provider',
                      'status': {'type': 'idle'}, 'turns': []}
            runtime_choice, sends = {}, []

            def request(method, params, **options):
                if method == 'thread/start':
                    runtime_choice.update(model=params['model'], effort=params['config']['model_reasoning_effort'])
                    return {'thread': thread, 'model': runtime_choice['model'], 'reasoningEffort': runtime_choice['effort']}
                if method == 'thread/read':
                    return {'thread': thread}
                if method == 'turn/start':
                    sends.append(dict(runtime_choice))
                return {}

            cli.client.request = Mock(side_effect=request)
            state = cli.create(directory, 'Fixture', model='gpt-6-sol', effort='low')
            self.assertEqual((state['latestModel'], state['latestReasoningEffort']), ('gpt-6-sol', 'low'))
            cli.call(thread_id, 'thread-follower-start-turn', {'turnStart': {
                'request': {'input': [{'type': 'text', 'text': 'Fixture'}]}, 'context': {'inheritThreadSettings': True}}})
            self.assertEqual(sends, [{'model': 'gpt-6-sol', 'effort': 'low'}])
            self.assertEqual(cli.metadata[thread_id]['modelProvider'], 'fixture-provider')
            self.assertEqual(cli.metadata[thread_id]['cwd'], directory)
            self.assertNotIn('thread/resume', [call.args[0] for call in cli.client.request.call_args_list])


if __name__ == '__main__':
    unittest.main()
