"""Bounded phone projections. Original desktop state stays authoritative.

Cursors belong to one in-memory timeline epoch. Replacement/reordered desktop
history invalidates them explicitly; appending or editing text does not.
"""
import hashlib
import json
import uuid
from collections import OrderedDict

from bridge.clients.codex.ipc_json import StoredString

PAGE_BYTES = 192 * 1024
TEXT_PREVIEW = 8000
DETAIL_CHARS = 16000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _text_size(value):
    return value.chars if isinstance(value, StoredString) else len(value)


def _text_slice(parts, start, end):
    result = []
    for part in parts:
        size = _text_size(part)
        if start < size and end > 0:
            result.append(part.slice(max(0, start), min(size, end)) if isinstance(part, StoredString)
                          else part[max(0, start):end])
        start -= size
        end -= size
        if end <= 0:
            break
    return ''.join(result)


def _content_identity(value):
    if isinstance(value, StoredString):
        return {'ipcTextSha256': value.digest, 'chars': value.chars}
    if isinstance(value, str) and len(value) > 4096:
        return {'ipcTextSha256': hashlib.sha256(value.encode(errors='surrogatepass')).hexdigest(), 'chars': len(value)}
    if isinstance(value, dict):
        return {key: _content_identity(part) for key, part in value.items()}
    if isinstance(value, (list, tuple)):
        return [_content_identity(part) for part in value]
    return value


def _message_text(message):
    text, output = message.get('text') or '', message.get('output')
    if output and (isinstance(text, StoredString) or isinstance(output, StoredString)):
        return (text, '\n\n', output)
    return text + '\n\n' + output if output else text


class Timeline:
    def __init__(self, image_cache=None):
        self.image_cache = image_cache
        self.epoch = uuid.uuid4().hex
        self.sequence = -1
        self.rows = []
        self.image_views = {}
        self.order_origin = 0
        self.details = {}
        self.detail_sources = {}
        self.detail_image_evidence = {}
        self.source_attachments = {}
        self.initial_users = set()
        self.conversation_rows = []
        self.conversation_positions = {}
        self.process_groups = {}
        self.positions = {}
        self.versions = OrderedDict()
        self.meta = {}
        self.limit = None
        self.conversation = False
        self.window_anchor = None
        self.source_anchor = None
        self.window_more = False

    def update(self, view, limit=None, conversation=False, *, retain_attachments=False):
        if self.sequence == view['sequence'] and self.limit == limit and self.conversation == conversation:
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
        visible = [i for i, entry in enumerate(entries) if entry[2] is None or not process_row(entry[2])]
        start = (visible[-limit] if len(visible) > limit else 0) if conversation and limit else max(0, len(entries) - limit) if limit is not None else 0
        # Keep the loaded prefix while new replies arrive. Expanding history
        # must not hash/copy bodies outside the requested presentation window.
        if self.window_anchor is not None and limit is not None:
            anchor = next((i for i, entry in enumerate(entries) if entry[0] == self.window_anchor), None)
            if anchor is not None:
                start = min(start, anchor)
        self.limit = limit
        self.conversation = conversation
        self.window_more = start > 0 or view.get('historyWindowMore', False)
        self.window_anchor = entries[start][0] if start < len(entries) else None
        # A tool call carried only to interpret a recent result must not expand
        # the next source read to every intervening historical item.
        self.source_anchor = next((entry[0] for entry in entries[start:]
                                   if entry[2] is not None and entry[2].get('kind') != 'fileChanges' and not entry[2].get('_historyCarry')), None)
        rows, details, detail_sources, initial_users = [], {}, {}, set()
        self.source_attachments = {}
        for identity, turn, message, first_user in entries[start:]:
            if message is not None:
                base = hashlib.sha256(encoded(identity[:3])).hexdigest()[:24]
                ordinal = identity[3]
                key = base + '-' + str(ordinal)
                text = _message_text(message)
                parts = text if isinstance(text, tuple) else (text,)
                activity = message['role'] in ('activity', 'fileChanges') and not assistant_output(message)
                preview_limit = 180 if activity else TEXT_PREVIEW
                size = sum(_text_size(part) for part in parts)
                attachments = [{k: str(a[k])[:4096 if k == 'path' else 300] for k in ('type', 'name', 'path', 'desktopId') if k in a}
                               for a in (message.get('attachments') or [])[:20]]
                version = hashlib.sha256(encoded([_content_identity(text), turn.get('model'), turn.get('effort'), attachments, message.get('fileSummary'), message.get('activityFiles'),
                                                 _content_identity(message.get('_detailSource'))])).hexdigest()[:24]
                row = {k: message[k] for k in ('role', 'kind', 'status', 'title', 'phase', 'requestId', 'interaction', 'toolName', 'activityFiles', 'forkMessageId', 'editMessageId', 'revertStepIndex', 'revertMessageId') if k in message}
                row.update(key=key, turnId=turn['id'], order=len(rows), version=version,
                           text=_text_slice(parts, 0, preview_limit),
                           truncated=(activity and bool(text)) or size > TEXT_PREVIEW, turnStatus=turn.get('status'),
                           editable=turn.get('actionable', True) and first_user and message.get('kind') == 'userMessage',
                           forkable=turn.get('actionable', True) and message['role'] == 'assistant' and message.get('phase') != 'commentary' and turn.get('status') == 'completed')
                if turn.get('startedAt') is not None:
                    row['turnStartedAt'] = turn['startedAt']
                if turn.get('completedAt') is not None:
                    row['turnCompletedAt'] = turn['completedAt']
                if turn.get('completionKnown') is False:
                    row['turnCompletionKnown'] = False
                timestamp = message.get('timestamp')
                if timestamp is None and first_user:
                    timestamp = turn.get('startedAt')
                if timestamp is not None:
                    row['timestamp'] = timestamp
                if message['role'] == 'user':
                    row['originalQuestion'] = first_user
                if message.get('fileSummary'):
                    row['fileSummary'] = message['fileSummary']
                    row['text'] = ''
                    row['truncated'] = False
                if assistant_output(message):
                    recorded = turn.get('_recordedReplyLabel', turn)
                    label = message if message.get('model') else recorded
                    model = label.get('model')
                    effort = label.get('effort')
                    if isinstance(model, str) and model.strip() and model.strip() != '<synthetic>':
                        row['model'] = model.strip()[:160]
                        if isinstance(effort, str) and effort.strip().lower() not in ('', 'off', 'none', 'disabled', 'false', '0', '关闭', '无'):
                            row['effort'] = effort.strip()[:80]
                if attachments:
                    row['attachments'] = attachments
                if retain_attachments and (source := message.get('_transferAttachments') or message.get('attachments')):
                    self.source_attachments[key] = source
                rows.append(row)
                if first_user and row['role'] == 'user' and row.get('interaction') != 'response':
                    initial_users.add(key)
                details[key] = text
                if message.get('_detailSource'):
                    detail_sources[key] = message['_detailSource']
                    row['truncated'] = True
            else:
                key = hashlib.sha256(encoded([turn['id'], 'error'])).hexdigest()[:24] + '-error'
                error = turn['error']
                text = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
                if isinstance(error, dict) and isinstance(error.get('message'), str) and error['message'].strip():
                    text = error['message']
                if isinstance(error, dict) and isinstance(error.get('codexErrorInfo'), dict) and 'responseStreamDisconnected' in error['codexErrorInfo'] and error.get('additionalDetails') == 'Connection failed: error sending request':
                    text = '电脑上的 Codex 无法连接模型服务。使用本机 CLI 时，可在聊天详情中检查 CLI 网络。'
                rows.append(dict(key=key, turnId=turn['id'], turnStatus=turn.get('status'), order=len(rows),
                                 role='error', text=text[:TEXT_PREVIEW],
                                 truncated=len(text) > TEXT_PREVIEW, version=hashlib.sha256(text.encode()).hexdigest()[:24]))
                details[key] = text
        old_keys = [row['key'] for row in self.rows]
        new_keys = [row['key'] for row in rows]
        conversation_keys, seen_process, boundary, turn_id = [], set(), 'start', None
        for row in rows:
            if row['turnId'] != turn_id:
                boundary, turn_id = 'start', row['turnId']
            if process_row(row):
                key = process_key(row, boundary)
                if key in seen_process:
                    continue
                seen_process.add(key)
            else:
                key = row['key']
                boundary = key
            conversation_keys.append(key)
        old_conversation = [row['key'] for row in self.conversation_rows]
        previous_keys = set(old_conversation)
        retained = [key for key in conversation_keys if key in previous_keys]
        # Prepending saved turns preserves existing row keys and cursors.
        first = new_keys.index(old_keys[0]) if old_keys and old_keys[0] in new_keys else 0
        if old_keys != new_keys[first:first + len(old_keys)] and not (conversation and old_conversation and retained == old_conversation):
            self.epoch = uuid.uuid4().hex
            self.versions.clear()
            self.order_origin = 0
        elif old_keys:
            self.order_origin -= first
        for index, row in enumerate(rows):
            row['order'] = self.order_origin + index
        self.sequence = view['sequence']
        # Keep native image evidence separate from response attachments, whose
        # private paths are removed by the authenticated presentation layer.
        self.image_views = {row['key']: [a['path'] for a in row.get('attachments', [])
                                       if a.get('type') == 'localImage' and a.get('path')]
                            for row in rows if row.get('kind') in ('ImageView', 'imageView')}
        for key, sources in detail_sources.items():
            paths = [item['path'] for item in sources
                     if item.get('type') in ('ImageView', 'imageView') and item.get('path')]
            if paths:
                self.image_views[key] = paths
        self.rows, self.details = rows, details
        self.detail_sources, self.initial_users = detail_sources, initial_users
        self.detail_image_evidence.clear()
        self.positions = {row['key']: i for i, row in enumerate(rows)}
        self.conversation_rows, self.process_groups, boundary, turn_id = [], {}, 'start', None
        for row in rows:
            if row['turnId'] != turn_id:
                boundary, turn_id = 'start', row['turnId']
            if not process_row(row):
                self.conversation_rows.append(row)
                boundary = row['key']
                continue
            key = process_key(row, boundary)
            if key not in self.process_groups:
                summary = {k: row[k] for k in ('turnId', 'turnStatus', 'turnStartedAt', 'turnCompletedAt', 'order') if k in row}
                summary.update(key=key, role='activity', kind='processPage', text='', title='执行过程', processCursor=self.cursor(key))
                self.conversation_rows.append(summary)
                self.process_groups[key] = []
            self.process_groups[key].append(row)
        for row in self.conversation_rows:
            if row.get('processCursor'):
                entries = [(r['key'], r['version'], r.get('status'), r.get('title'), r.get('toolName'),
                            r.get('timestamp'), r.get('model'), r.get('effort'))
                           for r in self.process_groups[row['key']]]
                row['version'] = hashlib.sha256(encoded(entries)).hexdigest()[:24]
        self.conversation_positions = {row['key']: i for i, row in enumerate(self.conversation_rows)}
        self.meta = {k: v for k, v in view.items() if k != 'turns'}
        self.meta['latestUserTurnId'] = next((t['id'] for t in reversed(view['turns'])
                                              if any(m['role'] == 'user'
                                                     and (not m.get('requestId') or m['requestId'] not in activation_ids)
                                                     for m in t['messages'])), None)
        self.versions[self.sequence] = {row['key']: hashlib.sha256(encoded(row)).digest() for row in rows + self.conversation_rows}
        while len(self.versions) > 16:
            self.versions.popitem(last=False)

    def cursor(self, key):
        return self.epoch + '.' + key

    def position(self, cursor):
        epoch, _, key = cursor.partition('.')
        if epoch != self.epoch or key not in self.positions:
            return None
        return self.positions[key]

    def page(self, limit=20, before=None, projection=None):
        rows = self.conversation_rows if projection == 'conversation' else self.rows
        positions = self.conversation_positions if projection == 'conversation' else self.positions
        end = positions.get(before.partition('.')[2]) if before and before.partition('.')[0] == self.epoch else None if before else len(rows)
        if end is None:
            return {**self.page(projection=projection), 'reset': True}
        limit = max(1, min(100, limit))
        selected, size = [], 0
        for row in reversed(rows[max(0, end - limit):end]):
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

    def changes(self, after, epoch, start, paged=False, through=None, projection=None):
        source = self.conversation_rows if projection == 'conversation' else self.rows
        positions = self.conversation_positions if projection == 'conversation' else self.positions
        position_of = lambda cursor: positions.get(cursor.partition('.')[2]) if cursor and cursor.partition('.')[0] == self.epoch else None
        first = position_of(start) if start else None
        if epoch != self.epoch or (not paged and after not in self.versions) or (start and first is None):
            return {**self.page(projection=projection), 'reset': True}
        previous, current = self.versions.get(after, {}), self.versions[self.sequence]
        rows = [row for row in source[first or 0:]
                if previous.get(row['key']) != current[row['key']]]
        if paged:
            # Revision expiry requires a bounded resync, not replacement of the
            # reader's loaded history. A true source replacement changes epoch.
            position = position_of(through) if through else None
            if through and position is None:
                return {**self.page(projection=projection), 'reset': True}
            if position is not None:
                rows = [row for row in rows if positions[row['key']] > position]
            selected, size = [], 0
            for row in rows:
                cost = len(encoded(row))
                if selected and (len(selected) >= 100 or size + cost > PAGE_BYTES):
                    break
                selected.append(row)
                size += cost
            return {'epoch': self.epoch, 'sequence': self.sequence, 'rows': selected,
                    'next': self.cursor(selected[-1]['key']) if len(selected) < len(rows) else None,
                    'meta': self.meta}
        if len(rows) > 100 or sum(len(encoded(row)) for row in rows) > PAGE_BYTES:
            return {**self.page(projection=projection), 'reset': True}
        return {'epoch': self.epoch, 'sequence': self.sequence, 'rows': rows, 'meta': self.meta}

    def detail(self, cursor, offset=0, version=None):
        position = self.position(cursor)
        if position is None:
            raise ValueError('这条内容已更新，请重新打开聊天')
        row = self.rows[position]
        if row['key'] in self.detail_sources:
            from .model import normalize_state
            value = normalize_state({'turns': [{'items': self.detail_sources[row['key']]}]}, image_cache=self.image_cache)['turns'][0]['messages'][0]
            self.details[row['key']] = _message_text(value)
            self.detail_sources.pop(row['key'])
            if value.get('attachments'):
                self.detail_image_evidence[row['key']] = [a for a in value['attachments']
                    if a.get('type') == 'toolImage' and a.get('desktopId') and a.get('data')]
                row['attachments'] = [{k: v for k, v in a.items() if k != 'data'} for a in value['attachments']]
        text = self.details[row['key']]
        parts = text if isinstance(text, tuple) else (text,)
        size = sum(_text_size(part) for part in parts)
        offset = max(0, min(offset, size)) if version == row['version'] else 0
        end = min(size, offset + DETAIL_CHARS)
        return {'key': row['key'], 'version': row['version'], 'text': _text_slice(parts, offset, end),
                **({'attachments': row['attachments']} if row.get('attachments') else {}),
                'offset': offset, 'next': end if end < size else None}

    def process(self, cursor, after=None, limit=20, *, before=None, sync=False):
        epoch, _, key = cursor.partition('.')
        if epoch != self.epoch or key not in self.process_groups:
            raise ValueError('过程记录已变化，请重新展开')
        rows = self.process_groups[key]
        # The live preview needs the recent end, including under the byte cap.
        # Historical reads keep their ordinary forward cursors.
        latest = after == 'latest'
        start, end = 0, len(rows)
        if before:
            keys = [row['key'] for row in rows]
            if before not in keys:
                raise ValueError('过程记录已变化，请重新展开')
            end = keys.index(before)
        if after and not latest:
            keys = [row['key'] for row in rows]
            if after not in keys:
                raise ValueError('过程记录已变化，请重新展开')
            start = keys.index(after) + 1
        limit = max(1, min(100, limit))
        backward = latest or before is not None
        candidates = reversed(rows[max(0, end - limit):end]) if backward else rows[start:start + limit]
        selected, size = [], 0
        for row in candidates:
            entry = {field: row[field] for field in ('key', 'version', 'role', 'kind', 'title', 'toolName', 'status', 'turnId', 'turnStatus', 'timestamp', 'interaction', 'phase', 'originalQuestion', 'model', 'effort', 'turnStartedAt', 'turnCompletedAt', 'turnCompletionKnown') if field in row}
            entry['detailCursor'] = self.cursor(row['key'])
            key = row['key']
            entry['hasDetail'] = bool(self.details.get(key) or key in self.detail_sources or row.get('attachments'))
            if sync:
                text = self.details.get(key, '')
                sources = self.detail_sources.get(key)
                if sources and row.get('kind') == 'reasoning':
                    item = sources[0]
                    summary = item.get('summary') or item.get('content') or ()
                    if isinstance(summary, list):
                        text = '\n'.join(part for part in summary if isinstance(part, str))
                elif sources and row.get('kind') == 'commandExecution':
                    text = sources[0].get('command') or ''
                plain = row['role'] in ('assistant', 'reasoning', 'user') or row.get('kind') in ('step', 'reasoning', 'todo/write', 'plan')
                cap = 1200 if plain else 180
                preview = text.encode()[:cap].decode(errors='ignore') if isinstance(text, str) else ''
                # Opaque tool bodies, image evidence and large payloads remain
                # on the item detail route. Do not forward unresolved paths.
                complete_summary = sources and row.get('kind') == 'reasoning' and text == preview and bool(text)
                entry.update(order=row['order'] - rows[0]['order'], text=preview,
                             truncated=bool(preview != text or sources and not complete_summary or row.get('attachments')),
                             **({field: row[field] for field in ('phase', 'turnStartedAt', 'turnCompletedAt', 'turnCompletionKnown', 'model', 'effort') if field in row}))
                if assistant_output(row):
                    entry.update(model=row.get('model'), effort=row.get('effort'))
                safe_attachments = [{k: v for k, v in attachment.items() if k != 'path'} for attachment in row.get('attachments', [])
                                    if attachment.get('desktopId') or attachment.get('uploadId')]
                if safe_attachments:
                    entry['attachments'] = safe_attachments
            else:
                entry['text'] = row.get('text', '')[:TEXT_PREVIEW] if row['role'] == 'assistant' else ''

            cost = len(encoded(entry))
            if selected and size + cost > PAGE_BYTES:
                break
            selected.append(entry)
            size += cost
        if sync:
            first = end - len(selected) if backward else start
            last = first + len(selected)
            return {'rows': selected[::-1] if backward else selected,
                    'previous': rows[first]['key'] if selected and first > 0 else None,
                    'next': rows[last - 1]['key'] if selected and last < len(rows) else None}
        if latest:
            return {'epoch': self.epoch, 'sequence': self.sequence, 'rows': selected[::-1],
                    'next': None, 'hasEarlier': len(selected) < len(rows)}
        return {'epoch': self.epoch, 'sequence': self.sequence, 'rows': selected,
                'next': selected[-1]['key'] if selected and start + len(selected) < len(rows) else None}


def assistant_output(row):
    return row.get('role') in ('assistant', 'reasoning') or row.get('kind') == 'reasoning'


def process_row(row):
    if row.get('fileSummary') or row.get('role') in ('fileChanges', 'error'):
        return False
    return (row.get('role') in ('activity', 'reasoning') or row.get('kind') == 'reasoning'
            or row.get('role') == 'user' and row.get('interaction') == 'response'
            or row.get('role') == 'assistant' and row.get('phase') == 'commentary')


def process_key(row, boundary):
    return 'process-' + hashlib.sha256(encoded([row.get('turnId'), boundary])).hexdigest()[:24]
