"""Private, same-machine Claude Desktop login snapshots.

The manager owns the lifecycle gate: import/restore/rollback run only after
verified native exit. Chromium's cookies and LevelDB stores cannot be copied
as one consistent login while the application is writing them. Claude Code
credentials and local Code/Cowork conversation directories are never touched.
"""
import base64
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from urllib import error, parse, request

from bridge.features.accounts.accounts import private_bytes, private_json
from bridge.features.auth.tls import client_context
from bridge.platforms.windows.claude import windows_claude_profile_paths

AUTH_ITEMS = ('Local State', 'Preferences', 'Cookies', 'Cookies-journal', 'Cookies-wal', 'Cookies-shm', 'Network',
              'DIPS', 'DIPS-wal', 'DIPS-shm', 'SharedStorage', 'SharedStorage-wal', 'SharedStorage-shm', 'WebStorage',
              'Local Storage', 'IndexedDB', 'Session Storage', 'Service Worker', 'ant-did')
CONFIG = 'claude_desktop_config.json'
TOKEN_KEY = 'oauth:tokenCache'
TTL = 300
AMBIGUOUS_PROFILE = '检测到多个 Claude 登录目录，无法安全切换账号；请在设置中指定客户端数据目录'


def _safe(path):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Claude 账号目录不能是符号链接')


def _json(path, default=None):
    _safe(path)
    if not path.exists():
        return {} if default is None else default
    if path.is_symlink():
        raise ValueError('Claude 账号文件不能是符号链接')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Claude 账号配置格式不正确')
    return value


def _copy(source, target):
    """Only regular, owner-private files; never follow a profile symlink."""
    _safe(source)
    _safe(target)
    if source.is_symlink():
        raise ValueError('Claude 账号文件不能是符号链接')
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.chmod(0o700)
        for child in source.iterdir():
            _copy(child, target/child.name)
    elif source.is_file():
        private_bytes(target, source.read_bytes())
    else:
        raise ValueError('Claude 账号快照包含不支持的文件')


def _remove(path):
    _safe(path)
    if path.is_symlink():
        raise ValueError('Claude 账号文件不能是符号链接')
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _cookies(home):
    for name in ('Network/Cookies', 'Cookies'):
        path = home/name
        if path.is_file():
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
                raise ValueError('Claude 登录目录不能是符号链接')
            try:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
                    rows = db.execute("SELECT host_key,name,value,encrypted_value,expires_utc FROM cookies WHERE name IN ('sessionKey','lastActiveOrg')").fetchall()
                valid = [(host, name, value or '', bytes(encrypted or b''), expiry)
                         for host, name, value, encrypted, expiry in rows
                         if host in ('claude.ai', '.claude.ai') and (value or encrypted)]
                if {row[1] for row in valid} >= {'sessionKey', 'lastActiveOrg'}:
                    return valid
            except sqlite3.Error:
                raise ValueError('Claude 登录数据库暂不可读，请退出客户端后重试') from None
    raise ValueError('没有找到已登录的 Claude Desktop 账号，请先在客户端登录')


def _fingerprint(rows):
    parts = sorted((host, name, value, encrypted.hex()) for host, name, value, encrypted, _ in rows)
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _web_json(path, cookies, organization):
    # Credentials belong only to this fixed official origin. No redirection or
    # caller-provided URL can forward them to another host.
    req = request.Request('https://claude.ai'+path, headers={
        'Cookie': '; '.join(name+'='+value for name, value in cookies.items()),
        'Accept': 'application/json', 'Origin': 'https://claude.ai',
        'Referer': 'https://claude.ai/settings/usage', 'x-organization-uuid': organization})
    try:
        with request.build_opener(_NoRedirect(), request.HTTPSHandler(context=client_context())).open(req, timeout=15) as response:
            value = json.loads(response.read(2*1024*1024))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError('Claude 登录已失效或需要在客户端完成验证') from None
        raise ValueError('Claude 暂时无法查询剩余额度，请稍后重试') from None
    except Exception:
        raise ValueError('Claude 暂时无法查询剩余额度，请稍后重试') from None


def _decrypt_cookie(host, encrypted, home):
    # The original OS protects native cookies. Decrypted values remain only in
    # this process, never in snapshots, command arguments, public data or logs.
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if sys.platform == 'darwin':
        from keyring.backends.macOS import Keyring
        vault = Keyring()
        password = vault.get_password('Claude Safe Storage', 'Claude') or vault.get_password('Claude Safe Storage', 'Claude Key')
        if not password or not encrypted.startswith(b'v10'):
            raise ValueError('无法读取 Claude 登录凭据，请解锁系统钥匙串后重试')
        key = hashlib.pbkdf2_hmac('sha1', password.encode(), b'saltysalt', 1003, dklen=16)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(b' '*16)).decryptor()
        padded = decryptor.update(encrypted[3:])+decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded)+unpadder.finalize()
    elif sys.platform == 'win32':
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if encrypted.startswith(b'v10'):
            wrapped = base64.b64decode(_json(home/'Local State').get('os_crypt', {}).get('encrypted_key', ''), validate=True)
            if not wrapped.startswith(b'DPAPI'):
                raise ValueError('当前 Claude 登录加密方式暂不支持额度查询')
            plaintext = AESGCM(_unprotect(wrapped[5:])).decrypt(encrypted[3:15], encrypted[15:], None)
        elif encrypted.startswith((b'v11', b'v20')):
            raise ValueError('当前 Claude 登录加密方式暂不支持额度查询')
        else:
            plaintext = _unprotect(encrypted)
    else:
        raise ValueError('当前系统暂不支持 Claude 加密登录的额度查询')
    digest = hashlib.sha256(host.encode()).digest()
    if plaintext.startswith(digest):
        plaintext = plaintext[32:]
    return plaintext.decode('utf-8')


def _unprotect(value):
    """Windows DPAPI, bound to the same logged-in OS user as Claude."""
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte*len(value)).from_buffer_copy(value)
    source, target = Blob(len(value), buffer), Blob()
    crypt32, kernel32 = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes, kernel32.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('无法读取 Claude 登录凭据，请使用保存账号时的系统用户')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def _timestamp(value):
    if type(value) in (int, float) and math.isfinite(value):
        return value
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()


def _public_usage(value):
    """Upgrade older ISO caches at the API boundary without rewriting them."""
    result = copy.deepcopy(value)
    fields = [(result, 'checkedAt')]
    fields.extend((window, 'resetsAt') for limit in result.get('limits', []) for window in limit.get('windows', []))
    for owner, key in fields:
        if key not in owner:
            continue
        try:
            owner[key] = _timestamp(owner[key])
        except (TypeError, ValueError, AttributeError, OSError, OverflowError):
            owner.pop(key, None)
    return result


def _usage_limits(data):
    rows = []
    for limit in data.get('limits', []) if isinstance(data.get('limits'), list) else []:
        if not isinstance(limit, dict):
            continue
        group = limit.get('group') or limit.get('kind')
        if group not in ('session', 'weekly'):
            continue
        scope = limit.get('scope') or {}
        product = (scope.get('model') or scope.get('surface') or {}) if isinstance(scope, dict) else {}
        label = '5h' if group == 'session' else '7d'
        if isinstance(product, dict) and isinstance(product.get('display_name'), str):
            label = product['display_name'][:80]+' · '+label
        rows.append((label, limit.get('percent'), limit.get('resets_at'), 300 if group == 'session' else 10080))
    if not rows:
        for key, label, duration in (('five_hour', '5h', 300), ('seven_day', '7d', 10080),
                                     ('seven_day_sonnet', 'Sonnet · 7d', 10080), ('seven_day_opus', 'Opus · 7d', 10080)):
            limit = data.get(key)
            if isinstance(limit, dict):
                rows.append((label, limit.get('utilization'), limit.get('resets_at'), duration))
    result = []
    for name, percent, resets_at, duration in rows:
        if isinstance(percent, bool):
            continue
        try:
            used = float(percent)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(used):
            continue
        used = max(0, min(100, used))
        window = {'usedPercent': used, 'remainingPercent': 100-used, 'windowDurationMins': duration}
        try:
            window['resetsAt'] = _timestamp(resets_at)
        except (TypeError, ValueError, AttributeError, OSError, OverflowError):
            pass
        result.append({'name': name, 'windows': [window]})
    return result


class ClaudeAccounts:
    def __init__(self, directory, data_home, *, executable=None, package_family=None, explicit_home=False):
        self.directory = Path(directory).absolute()
        self.mode_home = self.home = Path(data_home).absolute()
        self._ambiguous_profiles = set()
        if explicit_home:
            self.threep = self.home
        elif sys.platform == 'win32':
            groups = windows_claude_profile_paths(executable, self.home, package_family=package_family)
            markers = ('Local State', 'Preferences', 'Network/Cookies', 'Cookies',
                       'config.json', CONFIG, 'developer_settings.json')

            def select(kind):
                paths = groups[kind]
                populated = [path for path in paths if any((path/item).is_file() for item in markers)]
                if self.mode_home in paths:
                    # Discovery can return its canonical fallback when stopped
                    # profiles conflict. An absent fallback is not active proof.
                    if self.mode_home not in populated and populated:
                        self._ambiguous_profiles.add(kind)
                    return self.mode_home
                if len(populated) > 1:
                    self._ambiguous_profiles.add(kind)
                if populated:
                    return populated[0]
                # With no profile evidence, retain the canonical Store or
                # classic official path and the current local 3p default.
                if kind == 'official':
                    return next((path for path in paths if path.parent.parent.name == 'LocalCache'), paths[-1])
                return paths[0]

            self.home = select('official') if groups else self.home
            self.threep = select('thirdparty') if groups else self.home
        elif self.home.name == 'Claude-3p':
            self.threep = self.home
            self.home = self.home.with_name('Claude')
        elif self.home.name == 'Claude':
            self.threep = self.home.with_name('Claude-3p')
        else:
            # An explicit custom profile stays scoped to that profile.
            self.threep = self.home
        if sys.platform != 'win32':
            self.mode_home = self.home
        if any(path.is_symlink() for root in (self.directory, self.mode_home, self.home, self.threep) for path in (root, *root.parents)):
            raise ValueError('Claude 账号目录不能是符号链接')
        self.lock = threading.RLock()

    def _index(self):
        return _json(self.directory/'index.json', {'accounts': []})

    def _save(self, value):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        private_json(self.directory/'index.json', value)

    def _account(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('Claude 账号标识无效')
        row = next((row for row in self._index()['accounts'] if row['id'] == identifier), None)
        if row is None:
            raise ValueError('Claude 账号不存在，请刷新列表')
        return row, self.directory/'profiles'/identifier

    def _gateway(self):
        mode = _json(self.mode_home/CONFIG).get('deploymentMode')
        if mode != '3p':
            return None
        if 'thirdparty' in self._ambiguous_profiles:
            raise ValueError(AMBIGUOUS_PROFILE)
        library = self.threep/'configLibrary'
        meta = _json(library/'_meta.json')
        identifier = meta.get('appliedId')
        try:
            identifier = str(uuid.UUID(identifier))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Claude Desktop 当前 API 配置不可读') from None
        config = _json(library/(identifier+'.json'))
        if config.get('inferenceProvider') != 'gateway' or not config.get('inferenceGatewayBaseUrl'):
            raise ValueError('当前 Claude 第三方接入暂不支持保存')
        name = next((entry.get('name') for entry in meta.get('entries', []) if entry.get('id') == identifier), None)
        return {'id': identifier, 'name': name or 'Claude API', 'config': config}

    def _current(self):
        gateway = self._gateway()
        if gateway:
            fingerprint = hashlib.sha256(json.dumps(gateway['config'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            return 'api', fingerprint, gateway
        if 'official' in self._ambiguous_profiles:
            raise ValueError(AMBIGUOUS_PROFILE)
        return 'official', _fingerprint(_cookies(self.home)), None

    def public(self):
        with self.lock:
            index = self._index()
            try:
                kind, fingerprint, gateway = self._current()
            except (OSError, ValueError, sqlite3.Error):
                kind, fingerprint, gateway = None, None, None
            accounts = []
            active = None
            for saved in index['accounts']:
                row = {key: saved[key] for key in ('id', 'name', 'kind', 'email', 'usage', 'baseUrl') if key in saved}
                if row.get('usage'):
                    row['usage'] = _public_usage(row['usage'])
                row['saved'] = True
                row['active'] = saved['kind'] == kind and saved.get('fingerprint') == fingerprint
                if row['active']:
                    active = row['id']
                accounts.append(row)
            if active is None and fingerprint:
                active = 'current'
                row = {'id': active, 'name': (gateway or {}).get('name') or 'Claude Desktop',
                       'kind': kind, 'saved': False, 'active': True, 'canSwitch': False}
                try:
                    cache = _json(self.directory/'current-usage.json')
                except (OSError, ValueError):
                    cache = {}
                if cache.get('fingerprint') == fingerprint and cache.get('kind') == kind:
                    row['usage'] = _public_usage(cache['usage'])
                accounts.append(row)
            accounts.sort(key=lambda row: not row['active'])
            current = next((row for row in accounts if row['id'] == active), None)
            value = {'provider': 'claude', 'accounts': accounts, 'activeId': active, 'current': current,
                     'canManage': True, 'readOnly': False}
            if fingerprint is None:
                value['notice'] = '无法确认 Claude 当前登录，请在客户端登录后重试'
            return value

    def import_current(self, name=''):
        """Save the current desktop login after the manager verifies native exit."""
        with self.lock:
            if not isinstance(name, str) or len(name) > 120:
                raise ValueError('账号名称不能超过 120 字')
            kind, fingerprint, gateway = self._current()
            index = self._index()
            existing = next((row for row in index['accounts'] if row['kind'] == kind and row.get('fingerprint') == fingerprint), None)
            identifier = existing['id'] if existing else uuid.uuid4().hex
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            staged = self.directory/'staging'/uuid.uuid4().hex
            staged.mkdir(parents=True, mode=0o700)
            try:
                if kind == 'official':
                    for item in AUTH_ITEMS:
                        source = self.home/item
                        if source.exists() or source.is_symlink():
                            _copy(source, staged/item)
                    config = _json(self.home/'config.json')
                    private_json(staged/'auth.json', {TOKEN_KEY: config[TOKEN_KEY]} if TOKEN_KEY in config else {})
                    if _fingerprint(_cookies(staged)) != fingerprint:
                        raise ValueError('Claude 登录在保存时发生变化，请退出客户端后重试')
                else:
                    private_json(staged/'gateway.json', gateway)
                private_json(staged/'manifest.json', {'kind': kind, 'fingerprint': fingerprint})
                target = self.directory/'profiles'/identifier
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                previous = target.with_name(identifier+'.previous')
                if target.exists():
                    target.rename(previous)
                try:
                    staged.rename(target)
                    row = dict(existing or {}, id=identifier, name=name.strip() or (existing or {}).get('name') or (gateway or {}).get('name') or 'Claude 账号', kind=kind, fingerprint=fingerprint)
                    if gateway:
                        url = parse.urlsplit(gateway['config']['inferenceGatewayBaseUrl'])
                        row['baseUrl'] = url.scheme+'://'+(url.hostname or '')
                    index['accounts'] = [r for r in index['accounts'] if r['id'] != identifier]+[row]
                    self._save(index)
                except Exception:
                    _remove(target)
                    if previous.exists():
                        previous.rename(target)
                    raise
                _remove(previous)
            finally:
                _remove(staged)
            return self.public()

    def edit(self, identifier):
        """Desktop-only editor data; never return a saved credential."""
        with self.lock:
            row, snapshot = self._account(identifier)
            result = {key: row[key] for key in ('id', 'name', 'kind')}
            if row['kind'] == 'api':
                config = _json(snapshot/'gateway.json').get('config', {})
                url = parse.urlsplit(config.get('inferenceGatewayBaseUrl', ''))
                # Interactive/helper credentials remain native-managed. An URL
                # carrying private query data is not sent back to the editor.
                if (config.get('inferenceGatewayApiKey') and not url.query and not url.fragment
                        and not url.username and not url.password
                        and config.get('inferenceCredentialKind', 'static') == 'static'):
                    result['api'] = {'baseUrl': config['inferenceGatewayBaseUrl'],
                        'authScheme': config.get('inferenceGatewayAuthScheme', 'bearer'),
                        'models': [model if isinstance(model, str) else model.get('name', '')
                                   for model in config.get('inferenceModels', [])], 'hasKey': True}
            return result

    def rename(self, identifier, name):
        with self.lock:
            self._account(identifier)
            name = self._name(name)
            index = self._index()
            next(row for row in index['accounts'] if row['id'] == identifier)['name'] = name
            self._save(index)
            return self.public()

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
            raise ValueError('接入名称不能为空且不能超过 120 字')
        return value.strip()

    def save_api(self, value):
        """Save a gateway snapshot; applying it still uses the lifecycle gate."""
        with self.lock:
            name = self._name(value.get('name'))
            existing, old = None, None
            if value.get('id'):
                existing, snapshot = self._account(value['id'])
                if existing['kind'] != 'api' or not self.edit(existing['id']).get('api'):
                    raise ValueError('此接入由客户端管理，仅支持修改名称')
                old = _json(snapshot/'gateway.json')
            url = value.get('baseUrl')
            if (not isinstance(url, str) or len(url) > 2048
                    or any(c.isspace() or ord(c) < 32 or c in '\\\x7f' for c in url)):
                raise ValueError('请输入有效的 API 地址')
            try:
                parsed = parse.urlsplit(url)
                valid = (parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1'))
                valid = valid and parsed.hostname and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('API 地址须使用 HTTPS（本机可使用 HTTP），且不能包含凭据、查询参数或片段')
            key = value.get('apiKey', '')
            if key == '' and old:
                key = old['config'].get('inferenceGatewayApiKey')
            if not isinstance(key, str) or not re.fullmatch(r'[\x21-\x7e]{1,8192}', key):
                raise ValueError('请输入有效的 API Key')
            auth = value.get('authScheme', 'bearer')
            if auth not in ('bearer', 'x-api-key'):
                raise ValueError('API 认证方式无效')
            models = value.get('models')
            if (not isinstance(models, list) or not 1 <= len(models) <= 200
                    or any(not isinstance(model, str) or not re.fullmatch(r'[^\s\x00-\x1f\x7f]{1,200}', model) for model in models)):
                raise ValueError('请填写上游支持的模型 ID，每行一个，最多 200 个')
            config = copy.deepcopy(old['config']) if old else {}
            old_models = config.get('inferenceModels', [])
            by_name = {model if isinstance(model, str) else model.get('name', ''): model for model in old_models}
            # Reordering/removing models must not erase native metadata on
            # retained entries. Only newly added IDs use the string shorthand.
            config['inferenceModels'] = [by_name.get(model, model) for model in dict.fromkeys(models)]
            config.update(inferenceProvider='gateway', inferenceGatewayBaseUrl=url,
                          inferenceCredentialKind='static', inferenceGatewayApiKey=key,
                          inferenceGatewayAuthScheme=auth)
            gateway = {'id': old['id'] if old else str(uuid.uuid4()), 'name': name, 'config': config}
            identifier = existing['id'] if existing else uuid.uuid4().hex
            fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            index = self._index()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            target = self.directory/'profiles'/identifier
            staged = self.directory/'staging'/uuid.uuid4().hex
            _safe(staged)
            _safe(target)
            staged.mkdir(parents=True, mode=0o700)
            previous = target.with_name(identifier+'.previous')
            _safe(previous)
            try:
                private_json(staged/'gateway.json', gateway)
                private_json(staged/'manifest.json', {'kind': 'api', 'fingerprint': fingerprint})
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.exists():
                    target.rename(previous)
                try:
                    staged.rename(target)
                    row = {'id': identifier, 'name': name, 'kind': 'api', 'fingerprint': fingerprint,
                           'baseUrl': parsed.scheme+'://'+parsed.hostname}
                    index['accounts'] = [item for item in index['accounts'] if item['id'] != identifier]+[row]
                    self._save(index)
                except Exception:
                    _remove(target)
                    if previous.exists():
                        previous.rename(target)
                    raise
                _remove(previous)
            finally:
                _remove(staged)
            return self.public()

    def remove(self, identifier):
        """Forget the gateway's copy, leaving the native login untouched."""
        with self.lock:
            _, snapshot = self._account(identifier)
            _safe(snapshot)
            index = self._index()
            index['accounts'] = [row for row in index['accounts'] if row['id'] != identifier]
            # Keep rollback material until the index update has succeeded.
            staged = snapshot.with_name(identifier+'.removed')
            _safe(staged)
            moved = snapshot.exists()
            if moved:
                snapshot.rename(staged)
            try:
                self._save(index)
            except Exception:
                if moved:
                    staged.rename(snapshot)
                raise
            if moved:
                _remove(staged)
            return self.public()

    def _targets(self):
        targets = [('home', self.home, name) for name in (*AUTH_ITEMS, 'config.json', CONFIG)]
        targets += [('threep', self.threep, name) for name in (CONFIG, 'configLibrary')
                    if not (self.threep == self.home and name == CONFIG)]
        return targets

    def _backup(self):
        token = uuid.uuid4().hex
        root = self.directory/'transactions'/token
        root.mkdir(parents=True, mode=0o700)
        for scope, home, item in self._targets():
            source = home/item
            if source.exists() or source.is_symlink():
                _copy(source, root/scope/item)
        private_json(root/'complete.json', {'complete': True})
        return token

    def _transaction(self, token):
        if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise ValueError('Claude 恢复记录无效')
        root = self.directory/'transactions'/token
        if _json(root/'complete.json').get('complete') is not True:
            raise ValueError('Claude 恢复记录不可用')
        return root

    def rollback(self, token):
        with self.lock:
            root = self._transaction(token)
            for scope, home, item in self._targets():
                _remove(home/item)
                source = root/scope/item
                if source.exists():
                    _copy(source, home/item)

    def commit(self, token):
        with self.lock:
            _remove(self._transaction(token))

    def restore(self, identifier):
        """Apply a saved login only while stopped; return a private rollback ID."""
        with self.lock:
            if self._ambiguous_profiles:
                raise ValueError(AMBIGUOUS_PROFILE)
            row, snapshot = self._account(identifier)
            manifest = _json(snapshot/'manifest.json')
            if manifest.get('fingerprint') != row.get('fingerprint') or manifest.get('kind') != row['kind']:
                raise ValueError('Claude 账号快照不可用，请重新保存')
            token = self._backup()
            try:
                if row['kind'] == 'official':
                    _cookies(snapshot)
                    for item in AUTH_ITEMS:
                        _remove(self.home/item)
                        if (snapshot/item).exists():
                            _copy(snapshot/item, self.home/item)
                    config = _json(self.home/'config.json')
                    config.pop(TOKEN_KEY, None)
                    auth = _json(snapshot/'auth.json')
                    if TOKEN_KEY in auth:
                        config[TOKEN_KEY] = auth[TOKEN_KEY]
                    private_json(self.home/'config.json', config)
                else:
                    gateway = _json(snapshot/'gateway.json')
                    config_id = str(uuid.UUID(gateway['id']))
                    library = self.threep/'configLibrary'
                    meta = _json(library/'_meta.json')
                    entries = [entry for entry in meta.get('entries', []) if entry.get('id') != config_id]
                    entries.append({'id': config_id, 'name': gateway['name']})
                    private_json(library/(config_id+'.json'), gateway['config'])
                    private_json(library/'_meta.json', {**meta, 'entries': entries, 'appliedId': config_id})
                for home in dict.fromkeys((self.home, self.threep)):
                    config = _json(home/CONFIG)
                    config['deploymentMode'] = '3p' if row['kind'] == 'api' else '1p'
                    private_json(home/CONFIG, config)
                if self._current()[1] != row['fingerprint']:
                    raise ValueError('Claude 账号写入后未通过核对')
            except Exception:
                try:
                    self.rollback(token)
                except Exception:
                    raise ValueError('Claude 账号切换失败，原接入恢复未完成，请在桌面端恢复') from None
                raise ValueError('Claude 账号切换失败，已恢复原接入') from None
            return token

    def details(self, identifier, refresh=False):
        with self.lock:
            ephemeral = identifier == 'current'
            if ephemeral:
                active = self.public()['activeId']
                if active and active != 'current':
                    return self.details(active, refresh)
            if ephemeral:
                try:
                    kind, fingerprint, _ = self._current()
                except (OSError, ValueError):
                    return self.public()
                row = {'id': 'current', 'kind': kind, 'fingerprint': fingerprint}
                snapshot = self.home
                try:
                    cache = _json(self.directory/'current-usage.json')
                except (OSError, ValueError):
                    cache = {}
                if cache.get('fingerprint') == fingerprint and cache.get('kind') == kind:
                    row['usage'] = cache['usage']
            else:
                row, snapshot = self._account(identifier)
            usage = row.get('usage') or {}
            try:
                checked = _timestamp(usage.get('checkedAt', ''))
            except (TypeError, ValueError, AttributeError, OSError, OverflowError):
                checked = 0
            if not refresh and 0 <= time.time()-checked < TTL:
                return self.public()
            usage = {'status': 'unsupported' if row['kind'] == 'api' else 'error',
                     'checkedAt': time.time(), 'limits': []}
            if row['kind'] != 'api':
                try:
                    cookies = {}
                    # The active native cookie can rotate; use its current login
                    # only when the fingerprint still matches this saved account.
                    source = self.home if self.public()['activeId'] == identifier else snapshot
                    for host, name, value, encrypted, expiry in _cookies(source):
                        if expiry and expiry/1000000-11644473600 <= time.time():
                            continue
                        value = value or _decrypt_cookie(host, encrypted, source)
                        if not value or any(ord(c) < 33 or ord(c) > 126 or c in ';\r\n' for c in value):
                            raise ValueError('Claude 登录凭据不可用，请在客户端重新登录后保存')
                        cookies[name] = value
                    if not all(cookies.get(name) for name in ('sessionKey', 'lastActiveOrg')):
                        raise ValueError('Claude 登录已失效，请在客户端重新登录后保存')
                    org = cookies['lastActiveOrg']
                    data = _web_json('/api/organizations/'+parse.quote(org, safe='')+'/usage', cookies, org)
                    if ephemeral and self._current()[:2] != (kind, fingerprint):
                        raise ValueError('Claude 当前登录已发生变化，请重新查询')
                    usage['limits'] = _usage_limits(data)
                    if not usage['limits']:
                        raise ValueError('Claude 未返回可识别的剩余额度')
                    usage['status'] = 'ready'
                except Exception:
                    # Network/server bodies and OS errors may contain credentials.
                    usage['error'] = '无法查询 Claude 剩余额度，请确认客户端登录有效并解锁系统凭据存储后重试'
            if ephemeral:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.directory.chmod(0o700)
                private_json(self.directory/'current-usage.json', {'kind': kind, 'fingerprint': fingerprint, 'usage': usage})
            else:
                index = self._index()
                next(value for value in index['accounts'] if value['id'] == identifier)['usage'] = usage
                self._save(index)
            return self.public()
