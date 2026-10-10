#!/usr/bin/env python3
"""Credential-bearing Harness protocol fixture; never runs an agent."""
import argparse
import base64
import hashlib
import json
import os
import socket
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--profile'); parser.add_argument('--no-open', action='store_true')
parser.add_argument('--port', type=int); parser.add_argument('--public-url')
args = parser.parse_args()
TOKEN = 'fixture_launch_token_never_expose_0123456789'
COOKIE = 'dsh-auth-fixture=private_fixture_cookie'
Path(os.environ['DSH_HOME'], 'fixture.json').write_text(json.dumps({'argv': vars(args), 'cwd': os.getcwd()}))

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == '/?token=' + TOKEN:
            self.send_response(303); self.send_header('Set-Cookie', COOKIE + '; Path=/; HttpOnly; Max-Age=3600')
            self.send_header('Location', './'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if self.headers.get('Cookie') != COOKIE:
            self.send_error(401); return
        if self.path == '/api/remote.mux':
            key = self.headers['Sec-WebSocket-Key']
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101); self.send_header('Upgrade', 'websocket'); self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept); self.end_headers()
            self.connection.sendall(b'\x81\x05ready')
            self.connection.settimeout(5)
            try:
                while True:
                    data = self.connection.recv(65536)
                    if not data: break
                    self.connection.sendall(data)
            except OSError: pass
            self.close_connection = True; return
        if self.path.startswith('/redirect'):
            self.send_response(302); self.send_header('Location', '/download'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if self.path.startswith('/download'):
            data = bytes(range(256)) * 8192
        elif self.path == '/':
            data = b'<html><head><base href="./"><link rel="manifest" href="manifest.webmanifest"></head><body>Harness fixture</body></html>'
        else:
            data = json.dumps({'path': self.path, 'headers': dict(self.headers)}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Type', 'text/html' if self.path == '/' else 'application/octet-stream')
        self.send_header('Set-Cookie', 'dsh-auth-fixture=must_not_escape')
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)
    do_HEAD = do_GET
    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

class FixtureServer(ThreadingHTTPServer):
    def server_bind(self):
        # This loopback fixture must not wait on the runner's reverse DNS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

server = FixtureServer(('127.0.0.1', args.port), Handler)
print('API_KEY=should_never_reach_gateway_logs', flush=True)
print('dsh web: ' + f'http://127.0.0.1:{args.port}/?token=' + TOKEN, flush=True)
server.serve_forever()
