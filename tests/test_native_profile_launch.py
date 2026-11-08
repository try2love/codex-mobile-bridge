import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.platforms.windows.desktop import start


ROOT = Path(__file__).resolve().parents[1]


class WindowsNativeProfileLaunchTests(unittest.TestCase):
    def test_packaged_instance_uses_verified_alias_and_keeps_private_environment(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root = Path(directory)
            executable = root/'WindowsApps/Claude_4.4212/app/claude.exe'
            alias = root/'real-local/Microsoft/WindowsApps/Claude.exe'
            private = {'APPDATA': str(root/'profile/roaming'), 'LOCALAPPDATA': str(root/'profile/local')}
            original = dict(os.environ)
            with patch('bridge.platforms.windows.discovery_ext.application_execution_alias', return_value=alias) as discover, \
                    patch('subprocess.Popen') as launch, \
                    patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, create=True):
                start(executable, root, private, arguments=('--user-data-dir='+str(root/'profile'),), new_instance=True)
                discover.assert_called_once_with(executable)
                self.assertEqual(launch.call_args.args[0], [str(alias), '--user-data-dir='+str(root/'profile')])
                self.assertEqual(launch.call_args.kwargs['env']['LOCALAPPDATA'], private['LOCALAPPDATA'])
                self.assertEqual(launch.call_args.kwargs['env']['APPDATA'], private['APPDATA'])
                self.assertEqual(dict(os.environ), original)

    def test_unverified_package_cannot_fall_back_to_explorer_or_default_instance(self):
        with patch('bridge.platforms.windows.discovery_ext.application_execution_alias', return_value=None), \
                patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, '独立启动别名'):
                start(Path('/WindowsApps/Claude/app/claude.exe'), Path('/profile'), {}, new_instance=True)
            launch.assert_not_called()

    def test_unpacked_app_retains_direct_native_launch(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory, \
                patch('bridge.platforms.windows.discovery_ext.application_execution_alias') as discover, \
                patch('subprocess.Popen') as launch, \
                patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, create=True):
            root = Path(directory)
            executable = root/'DeepSeek.exe'
            start(executable, root, {'DSH_HOME': str(root/'profile')}, arguments=('--user-data-dir='+str(root/'ui'),), new_instance=True)
            discover.assert_not_called()
            self.assertEqual(launch.call_args.args[0], [str(executable), '--user-data-dir='+str(root/'ui')])

    def test_default_launch_does_not_change_existing_entry_behavior(self):
        with patch('bridge.platforms.windows.discovery_ext.application_execution_alias') as discover, \
                patch('subprocess.Popen') as launch, \
                patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, create=True):
            executable = Path('/WindowsApps/Claude/app/claude.exe')
            start(executable, Path('/profile'), {})
            discover.assert_not_called()
            self.assertEqual(launch.call_args.args[0], [str(executable)])


if __name__ == '__main__':
    unittest.main()
