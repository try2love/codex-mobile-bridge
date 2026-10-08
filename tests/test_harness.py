import base64
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.harness import Harness, save, command
from bridge.desktop import Desktop
from bridge.lifecycle import GatewayControl, request_pairing
import test_bridge

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'tests/fixtures/harness_runtime.py'


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        original_command = command
        launcher = patch('bridge.harness.command', side_effect=lambda path: [sys.executable, path] if path == str(FIXTURE) else original_command(path))
        launcher.start(); self.addCleanup(launcher.stop)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.runtime = Harness(self.directory)
        self.addCleanup(self.runtime.close)
        self.config = {'executable': str(FIXTURE), 'workspace': str(self.directory), 'home': str(self.directory/'home')}
        save(self.directory, self.config)

    def test_start_handshake_private_logs_and_stop(self):
        value = self.runtime.control({'action': 'start'})
        self.assertTrue(value['running'])
        process = self.runtime.process
        self.assertTrue(self.runtime.cookie.startswith('dsh-auth-fixture='))
        self.assertNotIn('fixture_cookie', json.dumps(value))
        self.assertNotIn('launch_token', json.dumps(value))
        self.assertNotIn('API_KEY', json.dumps(value))
        self.assertEqual(set(self.runtime.status()), {'running', 'state', 'url'})
        invocation = json.loads((self.directory/'home/fixture.json').read_text())
        self.assertEqual(invocation['cwd'], str(self.directory))
        self.assertEqual(invocation['argv']['profile'], 'web')
        self.runtime.control({'action': 'start'})
        self.assertIs(self.runtime.process, process)
        with self.assertRaises(ValueError): self.runtime.control({'action': 'save', 'config': self.config})
        self.runtime.cookie_until = 0
        self.assertTrue(self.runtime.endpoint()[1])
        self.runtime.control({'action': 'stop'})
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.runtime.cookie)
        with self.assertRaises(ValueError): self.runtime.endpoint()

    def test_local_control_and_offline_config(self):
        desktop = Desktop(self.directory)
        with patch.object(desktop, 'status', return_value={'running': False}):
            self.assertEqual(desktop.harness({'action': 'status'})['config'], self.config)
            with self.assertRaises(ValueError): desktop.harness({'action': 'start'})
        control = GatewayControl(self.directory)
        control.start(lambda: None, lambda value: None, harness=self.runtime.control)
        self.addCleanup(control.close)
        value = request_pairing(self.directory, {'action': 'harness', 'value': {'action': 'start'}}, timeout=8)
        self.assertTrue(value['running'])
        self.assertTrue(control.record['harnessManagement'])

    def test_failure_validation_and_shutdown(self):
        with self.assertRaises(ValueError): command('untrusted.cmd')
        with self.assertRaises(ValueError): save(self.directory, {**self.config, 'workspace': '/does/not/exist'})
        with patch('bridge.harness.subprocess.Popen', side_effect=OSError('private value')):
            with self.assertRaises(ValueError) as error: self.runtime.control({'action': 'start'})
        self.assertNotIn('private value', str(error.exception))
        self.assertEqual(self.runtime.phase, 'failed')
        self.runtime.close()
        with self.assertRaises(ValueError): self.runtime.control({'action': 'start'})


class ProxyTests(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def setUp(self):
        test_bridge.HttpTests.setUp(self)
        original_command = command
        launcher = patch('bridge.harness.command', side_effect=lambda path: [sys.executable, path] if path == str(FIXTURE) else original_command(path))
        launcher.start(); self.addCleanup(launcher.stop)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.runtime = Harness(self.temp.name)
        save(self.temp.name, {'executable': str(FIXTURE), 'workspace': self.temp.name, 'home': self.temp.name+'/home'})
        self.runtime.control({'action': 'start'})
        self.server.harness = self.runtime

    def tearDown(self):
        self.runtime.close()
        test_bridge.HttpTests.tearDown(self)
        self.temp.cleanup()

    def raw(self, method='GET', path='/harness/', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_auth_paths_status_and_no_management_api(self):
        status, headers, _ = self.raw()
        self.assertEqual(status, 303); self.assertEqual(headers['Location'], '/?open=harness')
        self.assertEqual(self.raw(headers={'Sec-Fetch-Site':'cross-site', 'Sec-Fetch-Mode':'navigate', 'Sec-Fetch-Dest':'document'})[0], 303)
        self.assertEqual(self.raw(path='/harness/assets.js')[0], 401)
        self.assertEqual(self.request('GET', '/api/harness')[0], 401)
        login = self.login()
        self.assertTrue(self.request('GET', '/api/harness', headers=login)[2]['running'])
        self.assertIn(b'crossorigin="use-credentials"', self.raw(headers=login)[2])
        self.assertEqual(self.request('POST', '/api/harness/start', {}, login)[0], 404)
        self.assertEqual(self.raw(path='/harness', headers=login)[1]['Location'], '/harness/')
        for path in ('/harness/../api/auth', '/harness/%2e%2e/api/auth', '/harness//example.org/', '/harness/a%5cb'):
            self.assertEqual(self.raw(path=path, headers=login)[0], 400, path)
        self.runtime.stop()
        self.assertEqual(self.raw(headers=login)[0], 503)

    def test_cookie_header_isolation_and_streaming(self):
        login = self.login()
        headers = {**login, 'Authorization': 'Bearer bridge-secret', 'X-Forwarded-For': '203.0.113.1', 'Forwarded': 'evil'}
        status, outgoing, body = self.raw(path='/harness/inspect?value=1', headers=headers)
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value['path'], '/inspect?value=1')
        self.assertEqual(value['headers']['Cookie'], 'dsh-auth-fixture=private_fixture_cookie')
        for key in ('Authorization', 'X-CSRF-Token', 'Forwarded', 'X-Forwarded-For'):
            self.assertNotIn(key, value['headers'])
        self.assertNotIn('dsh-auth', outgoing.get('Set-Cookie', ''))
        self.assertEqual(self.raw(path='/harness/redirect', headers=login)[1]['Location'], '/harness/download')
        data = bytes(range(256)) * 8192
        self.assertEqual(self.raw(path='/harness/download', headers=login)[2], data)
        self.assertEqual(self.raw('HEAD', '/harness/download', headers=login)[2], b'')
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            status, _, body = self.raw(method, '/harness/upload', data, {**login, 'Origin': self.origin})
            self.assertEqual(status, 200); self.assertEqual(body, data)

    def test_exact_origin_and_ip_rules(self):
        login = self.login()
        self.server.origins.add('https://other.example')
        for origin in (None, 'https://other.example', 'https://evil.example'):
            headers = {**login, **({'Origin': origin} if origin else {})}
            self.assertEqual(self.raw('POST', '/harness/api/action', b'{}', headers)[0], 403)
        self.assertEqual(self.raw(headers={**login, 'Sec-Fetch-Site': 'cross-site'})[0], 403)
        with patch.object(self.server.auth, 'permitted', return_value=False):
            self.assertEqual(self.raw(headers=login)[0], 403)
        self.assertEqual(self.raw('POST', '/harness/upload', headers={**login, 'Origin': self.origin, 'Transfer-Encoding': 'chunked'})[0], 400)
        body=b'framing'
        self.assertEqual(self.raw('POST', '/harness/upload', body, {**login, 'Origin': self.origin, 'Connection': 'Content-Length'})[2], body)

    def ws(self, login, origin=None, early=b''):
        conn = socket.create_connection(('127.0.0.1', self.port), timeout=4)
        key = base64.b64encode(b'0123456789abcdef').decode()
        request = (f'GET /harness/api/remote.mux HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                   f'Origin: {origin or self.origin}\r\nCookie: {login["Cookie"]}\r\n'
                   f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        conn.sendall(request.encode() + early); response = b''
        while not response.endswith(b'\r\n\r\n'): response += conn.recv(1)
        return conn, response

    def test_ws_immediate_frame_transfer_and_revocation(self):
        login = self.login()
        early = b'\x81\x80mask'
        conn, response = self.ws(login, early=early)
        self.addCleanup(conn.close)
        self.assertIn(b'101 Switching Protocols', response)
        self.assertEqual(conn.recv(7), b'\x81\x05ready')
        self.assertEqual(conn.recv(len(early)), early)
        frame = b'\x81\x80mask'
        conn.sendall(frame); self.assertEqual(conn.recv(len(frame)), frame)
        self.server.auth.logout(login['Cookie'].split('=', 1)[1])
        self.assertEqual(conn.recv(1), b'')
        conn, response = self.ws(self.login(), 'https://evil.example')
        conn.close(); self.assertIn(b'403', response)

    def test_ws_closed_on_runtime_stop(self):
        conn, _ = self.ws(self.login())
        self.addCleanup(conn.close)
        conn.recv(7)
        self.runtime.stop()
        self.assertEqual(conn.recv(1), b'')


if __name__ == '__main__': unittest.main()
