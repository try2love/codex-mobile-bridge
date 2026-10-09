"""Read-only discovery and history for existing desktop chats."""
import copy
import threading
from collections import OrderedDict
import json
import logging
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager, ExitStack
from pathlib import Path


class StoreUnavailable(RuntimeError):
    pass


class SessionStore:
    def __init__(self, codex_home):
        self.home = Path(codex_home).resolve()
        self.history_cache = OrderedDict()
        self.history_lock = threading.Lock()

    @contextmanager
    def _connect(self):
        database = None
        try:
            databases = sorted(self.home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not databases:
                raise StoreUnavailable("暂时找不到 Codex 会话数据库，请打开电脑 Codex 后重试。")
            database = databases[0]
            with ExitStack() as stack:
                target = database
                wal = Path(str(database) + '-wal')
                with database.open('rb') as source:
                    wal_mode = source.read(20)[18:20] == b'\x02\x02'
                if wal_mode and not wal.exists():
                    # macOS SQLite can fail on a read-only WAL database with no
                    # sidecars. Read a stable, private copy; never initialize or
                    # mark the live database immutable, which would miss WAL data.
                    def fingerprint():
                        stat = database.stat()
                        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
                    before = fingerprint()
                    folder = stack.enter_context(tempfile.TemporaryDirectory(prefix='codex-mobile-history-'))
                    target = Path(folder) / 'history.sqlite'
                    if wal.exists():
                        raise StoreUnavailable("Codex 会话数据库正在更新，请稍后重试。")
                    shutil.copyfile(database, target)
                    if wal.exists() or fingerprint() != before:
                        raise StoreUnavailable("Codex 会话数据库正在更新，请稍后重试。")
                mode = 'ro' if target == database else 'rw'
                conn = stack.enter_context(closing(sqlite3.connect(target.as_uri() + '?mode=' + mode, uri=True, timeout=3)))
                conn.row_factory = sqlite3.Row
                yield conn
        except (OSError, sqlite3.Error) as exc:
            logging.getLogger(__name__).warning('Session database read failed: database=%s sqlite=%s wal=%s shm=%s error=%s',
                database, getattr(exc, 'sqlite_errorname', type(exc).__name__),
                bool(database and Path(str(database) + '-wal').exists()),
                bool(database and Path(str(database) + '-shm').exists()), exc)
            raise StoreUnavailable("暂时无法读取 Codex 会话数据库，请稍后重试或重新打开电脑 Codex。") from exc

    def list(self, query="", limit=100, offset=0, archived=False):
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
            fields = [name for name in ("id", "name", "title", "cwd", "updated_at", "updated_at_ms", "recency_at", "recency_at_ms", "model_provider", "model", "originator", "source", "archived", "is_pinned") if name in columns]
            where = ["archived = ?"]
            params = [int(archived)]
            if "originator" in columns:
                # Older desktop imports have no originator but retain their app source.
                where.append("(originator IN ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge') OR (originator IS NULL AND source = 'vscode'))")
            if "thread_source" in columns:
                where.append("COALESCE(thread_source, '') != 'subagent'")
            if "source" in columns:
                where.append("COALESCE(source, '') NOT LIKE '%\"subagent\"%'")
            if query:
                search_fields = [name for name in ("name", "title", "cwd") if name in columns]
                where.append("(" + " OR ".join(name + " LIKE ?" for name in search_fields) + ")")
                params += ["%" + query + "%"] * len(search_fields)
            recency = "COALESCE(recency_at_ms, recency_at * 1000, updated_at * 1000)" if "recency_at_ms" in columns else "COALESCE(recency_at, updated_at)" if "recency_at" in columns else "updated_at"
            rows = conn.execute("SELECT " + ",".join(fields) + " FROM threads WHERE " + " AND ".join(where) + " ORDER BY " + recency + " DESC, id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            return [dict(row) for row in rows]

    def notification_changes(self, since=0):
        """Only metadata changed since discovery; never read rollout history."""
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            clocks = [name + ('' if name.endswith('_ms') else ' * 1000')
                      for name in ('updated_at', 'updated_at_ms', 'recency_at', 'recency_at_ms') if name in columns]
            clock = 'MAX(' + ','.join('COALESCE(' + c + ',0)' for c in clocks) + ',0)'
            owner = "(originator IN ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge') OR (originator IS NULL AND source = 'vscode'))" if 'originator' in columns else '1'
            filters = [owner, clock + ' >= ?']
            if 'thread_source' in columns:
                filters.append("COALESCE(thread_source, '') != 'subagent'")
            if 'source' in columns:
                filters.append("COALESCE(source, '') NOT LIKE '%\"subagent\"%'")
            return [dict(row) for row in conn.execute('SELECT id, ' + clock +
                    ' AS changed_at FROM threads WHERE ' + ' AND '.join(filters), (since,))]

    def lifecycle_threads(self):
        """Desktop roots and their complete agent subtree, using metadata only."""
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            fields = [name for name in ('id', 'originator', 'source') if name in columns]
            rows = [dict(row) for row in conn.execute('SELECT ' + ','.join(fields) + ' FROM threads')]
        desktop = {row['id'] for row in rows if 'originator' not in columns
                   or row.get('originator') in ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge')
                   or (row.get('originator') is None and row.get('source') == 'vscode')}
        children = {}
        for row in rows:
            parent = self._agent_source(row).get('parent_thread_id')
            if isinstance(parent, str):
                children.setdefault(parent, []).append(row['id'])
        pending = list(desktop)
        while pending:
            for child in children.get(pending.pop(), []):
                if child not in desktop:
                    desktop.add(child)
                    pending.append(child)
        return [{'id': identifier} for identifier in sorted(desktop)]

    def recencies(self, identifiers):
        if not identifiers:
            return {}
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            fields = [name for name in ('recency_at_ms', 'recency_at', 'updated_at_ms', 'updated_at') if name in columns]
            rows = conn.execute('SELECT id,' + ','.join(fields) + ' FROM threads WHERE id IN (' + ','.join('?' for _ in identifiers) + ')', identifiers)
            return {r['id']: next((r[k] * (1 if k.endswith('_ms') else 1000) for k in fields if r[k]), 0) for r in rows}

    def get(self, thread_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if not row:
                raise KeyError("找不到这个桌面会话")
            result = dict(row)
            desktop = result.get("originator") in ("Codex Desktop", "codex_work_desktop", "codex_mobile_bridge") or (result.get("originator") is None and result.get("source") == "vscode")
            if not desktop or result.get("thread_source") == "subagent" or '"subagent"' in (result.get("source") or ""):
                raise KeyError("不是桌面 App 会话")
            return result

    @staticmethod
    def _recent_lines(path, turn_limit):
        """Read complete turns backwards without parsing the older rollout prefix."""
        if turn_limit is None:
            return path.read_bytes(), True
        with path.open('rb') as stream:
            stream.seek(0, 2)
            position = stream.tell()
            parts, pending, starts = [], b'', 0
            while position:
                size = min(position, 65536)
                position -= size
                stream.seek(position)
                chunk = stream.read(size) + pending
                lines = chunk.splitlines(keepends=True)
                pending = lines.pop(0) if position else b''
                for line in reversed(lines):
                    parts.append(line)
                    if b'"task_started"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get('type') == 'event_msg' and record.get('payload', {}).get('type') == 'task_started':
                        starts += 1
                        if starts >= turn_limit:
                            return b''.join(reversed(parts)), position == 0 and len(parts) == len(lines)
            return b''.join(reversed(parts)), True

    def history(self, thread_id, turn_limit=None):
        return self._history(thread_id, self.get(thread_id), turn_limit)

    @staticmethod
    def _agent_source(meta):
        try:
            source = json.loads(meta.get('source') or '{}')
            spawn = source.get('subagent', {}).get('thread_spawn', {})
            return spawn if isinstance(spawn, dict) else {}
        except (ValueError, AttributeError):
            return {}

    def subagents(self, thread_id):
        self.get(thread_id)  # Only a desktop parent can authorize this subtree.
        result, pending, seen = [], [thread_id], {thread_id}
        with self._connect() as conn:
            while pending and len(result) < 128:
                parent = pending.pop(0)
                rows = conn.execute("SELECT * FROM threads WHERE source LIKE ? ORDER BY updated_at DESC LIMIT 129", ('%' + parent + '%',))
                for row in rows:
                    meta = dict(row); spawn = self._agent_source(meta)
                    if spawn.get('parent_thread_id') != parent or meta['id'] in seen: continue
                    seen.add(meta['id']); pending.append(meta['id'])
                    result.append({'id': meta['id'], 'parentId': parent,
                                   'title': meta.get('name') or meta.get('title') or '子智能体',
                                   'nickname': meta.get('agent_nickname') or spawn.get('agent_nickname'),
                                   'role': meta.get('agent_role') or spawn.get('agent_role'),
                                   'path': meta.get('agent_path') or spawn.get('agent_path'),
                                   'updated': meta.get('updated_at'), 'archived': bool(meta.get('archived'))})
                    if len(result) >= 128: break
        return {'agents': result, 'limited': len(result) >= 128}

    def subagent_history(self, thread_id, agent_id):
        self.get(thread_id)
        with self._connect() as conn:
            current = agent_id; seen = set(); target = None
            while current != thread_id:
                if current in seen or len(seen) >= 32: raise KeyError('子智能体不属于此聊天')
                seen.add(current)
                row = conn.execute('SELECT * FROM threads WHERE id = ?', (current,)).fetchone()
                if not row: raise KeyError('子智能体不存在')
                meta = dict(row)
                if target is None: target = meta
                current = self._agent_source(meta).get('parent_thread_id')
                if not current: raise KeyError('子智能体不属于此聊天')
        if target is None: raise KeyError('请选择子智能体')
        return self._history(agent_id, target, 10)

    def _history(self, thread_id, meta, turn_limit=None):
        path = Path(meta["rollout_path"])
        # The file path must be an actual rollout in this user's Codex home.
        resolved = path.resolve()
        if not any(root.resolve() in resolved.parents for root in (self.home / "sessions", self.home / "archived_sessions")):
            raise ValueError("会话记录路径不在 Codex 数据目录中")
        stat = resolved.stat()
        cache_key = (str(resolved), stat.st_ino, stat.st_size, stat.st_mtime_ns, turn_limit)
        with self.history_lock:
            cached = self.history_cache.get(cache_key)
            if cached is not None:
                self.history_cache.move_to_end(cache_key)
                result = copy.deepcopy(cached)
                result.update(title=meta.get('name') or meta.get('title'), cwd=meta['cwd'])
                result['modelProvider'] = meta.get('model_provider')
                if meta.get('model'):
                    result['latestModel'] = meta['model']
                return result
        raw, complete = self._recent_lines(resolved, turn_limit)
        items, turns, current = [], [], None
        for line in raw.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            payload = record.get("payload", {})
            if record.get("type") == "event_msg" and payload.get("type") == "task_started":
                current = {"turnId": payload.get("turn_id"), "status": "inProgress", "items": []}
                turns.append(current)
                items = current["items"]
            elif record.get("type") == "turn_context" and current is not None:
                settings = (payload.get('collaboration_mode') or {}).get('settings') or {}
                current['params'] = {'model':payload.get('model') or settings.get('model'),
                                     'effort':payload.get('effort') or settings.get('reasoning_effort')}
            elif record.get("type") == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
                if current:
                    current["status"] = "completed" if payload["type"] == "task_complete" else "interrupted"
            elif record.get("type") == "event_msg" and payload.get("type") == "item_completed":
                item = payload.get("item", {})
                if isinstance(item, dict) and item.get("type") in ("ImageView", "imageView"):
                    # ImageView evidence only exists in event records. Preserve it
                    # so saved history can still render the image after restart.
                    if current is None:
                        current = {"turnId": "history", "status": "completed", "items": []}
                        turns.append(current)
                        items = current["items"]
                    items.append({"id": item.get("id", str(len(items))), "type": "ImageView", "path": item.get("path", "")})
            elif record.get("type") == "response_item":
                if current is None:
                    current = {"turnId": "history", "status": "completed", "items": []}
                    turns.append(current)
                    items = current["items"]
                if payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
                    role = payload["role"]
                    if role == "user":
                        items.append({"id": str(len(items)), "type": "userMessage", "content": payload.get("content", [])})
                    else:
                        items.append({"id": str(len(items)), "type": "agentMessage", "text": "\n".join(x.get("text", "") for x in payload.get("content", []) if isinstance(x, dict)), "phase": payload.get("phase")})
                elif payload.get("type") in ("agent_message", "agentMessage"):
                    items.append({"id": payload.get("id", str(len(items))), "type": "agentMessage", "text": payload.get("text", "")})
                elif payload.get("type") in ("function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"):
                    items.append({"id": payload.get("call_id", str(len(items))), "type": "storedToolEvent", **payload})
        result = {"id": thread_id, "title": meta.get("name") or meta.get("title"), "cwd": meta["cwd"],
                "latestModel": meta.get('model') or next((t['params']['model'] for t in reversed(turns) if (t.get('params') or {}).get('model')), None),
                "latestReasoningEffort": next((t['params']['effort'] for t in reversed(turns) if (t.get('params') or {}).get('effort')), None),
                "modelProvider": meta.get("model_provider"),
                "turns": turns, "turnsPagination": {"hasLoadedOldest": complete}, "requests": [], "threadRuntimeStatus": {"type": "notLoaded"}}
        with self.history_lock:
            if len(raw) <= 4 * 1024 * 1024:
                self.history_cache[cache_key] = result
            while len(self.history_cache) > 8:
                self.history_cache.popitem(last=False)
        return copy.deepcopy(result)
