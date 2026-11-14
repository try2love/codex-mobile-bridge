"""First Windows inventory reuses the real registries with synthetic WSL reads."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.discovery import DISCOVERY_REVISION
from bridge.clients.manager import DesktopSessions
from bridge.features.endpoints.registry import Entries, entry_id
from bridge.platforms.windows import wsl


class InitialWslInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        platform = patch('bridge.clients.manager.sys', SimpleNamespace(platform='win32'))
        platform.start(); self.addCleanup(platform.stop)
        self.manager = DesktopSessions(self.root)
        self.addCleanup(self.manager.close)
        self.entries = Entries(self.root)
        self.entries.wsl = wsl
        self.manager.bridge = SimpleNamespace(entries=self.entries, accounts=SimpleNamespace())
        self.manager.config['enabled'] = dict.fromkeys(('codex', 'claude', 'deepseek'), False)
        self.manager._discovery_preferences = Mock(return_value={})
        self.native = {family: {'id': family, 'installed': False, 'executable': '',
            'dataDirectory': str(self.root/family)} for family in ('codex', 'claude', 'deepseek')}
        self.discover = self.patch('bridge.clients.discovery.discover_clients', return_value=self.native)
        self.patch('bridge.clients.manager.windows_session.status', return_value={'state': 'unavailable'})
        self.running = self.patch('bridge.platforms.windows.wsl.running_distributions', return_value=['Ubuntu'])
        self.process = self.patch('bridge.platforms.windows.wsl.subprocess.run', side_effect=self.read)
        self.launch = self.patch('bridge.platforms.windows.wsl.ensure_daemon', side_effect=AssertionError('No native start'))

    def patch(self, name, **kwargs):
        value = patch(name, **kwargs)
        result = value.start(); self.addCleanup(value.stop)
        return result

    def read(self, args, **kwargs):
        self.assertEqual(args, ['wsl.exe', '--distribution', 'Ubuntu', '--exec', 'python3', '-'])
        source = kwargs['input'].decode()
        value = ({'user': 'fixture', 'profiles': [{'configDir': '/home/fixture/.claude'}]}
                 if 'wsl_claude_probe' in source else
                 {'user': 'fixture', 'userHome': '/home/fixture', 'homes': [{'home': '/home/fixture/.codex'}]})
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b'')

    def inventory(self):
        return self.manager.control({'action': 'scan', 'setup': False, 'inventoryOnly': True})

    def assert_targets(self, value):
        codex = next(row for row in value['clients'] if row['id'] == 'codex')
        claude = next(row for row in value['clients'] if row['id'] == 'claude')
        self.assertEqual([row['kind'] for row in codex['entries']], ['desktop', 'wsl-cli'])
        self.assertEqual([row['kind'] for row in claude['entries']], ['wsl-claude-cli'])
        self.assertFalse(codex['entries'][1]['enabled'])
        self.assertFalse(claude['entries'][0]['enabled'])
        self.assertEqual(self.entries.bridges, {})
        self.assertFalse(self.entries.path.exists())
        self.assertFalse(self.manager.claude_wsl.path.exists())
        self.launch.assert_not_called()

    def test_first_inventory_discovers_both_running_targets_and_later_views_reuse_candidates(self):
        self.assert_targets(self.inventory())
        self.assertEqual(self.process.call_count, 2)
        for _ in range(3):
            self.assert_targets(self.inventory())
            self.assert_targets(self.manager.scan(inventory_only=True))
            self.assert_targets(self.manager.clients(refresh=True))
            self.assert_targets(self.manager.clients_status())
        self.assertEqual(self.process.call_count, 2)
        self.discover.assert_called_once()

    def test_existing_native_inventory_does_not_skip_first_wsl_inventory(self):
        self.manager.config.update(discovered=copy.deepcopy(self.native),
            discoveryRevision=DISCOVERY_REVISION, discoveryPreferences={})
        self.assertFalse(self.manager.clients(refresh=True)['inventoryAvailable'])
        self.assertFalse(self.manager.clients_status()['inventoryAvailable'])
        self.process.assert_not_called()
        self.assert_targets(self.inventory())
        self.assertTrue(self.manager.clients_status()['inventoryAvailable'])
        self.discover.assert_not_called()
        self.assertEqual(self.process.call_count, 2)

    def test_startup_full_inventory_runs_once_and_a_new_manager_rebuilds_memory_candidates(self):
        self.manager.gateway_running = True
        self.manager.start_enabled()
        self.assertEqual(self.process.call_count, 2)
        self.manager.start_enabled()
        self.assertEqual(self.process.call_count, 2)
        fresh = DesktopSessions(self.root)
        self.addCleanup(fresh.close)
        entries = Entries(self.root)
        entries.wsl = wsl
        fresh.bridge = SimpleNamespace(entries=entries, accounts=SimpleNamespace())
        fresh._discovery_preferences = Mock(return_value={})
        self.assertEqual(entries.candidates, {})
        self.assert_targets(fresh.control({'action': 'scan', 'inventoryOnly': True}))
        self.assertEqual(self.process.call_count, 4)
        self.discover.assert_called_once()

    def test_stopped_distributions_are_not_started_or_automatically_retried(self):
        self.running.return_value = []
        value = self.inventory()
        self.assertEqual(value['clients'][0]['entries'][1:], [])
        self.process.assert_not_called()
        self.running.return_value = ['Ubuntu']
        self.inventory()
        self.process.assert_not_called()
        self.assert_targets(self.manager.control({'action': 'scan', 'setup': False}))
        self.assertEqual(self.process.call_count, 2)

    def test_first_discovery_preserves_registered_switches_and_live_owners(self):
        from bridge.clients.claude.wsl import entry_id as claude_id
        codex = {'id': entry_id('Ubuntu', 'fixture', '/home/fixture/.codex'),
            'distribution': 'Ubuntu', 'user': 'fixture', 'home': '/home/fixture/.codex',
            'label': 'Saved Codex', 'enabled': True}
        claude = {'id': claude_id('Ubuntu', 'fixture', '/home/fixture/.claude'),
            'distribution': 'Ubuntu', 'user': 'fixture', 'configDir': '/home/fixture/.claude',
            'label': 'Saved Claude', 'enabled': False}
        self.entries.entries = [copy.deepcopy(codex)]
        self.manager.claude_wsl.entries = [copy.deepcopy(claude)]
        self.entries.path.parent.mkdir(parents=True)
        self.entries.path.write_text(json.dumps({'version': 1, 'entries': [codex]}))
        self.manager.claude_wsl.path.write_text(json.dumps({'version': 1, 'entries': [claude]}))
        paths = [self.entries.path, self.manager.claude_wsl.path]
        before = [path.read_bytes() for path in paths]
        owner = SimpleNamespace(server=SimpleNamespace(closed=False), tasks={'turn': 'active'})
        self.entries.bridges[codex['id']] = owner
        self.inventory()
        self.assertEqual(self.entries.entries, [codex])
        self.assertEqual(self.manager.claude_wsl.entries, [claude])
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertIs(self.entries.bridges[codex['id']], owner)
        self.assertEqual(owner.tasks, {'turn': 'active'})
        self.launch.assert_not_called()

    def test_manual_wsl_scan_satisfies_initial_pass_and_remains_repeatable(self):
        self.manager.control({'action': 'scan-wsl'})
        self.assertEqual(self.process.call_count, 2)
        self.assert_targets(self.inventory())
        self.assertEqual(self.process.call_count, 2)
        self.manager.control({'action': 'scan-wsl'})
        self.assertEqual(self.process.call_count, 4)

    def test_failed_registry_is_attempted_once_without_hiding_the_other_registry_or_native_inventory(self):
        original = self.entries.scan
        with patch.object(self.entries, 'scan', side_effect=OSError('Fixture Codex inventory unavailable')) as scan:
            first = self.inventory()
            self.assertTrue(first['inventoryAvailable'])
            self.assertEqual(first['errors'], [{'distribution': 'WSL Codex', 'error': 'Fixture Codex inventory unavailable'}])
            claude = next(row for row in first['clients'] if row['id'] == 'claude')
            self.assertEqual(claude['entries'][0]['kind'], 'wsl-claude-cli')
            for _ in range(3):
                self.assertEqual(self.inventory()['errors'], first['errors'])
                self.manager.clients(refresh=True)
            scan.assert_called_once()
            self.assertEqual(self.process.call_count, 1)
            scan.side_effect = original
            retry = self.manager.control({'action': 'scan', 'setup': False})
            self.assertEqual(retry['errors'], [])
            self.assert_targets(retry)
            self.assertEqual(self.process.call_count, 3)

    def test_failed_probe_returns_its_real_error_and_keeps_successful_codex_candidates(self):
        original = self.read
        def unavailable(args, **kwargs):
            return (subprocess.CompletedProcess(args, 1, b'', b'')
                    if b'wsl_claude_probe' in kwargs['input'] else original(args, **kwargs))
        self.process.side_effect = unavailable
        value = self.inventory()
        self.assertEqual(value['clients'][0]['entries'][1]['kind'], 'wsl-cli')
        self.assertEqual(value['errors'], [{'distribution': 'Ubuntu', 'error': '未找到可读取的 Claude Code 环境或 Python'}])
        self.assertEqual(self.inventory()['errors'], value['errors'])
        self.assertEqual(self.process.call_count, 2)

    def test_non_windows_inventory_never_invokes_either_wsl_registry(self):
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), \
                    patch('bridge.clients.manager.sys', SimpleNamespace(platform=platform)), \
                    patch.object(self.entries, 'scan') as codex, \
                    patch.object(self.manager.claude_wsl, 'scan') as claude:
                self.inventory()
                codex.assert_not_called()
                claude.assert_not_called()
        self.process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
