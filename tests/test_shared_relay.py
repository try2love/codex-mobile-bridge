import asyncio
import base64
import concurrent.futures
import gzip
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from relay.protocol import validate, origin, MAX_BODY, download_headers
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

    def test_desktop_account_access_and_attachments_keep_relay_boundaries(self):
        for provider in ('claude', 'deepseek'):
            root = '/api/desktop-sessions/'+provider+'/'
            for method, action in [('GET', 'account'), ('GET', 'access'), ('POST', 'access'), ('POST', 'uploads'), ('GET', 'uploads/'+THREAD+'/preview'), ('POST', 'uploads/'+THREAD+'/thumb')]:
                validate(method, root+action+'?sessionId=native')
            for method, action in [('POST', 'account'), ('GET', 'uploads'), ('POST', 'uploads/'+THREAD+'/preview'), ('GET', 'uploads/'+THREAD+'/thumb'), ('GET', 'uploads/../../file')]:
                with self.assertRaises(PermissionError):
                    validate(method, root+action)

    def test_protocol_v2_routes_and_management_boundary(self):
        for method, action in [('GET','workspace/download?path=a.txt'), ('POST','workspace/upload?path=a.txt'), ('POST','terminal'), ('POST','side-chat'), ('GET','catalog?kind=skills'), ('POST','permissions')]:
            validate(method, '/api/sessions/'+THREAD+'/'+action)
        for path in ['/api/accounts', '/api/account/reset', '/api/login', '/api/pair', 'https://example.com/api/sessions', '/api/sessions/'+THREAD+'/events', '/api/%73essions', '/api/../admin']:
            with self.assertRaises((ValueError, PermissionError)): validate('POST', path)
        with self.assertRaises(ValueError): validate('POST', '/api/sessions', MAX_BODY+1)
        with self.assertRaises(ValueError): origin('http://example.com')

    def test_desktop_allowlist_keeps_configuration_and_streams_local(self):
        for method in ('GET', 'POST'):
            validate(method, '/api/clients')
        for provider in ('claude', 'deepseek'):
            for method, action in [('GET', 'list'), ('GET', 'detail'), ('GET', 'catalog'),
                                   ('GET', 'projects'), ('POST', 'create'), ('POST', 'send'),
                                   ('POST', 'respond'), ('POST', 'settings'), ('POST', 'stop'),
                                   ('GET', 'workspace/download'), ('POST', 'workspace/upload'),
                                   ('GET', 'workspace/git-status'), ('POST', 'workspace/git-action'),
                                   ('GET', 'terminal'), ('POST', 'terminal')]:
                validate(method, '/api/desktop-sessions/'+provider+'/'+action+'?sessionId=native-session')
            for action in ('install', 'remove', 'status', 'scan', 'eval', 'restart', 'events', 'terminal/events'):
                for method in ('GET', 'POST'):
                    with self.assertRaises(PermissionError):
                        validate(method, '/api/desktop-sessions/'+provider+'/'+action)
            with self.assertRaises(PermissionError):
                validate('GET', '/api/desktop-sessions/'+provider+'/send')
        for path in ('/api/clients/scan', '/api/desktop-sessions/codex/list', '/api/desktop-sessions/arbitrary/list'):
            with self.assertRaises(PermissionError):
                validate('POST', path)


try:
    import aiohttp
    from aiohttp.test_utils import TestServer, TestClient
    from relay.server import create_app, COOKIE, STATE
    from bridge.features.network.shared_relay import Connector, Controller, save_config
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

    async def real_connector(self, device, desktop_sessions=None):
        directory = Path(self.temp.name)/device['deviceId']
        save_config(directory, {**device, 'url': self.url, 'enabled': True})
        bridge = SimpleNamespace(host_errors=[], list=lambda **kw: [{'title': device['deviceName'], 'id': THREAD}])
        bridge.create_chat = lambda project, title, identifier: self.created.append((device['deviceName'], identifier)) or {'id': identifier}
        bridge.for_host = lambda host: bridge
        bridge.terminal = lambda thread, owner, action, body: {'owner': owner, 'action': action}
        gateway = SimpleNamespace(bridge=bridge, web_dir=ROOT/'web', notifications=None, desktop_sessions=desktop_sessions,
                                  transfer_directory=directory/'gateway-settings')
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
        self.assertEqual((await self.client.get('/api/accounts', headers=p)).status, 403)
        self.assertEqual((await self.client.get('/api/auth', headers=p)).status, 200)

    async def test_compressed_control_request_cannot_bypass_four_kib_limit(self):
        device = await self.device('Fixture')
        pair = self.state.registry.pair(device['deviceId'])
        claim = self.state.registry.claim(pair['token'], 'Phone')['claim']
        raw = json.dumps({'claim': claim, 'padding': 'x'*16384}).encode()
        encoded = gzip.compress(raw)
        self.assertLess(len(encoded), 4096)
        self.assertGreater(len(raw), 4096)
        response = await self.client.post('/relay/claim-status', data=encoded,
            headers={'Origin': self.url, 'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        self.assertEqual(response.status, 415)
        self.assertEqual(self.state.registry.consume(claim), ({'state': 'pending'}, None))

    async def test_compressed_forward_is_rejected_before_connector_or_budget_use(self):
        device = await self.device('Fixture')
        await self.real_connector(device)
        phone = await self.phone(device)
        raw = json.dumps({'id': 'compressed', 'project': 'fixture', 'padding': 'x'*16384}).encode()
        response = await self.client.post('/api/sessions', data=gzip.compress(raw),
            headers={**phone, 'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        self.assertEqual(response.status, 415)
        self.assertEqual(self.created, [])
        self.assertEqual(self.state.pending, {})
        self.assertEqual(self.state.buffered, 0)

    async def test_identity_encoding_preserves_normal_json_and_control_limit(self):
        device = await self.device('Fixture')
        headers = {'Authorization': 'Bearer '+device['deviceToken'], 'Content-Encoding': 'identity'}
        response = await self.client.post('/relay/device', json={'action': 'status'}, headers=headers)
        self.assertEqual(response.status, 200)
        response = await self.client.post('/relay/device', json={'action': 'status', 'padding': 'x'*4096}, headers=headers)
        self.assertEqual(response.status, 400)

    async def test_all_content_encoding_fields_are_checked_without_decompression(self):
        # Even malformed encoded bytes must reach the boundary as wire bytes,
        # rather than raising a decompression error inside aiohttp first.
        for encodings in (['br'], ['gzip, identity'], ['identity', 'gzip'], ['gzip', 'identity']):
            with self.subTest(encodings=encodings):
                headers = [('Origin', self.url), ('Content-Type', 'application/json')]
                headers.extend(('Content-Encoding', value) for value in encodings)
                response = await self.client.post('/relay/claim-status', data=b'not compressed', headers=headers)
                self.assertEqual(response.status, 415)

    async def test_native_clients_sessions_files_and_terminal_share_authenticated_manager(self):
        a = await self.device('Alice')
        calls, uploads = [], []
        enabled = {'claude': True, 'deepseek': True}
        def require(provider):
            if not enabled[provider]:
                raise ValueError('disabled client')
        def control(value):
            calls.append(('toggle', value))
            enabled[value['provider']] = value['enabled']
            return {'clients': [{'id': key, 'enabled': value} for key, value in enabled.items()]}
        def call(provider, action, sid, body):
            require(provider)
            calls.append((provider, action, sid, body))
            return {'provider': provider, 'sessions': [{'id': 'native-session'}]}
        data = bytes(range(256))
        root = Path(self.temp.name) / 'native-workspace'; root.mkdir()
        (root / 'example.bin').write_bytes(data)
        def workspace(provider, sid):
            require(provider)
            self.assertEqual(sid, 'native-session')
            def files(thread, action, body):
                if action == 'upload':
                    uploads.append(base64.b64decode(body['encoded']))
                    return {'ok': True}
                if action == 'download-stream':
                    from bridge.features.workspace.workspace import operate
                    return operate(root, action, body)
                return {'data': base64.b64encode(data).decode(), 'name': 'example.bin'}
            return SimpleNamespace(identifier=THREAD, workspace=files,
                                   terminal=lambda thread, owner, action, body: {'owner': owner, 'provider': provider, 'action': action})
        manager = SimpleNamespace(clients=lambda: {'clients': [{'id': 'deepseek', 'enabled': True}]},
                                  toggle_client=control, require_enabled=require, call=call, list_with_activity=lambda p: call(p, 'list', None, {}), workspace_bridge=workspace)
        connector = await self.real_connector(a, manager)
        self.assertIs(connector.server.desktop_sessions, manager)
        p, other = await self.phone(a), await self.phone(a)
        self.assertEqual((await self.client.get('/api/clients')).status, 401)
        self.assertEqual((await self.client.get('/api/clients', headers=p)).status, 200)
        response = await self.client.get('/api/desktop-sessions/claude/list', headers=p)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['provider'], 'claude')
        bad_csrf = {**p, 'X-CSRF-Token': 'incorrect'}
        self.assertEqual((await self.client.post('/api/clients', json={'provider': 'deepseek', 'enabled': False}, headers=bad_csrf)).status, 401)
        for provider in ('claude', 'deepseek'):
            base = '/api/desktop-sessions/'+provider+'/'
            response = await self.client.post(base+'send', json={'sessionId': 'native-session', 'id': THREAD, 'text': 'hello'}, headers=p)
            self.assertEqual(response.status, 200)
            self.assertIn((provider, 'send', 'native-session', {'id': THREAD, 'text': 'hello'}), calls)
            download = await self.client.get(base+'workspace/download?sessionId=native-session&path=example.bin', headers=p)
            self.assertEqual(await download.read(), data)
            self.assertIn('example.bin', download.headers['Content-Disposition'])
            ranged = await self.client.get(base+'workspace/download?sessionId=native-session&path=example.bin',
                                          headers={**p, 'Range': 'bytes=64-127', 'If-Range': download.headers['ETag']})
            self.assertEqual(ranged.status, 206)
            self.assertEqual(ranged.headers['Content-Range'], 'bytes 64-127/256')
            self.assertEqual(await ranged.read(), data[64:128])
            upload = await self.client.post(base+'workspace/upload?sessionId=native-session&path=example.bin', data=data,
                                           headers={**p, 'Content-Type': 'application/octet-stream'})
            self.assertEqual(upload.status, 200)
            owners = []
            for headers in (p, other, p):
                response = await self.client.post(base+'terminal?sessionId=native-session', json={'action': 'open'}, headers=headers)
                self.assertEqual(response.status, 200)
                owners.append((await response.json())['owner'])
            self.assertNotEqual(owners[0], owners[1])
            self.assertEqual(owners[0], owners[2])
        self.assertEqual(uploads, [data, data])
        self.assertEqual((await self.client.post('/api/clients', json={'provider': 'deepseek', 'enabled': False}, headers=p)).status, 200)
        self.assertEqual((await self.client.get('/api/desktop-sessions/deepseek/list', headers=p)).status, 400)
        for path in ('/api/clients/scan', '/api/desktop-sessions/deepseek/install', '/api/desktop-sessions/claude/eval'):
            self.assertEqual((await self.client.post(path, json={}, headers=p)).status, 403)
        self.assertEqual((await self.client.get('/api/desktop-sessions/deepseek/events', headers=p)).status, 403)

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

    async def test_binary_stream_reduces_wire_bytes_and_static_assets_revalidate(self):
        self.state.timeout = 5
        a = await self.device('A'); connector = await self.real_connector(a); p = await self.phone(a)
        file = Path(self.temp.name) / 'eight-mib.bin'
        file.write_bytes(b'a' * (8 * 1024 * 1024))
        connector.server.bridge.artifact = lambda thread, identifier: {'path': file, 'name': file.name, 'image': False}
        url = '/api/sessions/' + THREAD + '/files/' + 'a' * 64
        original = connector.forward
        sizes = []
        class Meter:
            def __init__(self, ws): self.ws = ws
            async def send_bytes(self, value):
                sizes.append(len(value)); return await self.ws.send_bytes(value)
            async def send_json(self, value):
                sizes.append(len(json.dumps(value).encode())); return await self.ws.send_json(value)
        async def measured(client, message, stream=None):
            result = await original(client, message, (Meter(stream[0]), stream[1]) if stream else None)
            if result is not None: sizes.append(len(json.dumps(result).encode()))
            return result
        connector.forward = measured
        measured_sizes = []
        for streaming in (False, True):
            self.state.protocols[a['deviceId']] = streaming; sizes.clear()
            response = await self.client.get(url, headers=p)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), file.read_bytes())
            measured_sizes.append(sum(sizes))
            self.assertEqual(self.state.buffered, 0)
        self.assertLess(measured_sizes[1], measured_sizes[0] * .76)
        print('relay download 8 MiB wire bytes legacy=%d streaming=%d' % tuple(measured_sizes))
        first = await self.client.get('/app.js', headers={'Accept-Encoding': 'identity'})
        raw = await first.read()
        compressed = await self.client.get('/app.js', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(compressed.headers['Content-Encoding'], 'gzip')
        self.assertEqual(await compressed.read(), raw)
        cached = await self.client.get('/app.js', headers={'If-None-Match': first.headers['ETag']})
        self.assertEqual(cached.status, 304); self.assertEqual(await cached.read(), b'')
        print('relay app.js raw=%d gzip=%s revalidated-body=0' % (len(raw), compressed.headers['Content-Length']))

    async def test_scoped_pairing_two_computers_one_browser_cookie_jar(self):
        a, b = await self.device('A'), await self.device('B')
        await self.real_connector(a); await self.real_connector(b)
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as browser:
            for device in (a, b):
                scope = '/d/' + device['deviceId']
                pair = await (await self.action(device, 'pair')).json()
                claim = await (await browser.post(self.url + scope + '/relay/claim', json={'token': pair['url'].split('#pair=')[1], 'name': 'browser'}, headers={'Origin': self.url})).json()
                await self.action(device, 'approve', id=pair['id'], approved=True)
                response = await browser.post(self.url + scope + '/relay/claim-status', json=claim, headers={'Origin': self.url})
                self.assertEqual(response.cookies[COOKIE]['path'], scope + '/')
                self.assertEqual((await response.json())['base'], scope + '/')
            self.assertEqual(len(jar), 2)
            for device in (a, b):
                url = self.url + '/d/' + device['deviceId']
                auth = await (await browser.get(url + '/api/auth')).json()
                self.assertTrue(auth['authenticated']); self.assertTrue(auth['online'])
                self.assertEqual(auth['instanceId'], 'relay:' + device['deviceId'])
                self.assertEqual(auth['computer']['name'], device['deviceName'])
                result = await (await browser.get(url + '/api/sessions')).json()
                self.assertEqual(result['sessions'][0]['title'], device['deviceName'])
            legacy = await self.phone(b)
            a_cookie = jar.filter_cookies(self.server.make_url('/d/' + a['deviceId'] + '/'))[COOKIE].value
            response = await self.client.get('/d/' + a['deviceId'] + '/api/auth', headers={'Cookie': COOKIE + '=' + a_cookie + '; ' + legacy['Cookie']})
            self.assertEqual((await response.json())['deviceId'], a['deviceId'])
            wrong = await self.client.get('/d/' + a['deviceId'] + '/api/sessions', headers=legacy)
            self.assertEqual(wrong.status, 401)
            url = self.url + '/d/' + a['deviceId']
            auth = await (await browser.get(url + '/api/auth')).json()
            await browser.post(url + '/api/logout', json={}, headers={'Origin': self.url, 'X-CSRF-Token': auth['csrf']})
            self.assertFalse((await (await browser.get(url + '/api/auth')).json())['authenticated'])
            self.assertTrue((await (await browser.get(self.url + '/d/' + b['deviceId'] + '/api/auth')).json())['authenticated'])

    async def test_stream_credit_bounds_and_revocation_cancel_upstream(self):
        import threading
        from relay.stream import CHUNK, WINDOW
        self.state.timeout = 5
        device = await self.device('Stream fixture')
        connector = await self.real_connector(device)
        headers = await self.phone(device)
        file = Path(self.temp.name) / 'credited.bin'
        file.write_bytes(b'x' * CHUNK * (WINDOW + 10))
        connector.server.bridge.artifact = lambda thread, identifier: {'path': file, 'name': file.name, 'image': False}
        cancelled = threading.Event()
        full, released = threading.Event(), threading.Event()
        sent = []
        class Meter:
            def __init__(self, ws): self.ws = ws
            async def send_json(self, value): return await self.ws.send_json(value)
            async def send_bytes(self, value):
                await self.ws.send_bytes(value)
                sent.append(len(value) - 43)
                if len(sent) == WINDOW: full.set()
                if len(sent) == WINDOW + 1: released.set()
        original_forward = connector.forward
        async def observed(client, message, stream=None):
            try:
                return await original_forward(client, message, (Meter(stream[0]), stream[1]) if stream else None)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        connector.forward = observed
        ws = self.state.devices[device['deviceId']]
        original_send = ws.send_json
        acknowledgments = []
        async def withhold_ack(value, **kwargs):
            if value.get('type') == 'ack':
                acknowledgments.append(value)
            else:
                await original_send(value, **kwargs)
        ws.send_json = withhold_ack
        response = await self.client.get('/api/sessions/' + THREAD + '/files/' + 'a' * 64, headers=headers)
        self.assertTrue(await asyncio.to_thread(full.wait, 2))
        first_window = sum(sent)
        self.assertLessEqual(first_window, CHUNK * WINDOW)
        self.assertEqual(await response.content.readexactly(first_window), b'x' * first_window)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(response.content.readexactly(1), timeout=.05)
        self.assertEqual(len(acknowledgments), WINDOW)
        await original_send(acknowledgments[0])
        self.assertTrue(await asyncio.to_thread(released.wait, 2))
        self.assertEqual(await response.content.readexactly(sent[-1]), b'x' * sent[-1])
        phone = self.state.registry.phone(headers['Cookie'].split('=', 1)[1])
        await self.action(device, 'revoke-phone', id=phone['id'])
        with self.assertRaises(aiohttp.ClientPayloadError):
            await asyncio.wait_for(response.read(), timeout=2)
        self.assertTrue(await asyncio.to_thread(cancelled.wait, 2))
        self.assertEqual(self.state.pending, {})
        self.assertEqual(self.state.buffered, 0)

    async def test_relay_renewal_and_revocation_do_not_restore_grants(self):
        a = await self.device('A'); p = await self.phone(a)
        secret = p['Cookie'].split('=', 1)[1]
        old = self.state.registry.phone(secret)
        self.state.registry.db.execute('UPDATE phones SET expires=? WHERE id=?', (time.time() + 60, old['id']))
        self.state.registry.db.commit()
        response = await self.client.get('/api/auth', headers=p)
        self.assertIn('Max-Age=15552000', response.headers['Set-Cookie'])
        self.assertGreater(self.state.registry.phone(secret)['expires'], time.time() + 179 * 86400)
        self.state.registry.revoke_phone(a['deviceId'], old['id'])
        response = await self.client.get('/api/auth', headers=p)
        self.assertFalse((await response.json())['authenticated'])
        self.assertNotIn('Set-Cookie', response.headers)

    async def test_discovery_capability_is_read_only_and_tracks_tunnel_replacement(self):
        import hashlib
        a = await self.device('A'); ws = await self.socket(a)
        key = 'a' * 64
        payload = {'type': 'discovery', 'endpoints': ['https://old.example.com'], 'grants': [hashlib.sha256(key.encode()).hexdigest()]}
        await ws.send_json(payload)
        # Ordering fence: authenticated device status executes after WS scheduling.
        for _ in range(50):
            if a['deviceId'] in self.state.discovery: break
            await asyncio.sleep(.01)
        route = '/relay/discover/' + a['deviceId']
        self.assertEqual((await self.client.get(route)).status, 401)
        headers = {'X-Discovery-Key': key}
        self.assertEqual((await (await self.client.get(route, headers=headers)).json())['endpoints'], payload['endpoints'])
        self.assertEqual((await self.client.get('/d/' + a['deviceId'] + '/api/sessions', headers=headers)).status, 401)
        payload['endpoints'] = ['https://new.example.com']; await ws.send_json(payload)
        for _ in range(50):
            if self.state.discovery[a['deviceId']]['endpoints'] == payload['endpoints']: break
            await asyncio.sleep(.01)
        self.assertEqual((await (await self.client.get(route, headers=headers)).json())['endpoints'], payload['endpoints'])
        await ws.close()
        self.assertEqual((await self.client.get(route, headers=headers)).status, 401)

    async def test_headless_static_app_and_native_compatible_pair_url(self):
        a = await self.device('Alice'); p = await self.phone(a)
        response = await self.client.get('/',headers=p)
        html = await response.text()
        self.assertIn('/relay/mode.js',html); self.assertIn('workbench.js',html)
        pair = await (await self.action(a,'pair')).json()
        self.assertTrue(pair['url'].startswith(self.url+'/d/'+a['deviceId']+'/#pair='))
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

    async def test_real_range_resume_changed_content_and_empty_file(self):
        from bridge.features.workspace.workspace import operate
        a = await self.device('Alice'); connector = await self.real_connector(a); p = await self.phone(a)
        root = Path(self.temp.name) / 'download-workspace'; root.mkdir()
        file = root / 'example.bin'
        data = bytes(range(256)) * 8200; file.write_bytes(data)
        connector.server.bridge.workspace = lambda thread, action, params: operate(root, action, params)
        url = '/api/sessions/' + THREAD + '/workspace/download?path=example.bin'
        result, etag = bytearray(), None
        for offset in range(0, len(data), 1024 * 1024):
            response = await self.client.get(url, headers={**p, 'Range': 'bytes=%d-%d' % (offset, offset + 1024 * 1024 - 1),
                                                          **({'If-Range': etag} if etag else {})})
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
            self.assertEqual(response.headers['ETag'], etag or response.headers['ETag'])
            self.assertNotIn('Set-Cookie', response.headers)
            etag = response.headers['ETag']; result.extend(await response.read())
        self.assertEqual(bytes(result), data)
        file.write_bytes(b'changed')
        response = await self.client.get(url, headers={**p, 'Range': 'bytes=2-3', 'If-Range': etag})
        self.assertEqual(response.status, 200)
        self.assertNotEqual(response.headers['ETag'], etag)
        self.assertEqual(await response.read(), b'changed')
        response = await self.client.get(url, headers={**p, 'Range': 'bytes=7-'})
        self.assertEqual(response.status, 416)
        self.assertEqual(response.headers['Content-Range'], 'bytes */7')
        self.assertEqual(await response.read(), b'')
        file.write_bytes(b'')
        response = await self.client.get(url, headers={**p, 'Range': 'bytes=0-1048575'})
        self.assertEqual(response.status, 416)
        self.assertEqual(response.headers['Content-Range'], 'bytes */0')

    async def test_relay_range_headers_are_explicitly_allowlisted(self):
        for value, response in [({'Cookie': 'secret'}, False), ({'Authorization': 'secret'}, False),
                ({'Set-Cookie': 'secret'}, True), ({'Range': 'bytes=0-1\r\nCookie: secret'}, False),
                ({'Content-Range': 'bad'}, True), ({'ETag': 'not-quoted'}, True)]:
            with self.assertRaises(ValueError): download_headers(value, response)
        a = await self.device('Alice'); w = await self.socket(a); p = await self.phone(a)
        task = asyncio.create_task(self.client.get('/api/sessions/'+THREAD+'/workspace/download?path=a.bin',
                headers={**p, 'Range': 'bytes=2-4', 'If-Range': '"test"', 'X-Secret': 'not-forwarded'}))
        msg = await w.receive_json()
        self.assertEqual(msg['headers'], {'Range': 'bytes=2-4', 'If-Range': '"test"'})
        await w.send_json({'id': msg['id'], 'status': 206, 'body': base64.b64encode(b'abc').decode(),
                          'contentType': 'application/octet-stream',
                          'headers': {'ETag': '"test"', 'Content-Range': 'bytes 2-4/8', 'Accept-Ranges': 'bytes'}})
        response = await task
        self.assertEqual(response.status, 206)
        self.assertEqual(response.headers['Content-Length'], '3')
        self.assertEqual(response.headers['Content-Range'], 'bytes 2-4/8')
        self.assertEqual(await response.read(), b'abc')

    async def test_changed_large_artifact_requires_restart_without_oversized_relay_frame(self):
        self.state.timeout = 3
        a = await self.device('Alice'); connector = await self.real_connector(a); p = await self.phone(a)
        file = Path(self.temp.name) / 'large.bin'
        with file.open('wb') as stream: stream.truncate(MAX_BODY + 1)
        connector.server.bridge.artifact = lambda thread, identifier: {'path': file, 'name': file.name, 'image': False}
        url = '/api/sessions/' + THREAD + '/files/' + 'a' * 64
        # The file exceeds the default click-download policy as well as one
        # relay frame. Raising only that policy must still preserve bounded
        # ranges and changed-file restart handling across the relay.
        response = await self.client.get(url, headers={**p, 'Range': 'bytes=0-1048575'})
        self.assertEqual(response.status, 400)
        self.assertIn('下载限制', (await response.json())['error'])
        policy = {'clickDownloadMiB': MAX_BODY//(1024*1024)+1}
        response = await self.client.post('/api/file-transfer', json=policy, headers=p)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), policy)
        self.assertEqual(json.loads((connector.directory/'gateway-settings'/'file-transfer.json').read_text()), policy)
        from bridge.features.workspace.preferences import transfer_settings
        main_directory = connector.directory/'gateway-settings'
        self.assertEqual(transfer_settings(main_directory), policy)
        updated = {'clickDownloadMiB': policy['clickDownloadMiB']+1}
        transfer_settings(main_directory, updated)
        response = await self.client.get('/api/file-transfer', headers=p)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), updated)
        response = await self.client.get(url, headers={**p, 'Range': 'bytes=0-1048575'})
        self.assertEqual(response.status, 206)
        etag = response.headers['ETag']
        self.assertEqual(len(await response.read()), 1048576)
        with file.open('r+b') as stream: stream.write(b'changed')
        response = await self.client.get(url, headers={**p, 'Range': 'bytes=1048576-2097151', 'If-Range': etag})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(await response.read()), file.stat().st_size)
        self.assertEqual(self.state.buffered, 0)
        # Restarting with no old validator still downloads bounded ranges.
        response = await self.client.get(url, headers={**p, 'Range': 'bytes=0-1048575'})
        self.assertEqual(response.status, 206)
        self.assertNotEqual(response.headers['ETag'], etag)
        self.assertTrue((await response.read()).startswith(b'changed'))

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
