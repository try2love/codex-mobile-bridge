"""Same-origin HTTP/SSE gateway. Desktop RPC is never exposed directly."""
import gzip
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import socket
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, urlsplit, quote

from .auth import Auth
from .files import preview_project_file, preview_visualization, UPLOAD_LIMIT
from .attachments import MAX_FILE_BYTES
from .ipc import IPCError
from .catalog import CatalogError
from .creator import CreationError
from .runtime import RuntimeErrorDetail
from .remote import RemoteUnavailable

LOG = logging.getLogger(__name__)
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/compat.js": ("compat.js", "text/javascript; charset=utf-8"),
          "/chat-extras.js": ("chat-extras.js", "text/javascript; charset=utf-8"),
          "/conversation-switcher.js": ("conversation-switcher.js", "text/javascript; charset=utf-8"),
          "/chat-tools.js": ("chat-tools.js", "text/javascript; charset=utf-8"),
          "/improvements.js": ("improvements.js", "text/javascript; charset=utf-8"),
          "/project-mounts.js": ("project-mounts.js", "text/javascript; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
          "/timeline.js": ("timeline.js", "text/javascript; charset=utf-8"),
          "/vendor/markdown-it.min.js": ("vendor/markdown-it.min.js", "text/javascript; charset=utf-8"),
          "/vendor/texmath.js": ("vendor/texmath.js", "text/javascript; charset=utf-8"),
          "/vendor/katex/katex.min.js": ("vendor/katex/katex.min.js", "text/javascript; charset=utf-8"),
          "/vendor/katex/katex.min.css": ("vendor/katex/katex.min.css", "text/css; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
          "/brand-icon.png": ("brand-icon.png", "image/png"),
          "/icon.svg": ("icon.svg", "image/svg+xml")}
THREAD_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})(?:/(events|send|stop|history|respond|reconnect|queue|catalog|settings|permissions|poll|timeline|changes|detail|manage|open|info))?$")
FONT_ROUTE = re.compile(r"^/vendor/katex/fonts/(KaTeX_[A-Za-z0-9_-]+\.(woff2|woff|ttf))$")


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, bridge, config, web_dir):
        self.bridge = bridge
        data_dir = getattr(bridge, 'data_dir', None)
        self.auth = Auth(config["auth"], Path(data_dir) / 'remembered_sessions.json' if data_dir else None)
        self.origins = set(config["origins"])
        self.hosts = {urlsplit(o).netloc for o in self.origins}
        self.secure_hosts = {urlsplit(o).netloc for o in self.origins if o.startswith("https://")}
        self.web_dir = Path(web_dir)
        self.slots = threading.BoundedSemaphore(48)
        super().__init__(address, Handler)

    def server_bind(self):
        # HTTPServer resolves the listening IP with getfqdn(), which can stall
        # startup on machines without working reverse DNS. Origins are explicit.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        LOG.warning("HTTP connection failed")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CodexMobile/0.1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, format, *args):
        # Request bodies, cookies, query strings and conversation IDs are private.
        pass

    def headers_common(self, visualization=False):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if visualization else "DENY")
        # KaTeX emits inline layout styles; scripts and stylesheets stay same-origin.
        policy = "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
        if visualization:
            policy = "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net https://esm.sh https://cdnjs.cloudflare.com; style-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; img-src data: blob:; font-src data: https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; connect-src 'none'; frame-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
        self.send_header("Content-Security-Policy", policy)
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def output(self, status, data, content_type="application/json; charset=utf-8", cookie=None, visualization=False):
        body = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
        accepts_gzip = re.search(r'(?:^|,)\s*gzip\s*(?:;\s*q=([01](?:\.\d+)?))?\s*(?:,|$)',
                                 self.headers.get('Accept-Encoding', ''), re.IGNORECASE)
        compressible = content_type.startswith(('text/', 'application/json', 'image/svg+xml'))
        compressed = False
        if compressible and len(body) >= 1024 and accepts_gzip and float(accepts_gzip[1] or '1') > 0:
            candidate = gzip.compress(body, compresslevel=5, mtime=0)
            if len(candidate) < len(body):
                body, compressed = candidate, True
        self.send_response(status)
        self.headers_common(visualization=visualization)
        self.send_header("Content-Type", content_type)
        self.send_header('Vary', 'Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def cookie(self, token, clear=False, remember=False):
        secure = "; Secure" if self.headers.get("Host") in self.server.secure_hosts else ""
        age = 0 if clear else Auth.REMEMBER_SECONDS if remember else Auth.SESSION_SECONDS
        return f"{Auth.COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}"

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            return cookie[Auth.COOKIE].value if Auth.COOKIE in cookie else ""
        except Exception:
            return ""

    def check_request(self, write=False):
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") not in self.server.hosts:
            raise PermissionError("此访问地址未在网关配置中允许")
        origin = self.headers.get("Origin")
        if (origin and origin not in self.server.origins) or (write and not origin):
            raise PermissionError("不允许跨站请求")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise PermissionError("不允许跨站请求")

    def authorized(self, write=False):
        session = self.server.auth.get(self.token())
        if not session:
            self.close_connection = True
            self.output(401, {"error": "请登录", "code": "unauthenticated"})
            return None
        if write and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            raise PermissionError("登录验证已失效，请刷新页面")
        return session

    def read_json(self, max_bytes=512000):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("不支持分块请求体")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("需要 JSON 请求")
        sizes = self.headers.get_all("Content-Length", [])
        if len(sizes) != 1 or not sizes[0].isdigit():
            raise ValueError("请求长度无效")
        size = int(sizes[0])
        if not 0 < size <= max_bytes:
            raise ValueError("请求过大或为空")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("请求内容必须为对象")
        return value

    def do_GET(self):
        self.handle_method(False)

    def do_POST(self):
        self.handle_method(True)

    def handle_method(self, write):
        try:
            self.check_request(write)
            path = urlsplit(self.path).path
            query = parse_qs(urlsplit(self.path).query)
            if not write and path in STATIC:
                name, content_type = STATIC[path]
                return self.output(200, (self.server.web_dir / name).read_bytes(), content_type)
            font = FONT_ROUTE.fullmatch(path)
            if not write and font:
                file = self.server.web_dir / 'vendor/katex/fonts' / font[1]
                if file.is_file():
                    return self.output(200, file.read_bytes(), 'font/'+font[2])
            if not write and path == "/api/auth":
                session = self.server.auth.get(self.token())
                return self.output(200, {"authenticated": bool(session), "csrf": session["csrf"] if session else None,
                                         "passwordless": self.server.auth.config.get("mode") == "none",
                                         "transport": "poll" if self.headers.get("Host", "").endswith(".trycloudflare.com") else "sse"})
            if write and path == "/api/login":
                body = self.read_json()
                username, password = body.get("username", ""), body.get("password", "")
                if not isinstance(username, str) or not isinstance(password, str) or len(username) > 200 or len(password) > 1000:
                    raise ValueError("账号或密码格式不正确")
                token, session = self.server.auth.login(username, password, self.client_address[0], body.get('remember', False))
                self.server.auth.logout(self.token())
                return self.output(200, {"csrf": session["csrf"]}, cookie=self.cookie(token, remember=session['remember']))
            auth = self.authorized(write)
            if not auth:
                return
            if write and path == "/api/logout":
                self.read_json()
                self.server.auth.logout(self.token())
                return self.output(200, {"ok": True}, cookie=self.cookie("", clear=True))
            if not write and path == "/api/sessions":
                rows = self.server.bridge.list(query=query.get("q", [""])[0][:200], offset=max(0, int(query.get("offset", [0])[0])), archived=query.get("archived", ["false"])[0] == "true", working=query.get("working", ["false"])[0] == "true")
                return self.output(200, {"sessions": rows, "unavailableHosts": self.server.bridge.host_errors})
            if not write and path == "/api/task-status":
                return self.output(200, self.server.bridge.task_statuses(include_recent=query.get("switcher", ["false"])[0] == "true"))
            if not write and path == "/api/projects":
                return self.output(200, {"projects": self.server.bridge.projects()})
            if not write and path == '/api/diagnostics':
                return self.output(200, self.server.bridge.diagnostics())
            if write and path == '/api/creations':
                body = self.read_json(4096)
                return self.output(202, self.server.bridge.start_creation(body.get('projectKey'), body.get('title', ''), body.get('id')))
            if not write and path == '/api/creations':
                return self.output(200, {'creations': self.server.bridge.recent_creations()})
            creation = re.fullmatch(r'/api/creations/([0-9a-f-]{36})', path)
            if creation:
                if write:
                    body = self.read_json(4096)
                    if body.get('action') != 'recover':
                        raise ValueError('不支持的恢复操作')
                return self.output(200, self.server.bridge.creation_status(creation[1], recover=write))
            if write and path == "/api/sessions":
                body = self.read_json(4096)
                return self.output(200, self.server.bridge.create_chat(
                    body.get('projectKey'), body.get('title', ''), body.get('id')))
            bridge = self.server.bridge.for_host(query.get("host", ["local"])[0])
            mounts_match = re.fullmatch(r'/api/sessions/([0-9a-f-]{36})/project-mounts', path)
            if mounts_match:
                if not write:
                    return self.output(200, bridge.project_mounts(mounts_match[1]))
                body = self.read_json(32000)
                if body.get('action') == 'check':
                    return self.output(200, bridge.project_mount_check(mounts_match[1], body))
                return self.output(200, bridge.change_project_mount(mounts_match[1], body))
            attachment_match = re.fullmatch(r'/api/sessions/([0-9a-f-]{36})/attachments', path)
            if attachment_match and write:
                sizes = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not sizes[0].isdigit():
                    raise ValueError('上传长度无效')
                size = int(sizes[0])
                if size > MAX_FILE_BYTES:
                    raise ValueError('单个附件不能超过 20 MB')
                if self.headers.get_content_type() != 'application/octet-stream':
                    raise ValueError('上传内容格式无效')
                return self.output(200, bridge.upload_attachment(attachment_match[1],
                                   query.get('name', [''])[0], self.rfile, size))
            project_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/project-files(?:/(download|upload|preview|image))?", path)
            if project_match:
                relative = query.get('path', [''])[0]
                artifact_id = query.get('artifact', [''])[0]
                action = project_match[2]
                if write:
                    if artifact_id:
                        raise ValueError('请从项目文件入口上传')
                    if action != 'upload':
                        raise ValueError('不支持的文件操作')
                    sizes = self.headers.get_all('Content-Length', [])
                    if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not sizes[0].isdigit():
                        raise ValueError('上传长度无效')
                    size = int(sizes[0])
                    if size > UPLOAD_LIMIT:
                        raise ValueError('单个文件不能超过 50 MB')
                    if self.headers.get_content_type() != 'application/octet-stream':
                        raise ValueError('上传内容格式无效')
                    return self.output(200, bridge.upload_file(project_match[1], relative,
                                       query.get('name', [''])[0], self.rfile, size))
                if action in ('download', 'preview', 'image'):
                    target = bridge.project_files(project_match[1], relative, download=True, artifact_id=artifact_id)
                    if action == 'preview':
                        return self.output(200, preview_project_file(target))
                    if action == 'image':
                        info = preview_project_file(target)
                        if info['kind'] != 'image':
                            raise ValueError('不是可预览的图片')
                        return self.output(200, target.read_bytes(), info['mime'])
                    return self.download_project_file(target)
                if action:
                    raise ValueError('不支持的文件操作')
                return self.output(200, bridge.project_files(project_match[1], relative,
                                   max(0, int(query.get('offset', ['0'])[0])),
                                   artifact_id=artifact_id, locate=query.get('locate', ['0'])[0] == '1'))
            visualization_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/visualizations/([a-f0-9]{64})", path)
            if not write and visualization_match:
                try:
                    artifact = bridge.artifact(*visualization_match.groups())
                    content = preview_visualization(artifact)
                except (KeyError, ValueError, OSError):
                    return self.output(404, '<!doctype html><meta charset="utf-8"><p>可视化预览暂不可用，请重新加载对话或下载文件查看。</p>'.encode('utf-8'), 'text/html; charset=utf-8', visualization=True)
                return self.output(200, content, 'text/html; charset=utf-8', visualization=True)
            file_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/files/([a-f0-9]{64})", path)
            if not write and file_match:
                return self.download(bridge, *file_match.groups())
            image_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/images/([a-f0-9]{64})", path)
            if not write and image_match:
                data, mime = bridge.uploaded_image(*image_match.groups())
                return self.output(200, data, mime)
            match = THREAD_ROUTE.fullmatch(path)
            if not match:
                return self.output(404, {"error": "页面不存在"})
            thread_id, action = match.groups()
            if not write:
                if action == 'info':
                    return self.output(200, bridge.chat_info(thread_id))
                if action == 'timeline':
                    return self.output(200, bridge.timeline_read(thread_id, limit=int(query.get('limit', ['20'])[0]), before=query.get('before', [None])[0]))
                if action == 'detail':
                    return self.output(200, bridge.timeline_read(thread_id, 'detail', cursor=query.get('key', [''])[0],
                                                               offset=int(query.get('offset', ['0'])[0]), version=query.get('version', [None])[0]))
                if action == 'changes':
                    return self.changes(bridge, thread_id, int(query.get('after', ['-1'])[0]),
                                        query.get('epoch', [''])[0], query.get('start', [''])[0])
                if action is None:
                    return self.output(200, bridge.view(thread_id, background=True))
                if action == "catalog":
                    return self.output(200, bridge.catalog(thread_id, refresh=query.get("refresh") == ["true"]))
                if action == "poll":
                    return self.poll(bridge, thread_id, int(query.get("after", ["-1"])[0]))
                if action == "events":
                    return self.stream(bridge, thread_id)
                return self.output(404, {"error": "接口不存在"})
            body = self.read_json(12 * 1024 * 1024 if action == 'send' else 512000)
            if action == "send":
                result = bridge.send(thread_id, body.get("text"), body.get("id", ""), body.get("mode", "send"), body.get("skills", []), body.get('images', []), body.get('files', []))
            elif action == 'manage':
                result = bridge.manage_chat(thread_id, body.get('action'), body.get('title'), body.get('pinned'), body.get('confirmId'))
                if body.get('action') == 'delete':
                    root = self.server.bridge
                    with root.creation_lock:
                        for entry in root.creations.values():
                            if entry.get('result', {}).get('id') == thread_id and entry.get('result', {}).get('host', 'local') == bridge.host:
                                entry.update(status='deleted', message='这条对话已删除')
                                entry.pop('result', None)
                        root._save_creations()
            elif action == 'open':
                result = bridge.open_chat(thread_id)
            elif action == "settings":
                result = bridge.settings(thread_id, body.get("model"), body.get("effort"))
            elif action == "permissions":
                result = bridge.permissions(thread_id, body.get('mode'))
            elif action == "stop":
                result = bridge.interrupt(thread_id)
            elif action == "history":
                result = bridge.full_history(thread_id)
            elif action == "respond":
                response = body.get("response")
                if not isinstance(response, dict):
                    raise ValueError("请求回应格式不正确")
                result = bridge.respond(thread_id, body.get("requestId"), response)
            elif action == "reconnect":
                if body.get('progressive'):
                    bridge.session(thread_id, background=True, force=True)
                    result = {'ok': True}
                else:
                    result = bridge.view(thread_id, background=True, force=True)
            elif action == "queue":
                result = bridge.cancel_queued(thread_id, body.get("id", ""))
            else:
                return self.output(404, {"error": "接口不存在"})
            self.output(200, result)
        except PermissionError as exc:
            self.close_connection = True
            self.output(403, {"error": str(exc)})
        except KeyError:
            self.close_connection = True
            self.output(404, {"error": "找不到这个会话"})
        except ValueError as exc:
            self.close_connection = True
            self.output(400, {"error": str(exc)})
        except RuntimeErrorDetail as exc:
            self.close_connection = True
            self.output(409, {'error': str(exc), 'code': 'outcome_unknown' if exc.uncertain else 'operation_failed'})
        except (IPCError, CatalogError, RemoteUnavailable) as exc:
            self.close_connection = True
            self.output(409, {"error": str(exc), "code": "desktop_unavailable"})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception:
            LOG.exception("Gateway operation failed")
            self.close_connection = True
            self.output(500, {"error": "网关操作失败，请查看本机日志"})

    def download_project_file(self, path, mime='application/octet-stream', disposition='attachment', name=None):
        with path.open('rb') as stream:
            import os
            stat = os.fstat(stream.fileno())
            length = stat.st_size
            etag = '"' + hashlib.sha256(f'{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{length}'.encode()).hexdigest() + '"'
            start, end, partial = 0, length - 1, False
            requested = self.headers.get('Range')
            if requested and self.headers.get('If-Range', etag) == etag:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested.strip())
                valid = bool(match and any(match.groups()) and length)
                if valid:
                    first, last = match.groups()
                    if first:
                        start = int(first)
                        end = min(int(last), length - 1) if last else length - 1
                    else:
                        count = int(last)
                        start = max(0, length - count)
                        valid = count > 0
                    valid = valid and 0 <= start <= end < length
                if not valid:
                    self.send_response(416)
                    self.headers_common()
                    self.send_header('Content-Range', f'bytes */{length}')
                    self.send_header('ETag', etag)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                partial = True
            remaining = end - start + 1
            self.send_response(206 if partial else 200)
            self.headers_common()
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(remaining))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('ETag', etag)
            if partial:
                self.send_header('Content-Range', f'bytes {start}-{end}/{length}')
            self.send_header('Content-Disposition', disposition + "; filename*=UTF-8''" + quote(name or path.name, safe=''))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            stream.seek(start)
            while remaining:
                chunk = stream.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def download(self, bridge, thread_id, artifact_id):
        artifact = bridge.artifact(thread_id, artifact_id)
        mime = mimetypes.guess_type(artifact["name"])[0] if artifact["image"] else "application/octet-stream"
        disposition = "inline" if artifact["image"] else "attachment"
        return self.download_project_file(artifact['path'], mime or 'application/octet-stream', disposition, artifact['name'])

    def poll(self, bridge, thread_id, after):
        session = bridge.session(thread_id, background=True)
        with session.condition:
            session.viewers += 1
        try:
            with session.condition:
                session.condition.wait_for(lambda: session.sequence != after or bridge.closed.is_set(), timeout=12)
                changed = session.sequence != after
            if not self.authorized():
                return
            self.output(200, {"state": bridge.view(thread_id, attach=False, background=True) if changed else None})
        finally:
            with session.condition:
                session.viewers -= 1
                session.touched = time.monotonic()

    def changes(self, bridge, thread_id, after, epoch, start):
        session = bridge.session(thread_id, background=True)
        with session.condition:
            session.viewers += 1
        try:
            with session.condition:
                session.condition.wait_for(lambda: session.sequence != after or epoch != session.timeline.epoch or bridge.closed.is_set(), timeout=12)
            if self.authorized():
                self.output(200, bridge.timeline_read(thread_id, 'changes', after=after, epoch=epoch, start=start))
        finally:
            with session.condition:
                session.viewers -= 1
                session.touched = time.monotonic()

    def stream(self, bridge, thread_id):
        session = bridge.session(thread_id, background=True)
        token = self.token()
        with session.condition:
            session.viewers += 1
        self.send_response(200)
        self.headers_common()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        sequence = -1
        try:
            while not bridge.closed.is_set():
                if not self.server.auth.get(token):
                    self.wfile.write(b'event: logout\ndata: {}\n\n')
                    self.wfile.flush()
                    break
                with session.condition:
                    session.condition.wait_for(lambda: session.sequence != sequence or bridge.closed.is_set(), timeout=12)
                    updated = session.sequence != sequence
                    sequence = session.sequence
                if updated:
                    view = bridge.view(thread_id, attach=False, background=True)
                    payload = json.dumps(view, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"id: {sequence}\nevent: state\ndata: {payload}\n\n".encode())
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                bridge.closed.wait(0.2)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        finally:
            with session.condition:
                session.viewers -= 1
                session.touched = time.monotonic()
