import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.desktop_app import DesktopApp
from bridge.clients.claude.setup import running_app
from bridge.clients.lifecycle import launch_client, launch_deepseek


class WindowsDesktopLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.executable = self.root/'Store/Claude/app/Claude.exe'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'MZ-fixture')
        resource = self.executable.parent/'resources/app.asar'
        resource.parent.mkdir(); resource.write_bytes(b'fixture')
        self.manifest = self.executable.parent.parent/'AppxManifest.xml'
        self.manifest.write_text('<Package><Applications><Application Id="NativeDesktop" '
                                 'Executable="app/Claude.exe"><VisualElements/></Application></Applications></Package>')
        self.inventory = {'packages': [{'Name': 'Claude', 'PackageFamilyName': 'Claude_fixture',
                                       'InstallLocation': str(self.executable.parent.parent)}]}
        for target, value in [('bridge.clients.desktop_app.sys.platform', 'win32'),
                              ('subprocess.CREATE_NEW_PROCESS_GROUP', 512)]:
            mocked = patch(target, value, create=True); mocked.start(); self.addCleanup(mocked.stop)
        inventory = patch('bridge.platforms.windows.discovery_ext.windows_installations', return_value=self.inventory)
        self.snapshot = inventory.start(); self.addCleanup(inventory.stop)
        self.denied = PermissionError('Store executable denies CreateProcess')
        self.denied.winerror = 5

    def assert_shell_activation(self, spawn):
        arguments = spawn.call_args_list[1].args[0]
        self.assertEqual(Path(arguments[0]).name.casefold(), 'explorer.exe')
        self.assertEqual(arguments[1], r'shell:AppsFolder\Claude_fixture!NativeDesktop')
        self.assertEqual(spawn.call_count, 2)

    def test_cold_claude_launch_falls_back_to_registered_manifest_application(self):
        with patch('bridge.platforms.windows.session.require_interactive'), \
                patch('bridge.clients.claude.setup._main_pids', side_effect=[[], [42]]), \
                patch('bridge.clients.claude.setup.time.sleep'), \
                patch('subprocess.Popen', side_effect=[self.denied, Mock()]) as spawn:
            self.assertEqual(running_app(self.executable, self.root), 42)
        self.assert_shell_activation(spawn)

    def test_client_enable_uses_same_cold_store_activation(self):
        descriptor = {'id': 'claude', 'installed': True, 'executable': str(self.executable),
                      'dataDirectory': str(self.root)}
        with patch('bridge.platforms.windows.session.require_interactive'), \
                patch.object(DesktopApp, 'processes', side_effect=[[], [42]]), \
                patch('bridge.clients.claude.setup._main_pids', side_effect=[[], [42]]), \
                patch('bridge.clients.claude.setup.time.sleep'), \
                patch('subprocess.Popen', side_effect=[self.denied, Mock()]) as spawn:
            self.assertEqual(launch_client(descriptor), {'running': True, 'launched': True})
        self.assert_shell_activation(spawn)

    def test_desktop_start_uses_same_store_activation(self):
        with patch('subprocess.Popen', side_effect=[self.denied, Mock()]) as spawn:
            DesktopApp(self.executable, self.root).start()
        self.assert_shell_activation(spawn)

    def test_successful_direct_launch_preserves_home_without_native_snapshot(self):
        with patch('subprocess.Popen') as spawn:
            DesktopApp(self.executable, self.root).start()
        self.snapshot.assert_not_called()
        self.assertEqual(spawn.call_args.args[0], [str(self.executable)])
        self.assertEqual(spawn.call_args.kwargs['env']['CODEX_HOME'], str(self.root))

    def test_direct_dsh_launch_preserves_existing_home(self):
        executable = self.root/'DSH Desktop.exe'; executable.write_bytes(b'MZ')
        descriptor = {'id': 'deepseek', 'installed': True, 'executable': str(executable),
                      'dataDirectory': str(self.root)}
        with patch.object(DesktopApp, 'processes', side_effect=[[], [42]]), patch('subprocess.Popen') as spawn:
            self.assertTrue(launch_deepseek(descriptor)['running'])
        self.snapshot.assert_not_called()
        self.assertEqual(spawn.call_args.args[0], [str(executable)])
        self.assertEqual(spawn.call_args.kwargs['env']['DSH_HOME'], str(self.root))

    def test_missing_family_or_mismatched_manifest_does_not_guess_aumid(self):
        for missing in ('family', 'executable', 'application_id'):
            with self.subTest(missing=missing):
                inventory = {'packages': [dict(self.inventory['packages'][0])]}
                if missing == 'family':
                    inventory['packages'][0].pop('PackageFamilyName')
                executable = 'app/Other.exe' if missing == 'executable' else 'app/Claude.exe'
                identifier = '' if missing == 'application_id' else 'NativeDesktop'
                self.manifest.write_text('<Package><Applications><Application Id="'+identifier+'" '
                    'Executable="'+executable+'"><VisualElements/></Application></Applications></Package>')
                self.snapshot.return_value = inventory
                with patch('subprocess.Popen', side_effect=self.denied) as spawn:
                    with self.assertRaises(PermissionError):
                        DesktopApp(self.executable, self.root).start()
                self.assertEqual(spawn.call_count, 1)

    def test_other_launch_errors_are_not_retried(self):
        missing = FileNotFoundError('missing executable'); missing.winerror = 2
        with patch('subprocess.Popen', side_effect=missing) as spawn:
            with self.assertRaises(FileNotFoundError):
                DesktopApp(self.executable, self.root).start()
        spawn.assert_called_once()
        self.snapshot.assert_not_called()

    def test_cancellation_during_registration_lookup_prevents_activation(self):
        cancelled = threading.Event()
        def inventory():
            cancelled.set()
            return self.inventory
        self.snapshot.side_effect = inventory
        with patch('subprocess.Popen', side_effect=self.denied) as spawn:
            with self.assertRaisesRegex(ValueError, '已取消'):
                DesktopApp(self.executable, self.root).launch(cancelled=cancelled)
        self.assertEqual(spawn.call_count, 1)


if __name__ == '__main__':
    unittest.main()
