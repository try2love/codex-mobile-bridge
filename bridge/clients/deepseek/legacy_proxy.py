"""Authenticated, streaming proxy to the gateway's owned loopback Harness."""
import base64
import hashlib
import http.client
import select
import re
import socket
import time
from urllib.parse import urlsplit, unquote

from bridge.clients.deepseek.legacy_gateway import PREFIX

MAX_UPLOAD = 300 * 1024 * 1024
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
       'transfer-encoding', 'upgrade', 'proxy-connection'}
PRIVATE = {'cookie', 'authorization', 'x-csrf-token', 'host', 'origin', 'forwarded', 'content-length'}


def redirect(handler, target):
    handler.send_response(303)
    handler.headers_common()
    handler.send_header('Location', target)
    handler.send_header('Content-Length', '0')
    handler.end_headers()


def active(handler, process):
    return (process.poll() is None and handler.server.harness.process is process
            and handler.server.harness.phase == 'running'
            and handler.server.auth.permitted(handler.client()['ip']) and handler.login_session())


def headers(handler, port, cookie, websocket=False):
    blocked = HOP | PRIVATE | {x.strip().lower() for x in handler.headers.get('Connection', '').split(',')}
    result = {}
    for key, value in handler.headers.items():
        lower = key.lower()
        if lower not in blocked and not lower.startswith(('x-forwarded-', 'sec-fetch-', 'sec-websocket-')):
            result[key] = value
    result.update(Host=f'127.0.0.1:{port}', Origin=f'http://127.0.0.1:{port}', Cookie=cookie)
    result['Sec-Fetch-Site'] = 'same-origin'
    if websocket:
        result.update(Connection='Upgrade', Upgrade='websocket')
        for key in ('Sec-WebSocket-Key', 'Sec-WebSocket-Version', 'Sec-WebSocket-Protocol', 'Sec-WebSocket-Extensions'):
            if handler.headers.get(key):
                result[key] = handler.headers[key]
    else:
        result['Connection'] = 'close'
    return result


def location(value, port):
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != 'http' or parsed.netloc != f'127.0.0.1:{port}':
            raise ValueError('Harness 返回了不支持的重定向')
        value = parsed.path + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')
    if value.startswith('/'):
        value = PREFIX.rstrip('/') + value
    # Relative redirects must stay within the mount.
    if any(part == '..' for part in unquote(urlsplit(value).path).split('/')) or '\\' in value:
        raise ValueError('Harness 重定向路径无效')
    return value


def proxy(handler):
    path = urlsplit(handler.path)
    upgrade = handler.headers.get('Upgrade', '').lower() == 'websocket'
    write = handler.command not in ('GET', 'HEAD')
    handler.check_request(write or upgrade)
    host = handler.headers.get('Host', '')
    expected = ('https://' if host in handler.server.secure_hosts else 'http://') + host
    # The upstream UI cannot add Bridge's CSRF header. Require exact same-origin
    # for every mutation and WS instead, not merely another configured origin.
    if (write or upgrade or handler.headers.get('Origin')) and handler.headers.get_all('Origin', []) != [expected]:
        raise PermissionError('不允许跨站请求')
    if not handler.server.auth.permitted(handler.client()['ip']):
        raise PermissionError('此 IP 已被访问规则禁止')
    if not handler.login_session():
        handler.close_connection = True
        if handler.command == 'GET' and not upgrade and path.path in ('/harness', PREFIX):
            return redirect(handler, '/?open=harness')
        return handler.output(401, {'error': '请登录', 'code': 'unauthenticated'})
    if path.path == '/harness':
        return redirect(handler, PREFIX)
    if handler.server.harness is None:
        return handler.output(503, {'error': '请在电脑 App 中启动 DeepSeek Harness'})
    try:
        port, cookie, process = handler.server.harness.endpoint()
    except ValueError:
        return handler.output(503, {'error': '请在电脑 App 中启动 DeepSeek Harness'})
    target = handler.path[len(PREFIX) - 1:]
    decoded = unquote(path.path[len(PREFIX) - 1:])
    if (not target.startswith('/') or target.startswith('//') or '\\' in decoded
            or any(ord(c) < 32 for c in decoded) or any(p in ('.', '..') for p in decoded.split('/'))):
        raise ValueError('Harness 请求路径无效')
    if upgrade:
        if handler.command != 'GET' or path.path != PREFIX + 'api/remote.mux':
            raise ValueError('不支持的 Harness WebSocket 路径')
        return websocket(handler, target, port, cookie, process)
    return http_proxy(handler, target, port, cookie, process)


def http_proxy(handler, target, port, cookie, process):
    sizes = handler.headers.get_all('Content-Length', [])
    encodings = handler.headers.get_all('Transfer-Encoding', [])
    if encodings or len(sizes) > 1 or (sizes and (not sizes[0].isdigit() or int(sizes[0]) > MAX_UPLOAD)):
        # Browser File/FormData requests carry a length; reject ambiguous framing.
        raise ValueError('Harness 请求长度无效或超过 300 MiB')
    remaining = int(sizes[0]) if sizes else 0
    index = urlsplit(target).path in ('/', '/index.html')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
    sent = False
    handler.close_connection = True
    try:
        connection.putrequest(handler.command, target, skip_host=True, skip_accept_encoding=True)
        request_headers = headers(handler, port, cookie)
        if index:
            request_headers = {k: v for k, v in request_headers.items() if k.lower() != 'accept-encoding'}
            request_headers['Accept-Encoding'] = 'identity'
        for key, value in request_headers.items():
            connection.putheader(key, value)
        if sizes:
            connection.putheader('Content-Length', str(remaining))
        connection.endheaders()
        while remaining:
            if not active(handler, process):
                raise PermissionError('登录已失效')
            chunk = handler.rfile.read(min(65536, remaining))
            if not chunk:
                raise ValueError('Harness 上传中断')
            connection.send(chunk)
            remaining -= len(chunk)
        response = connection.getresponse()
        index_body = None
        if index and handler.command == 'GET' and response.status == 200 and response.getheader('Content-Type', '').startswith('text/html'):
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise ValueError('Harness HTML encoding is unsupported')
            index_body = response.read(1024 * 1024 + 1)
            if len(index_body) > 1024 * 1024:
                raise ValueError('Harness HTML is too large')
            # Web manifests omit credentials by default, even on the same origin.
            # Keep the manifest behind Bridge auth, rather than making it public.
            def manifest(match):
                tag = re.sub(rb'''\s+crossorigin(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?''', b'', match[0], flags=re.I)
                return tag[:-1] + b' crossorigin="use-credentials">'
            index_body = re.sub(rb'''<link\b[^>]*\brel\s*=\s*["']manifest["'][^>]*>''', manifest, index_body, flags=re.I)
        outgoing = []
        blocked = HOP | {'set-cookie', 'content-length', 'cache-control', 'referrer-policy', 'x-frame-options'}
        blocked |= {x.strip().lower() for x in response.getheader('Connection', '').split(',')}
        for key, value in response.getheaders():
            if key.lower() == 'location':
                value = location(value, port)
            if key.lower() not in blocked:
                outgoing.append((key, value))
        handler.send_response(response.status)
        for key, value in outgoing:
            handler.send_header(key, value)
        # Upstream owns its CSP (the official UI uses inline bootstrap scripts).
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('X-Frame-Options', 'DENY')
        handler.send_header('Set-Cookie', handler.cookie(handler.token()))
        handler.send_header('Connection', 'close')
        if handler.command == 'HEAD' and response.getheader('Content-Length'):
            handler.send_header('Content-Length', response.getheader('Content-Length'))
        handler.end_headers()
        sent = True
        if index_body is not None:
            handler.wfile.write(index_body)
            return
        while handler.command != 'HEAD' and active(handler, process):
            chunk = response.read1(65536)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()
    except (OSError, http.client.HTTPException, ValueError, PermissionError):
        if not sent:
            handler.output(502, {'error': 'Harness 连接中断，请在电脑 App 中检查运行状态'})
    finally:
        connection.close()


def websocket(handler, target, port, cookie, process):
    key = handler.headers.get('Sec-WebSocket-Key', '')
    try:
        valid = len(base64.b64decode(key, validate=True)) == 16
    except ValueError:
        valid = False
    if not valid or handler.headers.get('Sec-WebSocket-Version') != '13':
        raise ValueError('无效的 WebSocket 握手')
    if handler.headers.get('Transfer-Encoding') or handler.headers.get('Content-Length', '0') != '0':
        raise ValueError('WebSocket 握手不能包含请求体')
    upstream = socket.create_connection(('127.0.0.1', port), timeout=10)
    handler.close_connection = True
    sent = False
    try:
        fields = headers(handler, port, cookie, True)
        request = f'GET {target} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n'
        upstream.sendall(request.encode('latin-1'))
        # Unbuffered header read leaves any immediately emitted frames on the socket.
        response = bytearray()
        while not response.endswith(b'\r\n\r\n') and len(response) < 65536:
            byte = upstream.recv(1)
            if not byte:
                break
            response.extend(byte)
        rows = response.decode('latin-1').split('\r\n')
        values = {}
        for row in rows[1:]:
            if ':' in row:
                name, value = row.split(':', 1)
                values[name.lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if (not rows[0].startswith('HTTP/1.1 101 ') or values.get('sec-websocket-accept') != expected
                or values.get('upgrade', '').lower() != 'websocket'):
            raise ValueError('Harness WebSocket 握手失败')
        handler.send_response(101)
        handler.send_header('Upgrade', 'websocket')
        handler.send_header('Connection', 'Upgrade')
        for name in ('sec-websocket-accept', 'sec-websocket-protocol', 'sec-websocket-extensions'):
            if name in values:
                handler.send_header(name, values[name])
        handler.end_headers()
        handler.wfile.flush()
        sent = True
        client = handler.connection
        client.setblocking(False)
        upstream.setblocking(False)
        # BaseHTTPRequestHandler may already have read the first client frame
        # while parsing headers. Drain that buffer before switching to sockets;
        # keep normal buffered body reads intact for every other Bridge route.
        pending = handler.rfile.read1(65536)
        buffers = {client: bytearray(), upstream: bytearray(pending or b'')}
        peers = {client: upstream, upstream: client}
        checked = 0
        while True:
            if time.monotonic() - checked >= 1:
                if not active(handler, process):
                    break
                checked = time.monotonic()
            readers = [s for s in peers if len(buffers[peers[s]]) < 262144]
            writers = [s for s, buffer in buffers.items() if buffer]
            readable, writable, _ = select.select(readers, writers, [], 1)
            for sock in writable:
                try:
                    count = sock.send(buffers[sock])
                    del buffers[sock][:count]
                except BlockingIOError:
                    pass
            for sock in readable:
                try:
                    chunk = sock.recv(65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    return
                buffers[peers[sock]].extend(chunk)
    except (OSError, ValueError):
        if not sent:
            handler.output(502, {'error': 'Harness 实时连接失败'})
    finally:
        upstream.close()
