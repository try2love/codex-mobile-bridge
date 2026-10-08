"""Private Harness account snapshots; the manager owns native idle/quit/restart.

Harness account and API-key routes coexist. A restore replaces only the selected
route's credential, never another provider, the device identity, or session routes.
"""
import base64
import copy
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..accounts import private_bytes, private_json
from ..tls import client_context
from .deepseek_setup import _mapping, _owned_file, _scalar

GRANT_KEY = 'deepseek-account-platform/default'
API_REF = 'DEEPSEEK_API_KEY'
ORIGIN = 'https://platform.deepseek.com'
SLOTS = {'official': ('records', GRANT_KEY), 'api': ('refs', API_REF)}
PROVIDERS = {'official': 'deepseek-account', 'api': 'deepseek-official'}


def _read(path):
    if path.is_symlink():
        raise ValueError('Harness 账号文件不能是符号链接')
    if not path.exists():
        return None
    try:
        _owned_file(path, private=True)
        return path.read_bytes()
    except (OSError, ValueError):
        raise ValueError('Harness 账号文件不可读或权限不正确') from None


def _document(raw):
    try:
        value = _mapping(raw.decode('utf-8')) if raw else {}
        if not value:
            return {'version': 1, 'refs': {}, 'records': {}}
        if not isinstance(value, dict) or value.get('version') != 1 or set(value)-{'version', 'refs', 'records'}:
            raise ValueError()
        if any(not isinstance(value.get(key, {}), dict) for key in ('refs', 'records')):
            raise ValueError()
        if any(not isinstance(v, str) or not v for v in value.get('refs', {}).values()):
            raise ValueError()
        return value
    except (UnicodeError, ValueError, TypeError, AttributeError):
        raise ValueError('Harness 凭据格式无法识别，尚未修改账号') from None


def _credential(document, kind):
    section, key = SLOTS[kind]
    value = document.get(section, {}).get(key)
    if value is None:
        return None
    if kind == 'api':
        if isinstance(value, str) and value.strip():
            return value
    elif isinstance(value, dict) and value.get('kind') == 'grant':
        payload = value.get('payload')
        if (isinstance(payload, dict) and payload.get('version') == 1
                and payload.get('issuer') == ORIGIN and isinstance(payload.get('token'), str)
                and re.fullmatch(r'[\x21-\x7e]+', payload['token'])):
            return copy.deepcopy(value)
    raise ValueError('Harness 当前账号凭据无效，请在客户端重新登录')


def _fingerprint(kind, value):
    return hashlib.sha256(json.dumps([kind, value], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _yaml_lines(key, value, indent):
    prefix = ' '*indent+json.dumps(key, ensure_ascii=False)+':'
    if isinstance(value, dict):
        result = [prefix+'\n']
        for child, item in value.items():
            result.extend(_yaml_lines(child, item, indent+2))
        return result
    return [prefix+' '+json.dumps(value, ensure_ascii=False)+'\n']


def _replace(raw, kind, value):
    """Preserve unrelated YAML bytes instead of round-tripping their scalars."""
    document = _document(raw)
    section, key = SLOTS[kind]
    if not raw or raw.lstrip().startswith(b'{'):
        document.setdefault(section, {})[key] = value
        return (json.dumps(document, ensure_ascii=False, indent=2)+'\n').encode()
    lines = raw.decode('utf-8').splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if re.fullmatch(section+r':[ \t]*\r?\n?', line)]
    replacement = _yaml_lines(key, value, 2)
    if not starts:
        if section in document:
            raise ValueError('Harness 凭据格式无法识别，尚未修改账号')
        return (''.join(lines).rstrip('\r\n')+'\n'+section+':\n'+''.join(replacement)).encode()
    start = starts[0]+1
    end = next((i for i in range(start, len(lines)) if re.match(r'^[^\s#]', lines[i])), len(lines))
    entries = []
    for i in range(start, end):
        match = re.match(r'^  ([^\s:#][^:]*):', lines[i])
        if match:
            entries.append((i, _scalar(match[1])))
    target = next((n for n, (_, name) in enumerate(entries) if name == key), None)
    if target is None:
        lines[end:end] = replacement
    else:
        first = entries[target][0]
        last = entries[target+1][0] if target+1 < len(entries) else end
        # Leave trailing comments and blank lines with their following entry.
        while last > first+1 and (not lines[last-1].strip() or lines[last-1].lstrip().startswith('#')):
            last -= 1
        lines[first:last] = replacement
    output = ''.join(lines).encode()
    if _credential(_document(output), kind) != value:
        raise ValueError('Harness 凭据写入验证失败，尚未修改账号')
    return output


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(path, token):
    """Only fixed account endpoints receive a grant; never follow redirects."""
    if path not in ('/auth-api/v0/users/current', '/api/v0/users/get_user_summary'):
        raise ValueError('Harness 账号查询不受支持')
    request = urllib.request.Request(ORIGIN+path, headers={'x-dsh-auth-token': token, 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=client_context())).open(request, timeout=15) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        value = json.loads(raw)
        if value.get('code') == 40003:
            raise PermissionError()
        data = value.get('data')
        if value.get('code') != 0 or not isinstance(data, dict) or data.get('biz_code') != 0:
            raise ValueError()
        return data.get('biz_data')
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValueError('Harness 登录已失效，请在客户端重新登录') from None
        raise ValueError('Harness 余额查询失败，请稍后重试') from None
    except PermissionError:
        raise ValueError('Harness 登录已失效，请在客户端重新登录') from None
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError('Harness 余额查询失败，请稍后重试') from None


def _balance(value):
    if not isinstance(value, dict):
        raise ValueError('Harness 余额数据无法识别')
    wallets = {}
    for source, target in (('normal_wallets', 'toppedUp'), ('bonus_wallets', 'granted')):
        rows = value.get(source)
        if not isinstance(rows, list):
            raise ValueError('Harness 余额数据无法识别')
        for row in rows:
            try:
                if not isinstance(row, dict) or row.get('currency') not in ('CNY', 'USD') or not isinstance(row.get('balance'), str):
                    raise ValueError()
                amount = Decimal(row['balance'])
                if not amount.is_finite() or abs(amount.adjusted()) > 100:
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise ValueError('Harness 余额数据无法识别') from None
            item = wallets.setdefault(row['currency'], {'toppedUp': Decimal(0), 'granted': Decimal(0)})
            item[target] += amount
    return {'wallets': [{'currency': currency, 'remaining': str(values['toppedUp']+values['granted']),
                         **{key: str(amount) for key, amount in values.items()}}
                        for currency, values in wallets.items()]}


def _public_usage(value):
    usage = copy.deepcopy(value)
    checked = usage.get('checkedAt')
    if isinstance(checked, str):
        try:
            parsed = datetime.fromisoformat(checked.replace('Z', '+00:00'))
            usage['checkedAt'] = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError, OverflowError):
            usage.pop('checkedAt', None)
    return usage


class DeepSeekAccounts:
    def __init__(self, directory, data_home):
        self.directory = Path(directory).expanduser()
        self.home = Path(data_home).expanduser()
        if self.directory.is_symlink():
            raise ValueError('Harness 账号目录不能是符号链接')
        self.path = self.home/'.credentials.yaml'
        self.lock = threading.RLock()
        raw = _read(self.directory/'index.json')
        self.index = json.loads(raw) if raw else {'accounts': [], 'activeId': None}
        self.cache = {}

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        private_json(self.directory/'index.json', self.index)

    def _row(self, identifier):
        if identifier in ('current-official', 'current-api'):
            raise ValueError('Harness 当前接入尚未保存，请先在桌面添加账号')
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise ValueError('Harness 账号标识无效')
        row = next((row for row in self.index['accounts'] if row['id'] == identifier), None)
        if row is None:
            raise ValueError('Harness 账号不存在，请刷新列表')
        return row

    def _snapshot(self, row):
        raw = _read(self.directory/(row['id']+'.json'))
        try:
            saved = json.loads(raw)
            kind = saved['kind']
            section, key = SLOTS[kind]
            value = _credential({section: {key: saved['credential']}}, kind)
            if kind != row['kind'] or value is None or _fingerprint(kind, value) != row['fingerprint']:
                raise ValueError()
            return value
        except (ValueError, TypeError, KeyError):
            raise ValueError('Harness 已保存账号无效，请重新添加') from None

    def public(self):
        with self.lock:
            matches, routes, issue = [], [], None
            rows = [{**row, 'saved': True} for row in self.index['accounts']]
            try:
                document = _document(_read(self.path))
                for kind in SLOTS:
                    value = _credential(document, kind)
                    if value is not None:
                        routes.append(PROVIDERS[kind])
                        if kind != 'api' or not os.environ.get(API_REF):
                            stamp = _fingerprint(kind, value)
                            found = [row['id'] for row in rows if row['kind'] == kind and row['fingerprint'] == stamp]
                            if not found:
                                row = self._current_row(kind, stamp)
                                rows.append(row)
                                found.append(row['id'])
                            matches.extend(found)
            except ValueError as exc:
                issue = str(exc)
            active = self.index.get('activeId')
            if active not in matches:
                active = next(iter(matches), None)
            accounts = []
            for row in rows:
                entry = {key: row[key] for key in ('id', 'name', 'kind', 'provider', 'email', 'accountId', 'saved') if key in row}
                cached = self.cache.get(row['id'])
                if cached and cached[2] == row['fingerprint']:
                    entry['usage'] = _public_usage(cached[1])
                    entry.update(cached[3])
                accounts.append(entry)
            accounts.sort(key=lambda row: row['id'] not in matches)
            current = {'id': active, 'routes': routes}
            if issue:
                current['error'] = issue
            return {'accounts': accounts, 'activeId': active, 'activeIds': matches, 'current': current}

    def _current_row(self, kind, stamp):
        return {'id': 'current-'+kind, 'kind': kind, 'provider': PROVIDERS[kind], 'saved': False,
                'name': 'DeepSeek Harness' if kind == 'official' else 'DeepSeek API', 'fingerprint': stamp}

    def import_current(self, name='', kind=''):
        with self.lock:
            document = _document(_read(self.path))
            if not kind:
                kind = 'official' if _credential(document, 'official') is not None else 'api'
            if kind not in SLOTS:
                raise ValueError('Harness 接入类型无效')
            value = _credential(document, kind)
            if value is None:
                raise ValueError('未找到 Harness 当前接入，请先在客户端登录或配置 API')
            if kind == 'api' and os.environ.get(API_REF):
                raise ValueError('Harness API 由启动环境指定，无法切换本地凭据')
            stamp = _fingerprint(kind, value)
            row = next((item for item in self.index['accounts'] if item['kind'] == kind and item['fingerprint'] == stamp), None)
            if not isinstance(name, str) or len(name.strip()) > 100:
                raise ValueError('Harness 账号名称无效')
            if row is None:
                row = {'id': uuid.uuid4().hex, 'name': name.strip() or ('DeepSeek Harness' if kind == 'official' else 'DeepSeek API'),
                       'kind': kind, 'provider': PROVIDERS[kind], 'fingerprint': stamp}
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.directory.chmod(0o700)
                private_json(self.directory/(row['id']+'.json'), {'kind': kind, 'credential': value})
                self.index['accounts'].append(row)
            elif name.strip():
                row['name'] = name.strip()
            self.index['activeId'] = row['id']
            self._save()
            return self.public()

    def details(self, identifier, refresh=False):
        with self.lock:
            value = None
            if identifier in ('current-official', 'current-api'):
                kind = identifier.removeprefix('current-')
                value = _credential(_document(_read(self.path)), kind)
                if value is None:
                    raise ValueError('未找到 Harness 当前接入，请先在客户端登录或配置 API')
                row = self._current_row(kind, _fingerprint(kind, value))
            else:
                row = self._row(identifier)
            cached = self.cache.get(identifier)
            if not refresh and cached and cached[2] == row['fingerprint'] and time.monotonic()-cached[0] < 300:
                return self.public()
            usage = {'status': 'unsupported', 'checkedAt': time.time(), 'limits': []}
            identity = {}
            if row['kind'] == 'official':
                try:
                    token = (value or self._snapshot(row))['payload']['token']
                    usage.update(status='ready', balance=_balance(_request('/api/v0/users/get_user_summary', token)))
                    # Profile failure must not discard an independently valid balance.
                    try:
                        profile = _request('/auth-api/v0/users/current', token)
                        if isinstance(profile, dict):
                            for source, target in (('email', 'email'), ('id', 'accountId')):
                                if isinstance(profile.get(source), str) and len(profile[source]) <= 320:
                                    identity[target] = profile[source]
                            if row.get('saved') is not False:
                                row.update(identity)
                                self._save()
                    except ValueError:
                        pass
                except ValueError as exc:
                    usage.update(status='error', error=str(exc))
            # API routes may target custom origins; never send those keys to Platform.
            self.cache[identifier] = (time.monotonic(), usage, row['fingerprint'], identity)
            return self.public()

    @contextmanager
    def _writer(self):
        """Use the same exclusive sibling lock as native credentials-local."""
        if not self.home.is_dir():
            raise ValueError('未找到 Harness 数据目录')
        lock_path = self.path.with_name(self.path.name+'.lock')
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise ValueError('Harness 正在写入账号，请稍后重试') from None
        try:
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(str(os.getpid())+'\n')
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    def restore(self, identifier):
        """Called only after the manager proves idle and fully quits the desktop."""
        with self.lock, self._writer():
            row = self._row(identifier)
            if row['kind'] == 'api' and os.environ.get(API_REF):
                raise ValueError('Harness API 由启动环境指定，无法切换本地凭据')
            value = self._snapshot(row)
            before = _read(self.path)
            after = _replace(before, row['kind'], value)
            transaction = uuid.uuid4().hex
            saved = {'before': base64.b64encode(before).decode() if before is not None else None,
                     'expected': hashlib.sha256(after).hexdigest(), 'activeId': self.index.get('activeId')}
            rollback_path = self.directory/('rollback-'+transaction+'.json')
            private_json(rollback_path, saved)
            try:
                private_bytes(self.path, after)
                self.index['activeId'] = identifier
                self._save()
            except Exception:
                self.index['activeId'] = saved['activeId']
                if before is None:
                    self.path.unlink(missing_ok=True)
                else:
                    private_bytes(self.path, before)
                rollback_path.unlink(missing_ok=True)
                raise
            return transaction

    def _rollback_path(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
            raise ValueError('Harness 账号恢复标识无效')
        return self.directory/('rollback-'+token+'.json')

    def rollback(self, token):
        with self.lock, self._writer():
            path = self._rollback_path(token)
            raw = _read(path)
            if raw is None:
                raise ValueError('Harness 账号恢复记录不存在')
            saved = json.loads(raw)
            current = _read(self.path)
            if current is None or hashlib.sha256(current).hexdigest() != saved['expected']:
                raise ValueError('Harness 账号已被外部修改，请在桌面检查后恢复')
            if saved['before'] is None:
                self.path.unlink()
            else:
                private_bytes(self.path, base64.b64decode(saved['before'], validate=True))
            self.index['activeId'] = saved['activeId']
            self._save()
            path.unlink()

    def commit(self, token):
        with self.lock:
            self._rollback_path(token).unlink(missing_ok=True)
