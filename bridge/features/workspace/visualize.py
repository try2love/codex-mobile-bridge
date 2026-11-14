"""Bounded visualize references and read-only source loading, including SSH payloads."""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath

from bridge.features.workspace.workspace import Workspace

MARKER = '\ue200visualize\ue202'
END = '\ue201'
MAX_BYTES = 1024 * 1024


def reference_candidates(text):
    if not isinstance(text, str):
        return
    offset = 0
    while True:
        start = text.find(MARKER, offset)
        if start < 0:
            return
        end = text.find(END, start + len(MARKER), start + 8192)
        offset = start + len(MARKER) if end < 0 else end + len(END)
        if end < 0:
            continue
        try:
            value = json.loads(text[start + len(MARKER):end])
        except (ValueError, RecursionError):
            continue
        if (not isinstance(value, dict) or set(value) - {'path', 'mode', 'title'} or
                value.get('mode', 'regular') not in ('regular', 'wide') or
                not isinstance(value.get('path'), str) or
                len(value['path']) > 4096 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value['path']) or
                not value['path'].lower().endswith(('.html', '.htm')) or
                not (Path(value['path']).is_absolute() or PureWindowsPath(value['path']).is_absolute()) or
                'title' in value and (not isinstance(value['title'], str) or len(value['title']) > 200)):
            continue
        yield start, end + len(END), value['path']


def identifier(path):
    return hashlib.sha256(('visualize:' + path).encode('utf-8')).hexdigest()


def descriptors(paths):
    return [{'id': identifier(path), 'name': path.replace('\\', '/').rsplit('/', 1)[-1],
             'reference': path, 'references': [path], 'image': False, 'visualization': True}
            for path in sorted(set(paths))]


def read_source(path, cwd, home=None):
    """Recheck roots on the owning host and walk the original path without symlinks."""
    target = Path(path)
    if not target.is_absolute() or target.suffix.lower() not in ('.html', '.htm'):
        raise PermissionError('可视化文件路径无效')
    roots = [Path(cwd)] if cwd else []
    roots.append(Path(home or os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'visualizations')
    resolved = target.resolve(strict=True)
    if not any(root.resolve() in resolved.parents for root in roots):
        raise PermissionError('可视化文件超出授权目录')
    # Preserve original components: collapsing symlink/.. before the guarded
    # walk can read a different file from the one authorized above.
    with Workspace(target.anchor).download(target.relative_to(target.anchor).as_posix(), limit=MAX_BYTES) as (_, stream):
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('可视化内容超过 1 MB')
    return {'text': raw.decode('utf-8'), 'revision': hashlib.sha256(raw).hexdigest()}
