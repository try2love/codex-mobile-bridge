import json
import os
from pathlib import Path
import select
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from bridge.clients.discovery import discover_clients
from bridge.clients.zcode.installation import renderer_url
from bridge.platforms.linux import zcode_pipe


def package(root, name='@zcode/desktop', version='9.2.1-preview.7'):
    root.mkdir(exist_ok=True)
    exe = root/'zcode'
    exe.write_bytes(b'\x7fELF')
    exe.chmod(0o755)
    metadata = json.dumps({'name': name, 'productName': 'ZCode', 'version': version}).encode()
    index = json.dumps({'files': {'package.json': {'size': len(metadata), 'offset': '0'}}}).encode()
    (root/'resources').mkdir(exist_ok=True)
    (root/'resources/app.asar').write_bytes(struct.pack('<4I', 4, 8+len(index), 4+len(index), len(index)) + index + metadata)
    return exe


class ZCodeLinuxTests(unittest.TestCase):
    def test_shell_path_skips_cli_and_accepts_desktop_entry_with_packaged_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            cli = base/'bin'; cli.mkdir()
            (cli/'zcode').write_text('#!/bin/sh\nexit 22\n')
            (cli/'zcode').chmod(0o755)
            app = package(base/'custom name')
            entries = base/'data/applications'; entries.mkdir(parents=True)
            (entries/'zcode.desktop').write_text('[Desktop Entry]\nType=Application\nName=ZCode\nExec="'+str(app)+'" %U\n')
            env = {'XDG_DATA_HOME': str(entries.parent), 'XDG_DATA_DIRS': str(base/'empty'),
                   'PATH': str(cli)}
            result = discover_clients(platform='linux', home=base, env=env)
            self.assertEqual(result['zcode']['executable'], str(app))
            self.assertEqual(result['zcode']['version'], '9.2.1-preview.7')
            with patch('sys.platform', 'linux'):
                self.assertEqual(renderer_url(app), (app.parent/'resources/app.asar/out/renderer/index.html').as_uri())
            package(app.parent, name='another-electron-app')
            self.assertNotIn('zcode', discover_clients(platform='linux', home=base, env=env))
            self.assertIsNone(zcode_pipe.installation(cli/'zcode'))

    def test_only_direct_owned_zcode_host_is_excluded_from_main_and_never_signalled_on_quit(self):
        from types import SimpleNamespace
        from bridge.clients.lifecycle import _process_state
        exe = Path('/opt/ZCode/zcode')
        basename = {'image': str(exe), 'uid': 1000, 'zombie': False}
        rows = {11: {**basename, 'parent': 1}, 12: {**basename, 'parent': 11}}
        def inspect(commands):
            return _process_state({'id': 'zcode'}, SimpleNamespace(executable=exe), list(commands), commands)
        with patch('sys.platform', 'linux'), patch.object(zcode_pipe, 'Native') as native, patch('os.getuid', return_value=1000, create=True):
            native.return_value.identity.side_effect = rows.__getitem__
            commands = {11: 'ZCode', 12: 'zcode-host-local-1'}
            state = inspect(commands)
            self.assertEqual((state['mainPids'], state['runtimePids'], state['unknown']), ([11], [], False))
            for override in ({'parent': 1}, {'uid': 1001}, {'image': '/tmp/zcode'}):
                rows[12] = {**basename, 'parent': 11, **override}
                self.assertTrue(inspect(commands)['unknown'])
            self.assertTrue(inspect({12: commands[12]})['unknown'])

    def test_no_display_or_running_app_cannot_launch_a_new_process(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            app = package(Path(directory))
            with self.assertRaisesRegex(ValueError, '图形会话'):
                zcode_pipe.launch(app)
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}), patch.object(zcode_pipe.desktop, 'processes', return_value=[1234]):
                with self.assertRaisesRegex(ValueError, '已在运行'):
                    zcode_pipe.launch(app)

    @unittest.skipUnless(sys.platform == 'linux', 'requires native Linux descriptor inheritance and /proc')
    def test_anonymous_pipes_are_remapped_and_unrelated_descriptor_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            test_app = Path(directory)/'fixture'
            test_app.write_text('#!'+sys.executable+'\n'
                                'import os,sys\n'
                                "assert sys.argv[1:]==['--remote-debugging-pipe']\n"
                                "assert not os.path.exists('/proc/self/fd/'+os.environ['UNRELATED_FD'])\n"
                                'os.write(4,os.read(3,20))\n')
            test_app.chmod(0o700)
            with open(os.devnull) as unused:
                os.set_inheritable(unused.fileno(), True)
                env = {**os.environ, 'UNRELATED_FD': str(unused.fileno())}
                # Make sure the extra descriptor cannot coincide with the child's CDP fd.
                import fcntl
                duplicate = fcntl.fcntl(unused, fcntl.F_DUPFD, 30)
                try:
                    env['UNRELATED_FD'] = str(duplicate)
                    with patch.object(zcode_pipe, '_launch_context', return_value=(str(test_app), env)):
                        pid, write_fd, read_fd = zcode_pipe.launch(test_app)
                    try:
                        proof = zcode_pipe.identity(pid)
                        self.assertEqual((proof['pid'], proof['uid']), (pid, os.getuid()))
                        os.write(write_fd, b'private-pipe')
                        self.assertTrue(select.select([read_fd], [], [], 3)[0])
                        self.assertEqual(os.read(read_fd, 20), b'private-pipe')
                    finally:
                        os.close(write_fd); os.close(read_fd)
                        self.assertEqual(os.waitpid(pid, 0)[1], 0)
                finally:
                    os.close(duplicate)
