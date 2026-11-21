"""The production native stdout pump must not perform one read per byte."""
import io
import json
import threading
import unittest
from unittest.mock import Mock, patch

from bridge.clients.codex import desktop_host


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

    def test_oversized_reply_is_rejected_before_dispatch(self):
        router = Mock()
        with patch.object(desktop_host, 'MAX_LINE', 64), self.assertRaises(ValueError):
            desktop_host._forward_native_output(NativePipe(b' ' * 65 + b'\n'), router, threading.Event())
        router.from_native.assert_not_called()
