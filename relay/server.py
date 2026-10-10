"""Headless HTTPS-edge relay. Devices execute tasks; this service only routes."""
import asyncio
import base64
import hmac
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from .protocol import MAX_BODY, MAX_FRAME, origin, validate, decode, content_type, download_headers
from .registry import Registry, token, PHONE_LIFETIME
from .stream import WINDOW, unpack

# Same host-only cookie name as the existing native clients; relay token space
# is independent and the connector never forwards this cookie to the computer.
COOKIE = 'codex_mobile_session'
ROOT = Path(__file__).resolve().parents[1]


class Relay:
    def __init__(self, directory, public_origin, test_http=False, timeout=90):
        self.origin = origin(public_origin, test_http)
        self.host = urlsplit(self.origin).netloc
        self.secure = self.origin.startswith('https://')
        self.registry = Registry(directory)
        self.devices = {}
        self.protocols = {}
        self.discovery = {}
        self.pending = {}
        self.timeout = timeout
        self.attempts = {}
        self.buffered = 0

    def same_origin(self, request):
        if request.headers.get('Origin') != self.origin:
            raise PermissionError('Same-origin request required')

    def rate_limit(self, request):
        now = time.monotonic()
        self.attempts = {k: v for k, v in self.attempts.items() if v[0] > now - 60}
        key = request.remote or 'unknown'
        start, count = self.attempts.get(key, (now, 0))
        if count >= 30 or (key not in self.attempts and len(self.attempts) >= 4096):
            raise web.HTTPTooManyRequests()
        self.attempts[key] = start, count + 1

    async def body(self, request):
        if request.content_length is None or request.content_length > 4096:
            raise ValueError('Invalid control request size')
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError('JSON object required')
        return value

    def device_auth(self, request):
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('Bearer '):
            raise PermissionError('Device token required')
        return self.registry.device(authorization[7:])

    def phone_auth(self, request):
        scope = request.match_info.get('device')
        # Browsers can send a legacy root cookie alongside a scoped cookie of
        # the same name. Inspect each, never let the root cookie select a device.
        row = None
        for part in request.headers.get('Cookie', '')[:8192].split(';'):
            name, _, secret = part.strip().partition('=')
            if name != COOKIE:
                continue
            try:
                candidate = self.registry.phone(secret)
            except (PermissionError, ValueError):
                continue
            if not scope or candidate['device'] == scope:
                row = candidate
                request['phone_token'] = secret
                break
        if row is None:
            raise PermissionError('Phone authorization expired or belongs to another computer')
        if request.method != 'GET':
            self.same_origin(request)
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', '').encode(), row['csrf'].encode()):
                raise PermissionError('CSRF validation failed')
        return row

    def scope(self, request):
        identifier = request.match_info.get('device')
        return '/d/' + identifier if identifier else ''

    def api_path(self, request):
        return request.path_qs[len(self.scope(request)):]

    def renew(self, request, response, phone):
        if not self.registry.renew_phone(phone) and not self.api_path(request).startswith('/api/auth'):
            return response
        response.set_cookie(COOKIE, request['phone_token'], httponly=True, secure=self.secure,
                            samesite='Strict', path=self.scope(request) + '/', max_age=PHONE_LIFETIME)
        return response

    def fail(self, device, phone=None):
        for item in list(self.pending.values()):
            if item['device'] == device and (phone is None or item['phone'] == phone):
                if not item['future'].done():
                    item['future'].set_exception(ConnectionError('Device disconnected'))
                if item.get('queue') is not None:
                    item['failed'] = True
                    try:
                        item['queue'].put_nowait(None)
                    except asyncio.QueueFull:
                        pass

    async def register(self, request):
        self.rate_limit(request)
        value = await self.body(request)
        if set(value) != {'invitation', 'name'}:
            raise ValueError('Invitation and device name required')
        return web.json_response(self.registry.register(value['invitation'], value['name']), status=201)

    async def action(self, request):
        device = self.device_auth(request)
        value = await self.body(request)
        action = value.get('action')
        result = {'ok': True}
        if action == 'status':
            result = {'deviceName': device['name'], 'online': device['id'] in self.devices,
                      'pending': self.registry.pending(device['id']), 'phones': self.registry.phones(device['id'])}
        elif action == 'pair':
            result = self.registry.pair(device['id'])
            result['url'] = self.origin + '/d/' + device['id'] + '/#pair=' + result.pop('token')
        elif action == 'approve':
            self.registry.approve(device['id'], value.get('id'), value.get('approved'))
        elif action == 'revoke-phone':
            self.registry.revoke_phone(device['id'], value.get('id'))
            self.fail(device['id'], value.get('id'))
        elif action == 'revoke':
            self.registry.revoke(device['id'])
            self.fail(device['id'])
            ws = self.devices.get(device['id'])
            if ws is not None:
                await ws.close()
        else:
            raise ValueError('Unknown device action')
        return web.json_response(result)

    async def claim(self, request):
        self.same_origin(request)
        self.rate_limit(request)
        value = await self.body(request)
        return web.json_response(self.registry.claim(value.get('token'), value.get('name')))

    async def claim_status(self, request):
        self.same_origin(request)
        value = await self.body(request)
        result, secret = self.registry.consume(value.get('claim'), request.match_info.get('device'))
        result['base'] = self.scope(request) + '/'
        response = web.json_response(result)
        if secret:
            response.set_cookie(COOKIE, secret, httponly=True, secure=self.secure,
                                samesite='Strict', path=self.scope(request) + '/', max_age=PHONE_LIFETIME)
        return response

    async def auth(self, request):
        try:
            phone = self.phone_auth(request)
        except PermissionError:
            identifier = request.match_info.get('device', '')
            return web.json_response({'authenticated': False, 'relay': True, 'passwordRequired': False,
                                      'passwordless': False, 'instanceId': 'relay:' + identifier,
                                      'online': identifier in self.devices})
        return self.renew(request, web.json_response({'authenticated': True, 'csrf': phone['csrf'], 'relay': True,
                                  'trustedDevice': True, 'passwordless': False, 'instanceId': 'relay:' + phone['device'],
                                  'computer': {'name': phone['computer_name']},
                                  'transport': 'poll', 'deviceId': phone['device'],
                                  'online': phone['device'] in self.devices}), phone)

    async def logout(self, request):
        phone = self.phone_auth(request)
        self.registry.revoke_phone(phone['device'], phone['id'])
        self.fail(phone['device'], phone['id'])
        response = web.json_response({'ok': True})
        response.del_cookie(COOKIE, path=self.scope(request) + '/')
        return response

    async def discover(self, request):
        # Opaque read-only capability. Never accepts a phone login cookie as a
        # discovery key, and never discloses endpoints without a valid grant.
        identifier = request.match_info['device']
        value = self.discovery.get(identifier)
        supplied = request.headers.get('X-Discovery-Key', '')
        if (len(supplied) != 64 or identifier not in self.devices or not self.registry.active(identifier)
                or value is None or hashlib.sha256(supplied.encode()).hexdigest() not in value['grants']):
            raise PermissionError('Address discovery unavailable')
        return web.json_response({'deviceId': identifier, 'endpoints': value['endpoints'],
                                  'relay': self.origin + '/d/' + identifier})

    async def websocket(self, request):
        device = self.device_auth(request)
        if request.headers.get('Origin'):
            raise PermissionError('Device connection cannot originate in a browser')
        if len(self.devices) >= 200 and device['id'] not in self.devices:
            raise web.HTTPServiceUnavailable(reason='Online device limit reached')
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=MAX_FRAME, compress=False)
        await ws.prepare(request)
        old = self.devices.get(device['id'])
        self.devices[device['id']] = ws
        self.discovery.pop(device['id'], None)
        self.protocols[device['id']] = request.query.get('protocol') == '2'
        if old is not None:
            self.fail(device['id'])
            await old.close()
        try:
            async for msg in ws:
                if msg.type not in (WSMsgType.TEXT, WSMsgType.BINARY):
                    break
                if not self.registry.active(device['id']):
                    break
                try:
                    if msg.type == WSMsgType.BINARY:
                        identifier, chunk = unpack(msg.data)
                        item = self.pending.get(identifier)
                        if item is None:
                            continue
                        if item['device'] != device['id'] or item['ws'] is not ws or item.get('queue') is None or item.get('ended'):
                            raise ValueError('Unexpected download chunk')
                        item['received'] += len(chunk)
                        if item['received'] > item['length']:
                            raise ValueError('Download exceeds declared length')
                        item['queue'].put_nowait(chunk)
                        continue
                    value = json.loads(msg.data)
                    if value.get('type') == 'discovery':
                        endpoints, grants = value.get('endpoints'), value.get('grants')
                        if not isinstance(endpoints, list) or len(endpoints) > 16 or not isinstance(grants, list) or len(grants) > 512:
                            raise ValueError('Invalid discovery metadata')
                        for endpoint in endpoints:
                            p = urlsplit(endpoint)
                            if len(endpoint) > 2048 or p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:
                                raise ValueError('Invalid discovery endpoint')
                        if any(not isinstance(g, str) or not re.fullmatch('[a-f0-9]{64}', g) for g in grants):
                            raise ValueError('Invalid discovery grant')
                        self.discovery[device['id']] = {'endpoints': endpoints, 'grants': set(grants)}
                        continue
                    item = self.pending.get(value.get('id'))
                    if item is None or item['device'] != device['id'] or item['ws'] is not ws:
                        continue
                    if value.get('type') == 'abort' and item.get('queue') is not None:
                        item['failed'] = True
                        item['queue'].put_nowait(None)
                        continue
                    if value.get('type') == 'end':
                        if item.get('queue') is None or item.get('ended') or item['received'] != item['length']:
                            raise ValueError('Incomplete download')
                        item['ended'] = True
                        item['queue'].put_nowait(None)
                        continue
                    if item['future'].done():
                        continue
                    status = value.get('status')
                    if type(status) is not int or not 200 <= status <= 599 or 300 <= status < 400:
                        raise ValueError('Invalid upstream status')
                    streaming = value.get('type') == 'start'
                    if streaming:
                        length = value.get('length')
                        if not item['stream'] or type(length) is not int or not 0 <= length <= 9007199254740991:
                            raise ValueError('Invalid download length')
                        item.update(queue=asyncio.Queue(maxsize=WINDOW + 1), length=length, received=0)
                    body = b'' if streaming else decode(value.get('body'))
                    mime = content_type(value.get('contentType'))
                    if self.buffered + len(body) > 128 * 1024 * 1024:
                        raise ValueError('Relay response buffer is full')
                    self.buffered += len(body)
                    item['response_size'] = len(body)
                    disposition = value.get('disposition', '')
                    if not isinstance(disposition, str) or len(disposition) > 4096 or '\r' in disposition or '\n' in disposition:
                        raise ValueError('Invalid download header')
                    headers = download_headers(value.get('headers', {}), response=True)
                    item['future'].set_result((status, body, mime, disposition, headers))
                except (ValueError, TypeError, AttributeError, asyncio.QueueFull):
                    break
        finally:
            if self.devices.get(device['id']) is ws:
                del self.devices[device['id']]
                self.protocols.pop(device['id'], None)
                self.discovery.pop(device['id'], None)
                self.fail(device['id'])
            await ws.close()
        return ws

    async def forward(self, request):
        phone = self.phone_auth(request)
        length = request.content_length or 0
        path = self.api_path(request)
        try:
            validate(request.method, path, length)
        except PermissionError as exc:
            # A valid login does not expire when a capability is unavailable.
            raise web.HTTPForbidden(reason=str(exc)) from None
        if request.headers.get('Transfer-Encoding') or (request.method == 'POST' and request.content_length is None):
            raise ValueError('A bounded Content-Length is required')
        ws = self.devices.get(phone['device'])
        if ws is None:
            return web.json_response({'error': '电脑离线，请保持 Codex 和电脑网关运行。', 'code': 'device_offline'}, status=503)
        if len(self.pending) >= 64 or sum(i['device'] == phone['device'] for i in self.pending.values()) >= 12:
            raise web.HTTPTooManyRequests()
        if self.buffered + length > 128 * 1024 * 1024:
            raise web.HTTPTooManyRequests()
        identifier = token()
        future = asyncio.get_running_loop().create_future()
        item = {'device': phone['device'], 'phone': phone['id'], 'ws': ws, 'future': future, 'response_size': 0}
        item['stream'] = request.method == 'GET' and self.protocols.get(phone['device'], False)
        self.pending[identifier] = item
        self.buffered += length
        try:
            body = await request.read()
            mime = content_type(request.headers.get('Content-Type', 'application/json'))
            headers = download_headers({key: ','.join(request.headers.getall(key)) for key in
                                        ('Range', 'If-Range') if key in request.headers})
            await asyncio.wait_for(ws.send_json({'id': identifier, 'phone': phone['id'], 'method': request.method,
                                                'phones': [p['id'] for p in self.registry.phones(phone['device'])],
                                                'path': path, 'body': base64.b64encode(body).decode(),
                                                'contentType': mime, 'headers': headers, 'stream': item['stream']}), timeout=10)
            status, response, mime, disposition, range_headers = await asyncio.wait_for(future, timeout=self.timeout)
            # Revocation also applies to a response already in flight.
            self.phone_auth(request)
            headers = {'Content-Type': mime, **range_headers}
            if disposition:
                headers['Content-Disposition'] = disposition
            if item.get('queue') is not None:
                result = web.StreamResponse(status=status, headers={**headers, 'Content-Length': str(item['length']),
                                            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                            'Referrer-Policy': 'no-referrer'})
                self.renew(request, result, phone)
                await result.prepare(request)
                try:
                    while True:
                        chunk = await asyncio.wait_for(item['queue'].get(), timeout=self.timeout)
                        self.phone_auth(request)
                        if item.get('failed'):
                            raise ConnectionError('Device disconnected')
                        if chunk is None:
                            break
                        await result.write(chunk)
                        await asyncio.wait_for(ws.send_json({'type': 'ack', 'id': identifier}), timeout=10)
                    await result.write_eof()
                except (asyncio.TimeoutError, ConnectionError, OSError, PermissionError):
                    # Never append a JSON error to a partially delivered file.
                    if request.transport is not None:
                        request.transport.close()
                return result
            return self.renew(request, web.Response(status=status, body=response, headers=headers), phone)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            unknown = request.method == 'POST'
            return web.json_response({'error': '连接中断，操作结果待确认，请先查看状态，勿重复提交。' if unknown else '电脑连接中断，请稍后刷新。',
                                      'code': 'outcome_unknown' if unknown else 'device_offline'}, status=504 if unknown else 503)
        finally:
            self.pending.pop(identifier, None)
            if item['stream'] and not ws.closed:
                try:
                    await asyncio.wait_for(ws.send_json({'type': 'cancel', 'id': identifier}), timeout=2)
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    pass
            self.buffered -= length + item['response_size']
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def page(self, request):
        scope = self.scope(request)
        path = request.path[len(scope):]
        if path == '/relay/admin':
            raise web.HTTPFound('/relay/admin/')
        if path in ('/relay/', '/relay/pair.js', '/relay/mode.js'):
            name = {'/relay/': 'index.html', '/relay/pair.js': 'pair.js', '/relay/mode.js': 'mode.js'}[path]
            mime = 'text/html' if name.endswith('.html') else 'text/javascript'
            return web.Response(body=(ROOT/'relay'/'web'/name).read_bytes(), content_type=mime)
        # Serve the same v2 web assets; execution and authentication stay on devices.
        from bridge.api.assets import STATIC, FONT_ROUTE, asset_bytes
        if path in ('/', '/index.html'):
            try:
                self.phone_auth(request)
            except PermissionError:
                raise web.HTTPFound(scope + '/relay/')
            html = (ROOT/'web/index.html').read_text(encoding='utf-8')
            html = html.replace('<head>', '<head><script src="/relay/mode.js"></script>', 1)
            return web.Response(text=html, content_type='text/html')
        if path in STATIC:
            _, mime = STATIC[path]
            body = asset_bytes(ROOT/'web', path)
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            headers = {'Content-Type': mime, 'ETag': etag, 'Cache-Control': 'public, max-age=0, must-revalidate'}
            if request.headers.get('If-None-Match') == etag:
                return web.Response(status=304, headers=headers)
            response = web.Response(body=body, headers=headers)
            if len(body) > 1024 and mime.startswith(('text/', 'application/javascript', 'application/json')):
                response.enable_compression()
            return response
        match = FONT_ROUTE.fullmatch(path)
        if match:
            return web.FileResponse(ROOT/'web/vendor/katex/fonts'/match[1])
        raise web.HTTPNotFound()

    async def maintenance(self):
        while True:
            await asyncio.sleep(5)
            for identifier, ws in list(self.devices.items()):
                if not self.registry.active(identifier):
                    self.fail(identifier)
                    await ws.close()


STATE = web.AppKey('relay', Relay)


def create_app(directory, public_origin, test_http=False, timeout=90):
    state = Relay(directory, public_origin, test_http, timeout)

    @web.middleware
    async def boundary(request, handler):
        try:
            if request.host != state.host:
                raise PermissionError('Unrecognized relay host')
            supplied = request.headers.get('Origin')
            if supplied and supplied != state.origin:
                raise PermissionError('Unrecognized origin')
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise PermissionError('Cross-site access denied')
            if any(value.strip().lower() != 'identity' for value in request.headers.getall('Content-Encoding', [])):
                raise web.HTTPUnsupportedMediaType(reason='Compressed request bodies are not supported')
            response = await handler(request)
        except PermissionError as exc:
            response = web.json_response({'error': str(exc)}, status=401)
        except (ValueError, TypeError, KeyError) as exc:
            response = web.json_response({'error': str(exc)}, status=400)
        except web.HTTPException as exc:
            if 300 <= exc.status < 400:
                response = exc
            else:
                response = web.json_response({'error': exc.reason}, status=exc.status)
        if not response.prepared:
            defaults = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                     'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                                     'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}
            for key, value in defaults.items():
                response.headers.setdefault(key, value)
        return response

    # Content-Length and the shared buffer budget must describe the same bytes.
    # Disable decompression before parsing, including unauthenticated requests.
    app = web.Application(middlewares=[boundary], client_max_size=MAX_BODY + 1,
                          handler_args={'auto_decompress': False})
    app[STATE] = state
    from .admin import Admin
    Admin(state).routes(app)
    app.router.add_post('/relay/register', state.register)
    app.router.add_post('/relay/device', state.action)
    app.router.add_get('/relay/device/ws', state.websocket)
    app.router.add_post('/relay/claim', state.claim)
    app.router.add_post('/relay/claim-status', state.claim_status)
    app.router.add_get('/relay/discover/{device:[a-f0-9]{32}}', state.discover)
    scope = '/d/{device:[a-f0-9]{32}}'
    app.router.add_post(scope + '/relay/claim', state.claim)
    app.router.add_post(scope + '/relay/claim-status', state.claim_status)
    app.router.add_get(scope + '/api/auth', state.auth)
    app.router.add_post(scope + '/api/logout', state.logout)
    app.router.add_route('*', scope + '/api/{path:.*}', state.forward)
    app.router.add_get(scope + '/{path:.*}', state.page)
    app.router.add_get('/api/auth', state.auth)
    app.router.add_post('/api/logout', state.logout)
    app.router.add_route('*', '/api/{path:.*}', state.forward)
    async def health(request):
        return web.json_response({'ok': True, 'service': 'codex-bridge-relay'})
    app.router.add_get('/health', health)
    app.router.add_get('/{path:.*}', state.page)

    async def shutdown(app):
        for identifier, ws in list(state.devices.items()):
            state.fail(identifier)
            await ws.close()

    async def lifecycle(app):
        task = asyncio.create_task(state.maintenance())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for ws in list(state.devices.values()):
            await ws.close()
        state.registry.close()

    app.cleanup_ctx.append(lifecycle)
    app.on_shutdown.append(shutdown)
    return app
