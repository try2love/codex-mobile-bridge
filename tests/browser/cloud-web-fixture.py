"""Synthetic desktops for the Go account-Web fixture; never touches real clients."""
import asyncio
import json
import sys
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import Mock

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bridge.api.httpd import GatewayServer, Handler
from bridge.features.network.cloud_web import CloudWeb


class FixtureHandler(Handler):
    def _handle_method(self, write):
        path = urlsplit(self.path).path
        if path in ('/api/clients', '/api/sessions', '/api/projects', '/api/activity', '/api/file-transfer'):
            self.check_request(write=write)
            if not self.login_session():
                return self.output(401, {'error': 'fixture unauthorized'})
            value = {'sessions': [], 'connected': True, 'complete': True, 'remoteHosts': [], 'availableSources': ['desktop']}
            if path == '/api/clients':
                value = {'clients': [{'id': 'codex', 'name': 'Codex', 'enabled': True, 'connected': True,
                                     'installed': True, 'running': True, 'configured': True, 'selectable': True}], 'computer': 'Web fixture'}
            elif path == '/api/projects':
                value = {'projects': []}
            elif path == '/api/file-transfer':
                value = {'uploadMiB': 20, 'clickDownloadMiB': 20}
            if write:
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
            return self.output(200, value)
        return super()._handle_method(write)


async def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    servers, tasks = [], []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None), cookie_jar=aiohttp.DummyCookieJar()) as local, aiohttp.ClientSession() as remote:
        for device in settings['devices']:
            server = GatewayServer(('127.0.0.1', 0), Mock(),
                {'auth': {'mode': 'password', 'sessionHours': 0}, 'origins': [], 'localAccess': True}, ROOT/'web')
            server.RequestHandlerClass = FixtureHandler
            origin = 'http://127.0.0.1:' + str(server.server_port)
            server.origins.add(origin);server.hosts.add(origin.removeprefix('http://'))
            threading.Thread(target=server.serve_forever, daemon=True).start();servers.append(server)
            config = {'origin': settings['origin'], 'account_id': device['account_id'], 'gateway_id': device['id']}
            def request(origin, path):
                with urllib.request.urlopen(origin+path, timeout=5) as response:
                    return json.load(response)
            accounts = SimpleNamespace(config=lambda c=config: c,
                secret=lambda _, d=device: {'device_token': d['device_token']}, request=request,
                web_access_state=lambda _: {'allowed': True, 'revision': 1, 'error': ''})
            owner = SimpleNamespace(accounts=accounts, closed=threading.Event(), server=server, local=local,
                                    local_origin=origin, relay_current=lambda _: True)
            tasks.append(asyncio.create_task(CloudWeb(owner).run(remote)))
        print('Synthetic live desktop connectors running', flush=True)
        try:
            await asyncio.gather(*tasks)
        finally:
            for server in servers:
                server.shutdown();server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
