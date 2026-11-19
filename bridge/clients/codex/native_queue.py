"""Read pending inputs that still belong to the local Codex App."""
import json
import ntpath
import sqlite3
from contextlib import closing

from bridge.features.sessions.model import desktop_file_attachments, user_display_text


def _row(identifier, text, files, created):
    names = list(dict.fromkeys(ntpath.basename(name) for name in files if isinstance(name, str) and name))
    return {'id': identifier, 'text': user_display_text(text), 'attachments': [name for name in names if name],
            'at': created / 1000 if type(created) is int and created > 0 else None}


def read_native_queue(home, thread_id):
    """Project the durable app-server and Desktop queues, in their native order.

    None means a known source could not be read, not that its queue is empty.
    Neither source is a Bridge submission receipt or grants write authority.
    """
    rows, seen = [], set()
    database = home / 'queue_1.sqlite'
    try:
        if database.is_file():
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as conn:
                for identifier, payload, created in conn.execute(
                        'SELECT id,payload_json,created_at_ms FROM queued_items WHERE thread_id=? ORDER BY queue_order,id',
                        (thread_id,)):
                    value = json.loads(payload)['UserInput']
                    inputs = value['content']
                    if not isinstance(inputs, list):
                        return None
                    text = '\n'.join(item['text'] for item in inputs if item.get('type') == 'text')
                    files = [item['name'] for item in desktop_file_attachments(text)]
                    files += [item.get('path') or '图片' for item in inputs
                              if item.get('type') in ('localImage', 'image')]
                    rows.append(_row(identifier, text, files, created))
                    seen.add(identifier)
                    if isinstance(value.get('client_id'), str):
                        seen.add(value['client_id'])

        state = home / '.codex-global-state.json'
        if state.is_file():
            legacy = json.loads(state.read_text(encoding='utf-8')).get('queued-follow-ups', {})
            for item in legacy.get(thread_id, []):
                identifier, text = item['id'], item.get('text', '')
                submission = item.get('submission') or {}
                if (not isinstance(identifier, str) or not identifier or not isinstance(text, str)
                        or identifier in seen or item.get('clientUserMessageId') in seen
                        or submission.get('hostId', 'local') != 'local'
                        or submission.get('status') in ('sending', 'outcome-unknown')):
                    continue
                context = item.get('context') or {}
                files = [file['name'] for file in desktop_file_attachments(text)]
                files += [file.get('name') or file.get('path') for file in context.get('fileAttachments', [])]
                files += [file.get('localPath') or '图片' for file in context.get('imageAttachments', [])]
                rows.append(_row(identifier, text, files, item.get('createdAt')))
                seen.add(identifier)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
        return None
    return rows
