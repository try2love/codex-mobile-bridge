"""Read the original local ZCode task index and its visible saved transcript."""
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path

from bridge.clients.errors import BridgeUnavailable
from bridge.features.sessions.identity import same_cwd


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


class _TranscriptUnavailable(BridgeUnavailable):
    pass


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
        try:
            db.execute('ATTACH DATABASE ? AS cli', (transcript.as_uri() + '?mode=ro',))
        except sqlite3.OperationalError as error:
            raise _TranscriptUnavailable('暂时无法读取 ZCode 本机聊天记录') from error
        db.execute('BEGIN')
        yield db
    except sqlite3.Error as exc:
        raise BridgeUnavailable('暂时无法读取 ZCode 本机历史') from exc
    finally:
        if db is not None:
            db.close()


_TASKS = """
    SELECT t.task_id, t.title, t.workspace_path, t.updated_at, t.archived,
           t.model, c.id AS transcript_id, c.directory
    FROM tasks AS t LEFT JOIN cli.session AS c ON c.id = t.task_id
    WHERE t.deleted = 0 AND t.host_id = 'local' AND t.provider = 'glm'
      AND t.workspace_identity IS NULL AND c.time_deleted IS NULL
"""


def _session(row, *, allow_index_only=False):
    if not row:
        return None
    if row['transcript_id'] is None:
        if not allow_index_only or not isinstance(row['workspace_path'], str) or not Path(row['workspace_path']).is_absolute():
            return None
    elif not same_cwd(row['workspace_path'], row['directory'], os.name == 'nt'):
        return None
    return {'id': row['task_id'], 'provider': 'zcode', 'host': 'local',
            'backend': 'desktop', 'title': row['title'], 'cwd': row['workspace_path'],
            'updatedAt': row['updated_at'], 'archived': bool(row['archived']),
            'model': row['model'], 'status': 'unknown', 'runtimeKnown': False,
            'requests': [], 'readOnly': True, 'connectionState': 'detached',
            'capabilities': {'send': False, 'resume': False, 'stop': False}}


def _target(db, sid, *, allow_index_only=False):
    found = db.execute(_TASKS + ' AND t.task_id = ?', (sid,)).fetchone() if db is not None else None
    row = _session(found, allow_index_only=allow_index_only)
    if row is None:
        raise ValueError('找不到此本机 ZCode 聊天')
    if found['transcript_id'] is None:
        row['indexOnly'] = True
    return row


def saved_target(home, sid):
    """Resolve the original local task and its transcript without loading messages."""
    with _history(home) as db:
        return _target(db, sid, allow_index_only=True)


def resume_workspace(home, sid):
    try:
        row = saved_target(home, sid)
    except _TranscriptUnavailable:
        # The CLI database may still be reopening. The desktop's own task index
        # identifies the project; the live App must independently verify this
        # same ID and project before its V4 subscription can confer write access.
        index = Path(home).absolute() / 'tasks-index.sqlite'
        if index.parent.name != 'v2' or not _checked_db(index):
            raise
        with closing(sqlite3.connect(index.as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            db.execute('PRAGMA trusted_schema = OFF')
            rows = db.execute(
                "SELECT workspace_path, archived FROM tasks WHERE task_id=? AND deleted=0 "
                "AND host_id='local' AND provider='glm' AND workspace_identity IS NULL", (sid,)).fetchall()
        if len(rows) != 1:
            raise ValueError('找不到此本机 ZCode 聊天')
        row = {'cwd': rows[0][0], 'archived': rows[0][1]}
    if row['archived']:
        raise ValueError('无法继续已归档的 ZCode 会话')
    if not isinstance(row['cwd'], str) or not Path(row['cwd']).is_absolute():
        raise ValueError('ZCode 原项目目录无效')
    return row['cwd']


def send_boundary(home, sid):
    with _history(home) as db:
        row = _target(db, sid)
        if row['archived'] or not row['title']:
            raise ValueError('请先在 ZCode 打开未归档的聊天')
        seq = db.execute('SELECT max(sequence) FROM cli.message WHERE session_id = ?', (sid,)).fetchone()[0]
        return row['title'], seq if seq is not None else -1


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
    import json
    from .attachments import attachments

    body = body or {}
    before = body.get('historyBefore')
    limit = max(1, min(100, int(body.get('historyLimit', 40))))
    with _history(home) as db:
        session = _target(db, sid, allow_index_only=True)
        if session.get('indexOnly'):
            return {'session': session, 'readOnly': True, 'messages': [], 'requests': [],
                    'capabilities': session['capabilities'], 'history': {'hasMore': False, 'before': None},
                    'notice': '此聊天尚无已保存的消息。'}
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
            SELECT m.id, json_extract(m.data, '$.role') AS role,
                   CASE WHEN json_type(m.data, '$.metadata.conversationInputIntent.attachments')='array'
                        THEN json_extract(m.data, '$.metadata.conversationInputIntent.attachments')
                        WHEN json_type(m.data, '$.metadata.inputIntent.attachmentRefs')='array'
                        THEN json_extract(m.data, '$.metadata.inputIntent.attachmentRefs') END AS files,
                   coalesce(json_extract(m.data, '$.metadata.conversationInputIntent.sourceCommandId'),
                            json_extract(m.data, '$.metadata.inputIntent.sourceCommandId')) AS command
            FROM cli.message AS m
            WHERE m.session_id = ? AND json_extract(m.data, '$.role') IN ('user', 'assistant')
              AND json_extract(m.data, '$.semantics.uiVisibility') = 'visible'
              AND coalesce(json_extract(m.data, '$.synthetic'), 0) = 0
              AND ((json_extract(m.data, '$.role')='user' AND json_array_length(files)>0) OR
                   EXISTS (SELECT 1 FROM cli.part AS p WHERE p.message_id = m.id
                          AND p.session_id = m.session_id AND """ + _VISIBLE_TEXT + '))'
            + page + ' ORDER BY m.sequence DESC, m.time_created DESC, m.id DESC LIMIT ?',
            (*args, limit + 1)).fetchall()
        messages = []
        for row in reversed(rows[:limit]):
            message = {'id': row['id'], 'role': row['role'], 'text': ''}
            if row['role'] == 'user' and row['files']:
                message['attachments'] = attachments(sid, json.loads(row['files']), row['command'])
            messages.append(message)
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
            from .reply_metadata import reply_models
            models = reply_models(db, sid, (m['id'] for m in messages if m['role'] == 'assistant'))
            for message in messages:
                message.update(models.get(message['id'], {}))
                message['text'] = '\n'.join(texts[message['id']])
    return {'session': session, 'readOnly': True, 'messages': messages, 'requests': [],
            'capabilities': session['capabilities'],
            'history': {'hasMore': len(rows) > limit, 'before': messages[0]['id'] if len(rows) > limit else None},
            'notice': '当前为本机保存的聊天文字，仅供查看。'}


def new_user_message(home, sid, after, started, text):
    """One new visible user message with the complete text, in the same native thread."""
    with _history(home) as db:
        _target(db, sid)
        rows = db.execute("""
            SELECT id FROM cli.message WHERE session_id = ? AND sequence > ? AND time_created BETWEEN ? AND ?
              AND json_extract(data, '$.role') = 'user'
              AND json_extract(data, '$.semantics.uiVisibility') = 'visible'
              AND coalesce(json_extract(data, '$.synthetic'), 0) = 0 LIMIT 2
        """, (sid, after, started, started + 30000)).fetchall()
        if len(rows) != 1:
            return None
        mid = rows[0]['id']
        parts = db.execute("SELECT json_extract(p.data, '$.text') FROM cli.part AS p "
                           'WHERE p.session_id = ? AND p.message_id = ? AND ' + _VISIBLE_TEXT +
                           ' ORDER BY p.sequence, p.time_created, p.id', (sid, mid)).fetchall()
        return mid if parts and '\n'.join(part[0] for part in parts if isinstance(part[0], str)) == text else None
