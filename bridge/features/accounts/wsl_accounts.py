"""Registered-environment account routing, isolated from native local accounts."""
import contextlib
import json
from pathlib import Path
import re
import threading

from bridge.clients.codex.catalog import ModelStore
from bridge.features.accounts.wsl_account_models import WslAccountModels
from bridge.platforms.windows import wsl
from bridge.features.accounts.wsl_account_payload import worker_source
from bridge.features.accounts.wsl_account_worker import ERROR_MESSAGES
from bridge.features.accounts.draft_identity import draft_identity


class WslAccounts:
    def __init__(self, bridge):
        self.bridge = bridge
        self.lock = threading.RLock()
        self.closed = False
        self.models_store = ModelStore(Path(bridge.data_dir)/'wsl-accounts')
        self.model_catalog = WslAccountModels(self)

    def _registry(self, provider):
        if provider == 'codex':
            registry = getattr(self.bridge, 'entries', None)
        elif provider == 'claude':
            registry = getattr(getattr(self.bridge, 'desktop_sessions', None), 'claude_wsl', None)
        else:
            raise ValueError('WSL 客户端无效')
        if registry is None:
            raise ValueError('此网关不支持 WSL 账号')
        return registry

    def _row(self, provider, identifier, *, enabled=False):
        pattern = r'wsl:[0-9a-f]{32}' if provider == 'codex' else r'wsl-claude:[0-9a-f]{32}'
        if not isinstance(identifier, str) or not re.fullmatch(pattern, identifier):
            raise ValueError('WSL 入口身份无效')
        registry = self._registry(provider)
        row = next((dict(row) for row in registry.entries if row.get('id') == identifier), None)
        if row is None or (enabled and not row.get('enabled', True)):
            raise ValueError('WSL 入口未登记或已关闭')
        # Registration files are not an authorization to retarget another path.
        if provider == 'codex':
            from bridge.features.endpoints.registry import entry_id
            expected = entry_id(row['distribution'], row['user'], row['home'])
        else:
            from bridge.clients.claude.wsl import entry_id
            expected = entry_id(row['distribution'], row['user'], row['configDir'])
        if expected != identifier:
            raise ValueError('WSL 登记身份已变化')
        return row

    @staticmethod
    def _target(row):
        return {'distribution': row['distribution'], 'user': row['user'],
                'configDir': row.get('configDir', row.get('home'))}

    def environments(self, provider):
        registry = self._registry(provider)
        with registry.lock:
            entries = [dict(row) for row in registry.entries if row.get('distribution')]
        running = wsl.displayed_distributions(entries)
        rows = [{**self._target(row), 'id': row['id'], 'label': row['label'],
                 'enabled': row.get('enabled', True), 'running': row['distribution'] in running}
                for row in entries]
        return {'provider': provider, 'environments': rows}

    def _call(self, provider, row, request):
        payload = {**request, 'provider': provider, 'directory': self._target(row)['configDir']}
        # wsl.read passes all source bytes exclusively through subprocess stdin;
        # neither credentials nor the request are placed in process arguments.
        source = worker_source()
        source += '\ntry:\n value=execute(json.loads('+repr(json.dumps(payload, ensure_ascii=False))+'))\n print(json.dumps({"ok":True,"value":value}))\n'
        source += 'except AccountError as error:\n code=next((key for key,message in ERROR_MESSAGES.items() if message==str(error)),"failed")\n print(json.dumps({"ok":False,"errorCode":code}))\nexcept Exception:\n print(json.dumps({"ok":False,"errorCode":"failed"}))\n'
        result = wsl.read(row['distribution'], source, row['user'], timeout=25, allow_start=False)
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise ValueError(ERROR_MESSAGES.get(result.get('errorCode'), ERROR_MESSAGES['failed']) if isinstance(result, dict) else ERROR_MESSAGES['failed'])
        return result['value']

    def _public(self, provider, row, value):
        if provider == 'claude' and value.get('draftContinuations'):
            value = dict(value)
            value['draftContinuations'] = [{**item, **{key: draft_identity('claude', 'wsl-cli', row['id'], item[key])
                if item.get(key) else '' for key in ('previousAccountIdentity', 'accountIdentity')}}
                for item in value['draftContinuations']]
        return {**value, 'provider': provider, 'entry': row['id'], 'target': self._target(row),
                'sharedWithDesktop': False}

    def read(self, provider, entry):
        with self.lock:
            if self.closed:
                raise ValueError('网关正在停止')
            registry = self._registry(provider)
            with registry.lock:
                row = self._row(provider, entry)
                if row['distribution'] not in wsl.running_distributions():
                    return self._public(provider, row, {'available': False, 'identity': None, 'accounts': [],
                        'activeId': None, 'blockers': ['WSL 已停止，请在电脑端启动后查看账号'],
                        'capabilities': {'save': False, 'import': False, 'switch': False, 'restore': False, 'models': False}})
                value = self._call(provider, row, {'action': 'read'})
                if not row.get('enabled', True):
                    value['blockers'].append('此 WSL 入口已关闭')
                    value['capabilities'] = {key: False for key in value['capabilities']}
                    return self._public(provider, row, value)
            # Do not hold registration while waiting for the Claude manager.
            # Eligibility is advisory; change() rechecks under the write gates.
            try:
                with self._change_scope(provider, entry, 'inspect-switch') as release:
                    current = self._row(provider, entry, enabled=True)
                    if self._target(current) != self._target(row):
                        raise ValueError('WSL 登记身份已变化')
                    if provider != 'codex' or release is None:
                        with self._guard(provider, current):
                            pass
                    if (provider == 'codex' and release is not None and release.restartRequired and
                            value.get('runtimeReleaseCandidate') is True):
                        value['restartRequired'] = True
                        if value.get('configurationBlockers') == []:
                            value['capabilities']['attemptSwitch'] = True
                        if value.get('activeId') and any(account.get('id') == 'native' for account in value.get('accounts', [])):
                            value['capabilities']['attemptRestore'] = True
                    if provider == 'claude' and release is not None and release.recovery_identity:
                        if release.recovery_identity == value.get('identity'):
                            try:
                                value['draftContinuations'] = release.after_switch(value['identity'])
                            except Exception:
                                value['continuationPending'] = True
                                value['continuationError'] = '账号配置已切换，原会话续接尚未核验；请刷新检查，不要重复提交切换'
                        else:
                            value['continuationPending'] = True
                            value['continuationError'] = '原会话续接记录与当前账号不符，请检查原 WSL 配置'
                    if (provider == 'claude' and release is not None and release.releasable and
                            value.get('runtimeReleaseCandidate') is True):
                        # An attempt is permitted, not proof of safe switching:
                        # only the worker can rule out another native executor.
                        configuration = value.get('configurationBlockers')
                        if isinstance(configuration, list) and not configuration:
                            value['capabilities']['attemptSwitch'] = True
                        if value.get('activeId') and any(account.get('id') == 'native' for account in value.get('accounts', [])):
                            value['capabilities']['attemptRestore'] = True
            except ValueError as error:
                value['blockers'].append(str(error) if type(error) is ValueError else
                    '无法核验 WSL 切换条件，请刷新并检查运行状态及操作记录')
                value['capabilities'].update(switch=False, restore=False)
            except Exception:
                # Failed verification is not evidence that the target is busy.
                value['blockers'].append('无法核验 WSL 切换条件，请刷新并检查运行状态及操作记录')
                value['capabilities'].update(switch=False, restore=False)
            if provider == 'codex':
                path = Path(registry.directory)/row['id'][4:]/'account-restart.json'
                if path.exists():
                    try:
                        pending = json.loads(path.read_text(encoding='utf-8'))
                        expected = pending.get('appliedIdentity') or pending.get('beforeIdentity')
                        if pending.get('phase') in ('prepared', 'stopped', 'applied'):
                            value.update(restartPending=True, restartError='WSL 共享服务恢复尚未核验，请检查并恢复原服务')
                            committed = (pending.get('plannedIdentity') == value.get('identity') and
                                         pending.get('selectedAccount') == value.get('activeId'))
                            value['capabilities']['recoverRestart'] = bool(committed or expected and expected == value.get('identity'))
                    except (OSError, ValueError, TypeError):
                        value['blockers'].append('WSL 共享服务恢复记录无法核验')
            return self._public(provider, row, value)

    @contextlib.contextmanager
    def _guard(self, provider, row):
        with contextlib.ExitStack() as locks:
            registry = self._registry(provider)
            if provider == 'codex':
                endpoint = getattr(registry, 'bridges', {}).get(row['id'])
                if endpoint is not None:
                    locks.enter_context(endpoint.write_lock)
                    locks.enter_context(endpoint.lock)
                    if any(receipt.get('result', {}).get('status') in ('queued', 'unknown', 'pending')
                           for receipt in endpoint.receipts.values()):
                        raise ValueError('此 WSL 接入存在排队或结果待核对的操作')
                    if endpoint.server is not None and not endpoint.server.closed:
                        raise ValueError('此 WSL 共享服务仍连接，无法保证重新载入认证；请先退出原服务')
                    for session in endpoint.live.values():
                        if session.activating or session.refreshing or session.loading:
                            raise ValueError('此 WSL 会话状态尚未核验')
                # Persisted unresolved writes also block after a gateway restart.
                path = Path(registry.directory)/row['id'][4:]/'operations.json'
                if path.exists():
                    receipts = json.loads(path.read_text(encoding='utf-8'))
                    if any(item.get('result', {}).get('status') in ('queued', 'unknown', 'pending') for item in receipts.values()):
                        raise ValueError('此 WSL 接入存在排队或结果待核对的操作')
            hook = getattr(self.bridge, 'wsl_account_guard', None)
            if hook and provider == 'codex':
                locks.enter_context(hook(provider, row))
            yield

    @contextlib.contextmanager
    def _change_scope(self, provider, entry, action):
        registry = self._registry(provider)
        with contextlib.ExitStack() as locks:
            release = None
            if provider == 'claude' and action in ('switch', 'inspect-switch'):
                with registry.lock:
                    row = self._row(provider, entry, enabled=True)
                hook = getattr(self.bridge, 'wsl_account_guard', None)
                if hook is None:
                    raise ValueError('WSL Claude Code 操作记录保护尚不可用')
                # Manager mutation lock precedes registry/remote locks, matching
                # normal Claude sends. The hook rechecks target registration.
                release = locks.enter_context(hook(provider, row, read_only=True, release_idle=True) if action == 'inspect-switch'
                                              else hook(provider, row, release_idle=True))
            locks.enter_context(registry.lock)
            if provider == 'codex' and action in ('switch', 'inspect-switch', 'recover-restart'):
                row = self._row(provider, entry, enabled=True)
                endpoint = getattr(registry, 'bridges', {}).get(entry)
                if endpoint is None and hasattr(registry, 'get'):
                    endpoint = registry.get(entry)
                if endpoint is not None and hasattr(endpoint, 'connection_lock'):
                    from bridge.features.accounts.wsl_codex_restart import CodexRestart
                    release = locks.enter_context(CodexRestart(endpoint).scope(recover=action == 'recover-restart',
                        advisory=action == 'inspect-switch'))
            yield release

    def change(self, value, desktop=False):
        if not isinstance(value, dict):
            raise ValueError('WSL 账号请求无效')
        allowed = {'provider', 'entry', 'action', 'id', 'name', 'baseUrl', 'model', 'apiKey', 'authScheme',
                   'expectedIdentity', 'sourceId', 'sourceIdentity', 'refresh'}
        if set(value)-allowed:
            raise ValueError('WSL 账号请求包含未知字段')
        provider, entry, action = value.get('provider'), value.get('entry'), value.get('action')
        if action not in ('read', 'save-api', 'import-api', 'import-current', 'import-sources', 'switch', 'delete', 'rename', 'models', 'recover-restart'):
            raise ValueError('WSL 账号操作不支持')
        if action == 'models':
            return self.model_catalog.get(value)
        with self.lock:
            if self.closed:
                raise ValueError('网关正在停止')
            if action == 'read':
                return self.read(provider, entry)
            if action == 'recover-restart' and provider != 'codex':
                raise ValueError('仅 WSL Codex 支持恢复共享服务')
            registry = self._registry(provider)
            with self._change_scope(provider, entry, action) as release:
                row = self._row(provider, entry, enabled=action != 'read')
                if action == 'import-sources':
                    return self._public(provider, row, {'sources': self.bridge.wsl_account_sources(provider)})
                if row['distribution'] not in wsl.running_distributions():
                    raise ValueError('WSL 已停止，请在电脑端启动后操作')
                if action == 'recover-restart':
                    if release is None:
                        raise ValueError('此网关无法核验 WSL 共享服务恢复')
                    current = self._call(provider, row, {'action': 'read'})
                    if not value.get('expectedIdentity') or current.get('identity') != value['expectedIdentity']:
                        raise ValueError('WSL 账号身份已变化，请刷新')
                    release.observe(current)
                    current['draftContinuations'] = release.restore(current['identity'])
                    restarted = bool(release.record.get('daemon'))
                    current.update(restartPending=False, restartVerified=restarted, restartRequired=restarted)
                    return self._public(provider, row, current)
                request = dict(value)
                if action == 'import-api':
                    request.update(self.bridge.wsl_account_import(provider, value))
                    request['action'] = 'save-api'
                if action == 'switch' and release is not None:
                    current = self._call(provider, row, {'action': 'read'})
                    if (not value.get('expectedIdentity') or current.get('identity') != value['expectedIdentity'] or
                            not any(account.get('id') == value.get('id') for account in current.get('accounts', []))):
                        raise ValueError('WSL 账号配置已变化，请刷新后重新选择')
                    if value.get('id') != 'native' and current.get('configurationBlockers'):
                        raise ValueError(current['configurationBlockers'][0])
                    if value.get('id') == 'native' and not current.get('activeId'):
                        raise ValueError('原生配置已被外部修改，请使用原工具处理')
                    if release.releasable and current.get('runtimeReleaseCandidate') is not True:
                        raise ValueError((current.get('runtimeBlockers') or ['无法核验 WSL 执行器释放条件'])[0])
                    release.identity = current['identity']
                    if provider == 'codex' and release.record is not None:
                        release.intent = self._call(provider, row, {'action': 'preview-switch', 'id': value['id'],
                                                                  'expectedIdentity': current['identity']})
                    release()
                    if provider == 'codex' and release.record and release.record['daemon']:
                        request['_verifiedUpdaters'] = release.record['daemon'].get('updaters', [])
                try:
                    with self._guard(provider, row) if action == 'switch' and (provider != 'codex' or release is None) else contextlib.nullcontext():
                        result = self._call(provider, row, request)
                except Exception:
                    if provider == 'codex' and release is not None and release.record and release.record.get('phase') == 'stopped':
                        try:
                            current = self._call(provider, row, {'action': 'read'})
                            committed = release.observe(current)
                            drafts = release.restore(current['identity'])
                            if committed:
                                restarted = bool(release.record.get('daemon'))
                                current.update(restartVerified=restarted, restartRequired=restarted,
                                               switchReconciled=True, draftContinuations=drafts)
                                return self._public(provider, row, current)
                        except Exception:
                            pass  # Persistent handoff record exposes explicit recovery on refresh.
                    raise
                if action == 'switch' and release is not None:
                    try:
                        result['draftContinuations'] = release.after_switch(result['identity'])
                        if provider == 'codex':
                            result['restartVerified'] = bool(release.restartRequired)
                            result['restartRequired'] = bool(release.restartRequired)
                    except Exception:
                        if provider == 'codex':
                            result.update(restartPending=True, restartError='账号配置已切换，原共享服务恢复尚未核验；请检查并恢复服务，不要重复切换')
                            result['capabilities']['recoverRestart'] = True
                        else:
                            result['continuationPending'] = True
                            result['continuationError'] = '账号配置已切换，原会话续接尚未核验；请刷新检查，不要重复提交切换'
                return self._public(provider, row, result)

    def close(self):
        with self.lock:
            self.closed = True
        self.model_catalog.close()
