"""Short-lived, origin-bound sign-in grants; issuance is local-control only."""
import hashlib
import ipaddress
import secrets
import threading
import time
from urllib.parse import urlsplit


def phone_origin(value):
    if not isinstance(value, str):
        raise ValueError('地址不可用')
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError('地址不可用')
    host = url.hostname.lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == 'localhost' or host.endswith('.localhost')
    if loopback:
        raise ValueError('回环地址不能用于手机扫码，请开启局域网或外网连接')
    return url.scheme + '://' + url.netloc


class Pairing:
    TTL = 300

    def __init__(self, auth, origins):
        self.auth = auth
        self.origins = origins
        self.grants = {}
        self.failures = {}
        self.lock = threading.Lock()

    def control(self, value):
        action = value.get('action')
        with self.lock:
            now = time.time()
            self.grants = {k: v for k, v in self.grants.items() if v['expires'] + 300 > now}
            if action == 'create':
                origin = phone_origin(value.get('url'))
                if origin not in self.origins:
                    raise ValueError('地址不可用')
                # One current grant per entry. Refresh revokes the preceding QR.
                self.grants = {k: v for k, v in self.grants.items() if v['origin'] != origin}
                token, identifier = secrets.token_urlsafe(32), secrets.token_hex(16)
                expires = now + self.TTL
                self.grants[identifier] = {'origin': origin, 'digest': hashlib.sha256(token.encode()).digest(),
                                           'expires': expires, 'used': False}
                return {'id': identifier, 'url': origin + '/#pair=' + token, 'expires': expires, 'state': 'active'}
            identifier = value.get('id')
            grant = self.grants.get(identifier) if isinstance(identifier, str) else None
            if action == 'revoke':
                self.grants.pop(identifier, None)
                return {'state': 'revoked'}
            if action == 'status' and isinstance(value.get('ids'), list):
                ids = value['ids'][:64]
                return {'states': {key: ('expired' if key not in self.grants or self.grants[key]['expires'] <= now else 'used' if self.grants[key]['used'] else 'active') for key in ids if isinstance(key, str)}}
            if action == 'status':
                return {'state': 'expired' if not grant or grant['expires'] <= now else 'used' if grant['used'] else 'active'}
            raise ValueError('未知操作')

    def exchange(self, token, origin, address, user_agent='', client=None):
        if not isinstance(token, str) or len(token) != 43:
            raise PermissionError('二维码已失效或已使用，请在电脑上刷新二维码，或使用账号密码登录')
        digest = hashlib.sha256(token.encode()).digest()
        with self.lock:
            now = time.time()
            self.failures = {k: v for k, v in self.failures.items() if now - v[-1] < 300}
            recent = [t for t in self.failures.get(address, []) if now - t < 300]
            self.failures[address] = recent
            if len(recent) >= 8:
                raise PermissionError('尝试次数过多，请 5 分钟后再试')
            recent.append(now)
            for grant in self.grants.values():
                if secrets.compare_digest(grant['digest'], digest) and grant['origin'] == origin and grant['expires'] > now and not grant['used']:
                    grant['used'] = True
                    self.failures.pop(address, None)
                    return self.auth.new_session(address, user_agent, client)
        raise PermissionError('二维码已失效或已使用，请在电脑上刷新二维码，或使用账号密码登录')
