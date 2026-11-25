"""Default Desktop initialization also prepares ordinary GUI launches."""
import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.clients.codex import desktop_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]


class OrdinaryLaunchTests(unittest.TestCase):
    def setUp(self):
        from bridge.platforms.macos import codex_launch
        self.login_patch = patch.object(codex_launch, 'install_login_environment')
        self.login_patch.start()
        self.addCleanup(self.login_patch.stop)
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home/'bridge-desktop-runtime'
        self.root.mkdir(mode=0o700)
        self.helper = self.home/'helper'
        self.helper.write_bytes(b'fixture helper'); self.helper.chmod(0o700)
        self.application = self.home/'Codex.app/Contents/MacOS/Codex'
        self.config = {'version': 1, 'home': str(self.home), 'application': str(self.application),
                       'cli': str(self.home/'Codex.app/Contents/Resources/codex')}
        configuration = self.root/'settings.json'
        configuration.write_text(json.dumps(self.config), encoding='utf-8')
        configuration.chmod(0o600)

    @unittest.skipIf(os.name == 'nt', 'macOS launcher fixture uses POSIX ownership, modes and flock')
    def test_connected_runtime_can_prepare_ordinary_launch_without_rewriting_live_archive(self):
        from bridge.platforms.macos import codex_launch
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'ordinary_launch_helper', return_value=self.helper), \
             patch.object(codex_launch, 'get_environment', return_value=None), \
             patch.object(codex_launch, 'set_environment') as install:
            self.assertTrue(runtime.enable_ordinary_launch(self.home, self.application))
            launcher = Path(install.call_args.args[0])
            self.assertEqual(launcher.read_bytes(), self.helper.read_bytes())
            record = runtime._read(self.root/'ordinary-launch.json')
            self.assertEqual(record['application'], str(self.application))
            self.assertEqual(record['home'], str(self.home))
            self.assertEqual(record['cli'], self.config['cli'])
            self.assertIn('codex-desktop-runtime', record['command'])
            first = launcher.read_bytes()
            self.helper.write_bytes(b'new helper')
            runtime.enable_ordinary_launch(self.home, self.application)
            self.assertNotEqual(Path(install.call_args.args[0]), launcher)
            self.assertEqual(launcher.read_bytes(), first)

    @unittest.skipIf(os.name == 'nt', 'macOS launcher fixture uses POSIX ownership, modes and flock')
    def test_other_software_launch_override_is_preserved(self):
        from bridge.platforms.macos import codex_launch
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'ordinary_launch_helper', return_value=self.helper), \
             patch.object(codex_launch, 'get_environment', return_value='/external/custom-codex'), \
             patch.object(codex_launch, 'set_environment') as install:
            with self.assertRaisesRegex(ValueError, '启动配置'):
                runtime.enable_ordinary_launch(self.home, self.application)
            install.assert_not_called()
            self.assertFalse((self.root/'ordinary-launch.json').exists())

    @unittest.skipIf(os.name == 'nt', 'macOS launcher fixture uses POSIX ownership, modes and flock')
    def test_existing_owned_launch_path_is_refreshed_without_overwriting_other_keys(self):
        from bridge.platforms.macos import codex_launch
        old = self.root/'gui'/'old-generation'/'codex-launcher'
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'ordinary_launch_helper', return_value=self.helper), \
             patch.object(codex_launch, 'get_environment', return_value=str(old)), \
             patch.object(codex_launch, 'set_environment') as install:
            self.assertTrue(runtime.enable_ordinary_launch(self.home, self.application))
            self.assertEqual(len(install.call_args.args), 1)

    def test_other_platforms_and_changed_app_are_not_registered(self):
        with patch.object(runtime.sys, 'platform', 'freebsd'):
            self.assertFalse(runtime.enable_ordinary_launch(self.home, self.application))
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, 'settings', return_value=self.config):
            with self.assertRaisesRegex(ValueError, '程序已变化'):
                runtime.enable_ordinary_launch(self.home, self.home/'Other.app/Contents/MacOS/Other')

    @unittest.skipIf(os.name == 'nt', 'macOS login job verifies POSIX ownership and permission modes')
    def test_login_job_is_private_idempotent_and_bound_to_owned_dispatcher(self):
        from bridge.platforms.macos import codex_launch
        launcher = self.root/'gui'/'generation'/'codex-launcher'
        # Exercise the real writer under an isolated user Library.
        self.login_patch.stop()
        with patch.object(codex_launch.Path, 'home', return_value=self.home):
            codex_launch.install_login_environment(launcher)
            target = self.home/'Library/LaunchAgents'/(codex_launch.LABEL+'.plist')
            value = plistlib.loads(target.read_bytes())
            self.assertEqual(value['ProgramArguments'], [str(launcher), '--register-environment'])
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            before = target.stat().st_mtime_ns
            codex_launch.install_login_environment(launcher)
            self.assertEqual(target.stat().st_mtime_ns, before)
            value['ProgramArguments'] = ['/other/command', '--register-environment']
            target.write_bytes(plistlib.dumps(value))
            with self.assertRaisesRegex(ValueError, '保留原配置'):
                codex_launch.install_login_environment(launcher)


if __name__ == '__main__':
    unittest.main()
