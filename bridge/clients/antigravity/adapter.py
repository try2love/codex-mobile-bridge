"""Antigravity history, original-server writes and explicitly enabled hooks."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

from bridge.clients.errors import BridgeUnavailable, ResumePreflightBlocked
from bridge.clients.file_mentions import file_mentions
from bridge.features.sessions.create import ArchiveNotSent
from bridge.features.workspace.uploads import MAX_FILE, MAX_FILES, MAX_TOTAL, image_type
from bridge.platforms.antigravity import profile_lease
from .cli_session import AntigravityCli
from .hook_ipc import HookIPCServer
from .model import COMPOSER_PRESENTATION, PERMISSION_OPTIONS, SURFACE_LABELS, gateway_id
from .setup import disable_plugin, ensure_plugin, inspect_plugin, plugin_directory
from .store import AntigravityStore, read_annotation_pbtxt

_UNSET = object()
_SLASH_PREFIX_RE = re.compile(r'^/([A-Za-z0-9_-]+)(?:$|([ \t\r\n][\s\S]*))')
_KNOWN_NATIVE_SYSTEM_COMMANDS = frozenset({
    'goal', 'compact', 'schedule', 'browser', 'plan', 'grill-me', 'teamwork', 'learn', 'boost',
})
_BUSY_STRUCTURED_ERROR = '原会话运行中，排队或插话暂不支持 /指令 与 Skill，请等待当前回复结束后直接发送'


_DATA_IMAGE_URL_RE = re.compile(r'^data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)$')


def _parse_slash_prefix(text):
    if not isinstance(text, str):
        return None, ''
    match = _SLASH_PREFIX_RE.match(text)
    if not match:
        return None, text
    return match.group(1), match.group(2) or ''


class Antigravity:
    concurrent_stop = True

    def __init__(self, directory, home=None, model_catalog=None):
        self.directory = Path(directory).resolve()
        self.home = Path(home or Path.home()).expanduser().resolve()
        self.store = AntigravityStore(self.directory/'scratch', home=self.home)
        self.cli = AntigravityCli(self.home, model_catalog=model_catalog)
        self.hooks = None
        self._lease = None
        self._lock = threading.RLock()
        self.settings_file = self.directory/'session-settings.json'
        try:
            value = json.loads(self.settings_file.read_text(encoding='utf-8'))
            self._settings = value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            self._settings = {}

    def _hook_session(self, payload):
        cid, transcript = payload.get('conversationId'), payload.get('transcriptPath')
        if not isinstance(cid, str) or not isinstance(transcript, str):
            return None
        try:
            transcript_path = Path(transcript).expanduser().resolve()
        except OSError:
            return None
        candidates = [row for row in self.store.list_sessions() if row['nativeId'] == cid]
        for row in candidates:
            expected = Path(row['surfaceRoot'])/'brain'/cid/'.system_generated/logs/transcript.jsonl'
            if transcript_path == expected.resolve():
                return row['id']
        if not candidates and re.fullmatch(r'[0-9a-fA-F-]{36}', cid):
            for surface in self.store.surfaces():
                conv_dir = Path(surface['root'])/'brain'/cid
                if conv_dir.is_symlink():
                    continue
                expected = conv_dir/'.system_generated/logs/transcript.jsonl'
                if transcript_path == expected.resolve():
                    return gateway_id(surface['key'], cid)
        return None

    def ensure_plugin(self):
        with self._lock:
            if self._lease is None:
                self._lease = profile_lease(plugin_directory(self.home)/'gateway.lock')
            if self.hooks is None:
                self.hooks = HookIPCServer(self.directory, session_resolver=self._hook_session)
            try:
                return ensure_plugin(self.directory, self.home)
            except Exception:
                self.hooks.close()
                self.hooks = None
                self._lease.close()
                self._lease = None
                raise

    def disable_plugin(self):
        with self._lock:
            if self._lease is None:
                self._lease = profile_lease(plugin_directory(self.home)/'gateway.lock')
            result = disable_plugin(self.directory, self.home)
            if self.hooks:
                self.hooks.close()
                self.hooks = None
            self._lease.close()
            self._lease = None
            return result

    def status(self):
        surfaces = self.store.surfaces()
        live = any(self.cli.available(surface) for surface in SURFACE_LABELS)
        return {'installed': bool(surfaces) or live, 'configured': bool(surfaces) or live, 'connected': live,
                'historyAvailable': bool(surfaces),
                'reason': '原生服务已连接' if live else ('可读取本机历史；实时操作需要电脑上相应的 Antigravity 原生服务' if surfaces else '原生服务尚未连接；请在电脑打开原客户端'),
                'pluginEnabled': bool(self.hooks)}

    def account_scope(self, surface=None):
        if surface is not None:
            if surface not in SURFACE_LABELS:
                raise ValueError('Antigravity 来源无效')
            status = self.cli.user_status(surface)
            identity = status.get('email') or status.get('userId')
            if not isinstance(identity, str) or not identity:
                raise BridgeUnavailable('请先在对应 Antigravity 原客户端登录')
            return hashlib.sha256(identity.encode()).hexdigest()
        accounts = []
        for surface in SURFACE_LABELS:
            try:
                status = self.cli.user_status(surface)
            except (OSError, ValueError, BridgeUnavailable):
                continue
            identity = status.get('email') or status.get('userId')
            if identity:
                accounts.append((surface, identity))
        return hashlib.sha256(json.dumps(accounts, sort_keys=True).encode()).hexdigest() if accounts else ''

    def accounts(self):
        """Project native identities into the shared account panel, without reading credentials."""
        accounts, notices = [], []
        for surface, label in SURFACE_LABELS.items():
            if not self.cli.available(surface):
                notices.append(label+'：原生服务未连接')
                continue
            try:
                user = self.cli.user_status(surface)
            except (OSError, ValueError, BridgeUnavailable):
                notices.append(label+'：账号状态暂不可用')
                continue
            identity = user.get('email') or user.get('userId')
            if not isinstance(identity, str) or not identity:
                notices.append(label+'：尚未登录')
                continue
            identifier = surface+':'+hashlib.sha256(identity.encode()).hexdigest()
            accounts.append({'id': identifier, 'surface': surface,
                             'name': label+' · '+(user.get('name') or identity),
                             'email': user.get('email') or '', 'kind': 'official',
                             'saved': False, 'canReadQuota': False})
        return {'accounts': accounts, 'activeIds': [row['id'] for row in accounts],
                'activeId': accounts[0]['id'] if accounts else None,
                'current': {'name': ' / '.join(row['name'] for row in accounts)},
                'readOnly': True, 'canImport': False, 'canSwitch': False,
                'capabilities': {'addApi': False, 'quota': False},
                'message': '；'.join(notices),
                'enrollmentHint': 'APP、CLI、IDE 的登录状态分别读取。请在对应原客户端登录或切换账号，再刷新此页。'}

    def project_environments(self):
        return [{'surface': row['key'], 'host': 'local', 'hostLabel': '此电脑', 'name': row['name'],
                 'scope': str(row['root']), 'createRequiresMessage': True, 'createTitle': True} for row in self.store.surfaces()]

    def _waiting_state(self, surface, *, live=True, fresh=False, expected_identity=None):
        if not live:
            return {}, set()
        if fresh:
            return self.cli.waiting_state_by_cid(surface=surface, max_age=0,
                                                **({'expected_identity': expected_identity} if expected_identity is not None else {}))
        try:
            return self.cli.waiting_state_by_cid(surface=surface)
        except (OSError, ValueError, BridgeUnavailable):
            return {}, set()

    def _session(self, row, *, live=None, waiting_state=None):
        key = row['id']
        cid = row['nativeId']
        surface = row['surfaceDirKey']
        if live is None:
            live = self.cli.available(surface)
        waiting_by_cid, active_cids = waiting_state if waiting_state is not None else self._waiting_state(surface, live=live)
        hook_requests = self.hooks.pending_approvals_for(key) if self.hooks else []
        native_requests = list(waiting_by_cid.get(cid) or [])
        requests = hook_requests + native_requests
        hooked = bool(self.hooks and self.hooks.has_recent_heartbeat(key))
        active = row.get('status') == 'active' or hooked or (cid in active_cids) or bool(requests)
        permission = self.hooks.permission_mode(key) if self.hooks else 'desktop'
        work_mode = self._settings.get(key, {}).get('workMode')
        collaboration_mode = work_mode if (live and work_mode in ('default', 'plan')) else 'default'
        saved_settings = self._settings.get(key, {})
        raw_model = saved_settings.get('model') or ''
        raw_effort = saved_settings.get('effort') or ''
        proj_model, proj_effort, display_name = self.cli.resolve_model_display(surface, raw_model, raw_effort)
        title = row.get('title')
        archived = bool(row.get('archived'))
        if live:
            ann = self.cli.annotations_for(cid, surface=surface)
            if ann.get('title'):
                title = ann['title']
            if isinstance(ann.get('archived'), bool):
                archived = ann['archived']
        return {**row, 'title': title, 'archived': archived,
                'status': 'active' if active else 'idle', 'requests': requests,
                'model': proj_model, 'effort': proj_effort,
                **({'modelDisplayName': display_name} if display_name else {}),
                'collaborationMode': collaboration_mode,
                'permissionMode': permission,
                'permissionLabel': next(option['label'] for option in PERMISSION_OPTIONS if option['value'] == permission),
                'composerCapabilities': dict(COMPOSER_PRESENTATION),
                'connectionState': 'ready' if live else 'detached', 'readOnly': not live,
                'capabilities': {'send': live, 'stop': live, 'models': live, 'effort': live, 'manualModels': live, 'manualEffort': False,
                                 'permissions': bool(self.hooks), 'attachments': live, 'uploadFiles': live,
                                 'queue': hooked, 'steer': hooked, 'skills': live, 'multiSkills': live,
                                 'slashCommands': live, 'compact': live, 'rename': live, 'fork': live, 'revert': live, 'tasks': True,
                                 'plan': live, 'goal': live, 'archive': live, 'unarchive': live}}

    def management(self):
        """Inspect each native source and existing per-session settings without starting it."""
        found = {row['key']: row for row in self.store.surfaces()}
        live = {surface: self.cli.available(surface) for surface in SURFACE_LABELS}
        waiting = {surface: self._waiting_state(surface, live=live[surface]) for surface in SURFACE_LABELS}
        sessions = [self._session(row, live=live[row['surfaceDirKey']], waiting_state=waiting[row['surfaceDirKey']])
                    for row in self.store.list_sessions()]
        return {'surfaces': [
            {'id': surface, 'name': name, 'historyAvailable': surface in found,
             'connected': live[surface],
             'sessions': [row for row in sessions if row['surfaceDirKey'] == surface]}
            for surface, name in SURFACE_LABELS.items()],
            'plugin': {**inspect_plugin(self.directory, self.home), 'active': bool(self.hooks)},
            'permissionOptions': PERMISSION_OPTIONS}

    def record_path(self, sid):
        from bridge.features.workspace.workspace import Workspace
        row = self.store.resolve_session(sid)
        if row is None:
            raise ValueError('找不到 Antigravity 聊天')
        workspace = Workspace(row['surfaceRoot'])
        logs = 'brain/' + row['nativeId'] + '/.system_generated/logs/'
        for relative in (logs+'transcript.jsonl', logs+'transcript_full.jsonl', 'conversation_summaries.db'):
            try:
                info = workspace.info(relative)
                if info['kind'] == 'file':
                    return info['absolutePath']
            except (OSError, ValueError, PermissionError):
                continue
        return None

    @staticmethod
    def _build_media(body):
        """Convert data-URL resolvedImages into native SendUserCascadeMessage Media items."""
        media = []
        fallback_paths = []
        images = body.get('resolvedImages') or []
        if len(images) > MAX_FILES:
            raise ValueError('每条消息最多添加 10 个不同附件')
        total = 0
        for idx, row in enumerate(images):
            if not isinstance(row, dict):
                continue
            url = row.get('url')
            if isinstance(url, str):
                match = _DATA_IMAGE_URL_RE.match(url.strip())
                if not match:
                    raise ValueError('图片附件格式无效，请重新上传')
                mime = match.group(1).lower()
                raw_b64 = re.sub(r'\s+', '', match.group(2))
                if len(raw_b64) > 4 * ((MAX_FILE + 2) // 3):
                    raise ValueError('每个附件需为 1 字节至 20 MB')
                try:
                    decoded = base64.b64decode(raw_b64, validate=True)
                except ValueError as exc:
                    raise ValueError('图片附件编码无效，请重新上传') from exc
                if not decoded:
                    raise ValueError('图片附件内容为空，请重新上传')
                if len(decoded) > MAX_FILE:
                    raise ValueError('每个附件需为 1 字节至 20 MB')
                total += len(decoded)
                if total > MAX_TOTAL:
                    raise ValueError('每条消息的附件总计不能超过 100 MB')
                if image_type(decoded) != mime:
                    raise ValueError('图片附件类型与内容不匹配，请重新上传')
                name = str(row.get('name') or f'image-{idx + 1}.png').strip() or f'image-{idx + 1}.png'
                media.append({
                    'mime_type': mime,
                    'inline_data': base64.b64encode(decoded).decode('ascii'),
                    'description': name,
                    'display_name': name,
                })
            elif isinstance(row.get('path'), str) and row['path'].strip():
                fallback_paths.append(row)
        return media, fallback_paths

    @staticmethod
    def _build_scope_items(body):
        """Convert existing workspace resolvedReferences into native PathScopeItem entries."""
        scope_items = []
        fallback_refs = []
        for row in body.get('resolvedReferences') or []:
            if not isinstance(row, dict) or not isinstance(row.get('path'), str) or not row['path'].strip():
                continue
            candidate = Path(row['path'])
            if (candidate.is_absolute() and candidate.resolve() == candidate
                    and (candidate.is_file() or candidate.is_dir())):
                kind = 'directory' if candidate.is_dir() else 'file'
                if row.get('kind') not in (None, kind):
                    raise ValueError('项目引用类型已变化，请重新选择')
                scope_items.append({'item': {kind: {'absolute_uri': candidate.resolve().as_uri()}}})
            else:
                raise ValueError('项目引用已失效或目录已变化，请重新选择')
        return scope_items, fallback_refs

    @staticmethod
    def _prompt(body, *, exclude_scoped_references=False):
        text = str(body.get('text') or body.get('firstMessage') or '').strip()
        files = []
        if exclude_scoped_references:
            _, fallback_refs = Antigravity._build_scope_items(body)
            files.extend(fallback_refs)
        else:
            files.extend(row for row in (body.get('resolvedReferences') or []) if isinstance(row, dict) and isinstance(row.get('path'), str))
        for row in body.get('resolvedImages') or []:
            if isinstance(row, dict) and not isinstance(row.get('url'), str) and isinstance(row.get('path'), str):
                files.append(row)
        files.extend(row for row in (body.get('resolvedFiles') or []) if isinstance(row, dict) and isinstance(row.get('path'), str))
        return file_mentions(text, files) if files else text

    def _account(self, surface='app', refresh=False):
        payload = self.cli.catalog_models(surface=surface, refresh=refresh)
        user = payload['userStatus']
        has_effort = any(isinstance(row, dict) and bool(row.get('efforts')) for row in payload.get('models', []))
        out = {'account': {'provider': 'antigravity', 'name': user.get('name') or 'Antigravity',
                           'email': user.get('email') or '', 'tier': (user.get('userTier') or {}).get('name') or ''},
               'models': payload['models'], 'notice': payload['notice'],
               'capabilities': {'models': True, 'effort': has_effort, 'manualModels': True, 'manualEffort': False}}
        if payload.get('usageSummary'):
            out['usageSummary'] = payload['usageSummary']
        return out

    def _update_session_settings(self, key, *, model=_UNSET, effort=_UNSET, work_mode=_UNSET):
        with self._lock:
            entry = dict(self._settings.get(key) or {})
            if model is not _UNSET:
                entry['model'] = model
            if effort is not _UNSET:
                if effort:
                    entry['effort'] = effort
                else:
                    entry.pop('effort', None)
            if work_mode is not _UNSET:
                if work_mode and work_mode != 'default':
                    entry['workMode'] = work_mode
                else:
                    entry.pop('workMode', None)
            if entry:
                self._settings[key] = entry
            else:
                self._settings.pop(key, None)
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = self.settings_file.with_suffix('.tmp')
            temp.write_text(json.dumps(self._settings), encoding='utf-8')
            temp.chmod(0o600)
            temp.replace(self.settings_file)

    def _commands_by_name(self, *, surface, cid=None, model=None, effort=None, cwd=None):
        rows = self.cli.slash_commands(surface=surface, conversation_id=cid, model=model, effort=effort, cwd=cwd)
        return {row['name']: row for row in rows if isinstance(row, dict) and row.get('name')}

    def _slash_catalog(self, *, surface, cid, model, effort, cwd):
        try:
            commands = self.cli.slash_commands(surface=surface, conversation_id=cid, model=model, effort=effort, cwd=cwd)
        except (OSError, ValueError, BridgeUnavailable):
            return {'commands': [], 'skills': [], 'pluginError': '暂时无法读取 Antigravity 原生指令与 Skill 列表'}
        skills = [
            {'id': cmd['name'], 'name': cmd['title'], 'description': cmd['description'], 'selectable': True}
            for cmd in commands if cmd.get('category') == 'skill'
        ]
        public_commands = [
            {'id': cmd['name'], 'name': cmd['title'], 'description': cmd['description'], 'category': cmd['category']}
            for cmd in commands
        ]
        return {'commands': public_commands, 'skills': skills}

    @staticmethod
    def _validate_plugins_arg(raw):
        if raw is None:
            return []
        if not isinstance(raw, list) or any(not isinstance(item, str) or not item.strip() for item in raw):
            raise ValueError('所选 Skill 参数无效')
        unique = list(dict.fromkeys(item.strip() for item in raw))
        if len(unique) > 8:
            raise ValueError('最多选择 8 个 Skill')
        return unique

    def _build_message_items(self, prompt, *, plugins, default_command=None, scope_items=None, surface, cid=None, model=None, effort=None, cwd=None):
        cmd_name, remainder = _parse_slash_prefix(prompt)
        extra_scope = list(scope_items or [])
        if not cmd_name and not plugins and not default_command and not extra_scope:
            return None
        items = []
        leading_cmd = None
        if cmd_name or plugins or default_command:
            commands_by_name = self._commands_by_name(surface=surface, cid=cid, model=model, effort=effort, cwd=cwd)
            offered_skills = {name: row for name, row in commands_by_name.items() if row.get('category') == 'skill'}
            for skill_id in plugins:
                if skill_id not in offered_skills:
                    raise ValueError('技能已不可用，请刷新后选择')
            leading_cmd = commands_by_name.get(cmd_name) if cmd_name else None
            if cmd_name and leading_cmd is None and cmd_name in _KNOWN_NATIVE_SYSTEM_COMMANDS:
                raise BridgeUnavailable(f'当前 Antigravity 原生环境暂未提供 /{cmd_name} 指令')
            seen_commands = set()
            if leading_cmd is not None:
                items.append({'item': {'slashCommand': {'info': dict(leading_cmd['info'])}}})
                seen_commands.add(leading_cmd['name'])
            elif default_command:
                mode_cmd = commands_by_name.get(default_command)
                if not mode_cmd:
                    raise BridgeUnavailable(f'当前 Antigravity 原生环境暂未提供 /{default_command} 模式')
                items.append({'item': {'slashCommand': {'info': dict(mode_cmd['info'])}}})
                seen_commands.add(mode_cmd['name'])
            for skill_id in plugins:
                if skill_id not in seen_commands:
                    items.append({'item': {'slashCommand': {'info': dict(offered_skills[skill_id]['info'])}}})
                    seen_commands.add(skill_id)
        items.extend(extra_scope)
        if not items:
            return None
        if leading_cmd is not None:
            if remainder:
                items.append({'text': remainder})
        elif prompt:
            text_chunk = ' ' + prompt if not prompt.startswith((' ', '\n', '\t')) else prompt
            items.append({'text': text_chunk})
        return items

    def _ensure_native_idle(self, row, key, cid, surface, *, waiting_msg, busy_msg, expected_identity=None):
        if not self.cli.available(surface):
            raise BridgeUnavailable('原客户端服务未连接，请先打开 Antigravity 应用')
        native_busy = self.store.is_native_busy(row['surfaceRoot'], cid)
        waiting_by_cid, active_cids = self._waiting_state(surface, live=True, fresh=True,
                                                        **({'expected_identity': expected_identity} if expected_identity is not None else {}))
        if waiting_by_cid.get(cid) or (self.hooks and self.hooks.has_pending_approvals(key)):
            raise BridgeUnavailable(waiting_msg)
        if native_busy or cid in active_cids:
            raise BridgeUnavailable(busy_msg)

    def _reject_busy_structured_follow_up(self, prompt, body, *, surface, cid, model, effort, cwd, work_mode):
        if self._validate_plugins_arg(body.get('plugins')):
            raise BridgeUnavailable(_BUSY_STRUCTURED_ERROR)
        if body.get('workMode') in ('plan', 'goal') or work_mode == 'plan':
            raise BridgeUnavailable(_BUSY_STRUCTURED_ERROR)
        cmd_name, _ = _parse_slash_prefix(prompt)
        if not cmd_name:
            return
        try:
            known_names = set(self._commands_by_name(surface=surface, cid=cid, model=model, effort=effort, cwd=cwd))
        except (OSError, ValueError, BridgeUnavailable, AttributeError):
            # A failed catalog cannot prove this is plain text rather than a workflow.
            raise BridgeUnavailable(_BUSY_STRUCTURED_ERROR) from None
        if cmd_name in (known_names | _KNOWN_NATIVE_SYSTEM_COMMANDS):
            raise BridgeUnavailable(_BUSY_STRUCTURED_ERROR)

    @staticmethod
    def _validate_title(raw):
        if not isinstance(raw, str) or not 1 <= len(raw.strip()) <= 120 or re.search(r'[\x00-\x1f\x7f]', raw):
            raise ValueError('标题为 1–120 字，不能包含控制字符')
        return raw.strip()

    def _send_prompt_or_items(self, cid, prompt, *, model, surface, effort=None, items=None,
                              media=None, turn_generation=None, before_send=None, expected_identity=None):
        selection = {'effort': effort} if effort else {}
        if media:
            selection['media'] = media
        if turn_generation is not None:
            selection['turn_generation'] = turn_generation
        if before_send is not None:
            selection['before_send'] = before_send
        if expected_identity is not None:
            selection['expected_identity'] = expected_identity
        if items is not None:
            return self.cli.send_user_message(cid, prompt, model=model, surface=surface, items=items, **selection)
        return self.cli.send_user_message(cid, prompt, model=model, surface=surface, **selection)

    def _stop_task(self, cid, surface, body):
        task_id = str(body.get('taskId') or '').strip()
        bg_match = re.fullmatch(r'bg:(\d+)', task_id)
        if not bg_match and (not re.fullmatch(r'[0-9a-fA-F-]{36}', task_id) or task_id == cid):
            raise ResumePreflightBlocked('后台任务标识无效')
        try:
            expected_identity = self.cli.source_identity(surface)
            if bg_match:
                live_bg = self.cli.list_background_tasks(cid, surface=surface, expected_identity=expected_identity)
                if not any(task.get('id') == task_id and task.get('cancellable') is True for task in live_bg):
                    raise ResumePreflightBlocked('找不到该运行中的后台任务或任务已结束')
            else:
                children = self.cli.running_child_cids(cid, surface=surface, expected_identity=expected_identity)
                if task_id not in children:
                    raise ResumePreflightBlocked('该子任务已结束或无法核验其属于当前会话')
        except (OSError, ValueError, BridgeUnavailable) as exc:
            raise ResumePreflightBlocked(str(exc)) from exc
        if bg_match:
            self.cli.stop_background_step(cid, int(bg_match.group(1)), surface=surface,
                                          expected_identity=expected_identity)
        else:
            child_key = gateway_id(surface, task_id)
            before_stop = (lambda: self.hooks.request_stop(child_key)) if self.hooks else None
            self.cli.stop_child_cascade(task_id, surface=surface, before_stop=before_stop,
                                        expected_identity=expected_identity)
        return {'status': 'accepted'}

    def call(self, action, sid=None, body=None):
        body = body or {}
        turn_generation = self.cli.turn_generation() if action in ('send', 'goal', 'compact') else None
        if action == 'status':
            return self.status()
        if action in ('list', 'lifecycle'):
            rows = self.store.list_sessions()
            live = {surface: self.cli.available(surface) for surface in {row['surfaceDirKey'] for row in rows}}
            waiting = {surface: self._waiting_state(surface, live=live[surface]) for surface in live}
            return {'connected': True, 'complete': True,
                    'sessions': [self._session(row, live=live[row['surfaceDirKey']], waiting_state=waiting[row['surfaceDirKey']]) for row in rows]}
        if action == 'projects':
            return {'projects': self.store.discover_projects(), 'createRequiresMessage': True, 'createModels': True}
        if action == 'account':
            if body.get('surface'):
                surface = body['surface']
                if surface not in SURFACE_LABELS:
                    raise ValueError('Antigravity 来源无效')
                if self.cli.available(surface):
                    return self._account(surface, body.get('refresh') is True)
                return {'models': [], 'notice': '电脑原生服务尚未连接', 'capabilities': {'models': False, 'effort': False}}
            groups, errors = [], []
            for surface, label in SURFACE_LABELS.items():
                if not self.cli.available(surface):
                    continue
                try:
                    value = self._account(surface, body.get('refresh') is True)
                except (OSError, ValueError, BridgeUnavailable):
                    value = {'models': [], 'notice': '原客户端模型暂不可用'}
                    errors.append(label+'：'+value['notice'])
                groups.append({'surface': surface, 'label': label, **value})
            return {'groups': groups, 'models': [], 'modelError': '；'.join(errors),
                    'notice': '' if groups else '电脑原生服务尚未连接',
                    'capabilities': {'models': bool(groups), 'effort': False}}
        if action == 'create':
            prompt = self._prompt(body)
            project = body.get('directoryProject') or next((p for p in self.store.discover_projects()
                                                           if p['key'] == body.get('projectKey')), None)
            if not prompt or not project:
                raise ValueError('请选择 Antigravity 项目并输入首条消息')
            surface = project.get('surface') or 'app'
            cwd = project.get('cwd')
            model = body.get('model')
            effort = body.get('effort')
            if body.get('manualEffort') is True:
                raise ValueError('Antigravity 暂不支持手动填写思考强度')
            raw_title = body.get('title')
            cleaned_title = self._validate_title(raw_title) if isinstance(raw_title, str) and raw_title.strip() else (
                self._validate_title(raw_title) if raw_title not in (None, '') else '')
            plugins = self._validate_plugins_arg(body.get('plugins'))
            items = self._build_message_items(
                prompt, plugins=plugins, default_command=None,
                surface=surface, cid=None, model=model, effort=effort, cwd=cwd,
            )
            cid, expected_identity = self.cli.start_cascade(
                surface=surface, model=model, effort=effort, cwd=cwd,
            )
            title_unconfirmed = False
            if cleaned_title:
                try:
                    self.cli.update_annotations(cid, title=cleaned_title, surface=surface,
                                                expected_identity=expected_identity)
                except (OSError, ValueError, BridgeUnavailable):
                    title_unconfirmed = True
            self._send_prompt_or_items(cid, prompt, model=model, effort=effort, surface=surface, items=items,
                                       expected_identity=expected_identity)
            key = gateway_id(surface, cid)
            if model:
                try:
                    _, canonical_model, canonical_effort, _ = self.cli.canonicalize_model_selection(
                        model, effort, surface=surface, require_effort=False,
                    )
                except (OSError, ValueError, BridgeUnavailable):
                    canonical_model, canonical_effort = model, (effort or '')
                if canonical_model:
                    self._update_session_settings(key, model=canonical_model, effort=canonical_effort)
            return {'status': 'accepted', 'id': key, 'sessionId': key, 'result': {'id': key, 'sessionId': key},
                    **({'titleStatus': 'unconfirmed', 'notice': '会话已创建，标题结果待核对；请在原客户端检查'}
                       if title_unconfirmed else {})}
        row = self.store.resolve_session(sid)
        if row is None:
            raise ValueError('找不到此 Antigravity 会话')
        key, cid, surface = row['id'], row['nativeId'], row['surfaceDirKey']
        # Stop does not need the model/catalog or activity reads held by a send.
        if action == 'stop':
            if self.hooks:
                self.cli.stop(cid, surface=surface, before_stop=lambda: self.hooks.request_stop(key))
            else:
                self.cli.stop(cid, surface=surface)
            return {'status': 'accepted'}
        if action == 'stop-task':
            return self._stop_task(cid, surface, body)
        if action == 'rename':
            try:
                title = self._validate_title(body.get('title'))
            except ValueError as exc:
                raise ResumePreflightBlocked(str(exc)) from exc
            if not self.cli.available(surface):
                raise ResumePreflightBlocked('当前会话尚不能原生改名，请先打开原 Antigravity 应用')
            self.cli.update_annotations(cid, title=title, surface=surface)
            fresh_ann = self.cli.annotations_for(cid, surface=surface, fresh=True)
            confirmed_title = fresh_ann.get('title')
            if not confirmed_title:
                confirmed_title = read_annotation_pbtxt(row['surfaceRoot'], cid).get('title')
            if confirmed_title != title:
                raise BridgeUnavailable('改名结果待核对，请检查原客户端；不会重复提交')
            return {'status': 'confirmed', 'result': {'id': key, 'title': title}}
        if action in ('archive-state', 'archive'):
            live = self.cli.available(surface)
            if action == 'archive-state':
                waiting_state = self._waiting_state(surface, live=live, fresh=True) if live else ({}, set())
                session = self._session(row, live=live, waiting_state=waiting_state)
                native_known = True
                if live:
                    try:
                        if self.store.is_native_busy(row['surfaceRoot'], cid):
                            session['status'] = 'active'
                    except ValueError:
                        native_known = False
                pending_hooks = bool(self.hooks and (self.hooks.has_pending_approvals(key) or self.hooks.get_queued_items(key)))
                archive_safe = bool(live and native_known and session.get('runtimeKnown') is True
                                    and session['status'] == 'idle' and not session['requests'] and not pending_hooks)
                return {**session, 'archiveSafe': archive_safe, '_archiveFlags': {key: bool(session['archived'])}}
            archived = body.get('archived')
            if type(archived) is not bool:
                raise ArchiveNotSent('聊天归档设置无效')
            if not live:
                raise ArchiveNotSent('原客户端服务未连接，请先打开 Antigravity 应用')
            waiting_state = self._waiting_state(surface, live=True, fresh=True)
            session = self._session(row, live=True, waiting_state=waiting_state)
            try:
                native_busy = self.store.is_native_busy(row['surfaceRoot'], cid)
            except ValueError as exc:
                if not (body.get('disconnect') is True and archived is True):
                    raise ArchiveNotSent(str(exc)) from exc
                native_busy = True
            pending_hooks = bool(self.hooks and (self.hooks.has_pending_approvals(key) or self.hooks.get_queued_items(key)))
            if body.get('disconnect') is True and archived is True:
                if session['status'] == 'active' or native_busy or session['requests'] or pending_hooks:
                    if self.hooks:
                        self.cli.stop(cid, surface=surface, before_stop=lambda: self.hooks.request_stop(key))
                    else:
                        self.cli.stop(cid, surface=surface)
                # A successful cancellation RPC is not evidence that every task
                # or pending interaction has finished. Re-read before archiving.
                try:
                    fresh_row = self.store.resolve_session(key)
                    if fresh_row is None or fresh_row.get('runtimeKnown') is not True:
                        raise ValueError('聊天运行状态尚未确认')
                    native_busy = self.store.is_native_busy(fresh_row['surfaceRoot'], cid)
                    waiting_state = self._waiting_state(surface, live=True, fresh=True)
                    session = self._session(fresh_row, live=True, waiting_state=waiting_state)
                    pending_hooks = bool(self.hooks and (self.hooks.has_pending_approvals(key)
                                                         or self.hooks.get_queued_items(key)))
                except (OSError, ValueError, BridgeUnavailable) as exc:
                    raise ArchiveNotSent('停止后的聊天状态尚未确认，请在原客户端处理后重试') from exc
                if session['status'] != 'idle' or native_busy or session['requests'] or pending_hooks:
                    raise ArchiveNotSent('聊天仍有运行、排队或审批，请在原客户端处理后重试')
            elif session['status'] != 'idle' or native_busy or session['requests'] or pending_hooks:
                raise ArchiveNotSent('聊天有运行、排队、审批或未知状态，请先在原客户端处理')
            self.cli.update_annotations(cid, archived=archived, surface=surface)
            return {'status': 'accepted'}
        begin_turn = (lambda: self.hooks.begin_user_turn(key)) if self.hooks else None
        session = self._session(row, waiting_state=None if action == 'detail' else ({}, set()))
        if action == 'detail':
            if not session['readOnly'] and session.get('model') and not session.get('modelDisplayName'):
                try:
                    proj_model, proj_effort, display_name = self.cli.resolve_model_display(
                        surface, session['model'], session.get('effort') or '', refresh_if_unknown=True,
                    )
                    session['model'] = proj_model
                    session['effort'] = proj_effort
                    if display_name:
                        session['modelDisplayName'] = display_name
                except (OSError, ValueError, BridgeUnavailable):
                    pass
            page = self.store.read_transcript_page(row['surfaceRoot'], cid, limit=body.get('historyLimit') or 40,
                                                   before=body.get('historyBefore'), session_active=session['status'] == 'active')
            if page.get('contextUsage'):
                session['contextUsage'] = dict(page['contextUsage'])
            if not session['readOnly'] and not body.get('historyBefore'):
                try:
                    ctx_meta = self.cli.context_window_metadata(cid, surface=surface, step_count=row.get('stepCount'))
                except (OSError, ValueError, BridgeUnavailable):
                    ctx_meta = None
                if isinstance(ctx_meta, dict) and ctx_meta.get('contextWindow'):
                    if isinstance(session.get('contextUsage'), dict):
                        session['contextUsage']['contextWindow'] = ctx_meta['contextWindow']
                    elif ctx_meta.get('estimatedTokensUsed') is not None:
                        session['contextUsage'] = {
                            'usedTokens': ctx_meta['estimatedTokensUsed'],
                            'contextWindow': ctx_meta['contextWindow'],
                            'measurement': 'session',
                        }
                session.update(self.cli.current_model_quota(surface, session.get('model'), session.get('modelDisplayName')))
            queued = self.hooks.get_queued_items(key) if self.hooks else []
            return {'connected': True, 'session': session, **{k: page[k] for k in ('messages', 'turns', 'history')},
                    'readOnly': session['readOnly'],
                    'notice': '原生服务未连接，可继续阅读历史和编辑草稿；发送及模型设置暂不可用。' if session['readOnly'] else '',
                    'requests': session['requests'], 'queued': queued, 'capabilities': session['capabilities'],
                    'submissions': queued, 'draftAccountIdentity': self.account_scope()}
        if action == 'catalog':
            section = str(body.get('section') or 'all')
            refresh = body.get('refresh') is True
            if section == 'models':
                return {**self._account(surface, refresh), 'currentModel': session['model'], 'currentEffort': session['effort'],
                        'capabilities': session['capabilities'], 'skills': []}
            if section == 'commands':
                slash_data = self._slash_catalog(surface=surface, cid=cid, model=session['model'], effort=session['effort'], cwd=row.get('cwd'))
                return {'currentModel': session['model'], 'currentEffort': session['effort'], 'capabilities': session['capabilities'], **slash_data}
            account_data = self._account(surface, refresh)
            slash_data = self._slash_catalog(surface=surface, cid=cid, model=session['model'], effort=session['effort'], cwd=row.get('cwd'))
            merged_caps = {**account_data.get('capabilities', {}), **session['capabilities']}
            return {**account_data, 'currentModel': session['model'], 'currentEffort': session['effort'], 'capabilities': merged_caps, **slash_data}
        if action == 'tasks':
            child_tasks = self.store.list_tasks(row['surfaceRoot'], cid, live=False)
            bg_tasks = []
            if self.cli.available(surface):
                try:
                    identity = self.cli.source_identity(surface)
                    bg_tasks = self.cli.list_background_tasks(cid, surface=surface, expected_identity=identity)
                    running = self.cli.running_child_cids(cid, surface=surface, expected_identity=identity)
                    if self.cli.source_identity(surface) != identity:
                        raise BridgeUnavailable('原生服务进程已变化')
                    for task in child_tasks:
                        if task['id'] in running:
                            task.update(status='running', cancellable=True)
                except (OSError, ValueError, BridgeUnavailable):
                    bg_tasks = []
            return {'tasks': bg_tasks + child_tasks}
        if action == 'image':
            return self.store.read_image(row['surfaceRoot'], cid, body.get('source'))
        if action == 'resume':
            if not self.cli.available(surface):
                raise BridgeUnavailable('请先在电脑打开原会话所属的 Antigravity 应用')
            return {'status': 'ready'}
        if action == 'access':
            if 'mode' in body and not self.hooks:
                raise ValueError('请在电脑网关启用 Antigravity 接入')
            mode = self.hooks.set_permission_mode(key, body['mode']) if 'mode' in body else session['permissionMode']
            return {'status': 'accepted', 'mode': mode, 'options': PERMISSION_OPTIONS}
        if action == 'respond':
            request_id = body.get('requestId')
            if not isinstance(request_id, str) or not request_id.strip():
                raise ValueError('缺少请求 ID')
            if request_id.startswith('agy-wait:'):
                self.cli.respond_interaction(cid, request_id, body.get('decision'), answers=body.get('answers'), surface=surface)
                return {'status': 'accepted'}
            if not self.hooks:
                raise ValueError('审批连接不可用')
            self.hooks.resolve_approval(key, request_id, body.get('decision'))
            return {'status': 'accepted'}
        if action == 'settings':
            if body.get('manualEffort') is True:
                raise ValueError('Antigravity 暂不支持手动填写思考强度')
            model = body.get('model')
            effort = body.get('effort')
            _, canonical_model, canonical_effort, _ = self.cli.canonicalize_model_selection(
                model, effort, surface=surface, require_effort=True,
            )
            self._update_session_settings(key, model=canonical_model, effort=canonical_effort)
            return {'status': 'accepted', 'model': canonical_model, **({'effort': canonical_effort} if canonical_effort else {})}
        if action == 'work-mode':
            mode = body.get('mode')
            if mode not in ('default', 'plan'):
                raise ValueError('Antigravity 工作模式无效')
            self._ensure_native_idle(
                row, key, cid, surface,
                waiting_msg='原会话正在等待交互确认，请先回应后再切换工作模式',
                busy_msg=_BUSY_STRUCTURED_ERROR,
            )
            if mode == 'plan':
                commands = self._commands_by_name(surface=surface, cid=cid, model=session['model'], effort=session['effort'], cwd=row.get('cwd'))
                if 'plan' not in commands:
                    raise BridgeUnavailable('当前 Antigravity 原生环境暂未提供 /plan 模式')
            self._update_session_settings(key, work_mode=mode)
            return {'status': 'accepted', 'mode': mode}
        if action == 'goal':
            if body.get('operation') != 'set':
                raise ValueError('Antigravity 暂不支持此目标状态操作，请在原对话继续交互')
            objective = str(body.get('objective') or '').strip()
            if not objective:
                raise ValueError('请输入目标内容')
            if len(objective) > 4000:
                raise ValueError('目标不能超过 4000 字')
            self._ensure_native_idle(
                row, key, cid, surface,
                waiting_msg='原会话正在等待交互确认，请先回应后再设定目标',
                busy_msg='请等待 Antigravity 原生会话空闲后再设定目标',
            )
            commands = self._commands_by_name(surface=surface, cid=cid, model=session['model'], effort=session['effort'], cwd=row.get('cwd'))
            goal_cmd = commands.get('goal')
            if not goal_cmd:
                raise BridgeUnavailable('当前 Antigravity 原生环境暂未提供 /goal 指令')
            self._send_prompt_or_items(
                cid, f'/goal {objective}', model=session['model'], effort=session['effort'], surface=surface,
                turn_generation=turn_generation, before_send=begin_turn,
                items=[{'item': {'slashCommand': {'info': dict(goal_cmd['info'])}}}, {'text': ' ' + objective}],
            )
            return {'status': 'accepted', 'delivery': 'native', 'operation': 'set'}
        if action == 'compact':
            self._ensure_native_idle(
                row, key, cid, surface,
                waiting_msg='原会话正在等待交互确认，请先回应后再压缩上下文',
                busy_msg='请等待 Antigravity 原生会话空闲后再压缩上下文',
            )
            commands = self._commands_by_name(surface=surface, cid=cid, model=session['model'], effort=session['effort'], cwd=row.get('cwd'))
            compact_cmd = commands.get('compact')
            if not compact_cmd:
                raise BridgeUnavailable('当前 Antigravity 原生环境暂未提供 /compact 指令')
            self._send_prompt_or_items(
                cid, '/compact', model=session['model'], effort=session['effort'], surface=surface,
                turn_generation=turn_generation, before_send=begin_turn,
                items=[{'item': {'slashCommand': {'info': dict(compact_cmd['info'])}}}],
            )
            return {'status': 'accepted', 'delivery': 'native'}
        if action == 'fork':
            try:
                expected_identity = self.cli.source_identity(surface)
                self._ensure_native_idle(
                    row, key, cid, surface,
                    waiting_msg='原会话正在等待交互确认，请先回应后再创建分支',
                    busy_msg='请等待 Antigravity 原生会话空闲后再创建分支',
                    expected_identity=expected_identity,
                )
            except (ValueError, BridgeUnavailable) as exc:
                raise ResumePreflightBlocked(str(exc)) from exc
            anchor = str(body.get('messageId') or '').strip()
            if not re.fullmatch(r'\d+', anchor):
                raise ResumePreflightBlocked('请选择原会话中可分支的消息')
            step_index = int(anchor)
            valid_steps = self.store.valid_fork_steps(row['surfaceRoot'], cid)
            if step_index not in valid_steps:
                raise ResumePreflightBlocked('请选择原会话中可分支的消息')
            new_cid = self.cli.fork_conversation(cid, step_index, cwd=row.get('cwd'), surface=surface,
                                                 expected_identity=expected_identity)
            return {'status': 'ready', 'result': {'id': gateway_id(surface, new_cid)}}
        if action == 'queue-action':
            if not self.hooks:
                raise ValueError('排队连接不可用')
            operation, queue_id = body.get('operation'), body.get('queueId')
            if operation in ('cancel', 'edit'):
                item = self.hooks.remove_queued_item(key, queue_id)
            elif operation in ('queue', 'steer'):
                item = self.hooks.update_queue_mode(key, queue_id, operation)
            else:
                raise ValueError('排队操作无效')
            if item is None:
                raise ValueError('排队消息已发送或移除')
            return {'status': 'accepted', **({'text': item['text']} if operation == 'edit' else {})}
        if action == 'send':
            native_busy = self.store.is_native_busy(row['surfaceRoot'], cid)
            media, _ = self._build_media(body)
            scope_items, _ = self._build_scope_items(body)
            prompt = self._prompt(body, exclude_scoped_references=True)
            mode = body.get('mode') or 'send'
            plugins = self._validate_plugins_arg(body.get('plugins'))
            if (not prompt and not media and not scope_items) or mode not in ('send', 'queue', 'steer'):
                raise ValueError('Antigravity 消息或发送方式无效')
            waiting_by_cid, active_cids = self._waiting_state(surface, live=True, fresh=True)
            if (waiting_by_cid.get(cid) or (self.hooks and self.hooks.has_pending_approvals(key))) and mode == 'send':
                raise BridgeUnavailable('原会话正在等待交互确认，请先回应后再发送')
            if native_busy or cid in active_cids:
                if mode not in ('queue', 'steer') or not self.hooks or not self.hooks.has_recent_heartbeat(key):
                    raise BridgeUnavailable('原会话正在运行；请等待结束，或在原会话重新载入网关 Hook 后排队')
                if media:
                    raise BridgeUnavailable('原会话运行中，排队或插话暂不支持图片附件，请等待当前回复结束后直接发送')
                queued_prompt = self._prompt(body, exclude_scoped_references=False)
                self._reject_busy_structured_follow_up(
                    queued_prompt, {**body, 'plugins': plugins}, work_mode=session.get('collaborationMode'),
                    surface=surface, cid=cid, model=session['model'], effort=session['effort'], cwd=row.get('cwd'),
                )
                item = self.hooks.enqueue_follow_up(key, queued_prompt, mode=mode)
                return {'status': 'queued', 'id': item['id'], 'delivery': 'hook-' + mode}
            default_command = 'plan' if session.get('collaborationMode') == 'plan' else None
            items = self._build_message_items(
                prompt, plugins=plugins, default_command=default_command, scope_items=scope_items,
                surface=surface, cid=cid, model=session['model'], effort=session['effort'], cwd=row.get('cwd'),
            )
            before_send = begin_turn if self.hooks and not waiting_by_cid.get(cid) and not self.hooks.has_pending_approvals(key) else None
            self._send_prompt_or_items(cid, prompt, model=session['model'], effort=session['effort'], surface=surface,
                                       items=items, media=media, turn_generation=turn_generation, before_send=before_send)
            return {'status': 'accepted', 'delivery': 'native'}
        if action == 'revert':
            step_index = body.get('stepIndex')
            conversation_only = body.get('conversationOnly', True)
            if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0 or not isinstance(conversation_only, bool):
                raise ResumePreflightBlocked('撤回参数无效')
            try:
                expected_identity = self.cli.source_identity(surface)
                self._ensure_native_idle(row, key, cid, surface,
                    waiting_msg='原会话等待确认，请先回应后再撤回',
                    busy_msg='原会话正在运行，请先停止后再撤回', expected_identity=expected_identity)
                if self.hooks and self.hooks.get_queued_items(key):
                    raise ValueError('请先处理待发消息再撤回')
                if self.store.revert_anchor(row['surfaceRoot'], cid, step_index) != body.get('messageId'):
                    raise ValueError('原消息已变化或不属于该会话，请刷新后重新选择')
            except (OSError, ValueError, sqlite3.DatabaseError, BridgeUnavailable) as exc:
                raise ResumePreflightBlocked(str(exc)) from exc
            result = self.cli.revert_to_step(cid, step_index, conversation_only=conversation_only,
                                             surface=surface, expected_identity=expected_identity)
            reverted = (result or {}).get('metadata', {}).get('revertedUris') if isinstance(result, dict) else None
            return {'status': 'accepted', 'stepIndex': step_index, 'revertedUris': reverted or []}
        raise ValueError('此 Antigravity 操作尚不支持')

    def close(self):
        with self._lock:
            if self.hooks:
                self.hooks.close()
                self.hooks = None
            if self._lease:
                self._lease.close()
                self._lease = None
