"""Linux source/fixture contracts; native Desktop acceptance is separate."""
import configparser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bridge.clients.codex import desktop_runtime as runtime
from bridge.platforms.linux import codex_launch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == 'nt', 'Linux desktop fixtures require POSIX paths and locking')
class LinuxDesktopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root/'home';self.home.mkdir()
        self.app = self.root/'App/Codex';self.app.parent.mkdir()
        self.app.write_bytes(b'fixture');self.app.chmod(0o700)
        resources = self.app.parent/'resources';resources.mkdir()
        (resources/'app.asar').write_bytes(b'fixture')
        self.cli = resources/'codex';self.cli.write_bytes(b'fixture');self.cli.chmod(0o700)
        self.system = self.root/'system/applications';self.system.mkdir(parents=True)
        self.user = self.home/'data'
        self.entry = self.system/'codex.desktop'
        self.original = '[Desktop Entry]\nType=Application\nName=Codex\nIcon=codex\nExec="'+str(self.app)+'" %U\n'
        self.entry.write_text(self.original)
        environment = patch.dict(os.environ, {'XDG_DATA_HOME': str(self.user), 'XDG_DATA_DIRS': str(self.system.parent)})
        environment.start();self.addCleanup(environment.stop)

    def test_user_desktop_entry_preserves_original_arguments_and_never_changes_system_file(self):
        launcher = self.home/'private dir/codex-cli'
        codex_launch.install(self.app, self.home, launcher)
        target = self.user/'applications/codex.desktop'
        config = configparser.ConfigParser(interpolation=None);config.read(target)
        exec_line = config['Desktop Entry']['Exec']
        self.assertTrue(exec_line.startswith('/usr/bin/env '))
        self.assertIn('CODEX_CLI_PATH='+str(launcher), exec_line)
        self.assertTrue(exec_line.endswith('"'+str(self.app)+'" %U'))
        self.assertEqual(self.entry.read_text(), self.original)
        before = target.read_bytes();stamp = target.stat().st_mtime_ns
        codex_launch.install(self.app, self.home, launcher)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_prepared_entry_uses_exec_instead_of_bypassing_it_with_dbus(self):
        self.entry.write_text(self.original+'DBusActivatable=true\n')
        codex_launch.install(self.app, self.home, self.home/'launcher')
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.user/'applications/codex.desktop')
        self.assertEqual(config['Desktop Entry']['DBusActivatable'], 'false')
        self.assertIn('DBusActivatable=true', self.entry.read_text())

    def test_path_menu_launcher_resolves_to_the_selected_real_gui(self):
        binary = self.root/'bin';binary.mkdir()
        (binary/'chatgpt').symlink_to(self.app)
        self.entry.write_text(self.original.replace('"'+str(self.app)+'"', 'chatgpt'))
        with patch.dict(os.environ, {'PATH':str(binary)}):
            codex_launch.install(self.app,self.home,self.home/'launcher')
        config=configparser.ConfigParser(interpolation=None)
        config.read(self.user/'applications/codex.desktop')
        self.assertTrue(config['Desktop Entry']['Exec'].endswith('chatgpt %U'))
        self.assertEqual(config['Desktop Entry']['X-CMB-Original-Exec'],'chatgpt %U')

    def test_missing_ambiguous_and_symlinked_entries_are_not_overwritten(self):
        self.entry.unlink()
        with self.assertRaisesRegex(ValueError, '唯一'):
            codex_launch.install(self.app, self.home, self.home/'launcher')
        self.entry.write_text(self.original)
        other = self.system/'other.desktop';other.write_text(self.original)
        with self.assertRaisesRegex(ValueError, '唯一'):
            codex_launch.install(self.app, self.home, self.home/'launcher')
        other.unlink()
        target = self.user/'applications/codex.desktop';target.parent.mkdir(parents=True)
        target.symlink_to(self.entry)
        with self.assertRaisesRegex(ValueError, '身份'):
            codex_launch.install(self.app, self.home, self.home/'launcher')
        self.assertEqual(self.entry.read_text(), self.original)

    def test_runtime_configuration_and_settings_are_available_without_a_macos_identity_reader(self):
        with patch.object(runtime, 'sys', SimpleNamespace(platform='linux', executable='/not-installed-gateway', frozen=True)):
            runtime.configure(self.home, self.app, self.cli)
            self.assertEqual(runtime.settings(self.home)['application'], str(self.app))
            environment = runtime.launch_environment(self.home, self.app)
            self.assertEqual(environment['CODEX_CLI_PATH'], str(runtime.directory(self.home)/'codex-cli'))
            with patch('bridge.platforms.linux.codex_runtime.identity', return_value={'pid': 1}) as identify:
                self.assertEqual(runtime.process_identity(1), {'pid': 1})
                identify.assert_called_once_with(1)
            runtime.enable_ordinary_launch(self.home, self.app)
        self.assertTrue((self.user/'applications/codex.desktop').is_file())
        script = (runtime.directory(self.home)/'codex-cli').read_text()
        self.assertIn('if [ ! -x /not-installed-gateway ]', script)

    def test_linux_utility_host_uses_same_verified_application_ancestry(self):
        app = {'pid': 1, 'parent': 99, 'image': str(self.app)}
        host = {'pid': 2, 'parent': 1, 'image': str(self.app)}
        with patch.object(runtime.sys, 'platform', 'linux'), patch.object(runtime, 'process_identity', side_effect=lambda pid: {1:app, 2:host}[pid]):
            self.assertEqual(runtime.desktop_owner(host, str(self.app)), (app, host))

    def test_user_entry_with_same_id_remains_authoritative(self):
        user = self.user/'applications/codex.desktop';user.parent.mkdir(parents=True)
        other = self.root/'Other';other.write_text('fixture')
        content = '[Desktop Entry]\nName=Custom Codex\nExec='+str(other)+'\n'
        user.write_text(content)
        with self.assertRaisesRegex(ValueError, '唯一'):
            codex_launch.install(self.app, self.home, self.home/'launcher')
        self.assertEqual(user.read_text(), content)

    def test_unsupported_packaging_does_not_claim_initialization_support(self):
        (self.app.parent/'resources/app.asar').unlink()
        with patch.object(runtime.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(ValueError, '布局'):
                runtime.launcher_settings(self.app)

    def test_exec_paths_escape_percent_and_reserved_characters(self):
        self.assertEqual(codex_launch.quoted('/tmp/a%"`$\\'), '"/tmp/a%%\\"\\`\\$\\\\"')

    @unittest.skipUnless(sys.platform == 'linux', 'real Linux /proc required')
    def test_exited_unreaped_process_is_absent_instead_of_an_unknown_live_owner(self):
        from bridge.platforms.linux.codex_runtime import identity
        child=subprocess.Popen([sys.executable,'-c','pass'])
        self.addCleanup(child.wait)
        os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOWAIT)
        with self.assertRaises(ProcessLookupError):
            identity(child.pid)


if __name__ == '__main__':
    unittest.main()
