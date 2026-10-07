#!/usr/bin/env python3
"""Exercise the production iOS probe against local HTTP fixtures, without an iPhone."""
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/reachability-tests'
WORK.mkdir(parents=True, exist_ok=True)
redirect_hits = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        route = self.path.split('/')[1]
        if route == 'slow':
            threading.Event().wait(6)
        if route == 'redirect':
            self.send_response(302); self.send_header('Location', '/redirected/api/auth'); self.end_headers(); return
        if route == 'redirected': redirect_hits.append(True)
        body = json.dumps({'authenticated': False, 'passwordless': False, 'instanceId': 'fixture'}).encode()
        if route == 'html': body = b'<html>Not a gateway</html>'
        if route == 'wrong': body = b'{"status":"ok"}'
        if route == 'large': body = b'x' * 17000
        self.send_response(503 if route == 'error' else 200)
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
source = (ROOT / 'mobile/ios/BridgePreview/App.swift').read_text()
probe = source.split('final class GatewayReachability:', 1)[1].split('final class BridgeController:', 1)[0]
runner = '''
let base = CommandLine.arguments[1]
for (route, expected) in [("valid",true),("html",false),("wrong",false),("large",false),("error",false),("redirect",false),("slow",false)] {
    let probe = GatewayReachability(); var result: Bool? = nil
    let start = Date(); probe.start(base + "/" + route) { result = $0 }
    while result == nil && Date().timeIntervalSince(start) < 7 { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.02)) }
    precondition(result == expected, "Unexpected probe result: " + route)
    print(route + " passed")
}
let cancelled = GatewayReachability(); var called = false
cancelled.start(base + "/slow") { _ in called = true }; cancelled.cancel()
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.2))
precondition(!called, "Cancelled probe updated the screen")
print("cancel passed")
'''
(WORK/'main.swift').write_text('import Foundation\nfinal class GatewayReachability:' + probe + runner)
try:
    subprocess.run(['xcrun','swiftc','-module-cache-path',str(WORK/'cache'),str(WORK/'main.swift'),'-o',str(WORK/'probe-tests')],check=True,cwd=ROOT)
    subprocess.run([str(WORK/'probe-tests'), f'http://127.0.0.1:{server.server_port}'],check=True)
    assert not redirect_hits, 'Probe followed a redirect'
finally:
    server.shutdown(); server.server_close()
