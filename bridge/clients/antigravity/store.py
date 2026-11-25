"""Read-only SQLite summary index and JSONL transcript reader for Antigravity."""
import base64
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import unquote, urlsplit

from bridge.features.workspace.uploads import MAX_FILE, image_type
from .model import SURFACE_LABELS, compute_turn_fork_points, extract_user_text, gateway_id, normalize_steps, revert_message_id

SURFACE_DIRS = (
    ('app', '.gemini/antigravity'),
    ('ide', '.gemini/antigravity-ide'),
    ('cli', '.gemini/antigravity-cli'),
)

_FRACTION_RE = re.compile(r'\.(\d+)')
_MAX_FULL_FIELD_CHARS = 65536
_MAX_ANNOTATION_BYTES = 65536
_TRANSCRIPT_CACHE_TTL = 1.0
_PBTXT_ESCAPE_RE = re.compile(r'\\([0-7]{1,3}|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[abfnrtv\\\'"])')
_PBTXT_SIMPLE_ESCAPES = {
    'a': b'\a', 'b': b'\b', 'f': b'\f', 'n': b'\n', 'r': b'\r', 't': b'\t', 'v': b'\v',
    '\\': b'\\', "'": b"'", '"': b'"',
}


def _decode_pbtxt_string(raw):
    """Decode a protobuf text-format string literal (UTF-8 or octal/hex/C escapes)."""
    out = bytearray()
    pos = 0
    for match in _PBTXT_ESCAPE_RE.finditer(raw):
        out.extend(raw[pos:match.start()].encode('utf-8'))
        esc = match.group(1)
        if esc in _PBTXT_SIMPLE_ESCAPES:
            out.extend(_PBTXT_SIMPLE_ESCAPES[esc])
        elif esc.startswith('x'):
            out.append(int(esc[1:], 16))
        elif esc.startswith(('u', 'U')):
            out.extend(chr(int(esc[1:], 16)).encode('utf-8', errors='replace'))
        else:
            out.append(int(esc, 8) & 0xFF)
        pos = match.end()
    out.extend(raw[pos:].encode('utf-8'))
    return out.decode('utf-8', errors='replace')


def read_annotation_pbtxt(surface_root, conversation_id):
    """Read top-level title/archived metadata from annotations/<cid>.pbtxt safely."""
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(conversation_id or '')):
        return {}
    path = Path(surface_root) / 'annotations' / f'{conversation_id}.pbtxt'
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        if path.stat().st_size > _MAX_ANNOTATION_BYTES:
            return {}
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return {}
    result = {}
    depth = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if depth == 0:
            title_match = re.match(r'^title\s*:\s*"((?:[^"\\]|\\.)*)"\s*$', line)
            if title_match:
                result['title'] = _decode_pbtxt_string(title_match.group(1)).strip()
            else:
                arch_match = re.match(r'^archived\s*:\s*(true|false)\b', line, re.IGNORECASE)
                if arch_match:
                    result['archived'] = arch_match.group(1).lower() == 'true'
        # Track nested proto blocks while ignoring braces inside quoted strings.
        cleaned = re.sub(r'"(?:[^"\\]|\\.)*"', '', line)
        depth = max(0, depth + cleaned.count('{') - cleaned.count('}'))
    return result


def parse_timestamp(value):
    """Parse Antigravity SQLite/ISO timestamps (including nanosecond precision) to epoch seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return 0.0
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    text = _FRACTION_RE.sub(lambda m: '.' + (m.group(1)[:6]).ljust(6, '0'), text, count=1)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def local_file_path(uri, *, windows=None):
    """Decode a local file URI once, retaining Windows drive and UNC identity."""
    parsed = urlsplit(uri)
    if parsed.scheme != 'file' or parsed.query or parsed.fragment:
        return ''
    if windows is None:
        windows = os.name == 'nt'
    if windows:
        from nturl2path import url2pathname as windows_path
        return windows_path(('//' + parsed.netloc if parsed.netloc else '') + parsed.path)
    if parsed.netloc not in ('', 'localhost'):
        return ''
    return unquote(parsed.path)


def parse_workspace_uris(raw):
    """Extract the primary local workspace path from workspace_uris JSON."""
    if not isinstance(raw, str) or not raw.strip():
        return ''
    try:
        uris = json.loads(raw)
    except ValueError:
        uris = [raw.strip()]
    if not isinstance(uris, list):
        return ''
    for item in uris:
        if not isinstance(item, str) or not item.strip():
            continue
        item = item.strip()
        if item.startswith('file://'):
            path = local_file_path(item)
            if path:
                return str(Path(path))
        elif os.path.isabs(item):
            return str(Path(item))
    return ''


def _cap_large_fields(step):
    """Bound oversized fields from transcript_full.jsonl so mobile browsers stay responsive."""
    if not isinstance(step, dict):
        return step
    copied = dict(step)
    for key in ('content', 'thinking'):
        val = copied.get(key)
        if isinstance(val, str) and len(val) > _MAX_FULL_FIELD_CHARS:
            copied[key] = val[:_MAX_FULL_FIELD_CHARS] + '\n…（内容过长，已在手机端截断预览）'
    return copied


class AntigravityStore:
    """Discover and read local Antigravity surfaces without mutating state."""

    def __init__(self, scratch_dir, home=None):
        self.scratch_dir = Path(scratch_dir)
        self.home = Path(home).expanduser().resolve() if home else Path.home().resolve()
        self._transcript_cache = OrderedDict()
        self._cache_lock = threading.Lock()

    def surfaces(self):
        """Return existing surface directories in priority order (2.0 -> IDE -> CLI)."""
        found = []
        seen = set()
        for key, rel in SURFACE_DIRS:
            root = (self.home / rel).resolve()
            if root in seen:
                continue
            if (root / 'conversation_summaries.db').is_file() or (root / 'brain').is_dir():
                seen.add(root)
                found.append({'key': key, 'name': SURFACE_LABELS[key], 'root': root})
        return found

    def surface_root(self, key='app'):
        for item in self.surfaces():
            if item['key'] == key:
                return item['root']
        rel = dict(SURFACE_DIRS).get(key, '.gemini/antigravity')
        return (self.home / rel).resolve()

    @staticmethod
    def _read_db(db_path, read, default, *, strict=False):
        if not db_path.is_file():
            return default
        for attempt in range(3):
            try:
                conn = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.5)
                try:
                    conn.row_factory = sqlite3.Row
                    return read(conn)
                finally:
                    conn.close()
            except sqlite3.DatabaseError as exc:
                code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
                transient = isinstance(exc, sqlite3.OperationalError) and (
                    code in (5, 6, 14) or str(exc).lower() in (
                        'database is locked', 'database table is locked', 'unable to open database file'))
                if transient and attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                if strict:
                    raise
                # Unavailable native state is never evidence of idle. Do not
                # combine independently copied main/WAL/SHM files.
                return default

    def _query_db(self, db_path, sql, params=(), *, strict=False):
        return self._read_db(db_path, lambda conn: [dict(row) for row in conn.execute(sql, params)],
                             [], strict=strict)

    @staticmethod
    def _backend_for(dir_key, source):
        if dir_key == 'ide':
            return 'ide'
        if dir_key == 'cli' or source == 'CORTEX_TRAJECTORY_SOURCE_CLI':
            return 'cli'
        return 'app'

    def _query_summaries_with_archived(self, db_path, sql, params=()):
        def read(conn):
            rows = [dict(row) for row in conn.execute(sql, params)]
            ids = [row['conversation_id'] for row in rows]
            fts_archived = set()
            try:
                if ids:
                    fts_rows = conn.execute(
                        'SELECT DISTINCT conversation_id FROM conversations_fts WHERE archived = 1 '
                        'AND conversation_id IN (' + ','.join('?' for _ in ids) + ')', ids)
                    fts_archived = {str(r[0]).strip() for r in fts_rows if r[0]}
            except sqlite3.OperationalError as exc:
                if 'no such table' not in str(exc) and 'no such column' not in str(exc):
                    raise
            return rows, fts_archived
        return self._read_db(db_path, read, ([], set()))

    def _row_to_summary(self, dir_key, root, row, *, fts_archived=()):
        cid = str(row.get('conversation_id') or '').strip()
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', cid):
            return None
        sid = gateway_id(dir_key, cid)
        cwd = parse_workspace_uris(row.get('workspace_uris') or '')
        annotation = read_annotation_pbtxt(root, cid)
        title = str(annotation.get('title') or row.get('title') or '').strip()
        if not title:
            preview = extract_user_text(str(row.get('preview') or '')).splitlines()
            title = (preview[0][:80] if preview and preview[0].strip() else '') or f'聊天 {cid[:8]}'
        archived = annotation['archived'] if isinstance(annotation.get('archived'), bool) else (cid in fts_archived)
        updated_at = parse_timestamp(row.get('last_modified_time'))
        active = bool(row.get('not_fully_idle')) or str(row.get('status') or '').endswith('_RUNNING')
        backend = self._backend_for(dir_key, str(row.get('source') or ''))
        project_name = Path(cwd).name if cwd else SURFACE_LABELS.get(dir_key, 'Antigravity')
        return {
            'id': sid,
            'nativeId': cid,
            'surfaceDirKey': dir_key,
            'surfaceRoot': str(root),
            'provider': 'antigravity',
            'host': 'local',
            'backend': backend,
            'title': title,
            'cwd': cwd,
            'projectId': cwd or f'{dir_key}:unassigned',
            'projectName': project_name,
            'updatedAt': updated_at,
            'archived': archived,
            'status': 'active' if active else 'idle',
            'notFullyIdle': bool(row.get('not_fully_idle')),
            'stepCount': int(row.get('step_count') or 0),
            'runtimeKnown': True,
        }

    def list_sessions(self):
        """Return all top-level conversations across discovered surfaces."""
        sessions = []
        seen_native = set()
        sql = (
            'SELECT conversation_id, title, preview, step_count, last_modified_time, '
            'workspace_uris, status, source, project_id, parent_conversation_id, '
            'nesting_depth, not_fully_idle, killed '
            'FROM conversation_summaries '
            'WHERE nesting_depth = 0 AND (parent_conversation_id IS NULL OR parent_conversation_id = "") '
            'ORDER BY last_modified_time DESC LIMIT 300'
        )
        for surface in self.surfaces():
            db_path = surface['root'] / 'conversation_summaries.db'
            rows, fts_archived = self._query_summaries_with_archived(db_path, sql)
            for row in rows:
                cid = str(row.get('conversation_id') or '').strip()
                if not cid or (surface['root'], cid) in seen_native:
                    continue
                seen_native.add((surface['root'], cid))
                summary = self._row_to_summary(surface['key'], surface['root'], row, fts_archived=fts_archived)
                if summary is not None:
                    sessions.append(summary)
        sessions.sort(key=lambda item: item.get('updatedAt', 0), reverse=True)
        return sessions

    def resolve_session(self, sid):
        """Find a conversation by its source-scoped gateway UUID."""
        if not isinstance(sid, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', sid):
            return None
        for item in self.list_sessions():
            if item['id'] == sid:
                return item
        # Gateway IDs are UUIDv5 identities, not reversible native IDs. Scan only
        # IDs in bounded batches, then read the matching full summary by its PK.
        for surface in self.surfaces():
            db_path = surface['root'] / 'conversation_summaries.db'
            def find_id(conn):
                cursor = conn.execute('SELECT conversation_id FROM conversation_summaries '
                                      'WHERE nesting_depth = 0 AND '
                                      '(parent_conversation_id IS NULL OR parent_conversation_id = "")')
                while True:
                    batch = cursor.fetchmany(128)
                    if not batch:
                        return None
                    for row in batch:
                        if gateway_id(surface['key'], row[0]) == sid:
                            return row[0]
            cid = self._read_db(db_path, find_id, None)
            if cid:
                rows, archived = self._query_summaries_with_archived(
                    db_path, 'SELECT * FROM conversation_summaries WHERE conversation_id = ? '
                    'AND nesting_depth = 0 AND (parent_conversation_id IS NULL OR parent_conversation_id = "")', (cid,))
                if rows:
                    return self._row_to_summary(surface['key'], surface['root'], rows[0], fts_archived=archived)
        # Also check brain/<cid> directory if session was just created before summary flush
        for surface in self.surfaces():
            for candidate_dir in (surface['root'] / 'brain').glob('*') if (surface['root'] / 'brain').is_dir() else ():
                cid = candidate_dir.name
                if not re.fullmatch(r'[0-9a-fA-F-]{36}', cid) or candidate_dir.is_symlink():
                    continue
                if gateway_id(surface['key'], cid) == sid:
                    transcript = candidate_dir / '.system_generated/logs/transcript.jsonl'
                    mtime = transcript.stat().st_mtime if transcript.is_file() else 0.0
                    annotation = read_annotation_pbtxt(surface['root'], cid)
                    return {
                        'id': gateway_id(surface['key'], cid),
                        'nativeId': cid,
                        'surfaceDirKey': surface['key'],
                        'surfaceRoot': str(surface['root']),
                        'provider': 'antigravity',
                        'host': 'local',
                        'backend': surface['key'],
                        'title': str(annotation.get('title') or '').strip() or f'聊天 {cid[:8]}',
                        'cwd': '',
                        'projectId': f'{surface["key"]}:unassigned',
                        'projectName': surface['name'],
                        'updatedAt': mtime,
                        'archived': bool(annotation.get('archived')),
                        'status': 'idle',
                        'notFullyIdle': False,
                        'stepCount': 0,
                        'runtimeKnown': False,
                    }
        return None

    def is_native_busy(self, surface_root, conversation_id):
        """Check fresh SQLite not_fully_idle flag before launching cold CLI resume."""
        db_path = Path(surface_root) / 'conversation_summaries.db'
        rows = self._query_db(
            db_path,
            'SELECT not_fully_idle, status FROM conversation_summaries WHERE conversation_id = ? LIMIT 1',
            (conversation_id,), strict=True,
        )
        if not rows or rows[0].get('not_fully_idle') is None:
            raise ValueError('无法核验原生会话是否空闲，请在电脑检查会话')
        row = rows[0]
        if row['not_fully_idle'] or str(row.get('status') or '').endswith('_RUNNING'):
            return True
        if str(row.get('status') or '').endswith('_IDLE'):
            return False
        raise ValueError('无法核验原生会话是否空闲，请在电脑检查会话')

    def list_tasks(self, surface_root, conversation_id, *, live=False):
        """Return subagent child conversations for AgentsPanel."""
        db_path = Path(surface_root) / 'conversation_summaries.db'
        rows = self._query_db(
            db_path,
            'SELECT conversation_id, title, agent_name, status, not_fully_idle, killed, last_modified_time '
            'FROM conversation_summaries WHERE parent_conversation_id = ? '
            'ORDER BY last_modified_time DESC LIMIT 128',
            (conversation_id,),
        )
        tasks = []
        for row in rows:
            cid = str(row.get('conversation_id') or '')
            if not cid:
                continue
            raw_status = str(row.get('status') or '')
            if row.get('killed'):
                status = 'killed'
            elif row.get('not_fully_idle') or raw_status.endswith('_RUNNING'):
                status = 'running'
            elif 'ERROR' in raw_status or 'FAIL' in raw_status:
                status = 'failed'
            else:
                status = 'completed'
            title = str(row.get('title') or row.get('agent_name') or f'子代理 {cid[:8]}').strip()
            tasks.append({
                'id': cid,
                'title': title,
                'status': status,
                'cancellable': bool(live and status == 'running'),
            })
        return tasks

    @staticmethod
    def _load_reconciled_steps(compact_path):
        steps = []
        with compact_path.open('r', encoding='utf-8', errors='replace') as stream:
            for idx, line in enumerate(stream):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                step_idx = obj.get('step_index')
                if not isinstance(step_idx, int):
                    step_idx = idx
                    obj['step_index'] = step_idx
                # Branch reconciliation: if new step index is <= last step index,
                # the transcript rewound or branched. Prune the earlier abandoned steps.
                while steps and steps[-1].get('step_index', 0) >= step_idx:
                    steps.pop()
                steps.append(obj)
        return steps

    def valid_fork_steps(self, surface_root, conversation_id):
        """Return valid native turn-end step indices eligible for ForkConversation."""
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(conversation_id or '')):
            return set()
        conv_dir = Path(surface_root) / 'brain' / conversation_id
        if conv_dir.is_symlink():
            return set()
        compact_path = conv_dir / '.system_generated' / 'logs' / 'transcript.jsonl'
        if not compact_path.is_file():
            return set()
        all_steps = self._load_reconciled_steps(compact_path)
        points = compute_turn_fork_points(all_steps, session_active=False)
        messages, _, _ = normalize_steps(all_steps, session_active=False, turn_fork_points=points)
        return {int(msg['forkMessageId']) for msg in messages if isinstance(msg.get('forkMessageId'), str) and msg['forkMessageId'].isdigit()}

    def revert_anchor(self, surface_root, conversation_id, step_index):
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(conversation_id or '')):
            raise ValueError('原生会话路径无效')
        directory = Path(surface_root) / 'brain' / conversation_id
        if directory.is_symlink():
            raise ValueError('原生会话路径无效')
        # This is write authorization, so never read the display-page cache.
        for step in self._load_reconciled_steps(directory / '.system_generated/logs/transcript.jsonl'):
            if step.get('step_index') == step_index and step.get('type') == 'USER_INPUT':
                return revert_message_id(step)
        raise ValueError('目标不是当前原会话的用户消息')

    def read_transcript_page(self, surface_root, conversation_id, *, limit=40, before=None, session_active=False):
        """Read a reverse-paginated slice of transcript.jsonl with targeted full-line hydration."""
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(conversation_id or '')) or (Path(surface_root) / 'brain' / conversation_id).is_symlink():
            raise ValueError('原生会话路径无效')
        logs_dir = Path(surface_root) / 'brain' / conversation_id / '.system_generated' / 'logs'
        key = (str(logs_dir), int(limit or 40), '' if before is None else str(before), bool(session_active))
        started = time.monotonic()
        def signature():
            result = []
            for name in ('transcript.jsonl', 'transcript_full.jsonl'):
                try:
                    stat = (logs_dir / name).stat()
                    result.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
                except OSError:
                    result.append(None)
            return tuple(result)
        stamp = signature()
        with self._cache_lock:
            for old_key in list(self._transcript_cache):
                if old_key[0] == key[0] and self._transcript_cache[old_key][0] != stamp:
                    del self._transcript_cache[old_key]
            cached = self._transcript_cache.get(key)
            if cached and started - cached[2] < _TRANSCRIPT_CACHE_TTL and not (Path(surface_root) / 'brain' / conversation_id).is_symlink():
                self._transcript_cache.move_to_end(key)
                return deepcopy(cached[1])
        result = self._read_transcript_page(surface_root, conversation_id, limit=limit,
                                            before=before, session_active=session_active)
        if stamp[0] is not None and stamp == signature():
            with self._cache_lock:
                self._transcript_cache[key] = (stamp, deepcopy(result), started)
                self._transcript_cache.move_to_end(key)
                while len(self._transcript_cache) > 16:
                    self._transcript_cache.popitem(last=False)
        return result

    def _read_transcript_page(self, surface_root, conversation_id, *, limit=40, before=None, session_active=False):
        logs_dir = Path(surface_root) / 'brain' / conversation_id / '.system_generated' / 'logs'
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', conversation_id) or (Path(surface_root)/'brain'/conversation_id).is_symlink():
            raise ValueError('原生会话路径无效')
        compact_path = logs_dir / 'transcript.jsonl'
        full_path = logs_dir / 'transcript_full.jsonl'
        if not compact_path.is_file():
            return {
                'messages': [],
                'turns': [],
                'history': {'hasMore': False, 'before': None},
                'contextUsage': None,
            }

        before_index = None
        if before is not None and str(before).strip() != '':
            try:
                before_index = int(str(before).strip())
            except ValueError as exc:
                raise ValueError('历史游标格式无效') from exc

        all_steps = self._load_reconciled_steps(compact_path)
        turn_fork_points = compute_turn_fork_points(all_steps, session_active=session_active)
        steps = [s for s in all_steps if before_index is None or s['step_index'] < before_index]

        # Prefer six complete turns within the page budget. A single long turn
        # must still paginate; its earlier prompt remains available in older pages.
        target_steps = max(int(limit or 40), 120)
        start_pos = max(0, len(steps) - target_steps)
        user_positions = [i for i, s in enumerate(steps) if s.get('type') == 'USER_INPUT']
        if user_positions:
            min_turn_pos = user_positions[-min(6, len(user_positions))]
            start_pos = min(start_pos, min_turn_pos)
            # Snap backward to the nearest USER_INPUT boundary
            earlier_users = [pos for pos in user_positions if pos <= start_pos]
            if earlier_users:
                start_pos = earlier_users[-1]
        start_pos = max(0, len(steps) - 260, start_pos)
        if start_pos > 0:
            earlier_users = [pos for pos in user_positions if len(steps) - 260 <= pos <= start_pos]
            if earlier_users:
                start_pos = earlier_users[-1]

        has_more = start_pos > 0
        page = steps[start_pos:] if has_more else steps
        next_before = str(page[0]['step_index']) if has_more and page else None

        truncated_indices = {
            step['step_index']
            for step in page
            if isinstance(step.get('truncated_fields'), list) and step['truncated_fields']
        }
        if truncated_indices and full_path.is_file():
            hydrated = {}
            with full_path.open('r', encoding='utf-8', errors='replace') as stream:
                for idx, line in enumerate(stream):
                    if '"step_index"' not in line and idx not in truncated_indices:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    step_idx = obj.get('step_index', idx)
                    if step_idx in truncated_indices:
                        hydrated[step_idx] = _cap_large_fields(obj)
            if hydrated:
                page = [hydrated.get(step['step_index'], step) for step in page]

        messages, turns, usage = normalize_steps(
            page,
            session_active=session_active and before_index is None,
            turn_fork_points=turn_fork_points,
        )
        return {
            'messages': messages,
            'turns': turns,
            'history': {'hasMore': has_more, 'before': next_before},
            'contextUsage': usage,
        }

    @staticmethod
    def _extract_image_uri(jsonl_path, target_step, target_media):
        active = []
        with jsonl_path.open('r', encoding='utf-8', errors='replace') as stream:
            for idx, line in enumerate(stream):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                step_idx = obj.get('step_index', idx)
                while active and active[-1][0] >= step_idx:
                    active.pop()
                uri = None
                if step_idx == target_step:
                    media = obj.get('media')
                    if isinstance(media, list) and target_media < len(media) and isinstance(media[target_media], dict):
                        uri = str(media[target_media].get('uri') or '')
                active.append((step_idx, uri))
        for step_idx, uri in active:
            if step_idx == target_step and uri:
                return uri
        return None

    def read_image(self, surface_root, conversation_id, source):
        """Re-verify image membership in transcript.jsonl and return base64 bytes + MIME."""
        if not isinstance(source, dict):
            raise ValueError('图片引用格式无效')
        target_step = source.get('stepIndex')
        target_media = source.get('mediaIndex')
        if type(target_step) is not int or type(target_media) is not int or target_step < 0 or target_media < 0:
            raise ValueError('图片索引无效')
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(conversation_id or '')):
            raise ValueError('原生会话路径无效')
        raw_session_dir = Path(surface_root) / 'brain' / conversation_id
        if raw_session_dir.is_symlink():
            raise ValueError('原生会话路径无效')
        logs_dir = raw_session_dir / '.system_generated' / 'logs'
        compact_path = logs_dir / 'transcript.jsonl'
        full_path = logs_dir / 'transcript_full.jsonl'
        if not compact_path.is_file() and not full_path.is_file():
            raise ValueError('找不到原始会话记录')

        uri = self._extract_image_uri(compact_path, target_step, target_media) if compact_path.is_file() else None
        if not uri and full_path.is_file():
            uri = self._extract_image_uri(full_path, target_step, target_media)

        if not uri:
            raise ValueError('图片不在该会话历史记录中')
        raw_path = local_file_path(uri) if uri.startswith('file://') else uri
        raw_candidate = Path(raw_path)
        if not raw_candidate.is_absolute() or '..' in raw_candidate.parts:
            raise ValueError('原始图片不在当前会话或项目目录中')
        if raw_candidate.is_symlink():
            raise ValueError('不允许预览符号链接图片')
        image_path = raw_candidate.resolve()
        session_dir = raw_session_dir.resolve()
        surface_key = next((r['key'] for r in self.surfaces() if r['root'] == Path(surface_root)), 'app')
        owner = self.resolve_session(gateway_id(surface_key, conversation_id))
        allowed_pairs = [(raw_session_dir, session_dir)]
        if owner and owner.get('cwd'):
            cwd_path = Path(owner['cwd'])
            allowed_pairs.append((cwd_path, cwd_path.resolve()))
        matched = [(raw_root, resolved_root) for raw_root, resolved_root in allowed_pairs
                   if resolved_root == image_path or resolved_root in image_path.parents]
        if not matched:
            raise ValueError('原始图片不在当前会话或项目目录中')
        verified_lexical = False
        for raw_root, resolved_root in matched:
            for base in (raw_root, resolved_root):
                try:
                    rel = raw_candidate.relative_to(base)
                except ValueError:
                    continue
                cur = base
                for part in rel.parts:
                    cur = cur / part
                    if cur.is_symlink():
                        raise ValueError('不允许预览符号链接图片')
                verified_lexical = True
                break
            if verified_lexical:
                break
        if not verified_lexical:
            raise ValueError('不允许预览符号链接图片')
        if not image_path.is_file():
            raise ValueError('原始图片文件已移除或不可读')
        size = image_path.stat().st_size
        if not 0 < size <= MAX_FILE:
            raise ValueError('图片大小超出预览限制')
        raw = image_path.read_bytes()
        mime = image_type(raw)
        if not mime:
            raise ValueError('不支持的图片格式')
        return {
            'data': base64.b64encode(raw).decode('ascii'),
            'mediaType': mime,
        }

    def discover_projects(self):
        """Aggregate distinct local workspace directories from recent conversations."""
        projects = []
        seen = set()
        for session in self.list_sessions():
            cwd = session.get('cwd') or ''
            surface = session.get('surfaceDirKey') or 'app'
            if not cwd or (surface, cwd) in seen:
                continue
            path = Path(cwd)
            if not path.is_dir():
                continue
            seen.add((surface, cwd))
            projects.append({
                'key': f'{surface}:{cwd}',
                'name': path.name or cwd,
                'cwd': cwd,
                'host': 'local',
                'surface': surface,
            })
        return projects
