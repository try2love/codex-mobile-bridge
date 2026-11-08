"""Lazy execution owners for registered native profiles; default owners stay put."""
from functools import partial
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import threading

from bridge.clients.profiles import NativeProfile, resolve_profile
from bridge.features.clients.instances import Instances
from bridge.features.auth.auth import replace_session_file
from bridge.platforms import discovery


class ClientInstances:
    def __init__(self, manager):
        self.manager = manager
        self.lock = threading.RLock()
        self.children = {}
        self.error = None
        self.registry = Instances(manager.directory.parent,
            resolvers={(family, 'desktop-profile'): partial(resolve_profile, family)
                       for family in ('claude', 'deepseek')},
            defaults=self.defaults, codex=getattr(manager.bridge, 'entries', None))

    def defaults(self):
        """Reserve the actual default roots, including native API/CLI siblings."""
        manager = self.manager
        home = Path.home()
        selected = manager.config.get('discovered', {})
        for family, row in selected.items():
            executable = row.get('executable')
            if family not in ('codex', 'claude', 'deepseek', 'zcode') or not executable:
                continue
            executable = Path(executable).expanduser().resolve()
            if executable.is_file():
                installation = next((part for part in executable.parents if part.suffix == '.app'), executable.parent)
                yield NativeProfile(family, 'installation', installation, {'application': installation})
        claude = Path(selected.get('claude', {}).get('dataDirectory') or
                      discovery(sys.platform).claude_home(home, os.environ)).expanduser().resolve()
        base = Path(str(claude)[:-3]) if str(claude).endswith('-3p') else claude
        code = Path(os.environ.get('CLAUDE_CONFIG_DIR') or home/'.claude').expanduser().resolve()
        claude_roots = {'userData': base, 'thirdParty': Path(str(base)+'-3p'), 'code': code}
        if sys.platform == 'win32':
            # Native 3p uses Local AppData even when the active official profile
            # is Roaming/Store userData; old Roaming 3p is a migration source.
            for name, key in (('nativeThirdParty', 'LOCALAPPDATA'), ('legacyThirdParty', 'APPDATA')):
                if os.environ.get(key):
                    claude_roots[name] = Path(os.environ[key]).expanduser().resolve()/'Claude-3p'
        yield NativeProfile('claude', 'default', base, claude_roots)
        dsh = Path(manager.config.get('deepseekHome') or selected.get('deepseek', {}).get('dataDirectory') or
                   os.environ.get('DSH_HOME') or home/'.dsh').expanduser().resolve()
        ui_base = (home/'Library/Application Support' if sys.platform == 'darwin' else
                   Path(os.environ.get('APPDATA') or home/'AppData/Roaming') if sys.platform == 'win32' else
                   Path(os.environ.get('XDG_CONFIG_HOME') or home/'.config'))
        dsh_roots = {'runtimeHome': dsh, 'userData': ui_base/'@deepseek-ai/dsh-desktop'}
        if sys.platform == 'win32':
            # Both packaged names are in use by installed official Windows
            # builds. Reserve their UI data without inspecting its contents.
            dsh_roots.update(desktopUserData=ui_base/'DSH Desktop', legacyUserData=ui_base/'DSH')
        yield NativeProfile('deepseek', 'default', dsh, dsh_roots)
        entries = getattr(manager.bridge, 'entries', None)
        if entries is not None and entries.desktop_home:
            yield NativeProfile('codex', 'default', entries.desktop_home, {'config': entries.desktop_home})

    def capabilities(self, family=None):
        target = family
        result = ([{'family': 'codex', 'kind': 'codex-local',
                    'supported': self.registry.codex is not None}] if target in (None, 'codex') else [])
        for family in ('claude', 'deepseek'):
            if target not in (None, family):
                continue
            reason = ''
            try:
                resolve_profile(family, self.manager.directory.parent)
                if family == 'claude' and sys.platform == 'linux':
                    raise ValueError('当前 Linux 尚未提供 Claude 原生桌面接入，不能新增可运行的独立配置')
            except ValueError as exc:
                reason = str(exc)
            result.append({'family': family, 'kind': 'desktop-profile', 'supported': not reason,
                           **({'reason': reason} if reason else {})})
        if target in (None, 'zcode'):
            result.append(self.manager.zcode_application_capability())
        return result

    def metadata(self, family=None):
        """Passive aggregation failures cannot take the default clients offline."""
        try:
            rows = self.registry.list(family)
            self.error = None
            return rows
        except (ValueError, OSError):
            self.error = '独立接入配置暂不可用，请在客户端与接入中重新检查'
            return []

    def default_claude_home(self):
        profile = next(value for value in self.defaults() if value.family == 'claude')
        # Keep ordinary Windows native package/profile selection unchanged.
        previous = self.manager.config.get('discovered', {}).get('claude', {}).get('dataDirectory')
        if sys.platform == 'win32':
            return previous or str(profile.home)
        return str(_claude_mode_home(profile))

    def rows(self):
        result = []
        for row in self.metadata():
            if row['family'] == 'codex':
                continue  # Existing Entries owns its runtime/status contract.
            child = self.children.get(row['id'])
            try:
                snapshot = child.clients_status() if child is not None else None
            except (ValueError, OSError):
                snapshot = {'clients': [{'id': row['family'], 'setupStatus': 'failed',
                    'connected': False, 'configured': False, 'reason': '此独立接入暂不可用，请重新检查配置目录'}]}
            live = next((value for value in (snapshot or {}).get('clients', [])
                         if value.get('id') == row['family']), {})
            installed = self.manager.config.get('discovered', {}).get(row['family'], {}).get('installed') is True
            result.append({'installed': installed, 'configured': False, 'connected': False, 'running': False,
                'selectable': True, 'setupStatus': 'registered', 'reason': '独立配置已保存，尚未接入原应用',
                **live, **row, 'name': row['label'], 'icon': row['family'], 'entry': row['id'],
                'capabilities': {**live.get('capabilities', {}), 'accounts': True}})
        return result

    def resolve(self, family, entry, require_enabled=True):
        with self.lock:
            return self._resolve(family, entry, require_enabled)

    def _resolve(self, family, entry, require_enabled):
        selected = self.registry.resolve(family, entry, require_enabled=require_enabled)
        if selected is None:
            if require_enabled:
                self.manager.require_enabled(family)
            return self.manager
        if family == 'codex':
            raise ValueError('Codex 独立配置使用已有 Codex 会话接口')
        supported = next(row for row in self.capabilities(family) if row['family'] == family)
        if not supported['supported']:
            raise ValueError(supported['reason'])
        with self.lock:
            child = self.children.get(entry)
            if child is None:
                from bridge.clients.manager import DesktopSessions
                child = DesktopSessions(selected.state_root, gateway_running=self.manager.gateway_running,
                                        instance=selected, instance_owner=self)
                self.children[entry] = child
                try:
                    child.scan(scan_wsl=False)
                except Exception:
                    self.children.pop(entry, None)
                    child.close()
                    raise
            child.instance = selected
            return child

    def descriptor(self, child, family):
        selected = self.registry.resolve(family, child.instance.id, require_enabled=False)
        child.instance = selected
        row = dict(self.manager.config.get('discovered', {}).get(family, {}))
        if not row:
            from bridge.clients.discovery import discover_clients
            row = discover_clients()[family]
        active_home = _claude_mode_home(selected.profile) if family == 'claude' else selected.profile.home
        return {**row, 'id': family, 'dataDirectory': str(active_home),
            'dataDirectoryExplicit': True, 'nativeProfile': selected.profile,
            'profileLaunchReceipt': selected.state_root/'native-launch.json',
            'profileGuard': lambda: self.registry.resolve(family, selected.id, require_enabled=False),
            'profileInitialized': lambda: self.registry.pin_initialized_roots(selected.id)}

    def control(self, value):
        allowed = {'action', 'operation', 'family', 'kind', 'label', 'home', 'application', 'entry', 'confirmed'}
        if not isinstance(value, dict) or set(value)-allowed:
            raise ValueError('独立配置管理请求无效')
        operation = value.get('operation', 'list')
        family = value.get('family')
        if operation in ('list', 'scan'):
            if set(value)-{'action', 'operation', 'family', 'kind'}:
                raise ValueError('独立配置扫描请求无效')
            rows = self.registry.list(family)
            result = {'instances': [{**row, 'name': row['label']} for row in rows],
                      'capabilities': self.capabilities()}
            if operation == 'scan':
                candidates = []
                if family in (None, 'codex') and self.registry.codex is not None:
                    for row in self.registry.codex.local_profiles(scan=True).get('candidates', []):
                        candidates.append({**row, 'family': 'codex', 'kind': 'codex-local'})
                # Native profiles have no machine-wide index. Reuse only explicit
                # registrations; choosing a folder does not import any accounts.
                result['candidates'] = candidates
            return result
        if operation == 'remove':
            if set(value)-{'action', 'operation'} != {'family', 'entry', 'confirmed'} or value['confirmed'] is not True:
                raise ValueError('请确认移除此客户端配置')
            entry = value['entry']
            with self.manager.client_lock, self.lock:
                row = next((row for row in self.registry.list(family) if row['id'] == entry), None)
                if row is None:
                    raise ValueError('接入实例未登记')
                if row['enabled']:
                    raise ValueError('请先停用此客户端的手机接入，再移除配置')
                child = self.children.get(entry)
                lock = child.locks[family] if child else None
                if lock and not lock.acquire(timeout=3):
                    raise ValueError('此客户端正在处理操作，请稍后再移除配置')
                try:
                    with child._changing(family) if child else nullcontext():
                        if child:
                            child.assert_detachable()
                        if child:
                            self.children.pop(entry, None)
                            child.close()
                        self.registry.remove(family, entry)
                finally:
                    if lock:
                        lock.release()
                self.manager.client_cache = None
            return {'removed': entry, 'instances': self.registry.list(), 'capabilities': self.capabilities()}
        if operation == 'save' and family == 'zcode' and value.get('kind') == 'default-application':
            if set(value)-{'action', 'operation'} != {'family', 'kind', 'application'}:
                raise ValueError('客户端保存请求无效')
            return self.manager.register_zcode_application(value['application'])
        if operation != 'save' or set(value)-{'action', 'operation'} != {'family', 'kind', 'label', 'home'}:
            raise ValueError('独立配置保存请求无效')
        capability = next((row for row in self.capabilities() if row['family'] == family), None)
        if not capability or not capability['supported']:
            raise ValueError((capability or {}).get('reason', '当前平台不支持此独立接入'))
        row = self.registry.register({key: value[key] for key in ('family', 'kind', 'label', 'home')})
        self.manager.client_cache = None
        return {'instance': {**row, 'name': row['label']}, 'entry': row['id'],
                'instances': self.registry.list(), 'capabilities': self.capabilities()}

    def close(self):
        with self.lock:
            children = list(self.children.values())
            self.children.clear()
        for child in children:
            child.close()


def _claude_mode_home(profile):
    mode = profile.roots['userData']/'claude_desktop_config.json'
    if mode.is_file():
        if mode.is_symlink() or mode.stat().st_size > 1024*1024:
            raise ValueError('Claude 模式配置不可读')
        settings = json.loads(mode.read_text(encoding='utf-8-sig'))
        if not isinstance(settings, dict):
            raise ValueError('Claude 模式配置不可读')
        if settings.get('deploymentMode') == '3p':
            return profile.roots['thirdParty']
    return profile.roots['userData']


def scan_profile(manager):
    """Bind installed code to explicit data roots without launching or logging in."""
    family = manager.instance.family
    row = manager._descriptor(family)
    public = {key: value for key, value in row.items() if not key.startswith('profile') and key != 'nativeProfile'}
    manager.config['discovered'] = {family: public}
    if row.get('installed'):
        adapter = manager.adapters[family]
        if family == 'claude':
            adapter.configure(row['executable'], row['dataDirectory'], explicit_home=True)
        else:
            manager.config['deepseekHome'] = row['dataDirectory']
            installed = adapter.discover_existing()
            if installed.get('reused'):
                manager.config['deepseekAdapterDirectory'] = str(adapter.directory)
    replace_session_file(manager.config_path, json.dumps(manager.config).encode())
    manager.client_cache = None
    return manager.clients(refresh=True)


def prepare_deepseek_profile(profile, descriptor=None):
    """Seed only the connector slot; native first launch owns all other setup."""
    if profile.family != 'deepseek':
        raise ValueError('此配置不属于 DSH')
    directory = profile.home/'profiles/desktop'
    for path in (profile.home/'profiles', directory, directory/'cordis.patch.yml',
                 directory/'package.json', directory/'pnpm-workspace.yaml'):
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('DSH 独立配置中的插件目录不能转向其他位置')
    if descriptor is not None:
        from bridge.clients.deepseek.profile import prepare
        if prepare(profile, descriptor):
            return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory/'cordis.patch.yml'
    try:
        with target.open('x', encoding='utf-8') as stream:
            stream.write('# Bridge native profile connector\n')
        target.chmod(0o600)
    except FileExistsError:
        pass


def require_deepseek_account(manager, action, sid, body):
    """Never let a Bridge write borrow credentials from DSH's login shell."""
    adapter = manager.adapters['deepseek']
    if action == 'respond' and body.get('decision') in ('decline', 'deny') and 'answers' not in body:
        requests = adapter.call('detail', sid, {}).get('session', {}).get('requests', [])
        if any(isinstance(row, dict) and row.get('id') == body.get('requestId') and row.get('needsInput') is False
               for row in requests):
            return  # A real approval rejection cannot start a model turn.
    generates = action in ('send', 'compact', 'edit-message', 'respond') or (
        action == 'goal' and body.get('operation') not in ('pause', 'clear'))
    selection = action == 'settings' and any(key in body for key in ('model', 'effort'))
    creation = action == 'create' and any(body.get(key) for key in ('model', 'firstMessage', 'text'))
    if not (generates or selection or creation):
        return
    account = adapter.call('account', sid)
    sources = account.get('sources', [])
    if type(account.get('credentialSourcesVersion')) is not int or account['credentialSourcesVersion'] != 1:
        raise ValueError('此独立 DSH 连接尚未提供账号来源，请重新准备该独立配置的连接后重试')
    if not isinstance(sources, list) or not sources:
        raise ValueError('请先在此独立 DSH 配置中登录账号或应用 API 接入')
    # send does not apply body.model. Trust only the actual native selection for
    # generation, so a forged request cannot substitute an allowed route here.
    model = body.get('model') if selection or creation else None
    provider = ((account.get('current') or {}).get('id') if model is None else
                model.split('/', 1)[0] if isinstance(model, str) and '/' in model else None)
    source = next((row for row in sources if isinstance(row, dict) and row.get('id') == provider), {})
    if source.get('credentialSource') != 'file':
        raise ValueError('所选模型未对应此独立配置中保存的账号，请先选择该账号的模型')
