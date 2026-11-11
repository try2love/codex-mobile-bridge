"""Controlled Windows daemon maintenance tests; no system process is stopped."""
import base64
from contextlib import contextmanager
import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from bridge.clients.codex.daemon_lifecycle import request_windows_shutdown
from bridge.platforms.windows import codex_daemon_env as native


class ControlChannel:
    def __init__(self, echo):
        self.echo, self.frames, self.buffer = echo, [], b''

    def settimeout(self, timeout):
        assert timeout > 0

    def sendall(self, data):
        data = bytes(data)
        if data.startswith(b'GET '):
            assert data.startswith(b'GET /daemon/shutdown HTTP/1.1\r\n')
            nonce = data.split(b'Sec-WebSocket-Key: ', 1)[1].split(b'\r\n', 1)[0]
            token = base64.b64encode(hashlib.sha1(nonce + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
            self.buffer += b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: ' + token + b'\r\n\r\n'
            return
        assert data[:2] == b'\x81\x84'
        self.frames.append(bytes(byte ^ data[2 + index % 4] for index, byte in enumerate(data[6:])))
        if self.echo:
            self.buffer += bytes((0x81, len(self.echo))) + self.echo

    def recv(self, size):
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result


class WindowsShutdownTests(unittest.TestCase):
    def setUp(self):
        self.home = Path('/fixture')
        self.executable = self.home / 'codex.exe'
        self.generation = (4321, native.FILETIME_UNIX_EPOCH + 70, str(self.executable))
        self.kernel = Mock()
        self.kernel.WaitForSingleObject.return_value = native.WAIT_RUNNING

    def test_exact_peer_pid_and_live_held_handle_are_required(self):
        with patch('bridge.platforms.windows.local_service.read_record', side_effect=FileNotFoundError):
            drain = native.WindowsDaemonDrain(self.home, self.executable, self.kernel, 8, self.generation, True)
        with patch.object(native, '_peer_pid', return_value=2468):
            with self.assertRaisesRegex(OSError, 'another process'):
                drain.verify_peer(Mock())
        with patch.object(native, '_peer_pid', return_value=4321):
            drain.verify_peer(Mock())
            self.kernel.WaitForSingleObject.return_value = native.WAIT_EXITED
            with self.assertRaisesRegex(OSError, 'another process'):
                drain.verify_peer(Mock())
        drain.wait(3)
        self.kernel.WaitForSingleObject.assert_called_with(8, 3000)

    def test_dispatched_generation_is_persisted_and_different_generation_can_proceed(self):
        records = {}
        def read(path):
            if path not in records:
                raise FileNotFoundError
            return records[path]
        def write(path, row):
            records[path] = row
        with patch('bridge.platforms.windows.local_service.read_record', side_effect=read), \
                patch('bridge.platforms.windows.local_service.write_record', side_effect=write):
            def create(generation=self.generation, acquired=True):
                return native.WindowsDaemonDrain(self.home, self.executable, self.kernel, 8, generation, acquired)
            self.assertTrue(create(acquired=False).observe_only)
            drain = create()
            self.assertFalse(drain.observe_only)
            drain.remember_shutdown()
            self.assertTrue(create().observe_only)
            self.assertFalse(create((self.generation[0], self.generation[1] + 1, self.generation[2])).observe_only)
            with self.assertRaisesRegex(OSError, 'already being observed'):
                create().remember_shutdown()

    def test_changed_process_home_or_environment_never_reaches_native_lock(self):
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        for environment in ({native.SHUTDOWN_FLAG: '1', 'CODEX_HOME': '/other'},
                            {native.SHUTDOWN_FLAG: '0', 'CODEX_HOME': str(self.home)}):
            with self.subTest(environment=environment), \
                    patch(read_daemon_environment.__module__ + '.read_daemon_environment',
                          return_value=({native.SHUTDOWN_FLAG: '1'}, self.generation)), \
                    patch.object(native, '_kernel', return_value=self.kernel), \
                    patch.object(native, '_owner_sid'), \
                    patch.object(native, '_identity', return_value=self.generation[1:]), \
                    patch.object(native, '_environment', return_value=environment), \
                    patch.object(native, '_operation_lock') as lock:
                with self.assertRaisesRegex(OSError, 'another home'):
                    with native.windows_daemon_drain(self.home, self.executable, self.generation):
                        self.fail('unverified process')
                lock.assert_not_called()
                self.kernel.OpenProcess.assert_called_with(native.QUERY | native.VM_READ | native.SYNCHRONIZE, False, 4321)
                self.kernel.CloseHandle.assert_called_with(self.kernel.OpenProcess.return_value)

    def test_native_websocket_pid_frame_and_lost_or_wrong_ack(self):
        for reply in (b'4321', b'4322', b' 4321', b'"4321"', b''):
            with self.subTest(reply=reply):
                channel = ControlChannel(reply)
                @contextmanager
                def connect(_):
                    yield channel
                drain = Mock(pid=4321, connect=connect)
                def checkpoint():
                    self.assertEqual(channel.frames, [])
                    drain.verify_peer.assert_called_once_with(channel)
                    drain.verify_generation.assert_called_once_with()
                    drain.remember_shutdown.assert_not_called()
                def persist():
                    self.assertEqual(channel.frames, [])
                drain.remember_shutdown.side_effect = persist
                self.assertEqual(request_windows_shutdown(drain, checkpoint), reply == b'4321')
                self.assertEqual(channel.frames, [b'4321'])
                drain.remember_shutdown.assert_called_once_with()

    def test_absence_requires_valid_record_and_positive_process_evidence(self):
        birth = self.generation[1]
        record = {'processStartTime': str(birth)}
        with patch.object(native, '_kernel', return_value=self.kernel), \
                patch.object(native, '_birth', return_value=birth) as queried, \
                patch.object(native.ctypes, 'get_last_error', create=True) as last:
            self.assertFalse(native.recorded_process_absent(4321, {'processStartTime': '1'}))
            self.assertFalse(native.recorded_process_absent(4321, record))
            self.kernel.WaitForSingleObject.return_value = native.WAIT_EXITED
            self.assertTrue(native.recorded_process_absent(4321, record))
            queried.return_value = birth + 1
            self.kernel.WaitForSingleObject.return_value = native.WAIT_RUNNING
            self.assertTrue(native.recorded_process_absent(4321, record))
            self.kernel.OpenProcess.return_value = 0
            last.return_value = 5  # ACCESS_DENIED is unknown.
            self.assertFalse(native.recorded_process_absent(4321, record))
            last.return_value = 87
            self.assertTrue(native.recorded_process_absent(4321, record))
