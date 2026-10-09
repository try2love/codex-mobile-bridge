import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run
from bridge.clients.codex.catalog import Catalog
from bridge.features.sessions.create import CreationError, open_in_desktop


class LinuxTests(unittest.TestCase):
    def test_ubuntu_chatgpt_package_layout(self):
        runtime = Path('/usr/lib/chatgpt/resources/codex')
        with patch('bridge.clients.codex.catalog.sys.platform', 'linux'), \
                patch('bridge.clients.codex.catalog.shutil.which', return_value=None), \
                patch.object(Path, 'is_file', autospec=True, side_effect=lambda path: path == runtime), \
                patch('bridge.clients.codex.catalog.os.access', return_value=True):
            self.assertEqual(Catalog.find_runtime(), runtime)

    def test_bundled_runtime_precedes_path_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            launcher = root / 'chatgpt'
            launcher.touch()
            runtime = root / 'resources/codex-cli/bin/codex'
            runtime.parent.mkdir(parents=True)
            runtime.touch()
            with patch('bridge.clients.codex.catalog.sys.platform', 'linux'), \
                    patch('bridge.clients.codex.catalog.shutil.which', side_effect=lambda name: str(launcher) if name == 'chatgpt' else '/cli/codex'), \
                    patch('bridge.clients.codex.catalog.os.access', return_value=True):
                self.assertEqual(Catalog.find_runtime(), runtime.resolve())

    def test_runtime_fallback_and_missing_install(self):
        with patch('bridge.clients.codex.catalog.sys.platform', 'linux'), patch.object(Path, 'is_file', return_value=False):
            with patch('bridge.clients.codex.catalog.shutil.which', side_effect=lambda name: '/cli/codex' if name == 'codex' else None):
                self.assertEqual(Catalog.find_runtime(), Path('/cli/codex'))
            with patch('bridge.clients.codex.catalog.shutil.which', return_value=None):
                self.assertIsNone(Catalog.find_runtime())

    @unittest.skipIf(os.name == 'nt', 'POSIX executable permissions and symlinks')
    def test_symlinked_launcher_and_executable_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            launcher = root / 'app/chatgpt'
            launcher.parent.mkdir()
            launcher.touch()
            link = root / 'chatgpt'
            link.symlink_to(launcher)
            runtime = root / 'app/resources/codex'
            runtime.parent.mkdir()
            runtime.touch()
            is_file = Path.is_file
            with patch('bridge.clients.codex.catalog.shutil.which', side_effect=lambda name: str(link) if name == 'chatgpt' else None), \
                    patch.object(Path, 'is_file', autospec=True,
                                 side_effect=lambda path: is_file(path) if root in path.parents else False):
                runtime.chmod(0o600)
                self.assertIsNone(Catalog.find_linux_runtime())
                runtime.chmod(0o700)
                self.assertEqual(Catalog.find_linux_runtime(), runtime.resolve())

    def test_desktop_link_is_literal_and_keeps_remote_host(self):
        tid = '11111111-1111-4111-8111-111111111111'
        with patch('bridge.features.sessions.create.sys.platform', 'linux'), patch('bridge.features.sessions.create.subprocess.run') as opened:
            open_in_desktop(tid, 'remote host&value')
            self.assertEqual(opened.call_args.args[0],
                             ['xdg-open', 'codex://threads/' + tid + '?hostId=remote+host%26value'])
            self.assertTrue(opened.call_args.kwargs['check'])
            with self.assertRaises(ValueError):
                open_in_desktop('invalid', 'local')
            self.assertEqual(opened.call_count, 1)

    def test_missing_handler_and_timeout_are_actionable(self):
        tid = '11111111-1111-4111-8111-111111111111'
        for error in (FileNotFoundError(), subprocess.CalledProcessError(3, 'xdg-open'),
                      subprocess.TimeoutExpired('xdg-open', 10)):
            with self.subTest(error=error), patch('bridge.features.sessions.create.sys.platform', 'linux'), \
                    patch('bridge.features.sessions.create.subprocess.run', side_effect=error):
                with self.assertRaisesRegex(CreationError, 'xdg-open'):
                    open_in_desktop(tid, 'local')

    def test_linux_interfaces_do_not_depend_on_hostname_dns(self):
        data = [{'addr_info': [{'family': 'inet', 'local': ip} for ip in
                              ('127.0.0.1', '192.168.1.7', '172.16.1.2', '192.168.1.7', '0.0.0.0')]}]
        with patch('run.sys.platform', 'linux'), patch('run.shutil.which', return_value='/usr/sbin/ip'), \
                patch('run.subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(data))) as command, \
                patch('run.socket.getaddrinfo') as dns:
            self.assertEqual(run.addresses(), ['127.0.0.1', '172.16.1.2', '192.168.1.7', 'localhost'])
            self.assertEqual(command.call_args.args[0], ['/usr/sbin/ip', '-j', '-4', 'address', 'show', 'up'])
            dns.assert_not_called()

    def test_ip_failures_fall_back_without_breaking_startup(self):
        for value in ('invalid json', '{}', '[null]'):
            with self.subTest(value=value), patch('run.sys.platform', 'linux'), \
                    patch('run.shutil.which', return_value='/usr/sbin/ip'), \
                    patch('run.subprocess.run', return_value=subprocess.CompletedProcess([], 0, value)), \
                    patch.object(Path, 'exists', return_value=False), \
                    patch('run.socket.getaddrinfo', return_value=[(None, None, None, None, ('192.168.1.9', 0))]):
                self.assertIn('192.168.1.9', run.addresses())
