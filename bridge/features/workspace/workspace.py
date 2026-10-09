"""Project-scoped file operations; importing this module never starts a process."""
import base64
from contextlib import contextmanager
import difflib
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import threading
import uuid

MAX_TRANSFER = 20 * 1024 * 1024
MAX_PREVIEW = 512 * 1024
IMAGES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}


def download_range(size, etag, range_header='', if_range=''):
    """Only one byte range is supported; unsupported syntax is ignored."""
    result = {'status': 200, 'offset': 0, 'length': size, 'size': size, 'etag': etag}
    # We do not advertise Last-Modified: only an exact strong validator resumes.
    if if_range and if_range != etag:
        return result
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
    if not match or not any(match.groups()):
        return result
    first, last = match.groups()
    # Limit integer parsing independently of the host Python version.
    if len(first) > 20 or len(last) > 20:
        return {**result, 'status': 416, 'length': 0, 'contentRange': 'bytes */' + str(size)}
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        start, end = max(0, size - int(last)), size - 1
    if start >= size or end < start:
        return {**result, 'status': 416, 'length': 0, 'contentRange': 'bytes */' + str(size)}
    return {**result, 'status': 206, 'offset': start, 'length': end - start + 1,
            'contentRange': 'bytes %d-%d/%d' % (start, end, size)}


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
                if not path.exists():
                    raise FileNotFoundError('目录不存在')
                if not path.is_dir():
                    raise NotADirectoryError('路径不是目录')
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

    @contextmanager
    def download(self, path, range_header='', if_range='', limit=MAX_TRANSFER):
        try:
            with self._download(path, range_header, if_range, limit) as value:
                yield value
        except PermissionError:
            raise
        except OSError as error:
            raise ValueError('文件不可访问，请刷新目录后重试') from error

    @contextmanager
    def _download(self, path, range_header, if_range, limit):
        parts = self.parts(path)
        if not parts: raise ValueError('请选择文件')
        # A private disk snapshot binds the validator to exactly the bytes sent,
        # even if an editor replaces or writes the source during the response.
        with tempfile.TemporaryFile(prefix='bridge-download-') as snapshot:
            with self.directory(parts[:-1]) as (directory, fd):
                handle = self.open_file(directory, fd, parts[-1], os.O_RDONLY)
                with os.fdopen(handle, 'rb') as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode): raise ValueError('仅支持普通文件')
                    if before.st_size > limit: raise ValueError('文件超过 %d MB 下载限制' % (limit // 1024 // 1024))
                    def copy_range(selection):
                        digest, size = hashlib.sha256(), 0
                        stream.seek(0); snapshot.seek(0); snapshot.truncate()
                        while True:
                            chunk = stream.read(65536)
                            if not chunk: break
                            end = size + len(chunk)
                            if end > limit: raise ValueError('文件超过 %d MB 下载限制' % (limit // 1024 // 1024))
                            digest.update(chunk)
                            left = max(size, selection['offset'])
                            right = min(end, selection['offset'] + selection['length'])
                            if right > left: snapshot.write(chunk[left - size:right - size])
                            size = end
                        after = os.fstat(stream.fileno())
                        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size):
                            raise ValueError('文件正在变化，请稍后重新下载')
                        return '"sha256-' + digest.hexdigest() + '"'
                    # Hash all bytes, but normally retain only the requested
                    # chunk. No file-sized allocation or repeated full snapshot.
                    selection = download_range(before.st_size, '', range_header)
                    etag = copy_range(selection)
                    result = download_range(before.st_size, etag, range_header, if_range)
                    if result['length'] != selection['length'] or result['offset'] != selection['offset']:
                        result['etag'] = copy_range(result)
            result['name'] = parts[-1]
            snapshot.seek(0)
            yield result, snapshot

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
    """Project Git inspection and explicitly requested local Git operations."""
    def __init__(self, workspace):
        self.workspace = workspace
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.environment.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', LC_ALL='C')
        self.command = ['git', '--no-pager', '--no-optional-locks', '--literal-pathspecs',
                        '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-c', 'protocol.allow=never']
        self.write_command = ['git', '--no-pager', '--literal-pathspecs', '-c', 'core.fsmonitor=false',
                              '-c', 'protocol.allow=never', '-c', 'gc.auto=0']
        # Status may otherwise run a repository's clean/process filter on dirty files.
        code, keys, truncated = self.run(['config', '--name-only', '--get-regexp', r'^filter\..*\.(clean|process|required)$'])
        if code not in (0, 1) or truncated: raise ValueError('无法读取 Git 配置，请在电脑上检查仓库')
        for key in keys.decode('utf-8', 'replace').splitlines():
            self.command += ['-c', key + ('=false' if key.endswith('.required') else '=')]

    def run(self, args, limit=2 * 1024 * 1024, write=False, message=None):
        source = tempfile.TemporaryFile(dir=self.gitdir) if message is not None else None
        if source: source.write(message.encode('utf-8')); source.seek(0)
        try:
            process = subprocess.Popen((self.write_command if write else self.command) + args, cwd=self.workspace.root, env=self.environment,
                                       stdin=source if source else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise ValueError('此电脑或 SSH 服务器未安装 Git') from None
        finally:
            if source: source.close()
        expired = threading.Event()
        def timeout():
            expired.set()
            try: process.kill()
            except OSError: pass
        timer = threading.Timer(30 if write else 10, timeout); timer.start()
        try:
            data = process.stdout.read(limit + 1)
            truncated = len(data) > limit
            if truncated: process.kill()
            code = process.wait()
            if expired.is_set(): raise ValueError('Git 操作结果未确认，请刷新状态和历史后再决定是否重试' if write else 'Git 读取超时，请在电脑上检查仓库后重试')
            return code, data[:limit], truncated
        finally:
            timer.cancel(); process.stdout.close()
            if process.poll() is None: process.kill(); process.wait()

    def status(self):
        code, top, _ = self.run(['rev-parse', '--show-toplevel'])
        if code: return {'available': False, 'message': '当前项目不是 Git 工作区，或仓库不可访问'}
        repository = Path(os.fsdecode(top.rstrip(b'\n'))).resolve()
        code, gitdir, _ = self.run(['rev-parse', '--absolute-git-dir'])
        if code: raise ValueError('无法读取 Git 仓库位置')
        self.gitdir = Path(os.fsdecode(gitdir.rstrip(b'\n')))
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
        fingerprint = hashlib.sha256(raw)
        for name in ('index', 'HEAD', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD'):
            try: fingerprint.update((self.gitdir / name).read_bytes())
            except FileNotFoundError: pass
        for entry in entries:
            try:
                info = (self.workspace.root / entry['path']).lstat()
                fingerprint.update(str((entry['path'], info.st_mtime_ns, info.st_ctime_ns, info.st_size)).encode('utf-8', 'replace'))
            except FileNotFoundError: pass
        operation = 'merge' if (self.gitdir / 'MERGE_HEAD').exists() else 'other' if any((self.gitdir / name).exists() for name in ('rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'REVERT_HEAD')) else None
        return {'available': True, 'project': self.workspace.root.name, 'repository': repository.name, 'scope': prefix, 'branch': headers.get('branch.head', ''),
                'version': fingerprint.hexdigest(), 'operation': operation,
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
        return self.text_diff(before, after, result)

    @staticmethod
    def text_diff(before, after, result):
        if before is None or after is None: return {**result, 'kind': 'large'}
        try:
            if b'\0' in before or b'\0' in after: return {**result, 'kind': 'binary'}
            old, new = before.decode('utf-8'), after.decode('utf-8')
        except UnicodeDecodeError: return {**result, 'kind': 'binary'}
        if result.get('section') == 'conflict': return {**result, 'kind': 'conflict', 'text': new}
        old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
        if len(old_lines) + len(new_lines) > 8000: return {**result, 'kind': 'large'}
        lines = difflib.unified_diff(old_lines, new_lines, fromfile=result.get('oldPath') or result['path'], tofile=result['path'], n=3)
        text = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
        return {**result, 'text': text, 'metadataOnly': not text}


    def require_repository(self):
        state = self.status()
        if not state['available']: raise ValueError(state['message'])
        return state

    @staticmethod
    def revision(value):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
            raise ValueError('提交标识无效，请刷新历史')
        return value

    def history(self, limit=40, ref='all'):
        state = self.require_repository()
        if not isinstance(limit, int) or not 1 <= limit <= 1000 or ref not in ('all', 'current'):
            raise ValueError('历史查询无效')
        revisions = ['--all'] if ref == 'all' else []
        if state['commit'] != '(initial)': revisions.append('HEAD')
        elif ref == 'current': return {'commits': [], 'hasMore': False}
        code, raw, truncated = self.run(['log', '--topo-order', '-n', str(limit + 1),
            '--format=%H%x00%P%x00%an%x00%aI%x00%s%x00%D', '-z', *revisions, '--'])
        if code or truncated: raise ValueError('历史记录不可读取或过大，请在电脑上检查仓库')
        fields = raw.decode('utf-8', 'replace').split('\0')
        if fields[-1] == '': fields.pop()
        commits = []
        for start in range(0, len(fields), 6):
            oid, parents, author, date, subject, refs = fields[start:start + 6]
            commits.append({'id': oid, 'parents': parents.split(), 'author': author, 'date': date,
                            'subject': subject, 'refs': refs})
        return {'commits': commits[:limit], 'hasMore': len(commits) > limit, 'scope': state['scope']}

    def commit_detail(self, revision):
        self.require_repository(); self.revision(revision)
        code, raw, truncated = self.run(['show', '-s', '--format=%H%x00%P%x00%an%x00%aI%x00%B', revision, '--'], MAX_PREVIEW)
        if code or truncated: raise ValueError('提交详情不可读取')
        oid, parents, author, date, message = raw.decode('utf-8', 'replace').split('\0', 4)
        parents = parents.split()
        comparison = [parents[0], oid] if parents else ['--root', oid]
        code, raw, truncated = self.run(['diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '-z', '-r', '-M', *comparison, '--', '.'])
        if code or truncated: raise ValueError('此提交的文件列表过大或不可读取')
        tokens = iter(raw.decode('utf-8', 'replace').split('\0')); entries = []
        for header in tokens:
            if not header: continue
            old_mode, mode, old_id, new_id, change = header[1:].split(' ')
            path = next(tokens); old = None
            if change.startswith(('R', 'C')): old, path = path, next(tokens)
            if not path.startswith(self.prefix): continue
            entries.append({'path': path[len(self.prefix):], 'oldPath': old[len(self.prefix):] if old and old.startswith(self.prefix) else None,
                            'change': change[0], 'oldId': old_id if not old or old.startswith(self.prefix) else '0' * len(old_id),
                            'newId': new_id, 'submodule': old_mode == '160000' or mode == '160000'})
        return {'id': oid, 'parents': parents, 'author': author, 'date': date, 'message': message.rstrip(), 'entries': entries}

    def historical_diff(self, revision, path):
        self.workspace.parts(path)
        detail = self.commit_detail(revision)
        entry = next((entry for entry in detail['entries'] if entry['path'] == path), None)
        if not entry: raise ValueError('文件不属于此提交的项目变更')
        result = {'path': path, 'oldPath': entry['oldPath'], 'kind': 'diff'}
        if entry['submodule']: return {**result, 'kind': 'submodule'}
        before = b'' if set(entry['oldId']) == {'0'} else self.blob(entry['oldId'])
        after = b'' if set(entry['newId']) == {'0'} else self.blob(entry['newId'])
        return self.text_diff(before, after, result)

    def branches(self):
        state = self.require_repository()
        code, raw, truncated = self.run(['for-each-ref', '--sort=refname', '--format=%(refname:short)%00%(objectname)%00%(upstream:short)', 'refs/heads/'])
        if code or truncated: raise ValueError('无法读取分支列表')
        entries = []
        for line in raw.decode('utf-8', 'replace').splitlines():
            name, oid, upstream = line.split('\0')
            entries.append({'name': name, 'id': oid, 'upstream': upstream, 'current': name == state['branch']})
        return {'branches': entries}

    @contextmanager
    def mutation_lock(self):
        # Shared across HTTP workers and separate SSH Python processes.
        with (self.gitdir / 'codex-mobile-workbench.lock').open('a+b') as lock:
            if os.name == 'nt':
                import msvcrt
                lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
                try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError: raise ValueError('另一个 Git 操作正在执行，请稍后刷新') from None
            else:
                import fcntl
                try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError: raise ValueError('另一个 Git 操作正在执行，请稍后刷新') from None
            try: yield
            finally:
                if os.name == 'nt': lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def mutate(self, action, version, path='', branch='', message=''):
        allowed = ('stage', 'unstage', 'stage-all', 'unstage-all', 'commit', 'create-branch', 'switch-branch', 'merge', 'abort-merge')
        if action not in allowed: raise ValueError('不支持此 Git 操作')
        self.require_repository()
        with self.mutation_lock():
            state = self.require_repository()
            if not isinstance(version, str) or version != state['version']:
                raise ValueError('仓库已发生变化，请刷新并核对后重新操作')
            if state['operation'] == 'other': raise ValueError('仓库正在变基或拣选，请先在电脑上完成该操作')
            if action in ('commit', 'create-branch', 'switch-branch', 'merge', 'abort-merge') and state['scope'] != '.':
                raise ValueError('此操作影响整个仓库，请在仓库根目录对应的聊天中操作')
            if action in ('stage', 'unstage'):
                parts = self.workspace.parts(path)
                if not parts or '.git' in parts: raise ValueError('请选择项目中的变更文件')
                entry = next((e for e in state['entries'] if e['path'] == path), None)
                if not entry or entry['submodule']: raise ValueError('文件状态已变化或为子模块，请刷新后在电脑上处理')
                # Existing parent components must not redirect staging outside the project.
                if action == 'stage':
                    try:
                        with self.workspace.directory(parts[:-1]): pass
                    except FileNotFoundError:
                        if entry['worktree'] != 'D': raise
                paths = [path]
                if action == 'unstage' and entry['oldPath']: paths.append(entry['oldPath'])
            else: paths = ['.']
            if action.startswith('stage'):
                command = ['add', '-A', '--', *paths]
            elif action.startswith('unstage'):
                # In an unborn repository, remove only index entries; preserve even
                # worktree files edited again after their first staging.
                command = ['restore', '--staged', '--source=HEAD', '--', *paths] if state['commit'] != '(initial)' else ['rm', '-r', '-f', '--cached', '--ignore-unmatch', '--', *paths]
            elif action == 'commit':
                if state['branch'] == '(detached)': raise ValueError('当前为分离 HEAD，请先创建分支再提交')
                if any(e['conflict'] for e in state['entries']): raise ValueError('请先解决冲突并暂存文件')
                if state['operation'] != 'merge' and not any(not e['untracked'] and e['index'] != '.' for e in state['entries']): raise ValueError('请先暂存需要提交的文件')
                if not isinstance(message, str) or not message.strip() or len(message.encode('utf-8')) > 16000 or '\0' in message:
                    raise ValueError('请填写提交说明（最多 16 KB）')
                command = ['commit', '--file=-']
            elif action == 'abort-merge':
                if state['operation'] != 'merge': raise ValueError('当前没有可中止的合并')
                command = ['merge', '--abort']
            else:
                if state['operation']: raise ValueError('请先完成或中止当前合并')
                if state['entries']: raise ValueError('请先提交或在电脑上保存未提交的改动，再操作分支')
                if not isinstance(branch, str) or not branch or len(branch) > 200 or branch.startswith('-'):
                    raise ValueError('分支名称无效')
                code, normalized, _ = self.run(['check-ref-format', '--branch', branch])
                if code or normalized.decode('utf-8', 'replace').strip() != branch: raise ValueError('分支名称无效')
                names = {entry['name'] for entry in self.branches()['branches']}
                if action == 'create-branch':
                    if branch in names: raise ValueError('同名分支已存在')
                    command = ['switch', '-c', branch]
                else:
                    if branch not in names or branch == state['branch']: raise ValueError('请选择另一个已存在的本地分支')
                    command = ['switch', '--no-guess', branch] if action == 'switch-branch' else ['merge', '--no-edit', '--no-stat', '--no-autostash', 'refs/heads/' + branch]
            code, _, truncated = self.run(command, write=True, message=message if action == 'commit' else None)
            result = self.status()
            if code or truncated:
                if action == 'merge' and result.get('operation') == 'merge':
                    return {'outcome': 'conflict' if any(e['conflict'] for e in result['entries']) else 'merge-pending',
                            'message': '合并尚未完成，请检查变更；可解决冲突后提交，或中止本次合并。', 'state': result}
                raise ValueError('Git 操作未完成或结果未确认。请刷新状态和历史；如无变化，请在电脑上检查 Git 身份、签名、hooks 或仓库锁后重试。')
            labels = {'stage': '已暂存文件', 'unstage': '已取消暂存', 'stage-all': '已暂存项目变更', 'unstage-all': '已取消项目暂存',
                      'commit': '已提交到本地分支', 'create-branch': '已创建并切换分支', 'switch-branch': '已切换分支', 'merge': '分支已合并', 'abort-merge': '已中止合并'}
            return {'outcome': 'success', 'message': labels[action], 'state': result}


def operate(root, action, params):
    try:
        workspace = Workspace(root)
        if action == 'git-status': return GitWorkspace(workspace).status()
        if action == 'git-diff': return GitWorkspace(workspace).diff(**params)
        if action == 'git-history': return GitWorkspace(workspace).history(**params)
        if action == 'git-commit': return GitWorkspace(workspace).commit_detail(**params)
        if action == 'git-history-diff': return GitWorkspace(workspace).historical_diff(**params)
        if action == 'git-branches': return GitWorkspace(workspace).branches()
        if action == 'git-action': return GitWorkspace(workspace).mutate(**params)
        if action == 'list': return workspace.listing(**params)
        if action == 'preview': return workspace.read(params['path'])
        if action == 'download': return workspace.read(params['path'], download=True)
        if action == 'download-stream': return workspace.download(**params)
        if action == 'upload': return workspace.upload(**params)
        raise ValueError('不支持的文件操作')
    except PermissionError:
        raise
    except OSError as error:
        raise ValueError('文件不可访问，请刷新目录后重试') from error
