"""Same-origin HTTP/SSE gateway. Desktop RPC is never exposed directly."""
import base64
import gzip
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import secrets
import socket
import platform
import threading
import time
from functools import lru_cache
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from bridge.features.workspace.uploads import MAX_FILE
from bridge.features.workspace.workspace import MAX_TRANSFER, Workspace
from bridge.features.workspace.preferences import transfer_settings
from bridge.features.workspace.files import workspace_relative_path, media_type
from bridge.features.notifications.channels import settings as notification_settings, save_settings as save_notification_settings, save_webhook_endpoint, webhook_config, publish, publish_bark, publish_pushplus, publish_webhook, visible_webhook, WebhookDeliveryError, resolved_webhook_provider
from socketserver import TCPServer
from urllib.parse import parse_qs, parse_qsl, urlsplit, quote

from bridge.clients.manager import DesktopSessions, READS, WRITES
from bridge.app.service import SubmissionRejected
from bridge.clients.uploads import DesktopUploads
from bridge.clients.deepseek.legacy_gateway import Harness
from bridge.clients.deepseek.legacy_proxy import proxy as harness_proxy
from bridge.features.auth.pairing import Pairing
from bridge.features.auth.auth import Auth, LoginRejected
from bridge.features.updates.mobile import MobileUpdates
from bridge.clients.codex.ipc import IPCError
from bridge.features.sessions.goal import GoalError
from bridge.clients.codex.catalog import CatalogError
from bridge.features.sessions.store import StoreUnavailable
from bridge.clients.codex.remote import RemoteUnavailable
from bridge.features.sessions.create import ArchiveRejected, CreationError
from bridge.features.accounts.account import AccountError
from bridge.features.terminals.manager import TerminalSessionMissing
from bridge.api.html_preview import SHELL_PATH, SHELL_FILE, SHELL_CSP
from bridge.api.mermaid_render import SHELL_PATH as MERMAID_SHELL_PATH, shell_csp as mermaid_shell_csp, shell_bytes as mermaid_shell_bytes, is_public_renderer_script

LOG = logging.getLogger(__name__)
from bridge.features.endpoints.registry import codex_list_hosts
from bridge.api.assets import STATIC, FONT_ROUTE, PDF_CMAP_ROUTE, PDF_FONT_ROUTE, VERSION_KEY, served_asset_bytes


@lru_cache(maxsize=32)
def _prepared_static(data, content_type, use_gzip):
    digest = hashlib.sha256(data).hexdigest()
    compressed = False
    if use_gzip and content_type.startswith(('text/', 'application/manifest+json')) and len(data) >= 1024:
        candidate = gzip.compress(data, compresslevel=5, mtime=0)
        if len(candidate) < len(data):
            data, compressed = candidate, True
    etag = '"sha256-' + hashlib.sha256(data).hexdigest() + '"'
    return digest, data, compressed, etag

from bridge.features.sessions.cli_release import CliConnections
from bridge.app.lifecycle import private_json

THREAD_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})(?:/(events|send|stop|history|respond|reconnect|cli-network|queue|catalog|settings|permissions|poll|timeline|changes|detail|notifications|uploads|message-action|message-management|rename|pin|archive|delete|organization|compact))?$")
UPLOAD_PREVIEW_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})/uploads/([0-9a-f-]{36})/preview$")
UPLOAD_THUMB_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})/uploads/([0-9a-f-]{36})/thumb$")
DESKTOP_IMAGE_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})/desktop-images/([0-9a-f]{64})$")
GOAL_CANCEL_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})/goal/cancel$")
GOAL_EDIT_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})/goal/edit$")
GOAL_STATUS_ROUTE = re.compile(r"^/api/sessions/([0-9a-f-]{36})/goal/status$")


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # HTTP/2 proxies fan a cold page out into many simultaneous HTTP/1.1 connects.
    request_queue_size = 128

    def __init__(self, address, bridge, config, web_dir, data_dir=None, shared=None):
        self.transfer_directory = data_dir if shared is None else shared.transfer_directory
        self.mobile_updates_owner = shared is None and data_dir is not None
        self.mobile_updates = MobileUpdates(data_dir) if self.mobile_updates_owner else getattr(shared, 'mobile_updates', None)
        self.local_access = config.get('localAccess', True)
        if shared is None:
            self.bridge = bridge
            self.notifications = None
            self.network_adaptation = None
            self.harness = Harness(data_dir) if data_dir else None
            self.desktop_sessions = DesktopSessions(data_dir, bridge, gateway_running=True) if data_dir else None
            self.desktop_uploads = DesktopUploads(data_dir) if data_dir else None
            self.cli_connections = CliConnections(data_dir, lambda: [self.bridge, self.desktop_sessions], private_json) if data_dir else None
            self.instance_id = secrets.token_hex(16)
            self.auth = Auth(config["auth"], data_dir)
            self.origins = set(config["origins"])
            self.pairing = Pairing(self.auth, self.origins)
            self.hosts = {urlsplit(o).netloc for o in self.origins}
            self.secure_hosts = {urlsplit(o).netloc for o in self.origins if o.startswith("https://")}
            self.ui_language_path = Path(data_dir) / 'ui-language.json' if data_dir else None
            self.web_dir = Path(web_dir)
            self.slots = threading.BoundedSemaphore(48)
            self.terminal_sockets = threading.BoundedSemaphore(8)
        else:
            # Listeners serve one gateway: shared sessions, pairing, limits and
            # mutable tunnel origins, with a single notification manager.
            for name in ('bridge', 'cli_connections', 'harness', 'desktop_sessions', 'desktop_uploads', 'notifications', 'network_adaptation', 'instance_id', 'auth', 'origins', 'pairing', 'hosts', 'secure_hosts', 'web_dir', 'slots', 'terminal_sockets', 'ui_language_path'):
                setattr(self, name, getattr(shared, name))
        try:
            super().__init__(address, Handler)
        except OSError:
            if shared is None:
                if self.harness:
                    self.harness.close()
                if self.desktop_sessions:
                    self.desktop_sessions.close()
            raise

    def ui_language(self):
        try:
            value = json.loads(self.ui_language_path.read_text(encoding='utf-8')) if self.ui_language_path else {}
            return 'en' if value.get('language') == 'en' else 'zh'
        except (OSError, ValueError, AttributeError):
            return 'zh'

    def server_close(self):
        if self.mobile_updates_owner and self.cli_connections:
            self.cli_connections.close()
        if self.mobile_updates_owner:
            self.mobile_updates.close()
        super().server_close()

    def server_bind(self):
        # HTTPServer resolves the listening IP with getfqdn(), which can stall
        # startup on machines without working reverse DNS. Origins are explicit.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            LOG.warning("HTTP transport stage=admission outcome=capacity_rejected")
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

    def shutdown_request(self, request):
        # A rejected POST can still have unread body bytes. Closing immediately
        # can reset the connection on Windows before the client receives 401/403.
        # Half-close first, then discard a bounded tail without handling requests.
        try:
            request.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + .2
            remaining = 65536
            while remaining and time.monotonic() < deadline:
                request.settimeout(max(.001, deadline - time.monotonic()))
                chunk = request.recv(min(8192, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            super().shutdown_request(request)


class Handler(BaseHTTPRequestHandler):
    def desktop_context(self, provider, query, *, require_enabled=True):
        """Resolve explicit entries once; unknown entries never use defaults."""
        manager = self.server.desktop_sessions
        if manager is None:
            raise ValueError('桌面会话接入不可用')
        # Preserve empty values here: ?entry= is an invalid explicit selection,
        # not permission to operate on the default account.
        values = parse_qs(urlsplit(self.path).query, keep_blank_values=True).get('entry')
        if values is None:
            return manager, self.server.desktop_uploads, 'desktop:' + provider
        if len(values) != 1 or not re.fullmatch(r'(claude|deepseek):[0-9a-f]{32}', values[0]):
            raise ValueError('独立接入标识无效')
        selected = manager.resolve_instance(provider, values[0], require_enabled=require_enabled)
        return selected, selected.uploads, 'desktop:' + values[0]

    protocol_version = "HTTP/1.1"
    server_version = "CodexMobile/0.1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, format, *args):
        # Request bodies, cookies, query strings and conversation IDs are private.
        pass

    def headers_common(self, cache_control="no-store", preview_shell=False, style_nonce=None, mermaid_shell=False):
        if getattr(self, '_transport_trace', None):
            self.send_header('X-Bridge-Request-Id', self._transport_trace['id'])
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if preview_shell or mermaid_shell else "DENY")
        # KaTeX uses style attributes; only our terminal gets nonced dynamic styles.
        host = self.headers.get('Host', '')
        origin = ('https://' if host in self.server.secure_hosts else 'http://') + host
        frame_source = origin + SHELL_PATH + ' ' + origin + MERMAID_SHELL_PATH if host in self.server.hosts else "'none'"
        style_source = "'self'" + (" 'nonce-" + style_nonce + "'" if style_nonce else "")
        self.send_header("Content-Security-Policy", SHELL_CSP if preview_shell else mermaid_shell_csp(origin if host in self.server.hosts else "'none'") if mermaid_shell else
                         "default-src 'self'; script-src 'self'; style-src " + style_source + "; style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src " + frame_source + "; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def output(self, status, data, content_type="application/json; charset=utf-8", cookie=None, content_disposition=None, preview_shell=False, style_nonce=None, mermaid_shell=False):
        # Keep the browser cookie in step with the renewed server-side deadline,
        # including long-lived pages that do not revisit /api/auth.
        if cookie is None and 200 <= status < 300 and urlsplit(self.path).path.startswith('/api/'):
            token = self.token()
            if token and self.server.auth.get(token):
                cookie = self.cookie(token)
        body = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
        accepts_gzip = re.search(r'(?:^|,)\s*gzip\s*(?:;\s*q=([01](?:\.\d+)?))?\s*(?:,|$)',
                                 self.headers.get('Accept-Encoding', ''), re.IGNORECASE)
        compressible = content_type.startswith(('text/', 'application/json', 'image/svg+xml'))
        compressed = False
        if compressible and len(body) >= 1024 and accepts_gzip and float(accepts_gzip[1] or '1') > 0:
            candidate = gzip.compress(body, compresslevel=5, mtime=0)
            if len(candidate) < len(body):
                body, compressed = candidate, True
        self._transport_status = status
        self.send_response(status)
        self.headers_common(preview_shell=preview_shell, style_nonce=style_nonce, mermaid_shell=mermaid_shell)
        self.send_header("Content-Type", content_type)
        self.send_header('Vary', 'Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        if content_disposition:
            self.send_header("Content-Disposition", content_disposition)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)
        self._transport_written = True

    def static_output(self, path, content_type):
        data = served_asset_bytes(self.server.web_dir, path)
        if path in ('/', '/index.html'):
            nonce = secrets.token_urlsafe(24)
            data = data.replace(b'<head>', ('<head><meta name="bridge-style-nonce" content="' + nonce + '">').encode(), 1)
            return self.output(200, data, content_type, style_nonce=nonce)
        accepts_gzip = re.search(r'(?:^|,)\s*gzip\s*(?:;\s*q=([01](?:\.\d+)?))?\s*(?:,|$)',
                                 self.headers.get('Accept-Encoding', ''), re.IGNORECASE)
        use_gzip = bool(accepts_gzip and float(accepts_gzip[1] or '1') > 0)
        digest, data, compressed, etag = _prepared_static(data, content_type, use_gzip)
        version = parse_qsl(urlsplit(self.path).query, keep_blank_values=True)
        immutable = version == [(VERSION_KEY, digest)]
        cache_control = 'public, max-age=31536000, immutable' if immutable else 'no-cache'
        validators = self.headers.get('If-None-Match', '')
        matches = any(tag.strip() == '*' or tag.strip().removeprefix('W/') == etag
                      for tag in validators.split(','))
        self._transport_status = 304 if matches else 200
        self.send_response(304 if matches else 200)
        self.headers_common(cache_control=cache_control)
        self.send_header('Content-Type', content_type)
        self.send_header('Vary', 'Accept-Encoding')
        self.send_header('ETag', etag)
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        if not matches:
            self.send_header('Content-Length', str(len(data)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if not matches:
            self.wfile.write(data)
        self._transport_written = True

    def cookie(self, token, clear=False):
        secure = "; Secure" if self.headers.get("Host") in self.server.secure_hosts else ""
        age = 0 if clear else self.server.auth.cookie_age(token)
        lifetime = f"; Max-Age={age}" if age is not None else ""
        return f"{self.server.auth.cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict{lifetime}{secure}"

    def client(self):
        return self.server.auth.client(self.client_address[0], self.headers, self.headers.get('Host') in self.server.secure_hosts)

    def login_session(self):
        return self.server.auth.get(self.token(), self.client(), self.headers.get('User-Agent', ''))

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            name = self.server.auth.cookie_name
            if name in cookie:
                value = cookie[name].value
                discovery = getattr(self.server, 'connection_discovery', None)
                if discovery and value.startswith('cmbroute1_'):
                    host = self.headers.get('Host', '')
                    return discovery.resolve_cookie(value, ('https://' if host in self.server.secure_hosts else 'http://') + host)
                return value
            # Legacy cookies are accepted only while their token belongs to
            # this gateway. A successful response migrates to the new name.
            legacy = cookie[Auth.COOKIE].value if Auth.COOKIE in cookie else ""
            return legacy if legacy and self.server.auth.get(legacy) else ""
        except Exception:
            return ""

    def check_request(self, write=False):
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") not in self.server.hosts:
            raise PermissionError("此访问地址未在网关配置中允许")
        host = self.headers.get('Host', '')
        local_host = urlsplit('http://' + host).hostname in ('127.0.0.1', 'localhost')
        if not self.server.local_access and (local_host or (self.connection.getsockname()[0] == '127.0.0.1' and host not in self.server.secure_hosts)):
            raise PermissionError('本机网页访问已关闭')
        origin = self.headers.get("Origin")
        expected_origin = ('https://' if host in self.server.secure_hosts else 'http://') + host
        if len(self.headers.get_all("Origin", [])) > 1 or (origin and origin != expected_origin) or (write and not origin):
            raise PermissionError("不允许跨站请求")
        # External links may open the public HTML shell. Fetches, embedded pages
        # and API requests must still pass the cross-site restriction.
        homepage_navigation = (not write and self.command == 'GET' and urlsplit(self.path).path in ('/', '/harness', '/harness/', '/mobile-channel')
                               and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                               and self.headers.get('Sec-Fetch-Dest') == 'document')
        renderer_script = is_public_renderer_script(urlsplit(self.path).path, self.command,
                                                    self.headers.get('Sec-Fetch-Dest'))
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not (homepage_navigation or renderer_script):
            raise PermissionError("不允许跨站请求")

    def authorized(self, write=False):
        if not self.server.auth.permitted(self.client()['ip']):
            raise PermissionError('此 IP 已被访问规则禁止')
        session = self.login_session()
        if not session:
            self.close_connection = True
            self.output(401, {"error": "请登录", "code": "unauthenticated"})
            return None
        if write and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            raise PermissionError("登录验证已失效，请刷新页面")
        return session

    def read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("不支持分块请求体")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("需要 JSON 请求")
        sizes = self.headers.get_all("Content-Length", [])
        if len(sizes) != 1 or not sizes[0].isdigit():
            raise ValueError("请求长度无效")
        size = int(sizes[0])
        if not 0 < size <= 512000:
            raise ValueError("请求过大或为空")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("请求内容必须为对象")
        return value

    def do_GET(self):
        # cloudflared may forward empty chunked GETs. Unread framing must not be
        # parsed as the next request on a pooled proxy connection.
        if ('Transfer-Encoding' in self.headers or
                any(size.strip() != '0' for size in self.headers.get_all('Content-Length', []))):
            self.close_connection = True
        self.handle_method(False)

    def do_POST(self):
        self.handle_method(True)

    def do_HEAD(self):
        self.harness_method()

    def do_PUT(self):
        self.harness_method()

    do_PATCH = do_PUT
    do_DELETE = do_PUT

    def harness_method(self):
        if urlsplit(self.path).path.startswith('/harness/'):
            self.handle_method(self.command not in ('GET', 'HEAD'))
        else:
            self.send_error(405)

    def handle_method(self, write):
        # Correlate transport boundaries without recording identities, URLs,
        # credentials or message contents. Reset for each keep-alive request.
        path = urlsplit(self.path).path
        route = THREAD_ROUTE.fullmatch(path)
        operation = route[2] if route else None
        ray = self.headers.get('CF-Ray', '')
        ray = ray if re.fullmatch(r'[a-fA-F0-9]{16,32}(?:-[A-Z]{3})?', ray) else '-'
        traced = operation in ('send', 'reconnect', 'message-management') or ray != '-' and path in STATIC
        self._transport_trace = {'id': secrets.token_hex(8), 'ray': ray} if traced else None
        self._transport_status, self._transport_written = 0, False
        started = time.monotonic()
        if traced:
            LOG.log(logging.INFO if operation else logging.DEBUG, 'HTTP transport id=%s ray=%s route=%s stage=received',
                     self._transport_trace['id'], ray, operation or 'static')
        try:
            return self._handle_method(write)
        finally:
            if traced:
                elapsed = (time.monotonic() - started) * 1000
                level = logging.WARNING if not self._transport_written or self._transport_status >= 400 or elapsed >= 2000 else (
                    logging.INFO if operation else logging.DEBUG)
                LOG.log(level, 'HTTP transport id=%s ray=%s route=%s stage=response status=%d written=%s elapsed_ms=%.1f',
                        self._transport_trace['id'], ray, operation or 'static', self._transport_status, self._transport_written, elapsed)

    def _handle_method(self, write):
        try:
            if urlsplit(self.path).path == '/harness' or urlsplit(self.path).path.startswith('/harness/'):
                return harness_proxy(self)
            if not write and self.path == '/api/health':
                expected = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
                if (self.client_address[0] != '127.0.0.1' or self.connection.getsockname()[0] != '127.0.0.1'
                        or self.headers.get_all('Host', []) not in [[host] for host in expected]
                        or self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                    raise PermissionError('仅允许本机状态检查')
                return self.output(200, {'service': 'codex-mobile-bridge', 'instanceId': self.server.instance_id,
                                         'notifications': self.server.notifications is not None})
            self.check_request(write)
            path = urlsplit(self.path).path
            query = parse_qs(urlsplit(self.path).query)
            if not write and path == '/mobile-channel':
                # The App supplies the transport code only to its verified secondary view.
                return self.output(200, b'<!doctype html><meta charset=utf-8><title>Bridge</title>', 'text/html; charset=utf-8')
            if not write and path in STATIC:
                _, content_type = STATIC[path]
                return self.static_output(path, content_type)
            font = FONT_ROUTE.fullmatch(path)
            if not write and font:
                file = self.server.web_dir / 'vendor/katex/fonts' / font[1]
                if file.is_file():
                    return self.static_output(path, 'font/'+font[2])
            cmap = PDF_CMAP_ROUTE.fullmatch(path)
            if not write and cmap:
                file = self.server.web_dir / 'vendor/pdfjs/cmaps' / cmap[1]
                if file.is_file():
                    return self.static_output(path, 'application/octet-stream')
            pdf_font = PDF_FONT_ROUTE.fullmatch(path)
            if not write and pdf_font:
                file = self.server.web_dir / 'vendor/pdfjs/standard_fonts' / pdf_font[1]
                if file.is_file():
                    return self.static_output(path, 'font/ttf' if file.suffix == '.ttf' else 'application/octet-stream')
            if not write and path == "/api/auth":
                session = self.login_session()
                discovery = getattr(self.server, 'connection_discovery', None)
                return self.output(200, {"uiLanguage": self.server.ui_language(), "authenticated": bool(session), "trustedDevice": bool(session and session.get("trustedDevice")), "csrf": session["csrf"] if session else None,
                                         "loginStatus": self.server.auth.login_status(self.client()["ip"]),
                                         "instanceId": self.server.instance_id, "cookieName": self.server.auth.cookie_name,
                                         **({'draftIdentity': self.server.auth.draft_identity} if session else {}),
                                         **({'enabledClients': self.server.desktop_sessions.navigation_clients() if self.server.desktop_sessions else ['codex']} if session else {}),
                                         **({'listCacheIdentity': hashlib.sha256((self.server.instance_id + '\0' + session['csrf']).encode()).hexdigest()} if session else {}),
                                         **({"computer": {"name": socket.gethostname(), "platform": platform.system()}} if session else {}),
                                         **({'mobileRoutes': discovery.mobile_routes(self.token())} if session and discovery and self.server.auth.mobile_client(self.headers.get('User-Agent', '')) else {}),
                                         **({'connection': discovery.descriptor(self.token())} if session and discovery else {}),
                                         "notifications": self.server.notifications is not None,
                                         "passwordless": self.server.auth.config.get("mode") == "none",
                                         "transport": "poll" if self.headers.get("Host", "").endswith(".trycloudflare.com") else "sse"},
                                   cookie=self.cookie(self.token()) if session else None)
            if not write and path == '/api/connection/prove':
                discovery = getattr(self.server, 'connection_discovery', None)
                if discovery is None or not self.server.auth.permitted(self.client()['ip']):
                    raise PermissionError('Connection discovery unavailable')
                host = self.headers.get('Host', '')
                target = ('https://' if host in self.server.secure_hosts else 'http://') + host
                return self.output(200, discovery.prove(query.get('id', [''])[0], query.get('challenge', [''])[0], target, query.get('device', [None])[0]))
            if not self.server.auth.permitted(self.client()['ip']) and path != '/api/login':
                raise PermissionError('此 IP 已被访问规则禁止')
            if write and path == "/api/login":
                body = self.read_json()
                username, password = body.get("username", ""), body.get("password", "")
                if not isinstance(username, str) or not isinstance(password, str) or len(username) > 200 or len(password) > 1000:
                    raise ValueError("账号或密码格式不正确")
                token, session = self.server.auth.login(username, password, self.client()['ip'], self.headers.get('User-Agent', ''), self.client(), remember=body.get('remember'))
                self.server.auth.logout(self.token())
                return self.output(200, {"csrf": session["csrf"]}, cookie=self.cookie(token))
            if write and path == '/api/pair':
                body = self.read_json()
                host = self.headers.get('Host', '')
                origin = ('https://' if host in self.server.secure_hosts else 'http://') + host
                if self.headers.get('Origin') != origin:
                    raise PermissionError('不允许的请求来源')
                token, session = self.server.pairing.exchange(body.get('token'), origin, self.client()['ip'], self.headers.get('User-Agent', ''), self.client())
                self.server.auth.logout(self.token())
                return self.output(200, {'csrf': session['csrf']}, cookie=self.cookie(token))
            auth = self.authorized(write)
            if not auth:
                return
            terminal_socket = re.fullmatch(r'/api/sessions/([0-9a-f-]{36})/terminal', path)
            if path == '/api/connection/routes':
                discovery = getattr(self.server, 'connection_discovery', None)
                if not discovery or not self.server.auth.mobile_client(self.headers.get('User-Agent', '')):
                    raise PermissionError('需要手机网关绑定')
                return self.output(200, discovery.renew(self.token(), self.read_json()) if write else discovery.mobile_routes(self.token()))
            desktop_socket = re.fullmatch(r'/api/desktop-sessions/(deepseek|claude)/terminal', path)
            if not write and self.headers.get('Upgrade', '').lower() == 'websocket' and (terminal_socket or desktop_socket):
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('终端需要启用网关密码保护')
                # Browser WebSocket cannot set a custom header. The CSRF secret
                # is sent as a subprotocol, never as a URL or cookie substitute.
                self.check_request(True)
                if desktop_socket:
                    if self.server.desktop_sessions is None:
                        return self.output(503, {'error': '桌面会话接入不可用'})
                    sid = query.get('sessionId', [''])[0]
                    manager, _, _ = self.desktop_context(desktop_socket[1], query)
                    bridge = manager.workspace_bridge(desktop_socket[1], sid)
                    thread = bridge.identifier
                    enabled = lambda: manager.require_enabled(desktop_socket[1]) is None
                else:
                    bridge = self.server.bridge.for_host(query.get('host', ['local'])[0])
                    if self.server.desktop_sessions:
                        self.server.desktop_sessions.require_enabled('codex')
                    thread = terminal_socket[1]
                    enabled = lambda: self.server.desktop_sessions is None or self.server.desktop_sessions.require_enabled('codex') is None
                if len(query.get('id', [])) != 1 or len(query.get('after', [])) != 1:
                    raise ValueError('终端连接参数无效')
                identifier = query['id'][0]
                after = int(query['after'][0])
                if not 0 <= after <= 10**12:
                    raise ValueError('无效输出游标')
                from bridge.api.terminal_ws import serve
                return serve(self, bridge, thread, self.server.auth.key(self.token()), identifier, after, auth['csrf'], enabled)
            if path == SHELL_PATH:
                if write or query:
                    return self.output(405, {'error': '请求方式无效'})
                return self.output(200, (self.server.web_dir / SHELL_FILE).read_bytes(),
                                   'text/html; charset=utf-8', preview_shell=True)
            if path == MERMAID_SHELL_PATH:
                if write or query:
                    return self.output(405, {'error': '请求方式无效'})
                return self.output(200, mermaid_shell_bytes(self.server.web_dir),
                                   'text/html; charset=utf-8', mermaid_shell=True)
            mobile_update = re.fullmatch(r'/api/mobile/update(?:/(prepare|status/([a-f0-9]{64})|packages/([a-f0-9]{64})))?', path)
            if mobile_update:
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('请启用密码保护后使用电脑协助更新')
                updates = self.server.mobile_updates
                if updates is None:
                    return self.output(503, {'error': '此网关暂不支持协助更新'})
                operation, identifier, package = mobile_update.groups()
                if write:
                    if operation != 'prepare' or query:
                        return self.output(405, {'error': '请求方式无效'})
                    return self.output(200, updates.prepare(self.read_json()))
                if operation is None:
                    if set(query) != {'current'} or len(query['current']) != 1:
                        raise ValueError('更新请求无效')
                    return self.output(200, updates.check(query['current'][0]))
                if query:
                    raise ValueError('更新请求无效')
                if identifier:
                    return self.output(200, updates.status(identifier))
                if package:
                    with updates.download(package, **self.download_request()) as (metadata, stream):
                        return self.download_response(metadata, stream, 'application/vnd.android.package-archive')
                return self.output(405, {'error': '请求方式无效'})
            if path == '/api/clients/claude/reconnect':
                if not write:
                    return self.output(405, {'error': '请求方式无效'})
                value = self.read_json()
                if value != {}:
                    raise ValueError('重新连接请求无效')
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('请启用密码保护后再重新连接客户端')
                manager, _, _ = self.desktop_context('claude', query)
                if manager is None:
                    return self.output(503, {'error': '应用管理不可用'})
                result = manager.reconnect_claude()
                if manager is not self.server.desktop_sessions:
                    result = self.server.desktop_sessions.clients(refresh=True)
                return self.output(200, result)
            if path == '/api/file-transfer':
                return self.output(200, transfer_settings(self.server.transfer_directory, self.read_json() if write else None))
            if path == '/api/network-adaptation':
                if query:
                    raise ValueError('网络变化自动恢复请求无效')
                adaptation = self.server.network_adaptation
                if adaptation is None:
                    return self.output(503, {'error': '此网关暂不支持网络变化自动恢复'})
                if write:
                    if self.server.auth.config.get('mode') == 'none':
                        raise PermissionError('请启用密码保护后更改网络恢复设置')
                    value = self.read_json()
                    if not isinstance(value, dict) or set(value) != {'enabled'}:
                        raise ValueError('网络变化自动恢复请求无效')
                    return self.output(200, {**adaptation.control({'action': 'set', **value}), 'canEdit': True})
                return self.output(200, {**adaptation.public(), 'canEdit': self.server.auth.config.get('mode') != 'none'})
            if path == '/api/clients':
                manager = self.server.desktop_sessions
                if manager is None:
                    return self.output(503, {'error': '应用管理不可用'})
                if write:
                    value = self.read_json()
                    if value == {'action': 'scan-wsl'}:
                        return self.output(200, manager.scan_wsl())
                    if not isinstance(value, dict) or not {'provider', 'enabled'} <= set(value) or set(value) - {'provider', 'enabled', 'quitDesktop', 'initializeDesktop', 'connectionMode', 'forceDesktop', 'forceConfirmed', 'acknowledgeUnavailable'}:
                        raise ValueError('应用开关无效')
                    if (value.get('provider') == 'zcode' and value.get('enabled') is True
                            and self.server.auth.config.get('mode') == 'none'):
                        raise PermissionError('请启用密码保护后再连接 ZCode')
                    return self.output(200, manager.toggle_client(value))
                return self.output(200, manager.clients_status())
            desktop_image = re.fullmatch(r'/api/desktop-sessions/(deepseek|claude|zcode|antigravity)/images/([0-9a-f]{64})', path)
            if desktop_image:
                if write:
                    return self.output(405, {'error': '请求方式无效'})
                manager = self.server.desktop_sessions
                if manager is None:
                    return self.output(503, {'error': '桌面会话接入不可用'})
                provider, identifier = desktop_image.groups()
                manager, _, _ = self.desktop_context(provider, query)
                artifact, variant = manager.image_preview(provider, query.get('sessionId', [None])[0], identifier)
                return self.attachment_preview(artifact, variant, private=True, download=query.get('download') == ['1'])
            desktop_upload = re.fullmatch(r'/api/desktop-sessions/(deepseek|claude|zcode|antigravity)/uploads(?:/([0-9a-f-]{36})/(thumb|preview))?', path)
            if desktop_upload:
                manager, uploads = self.server.desktop_sessions, self.server.desktop_uploads
                if manager is None or uploads is None:
                    return self.output(503, {'error': '桌面会话接入不可用'})
                provider, identifier, operation = desktop_upload.groups()
                manager, uploads, _ = self.desktop_context(provider, query)
                manager.require_enabled(provider)
                sid = query.get('sessionId', [None])[0]
                scope = uploads.scope(provider, sid)
                # Check the original desktop conversation before accepting files.
                detail = manager.call(provider, 'detail', sid)
                if not detail.get('session'):
                    raise ValueError('会话标识无效')
                if not write and operation == 'preview':
                    download = query.get('download') == ['1']
                    artifact, variant = uploads.store.preview(scope, identifier, 'original' if download else query.get('variant', ['thumb'])[0])
                    return self.attachment_preview(artifact, variant, download=download)
                if not write or operation == 'preview':
                    return self.output(405, {'error': '请求方式无效'})
                maximum = 1024 * 1024 if operation == 'thumb' else MAX_FILE
                sizes = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not sizes[0].isdigit() or not 0 < int(sizes[0]) <= maximum:
                    raise ValueError('附件大小无效')
                data = self.rfile.read(int(sizes[0]))
                if len(data) != int(sizes[0]):
                    raise ValueError('附件上传中断，请重试')
                if operation == 'thumb':
                    result = uploads.store.set_thumb(scope, identifier, data, int(query.get('width', ['0'])[0]), int(query.get('height', ['0'])[0]))
                else:
                    result = uploads.store.put(scope, query.get('id', [''])[0], query.get('name', [''])[0], data)
                return self.output(200, result)
            desktop_accounts = re.fullmatch(r'/api/desktop-sessions/(deepseek|claude|zcode|antigravity)/accounts', path)
            if desktop_accounts:
                manager, _, _ = self.desktop_context(desktop_accounts.group(1), query, require_enabled=False)
                if manager is None:
                    return self.output(503, {'error': '桌面会话接入不可用'})
                value = self.read_json() if write else {'action': 'list'}
                operation = value.get('action')
                if operation not in (('details', 'switch') if write else ('list',)):
                    return self.output(403, {'error': '添加、修改与删除账号请在电脑端完成。'})
                if write and operation == 'switch' and (set(value) - {'action', 'id', 'syncLocalCli'} or not isinstance(value.get('id'), str) or value.get('syncLocalCli', False) not in (True, False)):
                    raise ValueError('账号切换请求包含不支持的字段')
                if operation == 'switch' and self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('请启用密码保护后再切换账号')
                return self.output(200, manager.client_accounts(desktop_accounts.group(1), {**value, 'operation': operation}))
            desktop_notifications = re.fullmatch(r'/api/desktop-sessions/(deepseek|claude|zcode|antigravity)/notifications', path)
            if desktop_notifications:
                manager = self.server.desktop_sessions
                if manager is None or self.server.notifications is None:
                    return self.output(503, {'error': '桌面会话接入不可用'})
                provider = desktop_notifications.group(1)
                manager, _, notification_host = self.desktop_context(provider, query)
                manager.require_enabled(provider)
                sid = query.get('sessionId', [''])[0]
                return self.output(200, self.server.notifications.policy(
                    sid, notification_host, self.read_json() if write else None))
            desktop_workspace = re.fullmatch(r'/api/desktop-sessions/(deepseek|claude|zcode|antigravity)/(workspace(?:/[a-z-]+)?|terminal)', path)
            desktop_projects = re.fullmatch(r'/api/desktop-sessions/(deepseek|claude|zcode|antigravity)/projects/(browse|register|trust)', path)
            if desktop_projects:
                if not write:
                    return self.output(405, {'error': '请通过目录选择器浏览'})
                manager = self.server.desktop_sessions
                if manager is None:
                    return self.output(503, {'error': '桌面会话接入不可用'})
                provider, action = desktop_projects.groups()
                manager, _, _ = self.desktop_context(provider, query)
                manager.require_enabled(provider)
                body = self.read_json()
                fields = ({'environment', 'path', 'hidden', 'name', 'identity'} if action == 'browse'
                          else {'projectKey', 'confirmed'} if action == 'trust'
                          else {'environment', 'path', 'createDirectory'})
                if not isinstance(body, dict) or set(body) != fields:
                    raise ValueError('项目目录请求无效')
                if action == 'browse':
                    return self.output(200, manager.project_directories(provider, **body))
                if action == 'trust':
                    return self.output(200, manager.project_trust(provider, **body))
                return self.output(200, {'project': manager.register_project(provider,
                    body['environment'], body['path'], body['createDirectory'])})
            if path == '/api/cli-connections':
                connections = self.server.cli_connections
                if connections is None:
                    raise ValueError('CLI 连接管理不可用')
                return self.output(200, connections.submit(self.read_json()) if write else connections.view())
            if path.startswith('/api/desktop-sessions/') and not desktop_workspace:
                parts = path.strip('/').split('/')
                manager = self.server.desktop_sessions
                if manager is None:
                    return self.output(503, {'error': '桌面会话接入不可用'})
                if len(parts) != 4 or parts[2] not in ('deepseek', 'claude', 'zcode', 'antigravity') or parts[3] not in (WRITES if write else READS):
                    return self.output(404, {'error': '接口不存在'})
                manager, uploads, _ = self.desktop_context(parts[2], query)
                manager.require_enabled(parts[2])
                body = self.read_json() if write else ({'section': query.get('section', ['all'])[0],
                                                         'refresh': query.get('refresh') == ['true']}
                                                        if parts[3] == 'catalog' else
                                                        {'refresh': query.get('refresh') == ['true']} if parts[3] == 'account' else {})
                sid = body.pop('sessionId', None) if write else query.get('sessionId', [None])[0]
                if not write and parts[3] == 'create-models':
                    body = {key: query[key][0] for key in ('environment', 'projectKey', 'path') if key in query}
                    body['refresh'] = query.get('refresh') == ['true']
                if not write and parts[3] == 'detail':
                    body['historyLimit'] = max(1, min(100, int(query.get('historyLimit', ['40'])[0])))
                    if query.get('historyBefore'):
                        before = query['historyBefore'][0]
                        if len(before) > 16384:
                            raise ValueError('历史游标过长')
                        body['historyBefore'] = before
                if write and any(key in body for key in ('resolvedImages', 'resolvedFiles', 'resolvedReferences')):
                    raise ValueError('附件格式无效')
                if write and parts[3] == 'send' and 'attachments' in body:
                    if uploads is None:
                        return self.output(503, {'error': '桌面会话接入不可用'})
                    detail = manager.call(parts[2], 'detail', sid)
                    session = detail.get('session') or {}
                    local_cli = parts[2] == 'claude' and session.get('backend') == 'cli'
                    images, files = uploads.resolve(
                        parts[2], sid, body.pop('attachments'), local_claude_cli=local_cli, native_files=detail.get('capabilities', {}).get('uploadFiles') is True)
                    body['resolvedImages'] = images
                    if files:
                        body['resolvedFiles'] = files
                feed = self.server.notifications.mobile.checkpoint() if parts[3] == 'list' and self.server.notifications else None
                result = manager.list_with_activity(parts[2]) if parts[3] == 'list' else manager.call(parts[2], parts[3], sid, body)
                if feed is not None:
                    result['notificationRead'] = {key: feed[key] for key in ('streamId', 'cursor')}
                return self.output(200, result)
            if path == '/api/harness' and not write:
                return self.output(200, self.server.harness.status() if self.server.harness else {'running': False, 'state': 'stopped', 'url': '/harness/'})
            if path == '/api/mobile/push':
                manager = self.server.notifications
                if manager is None:
                    return self.output(503, {'error': '手机推送暂不可用'})
                if not write:
                    return self.output(200, manager.mobile_push.status())
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('请启用密码保护后注册系统推送')
                origin = self.headers.get('Origin', '')
                if origin not in self.server.origins:
                    raise PermissionError('不允许的推送注册来源')
                return self.output(200, manager.mobile_push.register(self.read_json(), self.server.auth.key(self.token()), origin))
            if path == '/api/mobile/events/read' and write:
                manager = self.server.notifications
                if manager is None:
                    return self.output(503, {'error': '手机通知暂不可用'})
                body = self.read_json()
                if set(body) != {'provider', 'streamId', 'through'}:
                    raise ValueError('通知已读参数无效')
                manager.mobile.acknowledge(body['provider'], body['streamId'], body['through'])
                return self.output(200, manager.mobile_snapshot(manager.mobile.checkpoint()['cursor']))
            if not write and path == '/api/mobile/events':
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                manager = self.server.notifications
                if manager is None:
                    return self.output(503, {'error': '手机通知暂不可用'})
                value = query.get('after', ['0'])[0]
                if not value.isdigit() or len(value) > 16:
                    raise ValueError('通知游标格式不正确')
                wait = query.get('wait', ['0'])[0]
                stream = query.get('streamId', [None])[0]
                revision = query.get('revision', [None])[0]
                if (not wait.isascii() or not wait.isdigit() or len(wait) > 2 or int(wait) > 60
                        or (stream is not None and (not 1 <= len(stream) <= 128 or any(ord(c) < 32 for c in stream)))
                        or (revision is not None and not re.fullmatch('[a-f0-9]{32}', revision))):
                    raise ValueError('通知等待参数无效')
                result = manager.mobile_snapshot(int(value), wait=int(wait), stream=stream, revision=revision)
                if self.authorized():
                    return self.output(200, result)
                return
            if write and path == "/api/logout":
                self.read_json()
                self.server.auth.logout(self.token())
                return self.output(200, {"ok": True}, cookie=self.cookie("", clear=True))
            if path in ('/api/wsl-accounts', '/api/wsl-accounts/environments'):
                manager = getattr(self.server.bridge, 'wsl_accounts', None)
                if manager is None:
                    return self.output(503, {'error': 'WSL 账号管理不可用'})
                if write:
                    if path != '/api/wsl-accounts' or query:
                        raise ValueError('WSL 账号请求参数无效')
                    body = self.read_json()
                    common = {'provider', 'entry', 'action'}
                    fields = {'switch': (common | {'id', 'expectedIdentity'}, set()),
                              'models': (common | {'id'}, {'refresh'}),
                              'recover-restart': (common | {'expectedIdentity'}, set()),
                              'save-api': (common | {'name', 'baseUrl', 'model'}, {'id', 'apiKey', 'authScheme'}),
                              'import-api': (common | {'sourceId', 'sourceIdentity'}, set()),
                              'import-sources': (common, set()), 'import-current': (common, set()),
                              'delete': (common | {'id'}, set()), 'rename': (common | {'id', 'name'}, set())}
                    schema = fields.get(body.get('action')) if isinstance(body, dict) else None
                    if schema is None or not schema[0] <= set(body) or set(body) - schema[0] - schema[1]:
                        raise ValueError('WSL 账号请求字段无效')
                    if body['action'] == 'recover-restart' and body['provider'] != 'codex':
                        raise ValueError('仅 Codex WSL 支持恢复共享服务')
                    if 'refresh' in body and type(body['refresh']) is not bool:
                        raise ValueError('模型刷新参数无效')
                    if body['action'] != 'models' and self.server.auth.config.get('mode') == 'none':
                        raise PermissionError('免密访问不能管理 WSL 账号')
                    return self.output(200, manager.change(body, desktop=False))
                fields = {'provider'} if path.endswith('/environments') else {'provider', 'entry'}
                if set(query) != fields or any(len(values) != 1 or not values[0] for values in query.values()):
                    raise ValueError('WSL 账号查询参数无效')
                provider = query['provider'][0]
                if path.endswith('/environments'):
                    return self.output(200, manager.environments(provider))
                result = manager.read(provider, query['entry'][0])
                if self.server.auth.config.get('mode') == 'none':
                    result = {**result, 'capabilities': {**result.get('capabilities', {}),
                        'save': False, 'import': False, 'capture': False, 'switch': False, 'restore': False,
                        'attemptSwitch': False, 'attemptRestore': False, 'recoverRestart': False}}
                return self.output(200, result)
            if path == '/api/cli-access':
                if query:
                    raise ValueError('CLI 接入请求包含不支持的参数')
                manager = self.server.bridge.cli_access
                if manager is None:
                    return self.output(503, {'error': '本机 CLI 接入不可用'})
                if write:
                    if self.server.auth.config.get('mode') == 'none':
                        raise PermissionError('免密访问不能修改 CLI 接入')
                    return self.output(200, manager.change(self.read_json()))
                return self.output(200, manager.public())
            account_bridge = self.server.bridge
            if path == '/api/account' or path.startswith('/api/accounts') or path == '/api/account/reset':
                entry = query.get('entry', ['desktop'])[0]
                if entry.startswith('wsl:'):
                    if not write and path == '/api/accounts':
                        return self.output(200, self.server.bridge.for_host(entry).access_summary())
                    if not write and path == '/api/account':
                        return self.output(200, {'visible': False, 'reason': 'WSL 接入由原环境管理'})
                    raise ValueError('WSL 接入由原环境管理，不能对它执行桌面账号操作')
                account_bridge = self.server.bridge if entry == 'desktop' else self.server.bridge.account_target(entry)
            if write and path == '/api/accounts/ssh-retry':
                if account_bridge is not self.server.bridge:
                    raise ValueError('独立配置不管理默认 SSH 连接')
                body = self.read_json()
                if not isinstance(body, dict) or set(body) != {'host'}:
                    raise ValueError('SSH 重试请求包含不支持的字段')
                self.server.bridge.retry_ssh(body['host'])
                return self.output(202, account_bridge.accounts.public())
            if not write and path == '/api/accounts':
                entry = query.get('entry', ['desktop'])[0]
                if query.get('resetForecast') in (['1'], ['refresh']) and account_bridge.accounts.reset_dynamic_enabled:
                    account_bridge.accounts.reset_forecast.request(refresh=query['resetForecast'] == ['refresh'])
                result = account_bridge.accounts.public()
                result['canSwitch'] = result.get('canSwitch', True) and self.server.auth.config.get('mode') != 'none'
                result['canManageDesktop'] = account_bridge is self.server.bridge and self.server.auth.config.get('mode') != 'none'
                return self.output(200, result)
            if write and path == '/api/accounts/reset-dynamic':
                body = self.read_json()
                if set(body) != {'enabled'}:
                    raise ValueError('重置动态设置无效')
                account_bridge.accounts.set_reset_dynamic(body['enabled'])
                return self.output(200, account_bridge.accounts.public())
            if write and path == '/api/accounts/reminders':
                body = self.read_json()
                if set(body) != {'preferences'}:
                    raise ValueError('提醒设置无效')
                account_bridge.accounts.monitor.configure(body['preferences'])
                return self.output(200, account_bridge.accounts.public())
            if write and path == '/api/accounts/desktop-update':
                if account_bridge is not self.server.bridge:
                    raise ValueError('独立配置不管理默认 Codex Desktop')
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('免密访问不能操作桌面更新，请在桌面端操作')
                body = self.read_json()
                if body == {'action':'checkDesktopUpdate'}:
                    account_bridge.accounts.updates.check()
                elif body.get('action') == 'requestDesktopUpdate':
                    account_bridge.accounts.updates.request({k:v for k,v in body.items() if k != 'action'})
                else:
                    raise ValueError('桌面更新请求无效')
                return self.output(202, account_bridge.accounts.public())
            if write and path == '/api/accounts/desktop-runtime':
                if account_bridge is not self.server.bridge:
                    raise ValueError('独立配置不管理默认 Codex Desktop')
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('免密访问不能初始化桌面连接，请在桌面端操作')
                body = self.read_json()
                if not isinstance(body, dict) or set(body) != {'requestId', 'confirmed', 'tasksConfirmed'}:
                    raise ValueError('初始化请求包含不支持的字段')
                return self.output(202, account_bridge.accounts.control({'action': 'initializeDesktopRuntime', **body}))
            if write and path == '/api/accounts/account':
                body = self.read_json()
                return self.output(200, account_bridge.accounts.account(body))
            if write and path == '/api/accounts/details':
                body = self.read_json()
                if set(body) - {'id', 'section', 'refresh'} or not {'id', 'section'} <= set(body):
                    raise ValueError('账号详情请求包含不支持的字段')
                return self.output(202, account_bridge.accounts.info.request(body))
            if write and path == '/api/accounts/ignore-submission':
                body = self.read_json()
                if set(body) != {'threadId', 'submissionId', 'host'}:
                    raise ValueError('忽略请求包含不支持的字段')
                return self.output(200, account_bridge.accounts.ignore_submission(body))
            if write and path == '/api/accounts/switch':
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('免密访问不能切换账号，请在桌面端操作')
                body = self.read_json()
                if (set(body) - {'id', 'requestId', 'confirmed', 'tasksConfirmed', 'syncLocalCli'}
                        or not {'id', 'requestId', 'confirmed', 'tasksConfirmed'} <= set(body)
                        or 'syncLocalCli' in body and not isinstance(body['syncLocalCli'], bool)):
                    raise ValueError('账号切换请求包含不支持的字段')
                return self.output(202, account_bridge.accounts.switch(body))
            if not write and path == '/api/account':
                return self.output(200, account_bridge.account.read(refresh=query.get('cached', [''])[0] != '1'))
            if write and path == '/api/account/reset':
                return self.output(200, account_bridge.account.consume(self.read_json()))
            if not write and path == '/api/sessions/diagnostics':
                if set(query) - {'thread'}:
                    raise ValueError('诊断请求包含不支持的字段')
                report = self.server.bridge.discovery_diagnostics(query.get('thread', [None])[0])
                return self.output(200, report, content_disposition='attachment; filename="codex-session-diagnostics.json"')
            if not write and path == "/api/sessions":
                wsl_inventory_only = False
                native_enabled = True
                if self.server.desktop_sessions:
                    native_enabled = bool(self.server.desktop_sessions.enabled('codex'))
                    entry = query.get('entry', ['desktop'])[0]
                    if query.get('scope') == ['host'] and entry == 'desktop':
                        entry = query.get('host', ['local'])[0]
                    if entry == 'local' and 'entry' not in query and not native_enabled:
                        entries = getattr(self.server.bridge, 'entries', None)
                        wsl_inventory_only = bool(entries is not None and entries.hosts())
                    if not wsl_inventory_only:
                        self.server.desktop_sessions.require_enabled(
                            entry if entry.startswith(('wsl:', 'codex:')) else 'codex')
                source = query.get('source', ['all'])[0]
                if source not in ('all', 'desktop', 'cli', 'ide', 'wsl'):
                    raise ValueError('不支持的会话来源')
                feed = self.server.notifications.mobile.checkpoint() if self.server.notifications else None
                entry = query.get('entry', [None])[0]
                if query.get('scope', [None])[0] == 'host':
                    if entry not in (None, 'desktop'):
                        raise ValueError('Codex 入口与主机范围不能同时指定')
                    host = query.get('host', ['local'])[0]
                    host_infos = dict(self.server.bridge.hosts.hosts()) if native_enabled else {}
                    entries = getattr(self.server.bridge, 'entries', None)
                    registered = entries.hosts() if entries is not None and entry != 'desktop' else {}
                    if host.startswith('codex:'):
                        host_infos = {host: registered[host]} if host in registered else {}
                    else:
                        host_infos = codex_list_hosts(host_infos, registered)
                    if host != 'local' and host not in host_infos:
                        raise ValueError('未知 Codex 主机')
                    offset = max(0, int(query.get('offset', [0])[0]))
                    host_info = host_infos.get(host, {})
                    options = dict(query=query.get('q', [''])[0][:200], limit=offset + 101,
                                   archived=query.get('archived', ['false'])[0] == 'true',
                                   source=source, include_sources=True,
                                   **({'pin_order': True} if query.get('order') == ['recent'] else {}))
                    if wsl_inventory_only:
                        rows, available, errors = [], [], []
                    elif host.startswith('wsl:'):
                        target = self.server.bridge.for_host(host)
                        options['source'] = 'wsl' if source in ('all', 'wsl') else 'desktop'
                        try:
                            rows, available = target.list(**options)
                            errors = []
                        except (RemoteUnavailable, StoreUnavailable) as exc:
                            rows, available = [], []
                            errors = [{'host': host, 'label': host_info['label'], 'error': str(exc)}]
                    else:
                        target = self.server.bridge.for_host(host)
                        options.update(local_only=True,
                                       host_label=host_info.get('displayName') or host_info.get('alias') or host_info.get('label'))
                        rows, available = target.list(**options)
                        errors = target.host_errors
                    return self.output(200, {'sessions': rows, 'availableSources': available,
                                             'unavailableHosts': errors,
                                             'remoteHosts': list(host_infos) if host == 'local' else [],
                                             'complete': not errors,
                                             'notificationRead': {key: feed[key] for key in ('streamId', 'cursor')} if feed else None})
                entry_options = {'entry': entry} if entry else {}
                rows, available = self.server.bridge.list(
                    query=query.get("q", [""])[0][:200],
                    offset=max(0, int(query.get("offset", [0])[0])),
                    archived=query.get("archived", ["false"])[0] == "true",
                    source=source, include_sources=True, **entry_options)
                return self.output(200, {
                    "sessions": rows, "availableSources": available,
                    "unavailableHosts": self.server.bridge.host_errors,
                    'notificationRead': {key: feed[key] for key in ('streamId', 'cursor')} if feed else None})
            if path == '/api/codex/entries':
                entries = getattr(self.server.bridge, 'entries', None)
                if entries is None:
                    return self.output(200, {'entries': []})
                if not write:
                    return self.output(200, entries.public())
                body = self.read_json()
                if body == {'action': 'scan'}:
                    return self.output(200, entries.scan())
                if set(body) == {'action', 'id'} and body['action'] == 'connect':
                    if self.server.desktop_sessions:
                        self.server.desktop_sessions.require_enabled(body['id'])
                    return self.output(200, entries.get(body['id']).connect())
                if set(body) != {'action', 'id', 'enabled'} or body['action'] != 'configure':
                    raise ValueError('Codex 入口请求无效')
                return self.output(200, entries.configure(body['id'], body['enabled']))
            if path in ('/api/projects', '/api/projects/browse', '/api/projects/trust', '/api/projects/models'):
                entry = query.get('entry', ['desktop'])[0]
                if self.server.desktop_sessions:
                    self.server.desktop_sessions.require_enabled(entry if entry.startswith(('wsl:', 'codex:')) else 'codex')
                if entry != 'desktop':
                    target = self.server.bridge.entries.get(entry)
                else:
                    target = self.server.bridge
                if path.endswith('/models'):
                    if write: return self.output(405, {'error': '模型目录只支持读取'})
                    options = {key: query[key][0] for key in ('kind', 'host', 'path') if key in query}
                    return self.output(200, target.creation_models(query.get('project', [None])[0],
                        refresh=query.get('refresh') == ['true'], **options))
                if path.endswith('/trust'):
                    if not write: return self.output(405, {'error': '请通过项目确认操作信任'})
                    body = self.read_json()
                    if (entry != 'desktop' or not isinstance(body, dict)
                            or not {'key', 'confirmed'} <= set(body)
                            or set(body) - {'key', 'confirmed', 'confirmation'}):
                        raise ValueError('目录信任请求无效')
                    return self.output(200, target.project_trust(**body))
                if path.endswith('/browse'):
                    if not write: return self.output(405, {'error': '请通过目录选择器浏览'})
                    body = self.read_json()
                    if not isinstance(body, dict) or set(body) != {'host', 'path', 'hidden', 'name', 'identity'}:
                        raise ValueError('目录浏览请求无效')
                    return self.output(200, target.project_directories(**body))
                if write:
                    body = self.read_json()
                    if (not isinstance(body, dict) or not {'path', 'createDirectory', 'host'} <= set(body)
                            or set(body) - {'path', 'createDirectory', 'host', 'kind'}):
                        raise ValueError('项目目录请求无效')
                    options = {'kind': body['kind']} if 'kind' in body else {}
                    return self.output(200, {'project': target.register_project(body['path'], body['createDirectory'], body['host'], **options)})
                project_host = query.get('host', [None])[0]
                if project_host is not None and entry != 'desktop':
                    raise ValueError('项目环境请求无效')
                return self.output(200, {'projects': target.projects(host=project_host) if project_host is not None else target.projects(),
                    'environments': target.project_environments() if hasattr(target, 'project_environments') else [],
                    'createModels': hasattr(target, 'creation_models')})
            if write and path == '/api/activity':
                groups = self.read_json().get('hosts')
                if not isinstance(groups, dict) or len(groups) > 20:
                    raise ValueError('会话状态请求无效')
                rows = []
                for host, identifiers in groups.items():
                    if self.server.desktop_sessions:
                        self.server.desktop_sessions.require_enabled(host if host.startswith(('wsl:', 'codex:')) else 'codex')
                    rows.extend(self.server.bridge.for_host(host).activity(identifiers))
                return self.output(200, {'sessions': rows})
            if write and path == '/api/sessions':
                body = self.read_json()
                from bridge.features.sessions.creation_models import requested
                selected = requested(body)
                model_options = {'model_options': selected} if selected else {}
                entry = query.get('entry', ['desktop'])[0]
                if self.server.desktop_sessions:
                    self.server.desktop_sessions.require_enabled(entry if entry.startswith(('wsl:', 'codex:')) else 'codex')
                if entry != 'desktop':
                    return self.output(200, self.server.bridge.entries.get(entry).create_chat(
                        body.get('project'), body.get('title'), body.get('id'), **model_options))
                fields = (body.get('project'), body.get('title'), body.get('id'))
                result = (self.server.bridge.create_chat(*fields, kind=body['kind'], **model_options) if 'kind' in body
                          else self.server.bridge.create_chat(*fields, **model_options))
                return self.output(200, result)
            if path == '/api/notifications/defaults':
                if self.server.notifications is None:
                    raise ValueError('当前网关未启用通知服务')
                return self.output(200, self.server.notifications.defaults(self.read_json() if write else None))
            if path == '/api/notifications/channels/common':
                manager = self.server.notifications
                if manager is None:
                    raise ValueError('当前网关未启用通知服务')
                fields = ('includeTitle', 'clickBase', 'securityEnabled', 'addressEnabled', 'addressName')
                with manager.lock:
                    if write:
                        body = self.read_json()
                        if not isinstance(body, dict) or set(body) - set(fields):
                            raise ValueError('通知配置格式不正确')
                        config = save_notification_settings(manager.data_dir, body)
                    else:
                        config = notification_settings(manager.data_dir)
                    return self.output(200, {key: config[key] for key in fields})
            if path == '/api/notifications/channels/webhook/endpoints':
                if not write:
                    return self.output(405, {'error': '需要 POST 请求'})
                manager = self.server.notifications
                if manager is None:
                    raise ValueError('当前网关未启用通知服务')
                with manager.lock:
                    config, selected = save_webhook_endpoint(manager.data_dir, self.read_json())
                    return self.output(200, {**visible_webhook(config), 'selectedWebhookId': selected})
            if path == '/api/notifications/channels/webhook/url':
                if not write:
                    return self.output(405, {'error': '需要 POST 请求'})
                manager = self.server.notifications
                if manager is None:
                    raise ValueError('当前网关未启用通知服务')
                body = self.read_json()
                if not isinstance(body, dict) or set(body) - {'webhookId'}:
                    raise ValueError('Webhook 地址读取请求格式不正确')
                with manager.lock:
                    return self.output(200, {'webhookUrl': webhook_config(notification_settings(manager.data_dir), body.get('webhookId', 'legacy')).get('webhookUrl', '')})
            channel_parts = path.removeprefix('/api/notifications/channels/').split('/') if path.startswith('/api/notifications/channels/') else []
            channel = channel_parts[0] if channel_parts and channel_parts[0] in ('ntfy', 'bark', 'pushplus', 'webhook') and channel_parts[1:] in ([], ['test']) else None
            if channel or path in ('/api/notifications/pushplus', '/api/notifications/pushplus/test'):
                manager = self.server.notifications
                if manager is None:
                    raise ValueError('当前网关未启用通知服务')
                selected = channel or 'pushplus'
                fields = {'ntfy': ('enabled', 'server', 'topic', 'token', 'clearToken'),
                          'bark': ('barkEnabled', 'barkServer', 'barkKey', 'clearBarkKey'),
                          'pushplus': ('pushplusEnabled', 'pushplusToken', 'clearPushplusToken'),
                          'webhook': ('webhookEnabled', 'webhookUrl', 'webhookHeaders', 'webhookBodyTemplate', 'webhookProvider', 'webhookSignEnabled', 'webhookSignSecret', 'clearWebhookUrl', 'clearWebhookHeaders', 'clearWebhookSignSecret')}[selected]
                secret, marker = {'ntfy': ('token', 'hasToken'), 'bark': ('barkKey', 'hasBarkKey'),
                                  'pushplus': ('pushplusToken', 'hasPushplusToken'), 'webhook': ('webhookUrl', 'hasWebhookUrl')}[selected]
                with manager.lock:
                    config = notification_settings(manager.data_dir)
                    if path.endswith('/test'):
                        if not write:
                            return self.output(405, {'error': '需要 POST 请求'})
                        test_body = self.read_json()
                        try:
                            sender = {'ntfy': publish, 'bark': publish_bark, 'pushplus': publish_pushplus, 'webhook': publish_webhook}[selected]
                            name = {'ntfy': 'ntfy', 'bark': 'Bark', 'pushplus': 'PushPlus', 'webhook': 'Webhook'}[selected]
                            if selected == 'webhook':
                                config = webhook_config(config, test_body.get('webhookId', 'legacy') if isinstance(test_body, dict) else 'legacy')
                                sender(config, 'Codex 手机通知测试', f'收到这条消息表示 {name} 通道已连通。', event='test')
                            else:
                                sender(config, 'Codex 手机通知测试', f'收到这条消息表示 {name} 通道已连通。')
                        except WebhookDeliveryError as error:
                            raise ValueError(str(error)) from None
                        except Exception:
                            message = 'PushPlus 测试失败，请检查 Token 和网络' if not channel else f'{name} 测试失败，请检查服务地址、认证和网络'
                            raise ValueError(message) from None
                        provider = resolved_webhook_provider(config) if selected == 'webhook' else 'generic'
                        accepted = {'feishu': '飞书', 'dingtalk': '钉钉', 'discord': 'Discord', 'telegram': 'Telegram'}.get(provider, name)
                        return self.output(200, {'ok': True, 'message': f'{accepted} 已接受测试通知'})
                    if write:
                        body = self.read_json()
                        allowed = set(fields)
                        if not isinstance(body, dict) or set(body) - allowed or any(
                                key in body and not isinstance(body[key], bool) for key in allowed if key.startswith('clear')):
                            raise ValueError('通知配置格式不正确' if channel else 'PushPlus 配置格式不正确')
                        config = save_notification_settings(manager.data_dir, body)
                    visible = visible_webhook(config) if selected == 'webhook' else {key: config[key] for key in fields if key != secret and not key.startswith('clear')}
                    if selected != 'webhook':
                        visible[marker] = bool(config[secret])
                    return self.output(200, visible)
            if desktop_workspace:
                if self.server.desktop_sessions is None:
                    return self.output(503, {'error': '桌面会话接入不可用'})
                provider, operation = desktop_workspace.groups()
                sid = query.get('sessionId', [''])[0]
                manager, _, _ = self.desktop_context(provider, query)
                bridge = manager.workspace_bridge(provider, sid)
                path = '/api/sessions/' + bridge.identifier + '/' + operation
            else:
                bridge = self.server.bridge.for_host(query.get("host", ["local"])[0])
                if path.startswith('/api/sessions') and self.server.desktop_sessions:
                    host = query.get('host', ['local'])[0]
                    self.server.desktop_sessions.require_enabled(host if host.startswith(('wsl:', 'codex:')) else 'codex')
            side = query.get('side', [None])[0]
            if side is not None and self.server.auth.config.get('mode') == 'none':
                raise PermissionError('侧边聊天需要启用网关密码保护')
            side_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/side-chat", path)
            if side_match:
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('侧边聊天需要启用网关密码保护')
                owner = self.server.auth.key(self.token())
                body = self.read_json() if write else {}
                action = body.get('action') if write else 'list' if query.get('view') == ['native'] else 'read'
                fields = {'create': {'action', 'creationId'}, 'send': {'action', 'id', 'text', 'submissionId', 'attachments', 'skills', 'mode', 'workMode'},
                          'catalog': {'action', 'id'}, 'skills': {'action', 'id', 'query', 'offset', 'refresh', 'selected'},
                          'linux-permission-options': {'action', 'id'},
                          'permissions': {'action', 'id', 'preset', 'confirmed'}, 'cancel-queued': {'action', 'id', 'submissionId'}, 'settings': {'action', 'id', 'model', 'effort'},
                          'close': {'action', 'id'}, 'stop': {'action', 'id'},
                          'respond': {'action', 'id', 'requestId', 'response'}, 'read': set(), 'list': set()}
                if action not in fields or set(body) - fields[action] or (write and action in ('read', 'list')):
                    raise ValueError('无效侧边聊天操作')
                return self.output(200, bridge.side_chat(side_match[1], owner, action, body))
            agent_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/subagents", path)
            if agent_match:
                if write: return self.output(405, {'error': '子智能体面板仅查看现有任务'})
                return self.output(200, bridge.subagents(agent_match[1], query.get('id', [None])[0]))
            terminal_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/terminal", path)
            if terminal_match:
                if self.server.auth.config.get('mode') == 'none':
                    raise PermissionError('终端需要启用网关密码保护')
                owner = self.server.auth.key(self.token())
                if write:
                    body = self.read_json()
                    if set(body) - {'action', 'id', 'command', 'inputId', 'data', 'cols', 'rows', 'text', 'selectionStart'} or body.get('action') not in ('start', 'stop', 'open', 'input', 'resize', 'close', 'complete'):
                        raise ValueError('无效终端操作')
                    return self.output(200, bridge.terminal(terminal_match[1], owner, body['action'], body))
                identifier = query.get('id', [''])[0]
                return self.output(200, bridge.terminal(terminal_match[1], owner, ('poll' if query.get('mode') == ['pty'] else 'read') if identifier else 'info',
                                   {'id': identifier, 'after': int(query.get('after', ['0'])[0]), 'waitMs': int(query.get('waitMs', ['0'])[0])}))
            workspace_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/workspace(?:/(info|preview|download|upload|git-status|git-diff|git-history|git-commit|git-history-diff|git-branches|git-action))?", path)
            if workspace_match:
                thread_id, operation = workspace_match.groups()
                relative = query.get('path', [''])[0]
                if write:
                    if operation == 'git-action':
                        body = self.read_json()
                        if set(body) - {'action', 'version', 'path', 'branch', 'message'} or not {'action', 'version'} <= set(body):
                            raise ValueError('Git 操作请求无效')
                        return self.output(200, bridge.workspace(thread_id, operation, body))
                    if operation != 'upload': return self.output(405, {'error': '不支持此文件操作'})
                    sizes = self.headers.get_all('Content-Length', [])
                    if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not sizes[0].isdigit() or not 0 <= int(sizes[0]) <= MAX_TRANSFER:
                        raise ValueError('每个文件不能超过 20 MB')
                    content = self.rfile.read(int(sizes[0]))
                    if len(content) != int(sizes[0]): raise ValueError('上传中断，请重试')
                    return self.output(200, bridge.workspace(thread_id, 'upload', {'path': relative, 'encoded': base64.b64encode(content).decode()}))
                if operation == 'upload': return self.output(405, {'error': '请使用上传操作'})
                if operation == 'git-action': return self.output(405, {'error': 'Git 操作需要 POST 请求'})
                if operation == 'git-history':
                    return self.output(200, bridge.workspace(thread_id, operation, {'limit': int(query.get('limit', ['40'])[0]), 'ref': query.get('ref', ['all'])[0]}))
                if operation == 'git-commit':
                    return self.output(200, bridge.workspace(thread_id, operation, {'revision': query.get('revision', [''])[0]}))
                if operation == 'git-history-diff':
                    return self.output(200, bridge.workspace(thread_id, operation, {'revision': query.get('revision', [''])[0], 'path': relative}))
                if operation == 'git-branches': return self.output(200, bridge.workspace(thread_id, operation, {}))
                if operation == 'git-status': return self.output(200, bridge.workspace(thread_id, operation, {}))
                if operation == 'git-diff':
                    return self.output(200, bridge.workspace(thread_id, operation, {'path': relative, 'section': query.get('section', ['unstaged'])[0]}))
                if operation is None:
                    return self.output(200, bridge.workspace(thread_id, 'list', {'path': relative, 'hidden': query.get('hidden') == ['true'],
                        'search': query.get('search', [''])[0], 'offset': int(query.get('offset', ['0'])[0])}))
                if operation == 'download':
                    policy = transfer_settings(self.server.transfer_directory)
                    if query.get('info') == ['1']:
                        return self.output(200, {**bridge.workspace(thread_id, 'info', {'path': relative}), **policy, 'locatable': True})
                    preview = query.get('preview') == ['1']
                    if 'preview' in query and (not preview or 'explicit' in query or 'archive' in query):
                        raise ValueError('无效预览下载参数')
                    explicit = query.get('explicit') == ['1']
                    params = {'path': relative, 'limit': min(MAX_TRANSFER, policy['clickDownloadMiB'] * 1024 * 1024) if preview else None if explicit else policy['clickDownloadMiB'] * 1024 * 1024, **self.download_request()}
                    action = 'archive-stream' if explicit and query.get('archive') == ['1'] else 'download-stream'
                    with bridge.workspace(thread_id, action, params) as (metadata, stream):
                        mime = media_type(metadata['name']) if preview else None
                        return self.download_response(metadata, stream, mime or 'application/octet-stream',
                                                      'inline' if mime else 'attachment')
                result = bridge.workspace(thread_id, operation, {'path': relative})
                return self.output(200, result)
            file_match = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/files/([a-f0-9]{64})", path)
            if not write and file_match:
                return self.download(bridge, *file_match.groups(), info=query.get('info') == ['1'], preview=query.get('preview') == ['1'])
            preview_match = UPLOAD_PREVIEW_ROUTE.fullmatch(path)
            if not write and preview_match:
                return self.upload_preview(bridge, *preview_match.groups(), variant=query.get('variant', ['thumb'])[0], side=side, download=query.get('download') == ['1'])
            desktop_image_match = DESKTOP_IMAGE_ROUTE.fullmatch(path)
            if not write and desktop_image_match:
                return self.desktop_image_preview(bridge, *desktop_image_match.groups(), download=query.get('download') == ['1'])
            thumb_match = UPLOAD_THUMB_ROUTE.fullmatch(path)
            if write and thumb_match:
                sizes = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not sizes[0].isdigit() or not 0 < int(sizes[0]) <= 1024 * 1024:
                    raise ValueError('缩略图需为 1 字节至 1 MB')
                data = self.rfile.read(int(sizes[0]))
                if len(data) != int(sizes[0]):
                    raise ValueError('缩略图上传中断，请重试')
                try:
                    width, height = int(query.get('width', ['0'])[0]), int(query.get('height', ['0'])[0])
                except ValueError:
                    raise ValueError('缩略图尺寸无效') from None
                return self.output(200, bridge.side_attachment(thumb_match[1], side, 'set_thumb', thumb_match[2], data, width, height) if side is not None else bridge.upload_thumb(thumb_match[1], thumb_match[2], data, width, height))
            goal_match = GOAL_CANCEL_ROUTE.fullmatch(path)
            if write and goal_match:
                body = self.read_json()
                return self.output(200, bridge.cancel_goal(goal_match[1], body.get("id", ""), expected=body.get("expected")))
            goal_edit_match = GOAL_EDIT_ROUTE.fullmatch(path)
            if write and goal_edit_match:
                body = self.read_json()
                return self.output(200, bridge.edit_goal(goal_edit_match[1], body.get('objective'),
                                                        body.get('id', ''), expected=body.get('expected')))
            goal_status_match = GOAL_STATUS_ROUTE.fullmatch(path)
            if write and goal_status_match:
                body = self.read_json()
                return self.output(200, bridge.set_goal_status(goal_status_match[1], body.get("status"),
                                                              body.get("id", ""), body.get("uiLocale"), expected=body.get("expected")))
            match = THREAD_ROUTE.fullmatch(path)
            if not match:
                return self.output(404, {"error": "页面不存在"})
            thread_id, action = match.groups()
            if action == 'cli-network':
                if not hasattr(bridge, 'cli_network'):
                    raise ValueError('只支持本机 Codex CLI')
                return self.output(200, bridge.cli_network(thread_id, self.read_json() if write else None))
            if action == 'delete':
                if not hasattr(bridge, 'deletion'):
                    raise ValueError('此接入暂不支持永久删除，请在原客户端操作')
                if not write:
                    return self.output(200, bridge.deletion(thread_id))
                body = self.read_json()
                if (set(body) - {'retry'} != {'id', 'scope', 'confirmed'} or body['confirmed'] is not True
                        or not isinstance(body['id'], str)):
                    raise ValueError('请确认永久删除及受影响的聊天范围')
                return self.output(200, bridge.deletion(thread_id, body['id'], body['scope'],
                    **({'retry': body['retry']} if 'retry' in body else {})))
            if action == 'organization':
                if not hasattr(bridge, 'organization'):
                    raise ValueError('此客户端暂未提供原生项目与分区')
                return self.output(200, bridge.organization(thread_id, self.read_json() if write else None))
            if write and action == 'uploads':
                sizes = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not sizes[0].isdigit() or not 0 < int(sizes[0]) <= MAX_FILE:
                    raise ValueError('每个附件需为 1 字节至 20 MB')
                size = int(sizes[0])
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError('附件上传中断，请重试')
                return self.output(200, bridge.side_attachment(thread_id, side, 'put', query.get('id', [''])[0], query.get('name', [''])[0], data) if side is not None else bridge.upload(thread_id, query.get('id', [''])[0], query.get('name', [''])[0], data))
            if action == 'notifications':
                if getattr(bridge, 'capabilities', {}).get('notifications') is False:
                    if write:
                        raise ValueError('此 WSL 入口暂不提供后台通知')
                    return self.output(200, {'available': False, 'watching': False, 'notifyOnCompletion': False})
                if self.server.notifications is None:
                    return self.output(200, {'available': False, 'watching': False, 'notifyOnCompletion': False})
                body = self.read_json() if write else {}
                return self.output(200, self.server.notifications.policy(thread_id, bridge.host, body if write else None))
            if action == 'message-management':
                if not hasattr(bridge, 'message_management'):
                    return self.output(400, {'error': '此接入暂不支持消息管理'})
                return self.output(200, bridge.manage_messages(thread_id, self.read_json()) if write else bridge.message_management(thread_id, query.get('id', [])))
            if not write:
                if action == 'permissions':
                    return self.output(200, bridge.linux_permission_options(thread_id))
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
                    kind = query.get("kind", [None])[0]
                    try:
                        offset = max(0, int(query.get("offset", ["0"])[0]))
                        limit = min(500, max(1, int(query.get("limit", ["200"])[0])))
                    except ValueError:
                        raise ValueError("Skill 分页参数无效") from None
                    requested_ids = [value for value in query.get("id", []) if value]
                    if len(requested_ids) > 8:
                        raise ValueError("最多关联 8 个已选 Skill")
                    ids = list(dict.fromkeys(requested_ids))
                    return self.output(200, bridge.catalog(
                        thread_id, refresh=query.get("refresh") == ["true"], kind=kind,
                        query=query.get("q", [""])[0][:200], offset=offset, limit=limit, ids=ids))
                if action == "poll":
                    return self.poll(bridge, thread_id, int(query.get("after", ["-1"])[0]))
                if action == "events":
                    return self.stream(bridge, thread_id)
                return self.output(404, {"error": "接口不存在"})
            body = self.read_json()
            if action == "message-action":
                result = bridge.message_action(thread_id, body)
            elif action == "send":
                result = bridge.send(thread_id, body.get("text"), body.get("id", ""), body.get("mode", "send"),
                                     body.get("skills", []), work_mode=body.get("workMode"),
                                     attachments=body.get('attachments'), references=body.get('references'), ui_locale=body.get("uiLocale"))
            elif action == "archive":
                if set(body) not in ({'archived', 'id'}, {'archived', 'id', 'disconnect'}):
                    raise ValueError('聊天归档设置无效')
                result = bridge.archive(thread_id, body['archived'], body['id'], **({'disconnect': body['disconnect']} if 'disconnect' in body else {}))
            elif action == "pin":
                if set(body) not in ({'pinned'}, {'pinned', 'clientId', 'revision'}):
                    raise ValueError('聊天置顶设置无效')
                result = bridge.pin(thread_id, body['pinned'], **({'client_id': body['clientId'], 'revision': body['revision']} if 'clientId' in body else {}))
            elif action == "rename":
                result = bridge.rename(thread_id, body.get("title"))
            elif action == "permissions":
                if set(body) - {'preset', 'confirmed', 'expectedRuntime'}:
                    raise ValueError('权限设置无效')
                result = bridge.permissions(thread_id, body.get('preset'), body.get('confirmed', False),
                                            expected_runtime=body.get('expectedRuntime'))
            elif action == "settings":
                if 'fastMode' in body and not isinstance(body['fastMode'], bool):
                    raise ValueError('Fast 模式开关无效')
                result = bridge.settings(thread_id, body.get("model"), body.get("effort"), **({'fast_mode': body['fastMode']} if 'fastMode' in body else {}))
            elif action == "compact":
                if set(body) != {'id'} or not isinstance(body.get('id'), str):
                    raise ValueError('上下文压缩请求无效')
                result = bridge.compact(thread_id, body['id'])
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
                if 'rebuild' in body and (body.get('rebuild') is not True or body.get('activate') is not True or set(body) != {'activate', 'rebuild'}):
                    raise ValueError('重建连接请求无效')
                if body.get('activate') is True:
                    if hasattr(bridge, 'reconnect_session'):
                        session = bridge.reconnect_session(thread_id, rebuild=bool(body.get('rebuild')))
                    else:
                        # WSL entries own their CLI runtime, not a Desktop IPC follower.
                        session = bridge.rebuild_connection(thread_id) if body.get('rebuild') else bridge.activate(thread_id)
                    result = {'ok': True, 'connected': session.connected}
                elif body.get('progressive'):
                    bridge.session(thread_id, background=True, force=True)
                    result = {'ok': True}
                else:
                    result = bridge.view(thread_id, background=True, force=True)
            elif action == "queue":
                result = bridge.cancel_queued(thread_id, body.get("id", ""))
            else:
                return self.output(404, {"error": "接口不存在"})
            self.output(200, result)
        except LoginRejected as exc:
            self.close_connection = True
            self.output(403, {"error": str(exc), "loginStatus": exc.status})
        except PermissionError as exc:
            self.close_connection = True
            self.output(403, {"error": str(exc)})
        except TerminalSessionMissing as exc:
            self.close_connection = True
            self.output(404, {"error": str(exc.args[0]), "code": "terminal_missing"})
        except KeyError:
            self.close_connection = True
            self.output(404, {"error": "找不到这个会话"})
        except SubmissionRejected as exc:
            self.close_connection = True
            self.output(400, {"error": str(exc), "code": "send_not_submitted"})
        except ValueError as exc:
            self.close_connection = True
            self.output(400, {"error": str(exc)})
        except ArchiveRejected as exc:
            self.close_connection = True
            self.output(409, {'error': str(exc), 'code': 'archive_rejected', 'reason': exc.reason})
        except (IPCError, GoalError, CatalogError, RemoteUnavailable, CreationError, StoreUnavailable, AccountError) as exc:
            self.close_connection = True
            self.output(409, {"error": str(exc), "code": "desktop_unavailable"})
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception:
            LOG.exception("Gateway operation failed")
            self.close_connection = True
            self.output(500, {"error": "网关操作失败，请查看本机日志"})

    def download(self, bridge, thread_id, artifact_id, info=False, preview=False):
        artifact = bridge.artifact(thread_id, artifact_id)
        path = artifact['path']
        policy = transfer_settings(self.server.transfer_directory)
        workspace = Workspace(path.anchor)
        relative = path.relative_to(path.anchor).as_posix()
        if info:
            result = workspace.info(relative)
            cwd = bridge.store.get(thread_id).get('cwd')
            result['path'] = workspace_relative_path(path, cwd)
            result['locatable'] = result['path'] is not None
            return self.output(200, {**result, **policy})
        # Walk the authorized absolute path without following replaced parent
        # symlinks. Inline images retain their independent preview size bound.
        with Workspace(path.anchor).download(path.relative_to(path.anchor).as_posix(),
                limit=50 * 1024 * 1024 if artifact['image'] else policy['clickDownloadMiB'] * 1024 * 1024, **self.download_request()) as (metadata, stream):
            mime = mimetypes.guess_type(artifact['name'])[0] if artifact['image'] else media_type(artifact['name']) if preview else None
            return self.download_response(metadata, stream, mime or 'application/octet-stream',
                                          'inline' if artifact['image'] or mime else 'attachment')

    def download_request(self):
        # Duplicate Range fields are an unsupported multi-range request.
        return {'range_header': ','.join(self.headers.get_all('Range', [])),
                'if_range': ','.join(self.headers.get_all('If-Range', []))}

    def download_response(self, metadata, stream, mime='application/octet-stream', disposition='attachment'):
        self.send_response(metadata['status'])
        self.headers_common()
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(metadata['length']))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', metadata['etag'])
        if metadata.get('contentRange'):
            self.send_header('Content-Range', metadata['contentRange'])
        self.send_header('Content-Disposition', disposition + "; filename*=UTF-8''" + quote(metadata['name'], safe=''))
        token = self.token()
        if token and self.server.auth.get(token): self.send_header('Set-Cookie', self.cookie(token))
        self.end_headers()
        try:
            remaining = metadata['length']
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk: raise OSError('Download snapshot ended early')
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except OSError:
            # Never append a JSON error to an already-started binary response.
            self.close_connection = True

    def upload_preview(self, bridge, thread_id, upload_id, variant='thumb', side=None, download=False):
        if download:
            variant = 'original'
        artifact, served_variant = bridge.side_attachment(thread_id, side, 'preview', upload_id, variant) if side is not None else bridge.upload_preview(thread_id, upload_id, variant)
        return self.attachment_preview(artifact, served_variant, private=side is not None, download=download)

    def attachment_preview(self, artifact, served_variant, private=False, download=False):
        data = artifact['previewBytes'] if 'previewBytes' in artifact else Path(artifact["previewPath"]).read_bytes()
        etag = '"' + artifact["previewSha256"] + '"'
        cache_control = 'private, no-store' if private or download or served_variant == 'fallback-original' else 'private, max-age=31536000, immutable'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.headers_common(cache_control)
            self.send_header("ETag", etag)
            self.send_header("X-Preview-Variant", served_variant)
            self.end_headers()
            return
        self.send_response(200)
        self.headers_common(cache_control)
        self.send_header("Content-Type", artifact["previewMime"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("X-Preview-Variant", served_variant)
        self.send_header("Content-Disposition", ("attachment" if download else "inline") + "; filename*=UTF-8''" + quote(artifact["name"], safe=""))
        self.end_headers()
        self.wfile.write(data)

    def desktop_image_preview(self, bridge, thread_id, image_id, download=False):
        artifact, served_variant = bridge.desktop_image_preview(thread_id, image_id)
        if download:
            return self.attachment_preview(artifact, served_variant, download=True)
        data = artifact['previewBytes'] if 'previewBytes' in artifact else Path(artifact["previewPath"]).read_bytes()
        etag = '"' + artifact["previewSha256"] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.headers_common("private, max-age=31536000, immutable")
            self.send_header("ETag", etag)
            self.send_header("X-Preview-Variant", served_variant)
            self.end_headers()
            return
        self.send_response(200)
        self.headers_common("private, max-age=31536000, immutable")
        self.send_header("Content-Type", artifact["previewMime"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("X-Preview-Variant", served_variant)
        self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + quote(artifact["name"], safe=""))
        self.end_headers()
        self.wfile.write(data)

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
                if not self.login_session():
                    self.wfile.write(b'event: logout\ndata: {}\n\n')
                    self.wfile.flush()
                    break
                with session.condition:
                    session.condition.wait_for(lambda: session.sequence != sequence or bridge.closed.is_set(), timeout=12)
                    updated = session.sequence != sequence
                    sequence = session.sequence
                if not self.login_session():
                    self.wfile.write(b'event: logout\ndata: {}\n\n')
                    self.wfile.flush()
                    break
                if updated:
                    view = bridge.view(thread_id, attach=False, background=True)
                    payload = json.dumps(view, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"id: {sequence}\nevent: state\ndata: {payload}\n\n".encode())
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                bridge.closed.wait(0.2)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, socket.timeout):
            pass
        finally:
            with session.condition:
                session.viewers -= 1
                session.touched = time.monotonic()
