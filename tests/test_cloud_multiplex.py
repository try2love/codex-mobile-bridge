import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace

from bridge.features.network.cloud_session import Session


class CloudMultiplex(unittest.IsolatedAsyncioTestCase):
    async def test_completed_responses_awaiting_late_ack_do_not_reject_next_batch(self):
        received=[]
        owner=SimpleNamespace(large_streams=0)
        async def forward(stream,request,body):
            await stream.send({'kind':'response_start','status':200,'headers':{}})
            await stream.send({'kind':'response_end'})
            stream.response_complete=True
            await stream.receive(10)
        owner.forward_http=forward
        async def record(data):received.append(json.loads(data[1:]))
        channel=SimpleNamespace(owner=owner,binding='synthetic',channel='synthetic',mobile=b'key',
            queue=asyncio.Queue(128),record=record,
            cipher=SimpleNamespace(encrypt=lambda v:v,decrypt=lambda v:v))
        session=Session(channel)
        task=asyncio.create_task(session.run())
        try:
            for batch in range(3):
                for identity in range(batch*8+1,batch*8+9):
                    request={'kind':'request','stream_id':identity,'id':str(uuid.uuid4()),'method':'GET',
                             'path':'/api/auth','content_type':'application/json','headers':{},'length':0}
                    for value in [request,{'kind':'request_end','stream_id':identity}]:
                        await channel.queue.put(b'\0'+json.dumps(value).encode())
                async with asyncio.timeout(3):
                    while sum(v['kind']=='response_end' for v in received)<(batch+1)*8:
                        await asyncio.sleep(.001)
                self.assertLessEqual(len(session.streams),8)
            self.assertFalse(any(v['kind']=='stream_error' for v in received))
            # Retired confirmations are accepted as harmless late stream records.
            await channel.queue.put(b'\0'+json.dumps({'kind':'response_ack','stream_id':1}).encode())
            await channel.queue.put(b'\0'+json.dumps({'kind':'ping','stream_id':0,'id':'test'}).encode())
            async with asyncio.timeout(3):
                while not any(v['kind']=='pong' for v in received):await asyncio.sleep(.001)
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
