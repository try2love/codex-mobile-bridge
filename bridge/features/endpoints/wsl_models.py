"""Durable, execution-identity-bound model metadata for existing WSL daemons."""
import copy
import sqlite3
import threading
import time

from bridge.clients.codex.catalog import ModelStore, MODEL_REFRESH_SECONDS
from bridge.clients.codex.ipc import IPCError
from bridge.clients.codex.remote import RemoteUnavailable


class WslModelCatalog:
    def __init__(self, entry):
        self.entry = entry
        self.store = ModelStore(entry.data_dir)
        self.lock = threading.RLock()
        self.reads = {}
        self.timers = {}
        self.closed = False

    def identity(self, metadata, thread, config):
        runtime = self.entry.runtime()
        account = runtime.request('account/read', {'refreshToken': False}).get('account') or {}
        daemon = getattr(self.entry, 'daemon_identity', {})
        # Only a digest leaves WSL; raw auth/config/environment never enters
        # the gateway model database, logs, or a browser response.
        source = 'import hashlib,json\nfrom pathlib import Path\nh=hashlib.sha256()\n'
        source += 'paths=[Path(' + repr(self.entry.row['home']) + ')/n for n in ("auth.json","config.toml")]\n'
        profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
        catalog = ({**config, **profile}).get('model_catalog_json')
        if isinstance(catalog, str) and catalog.startswith('/'):
            source += 'paths.append(Path(' + repr(catalog) + '))\n'
        if isinstance(daemon.get('pid'), int):
            source += 'paths.append(Path("/proc")/' + repr(str(daemon['pid'])) + '/"environ")\n'
        source += 'for p in paths:\n h.update(str(p).encode());h.update(b"\\0")\n try:\n  h.update(p.read_bytes())\n except FileNotFoundError:\n  h.update(b"missing")\nprint(json.dumps({"digest":h.hexdigest()}))\n'
        digest = self.entry.read(self.entry.row['distribution'], source)['digest']
        return ModelStore.key('wsl-cli-models-v1', self.entry.host, self.entry.row['distribution'],
            self.entry.row['user'], self.entry.row['home'], metadata['cwd'],
            thread.get('modelProvider'), daemon, config, account, digest)

    def _record(self, identity):
        try:
            return self.store.read(identity)
        except (OSError, sqlite3.DatabaseError, ValueError):
            return None

    @staticmethod
    def _public(record, identity, *, error=None):
        at, value = record
        result = {**copy.deepcopy(value), 'cache': {
            'state': 'stale' if error or time.time() - at >= MODEL_REFRESH_SECONDS else 'fresh',
            'updatedAt': at, 'identity': identity}}
        if error:
            result['modelError'] = error
        return result

    def get(self, identifier, metadata, thread, config, refresh, fetch):
        try:
            identity = self.identity(metadata, thread, config)
        except (IPCError, RemoteUnavailable, KeyError, OSError, ValueError):
            # Identity uncertainty cannot reuse another account's catalog.
            return fetch()
        record = self._record(identity)
        if record and not refresh:
            self._schedule(identity, identifier, record[0])
            return self._public(record, identity)
        with self.lock:
            read_lock = self.reads.setdefault(identity, threading.Lock())
        with read_lock:
            record = self._record(identity)
            if record and not refresh:
                return self._public(record, identity)
            try:
                value = fetch()
                if self.identity(metadata, thread, config) != identity:
                    raise ValueError('账号身份已变化，请重新读取模型列表')
            except (IPCError, RemoteUnavailable, OSError):
                if record:
                    return self._public(record, identity, error='模型列表刷新失败，已保留上次结果')
                raise
            stable = {key: copy.deepcopy(v) for key, v in value.items()
                      if key not in ('currentModel', 'currentEffort')}
            if value.get('modelError'):
                return self._public(record, identity, error=value['modelError']) if record else value
            try:
                at = self.store.write(identity, stable)
            except (OSError, sqlite3.DatabaseError):
                return value
            self._schedule(identity, identifier, at, replace=True)
            return self._public((at, stable), identity)

    def _schedule(self, identity, identifier, at, replace=False):
        if identifier is None:
            return  # Creation catalogs have no session to watch or activate.
        with self.lock:
            if replace and identity in self.timers:
                self.timers.pop(identity).cancel()
            if self.closed or identity in self.timers:
                return
            def due():
                with self.lock:
                    self.timers.pop(identity, None)
                try:
                    if not self.closed and not self.entry.closed.is_set():
                        runtime = self.entry.runtime()
                        metadata = self.entry.store.get(identifier)
                        thread = runtime.request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
                        config = runtime.request('config/read', {'includeLayers': False, 'cwd': metadata['cwd']}).get('config') or {}
                        if self.identity(metadata, thread, config) == identity:
                            self.entry.catalog(identifier, refresh=True, kind='models')
                except (IPCError, RemoteUnavailable, OSError, ValueError):
                    self._schedule(identity, identifier, time.time() - MODEL_REFRESH_SECONDS + 300)
            timer = threading.Timer(max(1, at + MODEL_REFRESH_SECONDS - time.time()), due)
            timer.daemon = True
            self.timers[identity] = timer
            timer.start()

    def close(self):
        with self.lock:
            self.closed = True
            for timer in self.timers.values():
                timer.cancel()
            self.timers.clear()
