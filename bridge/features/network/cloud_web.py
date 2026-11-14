"""Account-Web requests, isolated from phone pairing and the encrypted relay."""
import asyncio
import json
import re
import struct
import time
import uuid
from contextlib import suppress
from urllib.parse import urlsplit

from relay.protocol import MAX_BODY, content_type, download_headers
from relay.http_framing import validate_http_request

PROTOCOL = 'bridge-web-proxy-v1'
CHUNK = 32768
WINDOW = 4


class CloudWeb:
    def __init__(self, owner):
        self.owner = owner
        self.ws = None
        self.tasks = {}
        self.sessions = {}
        self.lock = asyncio.Lock()
        self.config = {}
        self.token = ''
        self.origin = ''
        self.revision = 0
        self.blocked = getattr(owner, 'blocked_web_scopes', set())
        self.wake = asyncio.Event()

    def denied(self, config):
        return tuple(config.get(key) for key in ('origin', 'account_id', 'gateway_id')) in self.blocked

    def current(self):
        grant = self.owner.accounts.web_access_state(self.config)
        return (not self.denied(self.config) and grant['allowed'] is True and grant['revision'] == self.revision
                and not self.owner.closed.is_set() and self.config == self.owner.accounts.config()
                and self.owner.relay_current(self.config)
                and self.token == self.owner.accounts.secret(self.config).get('device_token'))

    async def send(self, value, data=None):
        async with self.lock:
            if not self.ws or self.ws.closed or not self.current():
                raise ConnectionError('cloud Web disconnected')
            if data is None:
                await asyncio.wait_for(self.ws.send_str(json.dumps(value, separators=(',', ':'))), 10)
            else:
                await asyncio.wait_for(self.ws.send_bytes(b'\1' + struct.pack('>Q', value) + data), 10)

    def forget(self, session_id):
        entry = self.sessions.pop(session_id, None)
        if entry:
            self.owner.server.auth.logout(entry[0])
        for stream in list(self.tasks.values()):
            if stream['request']['session_id'] == session_id:
                stream['task'].cancel()

    async def disconnect(self):
        for session_id in list(self.sessions):
            self.forget(session_id)
        pending = [entry['task'] for entry in list(self.tasks.values())]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self.ws and not self.ws.closed:
            with suppress(TimeoutError, ConnectionError):
                await asyncio.wait_for(self.ws.close(), 3)

    def validate_request(self, request):
        if not self.current():
            raise PermissionError('desktop Web access required')
        if (request.get('account_id') != self.config['account_id']
                or request.get('base') != '/d/' + self.config['gateway_id']
                or request.get('origin') != self.origin):
            raise PermissionError('Web account scope mismatch')
        uuid.UUID(request['session_id'])
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', request['csrf']):
            raise ValueError('invalid Web CSRF')
        if type(request['expires']) is not int or not time.time() < request['expires'] <= time.time() + 13 * 3600:
            raise ValueError('invalid Web session expiry')
        path, method, length = request['path'], request['method'], request['length']
        validate_http_request(method, path, length)
        if request.get('content_type'):
            content_type(request['content_type'])
        headers = dict(request.get('headers') or {})
        validator = headers.pop('If-None-Match', '')
        if not isinstance(validator, str) or len(validator) > 256 or any(ord(c) < 32 or ord(c) > 126 for c in validator):
            raise ValueError('invalid cache validator')
        download_headers(headers)

    async def forward(self, request, queue):
        stream_id = request['stream_id']
        try:
            self.validate_request(request)
            async with asyncio.timeout(request['expires'] - time.time()):
                await self.forward_request(request, queue)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A lost socket cannot receive an error; still retire the local task.
            with suppress(Exception):
                await self.send({'kind': 'stream_error', 'stream_id': stream_id})
        finally:
            self.tasks.pop(stream_id, None)

    async def forward_request(self, request, queue):
        sid, stream_id, length = request['session_id'], request['stream_id'], request['length']
        async def receive():
            return await asyncio.wait_for(queue.get(), 45)
        async def control(kind, **extra):
            await self.send({'kind': kind, 'stream_id': stream_id, **extra})
        body = bytearray()
        if length:
            await control('credit', count=WINDOW)
        while True:
            value = await receive()
            if isinstance(value, bytes):
                body.extend(value)
                if len(body) > length:
                    raise ValueError('Web request length exceeded')
                if len(body) < length:
                    await control('credit', count=1)
            elif value.get('kind') == 'request_end' and len(body) == length:
                break
            else:
                raise ValueError('invalid Web request body')
        if not self.current():
            raise PermissionError('Web account changed')
        for session_id, (_, _, expires) in list(self.sessions.items()):
            if expires <= time.time():
                self.forget(session_id)
        if sid not in self.sessions:
            if len(self.sessions) >= 32:
                raise ValueError('Web session capacity')
            token, session = self.owner.server.auth.new_session('127.0.0.1', 'Cloud account Web', remember=False)
            session['expires'] = min(session['expires'] or request['expires'], request['expires'])
            self.sessions[sid] = token, session, request['expires']
        token, session, _ = self.sessions[sid]
        if not self.owner.server.auth.get(token) or not self.owner.server.auth.permitted('127.0.0.1'):
            raise PermissionError('local Web session revoked')
        headers = {'Cookie': self.owner.server.auth.cookie_name + '=' + token,
                   'Origin': self.owner.local_origin, 'X-CSRF-Token': session['csrf'],
                   'Accept-Encoding': 'identity', **(request.get('headers') or {})}
        if request.get('content_type'):
            headers['Content-Type'] = request['content_type']
        path = urlsplit(request['path']).path
        # Auth and fixed resources are adapted; their validators describe the adapted bytes.
        adapted = path == '/api/auth' or self.owner.server.cloud_web_resource(path)
        if adapted:
            for name in ('If-None-Match', 'If-Range', 'Range'):
                headers.pop(name, None)
        async with self.owner.local.request(request['method'], self.owner.local_origin + request['path'],
                headers=headers, data=bytes(body) if body else None,
                allow_redirects=False) as response:
            if 300 <= response.status < 400 and response.status != 304:
                raise PermissionError('Web redirects forbidden')
            names = ('Content-Type', 'Content-Length', 'Content-Disposition', 'ETag', 'Cache-Control',
                     'Content-Range', 'Accept-Ranges', 'Content-Security-Policy', 'X-Frame-Options')
            result_headers = {k: response.headers[k] for k in names if k in response.headers}
            decorated = None
            if adapted and response.status == 200 and request['method'] != 'HEAD':
                raw = bytearray()
                async for part in response.content.iter_chunked(CHUNK):
                    raw.extend(part)
                    if len(raw) > MAX_BODY:
                        raise ValueError('Web asset too large')
                decorated, result_headers = self.owner.server.cloud_web_response(path, bytes(raw), result_headers,
                    {**request, 'local_origin': self.owner.local_origin})
            await control('response_start', status=response.status, headers=result_headers)
            async def chunks():
                if decorated is not None:
                    for offset in range(0, len(decorated), CHUNK):
                        yield decorated[offset:offset + CHUNK]
                else:
                    async for part in response.content.iter_chunked(CHUNK):
                        yield part
            credits, transferred = 0, 0
            events = result_headers.get('Content-Type', '').startswith('text/event-stream')
            async for part in chunks():
                transferred += len(part)
                if not events and transferred > MAX_BODY:
                    raise ValueError('Web response too large')
                if not credits:
                    value = await receive()
                    if not isinstance(value, dict) or value.get('kind') != 'credit' or type(value.get('count')) is not int or not 1 <= value['count'] <= WINDOW:
                        raise ValueError('invalid Web response credit')
                    credits = value['count']
                credits -= 1
                await self.send(stream_id, part)
            await control('response_end')

    async def connect(self, remote):
        import aiohttp
        config = self.owner.accounts.config()
        if not config or not self.owner.relay_current(config):
            return
        grant = self.owner.accounts.web_access_state(config)
        if self.denied(config) or grant['allowed'] is not True:
            return
        token = self.owner.accounts.secret(config)['device_token']
        capabilities = await asyncio.to_thread(self.owner.accounts.request, config['origin'], '/v1/capabilities')
        feature = capabilities.get('web_client', {})
        if not feature.get('enabled') or feature.get('protocol') != PROTOCOL:
            return
        origin = feature.get('origin', '')
        parsed = urlsplit(origin)
        source = urlsplit(config['origin'])
        development = (source.scheme == parsed.scheme == 'http' and
                       source.hostname == parsed.hostname == '127.0.0.1')
        if (parsed.scheme != 'https' and not development) or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('invalid Web origin')
        self.config, self.token, self.origin, self.revision = dict(config), token, origin, grant['revision']
        if not self.current():
            return
        url = config['origin'].replace('https://', 'wss://', 1).replace('http://', 'ws://', 1) + '/v1/ws/web-gateway'
        async with remote.ws_connect(url, headers={'Authorization': 'Bearer ' + token}, protocols=(PROTOCOL,),
                                     compress=0, max_msg_size=CHUNK + 10, heartbeat=20, autoping=True) as ws:
            self.ws = ws
            if not self.current():
                return
            if ws.protocol != PROTOCOL:
                raise ValueError('Web protocol mismatch')
            async for message in ws:
                if not self.current():
                    return
                if message.type == aiohttp.WSMsgType.BINARY:
                    data = message.data
                    if not 9 < len(data) <= CHUNK + 9 or data[0] != 1:
                        raise ValueError('invalid Web frame')
                    stream_id, value = struct.unpack('>Q', data[1:9])[0], data[9:]
                elif message.type == aiohttp.WSMsgType.TEXT:
                    if len(message.data) > 16384:
                        raise ValueError('Web control limit')
                    value = json.loads(message.data)
                    if not isinstance(value, dict):
                        raise ValueError('invalid Web control')
                    if value.get('kind') == 'close_session':
                        self.forget(value['session_id'])
                        continue
                    stream_id = value.get('stream_id')
                else:
                    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        return
                    raise ValueError('invalid Web frame type')
                if type(stream_id) is not int or not 0 < stream_id < 2 ** 53:
                    raise ValueError('invalid Web stream ID')
                if isinstance(value, dict) and value.get('kind') == 'request':
                    if stream_id in self.tasks or len(self.tasks) >= 64:
                        raise ValueError('Web stream capacity')
                    queue = asyncio.Queue(WINDOW + 4)
                    task = asyncio.create_task(self.forward(value, queue))
                    self.tasks[stream_id] = {'task': task, 'queue': queue, 'request': value}
                elif stream_id in self.tasks:
                    stream = self.tasks[stream_id]
                    if isinstance(value, dict) and value.get('kind') == 'cancel':
                        stream['task'].cancel()
                    elif stream['queue'].full():
                        stream['task'].cancel()
                    else:
                        stream['queue'].put_nowait(value)

    async def watch(self):
        while not self.owner.closed.is_set():
            try:
                current = not self.ws or self.current()
            except Exception:
                current = False
            if not current:
                await self.disconnect()
            for sid, (_, _, expires) in list(self.sessions.items()):
                if expires <= time.time():
                    self.forget(sid)
            await asyncio.sleep(1)

    async def run(self, remote):
        watcher = asyncio.create_task(self.watch())
        try:
            while not self.owner.closed.is_set():
                self.wake.clear()
                try:
                    await self.connect(remote)
                except Exception:
                    pass  # Never put credentials, URLs or user content into diagnostics.
                finally:
                    await self.disconnect()
                    self.ws = None
                try:
                    await asyncio.wait_for(self.wake.wait(), 15)
                except asyncio.TimeoutError:
                    pass
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await self.disconnect()
