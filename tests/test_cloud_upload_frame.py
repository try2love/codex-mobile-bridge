"""A full-sized encrypted upload frame must survive aiohttp's exclusive limit."""
import asyncio
import unittest
import uuid
import aiohttp
from aiohttp import web
from bridge.features.network.cloud_crypto import encode_frame, decode_frame, MAX_CHUNK

class CloudUploadFrame(unittest.IsolatedAsyncioTestCase):
    async def test_maximum_record_and_oversized_record_boundary(self):
        binding,channel=str(uuid.uuid4()),str(uuid.uuid4())
        record=encode_frame(2,binding,channel,b'X'*(MAX_CHUNK+16))
        async def socket(request):
            ws=web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_bytes(record)
            await ws.close()
            return ws
        app=web.Application();app.router.add_get('/',socket)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        try:
            port=site._server.sockets[0].getsockname()[1]
            async with aiohttp.ClientSession() as client:
                async with client.ws_connect(f'http://127.0.0.1:{port}/',max_msg_size=32819) as ws:
                    result=await asyncio.wait_for(ws.receive(),3)
                    self.assertEqual(result.type,aiohttp.WSMsgType.BINARY)
                    self.assertEqual(decode_frame(result.data)[3],b'X'*(MAX_CHUNK+16))
            with self.assertRaises(ValueError):decode_frame(record+b'X')
        finally:await runner.cleanup()
