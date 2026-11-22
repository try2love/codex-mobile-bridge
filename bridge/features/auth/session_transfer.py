"""Gateway cache namespace and compressed response-byte totals for active logins."""
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def hardware_id():
    try:
        if sys.platform == 'darwin':
            data = subprocess.run(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                                  capture_output=True, timeout=2, check=True).stdout
            match = re.search(rb'"IOPlatformUUID"\s*=\s*"([A-Fa-f0-9-]+)"', data)
            return match[1].decode() if match else ''
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                                access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return str(winreg.QueryValueEx(key, 'MachineGuid')[0])
        if sys.platform.startswith('linux'):
            return Path('/etc/machine-id').read_text().strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return ''


def namespace(directory, fallback, username):
    """Keep the already deployed v1 cache namespace stable across upgrades."""
    machine = hardware_id() if directory else ''
    slot = os.path.normcase(str(Path(directory).resolve())) if directory else ''
    gateway = hashlib.sha256(('gateway-v1\0' + (machine or fallback) + '\0' + slot).encode()).hexdigest()[:32]
    owner = hashlib.sha256(('cache-owner-v1\0' + gateway + '\0' + username).encode()).hexdigest()
    return {'version': 1, 'gatewayId': gateway, 'identity': owner}


class TransferUsage:
    def __init__(self, directory):
        self.path = Path(directory) / 'session-transfer.sqlite3' if directory else None
        self.lock = threading.Lock()
        self.db = None
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
            if self.db is not None:
                self.db.close()
                self.db = None

    def _connection(self):
        if self.db is None:
            db = sqlite3.connect(str(self.path) if self.path else ':memory:', timeout=3, check_same_thread=False,
                                 isolation_level=None)
            try:
                if self.path:
                    os.chmod(self.path, 0o600)
                    db.execute('PRAGMA journal_mode=WAL')
                    db.execute('PRAGMA synchronous=NORMAL')
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS usage (login TEXT, provider TEXT, host TEXT, session TEXT, '
                           'title TEXT, bytes INTEGER NOT NULL, method TEXT, host_label TEXT, reused_bytes INTEGER NOT NULL DEFAULT 0, '
                           'PRIMARY KEY(login, provider, host, session))')
                columns = {row[1] for row in db.execute('PRAGMA table_info(usage)')}
                for name in ('method', 'host_label'):
                    if name not in columns:
                        db.execute('ALTER TABLE usage ADD COLUMN ' + name + ' TEXT')
                if 'reused_bytes' not in columns:
                    db.execute('ALTER TABLE usage ADD COLUMN reused_bytes INTEGER NOT NULL DEFAULT 0')
                db.execute('COMMIT')
            except (OSError, sqlite3.Error):
                db.close()
                raise
            self.db = db
        return self.db

    def record(self, login, provider, host, session, title, size, *, method='', host_label='', reused_bytes=0):
        with self.lock:
            if self.closed:
                return
            self._connection().execute(
                'INSERT INTO usage(login,provider,host,session,title,bytes,method,host_label,reused_bytes) VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(login,provider,host,session) DO UPDATE SET bytes=usage.bytes+excluded.bytes,'
                'reused_bytes=usage.reused_bytes+excluded.reused_bytes,'
                "title=coalesce(nullif(excluded.title,''),usage.title),"
                "method=coalesce(nullif(excluded.method,''),usage.method),"
                "host_label=coalesce(nullif(excluded.host_label,''),usage.host_label)",
                (login, provider, host, session, str(title or '')[:200], size, method, host_label[:160], reused_bytes))

    def summary(self, login):
        with self.lock:
            if self.closed or (self.db is None and (self.path is None or not self.path.exists())):
                return {'totalBytes': 0, 'totalReusedBytes': 0, 'sessions': []}
            rows = self._connection().execute('SELECT provider,host,session,title,bytes,method,host_label,reused_bytes FROM usage WHERE login=? '
                                             'ORDER BY bytes DESC,provider,host,session', (login,)).fetchall()
        sessions = [{k: v for k, v in zip(('provider', 'host', 'sessionId', 'title', 'bytes', 'method', 'hostLabel', 'reusedBytes'), row)
                     if v is not None} for row in rows]
        return {'totalBytes': sum(row[4] for row in rows), 'totalReusedBytes': sum(row[7] for row in rows),
                'sessions': sessions}

    def discard(self, login):
        with self.lock:
            if not self.closed and (self.db is not None or self.path and self.path.exists()):
                self._connection().execute('DELETE FROM usage WHERE login=?', (login,))
