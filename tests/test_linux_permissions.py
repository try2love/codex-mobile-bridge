import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import test_bridge as support
from bridge.features.sessions.model import permission_mode
from bridge.features.sessions import linux_permissions
from bridge.features.sessions.side_chat import SideChat, SideChats
from bridge.features.sessions.linux_side_chat import LinuxSideChat, SideChatError
import threading
import uuid
from test_side_chat import RuntimeFixture
from bridge.clients.codex.ipc import IPCError
from bridge.features.sessions.linux_permissions import (read_permission_facts, desktop_permission_preferences, permission_options,
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



class LinuxScopeTests(unittest.TestCase):
    def test_local_linux_only_factory_and_capability_gate(self):
        for system in ('linux', 'darwin', 'win32'):
            for host in ('local', 'ssh-fixture'):
                with self.subTest(system=system, host=host), patch.object(linux_permissions.sys, 'platform', system):
                    expected = system == 'linux' and host == 'local'
                    self.assertEqual(linux_permissions.enabled(host), expected)
                    manager = SideChats('runtime', '/synthetic', '/synthetic', host)
                    self.assertIs(manager.factory, LinuxSideChat if expected else SideChat)
                    manager.close()

    def test_unknown_linux_runtime_blocks_write_but_other_platforms_keep_owner_validation(self):
        fixture = support.IntegrationTests()
        fixture.setUp(); self.addCleanup(fixture.tearDown)
        bridge = fixture.bridge
        session = bridge.session(support.THREAD)
        with patch.object(bridge.catalog_reader, 'linux_permission_capabilities', return_value={}) as capabilities:
            with patch.object(linux_permissions.sys, 'platform', 'linux'), patch.object(bridge, '_call') as write:
                with self.assertRaisesRegex(ValueError, '无法确认'):
                    bridge.permissions(support.THREAD, 'auto-review')
                write.assert_not_called()
                bridge.linux_permission_options(support.THREAD)
                self.assertTrue(bridge.view(support.THREAD)['linuxPermissionChecks'])
            for system, host in (('darwin', 'local'), ('win32', 'local'), ('linux', 'remote-fixture')):
                with self.subTest(system=system, host=host), patch.object(linux_permissions.sys, 'platform', system), \
                        patch.object(bridge, 'host', host), patch.object(bridge, '_target', return_value=session), \
                        patch.object(bridge, '_call', return_value={'applied': False}) as write:
                    capabilities.reset_mock()
                    with self.assertRaises(IPCError): bridge.permissions(support.THREAD, 'auto-review')
                    write.assert_called_once()
                    capabilities.assert_not_called()
                    with self.assertRaises(ValueError): bridge.linux_permission_options(support.THREAD)

    def test_picker_does_not_activate_a_desktop_session(self):
        fixture = support.IntegrationTests()
        fixture.setUp(); self.addCleanup(fixture.tearDown)
        with patch.object(linux_permissions.sys, 'platform', 'linux'), \
                patch.object(fixture.bridge.catalog_reader, 'linux_permission_capabilities', return_value={}), \
                patch.object(fixture.bridge, '_target', side_effect=AssertionError('must not activate')):
            options = fixture.bridge.linux_permission_options(support.THREAD)
            self.assertTrue(all(not row['available'] for row in options['options']))
            self.assertFalse(fixture.fixture.requests)

    def test_progressive_pages_and_changes_preserve_linux_permission_metadata(self):
        fixture = support.IntegrationTests()
        fixture.setUp(); self.addCleanup(fixture.tearDown)
        fixture.fixture.state['latestThreadSettings'] = {
            'permissions': ':workspace', 'approvalPolicy': 'on-request',
            'approvalsReviewer': 'guardian_subagent'}
        bridge = fixture.bridge
        bridge.session(support.THREAD)
        with patch.object(bridge, '_target', side_effect=AssertionError('must not activate')), \
                patch.object(bridge.catalog_reader, 'linux_permission_capabilities',
                             side_effect=AssertionError('passive history must not probe capabilities')):
            for system, host in (('linux', 'local'), ('darwin', 'local'),
                                 ('win32', 'local'), ('linux', 'remote-fixture')):
                with self.subTest(system=system, host=host), \
                        patch.object(linux_permissions.sys, 'platform', system), patch.object(bridge, 'host', host):
                    page = bridge.timeline_read(support.THREAD)
                    older = bridge.timeline_read(support.THREAD, before=page['before'])
                    changes = bridge.timeline_read(support.THREAD, 'changes', after=page['sequence'],
                                                   epoch=page['epoch'], start=page['before'])
                    enabled = system == 'linux' and host == 'local'
                    for result in (page, older, changes):
                        self.assertEqual(result['meta'].get('linuxPermissionChecks', False), enabled)
                        self.assertEqual(result['meta']['permissionMode'], 'auto-review' if enabled else 'ask')


class LinuxSidePermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home/'.codex-global-state.json').write_text('{}')
        class Runtime(RuntimeFixture):
            def request(runtime, method, params, **kwargs):
                if method in ('config/read', 'configRequirements/read', 'permissionProfile/list', 'experimentalFeature/list'):
                    runtime.calls.append((method, params))
                    return permission_response(method, params)
                return super().request(method, params)
        self.chat = LinuxSideChat('fixture', self.home, str(uuid.uuid4()), str(self.home),
            {'model': 'fixture', 'modelProvider': 'custom', 'permissions': ':workspace',
             'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}, Runtime)
        self.addCleanup(self.chat.close)
        timeout = patch('bridge.features.sessions.linux_side_chat.PERMISSION_CONFIRM_TIMEOUT', .08)
        timeout.start(); self.addCleanup(timeout.stop)

    def test_unconfirmed_and_normalized_updates_never_show_full_access(self):
        original = self.chat.runtime.request
        for response in ('empty', 'normalized', 'other-thread'):
            def request(method, params, **kwargs):
                if method == 'thread/settings/update':
                    if response != 'empty':
                        self.chat.runtime.emit('thread/settings/updated',
                            threadId=self.chat.id if response == 'normalized' else self.chat.parent,
                            threadSettings={'activePermissionProfile': {'id': ':workspace'},
                                            'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'})
                    return {}
                return original(method, params, **kwargs)
            with self.subTest(response=response), patch.object(self.chat.runtime, 'request', side_effect=request):
                with self.assertRaisesRegex(SideChatError, '尚未获运行时确认'):
                    self.chat.permissions('full-access', True)
                self.assertEqual(self.chat.view()['permissionMode'], 'ask')
                self.assertEqual(self.chat.turn_permissions['permissions'], ':workspace')

    def test_delayed_notification_confirms_and_updates_next_turn(self):
        original = self.chat.runtime.request
        timers = []
        def request(method, params, **kwargs):
            if method == 'thread/settings/update':
                timer = threading.Timer(.01, lambda: self.chat.runtime.emit('thread/settings/updated',
                    threadSettings={'activePermissionProfile': {'id': ':workspace'},
                                    'approvalPolicy': 'on-request', 'approvalsReviewer': 'auto_review'}))
                timers.append(timer); timer.start(); return {}
            return original(method, params, **kwargs)
        try:
            with patch.object(self.chat.runtime, 'request', side_effect=request):
                self.assertEqual(self.chat.permissions('auto-review')['permissionMode'], 'auto-review')
            self.chat.send('synthetic message', str(uuid.uuid4()))
            self.assertEqual(self.chat.runtime.calls[-1][1]['approvalsReviewer'], 'auto_review')
        finally:
            for timer in timers: timer.join()

    def test_missing_capabilities_and_hidden_full_access_never_write(self):
        with patch.object(self.chat.runtime, 'request', return_value={}) as request:
            with self.assertRaisesRegex(ValueError, '无法确认'): self.chat.permissions('auto-review')
            self.assertNotIn('thread/settings/update', [call.args[0] for call in request.call_args_list])
        (self.home/'.codex-global-state.json').write_text(json.dumps({'electron-persisted-atom-state':
            {'composer-permission-mode-visibility': {'full-access': False}}}))
        self.chat.runtime.calls.clear()
        with self.assertRaisesRegex(ValueError, '桌面设置'): self.chat.permissions('full-access', True)
        self.assertNotIn('thread/settings/update', [method for method, _ in self.chat.runtime.calls])

    def test_rejection_and_disconnect_do_not_apply_requested_permissions(self):
        original = self.chat.runtime.request
        for disconnected in (False, True):
            def request(method, params, **kwargs):
                if method == 'thread/settings/update':
                    if disconnected:
                        self.chat.runtime.emit('bridge/disconnected'); return {}
                    raise SideChatError('rejected')
                return original(method, params, **kwargs)
            with patch.object(self.chat.runtime, 'request', side_effect=request):
                with self.assertRaises(SideChatError): self.chat.permissions('full-access', True)
                self.assertEqual(self.chat.view()['permissionMode'], 'ask')

    def test_read_only_picker_does_not_write_and_external_updates_are_authoritative(self):
        options = self.chat.permission_options()
        self.assertTrue(all(row['available'] for row in options['options']))
        self.assertNotIn('thread/settings/update', [method for method, _ in self.chat.runtime.calls])
        self.chat.runtime.emit('thread/settings/updated', threadSettings={
            'activePermissionProfile': {'id': ':danger-full-access'}, 'approvalPolicy': 'never'})
        self.assertEqual(self.chat.view()['permissionMode'], 'full-access')
        self.chat.runtime.emit('thread/settings/updated', threadSettings={
            'sandboxPolicy': {'type': 'workspaceWrite'}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'})
        self.assertEqual(self.chat.view()['permissionMode'], 'ask')
        self.assertNotIn('permissions', self.chat.turn_permissions)

class LinuxPermissionHTTPTests(unittest.TestCase):
    def test_capability_reads_require_login_and_side_actions_keep_csrf(self):
        fixture = support.HttpTests()
        fixture.setUpClass(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        calls = []
        fixture.server.bridge.linux_permission_options = lambda thread: calls.append(thread) or {'options': []}
        path = '/api/sessions/'+support.THREAD+'/permissions'
        self.assertEqual(fixture.request('GET', path)[0], 401)
        self.assertFalse(calls)
        auth = fixture.login()
        self.assertEqual(fixture.request('GET', path, headers=auth)[0], 200)
        self.assertEqual(calls, [support.THREAD])
        side = '/api/sessions/'+support.THREAD+'/side-chat'
        fixture.server.bridge.side_chat = lambda *args: {'options': []}
        body = {'action': 'linux-permission-options', 'id': str(uuid.uuid4())}
        cookie = {key: value for key, value in auth.items() if key.lower() == 'cookie'}
        self.assertEqual(fixture.request('POST', side, body, cookie)[0], 403)
        self.assertEqual(fixture.request('POST', side, body, auth)[0], 200)

class LinuxReviewerCompatibilityTests(unittest.TestCase):
    def test_legacy_reviewer_display_changes_only_on_linux(self):
        state = {'latestThreadSettings': {'permissions': ':workspace', 'approvalPolicy': 'on-request',
                                         'approvalsReviewer': 'guardian_subagent'}}
        for system in ('linux', 'darwin', 'win32'):
            with patch.object(linux_permissions.sys, 'platform', system):
                self.assertEqual(permission_mode(state), 'ask')
                if linux_permissions.enabled():
                    self.assertEqual(linux_permissions.current_mode(state), 'auto-review')
