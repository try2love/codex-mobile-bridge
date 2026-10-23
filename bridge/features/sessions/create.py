"""Create or fork a persisted thread, then hand execution to the desktop App.

The short-lived app-server never receives turn/start. It is shut down before
returning, so the desktop can become the sole execution owner.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlencode


class CreationError(RuntimeError):
    pass


class CreationUnavailable(CreationError):
    """The runtime never started, so no thread mutation could have happened."""


class ForkUnavailable(CreationUnavailable):
    """The capability check failed before any thread could be created."""


def _runtime_operation(executable, codex_home, cwd, operation):
    if not executable:
        raise CreationUnavailable('找不到 Codex 运行时，请在电脑启动器的运行配置中指定路径')
    if not Path(cwd).is_dir():
        raise CreationUnavailable('项目目录不存在，请先在电脑 App 中检查项目')
    env = dict(os.environ, CODEX_HOME=str(codex_home))
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        process = subprocess.Popen([str(executable), 'app-server', '--listen', 'stdio://'],
                                   cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding='utf-8', **kwargs)
    except OSError as exc:
        raise CreationUnavailable('无法启动 Codex 运行时，请检查运行配置中的程序路径、项目目录和访问权限后重试') from exc
    messages = queue.Queue()

    def read():
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            messages.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    counter = 0

    def request(method, params):
        nonlocal counter
        counter += 1
        process.stdin.write(json.dumps({'id': counter, 'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        deadline = time.monotonic() + 25
        while True:
            try:
                result = messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise CreationError('创建聊天超时，请先检查桌面聊天列表，不要重复创建') from exc
            if result is None:
                raise CreationError('创建聊天的运行时已断开，请先检查桌面聊天列表')
            if result.get('id') != counter:
                if time.monotonic() >= deadline:
                    raise CreationError('创建聊天超时，请先检查桌面聊天列表')
                continue
            if 'error' in result:
                raise CreationError(result['error'].get('message', '创建聊天失败'))
            return result['result']

    try:
        request('initialize', {'clientInfo': {'name': 'codex_mobile_bridge', 'version': '0.2'},
                               'capabilities': {'experimentalApi': True}})
        process.stdin.write('{"method":"initialized"}\n')
        process.stdin.flush()
        result = operation(request)
    finally:
        # EOF flushes pending writes and releases the thread before App takeover.
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise CreationError('创建运行时未正常退出，请先检查桌面聊天列表')
        finally:
            reader.join(timeout=1)
            process.stdout.close()
    if process.returncode:
        raise CreationError('创建运行时退出异常，请先检查桌面聊天列表')
    return result


def independent_directory(data_dir, request_id):
    """A durable per-chat workspace, without registering a desktop project."""
    uuid.UUID(request_id)
    root = Path(data_dir) / 'independent-chats'
    target = root / request_id
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink(): raise CreationUnavailable('独立聊天目录不可用')
        target.mkdir(mode=0o700, exist_ok=True)
        if target.is_symlink() or not target.is_dir(): raise CreationUnavailable('独立聊天目录不可用')
        return str(target.resolve())
    except OSError as exc:
        raise CreationUnavailable('独立聊天目录不可用') from exc


def project_directory(value, create=False, expected=None):
    """Explicit project selection only; return the canonical directory identity."""
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('请输入完整的项目目录')
    if type(create) is not bool:
        raise ValueError('创建目录选项无效')
    value = value.strip()
    # UNC/device paths can initiate remote authentication on Windows. Projects
    # on SSH/WSL must be resolved on that registered host instead.
    if os.name == 'nt' and (value.startswith(('\\\\', '//')) or ':' in value[2:]):
        raise ValueError('请使用此电脑的本地绝对路径')
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('请使用绝对路径或 ~/ 开头的目录，不含 ..')
    try:
        if create and not path.exists():
            parent = path.parent.resolve(strict=True)
            if not parent.is_dir(): raise ValueError('父目录不存在')
            # Deliberately create only the requested final directory, never a
            # guessed hierarchy, and never overwrite existing contents.
            try: (parent/path.name).mkdir(mode=0o700)
            except FileExistsError: pass
        canonical = path.resolve(strict=True)
        if not canonical.is_dir(): raise ValueError('所选路径不是目录')
        if not os.access(canonical, os.R_OK | os.W_OK | os.X_OK):
            raise ValueError('项目目录不可读写')
        st = canonical.stat()
        identity = {'cwd': str(canonical), 'device': st.st_dev, 'inode': st.st_ino}
        if expected is not None and identity != expected:
            raise ValueError('项目目录已变化，请重新选择目录')
        return identity
    except OSError as exc:
        raise ValueError('项目目录不存在或不可访问；新目录请勾选创建，并确保父目录存在') from exc


def project_directory_listing(value=None, hidden=False, name=None, identity=None):
    """Explicit filesystem picker; only directories, never file contents."""
    if type(hidden) is not bool: raise ValueError('目录显示选项无效')
    if value is None: value = str(Path.home())
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('目录路径无效')
    if os.name == 'nt' and (value.startswith(('\\\\', '//')) or ':' in value[2:]):
        raise ValueError('请使用此电脑的本地目录')
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() or '..' in path.parts: raise ValueError('目录路径无效')
        path = path.resolve(strict=True)
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK): raise ValueError('目录不可浏览')
        st = path.stat()
        current = {'cwd': str(path), 'device': st.st_dev, 'inode': st.st_ino}
        if name is not None:
            if (not isinstance(name, str) or not name.strip() or len(name) > 200 or name in ('.', '..')
                    or any(c in name for c in '/\\:') or any(ord(c) < 32 for c in name)):
                raise ValueError('请输入有效的文件夹名称，不含路径分隔符')
            if identity != current: raise ValueError('当前目录已变化，请刷新后新建')
            path = Path(project_directory(str(path/name.strip()), create=True)['cwd'])
            st = path.stat(); current = {'cwd': str(path), 'device': st.st_dev, 'inode': st.st_ino}
        directories = []
        for child in path.iterdir():
            if not hidden and child.name.startswith('.'): continue
            try:
                if child.is_dir(): directories.append({'name': child.name, 'path': str(child)})
            except OSError: continue
        directories.sort(key=lambda item: (item['name'].casefold(), item['name']))
        roots = [{'name': '/', 'path': '/'}]
        if os.name == 'nt':
            import ctypes
            kernel = ctypes.windll.kernel32
            roots = [{'name': chr(n)+':', 'path': chr(n)+':\\'} for n in range(65,91)
                     if kernel.GetDriveTypeW(chr(n)+':\\') in (2,3)]
        return {'path': str(path), 'parent': str(path.parent) if path.parent != path else None,
                'home': str(Path.home()), 'roots': roots, 'identity': current, 'directories': directories}
    except (OSError, RuntimeError) as exc:
        raise ValueError('无法浏览此目录，请检查访问权限') from exc


def create_empty(executable, codex_home, cwd, title):
    def create(request):
        result = request('thread/start', {'cwd': cwd, 'ephemeral': False})
        thread_id = result['thread']['id']
        uuid.UUID(thread_id)
        request('thread/name/set', {'threadId': thread_id, 'name': title})
        request('thread/read', {'threadId': thread_id, 'includeTurns': True})
        return thread_id
    return _runtime_operation(executable, codex_home, cwd, create)


def fork_copy(executable, codex_home, cwd, source_id, turn_id, title, settings):
    if not executable:
        raise ForkUnavailable('找不到 Codex 运行时，请在电脑启动器的运行配置中指定路径')
    # Check the bundled runtime, not a guessed version. Older servers can silently
    # ignore unknown fields; that must never copy later turns or start a goal.
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    with tempfile.TemporaryDirectory(prefix='fork-schema-') as folder:
        try:
            result = subprocess.run([str(executable), 'app-server', 'generate-json-schema', '--experimental', '--out', folder],
                                    capture_output=True, timeout=30, **kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ForkUnavailable('无法检查 Codex 运行时，请检查运行配置中的程序路径后重试') from exc
        paths = list(Path(folder).rglob('ThreadForkParams.json'))
        if result.returncode or not paths:
            raise ForkUnavailable('此 Codex 版本暂不支持安全分支，请更新电脑 Codex App')
        properties = json.loads(paths[0].read_text(encoding='utf-8')).get('properties', {})
        if not {'lastTurnId', 'deferGoalContinuation'} <= properties.keys():
            raise ForkUnavailable('此 Codex 版本暂不支持安全分支，请更新电脑 Codex App')
    def fork(request):
        result = request('thread/fork', {'threadId': source_id, 'lastTurnId': turn_id,
                                        'deferGoalContinuation': True, 'ephemeral': False,
                                        'cwd': cwd, **settings})
        thread = result['thread']
        uuid.UUID(thread['id'])
        if thread['id'] == source_id:
            raise CreationError('分支结果无效，请检查桌面聊天列表')
        turns = thread.get('turns', [])
        if not turns or turns[-1].get('id') != turn_id:
            raise CreationError('分支位置未确认，请检查桌面聊天列表，不要重复创建')
        if result.get('modelProvider') != settings['modelProvider']:
            raise CreationError('分支模型提供商不一致，请检查桌面聊天列表')
        request('thread/name/set', {'threadId': thread['id'], 'name': title})
        return thread['id']
    return _runtime_operation(executable, codex_home, cwd, fork)


def open_in_desktop(thread_id, host):
    uuid.UUID(thread_id)
    url = 'codex://threads/' + thread_id
    if host != 'local':
        url += '?' + urlencode({'hostId': host})
    if sys.platform == 'darwin':
        subprocess.run(['open', url], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform == 'linux':
        try:
            subprocess.run(['xdg-open', url], check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CreationError('无法打开桌面聊天，请在桌面登录会话中运行网关，并检查 xdg-open 和 codex:// 协议关联') from exc
    elif os.name == 'nt':
        os.startfile(url)
    else:
        raise CreationError('请在支持 Codex App 的 Mac 或 Windows 电脑运行网关')


def rename_thread(executable, codex_home, thread_id, title):
    uuid.UUID(thread_id)
    return _runtime_operation(executable, codex_home, str(codex_home),
                              lambda request: request('thread/name/set', {'threadId': thread_id, 'name': title}))
