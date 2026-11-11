"""Only a verified static current-user route can replace a dead CLI proxy."""

import ctypes
from contextlib import ExitStack
import os
from pathlib import Path
import plistlib
import sys
import tempfile
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


class KnownMacProxySourceTests(unittest.TestCase):
    def test_only_empty_static_settings_use_known_source_and_preserve_bypass(self):
        for static, expected, called in (
                (({'https': A}, (), False), ({'https': A}, (), False), False),
                (None, None, False),
                (({}, ('*.local',), True), ({'http': B, 'https': B}, ('*.local',), True), True)):
            with self.subTest(static=static), patch.object(codex_system_proxy.sys, 'platform', 'darwin'), \
                 patch.object(codex_system_proxy, '_macos', return_value=static), \
                 patch.object(codex_system_proxy, '_flybird', return_value=B) as source:
                self.assertEqual(codex_system_proxy.current_user_proxy_settings(), expected)
                self.assertEqual(source.called, called)

    def test_pac_or_malformed_enabled_system_setting_never_falls_back(self):
        for raw in ('<dictionary> {\n  ProxyAutoConfigEnable : 1\n}',
                    '<dictionary> {\n  SOCKSEnable : 1\n}',
                    '<dictionary> {\n  HTTPSEnable : 1\n  HTTPSProxy : 127.0.0.1\n}'):
            with self.subTest(raw=raw), patch.object(codex_system_proxy.sys, 'platform', 'darwin'), \
                 patch.object(codex_system_proxy.subprocess, 'run', return_value=SimpleNamespace(stdout=raw)), \
                 patch.object(codex_system_proxy, '_flybird') as source:
                self.assertIsNone(codex_system_proxy.current_user_proxy_settings())
                source.assert_not_called()

    def test_listener_requires_original_pid_and_loopback_reachable_binding(self):
        header='Proto Recv-Q Send-Q Local Address Foreign Address (state) rhiwat shiwat pid epid state options\n'
        owned='tcp4 0 0 *.4128 *.* LISTEN 0 0 7 7 0 0\n'
        foreign='tcp4 0 0 127.0.0.1.4128 *.* LISTEN 0 0 8 8 0 0\n'
        for rows, expected in ((owned, True), (foreign, False), (owned+foreign, False)):
            with self.subTest(rows=rows), patch.object(codex_system_proxy.subprocess, 'run', return_value=SimpleNamespace(stdout=header+rows)):
                self.assertEqual(codex_system_proxy._flybird_listener(7, 4128), expected)


@unittest.skipUnless(sys.platform == 'darwin', 'macOS known-source file/metadata fixture')
class FlybirdSourceFileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.home=root/'home';self.app=root/'飞鸟加速.app'
        self.config=self.home/'Library/Application Support/com.fnjs.clash/clash/config.yaml'
        self.config.parent.mkdir(parents=True)
        self.config.write_text('mixed-port: 4128\n# synthetic fixture only\n')
        info=self.app/'Contents/Info.plist';info.parent.mkdir(parents=True)
        info.write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.fnjs.clash'}))
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(codex_system_proxy, '_FLYBIRD_APP', self.app))
        stack.enter_context(patch('pwd.getpwuid', return_value=SimpleNamespace(pw_dir=str(self.home))))
        self.read_processes=codex_system_proxy._flybird_processes
        self.processes=stack.enter_context(patch.object(codex_system_proxy, '_flybird_processes', return_value=[(7,0,'known-birth','core')]))
        self.listener=stack.enter_context(patch.object(codex_system_proxy, '_flybird_listener', return_value=True))
        self.connection=Mock();self.connection.recv.return_value=b'\x05\x00'
        self.connect=stack.enter_context(patch('socket.create_connection'))
        self.connect.return_value.__enter__.return_value=self.connection

    def test_current_user_file_root_core_and_greeting_only(self):
        self.assertEqual(codex_system_proxy._flybird(), B)
        self.connect.assert_called_once_with(('127.0.0.1',4128), timeout=.5)
        self.connection.sendall.assert_called_once_with(b'\x05\x01\x00')
        self.assertEqual(self.processes.call_count,2)
        self.assertEqual(self.listener.call_count,2)

    def test_symlink_or_source_generation_change_is_not_adopted(self):
        real=self.config.with_name('other.yaml');self.config.rename(real);self.config.symlink_to(real)
        self.assertIsNone(codex_system_proxy._flybird());self.connect.assert_not_called()
        self.config.unlink();real.rename(self.config)
        self.processes.side_effect=[[(7,0,'old','core')],[(8,0,'new','core')]]
        self.assertIsNone(codex_system_proxy._flybird())

    def test_config_mutation_during_probe_is_rejected(self):
        def reply(_):
            self.config.write_text('mixed-port: 4129\n');return b'\x05\x00'
        self.connection.recv.side_effect=reply
        self.assertIsNone(codex_system_proxy._flybird())

    def test_duplicate_or_invalid_mixed_port_is_not_adopted(self):
        for content in ('mixed-port: 4128\nmixed-port: 4129\n',
                        'mixed-port: 4128\nmixed-port: invalid\n',
                        'mixed-port: 0\n'):
            with self.subTest(content=content):
                self.config.write_text(content)
                self.assertIsNone(codex_system_proxy._flybird())
                self.connect.assert_not_called()

    def test_root_candidate_argv_must_select_exact_user_file(self):
        from bridge.platforms.macos import desktop, force_exit
        executable=self.app/'Contents/MacOS/core'
        row='7 0 Wed Nov 11 13:00:00 2026 '+str(executable)+'\n'
        native=Mock()
        def image(pid, buffer, size):
            buffer.value=os.fsencode(executable);return len(buffer.value)+1
        native.lib.proc_pidpath.side_effect=image
        with patch.object(codex_system_proxy.subprocess, 'run', return_value=SimpleNamespace(stdout=row)), \
             patch.object(force_exit, 'Native', return_value=native), \
             patch.object(desktop, 'commands', return_value={7:str(executable)+' -f "'+str(self.config)+'"'}) as commands:
            result=self.read_processes(executable,self.config)
            self.assertEqual(len(result),1);self.assertEqual(result[0][1],0)
            commands.return_value={7:str(executable)+' -f "'+str(self.config)+'.other"'}
            self.assertEqual(self.read_processes(executable,self.config),[])
