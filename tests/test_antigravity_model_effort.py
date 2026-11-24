"""Keep Antigravity command discovery and every send on the selected native variant."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.model import gateway_id


ROOT = Path(__file__).resolve().parents[1]
CID = '11111111-2222-4333-8444-555555555555'


class AntigravityModelEffortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.catalog = {'models': [{
            'id': 'm', 'name': 'M', 'efforts': ['medium', 'high'],
            'defaultEffort': 'medium', 'nativeModel': 'MODEL_M_MEDIUM',
            'variants': {
                'medium': {'id': 'm-medium', 'nativeModel': 'MODEL_M_MEDIUM'},
                'high': {'id': 'm-high', 'nativeModel': 'MODEL_M_HIGH'},
            },
        }]}
        self.adapter = Antigravity(self.project/'gateway', home=self.project/'home',
                                   model_catalog=lambda surface, **_: self.catalog)
        self.addCleanup(self.adapter.close)
        self.sid = gateway_id('app', CID)
        row = {'id': self.sid, 'nativeId': CID, 'surfaceDirKey': 'app',
               'surfaceRoot': str(self.project), 'cwd': str(self.project), 'status': 'idle'}
        self.calls = []

        def rpc(method, payload=None, *, surface='app', expected_identity=None, return_identity=False):
            self.calls.append((method, payload, surface))
            if method == 'StartCascade':
                reply = {'cascadeId': CID, 'projectEnvInfo': {'workspaceUris': [self.project.as_uri()]}}
                return reply, {'pid': 123}
            if method == 'GetSlashCommands':
                return {'commands': [{'info': {'name': name, 'type': kind}} for name, kind in (
                    ('plan', 'SLASH_COMMAND_TYPE_SYSTEM'), ('goal', 'SLASH_COMMAND_TYPE_SYSTEM'),
                    ('compact', 'SLASH_COMMAND_TYPE_SYSTEM'), ('inspect', 'SLASH_COMMAND_TYPE_SKILL'),
                )]}
            return {}

        for patcher in (
            mock.patch.object(self.adapter.cli, 'available', return_value=True),
            mock.patch.object(self.adapter.cli, 'waiting_state_by_cid', return_value=({}, set())),
            mock.patch.object(self.adapter.cli, 'call_ls_rpc', side_effect=rpc),
            mock.patch.object(self.adapter.store, 'resolve_session', return_value=row),
            mock.patch.object(self.adapter.store, 'is_native_busy', return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_high_on_wire(self, *, commands=False, start=False):
        sends = [payload for method, payload, _ in self.calls if method == 'SendUserCascadeMessage']
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]['cascade_config']['planner_config']['requested_model']['model'], 'MODEL_M_HIGH')
        command_calls = [payload for method, payload, _ in self.calls if method == 'GetSlashCommands']
        self.assertEqual(bool(command_calls), commands)
        for payload in command_calls:
            self.assertEqual(payload['cascadeConfig']['plannerConfig']['requestedModel']['model'], 'MODEL_M_HIGH')
        starts = [payload for method, payload, _ in self.calls if method == 'StartCascade']
        self.assertEqual(bool(starts), start)
        for payload in starts:
            self.assertEqual(payload['requested_model'], 'MODEL_M_HIGH')

    def test_all_session_send_routes_keep_saved_effort_and_native_command_scope(self):
        self.adapter.call('settings', self.sid, {'model': 'm', 'effort': 'high'})
        for action, body, commands in (
            ('send', {'text': 'normal'}, False),
            ('send', {'text': '/inspect code'}, True),
            ('send', {'text': 'use skill', 'plugins': ['inspect']}, True),
            ('compact', {}, True),
            ('goal', {'operation': 'set', 'objective': 'finish'}, True),
        ):
            with self.subTest(action=action, body=body):
                self.calls.clear()
                self.assertEqual(self.adapter.call(action, self.sid, body)['status'], 'accepted')
                self.assert_high_on_wire(commands=commands)
        self.adapter.call('work-mode', self.sid, {'mode': 'plan'})
        self.calls.clear()
        self.adapter.call('send', self.sid, {'text': 'plan next step'})
        self.assert_high_on_wire(commands=True)
        self.assertEqual(self.adapter._settings[self.sid]['workMode'], 'plan')

    def test_creation_uses_same_effort_for_start_first_message_and_skill_lookup(self):
        for first, plugins in (('plain', []), ('/inspect code', []), ('use skill', ['inspect'])):
            with self.subTest(first=first):
                self.calls.clear()
                self.adapter.call('create', body={
                    'firstMessage': first, 'plugins': plugins, 'model': 'm', 'effort': 'high',
                    'directoryProject': {'surface': 'app', 'cwd': str(self.project)},
                })
                self.assert_high_on_wire(commands=bool(plugins) or first.startswith('/'), start=True)
                self.assertEqual(self.adapter._settings[self.sid]['effort'], 'high')

    def test_legacy_flat_variant_and_manual_native_enum_stay_exact(self):
        for model in ('m-high', 'MODEL_M_HIGH'):
            with self.subTest(model=model):
                self.adapter._settings[self.sid] = {'model': model}
                self.calls.clear()
                self.adapter.call('send', self.sid, {'text': '/inspect code'})
                self.assert_high_on_wire(commands=True)

    def test_explicit_invalid_effort_never_falls_back_before_rpc(self):
        for model, effort in (('m', 'ultra'), ('missing', None),
                              ('MODEL_M_HIGH', 'medium'), (None, 'high')):
            for operation in ('send', 'commands'):
                with self.subTest(model=model, effort=effort, operation=operation):
                    self.calls.clear()
                    with self.assertRaises(ValueError):
                        if operation == 'send':
                            self.adapter.cli.send_user_message(CID, 'text', model=model, effort=effort)
                        else:
                            self.adapter.cli.slash_commands(model=model, effort=effort)
                    self.assertEqual(self.calls, [])

    def test_grouped_default_remains_the_same_for_commands_and_send(self):
        self.adapter.cli.slash_commands(model='m')
        self.adapter.cli.send_user_message(CID, 'text', model='m')
        commands, send = [payload for _, payload, _ in self.calls]
        self.assertEqual(commands['cascadeConfig']['plannerConfig']['requestedModel']['model'], 'MODEL_M_MEDIUM')
        self.assertEqual(send['cascade_config']['planner_config']['requested_model']['model'], 'MODEL_M_MEDIUM')

    def test_legacy_variant_auto_effort_projects_and_sends_with_its_native_variant(self):
        self.adapter._settings[self.sid] = {'model': 'm-high', 'effort': 'auto'}
        detail = self.adapter.call('detail', self.sid, {})
        self.assertEqual(detail['session']['model'], 'm')
        self.assertEqual(detail['session']['effort'], 'high')
        self.assertEqual(detail['session']['modelDisplayName'], 'M')
        self.calls.clear()
        self.adapter.call('send', self.sid, {'text': 'legacy auto'})
        self.assert_high_on_wire(commands=False)

        with self.assertRaisesRegex(ValueError, '思考强度'):
            self.adapter.cli.canonicalize_model_selection('m-high', 'medium')

    def test_manual_native_enum_normalizes_ascii_only(self):
        self.assertEqual(
            self.adapter.cli.canonicalize_model_selection('  model_placeholder_42  '),
            ('MODEL_PLACEHOLDER_42', 'MODEL_PLACEHOLDER_42', '', 'MODEL_PLACEHOLDER_42'),
        )
        for value in ('MODEL_ſ', 'MODEL_K', 'MODEL_İ'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'MODEL_'):
                self.adapter.cli.canonicalize_model_selection(value)
