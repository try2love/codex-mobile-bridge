import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import test_bridge as support
from bridge.model import permission_mode
from bridge.ipc import IPCError
from bridge.permissions import (read_permission_facts, desktop_permission_preferences, permission_options,
                                permission_settings, UNKNOWN, RESTRICTED, FULL_HIDDEN, AUTO_UNKNOWN)


def permission_response(method, params=None):
    return {'config/read': {'config': {'features': {'guardian_approval': True}}},
            'configRequirements/read': {'requirements': None},
            'permissionProfile/list': {'data': [{'id': key, 'allowed': True} for key in (':workspace', ':danger-full-access')]},
            'experimentalFeature/list': {'data': [{'name': 'guardian_approval', 'enabled': True}]}}[method]


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.facts = read_permission_facts(permission_response, '/workspace')
        self.preferences = {'fullAccess': True, 'autoReview': True}

    def options(self, current='ask', native=True):
        return {row['preset']: row for row in permission_options(self.facts, self.preferences, current, native=native)['options']}

    def test_native_rollout_is_distinct_from_runtime_support(self):
        self.preferences['autoReview'] = False
        self.assertEqual(self.options()['auto-review']['reason'], AUTO_UNKNOWN)
        self.facts['config'] = {}
        self.assertEqual(self.options()['auto-review']['reason'], AUTO_UNKNOWN)
        self.assertTrue(self.options(native=False)['auto-review']['available'])
        self.assertTrue(self.options(current='auto-review')['auto-review']['available'])
        self.preferences['autoReview'] = True
        self.assertTrue(self.options()['auto-review']['available'])
        self.facts['guardianEnabled'] = False
        self.assertFalse(self.options(current='auto-review')['auto-review']['available'])

    def test_null_is_unrestricted_empty_is_denied_and_policy_wins(self):
        for key in ('allowedApprovalPolicies', 'allowedApprovalsReviewers', 'allowedSandboxModes', 'allowedPermissionProfiles'):
            with self.subTest(key=key):
                self.facts['requirements'] = {key: None}
                self.assertTrue(all(row['available'] for row in self.options().values()))
                self.facts['requirements'][key] = {} if key == 'allowedPermissionProfiles' else []
                self.assertTrue(all(row['reason'] == RESTRICTED for row in self.options().values()))
        self.preferences['fullAccess'] = False
        self.assertEqual(self.options()['full-access']['reason'], RESTRICTED)

    def test_visibility_and_unknown_do_not_change_current_mode(self):
        self.preferences['fullAccess'] = False
        self.assertEqual(self.options()['full-access']['reason'], FULL_HIDDEN)
        self.assertTrue(self.options()['ask']['available'])
        self.facts.pop('requirements')
        self.assertTrue(all(row['reason'] == UNKNOWN for row in self.options().values()))
        self.assertEqual(permission_options({}, {}, 'custom')['current'], 'custom')

    def test_legacy_reviewer_and_restricted_profile(self):
        self.facts['requirements']['allowedApprovalsReviewers'] = ['user', 'guardian_subagent']
        self.assertTrue(self.options()['auto-review']['available'])
        self.assertEqual(permission_settings('auto-review', self.facts)['approvalsReviewer'], 'guardian_subagent')
        self.facts['profiles'][':danger-full-access'] = False
        self.assertFalse(self.options()['full-access']['available'])

    def test_reader_is_read_only_sanitized_paginated_and_fails_closed(self):
        calls = []
        def request(method, params):
            calls.append(method)
            response = copy.deepcopy(permission_response(method))
            if method == 'config/read': response['config']['secret'] = 'do not expose'
            if method == 'permissionProfile/list' and params['cursor'] is None:
                return {'data': [], 'nextCursor': 'page2'}
            return response
        facts = read_permission_facts(request, '/workspace')
        self.assertEqual(calls.count('permissionProfile/list'), 2)
        self.assertNotIn('secret', str(facts))
        def unsupported(*args): raise RuntimeError('unsupported')
        self.assertEqual(read_permission_facts(unsupported, '/workspace'), {})
        for invalid in ({}, {'requirements': []}, {'requirements': {'allowedSandboxModes': 'bad'}}):
            facts = read_permission_facts(lambda m, p: invalid if m == 'configRequirements/read' else permission_response(m, p), '/workspace')
            self.assertNotIn('requirements', facts)

    def test_desktop_preferences_read_only_and_host_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.codex-global-state.json'
            self.assertIsNone(desktop_permission_preferences(folder)['fullAccess'])
            for visibility in (False, {'full-access': False}, True, {}):
                raw = json.dumps({'electron-persisted-atom-state': {
                    'composer-permission-mode-visibility': visibility,
                    'permission-selection-by-host-id:local': {'kind': 'agent-mode', 'agentMode': 'guardian-approvals'},
                    'private-data': 'must not expose'}})
                path.write_text(raw)
                prefs = desktop_permission_preferences(folder)
                self.assertEqual(prefs['fullAccess'], visibility is True or visibility == {})
                self.assertTrue(prefs['autoReview'])
                self.assertFalse(desktop_permission_preferences(folder, 'remote')['autoReview'])
                self.assertNotIn('private-data', str(prefs))
                self.assertEqual(path.read_text(), raw)

    def test_remote_capabilities_are_fetched_on_target_host_without_cache(self):
        from bridge.remote import RemoteCatalog
        reader = RemoteCatalog('linux-host')
        with patch('bridge.remote.ssh_read', return_value=self.facts) as read:
            reader.permission_capabilities('/remote/project')
            reader.permission_capabilities('/remote/project')
        self.assertEqual(read.call_count, 2)
        alias, source = read.call_args.args
        self.assertEqual(alias, 'linux-host')
        self.assertIn('reader.permission_capabilities(', source)
        compile(source, '<remote-permissions>', 'exec')


class PermissionsTests(support.IntegrationTests):
    def setUp(self):
        super().setUp()
        facts = read_permission_facts(permission_response, '/workspace')
        for mocker in (patch.object(self.bridge.catalog_reader, 'permission_capabilities', return_value=facts),
                       patch('bridge.service.desktop_permission_preferences', return_value={'fullAccess': True, 'autoReview': True})):
            mocker.start()
            self.addCleanup(mocker.stop)

    # Reuse setup only, not the inherited integration tests.
    def test_permission_presets_use_original_owner(self):
        session = self.bridge.session(support.THREAD)
        original = copy.deepcopy(session.state)
        def call(target, method, body):
            self.assertIs(target, session)
            self.assertEqual(method, 'thread-follower-update-thread-settings')
            self.assertEqual(set(body), {'threadSettings'})
            with session.condition:
                session.state['latestThreadSettings'] = body['threadSettings']
                session.changed()
            return {'applied': True}
        with patch.object(self.bridge, '_call', side_effect=call):
            for preset in ('ask','auto-review','full-access'):
                result=self.bridge.permissions(support.THREAD,preset,confirmed=True)
                self.assertTrue(result['confirmed'])
                self.assertEqual(session.view()['permissionMode'],preset)
                self.assertEqual(session.state['latestModel'],original['latestModel'])
                self.assertEqual(session.state['modelProvider'],original['modelProvider'])
        self.assertEqual(session.owner,'owner')

    def test_side_fork_inherits_latest_permission_selection(self):
        from bridge.service import Bridge
        state = {'modelProvider':'custom', 'latestModel':'fixture',
                 'currentPermissions':{'approvalPolicy':'never', 'approvalsReviewer':'user'},
                 'latestThreadSettings':{'approvalPolicy':'on-request','approvalsReviewer':'auto_review',
                                         'activePermissionProfile':{'id':':workspace'}}}
        fork = Bridge._fork_settings(state)
        self.assertEqual(fork['approvalPolicy'],'on-request')
        self.assertEqual(fork['approvalsReviewer'],'auto_review')
        self.assertEqual(fork['permissions'],':workspace')

    def test_rejection_and_explicit_full_access(self):
        with patch.object(self.bridge, '_call', return_value={'applied':False}) as call:
            with self.assertRaises(ValueError):self.bridge.permissions(support.THREAD,'full-access')
            with self.assertRaises(ValueError):self.bridge.permissions(support.THREAD,'bad')
            call.assert_not_called()
            with self.assertRaises(IPCError):self.bridge.permissions(support.THREAD,'auto-review')
        self.assertEqual(permission_mode({'currentPermissions': {'approvalPolicy':'on-request','sandboxPolicy':{'type':'readOnly'}}}),'custom')
        self.assertEqual(permission_mode({'currentPermissions': {'approvalPolicy':'on-request','sandboxPolicy':{'type':'workspaceWrite'},'approvalsReviewer':'auto_review'}}),'auto-review')

    def test_write_rechecks_capabilities_and_never_sends_unavailable_mode(self):
        self.assertTrue(all(row['available'] for row in self.bridge.permission_options(support.THREAD)['options']))
        with patch.object(self.bridge.catalog_reader, 'permission_capabilities', return_value={}), patch.object(self.bridge, '_call') as call:
            with self.assertRaisesRegex(ValueError, '无法确认'):
                self.bridge.permissions(support.THREAD, 'full-access', True)
            call.assert_not_called()

    def test_native_option_get_does_not_mutate_settings(self):
        with patch.object(self.bridge, '_call') as call:
            result = self.bridge.permission_options(support.THREAD)
            self.assertEqual(len(result['options']), 3)
            call.assert_not_called()

# Avoid running the base tests a second time in discovery.
for name in dir(support.IntegrationTests):
    if name.startswith('test_') and name not in PermissionsTests.__dict__:
        setattr(PermissionsTests, name, None)


class PermissionHttpTests(support.HttpTests):
    def test_capability_routes_require_auth_and_preserve_chat_scope(self):
        self.server.bridge.permission_options = Mock(return_value={'current': 'ask', 'options': []})
        self.server.bridge.side_chat = Mock(return_value={'current': 'ask', 'options': []})
        route = '/api/sessions/' + support.THREAD
        self.assertEqual(self.request('GET', route + '/permissions')[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', route + '/permissions', headers=headers)[0], 200)
        self.server.bridge.permission_options.assert_called_once_with(support.THREAD)
        child = 'child-fixture'
        body = {'action': 'permission-options', 'id': child}
        self.assertEqual(self.request('POST', route + '/side-chat', body, headers)[0], 200)
        self.assertEqual(self.server.bridge.side_chat.call_args.args[0], support.THREAD)
        self.assertEqual(self.server.bridge.side_chat.call_args.args[2:], ('permission-options', body))
        self.assertEqual(self.request('POST', route + '/side-chat', {**body, 'preset': 'full-access'}, headers)[0], 400)


for name in dir(support.HttpTests):
    if name.startswith('test_') and name not in PermissionHttpTests.__dict__:
        setattr(PermissionHttpTests, name, None)
