"""The production native stdout pump must not perform one read per byte."""
import io
import json
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock

from bridge.clients.codex import desktop_host
from bridge.clients.codex.desktop_stdio import DesktopStdio
from bridge.clients.codex.ipc_json import StoredString, iter_wire_json


class NativePipe(io.RawIOBase):
    def __init__(self, data, fragment=None):
        self.data, self.position, self.reads, self.fragment = data, 0, 0, fragment

    def readable(self):
        return True

    def readinto(self, target):
        self.reads += 1
        count = min(len(target), len(self.data) - self.position)
        if self.fragment is not None:
            count = min(count, self.fragment)
        target[:count] = self.data[self.position:self.position + count]
        self.position += count
        return count


class HostedDesktopOutputTests(unittest.TestCase):
    def test_large_native_reply_is_read_in_chunks_before_the_next_event(self):
        packets = [{'id': 'history', 'result': {'text': '历史与工具输出' * 16384}},
                   {'method': 'turn/completed', 'params': {'threadId': 'original'}}]
        data = b''.join(json.dumps(packet, ensure_ascii=False).encode() + b'\n' for packet in packets)
        stream, router = NativePipe(data), Mock()
        desktop_host._forward_native_output(stream, router, threading.Event())
        self.assertEqual([call.args[0] for call in router.from_native.call_args_list], packets)
        self.assertLess(stream.reads, len(data) // 128)

    def test_fragmented_utf8_and_final_line_without_newline_are_preserved(self):
        packets = [{'method': 'delta', 'params': {'text': '中文'}}, {'id': 2, 'result': {}}]
        data = b'\r\n'.join(json.dumps(packet, ensure_ascii=False).encode() for packet in packets)
        router = Mock()
        desktop_host._forward_native_output(NativePipe(data, fragment=3), router, threading.Event())
        self.assertEqual([call.args[0] for call in router.from_native.call_args_list], packets)

    def test_malformed_reply_is_rejected_before_dispatch(self):
        router = Mock()
        with self.assertRaises(ValueError):
            desktop_host._forward_native_output(NativePipe(b'{"id": 1} extra\n'), router, threading.Event())
        router.from_native.assert_not_called()

    def test_one_large_history_item_and_next_control_reply_share_the_native_pipe(self):
        requests = queue.Queue()
        written = threading.Event()

        def write(wire):
            requests.put(json.loads(wire))
            written.set()

        router = DesktopStdio(write, Mock(), Mock(),
                              control_methods=desktop_host.CONTROL_METHODS)
        router.ready = True
        errors = []
        parts = []
        body_length = 65 * 1024 * 1024

        def pump():
            history, send = requests.get(timeout=5), requests.get(timeout=5)
            prefix = ('{"id":' + json.dumps(history['id']) +
                      ',"result":{"data":[{"turnId":"old","item":{"type":"agentMessage","text":"').encode()
            ending = b'\\nold suffix'
            suffix = b'"}}]}}\n'
            with tempfile.TemporaryFile() as native:
                native.write(prefix)
                block = b'x' * 1024 * 1024
                for _ in range(body_length // len(block)):
                    native.write(block)
                native.write(ending + suffix)
                native.write(json.dumps({'id': send['id'], 'result': {'turn': {'id': 'still-live'}}}).encode() + b'\n')
                native.seek(0)
                desktop_host._forward_native_output(native, router, threading.Event())

        def request(method):
            try:
                parts.append(router.request_packet(method, {'threadId': 'original'}, timeout=20))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=pump, daemon=True)]
        for method in ('thread/items/list', 'turn/start'):
            workers.append(threading.Thread(target=request, args=(method,), daemon=True))
        workers[0].start()
        workers[1].start()
        # Ensure the pump binds the large native result to the history RPC.
        self.assertTrue(written.wait(5))
        workers[2].start()
        for worker in workers:
            worker.join(25)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        history = next(result['result'] for result in parts if 'data' in result['result'])
        send = next(result['result'] for result in parts if 'turn' in result['result'])
        text = history['data'][0]['item']['text']
        self.assertEqual(send['turn']['id'], 'still-live')
        self.assertIsInstance(text, StoredString)
        self.assertEqual(text.chars, body_length + len('\nold suffix'))
        self.assertEqual(text.slice(body_length, text.chars), '\nold suffix')
        self.assertEqual(sum(map(len, iter_wire_json(text))), body_length + len(b'"\\nold suffix"'))
