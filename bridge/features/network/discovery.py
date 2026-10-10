"""Authenticated rendezvous metadata; a URL alone never proves computer identity."""
import hashlib
import hmac
import re
from urllib.parse import urlsplit

from .shared_relay import read_config


def mac(key, value):
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


class ConnectionDiscovery:
    def __init__(self, directory, gateway):
        self.directory, self.gateway = directory, gateway

    def config(self):
        cfg = read_config(self.directory)
        return cfg if cfg.get('enabled') and cfg.get('deviceToken') and cfg.get('deviceId') else {}

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

    def prove(self, identifier, challenge, target):
        if not re.fullmatch(r'[a-f0-9]{64}', identifier or '') or not re.fullmatch(r'[a-f0-9]{64}', challenge or ''):
            raise ValueError('Invalid identity challenge')
        cfg = self.config()
        if not cfg or target not in self.gateway.origins:
            raise PermissionError('Connection discovery unavailable')
        with self.gateway.auth.lock:
            for key, session in self.gateway.auth.sessions.items():
                if self.gateway.auth.valid(session) and hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), identifier):
                    return {'deviceId': cfg['deviceId'], 'proof': mac(key, cfg['deviceId'] + '\n' + target + '\n' + challenge)}
        raise PermissionError('Binding expired or revoked')
