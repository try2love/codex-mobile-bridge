"""SQLite metadata only; never stores model keys, chat bodies or raw tokens."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path

PHONE_LIFETIME = 180 * 86400


def token():
    return secrets.token_urlsafe(32)


def digest(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError('Invalid token')
    return hashlib.sha256(value.encode()).hexdigest()


def label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise ValueError('Name must contain 1–80 characters')
    return value.strip()


class Registry:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / 'relay.sqlite3'
        self.db = sqlite3.connect(path, timeout=10)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS invitations (id TEXT PRIMARY KEY, hash TEXT UNIQUE,
                owner TEXT, expires REAL, used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, hash TEXT UNIQUE,
                owner TEXT, name TEXT, created REAL, revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pairs (id TEXT PRIMARY KEY, hash TEXT UNIQUE,
                device TEXT, expires REAL, claim TEXT UNIQUE, name TEXT, state TEXT);
            CREATE TABLE IF NOT EXISTS phones (id TEXT PRIMARY KEY, hash TEXT UNIQUE,
                device TEXT, name TEXT, csrf TEXT, expires REAL, revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS admin_credentials (id INTEGER PRIMARY KEY CHECK(id=1), hash TEXT);
            CREATE TABLE IF NOT EXISTS admin_sessions (hash TEXT PRIMARY KEY, csrf TEXT, expires REAL);
        ''')

    def close(self):
        self.db.close()

    def invite(self, owner, hours=24):
        if type(hours) is not int or not 1 <= hours <= 720:
            raise ValueError('Invitation lifetime must be 1–720 hours')
        secret, identifier = token(), secrets.token_hex(16)
        with self.db:
            self.db.execute('INSERT INTO invitations VALUES(?,?,?,?,0)',
                            (identifier, digest(secret), label(owner), time.time() + hours * 3600))
        return {'id': identifier, 'invitation': secret}

    def invitations(self):
        return [dict(r) for r in self.db.execute('SELECT id,owner,expires,used FROM invitations ORDER BY expires DESC')]

    def rotate_admin_token(self):
        secret = token()
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO admin_credentials VALUES(1,?)', (digest(secret),))
            self.db.execute('DELETE FROM admin_sessions')
        return secret

    def admin_configured(self):
        return bool(self.db.execute('SELECT 1 FROM admin_credentials WHERE id=1').fetchone())

    def admin_login(self, key):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT hash FROM admin_credentials WHERE id=1').fetchone()
            if row is None or not hmac.compare_digest(row['hash'], digest(key)):
                raise PermissionError('管理密钥无效或尚未设置')
            secret, csrf, expires = token(), token(), time.time() + 8 * 3600
            self.db.execute('DELETE FROM admin_sessions WHERE expires<=?', (time.time(),))
            self.db.execute('INSERT INTO admin_sessions VALUES(?,?,?)', (digest(secret), csrf, expires))
        return secret, csrf

    def admin_session(self, secret):
        row = self.db.execute('SELECT csrf,expires FROM admin_sessions WHERE hash=? AND expires>?',
                              (digest(secret), time.time())).fetchone()
        if row is None:
            raise PermissionError('管理登录已失效，请重新登录')
        return dict(row)

    def admin_logout(self, secret):
        with self.db:
            self.db.execute('DELETE FROM admin_sessions WHERE hash=?', (digest(secret),))

    def register(self, invitation, name):
        name = label(name)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM invitations WHERE hash=? AND used=0 AND expires>?',
                                  (digest(invitation), time.time())).fetchone()
            if row is None:
                raise PermissionError('Invitation expired or already used')
            identifier, secret = secrets.token_hex(16), token()
            self.db.execute('UPDATE invitations SET used=1 WHERE id=?', (row['id'],))
            self.db.execute('INSERT INTO devices VALUES(?,?,?,?,?,0)',
                            (identifier, digest(secret), row['owner'], name, time.time()))
        return {'deviceId': identifier, 'deviceToken': secret, 'deviceName': name}

    def device(self, secret):
        row = self.db.execute('SELECT * FROM devices WHERE hash=? AND revoked=0', (digest(secret),)).fetchone()
        if row is None:
            raise PermissionError('Device authorization revoked or invalid')
        return dict(row)

    def active(self, identifier):
        return bool(self.db.execute('SELECT 1 FROM devices WHERE id=? AND revoked=0', (identifier,)).fetchone())

    def devices(self):
        return [dict(r) for r in self.db.execute('SELECT id,owner,name,created,revoked FROM devices ORDER BY created')]

    def revoke(self, identifier):
        with self.db:
            self.db.execute('UPDATE devices SET revoked=1 WHERE id=?', (identifier,))
            self.db.execute('UPDATE phones SET revoked=1 WHERE device=?', (identifier,))
            self.db.execute('DELETE FROM pairs WHERE device=?', (identifier,))

    def pair(self, device):
        secret, identifier = token(), secrets.token_hex(16)
        expires = time.time() + 300
        with self.db:
            self.db.execute('DELETE FROM pairs WHERE device=? OR expires<?', (device, time.time()))
            self.db.execute('INSERT INTO pairs VALUES(?,?,?,?,NULL,NULL,?)',
                            (identifier, digest(secret), device, expires, 'waiting'))
        return {'id': identifier, 'token': secret, 'expires': expires}

    def claim(self, secret, name):
        name = label(name)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute("SELECT * FROM pairs WHERE hash=? AND state='waiting' AND expires>?",
                                  (digest(secret), time.time())).fetchone()
            if row is None or not self.active(row['device']):
                raise PermissionError('Pairing expired or already claimed')
            claim = token()
            self.db.execute("UPDATE pairs SET claim=?,name=?,state='pending' WHERE id=?", (digest(claim), name, row['id']))
        return {'claim': claim}

    def pending(self, device):
        return [dict(r) for r in self.db.execute("SELECT id,name,expires FROM pairs WHERE device=? AND state='pending' AND expires>?", (device, time.time()))]

    def approve(self, device, identifier, approved):
        if type(approved) is not bool:
            raise ValueError('Explicit approval required')
        with self.db:
            count = self.db.execute("UPDATE pairs SET state=? WHERE device=? AND id=? AND state='pending' AND expires>?",
                                    ('approved' if approved else 'denied', device, identifier, time.time())).rowcount
            if count != 1:
                raise PermissionError('Pairing expired or belongs to another device')

    def consume(self, claim, device=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM pairs WHERE claim=? AND expires>?', (digest(claim), time.time())).fetchone()
            if row is None or not self.active(row['device']) or (device and row['device'] != device):
                raise PermissionError('Pairing expired')
            if row['state'] != 'approved':
                return {'state': row['state']}, None
            count = self.db.execute('SELECT COUNT(*) FROM phones WHERE device=? AND revoked=0 AND expires>?', (row['device'], time.time())).fetchone()[0]
            if count >= 16:
                raise ValueError('Revoke a phone before adding more (limit 16)')
            secret, identifier, csrf = token(), secrets.token_hex(16), token()
            self.db.execute('INSERT INTO phones VALUES(?,?,?,?,?,?,0)',
                            (identifier, digest(secret), row['device'], row['name'], csrf, time.time() + PHONE_LIFETIME))
            self.db.execute("UPDATE pairs SET state='used' WHERE id=?", (row['id'],))
        return {'state': 'approved', 'csrf': csrf, 'deviceId': row['device']}, secret

    def phone(self, secret):
        row = self.db.execute('SELECT p.*, d.name AS computer_name FROM phones p JOIN devices d ON p.device=d.id '
                              'WHERE p.hash=? AND p.revoked=0 AND d.revoked=0 AND p.expires>?', (digest(secret), time.time())).fetchone()
        if row is None or not self.active(row['device']):
            raise PermissionError('Phone authorization expired or revoked')
        return dict(row)

    def phones(self, device):
        return [dict(r) for r in self.db.execute('SELECT id,name,expires FROM phones WHERE device=? AND revoked=0 AND expires>?', (device, time.time()))]

    def renew_phone(self, phone):
        # At most one write per day; revoked/expired grants cannot be resurrected.
        if phone['expires'] < time.time() + PHONE_LIFETIME - 86400:
            with self.db:
                self.db.execute('UPDATE phones SET expires=? WHERE id=? AND revoked=0 AND expires>?',
                                (time.time() + PHONE_LIFETIME, phone['id'], time.time()))
            return True
        return False

    def revoke_phone(self, device, identifier):
        with self.db:
            self.db.execute('UPDATE phones SET revoked=1 WHERE device=? AND id=?', (device, identifier))
