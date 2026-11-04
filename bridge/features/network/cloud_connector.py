"""Bounded Noise channels forwarding only into a private, policy-preserving local HTTP listener."""
import asyncio
import base64
import hmac
import json
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from relay.protocol import MAX_BODY, content_type, download_headers, validate
from bridge.features.auth.tls import client_context
from bridge.features.auth.auth import session_file_lock
from bridge.features.network.cloud_account import Controller, CloudError, scope, entitlement_seconds
from bridge.features.network.cloud_crypto import (ControlCipher, control_prologue, decode_frame,
                                                 decode_key, encode_frame, encode_message, public_key,
                                                 pairing_safety_code)
from bridge.features.notifications.channels import read_json, write_json
from bridge.features.notifications.cloud import CloudNotifications

MAX_CHANNELS = 12
WINDOW = 4


def validate_request(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'id', 'method', 'path', 'content_type', 'headers', 'length'} or value['kind'] != 'request':
        raise ValueError('invalid request record')
    uuid.UUID(value['id'])
    if (not isinstance(value['path'], str) or not isinstance(value['method'], str)
            or type(value['length']) is not int or not 0 <= value['length'] <= MAX_BODY):
        raise ValueError('invalid request fields')
    if value['path'] == '/api/auth' and value['method'] == 'GET' and value['length'] == 0:
        pass
    elif value['method'] == 'GET' and urlsplit(value['path']).path.endswith('/events') and value['path'] != '/api/mobile/events':
        # SSE is the same authenticated session resource; not an arbitrary socket/RPC endpoint.
        path, separator, query = value['path'].partition('?')
        validate('GET', path.removesuffix('/events') + '/poll' + separator + query, value['length'])
    else:
        validate(value['method'], value['path'], value['length'])
    content_type(value['content_type'])
    download_headers(value['headers'])


class Channel:
    def __init__(self, owner, binding, channel, mobile):
        self.owner, self.binding, self.channel, self.mobile = owner, binding, channel, mobile
        self.queue = asyncio.Queue(8)
        self.cipher = None
        self.task = None

    async def record(self, data):
        await self.owner.send_frame(2, self.binding, self.channel, data)

    async def send(self, value):
        await self.record(self.cipher.encrypt(b'\0' + encode_message(value)))

    async def receive(self, timeout=60):
        raw = await asyncio.wait_for(self.queue.get(), timeout)
        data = self.cipher.decrypt(raw)
        if not data:
            raise ValueError('empty control record')
        if data[0] == 0:
            value = json.loads(data[1:])
            if not isinstance(value, dict):
                raise ValueError('invalid control record')
            return value
        if data[0] == 1:
            return data[1:]
        raise ValueError('unknown control record')

    async def run(self):
        o = self.owner
        private = base64.b64decode(o.secret['control_private'], validate=True)
        prologue = control_prologue(o.config['origin'], o.config['gateway_id'], self.binding,
                                    self.channel, public_key(private), self.mobile)
        self.cipher = ControlCipher(private, self.mobile, prologue)
        first = await asyncio.wait_for(self.queue.get(), 15)
        self.cipher.handshake_read(first)
        await self.record(self.cipher.handshake_write())
        await self.send({'kind': 'ready'})
        if await self.receive(15) != {'kind': 'key_confirm'}:
            raise ValueError('key confirmation required')
        await self.send({'kind': 'key_confirmed'})
        authorize = await self.receive(15)
        if not isinstance(authorize, dict):
            raise ValueError('invalid authorization record')
        if authorize.get('kind') == 'pair':
            if not await o.pair(self, authorize):
                return
        elif set(authorize) != {'kind', 'preview_public_key'} or authorize.get('kind') != 'authorize':
            raise PermissionError('local desktop approval required')
        pin = o.pin(self.binding)
        if not pin or not hmac.compare_digest(decode_key(pin['mobile_public_key']), self.mobile):
            raise PermissionError('unapproved mobile identity')
        if authorize.get('kind') == 'authorize' and authorize.get('preview_public_key') != pin['preview_public_key']:
            raise PermissionError('preview key replacement requires re-pairing')
        await self.send({'kind': 'authorized', 'desktop_preview_public_key': o.preview_public_key()})
        request = await self.receive()
        validate_request(request)
        size = request['length']
        if type(size) is not int or not 0 <= size <= MAX_BODY:
            raise ValueError('request size limit')
        body = bytearray()
        await self.send({'kind': 'credit', 'count': WINDOW})
        while True:
            part = await self.receive()
            if isinstance(part, bytes):
                if not part or len(body) + len(part) > size:
                    raise ValueError('request length mismatch')
                body.extend(part)
                await self.send({'kind': 'credit', 'count': 1})
            elif part == {'kind': 'request_end'}:
                if len(body) != size:
                    raise ValueError('request length mismatch')
                break
            else:
                raise ValueError('unexpected request record')
        await o.forward_http(self, request, body)


class Connector:
    def __init__(self, directory, gateway):
        self.directory, self.gateway = Path(directory), gateway
        self.accounts = Controller(directory)
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.access_lock = threading.Lock()
        self.channels = {}
        self.blocked_bindings = set()
        self.access_generations = {}
        self.thread = self.http_thread = None
        self.loop = self.ws = None
        self.config = self.secret = None
        self.sessions = {}
        self.notifications = CloudNotifications(directory)
        self.entitlement_deadline = 0
        self.policy_identity = None
        self.next_entitlement_check = 0
        self.wake = None

    def preview_public_key(self):
        return base64.b64encode(public_key(base64.b64decode(self.secret['preview_private'], validate=True))).decode('ascii')

    def pin(self, binding):
        config = self.accounts.config()
        if not config or not self.relay_current(config):
            return None
        if (self.account_identity(config), binding) in self.blocked_bindings:
            return None
        key = scope(config['origin'], config['account_id'])
        pin = read_json(self.directory / 'cloud-bindings.json', {}).get(key, {}).get(binding)
        if not isinstance(pin, dict) or pin.get('gateway_id') != config['gateway_id']:
            return None
        return pin if self.accounts.access.state(key, config['gateway_id'], binding)['allowed'] else None

    def status(self, state, code=''):
        config = self.config or self.accounts.config()
        write_json(self.directory / 'cloud-status.json', {
            'state': state, 'code': code, 'pid': __import__('os').getpid(),
            'scope': scope(config['origin'], config['account_id']) if config else '',
            'gateway_id': config.get('gateway_id')})

    def relay_current(self, config=None):
        config = self.accounts.config() if config is None else config
        return bool(not self.closed.is_set() and config and config.get('enabled') and self.config == config
                    and self.policy_identity == self.account_identity(config)
                    and (self.entitlement_deadline is None or self.entitlement_deadline > time.monotonic()))

    @staticmethod
    def account_identity(config):
        return tuple(config.get(key) for key in ('origin', 'account_id', 'gateway_id'))

    async def refresh_policy(self):
        started = time.monotonic()
        before = self.accounts.config()
        if not before:
            self.entitlement_deadline = 0
            self.policy_identity = None
            return False
        try:
            value = await asyncio.to_thread(self.accounts.refresh_entitlement)
        except (Exception, asyncio.CancelledError):
            self.entitlement_deadline = 0
            self.policy_identity = None
            raise
        after = self.accounts.config()
        if self.account_identity(before) != self.account_identity(after):
            self.entitlement_deadline = 0
            self.policy_identity = None
            return False
        remaining = entitlement_seconds(value)
        # Start before the request so network latency cannot extend a server grant.
        self.entitlement_deadline = None if remaining is None else started + remaining
        self.policy_identity = self.account_identity(after)
        self.next_entitlement_check = started + (min(60, remaining) if remaining else 60)
        return bool(not self.closed.is_set() and after.get('enabled') and value['active']
                    and (self.entitlement_deadline is None or self.entitlement_deadline > time.monotonic()))

    async def wait_for_change(self, seconds):
        if self.closed.is_set():
            return
        try:
            await asyncio.wait_for(self.wake.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self.wake.clear()

    async def watch_policy(self):
        while not self.closed.is_set():
            if self.ws and not self.ws.closed:
                try:
                    if not self.relay_current():
                        await self.disconnect()
                    elif time.monotonic() >= self.next_entitlement_check:
                        if not await self.refresh_policy() or not self.relay_current():
                            await self.disconnect()
                except CloudError as exc:
                    self.status('unavailable', exc.code)
                    await self.disconnect()
                except Exception:
                    self.status('unavailable', 'connection_failed')
                    await self.disconnect()
            await asyncio.sleep(1)

    def control(self, value):
        if not isinstance(value, dict):
            raise ValueError('invalid cloud operation')
        action = value.get('action', 'status')
        if action == 'devices':
            result = self.accounts.device_directory()
            identity = self.account_identity(self.accounts.config())
            for device in result['devices']:
                if (identity, device['binding_id']) in self.blocked_bindings:
                    device['allowed'] = False
                    if device['sync_state'] == 'confirmed':
                        device['sync_state'] = 'unconfirmed'
            return result
        if action == 'access':
            binding = str(uuid.UUID(value['binding_id']))
            config = self.accounts.config()
            identity = (self.account_identity(config), binding)
            with self.access_lock:
                generation = self.access_generations.get(identity, 0) + 1
                self.access_generations[identity] = generation
                self.blocked_bindings.add(identity)
            def block(binding):
                if self.loop and not self.loop.is_closed():
                    asyncio.run_coroutine_threadsafe(self.block_binding(binding), self.loop).result(timeout=5)
            # The in-memory fence also holds if writing the durable fence fails.
            block(binding)
            try:
                result = self.accounts.set_binding_access(value)
            except OSError:
                raise CloudError('device_access_storage_error') from None
            if result['allowed'] and result['remote_access_confirmed']:
                with self.access_lock:
                    if self.access_generations.get(identity) == generation and config == self.accounts.config():
                        self.blocked_bindings.discard(identity)
            return result
        before = self.accounts.config()
        try:
            return self.accounts.control(value)
        finally:
            if action in ('login', 'logout', 'entitlement', 'redeem') and self.loop:
                changed = before != self.accounts.config()
                def reconcile():
                    self.next_entitlement_check = 0
                    if self.wake:
                        self.wake.set()
                    if changed or action in ('login', 'logout'):
                        asyncio.create_task(self.disconnect())
                self.loop.call_soon_threadsafe(reconcile)

    async def pair(self, channel, value):
        if set(value) != {'kind', 'request_id', 'device_name', 'preview_public_key'} or value['request_id'] != channel.binding:
            raise ValueError('invalid pairing record')
        result = await asyncio.to_thread(self.record_proof, channel.binding, channel.mobile, value)
        if result['status'] == 'active':
            return True
        await channel.send({'kind': 'pair_pending', 'desktop_preview_public_key': self.preview_public_key(),
                            'safety_code': result['safety_code']})
        return False

    def saved_proofs(self):
        key = scope(self.config['origin'], self.config['account_id']) + ':' + self.config['gateway_id']
        return read_json(self.directory / 'cloud-device-proofs.json', {}).get(key, {})

    def save_proofs(self, value):
        path = self.directory / 'cloud-device-proofs.json'
        saved = read_json(path, {})
        key = scope(self.config['origin'], self.config['account_id']) + ':' + self.config['gateway_id']
        saved[key] = value
        write_json(path, saved)

    def record_proof(self, binding, mobile, value):
        preview = decode_key(value['preview_public_key'])
        name = value['device_name']
        if not isinstance(name, str) or not 1 <= len(name.encode('utf-8')) <= 100 or any(ord(c) < 32 for c in name):
            raise ValueError('invalid device name')
        with self.lock, session_file_lock(self.accounts.lock_path):
            if not self.relay_current():
                raise CloudError('gateway_not_connected')
            record = self.accounts.device_request('/v1/gateway/bindings/' + binding)
            if record.get('gateway_id') != self.config['gateway_id'] or record.get('mobile_public_key') != base64.b64encode(mobile).decode('ascii') or record.get('status') not in ('pending', 'active'):
                raise PermissionError('invalid pairing proof')
            private = decode_key(self.secret['control_private'])
            desktop = public_key(private)
            code = pairing_safety_code(self.config['origin'], self.config['gateway_id'], binding,
                                       private, mobile, desktop, mobile, decode_key(self.preview_public_key()), preview)
            pin = self.pin(binding)
            trusted = self.accounts.trusted_pin(self.config, binding)
            if trusted and (trusted.get('mobile_public_key') != record['mobile_public_key'] or trusted.get('preview_public_key') != value['preview_public_key']):
                raise PermissionError('device proof identity changed')
            if record['status'] == 'active' and pin:
                if pin.get('mobile_public_key') != record['mobile_public_key'] or pin.get('preview_public_key') != value['preview_public_key']:
                    raise PermissionError('device proof identity changed')
                return {'status': 'active', 'safety_code': code}
            saved = self.saved_proofs()
            candidate = {'binding_id': binding, 'installation_id': record['installation_id'], 'device_name': name,
                         'mobile_public_key': record['mobile_public_key'], 'preview_public_key': value['preview_public_key'],
                         'safety_code': code}
            old = saved.get(binding)
            if old and any(old.get(k) != candidate[k] for k in ('installation_id', 'mobile_public_key', 'preview_public_key', 'safety_code')):
                raise PermissionError('device proof identity changed')
            if len(saved) >= 200 and binding not in saved:
                raise CloudError('device_proof_capacity_reached')
            saved[binding] = candidate
            self.save_proofs(saved)
            return {'status': 'pending', 'safety_code': code}

    async def send_frame(self, kind, binding, channel, payload=b''):
        async with self.send_lock:
            if not self.ws or self.ws.closed or not self.relay_current():
                raise CloudError('gateway_not_connected')
            await asyncio.wait_for(self.ws.send_bytes(encode_frame(kind, binding, channel, payload)), 5)

    async def forward_http(self, channel, value, body):
        pin = self.pin(channel.binding)
        if not pin or not hmac.compare_digest(decode_key(pin['mobile_public_key']), channel.mobile):
            raise PermissionError('binding revoked')
        if channel.binding not in self.sessions:
            if len(self.sessions) >= 32:
                raise ValueError('local session capacity')
            self.sessions[channel.binding] = self.server.auth.new_session('127.0.0.1', 'Noise-approved mobile')
        token, session = self.sessions[channel.binding]
        if not self.server.auth.get(token) or not self.server.auth.permitted('127.0.0.1'):
            raise PermissionError('local session revoked')
        headers = {'Cookie': self.server.auth.cookie_name + '=' + token, 'Origin': self.local_origin,
                   'X-CSRF-Token': session['csrf'], 'Accept-Encoding': 'identity',
                   'Content-Type': value['content_type'], **value['headers']}
        async with self.local.request(value['method'], self.local_origin + value['path'], headers=headers,
                                      data=bytes(body) if value['method'] == 'POST' else None, allow_redirects=False) as response:
            if 300 <= response.status < 400:
                raise PermissionError('local redirects forbidden')
            decorated = None
            if response.status == 200 and value['method'] == 'GET' and urlsplit(value['path']).path == '/api/mobile/events':
                raw = bytearray()
                async for chunk in response.content.iter_chunked(32767):
                    if len(raw) + len(chunk) > 2 * 1024 * 1024:
                        raise ValueError('notification snapshot size limit')
                    raw.extend(chunk)
                snapshot = self.notifications.decorate(self.config, channel.binding, pin,
                                                       decode_key(self.secret['preview_private']), json.loads(raw))
                decorated = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode()
            result_headers = {'Content-Type': response.headers.get('Content-Type', 'application/octet-stream')}
            result_headers.update(download_headers({k: response.headers[k] for k in ('ETag', 'Content-Range', 'Accept-Ranges') if k in response.headers}, response=True))
            length = len(decorated) if decorated is not None else response.content_length
            if length is not None:
                if length > MAX_BODY:
                    raise ValueError('response size limit')
                result_headers['Content-Length'] = str(length)
            await channel.send({'kind': 'response_start', 'status': response.status, 'headers': result_headers})
            credits = 0
            async def chunks():
                if decorated is not None:
                    for offset in range(0, len(decorated), 32767):
                        yield decorated[offset:offset + 32767]
                else:
                    async for part in response.content.iter_chunked(32767):
                        yield part
            async for chunk in chunks():
                if not self.pin(channel.binding):
                    raise PermissionError('binding revoked')
                while not credits:
                    credit = await channel.receive(15)
                    if not isinstance(credit, dict) or set(credit) != {'kind', 'count'} or credit['kind'] != 'credit' or type(credit['count']) is not int or not 1 <= credit['count'] <= WINDOW:
                        raise ValueError('invalid response credit')
                    credits += credit['count']
                credits -= 1
                await channel.record(channel.cipher.encrypt(b'\1' + chunk))
            await channel.send({'kind': 'response_end'})
            await self.wait_response_close(channel)

    @staticmethod
    async def wait_response_close(channel):
        # The phone can still be sending window credits while response_end is
        # in flight. Let the receiver close the channel instead of racing it
        # with an immediate outer Close from the worker's finally block.
        async def drain():
            remaining = WINDOW
            while True:
                value = await channel.receive(5)
                if (not isinstance(value, dict) or set(value) != {'kind', 'count'}
                        or value['kind'] != 'credit' or type(value['count']) is not int
                        or not 1 <= value['count'] <= remaining):
                    raise ValueError('invalid final response credit')
                remaining -= value['count']
        try:
            await asyncio.wait_for(drain(), 5)
        except asyncio.TimeoutError:
            pass

    async def disconnect(self):
        if self.ws and not self.ws.closed:
            await self.ws.close()

    async def block_binding(self, binding):
        tasks = [channel.task for channel in list(self.channels.values()) if channel.binding == binding and channel.task]
        for task in tasks:
            task.cancel()
        if binding in self.sessions:
            token, _ = self.sessions.pop(binding)
            self.server.auth.logout(token)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def clear_authority(self):
        if hasattr(self, 'server'):
            for token, _ in self.sessions.values():
                self.server.auth.logout(token)
        self.sessions.clear()

    async def connect(self, remote):
        import aiohttp
        self.config = self.accounts.config()
        if not self.relay_current():
            raise CloudError('entitlement_required')
        self.secret = self.accounts.secret(self.config)
        self.server.auth.cookie_name = 'codex_cloud_' + scope(self.config['origin'], self.config['account_id']) + '_' + self.config['gateway_id']
        self.send_lock = asyncio.Lock()
        url = self.config['origin'].replace('https://', 'wss://', 1) + '/v1/ws/gateway'
        async with remote.ws_connect(url, headers={'Authorization': 'Bearer ' + self.secret['device_token']},
                                     protocols=('bridge-relay-v1',), compress=0, max_msg_size=32818,
                                     heartbeat=20, autoping=True) as ws:
            self.ws = ws
            if not self.relay_current():
                raise CloudError('entitlement_required')
            if ws.protocol != 'bridge-relay-v1':
                raise ValueError('relay protocol downgrade')
            self.status('connected')
            async for message in ws:
                if message.type != aiohttp.WSMsgType.BINARY:
                    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
                    raise ValueError('binary records required')
                kind, binding, channel_id, data = decode_frame(message.data)
                if kind == 1:
                    if channel_id in self.channels or len(self.channels) >= MAX_CHANNELS:
                        await self.send_frame(3, binding, channel_id)
                        continue
                    channel = Channel(self, binding, channel_id, data)
                    self.channels[channel_id] = channel
                    async def worker(channel=channel):
                        try:
                            await channel.run()
                        except (Exception, asyncio.CancelledError):
                            pass  # Do not log private business data, pairing proofs or crypto errors.
                        finally:
                            self.channels.pop(channel.channel, None)
                            if self.ws and not self.ws.closed:
                                try:
                                    await self.send_frame(3, channel.binding, channel.channel)
                                except Exception:
                                    pass
                    channel.task = asyncio.create_task(worker())
                else:
                    channel = self.channels.get(channel_id)
                    if not channel or channel.binding != binding:
                        continue
                    if kind == 3 or channel.queue.full():
                        channel.task.cancel()
                    else:
                        channel.queue.put_nowait(data)

    async def run(self):
        import aiohttp
        self.wake = asyncio.Event()
        trace = aiohttp.TraceConfig()
        async def reject_redirect(*args):
            raise ValueError('relay redirects forbidden')
        trace.on_request_redirect.append(reject_redirect)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=80),
                                         cookie_jar=aiohttp.DummyCookieJar(), trust_env=False) as local:
            self.local = local
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                             connector=aiohttp.TCPConnector(ssl=client_context()),
                                             cookie_jar=aiohttp.DummyCookieJar(), trust_env=True, trace_configs=[trace]) as remote:
                pump = asyncio.create_task(self.notification_pump())
                policy = asyncio.create_task(self.watch_policy())
                try:
                    while not self.closed.is_set():
                        try:
                            if not self.accounts.config():
                                self.status('disabled')
                                await self.wait_for_change(1)
                                continue
                            if not await self.refresh_policy():
                                self.status('disabled', 'entitlement_required')
                                await self.wait_for_change(30)
                                continue
                            self.status('connecting')
                            await self.connect(remote)
                        except CloudError as exc:
                            self.status('unavailable', exc.code)
                        except Exception:
                            self.status('unavailable', 'connection_failed')
                        finally:
                            tasks = [c.task for c in self.channels.values()]
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            self.channels.clear()
                            self.ws = None
                            self.clear_authority()
                            self.config = self.secret = None
                        # Only the outbound link reconnects. In-flight HTTP operations are never replayed.
                        await self.wait_for_change(3)
                finally:
                    pump.cancel()
                    policy.cancel()
                    await asyncio.gather(pump, policy, return_exceptions=True)

    async def notification_pump(self):
        while not self.closed.is_set():
            try:
                if self.ws and not self.ws.closed and self.relay_current():
                    await asyncio.to_thread(self.drain_notifications)
            except Exception:
                pass  # Neither notification content nor provider errors enter public status/logs.
            await asyncio.sleep(5)

    def drain_notifications(self):
        config = dict(self.config)
        manager = self.gateway.notifications
        if manager is None:
            return
        bindings = self.accounts.device_request('/v1/gateway/bindings', 'GET')['bindings']
        def current():
            return not self.closed.is_set() and config == self.accounts.config() and self.relay_current(config)
        def send(path, body):
            if not current():
                raise PermissionError('cloud account changed')
            binding = body.get('binding_id') or body.get('envelope', {}).get('binding_id')
            if path == '/v1/push/jobs' and not self.pin(binding):
                raise PermissionError('phone access disabled')
            return self.accounts.device_request(path, 'POST', body)
        self.notifications.drain(config, bindings, self.pin, decode_key(self.secret['preview_private']),
                                 manager.mobile_snapshot(0), send, current)

    def start(self, server_factory):
        import aiohttp  # Fail before starting any runtime if dependencies are unavailable.
        import noise.connection
        self.server = server_factory()
        self.local_origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.http_thread.start()
        def worker():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.run())
            self.loop.close()
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.loop and self.thread and self.thread.is_alive():
            if self.wake:
                self.loop.call_soon_threadsafe(self.wake.set)
            asyncio.run_coroutine_threadsafe(self.disconnect(), self.loop)
            self.thread.join(timeout=10)
        if self.http_thread:
            self.server.shutdown()
            self.http_thread.join(timeout=5)
            self.server.server_close()
        self.notifications.close()
        self.status('stopped')
