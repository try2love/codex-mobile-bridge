"""Desktop-only enrollment and shared, transactional account switching.

Credentials use Codex's native file store in owner-only directories. They never
appear in public status, HTTP responses, command arguments or diagnostic logs.
"""
import base64
import copy
import functools
import hashlib
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from bridge.features.accounts.account import AccountRPC, AccountError, Account
from bridge.clients.desktop_app import DesktopApp
from bridge.features.accounts.models import model_ids
from bridge.features.accounts.info import AccountInfo
from bridge.clients.codex.transport import connect_stream
from bridge.features.notifications.channels import read_json


def private_bytes(path, content):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('账号文件不能是符号链接')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.account-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def private_json(path, value):
    private_bytes(path, json.dumps(value, ensure_ascii=False).encode())


def token_owner(auth):
    tokens = auth.get('tokens') or {}
    try:
        encoded = tokens['id_token'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        # These are local native-login records, not independent token validation.
        return [tokens.get('account_id'), claims.get('sub'), claims.get('email')]
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def operation(method):
    """One gate for native mutations, background queue sends and activation."""
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        manager = getattr(self, 'accounts', None)
        if manager is None:
            return method(self, *args, **kwargs)
        with manager.gate:
            guard = getattr(manager, 'require_client_enabled', None)
            if guard is not None:
                guard()
            manager.check_ready()
            return method(self, *args, **kwargs)
    return call


class ManagedRPC(AccountRPC):
    METHODS = AccountRPC.METHODS | {'account/login/start', 'account/login/cancel', 'config/batchWrite', 'model/list'}

    def __init__(self, *args):
        super().__init__(*args)
        self.notifications = []

    def request(self, method, params=None):
        if method not in self.METHODS:
            raise ValueError('不支持的账号操作')
        self.counter += 1
        self.process.stdin.write(json.dumps({'id': self.counter, 'method': method, 'params': params})+'\n')
        self.process.stdin.flush()
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                value = self.messages.get(timeout=max(.01, end-time.monotonic()))
            except queue.Empty:
                break
            if value is None:
                break
            if value.get('id') == self.counter:
                if 'error' in value:
                    raise AccountError('运行时拒绝账号或配置操作，请检查登录及工作区限制')
                return value.get('result', {})
            self.notifications.append(value)
        raise AccountError('账号服务超时或已断开')

    def login_finished(self, cancel):
        end = time.monotonic() + 600
        while time.monotonic() < end and not cancel.is_set():
            if self.notifications:
                value = self.notifications.pop(0)
            else:
                try:
                    value = self.messages.get(timeout=.25)
                except queue.Empty:
                    continue
            if value is None:
                break
            if value.get('method') == 'account/login/completed':
                return value.get('params', {}).get('success') is True
        return False


class Accounts:
    def __init__(self, bridge):
        self.bridge = bridge
        self.home = bridge.codex_home
        self.root = bridge.data_dir/'accounts'
        if self.root.is_symlink():
            raise ValueError('账号目录不能是符号链接')
        self.lock = threading.RLock()
        self.gate = threading.RLock()
        self.index = read_json(self.root/'index.json', {'accounts': [], 'activeId': None, 'desktopExecutable': ''})
        self.state = read_json(self.root/'switch.json', {'phase': 'idle'})
        if self.state.get('phase') in ('preparing', 'stopping', 'applying', 'migrating', 'starting', 'verifying', 'restoring'):
            self.state.update(phase='interrupted', error='上次切换中断，请在桌面端恢复原接入')
        self.enrollment = None
        self.discovery = None
        self.candidates = {}
        self.info = AccountInfo(self)
        from bridge.features.updates.codex import DesktopUpdates
        from bridge.features.accounts.monitor import AccountMonitor
        self.updates = DesktopUpdates(self)
        self.monitor = AccountMonitor(self)
        self.login_cancel = threading.Event()
        self.thread = None

    @property
    def runtime(self):
        return self.bridge.catalog_reader.executable

    def directory(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise ValueError('账号标识无效')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        return self.root/identifier

    def row(self, identifier):
        self.directory(identifier)
        row = next((r for r in self.index['accounts'] if r['id'] == identifier), None)
        if row is None:
            raise ValueError('账号不存在，请刷新列表')
        return row

    def save(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_json(self.root/'index.json', self.index)

    def phase(self, phase, **values):
        with self.lock:
            self.state.update(phase=phase, **values)
            private_json(self.root/'switch.json', self.state)

    def check_ready(self):
        if self.updates.status()['state'] == 'requesting':
            raise ValueError('正在交接桌面更新，请稍后再操作')
        if self.state.get('phase') not in ('idle', 'complete', 'failed', 'restored'):
            raise ValueError('账号正在切换或等待恢复，请稍后再操作聊天')

    def active_matches(self):
        stamp = self.index.get('activeStamp')
        if not stamp:
            return False
        try:
            config_hash = hashlib.sha256((self.home/'config.toml').read_bytes()).hexdigest()
            if config_hash != stamp['config']:
                return False
            row = self.row(self.index['activeId'])
            if row['kind'] == 'chatgpt':
                tokens = read_json(self.home/'auth.json', {}).get('tokens') or {}
                return token_owner({'tokens': tokens}) == row.get('tokenOwner') and bool(row.get('tokenOwner'))
            auth = self.home/'auth.json'
            if 'auth' in stamp:
                return stamp['auth'] == (hashlib.sha256(auth.read_bytes()).hexdigest() if auth.is_file() else None)
            return read_json(auth, {}).get('OPENAI_API_KEY') == read_json(self.directory(row['id'])/'api.json', {}).get('key')
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def mark_active(self, identifier):
        self.index['activeId'] = identifier
        self.index['activeStamp'] = ({'config': hashlib.sha256((self.home/'config.toml').read_bytes()).hexdigest()}
                                     if identifier else None)
        if identifier and self.row(identifier)['kind'] == 'api':
            auth = self.home/'auth.json'
            self.index['activeStamp']['auth'] = hashlib.sha256(auth.read_bytes()).hexdigest() if auth.is_file() else None
        self.save()

    def public(self):
        with self.lock:
            current = self.info.current()
            verified = bool(current.get('id'))
            return {'accounts': [{**{k: row[k] for k in ('id', 'name', 'kind', 'email', 'planType', 'baseUrl', 'model') if k in row},
                                  'details': copy.deepcopy(self.info.entries.get(row['id'], {}))}
                                 for row in self.index['accounts']],
                    'activeId': current.get('id'), 'activeVerified': verified, 'current': current, 'blockers': self.blockers(),
                    'externalChange': bool(self.index.get('activeId')) and not verified, 'switch': dict(self.state),
                    'reminders':dict(self.monitor.preferences), 'reminderError':self.monitor.last_error,
                    'desktopUpdate':self.updates.status()}

    def desktop_status(self):
        result = self.public()
        with self.lock:
            result.update(desktopExecutable=self.index.get('desktopExecutable') or DesktopApp.discover(self.runtime),
                          enrollment=self.enrollment, discovery=self.discovery, codexHome=str(self.home))
        return result

    @staticmethod
    def name(value):
        name = value.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('请填写 1–80 字的账号名称')
        return name.strip()

    def assert_editable(self):
        self.check_ready()
        if self.enrollment and self.enrollment.get('phase') in ('starting', 'waiting'):
            raise ValueError('请先完成或取消正在进行的登录')

    @staticmethod
    def api_url(url):
        if not isinstance(url, str):
            raise ValueError('请填写有效 API 地址')
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError:
            raise ValueError('请填写有效 API 地址') from None
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or len(url) > 2000 or any(c.isspace() for c in url)
                or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
            raise ValueError('API 地址须为 HTTPS，只有本机服务可使用 HTTP')
        return url.rstrip('/')

    def api_credentials(self, value):
        url, key = self.api_url(value.get('baseUrl', '')), value.get('apiKey', '')
        if key == '' and value.get('id'):
            old = self.row(value['id'])
            if old['kind'] == 'api':
                key = read_json(self.directory(old['id'])/'api.json', {}).get('key', '')
        if not isinstance(key, str) or not 1 <= len(key) <= 8192 or any(c in key for c in '\r\n\x00'):
            raise ValueError('请填写有效 API Key')
        return url, key

    @staticmethod
    def source_stamp(source):
        return [hashlib.sha256((source/name).read_bytes()).hexdigest() if (source/name).is_file() else None
                for name in ('config.toml', 'auth.json')]

    def scan(self, value):
        with self.gate, self.bridge.account.lock, self.lock:
            self.assert_editable()
            path = value.get('source') or str(self.home)
            if not isinstance(path, str) or not Path(path).expanduser().is_absolute():
                raise ValueError('请填写 Codex 配置目录的完整路径')
            source = Path(path).expanduser().resolve()
            if not source.is_dir() or not any((source/name).is_file() for name in ('config.toml', 'auth.json')):
                raise ValueError('目录中没有找到 config.toml 或 auth.json')
            stamp = self.source_stamp(source)
            with ManagedRPC(source, self.runtime) as rpc:
                config = rpc.request('config/read', {'includeLayers': False}).get('config', {})
            auth = read_json(source/'auth.json', {})
            if stamp != self.source_stamp(source):
                raise ValueError('本机配置已变化，请重新扫描')
            candidates = []
            tokens, owner = auth.get('tokens') or {}, token_owner(auth)
            if owner and owner[1] and owner[2] and all(tokens.get(k) for k in ('access_token', 'refresh_token', 'account_id')):
                candidates.append({'kind': 'chatgpt', 'name': owner[2][:80], 'email': owner[2], 'tokenOwner': owner,
                                   'accountId': tokens['account_id'],
                                   'auth': {'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None, 'tokens': tokens,
                                            **({'last_refresh': auth['last_refresh']} if auth.get('last_refresh') else {})}})
            definitions = dict(config.get('model_providers') or {})
            if auth.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY'):
                definitions.setdefault('openai', {'name': 'OpenAI API', 'base_url': config.get('openai_base_url') or 'https://api.openai.com/v1', 'requires_openai_auth': True})
            for provider, definition in definitions.items():
                if not isinstance(definition, dict):
                    continue
                # Native defaults without a configured endpoint are not saved API connections.
                url = definition.get('base_url') or ((config.get('openai_base_url') or 'https://api.openai.com/v1') if provider == 'openai' else '')
                if not url:
                    continue
                key = (os.environ.get(definition['env_key'], '') if definition.get('env_key')
                       else definition.get('experimental_bearer_token') or '')
                if not key and not definition.get('env_key') and (provider == 'openai' or definition.get('requires_openai_auth')):
                    key = auth.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY', '')
                reason = None
                if any(definition.get(k) for k in ('http_headers', 'env_http_headers', 'auth', 'aws', 'query_params', 'gateway_oauth')):
                    reason = '暂不支持导入含额外请求配置的提供商'
                elif definition.get('wire_api', 'responses') != 'responses':
                    reason = '此提供商不是 Responses API，暂不支持导入'
                else:
                    try:
                        self.api_credentials({'baseUrl': url, 'apiKey': key})
                    except ValueError:
                        reason = '未找到可用 API Key 或地址，请检查环境变量或手动添加'
                variants = [(provider, config.get('model') or '')]
                variants += [(provider+' / '+name, profile.get('model') or config.get('model') or '')
                             for name, profile in (config.get('profiles') or {}).items()
                             if isinstance(profile, dict) and (profile.get('model_provider') or config.get('model_provider') or 'openai') == provider]
                for name, model in variants:
                    candidates.append({'kind': 'api', 'name': name[:80], 'baseUrl': url.rstrip('/'),
                                       'model': model, 'key': key, 'reason': reason})
            self.candidates = {}
            for candidate in candidates:
                candidate.update(id=uuid.uuid4().hex, source=source, stamp=stamp)
                self.candidates[candidate['id']] = candidate
            self.discovery = {'source': str(source), 'candidates': [],
                              'notice': ('系统凭据库中的官方登录不能直接复制，请使用官方登录添加'
                                         if not owner and config.get('cli_auth_credentials_store') in ('keyring', 'auto') else None)}
            self.update_discovery()
            return self.desktop_status()

    def duplicate(self, candidate):
        for row in self.index['accounts']:
            if row['kind'] != candidate['kind']:
                continue
            if row['kind'] == 'chatgpt' and row.get('tokenOwner') == candidate.get('tokenOwner'):
                return True
            if (row['kind'] == 'api' and row['baseUrl'] == candidate['baseUrl'] and row['model'] == candidate.get('model')
                    and read_json(self.directory(row['id'])/'api.json', {}).get('key') == candidate.get('key')):
                return True
        return False

    def update_discovery(self):
        if self.discovery is not None:
            self.discovery['candidates'] = [{**{k: row[k] for k in ('id', 'kind', 'name', 'email', 'baseUrl', 'model', 'reason') if k in row},
                                             'canImport': not row.get('reason'), 'imported': self.duplicate(row)}
                                            for row in self.candidates.values()]

    def candidate(self, identifier):
        row = self.candidates.get(identifier)
        if not row or row.get('reason'):
            raise ValueError('请重新扫描并选择可导入的配置')
        if row['stamp'] != self.source_stamp(row['source']):
            raise ValueError('本机配置已变化，请重新扫描')
        return row

    def import_account(self, value):
        with self.lock:
            self.assert_editable()
            candidate = dict(self.candidate(value.get('candidateId')))
            if value.get('name') is not None:
                candidate['name'] = self.name(value)
            if candidate['kind'] == 'api':
                candidate['model'] = value.get('model', candidate['model'])
            if not self.duplicate(candidate):
                if candidate['kind'] == 'api':
                    self.add_api({'name': candidate['name'], 'baseUrl': candidate['baseUrl'],
                                  'apiKey': candidate['key'], 'model': candidate['model']})
                else:
                    identifier = uuid.uuid4().hex
                    private_json(self.directory(identifier)/'auth.json', candidate['auth'])
                    self.index['accounts'].append({'id': identifier, **{k: candidate[k] for k in
                                                  ('kind', 'name', 'email', 'accountId', 'tokenOwner')}})
                    self.save()
            self.candidates[candidate['id']].update(model=candidate.get('model', ''))
            self.update_discovery()
            return self.desktop_status()

    def api_models(self, row, refresh=False):
        cached = self.info.entries.get(row['id'], {}).get('models') or {}
        if not refresh and cached.get('status') == 'ready' and time.time()-cached.get('checkedAt', 0) < 300:
            return copy.deepcopy(cached['models'])
        key = read_json(self.directory(row['id'])/'api.json', {})['key']
        models = [{'id': identifier, 'name': identifier, 'efforts': []} for identifier in model_ids(row['baseUrl'], key)]
        self.info.entries.setdefault(row['id'], {})['models'] = {'models':copy.deepcopy(models), 'status':'ready', 'checkedAt':time.time()}
        return models

    def current_models(self, refresh=False):
        with self.lock:
            self.check_ready()
            if not self.active_matches():
                return None
            row = dict(self.row(self.index['activeId']))
            if row['kind'] != 'api':
                result = self.bridge.catalog_reader.get_kind('models', str(self.home), refresh=refresh, provider='openai')
            else:
                result = {'models':[], 'modelSource':'api', 'fastMode':{'allowed':False}}
                try:
                    result['models'] = self.api_models(row, refresh)
                except ValueError as error:
                    result['modelError'] = str(error)
            return {**result, 'modelAccessName':row['name'], 'modelAccountId':row['id']}

    def models(self, value):
        with self.lock:
            self.assert_editable()
            if value.get('candidateId'):
                candidate = self.candidate(value['candidateId'])
                if candidate['kind'] != 'api':
                    raise ValueError('请选择 API 接入')
                url, key = self.api_credentials({'baseUrl': candidate['baseUrl'], 'apiKey': candidate['key']})
            else:
                url, key = self.api_credentials(value)
        # Only the explicit desktop button calls upstream; never poll models automatically.
        models = model_ids(url, key)
        return {**self.desktop_status(), 'models': models}

    def add_api(self, value):
        with self.lock:
            self.assert_editable()
            name = self.name(value)
            url, key, model = value.get('baseUrl', ''), value.get('apiKey', ''), value.get('model', '')
            url, key = self.api_credentials(value)
            if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,199}', model):
                raise ValueError('请填写有效模型 ID')
            existing = self.row(value['id']) if value.get('id') else None
            if existing and (existing['kind'] != 'api' or existing['id'] == self.info.current().get('id')):
                raise ValueError('请先切换到其他接入，再修改此 API')
            identifier = existing['id'] if existing else uuid.uuid4().hex
            row = {'id': identifier, 'kind': 'api', 'name': name, 'baseUrl': url.rstrip('/'), 'model': model}
            private_json(self.directory(identifier)/'api.json', {'key': key})
            if existing:
                existing.update(row)
            else:
                self.index['accounts'].append(row)
            self.info.entries.pop(identifier, None)
            self.save()
            return self.desktop_status()

    def login(self, value):
        with self.lock:
            self.assert_editable()
            name = self.name(value)
            replacing = self.row(value['replaceId']) if value.get('replaceId') else None
            if replacing and (replacing['kind'] != 'chatgpt' or replacing['id'] == self.info.current().get('id')):
                raise ValueError('请先切换到其他接入，再重新登录此账号')
            identifier = uuid.uuid4().hex
            folder = self.directory(identifier)
            folder.mkdir(parents=True, mode=0o700)
            private_bytes(folder/'config.toml', b'cli_auth_credentials_store = "file"\n')
            self.enrollment = {'phase': 'starting', 'id': identifier, 'name': name}
            self.login_cancel = threading.Event()
            def run():
                accepted = False
                try:
                    with ManagedRPC(folder, self.runtime) as rpc:
                        flow = rpc.request('account/login/start', {'type': 'chatgpt'})
                        url = flow.get('authUrl', '')
                        parsed = urlsplit(url)
                        if parsed.scheme != 'https' or parsed.hostname not in ('auth.openai.com', 'chatgpt.com', 'auth0.openai.com'):
                            raise ValueError('登录地址未通过验证')
                        with self.lock:
                            self.enrollment.update(phase='waiting', authUrl=url)
                        if not rpc.login_finished(self.login_cancel):
                            if flow.get('loginId'):
                                rpc.request('account/login/cancel', {'loginId': flow['loginId']})
                            raise ValueError('登录已取消或过期，请重新登录')
                        auth = rpc.request('account/read', {'refreshToken': False}).get('account') or {}
                        if auth.get('type') != 'chatgpt' or not (folder/'auth.json').is_file():
                            raise ValueError('尚未取得官方账号凭据')
                        saved_auth = read_json(folder/'auth.json', {})
                        tokens = saved_auth.get('tokens') or {}
                        owner = token_owner(saved_auth)
                        if not tokens.get('account_id') or not owner or not owner[1]:
                            raise ValueError('尚未取得账号身份，请重新登录')
                        with self.lock:
                            if self.login_cancel.is_set():
                                raise ValueError('登录已取消')
                            if any(r is not replacing and r.get('accountId') == tokens['account_id'] and r.get('email') == auth.get('email') for r in self.index['accounts']):
                                raise ValueError('此账号已添加，请先删除旧档案再重新授权')
                            self.index['accounts'].append({'id': identifier, 'name': name, 'kind': 'chatgpt',
                                                          'accountId': tokens['account_id'], 'tokenOwner': owner, 'email': auth.get('email'),
                                                          'planType': auth.get('planType')})
                            if replacing:
                                self.index['accounts'].remove(replacing)
                            self.save()
                            if replacing:
                                shutil.rmtree(self.directory(replacing['id']), ignore_errors=True)
                            accepted = True
                            self.enrollment = {'phase': 'complete', 'name': name}
                except Exception:
                    with self.lock:
                        self.enrollment = {'phase': 'failed', 'error': '登录未完成、已取消或账号已存在，请重新添加'}
                finally:
                    if not accepted:
                        shutil.rmtree(folder, ignore_errors=True)
            threading.Thread(target=run, daemon=True).start()
            return self.desktop_status()

    def account(self, value):
        """Operate one saved login without changing the desktop's active connection."""
        from bridge.features.accounts.account import Account
        action = value.get('operation', 'read')
        if action not in ('read', 'consume') or set(value) - {'action', 'id', 'operation', 'refresh', 'confirmed', 'requestId', 'accountKey', 'creditId'}:
            raise ValueError('账号重置请求包含不支持的字段')
        with self.gate, self.bridge.account.lock, self.lock:
            self.check_ready()
            row = dict(self.row(value.get('id')))
            if row['kind'] != 'chatgpt':
                raise ValueError('仅官方 ChatGPT 账号可使用重置卡')
            identity = self.info.read_identity()
            active = self.info.match(identity)
            home = self.home if active and active['id'] == row['id'] else self.directory(row['id'])
            if token_owner(read_json(home/'auth.json', {})) != row.get('tokenOwner'):
                raise ValueError('账号身份不匹配')
            if home != self.home:
                private_bytes(home/'config.toml', b'cli_auth_credentials_store = "file"\nmodel_provider = "openai"\n')
            target = Account(home, self.bridge.data_dir, executable=self.runtime)
            target.usage_cache = self.bridge.account.usage_cache
            target.subscription_cache = self.bridge.account.subscription_cache
            def checked_context(rpc):
                context = Account.context(rpc)
                if (context.get('loginType') != 'chatgpt' or context.get('email') != row.get('email') or
                        token_owner(read_json(home/'auth.json', {})) != row.get('tokenOwner')):
                    raise ValueError('账号身份已变化')
                return context
            target.context = checked_context
            result = target.control({**{k:v for k,v in value.items() if k not in ('action','id','operation')}, 'action':action})
            if action == 'consume':
                self.info.entries.get(row['id'], {}).pop('usage', None)
            return result

    def control(self, value):
        action = value.get('action', 'list')
        if action == 'account':
            return self.account(value)
        if action == 'reminders':
            self.monitor.configure(value.get('preferences'))
            return self.desktop_status()
        if action == 'checkDesktopUpdate':
            self.updates.check()
            return self.desktop_status()
        if action == 'requestDesktopUpdate':
            self.updates.request({k:v for k,v in value.items() if k != 'action'})
            return self.desktop_status()
        if action == 'ignoreSubmission':
            return self.ignore_submission(value)
        if action == 'details':
            self.info.request(value)
            return self.desktop_status()
        if action == 'list':
            return self.desktop_status()
        if action in ('scan', 'import', 'models'):
            return {'scan': self.scan, 'import': self.import_account, 'models': self.models}[action](value)
        if action == 'addApi':
            return self.add_api(value)
        if action == 'login':
            return self.login(value)
        if action == 'cancelLogin':
            self.login_cancel.set()
            return self.desktop_status()
        if action == 'switch':
            return self.switch(value)
        if action == 'recover':
            return self.recover(value)
        with self.lock:
            self.assert_editable()
            if action == 'scanDesktop':
                self.index['desktopExecutable'] = DesktopApp.scan(self.runtime, self.home)
            elif action == 'configure':
                executable = value.get('desktopExecutable')
                if not isinstance(executable, str) or not Path(executable).is_absolute():
                    raise ValueError('请填写桌面程序的完整路径')
                app = DesktopApp(executable, self.home)
                app.validate(self.runtime)
                self.index['desktopExecutable'] = str(app.executable)
            elif action in ('rename', 'delete'):
                row = self.row(value.get('id'))
                if action == 'rename':
                    row['name'] = self.name(value)
                else:
                    if row['id'] == self.info.current().get('id'):
                        raise ValueError('请先切换到其他接入，再删除此账号')
                    self.index['accounts'].remove(row)
                    self.save()
                    shutil.rmtree(self.directory(row['id']), ignore_errors=True)
            else:
                raise ValueError('不支持的账号管理操作')
            self.save()
            return self.desktop_status()

    def blockers(self):
        result = []
        for bridge in [self.bridge, *self.bridge.remote_bridges.values()]:
            with bridge.lock:
                sessions = list(bridge.live.items())
            for identifier, session in sessions:
                view = session.view()
                # Saved/disconnected snapshots are history, not evidence of a live task.
                if view.get('connected') and (view.get('requests') or view.get('status') in ('active', 'running', 'waiting', 'busy')):
                    result.append({'id': identifier, 'host': getattr(bridge, 'host', 'local'),
                                   'title': view.get('title') or identifier,
                                   'reason': 'approval' if view.get('requests') else 'running'})
            for key, row in list(bridge.submissions.items()):
                if row.get('status') in ('queued', 'unknown'):
                    result.append({'id': key.split(':')[0], 'host': getattr(bridge, 'host', 'local'),
                                   'title': row.get('title') or key.split(':')[0], 'reason': row['status'],
                                   'submissionId': key.split(':')[-1]})
        return result

    def ignore_submission(self, value):
        with self.gate:
            self.check_ready()
            host = value.get('host', 'local')
            bridge = self.bridge if host == 'local' else self.bridge.for_host(host)
            bridge.ignore_submission(value.get('threadId', ''), value.get('submissionId', ''))
            return self.public()

    def idle(self):
        blocked = self.blockers()
        if blocked:
            names = '、'.join(row['title'] for row in blocked[:3])
            raise ValueError('请先结束任务或处理待发送消息：'+names)

    def switch(self, value):
        if value.get('confirmed') is not True or value.get('tasksConfirmed') is not True:
            raise ValueError('请确认所有桌面任务已结束，并同意重启 Codex 桌面应用')
        request_id = value.get('requestId')
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError('切换请求标识无效')
        with self.gate, self.lock:
            row = dict(self.row(value.get('id')))
            ledger = read_json(self.root/'requests.json', {})
            if request_id in ledger:
                if ledger[request_id] != row['id']:
                    raise ValueError('同一请求不能切换到不同账号')
                return self.public()
            self.assert_editable()
            if self.info.current().get('id') == row['id']:
                return self.public()
            self.idle()
            executable = self.index.get('desktopExecutable') or DesktopApp.discover(self.runtime)
            if not executable:
                raise ValueError('请先在桌面端指定 Codex 桌面程序路径')
            app = DesktopApp(executable, self.home)
            app.validate(self.runtime)
            ledger[request_id] = row['id']
            private_json(self.root/'requests.json', ledger)
            self.state = {'phase': 'preparing', 'requestId': request_id, 'targetId': row['id'], 'error': None}
            self.phase('preparing')
            self.thread = threading.Thread(target=self._switch, args=(row, app), daemon=True)
            self.thread.start()
            return self.public()

    def snapshot_files(self):
        result = {}
        for name in ('config.toml', 'auth.json'):
            file = self.home/name
            if file.is_symlink():
                raise ValueError('切换不支持符号链接形式的认证或配置文件')
            result[name] = base64.b64encode(file.read_bytes()).decode() if file.exists() else None
        return result

    def restore_files(self, snapshot):
        for name, data in snapshot.items():
            if name not in ('config.toml', 'auth.json'):
                raise ValueError('恢复文件无效')
            if data is None:
                (self.home/name).unlink(missing_ok=True)
            else:
                private_bytes(self.home/name, base64.b64decode(data))

    def prepare(self, row, before):
        folder = self.root/'prepared'
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(mode=0o700)
        if before['config.toml'] is not None:
            private_bytes(folder/'config.toml', base64.b64decode(before['config.toml']))
        changes = {'cli_auth_credentials_store': 'file', 'model_provider': 'openai', 'model_providers.bridge_api': None, 'openai_base_url': None}
        if row['kind'] == 'chatgpt':
            private_bytes(folder/'auth.json', (self.directory(row['id'])/'auth.json').read_bytes())
            changes['model_providers.openai'] = None
            # Saved chats may still use this ID. Keep an official-auth alias so
            # the desktop can resume them normally without migrating projects.
            changes['model_providers.bridge_api'] = {
                'name': 'OpenAI', 'wire_api': 'responses',
                'requires_openai_auth': True, 'supports_websockets': True}
        else:
            secret = read_json(self.directory(row['id'])/'api.json', {})['key']
            private_json(folder/'auth.json', {'auth_mode': 'apikey', 'OPENAI_API_KEY': secret})
            changes.update(model_provider='bridge_api', model=row['model'], service_tier=None)
            provider = {'name': 'Bridge API', 'base_url': row['baseUrl'], 'wire_api': 'responses',
                        'requires_openai_auth': True, 'supports_websockets': False}
            changes['model_providers.bridge_api'] = provider
            # The desktop may explicitly select openai, including when resuming an
            # existing chat. Both provider IDs must use the selected API endpoint.
            changes['model_providers.openai'] = None
            changes['openai_base_url'] = row['baseUrl']
        with ManagedRPC(folder, self.runtime) as rpc:
            config = rpc.request('config/read', {'includeLayers': False}).get('config', {})
            if config.get('forced_login_method'):
                changes['forced_login_method'] = 'chatgpt' if row['kind'] == 'chatgpt' else 'api'
            if config.get('profile'):
                raise ValueError('当前配置启用了 profile，请先在桌面切回默认配置')
            if row['kind'] == 'chatgpt':
                # Let the official runtime select its default model.
                changes['model'] = None
            result = rpc.request('config/batchWrite', {'edits': [{'keyPath': key, 'value': val, 'mergeStrategy': 'replace'}
                                                                for key, val in changes.items()]})
            if result.get('status') != 'ok':
                raise ValueError('接入配置被其他配置层覆盖，无法安全切换')
        if row['kind'] == 'chatgpt':
            with ManagedRPC(folder, self.runtime) as rpc:
                auth = rpc.request('account/read', {'refreshToken': False}).get('account') or {}
                if auth.get('type') != 'chatgpt' or auth.get('email') != row.get('email'):
                    raise ValueError('保存的账号需要重新登录')
        return folder

    def wait_stopped(self):
        end = time.monotonic()+8
        while time.monotonic() < end:
            try:
                stream = connect_stream(self.bridge.ipc.path, timeout=.5)
            except OSError:
                return
            stream.close()
            time.sleep(.25)
        raise ValueError('仍有桌面实例占用连接，请在电脑端关闭后重试')

    def wait_ready(self, app, row=None):
        end = time.monotonic()+35
        while time.monotonic() < end:
            try:
                if not app.processes():
                    time.sleep(.5)
                    continue
                self.bridge.ipc.connect()
                if row:
                    with ManagedRPC(self.home, self.runtime) as rpc:
                        config = rpc.request('config/read', {'includeLayers': False}).get('config', {})
                        provider = config.get('model_provider') or 'openai'
                        if provider != ('openai' if row['kind'] == 'chatgpt' else 'bridge_api'):
                            raise ValueError('模型提供商未生效')
                        if row['kind'] == 'chatgpt':
                            auth = rpc.request('account/read', {'refreshToken': False}).get('account') or {}
                            tokens = read_json(self.home/'auth.json', {}).get('tokens') or {}
                            if auth.get('email') != row.get('email') or token_owner({'tokens': tokens}) != row.get('tokenOwner'):
                                raise ValueError('账号身份未生效')
                        else:
                            provider_config = (config.get('model_providers') or {}).get('bridge_api') or {}
                            auth = rpc.request('account/read', {'refreshToken': False}).get('account') or {}
                            if (auth.get('type') != 'apiKey' or provider_config.get('base_url') != row['baseUrl']
                                    or config.get('openai_base_url') != row['baseUrl']
                                    or config.get('model') != row['model']):
                                raise ValueError('API 接入配置未生效')
                return
            except Exception:
                time.sleep(.5)
        raise ValueError('未能确认桌面连接和目标账号，请检查电脑端')

    def invalidate(self):
        for bridge in [self.bridge, *self.bridge.remote_bridges.values()]:
            if getattr(bridge, 'side_chats', None):
                bridge.side_chats.close()
            bridge.ipc.close()
            bridge._disconnected()
            if hasattr(bridge.catalog_reader, 'invalidate_models'):
                bridge.catalog_reader.invalidate_models()
            elif hasattr(bridge.catalog_reader, 'cache'):
                bridge.catalog_reader.cache.clear()

    def retain_current(self, snapshot):
        current_id = self.index.get('activeId')
        if current_id and self.row(current_id)['kind'] == 'chatgpt' and snapshot['auth.json']:
            auth = json.loads(base64.b64decode(snapshot['auth.json']))
            if token_owner(auth) and token_owner(auth) == self.row(current_id).get('tokenOwner'):
                private_bytes(self.directory(current_id)/'auth.json', base64.b64decode(snapshot['auth.json']))

    def _switch(self, row, app):
        with self.bridge.account.lock:
            self._apply_switch(row, app)

    def _apply_switch(self, row, app):
        before = None
        stopped = False
        try:
            before = self.snapshot_files()
            self.retain_current(before)
            folder = self.prepare(row, before)
            backup = {'files': before, 'activeId': self.index.get('activeId'), 'desktopExecutable': str(app.executable)}
            private_json(self.root/'rollback.json', backup)
            self.phase('stopping')
            self.invalidate()
            app.stop(provider='codex')
            stopped = True
            # The GUI may refresh tokens or persist settings while exiting.
            after_stop = self.snapshot_files()
            changed = after_stop != before
            if changed:
                before = after_stop
                backup['files'] = before
                private_json(self.root/'rollback.json', backup)
                self.retain_current(before)
            self.wait_stopped()
            if changed:
                folder = self.prepare(row, before)
            self.phase('applying')
            private_bytes(self.home/'config.toml', (folder/'config.toml').read_bytes())
            if (folder/'auth.json').is_file():
                private_bytes(self.home/'auth.json', (folder/'auth.json').read_bytes())
            else:
                (self.home/'auth.json').unlink(missing_ok=True)
            self.phase('starting')
            app.start()
            self.phase('verifying')
            self.wait_ready(app, row)
            with self.lock:
                self.mark_active(row['id'])
                self.phase('complete')
            (self.root/'rollback.json').unlink(missing_ok=True)
        except Exception:
            if stopped and before is not None:
                try:
                    self.phase('restoring')
                    app.stop(provider='codex')
                    self.restore_files(before)
                    self.invalidate()
                    app.start()
                    self.wait_ready(app)
                    with self.lock:
                        self.mark_active(backup['activeId'])
                    self.phase('restored', error='切换未完成，已恢复原接入；请检查桌面程序或重新登录')
                    (self.root/'rollback.json').unlink(missing_ok=True)
                except Exception:
                    self.phase('interrupted', error='自动恢复未完成，请在桌面端恢复原接入')
            else:
                message = ('桌面程序未正常退出，请检查系统权限或手动关闭后重试' if self.state.get('phase') == 'stopping'
                           else '切换未执行，请检查账号登录、配置限制和桌面程序路径')
                self.phase('failed', error=message)
        finally:
            shutil.rmtree(self.root/'prepared', ignore_errors=True)

    def recover(self, value):
        if value.get('confirmed') is not True:
            raise ValueError('请确认恢复原接入并重启桌面应用')
        with self.gate, self.lock:
            if self.state.get('phase') != 'interrupted':
                raise ValueError('当前不需要恢复')
            backup = read_json(self.root/'rollback.json', None)
            if not backup:
                self.phase('failed', error='未修改接入，请重新发起切换')
                return self.desktop_status()
            self.phase('restoring')
            def run():
                with self.bridge.account.lock:
                    try:
                        app = DesktopApp(backup['desktopExecutable'], self.home)
                        app.validate(self.runtime)
                        app.stop(provider='codex')
                        self.restore_files(backup['files'])
                        if backup.get('threadsApplying'):
                            # Only older previews wrote thread migrations to this
                            # recovery record. New switches never load projects.
                            from bridge.features.sessions.access import ThreadAccess
                            ThreadAccess(self.bridge).restore(backup['threads'])
                        self.invalidate()
                        app.start()
                        self.wait_ready(app)
                        with self.lock:
                            self.mark_active(backup['activeId'])
                            self.phase('restored', error=None)
                        (self.root/'rollback.json').unlink(missing_ok=True)
                    except Exception:
                        self.phase('interrupted', error='恢复未完成，请检查桌面程序路径和运行状态')
            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()
            return self.desktop_status()
