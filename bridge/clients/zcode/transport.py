"""Use the gateway's Python runtime to reach the verified original ZCode window."""
import http.client
import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit

from bridge.clients.websocket import WebSocket
from .installation import renderer_url


def verified_target(targets, application, port):
    expected = renderer_url(application)
    matches = [row for row in targets if row.get('type') == 'page'
               and row.get('url', '').split('?')[0].split('#')[0] == expected]
    if len(matches) != 1:
        raise ValueError('ZCode native window is missing or ambiguous')
    endpoint = urlsplit(matches[0]['webSocketDebuggerUrl'])
    if (endpoint.scheme != 'ws' or endpoint.hostname != '127.0.0.1' or endpoint.port != port
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or not re.fullmatch(r'/devtools/page/[A-Za-z0-9_-]+', endpoint.path)):
        raise ValueError('Untrusted ZCode debugging endpoint')
    return endpoint.path


def renderer_source():
    return Path(__file__).with_name('renderer.mjs').read_text(encoding='utf-8').replace(
        'export async function nativeRequest', 'async function nativeRequest', 1).strip()


def expression(request):
    return '(' + renderer_source() + ')(' + json.dumps(request, ensure_ascii=False) + ')'


def bind_workspace_registry(rpc, request):
    group = 'bridge-zcode-registry'
    try:
        probe = rpc('Runtime.evaluate', {'expression': expression({**request, 'action': 'registry-probe'}),
                    'awaitPromise': True, 'objectGroup': group})
        value = probe.get('result', {})
        if value.get('type') == 'boolean' and value.get('value') is True:
            return
        if probe.get('exceptionDetails') or value.get('type') != 'function':
            raise ValueError('ZCode 原生工作区连接入口不可用')
        props = rpc('Runtime.getProperties', {'objectId': value['objectId'], 'ownProperties': True})
        scope = next((p for p in props.get('internalProperties', []) if p['name'] == '[[Scopes]]'), None)
        if not scope:
            raise ValueError('ZCode 原生工作区模块不可用')
        entries = rpc('Runtime.getProperties', {'objectId': scope['value']['objectId'], 'ownProperties': True})
        matches = []
        for entry in entries['result']:
            if entry.get('value', {}).get('description') != 'Module':
                continue
            module = rpc('Runtime.getProperties', {'objectId': entry['value']['objectId'], 'ownProperties': True})
            matches.extend(p for p in module['result'] if p.get('value', {}).get('type') == 'function'
                           and all(marker in p['value'].get('description', '') for marker in
                                   ('v4.workspace_connection.reused', 'v4.workspace_connection.created')))
        if len(matches) != 1:
            raise ValueError('ZCode 原生工作区注册表不唯一或版本已变化')
        result = rpc('Runtime.callFunctionOn', {'objectId': matches[0]['value']['objectId'],
                     'functionDeclaration': 'function(){window.__codexMobileZCodeAcquireWorkspace=this;return true}',
                     'returnByValue': True})
        if result.get('result', {}).get('value') is not True:
            raise ValueError('ZCode 原生工作区注册表不可用')
    finally:
        rpc('Runtime.releaseObjectGroup', {'objectGroup': group})


def request_native(request):
    port = request['port']
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid loopback port')
    deadline = time.monotonic() + max(0, (request['expiresAt'] / 1000 - time.time()))

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('ZCode 回执超时，结果待核对；不会自动重放')
        return value

    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=min(3, remaining()))
    try:
        connection.request('GET', '/json/list')
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Native ZCode target discovery failed')
        data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('ZCode target discovery is too large')
        path = verified_target(json.loads(data), request['application'], port)
    finally:
        connection.close()
    with socket.create_connection(('127.0.0.1', port), timeout=remaining()) as stream:
        with stream.makefile('rwb', buffering=0) as pipe:
            ws = WebSocket(pipe, pipe, path=path, host=f'127.0.0.1:{port}')
            sequence = 0

            def rpc(method, params):
                nonlocal sequence
                stream.settimeout(remaining())
                sequence += 1
                ws.send({'id': sequence, 'method': method, 'params': params})
                while True:
                    stream.settimeout(remaining())
                    reply = ws.receive()
                    if reply.get('id') != sequence:
                        continue
                    if reply.get('error'):
                        raise ValueError(reply['error'].get('message', 'ZCode native request failed'))
                    return reply['result']

            if request['action'] in ('attach', 'create'):
                bind_workspace_registry(rpc, request)
            result = rpc('Runtime.evaluate', {'expression': expression(request),
                         'awaitPromise': True, 'returnByValue': True})
            if result.get('exceptionDetails'):
                details = result['exceptionDetails']
                raise ValueError(details.get('exception', {}).get('description') or details.get('text'))
            return result['result'].get('value')
