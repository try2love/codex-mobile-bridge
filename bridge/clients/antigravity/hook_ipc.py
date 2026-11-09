"""Local Unix-socket / loopback IPC server for Antigravity lifecycle hooks."""
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import threading
import time
import uuid

from .model import format_tool_call

HEARTBEAT_TTL = 90.0
APPROVAL_TIMEOUT = 285.0
MAX_FRAME_BYTES = 4 * 1024 * 1024


def _read_frame(conn):
    header = b''
    while len(header) < 4:
        chunk = conn.recv(4 - len(header))
        if not chunk:
            return None
        header += chunk
    (length,) = struct.unpack('>I', header)
    if length <= 0 or length > MAX_FRAME_BYTES:
        return None
    data = bytearray()
    while len(data) < length:
        chunk = conn.recv(min(65536, length - len(data)))
        if not chunk:
            return None
        data.extend(chunk)
    return json.loads(data.decode('utf-8', errors='replace'))


def _write_frame(conn, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    conn.sendall(struct.pack('>I', len(raw)) + raw)


def _verify_peer_uid(conn):
    """On Linux AF_UNIX sockets, verify the connecting process belongs to the same UID."""
    if not hasattr(socket, 'SO_PEERCRED') or not hasattr(os, 'geteuid'):
        return True
    try:
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
        _, uid, _ = struct.unpack('3i', creds)
        return uid == os.geteuid()
    except OSError:
        return True


class _PendingApproval:
    def __init__(self, request_id, conversation_id, tool_name, tool_args, prompt):
        self.id = request_id
        self.conversation_id = conversation_id
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.prompt = prompt
        self.event = threading.Event()
        self.decision = None
        self.reason = None
        self.created_at = time.time()


class HookIPCServer:
    """Coordinate PreToolUse approvals, follow-up message queues, and heartbeats."""

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass

        self.endpoint_file = self.state_dir / 'hook-endpoint.json'
        self.queues_file = self.state_dir / 'queues.json'
        self.permissions_file = self.state_dir / 'permissions.json'
        self.sock_path = self.state_dir / 'hook.sock'
        self.token = secrets.token_hex(32)

        self._lock = threading.Lock()
        self._heartbeats = {}
        self._surface_hints = {}
        self._pending = {}
        self._stop_requests = set()
        self._queues = self._load_json(self.queues_file, {})
        self._permissions = self._load_json(self.permissions_file, {})

        self._server_sock = None
        self._closed = False
        self._thread = None
        self._start_server()

    @staticmethod
    def _load_json(path, default):
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        return dict(default)

    def _save_json(self, path, data):
        tmp = path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError:
            pass

    def _start_server(self):
        use_unix = hasattr(socket, 'AF_UNIX') and os.name != 'nt' and len(str(self.sock_path)) < 100
        if use_unix:
            try:
                if self.sock_path.exists() or self.sock_path.is_symlink():
                    self.sock_path.unlink()
            except OSError:
                pass
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(self.sock_path))
            try:
                os.chmod(self.sock_path, 0o600)
            except OSError:
                pass
            sock.listen(16)
            endpoint = {'transport': 'unix', 'address': str(self.sock_path), 'token': self.token}
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', 0))
            sock.listen(16)
            host, port = sock.getsockname()[:2]
            endpoint = {'transport': 'tcp', 'host': host, 'port': port, 'token': self.token}

        self._server_sock = sock
        self._save_json(self.endpoint_file, endpoint)
        self._thread = threading.Thread(target=self._accept_loop, name='antigravity-hook-ipc', daemon=True)
        self._thread.start()

    def close(self):
        with self._lock:
            self._closed = True
            pending_items = list(self._pending.values())
            self._pending.clear()
        for item in pending_items:
            item.decision = 'ask'
            item.event.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        for path in (self.sock_path, self.endpoint_file):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    def _accept_loop(self):
        while not self._closed and self._server_sock is not None:
            try:
                conn, _ = self._server_sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _handle_client(self, conn):
        with conn:
            conn.settimeout(APPROVAL_TIMEOUT + 10.0)
            if not _verify_peer_uid(conn):
                return
            try:
                req = _read_frame(conn)
            except (OSError, ValueError):
                return
            if not isinstance(req, dict) or req.get('token') != self.token:
                return
            event = str(req.get('event') or '')
            payload = req.get('payload')
            if not isinstance(payload, dict):
                payload = {}
            resp = self.dispatch_hook_event(event, payload)
            try:
                _write_frame(conn, resp)
            except OSError:
                pass

    def dispatch_hook_event(self, event, payload):
        """Process a hook event payload and return the Antigravity stdout JSON dict."""
        cid = str(payload.get('conversationId') or '').strip()
        transcript_path = str(payload.get('transcriptPath') or '')
        if cid:
            with self._lock:
                self._heartbeats[cid] = time.monotonic()
                if transcript_path:
                    self._surface_hints[cid] = transcript_path

        if event == 'pre_tool_use':
            return self._handle_pre_tool_use(cid, payload)
        if event == 'post_invocation':
            return self._handle_post_invocation(cid)
        if event == 'stop':
            return self._handle_stop(cid, payload)
        return {}

    def _handle_pre_tool_use(self, cid, payload):
        if not cid:
            return {'decision': 'ask'}
        mode = self.permission_mode(cid)
        if mode == 'auto-allow':
            return {'decision': 'allow'}
        if mode != 'mobile-ask':
            # Default 'desktop' mode: let desktop Antigravity handle permission prompts natively
            return {'decision': 'ask'}

        tool_call = payload.get('toolCall') if isinstance(payload.get('toolCall'), dict) else {}
        tool_name = str(tool_call.get('name') or 'tool')
        tool_args = tool_call.get('args') if isinstance(tool_call.get('args'), dict) else {}
        request_id = f'agy-perm-{uuid.uuid4().hex[:12]}'
        kind = 'fileChange' if 'file' in tool_name or 'write' in tool_name or 'edit' in tool_name else 'commandExecution'
        title = f'Antigravity 請求執行 {tool_name}'
        text = format_tool_call({'name': tool_name, 'args': tool_args})
        prompt = {
            'id': request_id,
            'kind': kind,
            'title': title,
            'text': text,
            'questions': [],
            'permissions': {},
            'options': [
                {'id': 'accept', 'label': '允許 (Allow)'},
                {'id': 'decline', 'label': '拒絕 (Deny)'},
            ],
        }
        pending = _PendingApproval(request_id, cid, tool_name, tool_args, prompt)
        with self._lock:
            if self._closed:
                return {'decision': 'ask'}
            self._pending[request_id] = pending

        try:
            signaled = pending.event.wait(timeout=APPROVAL_TIMEOUT)
            with self._lock:
                self._heartbeats[cid] = time.monotonic()
            if not signaled or pending.decision not in ('allow', 'deny'):
                return {'decision': 'ask'}
            if pending.decision == 'allow':
                return {'decision': 'allow'}
            return {
                'decision': 'deny',
                'reason': pending.reason or '手機端已拒絕此操作 (Denied from mobile bridge)',
            }
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def _pop_queued_texts(self, cid, *, modes=None):
        items = list(self._queues.get(cid) or [])
        if not items:
            return []
        popped = []
        remaining = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_mode = str(item.get('mode') or 'queue')
            if modes is None or item_mode in modes:
                text = str(item.get('text') or '').strip()
                if text:
                    popped.append(text)
            else:
                remaining.append(item)
        if remaining:
            self._queues[cid] = remaining
        else:
            self._queues.pop(cid, None)
        self._save_json(self.queues_file, self._queues)
        return popped

    def _handle_post_invocation(self, cid):
        if not cid:
            return {}
        with self._lock:
            if cid in self._stop_requests:
                self._stop_requests.discard(cid)
                self._heartbeats.pop(cid, None)
                return {'terminationBehavior': 'terminate'}
            texts = self._pop_queued_texts(cid, modes={'steer'})
        if not texts:
            return {}
        combined = '\n\n'.join(texts)
        return {
            'injectSteps': [{'userMessage': combined}],
            'terminationBehavior': 'force_continue',
        }

    def _handle_stop(self, cid, payload):
        if not cid:
            return {}
        reason = str(payload.get('terminationReason') or '')
        with self._lock:
            self._stop_requests.discard(cid)
            if reason in ('USER_CANCELED', 'ERROR', 'MAX_INVOCATIONS', 'MAX_TOKEN_BUDGET_EXCEEDED'):
                self._heartbeats.pop(cid, None)
                return {}
            texts = self._pop_queued_texts(cid)
            if not texts:
                self._heartbeats.pop(cid, None)
                return {}
        combined = '\n\n'.join(texts)
        return {
            'decision': 'continue',
            'reason': f'手機端發來新追問，請直接接續處理並回覆使用者：\n\n{combined}',
        }

    def has_recent_heartbeat(self, conversation_id, ttl=HEARTBEAT_TTL):
        with self._lock:
            ts = self._heartbeats.get(conversation_id)
            if ts is None:
                return False
            if (time.monotonic() - ts) <= ttl:
                return True
            return any(p.conversation_id == conversation_id for p in self._pending.values())

    def pending_approvals_for(self, conversation_id):
        with self._lock:
            return [
                dict(item.prompt)
                for item in self._pending.values()
                if item.conversation_id == conversation_id
            ]

    def has_pending_approvals(self, conversation_id):
        with self._lock:
            return any(item.conversation_id == conversation_id for item in self._pending.values())

    def resolve_approval(self, conversation_id, request_id, decision_raw):
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.conversation_id != conversation_id:
                raise ValueError('該審批請求已過期或已在桌面端處理')
            normalized = str(decision_raw or '').strip().lower()
            if normalized in ('accept', 'allow', 'approve', 'approved', 'yes'):
                pending.decision = 'allow'
            else:
                pending.decision = 'deny'
            pending.event.set()

    def enqueue_follow_up(self, conversation_id, text, mode='queue'):
        cleaned = str(text or '').strip()
        if not cleaned:
            raise ValueError('訊息內容不能為空')
        normalized_mode = 'steer' if str(mode or '').strip() == 'steer' else 'queue'
        entry = {
            'id': f'q-{uuid.uuid4().hex[:8]}',
            'text': cleaned,
            'mode': normalized_mode,
            'createdAt': time.time(),
        }
        with self._lock:
            bucket = list(self._queues.get(conversation_id) or [])
            bucket.append(entry)
            self._queues[conversation_id] = bucket
            self._save_json(self.queues_file, self._queues)
        return entry

    def queued_messages_for(self, conversation_id):
        with self._lock:
            items = list(self._queues.get(conversation_id) or [])
        out = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = str(item.get('text') or '').strip()
            if not text:
                continue
            item_mode = str(item.get('mode') or 'queue')
            title = (
                '補充當前任務（將於當前步驟完成後立即注入）'
                if item_mode == 'steer'
                else '完成後發送（已排隊，將於當前任務結束後送出）'
            )
            out.append({
                'id': str(item.get('id') or f'queued-{idx}'),
                'role': 'user',
                'title': title,
                'text': text,
                'mode': item_mode,
            })
        return out

    def get_queued_items(self, conversation_id):
        with self._lock:
            items = list(self._queues.get(conversation_id) or [])
        out = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = str(item.get('text') or '').strip()
            if not text:
                continue
            item_mode = str(item.get('mode') or 'queue')
            out.append({
                'id': str(item.get('id') or f'q-{idx}'),
                'text': text,
                'mode': item_mode,
                'createdAt': item.get('createdAt') or time.time(),
            })
        return out

    def update_queue_mode(self, conversation_id, queue_id, mode):
        normalized_mode = 'steer' if str(mode or '').strip() == 'steer' else 'queue'
        with self._lock:
            items = list(self._queues.get(conversation_id) or [])
            target = None
            for item in items:
                if str(item.get('id')) == str(queue_id):
                    item['mode'] = normalized_mode
                    target = item
                    break
            if target:
                self._queues[conversation_id] = items
                self._save_json(self.queues_file, self._queues)
                return target
        return None

    def remove_queued_item(self, conversation_id, queue_id):
        with self._lock:
            items = list(self._queues.get(conversation_id) or [])
            remaining = []
            removed = None
            for item in items:
                if str(item.get('id')) == str(queue_id):
                    removed = item
                else:
                    remaining.append(item)
            if removed:
                if remaining:
                    self._queues[conversation_id] = remaining
                else:
                    self._queues.pop(conversation_id, None)
                self._save_json(self.queues_file, self._queues)
                return removed
        return None

    def request_stop(self, conversation_id):
        with self._lock:
            self._stop_requests.add(conversation_id)
            for item in list(self._pending.values()):
                if item.conversation_id == conversation_id:
                    item.decision = 'deny'
                    item.reason = '手機端已中止當前操作'
                    item.event.set()

    def permission_mode(self, conversation_id):
        with self._lock:
            mode = self._permissions.get(conversation_id, 'desktop')
        return mode if mode in ('desktop', 'mobile-ask', 'auto-allow') else 'desktop'

    def set_permission_mode(self, conversation_id, mode):
        if mode not in ('desktop', 'mobile-ask', 'auto-allow'):
            raise ValueError('不支援的審批模式')
        with self._lock:
            self._permissions[conversation_id] = mode
            self._save_json(self.permissions_file, self._permissions)
        return mode
