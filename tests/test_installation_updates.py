"""Installation changes use filesystem evidence; running owners keep their paths."""
import copy
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.clients import lifecycle
from bridge.clients.codex.catalog import Catalog
from bridge.clients.discovery import (DISCOVERY_REVISION, discover_clients, inventory_current,
                                      inventory_fingerprint, inventory_preferences, inventory_registration)
from bridge.clients.manager import DesktopSessions
from bridge.platforms.linux import discovery as linux
from bridge.platforms.windows import discovery as windows

ROOT = Path(__file__).resolve().parents[1]


class InstallationFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def file(self, name, content=b'fixture executable'):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o700)
        return path

    def cached(self, rows, preferences=None):
        preferences = preferences or {}
        return {'discovered': copy.deepcopy(rows), 'discoveryRevision': DISCOVERY_REVISION,
                'discoveryPreferences': inventory_preferences(preferences),
                'discoveryFingerprint': inventory_fingerprint(rows, preferences)}

    def rows(self, executable):
        return {provider: {'id': provider, 'installed': provider == 'codex',
                           'executable': str(executable) if provider == 'codex' else ''}
                for provider in ('codex', 'claude', 'deepseek')}


class InventoryFingerprintTests(InstallationFixtures):
    def test_registered_package_fullname_change_invalidates_inventory_without_disk_changes(self):
        image = self.file('Codex.exe'); rows = self.rows(image); family = 'OpenAI.Codex_fixture'
        rows['codex']['packageFamilyName'] = family
        with patch('bridge.clients.discovery.sys', SimpleNamespace(platform='win32')), \
             patch.object(windows, 'package_registration_fingerprint', return_value={family: ['OpenAI.Codex_1']}) as registration:
            config = self.cached(rows); config['discoveryPackages'] = inventory_registration(rows)
            fingerprint = inventory_fingerprint(rows, {})
            self.assertTrue(inventory_current(config, {}))
            registration.return_value = {family: ['OpenAI.Codex_2']}
            self.assertEqual(inventory_fingerprint(rows, {}), fingerprint)
            self.assertFalse(inventory_current(config, {}))
            registration.assert_called_with({family})

    def test_binary_and_three_platform_metadata_changes_invalidate_an_existing_inventory(self):
        for name, binary, metadata in (
            ('macos', 'Codex.app/Contents/MacOS/Codex', 'Codex.app/Contents/Info.plist'),
            ('windows', 'OpenAI.Codex_1/app/Codex.exe', 'OpenAI.Codex_1/AppxManifest.xml'),
            ('linux', 'codex-desktop', 'resources/app.asar'),
            ('antigravity-cli', 'agy', 'agy.version.json')):
            with self.subTest(platform=name):
                image = self.file(name+'/'+binary)
                evidence = self.file(name+'/'+metadata, b'initial metadata')
                rows = self.rows(image); config = self.cached(rows)
                self.assertTrue(inventory_current(config, {}))
                evidence.write_bytes(b'updated installation metadata')
                self.assertFalse(inventory_current(config, {}))
                config = self.cached(rows)
                image.write_bytes(b'updated binary with a different length')
                self.assertFalse(inventory_current(config, {}))

    @unittest.skipUnless(os.name == 'posix', 'synthetic executable symlinks')
    def test_stable_linux_symlink_retarget_invalidates_cache_while_old_image_remains(self):
        old, new = self.file('v1/codex-desktop'), self.file('v2/codex-desktop')
        alias = self.root/'codex-desktop'; alias.symlink_to(old)
        rows = self.rows(old); rows['codex'].update(linux.candidate(alias, 'codex'), discoveryPath=str(alias))
        config = self.cached(rows); self.assertTrue(inventory_current(config, {}))
        alias.unlink(); alias.symlink_to(new)
        self.assertTrue(old.is_file()); self.assertFalse(inventory_current(config, {}))
        self.assertEqual(linux.candidate(alias, 'codex')['executable'], str(new))

    def test_linux_desktop_entry_change_invalidates_cache_without_removing_old_installation(self):
        old, new = self.file('v1/codex-desktop'), self.file('v2/codex-desktop')
        entry = self.file('data/applications/codex.desktop',
                          ('[Desktop Entry]\nType=Application\nName=Codex\nExec='+str(old)+'\n').encode())
        metadata = {}; env = {'PATH': '', 'XDG_DATA_HOME': str(self.root/'data'), 'XDG_DATA_DIRS': str(self.root/'empty')}
        self.assertIn(old, list(linux.candidates('codex', ('Codex',), self.root, env, metadata=metadata)))
        rows = self.rows(old); rows['codex'].update(metadata[old])
        config = self.cached(rows); self.assertTrue(inventory_current(config, {}))
        entry.write_text('[Desktop Entry]\nType=Application\nName=Codex\nExec='+str(new)+'\n')
        self.assertTrue(old.is_file()); self.assertFalse(inventory_current(config, {}))
        self.assertIn(new, list(linux.candidates('codex', ('Codex',), self.root, env)))

    def test_new_squirrel_and_msix_versions_invalidate_cache_even_when_old_directories_survive(self):
        for provider, old_name, new_name, executable in (
            ('claude', 'local/Programs/Claude/app-2.9', 'local/Programs/Claude/app-2.10', 'Claude.exe'),
            ('codex', 'programs/WindowsApps/OpenAI.Codex_2.9', 'programs/WindowsApps/OpenAI.Codex_2.10', 'app/Codex.exe')):
            with self.subTest(provider=provider):
                old = self.file(old_name+'/'+executable)
                if provider == 'codex': self.file(old_name+'/AppxManifest.xml')
                rows = self.rows(old); rows[provider] = {'id': provider, 'installed': True, 'executable': str(old)}
                config = self.cached(rows); self.assertTrue(inventory_current(config, {}))
                new = self.file(new_name+'/'+executable)
                if provider == 'codex': self.file(new_name+'/AppxManifest.xml')
                self.assertTrue(old.is_file()); self.assertFalse(inventory_current(config, {}))
                env = {'LOCALAPPDATA': str(self.root/'local'), 'ProgramFiles': str(self.root/'programs')}
                candidates = windows.candidates(provider, ('Claude',) if provider == 'claude' else ('Codex', 'ChatGPT'), self.root, env)
                self.assertEqual(next(path for path in candidates if path.is_file()), new)


class SelectedInstallationDiscoveryTests(InstallationFixtures):
    def windows_scan(self, selected, registered):
        with patch.object(windows, 'registered_candidates', side_effect=lambda provider, inventory: registered if provider == 'codex' else []), \
             patch.object(windows, 'candidates', return_value=[]):
            return discover_clients(selected, platform='win32', home=self.root, env={}, windows_inventory={})['codex']

    def test_unique_registered_same_family_and_aumid_migrates_selected_path_even_if_old_still_exists(self):
        for old_exists in (True, False):
            with self.subTest(old_exists=old_exists):
                old, new = self.file('selected/Codex.exe'), self.file('registered/Codex.exe')
                identity = {'packageFamilyName': 'OpenAI.Codex_fixture', 'applicationUserModelId': 'OpenAI.Codex_fixture!App'}
                preferences = {'desktopExecutable': str(old), 'desktopInstallation': {'executable': str(old), **identity}}
                if not old_exists: old.unlink()
                row = self.windows_scan(preferences, [{'executable': str(new), **identity}])
                self.assertEqual(row['executable'], str(new)); self.assertEqual(row['replacesExecutable'], str(old))

    def test_other_family_ambiguous_gui_and_unregistered_manual_path_cannot_authorize_migration(self):
        old, new, alternate = self.file('selected/Codex.exe'), self.file('registered/Codex.exe'), self.file('other/Codex.exe')
        identity = {'packageFamilyName': 'OpenAI.Codex_fixture', 'applicationUserModelId': 'OpenAI.Codex_fixture!App'}
        selected = {'desktopExecutable': str(old), 'desktopInstallation': {'executable': str(old), **identity}}
        for preferences, registered in (
            (selected, [{'executable': str(new), **identity, 'packageFamilyName': 'Other.Codex_fixture'}]),
            (selected, [{'executable': str(new), **identity, 'applicationUserModelId': 'OpenAI.Codex_fixture!Other'}]),
            (selected, [{'executable': str(path), **identity} for path in (new, alternate)]),
            ({'desktopExecutable': str(old)}, [{'executable': str(new), **identity}])):
            row = self.windows_scan(preferences, registered)
            self.assertEqual(row['executable'], str(old)); self.assertNotIn('replacesExecutable', row)

    def test_mac_executable_rename_is_verified_only_within_the_selected_bundle(self):
        bundle = self.root/'Applications/Codex.app'
        old = self.file('Applications/Codex.app/Contents/MacOS/OldCodex')
        new = self.file('Applications/Codex.app/Contents/MacOS/NewCodex')
        metadata = bundle/'Contents/Info.plist'
        metadata.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.openai.codex', 'CFBundleExecutable': new.name}))
        row = discover_clients({'desktopExecutable': str(old)}, platform='darwin', home=self.root,
                               env={}, applications=[bundle.parent])['codex']
        self.assertEqual(row['executable'], str(new)); self.assertEqual(row['replacesExecutable'], str(old))
        old.unlink(); new.unlink()
        other = self.file('Applications/OtherCodex.app/Contents/MacOS/Codex')
        (other.parent.parent/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.openai.codex', 'CFBundleExecutable': other.name}))
        row = discover_clients({'desktopExecutable': str(old)}, platform='darwin', home=self.root,
                               env={}, applications=[bundle.parent])['codex']
        self.assertEqual(row['executable'], str(other)); self.assertNotIn('replacesExecutable', row)


class InstallationHandoffTests(InstallationFixtures):
    def setUp(self):
        super().setUp()
        self.manager = DesktopSessions(self.root)
        self.addCleanup(self.manager.close)
        self.previous = {provider: {'id': provider, 'installed': True,
            'executable': str(self.file(provider+'/old/app')), 'dataDirectory': str(self.root/'profiles'/provider)}
            for provider in ('codex', 'claude', 'deepseek', 'zcode', 'antigravity')}
        self.previous['claude']['dataDirectoryExplicit'] = True
        self.previous['antigravity']['installations'] = {
            'app': {'executable': self.previous['antigravity']['executable']},
            'cli': {'executable': str(self.file('antigravity-cli/old/agy'))}}
        self.preferences = {}
        self.manager.config.update(self.cached(self.previous))
        self.accounts = SimpleNamespace(index={'desktopExecutable': self.previous['codex']['executable'],
            'desktopExecutableSource': 'scan'}, lock=RLock(), save=Mock())
        self.manager.bridge = SimpleNamespace(accounts=self.accounts)
        self.manager._discovery_preferences = Mock(return_value=self.preferences)
        self.manager.clients = Mock(side_effect=lambda **_: {'clients': copy.deepcopy(list(self.manager.config['discovered'].values()))})
        for name in ('_configure_zcode', '_configure_antigravity', '_prepare_discovered'):
            setattr(self.manager, name, Mock())
        self.discovery = self.patch('bridge.clients.discovery.discover_clients')
        self.live = set()
        self.processes = self.patch('bridge.clients.desktop_app.process_inventory', side_effect=lambda paths:
            {Path(path): {'pids': [100] if str(path) in self.live else [], 'commands': {}} for path in paths})
        self.launch = self.patch('bridge.clients.lifecycle.launch_client')
        self.stop = self.patch('bridge.clients.lifecycle.stop_client')

    def patch(self, name, **kwargs):
        patcher = patch(name, **kwargs); result = patcher.start(); self.addCleanup(patcher.stop); return result

    def replacement(self):
        rows = copy.deepcopy(self.previous)
        for provider, row in rows.items():
            row['dataDirectory'] = str(self.root/'new-auto-profiles'/provider)
            if provider != 'antigravity': row['executable'] = str(self.file(provider+'/new/app'))
        rows['antigravity']['installations']['cli']['executable'] = str(self.file('antigravity-cli/new/agy'))
        return rows

    def test_unchanged_inventory_does_not_rediscover_or_query_processes(self):
        self.manager.scan(inventory_only=True)
        self.discovery.assert_not_called(); self.processes.assert_not_called()
        self.manager._prepare_discovered.assert_not_called()
        self.accounts.save.assert_not_called(); self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_scan_catalog_follows_only_the_final_runtime_after_pending_owner_exit_without_losing_models(self):
        old_runtime = self.file('codex/old/resources/codex')
        new_runtime = self.file('codex/new/resources/codex')
        self.previous['codex']['runtime'] = str(old_runtime)
        self.manager.config.update(self.cached(self.previous))
        replacement = self.replacement(); replacement['codex']['runtime'] = str(new_runtime)
        self.discovery.return_value = replacement
        with patch.object(Catalog, 'find_runtime', return_value=old_runtime) as find:
            reader = Catalog(self.root/'catalog-home', allow_background_refresh=False)
            self.manager.bridge.catalog_reader = reader
            models = {'models': [{'id': 'fixture-model'}]}; cached = (123.0, models)
            reader.cache['project'] = cached; reader.kind_caches['models']['identity'] = cached
            reader.model_store.write('identity', models); persisted = reader.model_store.read('identity')
            self.live.add(self.previous['codex']['executable'])
            self.manager.scan()
            self.assertIn('pendingInstallation', self.manager.config['discovered']['codex'])
            self.assertEqual(reader.executable, old_runtime)
            self.live.clear(); self.manager.scan(inventory_only=True)
            self.assertTrue(old_runtime.is_file(), 'following the new bundle must not depend on deleting the old runtime')
            self.assertEqual(reader.executable, new_runtime)
            self.assertIs(reader.cache['project'], cached)
            self.assertIs(reader.kind_caches['models']['identity'], cached)
            self.assertEqual(reader.model_store.read('identity'), persisted)
            find.assert_called_once_with()
        self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_scan_cannot_replace_explicit_or_unrelated_automatic_catalog_runtimes(self):
        old_runtime = self.file('codex/old/resources/codex')
        new_runtime = self.file('codex/new/resources/codex')
        independent = self.file('independent-cli/codex')
        for explicit in (True, False):
            with self.subTest(explicit=explicit), patch.object(Catalog, 'find_runtime', return_value=independent) as find:
                self.previous['codex']['runtime'] = str(old_runtime)
                self.manager.config.update(self.cached(self.previous))
                replacement = self.replacement(); replacement['codex']['runtime'] = str(new_runtime)
                self.discovery.return_value = replacement
                reader = Catalog(self.root/'catalog-home', executable=old_runtime if explicit else None,
                                 allow_background_refresh=False)
                self.manager.bridge.catalog_reader = reader
                self.manager.scan()
                self.assertEqual(self.manager.config['discovered']['codex']['runtime'], str(new_runtime))
                self.assertEqual(reader.executable, old_runtime if explicit else independent)
                if explicit: find.assert_not_called()
                else: find.assert_called_once_with()
        self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_each_agent_keeps_its_running_installation_then_adopts_pending_without_full_rescan(self):
        replacement = self.replacement(); self.discovery.return_value = replacement
        waiting = ('codex', 'zcode', 'antigravity')
        self.live = {self.previous[provider]['executable'] for provider in ('codex', 'zcode')}
        self.live.add(self.previous['antigravity']['installations']['cli']['executable'])
        self.manager.scan()
        rows = self.manager.config['discovered']
        for provider in waiting:
            self.assertEqual(rows[provider]['executable'], self.previous[provider]['executable'])
            self.assertEqual(rows[provider]['pendingInstallation']['executable'], replacement[provider]['executable'])
        self.assertEqual(rows['antigravity']['installations'], self.previous['antigravity']['installations'])
        for provider in ('claude', 'deepseek'):
            self.assertEqual(rows[provider]['executable'], replacement[provider]['executable'])
            self.assertNotIn('pendingInstallation', rows[provider])
        self.manager.scan(inventory_only=True)
        self.assertEqual(self.discovery.call_count, 1)
        self.live.clear(); self.manager.scan(inventory_only=True)
        self.assertEqual(self.discovery.call_count, 1)
        rows = self.manager.config['discovered']
        for provider, row in rows.items():
            self.assertNotIn('pendingInstallation', row)
            self.assertEqual(row['dataDirectory'], self.previous[provider]['dataDirectory'])
            self.assertEqual(row['executable'], replacement[provider]['executable'])
        self.assertTrue(rows['claude']['dataDirectoryExplicit'])
        self.assertEqual(rows['antigravity']['installations'], replacement['antigravity']['installations'])
        self.assertEqual(self.accounts.index['desktopExecutable'], replacement['codex']['executable'])
        self.accounts.save.assert_called_once(); self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_process_query_failure_preserves_in_memory_and_saved_configuration(self):
        self.discovery.return_value = self.replacement()
        self.manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.manager.config_path.write_text(json.dumps(self.manager.config))
        previous = copy.deepcopy(self.manager.config); saved = self.manager.config_path.read_bytes()
        self.processes.side_effect = OSError('process identity unavailable')
        with self.assertRaisesRegex(OSError, 'identity unavailable'): self.manager.scan()
        self.assertEqual(self.manager.config, previous); self.assertEqual(self.manager.config_path.read_bytes(), saved)
        self.accounts.save.assert_not_called(); self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_manual_and_legacy_account_paths_are_never_overwritten_by_scanning(self):
        for source in ('manual', None):
            with self.subTest(source=source):
                self.manager.config.update(self.cached(self.previous))
                self.accounts.index = {'desktopExecutable': self.previous['codex']['executable']}
                if source: self.accounts.index['desktopExecutableSource'] = source
                self.discovery.return_value = self.replacement(); original = dict(self.accounts.index)
                self.manager.scan()
                self.assertEqual(self.accounts.index, original)
                self.accounts.save.assert_not_called(); self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_verified_same_installation_updates_manual_selection_without_changing_source_or_home(self):
        old = self.previous['codex']['executable']; self.preferences['desktopExecutable'] = old
        self.accounts.index['desktopExecutableSource'] = 'manual'
        self.accounts.home = self.root/'account-home'
        replacement = self.replacement(); replacement['codex']['replacesExecutable'] = old
        self.discovery.return_value = replacement
        self.manager.scan()
        self.assertEqual(self.accounts.index, {'desktopExecutable': replacement['codex']['executable'], 'desktopExecutableSource': 'manual'})
        self.assertEqual(self.accounts.home, self.root/'account-home')
        self.assertEqual(self.manager.config['discovered']['codex']['dataDirectory'], self.previous['codex']['dataDirectory'])
        self.accounts.save.assert_called_once()

    def test_manual_selection_needs_matching_replacement_proof_and_is_rechecked_inside_the_lock(self):
        old = self.previous['codex']['executable']; other = str(self.file('manual-other/Codex'))
        for proof, change_in_lock in ((None, False), (other, False), (old, True)):
            with self.subTest(proof=proof, change_in_lock=change_in_lock):
                self.manager.config.update(self.cached(self.previous))
                self.preferences['desktopExecutable'] = old
                self.accounts.index = {'desktopExecutable': old, 'desktopExecutableSource': 'manual'}
                replacement = self.replacement()
                if proof: replacement['codex']['replacesExecutable'] = proof
                self.discovery.return_value = replacement
                lock = RLock()
                def acquire(**kwargs):
                    if change_in_lock: self.accounts.index['desktopExecutable'] = other
                    return lock.acquire(**kwargs)
                self.accounts.lock = SimpleNamespace(acquire=acquire, release=lock.release)
                self.manager.scan()
                self.assertEqual(self.accounts.index['desktopExecutable'], other if change_in_lock else old)
                self.assertEqual(self.accounts.index['desktopExecutableSource'], 'manual')
                self.accounts.save.assert_not_called()

    def test_registration_failure_preserves_configuration_and_selected_index_before_saving(self):
        old = self.previous['codex']['executable']; self.preferences['desktopExecutable'] = old
        self.accounts.index['desktopExecutableSource'] = 'manual'
        replacement = self.replacement(); replacement['codex']['replacesExecutable'] = old
        self.discovery.return_value = replacement
        self.manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.manager.config_path.write_text(json.dumps(self.manager.config))
        previous, index = copy.deepcopy(self.manager.config), dict(self.accounts.index)
        saved = self.manager.config_path.read_bytes()
        with patch('bridge.clients.discovery.inventory_registration', side_effect=OSError('registration unavailable')):
            with self.assertRaisesRegex(OSError, 'registration unavailable'): self.manager.scan()
        self.assertEqual(self.manager.config, previous); self.assertEqual(self.accounts.index, index)
        self.assertEqual(self.manager.config_path.read_bytes(), saved)
        self.accounts.save.assert_not_called(); self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_explicit_new_home_survives_pending_installation_across_scans(self):
        replacement = self.replacement()
        new_home = str(self.root/'explicit-new-claude-home')
        self.preferences['claudeHome'] = new_home
        replacement['claude'].update(dataDirectory=new_home, dataDirectoryExplicit=True)
        self.discovery.return_value = replacement
        self.live.add(self.previous['claude']['executable'])
        self.manager.scan()
        pending = self.manager.config['discovered']['claude']['pendingInstallation']
        self.assertEqual(pending['dataDirectory'], new_home)
        self.live.clear(); self.manager.scan(inventory_only=True)
        row = self.manager.config['discovered']['claude']
        self.assertNotIn('pendingInstallation', row)
        self.assertEqual(row['dataDirectory'], new_home)
        self.assertTrue(row['dataDirectoryExplicit'])
        self.assertEqual(self.discovery.call_count, 1)

    def test_removed_installation_reappears_without_periodic_full_scans(self):
        old_image = Path(self.previous['codex']['executable'])
        old_image.unlink()
        absent = copy.deepcopy(self.previous)
        absent['codex'].update(installed=False, executable='')
        self.discovery.return_value = absent
        self.manager.scan(inventory_only=True)
        self.assertFalse(self.manager.config['discovered']['codex']['installed'])
        self.manager.scan(inventory_only=True)
        self.assertEqual(self.discovery.call_count, 1, 'unchanged absence does not require periodic full discovery')
        old_image.write_bytes(b'reinstalled executable')
        self.discovery.return_value = copy.deepcopy(self.previous)
        self.assertFalse(inventory_current(self.manager.config, self.preferences))
        self.manager.scan(inventory_only=True)
        self.assertTrue(self.manager.config['discovered']['codex']['installed'])
        self.assertEqual(self.discovery.call_count, 2)
        self.launch.assert_not_called(); self.stop.assert_not_called()

    def test_removed_msix_package_is_rediscovered_at_a_new_version_path_after_the_gap(self):
        old_package = self.root/'WindowsApps/OpenAI.Codex_2.9'
        old_image = self.file('WindowsApps/OpenAI.Codex_2.9/app/Codex.exe')
        self.file('WindowsApps/OpenAI.Codex_2.9/AppxManifest.xml')
        previous = copy.deepcopy(self.previous); previous['codex']['executable'] = str(old_image)
        self.accounts.index['desktopExecutable'] = str(old_image)
        self.manager.config.update(self.cached(previous))
        shutil.rmtree(old_package)
        absent = copy.deepcopy(previous); absent['codex'].update(installed=False, executable='')
        self.discovery.return_value = absent
        self.manager.scan(inventory_only=True)
        self.assertFalse(self.manager.config['discovered']['codex']['installed'])
        self.assertTrue(inventory_current(self.manager.config, self.preferences))
        self.manager.scan(inventory_only=True); self.assertEqual(self.discovery.call_count, 1)
        new_image = self.file('WindowsApps/OpenAI.Codex_2.10/app/Codex.exe')
        self.file('WindowsApps/OpenAI.Codex_2.10/AppxManifest.xml')
        self.assertFalse(old_package.exists())
        self.assertFalse(inventory_current(self.manager.config, self.preferences))
        replacement = copy.deepcopy(previous); replacement['codex']['executable'] = str(new_image)
        self.discovery.return_value = replacement
        self.manager.scan(inventory_only=True)
        self.assertEqual(self.manager.config['discovered']['codex']['executable'], str(new_image))
        self.assertTrue(self.manager.config['discovered']['codex']['installed'])
        self.assertEqual(self.discovery.call_count, 2)
        self.launch.assert_not_called(); self.stop.assert_not_called()


class InstallationLifecycleTests(InstallationFixtures):
    def test_dsh_reuses_a_pending_deleted_owner_but_refuses_restart_or_missing_image_launch(self):
        old_image, new_image = self.file('old/Harness'), self.file('new/Harness')
        descriptor = {'id': 'deepseek', 'installed': True, 'executable': str(old_image),
                      'dataDirectory': str(self.root/'original-home'),
                      'pendingInstallation': {'id': 'deepseek', 'installed': True, 'executable': str(new_image)}}
        old_image.unlink()
        with patch.object(lifecycle.DesktopApp, 'processes', return_value=[321]) as processes, \
             patch.object(lifecycle, '_commands', side_effect=lambda app, pids: {pid: str(app.executable) for pid in pids}) as commands, \
             patch.object(lifecycle, 'stop_client') as stop, \
             patch.object(lifecycle, 'native_desktop') as native:
            result = lifecycle.launch_deepseek(descriptor)
            self.assertTrue(result['running']); self.assertFalse(result['launched']); self.assertFalse(result['restarted'])
            self.assertEqual(commands.call_args.args[0].executable, old_image)
            self.assertEqual(commands.call_args.args[1], [321])
            with self.assertRaises(ValueError): lifecycle.launch_deepseek(descriptor, restart=True)
            stop.assert_not_called(); native.assert_not_called()
            processes.return_value = []
            with self.assertRaises(ValueError): lifecycle.launch_deepseek(descriptor)
            stop.assert_not_called(); native.assert_not_called()

    def test_pending_deleted_image_is_inspected_but_cannot_be_launched_after_its_owner_exits(self):
        old_image, new_image = self.file('old/Codex'), self.file('new/Codex')
        descriptor = {'id': 'codex', 'installed': True, 'executable': str(old_image),
                      'dataDirectory': str(self.root/'original-home'),
                      'pendingInstallation': {'id': 'codex', 'installed': True, 'executable': str(new_image)}}
        old_image.unlink()
        with patch.object(lifecycle, 'process_inventory', return_value={old_image: {
                'pids': [123], 'commands': {123: str(old_image)}}}) as inventory, \
             patch.object(lifecycle.DesktopApp, 'processes', return_value=[]) as processes, \
             patch.object(lifecycle.DesktopApp, 'start') as start:
            state = lifecycle.inspect_clients([descriptor])
            self.assertIn('codex', state)
            self.assertTrue(state['codex']['running'])
            self.assertEqual(state['codex']['pids'], [123])
            inventory.assert_called_once()
            with self.assertRaises(ValueError): lifecycle.launch_client(descriptor)
            start.assert_not_called()


class InstallationDescriptorTests(InstallationFixtures):
    def test_codex_pending_descriptor_keeps_missing_old_image_installed_for_process_inspection(self):
        old_image, new_image = self.file('old/Codex'), self.file('new/Codex')
        home = self.root/'original-home'
        pending = {'id': 'codex', 'installed': True, 'executable': str(new_image)}
        manager = DesktopSessions.__new__(DesktopSessions)
        manager.config = {'discovered': {'codex': {'id': 'codex', 'installed': True,
            'executable': str(old_image), 'dataDirectory': str(home), 'pendingInstallation': pending}}}
        accounts = SimpleNamespace(index={'desktopExecutable': str(old_image)}, home=home, runtime=None)
        manager.bridge = SimpleNamespace(for_host=lambda host: SimpleNamespace(accounts=accounts))
        old_image.unlink()
        descriptor = manager._descriptor('codex')
        self.assertTrue(descriptor['installed'])
        self.assertEqual(descriptor['executable'], str(old_image))
        self.assertEqual(descriptor['dataDirectory'], str(home))
        self.assertEqual(descriptor['pendingInstallation'], pending)

    def test_zcode_missing_installation_preserves_the_pending_old_descriptor_without_an_adapter(self):
        old_image, new_image = self.file('old/ZCode'), self.file('new/ZCode')
        pending = {'id': 'zcode', 'installed': True, 'executable': str(new_image)}
        previous = {'id': 'zcode', 'installed': True, 'application': str(old_image),
                    'executable': str(old_image), 'dataDirectory': str(self.root/'original-home'),
                    'pendingInstallation': pending, 'packageType': 'appimage'}
        manager = DesktopSessions.__new__(DesktopSessions)
        manager.config = {'discovered': {'zcode': previous}, 'zcodeConnectionMode': 'official-remote'}
        manager.adapters = {'zcode': None}; manager._zcode_config = {'application': str(old_image)}
        old_image.unlink()
        with patch('bridge.clients.zcode.installation.installation', return_value=None):
            descriptor = manager._descriptor('zcode')
        for key in ('installed', 'application', 'executable', 'dataDirectory', 'pendingInstallation', 'packageType'):
            self.assertEqual(descriptor[key], previous[key])
        self.assertTrue(descriptor['officialRemote'])
        self.assertFalse(descriptor['pipeSupported'])
