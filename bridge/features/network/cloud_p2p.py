"""Per-binding EasyTier instances and an independently authenticated Noise ingress."""
import asyncio
import json
import os
import secrets
import subprocess
import uuid
from pathlib import Path

from bridge.resources import project_root
from bridge.features.network.cloud_crypto import decode_frame, decode_key, encode_frame


def helper_path():
    name = 'bridge-p2p.exe' if os.name == 'nt' else 'bridge-p2p'
    root = project_root()
    for candidate in (root / 'p2p' / name, root / 'dist/p2p' / name,
                      root / 'native/bridge-p2p/target/release' / name):
        if candidate.is_file():
            return candidate
    return None


class P2P:
    def __init__(self, owner):
        self.owner = owner
        self.runner = None
        self.port = None
        self.offers = {}
        self.lock = asyncio.Lock()
        self.enabled = False

    def active(self):
        return any(getattr(c, 'p2p', False) for c in self.owner.channels.values())

    async def start(self):
        from aiohttp import web
        app = web.Application(client_max_size=32768)
        app.router.add_get('/bridge-p2p', self.accept)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def offer(self, channel):
        owner = self.owner
        context = owner.authority_context()
        config = context[0]
        generation = owner.access_generations.get((owner.account_identity(config), channel.binding), 0)
        response = await asyncio.to_thread(owner.accounts.device_request,
            '/v1/gateway/bindings/' + channel.binding + '/p2p', 'GET')
        binding = response.get('binding', {})
        if (not owner.authority_matches(context) or not owner.pin(channel.binding)
                or generation != owner.access_generations.get((owner.account_identity(config), channel.binding), 0)
                or response.get('origin') != config['origin'] or response.get('account_id') != config['account_id']
                or response.get('device_id') != config['gateway_id'] or binding.get('id') != channel.binding
                or binding.get('status') != 'active' or decode_key(binding.get('mobile_public_key')) != channel.mobile):
            raise PermissionError('P2P authority changed')
        async with self.lock:
            existing = self.offers.get(channel.binding)
            if existing and existing['process'].returncode is None:
                return existing['public']
            executable = helper_path()
            if not executable:
                raise RuntimeError('P2P helper unavailable')
            if not self.runner:
                await self.start()
            network = secrets.token_hex(24)
            settings = {'network': network, 'secret': secrets.token_hex(32),
                        'bootstrap': response['bootstrap'], 'desktop': True, 'service_port': self.port}
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            process = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=4096, **options)
            try:
                process.stdin.write((json.dumps(settings) + '\n').encode())
                await process.stdin.drain()
                ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
                if (ready.get('ready') is not True or not owner.pin(channel.binding)
                        or not owner.authority_matches(context)
                        or generation != owner.access_generations.get((owner.account_identity(config), channel.binding), 0)):
                    raise PermissionError('P2P setup invalidated')
                public = {'kind': 'p2p_offer', 'protocol': 'easytier-v1', 'binding_id': channel.binding,
                          'offer_id': network, 'access_revision': binding['access_revision'],
                          'settings': dict(settings, desktop=False)}
                self.offers[channel.binding] = {'public': public, 'process': process, 'generation': generation}
                return public
            except BaseException:
                await self.stop_process(process)
                raise

    async def accept(self, request):
        from aiohttp import web, WSMsgType
        from bridge.features.network.cloud_connector import Channel
        owner = self.owner
        binding_id = request.headers.get('X-Bridge-Binding', '')
        offer = self.offers.get(binding_id)
        pin = owner.pin(binding_id)
        if (request.headers.get('Origin') or not offer or not pin
                or request.headers.get('X-Bridge-P2P') != offer['public']['offer_id']
                or any(getattr(c, 'p2p', False) and c.binding == binding_id for c in owner.channels.values())):
            raise web.HTTPForbidden()
        # A new Noise session always checks the server. This is not an offline ticket.
        context = owner.authority_context()
        config = context[0]
        generation = owner.access_generations.get((owner.account_identity(config), binding_id), 0)
        try:
            result = await asyncio.to_thread(owner.accounts.device_request,
                '/v1/gateway/bindings/' + binding_id + '/p2p', 'GET')
        except Exception:
            raise web.HTTPServiceUnavailable() from None
        if (not owner.authority_matches(context) or not owner.pin(binding_id)
                or self.offers.get(binding_id) is not offer
                or generation != owner.access_generations.get((owner.account_identity(config), binding_id), 0)
                or result.get('origin') != config['origin'] or result.get('account_id') != config['account_id']
                or result.get('device_id') != config['gateway_id']
                or result.get('binding', {}).get('access_revision') != offer['public']['access_revision']):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(protocols=('bridge-p2p-v1',), max_msg_size=32819, compress=False)
        await ws.prepare(request)
        if (not owner.authority_matches(context) or not owner.pin(binding_id)
                or self.offers.get(binding_id) is not offer
                or generation != owner.access_generations.get((owner.account_identity(config), binding_id), 0)):
            await ws.close(code=1008, message=b'access_changed')
            return ws
        channel_id = str(uuid.uuid4())
        channel = Channel(owner, binding_id, channel_id, decode_key(pin['mobile_public_key']))
        channel.p2p = True
        channel.socket = ws
        async def record(data):
            raw = encode_frame(2, binding_id, channel_id, data)
            await asyncio.wait_for(ws.send_bytes(raw), 5)
            owner.p2p_tx += len(raw)
        channel.record = record
        owner.channels[channel_id] = channel
        channel.task = asyncio.create_task(channel.run())
        await ws.send_bytes(encode_frame(1, binding_id, channel_id, decode_key(result['binding']['gateway_public_key'])))
        async def read():
            async for message in ws:
                if message.type != WSMsgType.BINARY:
                    raise ConnectionError('P2P closed')
                kind, binding, identity, payload = decode_frame(message.data)
                if kind != 2 or binding != binding_id or identity != channel_id:
                    raise ValueError('invalid P2P route')
                owner.p2p_rx += len(message.data)
                await channel.queue.put(payload)
        reader = asyncio.create_task(read())
        try:
            await asyncio.wait((reader, channel.task), return_when=asyncio.FIRST_COMPLETED)
        finally:
            reader.cancel()
            channel.task.cancel()
            await asyncio.gather(reader, channel.task, return_exceptions=True)
            owner.channels.pop(channel_id, None)
            await ws.close()
        return ws

    @staticmethod
    async def stop_process(process):
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def block(self, binding):
        async with self.lock:
            offer = self.offers.pop(binding, None)
            if offer:
                await self.stop_process(offer['process'])

    async def close(self):
        for binding in list(self.offers):
            await self.block(binding)
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
