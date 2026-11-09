"""Read-only SQLite summary index and JSONL transcript reader for Antigravity."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from bridge.features.workspace.uploads import MAX_FILE, image_type
from .model import SURFACE_LABELS, extract_user_text, gateway_id, normalize_steps

SURFACE_DIRS = (
    ('app', '.gemini/antigravity'),
    ('ide', '.gemini/antigravity-ide'),
    ('cli', '.gemini/antigravity-cli'),
)

_FRACTION_RE = re.compile(r'\.(\d+)')
_MAX_FULL_FIELD_CHARS = 65536


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
            parsed = urlsplit(item)
            path = url2pathname(unquote(parsed.path))
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
            copied[key] = val[:_MAX_FULL_FIELD_CHARS] + '\n…（內容過長，已在手機端截斷預覽）'
    return copied


class AntigravityStore:
    """Discover and read local Antigravity surfaces without mutating state."""

    def __init__(self, scratch_dir, home=None):
        self.scratch_dir = Path(scratch_dir)
        self.home = Path(home).expanduser().resolve() if home else Path.home().resolve()

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

    def _query_db(self, db_path, sql, params=()):
        if not db_path.is_file():
            return []
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1.5)
            try:
                conn.row_factory = sqlite3.Row
                return [dict(row) for row in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()
        except sqlite3.OperationalError:
            self.scratch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp_dir = Path(tempfile.mkdtemp(prefix='db-ro-', dir=str(self.scratch_dir)))
            try:
                target = tmp_dir / 'conversation_summaries.db'
                shutil.copy2(db_path, target)
                for suffix in ('-wal', '-shm'):
                    sidecar = Path(str(db_path) + suffix)
                    if sidecar.is_file():
                        shutil.copy2(sidecar, Path(str(target) + suffix))
                conn = sqlite3.connect(str(target), timeout=1.5)
                try:
                    conn.row_factory = sqlite3.Row
                    return [dict(row) for row in conn.execute(sql, params).fetchall()]
                finally:
                    conn.close()
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _backend_for(dir_key, source):
        if dir_key == 'ide':
            return 'ide'
        if dir_key == 'cli' or source == 'CORTEX_TRAJECTORY_SOURCE_CLI':
            return 'cli'
        return 'app'

    def _row_to_summary(self, dir_key, root, row):
        cid = str(row.get('conversation_id') or '').strip()
        if not cid:
            return None
        sid = gateway_id(dir_key, cid)
        cwd = parse_workspace_uris(row.get('workspace_uris') or '')
        title = str(row.get('title') or '').strip()
        if not title:
            preview = extract_user_text(str(row.get('preview') or '')).splitlines()
            title = (preview[0][:80] if preview and preview[0].strip() else '') or f'對話 {cid[:8]}'
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
            'archived': False,
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
            for row in self._query_db(db_path, sql):
                cid = str(row.get('conversation_id') or '').strip()
                if not cid or (surface['root'], cid) in seen_native:
                    continue
                seen_native.add((surface['root'], cid))
                summary = self._row_to_summary(surface['key'], surface['root'], row)
                if summary is not None:
                    sessions.append(summary)
        sessions.sort(key=lambda item: item.get('updatedAt', 0), reverse=True)
        return sessions

    def resolve_session(self, sid):
        """Find a top-level or known conversation by gateway UUID or raw native UUID."""
        if not isinstance(sid, str) or not sid:
            return None
        for item in self.list_sessions():
            if item['id'] == sid or item['nativeId'] == sid:
                return item
        # Also check brain/<cid> directory if CLI just created a session before summary flush
        for surface in self.surfaces():
            for candidate_dir in (surface['root'] / 'brain').glob('*') if (surface['root'] / 'brain').is_dir() else ():
                cid = candidate_dir.name
                if cid == sid or gateway_id(surface['key'], cid) == sid:
                    transcript = candidate_dir / '.system_generated/logs/transcript.jsonl'
                    mtime = transcript.stat().st_mtime if transcript.is_file() else 0.0
                    return {
                        'id': gateway_id(surface['key'], cid),
                        'nativeId': cid,
                        'surfaceDirKey': surface['key'],
                        'surfaceRoot': str(surface['root']),
                        'provider': 'antigravity',
                        'host': 'local',
                        'backend': surface['key'],
                        'title': f'對話 {cid[:8]}',
                        'cwd': '',
                        'projectId': f'{surface["key"]}:unassigned',
                        'projectName': surface['name'],
                        'updatedAt': mtime,
                        'archived': False,
                        'status': 'idle',
                        'notFullyIdle': False,
                        'stepCount': 0,
                        'runtimeKnown': True,
                    }
        return None

    def is_native_busy(self, surface_root, conversation_id):
        """Check fresh SQLite not_fully_idle flag before launching cold CLI resume."""
        db_path = Path(surface_root) / 'conversation_summaries.db'
        rows = self._query_db(
            db_path,
            'SELECT not_fully_idle, status FROM conversation_summaries WHERE conversation_id = ? LIMIT 1',
            (conversation_id,),
        )
        if not rows:
            return False
        row = rows[0]
        return bool(row.get('not_fully_idle')) or str(row.get('status') or '').endswith('_RUNNING')

    def list_tasks(self, surface_root, conversation_id):
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
                'cancellable': False,
            })
        return tasks

    def read_transcript_page(self, surface_root, conversation_id, *, limit=40, before=None, session_active=False):
        """Read a reverse-paginated slice of transcript.jsonl with targeted full-line hydration."""
        logs_dir = Path(surface_root) / 'brain' / conversation_id / '.system_generated' / 'logs'
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
                raise ValueError('歷史游標格式無效') from exc

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
                if before_index is not None and step_idx >= before_index:
                    continue

                # Branch reconciliation: if new step index is <= last step index,
                # the transcript rewound or branched. Prune the earlier abandoned steps.
                while steps and steps[-1].get('step_index', 0) >= step_idx:
                    steps.pop()

                steps.append(obj)

        # Ensure we include at least min(6, available) complete USER_INPUT turns
        # and align the page start to a USER_INPUT step so a turn with many tool
        # calls never pushes its own user prompt or recent replies off the screen.
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
            earlier_users = [pos for pos in user_positions if pos <= start_pos]
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
                    if len(hydrated) >= len(truncated_indices):
                        break
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

        messages, turns, usage = normalize_steps(page, session_active=session_active and before_index is None)
        return {
            'messages': messages,
            'turns': turns,
            'history': {'hasMore': has_more, 'before': next_before},
            'contextUsage': usage,
        }

    def read_image(self, surface_root, conversation_id, source):
        """Re-verify image membership in transcript.jsonl and return base64 bytes + MIME."""
        if not isinstance(source, dict):
            raise ValueError('圖片引用格式無效')
        target_step = source.get('stepIndex')
        target_media = source.get('mediaIndex')
        if type(target_step) is not int or type(target_media) is not int or target_step < 0 or target_media < 0:
            raise ValueError('圖片索引無效')
        compact_path = Path(surface_root) / 'brain' / conversation_id / '.system_generated' / 'logs' / 'transcript.jsonl'
        if not compact_path.is_file():
            raise ValueError('找不到原始會話記錄')

        uri = None
        with compact_path.open('r', encoding='utf-8', errors='replace') as stream:
            for idx, line in enumerate(stream):
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get('step_index', idx) != target_step:
                    continue
                media = obj.get('media')
                if isinstance(media, list) and target_media < len(media) and isinstance(media[target_media], dict):
                    uri = str(media[target_media].get('uri') or '')
                break

        if not uri:
            raise ValueError('圖片不在該會話歷史記錄中')
        raw_path = url2pathname(unquote(urlsplit(uri).path)) if uri.startswith('file://') else uri
        image_path = Path(raw_path).resolve()
        if not image_path.is_file():
            raise ValueError('原始圖片檔案已移除或不可讀')
        size = image_path.stat().st_size
        if not 0 < size <= MAX_FILE:
            raise ValueError('圖片大小超出預覽限制')
        raw = image_path.read_bytes()
        mime = image_type(raw)
        if not mime:
            raise ValueError('不支援的圖片格式')
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
