"""Project-scoped file operations; importing this module never starts a process."""
import base64
from contextlib import contextmanager
import difflib
import os
from pathlib import Path
import stat
import subprocess
import threading
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



class GitWorkspace:
    """Read-only Git metadata and bounded blob comparisons within a chat project."""
    def __init__(self, workspace):
        self.workspace = workspace
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.environment.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', LC_ALL='C')
        self.command = ['git', '--no-pager', '--no-optional-locks', '--literal-pathspecs',
                        '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-c', 'protocol.allow=never']
        # Status may otherwise run a repository's clean/process filter on dirty files.
        code, keys, truncated = self.run(['config', '--name-only', '--get-regexp', r'^filter\..*\.(clean|process|required)$'])
        if code not in (0, 1) or truncated: raise ValueError('无法读取 Git 配置，请在电脑上检查仓库')
        for key in keys.decode('utf-8', 'replace').splitlines():
            self.command += ['-c', key + ('=false' if key.endswith('.required') else '=')]

    def run(self, args, limit=2 * 1024 * 1024):
        try:
            process = subprocess.Popen(self.command + args, cwd=self.workspace.root, env=self.environment,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise ValueError('此电脑或 SSH 服务器未安装 Git') from None
        expired = threading.Event()
        def timeout():
            expired.set()
            try: process.kill()
            except OSError: pass
        timer = threading.Timer(10, timeout); timer.start()
        try:
            data = process.stdout.read(limit + 1)
            truncated = len(data) > limit
            if truncated: process.kill()
            code = process.wait()
            if expired.is_set(): raise ValueError('Git 读取超时，请在电脑上检查仓库后重试')
            return code, data[:limit], truncated
        finally:
            timer.cancel(); process.stdout.close()
            if process.poll() is None: process.kill(); process.wait()

    def status(self):
        code, top, _ = self.run(['rev-parse', '--show-toplevel'])
        if code: return {'available': False, 'message': '当前项目不是 Git 工作区，或仓库不可访问'}
        repository = Path(os.fsdecode(top.rstrip(b'\n'))).resolve()
        prefix = self.workspace.root.relative_to(repository).as_posix()
        self.prefix = '' if prefix == '.' else prefix + '/'
        code, raw, truncated = self.run(['status', '--porcelain=v2', '--branch', '-z', '--untracked-files=all',
                                          '--renames', '--ignore-submodules=dirty', '--', '.'])
        if code or truncated: raise ValueError('Git 变更列表过大或不可读取，请在电脑上检查仓库')
        headers, entries = {}, []
        records = iter(raw.decode('utf-8', 'surrogateescape').split('\0'))
        for record in records:
            if not record: continue
            if record.startswith('# '):
                key, _, value = record[2:].partition(' '); headers[key] = value; continue
            kind = record[0]; old = None; submodule = False
            if kind == '?': path, xy = record[2:], '??'
            elif kind in ('1', '2', 'u'):
                parts = record.split(' ', {'1': 8, '2': 9, 'u': 10}[kind]); path, xy = parts[-1], parts[1]
                submodule = parts[2].startswith('S')
                if kind == '2': old = next(records)
            else: continue
            if not path.startswith(self.prefix): continue
            relative = path[len(self.prefix):]
            entries.append({'path': relative, 'oldPath': old[len(self.prefix):] if old and old.startswith(self.prefix) else None,
                            'index': xy[0], 'worktree': xy[1], 'conflict': kind == 'u', 'untracked': kind == '?', 'submodule': submodule})
        entries.sort(key=lambda entry: entry['path'].casefold())
        counts = headers.get('branch.ab', '').split()
        return {'available': True, 'project': self.workspace.root.name, 'scope': prefix, 'branch': headers.get('branch.head', ''),
                'commit': headers.get('branch.oid', ''), 'upstream': headers.get('branch.upstream'),
                'ahead': int(counts[0]) if counts else None, 'behind': -int(counts[1]) if counts else None, 'entries': entries}

    def blob(self, spec):
        code, data, truncated = self.run(['cat-file', 'blob', spec], MAX_PREVIEW)
        if truncated: return None
        if code: raise ValueError('文件状态已变化或 Git 对象不可用，请刷新 Git 面板')
        return data

    def working(self, path):
        parts = self.workspace.parts(path)
        with self.workspace.directory(parts[:-1]) as (directory, fd):
            handle = self.workspace.open_file(directory, fd, parts[-1], os.O_RDONLY)
            with os.fdopen(handle, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError('此文件类型暂不支持差异预览')
                data = stream.read(MAX_PREVIEW + 1)
                return data if len(data) <= MAX_PREVIEW else None

    def diff(self, path, section):
        self.workspace.parts(path)
        if section not in ('staged', 'unstaged', 'untracked', 'conflict'): raise ValueError('Git 差异类型无效')
        state = self.status()
        entry = next((item for item in state.get('entries', []) if item['path'] == path), None)
        if not entry: raise ValueError('文件状态已变化，请刷新 Git 面板')
        valid = ('conflict' if entry['conflict'] else 'untracked' if entry['untracked'] else None)
        if (valid and section != valid) or (not valid and (section not in ('staged', 'unstaged') or entry['index' if section == 'staged' else 'worktree'] == '.')):
            raise ValueError('文件状态已变化，请刷新 Git 面板')
        result = {'path': path, 'section': section, 'oldPath': entry['oldPath'], 'kind': 'diff'}
        if entry['submodule']: return {**result, 'kind': 'submodule'}
        if section in ('untracked', 'conflict'):
            before, after = b'', self.working(path)
        elif section == 'staged':
            old = entry['oldPath'] or path
            before = b'' if entry['index'] == 'A' or (entry['index'] == 'R' and not entry['oldPath']) else self.blob('HEAD:' + self.prefix + old)
            after = b'' if entry['index'] == 'D' else self.blob(':' + self.prefix + path)
        else:
            before = b'' if entry['worktree'] == 'A' else self.blob(':' + self.prefix + path)
            after = b'' if entry['worktree'] == 'D' else self.working(path)
        if before is None or after is None: return {**result, 'kind': 'large'}
        try:
            if b'\0' in before or b'\0' in after: return {**result, 'kind': 'binary'}
            old, new = before.decode('utf-8'), after.decode('utf-8')
        except UnicodeDecodeError: return {**result, 'kind': 'binary'}
        if section == 'conflict': return {**result, 'kind': 'conflict', 'text': new}
        old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
        if len(old_lines) + len(new_lines) > 8000: return {**result, 'kind': 'large'}
        lines = difflib.unified_diff(old_lines, new_lines, fromfile=entry['oldPath'] or path, tofile=path, n=3)
        text = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
        return {**result, 'text': text, 'metadataOnly': not text}


def operate(root, action, params):
    try:
        workspace = Workspace(root)
        if action == 'git-status': return GitWorkspace(workspace).status()
        if action == 'git-diff': return GitWorkspace(workspace).diff(**params)
        if action == 'list': return workspace.listing(**params)
        if action == 'preview': return workspace.read(params['path'])
        if action == 'download': return workspace.read(params['path'], download=True)
        if action == 'upload': return workspace.upload(**params)
        raise ValueError('不支持的文件操作')
    except PermissionError:
        raise
    except OSError as error:
        raise ValueError('文件不可访问，请刷新目录后重试') from error
