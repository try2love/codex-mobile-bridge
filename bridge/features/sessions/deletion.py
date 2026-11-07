"""Confirmed native deletion, with an exact subtree and durable non-replay receipts."""
import json
import threading
import uuid
from pathlib import Path

from bridge.features.sessions.create import CreationError, DeleteRejected, check_archive_runtime


class DeleteNotSent(ValueError):
    """No native deletion was dispatched."""


def deletion_scope(store, identifier):
    """Read metadata only; attached children are deleted before their parent."""
    root = store.get(identifier)
    rows, visited = [], set()

    def visit(row):
        sid = row['id']
        if sid in visited:
            raise ValueError('侧边聊天关系异常，请先在原客户端检查')
        visited.add(sid)
        for child in store.attached_children(sid):
            visit(child)
        rows.append({'id': sid, 'title': row.get('name') or row.get('title') or sid,
                     'archived': bool(row.get('archived'))})

    visit(root)
    identifiers = sorted(row['id'] for row in store.lifecycle_threads(root_id=identifier))
    return {'id': identifier, 'sessions': rows, 'scope': identifiers,
            'subagentCount': len(set(identifiers) - visited)}


def delete_native(request, snapshot, recheck):
    """Never resume, interrupt or replace an owner to make a deletion succeed."""
    try:
        if recheck() != snapshot:
            raise ValueError('聊天或侧边聊天已变化，请重新确认删除范围')
        check_archive_runtime(request, snapshot['scope'])
        for identifier in snapshot['scope']:
            thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
            if thread.get('id') != identifier:
                raise ValueError('原会话身份已变化，请重新打开菜单')
            if (thread.get('status') or {}).get('type') != 'notLoaded':
                background = request('thread/backgroundTerminals/list', {'threadId': identifier, 'limit': 1})
                if not isinstance(background.get('data'), list) or background['data'] or background.get('nextCursor'):
                    raise ValueError('聊天仍有后台任务，请先停止后再删除')
        if recheck() != snapshot:
            raise ValueError('聊天或侧边聊天已变化，请重新确认删除范围')
    except Exception as exc:
        raise DeleteNotSent(str(exc)) from exc
    for index, row in enumerate(snapshot['sessions']):
        try:
            request('thread/delete', {'threadId': row['id']})
        except DeleteRejected as exc:
            if index == 0:
                raise DeleteNotSent(str(exc)) from exc
            raise


class DeleteCommands:
    def __init__(self, directory, write_json):
        self.path = Path(directory) / 'session-deletions.json'
        self.write_json = write_json
        self.lock = threading.RLock()

    def perform(self, identifier, request_id, expected, read, existing, prepare):
        uuid.UUID(identifier)
        uuid.UUID(request_id)
        if (not isinstance(expected, list) or not expected or not all(isinstance(sid, str) for sid in expected)
                or len(expected) != len(set(expected))
                or identifier not in expected):
            raise ValueError('请重新确认删除范围')
        for sid in expected:
            uuid.UUID(sid)
        scope = sorted(expected)
        with self.lock:
            entries = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            old = entries.get(request_id)
            if old and (old['id'] != identifier or old['scope'] != scope):
                raise ValueError('相同请求标识不能用于不同删除操作')
            # Missing metadata is evidence only for a previously persisted scope.
            for row in entries.values():
                if row['status'] == 'unknown' and set(row['scope']) & set(scope):
                    if existing(row['scope']):
                        raise ValueError('此前删除结果仍待核对；不会重复提交，请在原客户端检查')
                    row['status'] = 'confirmed'
            if old:
                self.write_json(self.path, entries)
                return dict(old)
            snapshot = read()
            if snapshot['scope'] != scope:
                raise ValueError('聊天或侧边聊天已变化，请重新确认删除范围')
            call = prepare(snapshot)
            result = {'id': identifier, 'scope': scope, 'status': 'unknown'}
            entries[request_id] = result
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.write_json(self.path, entries)
            try:
                call()
            except DeleteNotSent:
                del entries[request_id]
                self.write_json(self.path, entries)
                raise
            except Exception as exc:
                raise CreationError('删除结果待核对；不会重复提交，请在原客户端检查') from exc
            if existing(scope):
                raise CreationError('删除结果待核对；不会重复提交，请在原客户端检查')
            result['status'] = 'confirmed'
            self.write_json(self.path, entries)
            return dict(result)
