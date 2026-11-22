"""Large desktop histories must not disconnect unrelated followed threads."""
import base64
import hashlib
import json
import os
import sqlite3
import time
import uuid
import zlib
import struct
import tempfile
import threading
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

from bridge.app.service import LiveSession
from bridge.clients.codex.app_server import _WebSocket, AppServerUnsupported
from bridge.clients.codex.ipc import DesktopIPC, IPCError
from bridge.clients.codex.shared_server import SharedServer
from bridge.clients.codex.ipc_json import FrameReader, StoredString
import test_bridge
from test_bridge import THREAD, state


class LargeSnapshotTests(unittest.TestCase):
    setUp = test_bridge.IntegrationTests.setUp
    tearDown = test_bridge.IntegrationTests.tearDown

    def test_large_snapshot_and_other_thread_updates_share_connection(self):
        session_a = self.bridge.session(THREAD)
        self.assertTrue(session_a.connected)
        session_b = LiveSession('00000000-0000-0000-0000-000000000065')
        session_b.discovering = True
        self.bridge.live[session_b.id] = session_b
        large = state()
        large['id'] = session_b.id
        large['turns'] = [{'turnId': 'large-turn', 'status': 'completed', 'items': [
            {'id': 'large-item', 'type': 'agentMessage', 'text': 'x' * (65 * 1024 * 1024) + '\n中文🚀'}]}]
        message = {'type': 'broadcast', 'sourceClientId': 'owner',
                   'method': 'thread-stream-state-changed', 'version': 11,
                   'params': {'hostId': 'local', 'conversationId': session_b.id,
                              'change': {'type': 'snapshot', 'revision': 1, 'conversationState': large}}}
        client_id = self.bridge.ipc.client_id
        errors = []

        def publish():
            try:
                self.fixture.send(message)
            except OSError as exc:
                errors.append(type(exc).__name__)

        worker = threading.Thread(target=publish, daemon=True)
        worker.start()
        worker.join(15)
        with session_b.condition:
            session_b.condition.wait_for(lambda: session_b.connected or not self.bridge.ipc.socket, timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(session_b.connected)
        self.assertTrue(session_a.connected)
        self.assertEqual(self.bridge.ipc.client_id, client_id)
        body = session_b.state['turns'][0]['items'][0]['text']
        self.assertIsInstance(body, StoredString)
        self.assertEqual(body.chars, len(large['turns'][0]['items'][0]['text']))
        self.assertEqual(body.slice(body.chars - 4, body.chars), '\n中文🚀')
        session_b.timeline.update(session_b.view())
        page = session_b.timeline.page()
        self.assertLess(len(json.dumps(page).encode()), 32 * 1024)
        self.assertTrue(page['rows'][0]['truncated'])
        # Both followers still receive subsequent patches on the same stream.
        for session in (session_a, session_b):
            message['params'] = {'hostId': 'local', 'conversationId': session.id, 'change': {
                'type': 'patches', 'baseRevision': 1, 'revision': 2,
                'patches': [{'op': 'replace', 'path': ['title'], 'value': 'Updated'}]}}
            self.fixture.send(message)
            with session.condition:
                self.assertTrue(session.condition.wait_for(lambda: session.revision == 2, timeout=3))
            self.assertEqual(session.state['title'], 'Updated')
        self.assertEqual([r['method'] for r in self.fixture.requests], ['initialize'])


    def test_over_256_mib_snapshot_keeps_two_native_routes_and_old_bodies(self):
        session_a = self.bridge.session(THREAD)
        second = str(uuid.uuid4())
        rollout = self.root / 'sessions/second.jsonl'
        rollout.touch()
        db = sqlite3.connect(self.root / 'state_5.sqlite')
        try:
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                       (second, 'Second', '/workspace', 2, 0, 'Codex Desktop', 'vscode', str(rollout)))
            db.commit()
        finally:
            db.close()
        session_b = LiveSession(second)
        session_b.discovering = True
        self.bridge.live[second] = session_b
        owner_snapshot = {**state(), 'id': second, 'turns': [{
            'turnId': 'history', 'status': 'completed', 'items': []}]}
        message = {'type': 'broadcast', 'sourceClientId': 'owner',
                   'method': 'thread-stream-state-changed', 'version': 13,
                   'params': {'hostId': 'local', 'conversationId': second,
                              'change': {'type': 'snapshot', 'revision': 1, 'conversationState': owner_snapshot}}}
        old_png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR7kAAAAASUVORK5CYII=')
        def png(index):
            comment = b'tEXtComment\x00' + b'fixture' * 192 * 1024 + str(index).zfill(2).encode('ascii')
            chunk = struct.pack('>I', len(comment) - 4) + comment + struct.pack('>I', zlib.crc32(comment))
            return old_png[:-12] + chunk + old_png[-12:]

        output = 'ordinary process text ' * 2000 + 'historical ending 🚀'
        images, steps = 36, 6600

        def items():
            for i in range(images):
                encoded_png = base64.b64encode(png(i)).decode()
                if i == 0:
                    yield {'type': 'userMessage', 'id': 'prompt', 'content': [
                        {'type': 'inputText', 'text': 'Old question'},
                        {'type': 'inputImage', 'imageUrl': 'data:image/png;base64,' + encoded_png}]}
                else:
                    yield {'type': 'mcpToolCall', 'id': 'shot-' + str(i), 'tool': 'capture',
                           'result': {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': encoded_png}]}}
            for i in range(steps):
                yield {'type': 'commandExecution', 'id': 'command-' + str(i),
                       'command': 'fixture command', 'aggregatedOutput': output}

        prefix, suffix = json.dumps(message).split('"items": []')
        prefix, suffix = (prefix + '"items": [').encode(), (']' + suffix).encode()
        length = len(prefix) + len(suffix) + images + steps - 1
        length += sum(len(json.dumps(item).encode()) for item in items())
        self.assertGreater(steps * len(output.encode()), 256 * 1024 * 1024)
        self.assertGreater(length, 256 * 1024 * 1024)
        client_id = self.bridge.ipc.client_id
        if os.environ.get('IPC_SNAPSHOT_METRICS') and os.name != 'nt':
            import resource
            rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.monotonic()
        errors = []

        def publish():
            try:
                with self.fixture.write_lock:
                    stream = self.fixture.client
                    stream.sendall(struct.pack('<I', length) + prefix)
                    for i, item in enumerate(items()):
                        if i:
                            stream.sendall(b',')
                        stream.sendall(json.dumps(item).encode())
                    stream.sendall(suffix)
            except (OSError, ValueError) as exc:
                errors.append(exc)

        worker = threading.Thread(target=publish, daemon=True)
        worker.start()
        with session_b.condition:
            self.assertTrue(session_b.condition.wait_for(lambda: session_b.connected, timeout=120))
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.bridge.ipc.client_id, client_id)
        elapsed = time.monotonic() - started

        for session in (session_a, session_b):
            receipt = self.bridge.send(session.id, 'New question', str(uuid.uuid4()))
            self.assertEqual(receipt['status'], 'accepted')
            self.fixture.send({**message, 'params': {
                'hostId': 'local', 'conversationId': session.id, 'change': {
                    'type': 'patches', 'baseRevision': 1, 'revision': 2, 'patches': [{
                        'op': 'add', 'path': ['turns', '-'], 'value': {
                            'turnId': 'new-turn', 'status': 'completed', 'items': [{
                                'type': 'agentMessage', 'id': 'answer-' + session.id, 'text': 'New answer'}]}}]}}})
            with session.condition:
                self.assertTrue(session.condition.wait_for(lambda: session.revision == 2, timeout=5))
            page = self.bridge.timeline_read(session.id)
            self.assertTrue(any(row.get('text') == 'New answer' for row in page['rows']))

        calls = [r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn']
        self.assertEqual([(r['targetClientId'], r['params']['conversationId']) for r in calls],
                         [('owner', session_a.id), ('owner', session_b.id)])
        turn = session_b.state['turns'][0]
        self.assertEqual(len(turn['items']), images + steps)
        body = turn['items'][images]['aggregatedOutput']
        self.assertIsInstance(body, StoredString)
        self.assertEqual(body.slice(body.chars - 19, body.chars), 'historical ending 🚀')
        session_b.sync_timeline.update(session_b.view(process_bodies=False), conversation=True)
        command = next(row for row in session_b.sync_timeline.rows if row['kind'] == 'commandExecution')
        command_cursor = session_b.sync_timeline.cursor(command['key'])
        session_b.sync_timeline.detail(command_cursor)
        chunk = session_b.sync_timeline.detail(command_cursor, len('fixture command\n\n') + body.chars - 19,
                                              command['version'])
        self.assertEqual(chunk['text'], 'historical ending 🚀')
        self.assertIsNone(chunk['next'])
        shot = next(row for row in session_b.sync_timeline.rows if row.get('toolName') == 'capture')
        user = next(row for row in session_b.sync_timeline.rows if row['role'] == 'user')
        self.assertEqual(user['attachments'][0]['desktopId'], hashlib.sha256(png(0)).hexdigest())
        self.assertNotIn(base64.b64encode(png(0))[:256].decode(), json.dumps(user))
        cursor = session_b.sync_timeline.cursor(shot['key'])
        detail = session_b.sync_timeline.detail(cursor)
        self.assertEqual(detail['attachments'][0]['desktopId'], hashlib.sha256(png(1)).hexdigest())
        evidence = session_b.sync_timeline.detail_image_evidence[shot['key']][0]
        self.assertEqual(session_b.tool_image_cache.preview(evidence), png(1))
        if os.environ.get('IPC_SNAPSHOT_METRICS') and os.name != 'nt':
            rss_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            print(f'IPC snapshot {length} bytes, {images} images, {steps} process steps, {elapsed:.3f}s, peak RSS {rss_before}->{rss_peak}')


class FrameBudgetTests(unittest.TestCase):
    def test_receive_uses_bounded_chunks_and_accepts_fragmented_header(self):
        stream = Mock()
        stream.recv.side_effect = [b'\x01', b'\x00\x00', b'\x00']
        self.assertEqual(DesktopIPC._exact(stream, 4), b'\x01\x00\x00\x00')
        sizes = []

        def recv(size):
            sizes.append(size)
            return b'x' * min(size, 256 * 1024)

        stream.recv.side_effect = recv
        self.assertEqual(len(DesktopIPC._exact(stream, 3 * 1024 * 1024 + 1)), 3 * 1024 * 1024 + 1)
        self.assertLessEqual(max(sizes), 1024 * 1024)

    def test_invalid_frame_rejected_before_body_allocation(self):
        stream = Mock()
        stream.recv.return_value = struct.pack('<I', 0)
        disconnected = Mock()
        ipc = DesktopIPC('unused', on_disconnect=disconnected)
        ipc.socket = stream
        with self.assertLogs('bridge.clients.codex.ipc', level='WARNING') as logs:
            ipc._read_loop(stream)
        stream.recv.assert_called_once_with(4)
        disconnected.assert_called_once_with()
        self.assertIn('bytes=0', logs.output[0])

    def test_truncated_maximum_wire_frame_only_reads_a_bounded_chunk(self):
        stream = Mock()
        stream.recv.side_effect = [struct.pack('<I', DesktopIPC.MAX_FRAME), b'{"type":', b'']
        disconnected = Mock()
        ipc = DesktopIPC('unused', on_disconnect=disconnected)
        ipc.socket = stream
        ipc._read_loop(stream)
        self.assertEqual([call.args[0] for call in stream.recv.call_args_list],
                         [4, DesktopIPC.READ_CHUNK, DesktopIPC.READ_CHUNK])
        disconnected.assert_called_once_with()

    def test_spooled_patch_leaves_keep_offsets_versions_and_control_fields(self):
        for order in ('before', 'after'):
            with self.subTest(order=order):
                text = 'same prefix ' * 1200 + '🚀\n'
                target = ['turns', 0, 'items', 0, 'text']
                payload = {'path': target, 'value': text} if order == 'before' else {'value': text, 'path': target}
                source = {'params': {'change': {'patches': [payload, {'value': text, 'path': ['title']}]}}}
                packet = json.dumps(source, ensure_ascii=False).encode()
                stream = Mock()
                stream.recv.side_effect = (packet[i:i+17] for i in range(0, len(packet), 17))
                result = FrameReader(stream, len(packet), 17).read()['params']['change']['patches']
                self.assertEqual(result[0]['path'], target)
                self.assertIsInstance(result[0]['value'], StoredString)
                self.assertEqual(result[0]['value'].slice(1331, 14402), text[1331:14402])
                self.assertEqual(result[1]['value'], text)
                self.assertIs(type(result[1]['value']), str)

    def test_outbound_budget_does_not_expand_with_snapshot_budget(self):
        self.assertEqual(DesktopIPC.MAX_REQUEST, 64 * 1024 * 1024)
        ipc = DesktopIPC('unused')
        ipc.socket = Mock()
        with patch.object(ipc, 'MAX_REQUEST', 10):
            with self.assertRaisesRegex(IPCError, '消息过大'):
                ipc._send({'text': 'not a small request'})
        ipc.socket.sendall.assert_not_called()


class NativeWebSocketTests(unittest.TestCase):
    def test_large_remote_item_and_next_ack_for_ssh_and_wsl(self):
        prefix = b'{"id":1,"result":{"data":[{"id":"turn","items":[{"type":"agentMessage","text":"'
        suffix = b'old ending"}]}]}}'
        one_mib = b'x' * 1024 * 1024
        max_chunk = 16 * len(one_mib)
        ack = b'{"id":2,"result":{"turn":{"id":"sent"}}}'

        with tempfile.TemporaryFile() as wire:
            wire.write(b'\x01\x7f' + struct.pack('!Q', len(prefix) + max_chunk))
            wire.write(prefix)
            for _ in range(16):
                wire.write(one_mib)
            wire.write(b'\x89\x02hi')
            for _ in range(3):
                wire.write(b'\x00\x7f' + struct.pack('!Q', max_chunk))
                for _ in range(16):
                    wire.write(one_mib)
            wire.write(b'\x80\x7f' + struct.pack('!Q', len(one_mib) + len(suffix)))
            wire.write(one_mib + suffix)
            wire.write(bytes((0x81, len(ack))) + ack)

            def verify(history, next_ack):
                body = history['result']['data'][0]['items'][0]['text']
                self.assertIsInstance(body, StoredString)
                self.assertEqual(body.chars, 65 * len(one_mib) + len('old ending'))
                self.assertEqual(body.slice(65 * len(one_mib), body.chars), 'old ending')
                self.assertEqual(next_ack['result']['turn']['id'], 'sent')

            wire.seek(0)
            ssh = _WebSocket.__new__(_WebSocket)
            ssh.reader, ssh.unsupported = wire, AppServerUnsupported
            ssh._control = Mock()
            verify(ssh.receive(), ssh.receive())
            ssh._control.assert_called_once_with(10, bytearray(b'hi'))

            wire.seek(0)
            stream = Mock()
            stream.recv.side_effect = wire.read
            wsl = SharedServer(stream)
            wsl.pending.update({1: Future(), 2: Future()})
            wsl._read()
            verify(wsl.pending[1].result(), wsl.pending[2].result())
            stream.sendall.assert_called_once()


if __name__ == '__main__':
    unittest.main()
