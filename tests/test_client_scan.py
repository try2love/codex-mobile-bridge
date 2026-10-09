"""Exercise discovery/setup orchestration without touching native app profiles."""
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.discovery import discover_clients

ROOT = Path(__file__).resolve().parents[1]


class ClaudeProfileDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.home = self.root/'home'
        self.apps = self.root/'Applications'; app = self.apps/'Claude.app'
        (app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/MacOS/Claude').write_bytes(b'fixture')
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.anthropic.claudefordesktop', 'CFBundleExecutable': 'Claude'}))
        self.default = self.home/'Library/Application Support/Claude'
        self.third = self.default.with_name('Claude-3p')

    def scan(self, profiles, preferences=None):
        with patch('bridge.clients.claude.setup._running_profiles', return_value=set(profiles), create=True):
            return discover_clients(preferences, platform='darwin', home=self.home, env={}, applications=[self.apps])['claude']

    def test_live_third_party_profile_is_used_in_scan_output(self):
        self.assertEqual(self.scan([self.third])['dataDirectory'], str(self.third))

    def test_explicit_home_wins_over_running_profile(self):
        value = self.scan([self.third], {'claudeHome': '~/selected-claude'})
        self.assertEqual(value['dataDirectory'], str(self.home/'selected-claude'))
        self.assertTrue(value['dataDirectoryExplicit'])

    def test_existing_third_party_folder_alone_is_not_active_evidence(self):
        self.third.mkdir(parents=True)
        (self.third/'developer_settings.json').write_text('{"allowDevTools":true}')
        self.assertEqual(self.scan([])['dataDirectory'], str(self.default))

    def test_ambiguous_running_profiles_do_not_pick_a_true_flag(self):
        self.assertEqual(self.scan([self.default, self.third])['dataDirectory'], str(self.default))


class FakeDeepSeek:
    def __init__(self, directory, home):
        self.directory, self.home = Path(directory), Path(home)
        self.connected = self.configured = False
        self.changed = True
        self.revision = None
        self.installs = 0
        self.install_error = None

    def discover_existing(self):
        return {'installed': False, 'desktopProfileReady': True}

    def ensure_installed(self):
        self.installs += 1
        if self.install_error:
            raise self.install_error
        changed, self.changed = self.changed, False
        return {'installed': True, 'changed': changed, 'restartRequired': changed}

    def call(self, action, *args):
        if action == 'status':
            if not self.connected:
                raise BridgeUnavailable('not connected')
            return {'connected': True, 'configured': self.configured, 'bridgeRevision': self.revision}
        return {'complete': True, 'bridgeRevision': 3, 'sessions': []}


class ClientScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root/'Codex'
        self.app.write_bytes(b'fixture executable')
        self.accounts = SimpleNamespace(index={'desktopExecutable': str(self.app)},
            info=SimpleNamespace(current=lambda: {'status': 'ready', 'kind': 'chatgpt'}), runtime=None, home=self.root/'codex-home')
        self.bridge = SimpleNamespace(accounts=self.accounts)
        self.bridge.for_host = lambda host: self.bridge
        self.discovered = {key: {'id': key, 'installed': True, 'executable': str(self.root/key),
            'dataDirectory': str(self.root/(key+'-home')), 'desktopProfileReady': True}
            for key in ('codex', 'claude', 'deepseek')}
        self.deepseek = FakeDeepSeek(self.root/'desktop-sessions/deepseek', self.root/'deepseek-home')
        self.claude = Mock()
        self.claude.configure.return_value = {'setupState': 'ready-to-connect', 'reason': '可以连接 Claude'}
        self.claude.connect.return_value = {'setupState': 'needs-permission', 'reason': '请授权辅助功能以连接 Claude'}
        self.claude.status.return_value = {'connected': False}
        self.running = False
        self.connect_on_launch = False
        self.launch = self.patch('bridge.clients.lifecycle.launch_deepseek', side_effect=self.launch_app)
        self.patch('bridge.clients.lifecycle.inspect_client', side_effect=lambda _: {'running': self.running, 'pids': [221, 222] if self.running else [], 'mainPids': [221] if self.running else [], 'runtimePids': [222] if self.running else [], 'unknown': False})
        self.patch('bridge.clients.lifecycle.stop_client', side_effect=lambda *args, **kwargs: setattr(self, 'running', False))
        self.patch('bridge.clients.manager.DeepSeek', return_value=self.deepseek)
        self.patch('bridge.clients.manager.Claude', return_value=self.claude)
        self.discovery = self.patch('bridge.clients.discovery.discover_clients', side_effect=lambda _: self.discovered)
        self.patch('bridge.app.desktop.Desktop.preferences', return_value={})
        self.manager = DesktopSessions(self.root, self.bridge)
        self.addCleanup(self.manager.close)

    def patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def launch_app(self, descriptor, restart=False):
        running = self.running
        self.running = True
        if self.connect_on_launch:
            self.deepseek.connected = self.deepseek.configured = True
        return {'running': True, 'launched': not running or restart, 'restarted': running and restart,
                'restartRequired': running and not restart and descriptor.get('restartRequired', False)}

    def scan(self):
        return {row['id']: row for row in self.manager.scan()['clients']}

    def test_scan_only_discovers_even_for_previously_enabled_clients(self):
        self.manager.config['enabled'] = {'claude': True, 'deepseek': True}
        self.scan()
        self.claude.connect.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual(self.deepseek.installs, 0)

    def test_detected_claude_remains_unverified_and_unselected(self):
        result = self.scan()['claude']
        self.assertTrue(result['installed'])
        self.assertTrue(result['selectable'])
        self.assertEqual(result['setupStatus'], 'ready-to-connect')
        self.assertFalse(result['configured'])
        self.assertFalse(result['enabled'])
        self.claude.configure.assert_called_once_with(str(self.root/'claude'), str(self.root/'claude-home'))
        self.claude.connect.assert_not_called()
        self.claude.prepare.assert_not_called()

    def test_explicit_claude_home_survives_scan(self):
        self.discovered['claude']['dataDirectoryExplicit'] = True
        self.scan()
        self.claude.configure.assert_called_once_with(str(self.root/'claude'), str(self.root/'claude-home'), explicit_home=True)

    def test_fresh_harness_scan_does_not_install_or_start_desktop(self):
        result = self.scan()['deepseek']
        self.assertEqual(self.deepseek.installs, 0)
        self.launch.assert_not_called()
        self.assertEqual(result['setupStatus'], 'discovered')
        self.assertTrue(result['selectable'])
        self.assertFalse(result['configured'])
        self.assertFalse(result['enabled'])

    def test_missing_and_later_installed_clients_use_the_same_scan(self):
        for provider in ('claude', 'deepseek'):
            self.discovered[provider]['installed'] = False
        first = self.scan()
        self.assertFalse(first['claude']['selectable'])
        self.assertEqual(first['deepseek']['setupStatus'], 'not-installed')
        for provider in ('claude', 'deepseek'):
            self.discovered[provider]['installed'] = True
        second = self.scan()
        self.assertTrue(second['claude']['selectable'])
        self.assertTrue(second['deepseek']['selectable'])
        self.launch.assert_not_called()
        self.assertEqual(self.discovery.call_count, 2)

    def test_one_failed_discovery_does_not_abort_other_clients(self):
        self.claude.configure.side_effect = OSError('Claude access denied')
        result = self.scan()
        self.assertEqual(result['claude']['setupStatus'], 'failed')
        self.assertEqual(result['deepseek']['setupStatus'], 'discovered')
        self.assertTrue(result['codex']['configured'])

    def test_verified_account_does_not_implicitly_enable_a_new_client(self):
        self.deepseek.connected = self.deepseek.configured = True
        self.deepseek.revision = 3
        self.manager.config['autoEnableDiscovered'] = True
        row = self.scan()['deepseek']
        self.assertTrue(row['configured'])
        self.assertFalse(row['enabled'])

    def test_offline_selection_is_saved_without_claiming_configuration(self):
        self.scan()
        for provider in ('claude', 'deepseek'):
            result = self.manager.toggle_client({'provider': provider, 'enabled': True})
            row = next(r for r in result['clients'] if r['id'] == provider)
            self.assertTrue(row['enabled'])
            self.assertFalse(row['configured'])
            self.assertFalse(result['gatewayRunning'])
        self.assertTrue(json.loads(self.manager.config_path.read_text())['enabled']['claude'])
        self.assertTrue(self.scan()['claude']['enabled'])
        self.launch.assert_not_called()
        self.claude.connect.assert_not_called()

    def test_scanning_a_running_gateway_does_not_start_new_apps(self):
        self.manager.gateway_running = True
        self.manager.config['enabled'] = {'claude': True, 'deepseek': True}
        self.scan()
        self.launch.assert_not_called()
        self.claude.connect.assert_not_called()
        self.assertEqual(self.deepseek.installs, 0)

    def test_manual_disabled_choice_survives_rescan(self):
        self.manager.config['enabled'] = {'deepseek': False, 'codex': False, 'claude': False}
        self.deepseek.connected = self.deepseek.configured = True
        rows = self.scan()
        self.assertTrue(rows['deepseek']['configured'])
        self.assertFalse(rows['deepseek']['enabled'])
        self.assertFalse(rows['codex']['enabled'])
        self.launch.assert_not_called()
        self.claude.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
