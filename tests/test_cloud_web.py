"""Account-Web exercises the live private listener with synthetic account authority."""
import asyncio
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from bridge.api import assets
from bridge.api.cloud_web import adapt_response, is_resource
from bridge.api.httpd import GatewayServer
from bridge.features.network.cloud_web import CloudWeb, CHUNK
from bridge.features.network.cloud_account import Controller
from bridge.features.network.cloud_connector import Connector
from bridge.features.notifications.channels import write_json

ROOT = Path(__file__).resolve().parents[1]


class CloudWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = GatewayServer(('127.0.0.1', 0), Mock(),
            {'auth': {'mode': 'password', 'sessionHours': 0}, 'origins': [], 'localAccess': True}, ROOT / 'web')
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.server.origins.add(self.origin)
        self.server.hosts.add(self.origin.removeprefix('http://'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.local = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        self.config = {'origin': 'https://cloud.test', 'account_id': str(uuid.uuid4()), 'gateway_id': str(uuid.uuid4())}
        self.grant = {'allowed': True, 'revision': 1, 'error': ''}
        accounts = SimpleNamespace(config=lambda: self.config, secret=lambda _: {'device_token': 'synthetic'},
                                   web_access_state=lambda _: dict(self.grant))
        self.owner = SimpleNamespace(server=self.server, local_origin=self.origin, local=self.local,
            closed=threading.Event(), accounts=accounts, relay_current=lambda _: True)
        self.web = CloudWeb(self.owner)
        self.web.config, self.web.token, self.web.origin = dict(self.config), 'synthetic', 'https://cloud.test:18787'
        self.web.revision = 1
        self.session = str(uuid.uuid4())

    async def asyncTearDown(self):
        await self.web.disconnect()
        await self.local.close()
        await asyncio.to_thread(self.server.shutdown)
        self.thread.join(2)
        self.server.server_close()

    def request(self, path='/', **extra):
        return dict(kind='request', stream_id=1, account_id=self.config['account_id'],
            base='/d/' + self.config['gateway_id'], origin=self.web.origin, session_id=self.session,
            csrf='a'*43, expires=int(time.time())+3600, method='GET', path=path, length=0, headers={}, **extra)

    async def proxy(self, path):
        request, queue, records, data = self.request(path), asyncio.Queue(), [], bytearray()
        async def send(value, part=None):
            if part is not None:
                data.extend(part)
                await queue.put({'kind': 'credit', 'count': 1})
            else:
                records.append(value)
                if value['kind'] == 'response_start':
                    await queue.put({'kind': 'credit', 'count': 4})
        self.web.send = send
        await queue.put({'kind': 'request_end'})
        await asyncio.wait_for(self.web.forward(request, queue), 10)
        self.assertEqual(records[-1]['kind'], 'response_end', records)
        return records[0], bytes(data)

    async def test_live_assets_and_private_auth_cross_the_actual_http_listener(self):
        start, html = await self.proxy('/')
        self.assertEqual(start['status'], 200)
        base = self.request()['base']
        self.assertIn((base + '/cloud-web.js?v=').encode(), html)
        self.assertIn((base + '/bootstrap.js?v=').encode(), html)
        self.assertNotIn(b'src="/bootstrap.js', html)
        self.assertIn(self.web.origin + base + '/html-preview-shell', start['headers']['Content-Security-Policy'])
        _, raw = await self.proxy('/api/auth')
        auth = json.loads(raw)
        self.assertTrue(auth['authenticated'])
        self.assertEqual(auth['csrf'], 'a'*43)
        self.assertNotIn('cookieName', auth)
        token, local, _ = self.web.sessions[self.session]
        self.assertNotEqual(auth['csrf'], local['csrf'])
        self.web.forget(self.session)
        self.assertIsNone(self.server.auth.get(token))

    async def test_disabled_gate_never_looks_up_capabilities_or_opens_socket(self):
        self.grant['allowed'] = False
        self.owner.accounts.request = Mock(side_effect=AssertionError('cloud network used'))
        remote = Mock()
        await self.web.connect(remote)
        remote.ws_connect.assert_not_called()
        with self.assertRaises(PermissionError):
            self.web.validate_request(self.request())
        self.assertEqual(self.web.sessions, {})

    async def test_revoking_access_invalidates_live_sessions_and_pending_streams(self):
        await self.proxy('/api/auth')
        token = self.web.sessions[self.session][0]
        phone_token, _ = self.server.auth.new_session('127.0.0.1', 'Synthetic paired phone', remember=False)
        waiting = asyncio.create_task(asyncio.Event().wait())
        self.web.tasks[2] = {'task': waiting, 'request': {**self.request(), 'stream_id': 2}}
        async def close():
            self.assertIsNone(self.server.auth.get(token))
            self.assertTrue(waiting.cancelled())
        self.web.ws = SimpleNamespace(closed=False, close=AsyncMock(side_effect=close))
        self.grant.update(allowed=False, revision=2)
        await self.web.disconnect()
        self.assertEqual(self.web.sessions, {})
        self.assertIsNotNone(self.server.auth.get(phone_token))
        self.grant.update(allowed=True, revision=3)
        self.assertFalse(self.web.current(), 're-enabling must not restore an old connection')

    async def test_disable_during_capability_lookup_cannot_finish_stale_connection(self):
        def capabilities(*_):
            self.grant.update(allowed=True, revision=3)
            return {'web_client': {'enabled': True, 'protocol': 'bridge-web-proxy-v1', 'origin': self.web.origin}}
        self.owner.accounts.request = capabilities
        remote = Mock()
        await self.web.connect(remote)
        remote.ws_connect.assert_not_called()

    async def test_permission_is_checked_again_after_body_before_local_dispatch(self):
        queue = asyncio.Queue()
        await queue.put({'kind': 'request_end'})
        self.grant.update(allowed=False, revision=2)
        with patch.object(self.local, 'request') as local_request:
            with self.assertRaises(PermissionError):
                await self.web.forward_request(self.request(), queue)
            local_request.assert_not_called()
        self.assertEqual(self.web.sessions, {})

    async def test_desktop_control_disconnects_real_web_sessions_only(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            write_json(controller.path, {**self.config, 'login': 'synthetic', 'enabled': True})
            controller.secret = lambda _: {'device_token': 'synthetic'}
            change = {'action': 'web-access', **self.config, 'allowed': True, 'expected_revision': 0}
            controller.control(change)
            connector = Connector(directory, None)
            try:
                connector.accounts = self.owner.accounts = controller
                self.web.config = controller.config()
                connector.web, connector.loop = self.web, asyncio.get_running_loop()
                self.web.blocked = connector.blocked_web_scopes
                connector.channels = {'phone-channel': object()}
                await self.proxy('/api/auth')
                token = self.web.sessions[self.session][0]
                self.web.ws = SimpleNamespace(closed=False, close=AsyncMock())
                result = await asyncio.to_thread(connector.control, {**change, 'allowed': False, 'expected_revision': 1})
                self.assertFalse(result['web_access']['allowed'])
                self.assertIsNone(self.server.auth.get(token))
                self.web.ws.close.assert_awaited()
                self.assertEqual(set(connector.channels), {'phone-channel'})
                self.assertEqual(connector.policy_generation, 0)
                await asyncio.to_thread(connector.control, {**change, 'expected_revision': 2})
                self.assertTrue(self.web.wake.is_set())
                self.assertFalse(self.web.current())
            finally:
                connector.notifications.close()

    async def test_boundaries_accept_browser_headers_and_reject_account_or_private_paths(self):
        good = self.request('/app.js?v=abcd&bridge_retry=1')
        good['headers'] = {'If-None-Match': '"previous"'}
        self.web.validate_request(good)
        for changes in ({'path': 'https://elsewhere.test/'}, {'path': '/api/cloud'},
                        {'path': '/api/login', 'method': 'POST'}, {'path': '/api/connection/prove'},
                        {'path': '/%2e%2e/app.js'}, {'path': '/app.js?other=1'},
                        {'account_id': str(uuid.uuid4())}, {'origin': 'https://elsewhere.test'},
                        {'length': 1}, {'headers': {'Authorization': 'Bearer forbidden'}},
                        {'headers': {'If-None-Match': 'bad\r\nheader'}}, {'expires': int(time.time())-1}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, PermissionError)):
                self.web.validate_request({**good, **changes})

    async def test_live_source_change_updates_html_version_without_server_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            web = Path(directory)
            (web/'index.html').write_text('<html><head><script src="/app.js"></script></head></html>')
            (web/'app.js').write_text('window.version=1;')
            (web/'cloud.js').write_text('/* adapter */')
            routes = {'/': ('index.html','text/html'), '/app.js': ('app.js','text/javascript'), '/cloud-web.js': ('cloud.js','text/javascript')}
            server = SimpleNamespace(web_dir=web)
            with patch.dict(assets.STATIC, routes):
                def current():
                    return adapt_response(server, '/', assets.served_asset_bytes(web, '/'), {}, self.request())[0]
                first = current()
                (web/'app.js').write_text('window.version=2;')
                second = current()
                self.assertNotEqual(first, second)
                self.assertIn(assets.current_version(web, '/app.js').encode(), second)

    async def test_manifest_scopes_every_computer_and_resource_allowlist(self):
        _, raw = await self.proxy('/manifest.webmanifest')
        manifest = json.loads(raw)
        self.assertEqual(manifest['start_url'], self.request()['base']+'/')
        self.assertTrue(all(i['src'].startswith(self.request()['base']+'/') for i in manifest['icons']))
        self.assertFalse(is_resource('/hosts/cloud-web.js'))
        self.assertFalse(is_resource('/vendor/pdfjs/../../secrets'))

    async def test_revocation_cancels_only_matching_browser(self):
        one, two = asyncio.create_task(asyncio.sleep(10)), asyncio.create_task(asyncio.sleep(10))
        self.web.tasks = {1: {'task': one, 'request': {'session_id': self.session}},
                          2: {'task': two, 'request': {'session_id': str(uuid.uuid4())}}}
        self.web.forget(self.session)
        await asyncio.sleep(0)
        self.assertTrue(one.cancelled())
        self.assertFalse(two.done())
