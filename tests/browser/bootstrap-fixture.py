"""Loopback-only startup fault fixture; serves production assets, never a real gateway.

Run: python3 -B tests/browser/bootstrap-fixture.py
Open /?case=success|fast|shell-once|shell-twice|shell-fatal|css-once|js-twice|js-fatal|runtime|eager-once|auth-once|
auth-hang|slow|bootstrap-missing|pair-unknown|external-script|opaque-error|external-runtime|
external-rejection|app-cached-failure. Each navigation resets counters.
Load bootstrap.browser.js and invoke runBootstrapBrowserChecks() in that page.
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bridge.api.assets import STATIC, served_asset_bytes  # noqa: E402

WEB = ROOT / 'web'
PAYLOADS = {path: served_asset_bytes(WEB, path) for path in STATIC}
state = {'case': 'success', 'requests': Counter(), 'writes': []}
BROWSER_CHECKS = {f'/__browser/{name}': ROOT / 'tests/browser' / name for name in (
    'desktop-history.browser.js', 'chat-reading-state.browser.js', 'persistent-drafts.browser.js', 'agent-model-settings.browser.js',
    'host-file-reference.browser.js', 'session-menu.browser.js', 'cli-release.browser.js', 'project-creation.browser.js', 'api-form.browser.js', 'composer-quotes.browser.js')}
PROBE = b"""window.__startupTest={scenario:new URLSearchParams(location.search).get('case'),executed:[],errors:[],rejections:[],blockedScripts:[],visibilitySamples:[],titles:[],shellLoads:0,started:performance.now(),loadClipboard:null};
addEventListener('error',e=>{if(e.target===window)__startupTest.errors.push(e.error?.name||'Error');});
addEventListener('unhandledrejection',e=>{__startupTest.rejections.push(e.reason?.name||'Error');});
addEventListener('securitypolicyviolation',e=>{if(e.effectiveDirective.startsWith('script-src'))__startupTest.blockedScripts.push(e.blockedURI);});
addEventListener('load',()=>{__startupTest.loadClipboard=!!window.BridgeClipboard;});
new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)if(node.tagName==='LINK'&&node.href&&new URL(node.href).pathname==='/style.css')__startupTest.shellLoads++;const title=document.getElementById('bootstrap-title');if(title&&!__startupTest.titles.includes(title.textContent))__startupTest.titles.push(title.textContent);if(document.getElementById('bootstrap-status')?.hidden)__startupTest.readyAt??=performance.now()-__startupTest.started;}).observe(document,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['hidden']});
function sample(){const root=document.getElementById('bootstrap-status');if(root){if(root.hidden){__startupTest.readyAt=performance.now()-__startupTest.started;return;}const style=getComputedStyle(root);__startupTest.visibilitySamples.push({at:performance.now()-__startupTest.started,visible:style.visibility!=='hidden',styled:style.position==='fixed'});}setTimeout(sample,25);}setTimeout(sample,25);"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def output(self, body, mime='text/javascript; charset=utf-8', status=200, cache_control='no-store'):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', cache_control)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/__fixture_health':
            self.output('{"fixture":"cmb-browser-regression"}', 'application/json')
            return
        if path in BROWSER_CHECKS:
            self.output(BROWSER_CHECKS[path].read_bytes())
            return
        if path == '/__state':
            self.output(json.dumps(state), 'application/json')
            return
        if path == '/__probe.js':
            self.output(PROBE)
            return
        if path == '/__checks.js':
            self.output((ROOT / 'tests/browser/bootstrap.browser.js').read_bytes())
            return
        if path == '/__ready.js':
            self.output("window.__startupTest.executed.push('/__ready.js');document.getElementById('login').hidden=false;BridgeBootstrap.ready();")
            return
        if path == '/__external-error.js':
            self.output({
                'opaque-error': 'dispatchEvent(new ErrorEvent("error",{message:"Script error."}));',
                'external-runtime': 'throw new Error("synthetic injected error");',
                'external-rejection': 'Promise.reject(new Error("synthetic injected rejection"));',
            }.get(state['case'], ''))
            return
        if path == '/':
            state.update(case=parse_qs(urlsplit(self.path).query).get('case', ['success'])[0], requests=Counter(), writes=[])
        state['requests'][path] += 1
        attempt, case = state['requests'][path], state['case']
        fail = (case == 'shell-once' and path == '/style.css' and attempt == 1
                or case == 'shell-twice' and path == '/style.css' and attempt <= 2
                or case == 'shell-fatal' and path == '/style.css'
                or case == 'css-once' and path == '/account.css' and attempt == 1
                or case == 'js-twice' and path == '/i18n.js' and attempt <= 2
                or case == 'js-fatal' and path == '/i18n.js'
                or case == 'eager-once' and path == '/message-actions.js' and attempt == 1
                or case == 'bootstrap-missing' and path == '/bootstrap.js'
                or case == 'auth-once' and path == '/api/auth' and attempt == 1)
        if fail:
            self.output('Temporary fixture failure', 'text/html', 503)
            return
        if case == 'app-cached-failure' and path == '/app.js' and not parse_qs(urlsplit(self.path).query).get('bridge_retry'):
            self.output('Cached non-script response', 'text/plain', cache_control='public, max-age=300, immutable')
            return
        if case == 'session-menu' and path.startswith('/api/'):
            sid = '12345678-1234-4234-9234-123456789012'
            clients = [{'id': provider, 'name': name, 'enabled': True, 'connected': True,
                        'installed': True, 'running': True, 'configured': True, 'selectable': True}
                       for provider, name in [('codex', 'Codex'), ('claude', 'Claude'), ('deepseek', 'DSH')]]
            provider = path.split('/')[3] if path.startswith('/api/desktop-sessions/') else 'codex'
            names = {'codex': 'Codex', 'claude': 'Claude', 'deepseek': 'DSH'}
            rows = [{'id': sid if provider == 'codex' else provider + '-synthetic-' + str(i), 'host': 'local',
                     'title': names.get(provider, provider) + ' · ' + title,
                     'projectName': '示例项目', 'projectKey': 'synthetic', 'cwd': '/synthetic/project',
                     'recency': 1793145600000 - i * 60000, 'updatedAt': 1793145600000 - i * 60000,
                     'backend': 'code', 'runtime': 'desktop', 'access': {'source': 'desktop'},
                     'capabilities': {'fork': True, 'archive': True, 'unarchive': True, 'rename': True,
                                      'organization': provider in ('codex', 'deepseek')}, 'bridgePinned': False}
                    for i, title in enumerate(['页面交互验证', '文档整理', '单元测试检查'])]
            if provider == 'codex':
                for i, row in enumerate(rows):
                    row['id'] = sid[:-1] + str(i)
            value = {}
            if path == '/api/auth':
                value = {'authenticated': True, 'csrf': 'synthetic', 'draftIdentity': 'synthetic',
                         'listCacheIdentity': 'a' * 64, 'computer': {'name': '菜单测试电脑'}, 'uiLanguage': 'zh'}
            elif path == '/api/clients':
                value = {'clients': clients, 'computer': '菜单测试电脑'}
            elif path == '/api/sessions' or path.endswith('/list'):
                value = {'sessions': rows, 'complete': True, 'connected': True,
                         'remoteHosts': [], 'availableSources': ['desktop']}
            elif path == '/api/accounts' or path.endswith('/accounts'):
                value = {'accounts': [], 'activeIds': []}
            elif path == '/api/account':
                value = {'visible': False}
            elif path.endswith('/organization'):
                value = {'projectId': None, 'sectionId': None, 'projects': [{'id': 'p', 'title': '示例项目'}],
                         'sections': [{'id': 's', 'title': '示例分区'}], 'status': 'confirmed',
                         'scope': 'bridge' if provider == 'claude' else 'native',
                         'sectionScope': 'native' if provider == 'codex' else 'bridge'}
            self.output(json.dumps(value), 'application/json')
            return
        if path == '/api/auth':
            if case == 'auth-hang' and attempt == 1:
                time.sleep(10)
            if case == 'slow':
                time.sleep(0.8)
            self.output(json.dumps({'authenticated': False, 'passwordless': False}), 'application/json')
            return
        if path not in PAYLOADS:
            self.output('', 'text/plain', 404)
            return
        body = PAYLOADS[path]
        if path == '/':
            body = body.replace(b'<head>', b'<head><script src="/__probe.js"></script>', 1)
            if case == 'external-script':
                body = body.replace(b'</body>', b'<script defer src="https://static.cloudflareinsights.com/beacon.min.js"></script></body>')
            if case in ('opaque-error', 'external-runtime', 'external-rejection'):
                body = body.replace(b'</body>', b'<script defer src="/__external-error.js"></script></body>')
            if case == 'fast':
                body = re.sub(rb'<link[^>]+data-bridge-href[^>]*>|<script[^>]+data-bridge-src[^>]*></script>', b'', body)
                body = body.replace(b'</body>', b'<script data-bridge-src="/__ready.js"></script></body>')
        elif path.endswith('.js'):
            if case == 'runtime' and path == '/i18n.js':
                body = b'throw new ReferenceError("synthetic runtime failure");\n' + body
            body += ('\nwindow.__startupTest.executed.push(' + json.dumps(path) + ');').encode()
        self.output(body, STATIC[path][1])

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        state['writes'].append(urlsplit(self.path).path)
        if self.path == '/api/pair' and state['case'] == 'pair-unknown':
            time.sleep(35)
        self.output('{}', 'application/json', 503)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    options = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', options.port), Handler)
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()
