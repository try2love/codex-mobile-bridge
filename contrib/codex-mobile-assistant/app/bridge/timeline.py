"""Bounded phone projections. Original desktop state stays authoritative.

Cursors belong to one in-memory timeline epoch. Replacement/reordered desktop
history invalidates them explicitly; appending or editing text does not.
"""
import hashlib
import json
import uuid
from collections import OrderedDict
from .images import attachment_summary

PAGE_BYTES = 192 * 1024
TEXT_PREVIEW = 8000
DETAIL_CHARS = 16000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def error_message(value):
    """Compare error items with the same error wrapped in turn metadata."""
    for _ in range(8):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return value.strip()
        elif isinstance(value, dict) and ('error' in value or 'message' in value):
            value = value.get('error') if value.get('error') is not None else value.get('message')
        else:
            break
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class Timeline:
    def __init__(self):
        self.epoch = uuid.uuid4().hex
        self.sequence = -1
        self.rows = []
        self.details = {}
        self.positions = {}
        self.versions = OrderedDict()
        self.meta = {}

    def update(self, view, message_times=None):
        if self.sequence == view['sequence']:
            return
        rows, details = [], {}
        for turn in view['turns']:
            occurrences = {}
            time_occurrences = {}
            saved_times = (message_times or {}).get(turn['id'], {})
            first_user = next((m for m in turn['messages'] if m['role'] == 'user'), None)
            last_assistant = next((m for m in reversed(turn['messages']) if m['role'] == 'assistant'), None)
            for message in turn['messages']:
                identity = [turn['id'], message.get('id'), message.get('kind')]
                base = hashlib.sha256(encoded(identity)).hexdigest()[:24]
                ordinal = occurrences.get(base, 0)
                occurrences[base] = ordinal + 1
                key = base + '-' + str(ordinal)
                text = message.get('text') or ''
                if message.get('output'):
                    text += '\n\n' + message['output']
                activity = message['role'] == 'activity'
                version = hashlib.sha256(text.encode()).hexdigest()[:24]
                row = {k: message[k] for k in ('role', 'kind', 'status', 'title', 'phase', 'activitySummary', 'cwd', 'exitCode', 'durationMs') if k in message}
                preview_limit = 4000 if message.get('kind') == 'reasoning' else 180 if activity else TEXT_PREVIEW
                row.update(key=key, turnId=turn['id'], order=len(rows), version=version,
                           text=text[:preview_limit], truncated=len(text) > preview_limit)
                role, time_key = message['role'], message.get('timestampKey')
                times = saved_times.get(role, {}).get(time_key, [])
                occurrence = time_occurrences.get((role, time_key), 0)
                time_occurrences[(role, time_key)] = occurrence + 1
                saved_at = times[occurrence] if occurrence < len(times) else None
                if role == 'user':
                    row['sentAt'] = message.get('sentAt') or saved_at
                    if not row['sentAt'] and message is first_user and message.get('kind') != 'steeringUserMessage':
                        row['sentAt'] = turn.get('startedAt')
                elif role == 'assistant':
                    row['completedAt'] = message.get('completedAt') or saved_at
                    if message is last_assistant and turn.get('status') == 'completed':
                        row['completedAt'] = saved_times.get('completedAt') or turn.get('completedAt') or row['completedAt']
                if message.get('attachments'):
                    row['attachments'] = [attachment_summary(a) for a in message['attachments'][:20]]
                rows.append(row)
                details[key] = text
            if turn.get('error') and not any(message['role'] == 'error' and
                    error_message(message.get('text')) == error_message(turn['error']) for message in turn['messages']):
                key = hashlib.sha256(encoded([turn['id'], 'error'])).hexdigest()[:24] + '-error'
                text = turn['error'] if isinstance(turn['error'], str) else json.dumps(turn['error'], ensure_ascii=False)
                rows.append(dict(key=key, turnId=turn['id'], order=len(rows), role='error', text=text[:TEXT_PREVIEW],
                                 truncated=len(text) > TEXT_PREVIEW, version=hashlib.sha256(text.encode()).hexdigest()[:24]))
                details[key] = text
        old_keys = [row['key'] for row in self.rows]
        if old_keys != [row['key'] for row in rows[:len(old_keys)]]:
            self.epoch = uuid.uuid4().hex
            self.versions.clear()
        self.sequence = view['sequence']
        self.rows, self.details = rows, details
        self.positions = {row['key']: i for i, row in enumerate(rows)}
        self.meta = {k: v for k, v in view.items() if k != 'turns'}
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
                'hasMore': start > 0, 'meta': self.meta}

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
