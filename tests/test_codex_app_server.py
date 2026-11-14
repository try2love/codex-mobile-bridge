import json
import base64
import hashlib
import os
import queue
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from bridge.clients.codex.app_server import (
    AppServerReadTimeout, AppServerUnknownResult, AppServerUnsupported, CodexAppServer)

ROOT = Path(__file__).resolve().parents[1]


class FixtureProcess:
    def __init__(self, argv, **kwargs):
        self.argv = argv
        request_read, request_write = os.pipe()
        response_read, response_write = os.pipe()
        self.stdin = os.fdopen(request_write, 'wb')
        self.stdout = os.fdopen(response_read, 'rb')
        self.requests = os.fdopen(request_read, 'rb')
        self.responses = os.fdopen(response_write, 'wb')
        self.seen = []
        self.handshake = threading.Event()
        self.ended = False
        self.drop = set()
        self.errors = {}
        self.results = {}
        self.worker = threading.Thread(target=self.serve, daemon=True)
        self.worker.start()

    def send(self, value):
        self.handshake.wait(2)
        data = json.dumps(value).encode()
        header = bytes((0x81, len(data))) if len(data) < 126 else b'\x81\x7e' + struct.pack('!H', len(data))
        self.responses.write(header + data)
        self.responses.flush()

    def serve(self):
        header = bytearray()
        while not header.endswith(b'\r\n\r\n'):
            byte = self.requests.read(1)
            if not byte:
                return
            header.extend(byte)
        key = next(line.split(':', 1)[1].strip() for line in header.decode().split('\r\n')
                   if line.lower().startswith('sec-websocket-key:'))
        accept = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.responses.write(('HTTP/1.1 101 Switching Protocols\r\n'
                              'connection: Upgrade\r\nupgrade: websocket\r\n'
                              f'sec-websocket-accept: {accept}\r\n\r\n').encode())
        self.responses.flush()
        self.handshake.set()
        while True:
            head = self.requests.read(2)
            if len(head) != 2:
                break
            length = head[1] & 127
            if length == 126:
                length = struct.unpack('!H', self.requests.read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self.requests.read(8))[0]
            mask = self.requests.read(4)
            payload = self.requests.read(length)
            message = json.loads(bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))
            self.seen.append(message)
            if 'id' not in message or 'method' not in message:
                continue
            method = message['method']
            if method in self.drop:
                continue
            if method in self.errors:
                self.send({'id': message['id'], 'error': self.errors[method]})
                continue
            if method == 'initialize':
                result = {'codexHome': '/fixture/.codex', 'platformFamily': 'unix',
                          'platformOs': 'macos', 'userAgent': 'test'}
            elif method == 'thread/loaded/list':
                cursor = message['params']['cursor']
                result = {'data': ['thread-a'] if cursor is None else ['thread-b'],
                          'nextCursor': 'page-2' if cursor is None else None}
            else:
                result = {'ok': True}
            result = self.results.get(method, result)
            self.send({'id': message['id'], 'result': result(message['params']) if callable(result) else result})
        self.ended = True
        self.responses.close()

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return 0


class AppServerTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.socket = Path(self.temp.name) / 'existing.sock'
        self.socket.touch()
        self.processes = []
        self.events = []
        self.server_requests = []

        def factory(*args, **kwargs):
            process = FixtureProcess(*args, **kwargs)
            self.processes.append(process)
            return process

        self.client = CodexAppServer(self.socket, on_event=self.events.append,
                                     on_server_request=self.server_requests.append,
                                     process_factory=factory)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_attaches_existing_socket_and_lists_loaded_threads(self):
        self.assertEqual(self.client.loaded_threads(), ['thread-a', 'thread-b'])
        process = self.processes[0]
        self.assertEqual(process.argv[-4:], ['app-server', 'proxy', '--sock', str(self.socket)])
        self.assertEqual([m['method'] for m in process.seen[:3]],
                         ['initialize', 'initialized', 'thread/loaded/list'])
        self.assertEqual(self.client.request('thread/read', {'threadId': 'thread-a'},
                                             replay_safe=True), {'ok': True})

    def test_events_and_server_requests_are_forwarded_without_auto_decision(self):
        self.client.connect()
        process = self.processes[0]
        process.send({'method': 'turn/started', 'params': {'threadId': 'thread-a'}})
        process.send({'id': 71, 'method': 'item/commandExecution/requestApproval',
                      'params': {'threadId': 'thread-a'}})
        # A round trip ensures preceding messages have been dispatched.
        self.client.request('thread/read', {'threadId': 'thread-a'}, replay_safe=True)
        self.assertEqual(self.events[0]['method'], 'turn/started')
        self.assertEqual(self.server_requests[0]['id'], 71)
        self.assertFalse(any(m.get('id') == 71 for m in process.seen))
        self.client.respond(71, {'decision': 'decline'})
        self.client.request('thread/read', {'threadId': 'thread-a'}, replay_safe=True)
        self.assertEqual(next(m['result'] for m in process.seen if m.get('id') == 71),
                         {'decision': 'decline'})

    def test_write_timeout_has_unknown_result_and_is_not_retried(self):
        self.client.connect()
        process = self.processes[0]
        process.drop.add('turn/start')
        with self.assertRaises(AppServerUnknownResult):
            self.client.request('turn/start', {'threadId': 'thread-a'}, timeout=.02)
        self.assertEqual(len([m for m in process.seen if m.get('method') == 'turn/start']), 1)

    def test_read_timeout_does_not_replace_connection_or_replay(self):
        self.client.connect()
        process = self.processes[0]
        process.drop.add('thread/read')
        with self.assertRaises(AppServerReadTimeout):
            self.client.request('thread/read', {'threadId': 'thread-a'}, timeout=.02, replay_safe=True)
        self.assertIs(self.client.process, process)
        self.assertEqual([m['method'] for m in process.seen].count('thread/read'), 1)

    def test_delete_maps_only_known_native_rejection_without_replay(self):
        from bridge.features.sessions.create import CreationError, DeleteRejected
        self.client.connect()
        process = self.processes[0]
        process.errors['thread/delete'] = {'code': -32600, 'message': 'thread thread-a already has an active writer'}
        with self.assertRaisesRegex(DeleteRejected, '尚未删除'):
            self.client.request('thread/delete', {'threadId': 'thread-a'})
        process.errors['thread/delete'] = {'code': -32600, 'message': 'cleanup failed'}
        with self.assertRaises(CreationError) as caught:
            self.client.request('thread/delete', {'threadId': 'thread-a'})
        self.assertNotIsInstance(caught.exception, DeleteRejected)
        self.assertEqual(len([m for m in process.seen if m.get('method') == 'thread/delete']), 2)

    def test_missing_socket_never_launches_proxy(self):
        self.socket.unlink()
        with self.assertRaises(Exception):
            self.client.connect()
        self.assertEqual(self.processes, [])

    def test_old_reader_does_not_read_or_dispatch_from_new_connection(self):
        class QueuedTransport:
            def __init__(self):
                self.messages = queue.Queue()

            def receive(self):
                return self.messages.get(timeout=2)

        old_process, new_process = object(), object()
        old_transport, new_transport = QueuedTransport(), QueuedTransport()
        self.client.process = old_process
        self.client.transport = old_transport
        reader = threading.Thread(target=self.client._read_loop,
                                  args=(old_process, old_transport), daemon=True)
        reader.start()
        self.client.process = new_process
        self.client.transport = new_transport
        old_transport.messages.put({'method': 'turn/started', 'params': {'threadId': 'old'}})
        new_transport.messages.put({'method': 'turn/started', 'params': {'threadId': 'new'}})
        reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertEqual(self.events, [])
        self.assertEqual(new_transport.receive()['params']['threadId'], 'new')
        self.client.process = None
        self.client.transport = None


if __name__ == '__main__':
    unittest.main()
