"""Read-only discovery and history for existing Codex chats."""
import copy
import uuid
import re
import os
import sys
import threading
from collections import OrderedDict
import json
import logging
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager, ExitStack
from pathlib import Path

ROLLOUT_TIME_BYTES = 2 * 1024 * 1024


def project_root_key(path, platform=None):
    """Compare registered roots for display without resolving filesystem paths."""
    if not isinstance(path, str) or not path:
        return None
    windows = path.replace('\\', '/')
    if windows.startswith('//?/'):
        extended = windows[4:]
        if extended[:4].lower() == 'unc/':
            windows = '//' + extended[4:]
        elif re.match(r'^[A-Za-z]:/', extended):
            windows = extended
    drive = re.match(r'^/?([A-Za-z]:/.*)$', windows)
    if drive:
        return 'windows', drive[1].rstrip('/').lower()
    unc = path.startswith('\\\\') or path.startswith('//?/') or (
        path.startswith('//') and (sys.platform if platform is None else platform) == 'win32')
    if (unc and not windows.startswith(('//?/', '//./'))
            and re.match(r'^//[^/]+/[^/]+', windows)):
        return 'windows', windows.rstrip('/').lower()
    return 'posix', path.rstrip('/') or '/'


def _indexed_item_clock(row, item_id):
    if not isinstance(item_id, str) or not item_id or 'item_id' not in row.keys() or row['item_id'] != item_id:
        return {}
    for field, source in (('completed_at_ms', 'native-index-item-completed'),
                          ('started_at_ms', 'native-index-item-started')):
        value = row[field] if field in row.keys() else None
        if type(value) in (int, float) and value > 0:
            return {'timestamp': value, '_timestampSource': source}
    return {}


def recorded_turn_label(payload):
    collaboration = payload.get('collaboration_mode')
    settings = collaboration.get('settings') if isinstance(collaboration, dict) else None
    settings = settings if isinstance(settings, dict) else {}
    effort = payload['effort'] if 'effort' in payload else settings.get('reasoning_effort')
    return {'model': payload.get('model') or settings.get('model'), 'effort': effort}


def _records(lines):
    for line in lines:
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(record, dict):
            yield record


def _recorded_token_usage(payload):
    info = payload.get('info')
    last = info.get('last_token_usage') if isinstance(info, dict) else None
    used = last.get('total_tokens') if isinstance(last, dict) else None
    if type(used) is int and used >= 0:
        return {'last': {'totalTokens': used}, 'modelContextWindow': info.get('model_context_window')}
    return None


def _rollout_metadata_tail(path, size):
    offset = max(0, size - ROLLOUT_TIME_BYTES)
    with path.open('rb') as stream:
        stream.seek(offset)
        raw = stream.read(size - offset)
    # At a bounded start, the first record may already be partial.
    return raw.partition(b'\n')[2] if offset else raw


def _last_recorded_usage(raw, thread_id):
    lines = (line for line in reversed(raw.splitlines()) if b'"token_count"' in line)
    for record in _records(lines):
        payload = record.get('payload')
        if (record.get('type') == 'event_msg' and isinstance(payload, dict) and
                payload.get('type') == 'token_count' and payload.get('thread_id', thread_id) == thread_id):
            usage = _recorded_token_usage(payload)
            if usage is not None:
                return usage
    return None


def rollout_message_times(raw, thread_id, include_turns=False, include_labels=False, *, records=None):
    """Native identities only; text and rollout positions are not clock evidence."""
    times, completed_turns, labels, turn_id = {}, {}, {}, None
    if records is None:
        records = _records(line for line in raw.splitlines() if any(token in line for token in
            (b'"task_started"', b'"response_item"', b'"item_completed"', b'"task_complete"', b'"turn_aborted"', b'"turn_context"')))
    for record in records:
        payload = record.get('payload') or {}
        kind = payload.get('type')
        if record.get('type') == 'event_msg' and kind == 'task_started':
            turn_id = payload.get('turn_id') if payload.get('thread_id', thread_id) == thread_id else None
            continue
        if include_labels and record.get('type') == 'turn_context' and payload.get('thread_id', thread_id) == thread_id:
            identity = payload.get('turn_id') or turn_id
            if isinstance(identity, str) and identity:
                labels[identity] = recorded_turn_label(payload)
            continue
        if record.get('type') == 'event_msg' and kind in ('task_complete', 'turn_aborted'):
            identity, stamp = payload.get('turn_id') or turn_id, record.get('timestamp')
            if payload.get('thread_id', thread_id) == thread_id and isinstance(identity, str) and identity and stamp is not None:
                completed_turns.setdefault(identity, []).append(stamp)
            continue
        if record.get('type') == 'response_item' and payload.get('thread_id', thread_id) == thread_id:
            role = payload.get('role') if kind == 'message' else 'assistant' if kind in ('agent_message', 'agentMessage') else None
            native_kind = {'user': 'userMessage', 'assistant': 'agentMessage'}.get(role)
            key = (payload.get('turn_id') or turn_id, payload.get('id'), native_kind, role)
            stamp, source = record.get('timestamp'), 'rollout-response-item'
        elif record.get('type') == 'event_msg' and kind == 'item_completed' and payload.get('thread_id') == thread_id:
            item = payload.get('item') or {}
            role = {'AgentMessage': 'assistant', 'UserMessage': 'user'}.get(item.get('type'))
            native_kind = {'user': 'userMessage', 'assistant': 'agentMessage'}.get(role)
            key = (payload.get('turn_id'), item.get('id'), native_kind, role)
            stamp, source = payload.get('completed_at_ms'), 'rollout-item-completed'
            if type(stamp) not in (int, float):
                stamp = record.get('timestamp')
        else:
            continue
        if not all(isinstance(value, str) and value for value in key) or stamp is None:
            continue
        times.setdefault(key, {}).setdefault(source, []).append(stamp)
    result = {}
    for key, sources in times.items():
        if all(len(values) == 1 for values in sources.values()):
            source = 'rollout-item-completed' if 'rollout-item-completed' in sources else 'rollout-response-item'
            result[key] = (sources[source][0], source)
    if include_turns:
        completed = {identity: values[0] for identity, values in completed_turns.items() if len(values) == 1}
        return (result, completed, labels) if include_labels else (result, completed)
    return result


def apply_rollout_message_times(turns, times):
    rows = {}
    for turn in turns:
        for item in items_array(turn.get('items')):
            role = {'userMessage': 'user', 'agentMessage': 'assistant'}.get(item.get('type'))
            if role and not item.get('_syntheticId'):
                key = (turn.get('turnId'), item.get('id'), item['type'], role)
                rows.setdefault(key, []).append(item)
    for key, items in rows.items():
        if (len(items) == 1 and key in times and
                (items[0].get('timestamp') is None or items[0].get('_timestampSource') == 'rollout-response-item')):
            items[0]['timestamp'], items[0]['_timestampSource'] = times[key]


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


def _retained_index_module():
    # SSH source injection supplies the same module without installing Bridge remotely.
    module = globals().get('_bridge_index_archive')
    if module is None:
        from bridge.features.sessions import index_archive
        module = index_archive
    return module


class SessionStore:
    DESKTOP_ORIGINATORS = ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge')
    CLI_ORIGINATORS = ('codex-tui', 'codex_cli_rs', 'codex_exec', 'codex-mobile-bridge-cli',
                       'codex_mobile_bridge_wsl', 'codex_mobile_bridge')

    def __init__(self, codex_home, source='all'):
        if source not in ('all', 'desktop', 'cli', 'ide'):
            raise ValueError('未知会话来源')
        self.source = source
        self.home = Path(codex_home).resolve()
        self.history_cache = OrderedDict()
        self.input_time_cache = OrderedDict()
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

    def list(self, query="", limit=100, offset=0, archived=False, source='all', pinned_ids=(), exclude_sources=(),
             transfer=False, project_roots=False, project_scope=False):
        if source not in ('all', 'desktop', 'cli', 'ide'):
            raise ValueError('不支持的会话来源')
        labels, matches = self._cli_inputs(query)
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
            fields = [name for name in ("id", "name", "title", "cwd", "updated_at", "updated_at_ms", "recency_at", "recency_at_ms", "model_provider", "model", "originator", "source", "thread_source", "archived", "is_pinned", "project_id", "attached_thread_parent_id", "rollout_path") if name in columns]
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
            if 'attached_thread_parent_id' in columns:
                # Only the native attachment relation hides a child. A normal
                # fork remains a root; a missing/out-of-scope parent must not
                # make its child disappear from the registered configuration.
                parent_filter = self._owner_filter(columns)
                for excluded in exclude_sources:
                    parent_filter += ' AND NOT (' + self._source_filter(columns, excluded) + ')'
                parent_filter = re.sub(r'\b(' + '|'.join(re.escape(name) for name in columns) + r')\b',
                                       lambda match: 'parent.' + match.group(), parent_filter)
                where.append("(attached_thread_parent_id IS NULL OR attached_thread_parent_id = '' OR NOT EXISTS "
                             '(SELECT 1 FROM threads AS parent WHERE parent.id = threads.attached_thread_parent_id AND ' + parent_filter + '))')
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
            project_tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('projects', 'project_roots')")}
            root_projects = {}
            if 'project_id' in columns and 'projects' in project_tables:
                identifiers = list({row['project_id'] for row in rows if row['project_id']})
                native_projects = {row['id']: dict(row) for row in conn.execute(
                    'SELECT id,name FROM projects WHERE id IN (' + ','.join('?' for _ in identifiers) + ')', identifiers)} if identifiers else {}
            if project_roots and {'projects', 'project_roots'} <= project_tables and 'cwd' in columns:
                directories = {project_root_key(row['cwd']) for row in rows if row['cwd'] and
                               ('project_id' not in columns or not row['project_id'])}
                if directories:
                    conn.create_function('bridge_list_directory', 1, lambda path: project_root_key(path) in directories)
                    for project in conn.execute('SELECT roots.path, p.id, p.name FROM project_roots AS roots '
                                                'JOIN projects AS p ON p.id = roots.project_id '
                                                'WHERE bridge_list_directory(roots.path)'):
                        root_projects.setdefault(project_root_key(project['path']), []).append(
                            {'id': project['id'], 'name': project['name']})
            for row in rows:
                item = dict(row)
                rollout_path = item.pop('rollout_path', None)
                if transfer:
                    try:
                        path = Path(rollout_path).resolve(strict=True)
                        if self._rollout_in_home(path):
                            item['sourceBytes'] = path.stat().st_size
                            item['sourceHome'] = str(self.home.resolve())
                    except (OSError, TypeError, ValueError):
                        pass
                else:
                    item['lastUserInputAt'] = self.last_user_input(rollout_path, item['id'])
                if native_projects is not None:
                    item['_nativeProject'] = native_projects.get(item.get('project_id'))
                    if project_scope and item['_nativeProject'] is not None:
                        item['_nativeProjectHome'] = str(self.home)
                if root_projects and not item.get('project_id'):
                    candidates = root_projects.get(project_root_key(item.get('cwd')))
                    if candidates:
                        item['_nativeRootProjects'] = candidates
                origin = self._origin(item)
                if self.source == 'cli' and self._matches(item):
                    origin = 'cli'
                item['origin'] = origin
                if item.get('attached_thread_parent_id'):
                    item['attachedParentId'] = item['attached_thread_parent_id']
                    item['orphanedSideChat'] = True
                if self.source == 'cli':
                    item['display_title'] = item.get('name') or labels.get(item['id']) or item.get('title')
                result.append(item)
            return result

    def last_user_input(self, path, thread_id):
        """Read native input events backwards, independently of activity/mtime."""
        if not isinstance(path, str) or not path:
            return None
        try:
            resolved = Path(path).resolve()
            if not self._rollout_in_home(resolved):
                return None
            with resolved.open('rb') as stream:
                before = os.fstat(stream.fileno())
                identity = (str(resolved), thread_id, before.st_dev, before.st_ino,
                            before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                with self.history_lock:
                    if identity in self.input_time_cache:
                        self.input_time_cache.move_to_end(identity)
                        return self.input_time_cache[identity]
                position, pending, found, value = before.st_size, b'', False, None
                while position and not found:
                    length = min(position, 64 * 1024)
                    position -= length
                    stream.seek(position)
                    lines = (stream.read(length) + pending).split(b'\n')
                    pending = lines.pop(0)
                    if not position:
                        lines.insert(0, pending); pending = b''
                    for line in reversed(lines):
                        if b'"user_message"' not in line and b'"item_completed"' not in line:
                            continue
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        payload = event.get('payload')
                        if event.get('type') != 'event_msg' or not isinstance(payload, dict) or payload.get('thread_id', thread_id) != thread_id:
                            continue
                        if payload.get('type') == 'user_message':
                            found, value = True, event.get('timestamp')
                        elif payload.get('type') == 'item_completed' and (payload.get('item') or {}).get('type') == 'UserMessage':
                            found, value = True, payload.get('completed_at_ms', event.get('timestamp'))
                        if found:
                            break
                after = os.fstat(stream.fileno())
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    return None
            with self.history_lock:
                self.input_time_cache[identity] = value
                while len(self.input_time_cache) > 512:
                    self.input_time_cache.popitem(last=False)
            return value
        except OSError:
            return None

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
            recognized = "originator IN ('Codex Desktop', 'codex_work_desktop', 'codex_mobile_bridge', 'codex-tui', 'codex_cli_rs', 'codex_exec', 'codex-mobile-bridge-cli', 'codex_mobile_bridge_wsl', 'codex_vscode')"
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
        """Desktop roots and complete attached/agent subtree, using metadata only."""
        with self._connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            fields = [name for name in ('id', 'originator', 'source', 'thread_source', 'attached_thread_parent_id') if name in columns]
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
            for parent in (self._agent_source(row).get('parent_thread_id'), row.get('attached_thread_parent_id')):
                if isinstance(parent, str) and parent:
                    children.setdefault(parent, []).append(row['id'])
        pending = list(roots)
        while pending:
            for child in children.get(pending.pop(), []):
                if child not in roots:
                    roots.add(child)
                    pending.append(child)
        return [{'id': identifier} for identifier in sorted(roots)]

    def attached_children(self, thread_id):
        """All native children, including archives; no process or owner activation."""
        self.get(thread_id)
        with self._connect() as conn:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(threads)')}
            if 'attached_thread_parent_id' not in columns:
                return []
            fields = [name for name in ('id', 'name', 'title', 'cwd', 'archived', 'updated_at', 'originator', 'source', 'thread_source') if name in columns]
            rows = conn.execute('SELECT ' + ','.join(fields) + ' FROM threads WHERE attached_thread_parent_id = ? ORDER BY updated_at DESC,id DESC', (thread_id,)).fetchall()
            return [dict(row, attachedParentId=thread_id) for row in rows if self._matches(dict(row)) and self._origin(dict(row)) in ('desktop', 'cli', 'ide')]

    def attached_parent(self, thread_id):
        row = self.get(thread_id)
        parent = row.get('attached_thread_parent_id')
        if not parent:
            return None
        try:
            source = self.get(parent)
        except KeyError:
            return {'id': parent, 'available': False}
        return {'id': parent, 'available': True, 'title': source.get('name') or source.get('title') or '未命名聊天'}

    def existing_ids(self, identifiers):
        """Metadata receipts for an already authorized lifecycle scope."""
        identifiers = tuple(set(identifiers))
        if not identifiers:
            return set()
        with self._connect() as conn:
            return {row['id'] for row in conn.execute('SELECT id FROM threads WHERE id IN (' +
                    ','.join('?' for _ in identifiers) + ')', identifiers)}

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
    def _recent_lines(path, turn_limit, end=None):
        """Read complete turns backwards without parsing the older rollout prefix."""
        with path.open('rb') as stream:
            stream.seek(0, 2)
            position = stream.tell()
            if end is not None:
                if not 0 <= end <= position:
                    raise ValueError('聊天历史分界点无效')
                if end:
                    stream.seek(end - 1)
                    if stream.read(1) != b'\n':
                        raise ValueError('聊天历史分界点不在记录边界')
                position = end
            if turn_limit is None:
                stream.seek(0)
                return stream.read(position), True, 0
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
                            return b''.join(reversed(parts)), position == 0 and len(parts) == len(lines), starts
            return b''.join(reversed(parts)), True, starts

    @staticmethod
    def _segment_header(path):
        with path.open('rb') as stream:
            line = stream.readline(1024 * 1024)
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            return {}
        payload = record.get('payload') if isinstance(record, dict) and record.get('type') == 'session_meta' else None
        return payload if isinstance(payload, dict) else {}

    def _history_tail(self, path, thread_id, limit):
        # An edit starts a new physical segment of the same logical thread.
        # Only the parent's records before the recorded byte boundary survive.
        parts, seen, end = [], set(), None
        while True:
            raw, complete, starts = self._recent_lines(path, limit, end)
            parts.append(raw)
            if not complete:
                break
            header = self._segment_header(path)
            base = header.get('history_base')
            if not base:
                break
            parent = base.get('thread_id') if isinstance(base, dict) else None
            if (not isinstance(thread_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', thread_id)
                    or (header.get('id') != thread_id if not seen else not isinstance(header.get('id'), str)) or not isinstance(parent, str)
                    or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', parent)
                    or parent in seen or type(base.get('end_byte_offset')) is not int):
                raise ValueError('无法核验聊天历史来源')
            if limit is not None:
                limit -= starts
                if limit <= 0:
                    complete = False
                    break
            seen.add(parent)
            end = base['end_byte_offset']
            if end == 0:
                break
            path = self._parent_rollout(thread_id, parent)
            if path is None:
                complete = False
                break
        return b''.join(reversed(parts)), complete

    def _parent_rollout(self, thread_id, parent):
        # history_base identifies a physical segment. A fork may retain its
        # source chat's logical id; validate the filename/header instead of
        # assuming that every ancestor was renamed to the child chat.
        matches = []
        for directory in (self.home/'sessions', self.home/'archived_sessions'):
            for path in directory.rglob('rollout-*' + parent + '.jsonl'):
                resolved = path.resolve()
                if resolved != path or not self._rollout_in_home(resolved):
                    continue
                meta = self._segment_header(resolved)
                logical = meta.get('id')
                if not isinstance(logical, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', logical):
                    continue
                if meta.get('session_id', logical) != logical:
                    continue
                if (path.name.endswith('-' + logical + '_' + parent + '.jsonl') or
                        logical == parent and path.name.endswith('-' + parent + '.jsonl')):
                    matches.append(path)
        return matches[0] if len(matches) == 1 else None

    def _index_tail(self, conn, rollout, thread_id, size, limit, segments=None, *, metadata=False):
        # Newer rollouts can be branches: their filename supplies the physical
        # index key, while every segment's header identifies the logical chat.
        columns = {row[1] for row in conn.execute('PRAGMA table_info(thread_items)')}
        extras = [name for name in ('item_id', 'item_type', 'started_at_ms', 'completed_at_ms') if name in columns]
        if metadata and not {'item_id', 'item_type'} <= columns:
            return []
        fields = ['thread_id', 'turn_id', *([] if metadata else ['item_json']), *extras]
        message_filter = ("AND item_type IN ('AgentMessage','agentMessage','assistantMessage','UserMessage','userMessage','steeringUserMessage') "
                          if metadata else '')
        segment = thread_id
        header = self._segment_header(rollout)
        if header.get('history_base'):
            segment = rollout.stem.rsplit('_', 1)[-1]
            if not (header.get('id') == thread_id and
                    re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', segment)
                    and rollout.name.endswith('-' + thread_id + '_' + segment + '.jsonl')):
                return None
        rows, seen, end, path = [], set(), None, rollout
        while segment not in seen:
            seen.add(segment)
            if segments is not None:
                if end is not None and segment == thread_id:
                    path = self._parent_rollout(thread_id, segment)
                segments[segment] = (path, size)
            projection = conn.execute('SELECT next_rollout_byte_offset FROM thread_history_projection_state '
                                      'WHERE thread_id=?', (segment,)).fetchone()
            if projection is None or (end is None and projection[0] != size):
                return None
            # Historical segments may have grown past the edit. Verify the exact
            # excluded turn boundary before taking any rows from that segment.
            if end is not None and not conn.execute(
                    'SELECT 1 FROM thread_turns WHERE thread_id=? AND rollout_ordinal=? AND rollout_byte_offset=?',
                    (segment, end, size)).fetchone():
                return None
            clause = '' if end is None else 'AND rollout_ordinal < ? AND COALESCE(updated_at_ordinal, rollout_ordinal) < ? '
            args = (segment,) if end is None else (segment, end, end)
            rows.extend(conn.execute('SELECT ' + ', '.join(fields) + ' FROM thread_items WHERE thread_id=? '
                                     + clause + message_filter + 'ORDER BY rollout_ordinal DESC LIMIT ?',
                                     (*args, limit - len(rows))))
            if len(rows) >= limit or not header.get('history_base'):
                return rows
            base = header['history_base']
            if not isinstance(base, dict):
                return None
            segment, end, size = base.get('thread_id'), base.get('end_ordinal_exclusive'), base.get('end_byte_offset')
            if (not isinstance(segment, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', segment)
                    or type(end) is not int or type(size) is not int or min(end, size) < 0):
                return None
            if size == 0:
                return rows
            if segment == thread_id:
                header = {}  # Original physical index, possibly already compacted off disk.
            else:
                path = self._parent_rollout(thread_id, segment)
                if path is None:
                    return None
                header = self._segment_header(path)
        return None

    def history_revision(self, thread_id):
        return self.history_status(thread_id)['revision']

    def history_status(self, thread_id, *, include_usage=False, known_revision=None):
        rollout = Path(self.get(thread_id)['rollout_path']).resolve()
        if not self._rollout_in_home(rollout):
            raise ValueError('会话记录路径不在 Codex 数据目录中')
        try:
            stat = rollout.stat()
        except FileNotFoundError:
            saved = _retained_index_module().pin(self.home, thread_id)
            return {'revision': ['native-index-retained', *saved['file'], saved['end'], *saved['signature'],
                    *(saved['lastTurn'].get(key) for key in ('status', 'started_at', 'completed_at', 'error_json'))]}
        revision = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
        result = {'revision': revision}
        if include_usage and revision != known_revision:
            result['tokenUsage'] = _last_recorded_usage(_rollout_metadata_tail(rollout, stat.st_size), thread_id)
        return result

    def transfer_snapshot(self, thread_id):
        """Pin the existing rollout prefix. A later append cannot enter this read."""
        meta = self.get(thread_id)
        path = Path(meta['rollout_path']).resolve()
        if not self._rollout_in_home(path):
            raise ValueError('会话记录路径不在 Codex 数据目录中')
        if not path.exists():
            return {'meta': meta, 'segments': [], 'nativeIndex': _retained_index_module().pin(self.home, thread_id),
                    'warnings': ['原始历史文件已缺失，已读取原生保存记录，较早内容可能不完整']}
        segments, seen, end = [], set(), None
        while True:
            with path.open('rb') as stream:
                stamp = os.fstat(stream.fileno())
                offset = stamp.st_size if end is None else end
                if not 0 <= offset <= stamp.st_size:
                    raise ValueError('聊天历史分界点无效')
                while offset:
                    start = max(0, offset - 65536)
                    stream.seek(start)
                    last_newline = stream.read(offset - start).rfind(b'\n')
                    if last_newline >= 0:
                        offset = start + last_newline + 1
                        break
                    offset = start
                if end is not None and offset != end:
                    raise ValueError('聊天历史分界点不在记录边界')
            segments.append([str(path), stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, offset])
            header = self._segment_header(path)
            base = header.get('history_base')
            if not base:
                break
            parent = base.get('thread_id') if isinstance(base, dict) else None
            end = base.get('end_byte_offset') if isinstance(base, dict) else None
            if ((header.get('id') != thread_id if not seen else not isinstance(header.get('id'), str)) or not isinstance(parent, str) or parent in seen
                    or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', parent)
                    or type(end) is not int or end < 0):
                raise ValueError('无法核验聊天历史来源')
            if end == 0:
                break
            seen.add(parent)
            path = self._parent_rollout(thread_id, parent)
            if path is None:
                return {'meta': {key: meta.get(key) for key in ('name', 'title', 'cwd', 'model', 'model_provider')},
                        'segments': list(reversed(segments)),
                        'warnings': ['较早历史分段暂不可读取，已保留当前可读取部分，记录可能不完整']}
        return {'meta': {key: meta.get(key) for key in ('name', 'title', 'cwd', 'model', 'model_provider')},
                'segments': list(reversed(segments))}

    def transfer_index_turns(self, snapshot):
        yield from _retained_index_module().turns(self.home, snapshot)

    def transfer_index_page(self, thread_id, snapshot, offset=-1):
        self.get(thread_id)
        if snapshot.get('id') != thread_id:
            raise ValueError('聊天历史来源不匹配')
        turn = next(_retained_index_module().turns(self.home, snapshot, after=offset), None)
        return {'turn': turn, 'next': turn['_archiveNextOrdinal'] if turn else snapshot['end']}

    def transfer_page(self, thread_id, meta, segment, offset=0, page_bytes=2 * 1024 * 1024, record_reader=None,
                      join_text=None):
        """Read complete turns from a pinned segment and preserve native row identities."""
        name, dev, ino, size, modified, end = segment
        path = Path(name)
        if not self._rollout_in_home(path) or path.resolve(strict=True) != path:
            raise ValueError('会话记录路径不在 Codex 数据目录中')
        def verify(stream):
            stamp = os.fstat(stream.fileno())
            if ((stamp.st_dev, stamp.st_ino) != (dev, ino) or stamp.st_size < size
                    or stamp.st_size == size and stamp.st_mtime_ns != modified):
                raise ValueError('聊天记录已改写，请重新准备传输')
        records = []
        with path.open('rb') as stream:
            verify(stream)
            stream.seek(offset)
            while stream.tell() < end:
                position = stream.tell()
                if record_reader:
                    record = record_reader(stream, end)
                else:
                    record = next(_records([stream.readline(end - position)]), {})
                if stream.tell() <= position:
                    raise ValueError('聊天记录已经缩短，请重新准备传输')
                if records and position - offset >= page_bytes:
                    if record.get('type') == 'event_msg' and (record.get('payload') or {}).get('type') == 'task_started':
                        stream.seek(position)
                        break
                if record.get('type') in ('event_msg', 'turn_context', 'response_item'):
                    records.append(record)
            verify(stream)
            next_offset = stream.tell()
        return {'state': self._decode_history(thread_id, meta, records, next_offset == end, join_text=join_text),
                'next': next_offset}

    def history(self, thread_id, turn_limit=None, item_limit=None):
        meta = self.get(thread_id)
        path = Path(meta['rollout_path']).resolve()
        if not self._rollout_in_home(path):
            raise ValueError('会话记录路径不在 Codex 数据目录中')
        if not path.exists():
            index = _retained_index_module()
            return index.history(self.home, meta, index.pin(self.home, thread_id), max(1, int(item_limit or 40)))
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
                segments, sources = {}, {}
                columns = {row[1] for row in conn.execute('PRAGMA table_info(thread_turns)')}
                has_offsets = 'rollout_byte_offset' in columns
                rows = self._index_tail(conn, rollout, thread_id, rollout_stat.st_size, item_limit + 1, segments)
                if not rows:
                    return None
                more = len(rows) > item_limit
                selected = list(reversed(rows[:item_limit]))
                metadata_rows = self._index_tail(conn, rollout, thread_id, rollout_stat.st_size,
                                                 100, segments, metadata=True) or []
                turns, headers = {}, {}

                def header_for(row):
                    if row['turn_id'] not in headers:
                        header = conn.execute('SELECT turn_id, status, error_json, started_at, first_user_item_id, '
                                              + ('rollout_byte_offset ' if has_offsets else 'NULL AS rollout_byte_offset ')
                                              + 'FROM thread_turns WHERE thread_id=? AND turn_id=?',
                                              (row['thread_id'], row['turn_id'])).fetchone()
                        if header is None:
                            raise ValueError('Native item has no turn header')
                        path, size = segments[row['thread_id']]
                        if path is not None and type(header['rollout_byte_offset']) is int:
                            sources[row['turn_id']] = (path, header['rollout_byte_offset'], size)
                        # Native index clocks use Unix seconds, like thread/read.
                        # Normalize here before joining rollout ISO completion times.
                        headers[row['turn_id']] = {'turnId': row['turn_id'], 'status': header['status'],
                                                'turnStartedAtMs': header['started_at'] * 1000 if header['started_at'] else None,
                                                '_firstUserItemId': header['first_user_item_id'],
                                                'error': json.loads(header['error_json']) if header['error_json'] else None,
                                                'items': []}
                    return headers[row['turn_id']]

                for row in selected:
                    if row['turn_id'] not in turns:
                        turns[row['turn_id']] = {**header_for(row), 'items': [],
                                                '_itemsComplete': not (more and row['turn_id'] == rows[item_limit]['turn_id'])}
                    item = json.loads(row['item_json'])
                    if not isinstance(item, dict) or not isinstance(item.get('type'), str):
                        return None
                    if item.get('type') in ('agentMessage', 'assistantMessage', 'userMessage', 'steeringUserMessage') and item.get('timestamp') is None:
                        item.update(_indexed_item_clock(row, item.get('id')))
                    turns[row['turn_id']]['items'].append(item)
                metadata_turns = {}
                for row in reversed(metadata_rows):
                    if not isinstance(row['item_id'], str) or not row['item_id']:
                        continue
                    kind = {'AgentMessage': 'agentMessage', 'UserMessage': 'userMessage'}.get(row['item_type'], row['item_type'])
                    role = 'assistant' if kind in ('agentMessage', 'assistantMessage') else 'user'
                    native = header_for(row)
                    turn = metadata_turns.setdefault(row['turn_id'], {
                        'id': row['turn_id'], 'status': native['status'], 'startedAt': native['turnStartedAtMs'],
                        'model': None, 'effort': None, 'messages': []})
                    turn['messages'].append({'id': row['item_id'], 'kind': kind, 'role': role,
                                             **_indexed_item_clock(row, row['item_id'])})
                metadata_keys = {(turn['id'], item['id'], item['kind'])
                                 for turn in metadata_turns.values() for item in turn['messages']}
                # Older body pages already loaded their own native metadata.
                # Keep those fields as well, without expanding the independent
                # adjacent-message query or rereading any additional bodies.
                for native in turns.values():
                    turn = metadata_turns.setdefault(native['turnId'], {
                        'id': native['turnId'], 'status': native['status'], 'startedAt': native['turnStartedAtMs'],
                        'model': None, 'effort': None, 'messages': []})
                    for item in native['items']:
                        kind = item.get('type')
                        if (kind not in ('agentMessage', 'assistantMessage', 'userMessage', 'steeringUserMessage')
                                or not isinstance(item.get('id'), str) or not item['id']):
                            continue
                        if (native['turnId'], item.get('id'), kind) in metadata_keys:
                            continue
                        turn['messages'].append({'kind': kind, 'role': 'assistant' if kind in ('agentMessage', 'assistantMessage') else 'user',
                                                 **{k: item[k] for k in ('id', 'timestamp', '_timestampSource', '_syntheticId') if k in item}})
                for turn in turns.values():
                    calls = {item.get('call_id', item.get('id')) for item in turn['items']
                             if item.get('type') in ('function_call', 'custom_tool_call')}
                    if any(item.get('type') in ('function_call_output', 'custom_tool_call_output')
                           and item.get('call_id') not in calls for item in turn['items']):
                        # Legacy raw items need their call to keep row identities
                        # stable. Fall back to a complete-turn tail at this edge.
                        return None
            message_times, completed_turns, labels, token_usage = self._indexed_message_times(
                thread_id, rollout, rollout_stat, include_usage=True)
            for turn_id, (path, offset, end) in sources.items():
                if 0 <= offset < end:
                    with path.open('rb') as stream:
                        stream.seek(offset)
                        turn_seen = False
                        while stream.tell() < end:
                            line = stream.readline(end - stream.tell())
                            if not line:
                                break
                            if b'"task_started"' not in line and b'"turn_context"' not in line:
                                continue
                            try:
                                record = json.loads(line)
                            except ValueError:
                                continue
                            payload = record.get('payload') or {}
                            if record.get('type') == 'event_msg' and payload.get('type') == 'task_started':
                                if turn_seen or payload.get('turn_id') != turn_id:
                                    break
                                turn_seen = True
                            if (record.get('type') == 'turn_context'
                                    and payload.get('thread_id', thread_id) == thread_id
                                    and (payload.get('turn_id') == turn_id or not payload.get('turn_id') and turn_seen)):
                                labels[turn_id] = recorded_turn_label(payload)
                                break
            for turn in turns.values():
                turn['_recordedReplyLabel'] = labels.get(turn['turnId'], {})
            for turn in metadata_turns.values():
                turn['_recordedReplyLabel'] = labels.get(turn['id'], {})
                if turn['id'] in completed_turns:
                    turn['completedAt'] = completed_turns[turn['id']]
                for item in turn['messages']:
                    key = (turn['id'], item['id'], item['kind'], item['role'])
                    if item.get('timestamp') is None and key in message_times:
                        item['timestamp'], item['_timestampSource'] = message_times[key]
            apply_rollout_message_times(turns.values(), message_times)
            for turn in turns.values():
                if turn['status'] in ('completed', 'interrupted', 'failed') and turn['turnId'] in completed_turns:
                    turn['completedAt'] = completed_turns[turn['turnId']]
            latest = rollout.stat()
            if (latest.st_ino, latest.st_size, latest.st_mtime_ns) != (rollout_stat.st_ino, rollout_stat.st_size, rollout_stat.st_mtime_ns):
                return None
            return {'id': thread_id, 'title': meta.get('name') or meta.get('title'), 'cwd': meta['cwd'],
                    'modelProvider': meta.get('model_provider'), 'latestModel': meta.get('model'),
                    'latestTokenUsageInfo': token_usage, 'tokenUsageFromHistory': True,
                    **({'_historyMetadata': {'id': thread_id, 'turns': list(metadata_turns.values())}} if metadata_turns else {}),
                    'turns': list(turns.values()), 'turnsPagination': {'hasLoadedOldest': not more},
                    'requests': [], 'threadRuntimeStatus': {'type': 'notLoaded'}, 'historyReadSource': 'native-index'}
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return None

    def _indexed_message_times(self, thread_id, rollout, stat, include_turns=False, include_labels=False,
                               *, include_usage=False):
        def result(data):
            return data if include_usage else data[:3] if include_labels else data[:2] if include_turns else data[0]

        key = ('message-times', thread_id, str(rollout), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with self.history_lock:
            if key in self.history_cache:
                self.history_cache.move_to_end(key)
                return result(self.history_cache[key])
        raw = _rollout_metadata_tail(rollout, stat.st_size)
        usage = _last_recorded_usage(raw, thread_id)
        times = (*rollout_message_times(raw, thread_id, True, True), usage)
        with self.history_lock:
            self.history_cache[key] = times
            while len(self.history_cache) > 8:
                self.history_cache.popitem(last=False)
        return result(times)

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
        raw, complete = self._history_tail(resolved, thread_id, turn_limit)
        lines = raw.splitlines()
        result = self._decode_history(thread_id, meta, _records(lines), complete, clock_records=_records(lines))
        with self.history_lock:
            if len(raw) <= 4 * 1024 * 1024:
                self.history_cache[cache_key] = result
            while len(self.history_cache) > 8:
                self.history_cache.popitem(last=False)
        return copy.deepcopy(result)

    @staticmethod
    def _decode_history(thread_id, meta, records, complete, clock_records=None, join_text=None):
        items, turns, current = [], [], None
        persisted_user_ids = []
        token_usage = None
        for record in records:
            payload = record.get("payload", {})
            if record.get("type") == "event_msg" and payload.get("type") == "task_started":
                current = {"turnId": payload.get("turn_id"), "status": "inProgress", "items": [],
                           "startedAt": record.get("timestamp"), "_recordedReplyLabel": {}}
                turns.append(current)
                items = current["items"]
            elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
                token_usage = _recorded_token_usage(payload) or token_usage
            elif record.get("type") == "turn_context" and current is not None:
                settings = (payload.get('collaboration_mode') or {}).get('settings') or {}
                current['params'] = {'model':payload.get('model') or settings.get('model'),
                                     'effort':payload.get('effort') or settings.get('reasoning_effort')}
                if (payload.get('thread_id', thread_id) == thread_id and
                        payload.get('turn_id', current['turnId']) == current['turnId']):
                    current['_recordedReplyLabel'] = recorded_turn_label(payload)
            elif record.get("type") == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
                if (current and payload.get('thread_id', thread_id) == thread_id
                        and payload.get('turn_id', current['turnId']) == current['turnId']):
                    current["status"] = "completed" if payload["type"] == "task_complete" else "interrupted"
                    if record.get('timestamp') is not None:
                        current['completedAt'] = record['timestamp']
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
                    native_id = payload.get('id')
                    identity = {'id': native_id if isinstance(native_id, str) and native_id else str(len(items)),
                                '_syntheticId': not isinstance(native_id, str) or not native_id,
                                '_timestampSource': 'rollout-response-item'}
                    if role == "user":
                        items.append({**identity, "type": "userMessage", "content": payload.get("content", []), "timestamp": record.get("timestamp")})
                    else:
                        texts = [x.get('text', '') for x in payload.get('content', []) if isinstance(x, dict)]
                        text = (join_text or '\n'.join)(texts)
                        items.append({**identity, "type": "agentMessage", "text": text, "phase": payload.get("phase"), "timestamp": record.get("timestamp"),
                                      **{k: payload[k] for k in ('model', 'effort') if k in payload}})
                elif payload.get("type") in ("agent_message", "agentMessage"):
                    native_id = payload.get('id')
                    items.append({"id": native_id if isinstance(native_id, str) and native_id else str(len(items)),
                                  '_syntheticId': not isinstance(native_id, str) or not native_id,
                                  '_timestampSource': 'rollout-response-item',
                                  "type": "agentMessage", "text": payload.get("text", ""), "timestamp": record.get("timestamp"),
                                  **{k: payload[k] for k in ('model', 'effort') if k in payload}})
                elif payload.get("type") in ("function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"):
                    items.append({"id": payload.get("call_id", str(len(items))), "type": "storedToolEvent", **payload})
                elif payload.get('type') == 'reasoning':
                    summary = [x.get('text') if isinstance(x, dict) else x for x in payload.get('summary') or ()]
                    if any(isinstance(x, str) and x.strip() for x in summary):
                        items.append({'id': payload.get('id') or str(len(items)), 'type': 'reasoning', 'summary': summary})
        apply_rollout_message_times(turns, rollout_message_times(b'', thread_id,
                                    records=records if clock_records is None else clock_records))
        # Rollout parsing already visited these records. Retain the native patch
        # projection once so a later bounded item window need not reopen bodies.
        for turn in turns:
            turn['_nativeFileChanges'] = native_turn_file_changes(turn)
            turn['_nativeFileChangesBasis'] = 'recorded'
        return {"id": thread_id, "title": meta.get("name") or meta.get("title"), "cwd": meta["cwd"],
                "latestModel": meta.get('model') or next((t['params']['model'] for t in reversed(turns) if (t.get('params') or {}).get('model')), None),
                "latestReasoningEffort": next((t['params']['effort'] for t in reversed(turns) if (t.get('params') or {}).get('effort')), None),
                "modelProvider": meta.get("model_provider"),
                "latestTokenUsageInfo": token_usage, "tokenUsageFromHistory": True,
                "persistedUserMessageIds": persisted_user_ids,
                "turns": turns, "turnsPagination": {"hasLoadedOldest": complete}, "requests": [], "threadRuntimeStatus": {"type": "notLoaded"}}
