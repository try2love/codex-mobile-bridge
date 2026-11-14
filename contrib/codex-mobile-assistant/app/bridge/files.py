"""Serve only artifacts referenced by this chat inside its workspace/visualizations."""
import hashlib
import json
import re
from pathlib import Path

from .model import ordered_turns, items_array

MARKDOWN_PATH = re.compile(r'!?\[[^\]\n]*\]\((?:<([^>]+)>|([^\n)]+))\)')
VISUALIZATION = re.compile(r'\ue200visualize\ue202([^\ue201]*)\ue201')


def artifact_paths(state, codex_home):
    roots = [Path(codex_home) / 'visualizations']
    if state.get('cwd'):
        roots.append(Path(state['cwd']))
    roots = [root.resolve() for root in roots]
    candidates = set()
    visualizations = set()
    for turn in ordered_turns(state):
        for item in items_array(turn.get('items')):
            if item.get('type') in ('agentMessage', 'assistantMessage'):
                for match in MARKDOWN_PATH.finditer(item.get('text', '')):
                    candidates.add(match[1] or match[2])
                for match in VISUALIZATION.finditer(item.get('text', '')):
                    try:
                        descriptor = json.loads(match[1])
                    except ValueError:
                        continue
                    if isinstance(descriptor, dict) and isinstance(descriptor.get('path'), str):
                        candidates.add(descriptor['path'])
                        visualizations.add(descriptor['path'])
            for attachment in item.get('content', []) if isinstance(item.get('content'), list) else []:
                if isinstance(attachment, dict) and attachment.get('type') in ('localImage', 'image', 'file'):
                    value = attachment.get('path', attachment.get('url'))
                    if isinstance(value, str):
                        candidates.add(value)
    result = {}
    for raw in candidates:
        # Preserve the displayed link while removing an editor line suffix from the path.
        value = re.sub(r':\d+$', '', raw)
        path = Path(value)
        if not path.is_absolute():
            continue
        try:
            path = path.resolve(strict=True)
            if not any(root in path.parents for root in roots) or not path.is_file() or path.stat().st_size > 50 * 1024 * 1024:
                continue
        except OSError:
            continue
        key = hashlib.sha256(str(path).encode()).hexdigest()
        result[key] = {'path': path, 'reference': raw, 'name': path.name,
                       'visualization': raw in visualizations and path.suffix.lower() in ('.html', '.htm'),
                       'image': path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp')}
    return result


def preview_visualization(artifact):
    if not artifact.get('visualization'):
        raise ValueError('此文件不是可视化预览')
    with artifact['path'].open('rb') as stream:
        content = stream.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError('可视化超过 1 MB，请下载查看')
    fragment = content.decode('utf-8-sig')
    # Served as a separate, opaque-origin sandbox; never inserted into the chat DOM.
    return ('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>:root{color-scheme:light;--background:#fff;--foreground:#171717;--card:#fff;--card-foreground:#171717;--muted:#f5f5f5;--muted-foreground:#737373;--border:#e5e5e5;--primary:#171717;--primary-foreground:#fff;--accent:#f5f5f5;--accent-foreground:#171717;--font-sans:system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;padding:8px;font:14px/1.5 var(--font-sans);color:var(--foreground);background:var(--background)}button,input,select,textarea{font:inherit}button{cursor:pointer}img,svg{max-width:100%}</style>
<script>window.openai={widgetState:null,setWidgetState:async function(value){this.widgetState=value}};</script>
</head><body>''' + fragment + '</body></html>').encode('utf-8')


def project_path(cwd, relative=''):
    if not cwd:
        raise ValueError('此对话没有项目目录')
    root = Path(cwd).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('项目目录不存在')
    part = Path(relative)
    if part.is_absolute() or part.drive or ':' in relative or '\x00' in relative:
        raise ValueError('文件路径无效')
    target = (root / part).resolve(strict=True)
    if target != root and root not in target.parents:
        raise ValueError('只能访问当前项目目录内的文件')
    return root, target


def project_listing(cwd, relative='', offset=0, focus=''):
    root, folder = project_path(cwd, relative)
    if not folder.is_dir():
        raise ValueError('不是文件夹')
    rows = []
    for item in folder.iterdir():
        try:
            resolved = item.resolve(strict=True)
            if root not in resolved.parents or not (resolved.is_dir() or resolved.is_file()):
                continue
            stat = resolved.stat()
            rows.append({'name': item.name, 'path': item.relative_to(root).as_posix(),
                         'directory': resolved.is_dir(), 'size': stat.st_size if resolved.is_file() else None})
        except OSError:
            continue
    rows.sort(key=lambda row: (not row['directory'], row['name'].casefold()))
    if focus:
        index = next((i for i, row in enumerate(rows) if row['path'] == focus), None)
        if index is not None:
            offset = index // 200 * 200
    path = folder.relative_to(root).as_posix()
    return {'name': root.name, 'path': '' if path == '.' else path,
            'rootPath': str(root), 'absolutePath': str(folder),
            'parent': None if folder == root else folder.parent.relative_to(root).as_posix(),
            'entries': rows[offset:offset + 200], 'offset': offset,
            'focusPath': focus, 'more': offset + 200 < len(rows)}


UPLOAD_LIMIT = 50 * 1024 * 1024
TEXT_PREVIEW_LIMIT = 256 * 1024


def upload_project_file(cwd, relative, name, stream, size):
    if not isinstance(name, str) or not name or len(name) > 240 or name in ('.', '..') or any(c in name for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in name) or name.endswith((' ', '.')):
        raise ValueError('文件名无效')
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}:
        raise ValueError('文件名无效')
    if not 0 <= size <= UPLOAD_LIMIT:
        raise ValueError('单个文件不能超过 50 MB')
    root, folder = project_path(cwd, relative)
    if not folder.is_dir():
        raise ValueError('上传目标不是文件夹')
    target = folder / name
    try:
        output = target.open('xb')
    except FileExistsError:
        raise ValueError('同名文件已存在，请修改文件名后上传') from None
    try:
        with output:
            remaining = size
            while remaining:
                block = stream.read(min(256 * 1024, remaining))
                if not block:
                    raise ValueError('上传中断，请重新选择文件')
                output.write(block)
                remaining -= len(block)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {'name': name, 'path': target.relative_to(root).as_posix(), 'size': size}


def preview_project_file(path):
    image_mimes = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
    if path.suffix.lower() in image_mimes:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('图片超过 10 MB，请下载查看')
        return {'kind': 'image', 'mime': image_mimes[path.suffix.lower()], 'name': path.name}
    with path.open('rb') as stream:
        data = stream.read(TEXT_PREVIEW_LIMIT + 1)
    truncated = len(data) > TEXT_PREVIEW_LIMIT
    data = data[:TEXT_PREVIEW_LIMIT]
    try:
        import codecs
        encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        content = codecs.getincrementaldecoder(encoding)().decode(data, final=not truncated)
    except UnicodeError:
        raise ValueError('此文件暂不支持预览，请下载查看') from None
    if any(ord(c) < 32 and c not in '\n\r\t' for c in content):
        raise ValueError('此文件暂不支持预览，请下载查看')
    return {'kind': 'text', 'name': path.name, 'text': content, 'truncated': truncated}
