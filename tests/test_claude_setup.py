import json
import os
import plistlib
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.claude.adapter import Claude
import bridge.clients.claude.setup as claude_setup
from bridge.clients.claude.setup import _main_pids, console_source, inspect_installation, native_action, running_app


ROOT = Path(__file__).resolve().parents[1]


class ClaudeDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root/'Claude.exe'
        self.executable.write_bytes(b'MZfixture')

    def archive(self, source):
        files = [('package.json', json.dumps({'version': '2.26454.0'})),
                 ('.vite/build/index.pre.js', source)]
        tree = {'files': {}}
        data = b''
        for name, content in files:
            parts = name.split('/')
            parent = tree
            for part in parts[:-1]:
                parent = parent['files'].setdefault(part, {'files': {}})
            raw = content.encode()
            parent['files'][parts[-1]] = {'size': len(raw), 'offset': str(len(data))}
            data += raw
        raw = json.dumps(tree).encode()
        size = 8 + len(raw)
        path = self.root/'resources/app.asar'
        path.parent.mkdir()
        path.write_bytes(struct.pack('<4I', 4, size, size - 4, len(raw)) + raw + data)

    @patch('bridge.clients.claude.setup.sys.platform', 'win32')
    def test_cdp_auth_gate_does_not_disable_native_console_connection(self):
        self.archive('remote-debugging-port; CLAUDE_CDP_AUTH; refusing to start')
        value = inspect_installation(self.executable)
        self.assertTrue(value['installed'])
        self.assertEqual(value['version'], '2.26454.0')
        self.assertEqual(value['automaticConnection'], 'native-console')
        self.assertEqual(value['setupState'], 'ready-to-connect')

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_unknown_package_does_not_claim_connectivity(self):
        value = inspect_installation(self.executable)
        self.assertTrue(value['installed'])
        self.assertEqual(value['automaticConnection'], 'native-console')
        self.assertEqual(value['setupState'], 'ready-to-connect')
        self.assertNotIn('connected', value)

    @patch('bridge.clients.claude.setup.sys.platform', 'linux')
    def test_platform_without_native_helper_stays_unsupported(self):
        self.assertEqual(inspect_installation(self.executable)['setupState'], 'unsupported')

    def test_missing_app_can_be_found_on_a_later_scan(self):
        self.executable.unlink()
        self.assertFalse(inspect_installation(self.executable)['installed'])
        self.executable.write_bytes(b'MZfixture')
        self.assertTrue(inspect_installation(self.executable)['installed'])

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_macos_bundle_retains_executable_and_version_discovery(self):
        bundle = self.root/'Claude.app'
        executable = bundle/'Contents/MacOS/Claude'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'fixture')
        (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': 'Claude', 'CFBundleShortVersionString': '2.26454.0'}))
        value = inspect_installation(bundle)
        self.assertEqual(value['executable'], str(executable.resolve()))
        self.assertEqual(value['version'], '2.26454.0')
        self.assertEqual(value['setupState'], 'ready-to-connect')

    def test_gateway_stopped_setup_persists_without_launching_or_creating_script(self):
        adapter = Claude(self.root/'adapter')
        with patch.object(adapter, 'prepare') as prepare:
            result = adapter.configure(self.executable, self.root/'profile')
            prepare.assert_not_called()
        self.assertFalse(result['connected'])
        self.assertFalse(result['configured'])
        self.assertFalse((adapter.directory/'connect-desktop.js').exists())
        other = Claude(adapter.directory)
        self.addCleanup(other.close)
        self.assertEqual(other.status()['executable'], str(self.executable))
        self.assertIsNone(other.loop)

    def test_scan_preserves_a_verified_existing_connection(self):
        adapter = Claude(self.root/'adapter')
        adapter.desktop = Mock(connected=True, capabilities={'code': ['getAll']})
        value = adapter.configure(self.executable)
        self.assertTrue(value['connected'])
        self.assertTrue(value['configured'])
        self.assertEqual(value['setupState'], 'connected')
        adapter.desktop.close.assert_not_called()


class ClaudeProfileEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root/'Applications/Claude.app/Contents/MacOS/Claude'
        self.default = self.root/'Library/Application Support/Claude'
        self.third = self.default.with_name('Claude-3p')

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_only_selected_executable_open_storage_establishes_profile(self):
        other = self.root/'Other/Claude.app/Contents/MacOS/Claude'
        processes = Mock(stdout=f' 42 {self.executable}\n 51 {other}\n')
        files = Mock(stdout=f'p42\nn{self.third}/Local Storage/leveldb/LOCK\n'
                            f'n{self.third}/Session Storage/LOCK\n'
                            f'n{self.default}/claude_desktop_config.json\n')
        with patch('bridge.platforms.macos.claude.subprocess.run', side_effect=[processes, files]) as run:
            self.assertEqual(claude_setup.claude_data_home(self.executable, self.default), self.third)
        self.assertEqual(run.call_args.args[0], ['/usr/sbin/lsof', '-a', '-p', '42', '-Fn'])

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_another_installation_cannot_lend_its_profile(self):
        processes = Mock(stdout=f'51 {self.root}/Other/Claude.app/Contents/MacOS/Claude\n')
        with patch('bridge.platforms.macos.claude.subprocess.run', return_value=processes) as run:
            self.assertEqual(claude_setup.claude_data_home(self.executable, self.default), self.default)
        self.assertEqual(run.call_count, 1)

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_conflicting_storage_roots_do_not_guess(self):
        files = Mock(stdout=f'p42\nn{self.third}/Session Storage/LOCK\nn{self.default}/Local Storage/leveldb/LOCK\n')
        with patch('bridge.clients.claude.setup.DesktopApp.processes', return_value=[42]), \
             patch('bridge.platforms.macos.claude.subprocess.run', return_value=files):
            self.assertEqual(claude_setup.claude_data_home(self.executable, self.default), self.default)

    def test_unavailable_process_inspection_retains_default(self):
        for error in (PermissionError('fixture'), subprocess.TimeoutExpired('lsof', 10)):
            with self.subTest(error=type(error).__name__), \
                 patch('bridge.clients.claude.setup._running_profiles', side_effect=error):
                self.assertEqual(claude_setup.claude_data_home(self.executable, self.default), self.default)

    def test_other_platforms_retain_existing_profile_without_mac_tools(self):
        with patch('bridge.clients.claude.setup.subprocess.run') as run:
            for platform in ('win32', 'linux'):
                self.assertEqual(claude_setup.claude_data_home(self.executable, self.default, platform), self.default)
        run.assert_not_called()


class ClaudeNativeConnection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = Claude(self.root/'adapter')
        self.adapter.directory.mkdir()
        profile = patch('bridge.clients.claude.setup._running_profiles', return_value=set())
        profile.start(); self.addCleanup(profile.stop)
        self.adapter.discovery = {'installed': True, 'executable': '/Applications/Claude.app/Contents/MacOS/Claude',
                                  'dataHome': str(self.root/'profile'), 'automaticConnection': 'native-console'}
        self.cancel_path = self.root/'cancel'

    def test_inactive_desktop_waits_without_launching_console_and_resumes_once(self):
        for blocked in ('needs-screen-saver', 'needs-unlock', 'needs-desktop'):
            with self.subTest(blocked=blocked):
                self.adapter.desktop = Mock(connected=False, capabilities={})
                actions = []
                def action(name, **kwargs):
                    actions.append(name)
                    if name == 'check':
                        return {'setupState': blocked if len(actions) == 1 else 'ready', 'reason': blocked}
                    if name == 'wait-desktop':
                        running.assert_not_called(); prepare.assert_not_called()
                        self.assertEqual(self.adapter.status()['setupState'], blocked)
                        return {'setupState': 'ready'}
                    if name == 'connect':
                        self.adapter.desktop.connected = True
                        return {'setupState': 'submitted'}
                    return {'setupState': 'connected'}
                with patch('bridge.clients.claude.adapter.native_action', side_effect=action), \
                     patch('bridge.clients.claude.adapter.running_app', return_value=42) as running, \
                     patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
                     patch.object(self.adapter, 'prepare', return_value={'consolePath': '/fixture'}) as prepare, \
                     patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
                    self.adapter._connect_native(False, self.cancel_path)
                self.assertEqual(actions, ['check', 'wait-desktop', 'check', 'connect', 'close-devtools'])
                self.assertTrue(self.adapter.status()['connected'])

    def test_existing_connector_can_recover_while_desktop_remains_in_screen_saver(self):
        self.adapter.desktop = Mock(connected=False, capabilities={})
        def action(name, **kwargs):
            if name == 'check': return {'setupState': 'needs-screen-saver', 'reason': 'screen saver'}
            self.assertEqual(name, 'wait-desktop')
            self.assertFalse(kwargs['recovered']())
            self.adapter.desktop.connected = True
            self.assertTrue(kwargs['recovered']())
            return {'setupState': 'connected'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=action) as native, \
             patch('bridge.clients.claude.adapter.running_app') as running, \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual([call.args[0] for call in native.call_args_list], ['check', 'wait-desktop'])
        running.assert_not_called()
        self.assertTrue(self.adapter.status()['connected'])

    def test_screen_saver_during_console_setup_is_preserved_until_resume(self):
        self.adapter.desktop = Mock(connected=False, capabilities={})
        actions = []
        def action(name, **kwargs):
            actions.append(name)
            if name == 'connect':
                if actions.count('connect') == 1:
                    return {'setupState': 'needs-screen-saver', 'reason': 'screen saver'}
                self.adapter.desktop.connected = True
                return {'setupState': 'submitted'}
            if name == 'wait-desktop':
                self.assertEqual(self.adapter.status()['setupState'], 'needs-screen-saver')
            return {'setupState': 'ready' if name != 'close-devtools' else 'connected'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=action), \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
             patch.object(self.adapter, 'prepare', return_value={'consolePath': '/fixture'}), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual(actions, ['check', 'connect', 'wait-desktop', 'check', 'connect', 'close-devtools'])

    def test_cancel_during_screen_saver_wait_never_starts_claude(self):
        self.adapter.desktop = Mock(connected=False, capabilities={})
        def action(name, **kwargs):
            if name == 'wait-desktop': self.adapter.setup_cancel.set()
            return {'setupState': 'needs-unlock', 'reason': 'locked'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=action) as native, \
             patch('bridge.clients.claude.adapter.running_app') as running, \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual([call.args[0] for call in native.call_args_list], ['check', 'wait-desktop'])
        running.assert_not_called()
        self.assertEqual(self.adapter.status()['setupState'], 'cancelled')

    def test_remote_reconnect_does_not_launch_claude_after_screen_saver_wait(self):
        self.adapter.desktop = Mock(connected=False, capabilities={}, unconfirmed_mutations=set())
        self.adapter.desktop.lock.locked.return_value = False
        states = iter([{'setupState': 'needs-screen-saver', 'reason': 'screen saver'},
                       {'setupState': 'ready'}, {'setupState': 'ready'}])
        def existing(executable, data_home, **kwargs):
            self.assertFalse(kwargs['allow_launch'])
            raise ValueError('请先在电脑端打开 Claude，再重新连接')
        with patch('bridge.clients.claude.adapter.native_action', side_effect=lambda *a, **k: next(states)), \
             patch('bridge.clients.claude.adapter.running_app', side_effect=existing), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter._connect_native(False, self.cancel_path, existing_only=True)
        self.assertEqual(self.adapter.status()['setupState'], 'failed')
        self.assertEqual(self.adapter.status()['reason'], '请先在电脑端打开 Claude，再重新连接')

    def test_permission_prompt_does_not_launch_or_inject_without_permission(self):
        action = Mock(return_value={'setupState': 'needs-permission', 'reason': 'permission required'})
        def wait(_):
            self.adapter.setup_cancel.set()
            return True
        with patch('bridge.clients.claude.adapter.native_action', action), \
             patch('bridge.clients.claude.adapter.running_app') as running, \
             patch.object(self.adapter.setup_cancel, 'wait', side_effect=wait):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual([c.args[0] for c in action.call_args_list], ['check', 'request-permission'])
        running.assert_not_called()
        self.assertFalse(self.adapter.status()['configured'])

    def test_developer_confirmation_is_left_pending_without_creating_bridge(self):
        action = Mock(side_effect=[{'setupState': 'ready'}, {'setupState': 'needs-developer-mode'}])
        with patch('bridge.clients.claude.adapter.native_action', action), \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=False), \
             patch.object(self.adapter, 'prepare') as prepare, \
             patch.object(self.adapter, '_setup_state', wraps=self.adapter._setup_state) as state, \
             patch.object(self.adapter.setup_cancel, 'wait', side_effect=lambda _: self.adapter.setup_cancel.set()):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual([c.args[0] for c in action.call_args_list], ['check', 'enable-devtools'])
        self.assertTrue(any(c.args[0] == 'needs-developer-mode' for c in state.call_args_list))
        prepare.assert_not_called()

    def test_first_launch_refreshes_profile_before_developer_mode_check(self):
        active = self.root/'Claude-3p'; active.mkdir(); (active/'developer_settings.json').write_text('{"allowDevTools":true}')
        observed = []
        def developer(path):
            observed.append(str(path)); self.adapter.setup_cancel.set()
            return claude_setup.developer_mode_enabled(path)
        with patch('bridge.clients.claude.adapter.native_action', return_value={'setupState': 'ready'}) as native, \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.setup._running_profiles', return_value={active}, create=True), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', side_effect=developer):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual(observed, [str(active)])
        self.assertNotIn('enable-devtools', [call.args[0] for call in native.call_args_list])

    def test_old_true_flag_cannot_authorize_active_third_party_profile(self):
        legacy = Path(self.adapter.discovery['dataHome']); legacy.mkdir(); (legacy/'developer_settings.json').write_text('{"allowDevTools":true}')
        active = self.root/'Claude-3p'; active.mkdir()
        observed = []
        def developer(path):
            result = claude_setup.developer_mode_enabled(path); observed.append(result)
            self.adapter.setup_cancel.set(); return result
        with patch('bridge.clients.claude.adapter.native_action', return_value={'setupState': 'needs-developer-mode'}) as native, \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.setup._running_profiles', return_value={active}, create=True), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', side_effect=developer):
            native.side_effect = [{'setupState': 'ready'}, {'setupState': 'needs-developer-mode'}]
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual(observed, [False])

    def test_explicit_home_is_not_changed_after_launch(self):
        self.adapter.discovery['dataHomeExplicit'] = True
        selected = self.adapter.discovery['dataHome']
        def developer(path):
            self.assertEqual(str(path), selected); self.adapter.setup_cancel.set(); return True
        with patch('bridge.clients.claude.adapter.native_action', return_value={'setupState': 'ready'}), \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.setup._running_profiles', create=True) as profiles, \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', side_effect=developer):
            self.adapter._connect_native(False, self.cancel_path)
        profiles.assert_not_called()

    def run_injection(self, heartbeat, trust=False):
        self.adapter.desktop = Mock(connected=False, capabilities={})
        actions = []
        def native(action, **kwargs):
            actions.append(action)
            if action == 'connect':
                self.assertFalse(self.adapter.status()['connected'])
                self.adapter.desktop.connected = heartbeat
                return {'setupState': 'submitted'}
            if action == 'close-devtools':
                self.adapter.setup_cancel.set()
                return {'setupState': 'connected'}
            if action == 'inspect-error':
                return {'setupState': 'needs-trust' if trust else 'failed', 'reason': 'trust required'}
            return {'setupState': 'ready'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=native), \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
             patch('bridge.clients.claude.adapter.time.monotonic', side_effect=[0, 0 if heartbeat else 19]), \
             patch.object(self.adapter, 'prepare', return_value={'consolePath': str(self.root/'script')}), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False), \
             patch.object(self.adapter, '_setup_state', wraps=self.adapter._setup_state) as state:
            self.adapter._connect_native(False, self.cancel_path)
        return actions, [c.args[0] for c in state.call_args_list]

    def test_submitted_script_without_heartbeat_never_reports_connected(self):
        actions, states = self.run_injection(False)
        self.assertIn('connect', actions)
        self.assertNotIn('close-devtools', actions)
        self.assertNotIn('connected', states)
        self.assertEqual(self.adapter.status()['setupState'], 'failed')

    def test_trust_failure_reports_native_user_action(self):
        _, states = self.run_injection(False, trust=True)
        self.assertNotIn('connected', states)
        self.assertEqual(self.adapter.status()['setupState'], 'needs-trust')

    def test_only_verified_heartbeat_allows_connected_and_console_cleanup(self):
        actions, states = self.run_injection(True)
        self.assertEqual(actions, ['check', 'connect', 'close-devtools'])
        self.assertIn('connected', states)

    def test_restart_refuses_active_tasks_before_stopping_monitor(self):
        self.adapter.desktop = Mock(connected=True, capabilities={})
        self.adapter.setup_thread = Mock()
        with patch.object(self.adapter, 'call', return_value={'sessions': [{'status': 'active'}]}):
            with self.assertRaisesRegex(ValueError, '任务运行'):
                self.adapter.connect(restart=True)
        self.assertFalse(self.adapter.setup_cancel.is_set())
        self.adapter.setup_thread.join.assert_not_called()

    def test_idle_restart_replaces_existing_connection_monitor(self):
        self.adapter.desktop = Mock(connected=True, capabilities={})
        monitor = self.adapter.setup_thread = Mock()
        monitor.is_alive.side_effect = [True, False]
        with patch.object(self.adapter, 'call', return_value={'sessions': []}), \
             patch('bridge.clients.claude.adapter.threading.Thread') as worker:
            self.adapter.connect(restart=True)
        monitor.join.assert_called_once_with(timeout=5)
        worker.return_value.start.assert_called_once()
        self.assertFalse(self.adapter.setup_cancel.is_set())
        self.assertTrue(worker.call_args.kwargs['args'][0])

    def test_handoff_restores_explicit_connection_intent(self):
        self.adapter.prepare()
        with patch('bridge.clients.claude.adapter.threading.Thread'):
            self.adapter.connect()
        # Use the real owner thread for close; the setup monitor is mocked.
        self.adapter.close()
        with patch.object(Claude, 'connect') as resume:
            other = Claude(self.adapter.directory)
        self.addCleanup(other.close)
        resume.assert_called_once_with()
        self.assertTrue(other.discovery['autoConnect'])
        self.assertIsNotNone(other.desktop)

    def test_disabled_client_does_not_restore_stale_connection_intent(self):
        (self.adapter.directory/'connection.json').write_text('{"token":"fixture"}')
        (self.adapter.directory/'discovery.json').write_text(json.dumps({**self.adapter.discovery, 'autoConnect': True}))
        with patch.object(Claude, 'prepare') as prepare, patch.object(Claude, 'connect') as connect:
            other = Claude(self.adapter.directory, auto_connect=False)
        self.addCleanup(other.close)
        prepare.assert_not_called(); connect.assert_not_called()
        self.assertIsNone(other.desktop)

    def test_connect_waits_for_cancelled_monitor_before_restoring_connection(self):
        self.adapter.setup_cancel.set()
        monitor = self.adapter.setup_thread = Mock()
        monitor.is_alive.return_value = False
        with patch('bridge.clients.claude.adapter.threading.Thread') as worker:
            self.adapter.connect()
        monitor.join.assert_called_once_with(timeout=5)
        worker.return_value.start.assert_called_once()
        self.assertFalse(self.adapter.setup_cancel.is_set())
        self.assertTrue(self.adapter.discovery['autoConnect'])

    def test_still_exiting_cancelled_monitor_cannot_report_successful_reconnect(self):
        self.adapter.setup_cancel.set()
        monitor = self.adapter.setup_thread = Mock()
        monitor.is_alive.return_value = True
        with patch('bridge.clients.claude.adapter.threading.Thread') as worker:
            with self.assertRaisesRegex(ValueError, '取消'):
                self.adapter.connect()
        monitor.join.assert_called_once_with(timeout=5)
        worker.assert_not_called()

    def test_explicit_cancel_survives_handoff_without_reactivating_bridge(self):
        self.adapter.prepare()
        with patch('bridge.clients.claude.adapter.threading.Thread'):
            self.adapter.connect()
        self.adapter.cancel()
        self.adapter.close()
        with patch.object(Claude, 'connect') as resume:
            other = Claude(self.adapter.directory)
        self.addCleanup(other.close)
        resume.assert_not_called()
        self.assertFalse(other.discovery['autoConnect'])
        self.assertIsNone(other.desktop)
        self.assertFalse(other.status()['connected'])

    def test_signed_handoff_heartbeat_does_not_invoke_native_ui(self):
        self.adapter.desktop = Mock(connected=False, capabilities={})
        def wait(seconds):
            if seconds == .2:
                self.adapter.desktop.connected = True
            else:
                self.adapter.setup_cancel.set()
            return False
        with patch('bridge.clients.claude.adapter.native_action') as native, \
             patch.object(self.adapter.setup_cancel, 'wait', side_effect=wait):
            self.adapter._connect_native(False, self.cancel_path)
        native.assert_not_called()

    def test_old_signed_connector_is_reinjected_without_restarting_claude(self):
        from bridge.clients.claude.mailbox import CONNECTOR_REVISION
        import time
        self.adapter.prepare()
        def heartbeat(revision=None):
            desktop = self.adapter.desktop
            value = {'generation': desktop.generation, 'timestamp': time.time(),
                     'connected': True, 'surfaces': {'code': ['getAll']}}
            if revision is not None: value['connectorRevision'] = revision
            (self.adapter.directory/'response.json').write_text(desktop.pack(value))
        for revision in (None, CONNECTOR_REVISION - 1, CONNECTOR_REVISION + 1):
            heartbeat(revision)
            self.assertFalse(self.adapter.desktop.connected)
            self.assertEqual(self.adapter.desktop.capabilities, {})
        prepare = self.adapter.prepare
        def replaced(reset=False):
            value = prepare(reset=reset)
            heartbeat()  # The old running injector follows the new generation.
            return value
        actions = []
        def native(action, **kwargs):
            actions.append(action)
            if action == 'connect':
                self.assertFalse(self.adapter.desktop.connected)
                heartbeat(CONNECTOR_REVISION)
                return {'setupState': 'submitted'}
            if action == 'close-devtools': self.adapter.setup_cancel.set()
            return {'setupState': 'ready'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=native), \
             patch('bridge.clients.claude.adapter.running_app', return_value=42) as running, \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False), \
             patch.object(self.adapter, 'prepare', side_effect=replaced), \
             patch.object(self.adapter, '_setup_state', wraps=self.adapter._setup_state) as state:
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual(actions, ['check', 'connect', 'close-devtools'])
        self.assertFalse(running.call_args.kwargs['restart'])
        self.assertIn('connected', [call.args[0] for call in state.call_args_list])

    def test_current_signed_connector_recovers_without_native_ui(self):
        from bridge.clients.claude.mailbox import CONNECTOR_REVISION
        import time
        self.adapter.prepare()
        desktop = self.adapter.desktop
        (self.adapter.directory/'response.json').write_text(desktop.pack({
            'generation': desktop.generation, 'timestamp': time.time(), 'connected': True,
            'connectorRevision': CONNECTOR_REVISION, 'surfaces': {'code': ['getAll']}}))
        self.assertTrue(desktop.connected)
        def wait(seconds):
            self.adapter.setup_cancel.set()
            return False
        with patch('bridge.clients.claude.adapter.native_action') as native, \
             patch.object(self.adapter.setup_cancel, 'wait', side_effect=wait):
            self.adapter._connect_native(False, self.cancel_path)
        native.assert_not_called()

    def test_successful_connection_does_not_reopen_console_after_later_heartbeat_loss(self):
        self.adapter.desktop = Mock(connected=True, capabilities={})
        def wait(_):
            self.adapter.desktop.connected = False
            return False
        with patch('bridge.clients.claude.adapter.native_action', return_value={'setupState': 'failed'}) as native, \
             patch.object(self.adapter.setup_cancel, 'wait', side_effect=wait):
            self.adapter._connect_native(False, self.cancel_path)
        native.assert_not_called()
        self.adapter.desktop.connected = False
        self.assertEqual(self.adapter.status()['setupState'], 'needs-retry')

    def test_connect_restores_existing_file_bridge_before_native_setup(self):
        (self.adapter.directory/'connection.json').write_text('{"token":"fixture"}')
        with patch.object(self.adapter, 'prepare') as prepare, \
             patch('bridge.clients.claude.adapter.threading.Thread'):
            self.adapter.connect()
        prepare.assert_called_once_with()

    def test_connected_cleanup_notice_survives_status_rendering(self):
        self.adapter.desktop = Mock(connected=True, capabilities={})
        reason = 'Claude 已连接；开发者工具未自动关闭，请手动关闭'
        self.adapter._setup_state('connected', reason, consoleCleanupPending=True)
        self.assertEqual(self.adapter.status()['reason'], reason)

    def test_cleanup_failure_keeps_verified_connection_with_an_actionable_notice(self):
        self.adapter.desktop = Mock(connected=False, capabilities={})
        def native(action, **kwargs):
            if action == 'connect':
                self.adapter.desktop.connected = True
                return {'setupState': 'submitted'}
            if action == 'close-devtools':
                return {'setupState': 'needs-attention', 'reason': 'window unavailable'}
            return {'setupState': 'ready'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=native) as action, \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
             patch.object(self.adapter, 'prepare', return_value={'consolePath': str(self.root/'script')}), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual([call.args[0] for call in action.call_args_list], ['check', 'connect', 'close-devtools'])
        self.assertTrue(self.adapter.status()['connected'])
        self.assertTrue(self.adapter.status()['consoleCleanupPending'])
        self.assertIn('手动关闭', self.adapter.status()['reason'])

    def test_user_interruption_requires_explicit_retry_without_reopening_console(self):
        self.adapter.desktop = Mock(connected=False, capabilities={})
        interrupted = {'setupState': 'needs-attention', 'reason': '焦点已离开 Claude，自动连接已停止，请重试'}
        with patch('bridge.clients.claude.adapter.native_action', side_effect=[{'setupState': 'ready'}, interrupted]) as native, \
             patch('bridge.clients.claude.adapter.running_app', return_value=42), \
             patch('bridge.clients.claude.adapter.developer_mode_enabled', return_value=True), \
             patch.object(self.adapter, 'prepare', return_value={'consolePath': str(self.root/'script')}), \
             patch.object(self.adapter.setup_cancel, 'wait', return_value=False):
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual([call.args[0] for call in native.call_args_list], ['check', 'connect'])
        self.assertEqual(self.adapter.status()['setupState'], 'needs-retry')

    def test_repeated_scan_and_connect_reuse_verified_connector_generation(self):
        from bridge.clients.claude.mailbox import CONNECTOR_REVISION
        import time
        self.adapter.prepare()
        desktop = self.adapter.desktop
        (self.adapter.directory/'response.json').write_text(desktop.pack({
            'generation': desktop.generation, 'timestamp': time.time(), 'connected': True,
            'connectorRevision': CONNECTOR_REVISION, 'surfaces': {'code': ['getAll']}}))
        discovered = dict(self.adapter.discovery)
        with patch('bridge.clients.claude.adapter.inspect_installation', return_value=discovered), \
             patch('bridge.clients.claude.adapter.native_action') as native, \
             patch.object(self.adapter, 'prepare', wraps=self.adapter.prepare) as prepare:
            for _ in range(5):
                self.adapter.configure(discovered['executable'], discovered['dataHome'])
                self.assertTrue(self.adapter.connect()['connected'])
        self.assertIs(self.adapter.desktop, desktop)
        prepare.assert_not_called();native.assert_not_called()

    def test_close_during_app_launch_prevents_script_creation(self):
        def launched(*args, **kwargs):
            self.adapter.cancel(persist=False)
            return 42
        with patch('bridge.clients.claude.adapter.native_action', return_value={'setupState': 'ready'}) as native, \
             patch('bridge.clients.claude.adapter.running_app', side_effect=launched), \
             patch.object(self.adapter, 'prepare') as prepare:
            self.adapter._connect_native(False, self.cancel_path)
        self.assertEqual([c.args[0] for c in native.call_args_list], ['check'])
        prepare.assert_not_called()

    def test_reset_preserves_token_but_rejects_previous_signed_generation(self):
        self.addCleanup(self.adapter.close)
        self.adapter.prepare()
        old = self.adapter.desktop
        old_response = old.pack({'generation': old.generation, 'connected': True})
        prepared = self.adapter.prepare(reset=True)
        self.assertEqual(self.adapter.desktop.token, old.token)
        self.assertNotEqual(self.adapter.desktop.generation, old.generation)
        (self.adapter.directory/'response.json').write_text(old_response)
        self.assertFalse(self.adapter.status()['connected'])
        source = Path(prepared['consolePath']).read_text()
        self.assertNotIn('\n', source)
        self.assertIn(self.adapter.desktop.generation, source)

    def test_transformed_console_script_preserves_file_protocol_behavior(self):
        source = (ROOT/'bridge/clients/claude/connector.js').read_text()
        config = {'cwd': 'D:/fixture', 'request': 'request.json', 'response': 'response.json',
                  'token': 'test', 'generation': 'run', 'reconnect': True, 'connectorRevision': 4}
        target = self.root/'connector.js'
        target.write_text(console_source(source, config))
        completed = subprocess.run(['node', 'tests/claude-connector.test.cjs'], cwd=ROOT,
                                   env={**os.environ, 'CONNECTOR_TEMPLATE': str(target)},
                                   capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_console_transform_preserves_configuration_string_whitespace(self):
        config = {'cwd': 'C:/two  spaces/中文', 'token': 'private'}
        self.assertIn(json.dumps(config, ensure_ascii=True), console_source('const config=__BRIDGE_CONFIG__;', config))


class NativeHelperProcess(unittest.TestCase):
    def test_helper_resource_paths_follow_source_and_frozen_roots(self):
        for platform, name in (('darwin', 'claude-bridge-helper'), ('win32', 'claude-bridge-helper.exe')):
            for frozen in (False, True):
                with self.subTest(platform=platform, frozen=frozen), \
                     patch('bridge.clients.claude.setup.sys.platform', platform), \
                     patch('bridge.clients.claude.setup.sys.frozen', frozen, create=True), \
                     patch('bridge.clients.claude.setup.project_root', return_value=ROOT):
                    expected = ROOT if frozen else ROOT/'dist'
                    self.assertEqual(claude_setup.helper_path(), expected/'client-helpers'/name)

    def test_existing_only_refuses_to_launch_a_closed_claude(self):
        with patch('bridge.clients.claude.setup.DesktopApp') as app, \
             patch('bridge.clients.claude.setup._main_pids', return_value=[]), \
             patch('bridge.clients.claude.setup.subprocess.run') as run, \
             patch('bridge.clients.claude.setup.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '请先在电脑端打开 Claude'):
                running_app('/Claude', '/profile', allow_launch=False)
        run.assert_not_called(); spawn.assert_not_called(); app.return_value.stop.assert_not_called()

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_screen_saver_wait_uses_one_read_only_helper_without_normal_timeout(self):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired('helper', 1)] * 3 + [('{"setupState":"ready"}', '')]
        with patch('bridge.clients.claude.setup.helper_path', return_value=Path(__file__)), \
             patch('bridge.clients.claude.setup.subprocess.Popen', return_value=process) as spawn, \
             patch('bridge.clients.claude.setup.time.monotonic', side_effect=AssertionError('Wait must not expire and respawn')):
            self.assertEqual(native_action('wait-desktop')['setupState'], 'ready')
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0], [__file__, '--wait-desktop'])
        process.terminate.assert_not_called()

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_connector_recovery_stops_only_the_desktop_wait_helper(self):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired('helper', 1), ('', '')]
        with patch('bridge.clients.claude.setup.helper_path', return_value=Path(__file__)), \
             patch('bridge.clients.claude.setup.subprocess.Popen', return_value=process):
            self.assertEqual(native_action('wait-desktop', recovered=lambda: True)['setupState'], 'connected')
        process.terminate.assert_called_once(); process.kill.assert_not_called()

    @patch('bridge.clients.claude.setup.sys.platform', 'win32')
    def test_windows_process_discovery_excludes_electron_children(self):
        executable = ROOT/'Claude.exe'
        rows = [{'ProcessId': 42, 'ExecutablePath': str(executable), 'CommandLine': 'Claude.exe'},
                {'ProcessId': 43, 'ExecutablePath': str(executable), 'CommandLine': 'Claude.exe --type=renderer'},
                {'ProcessId': 44, 'ExecutablePath': str(executable), 'CommandLine': 'Claude.exe --type gpu-process'},
                {'ProcessId': 45, 'ExecutablePath': str(ROOT/'Other.exe'), 'CommandLine': 'Other.exe'}]
        with patch('bridge.platforms.windows.claude.subprocess.CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch('bridge.platforms.windows.claude.subprocess.run', return_value=Mock(stdout=json.dumps(rows))):
            self.assertEqual(_main_pids(Mock(executable=executable)), [42])

    @patch('bridge.clients.claude.setup.sys.platform', 'win32')
    def test_windows_explicit_restart_only_closes_main_window(self):
        with patch('bridge.clients.claude.setup.DesktopApp') as app, \
             patch('bridge.clients.claude.setup._main_pids', side_effect=[[42], [], [99]]), \
             patch('bridge.platforms.windows.claude.subprocess.CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch('bridge.platforms.windows.claude.subprocess.run') as run, \
             patch('bridge.clients.claude.setup.subprocess.Popen'), \
             patch('bridge.clients.claude.setup.time.sleep'):
            self.assertEqual(running_app('/Claude', '/profile', restart=True), 99)
        self.assertIn('CloseMainWindow', run.call_args.args[0][-1])
        self.assertIn('-Id 42 ', run.call_args.args[0][-1])
        app.return_value.stop.assert_not_called()

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_macos_start_uses_selected_bundle_without_profile_arguments(self):
        executable = ROOT/'Fixture Claude.app/Contents/MacOS/Claude'
        with patch('bridge.clients.claude.setup.DesktopApp', return_value=Mock(executable=executable)), \
             patch('bridge.clients.claude.setup._main_pids', side_effect=[[], [42]]), \
             patch('bridge.platforms.macos.claude.subprocess.run') as run, \
             patch('bridge.clients.claude.setup.time.sleep'):
            self.assertEqual(running_app(str(executable), '/profile'), 42)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-a', str(ROOT/'Fixture Claude.app')])

    @patch('bridge.clients.claude.setup.sys.platform', 'win32')
    def test_windows_permission_preflight_never_starts_helper(self):
        with patch('bridge.clients.claude.setup.helper_path', return_value=Path(__file__)), \
             patch('bridge.clients.claude.setup.subprocess.Popen') as spawn:
            for action in ('check', 'request-permission'):
                self.assertEqual(native_action(action), {'setupState': 'ready'})
        spawn.assert_not_called()

    def test_cancelled_request_does_not_spawn_helper(self):
        cancel = threading.Event(); cancel.set()
        with patch('bridge.clients.claude.setup.subprocess.Popen') as spawn:
            result = native_action('connect', cancelled=cancel)
        self.assertEqual(result['setupState'], 'cancelled')
        spawn.assert_not_called()

    @patch('bridge.clients.claude.setup.sys.platform', 'darwin')
    def test_cancellation_terminates_only_the_helper(self):
        cancel = threading.Event()
        process = Mock()
        def communicate(**kwargs):
            if not cancel.is_set():
                cancel.set()
                raise subprocess.TimeoutExpired('helper', .2)
            return '', ''
        process.communicate.side_effect = communicate
        with patch('bridge.clients.claude.setup.helper_path', return_value=Path(__file__)), \
             patch('bridge.clients.claude.setup.subprocess.Popen', return_value=process):
            result = native_action('connect', pid=42, executable='/Claude', script='/script',
                                   cancel_path='/cancel', cancelled=cancel)
        self.assertEqual(result['setupState'], 'cancelled')
        process.terminate.assert_called_once()
        process.kill.assert_not_called()

    @patch('bridge.clients.claude.setup.sys.platform', 'win32')
    def test_windows_helper_receives_only_native_cli_arguments(self):
        process = Mock(returncode=0)
        process.communicate.return_value = ('script submitted', '')
        with patch('bridge.clients.claude.setup.helper_path', return_value=Path(__file__)), \
             patch('bridge.clients.claude.setup.subprocess.CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch('bridge.clients.claude.setup.subprocess.Popen', return_value=process) as spawn:
            result = native_action('connect', pid=42, executable='C:/Claude.exe', script='C:/script', cancel_path='C:/cancel')
        self.assertEqual(result['setupState'], 'submitted')
        self.assertEqual(spawn.call_args.args[0], [__file__, '42', 'C:/script', 'C:/Claude.exe', 'C:/cancel'])


if __name__ == '__main__':
    unittest.main()
