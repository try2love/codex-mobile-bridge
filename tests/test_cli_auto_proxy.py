"""Only a verified static current-user route can replace a dead CLI proxy."""

import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.clients.codex.daemon_network import automatic_proxy_update, current_proxy_replacement
from bridge.platforms import codex_system_proxy


A = 'http://127.0.0.1:3128'
B = 'http://127.0.0.1:4128'
PROVIDER = {'model_providers': {'fixture': {'base_url': 'https://model.fixture.invalid/v1'}}}
PROXY_SOURCE = 'bridge.platforms.codex_system_proxy.current_user_proxy_settings'
REACHABLE = 'bridge.clients.codex.daemon_network.reachable'


class AutomaticProxySelectionTests(unittest.TestCase):
    def test_only_effective_proxy_protocol_is_replaced(self):
        unrelated = 'http://127.0.0.1:2128'
        original = {'HTTPS_PROXY': A, 'HTTP_PROXY': unrelated, 'ALL_PROXY': unrelated,
                    'NO_PROXY': '127.0.0.1,ignored.invalid'}
        with patch(PROXY_SOURCE, return_value=({'https': B, 'http': unrelated}, (), False)), \
                patch(REACHABLE, side_effect=lambda endpoint: endpoint[1] == 4128):
            spec, updates = automatic_proxy_update(PROVIDER, 'fixture', original, {})
            self.assertEqual(updates, {'HTTPS_PROXY': B, 'https_proxy': B})
            self.assertEqual(current_proxy_replacement(spec), updates)
        self.assertEqual(original['HTTP_PROXY'], unrelated)
        self.assertEqual(original['ALL_PROXY'], unrelated)

    def test_no_route_change_when_native_handles_proxy_or_destination_is_bypassed(self):
        cases = (
            ({**PROVIDER, 'features': {'respect_system_proxy': True}}, {'HTTPS_PROXY': A}),
            (PROVIDER, {'HTTPS_PROXY': A, 'NO_PROXY': '.fixture.invalid'}),
            (PROVIDER, {'HTTPS_PROXY': A, 'NO_PROXY': 'bad/rule'}),
            (PROVIDER, {'HTTPS_PROXY': A, 'https_proxy': B}),
            (PROVIDER, {'HTTPS_PROXY': A, 'https_proxy': ''}),
            (PROVIDER, {'HTTPS_PROXY': 'https://remote.invalid:3128'}),
            ({'model_providers': {'fixture': {'base_url': 'http://127.0.0.1:7777'}}}, {'HTTP_PROXY': A}),
        )
        with patch(PROXY_SOURCE) as source, patch(REACHABLE) as probe:
            for config, original in cases:
                with self.subTest(config=config, original=original):
                    self.assertEqual(automatic_proxy_update(config, 'fixture', original, {}), (None, {}))
            source.assert_not_called()
            probe.assert_not_called()

    def test_live_old_proxy_or_inconclusive_system_replacement_never_queues(self):
        original = {'HTTPS_PROXY': A}
        with patch(PROXY_SOURCE) as source, patch(REACHABLE, return_value=True):
            self.assertEqual(automatic_proxy_update(PROVIDER, 'fixture', original, {}), (None, {}))
            source.assert_not_called()
        for settings in (None, ({'http': B}, (), False), ({'https': B}, ('*.fixture.invalid',), False),
                         ({'https': B}, ('bad/rule',), False),
                         ({'https': 'http://user:synthetic@127.0.0.1:4128'}, (), False),
                         ({'https': 'http://127.0.0.1:4128/?malformed=1'}, (), False),
                         ({'https': B}, (), False)):
            with self.subTest(settings=settings), patch(PROXY_SOURCE, return_value=settings), \
                    patch(REACHABLE, return_value=False):
                self.assertEqual(automatic_proxy_update(PROVIDER, 'fixture', original, {}), (None, {}))

    def test_proxy_fallback_updates_scheme_without_replacing_other_protocols(self):
        with patch(PROXY_SOURCE, return_value=({'https': B}, (), False)), \
                patch(REACHABLE, side_effect=lambda endpoint: endpoint[1] == 4128):
            spec, updates = automatic_proxy_update(PROVIDER, 'fixture', {'ALL_PROXY': A}, {})
            self.assertEqual(automatic_proxy_update(PROVIDER, 'fixture',
                {'ALL_PROXY': A, 'HTTP_PROXY': 'http://127.0.0.1:2128'}, {}), (None, {}))
        self.assertEqual(spec['scheme'], 'https')
        self.assertEqual(updates, {'https_proxy': B, 'HTTPS_PROXY': B})


class SystemProxySourceTests(unittest.TestCase):
    def test_macos_reads_one_authoritative_snapshot_including_bypass(self):
        raw = '''<dictionary> {
  ExceptionsList : <array> {
    0 : *.fixture.invalid
  }
  HTTPEnable : 0
  HTTPSEnable : 1
  HTTPSPort : 4128
  HTTPSProxy : ::1
  ExcludeSimpleHostnames : 1
  ProxyAutoConfigEnable : 0
}'''
        with patch.object(codex_system_proxy.subprocess, 'run', return_value=SimpleNamespace(stdout=raw)) as run:
            self.assertEqual(codex_system_proxy._macos(), ({'https': 'http://[::1]:4128'}, ['*.fixture.invalid'], True))
        run.assert_called_once()
        with patch.object(codex_system_proxy.subprocess, 'run',
                          return_value=SimpleNamespace(stdout=raw.replace('ConfigEnable : 0', 'ConfigEnable : 1'))):
            self.assertIsNone(codex_system_proxy._macos())

    def test_windows_current_user_settings_and_pac_priority(self):
        manual = ctypes.create_unicode_buffer('http=127.0.0.1:3128;https=127.0.0.1:4128')
        bypass = ctypes.create_unicode_buffer('*.fixture.invalid;<local>')
        pac = ctypes.create_unicode_buffer('https://pac.fixture.invalid')
        def config(pointer, pac_enabled):
            row = pointer._obj
            row.proxy, row.bypass = ctypes.addressof(manual), ctypes.addressof(bypass)
            if pac_enabled:
                row.pac = ctypes.addressof(pac)
            return True
        for pac_enabled in (False, True):
            read = Mock(side_effect=lambda pointer: config(pointer, pac_enabled))
            free = Mock()
            with patch.object(ctypes, 'WinDLL', create=True,
                              side_effect=lambda name: SimpleNamespace(WinHttpGetIEProxyConfigForCurrentUser=read)
                              if name == 'winhttp' else SimpleNamespace(GlobalFree=free)):
                result = codex_system_proxy._windows()
            if pac_enabled:
                self.assertIsNone(result)
                self.assertEqual(free.call_count, 3)
            else:
                self.assertEqual(result, ({'http': A, 'https': B}, ['*.fixture.invalid', '<local>'], False))
                self.assertEqual(free.call_count, 2)

    def test_linux_never_uses_inherited_gateway_proxy_as_a_system_source(self):
        with patch.object(codex_system_proxy.sys, 'platform', 'linux'), \
                patch.dict('os.environ', {'HTTPS_PROXY': B}):
            self.assertIsNone(codex_system_proxy.current_user_proxy_settings())
