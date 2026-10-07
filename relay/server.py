"""Headless HTTPS-edge relay. Devices execute tasks; this service only routes."""
import asyncio
import base64
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from .protocol import MAX_BODY, MAX_FRAME, origin, validate, decode, content_type
from .registry import Registry, token

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
        row = self.registry.phone(request.cookies.get(COOKIE, ''))
        if request.method != 'GET':
            self.same_origin(request)
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', '').encode(), row['csrf'].encode()):
                raise PermissionError('CSRF validation failed')
        return row

    def fail(self, device, phone=None):
        for item in list(self.pending.values()):
            if item['device'] == device and (phone is None or item['phone'] == phone):
                if not item['future'].done():
                    item['future'].set_exception(ConnectionError('Device disconnected'))

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
            result['url'] = self.origin + '/#pair=' + result.pop('token')
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
        result, secret = self.registry.consume(value.get('claim'))
        response = web.json_response(result)
        if secret:
            response.set_cookie(COOKIE, secret, httponly=True, secure=self.secure,
                                samesite='Strict', path='/', max_age=30 * 86400)
        return response

    async def auth(self, request):
        try:
            phone = self.phone_auth(request)
        except PermissionError:
            return web.json_response({'authenticated': False, 'relay': True, 'passwordRequired': False})
        return web.json_response({'authenticated': True, 'csrf': phone['csrf'], 'relay': True,
                                  'transport': 'poll', 'deviceId': phone['device'],
                                  'online': phone['device'] in self.devices})

    async def logout(self, request):
        phone = self.phone_auth(request)
        self.registry.revoke_phone(phone['device'], phone['id'])
        self.fail(phone['device'], phone['id'])
        response = web.json_response({'ok': True})
        response.del_cookie(COOKIE, path='/')
        return response

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
        if old is not None:
            self.fail(device['id'])
            await old.close()
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    break
                if not self.registry.active(device['id']):
                    break
                try:
                    value = json.loads(msg.data)
                    item = self.pending.get(value.get('id'))
                    if item is None or item['device'] != device['id'] or item['ws'] is not ws:
                        continue
                    if item['future'].done():
                        continue
                    status = value.get('status')
                    if type(status) is not int or not 200 <= status <= 599 or 300 <= status < 400:
                        raise ValueError('Invalid upstream status')
                    body = decode(value.get('body'))
                    mime = content_type(value.get('contentType'))
                    if self.buffered + len(body) > 128 * 1024 * 1024:
                        raise ValueError('Relay response buffer is full')
                    self.buffered += len(body)
                    item['response_size'] = len(body)
                    disposition = value.get('disposition', '')
                    if not isinstance(disposition, str) or len(disposition) > 4096 or '\r' in disposition or '\n' in disposition:
                        raise ValueError('Invalid download header')
                    item['future'].set_result((status, body, mime, disposition))
                except (ValueError, TypeError, AttributeError):
                    break
        finally:
            if self.devices.get(device['id']) is ws:
                del self.devices[device['id']]
                self.fail(device['id'])
            await ws.close()
        return ws

    async def forward(self, request):
        phone = self.phone_auth(request)
        length = request.content_length or 0
        validate(request.method, request.path_qs, length)
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
        self.pending[identifier] = item
        self.buffered += length
        try:
            body = await request.read()
            mime = content_type(request.headers.get('Content-Type', 'application/json'))
            await asyncio.wait_for(ws.send_json({'id': identifier, 'phone': phone['id'], 'method': request.method,
                                                'phones': [p['id'] for p in self.registry.phones(phone['device'])],
                                                'path': request.path_qs, 'body': base64.b64encode(body).decode(),
                                                'contentType': mime}), timeout=10)
            status, response, mime, disposition = await asyncio.wait_for(future, timeout=self.timeout)
            # Revocation also applies to a response already in flight.
            self.phone_auth(request)
            headers = {'Content-Type': mime}
            if disposition:
                headers['Content-Disposition'] = disposition
            return web.Response(status=status, body=response, headers=headers)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            unknown = request.method == 'POST'
            return web.json_response({'error': '连接中断，操作结果待确认，请先查看状态，勿重复提交。' if unknown else '电脑连接中断，请稍后刷新。',
                                      'code': 'outcome_unknown' if unknown else 'device_offline'}, status=504 if unknown else 503)
        finally:
            self.pending.pop(identifier, None)
            self.buffered -= length + item['response_size']
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def page(self, request):
        path = request.path
        if path == '/relay/admin':
            raise web.HTTPFound('/relay/admin/')
        if path in ('/relay/', '/relay/pair.js', '/relay/mode.js'):
            name = {'/relay/': 'index.html', '/relay/pair.js': 'pair.js', '/relay/mode.js': 'mode.js'}[path]
            mime = 'text/html' if name.endswith('.html') else 'text/javascript'
            return web.Response(body=(ROOT/'relay'/'web'/name).read_bytes(), content_type=mime)
        # Serve the same v2 web assets; execution and authentication stay on devices.
        from bridge.httpd import STATIC, FONT_ROUTE
        if path == '/':
            try:
                self.phone_auth(request)
            except PermissionError:
                raise web.HTTPFound('/relay/')
            html = (ROOT/'web/index.html').read_text(encoding='utf-8')
            html = html.replace('<head>', '<head><script src="/relay/mode.js"></script>', 1)
            return web.Response(text=html, content_type='text/html')
        if path in STATIC:
            name, mime = STATIC[path]
            return web.Response(body=(ROOT/'web'/name).read_bytes(), headers={'Content-Type': mime})
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
            response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                     'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                                     'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    app = web.Application(middlewares=[boundary], client_max_size=MAX_BODY + 1)
    app[STATE] = state
    from .admin import Admin
    Admin(state).routes(app)
    app.router.add_post('/relay/register', state.register)
    app.router.add_post('/relay/device', state.action)
    app.router.add_get('/relay/device/ws', state.websocket)
    app.router.add_post('/relay/claim', state.claim)
    app.router.add_post('/relay/claim-status', state.claim_status)
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
