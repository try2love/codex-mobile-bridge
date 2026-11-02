"""Read-only discovery and history for existing Codex chats."""
import copy
import uuid
import re
import os
import threading
from collections import OrderedDict
import json
import logging
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager, ExitStack
from pathlib import Path


def items_array(items):
    if isinstance(items, list):
        return items
    if isinstance(items, dict):
        entities = items.get("entitiesByKey", {})
        if entities:
            return [entities[e["value"]] for island in items.get("islands", []) for e in island.get("entries", []) if e.get("value") in entities]
        return list(items.get("items", []))
    return []


def persisted_submission_ids(state, thread_id):
    """Positive receipt evidence from saved native history, never prepared UI turns."""
    if not state or state.get('id') != thread_id:
        return set()
    identifiers = list(state.get('persistedUserMessageIds', []))
    for turn in state.get('turns', []):
        if not turn.get('turnId'):
            continue
        for item in items_array(turn.get('items')):
            if item.get('type') == 'userMessage':
                identifiers.append(item.get('clientId') or item.get('client_id'))
    result = set()
    for identifier in identifiers:
        if not isinstance(identifier, str):
            continue
        try:
            if str(uuid.UUID(identifier)) == identifier:
                result.add(identifier)
        except ValueError:
            pass
    return result


def native_patch_counts(diff):
    """File headers are metadata only before a hunk; hunk text can start +++/---."""
    added = removed = 0
    in_hunk = False
    unified = diff.startswith(('diff --git ', '--- ', '+++ '))
    for line in diff.splitlines():
        if line.startswith('@@'):
            in_hunk = True
            continue
        if line.startswith('diff --git '):
            in_hunk = False
            unified = True
            continue
        if unified and not in_hunk:
            continue
        added += line.startswith('+')
        removed += line.startswith('-')
    return added, removed


def native_file_changes(items):
    """Keep native patch evidence separate from tool prose and workspace state."""
    files, seen = {}, set()
    for item in items:
        if item.get('type') not in ('fileChange', 'FileChange') or item.get('status') in ('failed', 'declined', 'inProgress'):
            continue
        identity = item.get('id')
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        changes = item.get('changes') or []
        if isinstance(changes, dict):
            changes = [dict(change, path=path) for path, change in changes.items() if isinstance(change, dict)]
        for change in changes:
            if not isinstance(change, dict) or not isinstance(change.get('path'), str):
                continue
            diff = change.get('diff', change.get('unified_diff', ''))
            content_diff = not diff and change.get('type') in ('add', 'delete') and isinstance(change.get('content'), str)
            if content_diff:
                prefix = '+' if change['type'] == 'add' else '-'
                diff = '\n'.join(prefix + line for line in change['content'].splitlines())
            if not isinstance(diff, str):
                continue
            path = change['path']
            entry = files.setdefault(path, {'path': path, 'added': 0, 'removed': 0, 'diff': ''})
            if content_diff:
                lines = len(change['content'].splitlines())
                added, removed = (lines, 0) if change['type'] == 'add' else (0, lines)
            else:
                added, removed = native_patch_counts(diff)
            entry['added'] += added
            entry['removed'] += removed
            entry['diff'] += ('\n' if entry['diff'] else '') + diff
    return list(files.values())


def native_turn_file_changes(turn):
    if '_nativeFileChanges' in turn:
        return turn['_nativeFileChanges']
    diff = turn.get('diff')
    if isinstance(diff, str) and diff.startswith('diff --git '):
        changes = []
        for section in re.split(r'(?=^diff --git )', diff, flags=re.MULTILINE):
            if not section:
                continue
            target = re.search(r'^\+\+\+ (.+)$', section, re.MULTILINE)
            original = re.search(r'^--- (.+)$', section, re.MULTILINE)
            path = target.group(1) if target and target.group(1) != '/dev/null' else original.group(1) if original else None
            if not path:
                renamed = re.search(r'^rename to (.+)$', section, re.MULTILINE)
                path = renamed.group(1) if renamed else None
            if path and path.startswith('"'):
                try:
                    path = json.loads(path)
                except ValueError:
                    continue
            if path:
                changes.append({'path': path[2:] if path.startswith(('a/', 'b/')) else path, 'diff': section})
        if changes:
            return native_file_changes([{'type': 'fileChange', 'changes': changes}])
    return native_file_changes(items_array(turn.get('items', [])))


class StoreUnavailable(RuntimeError):
    pass


class SessionStore:
    DESKTOP_ORIGINATORS = ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge')
    CLI_ORIGINATORS = ('codex-tui', 'codex_cli_rs', 'codex-mobile-bridge-cli',
                       'codex_mobile_bridge_wsl', 'codex_mobile_bridge')

    def __init__(self, codex_home, source='all'):
        if source not in ('all', 'desktop', 'cli', 'ide'):
            raise ValueError('未知会话来源')
        self.source = source
        self.home = Path(codex_home).resolve()
        self.history_cache = OrderedDict()
        self.history_lock = threading.Lock()
        self.cli_inputs_lock = threading.Lock()
        self.cli_inputs_cache = None

    def _cli_inputs(self, query=''):
        """CLI resume labels/search, without changing saved titles or ownership."""
        if self.source != 'cli': return {}, set()
        path = self.home/'history.jsonl'
        try:
            st = path.stat()
            signature = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, query)
        except OSError:
            return {}, set()
        with self.cli_inputs_lock:
            if self.cli_inputs_cache and self.cli_inputs_cache[0] == signature:
                return self.cli_inputs_cache[1:]
            latest, matches = {}, set()
            needle = query.casefold()
            try:
                with path.open(encoding='utf-8', errors='replace') as stream:
                    for line in stream:
                        try: record = json.loads(line)
                        except ValueError: continue
                        if not isinstance(record, dict): continue
                        identifier, text = record.get('session_id'), record.get('text')
                        if not isinstance(identifier, str) or not isinstance(text, str) or not text.strip(): continue
                        latest[identifier] = ' '.join(text.split())[:200]
                        if needle and needle in text.casefold(): matches.add(identifier)
            except OSError:
                return {}, set()
            self.cli_inputs_cache = (signature, latest, matches)
            return latest, matches

    def _owner_filter(self, columns):
        if self.source == 'all':
            return self._user_origin_filter(columns)
        return self._source_filter(columns, self.source)

    def _matches(self, row):
        origin = self._origin(row)
        if self.source == 'all':
            return origin in ('desktop', 'cli', 'ide')
        if self.source == 'cli':
            return (origin == 'cli' or row.get('originator') in self.CLI_ORIGINATORS or
                    (row.get('originator') is None and row.get('source') == 'cli'))
        return origin == self.source

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

    def list(self, query="", limit=100, offset=0, archived=False, source='all', pinned_ids=(), exclude_sources=()):
        if source not in ('all', 'desktop', 'cli', 'ide'):
            raise ValueError('不支持的会话来源')
        labels, matches = self._cli_inputs(query)
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
            fields = [name for name in ("id", "name", "title", "cwd", "updated_at", "updated_at_ms", "recency_at", "recency_at_ms", "model_provider", "model", "originator", "source", "thread_source", "archived", "is_pinned", "project_id") if name in columns]
            where = ["archived = ?"]
            params = [int(archived)]
            where.append(self._user_origin_filter(columns))
            # The selected source narrows the otherwise shared user inventory.
            if self.source != 'all':
                where[-1] = self._owner_filter(columns)
            if "thread_source" in columns:
                where.append("COALESCE(thread_source, '') != 'subagent'")
            if "source" in columns:
                where.append("COALESCE(source, '') NOT LIKE '%\"subagent\"%'")
            if source != 'all':
                where.append(self._source_filter(columns, source))
            for excluded in exclude_sources:
                if excluded not in ('desktop', 'cli', 'ide'):
                    raise ValueError('不支持的会话来源')
                where.append('NOT (' + self._source_filter(columns, excluded) + ')')
            if query:
                search_fields = [name for name in ("name", "title", "cwd") if name in columns]
                # History can contain many matching IDs; a read-only function
                # avoids SQLite's placeholder limit, before sorting/pagination.
                conn.create_function('bridge_cli_input_match', 1, lambda identifier: identifier in matches)
                where.append("(" + " OR ".join([name + " LIKE ?" for name in search_fields] + ['bridge_cli_input_match(id)']) + ")")
                params += ["%" + query + "%"] * len(search_fields)
            recency = "COALESCE(recency_at_ms, recency_at * 1000, updated_at * 1000)" if "recency_at_ms" in columns else "COALESCE(recency_at, updated_at)" if "recency_at" in columns else "updated_at"
            pinned = set(pinned_ids)
            if pinned:
                conn.create_function('bridge_session_pinned', 1, lambda identifier: int(identifier in pinned))
            priority = 'bridge_session_pinned(id) DESC, ' if pinned else ''
            rows = conn.execute("SELECT " + ",".join(fields) + " FROM threads WHERE " + " AND ".join(where) + " ORDER BY " + priority + recency + " DESC, id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            result = []
            native_projects = None
            if 'project_id' in columns and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'").fetchone():
                identifiers = list({row['project_id'] for row in rows if row['project_id']})
                native_projects = {row['id']: dict(row) for row in conn.execute(
                    'SELECT id,name FROM projects WHERE id IN (' + ','.join('?' for _ in identifiers) + ')', identifiers)} if identifiers else {}
            for row in rows:
                item = dict(row)
                if native_projects is not None:
                    item['_nativeProject'] = native_projects.get(item.get('project_id'))
                origin = self._origin(item)
                if self.source == 'cli' and self._matches(item):
                    origin = 'cli'
                item['origin'] = origin
                if self.source == 'cli':
                    item['display_title'] = item.get('name') or labels.get(item['id']) or item.get('title')
                result.append(item)
            return result

    def available_sources(self):
        """Original sources across the complete visible inventory, independent of list filters."""
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
            where = [self._owner_filter(columns)]
            if 'thread_source' in columns:
                where.append("COALESCE(thread_source, '') != 'subagent'")
            if 'source' in columns:
                where.append("COALESCE(source, '') NOT LIKE '%\"subagent\"%'")
            fields = [name for name in ('originator', 'source', 'thread_source') if name in columns]
            if not fields:
                return []
            rows = conn.execute('SELECT DISTINCT ' + ','.join(fields) + ' FROM threads WHERE ' + ' AND '.join(where)).fetchall()
            return sorted({self._origin(dict(row)) for row in rows})

    @classmethod
    def _native_cli_filter(cls, columns):
        if not {'thread_source', 'originator'} <= columns:
            return '0'
        origins = ','.join(repr(origin) for origin in cls.DESKTOP_ORIGINATORS)
        return "(COALESCE(thread_source, '') = 'cli' AND COALESCE(originator, '') IN (" + origins + '))'

    def _source_filter(self, columns, source):
        if source == 'ide':
            return "COALESCE(originator, '') = 'codex_vscode'" if 'originator' in columns else '0'
        if source == 'desktop':
            originators = "originator IN ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge')" if 'originator' in columns else '0'
            legacy = "(originator IS NULL AND source = 'vscode')" if 'originator' in columns and 'source' in columns else "source = 'vscode'" if 'source' in columns else '0'
            return '(' + originators + ' OR ' + legacy + ') AND NOT ' + self._native_cli_filter(columns)
        origins = self.CLI_ORIGINATORS if self.source == 'cli' else tuple(
            origin for origin in self.CLI_ORIGINATORS if origin != 'codex_mobile_bridge')
        originators = 'originator IN (' + ','.join(repr(origin) for origin in origins) + ')' if 'originator' in columns else '0'
        legacy = "(originator IS NULL AND source = 'cli')" if 'originator' in columns and 'source' in columns else "source = 'cli'" if 'source' in columns else '0'
        return '(' + originators + ' OR ' + legacy + ' OR ' + self._native_cli_filter(columns) + ')'

    @staticmethod
    def _user_origin_filter(columns):
        if 'originator' in columns:
            recognized = "originator IN ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge', 'codex-tui', 'codex_cli_rs', 'codex-mobile-bridge-cli', 'codex_mobile_bridge_wsl', 'codex_vscode')"
            if 'source' in columns:
                return '(' + recognized + " OR (originator IS NULL AND source IN ('vscode', 'cli')))"
            return recognized
        return "source IN ('vscode', 'cli')" if 'source' in columns else '0'

    @classmethod
    def _origin(cls, row):
        if row.get('thread_source') == 'subagent' or '"subagent"' in (row.get('source') or ''):
            return 'subagent'
        if row.get('originator') == 'codex_vscode':
            return 'ide'
        if row.get('thread_source') == 'cli' and row.get('originator') in cls.DESKTOP_ORIGINATORS:
            return 'cli'
        if row.get('originator') in cls.DESKTOP_ORIGINATORS or (row.get('originator') is None and row.get('source') == 'vscode'):
            return 'desktop'
        if row.get('originator') in cls.CLI_ORIGINATORS or (row.get('originator') is None and row.get('source') == 'cli'):
            return 'cli'
        return 'other'

    def notification_changes(self, since=0):
        """Only metadata changed since discovery; never read rollout history."""
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            clocks = [name + ('' if name.endswith('_ms') else ' * 1000')
                      for name in ('updated_at', 'updated_at_ms', 'recency_at', 'recency_at_ms') if name in columns]
            clock = 'MAX(' + ','.join('COALESCE(' + c + ',0)' for c in clocks) + ',0)'
            owner = self._user_origin_filter(columns)
            if self.source != 'all':
                owner = self._owner_filter(columns)
            filters = [owner, clock + ' >= ?']
            if 'thread_source' in columns:
                filters.append("COALESCE(thread_source, '') != 'subagent'")
            if 'source' in columns:
                filters.append("COALESCE(source, '') NOT LIKE '%\"subagent\"%'")
            return [dict(row) for row in conn.execute('SELECT id, ' + clock +
                    ' AS changed_at FROM threads WHERE ' + ' AND '.join(filters), (since,))]

    def lifecycle_threads(self, root_id=None):
        """Desktop roots and their complete agent subtree, using metadata only."""
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            fields = [name for name in ('id', 'originator', 'source') if name in columns]
            rows = [dict(row) for row in conn.execute('SELECT ' + ','.join(fields) + ' FROM threads')]
        if root_id is not None:
            if not any(row['id'] == root_id for row in rows):
                raise KeyError('找不到这个 Codex 会话')
            roots = {root_id}
        elif self.source == 'all':
            roots = {row['id'] for row in rows if
                     ('originator' not in columns and row.get('source') != 'cli') or
                     self._origin(row) == 'desktop'}
        else:
            roots = {row['id'] for row in rows if self._matches(row)}
        children = {}
        for row in rows:
            parent = self._agent_source(row).get('parent_thread_id')
            if isinstance(parent, str):
                children.setdefault(parent, []).append(row['id'])
        pending = list(roots)
        while pending:
            for child in children.get(pending.pop(), []):
                if child not in roots:
                    roots.add(child)
                    pending.append(child)
        return [{'id': identifier} for identifier in sorted(roots)]

    def archive_flags(self, thread_id):
        """Complete archive metadata for one authorized root and its descendants."""
        self.get(thread_id)
        identifiers = {row['id'] for row in self.lifecycle_threads(root_id=thread_id)}
        with self._connect() as conn:
            flags = {row['id']: bool(row['archived']) for row in conn.execute('SELECT id, archived FROM threads')
                     if row['id'] in identifiers}
        if flags.keys() != identifiers:
            raise StoreUnavailable('无法完整核验聊天和子智能体的归档状态')
        return flags

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
                raise KeyError("找不到这个 Codex 会话")
            result = dict(row)
            origin = self._origin(result)
            if origin not in ('desktop', 'cli', 'ide'):
                raise KeyError("不是可访问的 Codex 会话")
            if not self._matches(result):
                raise KeyError("不是可访问的 Codex 会话")
            if self.source == 'cli':
                origin = 'cli'
                labels, _ = self._cli_inputs()
                result['display_title'] = result.get('name') or labels.get(thread_id) or result.get('title')
            result['origin'] = origin
            return result

    def _rollout_in_home(self, resolved):
        for base in (self.home / 'sessions', self.home / 'archived_sessions'):
            root = base.resolve()
            if root in resolved.parents:
                return True
            if os.name == 'nt':
                # Native Windows metadata may use \\?\ paths while CODEX_HOME
                # uses a drive path. Compare actual directories only in that case.
                for parent in resolved.parents:
                    try:
                        if root.samefile(parent):
                            return True
                    except OSError:
                        continue
        return False

    def discovery_diagnostics(self, thread_id=None):
        """Return aggregate source and read status, never chat content or private paths."""
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            fields = [name for name in ('id', 'originator', 'source', 'thread_source', 'archived', 'rollout_path') if name in columns]
            rows = [dict(row) for row in conn.execute('SELECT ' + ','.join(fields) + ' FROM threads')]
        counts = {'desktop': 0, 'cli': 0, 'ide': 0, 'other': 0, 'subagent': 0, 'visible': 0, 'archived': 0}
        target = None
        for row in rows:
            origin = self._origin(row)
            counts[origin] += 1
            if row.get('archived'):
                counts['archived'] += 1
            elif origin in ('desktop', 'cli', 'ide'):
                counts['visible'] += 1
            if thread_id is not None and row['id'] == thread_id:
                target = row
        result = {'status': 'ok', 'phase': 'discovery', 'homeSource': 'provided', 'counts': counts,
                  'schema': {'originator': 'originator' in columns, 'source': 'source' in columns,
                             'threadSource': 'thread_source' in columns, 'rolloutPath': 'rollout_path' in columns}}
        if thread_id is not None:
            if target is None:
                result['target'] = {'found': False, 'origin': None, 'archived': None,
                                    'reason': 'missing', 'read': 'unavailable'}
            else:
                origin = self._origin(target)
                reason = 'subagent' if origin == 'subagent' else 'unsupported_origin' if origin == 'other' else 'readable'
                if reason == 'readable':
                    path = target.get('rollout_path')
                    if not path:
                        reason = 'rollout_missing'
                    else:
                        resolved = Path(path).resolve()
                        if not self._rollout_in_home(resolved):
                            reason = 'rollout_outside_home'
                        elif not resolved.is_file():
                            reason = 'rollout_missing'
                        else:
                            try:
                                with resolved.open('rb') as stream:
                                    stream.read(1)
                            except OSError:
                                reason = 'rollout_unreadable'
                result['target'] = {'found': True, 'origin': origin, 'archived': bool(target.get('archived')), 'reason': reason,
                                    'read': 'ready' if reason == 'readable' else 'unavailable'}
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

    def history(self, thread_id, turn_limit=None, item_limit=None):
        meta = self.get(thread_id)
        if item_limit is not None:
            indexed = self._indexed_history(thread_id, meta, max(1, int(item_limit)))
            if indexed is not None:
                return indexed
        return self._history(thread_id, meta, turn_limit)

    def _indexed_history(self, thread_id, meta, item_limit):
        """Read the native persisted projection, without starting/resuming Codex.

        Internal schema support is optional. Missing/incompatible/empty indexes
        retain the rollout reader. The projection is display history only: it
        cannot establish live status, permissions, pending approvals or ownership.
        """
        database = self.home / 'thread_history_1.sqlite'
        try:
            rollout = Path(meta['rollout_path']).resolve()
            if not self._rollout_in_home(rollout):
                return None
            rollout_stat = rollout.stat()
            with database.open('rb') as stream:
                wal_mode = stream.read(20)[18:20] == b'\x02\x02'
            # Never initialize WAL sidecars in the user's native history store.
            if wal_mode and not all(Path(str(database) + suffix).exists() for suffix in ('-wal', '-shm')):
                return None
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.2)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute('BEGIN')
                projection = conn.execute('SELECT next_rollout_byte_offset FROM thread_history_projection_state '
                                          'WHERE thread_id=?', (thread_id,)).fetchone()
                # A fork may point at another rollout with the same thread ID;
                # an index behind (or ahead of) that file is not a recent page.
                if projection is None or projection[0] != rollout_stat.st_size:
                    return None
                rows = conn.execute('SELECT turn_id, item_json FROM thread_items WHERE thread_id=? '
                                    'ORDER BY rollout_ordinal DESC LIMIT ?', (thread_id, item_limit + 1)).fetchall()
                if not rows:
                    return None
                more = len(rows) > item_limit
                selected = list(reversed(rows[:item_limit]))
                turns = {}
                for row in selected:
                    if row['turn_id'] not in turns:
                        header = conn.execute('SELECT turn_id, status, error_json, started_at, first_user_item_id '
                                              'FROM thread_turns WHERE thread_id=? AND turn_id=?',
                                              (thread_id, row['turn_id'])).fetchone()
                        if header is None:
                            return None
                        turns[row['turn_id']] = {'turnId': row['turn_id'], 'status': header['status'],
                                                'turnStartedAtMs': header['started_at'],
                                                '_firstUserItemId': header['first_user_item_id'],
                                                '_itemsComplete': not (more and row['turn_id'] == rows[item_limit]['turn_id']),
                                                'error': json.loads(header['error_json']) if header['error_json'] else None,
                                                'items': []}
                    item = json.loads(row['item_json'])
                    if not isinstance(item, dict) or not isinstance(item.get('type'), str):
                        return None
                    turns[row['turn_id']]['items'].append(item)
                for turn in turns.values():
                    calls = {item.get('call_id', item.get('id')) for item in turn['items']
                             if item.get('type') in ('function_call', 'custom_tool_call')}
                    if any(item.get('type') in ('function_call_output', 'custom_tool_call_output')
                           and item.get('call_id') not in calls for item in turn['items']):
                        # Legacy raw items need their call to keep row identities
                        # stable. Fall back to a complete-turn tail at this edge.
                        return None
            latest = rollout.stat()
            if (latest.st_ino, latest.st_size, latest.st_mtime_ns) != (rollout_stat.st_ino, rollout_stat.st_size, rollout_stat.st_mtime_ns):
                return None
            return {'id': thread_id, 'title': meta.get('name') or meta.get('title'), 'cwd': meta['cwd'],
                    'modelProvider': meta.get('model_provider'), 'latestModel': meta.get('model'),
                    'turns': list(turns.values()), 'turnsPagination': {'hasLoadedOldest': not more},
                    'requests': [], 'threadRuntimeStatus': {'type': 'notLoaded'}, 'historyReadSource': 'native-index'}
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return None

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
        if not self._rollout_in_home(resolved):
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
        persisted_user_ids = []
        token_usage = None
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
            elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get('info')
                if isinstance(info, dict) and isinstance(info.get('last_token_usage'), dict):
                    token_usage = {'last': {'totalTokens': info['last_token_usage'].get('total_tokens')},
                                   'modelContextWindow': info.get('model_context_window')}
            elif record.get("type") == "turn_context" and current is not None:
                settings = (payload.get('collaboration_mode') or {}).get('settings') or {}
                current['params'] = {'model':payload.get('model') or settings.get('model'),
                                     'effort':payload.get('effort') or settings.get('reasoning_effort')}
            elif record.get("type") == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
                if current:
                    current["status"] = "completed" if payload["type"] == "task_complete" else "interrupted"
            elif record.get("type") == "event_msg" and payload.get("type") == "item_completed":
                item = payload.get("item", {})
                if (isinstance(item, dict) and item.get('type') == 'UserMessage' and current is not None and
                        payload.get('thread_id') == thread_id and payload.get('turn_id') == current.get('turnId')):
                    # The response_item supplies display text; this separate
                    # persisted event supplies the native acceptance identity.
                    persisted_user_ids.append(item.get('client_id'))
                if isinstance(item, dict) and item.get("type") in ("ImageView", "imageView"):
                    # ImageView evidence only exists in event records. Preserve it
                    # so saved history can still render the image after restart.
                    if current is None:
                        current = {"turnId": "history", "status": "completed", "items": []}
                        turns.append(current)
                        items = current["items"]
                    items.append({"id": item.get("id", str(len(items))), "type": "ImageView", "path": item.get("path", "")})
                elif isinstance(item, dict) and item.get("type") in ("FileChange", "fileChange") and current is not None:
                    items.append({**item, "type": "fileChange", "status": item.get("status", "completed")})
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
        # Rollout parsing already visited these records. Retain the native patch
        # projection once so a later bounded item window need not reopen bodies.
        for turn in turns:
            turn['_nativeFileChanges'] = native_turn_file_changes(turn)
            turn['_nativeFileChangesBasis'] = 'recorded'
        result = {"id": thread_id, "title": meta.get("name") or meta.get("title"), "cwd": meta["cwd"],
                "latestModel": meta.get('model') or next((t['params']['model'] for t in reversed(turns) if (t.get('params') or {}).get('model')), None),
                "latestReasoningEffort": next((t['params']['effort'] for t in reversed(turns) if (t.get('params') or {}).get('effort')), None),
                "modelProvider": meta.get("model_provider"),
                "latestTokenUsageInfo": token_usage, "tokenUsageFromHistory": True,
                "persistedUserMessageIds": persisted_user_ids,
                "turns": turns, "turnsPagination": {"hasLoadedOldest": complete}, "requests": [], "threadRuntimeStatus": {"type": "notLoaded"}}
        with self.history_lock:
            if len(raw) <= 4 * 1024 * 1024:
                self.history_cache[cache_key] = result
            while len(self.history_cache) > 8:
                self.history_cache.popitem(last=False)
        return copy.deepcopy(result)
