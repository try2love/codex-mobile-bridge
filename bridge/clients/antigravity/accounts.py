"""Default local OAuth snapshots; shared UI, no Electron/profile directory copies."""
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import uuid

from bridge.features.accounts.accounts import private_bytes, private_json
from bridge.clients.errors import BridgeUnavailable
from bridge.platforms.antigravity import login_processes, profile_lease
from .credentials import NativeCredentials


TOKEN_FILES = ('jetski-standalone-oauth-token', 'antigravity-cli/antigravity-oauth-token',
               'antigravity/antigravity-oauth-token')
SCOPE_HINT = ('APP、CLI、IDE 共用此电脑的 Google 登录。请先在原客户端登录，正常退出全部三类入口后保存；'
              '应用后重新打开验证登录。使用同一系统登录的自定义入口也会受影响；自定义目录的独立凭据、GCP、WIF 和 API 配置请在原客户端管理。')


def require_stopped():
    if login_processes():
        raise ValueError('请先正常退出此电脑的 Antigravity APP、CLI、IDE，再保存或应用账号')


def safe(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Antigravity 账号路径不能是符号链接')


def read(path):
    safe(path)
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def identity(raw):
    try:
        value = json.loads(raw)
        token = value.get('token')
        if (value.get('auth_method') != 'consumer' or value.get('wif_provider')
                or not isinstance(token, dict) or not isinstance(token.get('refresh_token'), str)
                or not token['refresh_token']):
            raise ValueError
        email = value.get('id_token_email')
        if not isinstance(email, str) or not email.strip():
            raise ValueError
        return email.strip()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('未识别到受支持的默认 Google 登录，请先在 Antigravity 原客户端完成登录') from None


class AntigravityAccounts:
    def __init__(self, directory, home, metadata):
        self.directory, self.home, self.metadata = Path(directory), Path(home), metadata
        self.lock = threading.RLock()

    def supported(self):
        return self.home == Path.home().resolve()

    def _check(self, *, stopped=True):
        if not self.supported():
            raise ValueError('仅支持默认用户目录的 Antigravity 账号；自定义目录请在原客户端管理')
        safe(self.directory)
        for name in TOKEN_FILES:
            safe(self.home/'.gemini'/name)
        if stopped:
            require_stopped()

    def _index(self):
        raw = read(self.directory/'index.json')
        rows = json.loads(raw)['accounts'] if raw is not None else []
        if not isinstance(rows, list) or any(not isinstance(row, dict) or
                not isinstance(row.get('id'), str) or not re.fullmatch('[0-9a-f]{32}', row['id']) for row in rows):
            raise ValueError('已保存的 Antigravity 账号索引无效')
        return rows

    def _row(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('已保存的 Antigravity 账号标识无效')
        row = next((item for item in self._index() if item['id'] == identifier), None)
        if row is None:
            raise ValueError('保存的 Antigravity 账号不存在')
        return row

    def public(self, *, native=None):
        # Polling reads native identity and private metadata only, never the vault.
        with self.lock:
            native = self.metadata() if native is None else native
            rows, active, matched = [], [], set()
            for saved in self._index():
                row = {key: saved[key] for key in ('id', 'name', 'email')}
                row.update(kind='official', saved=True, canReadQuota=False)
                matches = [item for item in native['accounts'] if item.get('email') == row['email']]
                row['connectedSurfaces'] = [item['surface'] for item in matches]
                if matches:
                    active.append(row['id'])
                    matched.update(item['id'] for item in matches)
                rows.append(row)
            for row in native['accounts']:
                if row['id'] not in matched:
                    rows.append({**row, 'canSwitch': False})
                    active.append(row['id'])
            supported = self.supported()
            return {**native, 'accounts': rows, 'activeIds': active,
                    'activeId': active[0] if active else None,
                    'readOnly': not supported, 'canImport': supported, 'canSwitch': supported,
                    'enrollmentHint': SCOPE_HINT,
                    'switchConfirmation': '将更新此电脑 APP、CLI、IDE 共用的 Google 登录，使用同一系统登录的自定义入口也会受影响；其他电脑不变。已核验空闲的单个 APP 或 IDE 会正常退出并重新打开；独立 CLI、其他实例或无法核验的入口需要先手动退出。聊天、配置和草稿保留。确认应用？',
                    'capabilities': {'addApi': False, 'quota': False, 'switchRequiresStopped': True}}

    def _snapshot(self, vault):
        return {'keyring': vault.read(), 'files': {name: read(self.home/'.gemini'/name) for name in TOKEN_FILES}}

    def _validate(self, value):
        if (not isinstance(value, dict) or set(value) != {'keyring', 'files'}
                or not isinstance(value['files'], dict) or set(value['files']) != set(TOKEN_FILES)):
            raise ValueError('Antigravity 登录快照格式无效')
        values = [value['keyring'], *value['files'].values()]
        emails = {identity(raw) for raw in values if raw is not None}
        if not emails:
            raise ValueError('请先在 Antigravity 原客户端登录，再保存当前接入')
        if len(emails) != 1:
            raise ValueError('默认入口的登录身份不一致，请先在原客户端统一登录后保存')
        return next(iter(emails))

    def validate_saved(self, identifier):
        """Reject invalid selections before requesting any native exit."""
        with self.lock:
            self._check(stopped=False)
            row = self._row(identifier)
            value = json.loads(read(self.directory/(identifier+'.json')) or '{}')
            if self._validate(value) != row['email'] or fingerprint(value) != row['fingerprint']:
                raise ValueError('已保存的登录快照不完整，请重新保存')
            return row['email']

    def import_current(self, name=''):
        if not isinstance(name, str) or len(name.strip()) > 100:
            raise ValueError('名称长度不能超过 100 字')
        with self.lock:
            self._check()
            with profile_lease(self.home/'.gemini/.bridge-account.lock'):
                vault = NativeCredentials()
                snapshot = self._snapshot(vault)
                email = self._validate(snapshot)
                self._check()
                if self._snapshot(vault) != snapshot:
                    raise ValueError('原生登录正在变化，请稍后重新保存')
                rows = self._index()
                # A new private snapshot replaces the prior entry only after both writes succeed.
                identifier = uuid.uuid4().hex
                path = self.directory/(identifier+'.json')
                row = {'id': identifier, 'name': name.strip() or email, 'email': email,
                       'fingerprint': fingerprint(snapshot)}
                previous = [item for item in rows if item['email'] == email]
                try:
                    private_json(path, snapshot)
                    private_json(self.directory/'index.json', {'accounts': [item for item in rows if item['email'] != email]+[row]})
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
                for item in previous:
                    (self.directory/(item['id']+'.json')).unlink(missing_ok=True)
                return self.public()

    def _write_file(self, name, value):
        path = self.home/'.gemini'/name
        safe(path)
        if value is None:
            path.unlink(missing_ok=True)
        else:
            private_bytes(path, value.encode('utf-8'))

    def restore(self, identifier):
        with self.lock:
            row = self._row(identifier)
            self._check()
            selected = json.loads(read(self.directory/(row['id']+'.json')) or 'null')
            self._validate(selected)
            if fingerprint(selected) != row['fingerprint']:
                raise ValueError('保存的 Antigravity 登录快照已变化')
            # Retain the saved list if post-commit discovery fails, but never
            # carry the previous native login identity across a switch.
            result = self.public(native={'accounts': []})
            with profile_lease(self.home/'.gemini/.bridge-account.lock'):
                vault = NativeCredentials()
                before = self._snapshot(vault)
                backup = self.directory/('rollback-'+uuid.uuid4().hex+'.json')
                private_json(backup, {'before': before, 'after': selected})
                written = []
                try:
                    self._check()
                    if self._snapshot(vault) != before:
                        raise ValueError('原生登录正在变化')
                    for name in TOKEN_FILES:
                        if read(self.home/'.gemini'/name) != before['files'][name]:
                            raise ValueError('原生登录文件正在变化')
                        written.append(name)
                        self._write_file(name, selected['files'][name])
                    if vault.read() != before['keyring']:
                        raise ValueError('系统登录凭据正在变化')
                    written.append('keyring')
                    vault.write(selected['keyring'])
                    self._check()
                    if self._snapshot(vault) != selected:
                        raise ValueError('原生登录配置写入未确认')
                except Exception:
                    complete = True
                    try:
                        self._check()
                        for name in reversed(written):
                            old = before['keyring'] if name == 'keyring' else before['files'][name]
                            new = selected['keyring'] if name == 'keyring' else selected['files'][name]
                            current = vault.read() if name == 'keyring' else read(self.home/'.gemini'/name)
                            if current == new:
                                if name == 'keyring':
                                    vault.write(old)
                                else:
                                    self._write_file(name, old)
                            elif current != old:
                                complete = False
                        if self._snapshot(vault) != before:
                            complete = False
                    except Exception:
                        complete = False
                    if complete:
                        backup.unlink(missing_ok=True)
                    raise ValueError('账号应用未完成；'+('已恢复原配置' if complete else '未覆盖外部变化，恢复备份已保留，请在电脑端检查')) from None
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).warning('Antigravity login applied; rollback backup retained')
                # Credential read-back above is the commit point. Cleanup or
                # presentation failures must not prevent the one safe restart.
                try:
                    result = self.public()
                except (OSError, ValueError, BridgeUnavailable):
                    pass
                return {**result, 'message': '登录配置已应用，尚未验证登录；请重新打开原客户端后刷新。'}

    def edit(self, identifier):
        row = self._row(identifier)
        return {key: row[key] for key in ('id', 'name')}

    def rename(self, identifier, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('名称长度应为 1–100 字')
        with self.lock:
            self._row(identifier)
            rows = self._index()
            for row in rows:
                if row['id'] == identifier:
                    row['name'] = name.strip()
            private_json(self.directory/'index.json', {'accounts': rows})
            return self.public()

    def remove(self, identifier):
        with self.lock:
            self._row(identifier)
            private_json(self.directory/'index.json', {'accounts': [row for row in self._index() if row['id'] != identifier]})
            (self.directory/(identifier+'.json')).unlink(missing_ok=True)
            return self.public()
