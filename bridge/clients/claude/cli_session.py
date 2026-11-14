"""Read saved Claude Code CLI chats and control only processes started here."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from bridge.clients.claude import model, file_archive
from bridge.clients.claude.history import file_stamp, page as history_page, image_event, session_custom_title, local_command_text, merge_live_page
from bridge.clients.claude.context import context_fields
from bridge.clients.claude.session_registry import assert_session_closed
from bridge.clients.claude.creation import creation_launch_options
from bridge.clients.errors import BridgeUnavailable, ResumePreflightBlocked
from bridge.clients.uploads import DesktopUploads
from bridge.features.sessions.display_metadata import confirm_native_title


MODES = {'default', 'acceptEdits', 'plan', 'dontAsk', 'auto', 'bypassPermissions'}
PERMISSION_OPTIONS = (
    ('default', '默认权限', '需要时请求确认'),
    ('acceptEdits', '允许文件编辑', '文件编辑自动允许，其他操作仍按规则确认'),
    ('plan', '计划模式', '先制定计划'),
    ('dontAsk', '不询问', '无法自动允许的操作直接拒绝'),
    ('auto', '自动模式', '由 Claude 判断可自动执行的操作'),
    ('bypassPermissions', '完全访问', '跳过权限确认'),
)
_MANAGED_API_ROUTING = {
    'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST',
    'CLAUDE_CODE_USE_ANTHROPIC_AWS',
    'CLAUDE_CODE_USE_BEDROCK',
    'CLAUDE_CODE_USE_FOUNDRY',
    'CLAUDE_CODE_USE_MANTLE',
    'CLAUDE_CODE_USE_VERTEX',
}


class ClaudeControlRejected(BridgeUnavailable):
    def __init__(self, reason):
        super().__init__('Claude Code 控制请求未确认，请检查原会话')
        self.reason = reason


def managed_api_env(source=None):
    """Keep ambient provider credentials and routing out of a selected API profile."""
    source = os.environ if source is None else source
    return {key: value for key, value in source.items()
            if not key.startswith('ANTHROPIC_') and key not in _MANAGED_API_ROUTING}


def project_account_identity(profile):
    """Bind directory selection to native account identity and effective API routing."""
    from bridge.clients.claude.remote_worker import auth_fingerprint
    environment = managed_api_env() if profile.get('managedApi') else os.environ.copy()
    return auth_fingerprint(profile['binary'], profile['directory'], environment)


def _supports_compact(catalog, *, desktop_projection=False):
    for row in catalog.get('commands') or []:
        if not isinstance(row, dict) or row.get('name') != 'compact':
            continue
        if row.get('builtin') is True:
            return True
        # Desktop projects SDK commands to name/description/argumentHint and
        # drops builtin. Match its observed native descriptor, never name alone:
        # custom commands can shadow compact and carry a source in description.
        if (desktop_projection and 'builtin' not in row and
                row.get('description') == 'Free up context by summarizing the conversation so far' and
                row.get('argumentHint') == '<optional custom summarization instructions>'):
            return True
    return False


def _compact_cli(owned, request_id):
    # The native slash dispatcher owns compaction; never substitute a model prompt.
    with owned.condition:
        if not _supports_compact(owned.catalog):
            raise BridgeUnavailable('当前 Claude Code 未提供原生上下文压缩命令')
        if owned.active or owned.pending or owned.error or owned.finished:
            raise BridgeUnavailable('请等待当前 Claude Code 任务结束后再压缩上下文')
        owned.active = True
        owned.write({'type': 'user', 'uuid': request_id,
                     'message': {'role': 'user', 'content': '/compact'}})


def _supports_rename(catalog):
    return ('ui_surface_v1' in (catalog.get('capabilities') or []) and
            any(isinstance(row, dict) and row.get('name') == 'rename' for row in catalog.get('commands') or []))


def _rename_cli(owned, row, title, readback):
    if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or
            re.search(r'[\x00-\x1f\x7f]', title)):
        raise ResumePreflightBlocked('标题为 1–120 字，不能包含控制字符')
    with owned.condition:
        if (not _supports_rename(owned.catalog) or owned.error or owned.finished or
                owned.native != row['nativeId'] or owned.cwd != row['cwd'] or owned.origin != row['origin']):
            raise ResumePreflightBlocked('当前 Claude Code 连接尚未提供已确认的原生改名能力')
    title = title.strip()
    # This is the official non-model control request. Do not send /rename as a
    # user prompt, append JSONL ourselves, or start a second CLI for metadata.
    owned.control({'subtype': 'rename_session', 'title': title, 'source': 'host', 'session_id': row['nativeId']})
    identity = {key: row.get(key) for key in ('nativeId', 'origin', 'cwd')}
    def read_title():
        after = readback()
        return ({'title': after.get('title'), 'identity': {key: after.get(key) for key in identity}}
                if after is not None else None)
    try:
        confirm_native_title(read_title, identity, title)
    except ValueError as error:
        raise BridgeUnavailable('改名结果待核对，请检查原 Claude Code 会话；不会重复提交') from error
    return {'status': 'confirmed', 'scope': 'native', 'result': {'id': row['id'], 'title': title}}


def _skill_catalog(catalog):
    """Use commands loaded by the native CLI with this profile and project."""
    rows = {}
    for item in catalog.get('commands') or []:
        if not isinstance(item, dict) or item.get('builtin'):
            continue
        name = item.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_:/-]{0,127}', name):
            continue
        rows[name] = {'id': name, 'name': name, 'description': item.get('description') or '',
                      'kind': 'skill', 'selectable': True}
    return sorted(rows.values(), key=lambda row: row['id'])


def _model_catalog(catalog):
    """Expose the CLI's resolved provider IDs, retaining its follow-default choice."""
    rows = {}
    for item in catalog.get('models') or []:
        if not isinstance(item, dict):
            continue
        value = item.get('value') or item.get('id')
        resolved = item.get('resolvedModel')
        if not isinstance(value, str) or not value:
            continue
        identifier = 'default' if value == 'default' else resolved or value
        if not isinstance(identifier, str) or not identifier:
            continue
        name = item.get('displayName') or item.get('name') or value
        if resolved and resolved not in name:
            name += ' · ' + resolved
        levels = item.get('supportedEffortLevels')
        efforts = ([level for level in levels if level in ('low', 'medium', 'high', 'xhigh', 'max')]
                   if isinstance(levels, list) and item.get('supportsEffort') is True else [])
        rows[identifier] = {'id': identifier, 'name': name,
                            'efforts': ['auto', *dict.fromkeys(efforts)] if efforts else [],
                            **({'supportsEffort': item['supportsEffort']} if type(item.get('supportsEffort')) is bool else {}),
                            **({'defaultEffort': 'auto'} if efforts else {})}
    return list(rows.values())


def _applied_settings(owned):
    settings = owned.control({'subtype': 'get_settings'})
    applied = settings.get('applied') if isinstance(settings, dict) else None
    if not isinstance(applied, dict) or not isinstance(applied.get('model'), str):
        raise BridgeUnavailable('Claude Code 未报告当前模型设置')
    return settings


def _has_resolved_models(catalog):
    # 2.1.168 reports effort levels but not resolved IDs, and rejects list_models.
    return any(isinstance(item, dict) and isinstance(item.get('resolvedModel'), str)
               and item['resolvedModel'] for item in catalog.get('models') or [])


def _cli_selection(owned, public):
    applied = _applied_settings(owned)['applied']
    return {'currentModel': applied['model'], 'currentEffort': applied.get('effort') or ''}


def _cli_catalog(owned, public, refresh=False):
    if refresh:
        if getattr(owned, 'model_list_unsupported', False):
            raise BridgeUnavailable('当前 Claude Code 版本不支持刷新模型目录')
        try:
            models = owned.control({'subtype': 'list_models'}).get('models')
        except ClaudeControlRejected as exc:
            if exc.reason != 'Unsupported control request subtype: list_models':
                raise
            owned.model_list_unsupported = True
            raise BridgeUnavailable('当前 Claude Code 版本不支持刷新模型目录') from None
        if not isinstance(models, list):
            raise BridgeUnavailable('Claude Code 未返回模型列表')
        owned.catalog['models'] = models
    rows = _model_catalog(owned.catalog)
    try:
        selection = _cli_selection(owned, public)
        verified = True
    except BridgeUnavailable:
        selection = {'currentModel': public['model'], 'currentEffort': ''}
        verified = False
    return {'models': rows, **selection,
            'capabilities': {'models': bool(verified), 'manualModels': bool(verified), 'manualEffort': bool(verified),
                             'effort': bool(verified and any(row['efforts'] for row in rows)),
                             'skills': True}, 'skills': _skill_catalog(owned.catalog)}


def _set_cli_settings(owned, body):
    rows = _model_catalog(owned.catalog)
    selected = next((row for row in rows if row['id'] == body.get('model')), None)
    manual_model = selected is None
    if manual_model:
        identifier = body.get('model')
        if not isinstance(identifier, str) or not re.fullmatch(r'[^\s\x00-\x1f\x7f]{1,256}', identifier):
            raise ValueError('请输入有效的模型名称')
        selected = {'id': identifier, 'efforts': []}
    effort = body.get('effort')
    if body.get('manualEffort') is True and (not isinstance(effort, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', effort)):
        raise ValueError('请输入有效的思考强度')
    if effort is not None and body.get('manualEffort') is not True and effort not in selected['efforts']:
        raise ValueError('该模型不支持此思考强度')
    _applied_settings(owned)
    target = next((item for item in owned.catalog.get('models') or []
                   if isinstance(item, dict) and (item.get('value') == 'default' and selected['id'] == 'default'
                   or item.get('resolvedModel') == selected['id'] or item.get('value') == selected['id'])), None)
    expected = (target.get('resolvedModel') or target.get('value')) if target else selected['id']
    owned.control({'subtype': 'set_model', 'model': selected['id']})
    if effort is not None:
        owned.control({'subtype': 'apply_flag_settings',
                       'settings': {'effortLevel': None if effort == 'auto' else effort}})
    settings = _applied_settings(owned)
    applied = settings['applied']
    if ((_has_resolved_models(owned.catalog) or manual_model) and applied.get('model') != expected or
            effort not in (None, 'auto') and applied.get('effort') != effort):
        raise BridgeUnavailable('Claude Code 未确认模型或思考强度设置，请刷新检查')
    sources = settings.get('sources')
    if effort == 'auto' and (not isinstance(sources, list) or
                            any(source.get('source') == 'flagSettings' and
                                source.get('settings', {}).get('effortLevel') is not None
                                for source in sources if isinstance(source, dict))):
        raise BridgeUnavailable('Claude Code 未确认默认思考强度，请刷新检查')
    owned.model = applied['model']


def _verify_creation_settings(owned, selection):
    applied = _applied_settings(owned)['applied']
    target = next((row for row in owned.catalog.get('models') or [] if isinstance(row, dict) and
                   selection['model'] in (row.get('value'), row.get('resolvedModel'))), None)
    expected = (target.get('resolvedModel') or target.get('value')) if target else selection['model']
    effort = selection.get('effort')
    if ((expected != 'default' and applied.get('model') != expected) or
            effort not in (None, 'auto') and applied.get('effort') != effort or
            effort == 'auto' and applied.get('effort') not in (None, 'auto')):
        raise ResumePreflightBlocked('新会话模型或思考强度尚未确认，首条消息未发送')
    owned.model = applied['model']

IMAGE_URL = re.compile(r'data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/]+={0,2})\Z')


def _profile():
    binary = shutil.which('claude')
    if not binary:
        binary = next((str(path) for path in (Path('/opt/homebrew/bin/claude'),
                       Path('/usr/local/bin/claude'), Path.home()/'.local/bin/claude')
                       if path.is_file() and os.access(path, os.X_OK)), None)
    if not binary:
        return None
    explicit = os.environ.get('CLAUDE_CONFIG_DIR')
    if explicit:
        directory = Path(explicit).expanduser().resolve()
        origin = 'environment:' + str(directory)
    else:
        directory = Path.home()/'.claude'
        origin = 'default:' + str(directory)
        cockpit = Path.home()/'.cockpit_tools'
        try:
            selected = json.loads((cockpit/'provider_current_accounts.json').read_text(encoding='utf-8'))['current_accounts']['claude_code_account']
            accounts = json.loads((cockpit/'claude_accounts.json').read_text(encoding='utf-8'))['accounts']
            instances = json.loads((cockpit/'claude_instances.json').read_text(encoding='utf-8'))['instances']
            if any(item.get('id') == selected for item in accounts):
                instance = next(item for item in instances if item.get('bindAccountId') == selected
                                and item.get('launchMode') == 'cli')
                candidate = Path(instance['userDataDir']).expanduser().resolve()
                if (candidate/'settings.json').is_file():
                    directory, origin = candidate, 'cockpit:' + selected + ':' + str(candidate)
        except (OSError, ValueError, KeyError, StopIteration, TypeError):
            pass
    try:
        settings_digest = hashlib.sha256((directory/'settings.json').read_bytes()).hexdigest()
    except OSError:
        settings_digest = 'none'
    return {'binary': binary, 'directory': directory, 'origin': origin + ':' + settings_digest}


def _auth_ready(profile):
    env = managed_api_env() if profile.get('managedApi') else os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = str(profile['directory'])
    try:
        process = subprocess.run([profile['binary'], 'auth', 'status', '--json'],
                                 env=env, capture_output=True, text=True, encoding='utf-8',
                                 errors='replace', timeout=5)
        return process.returncode == 0 and json.loads(process.stdout).get('loggedIn') is True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _events(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError('Claude 会话记录过大，无法读取')
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


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(item.get('text', '') for item in content
                         if isinstance(item, dict) and item.get('type') == 'text')
    return ''


def _message_content(body):
    """Build the CLI's native stream-json user content from server-resolved uploads."""
    from bridge.clients.file_mentions import file_mentions
    text = body['text']
    images = body.get('resolvedImages') or []
    files = body.get('resolvedFiles') or []
    if not images and not files:
        return file_mentions(text, body.get('resolvedReferences', []))
    if files:
        references = []
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not Path(item['path']).is_absolute() or not isinstance(item.get('name'), str):
                raise ValueError('文件附件格式无效')
            references.append({'name': item['name'], 'path': item['path']})
        text += ('\n\n' if text.strip() else '') + '请读取并参考这些已上传的本机文件：\n' + json.dumps(references, ensure_ascii=False)
    # Claude parses slash invocations from a single text block. Keep file
    # references with that text so adding an attachment preserves the Skill.
    text = file_mentions(text, body.get('resolvedReferences', []))
    blocks = [{'type': 'text', 'text': text}] if text.strip() else []
    for image in images:
        match = IMAGE_URL.fullmatch(image.get('url', '')) if isinstance(image, dict) else None
        if not match:
            raise ValueError('图片附件格式无效')
        blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': match[1], 'data': match[2]}})
    return blocks


def _saved(path, origin):
    try:
        native = str(uuid.UUID(path.stem))
        stamp = file_stamp(path)
    except (OSError, ValueError):
        return None
    title, cwd, selected_model, permission = None, '', '', None
    user_permission = None
    recent = deque(maxlen=128)
    context_events = deque(maxlen=3)
    command_chain = {}
    command_title = None
    try:
        with path.open(encoding='utf-8', errors='replace') as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
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
                        lines = _text(message.get('content')).strip().splitlines()
                        title = lines[0][:120] if lines else 'Claude Code 会话'
                    if event.get('permissionMode') in MODES:
                        user_permission = event['permissionMode']
                    if isinstance(event.get('uuid'), str):
                        recent.append({'type': 'user', 'uuid': event['uuid']})
                if kind in ('user', 'assistant') and isinstance(event.get('cwd'), str) and event['cwd']:
                    cwd = event['cwd']
                if kind == 'assistant' and isinstance(event.get('message'), dict):
                    candidate = event['message'].get('model')
                    if isinstance(candidate, str) and candidate != '<synthetic>':
                        selected_model = candidate
                if kind == 'mode' and event.get('mode') in MODES:
                    permission = event['mode']
                if (kind == 'assistant' and not any(event.get(key) for key in
                     ('isSidechain', 'parent_tool_use_id', 'isSynthetic', 'isMeta', 'isCompactSummary'))
                        and isinstance(event.get('message'), dict)
                        and isinstance(event['message'].get('usage'), dict)
                        and type(event['message']['usage'].get('input_tokens')) is int
                        and event['message']['usage']['input_tokens'] >= 0):
                    compacted = bool(context_events and context_events[0].get('subtype') == 'compact_boundary')
                    context_events.clear()
                    if compacted:
                        context_events.append({'type': 'system', 'subtype': 'compact_boundary'})
                    context_events.append({**{key: event[key] for key in ('type', 'isSidechain', 'parent_tool_use_id',
                                           'isSynthetic', 'isMeta', 'isCompactSummary') if key in event},
                                           'message': {key: event['message'][key] for key in
                                                       ('usage', 'model', 'context_window') if key in event['message']}})
                elif kind == 'system' and event.get('subtype') == 'compact_boundary':
                    context_events.clear()
                    context_events.append({'type': 'system', 'subtype': 'compact_boundary'})
                elif (kind == 'result' and context_events and
                      not any(event.get(key) for key in ('isSidechain', 'parent_tool_use_id',
                              'isSyntheticResult', 'isSynthetic', 'isMeta', 'isCompactSummary'))):
                    assistant = next((item for item in context_events if item.get('type') == 'assistant'), None)
                    model_name = (assistant or {}).get('message', {}).get('model')
                    models = event.get('modelUsage')
                    details = models.get(model_name) if isinstance(models, dict) else None
                    capacity = details.get('contextWindow') if isinstance(details, dict) else None
                    if model_name and type(capacity) is int and capacity > 0:
                        if context_events[-1].get('type') == 'result':
                            context_events.pop()
                        context_events.append({'type': 'result', 'modelUsage': {model_name: {'contextWindow': capacity}}})
    except OSError:
        return None
    try:
        final = file_stamp(path)
    except OSError:
        return None
    if final != stamp:
        return None
    if title is None:
        title = command_title
    if title is None:
        return None
    if not cwd:
        return None
    if permission is None:
        permission = user_permission
    return {'id': model.gateway_id('cli:' + origin, native), 'nativeId': native, 'backend': 'cli', 'mode': 'cli',
            'host': 'local', 'title': title, 'cwd': cwd, 'model': selected_model,
            'permissionMode': permission or 'default', 'permissionKnown': permission is not None,
            'updatedAt': stamp[3] / 1_000_000_000, 'archived': False, 'origin': origin, 'path': path,
            'stamp': stamp,
            'events': list(recent), 'contextEvents': list(context_events)}


class _Owned:
    def __init__(self, process, origin, native, cwd, *, spawn=None, control_directory=None):
        self.process, self.origin, self.native, self.cwd = process, origin, native, cwd
        self.connection_token = str(uuid.uuid4())
        self.condition = threading.Condition()
        self.responses = {}
        self.pending = {}
        self.events = []
        self.active = False
        self.model = None
        self.permission = None
        self.catalog = {}
        self.error = None
        self.preflight = None
        self.finished = False
        self.native_process = None
        self.spawn_profile = spawn
        self.control_directory = control_directory
        self.local_control = None
        self.eof_sent = False
        threading.Thread(target=self._read, daemon=True, name='claude-cli-reader').start()

    def bind_native_process(self):
        if sys.platform == 'darwin' and self.spawn_profile is not None:
            from bridge.clients.claude.owned_control import OwnedControl
            try:
                self.local_control = OwnedControl.capture(self.process, self.connection_token, self.origin,
                    self.native, self.cwd, self.spawn_profile, self.catalog, self.control_directory)
            except (OSError, ValueError):
                pass  # Keep existing task controls; unbound CLI force/exit proof stays unavailable.
            return
        if sys.platform != 'win32':
            return
        from bridge.platforms.windows import claude_process
        pid = self.catalog.get('pid')
        try:
            if self.process.poll() is not None:
                raise OSError('Claude CLI launcher exited before process binding')
            start = (claude_process.process_start_filetime(pid) if pid == self.process.pid
                     else claude_process.owned_child_start_filetime(pid, self.process.pid))
        except OSError as exc:
            raise BridgeUnavailable('无法确认 Claude Code 原生进程身份') from exc
        if start is None:
            raise BridgeUnavailable('Claude Code 原生进程已关闭')
        self.native_process = (pid, start)

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                mismatched = False
                with self.condition:
                    kind = event.get('type')
                    if kind == 'bridge_preflight':
                        if self.preflight is None:
                            self.preflight = event
                        if isinstance(event.get('cwd'), str):
                            self.cwd = event['cwd']
                        if event.get('status') == 'blocked':
                            self.error = event.get('message') or 'Claude Code 远端恢复前核验失败'
                    elif kind == 'control_response':
                        response = event.get('response') or {}
                        self.responses[response.get('request_id')] = response
                    elif kind == 'control_request' and (event.get('request') or {}).get('subtype') == 'can_use_tool':
                        self.pending[event.get('request_id')] = event
                    elif kind == 'control_cancel_request':
                        self.pending.pop(event.get('request_id'), None)
                    elif kind == 'system' and event.get('subtype') == 'init':
                        if event.get('session_id') != self.native or event.get('cwd') != self.cwd:
                            self.error = 'Claude 会话身份或工作目录不一致'
                            mismatched = True
                        self.model = event.get('model') or self.model
                        self.permission = event.get('permissionMode') or self.permission
                    elif kind == 'system' and event.get('subtype') == 'status':
                        if event.get('permissionMode') in MODES:
                            self.permission = event['permissionMode']
                    elif kind == 'system' and event.get('subtype') == 'compact_boundary':
                        self.events.append(event)
                    elif kind == 'result':
                        self.active = False
                        self.pending.clear()
                        self.events.append(event)
                    elif kind == 'assistant':
                        self.events.append(event)
                    self.condition.notify_all()
                if mismatched and (sys.platform != 'win32' or self.native_process is not None):
                    self.close()
        finally:
            with self.condition:
                self.finished = True
                self.condition.notify_all()

    def write(self, frame):
        with self.condition:
            if (self.process.poll() is not None or self.error or self.eof_sent
                    or self.local_control and self.local_control.intent()):
                raise BridgeUnavailable(self.error or 'Claude Code 会话已关闭，请重新继续')
            try:
                self.process.stdin.write(json.dumps(frame, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                raise BridgeUnavailable('Claude Code 写入结果不明，请检查会话') from None

    def control(self, request, timeout=20):
        rid = str(uuid.uuid4())
        self.write({'type': 'control_request', 'request_id': rid, 'request': request})
        deadline = time.monotonic() + timeout
        with self.condition:
            while rid not in self.responses and self.process.poll() is None and time.monotonic() < deadline:
                self.condition.wait(max(0, deadline-time.monotonic()))
            response = self.responses.pop(rid, None)
        if response and response.get('subtype') == 'error':
            raise ClaudeControlRejected(response.get('error'))
        if not response or response.get('subtype') != 'success':
            raise BridgeUnavailable('Claude Code 控制请求未确认，请检查原会话')
        return response.get('response') or {}

    def release_for_archive(self, timeout=20):
        return self.release_connection(stop=True, timeout=timeout)

    def release_connection(self, stop=False, timeout=20):
        """Finish this owned CLI only; never terminate a foreign or shared process."""
        deadline = time.monotonic() + timeout
        control = getattr(self, 'local_control', None)
        if getattr(self, 'eof_sent', False) or control and control.intent():
            return self.check_exit()
        if self.process.poll() is not None:
            return self.check_exit() if getattr(self, 'spawn_profile', None) is not None else None
        with self.condition:
            if self.error:
                raise BridgeUnavailable(self.error)
            active = self.active or bool(self.pending)
        if active and not stop:
            return {'status': 'waiting'}
        if active:
            self.control({'subtype': 'interrupt'}, timeout=max(.1, deadline-time.monotonic()))
        with self.condition:
            while (self.active or self.pending) and not self.finished and time.monotonic() < deadline:
                self.condition.wait(max(0, deadline-time.monotonic()))
            if self.error or self.active or self.pending:
                raise BridgeUnavailable('停止结果待核对，连接尚未释放')
        # EOF lets the original executor flush and exit; no terminate/kill fallback.
        self.eof_sent = True
        self.process.stdin.close()
        try:
            code = self.process.wait(timeout=max(.1, deadline-time.monotonic()))
            # Native print mode exits 1 after an acknowledged user interruption.
            if code != 0 and not (active and code == 1):
                raise BridgeUnavailable('原会话未正常退出，连接释放结果待核对')
        except subprocess.TimeoutExpired:
            raise BridgeUnavailable('原会话连接尚未释放，请核对原会话') from None
        return self.check_exit() if getattr(self, 'spawn_profile', None) is not None else {'status': 'released', 'notice': '连接已释放，可在电脑端继续'}

    def check_exit(self):
        if getattr(self, 'local_control', None) is not None:
            return self.local_control.check()
        return {'status': 'unknown', 'notice': '原 Claude CLI 的原生退出身份尚未核验，请在电脑端检查。'}

    def close(self):
        try:
            if self.native_process is not None:
                from bridge.platforms.windows import claude_process
                pid, start = self.native_process
                claude_process.terminate_matching_process(pid, start)
                self.native_process = None
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)


class ClaudeCli:
    def __init__(self, upload_root=None, profile_resolver=None, control_directory=None):
        self.profile_resolver = profile_resolver or (lambda: _profile())
        self.lock = threading.RLock()
        self.upload_root = Path(upload_root) if upload_root is not None else None
        self.control_directory = Path(control_directory) if control_directory is not None else None
        self.owned = {}
        self.index = {}
        self.auth_cache = None
        self.auth_refreshing = False
        self.closed = False

    def _ready(self, profile, *, fresh=False):
        if self.closed or not profile:
            return False
        now = time.time()
        with self.lock:
            cached = self.auth_cache
            if not fresh and cached and cached[0] == profile['origin'] and cached[1] > now:
                return cached[2]
        ready = _auth_ready(profile)
        with self.lock:
            self.auth_cache = (profile['origin'], now + 15, ready)
        return ready

    def status(self):
        if self.closed:
            return {'cliAvailable': False, 'cliAuthReady': False}
        profile = self.profile_resolver()
        if not profile:
            return {'cliAvailable': False, 'cliAuthReady': False}
        with self.lock:
            cached = self.auth_cache
            ready = bool(cached and cached[0] == profile['origin'] and cached[1] > time.time() and cached[2])
            if (not cached or cached[0] != profile['origin'] or cached[1] <= time.time()) and not self.auth_refreshing:
                self.auth_refreshing = True
                threading.Thread(target=self._refresh_auth_status, args=(profile,), daemon=True,
                                 name='claude-cli-auth-status').start()
        return {'cliAvailable': True, 'cliAuthReady': ready}

    def _refresh_auth_status(self, profile):
        try:
            self._ready(profile)
        finally:
            with self.lock:
                self.auth_refreshing = False

    def cli_connections(self):
        return owned_connections(self)

    def cli_force_callbacks(self, row):
        if sys.platform != 'darwin' or row.get('provider') != 'claude' or row.get('host') != 'local':
            return {}
        from bridge.clients.claude.owned_control import OwnedControl
        with self.lock:
            control = next((owned.local_control for owned in self.owned.values()
                            if getattr(owned, 'connection_token', None) == row.get('key')
                            and getattr(owned, 'local_control', None) is not None), None)
        if control is None:
            control = OwnedControl.restore(self.control_directory, row)
        if control is None or any(control.binding.get(name) != row.get(name)
                                  for name in ('key', 'provider', 'host', 'sessionId')):
            return {}
        def completed(result):
            if result['status'] == 'released':
                with self.lock:
                    for native, owned in list(self.owned.items()):
                        if getattr(owned, 'connection_token', None) == row['key']:
                            self.owned.pop(native)
            return result
        return {**control.configuration(), 'canForce': control.durable, '_force_preview': control.preview,
                '_force': lambda operation, generation: completed(control.force(operation, generation)),
                '_check': lambda: completed(control.check())}

    def busy(self):
        with self.lock:
            for owned in self.owned.values():
                if owned.process.poll() is None:
                    with owned.condition:
                        if owned.active or owned.pending:
                            return True
            return False

    def listing(self):
        profile = self.profile_resolver()
        if not profile:
            return {'connected': False, 'sessions': []}
        rows = {}
        root = profile['directory']/'projects'
        if root.is_dir():
            for project in root.iterdir():
                if not project.is_dir() or project.is_symlink():
                    continue
                for path in project.glob('*.jsonl'):
                    if path.is_symlink():
                        continue
                    row = self._saved_cached(path, profile['origin'])
                    if row:
                        rows[row['id']] = row
        archive_errors = []
        for path in file_archive.archive_paths(profile['directory'], archive_errors):
            row = self._saved_cached(path, profile['origin'])
            if row:
                rows[row['id']] = row
        for error in archive_errors:
            native = error['nativeId']
            sid = model.gateway_id('cli:' + profile['origin'], native)
            rows[sid] = {'id': sid, 'nativeId': native, 'backend': 'cli', 'mode': 'cli',
                         'host': 'local', 'title': error['notice'], 'cwd': '', 'model': '',
                         'permissionMode': 'default', 'permissionKnown': False, 'updatedAt': 0,
                         'archived': True, 'archiveIncomplete': True, 'origin': profile['origin'],
                         'path': profile['directory']/'bridge-archives'/native/(native+'.jsonl'),
                         'stamp': None, 'events': [], 'contextEvents': []}
        env = managed_api_env() if profile.get('managedApi') else os.environ.copy()
        env['CLAUDE_CONFIG_DIR'] = str(profile['directory'])
        enabled = file_archive.supported(profile['binary'], env)
        for row in rows.values():
            allowed = enabled and not row.get('archiveIncomplete')
            row['capabilities'] = {'archive': allowed, 'unarchive': allowed}
        with self.lock:
            self.index = rows
            public = [self._public(row) for row in rows.values()]
        return {'connected': True, 'sessions': public, 'complete': not archive_errors}

    def _public(self, row):
        value = {key: item for key, item in row.items() if key not in ('events', 'contextEvents', 'stamp', 'path', 'origin', 'permissionKnown')}
        value.update(context_fields(row['contextEvents']))
        owned = self.owned.get(row['nativeId'])
        live = owned and owned.origin == row['origin'] and owned.process.poll() is None and not owned.error
        if live:
            with owned.condition:
                pending = dict(owned.pending)
                active = owned.active
        else:
            pending, active = {}, False
        awaiting = bool(pending)
        value.update(status='active' if active else 'idle', runtimeKnown=True,
                     connectionState='awaiting' if awaiting else 'active' if active else 'ready' if live else 'closed',
                     requests=[{'id': rid, 'tool': event['request'].get('tool_name', '工具'),
                                'input': event['request'].get('input', {}),
                                'needsInput': event['request'].get('tool_name') == 'AskUserQuestion'}
                               for rid, event in pending.items()])
        value['capabilities'] = {**value.get('capabilities', {}), 'rename': bool(live and not row.get('archived') and
                                 not row.get('archiveIncomplete') and _supports_rename(owned.catalog))}
        if live:
            value['model'] = owned.model or value['model']
            value['permissionMode'] = owned.permission or value['permissionMode']
        if row.get('archiveIncomplete'):
            value.update(status='unknown', runtimeKnown=False, connectionState='unavailable')
        return value

    def _get(self, sid):
        profile = self.profile_resolver()
        with self.lock:
            cached = self.index.get(sid)
        row = None
        if cached and profile and cached['origin'] == profile['origin'] and not cached['path'].is_symlink():
            row = self._saved_cached(cached['path'], profile['origin'])
            if row:
                with self.lock:
                    self.index[sid] = row
        else:
            self.listing()
            with self.lock:
                row = self.index.get(sid)
        if row is None:
            raise ValueError('找不到 Claude Code 会话或原账号配置已切换')
        return row

    def _saved_cached(self, path, origin):
        try:
            stamp = file_stamp(path)
        except OSError:
            return None
        sid = model.gateway_id('cli:' + origin, path.stem)
        with self.lock:
            cached = self.index.get(sid)
        if cached and cached['origin'] == origin and cached.get('stamp') == stamp and cached['path'] == path:
            return cached
        row = _saved(path, origin)
        profile = self.profile_resolver()
        if row and profile and profile['origin'] == origin:
            if path.parent.parent == profile['directory']/'bridge-archives':
                row.update(file_archive.state(profile['directory'], path, row['nativeId']))
            row['capabilities'] = cached.get('capabilities', {}) if cached else {}
        return row

    def contains(self, sid):
        profile = self.profile_resolver()
        with self.lock:
            row = self.index.get(sid)
        if row and profile and row['origin'] == profile['origin'] and not row['path'].is_symlink() and row['path'].is_file():
            return True
        self.listing()
        return sid in self.index

    def create(self, cwd, origin, message, directory_identity=None, account_identity=None, selection=None):
        profile = self.profile_resolver()
        if not profile or profile['origin'] != origin or not self._ready(profile, fresh=True):
            raise BridgeUnavailable('原 Claude Code 账号配置已变化或不可用')
        if account_identity is not None and project_account_identity(profile) != account_identity:
            raise BridgeUnavailable('原 Claude Code 账号配置已变化或不可用')
        project = Path(cwd).resolve(strict=True)
        if not project.is_dir():
            raise ValueError('原项目目录不可用')
        if directory_identity is not None:
            stat = project.stat()
            if directory_identity != {'cwd': str(project), 'device': stat.st_dev, 'inode': stat.st_ino}:
                raise ValueError('项目目录已变化，请重新选择目录')
        native = str(uuid.uuid4())
        sid = model.gateway_id('cli:' + origin, native)
        env = managed_api_env() if profile.get('managedApi') else os.environ.copy()
        env['CLAUDE_CONFIG_DIR'] = str(profile['directory'])
        command = [profile['binary'], '-p', '--session-id', native, '--input-format', 'stream-json',
                   '--output-format', 'stream-json', '--verbose', '--replay-user-messages',
                   '--setting-sources', 'user,project,local', '--permission-prompt-tool', 'stdio',
                   '--allow-dangerously-skip-permissions']
        arguments, environment = creation_launch_options(selection)
        command.extend(arguments)
        for name, value in environment.items():
            if value is None: env.pop(name, None)
            else: env[name] = value
        if self.upload_root is not None:
            upload_dir = self.upload_root/DesktopUploads.scope('claude', sid)
            upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.upload_root.is_symlink() or upload_dir.is_symlink():
                raise ValueError('Claude Code 附件目录无效')
            command.extend(('--add-dir', str(upload_dir.resolve())))
        fresh = self.profile_resolver()
        if not fresh or fresh['origin'] != origin:
            raise BridgeUnavailable('原 Claude Code 账号配置已变化或不可用')
        if account_identity is not None and project_account_identity(fresh) != account_identity:
            raise BridgeUnavailable('原 Claude Code 账号配置已变化或不可用')
        from bridge.clients.claude.owned_control import spawn_profile
        spawn = spawn_profile(profile)
        process = subprocess.Popen(command, cwd=str(project), env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                   encoding='utf-8', errors='replace', bufsize=1)
        owned = _Owned(process, origin, native, str(project), spawn=spawn, control_directory=self.control_directory)
        try:
            owned.catalog = owned.control({'subtype': 'initialize', 'hooks': None}, timeout=30)
            owned.bind_native_process()
            if owned.error:
                raise BridgeUnavailable(owned.error)
            owned.permission = owned.catalog.get('current_permission_mode')
            if selection:
                # Initialize and verify the native settings before any user input.
                _verify_creation_settings(owned, selection)
            with self.lock:
                self.owned[native] = owned
            message_id = str(uuid.uuid4())
            with owned.condition:
                owned.active = True
                owned.write({'type': 'user', 'uuid': message_id,
                             'message': {'role': 'user', 'content': message}})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self.listing()
                with self.lock:
                    row = self.index.get(sid)
                    if row and any(event.get('type') == 'user' and event.get('uuid') == message_id
                                   for event in row['events']):
                        return {'status': 'ready', 'result': {'id': sid}}
                with owned.condition:
                    if owned.error or owned.finished:
                        break
                time.sleep(.1)
            raise BridgeUnavailable('Claude Code 新建消息结果不明，请检查会话列表')
        except Exception:
            if native not in self.owned:
                owned.close()
            raise

    def call(self, action, sid, body):
        try:
            row = self._get(sid)
        except (ValueError, BridgeUnavailable) as exc:
            if action == 'resume':
                raise ResumePreflightBlocked(str(exc)) from exc
            raise
        native = row['nativeId']
        if action == 'image':
            return model.history_image([image_event(row['path'], body.get('source'))], body.get('source'))
        with self.lock:
            owned = self.owned.get(native)
            if owned and owned.error and owned.process.poll() is None:
                raise (ResumePreflightBlocked if action == 'resume' else BridgeUnavailable)(owned.error)
            if owned and owned.origin != row['origin'] and owned.process.poll() is None:
                raise (ResumePreflightBlocked if action == 'resume' else BridgeUnavailable)(
                    '此会话已由另一 Claude Code 配置继续，请先结束该连接')
            if owned and (owned.origin != row['origin'] or owned.process.poll() is not None):
                owned = None
            public = self._public(row)
            live = owned is not None
            if action in ('archive-state', 'archive'):
                profile = self.profile_resolver()
                if not profile or profile['origin'] != row['origin']:
                    raise BridgeUnavailable('原 Claude Code 配置已变化')
                env = managed_api_env() if profile.get('managedApi') else os.environ.copy()
                env['CLAUDE_CONFIG_DIR'] = str(profile['directory'])
                enabled = file_archive.supported(profile['binary'], env)
                safe = False
                if enabled and live and action == 'archive' and body.get('disconnect') is True and body.get('archived') is True:
                    owned.release_for_archive()
                    live = False
                    public = self._public(row)
                if enabled and not live:
                    assert_session_closed(profile['directory'])
                    file_archive.assert_no_cli(profile['binary'])
                    safe = True
                result = {**public, **file_archive.state(profile['directory'], row['path'], native, verify=True),
                          'archiveSafe': safe, 'runtimeKnown': True,
                          'capabilities': {'archive': enabled, 'unarchive': enabled}}
                if action == 'archive-state':
                    return result
                if not safe:
                    raise BridgeUnavailable('无法确认原 Claude Code 会话已停止或原生归档能力未验证')
                if self.profile_resolver() != profile:
                    raise BridgeUnavailable('原 Claude Code 配置已变化')
                assert_session_closed(profile['directory'])
                file_archive.assert_no_cli(profile['binary'])
                if not file_archive.supported(profile['binary'], env):
                    raise BridgeUnavailable('Claude 原生程序在归档前变化或无法核验')
                value = file_archive.change(profile['directory'], row['path'], native, body.get('archived'))
                self.index.pop(sid, None)
                return value
            if action == 'detail':
                before = body.get('historyBefore')
                if before is not None and (not isinstance(before, str) or len(before) > 1024):
                    raise ValueError('Claude 历史游标无效')
                limit = body.get('historyLimit', 40)
                history_events, cursor, has_more = history_page(
                    row['path'], row['id'] + ':' + row['origin'], before, limit)
                if live:
                    with owned.condition:
                        live_events = list(owned.events)
                else:
                    live_events = []
                events = merge_live_page(history_events, live_events) if before is None else history_events
                public.update(context_fields(row['contextEvents'] + live_events))
                messages = model.transcript(events)
                unique = {item['id']: item for item in messages}
                can_resume = (not row['archived'] and not live and row['permissionKnown'] and self._ready(self.profile_resolver())
                              and Path(row['cwd']).is_dir())
                return {'connected': True, 'session': public, 'messages': list(unique.values()),
                        'turns': model.turns(events),
                        'history': {'before': cursor, 'hasMore': has_more},
                        'notice': '无法确认原会话权限模式，请在电脑端继续' if not live and not row['permissionKnown'] else '',
                        'capabilities': {
                            'resume': can_resume,
                            'send': live and public['connectionState'] == 'ready',
                            'models': live, 'permissions': live, 'stop': live,
                            'compact': live and not owned.active and not owned.pending and _supports_compact(owned.catalog),
                            'attachments': True, 'queue': False, 'steer': False, 'skills': live,
                            **row.get('capabilities', {}), 'rename': public['capabilities']['rename']}}
            if action == 'catalog':
                if not live:
                    return {'models': [], 'capabilities': {'models': False}, 'notice': '请先继续这条 Claude Code 会话'}
                return _cli_catalog(owned, public, body.get('refresh') is True)
            if action == 'access' and 'mode' not in body:
                options = [{'value': mode, 'label': label, 'description': description}
                           for mode, label, description in PERMISSION_OPTIONS]
                return {'status': 'accepted', 'mode': public['permissionMode'],
                        'options': options if live else []}
            if action == 'resume':
                if row['archived']:
                    raise ResumePreflightBlocked('请先恢复这条已归档的 Claude Code 会话')
                if live:
                    return {'status': 'ready'}
                profile = self.profile_resolver()
                if not profile or profile['origin'] != row['origin'] or not self._ready(profile, fresh=True):
                    raise ResumePreflightBlocked('原 Claude Code 账号配置已变化或不可用')
                if not row['permissionKnown'] or row['permissionMode'] not in MODES:
                    raise ResumePreflightBlocked('无法确认原会话权限模式，请在电脑端继续')
                try:
                    cwd = str(Path(row['cwd']).resolve(strict=True))
                except OSError as exc:
                    raise ResumePreflightBlocked('原项目目录不可用，请在电脑端检查') from exc
                env = managed_api_env() if profile.get('managedApi') else os.environ.copy()
                env['CLAUDE_CONFIG_DIR'] = str(profile['directory'])
                command = [profile['binary'], '-p', '--resume', native, '--input-format', 'stream-json',
                           '--output-format', 'stream-json', '--verbose', '--replay-user-messages',
                           '--setting-sources', 'user,project,local', '--permission-mode', row['permissionMode'],
                           '--permission-prompt-tool', 'stdio', '--allow-dangerously-skip-permissions']
                if self.upload_root is not None:
                    upload_dir = self.upload_root/DesktopUploads.scope('claude', sid)
                    upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if self.upload_root.is_symlink() or upload_dir.is_symlink():
                        raise ResumePreflightBlocked('Claude Code 附件目录无效')
                    command.extend(('--add-dir', str(upload_dir.resolve())))
                if row['model']:
                    command.extend(('--model', row['model']))
                try:
                    assert_session_closed(profile['directory'], native)
                except BridgeUnavailable as exc:
                    raise ResumePreflightBlocked(str(exc)) from exc
                from bridge.clients.claude.owned_control import spawn_profile
                spawn = spawn_profile(profile)
                process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                           encoding='utf-8', errors='replace', bufsize=1)
                owned = _Owned(process, profile['origin'], native, cwd, spawn=spawn, control_directory=self.control_directory)
                try:
                    owned.catalog = owned.control({'subtype': 'initialize', 'hooks': None}, timeout=30)
                    owned.bind_native_process()
                    if owned.error:
                        raise BridgeUnavailable(owned.error)
                    owned.permission = owned.catalog.get('current_permission_mode') or row['permissionMode']
                    if owned.permission != row['permissionMode']:
                        raise BridgeUnavailable('Claude Code 未保留原会话权限模式')
                except Exception:
                    owned.close()
                    raise
                self.owned[native] = owned
                return {'status': 'ready'}
            if not live:
                raise BridgeUnavailable('请先继续这条 Claude Code 会话')
            if action == 'rename':
                return _rename_cli(owned, row, body.get('title'), lambda: self._get(sid))
            if action == 'send':
                if body.get('mode') in ('queue', 'steer'):
                    raise ValueError('Claude Code CLI 暂不支持此发送选项')
                selected = body.get('plugins') or []
                if not isinstance(selected, list) or len(selected) > 1:
                    raise ValueError('Claude Code CLI 每条消息只能调用一个 Skill')
                if selected:
                    offered = {skill['id'] for skill in _skill_catalog(owned.catalog)
                               if skill['selectable']}
                    if selected[0] not in offered:
                        raise ValueError('技能已不可用，请刷新后选择')
                    message = '/' + selected[0] + (' ' + body['text'] if body['text'].strip() else '')
                else:
                    message = body['text']
                content = _message_content({**body, 'text': message})
                with owned.condition:
                    if owned.active or owned.pending:
                        raise BridgeUnavailable('请等待当前 Claude Code 任务结束后再发送')
                    owned.active = True
                    owned.write({'type': 'user', 'message': {'role': 'user', 'content': content}})
            elif action == 'compact':
                _compact_cli(owned, body['id'])
            elif action == 'stop':
                owned.control({'subtype': 'interrupt'})
            elif action == 'settings':
                _set_cli_settings(owned, body)
            elif action == 'access':
                mode = body.get('mode')
                if mode not in MODES:
                    raise ValueError('请选择 Claude Code 提供的权限模式')
                if mode == 'bypassPermissions' and body.get('confirmed') is not True:
                    raise ValueError('完全访问需要明确确认')
                result = owned.control({'subtype': 'set_permission_mode', 'mode': mode})
                if result.get('mode') != mode:
                    raise BridgeUnavailable('Claude Code 未确认权限模式')
                with owned.condition:
                    owned.permission = mode
            elif action == 'respond':
                rid = body.get('requestId')
                with owned.condition:
                    pending = owned.pending.get(rid)
                if not pending:
                    raise ValueError('请求已处理或过期')
                decision = {'accept': 'allow', 'decline': 'deny'}.get(body.get('decision'))
                if not decision:
                    raise ValueError('请选择本次允许或拒绝')
                response = {'behavior': decision}
                if decision == 'allow':
                    updated = pending['request'].get('input', {})
                    if pending['request'].get('tool_name') == 'AskUserQuestion':
                        updated = dict(updated)
                        answers = {}
                        for index, question in enumerate(updated.get('questions') or []):
                            key = str(question.get('id') or question.get('question') or index)
                            values = ((body.get('answers') or {}).get(key) or {}).get('answers') or []
                            if not values or not all(isinstance(value, str) for value in values):
                                raise ValueError('请回答每一项问题')
                            answers[question['question']] = ', '.join(values)
                        updated['answers'] = answers
                    response['updatedInput'] = updated
                else:
                    response['message'] = '用户拒绝了本次操作'
                # Claim before a potentially ambiguous pipe write. A new HTTP request ID
                # must never replay an approval whose result may already be applied.
                with owned.condition:
                    owned.pending.pop(rid, None)
                owned.write({'type': 'control_response', 'response': {'subtype': 'success',
                             'request_id': rid, 'response': response}})
            else:
                raise ValueError('不支持的 Claude Code 操作')
            return {'status': 'accepted'}

    def close(self):
        with self.lock:
            self.closed = True
            for owned in self.owned.values():
                owned.close()
            self.owned.clear()


def owned_connections(client, remote=False):
    """Local, SSH and WSL share the same owned print-session shutdown path."""
    with client.lock:
        rows = []
        for sid, row in client.index.items():
            owned = client.owned.get(sid if remote else row['nativeId'])
            if not owned or owned.origin != row['origin'] or owned.process.poll() is not None:
                continue
            token = owned.connection_token
            def release(stop, owned=owned, sid=sid, native=row['nativeId'], token=token):
                with client.lock:
                    if client.owned.get(sid if remote else native) is not owned or owned.connection_token != token:
                        raise BridgeUnavailable('原 CLI 连接已变化；未操作新的连接')
                    result = owned.release_connection(stop)
                    if result and result['status'] == 'released':
                        client.owned.pop(sid if remote else native, None)
                    return result or {'status': 'released'}
            public = {'key': token, 'provider': 'claude', 'sessionId': sid,
                         'host': row.get('host', 'local'), 'title': row.get('title') or sid,
                         'location': row.get('hostLabel') or row.get('host', '本机 CLI'), '_release': release}
            if not remote:
                public.update(getattr(client, 'cli_force_callbacks', lambda _row: {})(public))
            rows.append(public)
        return rows
