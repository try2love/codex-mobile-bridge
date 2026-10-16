import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path


def password_record(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000, dklen=32)
    return {"algorithm": "pbkdf2-sha256", "iterations": 600000, "salt": salt.hex(), "hash": digest.hex()}


class Auth:
    COOKIE = "codex_mobile_session"
    SESSION_SECONDS = 12 * 3600
    REMEMBER_SECONDS = 30 * 24 * 3600

    def __init__(self, config, session_file=None):
        self.config = config
        self.sessions = {}
        self.failures = {}
        self.lock = threading.Lock()
        self.session_file = Path(session_file) if session_file else None
        self.auth_version = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        self._load_sessions()

    @staticmethod
    def token_key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def _load_sessions(self):
        if not self.session_file:
            return
        try:
            if self.session_file.stat().st_size > 1024 * 1024:
                return
            saved = json.loads(self.session_file.read_text(encoding='utf-8'))
            if saved.get('authVersion') != self.auth_version:
                return
            now = time.time()
            for key, session in saved['sessions'].items():
                if (isinstance(key, str) and len(key) == 64 and all(c in '0123456789abcdef' for c in key)
                        and isinstance(session, dict) and session.get('remember') is True
                        and isinstance(session.get('expires'), (int, float))
                        and now < session['expires'] <= now + self.REMEMBER_SECONDS
                        and isinstance(session.get('csrf'), str) and len(session['csrf']) == 43):
                    self.sessions[key] = session
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.sessions = {}

    def _save_sessions(self):
        if not self.session_file:
            return
        saved = {key: value for key, value in self.sessions.items()
                 if value.get('remember') and value['expires'] > time.time()}
        self.session_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.session_file.parent,
                                             prefix='remember-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                temporary.chmod(0o600)
                json.dump({'authVersion': self.auth_version, 'sessions': saved}, file)
            os.replace(temporary, self.session_file)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def login(self, username, password, address, remember=False):
        if not isinstance(remember, bool):
            raise ValueError('记住登录选项无效')
        now = time.time()
        with self.lock:
            recent = [t for t in self.failures.get(address, []) if now - t < 300]
            self.failures[address] = recent
            if len(recent) >= 8:
                raise PermissionError("尝试次数过多，请 5 分钟后再试")
            # Reserve the attempt before hashing, so parallel attempts cannot bypass the limit.
            recent.append(now)
        if self.config.get("mode", "password") != "none":
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(self.config["salt"]), self.config["iterations"], dklen=32)
            valid = hmac.compare_digest(digest.hex(), self.config["hash"]) and hmac.compare_digest(username.encode(), self.config["username"].encode())
            if not valid:
                raise PermissionError("账号或密码不正确")
        with self.lock:
            self.failures.pop(address, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            token = secrets.token_urlsafe(32)
            session = {"csrf": secrets.token_urlsafe(32),
                       "expires": now + (self.REMEMBER_SECONDS if remember else self.SESSION_SECONDS),
                       "remember": remember}
            key = self.token_key(token)
            self.sessions[key] = session
            if remember:
                try:
                    self._save_sessions()
                except OSError as exc:
                    self.sessions.pop(key, None)
                    raise ValueError('无法保存登录状态，请取消“记住登录”后重试。') from exc
            return token, session

    def get(self, token):
        with self.lock:
            key = self.token_key(token)
            session = self.sessions.get(key)
            if session and session["expires"] > time.time():
                return session
            self.sessions.pop(key, None)
            return None

    def logout(self, token):
        with self.lock:
            session = self.sessions.pop(self.token_key(token), None)
            if session and session.get('remember'):
                self._save_sessions()
