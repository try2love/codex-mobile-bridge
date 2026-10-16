"""Read-only discovery and history for existing desktop chats."""
import copy
import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from contextlib import closing
from pathlib import Path


class SessionStore:
    def __init__(self, codex_home):
        self.home = Path(codex_home).resolve()
        self._receipt_cache = {}
        self._task_event_cache = {}
        self._message_time_cache = {}
        self._message_time_lock = threading.Lock()

    def _connect(self):
        databases = sorted(self.home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not databases:
            raise RuntimeError("找不到 Codex 会话数据库")
        conn = sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        return conn

    def list(self, query="", limit=100, offset=0, archived=False, working=False):
        with closing(self._connect()) as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
            fields = [name for name in ("id", "name", "title", "cwd", "updated_at", "updated_at_ms", "recency_at", "recency_at_ms", "model_provider", "model", "originator", "source", "archived", "is_pinned") if name in columns]
            if working:
                fields.append("rollout_path")
            where = ["archived = ?"]
            params = [int(archived)]
            if "originator" in columns:
                # Older desktop imports have no originator but retain their app source.
                where.append("(originator IN ('Codex Desktop', 'codex_work_desktop') OR (originator IS NULL AND source = 'vscode'))")
            if "thread_source" in columns:
                where.append("COALESCE(thread_source, '') != 'subagent'")
            if "source" in columns:
                where.append("COALESCE(source, '') NOT LIKE '%\"subagent\"%'")
            if query:
                search_fields = [name for name in ("name", "title", "cwd") if name in columns]
                where.append("(" + " OR ".join(name + " LIKE ?" for name in search_fields) + ")")
                params += ["%" + query + "%"] * len(search_fields)
            recency = "COALESCE(recency_at_ms, recency_at * 1000, updated_at * 1000)" if "recency_at_ms" in columns else "COALESCE(recency_at, updated_at)" if "recency_at" in columns else "updated_at"
            sql = "SELECT " + ",".join(fields) + " FROM threads WHERE " + " AND ".join(where) + " ORDER BY " + recency + " DESC, id DESC"
            if working:
                return [dict(row) for row in conn.execute(sql, params) if self._working_candidate(dict(row))]
            return [dict(row) for row in conn.execute(sql + " LIMIT ? OFFSET ?", params + [limit, offset])]

    def _working_candidate(self, row):
        return self.latest_task_event(row).get('type') == 'task_started'

    def latest_task_event(self, row):
        # Cache by file revision; completed tasks need no repeated history parsing.
        try:
            path = self._rollout_path(row if 'rollout_path' in row else self.get(row['id']))
            stat = path.stat()
            signature = (str(path), stat.st_mtime_ns, stat.st_size)
            cached = self._task_event_cache.get(row['id'])
            if cached and cached[0] == signature:
                return cached[1]
            with path.open('rb') as stream:
                stream.seek(0, 2)
                position, carry = stream.tell(), b''
                while position:
                    length = min(position, 65536)
                    position -= length
                    stream.seek(position)
                    lines = (stream.read(length) + carry).split(b'\n')
                    carry = lines.pop(0) if position else b''
                    for line in reversed(lines):
                        if b'event_msg' not in line:
                            continue
                        try:
                            record = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if record.get('type') != 'event_msg':
                            continue
                        kind = record.get('payload', {}).get('type')
                        if kind in ('task_started', 'task_complete', 'turn_aborted'):
                            event = {'type': kind, 'turnId': record.get('payload', {}).get('turn_id', ''), 'timestamp': record.get('timestamp', '')}
                            self._task_event_cache[row['id']] = (signature, event)
                            return event
            self._task_event_cache[row['id']] = (signature, {})
        except (OSError, ValueError, KeyError):
            pass
        return {}

    def get(self, thread_id):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if not row:
                raise KeyError("找不到这个桌面会话")
            result = dict(row)
            desktop = result.get("originator") in ("Codex Desktop", "codex_work_desktop") or (result.get("originator") is None and result.get("source") == "vscode")
            if not desktop or result.get("thread_source") == "subagent" or '"subagent"' in (result.get("source") or ""):
                raise KeyError("不是桌面 App 会话")
            return result

    def find_creation(self, request_id):
        import uuid
        marker = 'codex-mobile:' + str(uuid.UUID(request_id))
        with closing(self._connect()) as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            if 'thread_source' not in columns:
                return None
            rows = conn.execute('SELECT id FROM threads WHERE thread_source = ? LIMIT 2', (marker,)).fetchall()
        if len(rows) != 1:
            return None
        row = self.get(rows[0]['id'])
        return {'id': row['id'], 'title': row.get('name') or row.get('title') or '新对话', 'cwd': row['cwd']}

    def _rollout_path(self, meta):
        raw = str(meta["rollout_path"])
        # SQLite may store the Windows extended-length path spelling.
        if raw.startswith("\\\\?\\"):
            raw = raw[4:]
        resolved = Path(raw).resolve()
        if not any(root.resolve() in resolved.parents for root in (self.home / "sessions", self.home / "archived_sessions")):
            raise ValueError("会话记录路径不在 Codex 数据目录中")
        return resolved

    def delivery_receipts(self, thread_id):
        from .model import text_content
        from .images import attachment_summary
        path = self._rollout_path(self.get(thread_id))
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = self._receipt_cache.get(thread_id)
        if cached and cached[0] == stamp:
            return cached[1]
        receipts = []
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    payload = record.get('payload', {})
                    if record.get('type') != 'response_item' or payload.get('role') != 'user':
                        continue
                    at = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).timestamp()
                    content = payload.get('content', [])
                    images = [attachment_summary(item).get('imageId') for item in content
                              if item.get('type') in ('image', 'input_image')]
                    receipts.append({'at': at, 'text': text_content(content), 'images': images})
                except (ValueError, KeyError, TypeError):
                    continue
        self._receipt_cache[thread_id] = (stamp, receipts)
        return receipts

    def message_times(self, thread_id):
        # Read only appended complete records; streaming polls must not rescan the rollout.
        path = self._rollout_path(self.get(thread_id))
        with self._message_time_lock:
            stat = path.stat()
            stamp = (str(path), stat.st_mtime_ns, stat.st_size)
            cached = self._message_time_cache.get(thread_id)
            if cached and cached['stamp'] == stamp:
                return cached['snapshot']
            if (not cached or cached['stamp'][0] != str(path)
                    or stat.st_size <= cached['stamp'][2]):
                cached = {'stamp': stamp, 'offset': 0, 'turn': 'history', 'turns': {},
                          'revision': cached['revision'] + 1 if cached else 1}
            with path.open('rb') as stream:
                stream.seek(cached['offset'])
                while line := stream.readline():
                    if not line.endswith(b'\n'):
                        break
                    cached['offset'] = stream.tell()
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    payload = record.get('payload') or {}
                    kind, at = payload.get('type'), record.get('timestamp')
                    if record.get('type') == 'event_msg' and kind == 'task_started':
                        cached['turn'] = payload.get('turn_id') or 'history'
                    turn = cached['turns'].setdefault(cached['turn'], {})
                    if record.get('type') == 'event_msg' and kind == 'task_complete' and at:
                        target = cached['turns'].setdefault(payload.get('turn_id') or cached['turn'], {})
                        target['completedAt'] = at
                        cached['revision'] += 1
                    if record.get('type') != 'response_item' or not at:
                        continue
                    if kind == 'message' and payload.get('role') in ('user', 'assistant'):
                        role = payload['role']
                        content = payload.get('content', [])
                        text = content if isinstance(content, str) else '\n'.join(
                            str(x.get('text', '')) for x in content if isinstance(x, dict)
                            and x.get('type') in ('text', 'input_text', 'output_text', 'inputText'))
                    elif kind in ('agent_message', 'agentMessage'):
                        role, text = 'assistant', payload.get('text', '')
                    else:
                        continue
                    key = hashlib.sha256(text.encode()).hexdigest()
                    turn.setdefault(role, {}).setdefault(key, []).append(at)
                    cached['revision'] += 1
            cached['stamp'] = stamp
            cached['snapshot'] = (cached['revision'], copy.deepcopy(cached['turns']))
            self._message_time_cache[thread_id] = cached
            return cached['snapshot']

    def history(self, thread_id):
        meta = self.get(thread_id)
        resolved = self._rollout_path(meta)
        items, turns, current = [], [], None
        with resolved.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                payload = record.get("payload", {})
                if record.get("type") == "event_msg" and payload.get("type") == "task_started":
                    current = {"turnId": payload.get("turn_id"), "status": "inProgress", "items": [], "startedAt": record.get("timestamp")}
                    turns.append(current)
                    items = current["items"]
                elif record.get("type") == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
                    if current:
                        current["status"] = "completed" if payload["type"] == "task_complete" else "interrupted"
                        if payload["type"] == "task_complete":
                            current["completedAt"] = record.get("timestamp")
                elif record.get("type") == "response_item":
                    if current is None:
                        current = {"turnId": "history", "status": "completed", "items": []}
                        turns.append(current)
                        items = current["items"]
                    if payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
                        role = payload["role"]
                        if role == "user":
                            items.append({"id": str(len(items)), "type": "userMessage", "content": payload.get("content", []), "sentAt": record.get("timestamp")})
                        else:
                            items.append({"id": str(len(items)), "type": "agentMessage", "text": "\n".join(x.get("text", "") for x in payload.get("content", []) if isinstance(x, dict)), "phase": payload.get("phase"), "completedAt": record.get("timestamp")})
                    elif payload.get("type") in ("agent_message", "agentMessage"):
                        items.append({"id": payload.get("id", str(len(items))), "type": "agentMessage", "text": payload.get("text", ""), "completedAt": record.get("timestamp")})
                    elif payload.get("type") == "reasoning":
                        items.append({"id": payload.get("id") or str(len(items)), "type": "reasoning", "summary": payload.get("summary", [])})
                    elif payload.get("type") in ("function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"):
                        items.append({"id": payload.get("call_id", str(len(items))), "type": "storedToolEvent", **payload})
        return {"id": thread_id, "title": meta.get("name") or meta.get("title"), "cwd": meta["cwd"],
                "latestModel": meta.get("model"), "modelProvider": meta.get("model_provider"),
                "turns": turns, "requests": [], "threadRuntimeStatus": {"type": "notLoaded"}}
