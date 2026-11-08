"""Map original ZCode attachment evidence onto the shared chat/file viewers."""
import json
import os
import re
import stat
from pathlib import Path

from bridge.features.workspace.files import workspace_relative_path
from bridge.features.workspace.uploads import MAX_FILE
from bridge.features.workspace.workspace import Workspace


_IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
_ARTIFACT = re.compile(r'zcode-artifact://(sess_[0-9a-f-]{36})/(tool-result-[0-9a-f-]{36})\Z')


def attachments(sid, items, command=None):
    result = []
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('fileName'), str):
            continue
        ref = item.get('ref')
        mime = item.get('mime')
        size = item.get('bytes')
        value = {'name': item['fileName'], 'mime': mime, 'size': size}
        if command:
            value['_sourceCommand'] = command
        match = _ARTIFACT.fullmatch(ref) if isinstance(ref, str) else None
        if match and match[1] == sid and mime in _IMAGE_MIMES and type(size) is int and 0 < size <= MAX_FILE:
            value.update(type='image', _nativeImage={'ref': ref, 'mime': mime, 'size': size})
        result.append(value)
    return result


def read_image(home, sid, source):
    from .history import _history, _target

    ref, mime, size = (source.get(k) for k in ('ref', 'mime', 'size'))
    match = _ARTIFACT.fullmatch(ref) if isinstance(ref, str) else None
    if not match or match[1] != sid or mime not in _IMAGE_MIMES or type(size) is not int or not 0 < size <= MAX_FILE:
        raise ValueError('ZCode 图片引用无效')
    with _history(home) as db:
        _target(db, sid)
        visible = db.execute("""
            SELECT 1 FROM cli.part AS p JOIN cli.message AS m
              ON m.id=p.message_id AND m.session_id=p.session_id
            WHERE p.session_id=? AND json_extract(p.data,'$.type')='file'
              AND json_extract(p.data,'$.url')=? AND json_extract(p.data,'$.mime')=?
              AND coalesce(json_extract(p.data,'$.synthetic'),0)=0
              AND coalesce(json_extract(p.data,'$.ignored'),0)=0
              AND coalesce(json_extract(p.data,'$.metadata.visibility'),'visible')='visible'
              AND json_extract(m.data,'$.role')='user'
              AND json_extract(m.data,'$.semantics.uiVisibility')='visible'
              AND coalesce(json_extract(m.data,'$.synthetic'),0)=0 LIMIT 1
            """, (sid, ref, mime)).fetchone()
    if not visible:
        raise ValueError('图片已不属于此聊天')
    # Artifacts contain data URLs, not raw images. Only open the exact session's
    # upload using the same no-follow directory/regular-file checks as project reads.
    storage = Workspace(Path(home).parent)
    prefix = ('data:' + mime + ';base64,').encode('ascii')
    encoded_size = (size + 2) // 3 * 4
    with storage.directory(['cli', 'artifacts', sid]) as (folder, handle):
        pattern = re.compile(r'prompt-attachment-upload-[\w-]+-' + re.escape(match[2]) + r'\.txt\Z')
        with os.scandir(handle if handle is not None else folder) as entries:
            names = [entry.name for entry in entries if pattern.fullmatch(entry.name)]
        if len(names) != 1:
            raise ValueError('ZCode 原生图片不可用')
        with os.fdopen(storage.open_file(folder, handle, names[0], os.O_RDONLY), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != len(prefix) + encoded_size or stream.read(len(prefix)) != prefix:
                raise ValueError('ZCode 原生图片已变化')
            data = stream.read(encoded_size + 1)
    if len(data) != encoded_size:
        raise ValueError('ZCode 原生图片不完整')
    return {'mediaType': mime, 'data': data.decode('ascii')}


def remember_project_files(db, sid, command, files):
    # V4 preserves sourceCommandId and the attachment order. The gateway puts
    # validated project references before ordinary uploads when building this command.
    db.execute('CREATE TABLE IF NOT EXISTS zcode_project_attachments'
               ' (command TEXT PRIMARY KEY, sid TEXT NOT NULL, paths TEXT NOT NULL)')
    db.execute('INSERT INTO zcode_project_attachments VALUES (?,?,?)',
               (command, sid, json.dumps([str(f['path']) for f in files])))


def bind_project_files(db, sid, messages, cwd):
    by_command = {}
    for message in messages:
        for index, item in enumerate(message.get('attachments', ())):
            command = item.pop('_sourceCommand', None)
            if isinstance(command, str):
                by_command.setdefault(command, []).append((index, item))
    if not by_command or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='zcode_project_attachments'").fetchone():
        return
    for command, paths in db.execute('SELECT command,paths FROM zcode_project_attachments WHERE sid=? AND command IN ('
                                     + ','.join('?' for _ in by_command) + ')', (sid, *by_command)):
        paths = json.loads(paths)
        for index, item in by_command[command]:
            if index >= len(paths) or Path(paths[index]).name != item.get('name'):
                continue
            try:
                relative = workspace_relative_path(paths[index], cwd)
            except (ValueError, RuntimeError, OSError):
                continue
            if relative is not None:
                item['type'] = 'file'
                item['path'] = paths[index]
