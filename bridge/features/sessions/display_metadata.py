"""Gateway-owned display metadata; never mutates native execution identity."""
import json
import re
import threading
import time
import uuid
from pathlib import Path

from bridge.features.sessions.organization import OrganizationNotSent, validate_change


def confirm_native_title(read, identity, title):
    """Allow an accepted native metadata write to become visible, never replay it."""
    deadline, delay = time.monotonic()+2, 0.05
    while True:
        try:
            after = read()
        except (OSError, ValueError):
            after = None
        if after is not None:
            if after['identity'] != identity:
                raise ValueError('改名期间原会话身份已变化')
            if after['title'] == title:
                return after
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise ValueError('改名读回尚未确认')
        time.sleep(min(delay, remaining))
        delay = min(delay*2, 0.25)


class DisplayMetadata:
    def __init__(self, directory, save):
        self.path = Path(directory) / 'display-metadata.json'
        self.lock = threading.RLock()
        self.save = save

    def _read(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def _snapshot(self, data, scope, sid):
        group = data.get(scope, {})
        row = group.get('sessions', {}).get(sid, {})
        return {'projectId': row.get('projectId'), 'sectionId': row.get('sectionId'),
                'projects': group.get('projects', []), 'sections': group.get('sections', []),
                'scope': 'bridge', 'sectionScope': 'bridge', 'status': 'confirmed'}

    def snapshot(self, scope, sid):
        with self.lock:
            return self._snapshot(self._read(), scope, sid)

    def clear_alias(self, scope, sid):
        with self.lock:
            data = self._read()
            row = data.get(scope, {}).get('sessions', {}).get(sid, {})
            if 'title' in row:
                del row['title']
                self.save(self.path, data)

    def decorate(self, scope, row):
        with self.lock:
            data = self._read()
            saved = data.get(scope, {}).get('sessions', {}).get(row['id'], {})
            result = dict(row)
            if saved.get('title') and (row.get('capabilities') or {}).get('rename') is not True:
                result.update(title=saved['title'], nativeTitle=row.get('title'), renameScope='bridge')
            for kind in ('project', 'section'):
                if kind + 'Id' in saved:
                    identifier = saved[kind + 'Id']
                    title = next((item['title'] for item in data[scope].get(kind + 's', [])
                                  if item['id'] == identifier), None)
                    result.update({kind + 'Id': identifier, kind + 'Name': title,
                                   kind + 'Scope': 'bridge'})
            return result

    def change(self, scope, sid, change):
        if not isinstance(change, dict) or not isinstance(change.get('id'), str):
            raise ValueError('会话显示设置无效')
        uuid.UUID(change['id'])
        kind = change.get('kind')
        if kind not in ('rename', 'project', 'section', 'create-project', 'create-section'):
            raise ValueError('会话显示设置无效')
        fields = {'id', 'kind', 'title'} if kind == 'rename' or kind.startswith('create-') else {'id', 'kind', 'targetId', 'expectedId'}
        if set(change) != fields:
            raise ValueError('会话显示设置无效')
        title = change.get('title')
        if 'title' in fields and (not isinstance(title, str) or not title.strip() or len(title.strip()) > 120):
            raise ValueError('请输入 1–120 字的名称')
        with self.lock:
            data = self._read()
            group = data.setdefault(scope, {})
            receipts = group.setdefault('requests', {})
            old = receipts.get(change['id'])
            if old:
                if old != {'sid': sid, 'change': change}:
                    raise ValueError('相同请求标识不能用于不同操作')
                return {**self._snapshot(data, scope, sid), **group.get('sessions', {}).get(sid, {})}
            row = group.setdefault('sessions', {}).setdefault(sid, {})
            if kind == 'rename':
                row['title'] = title.strip()
            elif kind.startswith('create-'):
                field = kind.removeprefix('create-')
                choices = group.setdefault(field + 's', [])
                target = next((item['id'] for item in choices if item['title'] == title.strip()), None)
                if target is None:
                    target = str(uuid.uuid4())
                    choices.append({'id': target, 'title': title.strip()})
                row[field + 'Id'] = target
            else:
                validate_change(self._snapshot(data, scope, sid), {key: value for key, value in change.items() if key != 'id'})
                row[kind + 'Id'] = change['targetId']
            receipts[change['id']] = {'sid': sid, 'change': change}
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.save(self.path, data)
            return {**self._snapshot(data, scope, sid), **row}


class RenameCommands:
    """A pending native rename blocks new writes until native readback confirms it."""
    def __init__(self, directory, save):
        self.path = Path(directory) / 'session-renames.json'
        self.lock = threading.RLock()
        self.save = save

    def perform(self, sid, body, read, dispatch):
        if set(body) != {'id', 'title'} or not isinstance(body['id'], str):
            raise ValueError('聊天改名请求无效')
        uuid.UUID(body['id'])
        title = body['title']
        if (not isinstance(title, str) or not title.strip() or len(title.strip()) > 120
                or re.search(r'[\x00-\x1f\x7f]', title)):
            raise ValueError('标题为 1–120 字，不能包含控制字符')
        title = title.strip()
        with self.lock:
            entries = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            old = entries.get(body['id'])
            if old and (old['sid'] != sid or old['title'] != title):
                raise ValueError('相同请求标识不能用于不同操作')
            before = read()
            reconciled = False
            for entry in entries.values():
                if entry['sid'] == sid and entry['status'] == 'unknown':
                    if before['identity'] != entry['identity'] or before['title'] != entry['title']:
                        raise ValueError('此前改名结果待核对；不会重复提交，请先在原客户端核对')
                    entry['status'] = 'confirmed'
                    reconciled = True
            if old:
                self.save(self.path, entries)
                return {**before, 'status': old['status'], 'scope': 'native'}
            entry = {'sid': sid, 'title': title, 'identity': before['identity'], 'status': 'unknown'}
            if reconciled and before['title'] == title:
                entry['status'] = 'confirmed'
            entries[body['id']] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.save(self.path, entries)
            if entry['status'] == 'confirmed':
                return {**before, 'status': 'confirmed', 'scope': 'native'}
            try:
                result = dispatch(title)
                if isinstance(result, dict) and result.get('notSent') is True:
                    raise OrganizationNotSent(result.get('message') or '原会话状态已变化，请刷新后重试')
                after = confirm_native_title(read, before['identity'], title)
            except OrganizationNotSent:
                del entries[body['id']]
                self.save(self.path, entries)
                raise
            except Exception as exc:
                raise ValueError('改名结果待核对；不会重复提交，请先在原客户端核对') from exc
            entry['status'] = 'confirmed'
            self.save(self.path, entries)
            return {**after, 'status': 'confirmed', 'scope': 'native'}
