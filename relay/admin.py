"""Server administration, independent of device and phone authorization."""
import hmac
import time
from pathlib import Path

from aiohttp import web

COOKIE = 'codex_relay_admin'
PATH = '/relay/admin/'


class Admin:
    def __init__(self, relay):
        self.relay = relay

    def authenticate(self, request):
        session = self.relay.registry.admin_session(request.cookies.get(COOKIE, ''))
        if request.method != 'GET':
            self.relay.same_origin(request)
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', '').encode(), session['csrf'].encode()):
                raise PermissionError('CSRF validation failed')
        return session

    async def session(self, request):
        try:
            session = self.authenticate(request)
        except PermissionError:
            return web.json_response({'authenticated': False, 'configured': self.relay.registry.admin_configured()})
        return web.json_response({'authenticated': True, 'csrf': session['csrf']})

    async def login(self, request):
        self.relay.same_origin(request)
        self.relay.rate_limit(request)
        value = await self.relay.body(request)
        secret, csrf = self.relay.registry.admin_login(value.get('key'))
        # Re-login replaces this browser's previous session without retaining it.
        self.relay.registry.admin_logout(request.cookies.get(COOKIE, ''))
        response = web.json_response({'authenticated': True, 'csrf': csrf})
        response.set_cookie(COOKIE, secret, httponly=True, secure=self.relay.secure,
                            samesite='Strict', path=PATH, max_age=8 * 3600)
        return response

    async def logout(self, request):
        self.authenticate(request)
        self.relay.registry.admin_logout(request.cookies.get(COOKIE, ''))
        response = web.json_response({'ok': True})
        response.del_cookie(COOKIE, path=PATH)
        return response

    async def overview(self, request):
        self.authenticate(request)
        devices = self.relay.registry.devices()
        for device in devices:
            device['online'] = not device['revoked'] and device['id'] in self.relay.devices
        return web.json_response({'origin': self.relay.origin, 'devices': devices,
                                  'invitations': self.relay.registry.invitations(), 'now': time.time()})

    async def invite(self, request):
        self.authenticate(request)
        value = await self.relay.body(request)
        return web.json_response(self.relay.registry.invite(value.get('owner'), value.get('hours', 24)), status=201)

    async def revoke(self, request):
        self.authenticate(request)
        value = await self.relay.body(request)
        identifier = value.get('deviceId')
        if not isinstance(identifier, str) or not self.relay.registry.active(identifier):
            raise ValueError('设备不存在或已撤销')
        self.relay.registry.revoke(identifier)
        self.relay.fail(identifier)
        ws = self.relay.devices.get(identifier)
        if ws is not None:
            await ws.close()
        return web.json_response({'ok': True})

    async def page(self, request):
        name = 'admin.js' if request.path.endswith('/admin.js') else 'admin.html'
        return web.Response(body=(Path(__file__).parent/'web'/name).read_bytes(),
                            content_type='text/javascript' if name.endswith('.js') else 'text/html')

    def routes(self, app):
        app.router.add_get(PATH, self.page)
        app.router.add_get(PATH+'admin.js', self.page)
        app.router.add_get(PATH+'session', self.session)
        app.router.add_post(PATH+'login', self.login)
        app.router.add_post(PATH+'logout', self.logout)
        app.router.add_get(PATH+'overview', self.overview)
        app.router.add_post(PATH+'invite', self.invite)
        app.router.add_post(PATH+'revoke', self.revoke)
