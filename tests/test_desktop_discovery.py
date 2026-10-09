import hashlib
import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.integrations.discovery import BUNDLE_IDS, _candidate, discover_clients
from bridge.integrations.deepseek import DeepSeek, MARKER


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root/'user'
        self.home.mkdir()
        self.applications = self.root/'Applications'
        self.applications.mkdir()

    def bundle(self, name, provider, identifier=None):
        app = self.applications/(name+'.app')
        (app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/MacOS'/name).write_bytes(b'actual-desktop')
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': identifier or BUNDLE_IDS[provider], 'CFBundleExecutable': name,
            'CFBundleShortVersionString': '2.3.4'}))
        return app

    def scan_mac(self, preferences=None, env=None):
        return discover_clients(preferences, platform='darwin', home=self.home,
                                env=env or {}, applications=[self.applications])

    def test_original_and_renamed_bundles_discovered_without_gateway(self):
        codex = self.bundle('ChatGPT', 'codex')
        claude = self.bundle('My Claude', 'claude')
        self.bundle('DeepSeek Harness', 'deepseek')
        runtime = codex/'Contents/Resources/codex-cli/bin/codex'
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b'cli')
        rows = self.scan_mac()
        self.assertTrue(all(row['installed'] for row in rows.values()))
        self.assertEqual(rows['codex']['executable'], str(codex/'Contents/MacOS/ChatGPT'))
        self.assertEqual(rows['codex']['runtime'], str(runtime))
        self.assertEqual(rows['claude']['application'], str(claude))
        self.assertFalse(rows['deepseek']['desktopProfileReady'])
        self.assertFalse((self.home/'.dsh').exists())

    def test_rescan_finds_later_install_and_does_not_trust_product_name_alone(self):
        wrong = self.bundle('ChatGPT', 'codex', 'com.openai.chat')
        self.assertFalse(self.scan_mac()['codex']['installed'])
        self.bundle('Codex', 'codex')
        self.assertTrue(self.scan_mac()['codex']['installed'])
        self.assertNotEqual(self.scan_mac()['codex']['application'], str(wrong))

    def test_data_homes_preserve_explicit_and_environment_selection_without_reading_keys(self):
        desktop = self.home/'existing-harness'
        desktop.mkdir()
        (desktop/'credentials.yaml').write_text('secret: never-return-this')
        patchfile = desktop/'profiles/desktop/cordis.patch.yml'
        patchfile.parent.mkdir(parents=True)
        patchfile.write_text('[]\n')
        rows = self.scan_mac(env={'DSH_HOME': str(desktop), 'CODEX_HOME': '~/codex-main'})
        self.assertEqual(rows['deepseek']['dataDirectory'], str(desktop))
        self.assertTrue(rows['deepseek']['desktopProfileReady'])
        self.assertEqual(rows['codex']['dataDirectory'], str(self.home/'codex-main'))
        self.assertNotIn('never-return-this', json.dumps(rows))
        selected = self.scan_mac({'deepseekHome': '~/selected'}, {'DSH_HOME': str(desktop)})
        self.assertEqual(selected['deepseek']['dataDirectory'], str(self.home/'selected'))
        self.assertEqual(self.scan_mac(env={'DSH_HOME': '  '})['deepseek']['dataDirectory'], str(self.home/'.dsh'))

    def test_windows_standard_user_and_store_locations(self):
        local, programfiles = self.root/'Local', self.root/'Program Files'
        for path in [local/'Claude/app-2.4.0/Claude.exe',
                     local/'Programs/DeepSeek Harness/DeepSeek Harness.exe',
                     programfiles/'WindowsApps/OpenAI.Codex_1.2.3_x64/app/Codex.exe']:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'app')
        rows = discover_clients(platform='win32', home=self.home,
                                env={'LOCALAPPDATA': str(local), 'ProgramFiles': str(programfiles),
                                     'APPDATA': str(self.root/'Roaming')})
        self.assertTrue(all(row['installed'] for row in rows.values()))
        self.assertEqual(rows['claude']['dataDirectory'], str(self.root/'Roaming/Claude'))

    def test_linux_desktop_entry_handles_spaces_without_launching_command(self):
        executable = self.root/'My applications/Harness.AppImage'
        executable.parent.mkdir()
        executable.write_bytes(b'appimage')
        executable.chmod(0o700)
        entries = self.home/'.local/share/applications'
        entries.mkdir(parents=True)
        (entries/'deepseek.desktop').write_text(
            '[Desktop Entry]\nName=DeepSeek Harness\nType=Application\nExec="'+str(executable)+'" %U\n')
        with patch('bridge.integrations.discovery.shutil.which', return_value=None):
            rows = discover_clients(platform='linux', home=self.home,
                                    env={'PATH': '', 'XDG_DATA_DIRS': str(self.root/'empty')})
        self.assertEqual(rows['deepseek']['executable'], str(executable))
        self.assertEqual(rows['deepseek']['dataDirectory'], str(self.home/'.dsh'))

    @unittest.skipIf(os.name == 'nt', 'POSIX executable permissions and symlinks')
    def test_linux_launcher_resolves_real_gui_without_executing_script(self):
        app = self.root/'custom ChatGPT'
        app.mkdir()
        executable = app/'ChatGPT'
        executable.write_bytes(b'\x7fELFdesktop-fixture')
        executable.chmod(0o700)
        launcher = app/'codex-launcher'
        launcher.write_text('#!/bin/sh\nexit 99\n')
        launcher.chmod(0o700)
        link = self.root/'chatgpt'
        link.symlink_to(launcher)
        runtime = app/'resources/codex'
        runtime.parent.mkdir()
        runtime.write_bytes(b'cli')
        rows = discover_clients({'codexApplication': str(link)}, platform='linux', home=self.home, env={})
        self.assertEqual(rows['codex']['executable'], str(executable))
        self.assertEqual(rows['codex']['runtime'], str(runtime))
        launcher.chmod(0o600)
        self.assertIsNone(_candidate(link, 'codex', 'linux'))
        launcher.chmod(0o700)
        executable.chmod(0o600)
        self.assertIsNone(_candidate(link, 'codex', 'linux'))

    def test_linux_does_not_mistake_cli_or_unknown_script_for_desktop(self):
        for provider in ('codex', 'claude'):
            with self.subTest(provider=provider):
                folder = self.root/(provider+'-install')
                folder.mkdir()
                executable = folder/provider
                executable.write_bytes(b'\x7fELFcli-fixture')
                executable.chmod(0o700)
                self.assertIsNone(_candidate(executable, provider, 'linux'))
                resources = folder/'resources'
                resources.mkdir()
                (resources/'app.asar').touch()
                self.assertEqual(_candidate(executable, provider, 'linux')['executable'], str(executable))
                executable.write_text('#!/bin/sh\nexit 99\n')
                self.assertIsNone(_candidate(executable, provider, 'linux'))


class DeepSeekSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root/'existing-home'
        self.profile = self.home/'profiles/desktop/cordis.patch.yml'
        self.profile.parent.mkdir(parents=True)
        self.original = '- insert: []\n# existing user configuration\n'
        self.profile.write_text(self.original)
        self.adapter = DeepSeek(self.root/'gateway/connector', self.home)

    def test_scan_setup_is_idempotent_and_reversible_without_starting_host(self):
        first = self.adapter.ensure_installed()
        self.assertTrue(first['changed'])
        self.assertTrue(first['restartRequired'])
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.adapter.directory.iterdir()}
        before[self.profile] = (self.profile.read_bytes(), self.profile.stat().st_mtime_ns)
        self.assertEqual(self.adapter.ensure_installed(), {'installed': True, 'changed': False, 'restartRequired': False})
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
        self.assertEqual(self.adapter.installation_status(), {'installed': True, 'desktopProfileReady': True, 'conflict': False})
        self.profile.write_text(self.profile.read_text()+'# later user change\n')
        self.adapter.uninstall()
        self.assertIn(self.original, self.profile.read_text())
        self.assertIn('# later user change\n', self.profile.read_text())
        self.assertNotIn(MARKER, self.profile.read_text())

    def test_recognized_previous_gateway_is_reused_without_taking_ownership(self):
        self.adapter.ensure_installed()
        original = self.profile.read_bytes()
        other = DeepSeek(self.root/'other-gateway', self.home)
        self.assertTrue(other.installation_status()['conflict'])
        result = other.ensure_installed()
        self.assertTrue(result['reused'])
        self.assertFalse(result['restartRequired'])
        self.assertEqual(self.profile.read_bytes(), original)
        self.assertEqual(other.directory, self.adapter.directory)
        self.assertFalse((self.root/'other-gateway').exists())
        with self.assertRaisesRegex(ValueError, '未移除原接入'):
            other.uninstall()

    def test_missing_desktop_profile_does_not_initialize_separate_harness(self):
        adapter = DeepSeek(self.root/'new-gateway', self.root/'uninitialized')
        with self.assertRaisesRegex(ValueError, '先启动一次'):
            adapter.ensure_installed()
        self.assertFalse(adapter.home.exists())
        self.assertFalse(adapter.directory.exists())

    def test_conflicting_plugin_without_our_comment_is_not_duplicated(self):
        self.profile.write_text('- insert:\n    - id: codex-mobile-desktop\n      name: file:///another/connector.mjs\n')
        self.assertTrue(self.adapter.installation_status()['conflict'])
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.adapter.ensure_installed()
        self.assertFalse(self.adapter.directory.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes and symlinks')
    def test_patch_symlink_and_private_permissions_are_preserved(self):
        target = self.home/'personal-profile.yml'
        self.profile.rename(target)
        target.chmod(0o600)
        self.profile.symlink_to(target)
        self.adapter.ensure_installed()
        self.assertTrue(self.profile.is_symlink())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.adapter.uninstall()
        self.assertTrue(self.profile.is_symlink())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_connector_source_update_preserves_existing_authentication(self):
        self.adapter.ensure_installed()
        config = (self.adapter.directory/'connection.json').read_bytes()
        source = self.adapter.directory/'mobile-host.mjs'
        current = source.read_bytes()
        previous = b'// recognized prior connector fixture\n'
        source.write_bytes(previous)
        # Stand in for an explicitly allowlisted release, never arbitrary edits.
        with patch('bridge.integrations.deepseek_setup.PREVIOUS_SOURCE', hashlib.sha256(previous).hexdigest()):
            discovered = self.adapter.ensure_installed()
            self.assertTrue(discovered['updateRequired'])
            self.assertFalse(discovered['changed'])
            self.assertEqual(source.read_bytes(), previous)
            self.assertEqual(config, (self.adapter.directory/'connection.json').read_bytes())
            result = self.adapter.update_existing()
        self.assertTrue(result['changed'])
        self.assertTrue(result['restartRequired'])
        self.assertEqual(source.read_bytes(), current)
        self.assertEqual(config, (self.adapter.directory/'connection.json').read_bytes())
        self.assertEqual(self.profile.read_text().count(MARKER), 1)

    def test_unknown_source_in_owned_directory_is_not_overwritten(self):
        self.adapter.ensure_installed()
        (self.adapter.directory/'mobile-host.mjs').write_text('// older adapter')
        before = {path: path.read_bytes() for path in self.adapter.directory.iterdir()}
        before[self.profile] = self.profile.read_bytes()
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.adapter.ensure_installed()
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.adapter.update_existing()
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_blank_environment_uses_existing_default_home(self):
        with patch.dict(os.environ, {'DSH_HOME': '  '}), patch('bridge.integrations.deepseek.Path.home', return_value=self.root):
            self.assertEqual(DeepSeek(self.root/'gateway').home, self.root/'.dsh')


if __name__ == '__main__':
    unittest.main()
