"""Authenticated rendezvous metadata; a URL alone never proves computer identity."""
import hashlib
import hmac
import re
import socket
import ipaddress

from urllib.parse import urlsplit

from bridge.features.auth.auth import RouteToken

from .shared_relay import read_config


def mac(key, value):
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


class ConnectionDiscovery:
    def __init__(self, directory, gateway, published=None):
        self.directory, self.gateway = directory, gateway
        self.published = published or (lambda: sorted(tuple(gateway.origins)))

    def config(self):
        cfg = read_config(self.directory)
        return cfg if cfg.get('enabled') and cfg.get('deviceToken') and cfg.get('deviceId') else {}

    @property
    def gateway_id(self):
        return hashlib.sha256(self.gateway.auth.cookie_name.encode()).hexdigest()[:32]

    def mobile_routes(self, token):
        auth = self.gateway.auth
        with auth.lock:
            session = auth.get(token)
            if not session or not session.get('trustedDevice'):
                return None
            routes = []
            for origin in self.published():
                if origin not in self.gateway.origins:
                    continue
                url = urlsplit(origin)
                host = url.hostname
                if not host or host == 'localhost' or url.path or url.username or url.password:
                    continue
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    address = None
                if address and (address.is_loopback or address.is_unspecified):
                    continue
                if url.scheme == 'https' or (url.scheme == 'http' and address and address.is_private):
                    routes.append(origin)
            routes = list(dict.fromkeys(routes))
            bindings = session.get('routeBindings', {})
            if not isinstance(token, RouteToken):
                # Separate verifier for proof of possession. It is not a login credential.
                verifier = auth.key(mac(token, 'gateway-route-renew-v1'))
                current = {origin: auth.key('cmbroute1_' + mac(token, 'gateway-route-v1\n' + origin)) for origin in routes}
                if current != bindings or session.get('routeRenewal') != verifier:
                    session.pop('routeLegacyRenewal', None)
                    session.update(routeBindings=current, routeRenewal=verifier)
                    auth.persist()
                bindings = current
            return {'deviceId': self.gateway_id, 'name': socket.gethostname(), 'endpoints': routes,
                    'race': {'select': 'firstVerified', 'parallel': 4},
                    'renewal': 'paired' if session.get('routeRenewal') else 'anchor',
                    'unbound': [url for url in routes if url not in bindings]}

    def renew(self, token, body):
        """A scoped login plus a signed statement from the paired phone authorizes these exact URLs."""
        auth = self.gateway.auth
        with auth.lock:
            descriptor = self.mobile_routes(token)
            session = auth.get(token)
            claims = body.get('bindings')
            if (not descriptor or not isinstance(token, RouteToken) or body.get('deviceId') != self.gateway_id
                    or not isinstance(claims, dict) or not 1 <= len(claims) <= 32):
                raise PermissionError('无效的信道绑定')
            allowed = set(descriptor['endpoints'])
            anchor = body.get('anchor')
            current = session.get('routeBindings', {})
            verifier = session.get('routeRenewal') or session.get('routeLegacyRenewal')
            # Build 42 already registered a separate derived hash for the anchor;
            # it sends the root login on that address, so the derived secret is unused.
            if not verifier and isinstance(anchor, str) and current.get(anchor) != auth.key(str(token)):
                verifier = current.get(anchor)
            if not verifier or not claims.keys() <= allowed:
                raise PermissionError('请重新验证原网关绑定')
            proposed = {url: value for url, value in current.items() if url in allowed}
            for url, claim in claims.items():
                if not isinstance(claim, dict) or not re.fullmatch('[0-9a-f]{64}', str(claim.get('digest', ''))):
                    raise ValueError('信道凭据格式无效')
                digest = claim['digest']
                if url in proposed and not hmac.compare_digest(proposed[url], digest):
                    raise PermissionError('信道绑定身份已变化')
                message = '\n'.join(('route-grant-v1', self.gateway_id, url, digest))
                if not hmac.compare_digest(mac(verifier, message), str(claim.get('proof', ''))):
                    raise PermissionError('信道绑定验证失败')
                proposed[url] = digest
            if proposed != current:
                if not session.get('routeRenewal'):
                    session['routeLegacyRenewal'] = verifier
                session['routeBindings'] = proposed
                auth.persist()
            return {'bound': True}

    def resolve_cookie(self, value, target):
        if not value.startswith('cmbroute1_'):
            return value
        auth = self.gateway.auth
        digest = auth.key(value)
        with auth.lock:
            for key, session in auth.sessions.items():
                if auth.valid(session) and hmac.compare_digest(session.get('routeBindings', {}).get(target, ''), digest):
                    return RouteToken(value, key)
        return ''

    def descriptor(self, token):
        cfg = self.config()
        if not cfg or not self.gateway.auth.get(token):
            return None
        key = self.gateway.auth.key(token)
        return {'deviceId': cfg['deviceId'], 'relay': cfg['url'],
                'discoveryKey': mac(cfg['deviceToken'], 'discovery:' + key)}

    def snapshot(self):
        cfg = self.config()
        if not cfg:
            return None
        with self.gateway.auth.lock:
            keys = [key for key, session in self.gateway.auth.sessions.items() if self.gateway.auth.valid(session)]
        # No login tokens, hashes of login tokens, chat contents or API keys are
        # advertised. Discovery capabilities authorize address lookup only.
        grants = [hashlib.sha256(mac(cfg['deviceToken'], 'discovery:' + key).encode()).hexdigest() for key in keys[-512:]]
        endpoints = sorted(o for o in tuple(self.gateway.origins)
                           if urlsplit(o).hostname not in ('localhost', '127.0.0.1', '::1'))[:16]
        return {'type': 'discovery', 'endpoints': endpoints, 'grants': grants}

    def prove(self, identifier, challenge, target, device=None):
        if not re.fullmatch(r'[a-f0-9]{64}', identifier or '') or not re.fullmatch(r'[a-f0-9]{64}', challenge or ''):
            raise ValueError('Invalid identity challenge')
        if device is not None:
            if device != self.gateway_id or target not in self.gateway.origins:
                raise PermissionError('Connection discovery unavailable')
            auth = self.gateway.auth
            with auth.lock:
                for key, session in auth.sessions.items():
                    if not auth.valid(session) or not session.get('trustedDevice'):
                        continue
                    for candidate in (key, session.get('routeBindings', {}).get(target)):
                        if candidate and hmac.compare_digest(hashlib.sha256(candidate.encode()).hexdigest(), identifier):
                            return {'deviceId': device, 'proof': mac(candidate, device + '\n' + target + '\n' + challenge)}
            raise PermissionError('Binding expired or revoked')
        cfg = self.config()
        if not cfg or target not in self.gateway.origins:
            raise PermissionError('Connection discovery unavailable')
        with self.gateway.auth.lock:
            for key, session in self.gateway.auth.sessions.items():
                if self.gateway.auth.valid(session) and hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), identifier):
                    return {'deviceId': cfg['deviceId'], 'proof': mac(key, cfg['deviceId'] + '\n' + target + '\n' + challenge)}
        raise PermissionError('Binding expired or revoked')
