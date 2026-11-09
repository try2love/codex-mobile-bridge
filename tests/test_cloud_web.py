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
from urllib.parse import urlencode

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

    async def proxy(self, path, body=None):
        request, queue, records, data = self.request(path), asyncio.Queue(), [], bytearray()
        if body is not None:
            payload = json.dumps(body).encode()
            request.update(method='POST', length=len(payload), content_type='application/json')
            await queue.put(payload)
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
        return next(row for row in records if row['kind'] == 'response_start'), bytes(data)

    async def test_creation_models_keep_selected_project_host_and_entry_through_live_http(self):
        catalog = {'models': [{'id': 'chosen', 'efforts': ['high']}], 'cache': {'identity': 'project-account'}}
        target = self.server.bridge
        target.creation_models.return_value = catalog
        project = 'ssh:example|/home/user/Project with spaces'
        query = urlencode({'kind': 'desktop', 'project': project, 'refresh': 'true'})
        start, raw = await self.proxy('/api/projects/models?' + query)
        self.assertEqual(start['status'], 200, raw)
        self.assertEqual(json.loads(raw), catalog)
        target.creation_models.assert_called_once_with(project, refresh=True, kind='desktop')
        target.create_chat.assert_not_called()

        entry = 'codex:separate'
        separate = target.entries.get.return_value
        separate.creation_models.return_value = {**catalog, 'cache': {'identity': 'separate-account'}}
        query = urlencode({'entry': entry, 'kind': 'cli', 'host': entry, 'path': 'D:/Other Project'})
        start, raw = await self.proxy('/api/projects/models?' + query)
        self.assertEqual(start['status'], 200, raw)
        self.assertEqual(json.loads(raw)['cache']['identity'], 'separate-account')
        target.entries.get.assert_called_once_with(entry)
        separate.creation_models.assert_called_once_with(None, refresh=False, kind='cli', host=entry, path='D:/Other Project')
        separate.create_chat.assert_not_called()

    async def test_codex_directory_and_creation_reach_existing_validation_with_original_request_id(self):
        target = self.server.bridge
        target.project_directories.return_value = {'path': '/project', 'directories': []}
        browse = {'host': 'ssh:example', 'path': '/project', 'hidden': False, 'name': None, 'identity': None}
        start, _ = await self.proxy('/api/projects/browse', browse)
        self.assertEqual(start['status'], 200)
        target.project_directories.assert_called_once_with(**browse)
        target.register_project.return_value = {'key': 'ssh:example|/project'}
        start, _ = await self.proxy('/api/projects', {'host': 'ssh:example', 'path': '/project', 'createDirectory': False})
        self.assertEqual(start['status'], 200)
        target.register_project.assert_called_once_with('/project', False, 'ssh:example')
        target.project_trust.return_value = {'trusted': True}
        start, _ = await self.proxy('/api/projects/trust', {'key': 'ssh:example|/project', 'confirmed': True})
        self.assertEqual(start['status'], 200)
        target.project_trust.assert_called_once_with(key='ssh:example|/project', confirmed=True)
        target.create_chat.return_value = {'id': 'created', 'host': 'ssh:example'}
        identifier = str(uuid.uuid4())
        selection = {'model': 'chosen', 'effort': 'high', 'modelCatalogIdentity': 'project-account'}
        start, raw = await self.proxy('/api/sessions', {'id': identifier, 'project': 'ssh:example|/project', 'title': 'Test', **selection})
        self.assertEqual(start['status'], 200, raw)
        target.create_chat.assert_called_once_with('ssh:example|/project', 'Test', identifier, model_options=selection)

        start, _ = await self.proxy('/api/projects/browse', {**browse, 'arbitrary': 'forbidden'})
        self.assertEqual(start['status'], 400)
        self.assertEqual(target.project_directories.call_count, 1)

    async def test_desktop_creation_routes_keep_provider_and_environment_through_live_http(self):
        manager = self.server.desktop_sessions = Mock()
        manager.for_entry.return_value = manager
        for provider in ('claude', 'deepseek', 'zcode'):
            with self.subTest(provider=provider):
                manager.reset_mock()
                manager.call.return_value = {'models': [{'id': 'chosen'}], 'cache': {'identity': provider}}
                root = '/api/desktop-sessions/' + provider
                start, _ = await self.proxy(root + '/projects')
                self.assertEqual(start['status'], 200)
                query = urlencode({'environment': 'desktop|ssh:example|scope', 'projectKey': 'project', 'refresh': 'true'})
                start, raw = await self.proxy(root + '/create-models?' + query)
                self.assertEqual(start['status'], 200, raw)
                manager.call.assert_called_with(provider, 'create-models', None,
                    {'environment': 'desktop|ssh:example|scope', 'projectKey': 'project', 'refresh': True})
                manager.project_directories.return_value = {'path': '/project', 'directories': []}
                browse = {'environment': 'desktop|ssh:example|scope', 'path': '/project', 'hidden': False, 'name': None, 'identity': None}
                start, _ = await self.proxy(root + '/projects/browse', browse)
                self.assertEqual(start['status'], 200)
                manager.project_directories.assert_called_once_with(provider, **browse)
                manager.register_project.return_value = {'key': 'project'}
                start, _ = await self.proxy(root + '/projects/register', {'environment': browse['environment'], 'path': '/project', 'createDirectory': False})
                self.assertEqual(start['status'], 200)
                manager.register_project.assert_called_once_with(provider, browse['environment'], '/project', False)
                manager.project_trust.return_value = {'trusted': True}
                start, _ = await self.proxy(root + '/projects/trust', {'projectKey': 'project', 'confirmed': True})
                self.assertEqual(start['status'], 200)
                manager.project_trust.assert_called_once_with(provider, projectKey='project', confirmed=True)
                body = {'id': str(uuid.uuid4()), 'projectKey': 'project', 'environment': browse['environment'], 'model': 'chosen', 'modelCatalogIdentity': provider}
                start, _ = await self.proxy(root + '/create', body)
                self.assertEqual(start['status'], 200)
                manager.call.assert_called_with(provider, 'create', None, body)

    async def test_creation_allowlist_is_exact_and_does_not_expand_phone_or_admin_access(self):
        from relay.protocol import validate
        allowed = [('GET', '/api/projects/models'), ('POST', '/api/projects'),
                   ('POST', '/api/projects/browse'), ('POST', '/api/projects/trust')]
        for provider in ('claude', 'deepseek', 'zcode'):
            root = '/api/desktop-sessions/' + provider
            allowed += [('GET', root + '/create-models')]
            allowed += [('POST', root + '/projects/' + action) for action in ('browse', 'register', 'trust')]
        for method, path in allowed:
            with self.subTest(method=method, path=path):
                self.web.validate_request({**self.request(path), 'method': method})
                with self.assertRaises(PermissionError):
                    validate(method, path)
        denied = [('POST', '/api/projects/models'), ('GET', '/api/projects/browse'),
                  ('GET', '/api/projects/trust'), ('GET', '/api/projects/models/extra'),
                  ('GET', '/api/projects/\tmodels'), ('GET', '/api//projects/models')]
        for provider in ('claude', 'deepseek', 'zcode', 'arbitrary'):
            root = '/api/desktop-sessions/' + provider
            denied += [('POST', root + '/create-models'), ('GET', root + '/projects/register'),
                       ('GET', root + '/projects/browse'), ('POST', root + '/projects/remove'),
                       ('POST', root + '/install'), ('POST', root + '/scan')]
        denied += [('GET', '/api/desktop-sessions/arbitrary/create-models'), ('POST', '/api/desktop-sessions/arbitrary/create')]
        for method, path in denied:
            with self.subTest(method=method, path=path), self.assertRaises((ValueError, PermissionError)):
                self.web.validate_request({**self.request(path), 'method': method})

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
