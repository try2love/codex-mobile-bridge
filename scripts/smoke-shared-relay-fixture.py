"""Local-only browser fixture. Synthetic computer, no Codex or model requests."""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aiohttp import web
from relay.server import Relay, STATE, create_app
from bridge.shared_relay import Controller, Connector, read_config

directory = ROOT/'.tmp/relay-browser'
directory.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory(prefix='run-',dir=directory)
data = Path(temporary.name)
controller = Controller(data/'computer', test_http=True)
connector = None
original_page = Relay.page


async def page(self, request):
    if request.path == '/fixture/desktop':
        return web.Response(text='''<!doctype html><html><head><meta charset="utf-8"><title>Shared relay desktop fixture</title><link rel="stylesheet" href="/fixture/style.css"></head><body><main style="margin:20px;max-width:840px"><section data-panel="network"></section></main><script src="/fixture/setup.js"></script><script src="/fixture/desktop.js"></script></body></html>''', content_type='text/html')
    if request.path == '/fixture/style.css':
        return web.Response(body=(ROOT/'desktop/style.css').read_bytes(), content_type='text/css')
    if request.path == '/fixture/desktop.js':
        return web.Response(body=(ROOT/'desktop/shared-relay.js').read_bytes(), content_type='text/javascript')
    if request.path == '/fixture/setup.js':
        return web.Response(text="window.BridgeI18n={locale:()=> 'zh'};window.bridgeDesktop={sharedRelay:async value=>{const r=await fetch('/fixture/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});const data=await r.json();if(!r.ok)throw Error(data.error);return data;}};", content_type='text/javascript')
    return await original_page(self, request)


async def action(request):
    global connector
    value = await request.json()
    result = await asyncio.to_thread(controller.control, value)
    if value.get('action') == 'register':
        bridge = SimpleNamespace(host_errors=[], list=lambda **kw: [{'id':'11111111-1111-4111-8111-111111111111', 'title':'Shared relay test chat', 'host':'local', 'hostLabel':'Fixture computer', 'projectName':'Relay fixture', 'projectKey':'fixture'}])
        bridge.for_host = lambda host: bridge
        bridge.activity = lambda identifiers: []
        connector = Connector(controller.directory, SimpleNamespace(bridge=bridge,web_dir=ROOT/'web',notifications=None), test_http=True)
        connector.start()
    if value.get('action') == 'pair':
        code = "require('qrcode').toDataURL(JSON.parse(require('fs').readFileSync(0,'utf8')).url).then(x=>process.stdout.write(x))"
        completed = await asyncio.to_thread(subprocess.run, ['node','-e',code], input=json.dumps(result), text=True, capture_output=True, check=True)
        result['image'] = completed.stdout
    return web.json_response(result)


async def main():
    global connector
    with patch.object(Relay,'page',page):
        app = create_app(data/'server', 'http://127.0.0.1:1', test_http=True)
    app.router.add_post('/fixture/action', action)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    state = app[STATE]
    state.origin = 'http://127.0.0.1:'+str(port)
    state.host = '127.0.0.1:'+str(port)
    invite = state.registry.invite('browser-fixture')
    output = {'url':state.origin,'invitation':invite['invitation'], 'adminToken':state.registry.rotate_admin_token()}
    (directory/'fixture.json').write_text(json.dumps(output),encoding='utf-8')
    (directory/'fixture.json').chmod(0o600)
    print(json.dumps({'url': state.origin, 'credentials': str(directory/'fixture.json')}),flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        if connector:
            await asyncio.to_thread(connector.close)
        await runner.cleanup()
        temporary.cleanup()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
