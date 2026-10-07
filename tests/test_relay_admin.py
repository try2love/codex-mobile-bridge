import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import aiohttp
    from aiohttp.test_utils import TestServer, TestClient
    from relay.server import create_app, STATE, COOKIE as PHONE_COOKIE
    from relay.admin import COOKIE
except ImportError:
    aiohttp = None

ROOT = Path(__file__).resolve().parents[1]
(ROOT/'.tmp').mkdir(exist_ok=True)


@unittest.skipUnless(aiohttp, 'optional requirements-relay.txt not installed')
class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.app = create_app(self.temp.name, 'http://127.0.0.1:1', test_http=True)
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.url = str(self.server.make_url('')).rstrip('/')
        self.state = self.app[STATE]
        self.state.origin = self.url
        self.state.host = self.url.removeprefix('http://')
        self.client = TestClient(self.server, cookie_jar=aiohttp.DummyCookieJar())
        await self.client.start_server()
        self.r = self.state.registry

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def login(self, key=None):
        key = key or self.r.rotate_admin_token()
        response = await self.client.post('/relay/admin/login', json={'key': key}, headers={'Origin': self.url})
        self.assertEqual(response.status, 200)
        value = await response.json()
        cookie = response.headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Path=/relay/admin/', cookie)
        self.assertNotIn(key, cookie)
        return {'Cookie': COOKIE+'='+response.cookies[COOKIE].value,
                'Origin': self.url, 'X-CSRF-Token': value['csrf']}

    async def test_disabled_by_default_and_no_public_data_or_initialization(self):
        self.r.invite('private-owner')
        response = await self.client.get('/relay/admin/session')
        self.assertEqual(await response.json(), {'authenticated': False, 'configured': False})
        for path, body in [('overview', None), ('invite', {'owner': 'bad'}), ('revoke', {'deviceId': 'bad'}), ('logout', {})]:
            response = await (self.client.get('/relay/admin/'+path) if body is None else self.client.post('/relay/admin/'+path, json=body))
            self.assertEqual(response.status, 401)
            self.assertNotIn('private-owner', await response.text())
        response = await self.client.post('/relay/admin/login', json={'key': 'anything'}, headers={'Origin': self.url})
        self.assertEqual(response.status, 401)
        response = await self.client.get('/relay/admin/')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    async def test_terminal_invitation_web_invitation_device_online_and_revoke(self):
        headers = await self.login()
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, '-B', '-m', 'relay', '--data-dir', self.temp.name, 'invite', '--owner', 'CLI Alice'],
            cwd=ROOT, env={**os.environ, 'DISPLAY': '', 'WAYLAND_DISPLAY': ''}, capture_output=True, text=True, check=True)
        cli_invite = json.loads(result.stdout)['invitation']
        response = await self.client.post('/relay/admin/invite', headers=headers, json={'owner': 'Web Bob', 'hours': 1})
        self.assertEqual(response.status, 201)
        invitation = (await response.json())['invitation']
        device = self.r.register(invitation, 'Bob computer')
        phone_pair = self.r.pair(device['deviceId'])
        claim = self.r.claim(phone_pair['token'], 'Bob phone')['claim']
        self.r.approve(device['deviceId'], phone_pair['id'], True)
        _, phone_token = self.r.consume(claim)
        ws = await self.client.ws_connect('/relay/device/ws', headers={'Authorization': 'Bearer '+device['deviceToken']})
        try:
            response = await self.client.get('/relay/admin/overview', headers=headers)
            body = await response.json()
            self.assertTrue(body['devices'][0]['online'])
            self.assertEqual({r['owner'] for r in body['invitations']}, {'CLI Alice', 'Web Bob'})
            for secret in (invitation, cli_invite, device['deviceToken'], phone_token):
                self.assertNotIn(secret, json.dumps(body))
            self.assertTrue(next(r for r in body['invitations'] if r['owner'] == 'Web Bob')['used'])
            with self.assertRaises(PermissionError): self.r.register(invitation, 'replay')
            # Read the close concurrently so the WebSocket close handshake completes.
            message = asyncio.create_task(ws.receive())
            response = await self.client.post('/relay/admin/revoke', json={'deviceId': device['deviceId']}, headers=headers)
            self.assertEqual(response.status, 200)
            await asyncio.wait_for(message, 2)
            with self.assertRaises(PermissionError): self.r.phone(phone_token)
            with self.assertRaises(PermissionError): self.r.device(device['deviceToken'])
            body = await (await self.client.get('/relay/admin/overview', headers=headers)).json()
            self.assertFalse(body['devices'][0]['online'])
            self.assertEqual(body['devices'][0]['revoked'], 1)
        finally:
            await ws.close()

    async def test_phone_device_and_admin_credentials_are_not_interchangeable(self):
        headers = await self.login()
        device = self.r.register(self.r.invite('Alice')['invitation'], 'Alice computer')
        pair = self.r.pair(device['deviceId'])
        claim = self.r.claim(pair['token'], 'Phone')['claim']
        self.r.approve(device['deviceId'], pair['id'], True)
        _, phone = self.r.consume(claim)
        for other in ({'Authorization': 'Bearer '+device['deviceToken']}, {'Cookie': PHONE_COOKIE+'='+phone}, {'Cookie': COOKIE+'='+phone}):
            response = await self.client.get('/relay/admin/overview', headers=other)
            self.assertEqual(response.status, 401)
        response = await self.client.get('/api/sessions', headers=headers)
        self.assertEqual(response.status, 401)
        response = await self.client.post('/relay/device', json={'action': 'status'}, headers=headers)
        self.assertEqual(response.status, 401)

    async def test_admin_writes_require_origin_csrf_and_correct_host(self):
        headers = await self.login()
        for changes in ({'Origin': None}, {'Origin': 'https://evil.example'}, {'X-CSRF-Token': None}, {'X-CSRF-Token': 'wrong'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            altered = {k: v for k, v in {**headers, **changes}.items() if v is not None}
            for path, body in [('invite', {'owner': 'bad'}), ('revoke', {'deviceId': 'bad'}), ('logout', {})]:
                response = await self.client.post('/relay/admin/'+path, json=body, headers=altered)
                self.assertEqual(response.status, 401)
        self.assertEqual(self.r.invitations(), [])
        response = await self.client.post('/relay/admin/login', json={'key': 'bad'})
        self.assertEqual(response.status, 401)
        self.state.secure = True
        response = await self.client.post('/relay/admin/login', json={'key': self.r.rotate_admin_token()}, headers={'Origin': self.url})
        self.assertIn('Secure', response.headers['Set-Cookie'])

    async def test_cli_rotation_session_expiry_and_logout(self):
        key = self.r.rotate_admin_token()
        headers = await self.login(key)
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, '-B', '-m', 'relay', '--data-dir', self.temp.name, 'admin-token'],
            cwd=ROOT, env={**os.environ, 'DISPLAY': '', 'WAYLAND_DISPLAY': ''}, capture_output=True, text=True, check=True)
        new_key = json.loads(result.stdout)['adminToken']
        response = await self.client.get('/relay/admin/overview', headers=headers)
        self.assertEqual(response.status, 401)
        response = await self.client.post('/relay/admin/login', json={'key': key}, headers={'Origin': self.url})
        self.assertEqual(response.status, 401)
        headers = await self.login(new_key)
        cookie_secret = headers['Cookie'].split('=', 1)[1]
        dump = '\n'.join(self.r.db.iterdump())
        self.assertNotIn(new_key, dump)
        self.assertNotIn(cookie_secret, dump)
        response = await self.client.post('/relay/admin/logout', json={}, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        response = await self.client.get('/relay/admin/overview', headers=headers)
        self.assertEqual(response.status, 401)
        headers = await self.login(new_key)
        with self.r.db: self.r.db.execute('UPDATE admin_sessions SET expires=0')
        response = await self.client.get('/relay/admin/overview', headers=headers)
        self.assertEqual(response.status, 401)

    async def test_login_rate_limit_and_invitation_validation(self):
        headers = await self.login()
        for hours in (True, 0, 721, 1.5, '24'):
            response = await self.client.post('/relay/admin/invite', json={'owner': 'Alice', 'hours': hours}, headers=headers)
            self.assertEqual(response.status, 400)
        self.assertEqual(self.r.invitations(), [])
        for _ in range(29):
            response = await self.client.post('/relay/admin/login', json={'key': 'wrong'}, headers={'Origin': self.url})
            self.assertEqual(response.status, 401)
        response = await self.client.post('/relay/admin/login', json={'key': 'wrong'}, headers={'Origin': self.url})
        self.assertEqual(response.status, 429)


if __name__ == '__main__':
    unittest.main()
