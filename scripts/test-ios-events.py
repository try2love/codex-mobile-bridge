#!/usr/bin/env python3
"""Exercise production iOS event fetch/sync/lifecycle with real URLSession HTTP."""
import argparse
import json
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/ios-events-tests'
WORK.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument('--baseline', action='store_true')
args = parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def parse_request(self):
        # Capture real request-line + header bytes before the HTTP parser.
        original = self.rfile
        class Capture:
            def __init__(self): self.count = 0
            def readline(self, *a):
                data = original.readline(*a); self.count += len(data); return data
        captured = Capture(); self.rfile = captured
        try: result = super().parse_request()
        finally: self.rfile = original
        self.request_bytes = len(self.raw_requestline) + captured.count
        return result
    def do_GET(self):
        state = self.server.state
        query = parse_qs(urlsplit(self.path).query)
        if self.path.startswith('/control'):
            for key in ('cursor', 'status', 'unread'):
                if key in query: state[key] = int(query[key][0])
            if 'stream' in query: state['stream'] = query['stream'][0]
            if 'hold' in query: state['hold'] = True; state['release'].clear()
            if 'release' in query: state['release'].set()
            body = json.dumps({'requests': state['requests']}).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
        row = {'target': self.path, 'requestBytes': self.request_bytes,
               'cookie': self.headers.get('Cookie'), 'origin': self.headers.get('Origin'), 'agent': self.headers.get('User-Agent')}
        state['requests'].append(row)
        if state['hold']:
            state['hold'] = False
            state['release'].wait(5)
        after = int(query.get('after', ['0'])[0])
        events = [{'id': f'event-{i}', 'sequence': i, 'threadId': 'thread', 'host': 'local',
                   'kind': 'request' if i % 2 == 0 else 'completion', 'title': '任务提醒', 'body': '消息内容' * 30}
                  for i in range(max(1, state['cursor'] - 199), state['cursor'] + 1) if i > after]
        summary = [{'provider': 'claude', 'kind': 'request' if state['cursor'] % 2 == 0 else 'completion', 'sequence': state['cursor']}]
        body = json.dumps({'clients': [{'id': 'claude', 'enabled': True, 'unread': state.get('unread', state['cursor'])}], 'summary': summary, 'streamId': state['stream'], 'cursor': state['cursor'], 'events': events, 'enabled': True}, ensure_ascii=False).encode()
        row['responseBodyBytes'] = len(body)
        self.send_response(state['status']); self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', 'application/json')
        self.send_header('Set-Cookie', 'codex_mobile_session=fixture; Path=/; HttpOnly; SameSite=Strict; Max-Age=34560000')
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

servers = []
for _ in range(2):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.state = {'cursor': 200, 'status': 200, 'stream': 'fixture-stream', 'requests': [], 'hold': False, 'release': threading.Event()}
    threading.Thread(target=server.serve_forever, daemon=True).start(); servers.append(server)

source = (ROOT / 'mobile/ios/BridgePreview/App.swift').read_text()
def section(start, end):
    return source[source.index(start):source.index(end, source.index(start))]
fetch = section('    private func fetch(', '    private func sheet(')
sync = section('    @objc private func active()', '    private func requestStartupNotifications()').replace('self.computerNotifications[address]?.arrangedSubviews.forEach { $0.removeFromSuperview() }', 'self.computerNotifications[address]?.isHidden = true')
redirect = section('final class NoRedirect:', '// Foreground-only')
# OS/platform substitutes only. The production fetch, sync, foreground/background
# lifecycle and shared URLSession setup remain intact.
platform = r'''
import Foundation
final class UIApplication { enum State { case active, background }; static let shared = UIApplication(); var applicationState = State.active }
final class CookieStore {
    var cookies: [HTTPCookie] = [HTTPCookie(properties: [.name: "codex_mobile_session", .value: "fixture", .domain: "127.0.0.1", .path: "/"])!]
    var afterSet: (() -> Void)?
    var delay = false
    var pending: [() -> Void] = []
    func getAllCookies(_ done: @escaping ([HTTPCookie]) -> Void) { if delay { pending.append { done(self.cookies) } } else { done(cookies) } }
    func setCookie(_ cookie: HTTPCookie, completionHandler: @escaping () -> Void) { cookies = [cookie]; let hook = afterSet; afterSet = nil; hook?(); completionHandler() }
    func flush() { delay = false; let callbacks = pending; pending = []; callbacks.forEach { $0() } }
}
final class WKWebsiteDataStore { static let instance = WKWebsiteDataStore(); let httpCookieStore = CookieStore(); static func `default`() -> WKWebsiteDataStore { instance } }
final class MemoryDefaults {
    private var values: [String: Any] = [:]
    func set(_ value: Any?, forKey key: String) { values[key] = value }
    func object(forKey key: String) -> Any? { values[key] }
    func string(forKey key: String) -> String? { values[key] as? String }
    func integer(forKey key: String) -> Int { values[key] as? Int ?? 0 }
    func bool(forKey key: String) -> Bool { values[key] as? Bool ?? false }
    func stringArray(forKey key: String) -> [String]? { values[key] as? [String] }
    func removeObject(forKey key: String) { values.removeValue(forKey: key) }
}
enum MobileStrings { static func text(_ value: String) -> String { value } }
enum NotificationSource { static func apply(_ content: UNMutableNotificationContent, provider: String?, host: String?) {} }
final class UNMutableNotificationContent { var title = "", body = ""; struct Sound { static let `default` = Sound() }; var sound: Sound?; var userInfo: [String: Any] = [:] }
struct UNNotificationRequest { let identifier: String; let content: UNMutableNotificationContent; let trigger: String? }
final class UNUserNotificationCenter { static let instance = UNUserNotificationCenter(); static func current() -> UNUserNotificationCenter { instance }; var delivered: [String] = []; func add(_ request: UNNotificationRequest) { delivered.append(request.identifier) } }
struct Downloads { func pause() {} }
struct ConnectionDiscovery { func cancel() {} }
final class LiveActivityController { static let shared = LiveActivityController(); func pause() async {} }
'''
platform += "\nfinal class UILabel { var text: String?; var isHidden = false; var alpha = 1.0 }\n"
platform += section("enum ComputerNotificationSummary {", "enum MobileStrings {")
fields = '''
var routeDiscovery: ConnectionDiscovery?
var routeAttempted = false

final class BridgeController: NSObject {
    var origin = ""
    var computerNotifications: [String: UILabel] = [:]
    var computerSnapshots: [String: [String: Any]] = [:]
    var generation = 0
    let defaults = MemoryDefaults()
    var foregroundBaselines = Set<String>()
    var loading = false
    var timer: Timer?
    var activityTimer: Timer?
    let downloads = Downloads()
'''
if 'private var pollGeneration' in source:
    fields += '    var pollGeneration = 0\n    var pollTask: URLSessionDataTask?\n'
fields += section('    private let redirect = NoRedirect()', '    override func viewDidLoad()')
methods = '''
    func checkComputers() {}
    func stopComputerChecks() {}
    func registerNativePush() {}
    func syncLiveActivity() {}
    func poll() { sync() }
    func foreground() { UIApplication.shared.applicationState = .active; active() }
    func background() { UIApplication.shared.applicationState = .background; inactive() }
    func full(_ done: @escaping (Result<[String: Any], Error>) -> Void) { fetch(done) }
}
'''
runner = (ROOT / 'mobile/tests/MobileEventTests.swift').read_text()
(WORK / 'main.swift').write_text(platform + redirect + fields + fetch + sync + """
    private func updateComputerNotifications(_ data: [String: Any]?, address: String) {
        guard let data else { computerNotifications[address]?.isHidden = true; return }
        computerSnapshots[address] = data
        let rows = ComputerNotificationSummary.clients(data)
        computerNotifications[address]?.text = rows.map { String(describing: $0["unread"] ?? 0) }.joined(separator: ",")
        computerNotifications[address]?.isHidden = rows.isEmpty
    }
""" + methods + runner)
try:
    subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK / 'cache'),
                    str(ROOT / 'mobile/ios/BridgePreview/GatewayURL.swift'), str(WORK / 'main.swift'), '-o', str(WORK / 'events-tests')], check=True)
    subprocess.run([str(WORK / 'events-tests'), *[f'http://127.0.0.1:{s.server_port}' for s in servers],
                    'baseline' if args.baseline else 'regression'], check=True, timeout=30)
    for server in servers:
        assert all(row['agent'] == 'BridgeMobile/0.1-iOS' and row['origin'] == f'http://127.0.0.1:{server.server_port}' for row in server.state['requests'])
    (WORK / ('baseline.json' if args.baseline else 'requests.json')).write_text(json.dumps([s.state['requests'] for s in servers], indent=2))
finally:
    for server in servers:
        server.state['release'].set(); server.shutdown(); server.server_close()
