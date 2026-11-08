"""Read the model and reasoning level recorded for each original assistant reply."""
import os
import sqlite3

from bridge.clients.errors import BridgeUnavailable
from bridge.features.sessions.identity import same_cwd


def _text(value):
    return value if isinstance(value, str) and value.strip() else ''


def reply_models(db, sid, ids):
    ids = list(dict.fromkeys(i for i in ids if isinstance(i, str) and i))
    if not ids or db is None:
        return {}
    # model_usage records the completed call, including internal continuations
    # whose synthetic parent may omit the original reasoning preference.
    columns = {row[1] for row in db.execute('PRAGMA cli.table_info(model_usage)')}
    usage = {'session_id', 'turn_id', 'assistant_message_id', 'model_id', 'variant', 'status',
             'completed_at', 'attempt_index'} <= columns
    join = ("""LEFT JOIN cli.model_usage u ON u.session_id=m.session_id
                  AND u.turn_id=json_extract(m.data,'$.anchor.turnId') AND u.assistant_message_id=m.id
                  AND u.status='completed'""" if usage else '')
    actual = 'u.model_id,u.variant' if usage else 'NULL,NULL'
    order = ' ORDER BY u.completed_at DESC, u.attempt_index DESC' if usage else ''
    result = {}
    for start in range(0, len(ids), 200):
        batch = ids[start:start + 200]
        sql = f"""SELECT m.id, {actual}, json_extract(m.data,'$.modelId'),
                         coalesce(json_extract(p.data,'$.modelSelection.modelId'),json_extract(p.data,'$.model.modelID')),
                         coalesce(json_extract(p.data,'$.modelSelection.options.reasoningLevel'),json_extract(p.data,'$.model.variant'))
                  FROM cli.message m LEFT JOIN cli.message p ON p.id=json_extract(m.data,'$.parentID')
                      AND p.session_id=m.session_id AND json_extract(p.data,'$.role')='user'
                  {join} WHERE m.session_id=? AND m.id IN ({','.join('?' for _ in batch)})
                      AND json_extract(m.data,'$.role')='assistant'{order}"""
        for mid, actual_model, level, reported, requested, requested_level in db.execute(sql, (sid, *batch)):
            if mid in result:
                continue
            model = _text(actual_model) or _text(reported) or _text(requested)
            if model:
                result[mid] = {'model': model}
                effort = _text(level) or (_text(requested_level) if requested == model else '')
                if effort:
                    result[mid]['effort'] = effort
    return result


def native_reply_models(home, sid, workspace, ids):
    from .history import _history, _target
    ids = list(ids)
    if not ids:
        return {}
    try:
        with _history(home) as db:
            if db is not None and same_cwd(_target(db, sid)['cwd'], workspace, os.name == 'nt'):
                return reply_models(db, sid, ids)
    except (BridgeUnavailable, OSError, ValueError, sqlite3.Error):
        pass  # Missing historical labels must not obscure a verified live chat.
    return {}
