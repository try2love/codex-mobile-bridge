"""Multiplexed, bounded streams inside one authenticated Noise session."""

import asyncio
import collections
import json
import logging
import struct

from bridge.features.network.cloud_crypto import MAX_CHUNK, encode_message

LOG = logging.getLogger(__name__)
MAX_STREAMS = 8
WINDOW = 4
LARGE = 1024 * 1024


class Writer:
    """Round robin before encryption. Never reorder or drop encrypted records."""

    def __init__(self, channel):
        self.channel = channel
        self.queues = collections.OrderedDict()
        self.changed = asyncio.Condition()
        self.closed = False

    async def put(self, stream, data):
        if len(data) > MAX_CHUNK:
            raise ValueError('record too large')
        async with self.changed:
            await asyncio.wait_for(self.changed.wait_for(
                lambda: self.closed or len(self.queues.get(stream, ())) < 8), 10)
            if self.closed:
                raise ConnectionError('relay closed')
            self.queues.setdefault(stream, collections.deque()).append(data)
            self.changed.notify_all()

    async def run(self):
        try:
            while True:
                async with self.changed:
                    await self.changed.wait_for(lambda: self.queues)
                    stream, queue = self.queues.popitem(last=False)
                    data = queue.popleft()
                    if queue:
                        self.queues[stream] = queue
                    self.changed.notify_all()
                await self.channel.record(self.channel.cipher.encrypt(data))
        finally:
            async with self.changed:
                self.closed = True
                self.changed.notify_all()


class Stream:
    def __init__(self, session, identity):
        self.session, self.identity = session, identity
        self.owner = session.channel.owner
        self.binding = session.channel.binding
        self.mobile = session.channel.mobile
        self.queue = asyncio.Queue(8)
        self.task = None
        self.large = False
        self.response_complete = False

    async def send(self, value):
        await self.session.writer.put(self.identity, b'\0' + encode_message(
            dict(value, stream_id=self.identity)))

    async def send_body(self, body):
        await self.session.writer.put(self.identity, b'\1' + struct.pack('!Q', self.identity) + body)

    async def receive(self, timeout=60):
        return await asyncio.wait_for(self.queue.get(), timeout)

    async def promote_large(self):
        if self.large:
            return
        if self.session.large or self.owner.large_streams >= 2:
            raise OverflowError('attachment capacity')
        self.session.large = True
        self.owner.large_streams += 1
        self.large = True

    async def run(self, request):
        from bridge.features.network.cloud_connector import validate_request
        try:
            validate_request(request)
            size = request['length']
            if size > LARGE or any(part in request['path'] for part in ('/files/', '/upload', '/download')):
                await self.promote_large()
            body = bytearray()
            await self.send({'kind': 'credit', 'count': WINDOW})
            while True:
                part = await self.receive()
                if isinstance(part, bytes):
                    if not part or len(body) + len(part) > size:
                        raise ValueError('request length mismatch')
                    body.extend(part)
                    await self.send({'kind': 'credit', 'count': 1})
                elif part == {'kind': 'request_end'} and len(body) == size:
                    break
                else:
                    raise ValueError('invalid request body')
            await self.owner.forward_http(self, request, body)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = ('capacity' if isinstance(error, OverflowError) else
                    'access_denied' if isinstance(error, PermissionError) else
                    'timeout' if isinstance(error, TimeoutError) else 'request_failed')
            LOG.warning('relay_stream_end channel=%s stream=%s reason=%s',
                        self.session.channel.channel, self.identity, code)
            await self.send({'kind': 'stream_error', 'code': code})
        finally:
            self.session.streams.pop(self.identity, None)
            if self.large:
                self.session.large = False
                self.owner.large_streams -= 1


class Session:
    def __init__(self, channel):
        self.channel = channel
        self.writer = Writer(channel)
        self.streams = {}
        self.highest = 0
        self.seen = set()
        self.large = False
        self.controls = set()

    async def run(self):
        writer = asyncio.create_task(self.writer.run())
        reader = asyncio.create_task(self.read())
        try:
            done, _ = await asyncio.wait((writer, reader), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            tasks = [writer, reader, *self.controls, *(s.task for s in list(self.streams.values()))]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def read(self):
        while True:
            raw = await asyncio.wait_for(self.channel.queue.get(), 75)
            data = self.channel.cipher.decrypt(raw)
            if data[:1] == b'\0':
                value = json.loads(data[1:])
                if not isinstance(value, dict):
                    raise ValueError('invalid control')
                identity = value.pop('stream_id', None)
            elif data[:1] == b'\1' and len(data) > 9:
                identity = struct.unpack('!Q', data[1:9])[0]
                value = data[9:]
            else:
                raise ValueError('invalid stream record')
            if type(identity) is not int or not 0 <= identity <= 2**53 - 1:
                raise ValueError('invalid stream identity')
            if identity == 0:
                if isinstance(value, dict) and value.get('kind') == 'p2p_prepare' and not self.channel.p2p:
                    if not self.controls:
                        task = asyncio.create_task(self.prepare_p2p())
                        self.controls.add(task)
                        task.add_done_callback(self.controls.discard)
                    continue
                if not isinstance(value, dict) or value.get('kind') != 'ping':
                    raise ValueError('invalid heartbeat')
                await self.writer.put(0, b'\0' + encode_message(
                    {'kind': 'pong', 'stream_id': 0, 'id': value.get('id')}))
                continue
            if isinstance(value, dict) and value.get('kind') == 'request':
                if identity in self.seen or identity <= self.highest - 1024:
                    raise ValueError('stream identity reused')
                self.highest = max(identity, self.highest)
                self.seen.add(identity)
                self.seen = {item for item in self.seen if item > self.highest - 1024}
                # Completed responses awaiting ACK must not deny new work. Their
                # authenticated end record is already queued in order; late ACKs
                # can be ignored after bounded retirement.
                for completed in list(self.streams.values()):
                    if completed.response_complete:
                        self.streams.pop(completed.identity, None)
                        completed.task.cancel()
                if len(self.streams) >= MAX_STREAMS:
                    await self.writer.put(identity, b'\0' + encode_message(
                        {'kind': 'stream_error', 'stream_id': identity, 'code': 'capacity'}))
                    continue
                stream = Stream(self, identity)
                self.streams[identity] = stream
                stream.task = asyncio.create_task(stream.run(value))
                continue
            stream = self.streams.get(identity)
            if stream is None:
                continue  # Retired streams cannot become a new request.
            if value == {'kind': 'cancel'} or stream.queue.full():
                if value != {'kind': 'cancel'}:
                    await self.writer.put(identity, b'\0' + encode_message(
                        {'kind': 'stream_error', 'stream_id': identity, 'code': 'slow_consumer'}))
                stream.task.cancel()
                self.streams.pop(identity, None)
            else:
                stream.queue.put_nowait(value)

    async def prepare_p2p(self):
        try:
            offer = await self.channel.owner.p2p.offer(self.channel)
        except Exception:
            offer = {'kind': 'p2p_unavailable'}
        await self.writer.put(0, b'\0' + encode_message(dict(offer, stream_id=0)))
