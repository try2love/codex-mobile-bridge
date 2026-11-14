"""Native system-proxy opt-in stays scoped, versioned and never restarts work."""
import copy
import unittest
from pathlib import Path
from unittest.mock import Mock

from bridge.clients.codex.daemon_network import (
    enable_system_proxy_following, graceful_restart_supported, system_proxy_user_layer,
)
from bridge.clients.codex.ipc import IPCError


class CliSystemProxySettingTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(__file__).resolve().parent / 'fixture-codex-home'
        self.name = {'type': 'user', 'file': str(self.home / 'config.toml'), 'profile': None}

    def snapshot(self, user=False, effective=False, origin=None, version='user-v1'):
        layer = {'name': self.name.copy(), 'version': version,
                 'config': {'features': {'respect_system_proxy': user}, 'model_provider': 'original'}}
        return {'config': {'features': {'respect_system_proxy': effective}}, 'layers': [layer],
                'origins': {'features.respect_system_proxy': {'name': origin or self.name.copy(), 'version': version}}}

    def response(self):
        return {'status': 'ok', 'version': 'user-v2', 'filePath': str(self.home / 'config.toml')}

    def test_exact_cas_write_and_persisted_readback(self):
        probe = Mock(request=Mock(side_effect=[self.snapshot(), self.response(),
                                              self.snapshot(True, True, version='user-v2')]))
        self.assertTrue(enable_system_proxy_following(probe, self.home, expected_version='user-v1'))
        self.assertEqual(probe.request.call_args_list[1].args, ('config/value/write', {
            'keyPath': 'features.respect_system_proxy', 'value': True,
            'mergeStrategy': 'replace', 'expectedVersion': 'user-v1',
        }))
        self.assertEqual([call.args[0] for call in probe.request.call_args_list],
                         ['config/read', 'config/value/write', 'config/read'])

    def test_saved_user_true_is_not_written_again(self):
        probe = Mock(request=Mock(return_value=self.snapshot(True, True)))
        self.assertFalse(enable_system_proxy_following(probe, self.home, expected_version='user-v1'))
        probe.request.assert_called_once_with('config/read', {'includeLayers': True})

    def test_effective_session_true_still_persists_user_opt_in(self):
        probe = Mock(request=Mock(side_effect=[self.snapshot(False, True, {'type': 'sessionFlags'}),
                                              self.response(), self.snapshot(True, True, version='user-v2')]))
        self.assertTrue(enable_system_proxy_following(probe, self.home))

    def test_old_session_false_override_can_wait_for_fresh_start(self):
        response = {**self.response(), 'status': 'okOverridden'}
        probe = Mock(request=Mock(side_effect=[self.snapshot(False, False, {'type': 'sessionFlags'}), response,
                                              self.snapshot(True, False, {'type': 'sessionFlags'}, 'user-v2')]))
        self.assertTrue(enable_system_proxy_following(probe, self.home))

    def test_default_false_without_origin_can_be_enabled(self):
        before = self.snapshot(); before['origins'] = {}; before['layers'][0]['config']['features'] = {}
        probe = Mock(request=Mock(side_effect=[before, self.response(), self.snapshot(True, True, version='user-v2')]))
        self.assertTrue(enable_system_proxy_following(probe, self.home))

    def test_unconfigured_native_default_can_be_enabled(self):
        for features in (None, {}):
            with self.subTest(features=features):
                before = self.snapshot(); before['origins'] = {}; before['config']['features'] = features
                before['layers'][0]['config'].pop('features')
                probe = Mock(request=Mock(side_effect=[before, self.response(), self.snapshot(True, True, version='user-v2')]))
                self.assertTrue(enable_system_proxy_following(probe, self.home))

    def test_unrelated_native_features_do_not_block_missing_proxy_setting(self):
        before = self.snapshot(); before['origins'] = {}
        before['config']['features'] = {'auth_elicitation': True, 'tool_suggest': True, 'network_proxy': None}
        before['layers'][0]['config']['features'] = {'tool_suggest': True}
        probe = Mock(request=Mock(side_effect=[before, self.response(), self.snapshot(True, True, version='user-v2')]))
        self.assertTrue(enable_system_proxy_following(probe, self.home))

    def test_changed_preview_version_refuses_before_write_even_if_enabled(self):
        probe = Mock(request=Mock(return_value=self.snapshot(True, True, version='new-version')))
        with self.assertRaisesRegex(ValueError, '配置已变化'):
            enable_system_proxy_following(probe, self.home, expected_version='preview-version')
        self.assertEqual(probe.request.call_count, 1)

    def test_layer_identity_profile_and_version_are_required(self):
        mutations = [lambda s: s.pop('layers'), lambda s: s['layers'].clear(),
                     lambda s: s['layers'][0]['name'].update(file=str(self.home.parent / 'other/config.toml')),
                     lambda s: s['layers'][0]['name'].update(profile='another-profile'),
                     lambda s: s['layers'][0].update(version=''),
                     lambda s: s['layers'][0].update(disabledReason='inactive configuration'),
                     lambda s: s['layers'].append(copy.deepcopy(s['layers'][0]))]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                snapshot = self.snapshot(); mutate(snapshot)
                probe = Mock(request=Mock(return_value=snapshot))
                with self.assertRaises(ValueError): enable_system_proxy_following(probe, self.home)
                self.assertEqual(probe.request.call_count, 1)

    def test_managed_and_profile_false_overrides_refuse_before_write(self):
        for origin in ({'type': 'system'}, {'type': 'mdm'}, {'type': 'enterpriseManaged'},
                       {**self.name, 'profile': 'work'}):
            with self.subTest(origin=origin):
                probe = Mock(request=Mock(return_value=self.snapshot(False, False, origin)))
                with self.assertRaisesRegex(ValueError, '策略限制'):
                    enable_system_proxy_following(probe, self.home)
                self.assertEqual(probe.request.call_count, 1)

    def test_native_rejection_is_known_and_never_replayed(self):
        error = IPCError('native conflict'); error.rpc_error = {'data': {'config_write_error_code': 'configVersionConflict'}}
        probe = Mock(request=Mock(side_effect=[self.snapshot(), error]))
        with self.assertRaises(IPCError) as caught: enable_system_proxy_following(probe, self.home)
        self.assertFalse(getattr(caught.exception, 'system_proxy_unknown', False))
        self.assertEqual(probe.request.call_count, 2)

    def test_lost_write_receipt_is_unknown_and_never_replayed(self):
        probe = Mock(request=Mock(side_effect=[self.snapshot(), IPCError('connection lost')]))
        with self.assertRaises(IPCError) as caught: enable_system_proxy_following(probe, self.home)
        self.assertTrue(caught.exception.system_proxy_unknown)
        self.assertEqual(probe.request.call_count, 2)

    def test_malformed_receipt_is_unknown(self):
        for response in ({}, {**self.response(), 'filePath': str(self.home.parent / 'other/config.toml')}):
            with self.subTest(response=response):
                probe = Mock(request=Mock(side_effect=[self.snapshot(), response]))
                with self.assertRaises(IPCError) as caught: enable_system_proxy_following(probe, self.home)
                self.assertTrue(caught.exception.system_proxy_unknown)

    def test_write_success_with_unverified_readback_records_saved(self):
        changed = self.snapshot(True, True, version='concurrent-change')
        denied = self.snapshot(True, False, {'type': 'system'}, 'user-v2')
        for readback in (IPCError('read failed'), changed, denied):
            with self.subTest(readback=readback):
                probe = Mock(request=Mock(side_effect=[self.snapshot(), self.response(), readback]))
                with self.assertRaises((IPCError, ValueError)) as caught:
                    enable_system_proxy_following(probe, self.home)
                self.assertTrue(caught.exception.system_proxy_saved)
                self.assertFalse(getattr(caught.exception, 'system_proxy_unknown', False))

    def test_user_true_without_effective_or_session_override_is_not_accepted(self):
        for origins in ({}, self.snapshot()['origins']):
            with self.subTest(origins=origins):
                snapshot = self.snapshot(True, False); snapshot['origins'] = origins
                probe = Mock(request=Mock(return_value=snapshot))
                with self.assertRaises(ValueError): enable_system_proxy_following(probe, self.home)
                self.assertEqual(probe.request.call_count, 1)

    def test_read_only_user_layer_helper_returns_selected_layer(self):
        snapshot = self.snapshot()
        self.assertIs(system_proxy_user_layer(snapshot, self.home), snapshot['layers'][0])

    def test_exact_native_tui_runtime_prefix_and_version(self):
        for agent in ('codex-tui/0.164.0', 'codex-tui/0.164.0 (Mac OS 15.6.1)'):
            self.assertTrue(graceful_restart_supported({'userAgent': agent}, platform='darwin'))
            self.assertFalse(graceful_restart_supported({'userAgent': agent}, platform='win32'))
        for agent in ('other-codex-tui/0.164.0', 'codex-tui/0.164.00', 'codex-tui/0.164.0-beta',
                      'codex-tui/0.165.0', 'codex-tui/0.164.0evil'):
            self.assertFalse(graceful_restart_supported({'userAgent': agent}, platform='darwin'))
        self.assertTrue(graceful_restart_supported({'userAgent': 'codex-tui/0.167.0-alpha.3.2 (Windows 10)'}, platform='win32'))
