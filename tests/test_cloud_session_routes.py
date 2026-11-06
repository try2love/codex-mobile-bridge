import asyncio
import http.client
import json
import unittest
from pathlib import Path
from threading import BoundedSemaphore, Thread
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from bridge.app.cloud import create_cloud_server
from bridge.features.network.cloud_connector import Connector, validate_request
from relay.protocol import validate as shared_validate

ROOT = Path(__file__).resolve().parents[1]
THREAD = str(uuid4())


def packet(method, target, data):
    return {'kind': 'request', 'id': str(uuid4()), 'method': method, 'path': target,
            'content_type': 'application/json', 'length': len(data), 'headers': {}}


class CloudSessionRoutesTests(unittest.TestCase):
    def setUp(self):
        self.manager = Mock()
        gateway = SimpleNamespace(bridge=Mock(), desktop_sessions=self.manager,
                                  web_dir=ROOT / 'web', slots=BoundedSemaphore(8),
                                  terminal_sockets=BoundedSemaphore(1))
        self.server = create_cloud_server(gateway)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token, session = self.server.auth.new_session('127.0.0.1', 'Noise-approved mobile')
        self.csrf = session['csrf']

    def request(self, method, target, value=None, *, csrf=None):
        body = json.dumps(value).encode() if value is not None else b''
        validate_request(packet(method, target, body))
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request(method, target, body=body, headers={
                'Cookie': self.server.auth.cookie_name + '=' + self.token,
                'Origin': 'http://127.0.0.1:' + str(self.server.server_port),
                'X-CSRF-Token': self.csrf if csrf is None else csrf,
                'Content-Type': 'application/json',
            })
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_native_client_resume_and_mutations_reach_only_the_selected_manager(self):
        for provider, entry in (('claude', ''), ('deepseek', ''), ('zcode', ''),
                                ('claude', 'claude:' + 'a' * 32), ('deepseek', 'deepseek:' + 'b' * 32)):
            with self.subTest(provider=provider, entry=entry):
                manager = Mock() if entry else self.manager
                manager.reset_mock()
                self.manager.resolve_instance.return_value = manager
                manager.list_with_activity.return_value = {'sessions': [{'id': THREAD}]}
                manager.call.return_value = {'session': {'id': THREAD}, 'capabilities': {'resume': True}}
                path = '/api/desktop-sessions/' + provider + '/'
                suffix = '?entry=' + entry if entry else ''
                self.assertEqual(self.request('GET', path + 'list' + suffix)[0], 200)
                manager.list_with_activity.assert_called_once_with(provider)
                self.assertEqual(self.request('GET', path + 'detail?sessionId=' + THREAD
                                             + ('&entry=' + entry if entry else ''))[0], 200)
                if entry:
                    self.manager.call.assert_not_called()
                else:
                    self.manager.resolve_instance.assert_not_called()
                for action in ('resume', 'send', 'stop', 'respond'):
                    request_id = str(uuid4())
                    fields = {'id': request_id}
                    if action == 'resume':
                        fields['progressive'] = True
                    if action == 'send':
                        fields['text'] = 'fixture'
                    if action == 'respond':
                        fields.update(requestId='fixture-approval', response={'accepted': True})
                    manager.call.return_value = {'status': 'accepted', 'id': request_id}
                    self.assertEqual(self.request('POST', path + action + suffix, dict(fields, sessionId=THREAD)),
                                     (200, {'status': 'accepted', 'id': request_id}))
                    manager.call.assert_called_with(provider, action, THREAD, fields)
                if entry:
                    self.manager.resolve_instance.assert_called_with(provider, entry, require_enabled=True)
                self.manager.reset_mock()

    def test_codex_sessions_route_shared_events_projects_and_entry_host(self):
        host = 'codex:' + 'a' * 32
        root = '/api/sessions/' + THREAD
        target = self.server.bridge.for_host.return_value
        target.view.return_value = {'id': THREAD}
        self.assertEqual(self.request('GET', root + '?host=' + host), (200, {'id': THREAD}))
        self.server.bridge.for_host.assert_called_with(host)
        self.manager.require_enabled.assert_called_with(host)
        target.activate.return_value = SimpleNamespace(connected=True)
        self.assertEqual(self.request('POST', root + '/reconnect?host=' + host, {'activate': True})[1]['connected'], True)
        target.activate.assert_called_once_with(THREAD)
        for method, path in (('GET', root + '/events'), ('GET', root + '/poll'), ('GET', root + '/changes'),
                             ('GET', root + '/permissions'), ('POST', root + '/compact'), ('POST', root + '/cli-network'),
                             ('GET', '/api/projects/models'), ('POST', '/api/desktop-sessions/zcode/projects/browse'),
                             ('GET', '/api/desktop-sessions/deepseek/tasks'), ('GET', '/api/mobile/events'),
                             ('POST', '/api/mobile/events/read')):
            with self.subTest(path=path):
                validate_request(packet(method, path, b'{}' if method == 'POST' else b''))

    def test_disabled_entry_csrf_and_revoked_session_cannot_submit(self):
        target = '/api/desktop-sessions/zcode/resume'
        value = {'id': str(uuid4()), 'sessionId': THREAD, 'progressive': True}
        self.assertEqual(self.request('POST', target, value, csrf='expired')[0], 403)
        self.manager.call.assert_not_called()
        self.manager.require_enabled.side_effect = PermissionError('disabled')
        self.assertEqual(self.request('POST', target, value)[0], 403)
        self.manager.call.assert_not_called()
        self.server.auth.logout(self.token)
        self.assertEqual(self.request('POST', target, value)[0], 401)
        self.manager.call.assert_not_called()

    def test_untrusted_binding_unknown_routes_and_ambiguous_selectors_are_refused(self):
        owner = Mock()
        owner.pin.return_value = None
        with self.assertRaises(PermissionError):
            asyncio.run(Connector.forward_http(owner, SimpleNamespace(binding='revoked'), {}, b''))
        owner.local.request.assert_not_called()
        path = '/api/desktop-sessions/claude/'
        for method, target, data in (
                ('POST', path + 'raw-rpc', b'{}'), ('GET', path + 'send', b''), ('DELETE', path + 'detail', b''),
                ('GET', path + 'list?unknown=1', b''), ('GET', path + 'list?entry=', b''), ('GET', path + 'list?entry=deepseek:'+'a'*32, b''),
                ('GET', path + 'detail?sessionId=x&sessionId=y', b''), ('GET', path + 'list', b'smuggle'),
                ('GET', path + '../zcode/list', b''), ('GET', path + '%2e%2e/zcode/list', b''),
                ('GET', '//outside.test' + path + 'list', b''), ('GET', path + 'list#', b'')):
            with self.subTest(target=target), self.assertRaises((ValueError, PermissionError)):
                validate_request(packet(method, target, data))
        for provider in ('claude', 'deepseek', 'zcode'):
            with self.assertRaises(PermissionError):
                shared_validate('POST', '/api/desktop-sessions/' + provider + '/resume', 2)

    def test_connector_forwards_native_resume_to_private_listener_and_blocks_revoked_writes(self):
        try:
            import aiohttp
        except ImportError:
            self.skipTest('requires gateway relay dependencies')
        from base64 import b64encode
        from unittest.mock import AsyncMock
        mobile = bytes(range(32))
        pin = {'mobile_public_key': b64encode(mobile).decode()}
        self.manager.call.return_value = {'status': 'ready', 'writable': True}

        async def exchange():
            origin = 'http://127.0.0.1:' + str(self.server.server_port)
            async with aiohttp.ClientSession() as local:
                owner = SimpleNamespace(server=self.server, sessions={}, local_origin=origin, local=local,
                                        pin=Mock(return_value=pin), wait_response_close=AsyncMock())
                channel = SimpleNamespace(binding='fixture', mobile=mobile, send=AsyncMock(), send_body=AsyncMock(),
                                          receive=AsyncMock(return_value={'kind': 'credit', 'count': 1}),
                                          promote_large=AsyncMock())
                path = '/api/desktop-sessions/zcode/resume'
                nonce = str(uuid4())
                body = json.dumps({'id': nonce, 'sessionId': THREAD, 'progressive': True}).encode()
                record = packet('POST', path, body)
                validate_request(record)
                await Connector.forward_http(owner, channel, record, body)
                self.assertEqual(channel.send.await_args_list[0].args[0]['status'], 200)
                self.assertTrue(json.loads(channel.send_body.await_args.args[0])['writable'])
                self.manager.call.assert_called_once_with('zcode', 'resume', THREAD, {'id': nonce, 'progressive': True})
                token = owner.sessions['fixture'][0]
                owner.pin.return_value = None
                await Connector.block_binding(SimpleNamespace(channels={}, sessions=owner.sessions, server=self.server),
                                              'fixture')
                self.assertIsNone(self.server.auth.get(token))
                with self.assertRaises(PermissionError):
                    await Connector.forward_http(owner, channel, record, body)
                self.assertEqual(self.manager.call.call_count, 1)

        asyncio.run(exchange())
