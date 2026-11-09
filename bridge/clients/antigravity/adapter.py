"""Antigravity 2.0 / IDE / CLI hybrid adapter for Codex Mobile Bridge."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from bridge.clients.errors import BridgeUnavailable
from bridge.clients.file_mentions import file_mentions
from .cli_session import AntigravityCli
from .hook_ipc import HookIPCServer
from .model import SURFACE_LABELS, gateway_id
from .setup import disable_plugin, ensure_plugin, inspect_plugin
from .store import AntigravityStore

PERMISSION_OPTIONS = (
    ('desktop', '桌面端處理 (預設)', '由電腦端 Antigravity 處理審批，不阻塞桌面操作'),
    ('mobile-ask', '手機端審批', '危險工具與終端指令攔截並推送至手機端確認'),
    ('auto-allow', '自動允許', 'Hook 攔截範圍內工具自動放行'),
)


class Antigravity:
    """Coordinate read-only SQLite/JSONL inspection, Hook IPC, and `agy` CLI resume."""

    def __init__(self, directory, home=None):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.home = Path(home or os.environ.get('ANTIGRAVITY_HOME', '').strip() or Path.home()).expanduser().resolve()
        self.store = AntigravityStore(self.directory / 'scratch', home=self.home)
        self.hooks = HookIPCServer(self.directory)
        self.cli = AntigravityCli(home=self.home)
        self.settings_file = self.directory / 'session-settings.json'
        self._lock = threading.Lock()
        self._settings = self._load_settings()
        self._in_flight_sends = set()
        self._in_flight_lock = threading.Lock()
        self._recent_submissions = {}

    def _load_settings(self):
        try:
            if self.settings_file.is_file():
                data = json.loads(self.settings_file.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        return {}

    def _save_settings(self):
        tmp = self.settings_file.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(self._settings, ensure_ascii=False, indent=2), encoding='utf-8')
            os.chmod(tmp, 0o600)
            tmp.replace(self.settings_file)
        except OSError:
            pass

    def _session_settings(self, cid):
        with self._lock:
            entry = dict(self._settings.get(cid) or {})
        return {
            'model': str(entry.get('model') or 'auto'),
            'effort': str(entry.get('effort') or 'auto'),
        }

    def account_scope(self):
        return f'antigravity:{self.home}'

    def ensure_plugin(self):
        return ensure_plugin(self.directory, home=self.home, enabled=True)

    def disable_plugin(self):
        return disable_plugin(self.directory, home=self.home)

    def status(self):
        surfaces = self.store.surfaces()
        cli_ready = self.cli.available()
        plugin = inspect_plugin(self.directory, home=self.home)
        installed = bool(surfaces or cli_ready)
        return {
            'installed': installed,
            'configured': installed,
            'connected': installed,
            'cliAvailable': cli_ready,
            'cliAuthReady': cli_ready,
            'pluginInstalled': plugin['installed'],
            'pluginEnabled': plugin['enabled'],
            'surfaces': [item['key'] for item in surfaces],
            'setupState': 'ready' if installed else 'not-installed',
            'reason': 'Antigravity 狀態庫、Hook 管道與 CLI 均已就緒' if installed else '尚未偵測到 Antigravity 安裝或資料目錄',
        }

    def project_environments(self):
        surfaces = self.store.surfaces()
        if not surfaces and self.cli.available():
            surfaces = [{'key': 'app', 'name': SURFACE_LABELS['app'], 'root': self.store.surface_root('app')}]
        environments = []
        for item in surfaces:
            environments.append({
                'surface': item['key'],
                'host': 'local',
                'hostLabel': '此電腦',
                'name': f'{item["name"]} · 此電腦',
                'scope': str(item['root']),
                'createRequiresMessage': True,
            })
        return environments

    def _decorate_session(self, row):
        cid = row['nativeId']
        prefs = self._session_settings(cid)
        requests = self.hooks.pending_approvals_for(cid)
        is_active = (
            row.get('status') == 'active'
            or self.hooks.has_recent_heartbeat(cid)
            or self.cli.is_running(cid)
            or bool(requests)
        )
        capabilities = {
            'send': True,
            'stop': is_active,
            'models': True,
            'effort': True,
            'permissions': True,
            'attachments': True,
            'uploadFiles': True,
            'queue': True,
            'steer': True,
            'skills': False,
            'rename': False,
            'archive': False,
            'unarchive': False,
        }
        raw_model = str(prefs.get('model') or '').strip()
        if not raw_model or raw_model == 'auto':
            raw_model = 'gemini-3.8-flash'
        raw_effort = str(prefs.get('effort') or '').strip().lower()

        # Normalize models with embedded effort suffixes like gemini-3.8-flash-high
        model_id = raw_model
        effort_val = raw_effort
        for suffix in ('-high', '-medium', '-low'):
            if raw_model.endswith(suffix):
                model_id = raw_model[:-len(suffix)]
                if not effort_val or effort_val == 'auto':
                    effort_val = suffix[1:]
                break

        if not effort_val or effort_val == 'auto':
            effort_val = 'high' if 'flash' in model_id else 'medium'

        display_names = {
            'gemini-4-argon': 'Gemini 4 Argon',
            'gemini-3.8-flash': 'Gemini 3.8 Flash',
            'claude-opus-5-5': 'Claude Opus 5.5',
            'claude-sonnet-5-5': 'Claude Sonnet 5.5',
            'gpt-oss-120b-medium': 'GPT-OSS 120B',
            'gpt-oss-120b': 'GPT-OSS 120B',
        }
        display_name = display_names.get(model_id, model_id)
        effort_normalized = effort_val.lower()
        effort_label = effort_normalized.capitalize() if effort_normalized not in ('auto', 'none') else ''

        return {
            **row,
            'status': 'active' if is_active else 'idle',
            'requests': requests,
            'model': model_id,
            'modelDisplayName': display_name,
            'effort': effort_normalized,
            'effortLabel': effort_label,
            'reasoningEffort': effort_normalized,
            'permissionMode': self.hooks.permission_mode(cid),
            'connectionState': 'ready',
            'capabilities': capabilities,
        }

    @staticmethod
    def _compose_prompt(body):
        raw = body or {}
        text = str(raw.get('text') or raw.get('firstMessage') or raw.get('prompt') or '').strip()
        files = []
        for key in ('resolvedReferences', 'resolvedImages', 'resolvedFiles'):
            items = raw.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get('path'), str):
                        files.append(item)
        if files:
            text = file_mentions(text, files)
        return text.strip()

    def call(self, action, sid=None, body=None):
        body = body or {}
        if action == 'status':
            return self.status()

        if action in ('list', 'lifecycle'):
            rows = [self._decorate_session(row) for row in self.store.list_sessions()]
            return {
                'connected': True,
                'complete': True,
                'sessions': rows,
            }

        if action == 'projects':
            return {
                'projects': self.store.discover_projects(),
                'createRequiresMessage': True,
                'createModels': True,
            }

        if action == 'account':
            cat_payload = self.cli.catalog_models(refresh=body.get('refresh') is True)
            user_status = cat_payload.get('userStatus') or {}
            plan_status = user_status.get('planStatus') or {}
            configs = user_status.get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
            limits = []
            seen_bases = set()
            for c in configs:
                mid = c.get('modelId') or ''
                base_id = mid.rsplit('-', 1)[0] if '-' in mid else mid
                if base_id in seen_bases:
                    continue
                seen_bases.add(base_id)
                q_info = c.get('quotaInfo') or {}
                frac = q_info.get('remainingFraction')
                pct = round(frac * 100.0, 1) if frac is not None else 0.0
                reset_iso = q_info.get('resetTime')
                resets_at = None
                if reset_iso:
                    try:
                        resets_at = int(datetime.fromisoformat(reset_iso.replace('Z', '+00:00')).timestamp())
                    except Exception:
                        pass
                limits.append({
                    'name': c.get('label') or mid,
                    'windows': [
                        {
                            'remainingPercent': pct,
                            'resetsAt': resets_at,
                            'windowDurationMins': 1440,
                        }
                    ],
                })
            return {
                'account': {
                    'provider': 'antigravity',
                    'name': user_status.get('name') or 'Antigravity User',
                    'email': user_status.get('email') or '',
                    'tier': cat_payload.get('tier') or 'Google AI Pro',
                    'status': 'ready',
                },
                'quota': {'limits': limits},
                'credits': {
                    'prompt': plan_status.get('availablePromptCredits', 500),
                    'flow': plan_status.get('availableFlowCredits', 100),
                },
                'models': cat_payload['models'],
                'notice': cat_payload.get('notice') or '',
                'capabilities': {
                    'models': True,
                    'effort': True,
                    'manualModels': False,
                    'manualEffort': False,
                },
            }

        if action == 'create':
            prompt = self._compose_prompt(body)
            if not prompt:
                raise ValueError('新建 Antigravity 對話需要輸入首條訊息')
            project = body.get('directoryProject')
            surface_key = 'app'
            cwd = ''
            if isinstance(project, dict):
                surface_key = str(project.get('surface') or 'app')
                cwd = str(project.get('cwd') or '')
            if not cwd and body.get('projectKey'):
                matched = next(
                    (p for p in self.store.discover_projects() if p['key'] == body.get('projectKey')),
                    None,
                )
                if matched:
                    surface_key = matched.get('surface', 'app')
                    cwd = matched.get('cwd', '')
            if surface_key not in ('app', 'ide', 'cli'):
                surface_key = 'app'
            model = body.get('model') or 'gemini-3.8-flash'
            effort = body.get('effort') or 'high'
            cid = self.cli.start_cascade(source='CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT', model=model, effort=effort)
            if not cid:
                raise BridgeUnavailable('Antigravity Language Server 未返回新會話 ID')
            self.cli.send_user_message(cid, prompt, model=model, effort=effort)
            with self._lock:
                self._settings[cid] = {
                    'model': str(model),
                    'effort': str(effort),
                }
                self._save_settings()
            new_sid = gateway_id(surface_key, cid)
            return {
                'status': 'accepted',
                'id': new_sid,
                'sessionId': new_sid,
                'result': {'id': new_sid, 'sessionId': new_sid},
            }

        row = self.store.resolve_session(sid)
        if row is None:
            raise ValueError('找不到指定的 Antigravity 會話')
        cid = row['nativeId']
        surface_root = row['surfaceRoot']
        surface_key = row['surfaceDirKey']
        session = self._decorate_session(row)

        if action == 'detail':
            limit = int(body.get('historyLimit') or 40)
            before = body.get('historyBefore')
            is_active = session['status'] == 'active'
            page = self.store.read_transcript_page(
                surface_root,
                cid,
                limit=limit,
                before=before,
                session_active=is_active,
            )
            messages = list(page['messages'])
            turns = list(page['turns'])
            queued_items = self.hooks.get_queued_items(cid)
            session['queued'] = queued_items
            session['submissions'] = queued_items
            if page.get('contextUsage'):
                session['contextUsage'] = page['contextUsage']
            session['subagentTasks'] = self.store.list_tasks(surface_root, cid)
            return {
                'connected': True,
                'session': session,
                'messages': messages,
                'turns': turns,
                'requests': session['requests'],
                'queued': queued_items,
                'history': page['history'],
                'draftAccountIdentity': self.account_scope(),
                'capabilities': session['capabilities'],
            }

        if action == 'catalog':
            prefs = self._session_settings(cid)
            cat_payload = self.cli.catalog_models(refresh=body.get('refresh') is True)
            return {
                'models': cat_payload['models'],
                'currentModel': prefs['model'],
                'currentEffort': prefs['effort'],
                'notice': cat_payload.get('notice') or '',
                'usageSummary': cat_payload.get('usageSummary'),
                'skills': [],
                'capabilities': {
                    'models': True,
                    'effort': True,
                    'manualModels': False,
                    'manualEffort': False,
                    'skills': False,
                },
            }

        if action == 'tasks':
            return {'tasks': self.store.list_tasks(surface_root, cid)}

        if action == 'image':
            return self.store.read_image(surface_root, cid, body.get('source'))

        if action == 'access':
            if 'mode' in body:
                mode = self.hooks.set_permission_mode(cid, body.get('mode'))
            else:
                mode = self.hooks.permission_mode(cid)
            options = [
                {'value': val, 'label': label, 'description': desc}
                for val, label, desc in PERMISSION_OPTIONS
            ]
            return {'status': 'accepted', 'mode': mode, 'options': options}

        if action == 'settings':
            prefs = self._session_settings(cid)
            if 'model' in body and isinstance(body['model'], str) and body['model'].strip():
                prefs['model'] = body['model'].strip()
            if 'effort' in body and isinstance(body['effort'], str) and body['effort'].strip():
                prefs['effort'] = body['effort'].strip().lower()
            with self._lock:
                self._settings[cid] = prefs
                self._save_settings()
            return {'status': 'accepted', **prefs}

        if action == 'respond':
            self.hooks.resolve_approval(cid, body.get('requestId'), body.get('decision'))
            return {'status': 'accepted'}

        if action == 'queue-action':
            queue_id = str(body.get('queueId') or '').strip()
            op = str(body.get('operation') or '').strip()
            if not queue_id:
                raise ValueError('缺少佇列項目 ID')
            if op in ('steer', 'queue'):
                item = self.hooks.update_queue_mode(cid, queue_id, op)
                if not item:
                    raise ValueError('找不到指定的排隊訊息')
                return {'status': 'accepted', 'item': item}
            elif op == 'cancel':
                item = self.hooks.remove_queued_item(cid, queue_id)
                return {'status': 'accepted', 'removed': bool(item)}
            elif op == 'edit':
                item = self.hooks.remove_queued_item(cid, queue_id)
                text = (item or {}).get('text', '')
                return {'status': 'accepted', 'text': text}
            raise ValueError(f'不支援的佇列操作: {op}')

        if action == 'stop':
            self.cli.stop(cid)
            self.hooks.request_stop(cid)
            return {'status': 'accepted'}

        if action == 'resume':
            return {'status': 'ready'}

        if action == 'send':
            prompt = self._compose_prompt(body)
            if not prompt:
                raise ValueError('訊息內容不能為空')
            mode = str(body.get('mode') or 'send').strip()
            if mode not in ('send', 'queue', 'steer'):
                raise ValueError('不支援的發送模式')

            sub_id = str(body.get('id') or body.get('submissionId') or '').strip()
            text_hash = hashlib.sha256(f"{cid}:{prompt}:{mode}".encode('utf-8')).hexdigest()[:16]
            dedup_key = f"{cid}:{sub_id or text_hash}"

            now = time.time()
            with self._in_flight_lock:
                expired = [k for k, v in self._recent_submissions.items() if now - v[0] > 120.0]
                for k in expired:
                    self._recent_submissions.pop(k, None)

                if dedup_key in self._recent_submissions:
                    ts, cached_result = self._recent_submissions[dedup_key]
                    if now - ts < 45.0:
                        return cached_result

                if cid in self._in_flight_sends:
                    raise BridgeUnavailable('此會話已有正在處理中的發送請求，請勿重複提交')
                self._in_flight_sends.add(cid)

            try:
                native_busy = self.store.is_native_busy(surface_root, cid)
                hook_alive = self.hooks.has_recent_heartbeat(cid) or self.hooks.has_pending_approvals(cid)
                cli_busy = self.cli.is_running(cid)
                if (native_busy or hook_alive or cli_busy) and mode in ('queue', 'steer'):
                    queue_mode = 'steer' if mode == 'steer' else 'queue'
                    entry = self.hooks.enqueue_follow_up(cid, prompt, mode=queue_mode)
                    res = {
                        'status': 'accepted',
                        'delivery': f'hook-{queue_mode}',
                        'queueId': entry['id'],
                    }
                else:
                    prefs = self._session_settings(cid)
                    self.cli.send_user_message(
                        cid,
                        prompt,
                        model=prefs.get('model'),
                        effort=prefs.get('effort'),
                    )
                    res = {'status': 'accepted', 'delivery': 'ls-native'}

                with self._in_flight_lock:
                    self._recent_submissions[dedup_key] = (now, res)
                return res
            finally:
                with self._in_flight_lock:
                    self._in_flight_sends.discard(cid)

        raise ValueError(f'Antigravity 不支援的操作: {action}')

    def close(self):
        self.cli.close()
        self.hooks.close()
