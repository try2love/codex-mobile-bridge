"""Antigravity history, original-server writes and explicitly enabled hooks."""
import hashlib
import json
from pathlib import Path
import threading

from bridge.clients.errors import BridgeUnavailable
from bridge.clients.file_mentions import file_mentions
from bridge.platforms.antigravity import profile_lease
from .cli_session import AntigravityCli
from .hook_ipc import HookIPCServer
from .model import COMPOSER_PRESENTATION, PERMISSION_OPTIONS, SURFACE_LABELS, gateway_id
from .setup import disable_plugin, ensure_plugin, inspect_plugin, plugin_directory
from .store import AntigravityStore


class Antigravity:
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
        candidates = [row for row in self.store.list_sessions() if row['nativeId'] == cid]
        for row in candidates:
            expected = Path(row['surfaceRoot'])/'brain'/cid/'.system_generated/logs/transcript.jsonl'
            if Path(transcript).expanduser().resolve() == expected.resolve():
                return row['id']
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
                 'scope': str(row['root']), 'createRequiresMessage': True, 'createTitle': False} for row in self.store.surfaces()]

    def _waiting_state(self, surface, *, live=True, fresh=False):
        if not live:
            return {}, set()
        if fresh:
            return self.cli.waiting_state_by_cid(surface=surface, max_age=0)
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
        return {**row, 'status': 'active' if active else 'idle', 'requests': requests,
                'model': self._settings.get(key, {}).get('model') or '', 'effort': '',
                'permissionMode': permission,
                'permissionLabel': next(option['label'] for option in PERMISSION_OPTIONS if option['value'] == permission),
                'composerCapabilities': dict(COMPOSER_PRESENTATION),
                'connectionState': 'ready' if live else 'detached', 'readOnly': not live,
                'capabilities': {'send': live, 'stop': live and active, 'models': live, 'effort': False, 'manualModels': live, 'manualEffort': False,
                                 'permissions': bool(self.hooks), 'attachments': live, 'uploadFiles': live,
                                 'queue': hooked, 'steer': hooked, 'skills': False, 'rename': False,
                                 'plan': False, 'goal': False, 'archive': False, 'unarchive': False}}

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
    def _prompt(body):
        text = str(body.get('text') or body.get('firstMessage') or '').strip()
        files = [row for name in ('resolvedReferences', 'resolvedImages', 'resolvedFiles')
                 for row in body.get(name, []) if isinstance(row, dict) and isinstance(row.get('path'), str)]
        return file_mentions(text, files) if files else text

    def _account(self, surface='app', refresh=False):
        payload = self.cli.catalog_models(surface=surface, refresh=refresh)
        user = payload['userStatus']
        out = {'account': {'provider': 'antigravity', 'name': user.get('name') or 'Antigravity',
                           'email': user.get('email') or '', 'tier': (user.get('userTier') or {}).get('name') or ''},
               'models': payload['models'], 'notice': payload['notice'],
               'capabilities': {'models': True, 'effort': False, 'manualModels': True, 'manualEffort': False}}
        if payload.get('usageSummary'):
            out['usageSummary'] = payload['usageSummary']
        return out

    def call(self, action, sid=None, body=None):
        body = body or {}
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
            cid = self.cli.start_cascade(surface=surface, model=body.get('model'), cwd=cwd)
            self.cli.send_user_message(cid, prompt, model=body.get('model'), surface=surface)
            key = gateway_id(surface, cid)
            return {'status': 'accepted', 'id': key, 'sessionId': key, 'result': {'id': key, 'sessionId': key}}
        row = self.store.resolve_session(sid)
        if row is None:
            raise ValueError('找不到此 Antigravity 会话')
        key, cid, surface = row['id'], row['nativeId'], row['surfaceDirKey']
        session = self._session(row, waiting_state=None if action == 'detail' else ({}, set()))
        if action == 'detail':
            page = self.store.read_transcript_page(row['surfaceRoot'], cid, limit=body.get('historyLimit') or 40,
                                                   before=body.get('historyBefore'), session_active=session['status'] == 'active')
            if page.get('contextUsage'):
                session['contextUsage'] = page['contextUsage']
            queued = self.hooks.get_queued_items(key) if self.hooks else []
            return {'connected': True, 'session': session, **{k: page[k] for k in ('messages', 'turns', 'history')},
                    'readOnly': session['readOnly'],
                    'notice': '原生服务未连接，可继续阅读历史和编辑草稿；发送及模型设置暂不可用。' if session['readOnly'] else '',
                    'requests': session['requests'], 'queued': queued, 'capabilities': session['capabilities'],
                    'submissions': queued, 'draftAccountIdentity': self.account_scope()}
        if action == 'catalog':
            return {**self._account(surface, body.get('refresh') is True), 'currentModel': session['model'], 'skills': []}
        if action == 'tasks':
            return {'tasks': self.store.list_tasks(row['surfaceRoot'], cid)}
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
            if body.get('effort'):
                raise ValueError('请使用原客户端提供的完整模型条目选择思考强度')
            model = body.get('model')
            self.cli._model(model, surface)
            with self._lock:
                self._settings[key] = {'model': model}
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                temp = self.settings_file.with_suffix('.tmp')
                temp.write_text(json.dumps(self._settings), encoding='utf-8')
                temp.chmod(0o600)
                temp.replace(self.settings_file)
            return {'status': 'accepted', 'model': model}
        if action == 'stop':
            if self.hooks:
                self.hooks.request_stop(key)
            self.cli.stop(cid, surface=surface)
            return {'status': 'accepted'}
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
            prompt, mode = self._prompt(body), body.get('mode') or 'send'
            if not prompt or mode not in ('send', 'queue', 'steer'):
                raise ValueError('Antigravity 消息或发送方式无效')
            waiting_by_cid, active_cids = self._waiting_state(surface, live=True, fresh=True)
            if (waiting_by_cid.get(cid) or (self.hooks and self.hooks.has_pending_approvals(key))) and mode == 'send':
                raise BridgeUnavailable('原会话正在等待交互确认，请先回应后再发送')
            if native_busy or cid in active_cids:
                if mode not in ('queue', 'steer') or not self.hooks or not self.hooks.has_recent_heartbeat(key):
                    raise BridgeUnavailable('原会话正在运行；请等待结束，或在原会话重新载入网关 Hook 后排队')
                item = self.hooks.enqueue_follow_up(key, prompt, mode=mode)
                return {'status': 'queued', 'id': item['id'], 'delivery': 'hook-' + mode}
            self.cli.send_user_message(cid, prompt, model=session['model'], surface=surface)
            return {'status': 'accepted', 'delivery': 'native'}
        raise ValueError('此 Antigravity 操作尚不支持')

    def close(self):
        with self._lock:
            if self.hooks:
                self.hooks.close()
                self.hooks = None
            if self._lease:
                self._lease.close()
                self._lease = None
