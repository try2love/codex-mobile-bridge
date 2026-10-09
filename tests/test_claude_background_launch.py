"""Background startup fixtures never launch or control the real desktop app."""
import json
import os
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.claude import setup as claude_setup
from bridge.platforms.windows import discovery_ext as windows_discovery
from bridge.clients.claude.adapter import Claude


class WindowsClaudeBackgroundLaunch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root/'Store/app/Claude.exe'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'MZ-fixture')
        resources = self.executable.parent/'resources'
        resources.mkdir(); (resources/'app.asar').write_bytes(b'fixture')
        self.folder = self.executable.parent.parent
        self.alias = self.root/'Local/Microsoft/WindowsApps/claude-desktop.exe'
        self.app_id = 'Claude_fixture!Claude'
        self.platform = patch('bridge.clients.claude.setup.sys.platform', 'win32')
        self.platform.start(); self.addCleanup(self.platform.stop)

    def manifest(self):
        (self.folder/'AppxManifest.xml').write_text(
            '<Package><Applications><Application Id="Claude" Executable="app\\Claude.exe">'
            '<VisualElements/><Extensions><Extension Category="windows.appExecutionAlias" Executable="app\\Claude.exe">'
            '<AppExecutionAlias><ExecutionAlias Alias="claude-desktop.exe"/></AppExecutionAlias>'
            '</Extension></Extensions></Application></Applications></Package>')

    def link(self, *, target=None, app_id=None, family='Claude_fixture', version=3):
        content = ('\0'.join([family, app_id or self.app_id, str(target or self.executable), '0', ''])).encode('utf-16-le')
        return struct.pack('<IHHI', 0x8000001b, len(content) + 4, 0, version) + content

    def test_alias_requires_registered_manifest_and_actual_target_to_agree(self):
        self.manifest()
        inventory = {'packages': [{'Name': 'Claude', 'InstallLocation': str(self.folder), 'PackageFamilyName': 'Claude_fixture'}]}
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root/'Local')}), \
             patch.object(windows_discovery, 'windows_installations', return_value=inventory), \
             patch.object(windows_discovery, '_read_app_execution_link', return_value=self.link()) as read:
            self.assertEqual(windows_discovery.application_execution_alias(self.executable), self.alias)
            read.assert_called_once_with(self.alias)
            for bad in (self.link(target=self.root/'Other/Claude.exe'), self.link(app_id='Claude_fixture!Other'),
                        self.link(family='Claude_other'), self.link(version=4), b'ordinary exe', self.link()[:-2]):
                read.return_value = bad
                self.assertIsNone(windows_discovery.application_execution_alias(self.executable))

    def test_unregistered_package_cannot_borrow_an_alias(self):
        self.manifest()
        with patch.object(windows_discovery, 'windows_installations', return_value={}), \
             patch.object(windows_discovery, '_read_app_execution_link') as read:
            self.assertIsNone(windows_discovery.application_execution_alias(self.executable))
        read.assert_not_called()

    def test_startup_capability_requires_the_native_hidden_window_branch(self):
        source = 'gHa=()=>hHa??=!r.default.argv.includes("--startup"),bHa=e=>{let i=(gHa()||!1)&&!r;aSe(i?null:r?"background_launch":"os_login");new Window({show:i&&!f})}'
        self.assertTrue(claude_setup._native_startup_hidden(source))
        self.assertFalse(claude_setup._native_startup_hidden(source.replace('show:i&&!f', 'show:true')))
        self.assertFalse(claude_setup._native_startup_hidden('unrelated --startup token'))
        def members(path, names):
            values = {'package.json': json.dumps({'name': '@ant/desktop'}),
                      '.vite/build/index.js': 'require("./index.chunk-main.js");',
                      '.vite/build/index.chunk-main.js': source}
            return [(key, values[key]) for key in names if key in values]
        with patch.object(claude_setup, '_archive_members', side_effect=members):
            self.assertTrue(claude_setup.background_start_supported(self.executable))

    def test_existing_process_never_launches_or_inspects_startup_capabilities(self):
        with patch.object(claude_setup, '_main_pids', return_value=[42]), \
             patch.object(claude_setup, 'background_start_supported') as supported, \
             patch.object(claude_setup.subprocess, 'Popen') as spawn:
            self.assertEqual(claude_setup.background_running_app(self.executable, self.root), {'pid': 42, 'launched': False})
        supported.assert_not_called(); spawn.assert_not_called()

    def test_store_launch_uses_verified_alias_and_never_inherits_node_mode(self):
        self.manifest()
        with patch.object(claude_setup, '_main_pids', side_effect=[[], [], [42]]), \
             patch.object(claude_setup, 'background_start_supported', return_value=True), \
             patch.object(windows_discovery, 'application_execution_alias', return_value=self.alias), \
             patch.dict(os.environ, {'ELECTRON_RUN_AS_NODE': '1'}), \
             patch.object(claude_setup.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch.object(claude_setup.subprocess, 'Popen') as spawn:
            self.assertEqual(claude_setup.background_running_app(self.executable, self.root), {'pid': 42, 'launched': True})
        self.assertEqual(spawn.call_args.args[0], [str(self.alias), '--startup'])
        self.assertNotIn('ELECTRON_RUN_AS_NODE', spawn.call_args.kwargs['env'])
        self.assertEqual(spawn.call_args.kwargs['creationflags'], 0x08000000)

    def test_process_opened_during_discovery_is_not_reactivated(self):
        with patch.object(claude_setup, '_main_pids', side_effect=[[], [42]]), \
             patch.object(claude_setup, 'background_start_supported', return_value=True), \
             patch.object(claude_setup.subprocess, 'Popen') as spawn:
            self.assertEqual(claude_setup.background_running_app(self.executable, self.root)['launched'], False)
        spawn.assert_not_called()

    def test_unknown_startup_capability_and_unverified_alias_do_not_fall_back(self):
        with patch.object(claude_setup, '_main_pids', return_value=[]), \
             patch.object(claude_setup, 'background_start_supported', return_value=False), \
             patch.object(claude_setup.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '尚未验证后台启动'):
                claude_setup.background_running_app(self.executable, self.root)
        spawn.assert_not_called()
        self.manifest()
        with patch.object(claude_setup, '_main_pids', return_value=[]), \
             patch.object(claude_setup, 'background_start_supported', return_value=True), \
             patch.object(windows_discovery, 'application_execution_alias', return_value=None), \
             patch.object(claude_setup.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '别名'):
                claude_setup.background_running_app(self.executable, self.root)
        spawn.assert_not_called()

    def test_cancel_under_launch_gate_prevents_process_creation(self):
        cancel = threading.Event()
        gate = Mock()
        gate.__enter__ = Mock(side_effect=cancel.set)
        gate.__exit__ = Mock(return_value=False)
        with patch.object(claude_setup, '_main_pids', return_value=[]), \
             patch.object(claude_setup, 'background_start_supported', return_value=True), \
             patch.object(claude_setup.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '取消'):
                claude_setup.background_running_app(self.executable, self.root, cancelled=cancel, launch_gate=gate)
        spawn.assert_not_called()


class ClaudeBackgroundWorker(unittest.TestCase):
    def setUp(self):
        platform = patch('bridge.clients.claude.adapter.sys.platform', 'win32')
        platform.start(); self.addCleanup(platform.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = Claude(Path(self.temp.name)/'adapter', auto_connect=False)
        self.addCleanup(self.adapter.close)
        self.adapter.discovery = {'installed': True, 'automaticConnection': 'native-console',
                                  'executable': 'fixture-Claude.exe', 'dataHome': self.temp.name}
        self.adapter.reconnect()

    def test_explicit_enable_starts_background_worker_but_never_native_console(self):
        with patch('bridge.clients.claude.adapter.sys.platform', 'win32'), \
             patch('bridge.clients.claude.adapter.threading.Thread') as thread, \
             patch('bridge.clients.claude.adapter.native_action') as native:
            result = self.adapter.reconnect(launch=True)
        self.assertEqual(result['setupState'], 'starting')
        self.assertEqual(thread.call_args.kwargs['target'], self.adapter._connect_background)
        self.assertEqual(self.adapter.setup_mode, 'background')
        native.assert_not_called()
        self.adapter.setup_thread = None

    def test_cold_start_without_developer_mode_reports_required_user_choice(self):
        with patch('bridge.clients.claude.adapter.background_running_app', return_value={'pid': 42, 'launched': True}), \
             patch('bridge.clients.claude.adapter.claude_data_home', return_value=self.temp.name), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=False), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False) as wait, \
             patch('bridge.clients.claude.adapter.native_action') as native:
            self.adapter._connect_background(self.adapter.setup_cancel, 'fixture', self.temp.name)
        self.assertEqual(wait.call_count, 5)
        self.assertEqual(self.adapter.status()['setupState'], 'needs-developer-mode')
        self.assertIn('开发者模式', self.adapter.status()['reason'])
        native.assert_not_called()

    def test_cancelled_or_replaced_worker_cannot_overwrite_current_state(self):
        old = self.adapter.setup_cancel
        self.adapter.cancel()
        self.assertFalse(self.adapter._background_state(old, 'connecting', 'stale'))
        self.assertEqual(self.adapter.status()['setupState'], 'cancelled')
        self.adapter.setup_cancel = threading.Event()
        self.assertFalse(self.adapter._background_state(old, 'failed', 'stale'))

    def test_explicit_initialization_takes_over_background_wait_once(self):
        cancel = self.adapter.setup_cancel
        entered = threading.Event()
        def background():
            entered.set(); cancel.wait(5)
        worker = threading.Thread(target=background)
        self.adapter.setup_thread = worker; self.adapter.setup_mode = 'background'
        worker.start(); self.assertTrue(entered.wait(1))
        with patch('bridge.clients.claude.adapter.threading.Thread') as native, \
             patch('bridge.clients.claude.adapter.sys.platform', 'win32'):
            self.adapter.connect()
        self.assertFalse(worker.is_alive())
        self.assertTrue(cancel.is_set())
        self.assertEqual(native.call_args.kwargs['target'], self.adapter._connect_background)
        self.assertIs(native.call_args.kwargs['args'][0], self.adapter.setup_cancel)
        self.assertTrue(native.call_args.kwargs['kwargs']['foreground'])
        native.return_value.start.assert_called_once_with()
        self.assertEqual(self.adapter.setup_mode, 'background')
        self.adapter.setup_thread = None


if __name__ == '__main__':
    unittest.main()
