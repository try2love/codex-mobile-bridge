"""Project-scoped file operations; importing this module never starts a process."""
import base64
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid

MAX_TRANSFER = 20 * 1024 * 1024
MAX_PREVIEW = 512 * 1024
IMAGES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}


class Workspace:
    def __init__(self, root):
        if not root or not Path(root).is_absolute():
            raise ValueError('此聊天尚未关联项目目录')
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('项目目录不可用')

    @staticmethod
    def parts(path):
        if not isinstance(path, str) or len(path) > 4096 or any(ord(c) < 32 for c in path) or '\\' in path or ':' in path:
            raise ValueError('文件路径无效')
        parts = path.split('/') if path else []
        if any(p in ('', '.', '..') for p in parts):
            raise PermissionError('只能访问当前项目内的文件')
        return parts

    @contextmanager
    def directory(self, parts):
        # On POSIX, walk using directory handles so a renamed/replaced symlink
        # cannot redirect an in-flight read or upload outside the project.
        if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in parts:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd); fd = nxt
                yield self.root.joinpath(*parts), fd
            finally:
                os.close(fd)
        else:
            path = self.root
            for part in parts:
                path = path / part
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    raise PermissionError('暂不支持通过符号链接访问文件')
                if not path.is_dir():
                    raise ValueError('目录不存在')
            yield path, None

    @staticmethod
    def open_file(directory, fd, name, flags, mode=0o600):
        if fd is not None:
            return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode, dir_fd=fd)
        path = directory / name
        if path.is_symlink():
            raise PermissionError('暂不支持通过符号链接访问文件')
        return os.open(path, flags | getattr(os, 'O_BINARY', 0), mode)

    def listing(self, path='', hidden=False, search='', offset=0):
        parts = self.parts(path)
        if not isinstance(search, str) or len(search) > 200 or not isinstance(offset, int) or offset < 0:
            raise ValueError('目录查询无效')
        rows = []
        with self.directory(parts) as (directory, fd):
            with os.scandir(fd if fd is not None else directory) as entries:
                for entry in entries:
                    if entry.name.startswith('.bridge-upload-') or (not hidden and entry.name.startswith('.')) or search.casefold() not in entry.name.casefold():
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'blocked'
                        if getattr(entry, 'is_junction', lambda: False)(): kind = 'blocked'
                        rows.append({'name': entry.name, 'path': '/'.join(parts + [entry.name]), 'kind': kind,
                                     'size': info.st_size if kind == 'file' else None, 'modified': info.st_mtime})
                    except OSError:
                        continue
        rows.sort(key=lambda item: (item['kind'] != 'directory', item['name'].casefold(), item['name']))
        return {'project': self.root.name, 'path': path, 'entries': rows[offset:offset + 200],
                'total': len(rows), 'nextOffset': offset + 200 if len(rows) > offset + 200 else None}

    def read(self, path, download=False):
        parts = self.parts(path)
        if not parts: raise ValueError('请选择文件')
        with self.directory(parts[:-1]) as (directory, fd):
            handle = self.open_file(directory, fd, parts[-1], os.O_RDONLY)
            with os.fdopen(handle, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode): raise ValueError('仅支持普通文件')
                if download and info.st_size > MAX_TRANSFER: raise ValueError('文件超过 20 MB 下载限制')
                limit = MAX_TRANSFER if download or Path(path).suffix.lower() in IMAGES else MAX_PREVIEW
                data = stream.read(limit + 1)
        result = {'name': parts[-1], 'path': path, 'size': info.st_size, 'modified': info.st_mtime, 'kind': 'binary'}
        if len(data) > limit:
            if download: raise ValueError('文件超过 20 MB 下载限制')
            return result
        if download:
            return {**result, 'data': base64.b64encode(data).decode()}
        if Path(path).suffix.lower() in IMAGES:
            return {**result, 'kind': 'image', 'mime': IMAGES[Path(path).suffix.lower()], 'data': base64.b64encode(data).decode()}
        try:
            text = data.decode('utf-8-sig')
            if '\x00' not in text: return {**result, 'kind': 'text', 'text': text}
        except UnicodeDecodeError:
            pass
        return result

    def upload(self, path, encoded):
        parts = self.parts(path)
        if not parts or '.git' in parts: raise PermissionError('请选择项目内的普通文件位置')
        try: data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError): raise ValueError('上传内容无效') from None
        if len(data) > MAX_TRANSFER: raise ValueError('每个文件不能超过 20 MB')
        with self.directory(parts[:-1]) as (directory, fd):
            temporary = '.bridge-upload-' + uuid.uuid4().hex
            handle = self.open_file(directory, fd, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                with os.fdopen(handle, 'wb') as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                try:
                    # Publish only a complete file; link fails if the destination exists.
                    if fd is not None: os.link(temporary, parts[-1], src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    else: os.link(directory / temporary, directory / parts[-1])
                except FileExistsError:
                    existing = self.read(path, download=True)
                    if existing['data'] == encoded:
                        return {'name': parts[-1], 'path': path, 'size': len(data), 'existing': True}
                    raise ValueError('同名文件已存在，请修改上传文件名') from None
            finally:
                if fd is not None: os.unlink(temporary, dir_fd=fd)
                else: (directory / temporary).unlink()
        return {'name': parts[-1], 'path': path, 'size': len(data), 'existing': False}



def operate(root, action, params):
    try:
        workspace = Workspace(root)
        if action == 'list': return workspace.listing(**params)
        if action == 'preview': return workspace.read(params['path'])
        if action == 'download': return workspace.read(params['path'], download=True)
        if action == 'upload': return workspace.upload(**params)
        raise ValueError('不支持的文件操作')
    except PermissionError:
        raise
    except OSError as error:
        raise ValueError('文件不可访问，请刷新目录后重试') from error
