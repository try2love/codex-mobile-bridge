import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bridge.features.network.interfaces as interfaces
import bridge.platforms.linux.interfaces as linux_interfaces
from bridge.platforms.linux import desktop as linux_desktop
from bridge.clients.codex.catalog import Catalog
from bridge.features.sessions.create import CreationError, open_in_desktop


class LinuxTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'synthetic /proc symlinks')
    def test_deleted_desktop_image_remains_visible_only_for_its_exact_path_and_uid(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            root = Path(folder).resolve()
            expected = root/'old-version'/'desktop'
            processes = [root/str(pid) for pid in (123, 124, 125)]
            for process, image in zip(processes, (expected, root/'other-version'/'desktop', expected)):
                process.mkdir(); (process/'exe').symlink_to(str(image)+' (deleted)')
                (process/'cmdline').write_bytes(b'desktop\0--profile\0fixture\0')
            stat = Path.stat
            def owner(path, *args, **kwargs):
                return SimpleNamespace(st_uid=os.getuid()+1) if path == processes[2] else stat(path, *args, **kwargs)
            with patch.object(Path, 'iterdir', return_value=processes), \
                 patch.object(Path, 'stat', autospec=True, side_effect=owner), \
                 patch.object(linux_desktop.os, 'readlink', wraps=os.readlink) as readlink:
                state = {'pids': [], 'commands': {}}
                linux_desktop.inventory({}, lambda image: state if image == expected else None)
                self.assertEqual(state, {'pids': [123], 'commands': {123: 'desktop --profile fixture'}})
                self.assertEqual(linux_desktop.processes(lambda image: image == expected), [123])
                marked = {int(process.name): str(image) for process, image in
                          linux_desktop._process_rows(preserve_deleted_marker=True)}
                self.assertEqual(marked, {
                    123: str(expected)+' (deleted)',
                    124: str(root/'other-version'/'desktop')+' (deleted)',
                })
                self.assertNotIn(processes[2]/'exe', [call.args[0] for call in readlink.call_args_list])

    @unittest.skipUnless(os.name == 'posix', 'synthetic /proc symlinks')
    def test_live_images_remain_visible_but_relative_or_disappearing_images_do_not(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            root = Path(folder).resolve(); image = root/'desktop'; image.touch()
            processes = [root/str(pid) for pid in (123, 124, 125)]
            for process in processes: process.mkdir()
            (processes[0]/'exe').symlink_to(image)
            relative = processes[1]/'relative'; relative.touch()
            (processes[1]/'exe').symlink_to('relative')
            with patch.object(Path, 'iterdir', return_value=processes):
                self.assertEqual(list(linux_desktop._process_rows()), [(processes[0], image)])

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
        with patch.object(linux_interfaces.shutil, 'which', return_value='/usr/sbin/ip'), \
                patch.object(linux_interfaces.subprocess, 'run',
                             return_value=subprocess.CompletedProcess([], 0, json.dumps(data))) as command:
            rows = linux_interfaces.enumerate_interfaces()
            self.assertEqual(interfaces.eligible_lan_addresses(rows), ['172.16.1.2', '192.168.1.7'])
            self.assertEqual(command.call_args.args[0], ['/usr/sbin/ip', '-j', '-4', 'address', 'show', 'up'])


    def test_ip_failures_fail_closed_without_dns_guessing(self):
        for value in ('invalid json', '{}', '[null]'):
            with self.subTest(value=value), \
                    patch.object(linux_interfaces.subprocess, 'run',
                                 return_value=subprocess.CompletedProcess([], 0, value)):
                self.assertEqual(interfaces.eligible_lan_addresses(linux_interfaces.enumerate_interfaces()), [])
