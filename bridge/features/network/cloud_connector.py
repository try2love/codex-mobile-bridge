"""Bounded Noise channels forwarding only into a private, policy-preserving local HTTP listener."""
import asyncio
import base64
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path

from relay.protocol import MAX_BODY, content_type, download_headers, validate
from bridge.features.accounts.cloud_summary import SUMMARY_PATH, summary_query
from bridge.features.network.cloud_account_routes import authorize_account_route, validate_account_route
from bridge.features.network.cloud_routes import cloud_target, session_route
from bridge.features.auth.tls import client_context
from bridge.features.auth.auth import session_file_lock
from bridge.features.network.cloud_account import Controller, CloudError, scope
from bridge.features.network.cloud_crypto import (ControlCipher, control_prologue, decode_frame,
                                                 decode_key, encode_frame, encode_message, public_key,
                                                 pairing_safety_code)
from bridge.features.notifications.channels import read_json, write_json

MAX_CHANNELS = 16
WINDOW = 4
LOG = logging.getLogger(__name__)


def validate_request(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'id', 'method', 'path', 'content_type', 'headers', 'length'} or value['kind'] != 'request':
        raise ValueError('invalid request record')
    uuid.UUID(value['id'])
    if (not isinstance(value['path'], str) or not isinstance(value['method'], str)
            or type(value['length']) is not int or not 0 <= value['length'] <= MAX_BODY):
        raise ValueError('invalid request fields')
    method, target, size = value['method'], value['path'], value['length']
    path, query = cloud_target(method, target, size)
    if path == SUMMARY_PATH:
        summary_query(method, target, size)
        if value['headers']:
            raise PermissionError('云账号摘要不接受转发头')
    elif validate_account_route(method, path, query, size):
        if value['headers']:
            raise PermissionError('账号管理不接受转发头')
    elif not (method == 'GET' and target == '/api/auth' or session_route(method, path, query)):
        validate(method, target, size)
    content_type(value['content_type'])
    download_headers(value['headers'])


class Channel:
    def __init__(self, owner, binding, channel, mobile):
        self.owner, self.binding, self.channel, self.mobile = owner, binding, channel, mobile
        self.queue = asyncio.Queue(128)
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
        from bridge.features.network.cloud_session import Session
        await Session(self).run()


class Connector:
    def __init__(self, directory, gateway):
        self.directory, self.gateway = Path(directory), gateway
        self.accounts = Controller(directory)
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.access_lock = threading.Lock()
        self.channels = {}
        self.large_streams = 0
        self.blocked_bindings = set()
        self.access_generations = {}
        self.thread = self.http_thread = None
        self.loop = self.ws = None
        self.config = self.secret = None
        self.sessions = {}
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
        # Entitlement is enforced by the server. Cached display data is not authority.
        return bool(not self.closed.is_set() and config and self.config == config)

    @staticmethod
    def account_identity(config):
        return tuple(config.get(key) for key in ('origin', 'account_id', 'gateway_id'))

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
        authorize_account_route(self.server.desktop_sessions, value['method'], value['path'], body)
        headers = {'Cookie': self.server.auth.cookie_name + '=' + token, 'Origin': self.local_origin,
                   'X-CSRF-Token': session['csrf'], 'Accept-Encoding': 'identity',
                   'Content-Type': value['content_type'], **value['headers']}
        async with self.local.request(value['method'], self.local_origin + value['path'], headers=headers,
                                      data=bytes(body) if value['method'] == 'POST' else None, allow_redirects=False) as response:
            if 300 <= response.status < 400:
                raise PermissionError('local redirects forbidden')
            result_headers = {'Content-Type': response.headers.get('Content-Type', 'application/octet-stream')}
            result_headers.update(download_headers({k: response.headers[k] for k in ('ETag', 'Content-Range', 'Accept-Ranges') if k in response.headers}, response=True))
            disposition = response.headers.get('Content-Disposition')
            if disposition is not None:
                if len(disposition) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in disposition):
                    raise ValueError('invalid download filename')
                result_headers['Content-Disposition'] = disposition
            length = response.content_length
            if length is not None:
                if length > MAX_BODY:
                    raise ValueError('response size limit')
                result_headers['Content-Length'] = str(length)
                if length > 1024 * 1024:
                    await channel.promote_large()
            await channel.send({'kind': 'response_start', 'status': response.status, 'headers': result_headers})
            credits = 0
            transferred = 0
            event_stream = result_headers['Content-Type'].startswith('text/event-stream')
            async for chunk in response.content.iter_chunked(32759):
                transferred += len(chunk)
                if not event_stream and transferred > MAX_BODY:
                    raise ValueError('response size limit')
                if not event_stream and transferred > 1024 * 1024:
                    await channel.promote_large()
                if not self.pin(channel.binding):
                    raise PermissionError('binding revoked')
                while not credits:
                    credit = await channel.receive(15)
                    if not isinstance(credit, dict) or set(credit) != {'kind', 'count'} or credit['kind'] != 'credit' or type(credit['count']) is not int or not 1 <= credit['count'] <= WINDOW:
                        raise ValueError('invalid response credit')
                    credits += credit['count']
                credits -= 1
                await channel.send_body(chunk)
            await channel.send({'kind': 'response_end'})
            channel.response_complete = True
            await self.wait_response_close(channel)

    @staticmethod
    async def wait_response_close(channel):
        # A stream acknowledgement releases this HTTP task, not the Noise session.
        async def drain():
            remaining = WINDOW
            while True:
                value = await channel.receive(10)
                if value == {'kind': 'response_ack'}:
                    return
                if (not isinstance(value, dict) or set(value) != {'kind', 'count'}
                        or value['kind'] != 'credit' or type(value['count']) is not int
                        or not 1 <= value['count'] <= remaining):
                    raise ValueError('invalid final response credit')
                remaining -= value['count']
        try:
            await asyncio.wait_for(drain(), 10)
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
                                     # aiohttp rejects >= max_msg_size; wire decoder still enforces <=32818.
                                     protocols=('bridge-relay-v2',), compress=0, max_msg_size=32819,
                                     heartbeat=20, autoping=True) as ws:
            self.ws = ws
            if not self.relay_current():
                raise CloudError('entitlement_required')
            if ws.protocol != 'bridge-relay-v2':
                raise ValueError('relay protocol downgrade')
            self.status('connected')
            async for message in ws:
                if message.type != aiohttp.WSMsgType.BINARY:
                    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        LOG.warning('relay_socket_end close_code=%s reason=%s', ws.close_code,
                                    type(ws.exception()).__name__ if ws.exception() else 'peer_close')
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
                        except asyncio.CancelledError:
                            pass
                        except Exception as error:
                            LOG.warning('relay_session_end channel=%s reason=%s', channel.channel, type(error).__name__)
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
                failures = 0
                while not self.closed.is_set():
                    started = time.monotonic()
                    observers = []
                    try:
                        if not self.accounts.config():
                            self.status('disabled')
                            await self.wait_for_change(None)
                            continue
                        observers = [asyncio.create_task(self.watch_policy())]
                        self.status('connecting')
                        await self.connect(remote)
                    except CloudError as exc:
                        self.status('unavailable', exc.code)
                    except Exception as error:
                        status = getattr(error, 'status', 0)
                        code = {401: 'login_required', 402: 'entitlement_required', 403: 'access_denied',
                                409: 'gateway_already_connected', 426: 'protocol_upgrade_required',
                                429: 'capacity'}.get(status, 'connection_failed')
                        self.status('unavailable', code)
                        LOG.warning('relay_connect_failed reason=%s', code)
                    finally:
                        tasks = observers + [c.task for c in self.channels.values()]
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        self.channels.clear()
                        self.ws = None
                        self.clear_authority()
                        self.config = self.secret = None
                    # Only the outbound link reconnects. In-flight HTTP operations are never replayed.
                    failures = 0 if time.monotonic() - started >= 60 else min(failures + 1, 5)
                    await self.wait_for_change(min(15, 2 ** max(0, failures - 1) + secrets.randbelow(500) / 1000))

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
        self.status('stopped')
