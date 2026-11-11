"""Standalone Claude CLI SSH source. Sent to the selected host; bridge is not installed there."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from bridge.clients.claude.file_archive import archive_paths, state as archive_file_state, change as archive_file_change, supported as archive_supported, assert_no_cli as archive_assert_no_cli
from bridge.clients.claude.history import image_event, session_custom_title, local_command_text
from bridge.clients.claude.creation import creation_launch_options

MODES = {'default', 'acceptEdits', 'plan', 'dontAsk', 'auto', 'bypassPermissions'}


def draft_account_identity(directory, cwd=None, environment=None):
    """Use stable local account metadata without starting or querying Claude."""
    directory = Path(directory)
    selected = {name: value for name, value in (environment or {}).items()
                if name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
                            'ANTHROPIC_BASE_URL') and isinstance(value, str) and value}
    files = [directory/'settings.json', directory/'settings.local.json']
    if cwd:
        files.extend((Path(cwd)/'.claude/settings.json', Path(cwd)/'.claude/settings.local.json'))
    for path in files:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        settings = value.get('env') if isinstance(value, dict) else None
        if isinstance(settings, dict):
            selected.update({name: item for name, item in settings.items()
                             if name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                                         'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL')
                             and isinstance(item, str) and item})
    key = selected.get('ANTHROPIC_API_KEY') or selected.get('ANTHROPIC_AUTH_TOKEN') or selected.get('CLAUDE_CODE_OAUTH_TOKEN')
    if key:
        value = ['claude-code-api', str(directory.resolve()), selected.get('ANTHROPIC_BASE_URL') or '', key]
        return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
    metadata_paths = [directory/'.claude.json']
    if directory.resolve() == Path.home()/'.claude':
        metadata_paths.insert(0, directory.parent/'.claude.json')
    for path in metadata_paths:
        try:
            metadata = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        account = metadata.get('oauthAccount') if isinstance(metadata, dict) else None
        if not isinstance(account, dict):
            continue
        user = account.get('accountUuid') or account.get('emailAddress')
        organization = account.get('organizationUuid') or account.get('organizationId') or ''
        if isinstance(user, str) and user:
            value = ['claude-code', str(directory.resolve()), user, organization]
            return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
    try:
        credential = (directory/'.credentials.json').read_bytes()
        if credential:
            return hashlib.sha256(b'claude-code-credential\0' + credential).hexdigest()
    except OSError:
        pass
    return ''


def auth_status(binary, directory, environment=None):
    env = dict(os.environ if environment is None else environment)
    env['CLAUDE_CONFIG_DIR'] = str(directory)
    try:
        result = subprocess.run([binary, 'auth', 'status', '--json'], env=env,
                                capture_output=True, text=True, timeout=5)
        value = json.loads(result.stdout)
        return value if result.returncode == 0 and isinstance(value, dict) else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def auth_fingerprint(binary, directory, environment=None):
    status = (auth_status(binary, directory) if environment is None
              else auth_status(binary, directory, environment))
    if os.environ.get('MOBILE_BRIDGE_WSL_NATIVE') == '1' and type(status.get('loggedIn')) is not bool:
        # A cold native CLI can time out once. Unknown evidence must never
        # become a synthetic logged-out owner or replace an existing scope.
        status = (auth_status(binary, directory) if environment is None
                  else auth_status(binary, directory, environment))
        if type(status.get('loggedIn')) is not bool:
            raise ValueError('WSL Claude Code 登录状态暂无法核验，请稍后重试')
    stable = {key: status[key] for key in ('authMethod', 'apiProvider', 'email', 'accountId',
              'accountUuid', 'organizationId', 'organizationUuid', 'orgId', 'workspaceId')
              if isinstance(status.get(key), str) and status[key]}
    credentials = Path(directory)/'.credentials.json'
    environment = os.environ if environment is None else environment
    ambient = {key: environment[key] for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
               'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_CUSTOM_HEADERS')
               if environment.get(key)}
    identity = {'loggedIn': status.get('loggedIn') is True, 'stable': stable,
                'ambient': ambient}
    if not any(key in stable for key in ('email', 'accountId', 'accountUuid',
                                        'organizationId', 'organizationUuid', 'orgId', 'workspaceId')):
        try:
            identity['credentials'] = hashlib.sha256(credentials.read_bytes()).hexdigest()
        except OSError:
            identity['credentials'] = 'none'
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def native_wsl_binary(binary):
    """A Windows npm shim is not a Linux Claude Code installation."""
    if not binary:
        return False
    path = Path(binary).resolve()
    if re.match(r'^/mnt/[a-zA-Z](?:/|$)', str(path)) or path.suffix.lower() in ('.exe', '.cmd', '.bat'):
        return False
    try:
        with path.open('rb') as stream:
            header = stream.read(8192)
        if header.startswith(b'MZ'):
            return False
        if header.startswith(b'#!'):
            text = header.decode('utf-8', errors='replace').lower()
            if any(name in text for name in ('claude.exe', 'node.exe', 'cmd.exe', 'powershell.exe')):
                return False
            if 'node' in text.splitlines()[0]:
                node = shutil.which('node')
                if not node or not native_wsl_binary(node):
                    return False
        return True
    except OSError:
        return False


def profile():
    binary = shutil.which('claude') or next((str(p) for p in
        (Path.home()/'.local/bin/claude', Path('/usr/local/bin/claude'))
        if p.is_file() and os.access(p, os.X_OK)), None)
    if os.environ.get('MOBILE_BRIDGE_WSL_NATIVE') == '1' and not native_wsl_binary(binary):
        binary = next((str(p) for p in (Path.home()/'.local/bin/claude',
            Path('/usr/local/bin/claude'), Path.home()/'.npm-global/bin/claude',
            *Path.home().glob('.nvm/versions/node/*/bin/claude'))
            if p.is_file() and os.access(p, os.X_OK) and native_wsl_binary(p)), None)
    if not binary:
        return None
    explicit = os.environ.get('CLAUDE_CONFIG_DIR')
    directory = Path(explicit).expanduser().resolve() if explicit else Path.home()/'.claude'
    try:
        settings_bytes = (directory/'settings.json').read_bytes()
    except OSError:
        digest = 'none'
    else:
        if os.environ.get('MOBILE_BRIDGE_WSL_NATIVE') == '1':
            try:
                settings = json.loads(settings_bytes)
            except (ValueError, UnicodeError):
                pass  # Invalid settings retain their original byte identity.
            else:
                if isinstance(settings, dict):
                    # WSL's native TUI writes its display theme at first launch.
                    # Presentation changes must not replace the account scope.
                    settings.pop('theme', None)
                    settings_bytes = json.dumps(settings, sort_keys=True, separators=(',', ':')).encode()
        digest = hashlib.sha256(settings_bytes).hexdigest()
    binary = str(Path(binary).resolve())
    auth = auth_fingerprint(binary, directory)
    return {'binary': binary, 'directory': str(directory),
            'origin': ('environment:' if explicit else 'default:') + str(directory) + ':' + digest + ':' + auth}


def auth_ready(found):
    return auth_status(found['binary'], found['directory']).get('loggedIn') is True


def events(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError('Claude 会话记录过大')
    result = []
    with path.open(encoding='utf-8', errors='replace') as source:
        for line in source:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                result.append(event)
    return result


def _file_stamp(fd):
    stat = os.fstat(fd)
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def file_stamp(path):
    # Use the same descriptor API for both ends of a read. On Windows,
    # stat(path) and fstat(fd) can expose different file identity metadata.
    with path.open('rb') as stream:
        return _file_stamp(stream.fileno())


def history_page(path, identity, before=None, limit=40):
    """Standalone bounded reverse reader; this source runs without bridge installed."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Claude 历史分页大小无效')
    with path.open('rb') as stream:
        stamp = _file_stamp(stream.fileno())
        end = stamp[2]
        if before is not None:
            if not isinstance(before, str) or len(before) > 1024:
                raise ValueError('Claude 历史游标无效')
            try:
                source, version, end = json.loads(base64.urlsafe_b64decode(
                    before + '=' * (-len(before) % 4)))
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError('Claude 历史游标无效') from exc
            if source != identity or version != stamp or type(end) is not int or not 0 <= end <= stamp[2]:
                raise ValueError('Claude 历史已变化，请重新加载最近记录')
            if end:
                stream.seek(end - 1)
                if stream.read(1) != b'\n':
                    raise ValueError('Claude 历史游标无效')
        pos, pending, consumed, selected, boundary, visible = end, b'', 0, [], end, 0
        has_more = False
        while pos or pending:
            if pos:
                size = min(65536, pos, 4 * 1024 * 1024 - consumed)
                if size <= 0:
                    raise ValueError('Claude 历史单页扫描超过限制，请缩小分页范围')
                pos -= size
                stream.seek(pos)
                block = stream.read(size)
                if len(block) != size:
                    raise ValueError('Claude 历史读取期间发生变化')
                consumed += size
                pending = block + pending
            lines = pending.splitlines(keepends=True)
            keep = lines.pop(0) if pos else b''
            offset = pos + len(keep) + sum(map(len, lines))
            for line in reversed(lines):
                offset -= len(line)
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get('type') not in ('user', 'assistant', 'result', 'system'):
                    continue
                if event['type'] in ('user', 'assistant') and not event.get('isSynthetic') and not event.get('isMeta'):
                    visible += 1
                    if not event.get('uuid') and not (isinstance(event.get('message'), dict)
                                                      and event['message'].get('id')):
                        event = {**event, 'uuid': 'file:' + str(offset)}
                selected.append(event)
                boundary = offset
                if visible >= limit:
                    has_more = boundary > 0
                    break
            pending = keep
            if has_more:
                break
        if _file_stamp(stream.fileno()) != stamp:
            raise ValueError('Claude 历史读取期间发生变化')
        try:
            current = file_stamp(path)
        except OSError as exc:
            raise ValueError('Claude 历史读取期间发生变化') from exc
        if current != stamp:
            raise ValueError('Claude 历史读取期间发生变化')
        cursor = None
        if has_more:
            data = json.dumps([identity, stamp, boundary], separators=(',', ':')).encode()
            cursor = base64.urlsafe_b64encode(data).decode().rstrip('=')
        return {'events': list(reversed(selected)), 'history': {'before': cursor, 'hasMore': has_more},
                'stamp': stamp}


def saved(path, found, *, include_events=False):
    command_chain = {}
    command_title = None
    try:
        native = str(uuid.UUID(path.stem))
        if path.is_symlink() or not path.is_file():
            return None
        stamp = file_stamp(path)
    except (OSError, ValueError):
        return None
    title, cwd, model, permission, user_permission = None, '', '', None, None
    context = []
    history = [] if include_events else None
    try:
        with path.open(encoding='utf-8', errors='replace') as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if include_events:
                    history.append(event)
                kind = event.get('type')
                command = local_command_text(event, command_chain)
                if command_title is None and command_chain.get('phase') == 'command':
                    command_title = command[:120]
                custom_title = session_custom_title(event, native)
                if custom_title is not None:
                    title = custom_title
                if kind == 'user' and not event.get('isSidechain'):
                    if title is None and command is None and not event.get('isMeta') and not event.get('isSynthetic'):
                        message = event.get('message') if isinstance(event.get('message'), dict) else {}
                        content = message.get('content', '')
                        if isinstance(content, list):
                            content = '\n'.join(item.get('text', '') for item in content
                                                if isinstance(item, dict) and item.get('type') == 'text')
                        title = str(content).strip().splitlines()[0][:120] if str(content).strip() else 'Claude Code 会话'
                    if event.get('permissionMode') in MODES:
                        user_permission = event['permissionMode']
                if kind in ('user', 'assistant') and isinstance(event.get('cwd'), str) and event['cwd']:
                    cwd = event['cwd']
                if kind == 'assistant' and isinstance(event.get('message'), dict):
                    candidate = event['message'].get('model')
                    if (isinstance(candidate, str) and candidate != '<synthetic>'
                            and not event.get('isApiErrorMessage')):
                        model = candidate
                if kind == 'mode' and event.get('mode') in MODES:
                    permission = event['mode']
                if kind == 'system' and event.get('subtype') == 'compact_boundary':
                    context = [{'type': 'system', 'subtype': 'compact_boundary'}]
                elif (kind == 'assistant' and not any(event.get(key) for key in
                      ('isSidechain', 'parent_tool_use_id', 'isSynthetic', 'isMeta', 'isCompactSummary'))
                      and isinstance(event.get('message'), dict)
                      and isinstance(event['message'].get('usage'), dict)
                      and type(event['message']['usage'].get('input_tokens')) is int
                      and event['message']['usage']['input_tokens'] >= 0):
                    compacted = bool(context and context[0].get('subtype') == 'compact_boundary')
                    context = ([{'type': 'system', 'subtype': 'compact_boundary'}] if compacted else [])
                    context.append({**{key: event[key] for key in ('type', 'isSidechain', 'parent_tool_use_id',
                                   'isSynthetic', 'isMeta', 'isCompactSummary') if key in event},
                                   'message': {key: event['message'][key] for key in
                                               ('usage', 'model', 'context_window') if key in event['message']}})
                elif (kind == 'result' and context and
                      not any(event.get(key) for key in ('isSidechain', 'parent_tool_use_id',
                              'isSyntheticResult', 'isSynthetic', 'isMeta', 'isCompactSummary'))):
                    assistant = next((item for item in context if item.get('type') == 'assistant'), None)
                    model_name = (assistant or {}).get('message', {}).get('model')
                    models = event.get('modelUsage')
                    details = models.get(model_name) if isinstance(models, dict) else None
                    capacity = details.get('contextWindow') if isinstance(details, dict) else None
                    if model_name and type(capacity) is int and capacity > 0:
                        if context[-1].get('type') == 'result':
                            context.pop()
                        context.append({'type': 'result', 'modelUsage': {model_name: {'contextWindow': capacity}}})
    except OSError:
        return None
    try:
        if file_stamp(path) != stamp:
            return None
    except OSError:
        return None
    if title is None:
        title = command_title
    if title is None:
        return None
    if not cwd:
        return None
    project_settings = []
    for name in ('settings.json', 'settings.local.json'):
        try:
            project_settings.append(hashlib.sha256((Path(cwd)/'.claude'/name).read_bytes()).hexdigest())
        except OSError:
            project_settings.append(None)
    if permission is None:
        permission = user_permission
    row = {'nativeId': native, 'origin': found['origin'], 'cwd': cwd, 'model': model,
           'projectConfig': hashlib.sha256(json.dumps(project_settings).encode()).hexdigest(),
           'permissionMode': permission or 'default', 'permissionKnown': permission is not None,
           'updatedAt': path.stat().st_mtime, 'title': title, 'stamp': stamp,
           'contextEvents': context,
           'draftAccountIdentity': draft_account_identity(found['directory'], cwd, os.environ)}
    if include_events:
        row['events'] = history
    return row


def session_paths(found, errors=None):
    root = Path(found['directory'])/'projects'
    result = []
    if root.is_dir() and not root.is_symlink():
        for directory in root.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                result.extend(path for path in directory.glob('*.jsonl') if not path.is_symlink())
    return result + archive_paths(found['directory'], errors if errors is not None else [])


def sessions(found, errors=None):
    errors = errors if errors is not None else []
    result = []
    capable = archive_supported(found['binary'], os.environ.copy())
    for path in session_paths(found, errors):
        row = saved(path, found)
        if row:
            result.append({**row, 'archived': path.parent.parent == Path(found['directory'])/'bridge-archives',
                           'capabilities': {'archive': capable, 'unarchive': capable}})
    for error in errors:
        result.append({'nativeId': error['nativeId'], 'origin': found['origin'],
                       'title': 'Claude Code 归档待核对', 'cwd': '', 'model': '',
                       'permissionMode': 'default', 'permissionKnown': False, 'updatedAt': 0,
                       'stamp': None, 'contextEvents': [], 'archived': True, 'archiveIncomplete': True,
                       'runtimeKnown': False, 'status': 'unknown',
                       'capabilities': {'archive': False, 'unarchive': False}})
    return result


def session_path(found, native):
    native = str(uuid.UUID(native))
    matches = [path for path in session_paths(found) if path.stem == native and path.is_file()]
    if len(matches) != 1:
        raise ValueError('原 Claude Code 会话路径不可用或存在冲突')
    return matches[0]


def session(found, native):
    path = session_path(found, native)
    row = saved(path, found)
    if row:
        capable = archive_supported(found['binary'], os.environ.copy())
        return {**row, **archive_file_state(found['directory'], path, native),
                'capabilities': {'archive': capable, 'unarchive': capable}}
    raise ValueError('原 Claude Code 会话不可用')


def pid_domain():
    if sys.platform == 'linux':
        machine = Path('/etc/machine-id').read_text().strip()
        namespace = os.readlink('/proc/self/ns/pid')
        if not machine or not re.fullmatch(r'pid:\[\d+\]', namespace):
            raise ValueError('Linux 进程身份不可验证')
        return 'linux:' + machine + ':' + namespace
    if sys.platform == 'darwin':
        return 'darwin'
    raise ValueError('当前系统的 Claude 进程身份不可验证')


def linux_process_start(pid):
    # Kept standalone for SSH injection; matches platforms/linux/claude_process.py.
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except FileNotFoundError:
        return None
    fields = stat.rpartition(')')[2].split()
    if (not stat.startswith(f'{pid} (') or len(fields) < 20 or
            not re.fullmatch(r'[0-9]+', fields[19])):
        raise ValueError('无法核验 Claude Code 原会话是否仍在运行')
    return fields[19]


def assert_session_closed(found, native=None):
    directory = Path(found['directory'])/'sessions'
    if native is None and not directory.exists() and not directory.is_symlink():
        return
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('无法核验 Claude Code 原会话是否仍在运行')
    domain = pid_domain()
    for path in directory.iterdir():
        if path.suffix != '.json':
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise ValueError('无法核验 Claude Code 原会话是否仍在运行')
        entry = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(entry, dict):
            raise ValueError('无法核验 Claude Code 原会话是否仍在运行')
        if native is not None and entry.get('sessionId') != native:
            continue
        pid = entry.get('pid')
        start = entry.get('procStart')
        if (type(pid) is not int or pid <= 0 or path.stem != str(pid) or
                not isinstance(start, str) or not start or entry.get('pidDomain') != domain):
            raise ValueError('无法核验 Claude Code 原会话是否仍在运行')
        if sys.platform == 'linux':
            if not re.fullmatch(r'[0-9]+', start):
                raise ValueError('无法核验 Claude Code 原会话是否仍在运行')
            if linux_process_start(pid) == start:
                raise ValueError('原 Claude Code 会话仍在其他终端运行')
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except (PermissionError, OSError):
            raise ValueError('无法核验 Claude Code 原会话是否仍在运行') from None
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                                env={**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC'},
                                capture_output=True, text=True, timeout=3)
        if result.returncode != 0 or not result.stdout.strip():
            raise ValueError('无法核验 Claude Code 原会话是否仍在运行')
        if result.stdout.split() == start.split():
            raise ValueError('原 Claude Code 会话仍在其他终端运行')


def upload_root(found, scope):
    uuid.UUID(scope)
    root = Path(found['directory'])/'mobile-bridge'/'uploads'
    path = root/scope
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(part.is_symlink() for part in (root.parent, root, path)):
        raise ValueError('附件目录无效')
    return path.resolve()


def upload(found, request):
    identifier = str(uuid.UUID(request['id']))
    name = request['name']
    if not isinstance(name, str) or not name or len(name.encode()) > 200 or re.search(r'[\\/\x00-\x1f\x7f]', name):
        raise ValueError('附件文件名无效')
    data = base64.b64decode(request['data'], validate=True)
    if not 0 < len(data) <= 20*1024*1024 or hashlib.sha256(data).hexdigest() != request['sha256']:
        raise ValueError('附件内容不匹配')
    folder = upload_root(found, request['scope'])/identifier
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('附件路径无效')
    suffix = Path(name).suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,16}', suffix):
        suffix = '.bin'
    path = folder/('original'+suffix)
    if path.is_symlink():
        raise ValueError('附件路径无效')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != request['sha256']:
            raise ValueError('附件标识已被其他文件使用')
    else:
        part = folder/'original.part'
        with part.open('xb') as stream:
            stream.write(data)
        part.chmod(0o600)
        part.replace(path)
    return {'path': str(path.resolve())}


def continuation_identity(directory):
    files = {}
    for name in ('settings.json', '.credentials.json'):
        path = Path(directory)/name
        if path.is_symlink():
            raise ValueError('WSL 账号配置不可重定向')
        try:
            files[name] = base64.b64encode(path.read_bytes()).decode()
        except FileNotFoundError:
            files[name] = None
    return hashlib.sha256(json.dumps(files, ensure_ascii=False, indent=2, sort_keys=True).encode()).hexdigest()


def resume_directory(cwd):
    try:
        directory = Path(cwd).resolve(strict=True)
    except FileNotFoundError:
        raise ValueError('原项目目录不存在：'+cwd+'，请在远端恢复该目录后重试') from None
    except PermissionError:
        raise ValueError('原项目目录无访问权限：'+cwd) from None
    except OSError:
        raise ValueError('原项目目录不可用：'+cwd) from None
    if not directory.is_dir():
        raise ValueError('原项目路径不是目录：'+cwd)
    if not os.access(directory, os.R_OK | os.X_OK):
        raise ValueError('原项目目录无访问权限：'+cwd)
    return directory


def command(request):
    found = profile()
    operation = request.get('operation')
    if operation == 'account-continuation':
        if os.environ.get('MOBILE_BRIDGE_WSL_NATIVE') != '1' or not found:
            raise ValueError('WSL 账号续接核验不可用')
        identity = continuation_identity(found['directory'])
        rows = sessions(found)
        if found != profile() or identity != continuation_identity(found['directory']):
            raise ValueError('WSL 账号配置在续接核验期间变化')
        return {'identity': identity, 'profile': found, 'sessions': rows}
    if operation == 'list':
        errors = []
        rows = sessions(found, errors) if found else []
        return {'profile': found, 'sessions': rows, 'complete': not errors}
    if operation == 'projects':
        return {'profile': found, 'sessions': sessions(found) if found else [],
                'authReady': auth_ready(found) if found else False}
    if not found or found['origin'] != request.get('origin'):
        raise ValueError('原 Claude Code 配置已变化或不可用')
    if operation == 'resume-preflight':
        row = session(found, request['native'])
        if any(row[key] != request.get(key) for key in ('cwd', 'model', 'permissionMode')):
            raise ValueError('原 Claude Code 会话设置已变化，请刷新后重试')
        if not row['permissionKnown']:
            return {'ready': False, 'notice': '无法确认原会话权限模式，请在远端终端继续'}
        try:
            resume_directory(row['cwd'])
        except ValueError as exc:
            return {'ready': False, 'notice': str(exc)}
        ready = not row.get('archived') and auth_ready(found)
        return {'ready': ready, 'notice': '' if ready else '原 Claude Code 账号不可用，请在远端终端核对登录状态'}
    if operation in ('archive-state', 'archive'):
        native = str(uuid.UUID(request['native']))
        row = session(found, native)
        row.update(archive_file_state(found['directory'], session_path(found, native), native, verify=True))
        if not row['capabilities']['archive']:
            raise ValueError('原 Claude Code 归档能力或停止状态尚未确认')
        safe = False
        try:
            assert_session_closed(found)
            archive_assert_no_cli(found['binary'])
            safe = True
        except ValueError:
            if operation == 'archive':
                raise
        if found != profile():
            raise ValueError('原 Claude Code 配置已变化或不可用')
        if operation == 'archive':
            assert_session_closed(found)
            archive_assert_no_cli(found['binary'])
            if not archive_supported(found['binary'], os.environ.copy()):
                raise ValueError('原 Claude Code 二进制归档能力已变化，请重新核对')
            archive_file_change(found['directory'], session_path(found, native), native, request.get('archived'))
            row = session(found, native)
        return {**row, 'archiveSafe': safe, 'runtimeKnown': True, 'status': 'idle', 'requests': []}
    if operation == 'detail':
        return session(found, request['native'])
    if operation == 'image':
        return image_event(session_path(found, request['native']), request.get('source'))
    if operation == 'history':
        path = session_path(found, request['native'])
        scope = request.get('scope')
        if not isinstance(scope, str) or not scope:
            raise ValueError('Claude 历史来源无效')
        return history_page(path, found['origin'] + ':' + str(uuid.UUID(request['native'])) + ':' + scope,
                            request.get('historyBefore'), request.get('historyLimit', 40))
    if operation == 'auth':
        return {'ready': auth_ready(found)}
    if operation == 'upload':
        return upload(found, request)
    raise ValueError('不支持的 Claude Code 远端操作')


def resume(request):
    found = profile()
    if not found or found['origin'] != request.get('origin'):
        raise ValueError('原 Claude Code 配置已变化或不可用')
    row = session(found, request['native'])
    if row.get('archived'):
        raise ValueError('请先恢复归档的 Claude Code 会话')
    if row['cwd'] != request.get('cwd') or row['model'] != request.get('model') or row['permissionMode'] != request.get('permissionMode'):
        raise ValueError('原 Claude Code 会话设置已变化')
    if not row['permissionKnown'] or not auth_ready(found):
        raise ValueError('原 Claude Code 账号或权限模式不可用')
    cwd = resume_directory(row['cwd'])
    assert_session_closed(found, row['nativeId'])
    directory = upload_root(found, request['scope'])
    if profile() != found:
        raise ValueError('原 Claude Code 配置已变化或不可用')
    arguments = [found['binary'], '-p', '--resume', row['nativeId'], '--input-format', 'stream-json',
                 '--output-format', 'stream-json', '--verbose', '--replay-user-messages',
                 '--setting-sources', 'user,project,local', '--permission-mode', row['permissionMode'],
                 '--permission-prompt-tool', 'stdio', '--permission-prompts', 'host',
                 '--allow-dangerously-skip-permissions', '--add-dir', str(directory)]
    if row['model']:
        arguments.extend(('--model', row['model']))
    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = found['directory']
    os.chdir(cwd)
    print(json.dumps({'type': 'bridge_preflight', 'status': 'ready', 'cwd': str(cwd)}), flush=True)
    os.execvpe(found['binary'], arguments, env)


def create(request):
    found = profile()
    if not found or found['origin'] != request.get('origin') or not auth_ready(found):
        raise ValueError('原 Claude Code 账号配置已变化或不可用')
    native = str(uuid.UUID(request['native']))
    cwd = Path(request['cwd']).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError('原项目目录不可用')
    if request.get('directoryIdentity') is not None:
        stat = cwd.stat()
        if request['directoryIdentity'] != {'cwd': str(cwd), 'device': stat.st_dev, 'inode': stat.st_ino}:
            raise ValueError('项目目录已变化，请重新选择目录')
    directory = upload_root(found, request['scope'])
    if profile() != found:
        raise ValueError('原 Claude Code 配置已变化或不可用')
    arguments = [found['binary'], '-p', '--session-id', native, '--input-format', 'stream-json',
                 '--output-format', 'stream-json', '--verbose', '--replay-user-messages',
                 '--setting-sources', 'user,project,local', '--permission-prompt-tool', 'stdio',
                 '--permission-prompts', 'host', '--allow-dangerously-skip-permissions',
                 '--add-dir', str(directory)]
    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = found['directory']
    selected_args, selected_env = creation_launch_options(request.get('selection'))
    arguments.extend(selected_args)
    for key, value in selected_env.items():
        if value is None: env.pop(key, None)
        else: env[key] = value
    os.chdir(cwd)
    print(json.dumps({'type': 'bridge_preflight', 'status': 'ready', 'cwd': str(cwd)}), flush=True)
    os.execvpe(found['binary'], arguments, env)


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.readline())
        if request.get('operation') in ('resume', 'create'):
            (resume if request['operation'] == 'resume' else create)(request)
        else:
            print(json.dumps({'ok': True, 'value': command(request)}, ensure_ascii=False), flush=True)
    except Exception as exc:
        if 'request' in locals() and request.get('operation') in ('resume', 'create'):
            value = {'type': 'bridge_preflight', 'status': 'blocked', 'message': str(exc)[:300]}
        else:
            value = {'ok': False, 'message': str(exc)[:300]}
        print(json.dumps(value, ensure_ascii=False), flush=True)
