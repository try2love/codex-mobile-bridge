import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SOURCE = '''
function isDesktopInstallerQuitRequest(argv, platform) {
  return platform === "win32" && argv.includes("--dsh-installer-quit");
}
async function start() {
  if (!app.requestSingleInstanceLock()) { app.quit(); return; }
  if (isDesktopInstallerQuitRequest(process.argv, process.platform)) { app.quit(); return; }
  initializeProfile();
}
const requestQuit = (code) => { shutdown.request(code); };
app.on("second-instance", (_event, argv) => {
  if (isDesktopInstallerQuitRequest(argv, process.platform)) { requestQuit(0); return; }
  runtime.show();
});
'''


class DshWindowsQuitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.executable = self.root/'DSH Desktop.exe'; self.executable.touch()
        self.folder = self.root/'resources/app'; (self.folder/'lib').mkdir(parents=True)
        self.package = {'name': 'dsh-plugin-desktop', 'main': 'lib/main.js'}
        (self.folder/'package.json').write_text(json.dumps(self.package))
        (self.folder/'lib/main.js').write_text(SOURCE)
        self.app = SimpleNamespace(executable=self.executable)
        self.state = {'mainPids': [11], 'runtimePids': [12]}
        self.commands = {11: 'main', 12: 'host'}
        self.profile = self.root/'roaming/DSH Desktop'
        self.arguments = {'main': [str(self.executable)], 'host': [str(self.executable),
                          '--type=utility', '--user-data-dir='+str(self.profile)]}
        environment = patch.dict(os.environ, {'APPDATA': str(self.profile.parent)})
        environment.start(); self.addCleanup(environment.stop)

    def command(self):
        from bridge.integrations.dsh_windows_quit import installer_quit_command
        with patch('bridge.integrations.dsh_windows_quit._windows_argv', side_effect=lambda text: self.arguments[text]):
            return installer_quit_command(self.app, self.state, self.commands)

    def test_supported_default_instance_gets_only_native_quit_flag(self):
        self.assertEqual(self.command(), [str(self.executable), '--dsh-installer-quit'])

    def test_explicit_profile_is_matched_and_forwarded_without_other_launch_flags(self):
        profile = str(self.root/'custom user data')
        self.arguments['main'] += ['--user-data-dir', profile, '--open-window']
        self.arguments['host'][-1] = '--user-data-dir='+profile
        self.assertEqual(self.command(), [str(self.executable), '--user-data-dir='+profile, '--dsh-installer-quit'])

    def test_foreign_or_ambiguous_user_data_never_sends_a_flag(self):
        for args in ([str(self.executable)], [str(self.executable), '--user-data-dir='+str(self.root/'other')],
                     self.arguments['host']+['--user-data-dir='+str(self.profile)]):
            with self.subTest(args=args):
                self.arguments['host'] = args
                with self.assertRaisesRegex(ValueError, '数据目录'):
                    self.command()

    def test_unsupported_flag_or_foreground_before_handoff_fails_closed(self):
        for source in (SOURCE.replace('--dsh-installer-quit', '--other'),
                       SOURCE.replace('if (isDesktopInstallerQuitRequest(argv, process.platform))',
                                      'runtime.show(); if (isDesktopInstallerQuitRequest(argv, process.platform))')):
            (self.folder/'lib/main.js').write_text(source)
            with self.assertRaisesRegex(ValueError, '后台退出通道'):
                self.command()

    def test_unrelated_package_and_escaping_main_are_rejected(self):
        for package in ({**self.package, 'name': 'unrelated'}, {**self.package, 'main': '../outside.js'}):
            (self.folder/'package.json').write_text(json.dumps(package))
            with self.assertRaisesRegex(ValueError, '后台退出通道'):
                self.command()

    def test_asar_packaging_is_inspected_without_extracting_files(self):
        files = {'package.json': json.dumps(self.package).encode(), 'lib/main.js': SOURCE.encode()}
        tree, data = {'files': {}}, b''
        for name, content in files.items():
            node = tree
            parts = name.split('/')
            for part in parts[:-1]: node = node['files'].setdefault(part, {'files': {}})
            node['files'][parts[-1]] = {'offset': str(len(data)), 'size': len(content)}
            data += content
        raw = json.dumps(tree).encode(); size = 8+len(raw)
        (self.folder/'package.json').unlink(); (self.folder/'lib/main.js').unlink()
        (self.root/'resources/app.asar').write_bytes(struct.pack('<4I', 4, size, size-4, len(raw))+raw+data)
        self.assertEqual(self.command(), [str(self.executable), '--dsh-installer-quit'])


if __name__ == '__main__': unittest.main()
