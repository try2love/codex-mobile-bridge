"""Private, same-machine Antigravity account login snapshots.

The manager owns the lifecycle gate: switch / restore runs safely when idle or
stopped. Electron's LevelDB stores and Cookies are snapshotted alongside
the Linux Secret Service (Keyring) OAuth token and CLI session configuration,
allowing instant multi-account switching without remote session revocation or logout.
"""
import copy
import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from bridge.clients.antigravity.keyring import (
    get_cli_tokens,
    get_keyring_secret,
    parse_secret_identity,
    set_cli_tokens,
    set_keyring_secret,
)
from bridge.features.accounts.accounts import private_bytes, private_json

logger = logging.getLogger(__name__)

CONFIG_ITEMS = (
    'app_storage.json',
    'Preferences',
    'Local State',
    'Cookies',
    'Cookies-journal',
    'Cookies-wal',
    'Cookies-shm',
    'Network Persistent State',
    'Local Storage',
    'Session Storage',
)

AUTH_ITEMS = (
    'credential-user-scope',
    'plugin-secrets.json',
)


def _safe(path: Path) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Antigravity 帳號目錄不能是符號鏈接')


def _json(path: Path, default=None):
    _safe(path)
    if not path.exists():
        return {} if default is None else default
    if path.is_symlink():
        raise ValueError('Antigravity 帳號檔案不能是符號鏈接')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else default
    except Exception:
        return default


def _copy(source: Path, target: Path) -> None:
    """Only regular, owner-private files; never follow a profile symlink."""
    _safe(source)
    _safe(target)
    if source.is_symlink():
        raise ValueError('Antigravity 帳號檔案不能是符號鏈接')
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.chmod(0o700)
        for child in source.iterdir():
            _copy(child, target / child.name)
    elif source.is_file():
        private_bytes(target, source.read_bytes())


def _remove(path: Path) -> None:
    _safe(path)
    if path.is_symlink():
        raise ValueError('Antigravity 帳號檔案不能是符號鏈接')
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _stamp(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class AntigravityAccounts:
    """Manage multiple Antigravity account profiles on the local machine."""

    def __init__(self, directory, config_dir=None, auth_dir=None, metadata=None, keyring_enabled=None):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        user_home = Path.home()
        default_config = user_home / '.config' / 'Antigravity'
        default_auth = user_home / '.config' / 'antigravity'

        self.config_dir = (
            Path(config_dir).expanduser().resolve()
            if config_dir
            else default_config
        )
        self.auth_dir = (
            Path(auth_dir).expanduser().resolve()
            if auth_dir
            else default_auth
        )
        self.metadata = metadata
        self.lock = threading.RLock()
        self.keyring_enabled = (self.config_dir == default_config) if keyring_enabled is None else bool(keyring_enabled)

    def _index_path(self) -> Path:
        return self.directory / 'index.json'

    def _index(self) -> dict:
        data = _json(self._index_path(), default=None)
        if not isinstance(data, dict):
            data = {'accounts': []}
        return data

    def _save_index(self, data: dict) -> None:
        private_json(self._index_path(), data)

    def _current_identity(self) -> dict:
        """Inspect Keyring, on-disk config or fallback to RPC to determine current account identity."""
        # Priority 1: System Keyring (Single Source of Truth on desktop Antigravity >= 2.0.0)
        if self.keyring_enabled:
            try:
                secret_str = get_keyring_secret()
                if secret_str:
                    parsed = parse_secret_identity(secret_str)
                    if parsed.get('email'):
                        return {
                            'email': parsed['email'],
                            'name': parsed['email'].split('@')[0],
                            'tier': 'Google AI',
                            'authMethod': parsed.get('authMethod', 'consumer'),
                        }
            except Exception as exc:
                logger.debug('Keyring identity inspection failed: %s', exc)

        # Priority 2: config_dir / app_storage.json
        storage_path = self.config_dir / 'app_storage.json'
        if storage_path.is_file():
            storage = _json(storage_path, default={})
            login = storage.get('jetski.onboarding.lastLoginUsername')
            if login and isinstance(login, str) and login.strip():
                email = login.strip()
                return {'email': email, 'name': email.split('@')[0], 'tier': 'Google AI'}

        # Priority 3: auth_dir / plugin-secrets.json & credential-user-scope
        scope_path = self.auth_dir / 'credential-user-scope'
        secrets_path = self.auth_dir / 'plugin-secrets.json'
        scope = scope_path.read_text(encoding='utf-8').strip() if scope_path.is_file() else ''
        if secrets_path.is_file():
            secrets = _json(secrets_path, default={})
            for k, v in secrets.items():
                if k.endswith('_IDENTITY') and (not scope or scope in k) and isinstance(v, str):
                    email = v.strip()
                    return {'email': email, 'name': email.split('@')[0], 'tier': 'Google AI'}

        # Priority 4: RPC metadata callback if active
        if callable(self.metadata):
            try:
                res = self.metadata()
                if isinstance(res, dict) and res.get('account'):
                    acc = res['account']
                    email = str(acc.get('email') or '').strip()
                    name = str(acc.get('name') or '').strip()
                    tier = str(acc.get('tier') or '').strip()
                    if email:
                        return {'email': email, 'name': name or email.split('@')[0], 'tier': tier}
            except Exception:
                pass

        return {'email': '', 'name': 'Antigravity User', 'tier': 'Google AI'}

    def _current_fingerprint(self) -> str:
        """Generate a deterministic hash representing current on-disk credentials and Keyring."""
        parts = []

        if self.keyring_enabled:
            try:
                secret_str = get_keyring_secret()
                if secret_str:
                    parts.append(secret_str)
            except Exception:
                pass

        scope_path = self.auth_dir / 'credential-user-scope'
        secrets_path = self.auth_dir / 'plugin-secrets.json'
        storage_path = self.config_dir / 'app_storage.json'

        if scope_path.is_file():
            parts.append(scope_path.read_text(encoding='utf-8').strip())
        if secrets_path.is_file():
            parts.append(secrets_path.read_text(encoding='utf-8').strip())
        if storage_path.is_file():
            parts.append(storage_path.read_text(encoding='utf-8').strip())
        return hashlib.sha256('||'.join(parts).encode('utf-8')).hexdigest()

    def public(self) -> dict:
        with self.lock:
            index = self._index()
            rows = copy.deepcopy(index.get('accounts', []))
            current = self._current_identity()
            current_fp = self._current_fingerprint()

            active_id = None
            for r in rows:
                if current.get('email') and r.get('email') == current['email']:
                    active_id = r['id']
                    break
                if current_fp and r.get('fingerprint') == current_fp:
                    active_id = r['id']
                    break

            if not active_id and getattr(self, '_last_switched_id', None):
                if any(r['id'] == self._last_switched_id for r in rows):
                    active_id = self._last_switched_id

            public_rows = [
                {
                    'id': r['id'],
                    'name': r.get('name') or 'Antigravity 帳號',
                    'email': r.get('email', ''),
                    'kind': 'official',
                    'saved': True,
                    'tier': r.get('tier', 'Google AI Pro'),
                }
                for r in rows
            ]

            if current.get('email') and not active_id:
                public_rows.insert(
                    0,
                    {
                        'id': 'current',
                        'name': current.get('name') or 'Antigravity 帳號',
                        'email': current.get('email', ''),
                        'kind': 'official',
                        'saved': False,
                        'tier': current.get('tier', 'Google AI Pro'),
                    },
                )
                active_id = 'current'

            return {
                'accounts': public_rows,
                'activeId': active_id or (public_rows[0]['id'] if public_rows else None),
                'activeIds': [active_id] if active_id else [],
                'canImport': True,
                'canSwitch': True,
                'loginAccount': {
                    'id': active_id or 'current',
                    'name': current.get('name') or 'Antigravity 帳號',
                    'email': current.get('email', ''),
                },
            }

    def details(self, identifier: str, refresh: bool = False) -> dict:
        with self.lock:
            pub = self.public()
            row = next((r for r in pub['accounts'] if r['id'] == identifier), None)
            if not row:
                raise ValueError('所選 Antigravity 帳號不存在')

            details_data = {
                'provider': 'antigravity',
                'name': row.get('name', ''),
                'email': row.get('email', ''),
                'tier': row.get('tier', 'Google AI Pro'),
                'saved': row.get('saved', True),
            }

            # If requesting details for active account, pull live quota from metadata
            if row['id'] == pub.get('activeId') and callable(self.metadata):
                try:
                    live = self.metadata()
                    if isinstance(live, dict):
                        details_data['quota'] = live.get('quota', {})
                        details_data['credits'] = live.get('credits', {})
                except Exception:
                    pass

            return {**pub, 'details': details_data}

    def import_current(self, name: str = '') -> dict:
        """Snapshot current credentials into a profile without stopping the application."""
        with self.lock:
            current = self._current_identity()
            if not current.get('email'):
                raise ValueError('未偵測到已登入的 Antigravity 帳號，請先在桌面端登入')

            fingerprint = self._current_fingerprint()
            index = self._index()
            rows = index.get('accounts', [])

            # Check if this account was already saved
            existing = next((r for r in rows if r.get('email') == current['email']), None)
            identifier = existing['id'] if existing else uuid.uuid4().hex

            profile_dir = self.directory / 'profiles' / identifier
            staged_dir = self.directory / 'staging' / uuid.uuid4().hex
            staged_dir.mkdir(parents=True, mode=0o700)

            try:
                # 1. Copy config files
                staged_config = staged_dir / 'config'
                staged_config.mkdir(parents=True, mode=0o700)
                for item in CONFIG_ITEMS:
                    src = self.config_dir / item
                    if src.exists():
                        _copy(src, staged_config / item)

                # 2. Copy auth files
                staged_auth = staged_dir / 'auth'
                staged_auth.mkdir(parents=True, mode=0o700)
                for item in AUTH_ITEMS:
                    src = self.auth_dir / item
                    if src.exists():
                        _copy(src, staged_auth / item)

                # 3. Snapshot Keyring & CLI tokens
                has_keyring = False
                if self.keyring_enabled:
                    staged_keyring = staged_dir / 'keyring'
                    staged_keyring.mkdir(parents=True, mode=0o700)
                    secret_str = get_keyring_secret()
                    if secret_str:
                        private_bytes(staged_keyring / 'secret.json', secret_str.encode('utf-8'))
                        has_keyring = True

                    cli_tokens = get_cli_tokens()
                    if cli_tokens:
                        private_json(staged_keyring / 'cli_tokens.json', cli_tokens)

                manifest = {
                    'id': identifier,
                    'name': (name or current.get('name') or current['email']).strip(),
                    'email': current['email'],
                    'tier': current.get('tier', 'Google AI Pro'),
                    'kind': 'official',
                    'fingerprint': fingerprint,
                    'hasKeyring': has_keyring,
                    'createdAt': time.time(),
                }
                private_json(staged_dir / 'manifest.json', manifest)

                # Atomically replace profile directory
                profile_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if profile_dir.exists():
                    _remove(profile_dir)
                staged_dir.rename(profile_dir)

                # Update index
                row = {
                    'id': identifier,
                    'name': manifest['name'],
                    'email': manifest['email'],
                    'tier': manifest['tier'],
                    'kind': 'official',
                    'fingerprint': fingerprint,
                    'saved': True,
                }
                index['accounts'] = [r for r in rows if r['id'] != identifier] + [row]
                self._save_index(index)
            finally:
                if staged_dir.exists():
                    _remove(staged_dir)

            return self.public()

    def _account_profile(self, identifier: str) -> tuple[dict, Path]:
        index = self._index()
        row = next((r for r in index.get('accounts', []) if r['id'] == identifier), None)
        if not row:
            raise ValueError('所選 Antigravity 帳號不存在')
        profile_dir = self.directory / 'profiles' / identifier
        if not profile_dir.is_dir():
            raise ValueError('Antigravity 帳號快照遺失，請重新保存')
        return row, profile_dir

    def restore(self, identifier: str) -> str:
        """Apply a saved login snapshot; return a private rollback ID."""
        with self.lock:
            row, snapshot = self._account_profile(identifier)
            manifest = _json(snapshot / 'manifest.json', default={})
            if manifest.get('id') != identifier:
                raise ValueError('Antigravity 帳號快照校驗失敗')

            # Create backup for rollback
            token = uuid.uuid4().hex
            backup_root = self.directory / 'transactions' / token
            backup_root.mkdir(parents=True, mode=0o700)

            # 1. Backup current config & auth files
            b_config = backup_root / 'config'
            b_config.mkdir(parents=True, mode=0o700)
            for item in CONFIG_ITEMS:
                src = self.config_dir / item
                if src.exists():
                    _copy(src, b_config / item)

            b_auth = backup_root / 'auth'
            b_auth.mkdir(parents=True, mode=0o700)
            for item in AUTH_ITEMS:
                src = self.auth_dir / item
                if src.exists():
                    _copy(src, b_auth / item)

            # 2. Backup current Keyring & CLI tokens
            if self.keyring_enabled:
                b_keyring = backup_root / 'keyring'
                b_keyring.mkdir(parents=True, mode=0o700)
                curr_secret = get_keyring_secret()
                if curr_secret:
                    private_bytes(b_keyring / 'secret.json', curr_secret.encode('utf-8'))
                curr_cli = get_cli_tokens()
                if curr_cli:
                    private_json(b_keyring / 'cli_tokens.json', curr_cli)

            private_json(backup_root / 'complete.json', {'complete': True, 'targetId': identifier})

            try:
                # Restore config items
                snap_config = snapshot / 'config'
                if snap_config.is_dir():
                    for item in CONFIG_ITEMS:
                        target = self.config_dir / item
                        src = snap_config / item
                        if src.exists():
                            if target.exists():
                                _remove(target)
                            _copy(src, target)

                # Restore auth items
                snap_auth = snapshot / 'auth'
                if snap_auth.is_dir():
                    for item in AUTH_ITEMS:
                        target = self.auth_dir / item
                        src = snap_auth / item
                        if src.exists():
                            if target.exists():
                                _remove(target)
                            _copy(src, target)

                # Restore Keyring secret & CLI tokens
                if self.keyring_enabled:
                    snap_keyring = snapshot / 'keyring'
                    if snap_keyring.is_dir():
                        secret_file = snap_keyring / 'secret.json'
                        if secret_file.is_file():
                            target_secret = secret_file.read_text(encoding='utf-8')
                            set_keyring_secret(target_secret)

                        cli_file = snap_keyring / 'cli_tokens.json'
                        if cli_file.is_file():
                            cli_data = _json(cli_file, default={})
                            set_cli_tokens(cli_data)

            except Exception as exc:
                logger.error('Antigravity restore failed: %s', exc)
                self.rollback(token)
                raise ValueError('Antigravity 帳號切換失敗，已自動回滾') from None

            self._last_switched_id = identifier
            return token

    def rollback(self, token: str) -> None:
        with self.lock:
            self._last_switched_id = None
            backup_root = self.directory / 'transactions' / token
            if not backup_root.is_dir():
                raise ValueError('無效的回滾事務標識')

            b_config = backup_root / 'config'
            if b_config.is_dir():
                for item in CONFIG_ITEMS:
                    target = self.config_dir / item
                    src = b_config / item
                    if src.exists():
                        if target.exists():
                            _remove(target)
                        _copy(src, target)

            b_auth = backup_root / 'auth'
            if b_auth.is_dir():
                for item in AUTH_ITEMS:
                    target = self.auth_dir / item
                    src = b_auth / item
                    if src.exists():
                        if target.exists():
                            _remove(target)
                        _copy(src, target)

            if self.keyring_enabled:
                b_keyring = backup_root / 'keyring'
                if b_keyring.is_dir():
                    secret_file = b_keyring / 'secret.json'
                    if secret_file.is_file():
                        set_keyring_secret(secret_file.read_text(encoding='utf-8'))
                    cli_file = b_keyring / 'cli_tokens.json'
                    if cli_file.is_file():
                        set_cli_tokens(_json(cli_file, default={}))

    def commit(self, token: str) -> None:
        with self.lock:
            backup_root = self.directory / 'transactions' / token
            if backup_root.exists():
                _remove(backup_root)

    def rename(self, identifier: str, name: str) -> dict:
        clean_name = str(name or '').strip()
        if not (1 <= len(clean_name) <= 100):
            raise ValueError('帳號名稱長度應為 1–100 字')
        with self.lock:
            index = self._index()
            rows = index.get('accounts', [])
            row = next((r for r in rows if r['id'] == identifier), None)
            if not row:
                raise ValueError('所選 Antigravity 帳號不存在')
            row['name'] = clean_name
            self._save_index(index)

            # Update manifest in profile
            profile_dir = self.directory / 'profiles' / identifier
            manifest_file = profile_dir / 'manifest.json'
            if manifest_file.is_file():
                manifest = _json(manifest_file, default={})
                manifest['name'] = clean_name
                private_json(manifest_file, manifest)

            return self.public()

    def remove(self, identifier: str) -> dict:
        with self.lock:
            index = self._index()
            rows = index.get('accounts', [])
            index['accounts'] = [r for r in rows if r['id'] != identifier]
            self._save_index(index)

            profile_dir = self.directory / 'profiles' / identifier
            if profile_dir.exists():
                _remove(profile_dir)

            return self.public()

    def edit(self, identifier: str) -> dict:
        row, _ = self._account_profile(identifier)
        return {'id': row['id'], 'name': row.get('name', ''), 'kind': 'official'}

    def api_credentials(self, identifier: str) -> dict:
        raise ValueError('Antigravity 帳號不支援獨立 API 憑證導出')
