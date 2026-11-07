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
        if urlsplit(self.path).path == '/api/sessions/diagnostics':
            slow = parse_qs(urlsplit(self.path).query).get('thread') == ['aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee']
            body = b'x' * (2 * 1024 * 1024) if slow else b'{"hosts":[],"phase":"discovery"}'
            requests.append(('diagnostic', 0, self.headers.get('If-Range'), self.headers.get('Cookie'), self.headers.get('User-Agent'), self.headers.get('X-CMB-Cloud-View')))
            if self.headers.get('Cookie') != 'codex_mobile_session=fixture':
                self.send_response(401); self.send_header('Content-Length', '0'); self.end_headers(); return
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Disposition', 'attachment; filename="codex-session-diagnostics.json"')
            if slow: self.send_header('ETag', '"changing-report"')
            self.end_headers()
            try:
                if slow:
                    for offset in range(0, len(body), 16384):
                        self.wfile.write(body[offset:offset + 16384]); self.wfile.flush(); time.sleep(.014)
                else: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
            return
        scenario = parse_qs(urlsplit(self.path).query)['case'][0]
        start, end = map(int, re.fullmatch(r'bytes=(\d+)-(\d+)', self.headers['Range']).groups())
        requests.append((scenario, start, self.headers.get('If-Range'), self.headers.get('Cookie'), self.headers.get('User-Agent'), self.headers.get('X-CMB-Cloud-View')))
        if scenario.startswith('cloud') and (self.headers.get('X-CMB-Cloud-View') != 'a' * 32 or self.headers.get('Cookie')):
            self.send_response(403); self.send_header('Content-Length', '0'); self.end_headers(); return
        if scenario == 'unauthorized':
            self.send_response(401); self.send_header('Content-Length', '0'); self.end_headers(); return
        if scenario in ('changed-409', 'offline-409') and start:
            body = json.dumps({'code': 'download_changed' if scenario == 'changed-409' else 'desktop_unavailable'}).encode()
            self.send_response(409); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
        if scenario in ('redirect', 'cloud-redirect'):
            self.send_response(302); self.send_header('Location', '/capture'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if scenario == 'empty' or scenario == 'unknown-range' and start == len(content):
            self.send_response(416); self.send_header('Content-Range', f'bytes */{start}'); self.send_header('Content-Length', '0'); self.send_header('ETag', '"fixture"'); self.end_headers(); return
        if scenario == 'unknown':
            self.send_response(200); self.send_header('Connection', 'close'); self.end_headers()
            self.wfile.write(content[:30000]); self.close_connection = True; return
        payload_size = 55 * 1024 * 1024 if scenario == "large" else len(content)
        end = min(end, payload_size - 1)
        changed = scenario in ('changed', 'etag') and start > 0
        self.send_response(200 if scenario in ('changed', 'ignored-range') and start else 206)
        if scenario != 'no-etag': self.send_header('ETag', '"new"' if changed else '"fixture"')
        if scenario == 'oversize': total = 9_007_199_254_740_992
        else: total = '*' if scenario == 'unknown-range' else payload_size
        self.send_header('Content-Range', f'bytes {start + (scenario == "wrong-range")}-{end}/{total}')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Content-Disposition', 'attachment; filename="fixture.bin"')
        self.end_headers()
        try:
            for offset in range(start, end + 1, 16384):
                chunk = bytes(index % 251 for index in range(offset, min(offset + 16384, end + 1))) if scenario == "large" else content[offset:min(offset + 16384, end + 1)]
                self.wfile.write(chunk); self.wfile.flush()
                if scenario != "large": time.sleep(.014)
        except (BrokenPipeError, ConnectionResetError): pass


# Compile the production prompt branch with small frame/view stand-ins. This
# exercises its native identity/origin/generation decisions without UI automation.
app_source = (ROOT/'mobile/ios/BridgePreview/App.swift').read_text()
prompt_start = app_source.index('        guard webView===web else{completionHandler(nil);return}',
                                app_source.index('runJavaScriptTextInputPanelWithPrompt'))
prompt_end = app_source.index('        if prompt == "codexbridge-upload-source:"', prompt_start)
prompt_fixture = WORK/'CloudDownloadPrompt.swift'
prompt_fixture.write_text('''import Foundation
final class DownloadWebView { var url: URL?; init(_ url: URL?) { self.url = url } }
struct DownloadFrame { var isMainFrame = true; var request: URLRequest }
final class UIApplication {
    enum State { case active, inactive }
    static let shared = UIApplication()
    var applicationState = State.active
}
final class CloudDownloadPromptFixture {
    var web: DownloadWebView?
    var connectionError: Bool?
    var cloudView = true
    var documentAddress: String
    var origin: String
    var generation = 7
    var clipboardToken = "test-token"
    var cloudDownloadAuthorization: DownloadManager.CloudAuthorization?
    var request: URLRequest?
    var acceptsDownload = true
    init(_ origin: String, entry: URL) {
        self.origin = origin; self.documentAddress = origin
        web = DownloadWebView(URL(string: origin + "/"))
        cloudDownloadAuthorization = DownloadManager.CloudAuthorization(entry: entry, origin: origin, generation: generation)
    }
    func downloadArtifact(_ request: URLRequest) -> Bool { self.request = request; return acceptsDownload }
    func handle(_ prompt: String, defaultText: String?, webView: DownloadWebView, frame: DownloadFrame, completionHandler: (String?) -> Void) {
''' + app_source[prompt_start:prompt_end] + '''        completionHandler(nil)
    }
    func invoke(_ target: URL, token: String = "test-token", source: URL? = nil, main: Bool = true, view: DownloadWebView? = nil) -> String? {
        request = nil
        var result: String?
        handle("codexbridge-download:" + token, defaultText: target.absoluteString, webView: view ?? web!,
               frame: DownloadFrame(isMainFrame: main, request: URLRequest(url: source ?? URL(string: origin + "/")!))) { result = $0 }
        return result
    }
}
''')

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK/'module-cache'),
                    str(ROOT/'mobile/ios/BridgePreview/GatewayURL.swift'),
                    str(ROOT/'mobile/ios/BridgePreview/DownloadManager.swift'), str(prompt_fixture),
                    str(ROOT/'mobile/tests/DownloadManagerTests.swift'), '-o', str(WORK/'download-tests')], check=True)
    subprocess.run([str(WORK/'download-tests'), f'http://127.0.0.1:{server.server_port}', str(scratch)], check=True, timeout=60)
    assert not redirected, 'Downloader leaked a request to the redirect target'
    assert all(agent == 'BridgeMobile/0.1-iOS' for _, _, _, _, agent, _ in requests)
    resumed = [row for row in requests if row[0] == 'pause' and row[1] > 0]
    assert resumed and all(row[2] == '"fixture"' and row[3] == 'codex_mobile_session=renewed' for row in resumed)
    assert sum(row[0] == 'revoked' for row in requests) == 1, 'Resume sent a request without current credentials'
    assert not any(row[0] == 'cancel-before-auth' for row in requests), 'Delayed credentials started a cancelled task'
    cloud = [row for row in requests if row[0] == 'cloud']
    assert len(cloud) >= 4 and all(row[3] is None and row[5] == 'a' * 32 for row in cloud)
    assert all(row[2] == '"fixture"' for row in cloud if row[1] > 0), 'Cloud chunks lost If-Range'
    assert sum(row[0] == 'cloud-revoked' for row in requests) == 1, 'Revoked view capability reached HTTP'
    assert not any(row[0] == 'cloud-invalid-headers' for row in requests), 'Unapproved header reached HTTP'
    print('HTTP range, cookie/cloud authorization, generation revocation, UA and redirect assertions passed')
finally:
    server.shutdown(); server.server_close()
