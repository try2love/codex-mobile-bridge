"""Read Codex's persisted message index when its rollout has been compacted away."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connection(home):
    path = Path(home) / 'thread_history_1.sqlite'
    with path.open('rb') as stream:
        wal = stream.read(20)[18:20] == b'\x02\x02'
    if wal and not all(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('原生历史索引尚不可读取')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def signature(db, sid, end):
    return list(db.execute('SELECT COUNT(*),MAX(COALESCE(updated_at_ordinal,rollout_ordinal)) '
                          'FROM thread_items WHERE thread_id=? AND rollout_ordinal<=?', (sid, end)).fetchone())


def pin(home, sid):
    path = Path(home) / 'thread_history_1.sqlite'
    stamp = path.stat()
    with connection(home) as db:
        end = db.execute('SELECT MAX(rollout_ordinal) FROM thread_items WHERE thread_id=?', (sid,)).fetchone()[0]
        if end is None:
            raise FileNotFoundError('原生历史索引没有保存这条会话的消息')
        mark = signature(db, sid, end)
        last = db.execute('SELECT turn_id FROM thread_items WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1', (sid,)).fetchone()
        header = db.execute('SELECT * FROM thread_turns WHERE thread_id=? AND turn_id=?', (sid, last[0])).fetchone()
        if header is None:
            raise ValueError('原生历史缺少回合记录')
    return {'id': sid, 'end': end, 'signature': mark, 'file': [stamp.st_dev, stamp.st_ino], 'lastTurn': dict(header)}


def verify(home, db, snapshot):
    stamp = (Path(home) / 'thread_history_1.sqlite').stat()
    if ([stamp.st_dev, stamp.st_ino] != snapshot['file'] or
            signature(db, snapshot['id'], snapshot['end']) != snapshot['signature']):
        raise ValueError('聊天历史已经变化，请重新准备传输')


def turns(home, snapshot, limit=None, after=-1):
    """Release every bounded read transaction before projecting or yielding content."""
    cursor = after
    pending = None
    sid, end = snapshot['id'], snapshot['end']
    while True:
        with connection(home) as db:
            verify(home, db, snapshot)
            columns = 'turn_id,item_id,rollout_ordinal,item_json'
            if limit is not None:
                rows = db.execute('SELECT ' + columns + ' FROM thread_items WHERE thread_id=? AND rollout_ordinal<=? '
                                  'ORDER BY rollout_ordinal DESC LIMIT ?', (sid, end, limit + 1)).fetchall()
                more = len(rows) > limit
                rows = list(reversed(rows[:limit]))
            else:
                rows = db.execute('SELECT ' + columns + ' FROM thread_items WHERE thread_id=? AND rollout_ordinal>? '
                                  'AND rollout_ordinal<=? ORDER BY rollout_ordinal LIMIT 128', (sid, cursor, end)).fetchall()
                more = False
            headers = {}
            for row in rows:
                tid = row['turn_id']
                if tid not in headers:
                    header = db.execute('SELECT * FROM thread_turns WHERE thread_id=? AND turn_id=?', (sid, tid)).fetchone()
                    if header is None:
                        raise ValueError('原生历史缺少回合记录')
                    headers[tid] = snapshot['lastTurn'] if snapshot.get('lastTurn', {}).get('turn_id') == tid else dict(header)
            initial = None
            if more and rows:
                first = headers[rows[0]['turn_id']].get('first_user_item_id')
                if first and all(row['item_id'] != first for row in rows):
                    initial = db.execute('SELECT ' + columns + ' FROM thread_items WHERE thread_id=? AND item_id=? '
                                         'AND rollout_ordinal<=?', (sid, first, end)).fetchone()
        if not rows:
            break
        if initial is not None:
            rows.insert(0, initial)
        for index, row in enumerate(rows):
            tid = row['turn_id']
            if pending is None or pending['turnId'] != tid:
                if pending is not None:
                    yield pending
                header = headers[tid]
                pending = {'turnId': tid, 'status': header['status'], 'error': json.loads(header['error_json']) if header.get('error_json') else None,
                           'turnStartedAtMs': header['started_at'] * 1000 if header.get('started_at') else None,
                           'turnCompletedAtMs': header['completed_at'] * 1000 if header.get('completed_at') else None, '_firstUserItemId': header.get('first_user_item_id'),
                           '_itemsComplete': not (more and index == 0), 'items': []}
            item = json.loads(row['item_json'])
            if not isinstance(item, dict) or not isinstance(item.get('type'), str):
                raise ValueError('原生历史消息格式不可读取')
            pending['items'].append(item)
            pending['_archiveNextOrdinal'] = row['rollout_ordinal']
        cursor = rows[-1]['rollout_ordinal']
        if limit is not None:
            break
    with connection(home) as db:
        verify(home, db, snapshot)
    if pending is not None:
        yield pending


def history(home, meta, snapshot, limit):
    saved = list(turns(home, snapshot, limit))
    return {'id': snapshot['id'], 'title': meta.get('name') or meta.get('title'), 'cwd': meta['cwd'],
            'modelProvider': meta.get('model_provider'), 'latestModel': meta.get('model'), 'turns': saved,
            'turnsPagination': {'hasLoadedOldest': snapshot['signature'][0] <= limit}, 'requests': [],
            'threadRuntimeStatus': {'type': 'notLoaded'}, 'historyReadSource': 'native-index-retained',
            'savedHistoryPartial': True}
