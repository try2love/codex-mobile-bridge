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
