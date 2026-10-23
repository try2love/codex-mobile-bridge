"""Gateway-owned explicit project selections, isolated by execution host."""
import hashlib
import json
import threading
from pathlib import Path

from bridge.app.lifecycle import private_json


class Projects:
    def __init__(self, directory):
        self.path = Path(directory)/'projects.json'
        self.lock = threading.RLock()
        self.rows = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def list(self, hosts):
        with self.lock:
            return [dict(row) for row in self.rows.values() if row['host'] in hosts]

    def get(self, key):
        with self.lock:
            return dict(self.rows[key]) if key in self.rows else None

    def save(self, host, label, identity):
        key = host + '|directory:' + hashlib.sha256(identity['cwd'].encode('utf-8')).hexdigest()
        row = {'key': key, 'host': host, 'hostLabel': label, 'cwd': identity['cwd'],
               'name': identity['cwd'].replace('\\', '/').rstrip('/').rsplit('/', 1)[-1] or identity['cwd'],
               'directoryIdentity': identity}
        with self.lock:
            updated = {**self.rows, key: row}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            private_json(self.path, updated)
            self.rows = updated
        return dict(row)
