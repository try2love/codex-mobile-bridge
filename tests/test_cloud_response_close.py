"""End-of-response flow control uses synthetic channels, no account operations."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bridge.features.network.cloud_connector import Connector


class CloudResponseClose(unittest.IsolatedAsyncioTestCase):
    async def test_response_waits_for_peer_close_after_tail_credit(self):
        queue = asyncio.Queue()
        channel = AsyncMock()
        async def receive(_):
            return await queue.get()
        channel.receive.side_effect = receive
        task = asyncio.create_task(Connector.wait_response_close(channel))
        await queue.put({'kind': 'credit', 'count': 4})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(task.done(), 'response worker closed before the phone')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_invalid_or_excess_final_credit_is_rejected(self):
        for records in ([b'body'], [{'kind': 'request_end'}], [{'kind': 'credit', 'count': True}],
                        [{'kind': 'credit', 'count': 5}],
                        [{'kind': 'credit', 'count': 4}, {'kind': 'credit', 'count': 1}]):
            with self.subTest(records=records):
                channel = AsyncMock()
                channel.receive.side_effect = records
                with self.assertRaisesRegex(ValueError, 'invalid final response credit'):
                    await Connector.wait_response_close(channel)

    async def test_idle_peer_timeout_releases_only_the_completed_response(self):
        channel = AsyncMock()
        channel.receive.side_effect = asyncio.TimeoutError
        await Connector.wait_response_close(channel)
        channel.receive.assert_awaited_once_with(10)

    async def test_wait_has_total_deadline_even_if_peer_drips_credits(self):
        channel = AsyncMock()
        observed = []
        async def timeout(awaitable, seconds):
            observed.append(seconds)
            awaitable.close()
            raise asyncio.TimeoutError
        with patch('bridge.features.network.cloud_connector.asyncio.wait_for', timeout):
            await Connector.wait_response_close(channel)
        self.assertEqual(observed, [10])
        channel.receive.assert_not_awaited()

    async def test_forwarded_http_response_does_not_finish_before_receiver_close(self):
        body = b'{"sessions":[]}'
        queue, ended = asyncio.Queue(), asyncio.Event()
        async def chunks(_):
            yield body
        response = SimpleNamespace(status=200, headers={'Content-Type': 'application/json'},
                                   content_length=len(body),
                                   content=SimpleNamespace(iter_chunked=chunks))
        context = AsyncMock()
        context.__aenter__.return_value = response
        connector = object.__new__(Connector)
        connector.pin = Mock(return_value={'mobile_public_key': 'AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='})
        connector.sessions = {'binding': ('synthetic-token', {'csrf': 'synthetic-csrf'})}
        connector.server = SimpleNamespace(auth=SimpleNamespace(
            get=Mock(return_value=True), permitted=Mock(return_value=True), cookie_name='synthetic_cookie'))
        connector.local_origin = 'http://127.0.0.1:12345'
        connector.local = SimpleNamespace(request=Mock(return_value=context))
        controls, payloads = [], []
        async def send(value):
            controls.append(value)
            if value['kind'] == 'response_end':
                ended.set()
        async def receive(_):
            return await queue.get()
        channel = SimpleNamespace(binding='binding', mobile=b'\x01' + b'\0' * 31,
            send=send, receive=receive, cipher=SimpleNamespace(encrypt=lambda data: data),
            send_body=AsyncMock(side_effect=lambda data: payloads.append(data)))
        await queue.put({'kind': 'credit', 'count': 4})
        task = asyncio.create_task(connector.forward_http(channel,
            {'method': 'GET', 'path': '/api/sessions', 'content_type': 'application/json', 'headers': {}}, b''))
        await asyncio.wait_for(ended.wait(), 1)
        await queue.put({'kind': 'credit', 'count': 1})
        await asyncio.sleep(0)
        self.assertFalse(task.done(), 'completed HTTP response raced its receiver with outer Close')
        self.assertEqual([v['kind'] for v in controls], ['response_start', 'response_end'])
        self.assertEqual(payloads, [body])
        await queue.put({'kind': 'response_ack'})
        await asyncio.wait_for(task, 1)
