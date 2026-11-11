import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.local_rpc import LocalService, LocalRequestNotSent, packet, unpack, read_packet, write_packet


class LocalRpcTests(unittest.TestCase):
    def test_windows_pre_dispatch_and_explicit_peer_rejection_remain_retryable(self):
        from bridge.platforms import windows
        service = LocalService('.tmp', 'fixture')
        record = {'name': 'fixture', 'profile': str(service.profile), 'owner': {}, 'token': '0' * 64,
                  'endpoint': r'\\.\pipe\cmb-rpc-fixture-' + '0' * 32}
        native = SimpleNamespace(read_record=Mock(side_effect=PermissionError('private record')),
            local_connect=Mock(side_effect=lambda *args, **kwargs: nullcontext(Mock())), require_peer=Mock(), byte_stream=Mock())
        with patch.object(windows, 'local_service', native, create=True), \
             patch('bridge.clients.local_rpc.os', SimpleNamespace(name='nt', path=os.path)):
            with self.assertRaises(LocalRequestNotSent): service._exchange({})
            native.local_connect.assert_not_called()
            native.read_record.side_effect = None; native.read_record.return_value = record
            pipe = Mock(send=Mock(side_effect=OSError('partial write')))
            native.byte_stream.return_value = pipe
            with self.assertRaises(OSError): service._exchange({})
            pipe.send.side_effect = lambda data, **kwargs: len(data)
            response = io.BytesIO(packet({'ok': False, 'notSent': True, 'error': 'generation changed'}))
            pipe.recv.side_effect = lambda size, **kwargs: response.read(size)
            with self.assertRaises(LocalRequestNotSent): service._exchange({})

    def test_fragmented_shared_frames_and_size_boundaries(self):
        value = {'token': 'fixture', 'request': {'text': '中文消息'}}
        data = packet(value);stream = io.BytesIO(data)
        self.assertEqual(read_packet(lambda count: stream.read(min(count, 3))), value)
        chunks = []
        write_packet(lambda chunk: chunks.append(chunk[:3]) or min(3, len(chunk)), value)
        self.assertEqual(b''.join(chunks), data)
        for invalid in (b'\0\0\0\0', b'\x7f\xff\xff\xff', data[:-1], packet([])):
            with self.assertRaises((ValueError, EOFError)):unpack(invalid)

    def test_invalid_record_is_unavailable_not_a_global_client_failure(self):
        if os.name == 'nt':self.skipTest('Unix record fixture')
        with tempfile.TemporaryDirectory(dir='.tmp') as name:
            service = LocalService(name, 'zcode-official');service.record.write_text('[]');service.record.chmod(0o600)
            self.assertFalse(service.status()['available'])
            service.record.write_text('{}');self.assertFalse(service.status()['configured'])

    def test_names_cannot_escape_the_service_directory(self):
        for name in ('../profile', 'UPPER', 'control/pipe', '本机'):
            with self.assertRaises(ValueError):LocalService('.tmp', name)


@unittest.skipUnless(os.name == 'nt', 'Windows ACL and named-pipe fixture')
class WindowsLocalRpcTests(unittest.TestCase):
    def test_private_record_rejects_inherited_permissions_and_hard_links(self):
        from bridge.platforms.windows import local_service as native
        with tempfile.TemporaryDirectory(dir='.tmp') as name:
            record = Path(name)/'.local-services'/'fixture'/'ipc.json'
            native.write_record(record, {'fixture': True});self.assertEqual(native.read_record(record), {'fixture': True})
            copied = Path(name)/'linked.json';os.link(record, copied)
            with self.assertRaises(PermissionError):native.read_record(record)
            copied.unlink();record.unlink();record.write_text(json.dumps({'fixture': True}))
            with self.assertRaises(PermissionError):native.read_record(record)

    def test_private_pipe_keeps_os_server_pid_and_same_session_boundary(self):
        import threading
        import secrets
        from bridge.platforms.windows import local_service as native
        from bridge.platforms.windows import zcode_pipe
        endpoint = r'\\.\pipe\cmb-rpc-test-' + secrets.token_hex(16)
        listener = native.listener(endpoint);received = []
        def accept():
            with listener.accept() as conn:
                received.append(native.peer_pid(conn, server=True))
                pipe = native.byte_stream(conn)
                value = read_packet(lambda size: pipe.recv(size, timeout=3))
                write_packet(lambda data: pipe.send(data, timeout=3), value)
        thread = threading.Thread(target=accept);thread.start()
        try:
            with native.local_connect(endpoint) as conn:
                self.assertEqual(native.peer_pid(conn, server=False), os.getpid())
                if zcode_pipe._native().session:
                    native.require_peer(conn, server=False, expected=native.identity(os.getpid()))
                else:
                    with self.assertRaises(PermissionError):native.require_peer(conn, server=False)
                pipe = native.byte_stream(conn);value = {'native': 'fixture'}
                write_packet(lambda data: pipe.send(data, timeout=3), value)
                self.assertEqual(read_packet(lambda size: pipe.recv(size, timeout=3)), value)
            thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(received, [os.getpid()])
        finally:listener.close()


if __name__ == '__main__':unittest.main()
