import copy
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from bridge import network
from bridge.desktop import Desktop
from bridge.httpd import GatewayServer
from bridge.lifecycle import request_stop
import run

ROOT = Path(__file__).resolve().parents[1]


class SelectionTests(unittest.TestCase):
    def test_all_selected_empty_and_disabled_have_distinct_bindings(self):
        self.assertEqual(network.bindings({'lan': True}), ['0.0.0.0'])
        self.assertEqual(network.bindings({'lan': True, 'lanAddresses': []}), ['127.0.0.1'])
        self.assertEqual(network.bindings({'lan': True, 'lanAddresses': ['192.168.1.5', '172.20.0.1']}), ['127.0.0.1', '192.168.1.5', '172.20.0.1'])
        self.assertEqual(network.bindings({'lan': False, 'lanAddresses': ['192.168.1.5']}), ['127.0.0.1'])
        for value in ['all', [1], ['0.0.0.0'], ['127.0.0.1'], ['::1'], ['example.com'], ['224.0.0.1'], ['255.255.255.255']]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                network.selected_addresses(value)

    def test_desktop_adopts_older_gateway_without_private_health_route(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            desktop = Desktop(folder)
            legacy = MagicMock(status=200)
            legacy.read.return_value = b'{"authenticated":false,"passwordless":false,"instanceId":"legacy","notifications":true}'
            unknown = MagicMock(status=401)
            unknown.read.return_value = b'{"error":"login"}'
            with patch('bridge.desktop.http.client.HTTPConnection') as connect, patch('bridge.desktop.read_record', return_value={'pid': 42, 'instanceId': 'legacy'}):
                connect.return_value.getresponse.side_effect = [unknown, legacy]
                self.assertTrue(desktop.status()['running'])
                self.assertEqual([call.args for call in connect.return_value.request.call_args_list], [('GET', '/api/health'), ('GET', '/api/auth')])

    def test_persistence_and_unavailable_selection_never_falls_back(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            desktop = Desktop(folder)
            desktop.status = lambda: {'running': False}
            value = desktop.snapshot()
            value['preferences'].update(codexHome=folder, lanAddresses=['192.0.2.7'], localAccess=False)
            saved = desktop.save(value)
            self.assertEqual(saved['urls'], ['http://192.0.2.7:8787/'])
            self.assertEqual(desktop.config()['lanAddresses'], ['192.0.2.7'])
            self.assertFalse(desktop.config()['localAccess'])
            with patch('bridge.desktop.socket.socket') as sock, patch('bridge.desktop.subprocess.Popen') as spawn:
                sock.return_value.bind.side_effect = OSError('address unavailable')
                with self.assertRaisesRegex(ValueError, '不会自动开放'):
                    desktop.start()
                spawn.assert_not_called()
            invalid = copy.deepcopy(value)
            invalid['preferences']['lanAddresses'] = ['0.0.0.0']
            with self.assertRaises(ValueError):
                desktop.save(invalid)
            self.assertEqual(desktop.preferences()['lanAddresses'], ['192.0.2.7'])
            desktop.status = lambda: {'running': True}
            value['preferences']['localAccess'] = True
            with self.assertRaisesRegex(ValueError, '先停止'):
                desktop.save(value)


class LocalAccessTests(unittest.TestCase):
    def test_local_browser_blocked_but_private_health_and_tunnel_work(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            config = {'auth': {'mode': 'none'}, 'origins': ['https://phone.example.com'], 'localAccess': False}
            server = GatewayServer(('127.0.0.1', 0), SimpleNamespace(accounts=SimpleNamespace()), config, ROOT/'web', Path(folder))
            port = server.server_port
            host = f'127.0.0.1:{port}'
            server.hosts.update([host, f'192.0.2.7:{port}'])
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(path, authority=host, origin=None):
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                try:
                    headers = {'Host': authority}
                    if origin: headers['Origin'] = origin
                    conn.request('GET', path, headers=headers)
                    response = conn.getresponse()
                    return response.status, response.read()
                finally:
                    conn.close()
            try:
                for path in ['/', '/api/auth', '/api/sessions']:
                    self.assertEqual(request(path)[0], 403)
                # A LAN Host header cannot turn the private loopback listener into a browser entry.
                self.assertEqual(request('/', f'192.0.2.7:{port}')[0], 403)
                code, body = request('/api/health')
                self.assertEqual(code, 200)
                self.assertEqual(set(json.loads(body)), {'service', 'instanceId', 'notifications'})
                self.assertEqual(request('/api/health', origin='https://phone.example.com')[0], 403)
                self.assertEqual(request('/api/health', 'phone.example.com')[0], 403)
                self.assertEqual(request('/api/auth', 'phone.example.com')[0], 200)
                other = GatewayServer(('127.0.0.1', 0), server.bridge, config, ROOT/'web', Path(folder), shared=server)
                try:
                    for field in ['auth', 'pairing', 'origins', 'hosts', 'slots']:
                        self.assertIs(getattr(other, field), getattr(server, field))
                    self.assertEqual(other.instance_id, server.instance_id)
                    server.origins.add('https://new.trycloudflare.com')
                    self.assertIn('https://new.trycloudflare.com', other.origins)
                finally:
                    other.server_close()
            finally:
                server.shutdown();thread.join();server.server_close()

    def test_real_gateway_binds_only_selected_address_and_stops_every_listener(self):
        lan = next((ip for ip in run.addresses() if ip not in ('127.0.0.1', 'localhost')), None)
        if not lan:
            self.skipTest('No non-loopback IPv4 adapter available')
        for selected in ([], [lan]):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
                data = Path(folder)
                config = data/'config.json'
                config.write_text(json.dumps({'auth': {'mode': 'none'}, 'origins': [], 'lanAddresses': selected, 'localAccess': False}), encoding='utf8')
                with socket.socket() as probe:
                    probe.bind(('0.0.0.0', 0));port = probe.getsockname()[1]
                (data/'desktop.json').write_text(json.dumps({'port': port}), encoding='utf8')
                with (data/'log').open('wb') as log:
                    child = subprocess.Popen([sys.executable, '-B', str(ROOT/'run.py'), '--lan', '--config', str(config), '--codex-home', folder, '--port', str(port)], stdout=log, stderr=log)
                    try:
                        desktop = Desktop(data)
                        end = time.monotonic() + 15
                        while not desktop.status()['running'] and time.monotonic() < end and child.poll() is None:
                            time.sleep(.05)
                        self.assertTrue(desktop.status()['running'], (data/'log').read_text(encoding='utf8', errors='replace'))
                        conn = http.client.HTTPConnection(lan, port, timeout=2)
                        try:
                            if selected:
                                conn.request('GET', '/api/auth')
                                response = conn.getresponse()
                                self.assertEqual(response.status, 200)
                                self.assertEqual(json.loads(response.read())['instanceId'], desktop.status()['instanceId'])
                                conn.request('GET', '/api/health', headers={'Host': f'127.0.0.1:{port}'})
                                response = conn.getresponse();self.assertEqual(response.status, 403);response.read()
                            else:
                                with self.assertRaises(OSError):
                                    conn.connect()
                        finally:
                            conn.close()
                        request_stop(data)
                        child.wait(timeout=10)
                        self.assertEqual(child.returncode, 0)
                        for address in ['127.0.0.1'] + selected:
                            with socket.socket() as probe:
                                probe.settimeout(1)
                                self.assertNotEqual(probe.connect_ex((address, port)), 0)
                    finally:
                        if child.poll() is None:
                            child.terminate();child.wait(timeout=10)
