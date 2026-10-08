#!/usr/bin/env python3
"""Run the production Foundation downloader against local bounded HTTP fixtures."""
import re
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'.tmp/download-tests'
WORK.mkdir(parents=True, exist_ok=True)
scratch = WORK/'files'
scratch.mkdir(exist_ok=True)
content = bytes(range(251)) * ((3 * 1024 * 1024 + 301)//251 + 1)
content = content[:3 * 1024 * 1024 + 301]
requests, redirected = [], []


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_): pass

    def handle(self):
        try: super().handle()
        except (BrokenPipeError, ConnectionResetError): pass

    def do_GET(self):
        if self.path == '/capture':
            redirected.append(True)
            self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers(); return
        scenario = parse_qs(urlsplit(self.path).query)['case'][0]
        start, end = map(int, re.fullmatch(r'bytes=(\d+)-(\d+)', self.headers['Range']).groups())
        requests.append((scenario, start, self.headers.get('If-Range'), self.headers.get('Cookie'), self.headers.get('User-Agent')))
        if scenario == 'unauthorized':
            self.send_response(401); self.send_header('Content-Length', '0'); self.end_headers(); return
        if scenario in ('changed-409', 'offline-409') and start:
            body = json.dumps({'code': 'download_changed' if scenario == 'changed-409' else 'desktop_unavailable'}).encode()
            self.send_response(409); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
        if scenario == 'redirect':
            self.send_response(302); self.send_header('Location', '/capture'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if scenario == 'empty' or scenario == 'unknown-range' and start == len(content):
            self.send_response(416); self.send_header('Content-Range', f'bytes */{start}'); self.send_header('Content-Length', '0'); self.send_header('ETag', '"fixture"'); self.end_headers(); return
        if scenario == 'unknown':
            self.send_response(200); self.send_header('Connection', 'close'); self.end_headers()
            self.wfile.write(content[:30000]); self.close_connection = True; return
        end = min(end, len(content) - 1)
        changed = scenario in ('changed', 'etag') and start > 0
        self.send_response(200 if scenario in ('changed', 'ignored-range') and start else 206)
        if scenario != 'no-etag': self.send_header('ETag', '"new"' if changed else '"fixture"')
        if scenario == 'oversize': total = 50 * 1024 * 1024 + 1
        else: total = '*' if scenario == 'unknown-range' else len(content)
        self.send_header('Content-Range', f'bytes {start + (scenario == "wrong-range")}-{end}/{total}')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Content-Disposition', 'attachment; filename="fixture.bin"')
        self.end_headers()
        try:
            for offset in range(start, end + 1, 16384):
                self.wfile.write(content[offset:min(offset + 16384, end + 1)]); self.wfile.flush()
                time.sleep(.014)
        except (BrokenPipeError, ConnectionResetError): pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK/'module-cache'),
                    str(ROOT/'mobile/ios/BridgePreview/GatewayURL.swift'),
                    str(ROOT/'mobile/ios/BridgePreview/DownloadManager.swift'),
                    str(ROOT/'mobile/tests/DownloadManagerTests.swift'), '-o', str(WORK/'download-tests')], check=True)
    subprocess.run([str(WORK/'download-tests'), f'http://127.0.0.1:{server.server_port}', str(scratch)], check=True, timeout=40)
    assert not redirected, 'Downloader leaked a request to the redirect target'
    assert all(agent == 'BridgeMobile/0.1-iOS' for _, _, _, _, agent in requests)
    resumed = [row for row in requests if row[0] == 'pause' and row[1] > 0]
    assert resumed and all(row[2] == '"fixture"' and row[3] == 'codex_mobile_session=renewed' for row in resumed)
    assert sum(row[0] == 'revoked' for row in requests) == 1, 'Resume sent a request without current credentials'
    assert not any(row[0] == 'cancel-before-auth' for row in requests), 'Delayed credentials started a cancelled task'
    print('HTTP request range, refreshed credentials, UA and redirect assertions passed')
finally:
    server.shutdown(); server.server_close()
