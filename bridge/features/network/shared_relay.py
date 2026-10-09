"""Opt-in outbound relay connector; only talks to its own authenticated gateway."""
import asyncio
import base64
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

from relay.protocol import MAX_BODY, MAX_FRAME, origin, validate, decode, content_type, download_headers
from bridge.features.auth.tls import client_context


def read_config(directory):
    path = Path(directory) / 'shared-relay.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def save_config(directory, value, name='shared-relay.json'):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / name
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
    temporary.chmod(0o600)
    temporary.replace(path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class RelayError(ValueError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class Controller:
    def __init__(self, directory, test_http=False):
        self.directory = Path(directory)
        self.test_http = test_http

    def request(self, url, value, secret=None):
        headers = {'Content-Type': 'application/json'}
        if secret:
            headers['Authorization'] = 'Bearer ' + secret
        req = urllib.request.Request(url, json.dumps(value).encode(), headers)
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=client_context()))
        try:
            with opener.open(req, timeout=12) as response:
                return json.loads(response.read(65536))
        except urllib.error.HTTPError as exc:
            raise RelayError('中继拒绝请求，请检查邀请码、授权或服务地址（HTTP %s）。' % exc.code, exc.code) from None
        except (OSError, ValueError):
            raise ValueError('无法连接中继，请检查 HTTPS 地址、证书和网络。') from None

    def control(self, value):
        if not isinstance(value, dict):
            raise ValueError('Invalid relay request')
        action = value.get('action', 'status')
        cfg = read_config(self.directory)
        if action == 'register':
            if cfg.get('deviceToken'):
                raise ValueError('请先撤销当前设备，再注册其他中继。')
            if value.get('consent') is not True:
                raise ValueError('请确认允许此中继转发电脑操作。')
            url = origin(value.get('url', ''), self.test_http)
            result = self.request(url + '/relay/register', {'invitation': value.get('invitation'), 'name': value.get('name')})
            if not all(isinstance(result.get(k), str) and result[k] for k in ('deviceId', 'deviceToken', 'deviceName')):
                raise ValueError('中继注册响应无效。')
            save_config(self.directory, {**result, 'url': url, 'enabled': True})
            return {'registered': True, 'url': url, 'deviceName': result['deviceName'], 'online': False}
        if action == 'status' and not cfg.get('deviceToken'):
            return {'registered': False, 'online': False}
        if not cfg.get('deviceToken'):
            raise ValueError('请先注册共享中继。')
        url = origin(cfg['url'], self.test_http)
        if action == 'disable':
            save_config(self.directory, {**cfg, 'enabled': False})
            return {'registered': True, 'enabled': False, 'online': False}
        if action == 'enable':
            save_config(self.directory, {**cfg, 'enabled': True})
            return {'registered': True, 'enabled': True}
        if action not in ('status', 'pair', 'approve', 'revoke-phone', 'revoke'):
            raise ValueError('Unknown relay action')
        if action == 'revoke':
            # Close the local gate first even when the remote service is down.
            save_config(self.directory, {**cfg, 'enabled': False})
        try:
            result = self.request(url + '/relay/device', {'action': action, **{k: value[k] for k in ('id', 'approved') if k in value}}, cfg['deviceToken'])
        except ValueError as exc:
            if action == 'status':
                result = {'deviceName': cfg.get('deviceName', ''), 'online': False, 'error': str(exc), 'pending': [], 'phones': []}
            elif action == 'revoke' and isinstance(exc, RelayError) and exc.status == 401:
                # An administrator may already have revoked this device. Allow
                # removing its invalid local token so a new invite can be used.
                result = {'ok': True}
            else:
                raise
        if action == 'revoke':
            save_config(self.directory, {})
        if action == 'status':
            return {**result, 'registered': True, 'enabled': cfg.get('enabled', False), 'url': url}
        return result


class Connector:
    def __init__(self, directory, gateway, test_http=False):
        from bridge.api.httpd import GatewayServer
        self.directory = Path(directory)
        self.config = read_config(directory)
        self.origin = origin(self.config['url'], test_http)
        config = {'auth': {'mode': 'password', 'sessionHours': 0}, 'origins': [], 'localAccess': True}
        self.server = GatewayServer(('127.0.0.1', 0), gateway.bridge, config, gateway.web_dir)
        self.server.notifications = gateway.notifications
        self.server.desktop_sessions = getattr(gateway, 'desktop_sessions', None)
        self.local_origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.server.origins.add(self.local_origin)
        self.server.hosts.add(self.local_origin.removeprefix('http://'))
        self.sessions = {}
        self.closed = threading.Event()
        self.thread = None
        self.http_thread = None
        self.task = None
        self.loop = None

    def enabled(self):
        try:
            cfg = read_config(self.directory)
            return (not self.closed.is_set() and cfg.get('enabled') is True
                    and cfg.get('deviceToken') == self.config['deviceToken'] and cfg.get('url') == self.config['url'])
        except (OSError, ValueError):
            return False

    async def forward(self, client, message):
        identifier = message.get('id', '')
        def result(status, body):
            return {'id': identifier, 'status': status, 'body': base64.b64encode(json.dumps(body).encode()).decode(), 'contentType': 'application/json'}
        try:
            phone = message.get('phone')
            if not isinstance(phone, str) or not re.fullmatch('[a-f0-9]{32}', phone):
                raise ValueError('Invalid phone identity')
            phones = message.get('phones')
            if not isinstance(phones, list) or len(phones) > 16 or phone not in phones or any(not isinstance(p, str) or not re.fullmatch('[a-f0-9]{32}', p) for p in phones):
                raise ValueError('Invalid phone authorizations')
            for previous in list(self.sessions):
                if previous not in phones:
                    self.server.auth.logout(self.sessions.pop(previous)[0])
            body = decode(message.get('body'))
            validate(message.get('method'), message.get('path'), len(body))
            mime = content_type(message.get('contentType'))
            range_headers = download_headers(message.get('headers', {}))
            if not self.enabled():
                return result(503, {'error': '本机共享中继已关闭。'})
            if phone not in self.sessions:
                if len(self.sessions) >= 16:
                    raise ValueError('Too many local phone sessions; reconnect the gateway')
                self.sessions[phone] = self.server.auth.new_session('127.0.0.1', 'Shared relay phone')
            secret, session = self.sessions[phone]
            headers = {'Cookie': self.server.auth.COOKIE + '=' + secret,
                       'Origin': self.local_origin, 'X-CSRF-Token': session['csrf'],
                       'Content-Type': mime, 'Accept-Encoding': 'identity', **range_headers}
            async with client.request(message['method'], self.local_origin + message['path'], headers=headers,
                                      data=body if message['method'] == 'POST' else None, allow_redirects=False) as response:
                if (range_headers.get('Range') and range_headers.get('If-Range') and response.status == 200
                        and response.headers.get('Accept-Ranges') == 'bytes' and response.headers.get('ETag')
                        and response.headers['ETag'] != range_headers['If-Range']
                        and response.content_length is not None and response.content_length > MAX_BODY):
                    # The standard full 200 fallback cannot fit one relay frame.
                    # Ask clients to restart at zero instead of retrying forever
                    # with the old validator or buffering an oversized response.
                    return result(409, {'error': '文件已变化，请重新下载。', 'code': 'download_changed'})
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_BODY:
                        raise ValueError('Response exceeds shared relay limit (20 MiB)')
                return {'id': identifier, 'status': response.status, 'body': base64.b64encode(data).decode(),
                        'contentType': response.headers.get('Content-Type', 'application/octet-stream'),
                        'disposition': response.headers.get('Content-Disposition', ''),
                        'headers': download_headers({key: response.headers[key] for key in
                            ('ETag', 'Content-Range', 'Accept-Ranges') if key in response.headers}, response=True)}
        except (ValueError, PermissionError, KeyError, TypeError) as exc:
            return result(403, {'error': str(exc)})
        except Exception:
            return result(504, {'error': '电脑网关响应中断，请检查操作结果后再继续。', 'code': 'outcome_unknown'})

    async def run(self):
        import aiohttp
        delay = 1
        trace = aiohttp.TraceConfig()
        async def reject_redirect(session, context, params):
            params.response.close()
            raise ValueError('Relay redirects are not permitted')
        trace.on_request_redirect.append(reject_redirect)
        # Local forwarding deliberately ignores proxy environment variables.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=80), cookie_jar=aiohttp.DummyCookieJar()) as local:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                             connector=aiohttp.TCPConnector(ssl=client_context()),
                                             cookie_jar=aiohttp.DummyCookieJar(), trust_env=True,
                                             trace_configs=[trace]) as remote:
                while self.enabled():
                    pending = set()
                    try:
                        async with remote.ws_connect(self.origin + '/relay/device/ws',
                                                     headers={'Authorization': 'Bearer ' + self.config['deviceToken']},
                                                     heartbeat=25, max_msg_size=MAX_FRAME, compress=0) as ws:
                            delay = 1
                            seen = set()
                            async def handle(value):
                                reply = await self.forward(local, value)
                                if not ws.closed:
                                    await ws.send_json(reply)
                            while self.enabled():
                                try:
                                    message = await asyncio.wait_for(ws.receive(), timeout=1)
                                except asyncio.TimeoutError:
                                    continue
                                if message.type != aiohttp.WSMsgType.TEXT:
                                    break
                                value = json.loads(message.data)
                                identifier = value.get('id')
                                if not isinstance(identifier, str) or len(identifier) > 128 or identifier in seen:
                                    raise ValueError('Invalid or repeated request ID')
                                # Bounded replay set. Reconnect rather than forget old IDs.
                                if len(seen) >= 100000 or len(pending) >= 12:
                                    break
                                seen.add(identifier)
                                task = asyncio.create_task(handle(value))
                                pending.add(task)
                                def complete(task):
                                    pending.discard(task)
                                    if not task.cancelled():
                                        task.exception()
                                task.add_done_callback(complete)
                    except (aiohttp.ClientError, OSError, ValueError, asyncio.TimeoutError):
                        pass
                    finally:
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                    # No replay of any in-flight request after disconnection.
                    for _ in range(delay):
                        if not self.enabled():
                            break
                        await asyncio.sleep(1)
                    delay = min(30, delay * 2)

    def start(self):
        import aiohttp  # Fail locally before starting a background thread if not installed.
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.http_thread.start()
        def worker():
            async def main():
                self.loop = asyncio.get_running_loop()
                self.task = asyncio.current_task()
                try:
                    await self.run()
                except asyncio.CancelledError:
                    pass
            asyncio.run(main())
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.loop and self.loop.is_running() and self.task:
            self.loop.call_soon_threadsafe(self.task.cancel)
        if self.thread:
            self.thread.join(timeout=5)
        if self.http_thread:
            self.server.shutdown()
            self.http_thread.join(timeout=5)
        self.server.server_close()


def start(directory, gateway):
    if not read_config(directory).get('enabled'):
        return None
    connector = Connector(directory, gateway)
    try:
        connector.start()
    except Exception:
        connector.close()
        raise
    return connector
