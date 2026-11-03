"""Read the original local ZCode task index and its visible saved transcript."""
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

from bridge.clients.errors import BridgeUnavailable


def _checked_db(path):
    # A configured profile can point elsewhere. Never follow links to derive a
    # different native database, including SQLite's journal and WAL sidecars.
    for member in (path, *(Path(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal'))):
        try:
            info = member.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or member.resolve() != member):
            raise BridgeUnavailable('ZCode 本机历史数据库路径已变化')
    return path.is_file()


@contextmanager
def _history(home):
    home = Path(home).absolute()
    index = home / 'tasks-index.sqlite'
    transcript = home.parent / 'cli' / 'db' / 'db.sqlite'
    if home.name != 'v2' or not _checked_db(index) or not _checked_db(transcript):
        yield None
        return
    db = None
    try:
        db = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True, timeout=.2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only = ON')
        db.execute('PRAGMA trusted_schema = OFF')
        db.execute('ATTACH DATABASE ? AS cli', (transcript.as_uri() + '?mode=ro',))
        db.execute('BEGIN')
        yield db
    except sqlite3.Error as exc:
        raise BridgeUnavailable('暂时无法读取 ZCode 本机历史') from exc
    finally:
        if db is not None:
            db.close()


_TASKS = """
    SELECT t.task_id, t.title, t.workspace_path, t.updated_at, t.archived,
           t.model, c.directory
    FROM tasks AS t JOIN cli.session AS c ON c.id = t.task_id
    WHERE t.deleted = 0 AND t.host_id = 'local' AND t.provider = 'glm'
      AND t.workspace_identity IS NULL AND c.time_deleted IS NULL
"""


def _session(row):
    if not row or not row['workspace_path'] or not row['directory']:
        return None
    if os.path.normcase(row['workspace_path']) != os.path.normcase(row['directory']):
        return None
    return {'id': row['task_id'], 'provider': 'zcode', 'host': 'local',
            'backend': 'desktop', 'title': row['title'], 'cwd': row['workspace_path'],
            'updatedAt': row['updated_at'], 'archived': bool(row['archived']),
            'model': row['model'], 'status': 'unknown', 'runtimeKnown': False,
            'requests': [], 'readOnly': True, 'connectionState': 'detached',
            'capabilities': {'send': False, 'resume': False, 'stop': False}}


def saved_sessions(home):
    with _history(home) as db:
        rows = db.execute(_TASKS + ' ORDER BY t.updated_at DESC') if db is not None else ()
        sessions = [session for row in rows if (session := _session(row))]
    return {'sessions': sessions, 'connected': False, 'readOnly': True, 'capabilities': {'create': False}}


# For ordinary App launches the authority for visibility is the original
# transcript. It also stores model-only messages under the public "user" role.
_VISIBLE_TEXT = """
    json_extract(p.data, '$.type') = 'text'
    AND coalesce(json_extract(p.data, '$.synthetic'), 0) = 0
    AND coalesce(json_extract(p.data, '$.ignored'), 0) = 0
    AND coalesce(json_extract(p.data, '$.metadata.visibility'), 'visible') = 'visible'
"""


def saved_detail(home, sid, body=None):
    body = body or {}
    before = body.get('historyBefore')
    limit = max(1, min(100, int(body.get('historyLimit', 40))))
    with _history(home) as db:
        session = _session(db.execute(_TASKS + ' AND t.task_id = ?', (sid,)).fetchone()) if db is not None else None
        if session is None:
            raise ValueError('找不到此本机 ZCode 聊天')
        page = ''
        args = [sid]
        if before:
            pos = db.execute('SELECT sequence, time_created, id FROM cli.message WHERE session_id = ? AND id = ?',
                             (sid, before)).fetchone()
            if pos is None:
                raise ValueError('ZCode 历史已变化，请重新打开聊天')
            page = ' AND (m.sequence, m.time_created, m.id) < (?, ?, ?)'
            args.extend(pos)
        rows = db.execute("""
            SELECT m.id, json_extract(m.data, '$.role') AS role FROM cli.message AS m
            WHERE m.session_id = ? AND json_extract(m.data, '$.role') IN ('user', 'assistant')
              AND json_extract(m.data, '$.semantics.uiVisibility') = 'visible'
              AND coalesce(json_extract(m.data, '$.synthetic'), 0) = 0
              AND EXISTS (SELECT 1 FROM cli.part AS p WHERE p.message_id = m.id
                          AND p.session_id = m.session_id AND """ + _VISIBLE_TEXT + ')'
            + page + ' ORDER BY m.sequence DESC, m.time_created DESC, m.id DESC LIMIT ?',
            (*args, limit + 1)).fetchall()
        messages = [{'id': row['id'], 'role': row['role'], 'text': ''} for row in reversed(rows[:limit])]
        if messages:
            ids = [row['id'] for row in messages]
            parts = db.execute("""
                SELECT p.message_id, json_extract(p.data, '$.text') AS text
                FROM cli.part AS p WHERE p.session_id = ? AND p.message_id IN ("""
                + ','.join('?' for _ in ids) + ') AND ' + _VISIBLE_TEXT
                + ' ORDER BY p.message_id, p.sequence, p.time_created, p.id', (sid, *ids))
            texts = {mid: [] for mid in ids}
            for part in parts:
                if isinstance(part['text'], str):
                    texts[part['message_id']].append(part['text'])
            for message in messages:
                message['text'] = '\n'.join(texts[message['id']])
    return {'session': session, 'readOnly': True, 'messages': messages, 'requests': [],
            'capabilities': session['capabilities'],
            'history': {'hasMore': len(rows) > limit, 'before': messages[0]['id'] if len(rows) > limit else None},
            'notice': '当前为本机保存的聊天文字，仅供查看。'}
