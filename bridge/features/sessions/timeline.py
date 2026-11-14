"""Bounded phone projections. Original desktop state stays authoritative.

Cursors belong to one in-memory timeline epoch. Replacement/reordered desktop
history invalidates them explicitly; appending or editing text does not.
"""
import hashlib
import json
import uuid
from collections import OrderedDict

PAGE_BYTES = 192 * 1024
TEXT_PREVIEW = 8000
DETAIL_CHARS = 16000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class Timeline:
    def __init__(self):
        self.epoch = uuid.uuid4().hex
        self.sequence = -1
        self.rows = []
        self.order_origin = 0
        self.details = {}
        self.positions = {}
        self.versions = OrderedDict()
        self.meta = {}
        self.limit = None
        self.window_anchor = None
        self.source_anchor = None
        self.window_more = False

    def update(self, view, limit=None):
        if self.sequence == view['sequence'] and self.limit == limit:
            return
        activation_ids = set(view.get('goalActivationIds') or ())
        entries = []
        for turn in view['turns']:
            occurrences = {}
            first_user = next((m for m in turn['messages'] if m['role'] == 'user'), None)
            for message in turn['messages']:
                if message.get('requestId') and message['requestId'] in activation_ids:
                    continue
                identity = (turn['id'], message.get('id'), message.get('kind'))
                ordinal = message.get('_timelineOrdinal', occurrences.get(identity, 0))
                occurrences[identity] = ordinal + 1
                entries.append((identity + (ordinal,), turn, message, message.get('_firstUser', message is first_user)))
            if turn.get('status') == 'completed' and turn.get('fileChanges'):
                changes = turn['fileChanges']
                added = sum(change['added'] for change in changes)
                removed = sum(change['removed'] for change in changes)
                summary = {'id': 'native-file-changes', 'kind': 'fileChanges', 'role': 'fileChanges',
                           'fileSummary': {'count': len(changes), 'added': added, 'removed': removed, 'basis': turn.get('fileChangesBasis', 'recorded'),
                                           'files': [{k: c[k] for k in ('path', 'added', 'removed')} for c in changes]},
                           'text': ''}
                entries.append(((turn['id'], summary['id'], summary['kind'], 0), turn, summary, False))
            if turn.get('error'):
                entries.append(((turn['id'], 'error'), turn, None, False))
        start = max(0, len(entries) - limit) if limit is not None else 0
        # Keep the loaded prefix while new replies arrive. Expanding history
        # must not hash/copy bodies outside the requested presentation window.
        if self.window_anchor is not None and limit is not None:
            anchor = next((i for i, entry in enumerate(entries) if entry[0] == self.window_anchor), None)
            if anchor is not None:
                start = min(start, anchor)
        self.limit = limit
        self.window_more = start > 0 or view.get('historyWindowMore', False)
        self.window_anchor = entries[start][0] if start < len(entries) else None
        # A tool call carried only to interpret a recent result must not expand
        # the next source read to every intervening historical item.
        self.source_anchor = next((entry[0] for entry in entries[start:]
                                   if entry[2] is not None and entry[2].get('kind') != 'fileChanges' and not entry[2].get('_historyCarry')), None)
        rows, details = [], {}
        for identity, turn, message, first_user in entries[start:]:
            if message is not None:
                base = hashlib.sha256(encoded(identity[:3])).hexdigest()[:24]
                ordinal = identity[3]
                key = base + '-' + str(ordinal)
                text = message.get('text') or ''
                if message.get('output'):
                    text += '\n\n' + message['output']
                activity = message['role'] in ('activity', 'fileChanges')
                attachments = [{k: str(a[k])[:4096 if k == 'path' else 300] for k in ('type', 'name', 'path', 'desktopId') if k in a}
                               for a in (message.get('attachments') or [])[:20]]
                version = hashlib.sha256(encoded([text, turn.get('model'), turn.get('effort'), attachments, message.get('fileSummary')])).hexdigest()[:24]
                row = {k: message[k] for k in ('role', 'kind', 'status', 'title', 'phase', 'requestId') if k in message}
                row.update(key=key, turnId=turn['id'], order=len(rows), version=version,
                           text=text[:180 if activity else TEXT_PREVIEW],
                           truncated=(activity and bool(text)) or len(text) > TEXT_PREVIEW, turnStatus=turn.get('status'),
                           editable=turn.get('actionable', True) and first_user and message.get('kind') == 'userMessage',
                           forkable=turn.get('actionable', True) and message['role'] == 'assistant' and message.get('phase') != 'commentary' and turn.get('status') == 'completed')
                timestamp = message.get('timestamp')
                if timestamp is None and first_user:
                    timestamp = turn.get('startedAt')
                if timestamp is not None and message['role'] in ('user', 'assistant'):
                    row['timestamp'] = timestamp
                if message.get('fileSummary'):
                    row['fileSummary'] = message['fileSummary']
                    row['text'] = ''
                    row['truncated'] = False
                if message['role'] == 'assistant':
                    row.update({k:turn[k] for k in ('model','effort') if turn.get(k)})
                if attachments:
                    row['attachments'] = attachments
                rows.append(row)
                details[key] = text
            else:
                key = hashlib.sha256(encoded([turn['id'], 'error'])).hexdigest()[:24] + '-error'
                text = turn['error'] if isinstance(turn['error'], str) else json.dumps(turn['error'], ensure_ascii=False)
                error = turn['error']
                if isinstance(error, dict) and isinstance(error.get('codexErrorInfo'), dict) and 'responseStreamDisconnected' in error['codexErrorInfo'] and error.get('additionalDetails') == 'Connection failed: error sending request':
                    text = '电脑上的 Codex 无法连接模型服务。使用本机 CLI 时，可在聊天详情中检查 CLI 网络。'
                rows.append(dict(key=key, turnId=turn['id'], order=len(rows), role='error', text=text[:TEXT_PREVIEW],
                                 truncated=len(text) > TEXT_PREVIEW, version=hashlib.sha256(text.encode()).hexdigest()[:24]))
                details[key] = text
        old_keys = [row['key'] for row in self.rows]
        new_keys = [row['key'] for row in rows]
        # Prepending saved turns preserves existing row keys and cursors.
        first = new_keys.index(old_keys[0]) if old_keys and old_keys[0] in new_keys else 0
        if old_keys != new_keys[first:first + len(old_keys)]:
            self.epoch = uuid.uuid4().hex
            self.versions.clear()
            self.order_origin = 0
        elif old_keys:
            self.order_origin -= first
        for index, row in enumerate(rows):
            row['order'] = self.order_origin + index
        self.sequence = view['sequence']
        self.rows, self.details = rows, details
        self.positions = {row['key']: i for i, row in enumerate(rows)}
        self.meta = {k: v for k, v in view.items() if k != 'turns'}
        self.meta['latestUserTurnId'] = next((t['id'] for t in reversed(view['turns'])
                                              if any(m['role'] == 'user'
                                                     and (not m.get('requestId') or m['requestId'] not in activation_ids)
                                                     for m in t['messages'])), None)
        self.versions[self.sequence] = {row['key']: hashlib.sha256(encoded(row)).digest() for row in rows}
        while len(self.versions) > 16:
            self.versions.popitem(last=False)

    def cursor(self, key):
        return self.epoch + '.' + key

    def position(self, cursor):
        epoch, _, key = cursor.partition('.')
        if epoch != self.epoch or key not in self.positions:
            return None
        return self.positions[key]

    def page(self, limit=20, before=None):
        end = self.position(before) if before else len(self.rows)
        if end is None:
            return {**self.page(), 'reset': True}
        limit = max(1, min(100, limit))
        selected, size = [], 0
        for row in reversed(self.rows[max(0, end - limit):end]):
            cost = len(encoded(row))
            if selected and size + cost > PAGE_BYTES:
                break
            selected.append(row)
            size += cost
        selected.reverse()
        start = end - len(selected)
        return {'epoch': self.epoch, 'sequence': self.sequence, 'rows': selected,
                'before': self.cursor(selected[0]['key']) if selected else None,
                'hasMore': start > 0 or self.window_more or self.meta.get('savedHistoryMore', False), 'meta': self.meta}

    def changes(self, after, epoch, start):
        first = self.position(start) if start else None
        if epoch != self.epoch or after not in self.versions or (start and first is None):
            return {**self.page(), 'reset': True}
        previous, current = self.versions[after], self.versions[self.sequence]
        rows = [row for row in self.rows[first or 0:]
                if previous.get(row['key']) != current[row['key']]]
        if len(rows) > 100 or sum(len(encoded(row)) for row in rows) > PAGE_BYTES:
            return {**self.page(), 'reset': True}
        return {'epoch': self.epoch, 'sequence': self.sequence, 'rows': rows, 'meta': self.meta}

    def detail(self, cursor, offset=0, version=None):
        position = self.position(cursor)
        if position is None:
            raise ValueError('这条内容已更新，请重新打开聊天')
        row = self.rows[position]
        text = self.details[row['key']]
        offset = max(0, min(offset, len(text))) if version == row['version'] else 0
        end = min(len(text), offset + DETAIL_CHARS)
        return {'key': row['key'], 'version': row['version'], 'text': text[offset:end],
                'offset': offset, 'next': end if end < len(text) else None}
