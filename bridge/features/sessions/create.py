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
    """No thread mutation could have happened."""


class ForkUnavailable(CreationUnavailable):
    """The capability check failed before any thread could be created."""


class DeleteRejected(CreationError):
    """The native server explicitly rejected this delete, before mutation."""


def runtime_environment(codex_home, *, isolated_home=False):
    # Only a separately registered home has an enforced database boundary.
    env = dict(os.environ, CODEX_HOME=str(codex_home))
    if isolated_home:
        env.pop('CODEX_SQLITE_HOME', None)
    return env


def _runtime_operation(executable, codex_home, cwd, operation, *, failure_message=None, isolated_home=False):
    if not executable:
        raise CreationUnavailable('找不到 Codex 运行时，请在电脑启动器的运行配置中指定路径')
    if not Path(cwd).is_dir():
        raise CreationUnavailable('项目目录不存在，请先在电脑 App 中检查项目')
    env = runtime_environment(codex_home, isolated_home=isolated_home)
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
                raise CreationError(failure_message or '创建聊天超时，请先检查桌面聊天列表，不要重复创建') from exc
            if result is None:
                raise CreationError(failure_message or '创建聊天的运行时已断开，请先检查桌面聊天列表')
            if result.get('id') != counter:
                if time.monotonic() >= deadline:
                    raise CreationError(failure_message or '创建聊天超时，请先检查桌面聊天列表')
                continue
            if 'error' in result:
                if method in ('thread/archive', 'thread/unarchive'):
                    raise ArchiveRejected(result['error'])
                if method == 'thread/delete' and result['error'].get('code') == -32601:
                    raise DeleteRejected('当前原生运行时不支持永久删除，请在原客户端操作')
                raise CreationError(result['error'].get('message', failure_message or '创建聊天失败'))
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
            raise CreationError(failure_message or '创建运行时未正常退出，请先检查桌面聊天列表')
        finally:
            reader.join(timeout=1)
            process.stdout.close()
    if process.returncode:
        raise CreationError(failure_message or '创建运行时退出异常，请先检查桌面聊天列表')
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


def project_trust(executable, codex_home, identity, confirmed=False):
    """Trust only an explicitly selected, unchanged directory via native config RPC."""
    if type(confirmed) is not bool:
        raise ValueError('目录信任确认无效')
    cwd = project_directory(identity['cwd'], expected=identity)['cwd']
    def run(request):
        def read():
            return request('config/read', {'cwd': cwd, 'includeLayers': True})
        def trusted(value):
            return value.get('config', {}).get('projects', {}).get(cwd, {}).get('trust_level') == 'trusted'
        value = read()
        if confirmed and not trusted(value):
            layers = [layer for layer in value.get('layers') or []
                      if layer.get('name', {}).get('type') == 'user'
                      and not layer['name'].get('profile') and not layer.get('disabledReason')]
            if len(layers) != 1 or not layers[0].get('version'):
                raise CreationUnavailable('无法确认 Codex 用户配置版本，请在电脑 App 中信任此项目')
            project_directory(cwd, expected=identity)
            result = request('config/batchWrite', {'edits': [{
                'keyPath': 'projects.' + json.dumps(cwd, ensure_ascii=False) + '.trust_level',
                'value': 'trusted', 'mergeStrategy': 'replace'}],
                'filePath': None, 'expectedVersion': layers[0]['version'], 'reloadUserConfig': True})
            if result.get('status') != 'ok' or not trusted(read()):
                raise CreationError('目录信任结果尚未确认，请检查 Codex App 配置；不会自动重复提交')
            value = {'config': {'projects': {cwd: {'trust_level': 'trusted'}}}}
        return {'trusted': trusted(value), 'path': cwd}
    # Do not launch the temporary reader in an as-yet untrusted project.
    return _runtime_operation(executable, codex_home, str(codex_home), run,
                              failure_message='目录信任结果尚未确认，请检查 Codex App 配置；不会自动重复提交')


def apply_desktop_config():
    _open_desktop_url('codex://codex-app/apply-config')


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
            child = path/name.strip()
            # A lost mkdir response must never silently select an existing
            # folder or follow a replacement symlink on a later request.
            try:
                child.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise ValueError('文件夹已存在，请刷新后选择') from exc
            path = Path(project_directory(str(child))['cwd'])
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


def _native_project_support(executable, required):
    """Inspect this runtime before a new-directory operation can mutate it."""
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        with tempfile.TemporaryDirectory(prefix='project-schema-') as folder:
            result = subprocess.run([str(executable), 'app-server', 'generate-json-schema', '--experimental', '--out', folder],
                                    capture_output=True, timeout=30, **kwargs)
            def schema(name):
                paths = list(Path(folder).rglob(name + '.json'))
                return json.loads(paths[0].read_text(encoding='utf-8')) if paths else {}
            supported = (result.returncode == 0 and
                         'projectId' in schema('ThreadStartParams').get('properties', {}) and
                         bool(schema('ProjectListResponse').get('properties', {}).get('data')))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        supported = False
    if required and not supported:
        raise CreationUnavailable('此 Codex 版本不支持原生项目关联，请更新电脑 Codex App 后重试，或在 App 中添加项目后重新选择')
    return supported


def _native_project(request, cwd, project):
    canonical = lambda value: os.path.normcase(os.path.realpath(value))
    matches = []
    cursor = None
    seen = set()
    while True:
        page = request('project/list', {'cursor': cursor, 'limit': 100})
        matches.extend(row for row in page['data'] if any(
            canonical(root['path']) == canonical(cwd) for root in row.get('roots', [])))
        cursor = page.get('nextCursor')
        if cursor is None:
            break
        if cursor in seen:
            raise CreationError('原生项目列表游标无效，请在电脑 App 检查项目')
        seen.add(cursor)
    named = [row for row in matches if row.get('name') == project.get('name')]
    selected = named if named else matches
    if len(selected) > 1:
        raise CreationError('此目录对应多个原生项目，请在电脑 App 确认项目归属后重试')
    if selected:
        return selected[0]['id']
    raise CreationUnavailable('电脑 App 项目与运行时尚未对应，请检查所用 Codex App 和运行时配置后重试')


def model_overrides(model=None, effort=None):
    if model is None and effort is None:
        return {}
    if not isinstance(model, str) or not model or len(model) > 300 or any(c.isspace() or ord(c) < 32 for c in model):
        raise CreationUnavailable('新会话模型无效')
    if effort is not None and effort not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
        raise CreationUnavailable('新会话思考强度无效')
    return {'model': model, **({'config': {'model_reasoning_effort': effort}} if effort is not None else {})}


def create_empty(executable, codex_home, cwd, title, project=None, model=None, effort=None):
    overrides = model_overrides(model, effort)
    supported = project is not None and _native_project_support(executable, project.get('required', False))
    def create(request):
        project_id = _native_project(request, cwd, project) if supported else None
        params = {'cwd': cwd, 'ephemeral': False, **overrides}
        if project_id is not None:
            params['projectId'] = project_id
        result = request('thread/start', params)
        thread_id = result['thread']['id']
        uuid.UUID(thread_id)
        if model is not None and (result.get('model') != model or effort is not None and result.get('reasoningEffort') != effort):
            raise CreationError('聊天已创建，但模型或思考强度尚未确认，请检查原会话；不会重复创建')
        if project_id is not None and result['thread'].get('projectId') != project_id:
            raise CreationError('聊天已创建但原生项目归属未确认，请检查电脑 App，不要重复创建')
        request('thread/name/set', {'threadId': thread_id, 'name': title})
        saved = request('thread/read', {'threadId': thread_id, 'includeTurns': True})
        if project_id is not None and saved['thread'].get('projectId') != project_id:
            raise CreationError('聊天已创建但项目归属未持久化确认，请检查电脑 App，不要重复创建')
        return thread_id
    return _runtime_operation(executable, codex_home, cwd, create)


def fork_copy(executable, codex_home, cwd, source_id, turn_id, title, settings, *, thread_source=None, isolated_home=False):
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
        required = {'lastTurnId', 'deferGoalContinuation'}
        if thread_source is not None:
            required.add('threadSource')
        if not required <= properties.keys():
            raise ForkUnavailable('此 Codex 版本暂不支持安全分支，请更新电脑 Codex App')
    def fork(request):
        result = request('thread/fork', {'threadId': source_id, 'lastTurnId': turn_id,
                                        'deferGoalContinuation': True, 'ephemeral': False,
                                        'cwd': cwd, **settings, **({'threadSource': thread_source} if thread_source else {})})
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
    return _runtime_operation(executable, codex_home, cwd, fork, isolated_home=isolated_home)


def open_project_in_desktop(cwd):
    # Native newThread navigation registers the folder and opens an empty
    # composer. Never include a prompt, message, or submit instruction.
    _open_desktop_url('codex://threads/new?' + urlencode({'path': cwd}))


def open_in_desktop(thread_id, host):
    uuid.UUID(thread_id)
    url = 'codex://threads/' + thread_id
    if host != 'local':
        url += '?' + urlencode({'hostId': host})
    _open_desktop_url(url)


def _open_desktop_url(url):
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


def rename_thread(executable, codex_home, thread_id, title, *, isolated_home=False):
    uuid.UUID(thread_id)
    return _runtime_operation(executable, codex_home, str(codex_home),
                              lambda request: request('thread/name/set', {'threadId': thread_id, 'name': title}),
                              isolated_home=isolated_home)


class ArchiveRejected(CreationError):
    """The native archive RPC replied with an explicit error, not a lost reply."""
    def __init__(self, error):
        self.rpc_error = error if isinstance(error, dict) else {'message': str(error)}
        message = str(self.rpc_error.get('message', ''))
        self.reason = ('active_writer' if 'already has an active writer' in message else
                       'unsupported' if self.rpc_error.get('code') == -32601 else 'native_rejected')
        public = ('聊天仍由原 Codex 客户端持有；当前原生连接无法归档，请在持有该聊天的原客户端操作'
                  if self.reason == 'active_writer' else
                  '原 Codex 运行时不支持此归档操作，请在原客户端操作' if self.reason == 'unsupported' else
                  '原生客户端已拒绝此次归档操作，请检查原客户端后重试')
        super().__init__(public)


class ArchiveNotSent(CreationError):
    """Archive validation failed before a native mutation was dispatched."""


def check_archive_runtime(request, identifiers):
    """Read only: never resume a thread to establish archive safety."""
    for identifier in identifiers:
        thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        status = (thread.get('status') or {}).get('type')
        if (thread.get('id') != identifier or status not in ('idle', 'notLoaded') or
                thread.get('requests') or any(turn.get('status') == 'inProgress' for turn in thread.get('turns', []))):
            raise ValueError('聊天或子智能体正在运行、等待确认或状态未知，请先在原客户端处理')


def stop_archive_runtime(request, identifiers, timeout=20):
    """Stop verified turns and background terminals on their original runtime."""
    turns = []
    def background(identifier):
        thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        if thread.get('id') != identifier:
            raise ValueError('原会话身份已变化，尚未发送归档请求')
        if (thread.get('status') or {}).get('type') == 'notLoaded':
            return False
        result = request('thread/backgroundTerminals/list', {'threadId': identifier, 'limit': 1})
        if not isinstance(result.get('data'), list):
            raise ValueError('无法确认原生后台任务状态')
        return bool(result['data'] or result.get('nextCursor'))
    # Complete preflight before the first stop; never resume to discover activity.
    try:
        for identifier in identifiers:
            thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
            status = (thread.get('status') or {}).get('type')
            if thread.get('id') != identifier or status not in ('idle', 'notLoaded', 'active'):
                raise ValueError('无法确认原会话状态，未停止或归档')
            if status == 'active':
                rows = request('thread/turns/list', {'threadId': identifier, 'limit': 1, 'sortDirection': 'desc'})['data']
                if len(rows) != 1 or rows[0].get('status') != 'inProgress' or not isinstance(rows[0].get('id'), str):
                    raise ValueError('无法确认可停止的原生任务，未停止或归档')
                turns.append((identifier, rows[0]['id']))
            elif thread.get('requests'):
                raise ValueError('聊天有等待确认的请求，未停止或归档')
            background(identifier)
    except Exception as exc:
        raise ArchiveNotSent(str(exc)) from exc
    for identifier, turn_id in turns:
        # Stopping a parent can already settle its descendants.
        thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        if thread.get('id') != identifier:
            raise CreationError('原会话身份已变化，尚未发送归档请求')
        if (thread.get('status') or {}).get('type') == 'active':
            request('turn/interrupt', {'threadId': identifier, 'turnId': turn_id})
    for identifier in identifiers:
        if background(identifier):
            request('thread/backgroundTerminals/clean', {'threadId': identifier})
    deadline = time.monotonic() + timeout
    while True:
        try:
            check_archive_runtime(request, identifiers)
            if any(background(identifier) for identifier in identifiers):
                raise ValueError('后台任务尚未停止')
            return
        except ValueError:
            if time.monotonic() >= deadline:
                raise CreationError('停止结果待核对，尚未发送归档请求') from None
            time.sleep(.1)


def archive_thread(executable, codex_home, thread_id, archived, identifiers, *, isolated_home=False):
    uuid.UUID(thread_id)
    sent = False
    def dispatch(request):
        nonlocal sent
        check_archive_runtime(request, identifiers)
        sent = True
        return request('thread/archive' if archived else 'thread/unarchive', {'threadId': thread_id})
    try:
        return _runtime_operation(executable, codex_home, str(codex_home), dispatch, isolated_home=isolated_home)
    except Exception as exc:
        if not sent:
            raise ArchiveNotSent(str(exc)) from exc
        raise
