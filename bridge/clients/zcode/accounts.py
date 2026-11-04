"""Machine-local login snapshots and private API drafts; lifecycle transactions live in manager."""
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

from bridge.features.accounts.accounts import Accounts, private_json
from . import quota, api_config

SETTINGS = ('providerFamilyDomain', 'providerFamilyDomainUpdatedAt', 'providerFamilyDomainMigrated',
            'providerFamilyConnectionSelections', 'modelProviderFamilyModes')


def read(path):
    if path.is_symlink():
        raise ValueError('ZCode 账号文件不能是符号链接')
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('ZCode 账号文件格式无效')
    return value


def account_key(key):
    return key.startswith('oauth:') or key == 'zcodejwttoken'


def stamp(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ZCodeAccounts:
    def __init__(self, directory, home, metadata):
        self.directory, self.home, self.metadata = Path(directory), Path(home), metadata
        if self.home.is_symlink():
            raise ValueError('ZCode 数据目录不能是符号链接')
        self.lock = threading.RLock()
        self.quota_cache = None

    def index(self):
        return read(self.directory/'index.json').get('accounts', [])

    def snapshot(self):
        credentials = {k: v for k, v in read(self.home/'credentials.json').items() if account_key(k)}
        if not credentials or any(not isinstance(v, str) or not v.startswith('enc:v1:') for v in credentials.values()):
            raise ValueError('请先在 ZCode 登录账号')
        settings = read(self.home/'setting.json')
        return {'credentials': credentials, 'settings': {k: settings[k] for k in SETTINGS if k in settings}}

    def _row(self, identifier):
        row = next((r for r in self.index() if r['id'] == identifier), None)
        if not row:
            raise ValueError('保存的 ZCode 账号不存在')
        return row

    def public(self):
        return self._public()

    def _public(self, quota_for=None, refresh=False):
        with self.lock:
            rows = self.index()
            generation = quota.login_generation(self.home)
            try:
                current = self.metadata()
            except (OSError, ValueError):
                current = {}
            active = [r['id'] for r in rows if r['kind'] == 'official' and current.get('identity') and r['identity'] == current['identity']]
            # While stopped inside the transaction, validate restored encrypted bytes.
            if rows and not current.get('identity'):
                try:
                    fingerprint = stamp(self.snapshot())
                    active = [r['id'] for r in rows if r['kind'] == 'official' and r['fingerprint'] == fingerprint]
                except (OSError, ValueError):
                    pass
            official_active = bool(active)
            try:
                providers = api_config.rules(read(self.home/api_config.FILE))
                for row in rows:
                    if row['kind'] == 'api':
                        saved = self._saved(row)['provider']
                        native = next((item for item in providers if item['providerId'] == saved['providerId']), None)
                        if api_config.matches(saved, native):
                            active.append(row['id'])
            except (OSError, ValueError):
                pass  # Saved API drafts remain editable before the original App is initialized.
            public = [{k: r[k] for k in ('id', 'name', 'kind', 'email', 'saved')} for r in rows]
            if generation and current.get('provider') and current['provider'] != generation[0]:
                generation = None
            if (current.get('identity') or generation) and not official_active:
                public.append({'id': 'current', 'name': current.get('name') or 'ZCode', 'email': current.get('email', ''), 'kind': 'official', 'saved': False})
                active.insert(0, 'current')
            if quota_for is not None and quota_for not in active:
                raise ValueError('仅支持查看当前 ZCode 登录账号的额度')
            if self.quota_cache and self.quota_cache[0] != generation:
                self.quota_cache = None
            for row in public:
                row['canReadQuota'] = bool(row['kind'] == 'official' and generation and row['id'] in active)
                if row['canReadQuota']:
                    now = time.time()
                    if self.quota_cache and any(now >= window['validUntil']+1 for limit in self.quota_cache[2]['limits']
                                                for window in limit['windows']):
                        self.quota_cache = None
                    if quota_for is not None and (refresh or not self.quota_cache or now-self.quota_cache[1] >= 30):
                        usage = quota.latest(self.home, generation, now)
                        self.quota_cache = (generation, now, usage)
                    if self.quota_cache:
                        row['usage'] = self.quota_cache[2]
            if generation and quota.login_generation(self.home) != generation:
                self.quota_cache = None
                raise ValueError('ZCode 登录正在变化，请稍后重试')
            return {'accounts': public, 'activeIds': active, 'activeId': next(iter(active), None),
                    'current': {'name': current.get('name')}, 'canImport': bool(current.get('identity')),
                    'capabilities': {'addApi': True, 'quota': bool(generation), 'apiFormats': list(api_config.API_FORMATS),
                                     'switchRequiresStopped': True},
                    'enrollmentHint': '官方账号请先在 ZCode 登录，再保存当前接入。API 可在此保存；停用手机接入并正常退出原 App 后应用，重新启动后在聊天中选择对应模型。'}

    def import_current(self, name=''):
        with self.lock:
            before = self.snapshot()
            current = self.metadata()
            if not re.fullmatch(r'[0-9a-f]{64}', current.get('identity', '')) or before != self.snapshot():
                raise ValueError('ZCode 登录正在变化，请稍后重试')
            rows = self.index()
            row = next((r for r in rows if r['kind'] == 'official' and r['identity'] == current['identity']), None)
            if row is None:
                row = {'id': str(uuid.uuid4()), 'identity': current['identity'], 'kind': 'official', 'saved': True}
                rows.append(row)
            row.update(name=str(name or current['name'])[:100], email=current.get('email', ''), fingerprint=stamp(before))
            private_json(self.directory/(row['id']+'.json'), before)
            private_json(self.directory/'index.json', {'accounts': rows})
            return self.public()

    def edit(self, identifier):
        row = self._row(identifier)
        result = {k: row[k] for k in ('id', 'name', 'kind')}
        if row['kind'] == 'api':
            config = self._saved(row)['provider']['config']
            result['api'] = {'baseUrl': config['api']['baseUrl'], 'apiFormat': config['api']['type'],
                             'models': config['personalModelIds'], 'hasKey': bool(config['access']['apiKey'])}
        return result

    def _saved(self, row):
        selected = read(self.directory/(row['id']+'.json'))
        if stamp(selected) != row['fingerprint']:
            raise ValueError('保存的 ZCode 接入快照已变化')
        return selected

    def rename(self, identifier, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('名称长度应为 1–100 字')
        with self.lock:
            rows = self.index(); self._row(identifier)
            for row in rows:
                if row['id'] == identifier:
                    row['name'] = name.strip()
            private_json(self.directory/'index.json', {'accounts': rows})
            return self.public()

    def remove(self, identifier):
        with self.lock:
            self._row(identifier)
            private_json(self.directory/'index.json', {'accounts': [r for r in self.index() if r['id'] != identifier]})
            (self.directory/(identifier+'.json')).unlink(missing_ok=True)
            return self.public()

    def details(self, identifier, refresh=False):
        if not identifier:
            raise ValueError('请指定 ZCode 账号')
        return self._public(identifier, refresh)

    def save_api(self, value):
        name = value.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('名称长度应为 1–100 字')
        with self.lock:
            rows = self.index()
            existing = self._row(value['id']) if value.get('id') else None
            if existing and existing['kind'] != 'api':
                raise ValueError('官方登录只能在 ZCode 中修改')
            identifier = existing['id'] if existing else str(uuid.uuid4())
            previous = self._saved(existing) if existing else None
            url = Accounts.api_url(value.get('baseUrl', '').strip() if isinstance(value.get('baseUrl'), str) else '')
            saved = {'kind': 'api', 'provider': api_config.provider(value, identifier, name.strip(),
                                                                  url,
                                                                  previous['provider'] if previous else None)}
            row = {'id': identifier, 'name': name.strip(), 'kind': 'api', 'saved': True,
                   'email': '', 'fingerprint': stamp(saved)}
            path = self.directory/(identifier+'.json')
            try:
                private_json(path, saved)
                private_json(self.directory/'index.json', {'accounts': [item for item in rows if item['id'] != identifier]+[row]})
            except Exception:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    private_json(path, previous)
                raise
            return self.public()

    def restore(self, identifier):
        with self.lock:
            row = self._row(identifier)
            selected = self._saved(row)
            if row['kind'] == 'api':
                # The manager only reaches restore after the original App and
                # phone access are stopped. Do not compete with native writers.
                native = read(self.home/api_config.FILE)
                before = {api_config.FILE: native}
                after = {api_config.FILE: api_config.apply(native, selected['provider'])}
                return self._restore_files(before, after)
            before = {name: read(self.home/name) for name in ('credentials.json', 'setting.json')}
            after = {name: dict(value) for name, value in before.items()}
            # Drop account-derived project keys, retaining independent API/plugin credentials.
            after['credentials.json'] = {k: v for k, v in before['credentials.json'].items()
                                         if not account_key(k) and not k.startswith('project-key-location:')}
            after['credentials.json'].update(selected['credentials'])
            for key in SETTINGS:
                after['setting.json'].pop(key, None)
            after['setting.json'].update(selected['settings'])
            return self._restore_files(before, after)

    def _restore_files(self, before, after):
        token = uuid.uuid4().hex
        private_json(self.directory/('rollback-'+token+'.json'), {'before': before, 'after': after})
        try:
            for name, value in after.items():
                private_json(self.home/name, value)
        except Exception:
            for name, value in before.items():
                private_json(self.home/name, value)
            raise
        return token

    def _rollback_path(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
            raise ValueError('ZCode 账号恢复标识无效')
        return self.directory/('rollback-'+token+'.json')

    def rollback(self, token):
        with self.lock:
            path = self._rollback_path(token); backup = read(path)
            if any(read(self.home/name) != expected for name, expected in backup['after'].items()):
                raise ValueError('ZCode 配置已被外部修改；恢复备份已保留')
            for name, value in backup['before'].items():
                private_json(self.home/name, value)
            path.unlink()

    def commit(self, token):
        self._rollback_path(token).unlink(missing_ok=True)
