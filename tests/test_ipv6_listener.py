import http.client
import json
import socket
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import run

from bridge.api.httpd import GatewayServer
from test_bridge import ROOT


class IPv6ListenerTests(unittest.TestCase):
    def test_ipv6_loopback_real_http_and_host_origin_checks(self):
        if not socket.has_ipv6:
            self.skipTest('IPv6 unavailable on this host')
        config = {'auth': {'mode': 'none'}, 'origins': []}
        try:
            server = GatewayServer(('::1', 0), SimpleNamespace(), config, ROOT / 'web')
        except OSError as exc:
            if exc.errno in (47, 49, 97, 99):
                self.skipTest('IPv6 loopback unavailable: ' + str(exc.errno))
            raise
        self.addCleanup(server.server_close)
        self.assertEqual(server.address_family, socket.AF_INET6)
        self.assertEqual(server.socket.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 1)
        port = server.server_port
        authority = '[::1]:' + str(port)
        server.hosts.add(authority)
        server.origins.add('http://' + authority)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        def request(headers):
            connection = http.client.HTTPConnection('::1', port, timeout=3)
            try:
                connection.request('GET', '/style.css', headers=headers)
                response = connection.getresponse()
                response.read()
                return response.status
            finally:
                connection.close()
        self.assertEqual(request({'Origin': 'http://' + authority}), 200)
        self.assertEqual(request({'Host': 'wrong.example', 'Origin': 'http://' + authority}), 403)
        self.assertEqual(request({'Origin': 'https://wrong.example'}), 403)
        server.local_access = False
        self.assertEqual(request({'Origin': 'http://' + authority}), 403)

    def test_ipv4_default_remains_ipv4(self):
        server = GatewayServer(('127.0.0.1', 0), SimpleNamespace(), {'auth': {'mode': 'none'}, 'origins': []}, ROOT / 'web')
        self.addCleanup(server.server_close)
        self.assertEqual(server.address_family, socket.AF_INET)


class IPv6StartupTests(unittest.TestCase):
    def startup(self, arguments, *, authenticated=True):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory, ExitStack() as stack:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'auth': {'mode': 'password' if authenticated else 'none'}, 'origins': []}))
            stack.enter_context(patch.object(run.sys, 'argv', ['run.py', '--config', str(config), *arguments]))
            for module, name in [(run.sys, 'stdout'), (run.sys, 'stderr'), (run.os, 'umask'),
                                 (run.signal, 'signal'), (run, 'Bridge'), (run, 'GatewayControl'),
                                 (run, 'DynamicListenerManager'), (run, 'NetworkAdaptation')]:
                stack.enter_context(patch.object(module, name))
            stack.enter_context(patch.object(run.interfaces, 'enumerate_interfaces', return_value=[]))
            monitor = MagicMock()
            monitor.scan.side_effect = RuntimeError('fixture startup complete')
            stack.enter_context(patch.object(run, 'RuntimeNetworkMonitor', return_value=monitor))
            stack.enter_context(patch.object(run, 'adaptation_settings', return_value={'enabled': False}))
            factory = stack.enter_context(patch.object(run, 'GatewayServer'))
            try:
                run.main(connections=[])
            except RuntimeError as error:
                self.assertEqual(str(error), 'fixture startup complete')
            return factory.call_args_list

    def test_ipv6_deduplicates_and_keeps_ipv4_and_exact_origins(self):
        calls = self.startup(['--lan', '--listen-ipv6', '0:0:0:0:0:0:0:1', '--listen-ipv6', '::1',
                              '--listen-ipv6', '::', '--origin', 'https://[2001:db8::42]:8443'])
        self.assertEqual([call.args[0][0] for call in calls], ['127.0.0.1', '::1', '::'])
        origins = calls[0].args[2]['origins']
        self.assertIn('http://[::1]:8787', origins)
        self.assertIn('https://[2001:db8::42]:8443', origins)
        self.assertNotIn('http://[::]:8787', origins)
        self.assertIn('shared', calls[1].kwargs)

    def test_invalid_or_unauthenticated_external_ipv6_is_rejected(self):
        for address in ['127.0.0.1', 'fe80::1%en0', 'not-an-address']:
            with self.subTest(address=address), self.assertRaises(SystemExit):
                self.startup(['--listen-ipv6', address])
        with self.assertRaises(SystemExit):
            self.startup(['--listen-ipv6', '::', '--no-auth'])

    def test_ipv6_loopback_allows_existing_passwordless_mode(self):
        calls = self.startup(['--listen-ipv6', '::1'], authenticated=False)
        self.assertEqual([call.args[0][0] for call in calls], ['127.0.0.1', '::1'])
