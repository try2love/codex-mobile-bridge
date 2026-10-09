"""Connector transport boundaries; servers and credentials are isolated fixtures."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from bridge.integrations.deepseek import DeepSeek
from bridge.integrations.errors import BridgeUnavailable

ROOT = Path(__file__).resolve().parents[1]


class DeepSeekTransportTests(unittest.TestCase):
    def server(self, handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        def close():
            server.shutdown(); thread.join(timeout=2); server.server_close()
        self.addCleanup(close)
        return server

    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.received, self.forwarded = [], []
        self.status = 200
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                owner.received.append((self.path, self.headers.get('Authorization')))
                data = json.dumps({'sessions': [], 'error': 'Fixture rejected'}).encode()
                self.send_response(owner.status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                if 300 <= owner.status < 400:
                    self.send_header('Location', 'http://127.0.0.1:'+str(owner.target.server_port)+'/redirected')
                self.end_headers(); self.wfile.write(data)
        class Target(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                owner.forwarded.append(self.headers.get('Authorization'))
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')
            do_POST = do_GET
        self.target = self.server(Target)
        self.source = self.server(Handler)
        directory = Path(self.temp.name)
        (directory/'connection.json').write_text(json.dumps({'token': 'fixture-secret'}))
        (directory/'endpoint.json').write_text(json.dumps({'port': self.source.server_port}))
        self.adapter = DeepSeek(directory, directory/'home')

    def test_redirects_never_forward_connector_credentials(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(status=code):
                self.status = code
                self.forwarded.clear()
                rejected = False
                try: self.adapter.call('list')
                except BridgeUnavailable: rejected = True
                self.assertEqual(self.forwarded, [], 'Connector credential reached the redirect destination')
                self.assertTrue(rejected)

    def test_direct_request_ignores_proxy_environment_and_keeps_authentication(self):
        proxy = 'http://127.0.0.1:'+str(self.target.server_port)
        with patch.dict('os.environ', {'http_proxy': proxy, 'HTTP_PROXY': proxy, 'no_proxy': '', 'NO_PROXY': ''}):
            self.assertEqual(self.adapter.call('list')['sessions'], [])
        self.assertEqual(self.received, [('/mobile', 'Bearer fixture-secret')])
        self.assertEqual(self.forwarded, [])

    def test_native_error_remains_an_adapter_error(self):
        self.status = 401
        with self.assertRaisesRegex(BridgeUnavailable, 'Fixture rejected'):
            self.adapter.call('list')
        self.assertEqual(self.forwarded, [])


if __name__ == '__main__': unittest.main()
