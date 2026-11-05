"""Allowlisted desktop integrations with durable at-most-once mutation receipts."""
import hashlib
import os
import json
import sqlite3
import threading
import time
import socket
import subprocess
import secrets
import sys
import uuid
import weakref
from pathlib import Path
from contextlib import closing, contextmanager, nullcontext
from concurrent.futures import Future

from bridge.clients.claude.adapter import Claude
from bridge.clients.claude.accounts import ClaudeLoginDatabaseUnavailable
from bridge.clients.claude.remote_worker import draft_account_identity as claude_draft_account_identity
from bridge.clients.deepseek.adapter import DeepSeek, BRIDGE_REVISION, LIFECYCLE_REVISIONS, UPDATE_REASON
from bridge.clients.errors import BridgeUnavailable, TaskStateUnavailable, ResumePreflightBlocked
from bridge.clients.codex.catalog import ModelStore, MODEL_REFRESH_SECONDS
from bridge.features.accounts.draft_identity import draft_identity
from bridge.app.lifecycle import private_json
from bridge.platforms.windows import session as windows_session
from bridge.features.sessions.pins import SessionPins
from bridge.features.sessions.archive import ArchiveCommands
from bridge.features.sessions.projects import Projects
from bridge.features.sessions.display_metadata import DisplayMetadata, RenameCommands
from bridge.features.sessions.organization_commands import OrganizationCommands
from bridge.features.sessions.organization import OrganizationNotSent
from bridge.features.sessions.create import project_directory, project_directory_listing
from bridge.clients.codex.remote import ssh_read, payload
from bridge.resources import source_text

READS = {'list', 'detail', 'catalog', 'projects', 'create-models', 'account', 'access', 'organization', 'tasks'}
WRITES = {'send', 'stop', 'settings', 'respond', 'create', 'access', 'resume', 'fork', 'pin', 'archive', 'rename', 'organization', 'goal', 'compact', 'stop-task', 'edit-message'}


CLAUDE_HISTORY_NOTICE = '客户端尚未提供当前可选模型，历史模型仅供查看'


def _claude_catalog_sources(value, account=None, *, legacy=False):
    """Keep model provenance separate from native setting capabilities."""
    account = account or {}
    groups = [group for group in account.get('groups', []) if isinstance(group, dict)]
    sources = {row['id']: group.get('source', 'account-catalog') for group in groups
               for row in group.get('models', []) if isinstance(row, dict) and isinstance(row.get('id'), str)}
    offered = {row['id']: {**row, 'source': sources.get(row['id'], 'account-catalog')}
               for row in account.get('models', []) if isinstance(row, dict) and isinstance(row.get('id'), str)}
    native_source = value.get('source') or ('cached-unverified' if legacy else 'desktop-catalog')
    for row in value.get('models', []):
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            continue
        source = row.get('source', native_source)
        prior = offered.get(row['id'], {})
        # Preserve native reasoning metadata, but history cannot override a known provider source.
        if prior and source in ('session-history', 'cached-unverified'):
            source = prior['source']
        offered[row['id']] = {**prior, **row, 'source': source}
    result = {**value, 'models': list(offered.values()), 'modelSourcesVersion': 1}
    origins = {row['source'] for row in result['models']}
    result['source'] = next(iter(origins)) if len(origins) == 1 else 'mixed' if origins else native_source
    notices = [value['notice']] if value.get('notice') and value['notice'] != CLAUDE_HISTORY_NOTICE else []
    for group in groups:
        if group.get('notice') and group['notice'] not in notices:
            notices.append(group['notice'])
    if 'cached-unverified' in origins:
        notices.append('列表包含来源尚未核验的旧缓存条目，可刷新模型列表或手动输入。')
    elif 'session-history' in origins:
        notices.append(CLAUDE_HISTORY_NOTICE if origins == {'session-history'} else
                       '列表包含当前接入模型及历史模型，历史模型的可用性需由客户端确认。')
    if notices:
        result['notice'] = '\n'.join(notices)
    else:
        result.pop('notice', None)
    return result


def _copy_read(value):
    # Native responses are JSON; only containers need copying, not large strings.
    if isinstance(value, dict):
        return {key: _copy_read(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_read(item) for item in value]
    return value


class DesktopSessions:
    def __init__(self, directory, bridge=None, *, gateway_running=False, instance=None, instance_owner=None):
        if instance is not None and (bridge is not None or instance_owner is None):
            raise ValueError('独立桌面配置不能借用默认执行环境')
        self.instance, self.instance_owner = instance, instance_owner
        self.instances = None
        self.bridge = bridge
        self.gateway_running = gateway_running
        self.closed = threading.Event()
        self.startup_thread = None
        self.client_lock = threading.RLock()
        self.client_cache = None
        self.client_snapshot = None
        self.client_snapshot_lock = threading.Lock()
        self.client_refreshing = False
        self.client_refresh_error = False
        self.client_refresh_retry_at = 0
        self.workspace_roots = {}
        self.account_stores = {}
        self.account_operations = set()
        self.cli_sync_status = {}
        self.deepseek_recovery = None
        self.claude_quit = None
        self.zcode_pipe = None
        self.directory = Path(directory)/'desktop-sessions'
        from bridge.features.workspace.history_images import HistoryImages
        self.history_images = HistoryImages()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.session_pins = {provider: SessionPins(self.directory/provider, provider) for provider in ('claude', 'deepseek')}
        self.display_metadata = DisplayMetadata(self.directory, private_json)
        self.rename_commands = RenameCommands(self.directory, private_json)
        self.organization_commands = OrganizationCommands(self.directory, private_json)
        self.model_store = ModelStore(self.directory)
        self.directory_projects = Projects(self.directory/'projects')
        self.model_locks = {}
        self.model_refreshing = set()
        self.model_retry_at = {}
        self.model_capabilities = weakref.WeakKeyDictionary()
        self.model_timers = {}
        self.model_lock = threading.Lock()
        self.config_path = self.directory/'settings.json'
        self.config = json.loads(self.config_path.read_text(encoding='utf-8-sig')) if self.config_path.exists() else {}
        self.claude_wsl = None
        if sys.platform == 'win32' and instance is None:
            from bridge.clients.claude.wsl import WslClaudeEntries
            self.claude_wsl = WslClaudeEntries(self.directory)
        self.adapters = {}
        if instance is None or instance.family == 'deepseek':
            self.adapters['deepseek'] = DeepSeek(self.directory/'deepseek',
                str(instance.profile.home) if instance else self.config.get('deepseekHome'))
        if instance is None or instance.family == 'claude':
            self.adapters['claude'] = Claude(self.directory/'claude', auto_connect=False,
                                          hosts=bridge.hosts if bridge is not None and hasattr(bridge, 'hosts') else None,
                                          wsl_entries=self.claude_wsl, desktop_only=instance is not None,
                                          descriptor=lambda: self._descriptor('claude'))
        if bridge is not None and getattr(bridge, 'cli_access', None) is not None:
            bridge.desktop_sessions = self
            self.adapters['claude'].cli.profile_resolver = bridge.cli_access.claude_profile
        self.deepseek_restore_error = None
        if instance is not None and any(key in self.config for key in ('deepseekManualBinding', 'zcodePrototype')):
            raise ValueError('独立配置不能使用默认或第三方客户端绑定')
        if self.config.get('deepseekManualBinding'):
            from bridge.clients.deepseek.manual import ManualDeepSeek
            try:
                binding = self.config['deepseekManualBinding']
                self.adapters['deepseek'] = ManualDeepSeek(binding['file'], binding['instanceId'])
            except (OSError, ValueError, TypeError, KeyError):
                self.deepseek_restore_error = '手动 DSH 绑定无法验证，请重新绑定；不会切换到其他实例'
        elif self.config.get('deepseekAdapterDirectory') and 'deepseek' in self.adapters:
            try:
                self.adapters['deepseek'].use_existing(self.config['deepseekAdapterDirectory'])
            except (OSError, ValueError, TypeError):
                self.deepseek_restore_error = '已有 Harness 接入无法验证，请重新扫描'
                self.config.setdefault('setup', {})['deepseek'] = {
                    'setupStatus': 'failed', 'reason': '已有 Harness 接入无法验证，请重新扫描'}
        self._configure_zcode()
        self.locks = {name: threading.RLock() for name in ('codex', 'zcode', *self.adapters)}
        self.read_lock = threading.Lock()
        self.reads = {}
        self.pending_resumes = {provider: {} for provider in self.locks}
        self.activity = {}
        self.activity_pending = set()
        self.read_epochs = dict.fromkeys(self.locks, 0)
        self.read_changes = dict.fromkeys(self.locks, 0)
        self.binding_epochs = dict.fromkeys(self.locks, 0)
        self.binding_changes = dict.fromkeys(self.locks, 0)
        self.db_lock = threading.Lock()
        self.db = sqlite3.connect(self.directory/'requests.sqlite', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, result TEXT)')
        self.db.commit()
        (self.directory/'requests.sqlite').chmod(0o600)
        self.receipt_dbs = {}
        self._codex_guard = lambda: self.require_enabled('codex')
        if self.bridge is not None:
            self.bridge.accounts.require_client_enabled = self._codex_guard
        if instance is None:
            from bridge.clients.instances import ClientInstances
            self.instances = ClientInstances(self)
        else:
            from bridge.clients.uploads import DesktopUploads
            self.uploads = DesktopUploads(instance.state_root)

    def resolve_instance(self, family, entry=None, *, require_enabled=True):
        if self.instance is not None:
            if family != self.instance.family or entry not in (None, self.instance.id):
                raise ValueError('接入实例与平台不匹配')
            self.instance_owner.registry.resolve(family, self.instance.id, require_enabled=require_enabled)
            return self
        return self.instances.resolve(family, entry, require_enabled)

    def instance_rows(self):
        return self.instances.rows() if self.instances is not None else []

    def instance_control(self, value):
        if self.instances is None:
            raise ValueError('请从网关管理独立接入')
        return self.instances.control(value)

    def _configure_zcode(self):
        if self.instance is not None:
            return
        if sys.platform not in ('darwin', 'win32', 'linux'):
            return
        from bridge.clients.zcode.adapter import ZCodeDesktop, ZCodePrototype
        legacy = self.config.get('zcodePrototype')
        row = self.config.get('discovered', {}).get('zcode', {})
        if not legacy and not row.get('installed'):
            if 'zcode' in self.adapters:
                self.adapters = {key: item for key, item in self.adapters.items() if key != 'zcode'}
            return
        from bridge.clients.zcode.connections import selected_mode
        source = row if row.get('installed') else legacy or {}
        application = source.get('application')
        if application:
            if sys.platform == 'darwin':
                from bridge.clients.zcode.pipe_host import PipeHostClient
            else:
                from bridge.clients.zcode.local_pipe_host import LocalPipeClient as PipeHostClient
            if self.zcode_pipe is None or str(self.zcode_pipe.application) != application:
                self.zcode_pipe = PipeHostClient(self.directory.parent, application)
        else:
            self.zcode_pipe = None
        if selected_mode(self.config) == 'official-remote':
            from bridge.clients.local_rpc import LocalService
            from bridge.clients.zcode.official import ZCodeOfficial
            config = {key: source[key] for key in ('application', 'dataDirectory') if key in source}
            config['officialRemote'] = True
            previous = self.adapters.get('zcode')
            if previous and previous.config == config:
                return
            directory = self.directory/'zcode'
            adapter = ZCodeOfficial(directory, config, LocalService(self.directory.parent, 'zcode-official'))
            self.session_pins.setdefault('zcode', SessionPins(directory, 'zcode'))
            self.adapters = {**self.adapters, 'zcode': adapter}
            return
        private_pipe = self.zcode_pipe is not None and self.zcode_pipe.supported()
        if private_pipe:
            config = {key: source[key] for key in ('application', 'dataDirectory') if key in source}
            config['privatePipe'] = True
        else:
            config = {key: source[key] for key in ('application', 'dataDirectory') if key in source}
        previous = self.adapters.get('zcode')
        if previous and previous.config == config:
            return
        directory = self.directory/'zcode'
        if private_pipe:
            try:
                adapter = ZCodePrototype(directory, config, pipe=self.zcode_pipe if private_pipe else None)
            except (ValueError, KeyError, OSError):
                # A removed test workspace must not prevent the gateway from starting.
                adapter = ZCodeDesktop(directory, config)
        else:
            adapter = ZCodeDesktop(directory, config)
        if 'zcode' not in self.session_pins:
            self.session_pins['zcode'] = SessionPins(directory, 'zcode')
        # Status readers may be iterating the previous installation snapshot.
        self.adapters = {**self.adapters, 'zcode': adapter}

    def _receipts(self, adapter):
        # A recovered connector shares its original mutation receipts with the
        # still-running gateway. Never replay an uncertain request after recovery.
        path = getattr(adapter, 'receipts_path', None)
        if not isinstance(path, (str, Path)):
            return self.db
        path = Path(path).resolve()
        if path == self.directory/'requests.sqlite':
            return self.db
        if path not in self.receipt_dbs:
            if not path.is_file():
                raise BridgeUnavailable('此前接入的操作记录不可用，请重新扫描')
            self.receipt_dbs[path] = sqlite3.connect(path, check_same_thread=False)
        return self.receipt_dbs[path]

    @contextmanager
    def _changing(self, provider, rebind=True):
        # Keep progress reads available during writes, but never join a read from
        # an earlier account/connector generation. Safety checks bypass _read.
        with self.locks[provider]:
            if rebind and provider == 'zcode' and provider in self.adapters:
                self.adapters[provider].release_window()
            if rebind:
                self.pending_resumes[provider].clear()
            with self.read_lock:
                self.read_epochs[provider] += 1
                self.read_changes[provider] += 1
                if rebind:
                    self.binding_epochs[provider] += 1
                    self.binding_changes[provider] += 1
            try:
                yield
            finally:
                with self.read_lock:
                    self.read_epochs[provider] += 1
                    self.read_changes[provider] -= 1
                    if rebind:
                        self.binding_epochs[provider] += 1
                        self.binding_changes[provider] -= 1

    def _read(self, provider, action, sid=None, body=None):
        body = body or {}
        with self.read_lock:
            if self.closed.is_set():
                raise BridgeUnavailable('客户端连接已关闭')
            adapter = self.adapters[provider]
            epoch = self.read_epochs[provider]
            binding = self.binding_epochs[provider]
            key = (provider, id(adapter), epoch, action, sid, json.dumps(body, sort_keys=True))
            future = self.reads.get(key) if not self.read_changes[provider] else None
            owner = future is None
            if owner:
                future = Future()
                if not self.read_changes[provider]:
                    self.reads[key] = future
        if owner:
            try:
                value = self._adapter_call(provider, action, sid, body)
                if action == 'detail' and isinstance(value, dict):
                    from bridge.features.sessions.model import desktop_file_attachments, user_display_text
                    for message in value.get('messages', []):
                        if message.get('role') == 'user':
                            text = message.get('text', '')
                            files = desktop_file_attachments(text)
                            if files:
                                message['text'] = user_display_text(text)
                                message.setdefault('attachments', []).extend(files)
                    if provider == 'zcode':
                        from bridge.clients.zcode.attachments import bind_project_files
                        with self.db_lock:
                            bind_project_files(self._receipts(adapter), sid, value.get('messages', []),
                                               value.get('session', {}).get('cwd'))
                    self.history_images.bind(provider, sid, value.get('messages', []))
                    from bridge.features.workspace.files import workspace_references
                    value['files'] = workspace_references(value.get('messages', []), value.get('session', {}).get('cwd'))
                    # A truncated tail or any older page can start or end in
                    # the middle of a turn. Its last turn is not current state.
                    history = value.get('history')
                    partial_history = body.get('historyBefore') is not None or isinstance(history, dict) and history.get('hasMore') is True
                    if not partial_history and value.get('turns') and isinstance(value.get('session'), dict):
                        last = value['turns'][-1]
                        value['session'].update(turnId=last.get('turnId'), turnStatus=last.get('status'))
                future.set_result(value)
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                with self.read_lock:
                    if self.reads.get(key) is future:
                        del self.reads[key]
        # Copy before the last generation check so a slow copy cannot publish
        # a response from an account that was switched in the meantime.
        result = _copy_read(future.result())
        with self.read_lock:
            if self.closed.is_set() or self.binding_epochs[provider] != binding or self.adapters.get(provider) is not adapter:
                raise BridgeUnavailable('客户端状态已变化，请重试')
        # Consumers may annotate rows. Sharing a result object crosses requests.
        return result

    def _model_identity(self, provider, sid, creation=None):
        adapter = self.adapters[provider]
        if provider in ('deepseek', 'zcode'):
            return self._account_model_identity(provider)
        if provider == 'claude' and creation is None and sid in adapter.index:
            return self._surface_model_identity(self._account_model_identity(provider), adapter.index[sid]['backend'])
        paths = []
        scope = None
        if provider == 'claude':
            with adapter.cli.lock, adapter.remote_cli.lock:
                local = adapter.cli.index.get(sid)
                remote = adapter.remote_cli.index.get(sid)
            if creation is not None:
                local = creation if creation.get('host', 'local') == 'local' else None
                remote = creation if local is None else None
            if remote:
                host = adapter.remote_cli.hosts.hosts().get(remote['host']) if adapter.remote_cli.hosts else None
                if not host or host['alias'] != remote['alias']:
                    raise BridgeUnavailable('SSH 主机配置已变化')
                scope = ('wsl-cli' if remote.get('runtime') == 'wsl' else 'ssh-cli', remote['host'], remote['alias'], remote['origin'], remote['cwd'],
                         remote.get('projectConfig'))
            elif local:
                profile = adapter.cli.profile_resolver()
                if not profile or profile['origin'] != local['origin']:
                    raise BridgeUnavailable('Claude Code 接入已变化')
                directory = Path(profile['directory'])
                paths.extend((directory/'.credentials.json', directory/'settings.json'))
                paths.extend(Path(profile[key]) for key in ('authPath', 'configPath') if profile.get(key))
                paths.extend((Path(local['cwd'])/'.claude/settings.json', Path(local['cwd'])/'.claude/settings.local.json'))
                scope = ('local-cli', profile['origin'], str(directory), local['cwd'],
                         tuple((name, os.environ.get(name)) for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                           'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_CUSTOM_HEADERS')))
            else:
                raise ValueError('找不到 Claude 会话')
        stamps = []
        for path in paths:
            try:
                stamps.append(hashlib.sha256(path.read_bytes()).hexdigest())
            except OSError:
                stamps.append(None)
        return ModelStore.key('desktop', provider, scope, stamps)

    @staticmethod
    def _surface_model_identity(directory_identity, surface):
        return ModelStore.key('desktop-surface-models-v1', directory_identity, surface)

    def _desktop_model_directory(self, provider, surface=None, *, refresh=False):
        value = self._account_models(provider, refresh=refresh)
        if provider != 'claude' or surface is None:
            return value
        directory_identity = value['cache']['identity']
        groups = []
        for row in value.get('groups', []):
            if isinstance(row, dict):
                groups.append({**row, **_claude_catalog_sources(row)})
        group = next((row for row in groups if row.get('surface') == surface), None)
        api_only = bool(groups) and all(row.get('surface') == 'api' for row in groups)
        if group is not None:
            selected = group
        elif api_only:
            rows = {row['id']: row for group in groups for row in group['models']}
            selected = {'models': list(rows.values())}
        elif not groups:
            selected = {'models': value.get('models', []), 'source': value.get('source', 'account-catalog')}
        else:
            selected = {'models': []}
        projected = _claude_catalog_sources(selected)
        return {**value, **projected, 'groups': groups, 'cache': {**value['cache'],
            'identity': self._surface_model_identity(directory_identity, surface),
            'directoryIdentity': directory_identity, 'surface': surface}}

    def _cli_model_capabilities(self, sid, identity, value=None):
        """Keep verified controls on the current owner, outside shared catalogs."""
        adapter = self.adapters['claude']
        with adapter.cli.lock, adapter.remote_cli.lock:
            row = adapter.cli.index.get(sid)
            owned = adapter.cli.owned.get(row['nativeId']) if row else adapter.remote_cli.owned.get(sid)
            row = row or adapter.remote_cli.index.get(sid)
            if (not row or not owned or owned.origin != row['origin'] or
                    owned.process.poll() is not None or owned.error):
                return {'models': False, 'effort': False, 'manualModels': False, 'manualEffort': False}
        evidence = self.model_capabilities.get(owned)
        epoch = self.binding_epochs['claude']
        if value is None and evidence and evidence[:2] == (identity, epoch):
            return _copy_read(evidence[2])
        if value is None:
            # The native helper uses the existing process's initialize catalog
            # and reads get_settings; it does not list models or claim a chat.
            value = self._read('claude', 'catalog', sid, {'section': 'models'})
        if self._model_identity('claude', sid) != identity:
            raise BridgeUnavailable('客户端账号或配置已变化，请重试')
        source = value.get('capabilities', {})
        capabilities = {key: source.get(key) is True for key in ('models', 'effort', 'manualModels', 'manualEffort')}
        with adapter.cli.lock, adapter.remote_cli.lock:
            current = adapter.cli.owned.get(row['nativeId']) if sid in adapter.cli.index else adapter.remote_cli.owned.get(sid)
            if (current is not owned or owned.process.poll() is not None or owned.error or
                    self.binding_epochs['claude'] != epoch):
                return {'models': False, 'effort': False, 'manualModels': False, 'manualEffort': False}
            self.model_capabilities[owned] = (identity, epoch, capabilities)
        return _copy_read(capabilities)

    def _draft_account_identity(self, provider, sid, detail):
        """Use verified account ownership only; unknown routes stay transient."""
        if provider == 'zcode':
            return detail.get('draftAccountIdentity', '')
        adapter = self.adapters[provider]
        if provider == 'claude':
            with adapter.cli.lock, adapter.remote_cli.lock:
                local = adapter.cli.index.get(sid)
                remote = adapter.remote_cli.index.get(sid)
            if remote:
                owner = (detail.get('session') or {}).get('draftAccountIdentity')
                return draft_identity('claude', 'wsl-cli' if remote.get('runtime') == 'wsl' else 'ssh-cli', remote['host'], owner) if owner else ''
            if local:
                profile = adapter.cli.profile_resolver()
                if not profile or local['origin'] != profile['origin']:
                    return ''
                access = getattr(self.bridge, 'cli_access', None) if self.bridge else None
                if access:
                    with access.lock:
                        identifier = access.index.get('activeClaude')
                        row = next((item for item in access.index['claude'] if item['id'] == identifier), None)
                    if (row and row['kind'] == 'api' and
                            profile['origin'].startswith(row['originBase']+':')):
                        key = json.loads(Path(row['secretPath']).read_text(encoding='utf-8')).get('key')
                        return draft_identity('claude', 'local-cli', row['id'], row.get('baseUrl'),
                                              row.get('authScheme'), key) if key and row.get('baseUrl') else ''
                owner = claude_draft_account_identity(profile['directory'], local.get('cwd'))
                if owner:
                    return draft_identity('claude', 'local-cli', owner)
                return ''
            if sid not in adapter.index:
                return ''
            store = self._account_store('claude')
            with store.lock:
                kind, fingerprint, gateway = store._current()
                if kind == 'api' and gateway:
                    config = gateway['config']
                    endpoint = config.get('inferenceGatewayBaseUrl')
                    credential = config.get('inferenceGatewayApiKey')
                    owner = ('api', gateway['id'], endpoint,
                             config.get('inferenceGatewayAuthScheme', 'bearer'), credential) if endpoint and credential else None
                elif kind == 'official' and fingerprint:
                    row = next((item for item in store._index()['accounts']
                                if item['kind'] == kind and item.get('fingerprint') == fingerprint), None)
                    owner = ('saved', row['id'], fingerprint) if row else ('current', fingerprint)
                else:
                    owner = None
            return draft_identity('claude', 'desktop', *owner) if owner else ''

        session = detail.get('session') or {}
        model = session.get('model') or ''
        source = model.split('/', 1)[0]
        kind = {'deepseek-account': 'official', 'deepseek-official': 'api'}.get(source)
        if not kind:
            return ''
        store = self._account_store('deepseek')
        with store.lock:
            snapshot = store.public()
            from bridge.clients.deepseek.accounts import _read, _document, _credential, _fingerprint
            current = _credential(_document(_read(store.path)), kind)
            fingerprint = _fingerprint(kind, current) if current is not None else None
        active = [row for row in snapshot['accounts']
                  if row['id'] in snapshot['activeIds'] and row['kind'] == kind]
        if len(active) != 1:
            return ''
        row = active[0]
        owner = row.get('accountId') if kind == 'official' else None
        owner = owner or fingerprint
        return draft_identity('deepseek', 'desktop', kind, owner) if owner else ''

    def _model_live(self, provider, sid):
        adapter = self.adapters[provider]
        if provider == 'zcode':
            return adapter.status().get('connected') is True
        if provider == 'deepseek':
            return (adapter.directory/'endpoint.json').is_file()
        with adapter.cli.lock, adapter.remote_cli.lock:
            local = adapter.cli.index.get(sid)
            remote = adapter.remote_cli.index.get(sid)
            if local:
                owned = adapter.cli.owned.get(local['nativeId'])
                return bool(owned and owned.origin == local['origin'] and owned.process.poll() is None and not owned.error)
            if remote:
                owned = adapter.remote_cli.owned.get(sid)
                return bool(owned and owned.origin == remote['origin'] and owned.process.poll() is None and not owned.error)
        return bool(adapter.desktop and adapter.desktop.connected and not adapter.setup_cancel.is_set()
                    and not adapter.connection_probe_failed and sid in adapter.index)

    def _model_refresh_unsupported(self, provider, sid):
        if provider != 'claude':
            return False
        adapter = self.adapters[provider]
        with adapter.cli.lock, adapter.remote_cli.lock:
            local = adapter.cli.index.get(sid)
            owned = (adapter.cli.owned.get(local['nativeId']) if local else
                     adapter.remote_cli.owned.get(sid))
        return bool(owned and getattr(owned, 'model_list_unsupported', False))

    def _cached_model_result(self, provider, sid, identity, persisted, *, error=None, unavailable=False):
        if self._model_identity(provider, sid) != identity:
            raise BridgeUnavailable('客户端账号或配置已变化，请重试')
        result = ModelStore.directory(persisted[1])
        live = self._model_live(provider, sid)
        if not live or unavailable:
            result['capabilities'] = {**result.get('capabilities', {}), 'models': False, 'effort': False}
            result['notice'] = '请先继续这条会话' if provider == 'claude' else '桌面客户端未连接'
        elif provider == 'claude':
            result['capabilities'] = self._cli_model_capabilities(sid, identity)
        result['cache'] = {'state': 'stale' if error or time.time()-persisted[0] >= MODEL_REFRESH_SECONDS else 'fresh',
                           'updatedAt': persisted[0], 'identity': identity}
        if error:
            result['modelError'] = error
            if error.startswith('当前 Claude Code 版本不支持刷新模型目录'):
                result['notice'] = error
        if self._model_identity(provider, sid) != identity:
            raise BridgeUnavailable('客户端账号或配置已变化，请重试')
        return result

    def _catalog_models(self, provider, sid, body):
        native_claude = provider == 'claude' and sid in self.adapters[provider].index
        if provider in ('deepseek', 'zcode') or native_claude:
            identity = self._model_identity(provider, sid)
            surface = self.adapters[provider].index[sid]['backend'] if native_claude else None
            value = self._desktop_model_directory(provider, surface, refresh=body.get('refresh') is True)
            if self._model_identity(provider, sid) != identity or value.get('cache', {}).get('identity') != identity:
                raise BridgeUnavailable('客户端账号或配置已变化，请重试')
            value = {**ModelStore.directory(value), **{key: value[key] for key in
                     ('cache', 'notice', 'modelError') if key in value}}
            if not self._model_live(provider, sid):
                value['capabilities'] = {'models': False, 'effort': False}
                value['notice'] = '桌面客户端未连接'
            elif native_claude:
                native = self.adapters[provider].session_capabilities(surface)
                value['capabilities'] = {key: native[key] for key in ('models', 'effort', 'manualModels', 'manualEffort')}
            return value
        identity = self._model_identity(provider, sid)
        refresh = body.get('refresh') is True
        try:
            persisted = self.model_store.read(identity)
        except (OSError, sqlite3.DatabaseError, ValueError):
            persisted = None
        if persisted and not refresh:
            if (self._model_live(provider, sid) and not self._model_refresh_unsupported(provider, sid)
                    and time.time() - persisted[0] >= MODEL_REFRESH_SECONDS):
                self._start_model_refresh(provider, sid, identity)
            elif self._model_live(provider, sid) and not self._model_refresh_unsupported(provider, sid):
                self._schedule_model_refresh(provider, sid, identity, persisted[0])
            return self._cached_model_result(provider, sid, identity, persisted)
        with self.model_lock:
            lock = self.model_locks.setdefault(identity, threading.Lock())
        with lock:
            if not refresh:
                try:
                    persisted = self.model_store.read(identity)
                except (OSError, sqlite3.DatabaseError, ValueError):
                    persisted = None
                if persisted:
                    return self._cached_model_result(provider, sid, identity, persisted)
            try:
                value = self._read(provider, 'catalog', sid, body)
            except (OSError, ValueError, BridgeUnavailable) as exc:
                if persisted:
                    error = ('当前 Claude Code 版本不支持刷新模型目录，已保留上次结果'
                             if str(exc) == '当前 Claude Code 版本不支持刷新模型目录' else
                             '模型目录刷新失败，已保留上次结果')
                    return self._cached_model_result(provider, sid, identity, persisted, error=error)
                raise
            if self._model_identity(provider, sid) != identity:
                raise BridgeUnavailable('客户端账号或配置已变化，请重试')
            if provider == 'claude':
                self._cli_model_capabilities(sid, identity, value)
            value['cache'] = {'state': 'unavailable', 'updatedAt': None, 'identity': identity}
            if (value.get('modelError') or value.get('capabilities', {}).get('models') is False) and persisted:
                return self._cached_model_result(provider, sid, identity, persisted,
                    error=value.get('modelError') or '模型目录刷新失败，已保留上次结果',
                    unavailable=value.get('capabilities', {}).get('models') is False)
            if isinstance(value.get('models'), list) and value.get('capabilities', {}).get('models') is not False:
                stable = ModelStore.directory(value)
                try:
                    updated_at = self.model_store.write(identity, stable)
                    self._schedule_model_refresh(provider, sid, identity, updated_at, replace=True)
                    value['cache'] = {'state': 'fresh', 'updatedAt': updated_at, 'identity': identity}
                except (OSError, sqlite3.DatabaseError):
                    pass
            if self._model_identity(provider, sid) != identity:
                raise BridgeUnavailable('客户端账号或配置已变化，请重试')
            return value

    def _start_model_refresh(self, provider, sid, identity):
        with self.model_lock:
            if (self.closed.is_set() or not self._model_live(provider, sid) or
                    self._model_refresh_unsupported(provider, sid) or identity in self.model_refreshing or
                    time.time() < self.model_retry_at.get(identity, 0)):
                return
            self.model_refreshing.add(identity)
        def update():
            try:
                if self._model_live(provider, sid) and self._model_identity(provider, sid) == identity:
                    self._catalog_models(provider, sid, {'section': 'models', 'refresh': True})
            except (OSError, ValueError, BridgeUnavailable):
                pass
            finally:
                with self.model_lock:
                    self.model_retry_at[identity] = time.time() + 60
                    self.model_refreshing.discard(identity)
                if (not self.closed.is_set() and self._model_live(provider, sid) and
                        not self._model_refresh_unsupported(provider, sid) and self._model_identity(provider, sid) == identity):
                    try:
                        record = self.model_store.read(identity)
                    except (OSError, sqlite3.DatabaseError, ValueError):
                        record = None
                    self._schedule_model_refresh(provider, sid, identity, record[0] if record else time.time(), retry=True)
        threading.Thread(target=update, daemon=True, name='desktop-model-refresh-'+provider).start()

    def _schedule_model_refresh(self, provider, sid, identity, updated_at, retry=False, replace=False):
        with self.model_lock:
            if replace and identity in self.model_timers:
                self.model_timers.pop(identity).cancel()
            if self.closed.is_set() or self._model_refresh_unsupported(provider, sid) or identity in self.model_timers:
                return
            delay = max(60 if retry else 1, updated_at + MODEL_REFRESH_SECONDS - time.time())
            def due():
                with self.model_lock:
                    self.model_timers.pop(identity, None)
                if (not self.closed.is_set() and self._model_live(provider, sid) and
                        not self._model_refresh_unsupported(provider, sid) and self._model_identity(provider, sid) == identity):
                    self._start_model_refresh(provider, sid, identity)
            timer = threading.Timer(delay, due)
            timer.daemon = True
            self.model_timers[identity] = timer
            timer.start()

    def _account_model_identity(self, provider):
        adapter = self.adapters[provider]
        if provider == 'zcode':
            return ModelStore.key('desktop-model-directory-v2', provider, str(adapter.home),
                                  adapter.account_scope())
        if provider == 'deepseek' and self.config.get('deepseekManualBinding'):
            if self.deepseek_restore_error:
                raise BridgeUnavailable(self.deepseek_restore_error)
            adapter._connection()
            endpoint = self._deepseek_endpoint(adapter)
            paths = [Path(adapter.binding['profileDirectory'])/'cordis.patch.yml',
                     adapter.home/'.credentials.yaml', adapter.home/'settings.yaml']
            stamps = []
            for path in paths:
                try:
                    stamps.append(hashlib.sha256(path.read_bytes()).hexdigest())
                except OSError:
                    stamps.append(None)
            return ModelStore.key('desktop-model-directory-v2', provider, adapter.binding, endpoint, stamps)
        store = self._account_store(provider)
        paths = []
        if provider == 'claude':
            with store.lock:
                kind, fingerprint, _ = store._current()
            if not fingerprint:
                raise BridgeUnavailable('无法确认 Claude 当前账号')
            scope = (str(store.home), str(store.threep), kind, fingerprint)
            paths.append(store.home/'claude_desktop_config.json')
        else:
            from bridge.clients.deepseek.accounts import _read, _document
            with store.lock:
                source = _document(_read(store.path))
            scope = (str(adapter.home), source, os.environ.get('DEEPSEEK_API_KEY') if getattr(self, 'instance', None) is None else None)
            paths.append(adapter.home/'profiles/desktop/cordis.patch.yml')
            paths.extend((adapter.home/'settings.yaml', adapter.home/'cordis.patch.yml'))
        stamps = []
        for path in paths:
            try:
                stamps.append(hashlib.sha256(path.read_bytes()).hexdigest())
            except OSError:
                stamps.append(None)
        return ModelStore.key('desktop-model-directory-v2', provider, scope, stamps)

    def _account_model_live(self, provider):
        adapter = self.adapters[provider]
        if provider == 'zcode':
            return adapter.status().get('connected') is True
        if provider == 'claude':
            try:
                store = self._account_store(provider)
                with store.lock:
                    kind = store._current()[0]
                if kind == 'api':
                    return True
                return bool(adapter.desktop and adapter.desktop.connected and not adapter.setup_cancel.is_set()
                            and not adapter.connection_probe_failed)
            except (OSError, ValueError, sqlite3.Error):
                return False
        return (adapter.directory/'endpoint.json').is_file()

    def _account_model_result(self, provider, identity, persisted, error=None):
        if self._account_model_identity(provider) != identity:
            raise BridgeUnavailable('客户端账号或配置已变化，请重试')
        value = ModelStore.directory(persisted[1])
        value['cache'] = {'state': 'stale' if error or time.time()-persisted[0] >= MODEL_REFRESH_SECONDS else 'fresh',
                          'updatedAt': persisted[0], 'identity': identity}
        if error:
            value['modelError'] = error
        if self._account_model_identity(provider) != identity:
            raise BridgeUnavailable('客户端账号或配置已变化，请重试')
        return value

    def _account_models(self, provider, refresh=False):
        identity = self._account_model_identity(provider)
        try:
            persisted = self.model_store.read(identity)
        except (OSError, sqlite3.DatabaseError, ValueError):
            persisted = None
        if persisted and not refresh:
            if self._account_model_live(provider):
                self._schedule_account_model_refresh(provider, identity, persisted[0])
            return self._account_model_result(provider, identity, persisted)
        with self.model_lock:
            lock = self.model_locks.setdefault(identity, threading.Lock())
        with lock:
            if not refresh:
                try:
                    persisted = self.model_store.read(identity)
                except (OSError, sqlite3.DatabaseError, ValueError):
                    persisted = None
                if persisted:
                    return self._account_model_result(provider, identity, persisted)
            try:
                if provider == 'claude':
                    store = self._account_store(provider)
                    with store.lock:
                        kind = store._current()[0]
                    value = store.models()
                    adapter = self.adapters[provider]
                    connected = bool(adapter.desktop and adapter.desktop.connected and not adapter.setup_cancel.is_set()
                                     and not adapter.connection_probe_failed)
                    if connected:
                        native = self._read(provider, 'account')
                        if isinstance(native.get('groups'), list):
                            groups = []
                            for group in native['groups']:
                                if not isinstance(group, dict):
                                    continue
                                catalog = _claude_catalog_sources(group, value if kind == 'api' else None)
                                groups.append({**group, **catalog})
                            models = {row['id']: row for group in groups if isinstance(group, dict)
                                      for row in group.get('models', []) if isinstance(row, dict) and isinstance(row.get('id'), str)}
                            value = {**native, 'groups': groups, 'models': list(models.values()), 'modelSourcesVersion': 1}
                else:
                    value = self._read(provider, 'account')
            except (OSError, ValueError, BridgeUnavailable):
                if persisted:
                    return self._account_model_result(provider, identity, persisted, '模型目录刷新失败，已保留上次结果')
                raise
            if self._account_model_identity(provider) != identity:
                raise BridgeUnavailable('客户端账号或配置已变化，请重试')
            if (value.get('modelError') or value.get('capabilities', {}).get('models') is False
                    or not isinstance(value.get('models'), list)
                    or provider == 'claude' and not value.get('models')):
                if persisted:
                    return self._account_model_result(provider, identity, persisted,
                                                      value.get('modelError') or '模型目录刷新失败，已保留上次结果')
                if self._account_model_identity(provider) != identity:
                    raise BridgeUnavailable('客户端账号或配置已变化，请重试')
                return {**value, 'cache': {'state': 'unavailable', 'updatedAt': None, 'identity': identity}}
            try:
                updated_at = self.model_store.write(identity, ModelStore.directory(value))
                self._schedule_account_model_refresh(provider, identity, updated_at, replace=True)
            except (OSError, sqlite3.DatabaseError):
                updated_at = time.time()
            if self._account_model_identity(provider) != identity:
                raise BridgeUnavailable('客户端账号或配置已变化，请重试')
            return {**value, 'cache': {'state': 'fresh', 'updatedAt': updated_at, 'identity': identity}}

    def _schedule_account_model_refresh(self, provider, identity, updated_at, replace=False):
        with self.model_lock:
            if replace and identity in self.model_timers:
                self.model_timers.pop(identity).cancel()
            if self.closed.is_set() or not self.gateway_running or identity in self.model_timers:
                return
            delay = max(1, updated_at + MODEL_REFRESH_SECONDS - time.time())
            def due():
                with self.model_lock:
                    self.model_timers.pop(identity, None)
                if self.closed.is_set() or not self._account_model_live(provider):
                    return
                try:
                    self._account_models(provider, refresh=True)
                except (OSError, ValueError, BridgeUnavailable):
                    pass
                finally:
                    if not self.closed.is_set():
                        try:
                            if self._account_model_identity(provider) == identity:
                                record = self.model_store.read(identity)
                                if record:
                                    self._schedule_account_model_refresh(provider, identity,
                                        max(record[0], time.time()-MODEL_REFRESH_SECONDS+300))
                        except (OSError, sqlite3.DatabaseError, ValueError, BridgeUnavailable):
                            pass
            timer = threading.Timer(delay, due)
            timer.daemon = True
            self.model_timers[identity] = timer
            timer.start()

    def cli_connections(self):
        adapter = self.adapters.get('claude')
        return adapter.cli.cli_connections() + adapter.remote_cli.cli_connections() if adapter else []

    def list_with_activity(self, provider):
        with self.read_lock:
            binding, adapter = self.binding_epochs[provider], self.adapters[provider]
        value = self.call(provider, 'list')
        pins = set(self.session_pins[provider].identifiers())
        value = {**value, 'sessions': [{**row, 'bridgePinned': row['id'] in pins} for row in value.get('sessions', [])]}
        if value.get('connected') is not False:
            self._list_activity(provider, adapter, binding, value.get('sessions', []))
        with self.read_lock:
            if self.binding_epochs[provider] != binding or self.adapters.get(provider) is not adapter:
                raise BridgeUnavailable('客户端状态已变化，请重试')
        return value

    def _display_scope(self, provider, row):
        adapter = self.adapters[provider]
        # Connector directory/home identify the bound instance; host/backend
        # separate CLI, WSL, Code and Cowork without changing their ownership.
        return json.dumps([provider, str(adapter.directory), str(getattr(adapter, 'home', '')),
                           row.get('host', 'local'), row.get('backend', 'desktop')], ensure_ascii=False)

    def _metadata_row(self, provider, sid):
        result = self._adapter_call(provider, 'list', None, {})
        row = next((row for row in result.get('sessions', []) if row.get('id') == sid), None)
        if not row:
            raise ValueError('原会话暂不可读，请刷新列表后重试')
        return row

    def _session_metadata(self, provider, action, sid, body):
        with self._changing(provider, rebind=False):
            self.require_enabled(provider)
            row = self._metadata_row(provider, sid)
            scope = self._display_scope(provider, row)
            scoped = str(uuid.uuid5(uuid.NAMESPACE_URL, scope + '\n' + sid))
            capabilities = row.get('capabilities') or {}
            if action == 'rename':
                def read_name():
                    latest = self._metadata_row(provider, sid)
                    return {'title': latest.get('title'), 'identity': {key: latest.get(key)
                        for key in ('id', 'cwd', 'host', 'backend', 'provider', 'modelProvider')}}
                def dispatch_title(title):
                    if capabilities.get('rename') is not True:
                        raise OrganizationNotSent('当前会话尚不能原生改名，请先连接原会话；若仍不可用，请在原客户端修改')
                    try:
                        return self._adapter_call(provider, 'rename', sid, {'title': title, 'id': body['id']})
                    except ResumePreflightBlocked as exc:
                        raise OrganizationNotSent(str(exc)) from exc
                result = self.rename_commands.perform(scoped, body, read_name, dispatch_title)
                self.display_metadata.clear_alias(scope, sid)
                return result
            native = capabilities.get('organization') is True
            def read():
                local = self.display_metadata.snapshot(scope, sid)
                if not native:
                    return local
                result = self._adapter_call(provider, 'organization', sid, {'operation': 'read'})
                return {**local, **result, 'sections': local['sections'], 'sectionId': local['sectionId'],
                        'scope': 'native', 'projectScope': 'native', 'sectionScope': 'bridge'}
            if not body:
                return read()
            if not native or body.get('kind') in ('section', 'create-section'):
                self.display_metadata.change(scope, sid, body)
                return read()
            if body.get('kind') != 'project':
                raise ValueError('请在原客户端中创建项目')
            def dispatch(change):
                result = self._adapter_call(provider, 'organization', sid,
                    {'operation': 'set-project', 'projectId': change['targetId']})
                if result.get('notSent') is True:
                    raise OrganizationNotSent(result.get('message') or '项目设置未发送，请刷新后重试')
                return read()
            return self.organization_commands.perform(scoped, body, read, dispatch)

    def _list_activity(self, provider, adapter, binding, rows):
        """Enrich list indicators asynchronously; never wait for transcripts on list load."""
        candidates = []
        with self.read_lock:
            if self.closed.is_set() or self.binding_epochs[provider] != binding:
                return
            for row in rows:
                key = (provider, binding, row['id'])
                signature = (row.get('updatedAt'), row.get('status'))
                cached = self.activity.get(key)
                if cached:
                    row.update(cached[1])
                if cached is None or cached[0] != signature or (cached[1].get('turnStatus') == 'inProgress' and row.get('status') != 'active'):
                    candidates.append((key, signature))
            # Keep only current-account/current-list presentation state.
            valid = {(provider, binding, row['id']) for row in rows}
            self.activity = {k: v for k, v in self.activity.items() if k[0] != provider or k in valid}
            if provider in self.activity_pending or not candidates:
                return
            self.activity_pending.add(provider)
        def refresh():
            try:
                # Changed known sessions precede older sessions still awaiting a baseline.
                candidates.sort(key=lambda item: item[0] not in self.activity)
                for key, signature in candidates[:8]:
                    with self.read_lock:
                        if self.closed.is_set() or self.binding_epochs[provider] != binding or self.adapters.get(provider) is not adapter:
                            return
                    try:
                        detail = self._read(provider, 'detail', key[2])
                        if detail.get('connected') is False:
                            continue
                        row = detail.get('session') or {}
                        fields = {name: row[name] for name in ('turnId', 'turnStatus') if name in row}
                        with self.read_lock:
                            if not self.closed.is_set() and self.binding_epochs[provider] == binding:
                                self.activity[key] = (signature, fields)
                    except (OSError, ValueError):
                        continue
            finally:
                with self.read_lock:
                    self.activity_pending.discard(provider)
        threading.Thread(target=refresh, daemon=True, name='desktop-activity-'+provider).start()

    def _project_environments(self, provider, host_id=None):
        if provider == 'zcode':
            scope = hashlib.sha256(self.adapters[provider].account_scope().encode()).hexdigest()[:24]
            return [{'key': 'desktop|local|'+scope, 'host': 'local', 'hostLabel': '此电脑', 'surface': 'desktop',
                     'name': 'ZCode', 'createRequiresMessage': True, 'createTitle': False}]
        adapter = self.adapters[provider]
        scoped = {'host_id': host_id} if host_id is not None and provider == 'claude' else {}
        rows = (adapter.remote_cli.wsl.project_environments(**scoped) if provider == 'claude' and
                not self.config.get('enabled', {}).get('claude', False) and self._wsl_claude_enabled()
                else adapter.project_environments(**scoped))
        result = []
        for row in rows:
            scope = row['scope']
            if row['surface'] != 'cli':
                try:
                    scope += '|' + self._account_model_identity(provider)
                except (OSError, ValueError, KeyError, TypeError, BridgeUnavailable):
                    continue
            digest = hashlib.sha256(scope.encode()).hexdigest()[:24]
            result.append({**row, 'key': row['surface']+'|'+row['host']+'|'+digest})
        return result

    @staticmethod
    def _public_project(row):
        return {key: value for key, value in row.items()
                if key not in ('scope', 'origin', 'alias', 'directoryIdentity', 'accountFingerprint')}

    def _project_environment(self, provider, key):
        self.require_enabled(provider)
        parts = key.split('|', 2) if isinstance(key, str) else []
        if len(parts) != 3:
            raise ValueError('运行环境或账号已变化，请重新选择项目')
        selected = next((row for row in self._project_environments(provider, host_id=parts[1]) if row['key'] == key), None)
        if selected is None:
            raise ValueError('运行环境或账号已变化，请重新选择项目')
        return selected

    def creation_models(self, provider, body):
        """Read account/project catalogs without creating or claiming a CLI session."""
        environment = self._project_environment(provider, body.get('environment'))
        project = self.directory_projects.get(body.get('projectKey'))
        if project:
            if project.get('provider') != provider or project.get('environment') != environment['key']:
                raise ValueError('项目或运行环境已变化，请重新选择')
        elif body.get('projectKey'):
            if provider == 'claude':
                adapter = self.adapters[provider]
                if environment['surface'] == 'cli':
                    rows = (adapter.cli_projects(include_remote=False) if environment['host'] == 'local' else
                            adapter.remote_cli.projects(host_id=environment['host']))
                else:
                    rows = []
                    for row in adapter.index.values():
                        if row.get('backend') != environment['surface']:
                            continue
                        cwd = (row.get('folders') or [''])[0] if environment['surface'] == 'cowork' else row.get('cwd')
                        if cwd: rows.append({'key': environment['surface']+':'+cwd, 'cwd': cwd,
                                             'surface': environment['surface'], 'host': 'local'})
            else:
                rows = self._adapter_call(provider, 'projects', None, {}).get('projects', [])
            project = next((row for row in rows if row['key'] == body['projectKey']), None)
            if not project or project.get('surface', 'desktop') != environment['surface'] or project.get('host', 'local') != environment['host']:
                raise ValueError('项目或运行环境已变化，请重新选择')
        elif body.get('path'):
            project = {'cwd': self._project_directory(environment, body['path'])['cwd']}
        else:
            raise ValueError('请先选择新会话的项目目录')
        refresh = body.get('refresh') is True
        if environment['surface'] == 'cli':
            adapter = self.adapters[provider]
            target = {**environment, **project}
            target.setdefault('origin', environment.get('origin'))
            with adapter.cli.lock, adapter.remote_cli.lock:
                index = adapter.cli.index if target['host'] == 'local' else adapter.remote_cli.index
                found = next((row for row in index.values() if row['cwd'] == target['cwd'] and row['origin'] == target['origin'] and row.get('host', 'local') == target['host']), None)
            if found:
                target = {**target, **found}
            identity = self._model_identity(provider, None, creation=target)
            try:
                persisted = self.model_store.read(identity)
            except (OSError, sqlite3.DatabaseError, ValueError):
                persisted = None
            if found and (refresh or not persisted):
                value = self._catalog_models(provider, found['id'], {'refresh': refresh, 'section': 'models'})
                if value.get('models'):
                    return {**value, 'capabilities': {'models': True, 'effort': True, 'manualModels': True, 'manualEffort': True}}
            if persisted:
                return {**_copy_read(persisted[1]), 'cache': {'identity': identity, 'updatedAt': persisted[0],
                        'state': 'stale' if refresh or time.time()-persisted[0]>=MODEL_REFRESH_SECONDS else 'fresh'},
                        **({'notice': '当前 CLI 未接入，已保留模型缓存；不会为刷新目录占用会话。'} if refresh and not found else {}),
                        'capabilities': {'models': True, 'effort': True, 'manualModels': True, 'manualEffort': True}}
            return {'models': [], 'cache': {'identity': identity, 'updatedAt': None, 'state': 'unavailable'},
                    'capabilities': {'models': True, 'effort': True, 'manualModels': True, 'manualEffort': True},
                    'notice': '此环境尚无可复用模型目录，可手动填写；不会为读取目录启动 CLI 会话。'}
        value = self._desktop_model_directory(provider, environment['surface'], refresh=refresh)
        capabilities = {'models': True, 'effort': True, 'manualModels': provider == 'deepseek',
                        'manualEffort': provider == 'deepseek'}
        return {**value, 'capabilities': capabilities}

    def _project_alias(self, environment):
        hosts = self.bridge.hosts if self.bridge else self.adapters['claude'].hosts
        host = hosts.hosts().get(environment['host']) if hosts else None
        if not host or host['alias'] != environment.get('alias'):
            raise ValueError('原 SSH 主机配置已变化')
        return host['alias']

    def _project_directory(self, environment, path, create=False, expected=None):
        if environment['host'] == 'local':
            return project_directory(path, create, expected)
        source = source_text('features/sessions/create.py')
        source += '\nprint(json.dumps(project_directory(**' + payload({
            'value': path, 'create': create, 'expected': expected}) + ')))\n'
        return self._project_read(environment, source)

    def _project_read(self, environment, source):
        if environment['host'].startswith('wsl-claude:'):
            return self.claude_wsl.read(environment['host'], source)
        return ssh_read(self._project_alias(environment), source)

    def register_project(self, provider, environment, path, create=False):
        with self.locks[provider]:
            selected = self._project_environment(provider, environment)
            identity = self._project_directory(selected, path, create)
            row = self.directory_projects.save(selected['host'], selected['hostLabel'], identity,
                scope=provider+'|'+environment, provider=provider, environment=environment,
                surface=selected['surface'], createRequiresMessage=selected['createRequiresMessage'])
            return self._public_project(row)

    def project_directories(self, provider, environment, path=None, hidden=False, name=None, identity=None):
        with self.locks[provider]:
            selected = self._project_environment(provider, environment)
            if selected['host'] == 'local':
                return project_directory_listing(path, hidden, name, identity)
            source = source_text('features/sessions/create.py')
            source += '\nprint(json.dumps(project_directory_listing(**' + payload({
                'value': path, 'hidden': hidden, 'name': name, 'identity': identity}) + ')))\n'
            return self._project_read(selected, source)

    def project_trust(self, provider, projectKey, confirmed):
        if type(confirmed) is not bool or not isinstance(projectKey, str) or not projectKey:
            raise ValueError('工作区确认格式无效')
        with self._changing(provider, rebind=False):
            project = self.directory_projects.get(projectKey)
            if not project or project.get('provider') != provider:
                raise ValueError('请重新选择项目目录')
            environment = self._project_environment(provider, project['environment'])
            self._project_directory(environment, project['cwd'], expected=project['directoryIdentity'])
            return self._adapter_call(provider, 'project-trust', None,
                {'directoryProject': {**environment, **project}, 'confirmed': confirmed})

    def _archive(self, provider, sid, body):
        if (set(body) not in ({'id', 'archived'}, {'id', 'archived', 'disconnect'}) or
                not isinstance(body.get('archived'), bool) or not isinstance(body.get('id'), str) or
                type(body.get('disconnect', False)) is not bool or (body.get('disconnect') and body['archived'] is not True)):
            raise ValueError('归档请求格式无效')
        uuid.UUID(body['id'])
        with self._changing(provider, rebind=False):
            self.require_enabled(provider)
            adapter = self.adapters[provider]
            directory = adapter.directory
            scoped = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([provider, str(directory), str(getattr(adapter, 'home', '')), sid])))
            commands = ArchiveCommands(directory)
            def read():
                return self._adapter_call(provider, 'archive-state', sid, {})
            def prepare(state):
                if not state.get('capabilities', {}).get('archive') or not state.get('capabilities', {}).get('unarchive'):
                    raise ValueError('当前接入尚未确认原生归档和恢复能力')
                if (state.get('runtimeKnown') is not True or state.get('status') not in ('idle', 'active') or
                        (not body.get('disconnect') and (not state.get('archiveSafe') or state.get('status') != 'idle' or state.get('requests')))):
                    raise ValueError('聊天有运行、排队、审批或未知状态，请先在原客户端处理')
                with self.db_lock:
                    db = self._receipts(adapter)
                    if any(json.loads(row[0]).get('status') in ('unknown', 'queued') for row in db.execute('SELECT result FROM requests')):
                        raise ValueError('此接入有排队或待核对操作，请先在原客户端处理')
                options = {'archived': body['archived']}
                if body.get('disconnect'):
                    options['disconnect'] = True
                return lambda: self._adapter_call(provider, 'archive', sid, options)
            result = commands.perform(scoped, body['archived'], body['id'], read, prepare)
            return {**result, 'id': sid}

    def _resume_step(self, provider, adapter, sid, rid, first):
        # The durable receipt identifies the logical request. Only its original
        # adapter generation can continue observing the live subscription.
        pending = self.pending_resumes[provider]
        now = time.monotonic()
        for old in [k for k, (_, _, last) in pending.items() if now - last > 600]:
            del pending[old]
        ticket = pending.get(rid)
        if self.adapters.get(provider) is not adapter or not first and (ticket is None or ticket[0] is not adapter):
            raise ResumePreflightBlocked('原会话接续已过期，请重新连接。')
        if first and len(pending) >= 64:
            raise ResumePreflightBlocked('正在恢复的会话较多，请稍后连接。')
        result, continuation = adapter.resume_step(sid, rid, None if first else ticket[1])
        if result['status'] == 'pending' and continuation is not None:
            pending[rid] = (adapter, continuation, now)
        else:
            pending.pop(rid, None)
        return result

    def call(self, provider, action, sid=None, body=None):
        if provider not in self.adapters or action not in READS | WRITES:
            raise ValueError('不支持的桌面操作')
        if provider == 'deepseek' and self.deepseek_restore_error:
            raise BridgeUnavailable(self.deepseek_restore_error)
        if action not in ('list', 'projects', 'create', 'create-models', 'account') and (not isinstance(sid, str) or not sid or len(sid) > 512):
            raise ValueError('会话标识无效')
        body = body or {}
        if any(key in body for key in ('directoryProject', 'projectDirectory')):
            raise ValueError('项目目录必须通过目录选择器登记')
        if action == 'pin':
            self.require_enabled(provider)
            if set(body) not in ({'pinned'}, {'pinned', 'clientId', 'revision'}):
                raise ValueError('聊天置顶设置无效')
            return self.session_pins[provider].set(sid, body['pinned'], body.get('clientId'), body.get('revision'))
        adapter = self.adapters[provider]
        if action == 'archive':
            return self._archive(provider, sid, body)
        if action in ('rename', 'organization'):
            return self._session_metadata(provider, action, sid, body)
        if action == 'account':
            return self._account_models(provider, refresh=body.get('refresh') is True)
        if action == 'create-models':
            return self.creation_models(provider, body)
        if action == 'catalog' and body.get('section') == 'models':
            return self._catalog_models(provider, sid, body)
        if action in READS and not (action == 'access' and 'mode' in body):
            result = self._read(provider, action, sid, body)
            if action == 'list':
                result['sessions'] = [self.display_metadata.decorate(self._display_scope(provider, row), row)
                                      for row in result.get('sessions', [])]
            if action == 'detail' and result.get('session'):
                if callable(getattr(type(adapter), 'resume_step', None)) and result.get('capabilities', {}).get('resume'):
                    result['capabilities'] = {**result['capabilities'], 'progressiveResume': True}
                row = result['session']
                result['session'] = self.display_metadata.decorate(self._display_scope(provider, row), row)
            if action == 'projects':
                environments = self._project_environments(provider)
                result['createModels'] = True
                for project in result.get('projects', []):
                    environment = next((row for row in environments
                        if row['host'] == project.get('host', 'local') and
                        row['surface'] == project.get('surface', 'desktop')), None)
                    if environment:
                        project['environment'] = environment['key']
                keys = {row['key'] for row in environments}
                registered = [self._public_project(row) for row in self.directory_projects.list(
                    {row['host'] for row in environments})
                    if row.get('provider') == provider and row.get('environment') in keys]
                result['projects'] = result.get('projects', []) + registered
                result['environments'] = [self._public_project(row) for row in environments]
            if action == 'detail' and result.get('session'):
                if (result.get('readOnly') is True or result['session'].get('readOnly') is True or
                        provider == 'zcode' and result['session'].get('controlScope') == 'session' and
                        result.get('capabilities', {}).get('models') is False):
                    result['modelCatalogIdentity'] = result['draftAccountIdentity'] = ''
                    return result
                try:
                    result['modelCatalogIdentity'] = self._model_identity(provider, sid)
                except ClaudeLoginDatabaseUnavailable:
                    # History was read from its native owner. Only account-bound
                    # capabilities require the unreadable Desktop fingerprint.
                    result['modelCatalogIdentity'] = result['draftAccountIdentity'] = ''
                    result.pop('draftContinuation', None)
                    caps = result.get('capabilities') or result['session'].get('capabilities', {})
                    result['capabilities'] = {**caps, 'models': False, 'effort': False,
                                              'manualModels': False, 'manualEffort': False}
                    result['notice'] = 'Claude 账号暂不可读，模型切换与账号草稿暂不可用'
                    return result
                try:
                    result['draftAccountIdentity'] = self._draft_account_identity(provider, sid, result)
                except (OSError, ValueError, KeyError, TypeError, AttributeError, BridgeUnavailable):
                    result['draftAccountIdentity'] = ''
                hint = result.pop('draftContinuation', None)
                session = result['session']
                if (provider == 'claude' and result['draftAccountIdentity'] and hint and
                        session.get('runtime') == 'wsl' and hint.get('entry') == session.get('host') and
                        hint.get('id') == sid and hint.get('accountIdentity') == session.get('draftAccountIdentity')):
                    result['draftContinuation'] = {**hint, 'accountIdentity': result['draftAccountIdentity'],
                        'previousAccountIdentities': [draft_identity('claude', 'wsl-cli', hint['entry'], identity)
                            for identity in hint.get('previousAccountIdentities', [])]}
            return result
        rid = str(uuid.UUID(body.get('id', '')))
        progressive = action == 'resume' and body.get('progressive') is True
        if 'progressive' in body and (not progressive or set(body) != {'id', 'progressive'} or
                                      not callable(getattr(type(adapter), 'resume_step', None))):
            raise ValueError('会话接续请求无效')
        if action == 'compact' and set(body) != {'id'}:
            raise ValueError('上下文压缩请求无效')
        if action == 'stop-task' and (set(body) != {'id', 'taskId'} or
                not isinstance(body.get('taskId'), str) or not 1 <= len(body['taskId']) <= 512):
            raise ValueError('后台任务标识无效')
        if action == 'send' and (not isinstance(body.get('text'), str) or
                                 not body['text'].strip() and not (body.get('resolvedImages') or body.get('resolvedFiles') or body.get('references')) or len(body['text']) > 100000):
            raise ValueError('请输入 1–100000 字的消息')
        if action == 'create' and (not isinstance(body.get('title', ''), str) or len(body.get('title', '')) > 120):
            raise ValueError('聊天名称不能超过 120 字')
        if action == 'send' and 'workMode' in body:
            if body['workMode'] not in ('default', 'plan', 'goal') or body.get('mode', 'send') != 'send':
                raise ValueError('工作模式仅用于发送新消息')
            if body['workMode'] == 'goal' and any(body.get(key) for key in ('references', 'resolvedImages', 'resolvedFiles', 'plugins')):
                raise ValueError('请先发送附件和 Skill，再单独设定目标')
        if action == 'goal':
            if body.get('operation') not in ('pause', 'resume', 'clear', 'edit') or not isinstance(body.get('expected'), dict):
                raise ValueError('目标操作无效，请刷新会话后重试')
            if body['operation'] == 'edit' and (not isinstance(body.get('objective'), str) or not 1 <= len(body['objective'].strip()) <= 4000):
                raise ValueError('请输入 1–4000 字的目标')
        if action == 'edit-message' and (set(body) != {'id', 'messageId', 'text'} or not isinstance(body.get('text'), str) or not 1 <= len(body['text'].strip()) <= 100000):
            raise ValueError('请输入 1–100000 字的消息')
        if action in ('fork', 'edit-message') and (set(body) - {'id', 'messageId', *(['text'] if action == 'edit-message' else [])} or
                                 not isinstance(body.get('messageId'), str) or
                                 not body['messageId'].strip() or len(body['messageId']) > 512):
            raise ValueError('请选择原会话中可分支的消息')
        from bridge.features.sessions.creation_models import intent as creation_intent
        fingerprint = hashlib.sha256(json.dumps([provider, action, sid, creation_intent(body) if action == 'create' else body], sort_keys=True).encode()).hexdigest()
        # Normal chat writes keep progress reads available; account/lifecycle
        # operations additionally invalidate the connector binding.
        with self._changing(provider, rebind=False):
            if self.config.get('enabled', {}).get(provider) is False and not (provider == 'claude' and self._wsl_claude_enabled()):
                raise ValueError('此应用已关闭，请在应用管理中开启')
            native_claude_model_write = (provider == 'claude' and action == 'settings' and
                                         ('model' in body or 'effort' in body) and sid in adapter.index)
            if action == 'settings' and ('modelCatalogIdentity' in body or native_claude_model_write):
                current_identity = self._model_identity(provider, sid)
                if 'modelCatalogIdentity' in body and (not body['modelCatalogIdentity'] or
                                                       current_identity != body['modelCatalogIdentity']):
                    raise ValueError('账号已切换，请重新打开模型设置')
            scope = None
            if provider == 'claude':
                from bridge.features.accounts.wsl_account_guard import request_scope
                scope = request_scope(self, provider, action, sid, body)
            reference_files = []
            if action == 'send' and 'references' in body:
                reference_files = self.workspace_bridge(provider, sid).workspace(sid, 'references', {'references': body['references']})
                if len(reference_files) + len(body.get('resolvedImages', [])) + len(body.get('resolvedFiles', [])) > 10:
                    raise ValueError('每条消息最多添加 10 个不同附件')
            cached = None
            with self.db_lock:
                db = None
                try:
                    db = self._receipts(adapter)
                    result = {'status': 'unknown', 'id': rid, 'message': '结果待核对，请检查桌面原会话；相同请求不会重复执行'}
                    inserted = db.execute('INSERT OR IGNORE INTO requests VALUES (?,?,?)', (rid, fingerprint, json.dumps(result))).rowcount
                    if inserted and reference_files and provider == 'zcode':
                        from bridge.clients.zcode.attachments import remember_project_files
                        remember_project_files(db, sid, rid, reference_files)
                    if inserted and scope is not None:
                        db.execute('CREATE TABLE IF NOT EXISTS request_scopes (id TEXT PRIMARY KEY, provider TEXT, host TEXT, sid TEXT)')
                        db.execute('INSERT INTO request_scopes VALUES (?,?,?,?)', (rid, *scope))
                    db.commit()
                    if not inserted:
                        old = db.execute('SELECT fingerprint,result FROM requests WHERE id=?', (rid,)).fetchone()
                        if old[0] != fingerprint:
                            raise ValueError('相同请求 ID 不能用于不同操作')
                        cached = json.loads(old[1])
                        if (provider == 'zcode' and action == 'send' and cached.get('status') == 'unknown'
                                and type(cached.get('windowReceiptAfter')) is int):
                            checked = adapter.check_window_receipt(sid, body, cached)
                            if checked['status'] == 'accepted':
                                db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(checked), rid))
                                db.commit()
                            return checked
                        if not progressive or cached.get('status') != 'pending':
                            return cached
                except sqlite3.OperationalError as exc:
                    if db is not None:
                        db.rollback()
                    if any(word in str(exc).lower() for word in ('locked', 'busy')):
                        raise BridgeUnavailable('接入的操作记录正被另一网关占用，请停止旧网关后重试') from None
                    raise
            try:
                dispatched = body
                if action == 'send' and reference_files:
                    dispatched = {key: value for key, value in body.items() if key != 'references'}
                    dispatched['resolvedReferences'] = reference_files
                if action == 'create':
                    project = self.directory_projects.get(body.get('projectKey'))
                    if project and project.get('provider') == provider:
                        environment = self._project_environment(provider, project['environment'])
                        self._project_directory(environment, project['cwd'], expected=project['directoryIdentity'])
                        dispatched = {**body, 'directoryProject': {**environment, **project}}
                    from bridge.features.sessions.creation_models import requested, validate
                    if requested(body):
                        try:
                            validate(body, self.creation_models(provider, body))
                        except (OSError, ValueError) as error:
                            raise ResumePreflightBlocked(str(error)) from error
                    dispatched = {key: value for key, value in dispatched.items()
                                  if key not in ('environment', 'modelCatalogIdentity')}
                work_mode = dispatched.get('workMode') if action == 'send' else None
                if work_mode is not None:
                    detail = self._adapter_call(provider, 'detail', sid, {})
                    caps = detail.get('capabilities', {})
                    if not caps.get('goal' if work_mode == 'goal' else 'plan'):
                        raise ValueError('当前客户端未提供此原生工作模式')
                    if work_mode == 'goal':
                        if len(dispatched['text']) > 4000:
                            raise ValueError('目标不能超过 4000 字')
                        result = self._adapter_call(provider, 'goal', sid, {'id': rid, 'operation': 'set', 'objective': dispatched['text']})
                    else:
                        if detail['session'].get('collaborationMode', 'default') != work_mode:
                            mode_result = self._adapter_call(provider, 'work-mode', sid, {'id': str(uuid.uuid5(uuid.UUID(rid), 'work-mode')), 'mode': work_mode})
                            if mode_result.get('status') not in ('accepted', 'ready'):
                                raise BridgeUnavailable('原生模式未确认，消息未发送')
                        result = self._adapter_call(provider, action, sid, {key: value for key, value in dispatched.items() if key != 'workMode'})
                else:
                    result = (self._resume_step(provider, adapter, sid, rid, bool(inserted)) if progressive
                              else self._adapter_call(provider, action, sid, dispatched))
            except ResumePreflightBlocked as exc:
                result = {'status': 'blocked', 'id': rid, 'message': str(exc)}
            except Exception as exc:
                # The desktop may already have accepted a command before a timeout.
                # Do not turn an uncertain outcome into a safe-to-retry failure.
                result['message'] = str(exc) if isinstance(exc, (ValueError, BridgeUnavailable)) else result['message']
            if progressive and result.get('status') != 'pending':
                self.pending_resumes[provider].pop(rid, None)
            if cached == result:
                return result
            with self.db_lock:
                db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(result), rid))
                db.commit()
            return result

    def notification_rows(self):
        """Read live desktop state without connecting, activating or creating chats."""
        if not self.gateway_running or self.closed.is_set():
            return []
        rows = []
        for provider, adapter in self.adapters.items():
            if not self.enabled(provider):
                continue
            try:
                status = adapter.call('status') if provider == 'deepseek' else adapter.status()
                if status.get('connected') is not True and not (provider == 'claude' and status.get('cliAvailable') is True):
                    continue
                listing = self._read(provider, 'list')
                if listing.get('connected') is False:
                    continue
                for row in listing.get('sessions', []):
                    if isinstance(row, dict) and isinstance(row.get('id'), str) and row['id']:
                        rows.append({**row, 'host': 'desktop:' + (self.instance.id if self.instance else provider), 'provider': provider})
            except (OSError, ValueError, BridgeUnavailable):
                continue
        if self.instances is not None:
            for entry in self.instances.metadata():
                if entry['family'] == 'codex' or not entry['enabled']:
                    continue
                try:
                    rows.extend(self.resolve_instance(entry['family'], entry['id']).notification_rows())
                except (OSError, ValueError, BridgeUnavailable):
                    continue
        return rows

    def status(self):
        result = {}
        for provider, adapter in self.adapters.items():
            try:
                if provider == 'deepseek' and self.deepseek_restore_error:
                    raise BridgeUnavailable(self.deepseek_restore_error)
                value = adapter.call('status') if provider == 'deepseek' else adapter.status()
            except (ValueError, OSError):
                value = {'connected': False}
            result[provider] = value
        return result

    def _wsl_claude_enabled(self):
        if self.claude_wsl is None:
            return False
        with self.claude_wsl.lock:
            return any(row.get('enabled', True) for row in self.claude_wsl.entries)

    def image_preview(self, provider, sid, identifier):
        self.require_enabled(provider)
        return self.history_images.preview(provider, sid, identifier,
            lambda source: self._adapter_call(provider, 'image', sid, {'source': source}))

    def _adapter_call(self, provider, action, sid, body):
        adapter = self.adapters[provider]
        if self.instance is not None and action in WRITES | {'work-mode', 'project-trust'}:
            from bridge.clients.lifecycle import inspect_client
            from bridge.clients.profile_process import require_owned
            descriptor = self._descriptor(provider)
            native = inspect_client(descriptor)
            if not native['running']:
                raise BridgeUnavailable('请先开启此独立配置的原生应用')
            require_owned(descriptor, native)
            self.instance_owner.registry.pin_initialized_roots(self.instance.id)
            if provider == 'deepseek':
                from bridge.clients.instances import require_deepseek_account
                require_deepseek_account(self, action, sid, body or {})
        if provider != 'claude' or self.config.get('enabled', {}).get('claude', False) or not self._wsl_claude_enabled():
            return adapter.call(action, sid, body)
        remote = adapter.remote_cli.wsl
        if action == 'list':
            return remote.listing()
        if action == 'projects':
            return {'projects': [{key: value for key, value in row.items() if key not in ('origin', 'alias')}
                                 for row in remote.projects()], 'createRequiresMessage': True}
        if action == 'create':
            project = body.get('directoryProject') or next((row for row in remote.projects()
                if row['key'] == body.get('projectKey')), None)
            if not project or not project['host'].startswith('wsl-claude:'):
                raise ValueError('请选择已开启的 WSL Claude Code 项目')
            return adapter.create_cli(project, body)
        if not remote.contains(sid):
            raise ValueError('找不到已开启的 WSL Claude Code 会话')
        return remote.call(action, sid, body)

    def navigation_clients(self):
        """Configured native/WSL targets, without probing processes or connections."""
        with self.read_lock:
            enabled = self.config.get('enabled', {})
            targets = [provider for provider in ('codex', *self.adapters)
                       if enabled.get(provider, provider == 'codex')]
        entries = getattr(self.bridge, 'entries', None)
        return targets + (list(entries.hosts()) if entries is not None else []) + (
            [row['id'] for row in self.instances.metadata() if row['enabled'] and row['family'] != 'codex']
            if self.instances is not None else [])

    def enabled(self, provider):
        if self.instance is not None:
            if provider != self.instance.family:
                return False
            return any(row['id'] == self.instance.id and row['enabled']
                       for row in self.instance_owner.registry.list(provider))
        if isinstance(provider, str) and provider.startswith(('claude:', 'deepseek:', 'zcode:')):
            return any(row['id'] == provider and row['enabled'] for row in self.instances.metadata())
        if isinstance(provider, str) and provider.startswith(('wsl:', 'codex:')):
            entries = getattr(self.bridge, 'entries', None)
            return bool(entries and entries.enabled(provider))
        native = self.config.get('enabled', {}).get(provider, provider == 'codex')
        return bool(native or provider == 'claude' and self._wsl_claude_enabled())

    def require_enabled(self, provider):
        if self.instance is not None:
            self.instance_owner.registry.resolve(provider, self.instance.id, require_enabled=True)
        elif isinstance(provider, str) and provider.startswith(('claude:', 'deepseek:', 'zcode:')):
            self.instances.registry.resolve(provider.split(':')[0], provider, require_enabled=True)
            return
        if provider == 'deepseek' and self.deepseek_restore_error:
            raise BridgeUnavailable(self.deepseek_restore_error)
        if not self.enabled(provider):
            raise ValueError('此应用已关闭，请在应用管理中开启')

    @staticmethod
    def _deepseek_endpoint(adapter):
        # An unchanged live endpoint may still be running the previous connector
        # source. Keep its restart action until a different host generation runs.
        try:
            value = json.loads((adapter.directory/'endpoint.json').read_text())
            return {'port': value['port'], 'pid': value['pid']}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _deepseek_restart_pending(setup, endpoint, connected, revision=None):
        if setup.get('setupStatus') != 'restart-required':
            return False
        if setup.get('requiredBridgeRevision'):
            return not connected or revision != setup['requiredBridgeRevision']
        if connected and revision == BRIDGE_REVISION:
            return False
        if not connected or endpoint is None:
            return True
        previous = setup.get('restartEndpoint')
        if previous is not None:
            return previous == endpoint
        # A previously live connector with no readable identity cannot prove
        # it reloaded. Only an explicit restart can clear that uncertain case.
        return setup.get('restartConnected', True)

    def scan(self, setup=True, *, scan_wsl=True):
        if self.instance is not None:
            from bridge.clients.instances import scan_profile
            return scan_profile(self)
        # Discovery only: neither a GUI refresh nor a manual rescan launches apps.
        # Keep the old setup argument for callers from older desktop packages.
        from bridge.app.desktop import Desktop
        from bridge.clients.discovery import discover_clients
        with self.client_lock:
            preferences = {**Desktop(self.directory.parent).preferences(),
                           **{k: v for k, v in self.config.items() if k.endswith('Home')}}
            if self.bridge:
                preferences['desktopExecutable'] = self.bridge.accounts.index.get('desktopExecutable')
            custom_claude = bool(self.instances and self.instances.metadata('claude'))
            if custom_claude and not preferences.get('claudeHome'):
                preferences['claudeHome'] = self.instances.default_claude_home()
            discovered = discover_clients(preferences)
            if custom_claude and not self.config.get('claudeHome'):
                discovered['claude']['dataDirectoryExplicit'] = False
            self.config['discovered'] = discovered
            with self.read_lock:
                self._configure_zcode()
            outcomes = self.config.setdefault('setup', {})
            for provider in ('claude', 'deepseek'):
                if provider == 'deepseek' and self.config.get('deepseekManualBinding'):
                    continue  # Discovery cannot replace an explicit plugin binding.
                row = discovered[provider]
                if not row['installed']:
                    outcomes[provider] = {'setupStatus': 'not-installed', 'reason': '尚未安装此客户端'}
                    continue
                with self._changing(provider):
                    try:
                        if provider == 'claude':
                            options = {'explicit_home': True} if row.get('dataDirectoryExplicit') else {}
                            status = self.adapters[provider].configure(row['executable'], row['dataDirectory'], **options)
                            outcomes[provider] = {'setupStatus': status.get('setupState', 'discovered'),
                                                  'reason': status.get('reason', '')}
                        else:
                            adapter = self.adapters[provider]
                            if str(adapter.home) != row['dataDirectory']:
                                adapter = DeepSeek(self.directory/'deepseek', row['dataDirectory'])
                                self.adapters[provider] = adapter
                            self.config['deepseekHome'] = str(adapter.home)
                            installed = adapter.discover_existing()
                            self.deepseek_restore_error = None
                            if installed.get('reused'):
                                self.config['deepseekAdapterDirectory'] = str(adapter.directory)
                            if installed.get('updateRequired'):
                                from bridge.clients.lifecycle import inspect_client
                                native = inspect_client(row)
                                if native['running'] and not native.get('mainPids'):
                                    outcomes[provider] = {'setupStatus': 'failed',
                                        'reason': '检测到残留 Harness 后台实例且无法核对任务状态，请在电脑端退出 Harness 后重试'}
                                else:
                                    outcomes[provider] = {'setupStatus': 'restart-required', 'reason': UPDATE_REASON,
                                                          'requiredBridgeRevision': BRIDGE_REVISION}
                            elif not installed.get('installed'):
                                outcomes[provider] = {'setupStatus': 'discovered',
                                                      'reason': '已发现客户端，启用后随网关启动并接入'}
                    except (ValueError, OSError, subprocess.SubprocessError) as exc:
                        outcomes[provider] = {'setupStatus': 'failed', 'reason': str(exc)}
                        if provider == 'deepseek':
                            self.deepseek_restore_error = str(exc)
            private_json(self.config_path, self.config)
            self.client_cache = None
            entries = getattr(self.bridge, 'entries', None)
            if scan_wsl and sys.platform == 'win32':
                if entries is not None:
                    entries.scan()
                self.claude_wsl.scan()
            return self.clients(refresh=True)

    def start_enabled(self):
        """Start selected clients only after the gateway service has started."""
        if not self.gateway_running or self.closed.is_set():
            return
        self.scan(scan_wsl=False)
        for provider in ('codex', 'claude', 'deepseek', 'zcode'):
            with self.client_lock:
                if self.instance is not None and provider != self.instance.family:
                    continue
                if provider == 'zcode' and provider not in self.adapters:
                    continue
                if self.closed.is_set():
                    return
                if not self.config.get('enabled', {}).get(provider, provider == 'codex'):
                    continue
                try:
                    self.toggle_client({'provider': provider, 'enabled': True}, startup=True)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    setup = self.config.setdefault('setup', {})
                    previous = setup.get(provider, {}) if provider == 'deepseek' else {}
                    setup[provider] = {**previous, 'setupStatus': (
                        'restart-required' if previous.get('requiredBridgeRevision') else 'failed'), 'reason': str(exc)}
                    private_json(self.config_path, self.config)
                    self.client_cache = None
        if self.instances is not None:
            for row in self.instances.metadata():
                if self.closed.is_set():
                    return
                if row['family'] != 'codex' and row['enabled']:
                    try:
                        self.resolve_instance(row['family'], row['id']).start()
                    except (ValueError, OSError):
                        continue

    def start(self):
        if self.startup_thread is None and not self.closed.is_set():
            self.startup_thread = threading.Thread(target=self.start_enabled, daemon=True)
            self.startup_thread.start()

    def _descriptor(self, provider):
        if getattr(self, 'instance', None) is not None:
            if provider != self.instance.family:
                raise ValueError('接入实例与平台不匹配')
            return self.instance_owner.descriptor(self, provider)
        if provider == 'zcode' and provider in self.adapters:
            adapter = self.adapters[provider]
            from bridge.clients.zcode.connections import selected_mode
            from bridge.clients.zcode.installation import installation
            app = installation(adapter.config.get('application', ''))
            return {'id': provider, 'installed': bool(app), 'compatibilitySupported': False,
                    'officialRemote': selected_mode(self.config) == 'official-remote',
                    'pipeSupported': bool(self.zcode_pipe and self.zcode_pipe.supported()),
                    'application': adapter.config.get('application', ''),
                    'installationIdentity': app.get('installationIdentity') if app else None,
                    'packageType': app.get('packageType') if app else None,
                    'coldStartVerified': app.get('coldStartVerified', True) if app else False,
                    'executable': app['executable'] if app else '', 'dataDirectory': str(adapter.home),
                    'debugFallbackPort': None}
        if provider == 'deepseek' and self.config.get('deepseekManualBinding'):
            return {'id': provider, 'installed': False, 'executable': '',
                    'dataDirectory': str(self.adapters[provider].home)}
        row = dict(self.config.get('discovered', {}).get(provider, {}))
        if provider == 'codex' and self.bridge:
            from bridge.clients.desktop_app import DesktopApp
            accounts = self.bridge.for_host('local').accounts
            executable = self._codex_executable(accounts)
            row.update(id='codex', executable=executable, dataDirectory=str(accounts.home),
                       installed=bool(executable and Path(executable).is_file()))
        if provider in ('claude', 'deepseek') and getattr(self, 'instances', None) is not None:
            row['excludeProfiles'] = [value['home'] for value in self.instances.metadata(provider)]
            if self.instances.error:
                row['excludeProfiles'] = ['unavailable-registry']
        return row

    def _start_zcode_connection(self, descriptor, *, initialize, acknowledge_unavailable):
        from bridge.clients.lifecycle import inspect_client
        from bridge.clients.zcode.adapter import ZCodePrototype

        # Allow a new Linux/Windows renderer to mount its native services, including
        # time spent finding and launching the original process (mobile timeout: 45s).
        cold_deadline = time.monotonic() + 30 if sys.platform in ('linux', 'win32') else None
        if not descriptor.get('pipeSupported'):
            raise ValueError('当前安装的 ZCode 不支持此本机实时连接。')
        existing = self.adapters['zcode']
        config = {'application': existing.config['application'], 'dataDirectory': str(existing.home),
                  'privatePipe': True}
        adapter = existing if existing.config == config else ZCodePrototype(
            existing.directory, config, pipe=self.zcode_pipe)
        state = inspect_client(descriptor)
        if state.get('unknown') or state['running'] and len(state.get('mainPids', [])) != 1:
            raise ValueError('无法确认唯一的 ZCode 进程，请刷新后重试。')
        owner = adapter.connection_owner(descriptor['executable'])
        launched = False
        if owner:
            if not state['running'] or state['mainPids'] != [owner['pid']]:
                raise ValueError('ZCode 进程已变化，请刷新后重试。')
        else:
            if descriptor.get('packageType') == 'appimage' and not descriptor.get('coldStartVerified'):
                raise ValueError('此 AppImage 的冷启动尚未核验，请先使用已运行原 App 的官方远控连接。')
            if state['running']:
                if not initialize:
                    raise ValueError('请在应用管理中初始化连接，以接入正在运行的 ZCode。')
                self._stop_client('zcode', descriptor, state, acknowledge_unavailable=acknowledge_unavailable)
            # Recheck after a potentially slow normal quit. An update or a new
            # listener must not inherit the decision made for the previous App.
            fresh = self._descriptor('zcode')
            same_installation = ((fresh.get('application'), fresh.get('installationIdentity')) ==
                                 (descriptor.get('application'), descriptor.get('installationIdentity'))
                                 if descriptor.get('packageType') == 'appimage' else
                                 fresh['executable'] == descriptor['executable'])
            if not fresh['pipeSupported'] or not same_installation:
                raise ValueError('ZCode 安装已变化，请刷新后重试。')
            if state['running'] and adapter.connection_owner(fresh['executable']):
                raise ValueError('ZCode 进程已变化，请刷新后重试。')
            owner = self.zcode_pipe.start()
            launched = True
            fresh = self._descriptor('zcode')
            state = inspect_client(fresh)
            if state.get('unknown') or len(state.get('mainPids', [])) != 1:
                raise ValueError('无法确认唯一的 ZCode 进程，请刷新后重试。')
        if owner and state['mainPids'] != [owner['pid']]:
            raise ValueError('ZCode 进程已变化，请刷新后重试。')
        if sys.platform in ('win32', 'linux'):
            adapter.start_private_application(owner)
        else:
            adapter.connect_application(state['mainPids'][0], wait=launched,
                                        deadline=cold_deadline if launched else None)
        self.adapters = {**self.adapters, 'zcode': adapter}
        self.config['zcodeConnectionMode'] = 'private-pipe'
        self.config.pop('zcodeCompatibility', None)

    def _codex_executable(self, accounts):
        from bridge.clients.desktop_app import DesktopApp
        # Windows may cache the CLI outside its Store-installed desktop bundle.
        return (accounts.index.get('desktopExecutable') or DesktopApp.discover(accounts.runtime)
                or self.config.get('discovered', {}).get('codex', {}).get('executable', ''))

    def _verified(self, provider):
        if provider == 'deepseek' and self.config.get('deepseekManualBinding'):
            return {}
        row = self.config.get('discovered', {}).get(provider, {})
        return {key: row.get(key) for key in ('executable', 'dataDirectory')}

    def _assert_idle(self, provider, state, *, native_confirmation=False):
        unknown = '无法确认客户端所有任务均已结束；可选择“仅停用手机接入”保留电脑 App，或在电脑端退出后重试'
        if provider == 'deepseek' and len(state.get('runtimePids', [])) > 1:
            raise ValueError('检测到多个 Harness 后台实例，无法确认全部任务状态；请在电脑上结束多余实例后重试')
        if state.get('unknown') or len(state.get('mainPids', [])) > 1:
            raise ValueError(unknown)
        if provider == 'codex':
            self.bridge.accounts.idle()
            self.bridge.assert_desktop_idle()
            return
        adapter = self.adapters[provider]
        if provider == 'claude' and self._claude_native_cli_busy():
            raise ValueError('有 Claude Code 任务运行或等待确认，请先结束任务再关闭客户端')
        if provider != 'zcode':
            try:
                status = adapter.call('status') if provider == 'deepseek' else adapter.status()
            except (ValueError, OSError, BridgeUnavailable):
                raise TaskStateUnavailable(unknown) from None
            if not status.get('connected'):
                raise TaskStateUnavailable('客户端尚未连接，无法确认任务状态；可选择“仅停用手机接入”，或在电脑端退出 App 后重试')
        if provider == 'deepseek':
            hosts = state.get('runtimePids', [])
            endpoint = self._deepseek_endpoint(adapter)
            if len(hosts) != 1 or not endpoint or endpoint.get('pid') != hosts[0]:
                raise ValueError(unknown)
            if status.get('bridgeRevision') not in LIFECYCLE_REVISIONS:
                raise ValueError('当前 Harness 接入版本无法核对任务状态，请在电脑端退出 Harness 后再连接')
        try:
            listing = (adapter.call('list', timeout=8) if native_confirmation else
                       adapter.desktop_listing() if provider == 'claude' else
                       adapter.call('lifecycle'))
        except (ValueError, OSError, BridgeUnavailable):
            if provider == 'zcode':
                raise TaskStateUnavailable('无法确认任务状态，请确认是否正常退出 ZCode。') from None
            raise TaskStateUnavailable(unknown) from None
        sessions = listing.get('sessions')
        if not isinstance(sessions, list):
            raise TaskStateUnavailable(unknown)
        # An incomplete list may still prove a task busy; check that evidence first.
        if any(isinstance(row, dict) and (row.get('requests') or
               row.get('status') in ('active', 'running', 'waiting', 'busy')) for row in sessions):
            raise ValueError('有任务运行或等待确认，请先结束任务再关闭或重启客户端')
        if (listing.get('complete') is not True or
                provider == 'deepseek' and listing.get('bridgeRevision') not in LIFECYCLE_REVISIONS or
                any(not isinstance(row, dict) or row.get('status') not in ('idle', 'stopped', 'completed') or
                    row.get('runtimeKnown') is not True for row in sessions)):
            raise TaskStateUnavailable(unknown)

    def _stop_client(self, provider, descriptor, state, *, native_confirmation=False, force=False, acknowledge_unavailable=False):
        from bridge.clients.lifecycle import stop_client, stop_deepseek
        native_confirmation = native_confirmation and provider == 'claude' and sys.platform == 'win32'
        if not state['running'] and not force:
            if provider == 'claude':
                self.adapters[provider].cancel()
            return
        try:
            if not force:
                self._assert_idle(provider, state, native_confirmation=native_confirmation)
        except TaskStateUnavailable:
            # Only explicitly confirmed normal quit may accept unavailable task
            # evidence. Known activity, writes and ambiguous identities still block.
            if not (acknowledge_unavailable and provider in ('codex', 'zcode') or
                    native_confirmation and provider == 'claude' and sys.platform == 'win32'):
                raise
        if provider == 'claude':
            # Cancel the monitor before native quit, so it cannot relaunch Claude.
            self.adapters[provider].cancel(persist=False)
        try:
            if force:
                from bridge.clients.lifecycle import force_stop_client
                force_stop_client(descriptor, state=state)
            elif provider == 'zcode' and sys.platform == 'win32' and getattr(self.adapters['zcode'], 'pipe', None):
                from bridge.platforms.windows.codex_quit import stop_private_zcode
                pipe = self.adapters['zcode'].pipe
                owner = pipe.owner()
                if owner is None or stop_private_zcode(descriptor['executable'], state['mainPids'], owner, lambda: pipe.request_quit(owner)) is False:
                    stop_client(descriptor, state=state)
            elif provider == 'deepseek' and sys.platform == 'win32':
                stop_deepseek(descriptor, self.adapters[provider], state=state)
            else:
                stop_client(descriptor, state=state)
        except Exception:
            if provider == 'claude':
                try:
                    self.adapters[provider].reconnect()
                except Exception:
                    pass  # A recovery failure must not hide the native quit error.
            raise
        if provider == 'claude':
            self.adapters[provider].cancel()

    def _deepseek_connection(self, phase, message, *, clear=(), **values):
        setup = self.config.setdefault('setup', {}).setdefault('deepseek', {})
        previous = dict(setup)
        for key in clear:
            setup.pop(key, None)
        setup.update(connectionState=phase, connectionReason=message, **values)
        if phase not in ('starting', 'connecting'):
            for key in ('connectionStartedAt', 'connectionDeadline'):
                setup.pop(key, None)
        if previous != setup:
            private_json(self.config_path, self.config)
            self.client_cache = None

    def _begin_deepseek_connection(self):
        now = time.time()
        self._deepseek_connection('starting', '正在启动 Harness 桌面应用',
                                  connectionStartedAt=now, connectionDeadline=now + (120 if sys.platform == 'win32' else 60))

    def _deepseek_launched(self, result, *, first_launch=False):
        if not result.get('running') and result.get('launched') is not True:
            raise ValueError('客户端尚未启动，请在电脑端检查后重试')
        setup = self.config['setup']['deepseek']
        if first_launch and result.get('running'):
            reason = 'Harness 已打开，请完成首次设置后点击接入'
            self._deepseek_connection('idle', reason, setupStatus='needs-first-launch', reason=reason)
            return
        phase = 'connecting' if result.get('running') else 'starting'
        reason = 'Harness 已启动，正在等待桌面连接' if result.get('running') else '正在启动 Harness 桌面应用'
        values = {'pendingFirstLaunch': first_launch}
        if not setup.get('requiredBridgeRevision'):
            values.update(setupStatus=phase, reason=reason)
        self._deepseek_connection(phase, reason, **values)

    def _start_deepseek(self, restart=False):
        try:
            return self._prepare_deepseek(restart)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            setup = self.config.setdefault('setup', {}).get('deepseek', {})
            values = {} if setup.get('requiredBridgeRevision') else {'setupStatus': 'failed', 'reason': str(exc)}
            self._deepseek_connection('error', str(exc), **values)
            raise

    def _prepare_deepseek(self, restart=False):
        from bridge.clients.lifecycle import inspect_client, launch_deepseek
        if self.deepseek_restore_error:
            raise BridgeUnavailable(self.deepseek_restore_error)
        row = self._descriptor('deepseek')
        adapter = self.adapters['deepseek']
        setup = self.config.setdefault('setup', {})
        previous = setup.get('deepseek', {})
        state = inspect_client(row)
        if self.instance is not None and not state['running']:
            from bridge.clients.instances import prepare_deepseek_profile
            prepare_deepseek_profile(self.instance.profile, row)
        if state['running'] and not state.get('mainPids'):
            raise ValueError('检测到残留 Harness 后台实例且无法核对任务状态，请在电脑端退出 Harness 后重试')
        if not (adapter.home/'profiles/desktop/cordis.patch.yml').is_file():
            self._begin_deepseek_connection()
            result = launch_deepseek(row)
            self._deepseek_launched(result, first_launch=True)
            return
        installed = adapter.ensure_installed()
        if installed.get('reused'):
            self.config['deepseekAdapterDirectory'] = str(adapter.directory)
        try:
            status = adapter.call('status')
        except BridgeUnavailable:
            status = {}
        needs_update = bool(installed.get('updateRequired') or previous.get('requiredBridgeRevision') and
                            status.get('bridgeRevision') != BRIDGE_REVISION or status.get('updateRequired'))
        needs_reload = needs_update or previous.get('setupStatus') == 'restart-required' and not status.get('connected')
        if state['running'] and needs_reload and not restart:
            self._deepseek_connection('idle', UPDATE_REASON, setupStatus='restart-required',
                                      reason=UPDATE_REASON, requiredBridgeRevision=BRIDGE_REVISION)
            return
        if state['running'] and restart:
            self._stop_client('deepseek', row, state)
        if needs_update or getattr(adapter, 'reused', False) and (restart or not state['running']):
            adapter.update_existing()
            setup['deepseek'] = {'setupStatus': 'restart-required', 'reason': UPDATE_REASON,
                                 'requiredBridgeRevision': BRIDGE_REVISION}
            private_json(self.config_path, self.config)
        self._begin_deepseek_connection()
        result = launch_deepseek(row)
        self._deepseek_launched(result)

    def connect_deepseek(self, restart=False):
        if self.config.get('deepseekManualBinding'):
            raise ValueError('请在已绑定的第三方客户端中重新加载插件；网关不会重启它')
        if not self.gateway_running:
            return self.toggle_client({'provider': 'deepseek', 'enabled': True})
        with self.client_lock, self._changing('deepseek'):
            self._start_deepseek(restart)
            return self.clients(refresh=True)

    def recover_deepseek(self, value):
        """Private desktop control only; web app controls never dispatch here."""
        if self.config.get('deepseekManualBinding'):
            raise ValueError('手动绑定不管理第三方客户端进程，请在客户端内重启或退出')
        from bridge.clients.deepseek.recovery import DeepSeekRecovery
        with self.client_lock, self._changing('deepseek'):
            if not self.config.get('discovered', {}).get('deepseek'):
                self.scan(scan_wsl=False)
            descriptor, adapter = self._descriptor('deepseek'), self.adapters['deepseek']
            if self.deepseek_recovery is None:
                self.deepseek_recovery = DeepSeekRecovery()
            if value.get('action') == 'deepseek-recovery-preview':
                return self.deepseek_recovery.preview(descriptor, adapter, value.get('restart', True))
            restart = self.deepseek_recovery.confirm(descriptor, adapter, value)
            # Native exit has completed; source upgrades cannot invalidate live tasks.
            if restart:
                installed = adapter.ensure_installed()
                if installed.get('updateRequired'):
                    adapter.update_existing()
                self.deepseek_restore_error = None
                self.config.setdefault('setup', {})['deepseek'] = {'setupStatus': 'connecting', 'reason': '正在等待 Harness 连接'}
                self._start_deepseek()
            else:
                self.config.setdefault('setup', {})['deepseek'] = {'setupStatus': 'ready', 'reason': 'Harness 已完整退出'}
            # Preserve the user's startup choice if quitting or restarting failed.
            self.config.setdefault('enabled', {})['deepseek'] = restart
            private_json(self.config_path, self.config)
            self.client_cache = None
            return self.clients(refresh=True)

    def connect_claude(self, restart=False):
        if not self.gateway_running:
            return self.toggle_client({'provider': 'claude', 'enabled': True})
        from bridge.clients.lifecycle import inspect_client, launch_client
        with self.client_lock, self._changing('claude'):
            if not restart and self.adapters['claude'].status().get('connected') is True:
                return self.clients(refresh=True)
            descriptor = self._descriptor('claude')
            if restart:
                self._stop_client('claude', descriptor, inspect_client(descriptor))
            if sys.platform != 'win32':
                result = launch_client(descriptor, allow_native_relaunch=not restart)
                if not result.get('running'):
                    raise ValueError('客户端尚未启动，请在电脑端检查后重试')
            self.adapters['claude'].connect()
            return self.clients(refresh=True)

    def _claude_reconnect_supported(self):
        adapter = self.adapters['claude']
        discovery = adapter.discovery
        return bool(isinstance(discovery, dict) and discovery.get('installed') and
                    discovery.get('automaticConnection') == 'native-console' and
                    (adapter.directory/'connection.json').is_file())

    def reconnect_claude(self):
        """Reconnect a prepared, running app; never enable, launch or restart it."""
        from bridge.clients.lifecycle import inspect_client
        with self.client_lock, self._changing('claude'):
            if not self.gateway_running:
                raise ValueError('请先启动网关，再重新连接 Claude')
            self.require_enabled('claude')
            if not self.config.get('enabled', {}).get('claude', False):
                raise ValueError('Claude Desktop 接入已关闭，请先开启原电脑接入')
            adapter = self.adapters['claude']
            if adapter.status().get('connected'):
                try:
                    adapter.check_connection()
                except BridgeUnavailable:
                    pass  # One explicit reconnect also repairs a stale heartbeat.
                else:
                    return self.clients(refresh=True)
            if not self._claude_reconnect_supported():
                raise ValueError('请先在电脑端配置 Claude 接入，再重新连接')
            state = inspect_client(self._descriptor('claude'))
            if not state['running'] or not state['mainPids']:
                raise ValueError('请先在电脑端打开 Claude，再重新连接')
            if state['unknown'] or len(state['mainPids']) != 1:
                raise ValueError('Claude 进程无法确认，请在电脑端检查后重试')
            if sys.platform == 'darwin':
                adapter.reconnect()
            else:
                adapter.connect(existing_only=True)
            self.client_cache = None
            return self.clients(refresh=True)

    def _claude_quit_snapshot(self, descriptor):
        # Reuse the recovery path's PID/start-time/command identity checks.
        # This probes processes only; no Harness endpoint or operation is used.
        from bridge.clients.deepseek.recovery import _snapshot
        try:
            return _snapshot(descriptor)
        except (ValueError, OSError, subprocess.SubprocessError):
            raise ValueError('Claude 进程已变化，请重新检查后再确认退出') from None

    def _claude_native_cli_busy(self):
        adapter = self.adapters['claude']
        remote = getattr(adapter, 'remote_cli', None)
        if self.claude_wsl is not None:
            remote = remote.ssh
        return adapter.cli.busy() is True or remote is not None and remote.busy() is True

    def _claude_quit_evidence(self):
        adapter = self.adapters['claude']
        try:
            if self._claude_native_cli_busy():
                return {'busy': True, 'unknown': False}
            if not adapter.status().get('connected'):
                return {'busy': False, 'unknown': True}
            listing = adapter.desktop_listing()  # Fresh Desktop evidence, never shared.
            sessions = listing.get('sessions')
            if not isinstance(sessions, list):
                return {'busy': False, 'unknown': True}
            busy = any(isinstance(row, dict) and (row.get('requests') or
                       row.get('status') in ('active', 'running', 'waiting', 'busy')) for row in sessions)
            known = listing.get('complete') is True and all(isinstance(row, dict) and
                row.get('runtimeKnown') is True and row.get('status') in ('idle', 'stopped', 'completed')
                for row in sessions)
            return {'busy': bool(busy), 'unknown': not known}
        except (ValueError, OSError, BridgeUnavailable):
            return {'busy': False, 'unknown': True}

    def quit_claude(self, value):
        """Desktop-only, explicitly confirmed native quit without force killing."""
        from bridge.clients.lifecycle import stop_client
        with self.client_lock:
            descriptor = self._descriptor('claude')
            if value.get('action') == 'claude-quit-preview':
                snapshot = self._claude_quit_snapshot(descriptor)
                state, evidence = snapshot['state'], self._claude_quit_evidence()
                allowed = (sys.platform in ('darwin', 'win32') and not state['unknown'] and
                           not snapshot['unrecognized'] and len(state['mainPids']) == 1 and not state['runtimePids'])
                token = secrets.token_hex(24)
                self.claude_quit = {'token': token, 'expires': time.monotonic()+300,
                                    'snapshot': snapshot, 'allowed': allowed, 'unknown': evidence['unknown']}
                return {'token': token, 'canQuit': allowed and not evidence['busy'],
                        'busy': evidence['busy'], 'requiresUnknownConfirmation': evidence['unknown']}
            with self._changing('claude'):
                pending = self.claude_quit
                if (not pending or not isinstance(value.get('token'), str) or
                        not secrets.compare_digest(value['token'], pending['token']) or time.monotonic() >= pending['expires']):
                    raise ValueError('Claude 退出确认已过期，请重新检查')
                if value.get('confirmed') is not True:
                    raise ValueError('请确认退出 Claude')
                if sys.platform not in ('darwin', 'win32'):
                    raise ValueError('请在电脑端退出 Claude；此系统不支持正常退出确认')
                snapshot = self._claude_quit_snapshot(descriptor)
                if snapshot != pending['snapshot']:
                    raise ValueError('Claude 进程已变化，请重新检查后再确认退出')
                evidence = self._claude_quit_evidence()
                if evidence['busy']:
                    raise ValueError('有任务运行或等待确认，请先结束任务再关闭或重启客户端')
                if not pending['allowed']:
                    raise ValueError('Claude 进程无法确认，请在电脑端检查后重试')
                if evidence['unknown'] and (not pending['unknown'] or value.get('acknowledgeUnknown') is not True):
                    raise ValueError('无法核验 Claude 任务状态，请重新检查并明确确认可能中断任务')
                self.claude_quit = None
                adapter = self.adapters['claude']
                adapter.cancel(persist=False)
                if self._claude_quit_snapshot(descriptor) != snapshot:
                    raise ValueError('Claude 进程已变化，请重新检查后再确认退出')
                # stop_client uses macOS quit / Windows CloseMainWindow. Linux
                # SIGTERM and the Harness runtime termination path are excluded.
                stop_client(descriptor, state=snapshot['state'])
                adapter.cancel()
                self.config.setdefault('enabled', {})['claude'] = False
                private_json(self.config_path, self.config)
                self.client_cache = None
                return self.clients(refresh=True)

    def _zcode_official_status(self):
        if sys.platform not in ('darwin', 'win32', 'linux'):
            return {}
        from bridge.clients.local_rpc import LocalService
        return LocalService(self.directory.parent, 'zcode-official').status()

    def _select_zcode_connection(self, mode):
        """Called under the provider write gate; changing transport never exits the App."""
        from bridge.clients.zcode.connections import MODES, selected_mode
        if mode not in MODES:
            raise ValueError('ZCode 连接方式无效')
        previous = selected_mode(self.config)
        if mode == 'official-remote':
            if not self._zcode_official_status().get('configured'):
                raise ValueError('请先在电脑网关配置并登录官方远控')
        elif not self.zcode_pipe or not self.zcode_pipe.supported():
            raise ValueError('当前安装暂不能使用本机连接')
        with self._changing('zcode', rebind=previous != mode):
            if previous != mode and previous == 'official-remote':
                self.adapters['zcode'].disconnect()
            self.config['zcodeConnectionMode'] = mode
            self.config.pop('zcodeOfficialRemote', None)
            self.config.pop('zcodeCompatibility', None)
            self._configure_zcode()
            private_json(self.config_path, self.config)
            self.client_cache = None

    def clients(self, refresh=False):
        with self.client_lock:
            if self.instance is not None:
                selected = self.instance_owner.registry.resolve(self.instance.family, self.instance.id,
                                                                require_enabled=False)
                self.config.setdefault('enabled', {})[self.instance.family] = selected.enabled
            pending = self.client_cache and any(row.get('connectionState') in ('starting', 'connecting')
                                               for row in self.client_cache[1]['clients'])
            if not refresh and self.client_cache and time.monotonic() - self.client_cache[0] < (1 if pending else 15):
                result = self.client_cache[1]
                return {**result, 'windowsSession': windows_session.status()} if sys.platform == 'win32' else result
            rows = []
            discovered = self.config.get('discovered', {})
            ready = False
            reason = '请在电脑端安装 Codex 并配置账号或 API'
            if self.bridge is not None:
                try:
                    from bridge.clients.desktop_app import DesktopApp
                    accounts = self.bridge.for_host('local').accounts
                    current = accounts.info.current()
                    installed = self._codex_executable(accounts)
                    ready = bool(installed and Path(installed).is_file() and current.get('status') == 'ready' and current.get('kind') in ('chatgpt', 'api'))
                    if ready and current.get('kind') == 'api':
                        ready = bool((accounts.info.identity or {}).get('key') or accounts.active_matches())
                    reason = '接入配置已就绪' if ready else ('正在检查账号配置' if current.get('status') == 'checking' else reason)
                except (AttributeError, ValueError, OSError):
                    pass
            rows.append({'id': 'codex', 'name': 'Codex', 'installed': discovered.get('codex', {}).get('installed', ready), 'configured': ready, 'connected': False,
                         'setupStatus': 'ready' if ready else 'unconfigured',
                         'enabled': self.enabled('codex'), 'reason': reason})
            for provider, name in [('claude', 'Claude'), ('deepseek', 'DSH')]:
                if provider not in self.adapters:
                    continue
                connected = ready = False
                invalid = False
                status = {}
                setup = self.config.get('setup', {}).get(provider, {})
                reason = setup.get('reason') or '请在电脑端连接客户端并配置账号或 API'
                setup_status = setup.get('setupStatus', 'not-scanned')
                try:
                    adapter = self.adapters[provider]
                    if provider == 'deepseek' and self.deepseek_restore_error:
                        raise BridgeUnavailable(self.deepseek_restore_error)
                    status = adapter.call('status') if provider == 'deepseek' else adapter.status()
                    if provider == 'claude':
                        setup_status = status.get('setupState', setup_status)
                        reason = status.get('reason') or reason
                    connected = status.get('connected') is True if provider == 'deepseek' else bool(status.get('connected'))
                    if connected:
                        if provider == 'deepseek':
                            ready = status.get('configured') is True and not status.get('updateRequired')
                            invalid = status.get('configured') is False
                            reason = (('已恢复此前的 Harness 接入' if setup.get('setupStatus') == 'recovered' else '接入配置已就绪') if ready else '请在桌面配置账号或 API')
                        else:
                            sessions = adapter.desktop_listing().get('sessions', [])
                            if sessions:
                                catalog = self._read(provider, 'catalog', sessions[0]['id'], {'section': 'models'})
                                ready = bool(catalog.get('models'))
                                invalid = catalog.get('models') == []
                            reason = (status.get('reason') if status.get('consoleCleanupPending') else '桌面连接与模型配置可用') if ready else '请在桌面登录并配置模型、创建一个项目会话'
                except (ValueError, OSError, BridgeUnavailable):
                    reason = setup.get('reason') or '客户端连接不可用，请在电脑端检查'
                pending_restart = provider == 'deepseek' and self._deepseek_restart_pending(
                    setup, self._deepseek_endpoint(adapter), connected, status.get('bridgeRevision'))
                if provider == 'deepseek' and (status.get('updateRequired') or setup.get('requiredBridgeRevision') and pending_restart):
                    ready = False
                    setup_status = 'restart-required'
                    reason = UPDATE_REASON
                elif pending_restart:
                    reason = setup.get('reason') or '请结束当前任务后重启 Harness 完成接入'
                elif provider == 'deepseek' and connected:
                    # Transport readiness and account setup are separate. A live
                    # current connector also clears stale restart/failure labels.
                    setup_status = 'recovered' if ready and setup_status == 'recovered' else 'ready' if ready else 'unconfigured'
                verified = self.config.setdefault('verified', {})
                binding = self._verified(provider)
                if invalid and provider in verified and not (provider == 'deepseek' and self.config.get('deepseekManualBinding')):
                    del verified[provider]
                    private_json(self.config_path, self.config)
                if ready and binding.get('executable'):
                    if verified.get(provider) != binding:
                        verified[provider] = binding
                        private_json(self.config_path, self.config)
                remembered = bool(binding.get('executable') and verified.get(provider) == binding and
                                  discovered.get(provider, {}).get('installed'))
                configured = ready or remembered or (provider == 'claude' and status.get('cliAuthReady') is True)
                if provider == 'deepseek' and (pending_restart or status.get('updateRequired')):
                    configured = False
                if not connected and remembered and setup_status not in (
                        'failed', 'needs-retry', 'needs-unlock', 'needs-desktop', 'needs-developer-mode', 'needs-trust',
                        'needs-permission', 'needs-screen-saver', 'restart-required', 'needs-initialization', 'needs-first-launch', 'starting', 'connecting'):
                    reason = '客户端已配置，可开启桌面应用'
                if provider == 'deepseek' and setup.get('setupStatus') == 'failed' and not connected:
                    setup_status, reason = 'failed', setup.get('reason', reason)
                rows.append({'id': provider, 'name': name, 'installed': discovered.get(provider, {}).get('installed', connected),
                             'setupStatus': ('recovered' if provider == 'deepseek' and setup_status == 'recovered' else 'ready') if ready and not pending_restart else setup_status, 'configured': configured, 'connected': connected,
                             'enabled': self.enabled(provider), 'reason': reason,
                             **({'cliAvailable': status.get('cliAvailable') is True,
                                 'cliAuthReady': status.get('cliAuthReady') is True,
                                 'canStartDesktop': status.get('automaticConnection') == 'native-console'} if provider == 'claude' else {})})
            if 'zcode' in self.adapters:
                enabled = self.enabled('zcode')
                adapter = self.adapters['zcode']
                observing = (enabled and self.gateway_running and not self.closed.is_set()
                             and getattr(adapter, 'application_start', None))
                startup = adapter.observe_application_start() if observing else None
                status = startup or (adapter.inventory_status() if enabled else {})
                connected = status.get('connected') is True
                zcode = self._descriptor('zcode')
                from bridge.clients.zcode.connections import connection_modes
                modes = connection_modes(self.config, installed=zcode['installed'],
                                         pipe_supported=zcode['pipeSupported'],
                                         official=self._zcode_official_status(), official_supported=sys.platform in ('darwin', 'win32', 'linux'))
                modes['connectionModes'][0]['connected'] = bool(modes['connectionMode'] == 'private-pipe' and
                                                              (connected or status.get('sessionAccess')))
                installed = zcode['installed']
                history_only = status.get('historyAvailable') and not status.get('retryable')
                rows.append({'id': 'zcode', 'name': 'ZCode', 'installed': installed,
                             'description': '管理 ZCode 桌面应用与手机接入。',
                             'enabled': enabled, 'configured': installed, 'connected': connected,
                             'setupStatus': ('ready' if connected else 'session-access' if status.get('sessionAccess') else
                                             'history-only' if history_only else 'not-connected'),
                             'reason': '原生会话已接入' if connected else status.get('reason', ''),
                             'retryable': status.get('retryable') is True,
                             'confirmTaskState': True, 'historyAvailable': status.get('historyAvailable') is True,
                             'pipeSupported': zcode['pipeSupported'], 'compatibilitySupported': zcode['compatibilitySupported'],
                             'officialRemote': zcode['officialRemote'],
                             **modes,
                             'pipeReady': bool(getattr(self.adapters['zcode'], 'pipe', None) and (connected or status.get('sessionAccess'))),
                             'initializationMode': 'zcode-local-compatibility' if zcode['compatibilitySupported'] else 'private-pipe',
                             'canInitializeDesktop': bool(not zcode['officialRemote'] and self.gateway_running and (zcode['pipeSupported'] or zcode['compatibilitySupported'])
                                                          and zcode.get('coldStartVerified', True)
                                                          and not (connected or status.get('sessionAccess'))),
                             'compatibilityLaunch': zcode['compatibilitySupported'] and self.config.get('zcodeCompatibility') is True,
                             'capabilities': {'create': status.get('canCreate') is True, 'accounts': True,
                                              'notifications': True}})
                if startup:
                    phase = startup['connectionState']
                    rows[-1].update(connectionState=phase, setupStatus='connecting' if phase == 'connecting' else 'failed',
                                    canInitializeDesktop=phase != 'connecting')
            if self.instance is not None:
                rows = [row for row in rows if row['id'] == self.instance.family]
            from bridge.clients.lifecycle import inspect_clients
            descriptors = []
            for row in rows:
                if row['installed']:
                    if row['id'] == 'deepseek' and self.config.get('deepseekManualBinding'):
                        continue
                    try:
                        descriptors.append(self._descriptor(row['id']))
                    except (AttributeError, ValueError, OSError):
                        pass
            try:
                native_states = inspect_clients(descriptors) if descriptors else {}
            except (OSError, subprocess.SubprocessError):
                native_states = {}
            for row in rows:
                native = {}
                if row['id'] == 'deepseek' and self.config.get('deepseekManualBinding'):
                    row.update(bindingMode='manual', installed=True, running=False, mainRunning=False,
                               backgroundRunning=False, backgroundCount=0, canForceQuit=False,
                               selectable=True, connectionState='connected' if row['connected'] else 'disconnected')
                    if not row['connected']:
                        row['reason'] = self.deepseek_restore_error or '请打开已绑定的第三方客户端并加载接入插件'
                    continue
                row.update(running=False, mainRunning=False, backgroundRunning=False, backgroundCount=0)
                multi_profile = self.instance is not None or (self.instances is not None and row['id'] in ('claude', 'deepseek')
                                                              and bool(self.instances.metadata(row['id'])))
                row['canForceQuit'] = bool(not multi_profile and sys.platform in ('darwin', 'win32', 'linux') and self.gateway_running and row['installed'])
                if row['installed']:
                    try:
                        native = native_states.get(row['id'])
                        if native is None:
                            raise ValueError('客户端进程状态不可用')
                        row.update(running=native['running'], mainRunning=bool(native.get('mainPids')),
                                   backgroundRunning=bool(native.get('runtimePids')), backgroundCount=len(native.get('runtimePids', [])))
                        if (self.instance is not None and self.instance.pending_roots and row['connected']
                                and native['running'] and not native.get('unknown') and len(native.get('mainPids', [])) == 1):
                            from bridge.clients.profile_process import require_owned
                            require_owned(self._descriptor(row['id']), native)
                            self.instance = self.instance_owner.registry.pin_initialized_roots(self.instance.id)
                        if row['id'] == 'codex':
                            row['connected'] = row['configured'] and native['running']
                        if row['id'] == 'deepseek' and (row['backgroundCount'] > 1 or row['backgroundRunning'] and not row['mainRunning']):
                            row.update(setupStatus='recovery-required', reason='Harness 存在残留或多个后台实例，请在桌面使用完整退出并重新接入')
                    except (ValueError, OSError, subprocess.SubprocessError):
                        pass
                if row['id'] == 'zcode':
                    row['canInitializeDesktop'] = bool(row['canInitializeDesktop'] and native and
                        native.get('unknown') is False and (not native['running'] or len(native.get('mainPids', [])) == 1))
                if row['id'] == 'deepseek':
                    self._deepseek_progress(row, native_states.get('deepseek'))
                elif row['id'] == 'claude':
                    self._claude_progress(row, native_states.get('claude'))
                # Selection is a startup preference; readiness still requires live evidence.
                row['selectable'] = bool(row['configured'] or row['installed'] and row['setupStatus'] != 'unsupported'
                                         or row.get('cliAvailable'))
                if row['id'] == 'claude':
                    desktop_supported = row['canStartDesktop'] and self.gateway_running and row['installed'] and not row['connected']
                    native_state = native or {}
                    row['canStartDesktop'] = bool(desktop_supported and not row['running'] and native_state.get('unknown') is False)
                    row['canInitializeDesktop'] = bool(desktop_supported and row['running'] and
                                                       len(native_state.get('mainPids', [])) == 1 and native_state.get('unknown') is False)
                    row['initializationMode'] = 'background' if sys.platform == 'win32' else 'foreground'
                    row['reconnectSupported'] = self._claude_reconnect_supported()
                    row['canReconnect'] = bool(row['reconnectSupported'] and self.gateway_running and row['enabled'] and
                                               len(native_state.get('mainPids', [])) == 1 and not native_state.get('unknown', True) and
                                               row['setupStatus'] != 'connecting')
                    row['canConfirmQuit'] = bool(sys.platform in ('darwin', 'win32') and row['mainRunning'])
                    if self._wsl_claude_enabled() and not self.config.get('enabled', {}).get('claude', False):
                        row.update(canStartDesktop=False, canInitializeDesktop=False, canReconnect=False)
                if not self.gateway_running and row['enabled'] and row['setupStatus'] != 'recovery-required':
                    if row['id'] == 'claude' and sys.platform == 'darwin':
                        row['reason'] = '已选择，启动网关后在后台打开 Claude 并尝试恢复已有连接；需要初始化时会提示。'
                    else:
                        row['reason'] = ('已选择，启动网关后在后台启动并连接' if row['id'] == 'claude'
                                         else '已选择，启动网关后自动接入')
            entries = getattr(self.bridge, 'entries', None)
            if self.instance is None:
                entries = getattr(self.bridge, 'entries', None)
                codex = rows[0]
                codex['desktopRestartRecommended'] = bool(self.bridge and
                    getattr(self.bridge, 'desktop_restart_recommended', lambda: False)())
                native = {**codex, 'id': 'desktop', 'kind': 'desktop'}
                applications = entries.applications() if entries is not None else []
                rows.extend(entry for entry in applications if entry.get('kind') == 'codex-local')
                codex_entries = [native, *(entry for entry in applications if entry.get('kind') != 'codex-local')]
                codex.update(entries=codex_entries, desktopEnabled=native['enabled'],
                             nativeConfigured=native['configured'], nativeSelectable=native['selectable'],
                             nativeInstalled=native['installed'], nativeRunning=native['running'],
                             nativeConnected=native['connected'])
                for field in ('enabled', 'configured', 'selectable', 'installed', 'connected', 'running'):
                    codex[field] = any(entry.get(field) is True for entry in codex_entries)
                if codex['configured'] and not native['configured']:
                    codex.update(setupStatus='ready', reason='WSL Codex 接入可用')
                claude = next(row for row in rows if row['id'] == 'claude')
                claude['nativeEnabled'] = self.config.get('enabled', {}).get('claude', False)
                claude['entries'] = self.claude_wsl.applications() if self.claude_wsl is not None else []
                if any(row['enabled'] for row in claude['entries']):
                    claude.update(enabled=True, selectable=True, configured=True)
                rows.extend(self.instance_rows())
            result = {'clients': rows, 'computer': socket.gethostname(), 'gatewayRunning': self.gateway_running,
                      'wslSupported': entries is not None and sys.platform == 'win32'}
            if self.instances is not None and self.instances.error:
                result['instanceError'] = self.instances.error
            if sys.platform == 'win32':
                result['windowsSession'] = windows_session.status()
            self.client_cache = (time.monotonic(), result)
            with self.client_snapshot_lock:
                if not self.closed.is_set():
                    self.client_snapshot = _copy_read(result)
                    self.client_refresh_error = False
                    self.client_refresh_retry_at = 0
            return result

    def clients_status(self):
        """Return display status without waiting for startup or native inspection."""
        if self.closed.is_set():
            raise BridgeUnavailable('客户端连接已关闭')
        cached = None
        if self.client_lock.acquire(blocking=False):
            try:
                cached = self.client_cache
                pending = cached and any(row.get('connectionState') in ('starting', 'connecting')
                                         for row in cached[1]['clients'])
                if cached and time.monotonic() - cached[0] < (1 if pending else 15):
                    with self.client_snapshot_lock:
                        if not self.client_refreshing and not self.client_refresh_error:
                            return {**_copy_read(cached[1]), 'pending': False}
            finally:
                self.client_lock.release()
        with self.client_snapshot_lock:
            snapshot = _copy_read(self.client_snapshot) if self.client_snapshot else {'clients': []}
            now = time.monotonic()
            start = not self.client_refreshing and now >= self.client_refresh_retry_at and not self.closed.is_set()
            if start:
                self.client_refreshing = True
            failed = self.client_refresh_error and not self.client_refreshing
        if start:
            threading.Thread(target=self._refresh_clients_status, daemon=True).start()
        return {**snapshot, 'pending': not failed, **({'refreshError': '客户端状态读取失败，请重试。'} if failed else {})}

    def _refresh_clients_status(self):
        try:
            if not self.closed.is_set():
                self.clients(refresh=True)
        except Exception:
            with self.client_snapshot_lock:
                self.client_refresh_error = True
                self.client_refresh_retry_at = time.monotonic() + 2
        finally:
            with self.client_snapshot_lock:
                self.client_refreshing = False

    def _claude_progress(self, row, native):
        row.update(connectionState='connected' if row['connected'] else 'idle', retryable=False)
        if row['connected'] or not self.gateway_running or not row['enabled']:
            return
        phase = row['setupStatus']
        if sys.platform == 'darwin' and phase in ('needs-screen-saver', 'needs-unlock', 'needs-desktop'):
            row['connectionState'] = 'connecting'
        elif phase in ('starting', 'connecting', 'needs-initialization'):
            row['connectionState'] = phase
            if phase == 'connecting' and native is not None and not native['running']:
                # The initialization worker launches the process asynchronously.
                # Keep its pending state (and short cache) until it reports an
                # outcome; an absent process here is not a failed launch.
                row['connectionState'] = 'starting'
            elif phase == 'needs-initialization' and native is not None and not native['running']:
                row.update(connectionState='error', reason='Claude 未运行，请重试后台启动', retryable=True)
        elif phase in ('failed', 'needs-retry'):
            row.update(connectionState='error', retryable=True)
        elif phase in ('needs-developer-mode', 'needs-trust', 'needs-permission', 'needs-unlock', 'needs-desktop'):
            row['connectionState'] = 'needs-initialization'

    def _deepseek_progress(self, row, native):
        """Describe observed startup without relaunching or touching desktop tasks."""
        setup = self.config.get('setup', {}).get('deepseek', {})
        row.update(connectionState='connected' if row['connected'] else 'idle', retryable=False)
        if row['setupStatus'] == 'recovery-required':
            row['connectionState'] = 'error'
            return
        if row['connected']:
            if row['setupStatus'] != 'restart-required':
                self._deepseek_connection('connected', row['reason'], setupStatus=row['setupStatus'], reason=row['reason'],
                    clear=('requiredBridgeRevision', 'restartEndpoint', 'restartConnected', 'pendingFirstLaunch'))
            else:
                self._deepseek_connection('connected', row['reason'])
            return
        if not self.gateway_running or not row['enabled']:
            return
        phase = setup.get('connectionState') or setup.get('setupStatus')
        reason = setup.get('connectionReason') or row['reason']
        if phase in ('starting', 'connecting'):
            deadline = setup.get('connectionDeadline')
            if not isinstance(deadline, (int, float)):
                deadline = time.time() + (120 if sys.platform == 'win32' else 60)
                self._deepseek_connection(phase, reason, connectionDeadline=deadline)
            if time.time() >= deadline:
                phase, reason = 'timeout', 'Harness 连接超时，请检查桌面应用后重试接入'
            elif native is not None and not native['running'] and phase == 'connecting':
                phase, reason = 'error', 'Harness 已退出，连接未完成，请重试启动'
            elif native is not None and native['running']:
                if setup.get('pendingFirstLaunch'):
                    reason = 'Harness 已打开，请完成首次设置后点击接入'
                    self._deepseek_connection('idle', reason, setupStatus='needs-first-launch', reason=reason)
                    row.update(setupStatus='needs-first-launch', reason=reason, retryable=True)
                    return
                phase, reason = 'connecting', 'Harness 已启动，正在等待桌面连接'
            values = {} if setup.get('requiredBridgeRevision') else {
                'setupStatus': 'failed' if phase == 'error' else phase, 'reason': reason}
            self._deepseek_connection(phase, reason, **values)
            row['setupStatus'] = setup.get('setupStatus', row['setupStatus'])
        elif phase == 'connected':
            phase = 'error'
            reason = ('Harness 连接已中断，请重试接入' if native is None or native['running']
                      else 'Harness 未运行，请重试启动')
        elif row['setupStatus'] == 'failed':
            phase = 'error'
        if phase in ('starting', 'connecting', 'error', 'timeout'):
            row.update(connectionState=phase, reason=reason, retryable=phase in ('error', 'timeout'))
        elif row['setupStatus'] in ('needs-first-launch', 'restart-required'):
            row['retryable'] = True

    def toggle_client(self, value, *, startup=False):
        provider, enabled = value.get('provider'), value.get('enabled')
        if self.instance is None and isinstance(provider, str) and provider.startswith(('claude:', 'deepseek:', 'zcode:')):
            family = provider.split(':')[0]
            if enabled is False and value.get('quitDesktop') is False and not value.get('forceDesktop'):
                # Revoking remote access remains available even if the native
                # folder was removed/replaced. It cannot attach or stop an App.
                self.instances.registry.configure(provider, False)
                child = self.instances.children.get(provider)
                if child is not None:
                    child.config.setdefault('enabled', {})[family] = False
                    if family == 'claude':
                        child.adapters[family].cancel()
                    child.client_cache = None
                self.client_cache = None
                return self.clients(refresh=True)
            child = self.resolve_instance(family, provider, require_enabled=False)
            child.toggle_client({**value, 'provider': family}, startup=startup)
            self.client_cache = None
            return self.clients(refresh=True)
        if self.instance is not None and provider != self.instance.family:
            raise ValueError('接入实例与平台不匹配')
        if isinstance(provider, str) and provider.startswith('wsl-claude:'):
            if self.claude_wsl is None:
                raise ValueError('当前电脑不支持 WSL 应用')
            if type(enabled) is not bool or set(value) - {'action', 'provider', 'enabled', 'quitDesktop'}:
                raise ValueError('WSL Claude Code 应用开关无效')
            if value.get('quitDesktop', False) is not False:
                raise ValueError('请在原 WSL 终端退出 Claude Code；这里只停用手机访问')
            with self.client_lock, self._changing('claude'):
                remote = self.adapters['claude'].remote_cli.wsl
                with remote.lock:
                    active = [owned for sid, owned in remote.owned.items()
                              if remote.index.get(sid, {}).get('host') == provider]
                    for owned in active if not enabled else []:
                        with owned.condition:
                            if owned.process.poll() is None and (owned.active or owned.pending):
                                raise ValueError('WSL Claude Code 有进行中的任务，请先结束任务')
                self.claude_wsl.configure(provider, enabled)
                if not enabled:
                    with remote.lock:
                        for sid, row in list(remote.index.items()):
                            if row['host'] == provider:
                                owned = remote.owned.pop(sid, None)
                                if owned is not None:
                                    owned.close()
                                remote.index.pop(sid, None)
                self.client_cache = None
            return self.clients(refresh=True)
        if isinstance(provider, str) and provider.startswith(('wsl:', 'codex:')):
            entries = getattr(self.bridge, 'entries', None)
            if entries is None or type(enabled) is not bool or set(value) - {'action', 'provider', 'enabled', 'quitDesktop'}:
                raise ValueError('Codex 入口开关无效')
            if value.get('quitDesktop', False) is not False:
                where = '原 WSL 终端' if provider.startswith('wsl:') else '原客户端'
                raise ValueError(f'请在{where}退出 Codex；应用管理只停用手机访问，不会终止原任务')
            with self.client_lock:
                entries.configure(provider, enabled)
                self.client_cache = None
                return self.clients(refresh=True)
        if provider not in ('codex', 'claude', 'deepseek', 'zcode') or type(enabled) is not bool or (
                provider == 'zcode' and provider not in self.adapters):
            raise ValueError('应用开关无效')
        if 'quitDesktop' in value and (type(value['quitDesktop']) is not bool or enabled):
            raise ValueError('应用开关无效')
        if 'forceDesktop' in value and (type(value['forceDesktop']) is not bool or enabled
                or value.get('quitDesktop') is not True or value.get('forceConfirmed') is not True
                or sys.platform not in ('darwin', 'win32', 'linux') or not self.gateway_running):
            raise ValueError('后台强制结束请求无效')
        initialize_zcode = provider == 'zcode' and value.get('initializeDesktop') is True
        if 'connectionMode' in value and (provider != 'zcode' or not enabled or
                value['connectionMode'] not in ('private-pipe', 'official-remote') or
                initialize_zcode and value['connectionMode'] != 'private-pipe'):
            raise ValueError('应用连接方式无效')
        if 'initializeDesktop' in value and (type(value['initializeDesktop']) is not bool
                or provider not in ('claude', 'zcode') or not enabled
                or initialize_zcode and (startup or not self.gateway_running)):
            raise ValueError('应用初始化请求无效')
        acknowledge_unavailable = value.get('acknowledgeUnavailable', False)
        if 'acknowledgeUnavailable' in value and (acknowledge_unavailable is not True or
                provider not in ('codex', 'zcode') or not initialize_zcode and (enabled or value.get('quitDesktop') is not True) or
                value.get('forceDesktop') or sys.platform not in ('darwin', 'win32', 'linux') or
                sys.platform == 'linux' and provider != 'zcode' or
                not self.gateway_running or provider == 'codex' and (not self.bridge or
                not self.bridge.desktop_restart_recommended())):
            raise ValueError('桌面恢复退出确认无效，请重新检查连接状态')
        # Older clients use disabling as native quit. New clients choose explicitly.
        quit_desktop = value.get('quitDesktop', provider != 'zcode')
        if provider == 'deepseek' and self.config.get('deepseekManualBinding'):
            with self.client_lock, self._changing(provider):
                if not enabled and quit_desktop:
                    raise ValueError('手动绑定仅支持停用手机接入；请在第三方客户端退出应用')
                if enabled and self.deepseek_restore_error:
                    raise ValueError(self.deepseek_restore_error)
                self.config.setdefault('enabled', {})[provider] = enabled
                private_json(self.config_path, self.config)
                self.client_cache = None
                return self.clients(refresh=True)
        from bridge.clients.lifecycle import inspect_client, launch_client
        accounts = self.bridge.accounts if provider == 'codex' and self.bridge else None
        with self.client_lock, self._changing(provider, rebind='connectionMode' not in value), accounts.gate if accounts else nullcontext(), accounts.lock if accounts else nullcontext():
            if provider == 'zcode' and 'connectionMode' in value:
                self._select_zcode_connection(value['connectionMode'])
            if not enabled and not quit_desktop:
                # Revoke gateway access without inspecting or interrupting tasks.
                # Keep the same write gate so a queued send sees the new setting.
                if provider == 'claude':
                    self.adapters[provider].cancel()
                from bridge.clients.zcode.connections import selected_mode
                if provider == 'zcode' and selected_mode(self.config) == 'official-remote':
                    self.adapters[provider].disconnect()
                self.config.setdefault('enabled', {})[provider] = False
                private_json(self.config_path, self.config)
                self.client_cache = None
                return self.clients()
            if accounts:
                accounts.assert_editable()
            if enabled:
                row = next(row for row in self.clients(refresh=True)['clients'] if row['id'] == provider)
                if provider == 'codex':
                    row = row['entries'][0]
                if not row['selectable']:
                    raise ValueError(row['reason'])
            if not self.gateway_running:
                self.config.setdefault('enabled', {})[provider] = enabled
                private_json(self.config_path, self.config)
                self.client_cache = None
                return self.clients()
            descriptor = self._descriptor(provider)
            if enabled:
                if provider == 'deepseek':
                    self._start_deepseek()
                elif provider == 'zcode' and descriptor['officialRemote']:
                    state = inspect_client(descriptor)
                    if state.get('unknown') or len(state.get('mainPids', [])) > 1:
                        raise ValueError('无法确认唯一的 ZCode 进程，请刷新后重试。')
                    launched = False
                    if not state['running']:
                        result = launch_client(descriptor)
                        if not result.get('running'):
                            raise ValueError('ZCode 尚未启动，请刷新后重试。')
                        launched = result.get('launched') is True
                    self.adapters[provider].connect_application(None, wait=launched)
                elif provider == 'zcode' and (initialize_zcode or descriptor['pipeSupported']):
                    self._start_zcode_connection(descriptor, initialize=initialize_zcode,
                                                 acknowledge_unavailable=acknowledge_unavailable)
                elif provider == 'claude':
                    if value.get('initializeDesktop') is True:
                        self.adapters[provider].connect()
                    elif self.adapters[provider].status().get('cliAvailable') is True:
                        # CLI/SSH availability does not replace a previously
                        # prepared Desktop mailbox. Recover that signed channel
                        # without launching an App for a CLI-only selection.
                        if (self._claude_reconnect_supported()
                                and self.adapters[provider].discovery.get('autoConnect') is not False
                                and not self.adapters[provider].status().get('connected')):
                            self.adapters[provider].reconnect()
                    elif sys.platform == 'darwin':
                        result = launch_client(descriptor)
                        if not result.get('running'):
                            raise ValueError('客户端尚未启动，请在电脑端检查后重试')
                        self.adapters[provider].reconnect()
                    elif sys.platform == 'win32':
                        if self.instance is not None:
                            result = launch_client(descriptor)
                            if not result.get('running'):
                                raise ValueError('客户端尚未启动，请在电脑端检查后重试')
                            self.adapters[provider].reconnect()
                        else:
                            self.adapters[provider].reconnect(launch=True)
                    else:
                        raise ValueError('此系统不支持 Claude 桌面连接')
                else:
                    result = launch_client(descriptor)
                    if provider == 'zcode' and not startup and not result.get('launched'):
                        adapter = self.adapters[provider]
                        if getattr(adapter, 'sid', None) and adapter.status().get('retryable'):
                            if not adapter.reconnect_existing():
                                raise ValueError('ZCode 未能恢复原会话，请在聊天中重试连接。')
                    if not result.get('running'):
                        raise ValueError('客户端尚未启动，请在电脑端检查后重试')
            else:
                if provider == 'claude' and self._claude_native_cli_busy():
                    raise ValueError('有 Claude Code 任务运行或等待确认，请先结束任务再关闭客户端')
                self._stop_client(provider, descriptor, inspect_client(descriptor),
                                  native_confirmation=value.get('quitDesktop') is True,
                                  force=value.get('forceDesktop') is True,
                                  **({'acknowledge_unavailable': True} if acknowledge_unavailable else {}))
            # Persist only after the native lifecycle operation succeeds.
            self.config.setdefault('enabled', {})[provider] = enabled
            private_json(self.config_path, self.config)
            if not enabled and hasattr(self, 'terminals'):
                identifiers = {str(uuid.uuid5(uuid.NAMESPACE_URL, p + ':' + sid)) for p, sid in self.workspace_roots if p == provider}
                with self.terminals.lock:
                    for collection in (self.terminals.sessions, self.terminals.jobs):
                        for (_, thread, _), process in collection.items():
                            if thread in identifiers:
                                process.stop()
            self.client_cache = None
            return self.clients()

    def _account_store(self, provider):
        if provider == 'deepseek' and self.config.get('deepseekManualBinding'):
            raise ValueError('手动绑定的账号和 API 请在第三方客户端中管理')
        if provider not in self.adapters:
            raise ValueError('不支持的桌面客户端')
        if provider != 'zcode' and provider not in self.config.get('discovered', {}):
            self.scan(scan_wsl=False)
        descriptor = self._descriptor(provider)
        home = descriptor.get('dataDirectory')
        if not home or not descriptor.get('installed'):
            raise ValueError('尚未安装此客户端')
        key = (provider, home)
        if provider == 'claude':
            key += (descriptor.get('executable'), descriptor.get('packageFamilyName'),
                    bool(descriptor.get('dataDirectoryExplicit')))
        if key not in self.account_stores:
            if provider == 'zcode':
                from bridge.clients.zcode.accounts import ZCodeAccounts
                store = ZCodeAccounts(self.directory/'zcode-accounts', home,
                                      lambda: self.adapters[provider].call('account')['account'],
                                      quota_reader=lambda refresh: self.adapters[provider].call('quota', body={'refresh': refresh}))
            elif provider == 'claude':
                from bridge.clients.claude.accounts import ClaudeAccounts
                store = ClaudeAccounts(self.directory/'claude-accounts', home,
                    executable=descriptor.get('executable'), package_family=descriptor.get('packageFamilyName'),
                    explicit_home=bool(descriptor.get('dataDirectoryExplicit')),
                    **({'native_profile': self.instance.profile} if getattr(self, 'instance', None) else {}))
            else:
                from bridge.clients.deepseek.accounts import DeepSeekAccounts
                store = DeepSeekAccounts(self.directory/'deepseek-accounts', home,
                                         **({'inherit_environment': False} if getattr(self, 'instance', None) else {}))
            self.account_stores[key] = store
        return self.account_stores[key]

    def _accounts_public(self, provider, value):
        try:
            identity = self._account_model_identity(provider)
        except (OSError, ValueError, KeyError, TypeError, BridgeUnavailable):
            identity = ''
        return {**value, 'provider': provider, 'gatewayRunning': self.gateway_running,
                'canSwitch': provider not in self.account_operations,
                'canSyncLocalCli': provider == 'claude' and getattr(self, 'instance', None) is None
                    and getattr(getattr(self, 'bridge', None), 'cli_access', None) is not None,
                'cliSync': self.cli_sync_status.get(provider), 'modelCatalogIdentity': identity}

    def _launch_account_client(self, provider, descriptor):
        from bridge.clients.lifecycle import launch_client
        if self.instance is not None:
            descriptor = self._descriptor(provider)
            if provider == 'claude':
                self.adapters[provider].configure(descriptor['executable'], descriptor['dataDirectory'], explicit_home=True)
        if provider == 'zcode':
            if not launch_client(descriptor).get('running'):
                raise ValueError('ZCode 尚未启动，请在电脑端检查后重试')
        elif provider == 'deepseek':
            self._start_deepseek()
        elif provider == 'claude' and sys.platform == 'win32' and self.instance is None:
            from bridge.clients.claude.setup import background_running_app
            reconnect = self.gateway_running and self.enabled(provider)
            # Account transactions require a confirmed main process before
            # committing restored credentials; reconnect alone is asynchronous.
            background_running_app(descriptor['executable'], descriptor['dataDirectory'],
                                   initialize_console=reconnect,
                                   explicit_home=bool(descriptor.get('dataDirectoryExplicit')))
            if reconnect:
                self.adapters[provider].reconnect(launch=True)
        else:
            result = launch_client(descriptor)
            if not result.get('running'):
                raise ValueError('客户端尚未启动，请在电脑端检查后重试')
            if self.gateway_running and self.enabled(provider):
                self.adapters[provider].reconnect()

    def client_accounts(self, provider, value):
        operation = value.get('operation', 'list')
        if provider == 'deepseek' and self.config.get('deepseekManualBinding'):
            if operation != 'list':
                raise ValueError('手动绑定的账号和 API 请在第三方客户端中管理')
            if self.deepseek_restore_error:
                raise BridgeUnavailable(self.deepseek_restore_error)
            native = self.adapters[provider].call('account')
            return {**native, 'accounts': [], 'provider': provider, 'gatewayRunning': self.gateway_running,
                    'canImport': False, 'canSwitch': False, 'readOnly': True,
                    'message': '账号和 API 请在已绑定的第三方客户端中管理'}
        sync_cli = value.get('syncLocalCli') is True
        if 'syncLocalCli' in value and not isinstance(value['syncLocalCli'], bool):
            raise ValueError('本机 CLI 同步选项无效')
        if sync_cli and (provider != 'claude' or operation != 'switch'):
            raise ValueError('本机 CLI 同步仅支持 Claude Desktop 接入切换')
        store = self._account_store(provider)
        if operation == 'list':
            return self._accounts_public(provider, store.public())
        if operation == 'details':
            return self._accounts_public(provider, store.details(value.get('id'), refresh=value.get('refresh') is True))
        if operation == 'edit':
            return store.edit(value.get('id'))
        if operation == 'models-draft':
            # Desktop-only enrollment, like save-api. The shared probe only GETs
            # model metadata; it neither saves the draft nor changes the owner.
            from bridge.features.accounts.api_drafts import model_draft
            protocol = ('anthropic-messages' if provider == 'claude' else
                        value.get('apiFormat') if provider == 'zcode' else 'openai-chat-completions')
            return model_draft(value, protocol=protocol,
                               fixed_url='https://api.deepseek.com' if provider == 'deepseek' else None,
                               saved=store.api_credentials)
        if operation in ('save-api', 'rename', 'remove'):
            # Desktop IPC only: HTTP exposes list/details/switch. These mutate
            # saved copies, never the native login or a running conversation.
            with self.client_lock:
                if operation == 'save-api':
                    result = store.save_api(value)
                elif operation == 'rename':
                    result = store.rename(value.get('id'), value.get('name'))
                else:
                    result = store.remove(value.get('id'))
                return self._accounts_public(provider, result)
        if operation not in ('import-current', 'switch'):
            raise ValueError('不支持的账号操作')
        from bridge.clients.lifecycle import inspect_client
        with self.client_lock, self._changing(provider):
            if sync_cli:
                if not self.bridge or not getattr(self.bridge, 'cli_access', None):
                    raise ValueError('本机 Claude Code CLI 接入管理器不可用')
                from bridge.features.accounts.cli_sync import LocalCliSync
                LocalCliSync(self.bridge).preflight('claude')
                selected = next((row for row in store.public()['accounts'] if row['id'] == value.get('id')), None)
                if not selected or selected.get('kind') != 'api' or selected.get('saved') is not True:
                    raise ValueError('官方 Claude Desktop 登录不能同步到本机 CLI')
            # Harness persists one atomic credential file; saving it does not
            # require the multi-file profile snapshot used by Claude.
            if provider in ('deepseek', 'zcode') and operation == 'import-current':
                options = {'kind': value.get('kind', '')} if provider == 'deepseek' else {}
                result = store.import_current(name=value.get('name', ''), **options)
                return self._accounts_public(provider, result)
            descriptor = self._descriptor(provider)
            state = inspect_client(descriptor)
            restart = state['running'] or self.gateway_running and self.enabled(provider)
            if provider == 'zcode' and operation == 'switch' and restart:
                # A normal restart cannot verify a changed login through the old
                # test process. Leave the current login and running app untouched.
                raise ValueError('请先停用 ZCode 手机接入并退出原 App，再切换已保存账号')
            if state['running'] and not self.gateway_running:
                raise ValueError('请先退出客户端，再保存或切换账号；也可启动网关后操作')
            # Stop only after all tasks and requests are confirmed idle.
            self._stop_client(provider, descriptor, state)
            self.account_operations.add(provider)
            rollback = None
            try:
                if operation == 'import-current':
                    options = {'kind': value.get('kind', '')} if provider == 'deepseek' else {}
                    result = store.import_current(name=value.get('name', ''), **options)
                else:
                    rollback = store.restore(value.get('id'))
                    result = store.public()
                    active = result.get('activeIds', [result.get('activeId')])
                    if value.get('id') not in active:
                        raise ValueError('账号配置尚未生效，请重试')
                if restart:
                    self._launch_account_client(provider, descriptor)
                    if operation == 'switch':
                        result = store.public()
                        active = result.get('activeIds', [result.get('activeId')])
                        if value.get('id') not in active:
                            raise ValueError('账号配置尚未生效，请重试')
                if operation == 'switch' and sync_cli:
                    try:
                        imported = self.bridge.cli_access.change({'provider': 'claude', 'action': 'importDesktop',
                                                                   'desktopId': value['id']})
                        saved_id = imported.get('savedId')
                        if not saved_id:
                            raise ValueError('Claude Code CLI 接入未保存')
                        selected = self.bridge.cli_access.change({'provider': 'claude', 'action': 'switch',
                                                                   'id': saved_id, 'confirmed': True})
                        if selected.get('claude', {}).get('activeId') != saved_id:
                            raise ValueError('Claude Code CLI 未确认目标接入')
                        self.cli_sync_status[provider] = {'status': 'verified'}
                    except Exception as error:
                        self.cli_sync_status[provider] = {'status': 'failed', 'error': str(error),
                                                          'desktopStatus': 'verified'}
                elif operation == 'switch':
                    self.cli_sync_status[provider] = {'status': 'unchanged'}
            except Exception as error:
                try:
                    if rollback is not None:
                        current = inspect_client(descriptor)
                        if current['running']:
                            self._stop_client(provider, descriptor, current)
                        store.rollback(rollback)
                    if state['running']:
                        self._launch_account_client(provider, descriptor)
                except (ValueError, OSError, subprocess.SubprocessError):
                    raise ValueError('账号操作未完成，请在电脑端检查；恢复备份已保留') from error
                raise
            else:
                if rollback is not None and hasattr(store, 'commit'):
                    try:
                        store.commit(rollback)
                    except OSError:
                        pass  # Keep the private rollback snapshot if cleanup fails.
            finally:
                self.account_operations.discard(provider)
                with self.read_lock:
                    self.workspace_roots = {key: row for key, row in self.workspace_roots.items() if key[0] != provider}
                self.client_cache = None
            return self._accounts_public(provider, result)

    def workspace_bridge(self, provider, sid):
        # Obtain the root from the desktop, never from a browser-supplied path.
        self.require_enabled(provider)
        if not isinstance(sid, str) or not sid or len(sid) > 512:
            raise ValueError('会话标识无效')
        with self.read_lock:
            epoch = self.binding_epochs[provider]
            if self.binding_changes[provider]:
                raise BridgeUnavailable('客户端状态已变化，请重试')
            cached = self.workspace_roots.get((provider, sid))
            cached = cached if cached and cached[2] == epoch and time.monotonic() - cached[0] < 10 else None
        if cached:
            row = cached[1]
        else:
            rows = self.call(provider, 'list').get('sessions', [])
            row = next((row for row in rows if row.get('id') == sid), None)
            if row is None:
                raise ValueError('桌面会话不存在')
            with self.read_lock:
                if self.binding_epochs[provider] != epoch or self.binding_changes[provider]:
                    raise BridgeUnavailable('客户端状态已变化，请重试')
                self.workspace_roots[(provider, sid)] = (time.monotonic(), row, epoch)
        from bridge.features.workspace.workspace import Workspace
        root = row.get('cwd')
        if provider == 'claude' and str(row.get('host', '')).startswith('wsl-claude:'):
            return WslClaudeWorkspace(self, sid, row)
        Workspace(root)  # Reject unavailable and non-local workspaces (e.g. Cowork VM).
        return DesktopWorkspace(self, provider, sid, root)

    def control(self, value):
        action = value.get('action', 'status')
        if action == 'client-profiles':
            return self.instance_control(value)
        entry = value.get('entry')
        if entry is not None:
            family = value.get('provider') or str(entry).split(':')[0]
            child = self.resolve_instance(family, entry, require_enabled=False)
            if child is not self:
                return child.control({key: item for key, item in value.items() if key != 'entry'})
            if self.instance is not None:
                value = {key: item for key, item in value.items() if key != 'entry'}
        if self.instance is not None:
            family = value.get('provider')
            if family is not None and family != self.instance.family:
                raise ValueError('接入实例与平台不匹配')
            allowed = {'accounts', 'account', 'clients', 'status', 'scan', 'toggle-client'}
            allowed.update({'claude-prerequisites', 'claude-quit-preview', 'claude-quit-confirm',
                            'connect-claude', 'restart-claude', 'cancel-claude', 'prepare-claude'}
                           if self.instance.family == 'claude' else {'connect-deepseek', 'restart-deepseek'})
            if action not in allowed:
                raise ValueError('此操作不适用于独立桌面配置')
        if action == 'preview-deepseek-plugin':
            from bridge.clients.deepseek.manual import ManualDeepSeek
            adapter = ManualDeepSeek(value.get('file', ''))
            status = adapter.call('status')
            if status.get('connected') is not True:
                raise ValueError('插件尚未连接，请在第三方客户端加载插件')
            return {'profile': adapter.binding['profile'], 'home': adapter.binding['home'],
                    'instanceId': adapter.binding['instanceId'], 'plugin': adapter.plugin,
                    'file': str(adapter.directory/'binding.json')}
        if action == 'scan-deepseek-plugins':
            from bridge.clients.deepseek.manual import discover_plugins
            return {'candidates': discover_plugins(value.get('home'))}
        if action == 'codex-profiles':
            entries = getattr(self.bridge, 'entries', None)
            if entries is None:
                raise ValueError('当前网关未提供本机 Codex 配置管理')
            operation = value.get('operation')
            if operation in ('list', 'scan') and set(value) == {'action', 'operation'}:
                return entries.local_profiles(scan=operation == 'scan')
            if operation == 'save' and set(value) == {'action', 'operation', 'home', 'label'}:
                result = entries.save_local(value['home'], value['label'])
                self.client_cache = None
                return result
            raise ValueError('Codex 配置请求无效')
        if action == 'scan-wsl':
            return self.scan_wsl()
        if action == 'claude-prerequisites':
            result = self.adapters['claude'].prerequisites(request_permission=value.get('requestPermission') is True)
            return {**result, 'gatewayRunning': self.gateway_running}
        if action in ('claude-quit-preview', 'claude-quit-confirm'):
            return self.quit_claude(value)
        if action in ('deepseek-recovery-preview', 'deepseek-recovery-confirm'):
            return self.recover_deepseek(value)
        if action == 'accounts':
            return self.client_accounts(value.get('provider'), value)
        if action == 'account':
            provider = value.get('provider')
            if provider not in self.adapters:
                raise ValueError('不支持的桌面客户端')
            return self._account_models(provider, refresh=value.get('refresh') is True)
        if action == 'scan':
            return self.scan(setup=value.get('setup', True) is True)
        if action in ('connect-deepseek', 'restart-deepseek'):
            return self.connect_deepseek(restart=action == 'restart-deepseek')
        if action in ('connect-claude', 'restart-claude', 'cancel-claude'):
            adapter = self.adapters['claude']
            if action == 'cancel-claude':
                with self._changing('claude'):
                    adapter.cancel()
            else:
                return self.connect_claude(restart=action == 'restart-claude')
            return self.clients(refresh=True)
        if action == 'clients':
            return self.clients(refresh=value.get('refresh') is True)
        if action == 'zcode-official':
            if (set(value) != {'action', 'enabled'} or type(value['enabled']) is not bool or
                    sys.platform not in ('darwin', 'win32', 'linux') or 'zcode' not in self.adapters):
                raise ValueError('ZCode 官方远控设置无效')
            if value['enabled']:
                return self.toggle_client({'provider': 'zcode', 'enabled': True, 'connectionMode': 'official-remote'})
            with self.client_lock:
                self._select_zcode_connection('private-pipe')
            return self.clients(refresh=True)
        if action == 'zcode-compatibility':
            raise ValueError('本机兼容端口已移除，请选择本机连接或官方远控')
        if action == 'toggle-client':
            return self.toggle_client(value)
        if action == 'status':
            if self.instance is not None:
                return {'backends': self.status(), 'instance': self.instance.id}
            return {'backends': self.status(), 'deepseekHome': str(self.adapters['deepseek'].home),
                    'deepseekBinding': self.config.get('deepseekManualBinding'),
                    'claudeWorkspace': str(self.adapters['claude'].directory)}
        provider = 'claude' if action == 'prepare-claude' else 'deepseek'
        with self._changing(provider):
            if action == 'export-deepseek-plugin':
                from bridge.clients.deepseek.manual import export_package
                return export_package(value.get('destination', ''))
            if action in ('bind-deepseek-plugin', 'unbind-deepseek-plugin'):
                if self.enabled('deepseek'):
                    raise ValueError('请先仅停用 DSH 手机接入，再切换或解除绑定')
                if action == 'bind-deepseek-plugin':
                    from bridge.clients.deepseek.manual import ManualDeepSeek
                    adapter = ManualDeepSeek(value.get('file', ''), value.get('instanceId'))
                    status = adapter.call('status')
                    if status.get('connected') is not True:
                        raise ValueError('插件尚未连接，请在第三方客户端加载插件')
                    # Independent gateways using this plugin share mutation receipts.
                    with closing(sqlite3.connect(adapter.receipts_path)) as db, db:
                        db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, result TEXT)')
                    adapter.receipts_path.chmod(0o600)
                    self.config['deepseekManualBinding'] = {'file': str(adapter.directory/'binding.json'),
                                                           'instanceId': adapter.binding['instanceId']}
                else:
                    self.config.pop('deepseekManualBinding', None)
                    adapter = DeepSeek(self.directory/'deepseek', self.config.get('deepseekHome'))
                self.adapters['deepseek'] = adapter
                self.deepseek_restore_error = None
                self.config.setdefault('setup', {}).pop('deepseek', None)
                private_json(self.config_path, self.config)
                self.client_cache = None
                return {'bound': action == 'bind-deepseek-plugin'}
            if action == 'prepare-claude':
                return self.adapters['claude'].prepare()
            if action == 'install-deepseek':
                if self.config.get('deepseekManualBinding'):
                    raise ValueError('请先解除手动绑定，再使用官方客户端一键安装')
                home = value.get('home')
                if not isinstance(home, str) or not Path(home).expanduser().is_dir():
                    raise ValueError('请选择 Harness 数据目录')
                adapter = DeepSeek(self.directory/'deepseek', home)
                result = adapter.install()
                self.config['deepseekHome'] = str(adapter.home)
                private_json(self.config_path, self.config)
                self.adapters['deepseek'] = adapter
                return result
            if action == 'remove-deepseek':
                return self.adapters['deepseek'].uninstall()
        raise ValueError('不支持的接入操作')

    def scan_wsl(self):
        entries = getattr(self.bridge, 'entries', None)
        if entries is None or self.claude_wsl is None:
            raise ValueError('当前电脑不支持 WSL 应用')
        with self.client_lock:
            result = entries.scan()
            claude = self.claude_wsl.scan()
            self.client_cache = None
            return {**self.clients(refresh=True), 'errors': result.get('errors', []) + claude.get('errors', [])}

    def close(self):
        self.closed.set()
        if self.instances is not None:
            self.instances.close()
        with self.model_lock:
            for timer in self.model_timers.values():
                timer.cancel()
            self.model_timers.clear()
        if self.startup_thread:
            self.startup_thread.join(timeout=5)
        if self.bridge is not None and getattr(self.bridge.accounts, 'require_client_enabled', None) is self._codex_guard:
            del self.bridge.accounts.require_client_enabled
        if hasattr(self, 'terminals'):
            self.terminals.close()
        for lock in self.locks.values():
            lock.acquire()
        try:
            if 'claude' in self.adapters:
                self.adapters['claude'].close()
            if self.zcode_pipe is not None:
                self.zcode_pipe.close()
            with self.db_lock:
                for db in self.receipt_dbs.values():
                    db.close()
                self.db.close()
        finally:
            for lock in self.locks.values():
                lock.release()


class DesktopWorkspace:
    """Use the existing file and terminal services with a desktop-verified root."""
    host = 'local'

    def __init__(self, manager, provider, sid, root):
        from bridge.features.terminals.manager import TerminalManager
        self.manager, self.provider = manager, provider
        self.root = root
        self.identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, provider + ':' + sid))
        with manager.client_lock:
            if not hasattr(manager, 'terminals'):
                manager.terminals = TerminalManager()
        self.terminals = manager.terminals
        self.store = self

    def get(self, identifier):
        if identifier != self.identifier:
            raise ValueError('会话标识不一致')
        return {'cwd': self.root}

    def workspace(self, sid, action, params):
        from bridge.features.workspace.workspace import operate
        # HTTP may resolve this object before waiting for an upload body. Check
        # again under the same gate used by disabling access before any write.
        with self.manager.locks[self.provider]:
            self.manager.require_enabled(self.provider)
            return operate(self.root, action, params)

    def terminal(self, sid, owner, action, params):
        from bridge.app.service import Bridge
        with self.manager.locks[self.provider]:
            self.manager.require_enabled(self.provider)
        return Bridge.terminal(self, self.identifier, owner, action, params)


class WslClaudeWorkspace:
    """Native Linux file boundaries with the same standalone workspace rules."""
    host = 'local'

    def __init__(self, manager, sid, row):
        self.manager, self.sid, self.row = manager, sid, row
        self.identifier = sid
        self.store = self

    def get(self, identifier):
        if identifier != self.identifier:
            raise ValueError('会话标识不一致')
        return {'cwd': self.row['cwd']}

    def workspace(self, sid, action, params):
        with self.manager.locks['claude']:
            self.manager.require_enabled('claude')
            remote = self.manager.adapters['claude'].remote_cli.wsl
            fresh = remote._get(self.sid)
            if fresh['cwd'] != self.row['cwd'] or fresh['origin'] != self.row.get('origin', fresh['origin']):
                raise ValueError('WSL Claude Code 工作目录或账号已变化')
            source = source_text('features/workspace/workspace.py')
            arguments = payload({'root': fresh['cwd'], 'action': action, 'params': params})
            if action in ('download-stream', 'archive-stream'):
                source += '\nimport json,sys\ntry:\n with operate(**'+arguments+') as (metadata, stream):\n  sys.stdout.buffer.write(json.dumps(metadata).encode()+b"\\n")\n  while True:\n   chunk=stream.read(65536)\n   if not chunk: break\n   sys.stdout.buffer.write(chunk)\nexcept (ValueError, PermissionError, OSError) as error:\n sys.stdout.buffer.write(json.dumps({"error":str(error),"permission":isinstance(error,PermissionError)}).encode()+b"\\n")\n'
                return self._download(fresh['host'], source)
            source += '\nimport json\ntry:\n result={"value":operate(**'+arguments+')}\nexcept (ValueError,PermissionError) as error:\n result={"error":str(error),"permission":isinstance(error,PermissionError)}\nprint(json.dumps(result))\n'
            result = self.manager.claude_wsl.read(fresh['host'], source)
            if 'error' in result:
                if result.get('permission'):
                    raise PermissionError(result['error'])
                raise ValueError(result['error'])
            return result['value']

    @contextmanager
    def _download(self, host, source):
        from bridge.platforms.windows import wsl
        with self.manager.locks['claude']:
            self.manager.require_enabled('claude')
            row = self.manager.claude_wsl.selected(host)
            self.manager.adapters['claude'].remote_cli.wsl._get(self.sid)
            with wsl.download(row['distribution'], source, row['user']) as value:
                yield value

    def terminal(self, sid, owner, action, params):
        raise ValueError('WSL Claude Code 暂不提供手机终端')
