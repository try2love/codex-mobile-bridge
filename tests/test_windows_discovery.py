import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.integrations.discovery import discover_clients


class WindowsDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root/'user'
        self.home.mkdir()
        self.env = {'LOCALAPPDATA': str(self.root/'Local'),
                    'APPDATA': str(self.root/'Roaming'),
                    'ProgramFiles': str(self.root/'Program Files')}

    def executable(self, path, *, electron=True):
        path = self.root/path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'MZ-fixture')
        if electron:
            resource = path.parent/'resources/app.asar'
            resource.parent.mkdir(exist_ok=True)
            resource.write_bytes(b'fixture')
        return path

    def scan(self, inventory, preferences=None):
        return discover_clients(preferences, platform='win32', home=self.home,
                                env=self.env, windows_inventory=inventory)

    def test_store_registration_finds_apps_without_listing_windowsapps(self):
        codex = self.executable('Protected/OpenAI.Codex_1/app/ChatGPT.exe')
        claude = self.executable('Protected/Claude_2/app/Claude.exe')
        inventory = {'packages': [
            {'Name': 'OpenAI.Codex', 'InstallLocation': str(codex.parent.parent)},
            {'Name': 'Claude', 'InstallLocation': str(claude.parent.parent),
             'PackageFamilyName': 'Claude_testfamily'}]}
        with patch('pathlib.Path.glob', side_effect=PermissionError('protected root')):
            rows = self.scan(inventory)
        self.assertEqual(rows['codex']['executable'], str(codex))
        self.assertEqual(rows['claude']['executable'], str(claude))
        self.assertEqual(rows['claude']['packageFamilyName'], 'Claude_testfamily')

    def test_stopped_custom_drive_dsh_uses_versioned_uninstall_icon_parent(self):
        dsh = self.executable('Other drive/DSH/DSH Desktop/DSH Desktop.exe')
        rows = self.scan({'uninstall': [{'DisplayName': 'DSH Desktop 2.0.17',
                                        'DisplayIcon': str(dsh.parent/'uninstallerIcon.ico')}]})
        self.assertEqual(rows['deepseek']['executable'], str(dsh))
        self.assertEqual(rows['deepseek']['dataDirectory'], str(self.home/'.dsh'))

    def test_store_manifest_selects_visible_gui_before_shims_and_helpers(self):
        shim = self.executable('Store/App/app/Codex.exe')
        self.executable('Store/App/app/ChatGPT.exe')
        gui = self.executable('Store/App/gui/ChatGPT.exe')
        helper = self.executable('Store/App/app/resources/codex-command-runner.exe', electron=False)
        (shim.parent.parent/'AppxManifest.xml').write_text(
            '<Package xmlns="urn:appx" xmlns:uap="urn:uap"><Applications>'
            '<Application Id="CodexCoreCommandRunner" Executable="app/resources/codex-command-runner.exe">'
            '<uap:VisualElements AppListEntry="none"/></Application>'
            '<Application Id="Hidden" Executable="app/Codex.exe">'
            '<uap:VisualElements AppListEntry="none"/></Application>'
            '<Application Id="App" Executable="gui\\ChatGPT.exe"><uap:VisualElements/></Application>'
            '</Applications></Package>')
        rows = self.scan({'packages': [{'Name': 'OpenAI.Codex', 'InstallLocation': str(shim.parent.parent)}]})
        self.assertEqual(rows['codex']['executable'], str(gui))
        self.assertNotEqual(rows['codex']['executable'], str(helper))

    def test_store_fallback_prefers_chatgpt_over_codex_shim(self):
        shim = self.executable('Store/App/app/Codex.exe')
        gui = self.executable('Store/App/app/ChatGPT.exe')
        inventory = {'packages': [{'Name': 'OpenAI.Codex', 'InstallLocation': str(shim.parent.parent)}]}
        for manifest in (None, '<broken', ' ' * (1024 * 1024 + 1)):
            with self.subTest(manifest='missing' if manifest is None else len(manifest)):
                if manifest is not None:
                    (shim.parent.parent/'AppxManifest.xml').write_text(manifest)
                self.assertEqual(self.scan(inventory)['codex']['executable'], str(gui))

    def test_manifest_does_not_escape_package_or_select_runtime(self):
        gui = self.executable('Store/App/app/ChatGPT.exe')
        self.executable('Store/Escape/ChatGPT.exe')
        self.executable('Store/App/app/resources/codex.exe', electron=False)
        (gui.parent.parent/'AppxManifest.xml').write_text(
            '<Package><Applications>'
            '<Application Executable="../Escape/ChatGPT.exe"><VisualElements/></Application>'
            '<Application Executable="app/resources/codex.exe"><VisualElements/></Application>'
            '</Applications></Package>')
        rows = self.scan({'packages': [{'Name': 'OpenAI.Codex', 'InstallLocation': str(gui.parent.parent)}]})
        self.assertEqual(rows['codex']['executable'], str(gui))

    def test_registered_install_location_and_quoted_display_icon(self):
        dsh = self.executable('Custom/DSH Desktop.exe')
        claude = self.executable('Custom Claude/Claude.exe')
        rows = self.scan({'uninstall': [
            {'DisplayName': 'DeepSeek Harness', 'InstallLocation': str(dsh.parent)},
            {'DisplayName': 'Claude Desktop', 'DisplayIcon': '"'+str(claude)+'",0'}]})
        self.assertEqual(rows['deepseek']['executable'], str(dsh))
        self.assertEqual(rows['claude']['executable'], str(claude))

    def test_app_paths_and_running_gui_fallback_exclude_cli(self):
        runtime = self.executable('Codex/bin/version/codex.exe', electron=False)
        gui = self.executable('Custom Codex/ChatGPT.exe')
        claude = self.executable('Custom Claude/Claude.exe')
        rows = self.scan({'appPaths': [{'Name': 'codex.exe', 'Path': str(runtime)},
                                      {'Name': 'Claude.exe', 'Path': str(claude)}],
                          'processes': [{'ProcessName': 'codex', 'Path': str(runtime)},
                                        {'ProcessName': 'ChatGPT', 'Path': str(gui)}]})
        self.assertEqual(rows['codex']['executable'], str(gui))
        self.assertEqual(rows['claude']['executable'], str(claude))
        self.assertFalse(self.scan({'processes': [{'ProcessName': 'codex', 'Path': str(runtime)}]})['codex']['installed'])

    def test_unpacked_electron_dsh_and_missing_candidates(self):
        dsh = self.executable('Portable/DSH Desktop.exe', electron=False)
        package = dsh.parent/'resources/app/package.json'
        package.parent.mkdir(parents=True)
        package.write_text('{}')
        rows = self.scan({'processes': [{'ProcessName': 'DSH Desktop', 'Path': str(dsh)},
                                       {'ProcessName': 'Claude', 'Path': str(self.root/'missing/Claude.exe')}]})
        self.assertTrue(rows['deepseek']['installed'])
        self.assertFalse(rows['claude']['installed'])

    def test_explicit_application_precedes_registered_app(self):
        explicit = self.executable('Selected/Claude.exe')
        registered = self.executable('Registered/Claude.exe')
        rows = self.scan({'processes': [{'ProcessName': 'Claude', 'Path': str(registered)}]},
                         {'claudeApplication': str(explicit)})
        self.assertEqual(rows['claude']['executable'], str(explicit))

    def test_similar_product_names_do_not_match(self):
        wrong = self.executable('Wrong/DSH Desktop.exe')
        rows = self.scan({'uninstall': [{'DisplayName': 'DSH Desktop Tools',
                                        'InstallLocation': str(wrong.parent)}],
                          'packages': [{'Name': 'Unrelated.Claude',
                                        'InstallLocation': str(wrong.parent)}]})
        self.assertFalse(any(row['installed'] for row in rows.values()))

    def test_native_snapshot_runs_once_per_real_scan(self):
        with patch.dict(os.environ, self.env, clear=True), \
                patch('bridge.integrations.discovery.windows_installations', return_value={}) as inventory:
            discover_clients(platform='win32', home=self.home)
        inventory.assert_called_once_with()

    def test_explicit_environment_does_not_inspect_host_installations(self):
        with patch('bridge.integrations.discovery.windows_installations') as inventory:
            discover_clients(platform='win32', home=self.home, env=self.env)
        inventory.assert_not_called()


class WindowsInventoryTests(unittest.TestCase):
    def inventory(self):
        from bridge.integrations.windows_discovery import windows_installations
        return windows_installations()

    def test_snapshot_uses_windows_powershell_utf8_and_bounded_timeout(self):
        response = Mock(stdout=json.dumps({'packages': [{'Name': 'Claude'}]}), returncode=0)
        with patch('bridge.integrations.windows_discovery.subprocess.run', return_value=response) as run:
            self.assertEqual(self.inventory()['packages'][0]['Name'], 'Claude')
        self.assertEqual(run.call_args.args[0][0], 'powershell.exe')
        self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')
        self.assertLessEqual(run.call_args.kwargs['timeout'], 15)

    def test_unavailable_metadata_does_not_abort_filesystem_scan(self):
        for error in (OSError('no powershell'), subprocess.TimeoutExpired('powershell.exe', 12)):
            with self.subTest(error=type(error).__name__), \
                    patch('bridge.integrations.windows_discovery.subprocess.run', side_effect=error):
                self.assertEqual(self.inventory(), {})
        for output in ('invalid-json', '[]', 'null'):
            with self.subTest(output=output), \
                    patch('bridge.integrations.windows_discovery.subprocess.run', return_value=Mock(stdout=output)):
                self.assertEqual(self.inventory(), {})


if __name__ == '__main__':
    unittest.main()
