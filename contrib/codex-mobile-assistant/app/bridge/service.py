import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
import subprocess
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .ipc import DesktopIPC, IPCError
from .transport import ipc_endpoint
from .model import apply_patches, normalize_state, normalize_request, ordered_turns, async_requests, PERMISSION_PRESETS, permission_mode, permission_settings_mode, effective_permission_mode
from .store import SessionStore
from .files import artifact_paths, project_path as workspace_file_path, project_listing, upload_project_file
from .catalog import Catalog
from .remote import AppHosts, RemoteStore, RemoteCatalog, RemoteCreator, RemoteActions, RemoteUnavailable, project_path
from .creator import ThreadCreator, CreationError, open_desktop_chat
from .creations import CreationMixin
from .mounts import ProjectMountMixin
from .actions import ThreadActions
from .timeline import Timeline
from .images import prepare_images, save_images, image_inputs, image_path
from .attachments import upload_attachment, load_attachments, attachment_prompt, MAX_ATTACHMENTS
from .summary_language import with_summary_language


class LiveSession:
    def __init__(self, thread_id):
        self.id = thread_id
        self.owner = None
        self.discovering = False
        self.state = None
        self.revision = None
        self.sequence = 0
        self.connected = False
        self.connecting = False
        self.retry_at = 0
        self.error = None
        self.viewers = 0
        self.touched = time.monotonic()
        self.condition = threading.Condition(threading.RLock())
        self.attach_lock = threading.Lock()
        self.action_lock = threading.Lock()
        self.timeline = Timeline()
        self.timeline_time_revision = None
        self.timeline_message_times = {}

    def changed(self):
        self.sequence += 1
        self.condition.notify_all()

    def view(self):
        with self.condition:
            result = normalize_state(self.state or {"id": self.id}, self.connected)
            result["sequence"] = self.sequence
            result["connectionError"] = self.error
            result["connecting"] = self.connecting
            result["loadingHistory"] = self.state is None
            return result


class Bridge(CreationMixin, ProjectMountMixin):
    def __init__(self, codex_home, data_dir, host="local", alias=None, ipc_path=None, codex_bin=None):
        self.host = host
        self.codex_home = Path(codex_home)
        self.data_dir = Path(data_dir)
        self.hosts = AppHosts(codex_home)
        self.remote_bridges = {}
        self.host_errors = []
        self.store = RemoteStore(alias) if alias else SessionStore(codex_home)
        self.catalog_reader = RemoteCatalog(alias) if alias else Catalog(codex_home, codex_bin)
        self.creator = RemoteCreator(alias) if alias else ThreadCreator(codex_home, self.catalog_reader.executable)
        self.actions = RemoteActions(alias) if alias else ThreadActions(codex_home, self.catalog_reader.executable)
        self.active_creations = {}
        self.creation_lock = threading.RLock()
        self.creations_path = self.data_dir / 'new_threads.json'
        self.creations = json.loads(self.creations_path.read_text(encoding='utf-8')) if self.creations_path.exists() else {}
        self.phone_meta_path = self.data_dir / 'phone_chats.json'
        self.phone_meta = json.loads(self.phone_meta_path.read_text(encoding='utf-8')) if self.phone_meta_path.exists() else {}
        self.live = {}
        self.lock = threading.RLock()
        self.ipc = DesktopIPC(ipc_path or ipc_endpoint(codex_home), self._event, self._disconnected)
        self.closed = threading.Event()
        Path(data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ledger_path = Path(data_dir) / "submissions.json"
        self.submit_lock = threading.Lock()
        self.submissions = json.loads(self.ledger_path.read_text(encoding='utf-8')) if self.ledger_path.exists() else {}
        for key, value in self.submissions.items():
            if value.get("status") == "queued":
                self.live.setdefault(key.split(":")[0], LiveSession(key.split(":")[0]))
        threading.Thread(target=self._maintain, daemon=True).start()

    def for_host(self, host):
        if host == self.host:
            return self
        available = self.hosts.hosts()
        if self.host != "local" or host not in available:
            raise KeyError("未知 SSH 主机")
        with self.lock:
            if host not in self.remote_bridges:
                folder = self.data_dir / 'hosts' / hashlib.sha256(host.encode()).hexdigest()[:16]
                self.remote_bridges[host] = Bridge(self.codex_home, folder, host, available[host]['alias'], ipc_path=self.ipc.path)
            return self.remote_bridges[host]

    def list(self, query="", limit=100, offset=0, archived=False, working=False):
        sources = [(self, "此电脑")]
        if self.host == 'local':
            sources.extend((self.for_host(host), info.get('displayName') or info['alias']) for host, info in self.hosts.hosts().items())
        def read(source):
            bridge, label = source
            try:
                rows = bridge.store.list(limit=limit + offset, archived=archived, query=query, **({"working": True} if working else {}))
                with bridge.lock:
                    pinned_ids = [key for key, value in bridge.phone_meta.items() if value.get('pinned')]
                present = {row['id'] for row in rows}
                for thread_id in ([] if working else pinned_ids):
                    if thread_id in present:
                        continue
                    try:
                        row = bridge.store.get(thread_id)
                    except KeyError:
                        continue
                    if bool(row.get('archived')) == archived and (not query or any(query.casefold() in str(row.get(k, '')).casefold() for k in ('name', 'title', 'cwd'))):
                        rows.append({k: row[k] for k in ('id', 'name', 'title', 'cwd', 'updated_at', 'recency_at_ms', 'archived') if k in row})
                if working:
                    # Persisted open turns are only candidates; confirm with desktop state.
                    for row in rows:
                        bridge.session(row['id'], background=True)
                rows = self.hosts.decorate(rows, bridge.host, label)
                with bridge.lock:
                    for row in rows:
                        session = bridge.live.get(row['id'])
                        row['connected'] = bool(session and session.connected)
                        row['working'] = bool(session and session.connected and session.view()['status'] == 'active')
                        row['title'] = row.get('name') or row.get('title') or '未命名聊天'
                        row['pinned'] = bool(bridge.phone_meta.get(row['id'], {}).get('pinned'))
                return ([row for row in rows if row["working"]] if working else rows), None
            except RemoteUnavailable as exc:
                return [], {'host': bridge.host, 'label': label, 'error': str(exc)}
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
            results = list(pool.map(read, sources))
        self.host_errors = [error for _, error in results if error]
        rows = [row for group, _ in results for row in group]
        rows.sort(key=lambda row: (row['pinned'], row['recency'], row['id'], row['host']), reverse=True)
        return rows[offset:offset + limit]

    def manage_chat(self, thread_id, action, title=None, pinned=None, confirm_id=None):
        uuid.UUID(thread_id)
        if action not in ('rename', 'pin', 'archive', 'unarchive', 'delete'):
            raise ValueError('不支持的聊天操作')
        if action == 'rename' and (not isinstance(title, str) or not title.strip() or len(title) > 120):
            raise ValueError('标题为 1–120 字')
        session = self.session(thread_id, attach=False)
        with session.action_lock:
            metadata = self.store.get(thread_id)
            if action == 'pin':
                if type(pinned) is not bool:
                    raise ValueError('置顶状态无效')
                with self.lock:
                    self.phone_meta.setdefault(thread_id, {})['pinned'] = pinned
                    temporary = self.phone_meta_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(self.phone_meta, ensure_ascii=False), encoding='utf-8')
                    temporary.chmod(0o600)
                    temporary.replace(self.phone_meta_path)
                return {'ok': True, 'pinned': pinned}
            if action in ('archive', 'delete'):
                with self.submit_lock:
                    if any(k.startswith(thread_id + ':') and v.get('status') in ('queued', 'unknown') for k, v in self.submissions.items()):
                        raise ValueError('此聊天还有排队或未确认消息，请先处理')
            if action == 'archive':
                self._target(thread_id)
                with session.condition:
                    if session.view()['status'] != 'idle' or session.view()['requests']:
                        raise ValueError('请先停止当前任务并处理待确认请求，再归档')
            if action == 'delete':
                if confirm_id != thread_id:
                    raise ValueError('请确认要永久删除的这条聊天')
                if not metadata.get('archived'):
                    raise ValueError('请先归档此聊天，再永久删除')
                with session.condition:
                    if session.connected and (session.view()['status'] == 'active' or session.view()['requests']):
                        raise ValueError('此聊天仍在运行或等待回应，不能删除')
            result = self.actions.apply(thread_id, action, title)
            if action in ('archive', 'unarchive'):
                try:
                    self.ipc.notify_archive(thread_id, action == 'archive', self.host)
                except IPCError:
                    result['warning'] = '记录已修改，桌面刷新后可看到变化。'
            if isinstance(self.store, RemoteStore):
                self.store.invalidate()
            with session.condition:
                if action == 'rename' and session.state is not None:
                    session.state['title'] = title.strip()
                if action in ('archive', 'delete'):
                    session.connected = False
                    session.error = '此聊天已归档' if action == 'archive' else '此聊天已删除'
                session.changed()
            if action in ('archive', 'delete'):
                try:
                    self.ipc.follow(thread_id, session.owner, False, host=self.host)
                except IPCError:
                    pass
                with self.lock:
                    self.live.pop(thread_id, None)
            return result

    def open_chat(self, thread_id):
        metadata = self.store.get(thread_id)
        if metadata.get('archived'):
            raise ValueError('请先恢复归档，再打开桌面聊天')
        open_desktop_chat(thread_id, self.host)
        self.session(thread_id, background=True, force=True)
        return {'ok': True}

    def chat_info(self, thread_id):
        row = self.store.get(thread_id)
        with self.lock:
            pinned = bool(self.phone_meta.get(thread_id, {}).get('pinned'))
            session = self.live.get(thread_id)
        live = {}
        if session:
            with session.condition:
                live = {'connected': session.connected, 'connecting': session.connecting,
                        'status': (session.state or {}).get('threadRuntimeStatus', {}).get('type', 'idle')}
        return {'id': thread_id, 'host': self.host, 'title': row.get('name') or row.get('title') or '未命名聊天',
                'archived': bool(row.get('archived')), 'pinned': pinned, 'cwd': row.get('cwd') or '',
                'connected': bool(live.get('connected')), 'connecting': bool(live.get('connecting')),
                'status': live.get('status', 'idle')}

    def diagnostics(self):
        try:
            self.ipc.connect()
            return {'gateway': 'online', 'desktop': 'available', 'message': '手机已连上网关，桌面通道可用。若聊天未连接，请打开对应桌面聊天。'}
        except IPCError:
            return {'gateway': 'online', 'desktop': 'unavailable', 'message': '网关正常，Codex 桌面通道未连接。请在电脑打开 Codex。'}

    def projects(self):
        state = self.hosts.state()
        projects = [{'key': 'local|projectless', 'host': 'local', 'hostLabel': '此电脑',
                     'name': '独立对话', 'cwd': '', 'roots': [], 'projectId': None}]
        for project in state.get('local-projects', {}).values():
            roots = [p for p in project.get('rootPaths', []) if isinstance(p, str) and os.path.isabs(p)]
            if not roots or not project.get('id'):
                continue
            projects.append({'key': 'local|' + project['id'], 'host': 'local', 'hostLabel': '此电脑',
                             'name': project.get('name') or Path(roots[0]).name, 'cwd': roots[0],
                             'roots': roots, 'projectId': project['id']})
        hosts = self.hosts.hosts()
        for project in state.get('remote-projects', []):
            host = project.get('hostId')
            if host not in hosts or not project.get('id') or not project.get('remotePath', '').startswith('/'):
                continue
            cwd = project['remotePath']
            projects.append({'key': host + '|' + project['id'], 'host': host,
                             'hostLabel': hosts[host].get('displayName') or hosts[host]['alias'],
                             'name': project.get('label') or cwd.rsplit('/', 1)[-1], 'cwd': cwd,
                             'roots': [cwd], 'projectId': project['id']})
        # Older desktops may have chats but no saved local-projects registry.
        try:
            recent = SessionStore(self.codex_home).list(limit=100)
        except (OSError, RuntimeError):
            recent = []
        known = [project_path(root) for p in projects if p['host'] == 'local' for root in p['roots']]
        mobile_root = project_path(str((self.codex_home / 'mobile-workspaces').resolve()))
        for row in recent:
            cwd = row.get('cwd') or ''
            normal = project_path(cwd)
            if not os.path.isabs(cwd) or normal.startswith(mobile_root + '/') or any(normal == root or normal.startswith(root + '/') for root in known):
                continue
            projects.append({'key': 'local|cwd:' + hashlib.sha256(cwd.encode()).hexdigest()[:24],
                             'host': 'local', 'hostLabel': '此电脑', 'name': Path(cwd).name or cwd,
                             'cwd': cwd, 'roots': [cwd], 'projectId': None})
            known.append(normal)
        return projects

    def session(self, thread_id, attach=True, background=False, force=False):
        uuid.UUID(thread_id)
        with self.lock:
            session = self.live.get(thread_id)
        if session is None:
            # SSH/SQLite reads must not block IPC events for other chats.
            self.store.get(thread_id)
            with self.lock:
                session = self.live.setdefault(thread_id, LiveSession(thread_id))
        session.touched = time.monotonic()
        if background:
            if attach:
                self._refresh_async(session, force)
            return session
        if attach and not session.connected:
            self._attach(session)
        if session.state is None:
            fallback = self.store.history(thread_id)
            with session.condition:
                if session.state is None:
                    session.state = fallback
                    session.changed()
        return session

    def _refresh_async(self, session, force=False):
        with session.condition:
            if session.connected or session.connecting or self.closed.is_set():
                return
            if not force and time.monotonic() < session.retry_at:
                return
            session.connecting = True
            session.changed()
        def refresh():
            try:
                if session.state is None:
                    try:
                        fallback = self.store.history(session.id)
                        with session.condition:
                            if session.state is None:
                                session.state = fallback
                                session.changed()
                    except (OSError, ValueError, KeyError, RemoteUnavailable):
                        # A missing saved rollout must not prevent a live snapshot.
                        logging.getLogger(__name__).warning("Saved history unavailable; trying desktop snapshot")
                if not self.closed.is_set():
                    self._attach(session)
            except Exception:
                logging.getLogger(__name__).exception("Background session read failed")
                with session.condition:
                    session.error = "读取会话失败，请重新连接或查看网关日志。"
            finally:
                with session.condition:
                    session.connecting = False
                    session.retry_at = time.monotonic() + 15
                    session.changed()
        threading.Thread(target=refresh, daemon=True).start()

    def _attach(self, session):
        with session.attach_lock:
            if session.connected:
                return
            try:
                if self.host == "local":
                    owner = self.ipc.owner(session.id, self.host)
                    with session.condition:
                        session.owner = owner
                        session.revision = None
                    self.ipc.follow(session.id, owner, host=self.host)
                else:
                    # Remote owners publish snapshots but are not registered by local discovery.
                    self.ipc.connect()
                    with session.condition:
                        session.owner = None
                        session.revision = None
                        session.discovering = True
                    self.ipc.follow(session.id, None, host=self.host)
                with session.condition:
                    if not session.condition.wait_for(lambda: session.connected or self.closed.is_set(), timeout=8):
                        raise IPCError("桌面聊天尚未就绪，点击“打开桌面聊天”后会自动连接。")
            except IPCError as exc:
                if self.host != "local":
                    try:
                        self.ipc.follow(session.id, None, False, host=self.host)
                    except IPCError:
                        pass
                with session.condition:
                    session.discovering = False
                    session.connected = False
                    session.error = "点击“打开桌面聊天”，助手会自动连接这条历史对话。" if "no-client-found" in str(exc) else str(exc)
                    session.changed()

    def _event(self, message):
        if message.get("method") == "client-status-changed":
            params = message.get("params", {})
            if params.get("status") == "disconnected":
                with self.lock:
                    sessions = list(self.live.values())
                for session in sessions:
                    if session.owner == params.get("clientId"):
                        self._invalidate(session, "桌面会话连接已断开，正在等待重连")
            return
        if message.get("method") != "thread-stream-state-changed":
            return
        if message.get("version") != 11:
            self._disconnected()
            return
        params = message.get("params", {})
        if params.get("hostId") != self.host:
            return
        with self.lock:
            session = self.live.get(params.get("conversationId"))
        if session is None:
            return
        change = params.get("change", {})
        with session.condition:
            if session.discovering and change.get("type") == "snapshot" and (change.get("conversationState") or {}).get("id") == session.id:
                session.owner = message.get("sourceClientId")
                session.discovering = False
            if not session.owner or message.get("sourceClientId") != session.owner:
                return
            try:
                if change.get("type") == "snapshot":
                    state = change["conversationState"]
                    if state.get("id") != session.id:
                        raise ValueError("Session mismatch")
                    session.state = state
                elif change.get("type") == "patches":
                    if session.revision is None or change.get("baseRevision") != session.revision:
                        raise ValueError("Revision mismatch")
                    session.state = apply_patches(session.state, change["patches"])
                else:
                    return
                session.revision = change["revision"]
                session.connected = True
                session.error = None
                session.changed()
            except (ValueError, KeyError, IndexError, TypeError):
                session.connected = False
                session.revision = None
                session.error = "同步状态发生变化，正在重新读取桌面快照"
                session.changed()

    def _invalidate(self, session, message):
        with session.condition:
            session.connected = False
            session.revision = None
            session.error = message
            session.changed()

    def _disconnected(self):
        with self.lock:
            sessions = list(self.live.values())
        for session in sessions:
            self._invalidate(session, "桌面 App 连接已断开，正在等待重连")

    def _maintain(self):
        delay = 3
        while not self.closed.wait(delay):
            with self.lock:
                sessions = list(self.live.values())
            for session in sessions:
                with self.submit_lock:
                    queued = [(k, dict(v)) for k, v in self.submissions.items() if k.startswith(session.id + ":") and v["status"] == "queued"]
                if queued and session.connected and session.view().get("status") == "idle":
                    key, entry = queued[0]
                    try:
                        self._send_queued(session, key, entry)
                    except Exception:
                        pass  # Unknown outcomes stay recorded and are never automatically replayed.
                if (session.viewers > 0 or queued) and not session.connected:
                    self._refresh_async(session)
                elif session.viewers == 0 and not queued and time.monotonic() - session.touched > 300:
                    with self.lock:
                        if session.viewers != 0:
                            continue
                        self.live.pop(session.id, None)
                    if session.connected:
                        try:
                            self.ipc.follow(session.id, session.owner, False, host=self.host)
                        except IPCError:
                            pass
            delay = 3 if self.ipc.client_id else min(30, delay * 2)

    def _target(self, thread_id):
        session = self.session(thread_id)
        with session.condition:
            if not session.connected or not session.owner:
                raise IPCError(session.error or "请先在桌面 App 打开此聊天")
        return session

    def _call(self, session, method, params, timeout=30):
        return self.ipc.request(method, {"conversationId": session.id, **params}, target=session.owner, host=self.host, timeout=timeout)["result"]

    def _save_ledger(self):
        # Keep accepted or uncertain submissions durable across process restarts.
        target = self.ledger_path.with_suffix(".tmp")
        target.write_text(json.dumps(self.submissions, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.ledger_path)

    def _reconcile_submissions(self, session):
        with self.submit_lock:
            entries = {key: copy.deepcopy(value) for key, value in self.submissions.items()
                       if key.startswith(session.id + ':')}
        pending = {key: value for key, value in entries.items() if value['status'] == 'unknown'}
        if not pending:
            return
        # A desktop acknowledgement with the exact client id is authoritative.
        confirmed = set()
        with session.condition:
            def visit(node):
                if isinstance(node, dict):
                    client_id = node.get('clientUserMessageId')
                    if isinstance(client_id, str):
                        confirmed.add(session.id + ':' + client_id)
                    for value in node.values():
                        visit(value)
                elif isinstance(node, list):
                    for value in node:
                        visit(value)
            visit(session.state or {})
        # Older desktops omit the client id. Match a unique persisted user message
        # within the RPC window, including exact image hashes. Never infer delivery
        # from idle status or an assistant reply, and never replay unknown messages.
        if self.host == 'local' and set(pending) - confirmed:
            try:
                receipts = self.store.delivery_receipts(session.id)
            except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error):
                receipts = []
            matches = {}
            for index, receipt in enumerate(receipts):
                candidates = [key for key, entry in entries.items()
                              if entry['status'] != 'queued' and not entry.get('skills')
                              and 0 <= receipt['at'] - entry.get('dispatchedAt', entry['at']) <= (30 if entry['mode'] == 'steer' else 90)
                              and receipt['text'] == entry.get('wireText', entry['text'])
                              and receipt['images'] == [image['id'] for image in entry.get('images', [])]]
                for key in candidates:
                    matches.setdefault(key, []).append((index, len(candidates)))
            confirmed.update(key for key, hits in matches.items()
                             if len(hits) == 1 and hits[0][1] == 1)
        with self.submit_lock:
            changed = False
            for key in confirmed & pending.keys():
                if self.submissions[key]['status'] == 'unknown':
                    self.submissions[key]['status'] = 'accepted'
                    changed = True
            if changed:
                self._save_ledger()
        if changed:
            with session.condition:
                session.changed()

    def view(self, thread_id, attach=True, background=False, force=False):
        session = self.session(thread_id, attach=attach, background=background, force=force)
        self._reconcile_submissions(session)
        view = session.view()
        view["host"] = self.host
        view["hostLabel"] = "此电脑" if self.host == "local" else self.hosts.hosts().get(self.host, {}).get("displayName", self.host)
        with session.condition:
            artifacts = artifact_paths(session.state or {}, self.store.home) if self.host == "local" else {}
        view["files"] = [{"id": k, "name": v["name"], "reference": v["reference"], "image": v["image"], "visualization": v['visualization'], "absolutePath": str(v["path"])} for k, v in artifacts.items()]
        with self.submit_lock:
            view["submissions"] = [{"id": k.split(":")[1], "text": v["text"], "status": v["status"], "imageCount": len(v.get('images', [])), 'fileCount': len(v.get('files', []))}
                                   for k, v in self.submissions.items()
                                   if k.startswith(thread_id + ":") and v["status"] in ("queued", "unknown")]
        return view

    def upload_file(self, thread_id, relative, name, stream, size):
        if self.host != 'local':
            raise ValueError('文件传输目前支持电脑本地项目')
        meta = self.store.get(thread_id)
        return upload_project_file(meta.get('cwd'), relative, name, stream, size)

    def task_statuses(self, include_recent=False):
        # Discover running desktop tasks, then retain their live terminal state.
        self.list(working=True)
        with self.lock:
            bridges = [self, *self.remote_bridges.values()] if self.host == 'local' else [self]
        rows = []
        for bridge in bridges:
            with bridge.lock:
                sessions = list(bridge.live.values())
            for session in sessions:
                with session.condition:
                    if not session.connected:
                        continue
                    view = session.view()
                    row = {'id': session.id, 'host': bridge.host, 'title': view.get('title', '对话'),
                           'status': view['status'], 'requests': [r['id'] for r in view.get('requests', [])],
                           'error': bool(view.get('latestError'))}
                    if include_recent:
                        last = view['turns'][-1] if view['turns'] else {}
                        phase = ('attention' if row['requests'] else 'running' if view['status'] == 'active'
                                 else 'attention' if row['error'] else 'completed' if last.get('status') == 'completed' else '')
                        detail = ('等你回复' if any('requestUserInput' in r.get('method', '') for r in view.get('requests', []))
                                  else '等待授权或确认' if row['requests'] else '任务异常，请查看' if row['error']
                                  else '正在运行' if phase == 'running' else '已完成')
                        methods = [r.get('method', '') for r in view.get('requests', [])]
                        action_kind = ('reply' if any('requestUserInput' in method for method in methods)
                                       else 'approval' if methods and all('requestApproval' in method for method in methods)
                                       else 'confirm' if methods else '')
                        row.update(phase=phase, detail=detail, actionKind=action_kind, cwd=view.get('cwd') or '', connected=True,
                                   at=last.get('startedAt') or 0,
                                   token=str(last.get('id') or '') + ':' + phase + ':' + json.dumps(row['requests'], sort_keys=True))
                        if row['error']:
                            row['token'] += ':' + hashlib.sha256(json.dumps(view['latestError'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
                    rows.append(row)
        if include_recent:
            known = {(row['host'], row['id']) for row in rows}
            # Include confirmed recent completions, even if never opened on this phone.
            for meta in self.store.list(limit=50):
                if (self.host, meta['id']) in known:
                    continue
                event = self.store.latest_task_event(meta) if isinstance(self.store, SessionStore) else {}
                if event.get('type') != 'task_complete':
                    continue
                rows.append({**meta, 'host': self.host, 'title': meta.get('name') or meta.get('title') or '未命名聊天',
                             'status': 'idle', 'requests': [], 'error': False, 'connected': False,
                             'phase': 'completed', 'detail': '已完成', 'token': str(event.get('turnId') or event.get('timestamp', '')) + ':completed:[]'})
            for bridge in bridges:
                group = [row for row in rows if row['host'] == bridge.host]
                self.hosts.decorate(group, bridge.host, '此电脑' if bridge.host == 'local' else self.hosts.hosts().get(bridge.host, {}).get('displayName', bridge.host))
                for row in group:
                    row['at'] = row.get('at') or row.get('recency') or 0
            rows = [row for row in rows if row.get('phase')]
            rows.sort(key=lambda row: row.get('at', 0), reverse=True)
            completed = [row for row in rows if row['phase'] == 'completed'][:20]
            rows = [row for row in rows if row['phase'] != 'completed'] + completed
            return {'tasks': rows, 'unavailableHosts': self.host_errors, 'completedLimit': 20}
        return {'tasks': rows}

    def project_files(self, thread_id, relative='', offset=0, download=False, artifact_id='', locate=False):
        if self.host != 'local':
            raise ValueError('文件传输目前支持电脑本地项目')
        meta = self.store.get(thread_id)
        cwd = meta.get('cwd')
        focus = ''
        if artifact_id:
            artifact = self.artifact(thread_id, artifact_id)
            path = artifact['path']
            root = Path(cwd).resolve() if cwd else None
            # Generated files outside the project stay within their own folder.
            if root is None or root not in path.parents:
                root = path.parent
            cwd = str(root)
            if locate and not download:
                focus = path.relative_to(root).as_posix()
                relative = path.parent.relative_to(root).as_posix()
        if download:
            _, path = workspace_file_path(cwd, relative)
            if not path.is_file():
                raise ValueError('请选择一个文件下载')
            return path
        result = project_listing(cwd, relative, offset, focus)
        result['readOnly'] = bool(artifact_id)
        return result

    def artifact(self, thread_id, artifact_id):
        session = self.session(thread_id, attach=False)
        with session.condition:
            files = artifact_paths(session.state or {}, self.store.home) if self.host == "local" else {}
        if artifact_id not in files:
            raise KeyError("文件不属于此聊天的工作目录")
        return files[artifact_id]

    def timeline_read(self, thread_id, mode='page', **options):
        session = self.session(thread_id, background=True)
        if mode != 'detail':
            self._reconcile_submissions(session)
        time_revision, message_times = None, {}
        if mode != 'detail' and self.host == 'local':
            try:
                time_revision, message_times = self.store.message_times(thread_id)
            except (OSError, ValueError, KeyError):
                pass  # Missing rollout timestamps must not block live conversation updates.
        with session.condition:
            if time_revision is not None and (session.timeline_time_revision is None or time_revision > session.timeline_time_revision):
                session.timeline_time_revision = time_revision
                session.timeline_message_times = message_times
                session.changed()
            projection = session.timeline
            if projection.sequence != session.sequence:
                projection.update(session.view(), session.timeline_message_times)
            if mode == 'detail':
                result = projection.detail(**options)
                row = projection.rows[projection.positions[result['key']]]
                texts = [result['text']] if row['role'] == 'assistant' else []
            else:
                result = projection.changes(**options) if mode == 'changes' else projection.page(**options)
                result['meta'] = dict(result['meta'], host=self.host,
                                      hostLabel='此电脑' if self.host == 'local' else self.hosts.hosts().get(self.host, {}).get('displayName', self.host))
                texts = [row['text'] for row in result['rows'] if row['role'] == 'assistant']
            cwd = (session.state or {}).get('cwd')
        # Resolve only links in the delivered page, not every file in the chat.
        file_state = {'cwd': cwd, 'turns': [{'items': [{'type': 'agentMessage', 'text': text} for text in texts]}]}
        artifacts = artifact_paths(file_state, self.store.home) if texts and self.host == 'local' else {}
        result['files'] = [{'id': k, 'name': v['name'], 'reference': v['reference'], 'image': v['image'], 'visualization': v['visualization'], 'absolutePath': str(v['path'])} for k, v in artifacts.items()]
        if mode != 'detail':
            with self.submit_lock:
                result['meta']['submissions'] = [{'id': k.split(':')[1], 'text': v['text'], 'status': v['status'], 'imageCount': len(v.get('images', [])), 'fileCount': len(v.get('files', []))}
                                                 for k, v in self.submissions.items()
                                                 if k.startswith(thread_id + ':') and v['status'] in ('queued', 'unknown')]
        return result

    def catalog(self, thread_id, refresh=False):
        session = self.session(thread_id)
        with session.condition:
            cwd = session.state.get("cwd")
            model = session.state.get("latestModel")
            effort = session.state.get("latestReasoningEffort") or (session.state.get("latestThreadSettings") or {}).get("effort")
        catalog = self.catalog_reader.get(cwd, refresh=refresh)
        return {**catalog, "currentModel": model, "currentEffort": effort}

    def _resolve_skills(self, session, skills):
        if not isinstance(skills, list) or len(skills) > 8 or not all(isinstance(s, str) for s in skills):
            raise ValueError("最多选择 8 个 Skill")
        if not skills:
            return []
        catalog = self.catalog(session.id)
        by_id = {s["id"]: s for s in catalog["skills"]}
        selected = []
        for key in sorted(set(skills)):
            skill = by_id.get(key)
            if not skill or (self.host == "local" and not Path(skill["path"]).is_file()):
                raise ValueError("Skill 不可用，请刷新列表")
            selected.append({"id": key, "name": skill["name"], "path": skill["path"]})
        return selected

    def settings(self, thread_id, model, effort):
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,199}", model):
            raise ValueError("模型 ID 格式不正确")
        if effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            raise ValueError("请选择有效的推理强度")
        session = self._target(thread_id)
        known = next((m for m in self.catalog(thread_id)["models"] if m["id"] == model), None)
        if known and effort not in known["efforts"]:
            raise ValueError("这个模型不支持所选推理强度")
        with session.action_lock:
            result = self._call(session, "thread-follower-update-thread-settings", {"threadSettings": {"model": model, "effort": effort}})
        if not result.get("applied"):
            raise IPCError("桌面未应用模型设置，请刷新后重试")
        with session.condition:
            confirmed = session.condition.wait_for(lambda: session.state.get("latestModel") == model and session.view().get("effort") == effort, timeout=5)
        return {"applied": True, "confirmed": confirmed, "model": model, "effort": effort}

    def permissions(self, thread_id, mode):
        if not isinstance(mode, str) or mode not in PERMISSION_PRESETS:
            raise ValueError('请选择请求批准、帮我批准或完全访问权限；若仍显示旧选项，请刷新页面')
        session = self._target(thread_id)
        with session.action_lock:
            result = self._call(session, 'thread-follower-update-thread-settings',
                                {'threadSettings': dict(PERMISSION_PRESETS[mode])})
            if not result.get('applied'):
                raise IPCError('桌面未应用权限设置，请刷新后重试')
            with session.condition:
                confirmed = session.condition.wait_for(
                    lambda: permission_mode(session.state or {}) == mode, timeout=5)
                current_mode = permission_mode(session.state or {})
                effective_mode = effective_permission_mode(session.state or {})
        return {'applied': True, 'confirmed': confirmed, 'mode': mode,
                'currentMode': current_mode, 'effectiveMode': effective_mode, 'scope': 'next-turn'}

    def cancel_queued(self, thread_id, submission_id):
        uuid.UUID(submission_id)
        session = self.session(thread_id, attach=False)
        with session.action_lock:
            with self.submit_lock:
                key = thread_id + ":" + submission_id
                prior = self.submissions.get(key)
                if not prior or prior["status"] != "queued":
                    raise ValueError("此消息已离开队列，请查看会话")
                prior["status"] = "cancelled"
                self._save_ledger()
            with session.condition:
                session.changed()
        return {"status": "cancelled"}

    def upload_attachment(self, thread_id, name, stream, size):
        if self.host != 'local':
            raise ValueError('普通文件附件目前支持此电脑上的对话，SSH 对话仍可添加图片')
        self.store.get(thread_id)
        return upload_attachment(self.data_dir, thread_id, name, stream, size)

    def send(self, thread_id, text, submission_id, mode="send", skills=None, images=None, files=None):
        uuid.UUID(submission_id)
        prepared = prepare_images([] if images is None else images)
        descriptors = [descriptor for descriptor, _ in prepared]
        if files and self.host != 'local':
            raise ValueError('普通文件附件目前支持此电脑上的对话')
        file_descriptors = load_attachments(self.data_dir, thread_id, [] if files is None else files)
        if len(descriptors) + len(file_descriptors) > MAX_ATTACHMENTS:
            raise ValueError('每条消息最多添加 4 个附件（含图片）')
        if not isinstance(text, str) or len(text) > 100000 or not (text.strip() or descriptors or file_descriptors):
            raise ValueError("请输入消息或添加附件，文字不能超过 100000 字")
        if mode not in ("send", "steer", "queue"):
            raise ValueError("未知发送方式")
        session = self._target(thread_id)
        selected = self._resolve_skills(session, [] if skills is None else skills)
        key = thread_id + ":" + submission_id
        with session.action_lock:
            with self.submit_lock:
                prior = self.submissions.get(key)
                if prior:
                    if prior["text"] != text or prior["mode"] != mode or prior.get("skills", []) != selected or prior.get("images", []) != descriptors or prior.get('files', []) != file_descriptors:
                        raise ValueError("同一消息标识不能用于不同内容")
                    return {"status": prior["status"], "duplicate": True, "id": submission_id}
            with session.condition:
                active = session.state.get("threadRuntimeStatus", {}).get("type") == "active"
                if active and mode == "send":
                    raise ValueError("Codex 正在执行。请选择「排队发送」或「补充当前任务」。")
                if not active and mode == "steer":
                    raise ValueError("当前任务已经结束，请使用普通发送")
            with self.submit_lock:
                save_images(self.data_dir, thread_id, prepared)
                entry = {"text": text, "mode": mode, "skills": selected, "images": descriptors, 'files': file_descriptors,
                         'wireText': with_summary_language(attachment_prompt(self.data_dir, thread_id, text, file_descriptors)),
                         "status": "queued" if mode == "queue" else "unknown", "at": time.time()}
                self.submissions[key] = entry
                self._save_ledger()
            with session.condition:
                session.changed()
            if mode == "queue":
                return {"status": "queued", "id": submission_id}
            return self._dispatch(session, key, entry)

    def _send_queued(self, session, key, entry):
        with session.action_lock:
            with session.condition:
                if not session.connected or session.state.get("threadRuntimeStatus", {}).get("type") != "idle":
                    return
            with self.submit_lock:
                if self.submissions[key]["status"] != "queued":
                    return
                self.submissions[key]["status"] = "unknown"
                self.submissions[key]["dispatchedAt"] = time.time()
                self._save_ledger()
            try:
                self._dispatch(session, key, entry)
            finally:
                with session.condition:
                    session.changed()

    def _dispatch(self, session, key, entry):
        submission_id = key.split(":")[1]
        load_attachments(self.data_dir, session.id, entry.get('files', []))
        text = entry.get('wireText', entry['text'])
        request = {"threadId": session.id, "input": [{"type": "text", "text": text, "text_elements": []}], "clientUserMessageId": submission_id}
        if not text.strip():
            request['input'] = []
        request['input'].extend(image_inputs(self.data_dir, session.id, entry.get('images', [])))
        request["input"].extend({"type": "skill", "name": s["name"], "path": s["path"]} for s in entry.get("skills", []))
        if entry["mode"] != "steer":
            with session.condition:
                selected_mode = permission_settings_mode((session.state or {}).get('latestThreadSettings'))
            # Send the user's saved preset explicitly; desktop defaults can differ from it.
            if selected_mode in PERMISSION_PRESETS:
                request.update(PERMISSION_PRESETS[selected_mode])
            response = self._call(session, "thread-follower-start-turn", {
                "turnStart": {"request": request, "context": {"inheritThreadSettings": True, "attachments": [], "commentAttachments": []}}}, timeout=90)
        else:
            response = self._call(session, "thread-follower-steer-turn", {
                "input": request["input"], "clientUserMessageId": submission_id,
                "restoreMessage": {"request": request, "context": {"attachments": [], "commentAttachments": []}},
                "attachments": []}, timeout=30)
        with self.submit_lock:
            self.submissions[key]["status"] = "accepted"
            self._save_ledger()
        with session.condition:
            session.changed()
        return {"status": "accepted", "id": submission_id, "result": response}

    def uploaded_image(self, thread_id, image_id):
        self.store.get(thread_id)
        with self.submit_lock:
            descriptor = next((image for key, entry in self.submissions.items()
                               if key.startswith(thread_id + ':')
                               for image in entry.get('images', []) if image['id'] == image_id), None)
        if not descriptor:
            raise KeyError('图片不存在')
        return image_path(self.data_dir, thread_id, image_id).read_bytes(), descriptor['mime']

    def interrupt(self, thread_id):
        session = self._target(thread_id)
        with session.condition:
            active = next((t for t in reversed(ordered_turns(session.state)) if t.get("status") == "inProgress"), None)
            if not active or not active.get("turnId"):
                raise ValueError("当前没有可停止的任务")
            turn_id = active["turnId"]
        return self._call(session, "thread-follower-interrupt-turn", {"mode": "user-stop", "expectedTurnId": turn_id})

    def full_history(self, thread_id):
        session = self._target(thread_id)
        return self._call(session, "thread-follower-load-complete-history", {}, timeout=180)

    def respond(self, thread_id, request_id, response):
        session = self._target(thread_id)
        with session.condition:
            async_pending = next((r for r in async_requests(session.state) if r["id"] == request_id), None)
        if async_pending:
            questions = async_pending["params"]["questions"]
            answers = response.get("answers", {})
            if set(answers) != {q["id"] for q in questions} or any(not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], str) or not v[0].strip() or len(v[0]) > 20000 for v in answers.values()):
                raise ValueError("请回答所有问题")
            replies = [{"questionItemId": q["id"], "question": q["question"], "answer": answers[q["id"]][0]} for q in questions]
            text = "<send_user_message_question_reply>\n" + json.dumps(replies, ensure_ascii=False, separators=(",", ":")) + "\n</send_user_message_question_reply>"
            submission = str(uuid.uuid5(uuid.UUID(thread_id), request_id + text))
            with session.condition:
                active = session.state.get("threadRuntimeStatus", {}).get("type") == "active"
            return self.send(thread_id, text, submission, "steer" if active else "send")
        with session.condition:
            pending = next((r for r in session.state.get("requests", []) if str(r.get("id")) == str(request_id)), None)
            if not pending:
                raise ValueError("此请求已处理或已过期")
            pending = copy.deepcopy(pending)
        if not normalize_request(pending)["supported"]:
            raise ValueError("此类请求请在桌面 App 中处理")
        method = pending.get("method", "")
        params = pending.get("params", {})
        mapping = {
            "item/commandExecution/requestApproval": "thread-follower-command-approval-decision",
            "item/fileChange/requestApproval": "thread-follower-file-approval-decision",
            "item/permissions/requestApproval": "thread-follower-permissions-request-approval-response",
            "item/tool/requestUserInput": "thread-follower-submit-user-input",
            "tool/requestUserInput": "thread-follower-submit-user-input",
            "mcpServer/elicitation/request": "thread-follower-submit-mcp-server-elicitation-response",
        }
        if method not in mapping:
            raise ValueError("此类请求请在桌面 App 中处理")
        payload = {"requestId": pending["id"]}
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            decision = response.get("decision")
            if decision not in ("accept", "decline", "cancel"):
                raise ValueError("只支持本次批准、拒绝或取消")
            available = params.get("availableDecisions")
            if available and decision not in available:
                raise ValueError("该审批不支持所选操作")
            payload["decision"] = decision
        elif method == "item/permissions/requestApproval":
            if response.get("decision") not in ("accept", "decline"):
                raise ValueError("请选择批准或拒绝")
            payload["response"] = {"permissions": params.get("permissions", {}) if response["decision"] == "accept" else {}, "scope": "turn"}
        elif method in ("item/tool/requestUserInput", "tool/requestUserInput"):
            answers = response.get("answers")
            if not isinstance(answers, dict):
                raise ValueError("请填写问题答案")
            allowed_ids = {q["id"] for q in params.get("questions", [])}
            if not allowed_ids or set(answers) != allowed_ids or any(not isinstance(v, list) or not v or not all(isinstance(s, str) and len(s) <= 20000 for s in v) for v in answers.values()):
                raise ValueError("答案不完整或格式不正确")
            payload["response"] = {"answers": {k: {"answers": v} for k, v in answers.items()}}
        else:
            action = response.get("action")
            if action not in ("accept", "decline", "cancel"):
                raise ValueError("未知确认操作")
            content = response.get("content")
            if action == "accept":
                validate_form(content, params.get("requestedSchema", {}))
            payload["response"] = {"action": action, "content": content if action == "accept" else None}
        return self._call(session, mapping[method], payload)

    def close(self):
        self.closed.set()
        for bridge in list(self.remote_bridges.values()):
            bridge.close()
        with self.lock:
            sessions = list(self.live.values())
        for session in sessions:
            try:
                if session.connected:
                    self.ipc.follow(session.id, session.owner, False, host=self.host)
            except IPCError:
                pass
        self.ipc.close()


def validate_form(value, schema):
    """Validate the small JSON-schema form subset that the phone can render."""
    from .model import form_supported
    if not form_supported(schema):
        raise ValueError("此表单需要在桌面填写")
    if not isinstance(value, dict):
        raise ValueError("请填写表单")
    properties = schema.get("properties", {})
    if set(value) - set(properties) or set(schema.get("required", [])) - set(value):
        raise ValueError("表单字段不完整")
    for key, item in value.items():
        field = properties[key]
        kind = field.get("type", "string")
        valid = ((kind == "string" and isinstance(item, str)) or
                 (kind == "boolean" and isinstance(item, bool)) or
                 (kind in ("number", "integer") and type(item) in (int, float) and (kind != "integer" or int(item) == item)))
        if not valid or ("enum" in field and item not in field["enum"]):
            raise ValueError("表单字段格式不正确：" + key)
        if isinstance(item, str) and not field.get("minLength", 0) <= len(item) <= field.get("maxLength", 20000):
            raise ValueError("表单文本长度不正确：" + key)
        if type(item) in (int, float) and not field.get("minimum", float("-inf")) <= item <= field.get("maximum", float("inf")):
            raise ValueError("表单数值超出范围：" + key)
