"""Cloud listener assembly uses synthetic services, not real accounts or clients."""
import ast
import asyncio
import builtins
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bridge.app.cloud as assembly
from bridge.features.auth.auth import Auth
from bridge.features.auth.pairing import Pairing
from bridge.features.network.cloud_connector import Connector


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('transfer_directory', 'mobile_updates', 'notifications', 'desktop_sessions',
            'desktop_uploads', 'harness', 'ui_language_path', 'slots', 'terminal_sockets')


class CloudAssemblyTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / '.tmp' / 'cloud-boundary'
        root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        auth = Auth({'mode': 'none'})
        origins = {'https://direct.example.test'}
        self.gateway = SimpleNamespace(
            bridge=Mock(), web_dir=ROOT / 'web', auth=auth, origins=origins,
            pairing=Pairing(auth, origins), hosts={'direct.example.test'},
            secure_hosts={'direct.example.test'}, instance_id='synthetic-direct-instance',
            transfer_directory=self.directory, mobile_updates=Mock(), notifications=None,
            desktop_sessions=None, desktop_uploads=Mock(), harness=Mock(),
            ui_language_path=self.directory / 'ui-language.json',
            slots=threading.BoundedSemaphore(48), terminal_sockets=threading.BoundedSemaphore(8))

    def connector(self):
        connector = Connector(self.directory, self.gateway)
        self.addCleanup(connector.notifications.close)
        return connector

    @staticmethod
    def fake_listener():
        return SimpleNamespace(server_port=18787, origins=set(), hosts=set(), auth=Mock(),
                               pairing=Mock(), serve_forever=Mock(), shutdown=Mock(),
                               server_close=Mock())

    def test_application_builds_a_loopback_listener_with_an_independent_auth_policy(self):
        listener = self.fake_listener()
        auth, pairing = listener.auth, listener.pairing
        with patch.object(assembly, 'GatewayServer', return_value=listener) as factory:
            result = assembly.create_cloud_server(self.gateway)
        factory.assert_called_once_with(
            ('127.0.0.1', 0), self.gateway.bridge,
            {'auth': {'mode': 'password', 'sessionHours': 0}, 'origins': [], 'localAccess': True},
            self.gateway.web_dir)
        self.assertIs(result, listener)
        for name in SERVICES:
            self.assertIs(getattr(listener, name), getattr(self.gateway, name), name)
        self.assertIs(listener.auth, auth)
        self.assertIs(listener.pairing, pairing)
        self.assertEqual(listener.origins, {'http://127.0.0.1:18787'})
        self.assertEqual(listener.hosts, {'127.0.0.1:18787'})
        self.assertEqual(self.gateway.origins, {'https://direct.example.test'})
        listener.serve_forever.assert_not_called()

    def test_real_listener_shares_services_but_not_auth_pairing_or_cookie_namespaces(self):
        server = assembly.create_cloud_server(self.gateway)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], '127.0.0.1')
        self.assertGreater(server.server_port, 0)
        self.assertEqual(server.auth.config, {'mode': 'password', 'sessionHours': 0})
        self.assertIsNone(server.auth.path)
        self.assertIsNot(server.auth, self.gateway.auth)
        self.assertIsNot(server.pairing, self.gateway.pairing)
        self.assertIs(server.pairing.auth, server.auth)
        self.assertIs(server.pairing.origins, server.origins)
        self.assertNotEqual(server.auth.cookie_name, self.gateway.auth.cookie_name)
        self.assertNotEqual(server.instance_id, self.gateway.instance_id)
        self.assertEqual(server.secure_hosts, set())
        self.assertFalse(server.mobile_updates_owner)
        for name in SERVICES:
            self.assertIs(getattr(server, name), getattr(self.gateway, name), name)
        token, _ = self.gateway.auth.new_session('127.0.0.1', 'synthetic-direct-client')
        self.assertIsNone(server.auth.get(token))
        token, _ = server.auth.new_session('127.0.0.1', 'synthetic-cloud-client')
        self.assertIsNone(self.gateway.auth.get(token))
        server.server_close()
        self.gateway.mobile_updates.close.assert_not_called()
        self.gateway.harness.close.assert_not_called()

    def test_failed_assembly_closes_only_the_new_listener(self):
        gateway = self.gateway
        class BrokenGateway:
            def __getattr__(self, name):
                if name == 'ui_language_path':
                    raise RuntimeError('synthetic assembly failure')
                return getattr(gateway, name)
        listener = self.fake_listener()
        with patch.object(assembly, 'GatewayServer', return_value=listener):
            with self.assertRaisesRegex(RuntimeError, 'synthetic assembly failure'):
                assembly.create_cloud_server(BrokenGateway())
        listener.server_close.assert_called_once_with()
        listener.serve_forever.assert_not_called()
        gateway.mobile_updates.close.assert_not_called()
        gateway.harness.close.assert_not_called()

    def test_missing_dependencies_fail_before_creating_a_listener_or_threads(self):
        real_import = builtins.__import__
        for missing in ('aiohttp', 'noise.connection'):
            with self.subTest(module=missing):
                connector, factory = self.connector(), Mock()
                def load(name, *args, **kwargs):
                    if name == missing:
                        raise ModuleNotFoundError('synthetic missing dependency')
                    return real_import(name, *args, **kwargs)
                with patch('builtins.__import__', side_effect=load), \
                        patch('bridge.features.network.cloud_connector.threading.Thread') as thread:
                    with self.assertRaises(ModuleNotFoundError):
                        connector.start(factory)
                factory.assert_not_called()
                thread.assert_not_called()
                self.assertFalse(hasattr(connector, 'server'))
                self.assertIsNone(connector.http_thread)
                self.assertIsNone(connector.thread)

    def test_failed_listener_factory_does_not_start_any_runtime(self):
        connector = self.connector()
        factory = Mock(side_effect=OSError('synthetic listener failure'))
        with patch('bridge.features.network.cloud_connector.threading.Thread') as thread:
            with self.assertRaisesRegex(OSError, 'synthetic listener failure'):
                connector.start(factory)
        factory.assert_called_once_with()
        thread.assert_not_called()
        self.assertFalse(hasattr(connector, 'server'))

    def test_connector_uses_the_injected_listener_and_keeps_its_lifecycle(self):
        connector, listener = self.connector(), self.fake_listener()
        factory = Mock(return_value=listener)
        http_thread, cloud_thread = Mock(), Mock()
        cloud_thread.is_alive.return_value = False
        with patch('bridge.features.network.cloud_connector.threading.Thread',
                   side_effect=[http_thread, cloud_thread]) as thread:
            connector.start(factory)
        factory.assert_called_once_with()
        self.assertIs(connector.server, listener)
        self.assertEqual(connector.local_origin, 'http://127.0.0.1:18787')
        self.assertEqual(thread.call_args_list[0].kwargs, {'target': listener.serve_forever, 'daemon': True})
        self.assertEqual(thread.call_count, 2)
        http_thread.start.assert_called_once_with()
        cloud_thread.start.assert_called_once_with()
        connector.close()
        listener.shutdown.assert_called_once_with()
        http_thread.join.assert_called_once_with(timeout=5)
        listener.server_close.assert_called_once_with()
        self.gateway.mobile_updates.close.assert_not_called()

    def test_entry_point_supplies_the_application_factory_lazily(self):
        tree = ast.parse((ROOT / 'run.py').read_text(encoding='utf-8'))
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                   and node.module == 'bridge.app.cloud']
        self.assertEqual([entry.name for node in imports for entry in node.names], ['create_cloud_server'])
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == 'cloud' and node.func.attr == 'start']
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0].args[0], ast.Lambda)
        self.assertEqual(ast.unparse(calls[0].args[0].body), 'create_cloud_server(server)')

    def test_loopback_http_preserves_auth_origin_csrf_and_listener_shutdown(self):
        connector = self.connector()
        started = threading.Event()
        # Exercise only injected HTTP lifecycle, never the outbound cloud transport.
        async def idle():
            started.set()
            while not connector.closed.is_set():
                await asyncio.sleep(.01)
            await asyncio.sleep(.05)
        connector.run = idle
        connector.accounts.request = Mock(side_effect=AssertionError('no real cloud requests'))
        factory = Mock(side_effect=lambda: assembly.create_cloud_server(self.gateway))
        try:
            connector.start(factory)
            self.assertTrue(started.wait(3), 'isolated connector worker did not start')
            server = connector.server
            def request(method, path, headers=None, body=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.request(method, path, headers={'Connection': 'close', **(headers or {})}, body=body)
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            code, result = request('GET', '/api/auth')
            self.assertEqual(code, 200)
            self.assertFalse(result['authenticated'])
            self.assertFalse(result['passwordless'])
            self.assertEqual(request('GET', '/api/auth', {'Host': 'other.example.test'})[0], 403)
            self.assertEqual(request('GET', '/api/auth', {'Origin': 'https://direct.example.test'})[0], 403)
            direct, _ = self.gateway.auth.new_session('127.0.0.1', 'synthetic-direct-client')
            self.assertEqual(request('POST', '/api/logout', {
                'Origin': connector.local_origin, 'Content-Type': 'application/json',
                'Cookie': self.gateway.auth.cookie_name + '=' + direct}, b'{}')[0], 401)
            token, session = server.auth.new_session('127.0.0.1', 'synthetic-cloud-client')
            headers = {'Cookie': server.auth.cookie_name + '=' + token,
                       'Origin': connector.local_origin, 'Content-Type': 'application/json'}
            self.assertEqual(request('POST', '/api/logout', {**headers, 'Origin': 'https://direct.example.test'}, b'{}')[0], 403)
            self.assertEqual(request('POST', '/api/logout', headers, b'{}')[0], 403)
            self.assertEqual(request('POST', '/api/logout', {**headers, 'X-CSRF-Token': session['csrf']}, b'{}')[0], 200)
            self.assertIsNone(server.auth.get(token))
            self.assertIsNotNone(self.gateway.auth.get(direct))
            connector.accounts.request.assert_not_called()
            factory.assert_called_once_with()
        finally:
            connector.close()
        self.assertFalse(connector.http_thread.is_alive())
        self.assertFalse(connector.thread.is_alive())
        self.assertTrue(connector.loop.is_closed())
        self.assertEqual(connector.server.socket.fileno(), -1)
        self.gateway.mobile_updates.close.assert_not_called()
        self.gateway.harness.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
