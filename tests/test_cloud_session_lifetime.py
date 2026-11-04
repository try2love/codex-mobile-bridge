"""Cloud sessions use real loopback HTTP with synthetic bindings, never accounts."""
import asyncio
import base64
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

from bridge.app.cloud import create_cloud_server
from bridge.features.auth.auth import Auth
from bridge.features.network.cloud_account import scope
from bridge.features.network.cloud_connector import Connector
from bridge.features.notifications.channels import write_json


ROOT = Path(__file__).resolve().parents[1]


class CloudSessionLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.gateway = SimpleNamespace(bridge=Mock(), web_dir=ROOT / 'web',
            auth=Auth({'mode': 'none', 'sessionHours': 12}),
            ui_language_path=self.directory / 'ui-language.json',
            slots=threading.BoundedSemaphore(48), terminal_sockets=threading.BoundedSemaphore(8))
        self.connector = Connector(self.directory, self.gateway)
        self.server = create_cloud_server(self.gateway)
        self.connector.server = self.server
        self.connector.local_origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.local = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False)
        self.connector.local = self.local
        # Response-close flow control has its own tests; only skip that final wait.
        self.connector.wait_response_close = AsyncMock()
        self.config = {'origin': 'https://cloud.example.test', 'account_id': str(uuid.uuid4()),
                       'gateway_id': str(uuid.uuid4()), 'login': 'synthetic', 'enabled': True}
        self.binding = str(uuid.uuid4())
        self.mobile = b'\x01' + b'\0' * 31
        write_json(self.connector.accounts.path, self.config)
        self.connector.config = dict(self.config)
        self.connector.policy_identity = self.connector.account_identity(self.config)
        self.connector.entitlement_deadline = None
        self.key = scope(self.config['origin'], self.config['account_id'])
        write_json(self.directory / 'cloud-bindings.json', {self.key: {self.binding: {
            'gateway_id': self.config['gateway_id'],
            'mobile_public_key': base64.b64encode(self.mobile).decode('ascii')}}})
        self.access = {'allowed': True, 'generation': 1, 'remote_revision': 1,
                       'remote_status': 'active', 'sync_state': 'confirmed'}
        self.set_access(True)

    async def asyncTearDown(self):
        self.connector.clear_authority()
        await self.local.close()
        await asyncio.to_thread(self.server.shutdown)
        self.worker.join(timeout=3)
        self.server.server_close()
        self.connector.notifications.close()

    def set_access(self, allowed):
        self.connector.accounts.access.save(self.key, self.config['gateway_id'], self.binding,
                                            {**self.access, 'allowed': allowed})

    async def forward(self, method='GET', path='/api/auth', body=b''):
        records, chunks = [], []
        channel = SimpleNamespace(binding=self.binding, mobile=self.mobile,
            send=AsyncMock(side_effect=records.append),
            receive=AsyncMock(return_value={'kind': 'credit', 'count': 4}),
            cipher=SimpleNamespace(encrypt=lambda value: value),
            record=AsyncMock(side_effect=lambda value: chunks.append(value[1:])))
        await self.connector.forward_http(channel, {'method': method, 'path': path,
            'content_type': 'application/json', 'headers': {}}, body)
        self.assertEqual(records[0]['status'], 200)
        self.assertEqual(records[-1], {'kind': 'response_end'})
        return json.loads(b''.join(chunks))

    async def test_connected_binding_survives_twelve_hours_and_direct_sessions_still_expire(self):
        self.assertTrue((await self.forward())['authenticated'])
        token, session = self.connector.sessions[self.binding]
        direct, _ = self.gateway.auth.new_session('127.0.0.1', 'synthetic direct browser')
        with patch('bridge.features.auth.auth.time.time', return_value=session['created'] + 13 * 3600):
            for _ in range(2):
                self.assertTrue((await self.forward())['authenticated'])
            self.assertEqual(self.connector.sessions[self.binding][0], token)
            self.assertIsNone(self.gateway.auth.get(direct))

    async def test_explicit_local_logout_does_not_reissue_a_session(self):
        await self.forward()
        token, session = self.connector.sessions[self.binding]
        await self.forward('POST', '/api/logout', b'{}')
        with patch.object(self.server.auth, 'new_session', wraps=self.server.auth.new_session) as create:
            with patch('bridge.features.auth.auth.time.time', return_value=session['created'] + 13 * 3600):
                for _ in range(2):
                    with self.assertRaisesRegex(PermissionError, 'local session revoked'):
                        await self.forward()
            create.assert_not_called()
        self.assertIsNone(self.server.auth.get(token))

    async def test_access_close_revokes_cached_session_and_reopen_needs_current_permission(self):
        await self.forward()
        old, _ = self.connector.sessions[self.binding]
        self.set_access(False)
        await self.connector.block_binding(self.binding)
        self.assertNotIn(self.binding, self.connector.sessions)
        self.assertIsNone(self.server.auth.get(old))
        with self.assertRaisesRegex(PermissionError, 'binding revoked'):
            await self.forward()
        self.set_access(True)
        self.assertTrue((await self.forward())['authenticated'])
        self.assertNotEqual(self.connector.sessions[self.binding][0], old)

    async def test_logout_entitlement_expiry_and_changed_keys_cannot_use_cached_session(self):
        await self.forward()
        self.connector.entitlement_deadline = time.monotonic() - 1
        with self.assertRaisesRegex(PermissionError, 'binding revoked'):
            await self.forward()
        self.connector.entitlement_deadline = None
        write_json(self.connector.accounts.path, {})
        with self.assertRaisesRegex(PermissionError, 'binding revoked'):
            await self.forward()
        write_json(self.connector.accounts.path, self.config)
        self.mobile = b'\x02' + b'\0' * 31
        with self.assertRaisesRegex(PermissionError, 'binding revoked'):
            await self.forward()

    async def test_disconnect_clears_internal_authority_without_touching_direct_session(self):
        await self.forward()
        cloud, _ = self.connector.sessions[self.binding]
        direct, _ = self.gateway.auth.new_session('127.0.0.1', 'synthetic direct browser')
        self.connector.clear_authority()
        self.assertEqual(self.connector.sessions, {})
        self.assertIsNone(self.server.auth.get(cloud))
        self.assertIsNotNone(self.gateway.auth.get(direct))

    async def test_unknown_post_outcome_is_never_replayed(self):
        await self.forward()
        with patch.object(self.local, 'request', side_effect=OSError('synthetic lost response')) as request:
            with self.assertRaisesRegex(OSError, 'synthetic lost response'):
                await self.forward('POST', '/api/logout', b'{}')
            request.assert_called_once()
