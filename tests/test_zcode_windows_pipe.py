import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from bridge.platforms.windows import zcode_pipe


class WindowsPrivateRoute(unittest.TestCase):
    def test_only_local_pipe_namespace_accepted(self):
        local = r'\\.\pipe\cmb-zcode-test'
        self.assertEqual(zcode_pipe._local_path(local), local)
        for path in (r'\\other\pipe\test', r'\\.\pipe\bad\alias', local[:9], 'localhost:17742'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                zcode_pipe._local_path(path)

    def test_suitable_installation_has_no_release_number_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)/'ZCode.exe'
            app.touch()
            (app.parent/'resources').mkdir()
            (app.parent/'resources/app.asar').touch()
            for version in ('1.0.0', '3.17.2', '70.4.19.2'):
                with self.subTest(version=version), patch.object(zcode_pipe, 'product_version', return_value=version):
                    self.assertTrue(zcode_pipe.is_supported(app))

    def test_only_original_desktop_children_are_excluded_from_main_pids(self):
        from shlex import split
        exe = Path('/install/ZCode.exe')
        script = exe.parent/'resources/glm/zcode.cjs'
        commands = {10: str(exe), 11: f'{exe} --type=utility --utility-sub-type=node.mojom.NodeService',
                    12: f'{exe} {script} app-server --stdio --surface desktop',
                    13: f'{exe} {script} __zcode-plugin-host plugin.js'}
        ids = (10, 12, 13)
        def row(pid, *, parent):
            return {'pid': pid, 'image': os.path.normcase(str(exe)), 'session': 1, 'created': pid,
                    'parent': parents[pid]}
        parents = {10: 1, 11: 10, 12: 11, 13: 12}
        with patch.object(zcode_pipe, '_windows_argv', side_effect=split), patch.object(zcode_pipe, '_identity', side_effect=row):
            self.assertEqual(zcode_pipe.main_processes(exe, ids, commands), ([10], False))
            parents[11] = 3  # Identical command lines from a different GUI cannot qualify.
            self.assertEqual(zcode_pipe.main_processes(exe, ids, commands), ([10], True))
            parents[11] = 10
            parents[13] = 11
            self.assertEqual(zcode_pipe.main_processes(exe, ids, commands), ([10], True))
            parents[13] = 12
            clone = {**commands, 12: f'{exe} /other/zcode.cjs app-server --stdio --surface desktop'}
            self.assertTrue(zcode_pipe.main_processes(exe, ids, clone)[1])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows Win32 and local named pipe')
    def test_session_identity_and_local_pipe_peer_process(self):
        name = r'\\.\pipe\cmb-zcode-test-' + uuid.uuid4().hex
        listener = zcode_pipe.local_listener(name)
        result = []
        def echo():
            with listener.accept() as writer:
                result.append(zcode_pipe.peer_pid(writer, server=True))
                writer.send_bytes(b'ack')
        thread = threading.Thread(target=echo, daemon=True)
        thread.start()
        try:
            with zcode_pipe.local_connect(name) as client:
                self.assertEqual(zcode_pipe.peer_pid(client, server=False), os.getpid())
                self.assertTrue(client.poll(2))
                self.assertEqual(client.recv_bytes(3), b'ack')
            thread.join(2)
            self.assertEqual(result, [os.getpid()])
        finally:
            listener.close()
        args = zcode_pipe.process_args(os.getpid())
        self.assertEqual(Path(args[0]).resolve(), Path(sys._base_executable).resolve())
        if zcode_pipe._native().session:
            self.assertEqual(zcode_pipe.identity(os.getpid())['pid'], os.getpid())
        else:
            with self.assertRaises(PermissionError):
                zcode_pipe.identity(os.getpid())
