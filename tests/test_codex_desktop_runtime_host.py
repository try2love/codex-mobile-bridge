"""Adapter startup ownership and per-message transport failure boundaries."""
import base64
import hashlib
import io
import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex import desktop_runtime as runtime
from bridge.clients.codex import desktop_runtime_host as host
from bridge.clients.websocket import WebSocket

ROOT = Path(__file__).resolve().parents[1]


class AdapterLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = runtime.private_directory(runtime.directory(self.home))
        self.active = self.root / 'active.json'
        self.endpoint = self.home / 'native.sock'
        self.generation = str(uuid.uuid4())
        self.config = {'home': str(self.home), 'cli': '/fixture/launcher'}
        self.app = {'pid': 11, 'parent': 1, 'image': '/fixture/app'}
        self.adapter = {'pid': os.getpid(), 'parent': 11, 'image': '/fixture/adapter'}
        self.child = {'pid': 22, 'parent': os.getpid(), 'image': '/fixture/launcher'}
        self.native = Mock(pid=22, returncode=0)
        self.native.poll.return_value = None
        self.native.wait.side_effect = subprocess.TimeoutExpired('native', 25)
        self.proxy = Mock(stdout=io.BytesIO(), stdin=io.BytesIO())
        self.proxy.poll.return_value = 0

    def identity(self, pid):
        return dict({11: self.app, os.getpid(): self.adapter, 22: self.child}[pid])

    def spawn(self, argv, **kwargs):
        if '--sock' in argv:
            self.child['image'] = '/fixture/CodexCLI.app/codex'
            return self.proxy
        reserved = runtime._read(self.active)
        self.assertIsNone(reserved['runtime'])
        self.assertFalse(reserved['ready'])
        self.server = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.server.close)
        self.server.bind(str(self.endpoint))
        self.endpoint.chmod(0o600)
        return self.native

    def serve(self):
        with patch.object(host, 'socket_path', return_value=self.endpoint), \
             patch.object(host, 'process_identity', side_effect=self.identity), \
             patch.object(host.subprocess, 'Popen', side_effect=self.spawn), \
             patch.object(host, 'WebSocket', side_effect=ValueError('handshake rejected')):
            return host._serve(self.config, self.app, ['app-server'], self.endpoint,
                               {'id': 1, 'method': 'initialize'}, self.generation)

    def test_startup_failure_retains_child_before_handshake_and_never_kills(self):
        with self.assertRaisesRegex(ValueError, 'handshake rejected'):
            self.serve()
        record = runtime._read(self.active)
        self.assertFalse(record['ready'])
        self.assertEqual(record['runtime']['pid'], 22)
        self.assertEqual(self.endpoint.stat().st_mode & 0o777, 0o600)
        self.native.terminate.assert_called_once()
        self.native.kill.assert_not_called()
        # Exec before readiness does not make the still-running child replaceable.
        current = {**record['runtime'], 'image': '/fixture/CodexCLI.app/codex',
                   'parent': 1, 'birth': [1, 2], 'started': [3, 4], 'uid': 501, 'ruid': 501}
        record['runtime'].update({key: current[key] for key in ('birth', 'started', 'uid', 'ruid')})
        with patch.object(runtime, 'process_identity', return_value=current):
            with self.assertRaisesRegex(ValueError, '尚未退出'):
                runtime.require_inactive(record)

    def test_failed_start_is_cleaned_only_after_native_normal_exit(self):
        self.native.wait.side_effect = None
        self.native.terminate.side_effect = lambda: setattr(self.native.poll, 'return_value', 0)
        with self.assertRaisesRegex(ValueError, 'handshake rejected'):
            self.serve()
        self.assertFalse(self.active.exists())
        self.assertFalse(self.endpoint.exists())
        self.native.kill.assert_not_called()

    def test_foreign_socket_symlink_is_rejected_without_changing_target(self):
        target = self.home / 'target'
        target.write_text('original'); target.chmod(0o644)
        self.endpoint.symlink_to(target)
        record = {'generation': self.generation}
        runtime._write(self.active, json.dumps(record))
        with patch.object(host, 'socket_path', return_value=self.endpoint):
            with self.assertRaises(ValueError):
                host._private_socket(self.config, self.endpoint, self.generation, self.active, record)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_socket_uses_shared_verifier_without_mutating_link_or_target(self):
        record = {'generation': self.generation}
        runtime._write(self.active, json.dumps(record))
        with patch.object(host, 'socket_path', return_value=self.endpoint), \
             patch.object(host, 'verified_socket', return_value=(1, 42)) as verify, \
             patch.object(host.os, 'chmod') as chmod:
            host._private_socket(self.config, self.endpoint, self.generation, self.active, record)
        self.assertEqual(verify.call_count, 2)
        self.assertTrue(all(call.args == (self.endpoint,) for call in verify.call_args_list))
        chmod.assert_not_called()

    def test_socket_identity_swap_during_verification_is_rejected(self):
        record = {'generation': self.generation}
        runtime._write(self.active, json.dumps(record))
        with patch.object(host, 'socket_path', return_value=self.endpoint), \
             patch.object(host, 'verified_socket', side_effect=[(1, 42), (1, 43)]):
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                host._private_socket(self.config, self.endpoint, self.generation, self.active, record)

    def test_adapter_generation_swap_during_verification_is_rejected(self):
        record = {'generation': self.generation}
        runtime._write(self.active, json.dumps(record))
        def replaced(_):
            runtime._write(self.active, json.dumps({'generation': 'different'}))
            return (1, 42)
        with patch.object(host, 'socket_path', return_value=self.endpoint), \
             patch.object(host, 'verified_socket', side_effect=replaced) as verify:
            with self.assertRaisesRegex(ValueError, 'generation changed'):
                host._private_socket(self.config, self.endpoint, self.generation, self.active, record)
        verify.assert_called_once()

    def test_ready_is_published_only_after_native_initialize_with_exec_identity(self):
        initialized = threading.Event(); ended = threading.Event()
        transport = Mock()
        initialize = {'id': 1, 'method': 'initialize'}
        def closed():
            ended.wait(2)
            raise EOFError()
        def receive():
            self.assertFalse(runtime._read(self.active)['ready'])
            transport.receive.side_effect = closed
            return {'id': 1, 'result': {'codexHome': str(self.home)}}
        transport.receive.side_effect = receive
        def read_line(_):
            self.assertTrue(initialized.wait(2))
            return b''
        output = Mock()
        def written(text):
            if text.startswith('{'):
                record = runtime._read(self.active)
                self.assertTrue(record['ready'])
                self.assertEqual(record['runtime']['image'], '/fixture/CodexCLI.app/codex')
                initialized.set()
        output.write.side_effect = written
        def terminated():
            self.native.poll.return_value = 0
            ended.set()
        self.native.terminate.side_effect = terminated
        self.native.wait.side_effect = None
        with patch.object(host, 'socket_path', return_value=self.endpoint), \
             patch.object(host, 'process_identity', side_effect=self.identity), \
             patch.object(host.subprocess, 'Popen', side_effect=self.spawn), \
             patch.object(host, 'WebSocket', return_value=transport), \
             patch.object(host.sys, 'stdin', SimpleNamespace(buffer=SimpleNamespace(readline=read_line))), \
             patch.object(host.sys, 'stdout', output):
            self.assertEqual(host._serve(self.config, self.app, ['app-server'], self.endpoint,
                                         initialize, self.generation), 0)
        self.assertTrue(initialized.is_set())
        self.assertFalse(self.active.exists())
        transport.send.assert_called_once_with(initialize)


class TransportCapacityTests(unittest.TestCase):
    def websocket(self, limit=24, maximum=1024):
        key = base64.b64encode(b'k' * 16).decode()
        accept = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        response = (f'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: {accept}\r\n'
                    f'x-codex-websocket-max-unfragmented-message-bytes: {limit}\r\n\r\n')
        writer = io.BytesIO()
        with patch('bridge.clients.websocket.os.urandom', return_value=b'k' * 16):
            transport = WebSocket(io.BytesIO(response.encode()), writer,
                                  max_message_bytes=maximum, oversized=host.MessageTooLarge)
        writer.seek(0); writer.truncate()
        return transport, writer

    def frames(self, wire):
        stream = io.BytesIO(wire)
        result = []
        while header := stream.read(2):
            size = header[1] & 127
            if size == 126: size = struct.unpack('!H', stream.read(2))[0]
            if size == 127: size = struct.unpack('!Q', stream.read(8))[0]
            mask = stream.read(4)
            data = stream.read(size)
            result.append((header[0], bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))))
        return result

    def test_native_advertised_frame_cap_is_used_with_complete_json_message(self):
        transport, writer = self.websocket()
        message = {'id': 4, 'method': 'test', 'params': {'text': '附件' * 30}}
        transport.send(message)
        frames = self.frames(writer.getvalue())
        self.assertGreater(len(frames), 1)
        self.assertEqual(frames[0][0], 1)
        self.assertEqual(frames[-1][0], 0x80)
        self.assertTrue(all(len(data) <= 24 for _, data in frames))
        self.assertEqual(json.loads(b''.join(data for _, data in frames)), message)

    def test_oversized_request_returns_error_before_any_write_and_next_request_survives(self):
        transport, writer = self.websocket(maximum=100)
        stream = io.BytesIO((json.dumps({'id': 'large', 'method': 'turn/start', 'params': 'a' * 101}) +
                             '\n' + json.dumps({'id': 'next', 'method': 'thread/list'}) + '\n').encode())
        replies = []
        host._forward_input(stream, transport, replies.append, threading.Event())
        self.assertEqual(replies[0]['id'], 'large')
        self.assertEqual(replies[0]['error']['code'], -32600)
        self.assertEqual(json.loads(b''.join(data for _, data in self.frames(writer.getvalue())))['id'], 'next')

    def test_overlong_line_is_drained_once_and_does_not_consume_next_message(self):
        transport = Mock()
        stream = io.BytesIO(b'{"long":"' + b'x' * 256 + b'"}\n{"id":2,"method":"ping"}\n')
        replies = []
        with patch.object(host, 'MAX_LINE', 64):
            host._forward_input(stream, transport, replies.append, threading.Event())
        self.assertEqual(len(replies), 1)
        self.assertIsNone(replies[0]['id'])
        transport.send.assert_called_once_with({'id': 2, 'method': 'ping'})

    def test_malformed_json_is_recoverable_without_transport_write(self):
        transport = Mock(); replies = []
        host._forward_input(io.BytesIO(b'bad\n[]\n{"id":2,"method":"ping"}\n'), transport,
                            replies.append, threading.Event())
        self.assertEqual([reply['error']['code'] for reply in replies], [-32700, -32600])
        transport.send.assert_called_once_with({'id': 2, 'method': 'ping'})


if __name__ == '__main__':
    unittest.main()
