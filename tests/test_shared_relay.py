import asyncio
import base64
import concurrent.futures
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from relay.protocol import validate, origin, MAX_BODY
from relay.registry import Registry

ROOT = Path(__file__).resolve().parents[1]
(ROOT/'.tmp').mkdir(exist_ok=True)
THREAD = '11111111-1111-4111-8111-111111111111'


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.r = Registry(self.temp.name)

    def tearDown(self):
        self.r.close()
        self.temp.cleanup()

    def register(self, owner='Alice'):
        return self.r.register(self.r.invite(owner)['invitation'], owner+' computer')

    def test_invitation_consumed_atomically_across_process_connections(self):
        secret = self.r.invite('Alice')['invitation']
        def register(_):
            r = Registry(self.temp.name)
            try:
                return r.register(secret, 'computer')
            except PermissionError:
                return None
            finally:
                r.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(register, range(8)))
        self.assertEqual(sum(v is not None for v in values), 1)

    def test_pair_approval_ownership_replay_and_revocation(self):
        a, b = self.register(), self.register('Bob')
        pair = self.r.pair(a['deviceId'])
        claim = self.r.claim(pair['token'], 'phone')['claim']
        self.assertEqual(self.r.consume(claim), ({'state': 'pending'}, None))
        with self.assertRaises(PermissionError): self.r.approve(b['deviceId'], pair['id'], True)
        with self.assertRaises(PermissionError): self.r.claim(pair['token'], 'another')
        self.r.approve(a['deviceId'], pair['id'], True)
        result, secret = self.r.consume(claim)
        self.assertEqual(result['state'], 'approved')
        self.assertEqual(self.r.consume(claim), ({'state': 'used'}, None))
        phone = self.r.phone(secret)
        self.r.revoke_phone(b['deviceId'], phone['id'])
        self.assertEqual(self.r.phone(secret)['device'], a['deviceId'])
        self.r.revoke_phone(a['deviceId'], phone['id'])
        with self.assertRaises(PermissionError): self.r.phone(secret)

    def test_expiry_restart_hashes_and_admin_revoke(self):
        invite = self.r.invite('Alice')
        self.r.db.execute('UPDATE invitations SET expires=0'); self.r.db.commit()
        with self.assertRaises(PermissionError): self.r.register(invite['invitation'], 'a')
        a = self.register()
        self.assertNotIn(a['deviceToken'], str(list(self.r.db.iterdump())))
        self.r.close(); self.r = Registry(self.temp.name)
        self.assertEqual(self.r.device(a['deviceToken'])['id'], a['deviceId'])
        self.r.revoke(a['deviceId'])
        with self.assertRaises(PermissionError): self.r.device(a['deviceToken'])

    def test_protocol_v2_routes_and_management_boundary(self):
        for method, action in [('GET','workspace/download?path=a.txt'), ('POST','workspace/upload?path=a.txt'), ('POST','terminal'), ('POST','side-chat'), ('GET','catalog?kind=skills'), ('POST','permissions')]:
            validate(method, '/api/sessions/'+THREAD+'/'+action)
        for path in ['/api/accounts', '/api/account/reset', '/api/login', '/api/pair', 'https://example.com/api/sessions', '/api/sessions/'+THREAD+'/events', '/api/%73essions', '/api/../admin']:
            with self.assertRaises((ValueError, PermissionError)): validate('POST', path)
        with self.assertRaises(ValueError): validate('POST', '/api/sessions', MAX_BODY+1)
        with self.assertRaises(ValueError): origin('http://example.com')


try:
    import aiohttp
    from aiohttp.test_utils import TestServer, TestClient
    from relay.server import create_app, COOKIE, STATE
    from bridge.shared_relay import Connector, Controller, save_config
except ImportError:
    aiohttp = None


@unittest.skipUnless(aiohttp, 'optional requirements-relay.txt not installed')
class RelayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.app = create_app(Path(self.temp.name)/'relay', 'http://127.0.0.1:1', test_http=True, timeout=.25)
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.url = str(self.server.make_url('')).rstrip('/')
        self.state = self.app[STATE]; self.state.origin = self.url
        self.state.host = self.url.removeprefix('http://')
        self.client = TestClient(self.server, cookie_jar=aiohttp.DummyCookieJar())
        await self.client.start_server()
        self.connectors = []
        self.sockets = []
        self.created = []

    async def asyncTearDown(self):
        for connector in self.connectors:
            await asyncio.to_thread(connector.close)
        for ws in self.sockets:
            await ws.close()
        await self.client.close()
        self.temp.cleanup()

    async def device(self, name):
        invitation = self.state.registry.invite(name)['invitation']
        response = await self.client.post('/relay/register', json={'invitation': invitation, 'name': name})
        self.assertEqual(response.status, 201)
        return await response.json()

    async def action(self, device, action, **value):
        return await self.client.post('/relay/device', json={'action': action, **value}, headers={'Authorization': 'Bearer '+device['deviceToken']})

    async def phone(self, device):
        pair = await (await self.action(device, 'pair')).json()
        response = await self.client.post('/relay/claim', json={'token': pair['url'].split('#pair=')[1], 'name': 'Phone'}, headers={'Origin': self.url})
        claim = (await response.json())['claim']
        await self.action(device, 'approve', id=pair['id'], approved=True)
        response = await self.client.post('/relay/claim-status', json={'claim': claim}, headers={'Origin': self.url})
        self.assertEqual(response.status, 200)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', response.headers['Set-Cookie'])
        return {'Cookie': COOKIE+'='+response.cookies[COOKIE].value, 'Origin': self.url,
                'X-CSRF-Token': (await response.json())['csrf']}

    async def socket(self, device):
        ws = await self.client.ws_connect('/relay/device/ws', headers={'Authorization': 'Bearer '+device['deviceToken']})
        self.sockets.append(ws)
        return ws

    async def real_connector(self, device):
        directory = Path(self.temp.name)/device['deviceId']
        save_config(directory, {**device, 'url': self.url, 'enabled': True})
        bridge = SimpleNamespace(host_errors=[], list=lambda **kw: [{'title': device['deviceName'], 'id': THREAD}])
        bridge.create_chat = lambda project, title, identifier: self.created.append((device['deviceName'], identifier)) or {'id': identifier}
        bridge.for_host = lambda host: bridge
        bridge.terminal = lambda thread, owner, action, body: {'owner': owner, 'action': action}
        gateway = SimpleNamespace(bridge=bridge, web_dir=ROOT/'web', notifications=None)
        connector = Connector(directory, gateway, test_http=True)
        connector.start(); self.connectors.append(connector)
        for _ in range(100):
            if device['deviceId'] in self.state.devices:
                return connector
            await asyncio.sleep(.02)
        self.fail('Connector did not come online')

    async def test_two_real_connectors_route_independently_and_phone_owners_are_separate(self):
        a, b = await self.device('Alice'), await self.device('Bob')
        await self.real_connector(a); await self.real_connector(b)
        pa, pb, pa2 = await self.phone(a), await self.phone(b), await self.phone(a)
        for headers, expected in [(pa,'Alice'),(pb,'Bob')]:
            response = await self.client.get('/api/sessions?deviceId='+b['deviceId'], headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['sessions'][0]['title'], expected)
        owners = []
        for headers in (pa, pa2, pa):
            r = await self.client.post('/api/sessions/'+THREAD+'/terminal', json={'action':'open'}, headers=headers)
            self.assertEqual(r.status, 200)
            owners.append((await r.json())['owner'])
        self.assertNotEqual(owners[0], owners[1]); self.assertEqual(owners[0], owners[2])
        r = await self.client.post('/api/sessions', json={'id':'submission','project':'fixture'}, headers=pa)
        self.assertEqual(r.status, 200); self.assertEqual(self.created, [('Alice','submission')])

    async def test_auth_origin_csrf_host_and_management_denials(self):
        a = await self.device('Alice'); p = await self.phone(a)
        self.assertEqual((await self.client.get('/api/sessions')).status, 401)
        self.assertEqual((await self.client.get('/api/sessions', headers={**p, 'Origin':'https://evil.test'})).status, 401)
        self.assertEqual((await self.client.post('/api/sessions', json={}, headers={**p,'X-CSRF-Token':'bad'})).status, 401)
        self.assertEqual((await self.client.get('/api/sessions', headers={**p,'Host':'evil.test'})).status, 401)
        self.assertEqual((await self.client.get('/api/accounts', headers=p)).status, 401)
        self.assertEqual((await self.client.get('/api/auth', headers=p)).status, 200)

    async def test_wrong_device_cannot_supply_a_response(self):
        a,b = await self.device('Alice'),await self.device('Bob')
        wa,wb = await self.socket(a),await self.socket(b)
        p = await self.phone(a)
        task = asyncio.create_task(self.client.get('/api/sessions', headers=p))
        msg = await wa.receive_json()
        body = base64.b64encode(b'{"device":"Bob"}').decode()
        await wb.send_json({'id':msg['id'], 'status':200,'body':body,'contentType':'application/json'})
        await asyncio.sleep(.03); self.assertFalse(task.done())
        body = base64.b64encode(b'{"device":"Alice"}').decode()
        await wa.send_json({'id':msg['id'], 'status':200,'body':body,'contentType':'application/json'})
        self.assertEqual((await (await task).json())['device'], 'Alice')

    async def test_disconnect_write_is_unknown_and_not_replayed(self):
        a = await self.device('Alice'); w = await self.socket(a); p = await self.phone(a)
        task = asyncio.create_task(self.client.post('/api/sessions', json={'id':'same'}, headers=p))
        await w.receive_json(); await w.close()
        result = await task
        self.assertEqual(result.status, 504)
        self.assertEqual((await result.json())['code'], 'outcome_unknown')
        w2 = await self.socket(a)
        with self.assertRaises(asyncio.TimeoutError): await asyncio.wait_for(w2.receive_json(), .05)
        self.assertEqual(self.state.pending, {})

    async def test_revocation_applies_to_inflight_responses_and_reconnect(self):
        a = await self.device('Alice'); w = await self.socket(a); p = await self.phone(a)
        task = asyncio.create_task(self.client.get('/api/sessions', headers=p))
        await w.receive_json()
        phone = self.state.registry.phones(a['deviceId'])[0]
        await self.action(a,'revoke-phone',id=phone['id'])
        self.assertNotEqual((await task).status,200)
        self.assertEqual((await self.client.get('/api/sessions', headers=p)).status,401)
        await self.action(a,'revoke')
        with self.assertRaises(aiohttp.WSServerHandshakeError): await self.socket(a)

    async def test_controller_registration_disable_revoke_and_no_secret_status(self):
        directory = Path(self.temp.name)/'controller'
        c = Controller(directory,test_http=True)
        secret = self.state.registry.invite('owner')['invitation']
        with self.assertRaises(ValueError):
            await asyncio.to_thread(c.control, {'action':'register','url':self.url,'invitation':secret,'name':'a'})
        result = await asyncio.to_thread(c.control, {'action':'register','url':self.url,'invitation':secret,'name':'a','consent':True})
        self.assertNotIn('deviceToken',result)
        saved = json.loads((directory/'shared-relay.json').read_text())
        self.assertNotIn(secret,str(saved))
        result = await asyncio.to_thread(c.control, {'action':'status'})
        self.assertNotIn(saved['deviceToken'],str(result))
        await asyncio.to_thread(c.control, {'action':'disable'})
        self.assertFalse(json.loads((directory/'shared-relay.json').read_text())['enabled'])
        await asyncio.to_thread(c.control, {'action':'revoke'})
        self.assertEqual(json.loads((directory/'shared-relay.json').read_text()),{})

    async def test_headless_static_app_and_native_compatible_pair_url(self):
        a = await self.device('Alice'); p = await self.phone(a)
        response = await self.client.get('/',headers=p)
        html = await response.text()
        self.assertIn('/relay/mode.js',html); self.assertIn('workbench.js',html)
        pair = await (await self.action(a,'pair')).json()
        self.assertTrue(pair['url'].startswith(self.url+'/#pair='))
        self.assertEqual((await self.client.get('/relay/')).status,200)

    async def test_binary_forwarding_preserves_download_and_strips_upstream_cookies(self):
        a = await self.device('Alice'); w = await self.socket(a); p = await self.phone(a)
        task = asyncio.create_task(self.client.get('/api/sessions/'+THREAD+'/workspace/download?path=a.bin',headers=p))
        msg = await w.receive_json()
        data = bytes(range(256)) * 64
        await w.send_json({'id':msg['id'],'status':200,'body':base64.b64encode(data).decode(),
                          'contentType':'application/octet-stream','disposition':'attachment; filename="a.bin"',
                          'Set-Cookie':'other=secret'})
        response = await task
        self.assertEqual(await response.read(),data)
        self.assertEqual(response.headers['Content-Disposition'],'attachment; filename="a.bin"')
        self.assertNotIn('Set-Cookie',response.headers)
        self.assertEqual(self.state.buffered,0)

    async def test_pause_closes_local_forward_gate_even_before_socket_disconnect(self):
        a = await self.device('Alice'); c = await self.real_connector(a); p = await self.phone(a)
        controller = Controller(c.directory,test_http=True)
        await asyncio.to_thread(controller.control,{'action':'disable'})
        response = await self.client.post('/api/sessions',json={'id':'must-not-run'},headers=p)
        self.assertIn(response.status,(503,504))
        self.assertEqual(self.created,[])

    async def test_cli_admin_and_desktop_private_dispatch_without_gui(self):
        import os
        import subprocess
        import sys
        environment = {**os.environ, 'DISPLAY':'', 'WAYLAND_DISPLAY':''}
        result = await asyncio.to_thread(subprocess.run,[sys.executable,'-B','desktop.py','shared-relay','--data-dir',str(Path(self.temp.name)/'private')],
                                         input='{"action":"status"}',text=True,capture_output=True,check=True,cwd=ROOT,env=environment)
        self.assertFalse(json.loads(result.stdout)['result']['registered'])
        result = await asyncio.to_thread(subprocess.run,[sys.executable,'-B','-m','relay','--data-dir',str(Path(self.temp.name)/'relay'),'invite','--owner','cli-user'],
                                         text=True,capture_output=True,check=True,cwd=ROOT,env=environment)
        invitation = json.loads(result.stdout)['invitation']
        response = await self.client.post('/relay/register',json={'invitation':invitation,'name':'CLI computer'})
        self.assertEqual(response.status,201)

    async def test_device_websocket_does_not_follow_redirects(self):
        from aiohttp import web
        redirected = asyncio.Event()
        received = []
        async def first(request):
            redirected.set()
            raise web.HTTPTemporaryRedirect('/credential-sink')
        async def sink(request):
            received.append(True)
            return web.Response()
        app = web.Application()
        app.router.add_get('/relay/device/ws',first)
        app.router.add_get('/credential-sink',sink)
        server = TestServer(app)
        await server.start_server()
        directory = Path(self.temp.name)/'redirect'
        save_config(directory,{'url':str(server.make_url('')).rstrip('/'),'deviceToken':'synthetic','enabled':True})
        connector = Connector(directory,SimpleNamespace(bridge=SimpleNamespace(),web_dir=ROOT/'web',notifications=None),test_http=True)
        try:
            connector.start()
            await asyncio.wait_for(redirected.wait(),3)
            await asyncio.sleep(.1)
            self.assertEqual(received,[])
        finally:
            await asyncio.to_thread(connector.close)
            await server.close()

    async def test_administrator_revocation_can_be_cleared_locally_and_registered_again(self):
        a = await self.device('Alice')
        directory = Path(self.temp.name)/'revoked-local'
        save_config(directory,{**a,'url':self.url,'enabled':True})
        controller = Controller(directory,test_http=True)
        self.state.registry.revoke(a['deviceId'])
        state = await asyncio.to_thread(controller.control,{'action':'status'})
        self.assertTrue(state['registered']); self.assertFalse(state['online'])
        self.assertIn('error',state)
        await asyncio.to_thread(controller.control,{'action':'revoke'})
        secret = self.state.registry.invite('Alice')['invitation']
        result = await asyncio.to_thread(controller.control,{'action':'register','url':self.url,'invitation':secret,'name':'Alice again','consent':True})
        self.assertTrue(result['registered'])


if __name__ == '__main__':
    unittest.main()
