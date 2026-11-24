"""Passive history export into a private, version-addressed range store."""
import base64
import copy
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime
from itertools import chain
from pathlib import Path

from bridge.clients.codex.ipc_json import FrameReader, StoredString, join_text
from bridge.features.sessions.model import merge_saved_message_times, normalize_state, ordered_turns, prepend_saved_process
from bridge.features.sessions.timeline import Timeline, encoded
from bridge.features.workspace.uploads import image_type
from bridge.features.workspace.workspace import Workspace

CHUNK_BYTES = 512 * 1024
PAGE_ITEMS = 60
CATALOG_ITEMS = 100
HASH = re.compile(r'[0-9a-f]{64}')
PROVIDERS = ('claude', 'deepseek', 'zcode', 'antigravity')
LOG = logging.getLogger(__name__)


def _hash(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _millis(stamp):
    if isinstance(stamp, (float, int)):
        return int(stamp if stamp >= 10**12 else stamp * 1000)
    if isinstance(stamp, str):
        try:
            return int(datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000)
        except (ValueError, OverflowError):
            pass
    return 0


def _session(client, entry, host, row, source_key):
    sid = row['id']
    return {'id': _hash(source_key), 'sessionId': sid, 'client': client, 'entry': entry,
            'host': host, 'title': (row.get('name') or row.get('title') or '未命名聊天')[:512],
            'updatedAt': _millis(row.get('updated_at_ms') or row.get('updated_at') or row.get('updatedAt')),
            'sourceBytes': row.get('sourceBytes'), 'archived': bool(row.get('archived')),
            'cacheSessionId': encoded([client if client == 'codex' else entry, host, sid]).decode()}


def _local_file_size(path):
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def _source_error(session, error):
    LOG.warning('Unable to pin history transfer source (%s, %s)', session['client'], type(error).__name__, exc_info=True)
    if isinstance(error, FileNotFoundError):
        reason, message = 'missing_history', '原始历史及原生保存记录暂不可读取'
    elif isinstance(error, PermissionError):
        reason, message = 'permission_denied', '没有权限读取原会话保存记录'
    elif isinstance(error, sqlite3.Error):
        reason, message = 'history_database', '原生历史数据库暂不可读取，请稍后重试'
    elif isinstance(error, ValueError) and str(error) in ('找不到原会话历史分段', '无法核验聊天历史来源'):
        reason, message = 'history_lineage', '原会话的历史分段暂不可读取'
    else:
        reason, message = 'source_unavailable', '原会话接入暂不可读取，请检查对应客户端连接'
    return {'id': session['id'], 'message': message, 'reason': reason}


class HistorySources:
    """Resolve the same configured entry/session pairs as the ordinary API."""
    def __init__(self, bridge, desktop, desktop_uploads=None):
        self.bridge = bridge
        self.desktop = desktop
        self.desktop_uploads = desktop_uploads

    def _codex_targets(self):
        bridge = self.bridge
        if self.desktop is None or self.desktop.enabled('codex'):
            yield 'local'
            yield from bridge.hosts.hosts()
        entries = getattr(bridge, 'entries', None)
        if entries:
            for host in entries.hosts():
                if self.desktop is None or self.desktop.enabled(host):
                    yield host

    def _desktop_targets(self):
        manager = self.desktop
        if not manager:
            return
        for entry in dict.fromkeys([*PROVIDERS, *manager.navigation_clients()]):
            family = entry.partition(':')[0]
            if family not in PROVIDERS or not manager.enabled(entry):
                continue
            yield family, entry

    def inventory(self):
        result, unavailable = {}, []
        for host in self._codex_targets():
            try:
                target = self.bridge if host == 'local' else self.bridge.for_host(host)
                for archived in (False, True):
                    offset = 0
                    while True:
                        rows = target.store.list(limit=CATALOG_ITEMS, offset=offset, archived=archived,
                                                 transfer=True)
                        for row in rows:
                            native_host = 'local' if host == 'local' or host.startswith('codex:') else host
                            key = ['codex', native_host, row.get('sourceHome') or host, row['id']]
                            item = _session('codex', host, host, {**row, 'archived': archived}, key)
                            result.setdefault(item['id'], item)
                        if len(rows) < CATALOG_ITEMS:
                            break
                        offset += len(rows)
            except (OSError, RuntimeError, ValueError, KeyError):
                unavailable.append({'client': 'codex', 'entry': host})
        for family, entry in self._desktop_targets():
            try:
                manager = self.authorize({'client': family, 'entry': entry})
                value = manager.call(family, 'list')
                adapter = manager.adapters.get(family)
                for row in value.get('sessions', []):
                    local = row.get('host') in (None, 'local')
                    native_id = row.get('nativeId') or row['id']
                    directory = (row.get('surfaceDirKey') or str(getattr(adapter, 'directory', '')) or entry)
                    key = [family, row.get('host', 'local'), row.get('backend', 'desktop'), directory, native_id]
                    # Only metadata already returned by inventory is inspected.
                    # Some adapters' record_path runs a native command.
                    path = row.get('path')
                    size = _local_file_size(Path(path)) if local and isinstance(path, str) and Path(path).is_absolute() else None
                    item = _session(family, entry, 'local', {**row, 'sourceBytes': size}, key)
                    result.setdefault(item['id'], item)
            except (OSError, RuntimeError, ValueError, KeyError):
                unavailable.append({'client': family, 'entry': entry})
        rows = sorted(result.values(), key=lambda row: (row['updatedAt'], row['id']), reverse=True)
        return rows, unavailable

    def authorize(self, session):
        family, entry = session['client'], session['entry']
        if family == 'codex':
            if self.desktop:
                self.desktop.require_enabled(entry if entry.startswith(('codex:', 'wsl:')) else 'codex')
            return self.bridge.for_host(entry)
        manager = self.desktop.resolve_instance(family, entry) if ':' in entry else self.desktop
        if manager is None:
            raise ValueError('桌面会话接入不可用')
        manager.require_enabled(family)
        return manager

    def pin(self, session):
        target = self.authorize(session)
        sid = session['sessionId']
        if session['client'] == 'codex':
            owner = _current_owner_turn(target, sid)
            try:
                snapshot = target.store.transfer_snapshot(sid)
            except FileNotFoundError:
                if owner is None:
                    raise
                snapshot = {'meta': {}, 'segments': []}
            return {**snapshot, 'owner': owner}
        # Hold only the revision while pinning multiple sessions. The fallback
        # sync reader verifies the source again while traversing native pages.
        return _sync_read(target, session, 'watch')['version']

    def pin_local(self, session):
        if session['client'] == 'codex' and (session['host'] == 'local' or session['host'].startswith('codex:')):
            return self.pin(session)

    def records(self, session, snapshot):
        target = self.authorize(session)
        if session['client'] == 'codex':
            yield from _codex_rows(target, session['sessionId'], snapshot)
        else:
            uploads = getattr(target, 'uploads', None) or self.desktop_uploads
            newest = _sync_read(target, session, 'recent', transfer=True)
            if newest.get('sourceVersion') != snapshot:
                raise ValueError('聊天历史已经变化，请重新准备传输')
            yield from _desktop_rows(target, session, newest, uploads)


def _stamp_row(row, turn, initial_users, offset):
    result = {**row, 'id': row['key'], 'order': row['order'] + offset}
    if row['role'] == 'user':
        result['originalQuestion'] = row['key'] in initial_users
    if row.get('kind') == 'processPage':
        result['processCursor'] = row['key']
        return result, 0
    result['detailCursor'] = row['key']
    initial = next((item for item in turn['messages']
                    if item.get('role') == 'user' and item.get('interaction') != 'response'), {})
    return result, _millis(initial.get('timestamp') or turn.get('startedAt'))


def _current_owner_turn(target, sid):
    live = getattr(target, 'live', None)
    if not isinstance(live, dict):
        return None
    with target.lock:
        session = live.get(sid)
    if session is None:
        return None
    with session.condition:
        state = session.state
        if not session.connected or not isinstance(state, dict) or state.get('id', state.get('sessionId')) != sid:
            return None
        turns = ordered_turns(state)
        if not turns or not turns[-1].get('turnId'):
            return None
        return {'id': sid, 'turns': [copy.deepcopy(turns[-1])]}


def _merge_owner_turn(live, saved):
    # A completed saved turn is newer than a still-running owner snapshot.
    if not live['messages'] or (saved['status'] in ('completed', 'interrupted', 'failed')
                               and live['status'] in ('inProgress', 'active')):
        return saved
    prepend_saved_process([live], [saved])
    if live.get('itemsComplete') is False:
        return None
    if not live.get('startedAt'):
        live['startedAt'] = saved.get('startedAt')
    if saved.get('_recordedReplyLabel', {}).get('model'):
        live['_recordedReplyLabel'] = saved['_recordedReplyLabel']
    return live


class _RolloutReader(FrameReader):
    def history_leaf(self, path):
        if path[:1] == ('payload',):
            path = ('params', 'change', 'conversationState', 'turns', 0, 'items', 0, *path[1:])
        return super().history_leaf(path)


def _rollout_record_reader():
    storage = None

    def read(stream, end):
        nonlocal storage
        # Most rollout records are small. Decode them with the bounded native
        # JSON parser; retain the streaming/spooled reader for large tool output.
        start = stream.tell()
        raw = stream.readline(min(65537, end - start))
        if raw.endswith(b'\n') or stream.tell() == end:
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                return {}
            reader = _RolloutReader(None, None, 64 * 1024)
            reader.storage = storage
            try:
                value = reader.retain_decoded(value)
                return value if isinstance(value, dict) else {}
            finally:
                storage = reader.storage
        stream.seek(start)
        line_ended = False

        class Line:
            @staticmethod
            def recv(size):
                nonlocal line_ended
                if line_ended:
                    return b''
                data = stream.readline(min(size, end - stream.tell()))
                line_ended = not data or data.endswith(b'\n')
                return data

        reader = _RolloutReader(Line(), None, 64 * 1024)
        reader.storage = storage
        try:
            return reader.read()
        except ValueError:
            while Line.recv(65536):
                pass
            return {}
        finally:
            storage = reader.storage

    return read


def _codex_rows(target, sid, snapshot):
    """Use the ordinary row projector in turn-complete windows."""
    filesystem_local = getattr(target.store, 'home', None) is not None
    uploads = getattr(target, 'uploads', None)
    live_view = (normalize_state(snapshot['owner'], process_bodies=False, retain_transfer_attachments=True)
                 if snapshot.get('owner') else None)
    live_turn = live_view['turns'][0] if live_view and live_view['turns'] else None
    order = 0
    def saved_pages():
        if snapshot.get('nativeIndex'):
            for turn in target.store.transfer_index_turns(snapshot['nativeIndex']):
                yield {'id': sid, 'turns': [turn]}
            return
        for segment in snapshot['segments']:
            offset = 0
            while offset < segment[-1]:
                options = {'record_reader': _rollout_record_reader(), 'join_text': join_text} if filesystem_local else {}
                page = target.store.transfer_page(sid, snapshot['meta'], segment, offset, **options)
                if not offset < page['next'] <= segment[-1]:
                    raise ValueError('聊天历史读取未继续推进')
                offset = page['next']
                yield page['state']

    for state in saved_pages():
        view = normalize_state(state, process_bodies=False, retain_transfer_attachments=True)
        if live_turn:
            for idx, turn in enumerate(view['turns']):
                if turn['id'] == live_turn['id']:
                    merge_saved_message_times(live_view, view)
                    merged = _merge_owner_turn(live_turn, turn)
                    if merged is None:
                        snapshot.setdefault('warnings', []).append('当前回合有未同步内容，稍后可补传')
                    else:
                        view['turns'][idx] = merged
                    live_turn = None
                    break
        order += (yield from _project_codex_rows(view, sid, uploads, filesystem_local, order))
    if live_turn:
        if live_turn.get('itemsComplete') is False:
            if not snapshot['segments'] and not snapshot.get('nativeIndex'):
                raise ValueError('原会话仅提供当前回合的部分内容')
            snapshot.setdefault('warnings', []).append('当前回合有未同步内容，稍后可补传')
        else:
            yield from _project_codex_rows(live_view, sid, uploads, filesystem_local, order)


def _project_codex_rows(view, sid, uploads, filesystem_local, order):
    timeline = Timeline()
    timeline.update({**view, 'sequence': 0}, conversation=True, retain_attachments=True)
    turns = {turn['id']: turn for turn in view['turns']}
    # File evidence is from original attachments or ImageView, not prose.
    for row in timeline.conversation_rows:
        group = timeline.process_groups.get(row['key']) or ()
        for raw in chain((row,), group):
            turn = turns[raw['turnId']]
            record, question_time = _stamp_row(raw, turn, timeline.initial_users, order)
            if group and raw is row:
                record.update(order=record['order'] - .5, count=len(group), processCount=len(group),
                              firstKey=group[0]['key'], lastKey=group[-1]['key'])
                initial = next((item for item in turn['messages']
                                if item.get('role') == 'user' and item.get('interaction') != 'response'), {})
                question_time = _millis(initial.get('timestamp') or turn.get('startedAt'))
                yield record, question_time, (), ()
                continue
            attachments = timeline.source_attachments.get(raw['key'])
            if raw['key'] in timeline.detail_sources:
                detail = timeline.detail(timeline.cursor(raw['key']), version=raw['version'])
                attachments = timeline.detail_image_evidence.get(raw['key']) or detail.get('attachments')
            if raw is not row:
                record['hasDetail'] = bool(timeline.details.get(raw['key']) or attachments)
            candidates = _attachment_sources(attachments or (), uploads=uploads, sid=sid,
                local_image=filesystem_local and (raw.get('role') == 'user' or raw.get('kind') in ('ImageView', 'imageView')))
            yield record, question_time, _body_parts(timeline, raw), candidates
    return len(timeline.rows)


def _body_parts(timeline, row):
    if not row.get('truncated'):
        return
    offset = 0
    while True:
        value = timeline.detail(timeline.cursor(row['key']), offset=offset, version=row['version'])
        yield {field: value[field] for field in ('offset', 'text', 'next')}
        offset = value['next']
        if offset is None:
            return


def _sync_read(target, session, mode, **options):
    scan = None
    while True:
        page = target.history_sync(session['client'], session['sessionId'], mode,
                                   {**options, **({'scan': scan} if scan else {})})
        if page.get('scanReset'):
            raise ValueError('聊天历史已经变化，请重新准备传输')
        if not page.get('pending'):
            return page
        next_scan = page.get('scan')
        if not next_scan or next_scan == scan:
            raise ValueError('聊天历史读取未继续推进')
        scan = next_scan


def _desktop_rows(target, session, newest, uploads):
    cursor, seen = None, set()
    page = newest
    source_version = newest.get('sourceVersion')
    while True:
        times = {turn['id']: _millis(turn.get('questionTime') or turn.get('startedAt')) for turn in page.get('turns', [])}
        for row in page.get('rows', []):
            row = {key: value for key, value in row.items() if key not in ('requestId',)}
            time_ms = times.get(row['turnId'], _millis(row.get('turnStartedAt')))
            yield from _desktop_record(target, session, row, time_ms, page.get('transferAttachments', {}), uploads)
            group = row.get('processCursor')
            if not group:
                continue
            after = None
            while True:
                process = _sync_read(target, session, 'process', key=group, limit=100, transfer=True,
                                     **({'after': after} if after else {}))
                for step in process['rows']:
                    yield from _desktop_record(target, session, {**step, 'order': row['order'] + step['order'] + .5},
                                               time_ms, process.get('transferAttachments', {}), uploads)
                new_after = process.get('next')
                if not new_after:
                    break
                if new_after == after:
                    raise ValueError('过程历史读取未继续推进')
                after = new_after
        if not page.get('hasMore'):
            if page.get('transferSourceComplete') is False:
                raise ValueError('原客户端尚未提供完整的会话历史')
            break
        cursor = page.get('before')
        if not cursor or cursor in seen:
            raise ValueError('原客户端尚未提供完整的会话历史')
        seen.add(cursor)
        page = _sync_read(target, session, 'older', before=cursor, limit=20, transfer=True)
    if source_version and _sync_read(target, session, 'watch').get('version') != source_version:
        raise ValueError('聊天历史已经变化，请重新准备传输')


def _desktop_record(target, session, row, question_time, evidence, uploads):
    sid, family = session['sessionId'], session['client']
    attachments = evidence.get(row['key'], row.get('attachments') or ())

    def parts():
        if not row.get('truncated'):
            return
        offset = 0
        while True:
            value = _sync_read(target, session, 'body', key=row['key'], offset=offset, version=row['version'])
            if value.get('version') != row['version'] or value.get('offset') != offset:
                raise ValueError('聊天正文版本已经变化，请重新准备传输')
            yield {field: value[field] for field in ('offset', 'text', 'next')}
            offset = value['next']
            if offset is None:
                break

    def sources():
        native = [(a, a.get('desktopId')) for a in attachments if a.get('desktopId')]
        for attachment, identifier in native:
            try:
                value, variant = target.image_preview(family, sid, identifier)
            except (OSError, ValueError, KeyError):
                continue
            data = value.get('previewBytes')
            if isinstance(data, bytes):
                yield attachment, {'data': data, 'mime': value.get('previewMime'), 'variant': variant if variant == 'thumb' else 'original',
                                   'name': attachment.get('name') or value.get('name') or 'image'}
        if uploads and hasattr(uploads, 'scope'):
            yield from _attachment_sources(attachments, uploads=uploads.store, sid=uploads.scope(family, sid), local_image=False)
        else:
            yield from _attachment_sources(attachments, uploads=None, sid=sid, local_image=False)

    yield row, question_time, parts(), sources()


def _attachment_sources(attachments, *, uploads, sid, local_image):
    """Identify immutable uploads, native image evidence and embedded images."""
    paths = {a['path'] for a in attachments if isinstance(a.get('path'), str)}
    by_path = uploads.by_path(sid, paths) if uploads and paths and hasattr(uploads, 'by_path') else {}
    for a in attachments:
        path = a.get('path')
        value = a.get('data') or a.get('image_url') or a.get('imageUrl') or a.get('url')
        if isinstance(value, dict):
            value = value.get('url')
        start = 0
        if isinstance(value, str) and value.startswith('data:'):
            header, marker, tail = value.partition(';base64,')
            if not marker:
                continue
            if isinstance(value, StoredString):
                start = len(header) + len(marker)
            else:
                value = tail
        if value and hasattr(value, 'chunks'):
            yield a, {'chunks': value.chunks, 'name': a.get('name') or 'image', 'imageOnly': True}
            continue
        upload = by_path.get(str(Path(path).resolve())) if isinstance(path, str) and by_path else None
        if upload:
            yield a, {'path': Path(upload['localPath']), 'name': upload['name'],
                      'mime': upload.get('image'), 'uploadId': upload['id']}
        elif isinstance(path, str) and a.get('type') in ('localImage', 'image', 'input_image') and local_image:
            target = Path(path)
            if target.is_absolute():
                yield a, {'path': target, 'name': a.get('name') or target.name, 'imageOnly': True}
        elif isinstance(value, str) and (a.get('data') or not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', value)):
            if a.get('type') in ('toolImage', 'input_image', 'image') and value:
                yield a, {'base64': value, 'name': a.get('name') or 'image', 'imageOnly': True}
        elif isinstance(value, StoredString) and start:
            yield a, {'base64': value, 'start': start, 'name': a.get('name') or 'image', 'imageOnly': True}
        else:
            thumb = a.get('previewData') or a.get('thumbnail') or a.get('thumb')
            if isinstance(thumb, str) and re.match(r'data:image/(?:png|jpeg|gif|webp);base64,', thumb):
                header, _, data = thumb.partition(',')
                source = {'base64': thumb, 'start': len(header) + 1} if isinstance(thumb, StoredString) else {'base64': data}
                yield a, {**source, 'name': a.get('name') or 'image',
                          'imageOnly': True, 'variant': 'thumb'}


@contextmanager
def _attachment_stream(source):
    if 'path' in source:
        path = source['path'].resolve(strict=True)
        # Reuse the existing no-symlink directory/file opener. The chunk store
        # is the snapshot, so no second temporary file of this size is needed.
        root = Workspace(path.anchor)
        parts = path.relative_to(path.anchor).parts
        with root.directory(parts[:-1]) as (directory, fd):
            handle = root.open_file(directory, fd, parts[-1], os.O_RDONLY)
            with os.fdopen(handle, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('原附件不是普通文件')
                head = stream.read(12)
                mime = image_type(head)
                if source.get('imageOnly') and not mime:
                    raise ValueError('原图片格式不可读取')

                def checked():
                    yield from _stream_chunks(stream, head)
                    after = os.fstat(stream.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('准备期间原附件已变化')

                yield mime or source.get('mime') or mimetypes.guess_type(source['name'])[0] or 'application/octet-stream', checked()
        return
    if 'data' in source:
        data = source['data']
        yield source['mime'] or 'application/octet-stream', _stream_chunks((data,))
        return

    def parts():
        data = source['base64']
        step = 4 * 65536
        stored = isinstance(data, StoredString)
        size = data.chars if stored else len(data)
        for i in range(source.get('start', 0), size, step):
            text = data.slice(i, min(i + step, size)) if stored else data[i:i + step]
            if i + step < size and '=' in text:
                raise ValueError('图片编码无效')
            yield base64.b64decode(text, validate=True)

    chunks = source['chunks']() if 'chunks' in source else parts()
    head = next(chunks, b'')
    mime = image_type(head)
    if not mime:
        raise ValueError('原图片格式不可读取')
    yield mime, _stream_chunks(chunks, head)


def _stream_chunks(source, head=b''):
    pending = bytearray(head)
    reads = iter(lambda: source.read(65536), b'') if hasattr(source, 'read') else source
    for block in reads:
        pending.extend(block)
        while len(pending) >= CHUNK_BYTES:
            yield bytes(pending[:CHUNK_BYTES])
            del pending[:CHUNK_BYTES]
    if pending:
        yield bytes(pending)


def _record_json(row, question_time, parts):
    """Stream a one-row turn fragment, independent of the turn's total size."""
    safe = {key: value for key, value in row.items() if key not in ('requestId',)}
    message = {key: row[key] for key in ('key', 'version', 'role', 'text', 'order') if key in row}
    message['row'] = safe
    prefix = encoded({'id': row['turnId'], 'questionTime': question_time})
    yield prefix[:-1] + b',"messages":[' + encoded(message)[:-1] + b',"bodyParts":{'
    for index, part in enumerate(parts):
        yield (b',' if index else b'') + encoded(str(part['offset'])) + b':' + encoded(part)
    yield b'}}]}'


class HistoryTransfer:
    """Persist the prepared manifest and immutable chunks across gateway restarts."""
    def __init__(self, data_dir, sources):
        self.sources = sources
        self.root = Path(data_dir) / 'history-transfer'
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = self.root / 'transfer.sqlite'
        self.catalog_lock = threading.Lock()
        self.catalog_value = None
        self.work_slots = threading.BoundedSemaphore(2)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript("""
                CREATE TABLE IF NOT EXISTS manifests (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, captured INTEGER NOT NULL,
                    sessions INTEGER NOT NULL, estimate INTEGER NOT NULL,
                    status TEXT NOT NULL, errors TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS items (
                    ordinal INTEGER PRIMARY KEY, manifest TEXT NOT NULL, id TEXT NOT NULL,
                    scope TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL, bytes INTEGER NOT NULL,
                    descriptor TEXT NOT NULL, UNIQUE (manifest, id)
                );
                CREATE INDEX IF NOT EXISTS items_manifest ON items (manifest, kind, ordinal);
                CREATE INDEX IF NOT EXISTS items_version ON items (version);
                CREATE TABLE IF NOT EXISTS chunks (
                    version TEXT NOT NULL, sequence INTEGER NOT NULL, digest TEXT NOT NULL,
                    size INTEGER NOT NULL, body BLOB,
                    PRIMARY KEY (version, sequence)
                ) WITHOUT ROWID;
            """)
            if 'summary' not in {row[1] for row in db.execute('PRAGMA table_info(manifests)')}:
                db.execute("ALTER TABLE manifests ADD COLUMN summary TEXT NOT NULL DEFAULT '{}'")
            db.execute("UPDATE manifests SET status='failed',errors=? WHERE status='preparing'",
                       (self._json([{'message': '网关重启，重新开始即可继续下载'}]),))
        self.db.chmod(0o600)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.db, timeout=30)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    @staticmethod
    def _json(value):
        return encoded(value).decode()

    def _inventory(self, fresh=False):
        with self.catalog_lock:
            now = time.monotonic()
            if not fresh and self.catalog_value and now - self.catalog_value[0] < 20:
                return self.catalog_value[1:]
            sessions, unavailable = self.sources.inventory()
            self.catalog_value = (time.monotonic(), sessions, unavailable)
            return sessions, unavailable

    def catalog(self, cursor):
        sessions, unavailable = self._inventory(fresh=cursor == 0)
        page = sessions[cursor:cursor + CATALOG_ITEMS]
        after = cursor + len(page)
        return {'sessions': page, 'nextCursor': str(after) if after < len(sessions) else None,
                'unavailable': unavailable, 'sessionCount': len(sessions), 'recordBatch': 1}

    def start(self, owner, body):
        ids = body.get('ids')
        everything = body.get('all') is True
        estimate = body.get('estimate', False)
        clients = body.get('clients', [])
        priority = body.get('prioritySession')
        batch_records = body.get('batchRecords', False)
        if (set(body) - {'ids', 'all', 'estimate', 'clients', 'prioritySession', 'batchRecords'} or type(estimate) is not bool
                or type(batch_records) is not bool
                or priority is not None and (not isinstance(priority, str) or len(priority) > 4096)
                or not isinstance(clients, list) or any(client not in ('codex', *PROVIDERS) for client in clients)
                or 'all' in body and not everything or everything and ids is not None
                or not everything and (not isinstance(ids, list) or not ids or
                    any(not isinstance(item, str) or not HASH.fullmatch(item) for item in ids))):
            raise ValueError('传输选择无效')
        if not self.work_slots.acquire(blocking=False):
            raise ValueError('网关正在准备其他传输，请稍后重试')
        try:
            sessions, _ = self._inventory()
            index = {item['id']: item for item in sessions}
            if everything:
                selected = [row for row in sessions if not clients or row['client'] in clients]
            elif any(item not in index for item in ids):
                raise ValueError('聊天选择已变化，请刷新列表')
            else:
                selected = [index[item] for item in dict.fromkeys(ids)]
            if not selected:
                raise ValueError('聊天选择已变化，请刷新列表')
            if priority:
                selected.sort(key=lambda row: priority not in (row['id'], row['cacheSessionId'], row['sessionId']))
            capture = int(time.time() * 1000)
            transfer = secrets.token_hex(16)
            pinned, errors = {}, []
            for session in selected:
                try:
                    pinned[session['id']] = self.sources.pin_local(session)
                except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error) as error:
                    errors.append(_source_error(session, error))
            with self.connect() as db:
                cutoff = capture - 24 * 60 * 60 * 1000
                db.execute("DELETE FROM items WHERE manifest IN (SELECT id FROM manifests WHERE estimate=1 AND captured<? AND status!='preparing')",
                           (cutoff,))
                db.execute("DELETE FROM manifests WHERE estimate=1 AND captured<? AND status!='preparing'", (cutoff,))
                db.execute("DELETE FROM chunks WHERE body IS NULL AND NOT EXISTS (SELECT 1 FROM items WHERE items.version=chunks.version)")
                db.execute('INSERT INTO manifests(id,owner,captured,sessions,estimate,status) VALUES (?,?,?,?,?,?)',
                           (transfer, owner, capture, len(selected), estimate, 'preparing'))
            # Pin all inputs before projecting any selected session. Local
            # rollout snapshots are cheap stat/cursor reads.
            threading.Thread(target=self._prepare, args=(transfer, selected, estimate, pinned, errors, batch_records),
                             daemon=True, name='history-transfer').start()
        except BaseException:
            self.work_slots.release()
            raise
        return {'id': transfer, 'sessionCount': len(selected), 'capturedAt': capture,
                'status': 'preparing', 'estimate': estimate}

    def _prepare(self, transfer, selected, estimate, pinned, errors, batch_records=False):
        warnings, processed = [], len(errors)
        try:
            for session in selected:
                if session['id'] not in pinned:
                    continue
                try:
                    if pinned[session['id']] is None:
                        pinned[session['id']] = self.sources.pin(session)
                except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error) as error:
                    pinned.pop(session['id'])
                    errors.append(_source_error(session, error))
                    processed += 1
            for session in selected:
                if session['id'] not in pinned:
                    continue
                snapshot = pinned.pop(session['id'])
                with self.connect() as db:
                    db.execute('UPDATE manifests SET summary=? WHERE id=?',
                               (self._json({'processed': processed, 'currentTitle': session['title'], 'warnings': warnings}), transfer))
                try:
                    self._stage(transfer, session, snapshot, estimate, batch_records)
                    if isinstance(snapshot, dict):
                        warnings.extend({'id': session['id'], 'cacheSessionId': session['cacheSessionId'], 'message': text}
                                        for text in snapshot.get('warnings', []))
                except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
                    LOG.warning('Unable to prepare a history transfer source', exc_info=True)
                    with self.connect() as db:
                        db.execute('DELETE FROM items WHERE manifest=? AND scope=?', (transfer, session['id']))
                    errors.append({'id': session['id'], 'message': '无法准备该会话的完整记录'})
                processed += 1
                with self.connect() as db:
                    db.execute('UPDATE manifests SET errors=?, summary=? WHERE id=?',
                               (self._json(errors), self._json({'processed': processed, 'warnings': warnings}), transfer))
            with self.connect() as db:
                totals = {row['kind']: (row['count'], row['bytes']) for row in db.execute(
                    'SELECT kind,COUNT(*) AS count,SUM(bytes) AS bytes FROM items WHERE manifest=? GROUP BY kind', (transfer,))}
                records, text_bytes = totals.get('record', (0, 0))
                attachments, attachment_bytes = totals.get('attachment', (0, 0))
                summary = {'processed': processed, 'warnings': warnings,
                           'sessions': len(selected) - len(errors), 'records': records, 'attachments': attachments,
                           'textBytes': text_bytes, 'attachmentBytes': attachment_bytes}
                db.execute('UPDATE manifests SET status=?, errors=?, summary=? WHERE id=?',
                           ('ready' if len(errors) < len(selected) else 'failed', self._json(errors), self._json(summary), transfer))
        except Exception:
            LOG.exception('Unable to prepare a history transfer')
            with self.connect() as db:
                db.execute('UPDATE manifests SET status=?, errors=? WHERE id=?',
                           ('failed', self._json([{'message': '网关无法准备本次传输'}]), transfer))
        finally:
            self.work_slots.release()

    def _stage(self, transfer, session, snapshot, estimate, batch_records=False):
        batch, batch_meta, batch_bytes = [], [], 0
        batch_common = None

        def flush():
            nonlocal batch, batch_meta, batch_bytes, batch_common
            if not batch:
                return
            prefix = encoded({'id': batch_common['turnId'], 'questionTime': batch_common['questionTime']})
            payload = chain([prefix[:-1] + b',"messages":['], (part for index, message in enumerate(batch)
                for part in ((b',' if index else b''), message)), [b']}'])
            self._put(transfer, session['id'],
                      {**batch_common, 'kind': 'record', 'mime': 'application/json', 'name': 'record.json',
                       'records': batch_meta}, _stream_chunks(payload), estimate)
            batch, batch_meta, batch_bytes, batch_common = [], [], 0, None

        for row, question_time, parts, attachments in self.sources.records(session, snapshot):
            common = {'session': session, 'cacheSessionId': session['cacheSessionId'], 'turnId': row['turnId'],
                      'questionTime': question_time, 'key': row['key'], 'recordVersion': row['version'],
                      'order': row['order'], 'chunkBytes': CHUNK_BYTES}
            visible = []
            for attachment, source in attachments:
                path = attachment.get('path')
                desktop_id = attachment.get('desktopId')
                upload_id = source.get('uploadId') or attachment.get('uploadId')
                try:
                    if not desktop_id and not upload_id and source.get('imageOnly') and 'path' in source:
                        desktop_id = hashlib.sha256(str(source['path'].resolve()).encode()).hexdigest()
                    with _attachment_stream(source) as (mime, data):
                        descriptor = self._put(transfer, session['id'], {
                            **common, 'kind': 'attachment', 'name': source['name'], 'mime': mime,
                            'recordKey': row['key'],
                            'attachmentKey': upload_id or desktop_id or path,
                            **({'uploadId': upload_id} if upload_id else {}),
                            **({'desktopId': desktop_id} if desktop_id else {}),
                            **({'path': path} if path and not upload_id else {}),
                            'variant': source.get('variant', 'original')}, data, estimate)
                except (OSError, ValueError, PermissionError):
                    continue
                visible.append(({key: value for key, value in attachment.items() if key in
                                 ('type', 'name', 'path', 'desktopId', 'uploadId', 'thumbnail', 'thumb')},
                                {'offlineVersion': descriptor['version'], 'offlineBytes': descriptor['bytes'],
                                 'mime': mime, 'offlineName': source['name'],
                                 **({'uploadId': upload_id} if upload_id else {}),
                                 **({'desktopId': desktop_id or descriptor['version']} if mime.startswith('image/') and not upload_id else {})}))
            row = dict(row)
            if visible:
                existing = row.get('attachments', [])
                updated = []
                for attachment, details in visible:
                    match = next((item for item in existing if any(
                        value and item.get(k) == value for k, value in attachment.items()
                        if k in ('path', 'desktopId', 'uploadId'))), attachment)
                    match = {**match, **details}
                    if match.get('uploadId'):
                        match.pop('path', None)
                    updated.append(match)
                identities = {value for item in updated for key in ('path', 'desktopId', 'uploadId')
                              if (value := item.get(key))}
                updated.extend(item for item in existing if not any(item.get(k) in identities for k in
                                                                   ('path', 'desktopId', 'uploadId')))
                row['attachments'] = updated
            text_chars = 0

            def counted():
                nonlocal text_chars
                for part in parts:
                    # JavaScript offsets and archive checks use UTF-16 units.
                    text_chars += len(part['text'].encode('utf-16-le')) // 2
                    yield part

            stream = _stream_chunks(_record_json(row, question_time, counted()))
            first = next(stream)
            if batch_records and len(first) < CHUNK_BYTES:
                prefix = encoded({'id': row['turnId'], 'questionTime': question_time})[:-1] + b',"messages":['
                message = first[len(prefix):-2]
                if batch and (batch_common['turnId'] != row['turnId'] or
                              batch_common['questionTime'] != question_time or len(batch) >= 64 or
                              batch_bytes + len(message) > 1024 * 1024):
                    flush()
                if not batch:
                    batch_common = common
                batch.append(message)
                batch_bytes += len(message)
                batch_meta.append({'key': row['key'], 'recordVersion': row['version'],
                                   'textLength': text_chars or len(row.get('text', '').encode('utf-16-le')) // 2})
            else:
                flush()
                self._put(transfer, session['id'],
                          {**common, 'kind': 'record', 'mime': 'application/json',
                           'name': 'record.json', 'truncated': bool(row.get('truncated')),
                           'textLength': len(row.get('text', '').encode('utf-16-le')) // 2},
                          chain([first], stream), estimate,
                          size_meta=lambda: {'textLength': text_chars or len(row.get('text', '').encode('utf-16-le')) // 2})
        flush()

    def _put(self, transfer, scope, descriptor, data, estimate, size_meta=None):
        whole = hashlib.sha256()
        total = 0
        staging = secrets.token_hex(16)
        # The staging namespace is private. Publish the reference and its
        # final hash atomically, and reuse bytes across transfer manifests.
        with self.connect() as db:
            for i, block in enumerate(data):
                whole.update(block)
                db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',
                           (staging, i, hashlib.sha256(block).hexdigest(), len(block), None if estimate else block))
                total += len(block)
            version = whole.hexdigest()
            if not estimate:
                db.execute('DELETE FROM chunks WHERE version=? AND body IS NULL', (version,))
            db.execute('UPDATE OR IGNORE chunks SET version=? WHERE version=?', (version, staging))
            db.execute('DELETE FROM chunks WHERE version=?', (staging,))
            if descriptor['kind'] == 'attachment' and not descriptor.get('attachmentKey'):
                descriptor = {**descriptor, 'attachmentKey': version, 'desktopId': version}
            item = _hash([scope, descriptor['key'], descriptor['kind'], version,
                          descriptor.get('attachmentKey'), descriptor.get('variant')])
            descriptor = {**descriptor, **(size_meta() if size_meta else {}),
                          'id': item, 'version': version, 'bytes': total}
            db.execute('INSERT OR IGNORE INTO items(manifest,id,scope,kind,version,bytes,descriptor) VALUES(?,?,?,?,?,?,?)',
                       (transfer, item, scope, descriptor['kind'], version, total, self._json(descriptor)))
        return descriptor

    def _manifest(self, db, owner, transfer):
        manifest = db.execute('SELECT * FROM manifests WHERE id=? AND owner=?', (transfer, owner)).fetchone()
        if manifest is None:
            raise KeyError('找不到这次传输')
        return manifest

    def manifest(self, owner, transfer, cursor):
        with self.connect() as db:
            manifest = self._manifest(db, owner, transfer)
            errors = json.loads(manifest['errors'])
            result = {'id': transfer, 'status': manifest['status'], 'sessionCount': manifest['sessions'],
                      'capturedAt': manifest['captured'], 'estimate': bool(manifest['estimate']),
                      'failedSessions': len(errors), 'errors': errors, 'items': [], 'nextCursor': None}
            result['summary'] = json.loads(manifest['summary'])
            result['warnings'] = result['summary'].get('warnings', [])
            if manifest['status'] != 'ready':
                if errors:
                    result['error'] = errors[0]['message']
                return result
            lane, after = (cursor[0], int(cursor[1:])) if cursor else ('r', 0)
            rows = self._page(db, transfer, 'record' if lane == 'r' else 'attachment', after, PAGE_ITEMS + 1)
            if lane == 'r' and len(rows) < PAGE_ITEMS + 1:
                rows.extend(self._page(db, transfer, 'attachment', 0, PAGE_ITEMS + 1 - len(rows)))
            for row in rows[:PAGE_ITEMS]:
                item = json.loads(row['descriptor'])
                self.sources.authorize(item['session'])
                if item['bytes'] <= 16 * CHUNK_BYTES:
                    item['chunks'] = self._chunks(db, item['version'])
                result['items'].append(item)
            if len(rows) > PAGE_ITEMS:
                last = rows[PAGE_ITEMS - 1]
                result['nextCursor'] = last['kind'][0] + str(last['ordinal'])
            return result

    @staticmethod
    def _page(db, transfer, kind, after, limit):
        return db.execute('SELECT kind,ordinal,descriptor FROM items WHERE manifest=? AND kind=? AND ordinal>? ORDER BY ordinal LIMIT ?',
                          (transfer, kind, after, limit)).fetchall()

    @staticmethod
    def _chunks(db, version, index=None):
        query = 'SELECT sequence, digest, size FROM chunks WHERE version=?'
        args = (version,)
        if index is not None:
            query += ' AND sequence=?'
            args += (index,)
        return [{'index': row['sequence'], 'sha256': row['digest'], 'offset': row['sequence'] * CHUNK_BYTES,
                 'bytes': row['size']} for row in db.execute(query + ' ORDER BY sequence', args)]

    def _item(self, db, owner, transfer, item):
        manifest = self._manifest(db, owner, transfer)
        if manifest['status'] != 'ready':
            raise ValueError('传输清单还未准备好')
        row = db.execute('SELECT descriptor FROM items WHERE manifest=? AND id=?', (transfer, item)).fetchone()
        if row is None:
            raise KeyError('传输清单中没有该内容')
        descriptor = json.loads(row[0])
        self.sources.authorize(descriptor['session'])
        return manifest, descriptor

    def chunk(self, owner, transfer, item, index):
        with self.connect() as db:
            _, descriptor = self._item(db, owner, transfer, item)
            metadata = self._chunks(db, descriptor['version'], index)
            if not metadata:
                raise ValueError('传输分段无效')
            return metadata[0]

    def content(self, owner, transfer, item, offset, length):
        with self.connect() as db:
            manifest, descriptor = self._item(db, owner, transfer, item)
            if manifest['estimate']:
                raise ValueError('请开始传输后再下载')
            if offset % CHUNK_BYTES or offset >= descriptor['bytes']:
                raise ValueError('传输分段无效')
            size = min(CHUNK_BYTES, descriptor['bytes'] - offset)
            if length not in (size, CHUNK_BYTES):
                raise ValueError('传输分段无效')
            chunk = db.execute('SELECT digest,body FROM chunks WHERE version=? AND sequence=?',
                               (descriptor['version'], offset // CHUNK_BYTES)).fetchone()
            if chunk is None or chunk['body'] is None or len(chunk['body']) != size:
                raise ValueError('传输内容已不可用')
            return descriptor, chunk['digest'], chunk['body']
