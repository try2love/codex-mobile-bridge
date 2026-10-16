"""Read-only per-account metadata and actual local connection identification."""
import hashlib
import threading
import time

from bridge.features.accounts.account import Account, AccountError
from bridge.features.notifications.channels import read_json

CURRENT_LOGIN_ID = 'current-login'


class AccountInfo:
    def __init__(self, manager):
        self.manager = manager
        self.identity = None
        self.stamp = None
        self.probing = False
        self.checked_at = 0
        self.entries = {}

    def source_stamp(self):
        home = self.manager.home
        return tuple(hashlib.sha256((home/name).read_bytes()).hexdigest() if (home/name).is_file() else None
                     for name in ('config.toml', 'auth.json'))

    def read_identity(self):
        from bridge.features.accounts.accounts import ManagedRPC, token_owner
        manager = self.manager
        with ManagedRPC(manager.home, manager.runtime) as rpc:
            config = rpc.request('config/read', {'includeLayers': False}).get('config', {})
            profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
            effective = {**config, **profile}
            provider = effective.get('model_provider') or 'openai'
            definition = (effective.get('model_providers') or {}).get(provider) or {}
            custom = provider != 'openai' or effective.get('openai_base_url') or any(definition.get(k) for k in
                ('base_url','env_key','experimental_bearer_token','http_headers','env_http_headers','auth','gateway_oauth'))
            auth = read_json(manager.home/'auth.json', {})
            try:
                native_auth = rpc.request('account/read', {'refreshToken': False})
                login = Account.login_context(rpc, auth=native_auth)
            except AccountError:
                if not custom:
                    raise
                login = None
            login = {'context': login, 'owner': token_owner(auth)} if login else None
            if not custom:
                account = native_auth.get('account') or {}
                if account.get('type') == 'chatgpt':
                    return {'kind':'chatgpt', 'name':account.get('email') or 'ChatGPT',
                            'owner': token_owner(auth) if config.get('cli_auth_credentials_store') != 'keyring' else None,
                            'login': login}
                if account.get('type') != 'apiKey':
                    return {'kind':'signedOut', 'name':''}
            import os
            key = (os.environ.get(definition['env_key'], '') if definition.get('env_key')
                   else definition.get('experimental_bearer_token') or '')
            if not key and not definition.get('env_key') and (provider == 'openai' or definition.get('requires_openai_auth')):
                key = auth.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY', '')
            return {'kind':'api', 'name':'API · '+provider, 'baseUrl':(definition.get('base_url') or (effective.get('openai_base_url') if provider == 'openai' else None) or 'https://api.openai.com/v1').rstrip('/'),
                    'key':key, 'model':effective.get('model'), 'login': login}

    def current_login(self):
        try:
            stamp = self.source_stamp()
        except OSError:
            return None
        if stamp != self.stamp or not self.identity or not self.identity.get('login'):
            return None
        login = self.identity['login']
        context = login['context']
        saved = self.match({'kind': 'chatgpt', 'owner': login['owner']})
        if saved:
            return {**{k: saved[k] for k in ('id','name','kind','email','planType') if k in saved},
                    'email': context.get('email'), 'planType': context.get('planType'),
                    'accountKey': context['accountKey'],
                    'details': self.entries.get(saved['id'], {})}
        return {'id': CURRENT_LOGIN_ID, 'kind': 'chatgpt', 'name': 'ChatGPT',
                'email': context.get('email'), 'planType': context.get('planType'),
                'accountKey': context['accountKey'], 'transient': True,
                'details': {k: v for k, v in self.entries.get(CURRENT_LOGIN_ID, {}).items()
                            if v.get('accountKey') == context['accountKey']}}

    def match(self, identity):
        if not identity:
            return None
        matches = []
        manager = self.manager
        for row in manager.index['accounts']:
            if row['kind'] != identity['kind']:
                continue
            if row['kind'] == 'chatgpt' and identity.get('owner') and row.get('tokenOwner') == identity['owner']:
                matches.append(row)
            elif (row['kind'] == 'api' and identity.get('key') and row['baseUrl'] == identity.get('baseUrl')
                  and read_json(manager.directory(row['id'])/'api.json', {}).get('key') == identity['key']):
                matches.append(row)
        return next((r for r in matches if r.get('model') == identity.get('model')), matches[0] if matches else None)

    def current(self):
        manager = self.manager
        try:
            stamp = self.source_stamp()
        except OSError:
            return {'status':'error'}
        if not self.probing and (stamp != self.stamp or time.time()-self.checked_at > 60):
            # No native calls under the status lock; failed/missing runtimes remain readable.
            if manager.runtime and manager.runtime.is_file():
                self.probing = True
                threading.Thread(target=self.probe, daemon=True).start()
        if stamp == self.stamp and self.identity:
            row = self.match(self.identity)
            return {'status':'ready', 'kind':self.identity['kind'], 'id':row['id'] if row else None,
                    'name':row['name'] if row else self.identity['name']}
        if manager.active_matches():
            row = manager.row(manager.index['activeId'])
            return {'status':'ready', 'kind':row['kind'], 'id':row['id'], 'name':row['name']}
        return {'status':'checking' if self.probing else 'error'}

    def probe(self):
        try:
            with self.manager.bridge.account.lock:
                before = self.source_stamp()
                identity = self.read_identity()
                with self.manager.lock:
                    if before == self.source_stamp():
                        self.identity, self.stamp = identity, before
        except Exception:
            with self.manager.lock:
                self.identity, self.stamp = None, self.source_stamp()
        finally:
            with self.manager.lock:
                self.checked_at = time.time()
                self.probing = False

    def request(self, value):
        manager = self.manager
        section = value.get('section')
        if section not in ('usage','models') or not isinstance(value.get('refresh', False), bool):
            raise ValueError('账号详情请求无效')
        with manager.lock:
            manager.check_ready()
            row = self.current_login() if value.get('id') == CURRENT_LOGIN_ID else manager.row(value.get('id'))
            if not row or row['id'] != value.get('id'):
                raise ValueError('账号身份已变化，请刷新列表')
            row = dict(row)
            if row.get('transient') and section != 'usage':
                raise ValueError('请在聊天模型设置中查看当前接入的模型')
            if section == 'usage' and row['kind'] != 'chatgpt':
                raise ValueError('API 接入不提供官方额度')
            info = self.entries.setdefault(row['id'], {})
            cached = info.get(section) or {}
            if row.get('transient') and cached.get('accountKey') != row['accountKey']:
                cached = {}
            ttl = 0 if value.get('refresh') else 300
            if cached.get('status') == 'loading' or time.time()-cached.get('checkedAt', 0) < ttl:
                return manager.public()
            info[section] = {**cached, 'status':'loading', 'error':None}
            if row.get('transient'):
                info[section]['accountKey'] = row['accountKey']
            threading.Thread(target=self.read, args=(row,section,value.get('refresh',False),info[section]), daemon=True).start()
            return manager.public()

    def read(self, row, section, refresh=False, pending=None):
        with self.manager.lock:
            if pending is None:
                pending = self.entries.get(row['id'], {}).get(section)
        if row.get('transient'):
            try:
                result = self.manager.account({'id': CURRENT_LOGIN_ID, 'operation': 'read', 'refresh': refresh})
                if not result.get('visible') or result.get('accountKey') != row['accountKey']:
                    raise ValueError('账号身份已变化')
                result.update(status='error' if result.get('error') else 'ready', checkedAt=time.time())
            except Exception:
                result = {'status': 'error', 'checkedAt': time.time(), 'accountKey': row['accountKey'],
                          'error': '额度暂不可用，请检查登录后重试'}
            with self.manager.lock:
                info = self.entries.setdefault(CURRENT_LOGIN_ID, {})
                # A replaced slot belongs to a newer query, even for the same login.
                if info.get(section) is not pending:
                    return
                current = self.current_login()
                if current and current.get('accountKey') == row['accountKey']:
                    if result['status'] == 'error':
                        result = {**info.get(section, {}), **result}
                    info[section] = result
                else:
                    info.pop(section, None)
            return
        from bridge.features.accounts.accounts import ManagedRPC, token_owner, private_bytes
        manager = self.manager
        result = {}
        try:
            if row['kind'] == 'api':
                result = {'models':manager.api_models(row, refresh)}
            else:
                with manager.bridge.account.lock:
                    with manager.lock:
                        manager.check_ready()
                        manager.row(row['id'])
                    identity = self.read_identity()
                    active = self.match(identity)
                    login = identity.get('login')
                    native_login = bool(section == 'usage' and login and login.get('owner') and
                                        login['owner'] == row.get('tokenOwner'))
                    home = manager.home if native_login or active and active['id'] == row['id'] else manager.directory(row['id'])
                    if home != manager.home:
                        private_bytes(home/'config.toml', b'cli_auth_credentials_store = "file"\nmodel_provider = "openai"\n')
                    if token_owner(read_json(home/'auth.json', {})) != row.get('tokenOwner'):
                        raise ValueError('账号身份不匹配')
                    with ManagedRPC(home, manager.runtime) as rpc:
                        context = Account.login_context(rpc) if native_login else Account.context(rpc)
                        if (not context or context.get('loginType') != 'chatgpt' or context.get('email') != row.get('email') or
                                native_login and context['accountKey'] != login['context']['accountKey']):
                            raise ValueError('账号身份不匹配')
                        if section == 'usage':
                            result = manager.bridge.account.limits(rpc, context, refresh=refresh)
                            result.update(planType=context.get('planType'),
                                          subscription=manager.bridge.account.subscription(home, context, refresh=refresh))
                        else:
                            models, cursor, seen = [], None, set()
                            while True:
                                page = rpc.request('model/list', {'includeHidden':False,'limit':100,'cursor':cursor})
                                for model in page.get('data', []):
                                    identifier = model.get('model') or model.get('id')
                                    if isinstance(identifier, str) and not model.get('hidden'):
                                        models.append({'id':identifier,'name':model.get('displayName') or identifier})
                                cursor = page.get('nextCursor')
                                if not cursor:
                                    break
                                if cursor in seen or len(seen) >= 20:
                                    raise ValueError('模型目录分页未完成')
                                seen.add(cursor)
                            result = {'models':list({m['id']:m for m in models}.values())}
                    if token_owner(read_json(home/'auth.json', {})) != row.get('tokenOwner'):
                        raise ValueError('账号身份已变化')
                    if home == manager.home:
                        private_bytes(manager.directory(row['id'])/'auth.json', (home/'auth.json').read_bytes())
            result.update(status='ready',checkedAt=time.time())
        except Exception:
            result = {'status':'error','checkedAt':time.time(),
                      'error':'额度暂不可用，请检查登录后重试' if section == 'usage' else '模型列表暂不可用，请检查接入后重试'}
        with manager.lock:
            if self.entries.get(row['id'], {}).get(section) is not pending:
                return
            if any(r['id'] == row['id'] for r in manager.index['accounts']):
                if result['status'] == 'error':
                    result = {**self.entries.get(row['id'], {}).get(section, {}), **result}
                self.entries.setdefault(row['id'], {})[section] = result
