import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from bridge.features.accounts.draft_identity import draft_identity


@contextmanager
def session_file_lock(path):
    """Serialize session-file replacements across gateway and desktop processes."""
    lock_path = path.with_name(path.name + '.lock')
    with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600), 'r+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def replace_session_file(path, contents):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(contents)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def session_hours(value):
    if type(value) is not int or not 0 <= value <= 87600:
        raise ValueError('登录有效期必须为 0–87600 的整数小时')
    return value


def canonical_ip(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def access_policy(value):
    if not isinstance(value, dict) or type(value.get('allowlistEnabled', False)) is not bool:
        raise ValueError('IP 访问规则格式不正确')
    result = {'allowlistEnabled': value.get('allowlistEnabled', False)}
    for key in ('allowlist', 'blocklist', 'trustedProxies'):
        entries = value.get(key, [])
        if not isinstance(entries, list) or len(entries) > 256:
            raise ValueError('IP 列表最多允许 256 个地址')
        try:
            if any(not isinstance(item, str) or '%' in item for item in entries):
                raise ValueError()
            result[key] = list(dict.fromkeys(canonical_ip(item.strip()) for item in entries))
        except ValueError:
            raise ValueError('请填写有效的 IPv4 或 IPv6 地址，每行一个') from None
    if result['allowlistEnabled'] and not result['allowlist']:
        raise ValueError('启用白名单前，请至少添加一个允许访问的 IP')
    return result


def password_record(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000, dklen=32)
    return {"algorithm": "pbkdf2-sha256", "iterations": 600000, "salt": salt.hex(), "hash": digest.hex()}


class RouteToken(str):
    """Accepted only after matching this request's approved origin and live parent."""
    def __new__(cls, value, parent):
        token = super().__new__(cls, value)
        token.parent = parent
        return token


class LoginRejected(PermissionError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class Auth:
    COOKIE = "codex_mobile_session"
    MAX_FAILURES = 5
    REMEMBER_HOURS = 7 * 24

    @staticmethod
    def mobile_client(user_agent):
        # Only selects lifetime AFTER authentication; this is not proof of identity.
        return bool(re.search(r'(?:^|\s)BridgeMobile/[\w.-]+-(?:iOS|Android)(?:\s|$)', user_agent))

    def __init__(self, config, data_dir=None):
        self.config = config
        self.hours = session_hours(config.get('sessionHours', 12))
        identity = {k: config.get(k) for k in ('mode', 'username', 'salt', 'hash', 'iterations')}
        self.identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        legacy_identity = hashlib.sha256(json.dumps({**identity, 'sessionHours': self.hours}, sort_keys=True).encode()).hexdigest()
        self.path = Path(data_dir) / 'auth-sessions.json' if data_dir is not None else None
        # A cookie namespace belongs to the gateway data directory, not the
        # process or its (possibly changed) login policy.
        self.cookie_name = self.COOKIE + '_' + secrets.token_hex(12)
        self.sessions = {}
        self.policy = access_policy({})
        self.failures = {}
        self.auto_blocks = {}
        self.lock = threading.RLock()
        self._loaded = self.path.read_bytes() if self.path and self.path.exists() else None
        if self.path and self.path.exists():
            saved = json.loads(self._loaded)
            self.cookie_name = saved.get('cookieName') or self.cookie_name
            self.policy = access_policy(saved['policy'])
            self.failures = saved.get('failures', {})
            self.auto_blocks = saved.get('autoBlocks', {})
            if saved['identity'] in (self.identity, legacy_identity):
                self.sessions = {k: v for k, v in saved['sessions'].items() if self.valid(v)}
                if saved['identity'] != self.identity:
                    self.persist()
            else:
                self.persist()
            if not saved.get('cookieName'):
                self.persist()
        elif self.path:
            self.persist()

    @property
    def draft_identity(self):
        # The persisted cookie namespace follows the data directory. Neither
        # renewable session tokens nor password hashes belong in a draft key.
        return draft_identity('gateway', self.cookie_name, self.config.get('username') or '')

    @staticmethod
    def valid(session):
        return session['expires'] == 0 or session['expires'] > time.time()

    @staticmethod
    def key(token):
        return token.parent if isinstance(token, RouteToken) else hashlib.sha256(token.encode()).hexdigest()

    def persist(self):
        if self.path is None:
            return
        with session_file_lock(self.path):
            current = self.path.read_bytes() if self.path.exists() else None
            if current != self._loaded:
                raise ValueError('设备授权已变化，请刷新后重试')
            contents = json.dumps({'identity': self.identity, 'cookieName': self.cookie_name, 'policy': self.policy, 'sessions': self.sessions, 'failures': self.failures, 'autoBlocks': self.auto_blocks}).encode('utf-8')
            replace_session_file(self.path, contents)
            self._loaded = contents

    @classmethod
    def preserve_trusted_on_password_change(cls, old_config, new_config, data_dir, publish_config):
        """Move current trusted phone sessions and publish credentials as one locked operation.

        A process crash between session and config writes fails closed. A
        caught config-write failure restores the exact previous session file.
        """
        path = Path(data_dir) / 'auth-sessions.json'
        old_identity = cls(old_config).identity
        new_identity = cls(new_config).identity
        with session_file_lock(path):
            before = path.read_bytes() if path.exists() else None
            if before is None:
                publish_config()
                return
            saved = json.loads(before)
            if saved['identity'] != old_identity:
                raise ValueError('设备授权已变化，请刷新后重试')
            migrated = {**saved, 'identity': new_identity,
                        'sessions': {key: session for key, session in saved['sessions'].items()
                                     if cls.valid(session) and session.get('trustedDevice') is True
                                     and cls.mobile_client(session.get('userAgent', ''))}}
            after = json.dumps(migrated).encode('utf-8')
            replace_session_file(path, after)
            try:
                publish_config()
            except Exception:
                # No cooperating writer can change this file while the lock is held.
                if path.read_bytes() == after:
                    replace_session_file(path, before)
                raise

    def permitted(self, address):
        with self.lock:
            return (address not in self.auto_blocks and address not in self.policy['blocklist'] and
                    (not self.policy['allowlistEnabled'] or address in self.policy['allowlist']))

    def client(self, peer, headers, secure=False):
        """Only trust forwarded IPs from a configured proxy or local HTTPS tunnel."""
        peer = canonical_ip(peer)
        with self.lock:
            trusted = set(self.policy['trustedProxies']) | {'127.0.0.1', '::1'}
        if secure and peer in trusted:
            # A trusted proxy must append or overwrite X-Forwarded-For. Walk from
            # the immediate proxy backwards, never trust the user-controlled left end.
            raw = headers.get('CF-Connecting-IP') if headers.get('Host', '').endswith('.trycloudflare.com') else None
            raw = raw or headers.get('X-Forwarded-For', '')
            try:
                chain = [canonical_ip(part.strip()) for part in raw.split(',')]
                address = peer
                for candidate in reversed(chain):
                    if address not in trusted:
                        break
                    address = candidate
                return {'ip': address, 'peer': peer, 'source': 'forwarded'}
            except ValueError:
                return {'ip': peer, 'peer': peer, 'source': 'proxy'}
        return {'ip': peer, 'peer': peer, 'source': 'proxy' if secure else 'direct'}

    def login_status(self, address):
        with self.lock:
            blocked = not self.permitted(address)
            return {'attemptsRemaining': 0 if blocked else max(0, self.MAX_FAILURES - self.failures.get(address, 0)),
                    'attemptLimit': self.MAX_FAILURES, 'blocked': blocked}

    def login(self, username, password, address, user_agent='', client=None, remember=None):
        if remember is not None and type(remember) is not bool:
            raise ValueError('记住密码选项格式不正确')
        address = canonical_ip(address)
        # Serialize validation and durable accounting: simultaneous requests must
        # not create extra password attempts or clear a newly applied block.
        with self.lock:
            if not self.permitted(address):
                raise LoginRejected('此 IP 已被封禁，请在电脑网关 App 的“登录设备”中解除。', self.login_status(address))
            if self.config.get('mode', 'password') != 'none':
                digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(self.config['salt']), self.config['iterations'], dklen=32)
                valid = hmac.compare_digest(digest.hex(), self.config['hash']) and hmac.compare_digest(username.encode(), self.config['username'].encode())
                if not valid:
                    count = self.failures.get(address, 0) + 1
                    self.failures[address] = count
                    if count >= self.MAX_FAILURES:
                        self.auto_blocks[address] = {'id': secrets.token_hex(16), 'ip': address,
                                                     'blockedAt': time.time(), 'attempts': count}
                        self.sessions = {k: v for k, v in self.sessions.items() if v['ip'] != address}
                    self.persist()
                    message = ('此 IP 已被封禁，请在电脑网关 App 的“登录设备”中解除。'
                               if address in self.auto_blocks else '账号或密码不正确')
                    raise LoginRejected(message, self.login_status(address))
            self.failures.pop(address, None)
            return self.new_session(address, user_agent, client, remember)

    def new_session(self, address='', user_agent='', client=None, remember=None):
        with self.lock:
            if address and not self.permitted(address):
                raise PermissionError('此 IP 已被访问规则禁止')
            now = time.time()
            trusted = self.mobile_client(user_agent)
            hours = 0 if trusted else self.REMEMBER_HOURS if remember is True else self.hours
            self.sessions = {k: v for k, v in self.sessions.items() if self.valid(v)}
            token = secrets.token_urlsafe(32)
            session = {"csrf": secrets.token_urlsafe(32), "expires": now + hours * 3600 if hours else 0,
                       "persistent": trusted or remember is not False,
                       'trustedDevice': trusted, 'remembered': remember is True and not trusted,
                       'created': now, 'lastSeen': now, 'userAgent': user_agent[:512],
                       **(client or {'ip': address, 'peer': address, 'source': 'direct'})}
            self.sessions[self.key(token)] = session
            # Password login and one-use pairing are both successful authentication.
            self.failures.pop(address, None)
            self.persist()
            return token, session

    def get(self, token, client=None, user_agent=''):
        with self.lock:
            session = self.sessions.get(self.key(token))
            if client and not self.permitted(client['ip']):
                return None
            if session and self.valid(session):
                if client:
                    now = time.time()
                    upgrade = self.mobile_client(user_agent) and not session.get('trustedDevice')
                    # Migrate previously issued seven-day cookies while they are valid.
                    remembered = session.get('remembered', session.get('persistent', True) and
                                             session['expires'] - session['created'] == self.REMEMBER_HOURS * 3600)
                    changed = upgrade or session['ip'] != client['ip'] or now - session['lastSeen'] >= 60
                    if upgrade:
                        session.update(expires=0, persistent=True, trustedDevice=True, remembered=False)
                    elif remembered and changed:
                        session.update(expires=now + self.REMEMBER_HOURS * 3600, remembered=True)
                    session.update(client, userAgent=user_agent[:512], lastSeen=now if changed else session['lastSeen'])
                    if changed:
                        self.persist()
                return session
            if self.sessions.pop(self.key(token), None):
                self.persist()
            return None

    def cookie_age(self, token):
        session = self.get(token)
        if not session:
            return 0
        if not session.get('persistent', True):
            return None
        # Browsers cap cookie lifetimes; /api/auth reissues a cookie after renewal.
        return min(400 * 86400, max(1, math.ceil(session['expires'] - time.time()))) if session['expires'] else 400 * 86400

    def logout(self, token):
        with self.lock:
            if self.sessions.pop(self.key(token), None):
                self.persist()

    def manage(self, value):
        """Local desktop control only; never exposed as a public HTTP endpoint."""
        with self.lock:
            action = value.get('action', 'list')
            if action == 'save':
                self.policy = access_policy(value.get('policy'))
                self.sessions = {k: v for k, v in self.sessions.items() if self.permitted(v['ip'])}
                self.persist()
            elif action == 'unblock':
                address = canonical_ip(value.get('ip', ''))
                self.auto_blocks.pop(address, None)
                self.failures.pop(address, None)
                self.policy['blocklist'] = [ip for ip in self.policy['blocklist'] if ip != address]
                self.persist()
            elif action in ('revoke', 'block'):
                session = self.sessions.get(value.get('id'))
                if session and action == 'block':
                    address = canonical_ip(session['ip'])
                    self.policy = access_policy({**self.policy, 'blocklist': list(dict.fromkeys(self.policy['blocklist'] + [address]))})
                    self.sessions = {k: v for k, v in self.sessions.items() if v['ip'] != address}
                else:
                    self.sessions.pop(value.get('id'), None)
                self.persist()
            elif action != 'list':
                raise ValueError('未知设备管理操作')
            return {'policy': {k: list(v) if isinstance(v, list) else v for k, v in self.policy.items()},
                    'autoBlocks': sorted(self.auto_blocks.values(), key=lambda row: row['blockedAt'], reverse=True),
                    'sessions': sorted([{'id': k, **{field: val for field, val in v.items() if field not in ('csrf', 'routeBindings', 'routeRenewal', 'routeLegacyRenewal')}}
                                        for k, v in self.sessions.items() if self.valid(v)],
                                       key=lambda row: row['lastSeen'], reverse=True)}
