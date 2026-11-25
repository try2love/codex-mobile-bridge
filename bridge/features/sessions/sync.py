"""Read-only synchronization over the existing timeline projection."""
import hashlib
import re
import time

from .timeline import PAGE_BYTES, assistant_output, encoded

_ITEM = re.compile(r'(?:[0-9a-f]{24}-(?:[0-9]+|error)|process-[0-9a-f]{24})\Z')
_VERSION = re.compile(r'[0-9a-f]{24}\Z')
_RUNNING = {'inProgress', 'active'}
CONTEXT_FIELDS = ('contextUsage', 'contextUsageStatus', 'lastPromptContextUsage')


class SyncCursorChanged(ValueError):
    """A validated read cursor no longer belongs to the current projection."""

    code = 'sync_cursor_changed'


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()[:24]


def parse_query(query):
    mode = query.get('mode', ['recent'])[0]
    fields = {
        'recent': {'limit', 'ifVersion', 'known', 'omitTurn', 'indexOnly', 'heldText'},
        'older': {'limit', 'before', 'ifVersion', 'known', 'omitTurn', 'indexOnly'},
        'range': {'limit', 'start', 'end', 'ifVersion', 'known', 'omitTurn', 'indexOnly'},
        'process': {'key', 'limit', 'before', 'after', 'ifVersion', 'known', 'indexOnly'},
        'body': {'key', 'offset', 'version', 'ifVersion'},
        'watch': {'ifVersion', 'waitMs'},
    }
    if mode in fields and mode != 'watch':
        fields[mode].add('scan')
    if len(query.get('mode', (mode,))) != 1 or mode not in fields or set(query) - fields[mode] - {'mode', 'host', 'entry', 'sessionId', 'side'}:
        raise ValueError('无效同步参数')
    options = {}
    for name in fields[mode] - {'known', 'omitTurn', 'heldText'}:
        if name not in query:
            continue
        values = query[name]
        if len(values) != 1 or not values[0]:
            raise ValueError('无效同步参数')
        value = values[0]
        if name == 'scan':
            if not re.fullmatch(r'[0-9a-f]{32}', value):
                raise ValueError('无效同步续页')
        elif name == 'indexOnly':
            if value != '1':
                raise ValueError('无效同步参数')
            value = True
        elif name in ('limit', 'offset', 'waitMs'):
            if not value.isascii() or not value.isdigit() or len(value) > 9:
                raise ValueError('无效同步参数')
            value = int(value)
            if name == 'waitMs' and not 0 <= value <= 8000:
                raise ValueError('同步等待超过限制')
            if name == 'limit' and not 1 <= value <= 100:
                raise ValueError('无效同步页大小')
        elif not (_VERSION if name in ('version', 'ifVersion') else _ITEM).fullmatch(value):
            if not (name == 'after' and value == 'latest'):
                raise ValueError('无效同步游标')
        options[name] = value
    known = query.get('known', ())
    if len(known) > 100:
        raise ValueError('同步索引超过 100 项')
    if 'known' in fields[mode]:
        options['known'] = {}
        for item in known:
            key, _, version = item.partition(':')
            if not _ITEM.fullmatch(key) or not _VERSION.fullmatch(version) or key in options['known']:
                raise ValueError('无效同步索引')
            options['known'][key] = version
    held_text = query.get('heldText', ())
    if len(held_text) > 40 or any(not _VERSION.fullmatch(value) for value in held_text):
        raise ValueError('无效的正文索引')
    if held_text:
        options['heldText'] = frozenset(held_text)
    omit_turns = query.get('omitTurn', ())
    if len(omit_turns) > 100 or any(not t or len(t) > 512 or any(ord(c) < 32 for c in t) for t in omit_turns):
        raise ValueError('无效的跳过回合列表')
    if omit_turns:
        options['omitTurn'] = frozenset(omit_turns)
    required = {'older': {'before'}, 'range': {'start', 'end'}, 'process': {'key'}, 'body': {'key'}}
    if required.get(mode, set()) - options.keys() or 'before' in options and 'after' in options:
        raise ValueError('缺少同步定位参数')
    return mode, options


def primary(row):
    return row.get('role') in ('user', 'assistant')


def cursors(row):
    if row.get('kind') == 'processPage':
        return {'processCursor': row['key']}
    return {'detailCursor': row['key']}


def select_page(timeline, mode, limit=20, before=None, start=None, end=None):
    """Return a contiguous projection, including the initial input of a cut turn."""
    source = timeline.conversation_rows
    positions = timeline.conversation_positions
    if mode == 'range':
        first, last = positions.get(start), positions.get(end)
        if first is None or last is None or first > last:
            return [], None, False, False
        # Both anchors were observed in the same projection. A cap means the
        # response is partial, so it cannot prove deletions in the caller's range.
        selected = source[first:last + 1]
        anchors = [i for i, row in enumerate(selected) if primary(row)]
        complete = len(anchors) <= limit
        return selected if complete else selected[:anchors[limit]], None, False, complete

    stop = positions.get(before) if before else len(source)
    if stop is None:
        return [], None, False, False
    default_count = 20 if mode == 'recent' else limit
    anchors = [i for i in range(stop) if primary(source[i])]
    first = anchors[-default_count] if len(anchors) >= default_count else 0
    if not before and first:
        first_turn = source[first]['turnId']
        initial = next((i for i, row in enumerate(source[:first])
                        if row['turnId'] == first_turn and row['key'] in timeline.initial_users), None)
        if timeline.meta.get('syncPartialHead') and source[0]['turnId'] == first_turn:
            initial = None
        # Keep the exact recent quota plus the earlier input. The gap is
        # still pageable at the first counted row.
        extra = source[initial] if initial is not None else None
    else:
        extra = None
    selected = source[first:stop]
    if extra is not None:
        summaries = [row for row in source[initial + 1:first]
                     if row['turnId'] == first_turn and row.get('kind') == 'processPage']
        selected = [extra, *summaries, *selected]
    has_more = first > 0 or timeline.window_more or timeline.meta.get('historyComplete') is False or bool(timeline.meta.get('savedHistoryMore'))
    return selected, source[first]['key'] if first < stop and has_more else None, has_more, True


def _bounded_rows(rows):
    # Use stable per-row preview limits even when only a subset of the page
    # needs its body. Long bodies still use the ordinary chunk endpoint.
    result = []
    for row in rows:
        text = row.get('text', '')
        preview = text.encode()[:6000].decode(errors='ignore')
        if preview != text:
            row = {**row, 'text': preview, 'truncated': True}
        result.append(row)
    return result


def _conditional(index, rows, version, known=None, ifVersion=None):
    unchanged = [item['key'] for item in index
                 if ifVersion == version or (known or {}).get(item['key']) == item['version']]
    keys = set(unchanged)
    reused_bytes = sum(len(row.get('text', '').encode('utf-8')) for row in rows if row['key'] in keys)
    return {'version': version, 'notModified': ifVersion == version, 'index': index,
            'unchanged': unchanged, 'rows': [row for row in rows if row['key'] not in keys],
            **({'reusedBytes': reused_bytes} if reused_bytes else {})}


def page(timeline, mode, *, known=None, ifVersion=None, omitTurn=(), indexOnly=False, heldText=(), **options):
    if mode == 'range':
        options.setdefault('limit', 100)
    selected, before, has_more, complete = select_page(timeline, mode, **options)
    selected = _bounded_rows(selected)
    # Charge the contiguous page before dropping cached bodies, so cache
    # residency never changes the history boundary.
    costs = [len(encoded(row)) for row in selected]
    size = sum(costs)
    while len(selected) > 1 and size > PAGE_BYTES:
        if mode == 'range':
            selected.pop()
            size -= costs.pop()
            complete = False
        else:
            selected.pop(0)
            size -= costs.pop(0)
            before, has_more = selected[0]['key'], True
    rows = [dict(row, id=row['key'], **cursors(row)) for row in selected if row['turnId'] not in omitTurn]
    for row in rows:
        if row.get('role') == 'user':
            row['originalQuestion'] = row['key'] in timeline.initial_users
    index_fields = ('key', 'version', 'role', 'kind', 'turnId', 'turnStatus', 'turnStartedAt', 'turnCompletedAt',
                    'turnCompletionKnown', 'timestamp', 'originalQuestion', 'phase', 'interaction', 'editable', 'forkable',
                    'editMessageId', 'forkMessageId', 'revertMessageId', 'revertStepIndex', 'order',
                    'detailCursor', 'processCursor', 'model', 'effort')
    index = [{**{key: row[key] for key in index_fields if key in row},
              **({'model': row.get('model'), 'effort': row.get('effort')} if assistant_output(row) else {})}
             for row in rows]
    turns = {}
    for row in rows:
        turn = turns.setdefault(row['turnId'], {'id': row['turnId'], 'status': row.get('turnStatus'), 'processes': []})
        for key, field in (('turnStartedAt', 'startedAt'), ('turnCompletedAt', 'completedAt')):
            if key in row:
                turn[field] = row[key]
        if row.get('originalQuestion') and row.get('timestamp') is not None:
            turn['questionTime'] = row['timestamp']
        if row.get('role') == 'error' and row.get('text'):
            turn['error'] = row['text'][:500]
        if row.get('processCursor'):
            turn['processes'].append({key: row[key] for key in ('key', 'version', 'processCursor', 'order', 'title')})
    coverage = {'start': selected[0]['key'] if selected else None, 'end': selected[-1]['key'] if selected else None,
                'complete': complete and mode == 'range'}
    # An ordinary recent or older page cannot prove absence. For an exact
    # matched range the caller's known keys are the previously observed members.
    # A collapsed conversation projection omits process children that remain
    # present in the flat source. Absence must be checked against both views.
    present = timeline.conversation_positions.keys() | timeline.positions.keys()
    deleted = sorted(set(known or ()) - present) if coverage['complete'] else []
    # Orders are relative to the loaded source, so prepending older records
    # may shift them even though the contents of this interval stay identical.
    if mode == 'range' and not selected and timeline.meta.get('syncSourceComplete'):
        deleted = sorted(key for key in {*list(known or ()), options['start'], options['end']}
                         if key not in present)
    version = digest([[{k: v for k, v in item.items() if k != 'order'} for item in index],
                      list(turns), before, has_more, coverage])
    result = _conditional(index, [] if indexOnly else rows, version, known, ifVersion)
    if heldText:
        # The client can reuse a text body proved identical to this authorized
        # row. All metadata and attachment references still come from here.
        for position, row in enumerate(result['rows']):
            text = row.get('text')
            if text:
                fingerprint = hashlib.sha256(text.encode()).hexdigest()[:24]
                if fingerprint in heldText:
                    result['rows'][position] = {**{k: v for k, v in row.items() if k != 'text'}, 'textRef': fingerprint}
    meta = {k: v for k, v in timeline.meta.items() if not k.startswith('sync')}
    return {**result,
            **({'indexOnly': True} if indexOnly else {}), 'turns': list(turns.values()),
            'before': before, 'hasMore': has_more, 'range': coverage, 'deleted': deleted,
            'reset': not complete and not index, 'meta': meta}


def reference_rows(timeline, result):
    """File metadata follows the authorized page, including reused text rows."""
    return [timeline.rows[timeline.positions[item['key']]] for item in result['index']
            if item['key'] in timeline.positions]


def process(timeline, *, key, before=None, after=None, limit=20, known=None, ifVersion=None, indexOnly=False,
            from_start=False):
    group = timeline.process_groups.get(key)
    if not group:
        raise SyncCursorChanged('过程记录已变化，正在更新')
    if any(cursor and cursor != 'latest' and cursor not in {row['key'] for row in group}
           for cursor in (before, after)):
        raise SyncCursorChanged('过程记录已变化，正在更新')
    latest = not before and (after == 'latest' or not from_start and not after and group[-1].get('turnStatus') in _RUNNING)
    if latest:
        after = 'latest'
    result = timeline.process(timeline.cursor(key), before=before, after=after, limit=limit, sync=True)
    rows = [dict(row, id=row['key'], detailCursor=row['key']) for row in result['rows']]
    index = [{k: v for k, v in row.items() if k not in ('text', 'attachments')} for row in rows]
    if latest and known and result['previous']:
        # Join only the contiguous prefix immediately preceding this latest page.
        # Check the current projection before allowing any cached body to paint.
        first = next(i for i, row in enumerate(group) if row['key'] == result['previous'])
        held = []
        remaining_bytes = PAGE_BYTES - sum(len(encoded(row)) for row in rows)
        while first and known.get(group[first - 1]['key']) == group[first - 1]['version']:
            row = group[first - 1]
            item = {field: row[field] for field in
                    ('key', 'version', 'role', 'kind', 'title', 'toolName', 'status', 'turnId', 'turnStatus',
                     'phase', 'timestamp', 'interaction', 'originalQuestion', 'turnStartedAt', 'turnCompletedAt', 'turnCompletionKnown', 'model', 'effort') if field in row}
            if assistant_output(row):
                item.update(model=row.get('model'), effort=row.get('effort'))
            item.update(order=row['order'] - group[0]['order'], detailCursor=row['key'])
            remaining_bytes -= len(encoded(item))
            if remaining_bytes < 0:
                break
            held.append(item)
            first -= 1
        if held:
            index = [*reversed(held), *index]
            result['previous'] = group[first]['key'] if first else None
    version = digest([index, result['previous'], result['next']])
    deleted = sorted(set(known or ()) - timeline.positions.keys()) if timeline.meta.get('syncSourceComplete') else []
    return {**_conditional(index, [] if indexOnly else rows, version, known, ifVersion),
            **({'indexOnly': True} if indexOnly else {}), 'previous': result['previous'], 'next': result['next'], 'hasEarlier': bool(result['previous']),
            'hasMore': bool(result['next']), 'deleted': deleted, 'files': []}


def body(timeline, *, key, offset=0, version=None, ifVersion=None):
    position = timeline.positions.get(key)
    if position is None:
        raise SyncCursorChanged('这条内容已更新，正在更新聊天')
    entry = timeline.rows[position]
    if ifVersion == entry['version']:
        detail = timeline.detail(timeline.cursor(key), offset=offset, version=version)
        reused_bytes = len(detail['text'].encode('utf-8'))
        return {'key': key, 'version': ifVersion, 'notModified': True, 'files': [],
                **({'reusedBytes': reused_bytes} if reused_bytes else {})}
    return timeline.detail(timeline.cursor(key), offset=offset, version=version)


def watch_file(path):
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def wait_for_watch(read, wait_ms, source=None):
    """Recheck a compact source marker before computing another projection."""
    probe, wait, interval = source or (lambda: None, lambda old, delay: time.sleep(delay), 2)
    before = probe() if wait_ms else None
    deadline = time.monotonic() + wait_ms / 1000
    value = read()
    while value.get('notModified') and (remaining := deadline - time.monotonic()) > 0:
        wait(before, min(remaining, interval))
        after = probe()
        if after is None or after != before:
            value = read()
        before = after
    return {**value, 'waitSupported': True}


def watch(timeline, *, ifVersion=None):
    control = timeline.meta.get('syncControl', {key: timeline.meta.get(key) for key in
        ('status', 'connected', 'connecting', 'requests', 'goal', 'access', 'permissionMode', *CONTEXT_FIELDS)})
    version = digest([timeline.meta.get('syncSourceRevision'), control,
                      [(row['key'], row['version'], row.get('turnStatus'), row.get('status'), row.get('timestamp'), row.get('model'), row.get('effort'))
                       for row in timeline.rows[-20:]]])
    return {'version': version, 'notModified': version == ifVersion}


def read(timeline, mode, **options):
    if mode == 'process':
        return process(timeline, **options)
    if mode == 'body':
        return body(timeline, **options)
    if mode == 'watch':
        return watch(timeline, **options)
    result = page(timeline, mode, **options)
    if mode == 'recent':
        result['sourceVersion'] = watch(timeline)['version']
    return result


def needs_older(timeline, mode, options):
    """Check whether the loaded source can serve the requested interval."""
    if mode == 'body':
        return options['key'] not in timeline.positions
    if mode == 'process':
        group = timeline.process_groups.get(options['key'])
        return not group or (bool(timeline.meta.get('syncPartialHead')) and
                             group[0]['turnId'] == timeline.rows[0]['turnId'])
    if mode == 'range':
        return options['start'] not in timeline.conversation_positions or options['end'] not in timeline.conversation_positions
    source = timeline.conversation_rows
    before = options.get('before')
    stop = timeline.conversation_positions.get(before, 0) if before else len(source)
    limit = 20 if mode == 'recent' else options.get('limit', 20)
    anchors = [row for row in source[:stop] if primary(row)]
    if len(anchors) < limit:
        return True
    first_turn = anchors[-limit]['turnId']
    return bool(source and source[0]['turnId'] == first_turn and
                (timeline.meta.get('syncPartialHead') or
                 not any(row['turnId'] == first_turn and row['key'] in timeline.initial_users
                         for row in source[:stop])))
