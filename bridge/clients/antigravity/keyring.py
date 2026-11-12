"""Cross-platform Secret Service / Keyring integration for Antigravity.

Antigravity >= 2.0.0 on Linux/macOS/Windows stores its active OAuth session token
in the OS Keyring / Secret Service ('service': 'gemini', 'username': 'antigravity')
and optionally in ~/.gemini/antigravity-cli/antigravity-oauth-token.

References & Acknowledgements:
- Schema attributes and D-Bus integration follow freedesktop.org Secret Service specifications.
- Keyring sync conventions inspired by community implementations including AntiGravity Switch (MIT License).
"""
import base64
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = 'gemini'
USERNAME = 'antigravity'
KEYRING_LABEL = "Password for 'antigravity' on 'gemini'"


def _linux_get_secret() -> str | None:
    try:
        import gi
        gi.require_version('Secret', '1')
        from gi.repository import Secret

        schema = Secret.Schema.new(
            'org.freedesktop.Secret.Generic',
            Secret.SchemaFlags.NONE,
            {
                'service': Secret.SchemaAttributeType.STRING,
                'username': Secret.SchemaAttributeType.STRING,
            },
        )
        val = Secret.password_lookup_sync(
            schema, {'service': SERVICE_NAME, 'username': USERNAME}, None
        )
        if val:
            return val
    except Exception as exc:
        logger.debug('gi.repository.Secret lookup error: %s', exc)

    try:
        res = subprocess.run(
            ['secret-tool', 'lookup', 'service', SERVICE_NAME, 'username', USERNAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass

    return None


def _linux_set_secret(payload: str) -> bool:
    success = False
    try:
        import gi
        gi.require_version('Secret', '1')
        from gi.repository import Secret

        schema = Secret.Schema.new(
            'org.freedesktop.Secret.Generic',
            Secret.SchemaFlags.NONE,
            {
                'service': Secret.SchemaAttributeType.STRING,
                'username': Secret.SchemaAttributeType.STRING,
            },
        )
        attrs = {'service': SERVICE_NAME, 'username': USERNAME}

        # Store in DEFAULT collection
        ok_def = Secret.password_store_sync(
            schema, attrs, Secret.COLLECTION_DEFAULT, KEYRING_LABEL, payload, None
        )
        if ok_def:
            success = True

        # Store in 'login' collection if present (prevents agy CLI and app keyring divergence)
        try:
            Secret.password_store_sync(schema, attrs, 'login', KEYRING_LABEL, payload, None)
        except Exception:
            pass

    except Exception as exc:
        logger.debug('gi.repository.Secret store error: %s', exc)

    if success:
        return True

    try:
        for col_args in [['--collection=login', f'--label={KEYRING_LABEL}'], [f'--label={KEYRING_LABEL}']]:
            subprocess.run(
                ['secret-tool', 'store', *col_args, 'service', SERVICE_NAME, 'username', USERNAME],
                input=payload.encode('utf-8'),
                check=True,
                timeout=5,
            )
        return True
    except Exception:
        pass

    return False


def _macos_get_secret() -> str | None:
    try:
        res = subprocess.run(
            ['security', 'find-generic-password', '-s', SERVICE_NAME, '-a', USERNAME, '-w'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            val = res.stdout.strip()
            if val.startswith('go-keyring-base64:'):
                encoded = val[len('go-keyring-base64:'):]
                val = base64.b64decode(encoded).decode('utf-8', errors='replace')
            return val
    except Exception as exc:
        logger.debug('macOS Keychain lookup error: %s', exc)
    return None


def _macos_set_secret(payload: str) -> bool:
    try:
        encoded = base64.b64encode(payload.encode('utf-8')).decode('ascii')
        full_val = f'go-keyring-base64:{encoded}'
        subprocess.run(
            ['security', 'delete-generic-password', '-s', SERVICE_NAME, '-a', USERNAME],
            capture_output=True,
            timeout=5,
        )
        res = subprocess.run(
            [
                'security',
                'add-generic-password',
                '-s',
                SERVICE_NAME,
                '-a',
                USERNAME,
                '-w',
                full_val,
                '-A',
            ],
            capture_output=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception as exc:
        logger.debug('macOS Keychain store error: %s', exc)
        return False


def _windows_get_secret() -> str | None:
    try:
        import keyring
        return keyring.get_password(SERVICE_NAME, USERNAME)
    except Exception:
        return None


def _windows_set_secret(payload: str) -> bool:
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, USERNAME, payload)
        return True
    except Exception:
        return False


def get_keyring_secret() -> str | None:
    """Retrieve the raw JSON secret payload from the system Keyring."""
    if sys.platform.startswith('linux'):
        return _linux_get_secret()
    if sys.platform == 'darwin':
        return _macos_get_secret()
    if sys.platform == 'win32':
        return _windows_get_secret()
    return None


def set_keyring_secret(payload: str) -> bool:
    """Persist the JSON secret payload into the system Keyring."""
    if not payload:
        return False
    if sys.platform.startswith('linux'):
        return _linux_set_secret(payload)
    if sys.platform == 'darwin':
        return _macos_set_secret(payload)
    if sys.platform == 'win32':
        return _windows_set_secret(payload)
    return False


def get_cli_tokens() -> dict[str, str]:
    """Retrieve any on-disk CLI / standalone OAuth token files."""
    home = Path.home()
    tokens = {}

    cli_token_path = home / '.gemini' / 'antigravity-cli' / 'antigravity-oauth-token'
    if cli_token_path.is_file():
        try:
            tokens['antigravity-oauth-token'] = cli_token_path.read_text(encoding='utf-8')
        except Exception:
            pass

    jetski_token_path = home / '.gemini' / 'jetski-standalone-oauth-token'
    if jetski_token_path.is_file():
        try:
            tokens['jetski-standalone-oauth-token'] = jetski_token_path.read_text(encoding='utf-8')
        except Exception:
            pass

    return tokens


def set_cli_tokens(tokens: dict[str, str]) -> None:
    """Restore on-disk CLI / standalone OAuth token files."""
    if not isinstance(tokens, dict):
        return

    home = Path.home()

    if 'antigravity-oauth-token' in tokens:
        cli_dir = home / '.gemini' / 'antigravity-cli'
        if cli_dir.is_dir():
            target = cli_dir / 'antigravity-oauth-token'
            try:
                content = tokens['antigravity-oauth-token']
                tmp_target = cli_dir / f'.tmp_tok_{os.getpid()}'
                tmp_target.write_text(content, encoding='utf-8')
                tmp_target.chmod(0o600)
                tmp_target.replace(target)
            except Exception as exc:
                logger.debug('Failed to write antigravity-oauth-token: %s', exc)

    if 'jetski-standalone-oauth-token' in tokens:
        gemini_dir = home / '.gemini'
        if gemini_dir.is_dir():
            target = gemini_dir / 'jetski-standalone-oauth-token'
            try:
                content = tokens['jetski-standalone-oauth-token']
                tmp_target = gemini_dir / f'.tmp_jtok_{os.getpid()}'
                tmp_target.write_text(content, encoding='utf-8')
                tmp_target.chmod(0o600)
                tmp_target.replace(target)
            except Exception as exc:
                logger.debug('Failed to write jetski-standalone-oauth-token: %s', exc)


def parse_secret_identity(secret_json: str | None) -> dict:
    """Extract email, auth method, and token expiry from a Keyring payload."""
    if not secret_json:
        return {}
    try:
        data = json.loads(secret_json)
        email = data.get('id_token_email') or ''
        auth_method = data.get('auth_method') or 'consumer'
        token_data = data.get('token') or {}
        access_token = token_data.get('access_token') or ''
        refresh_token = token_data.get('refresh_token') or ''
        expiry = token_data.get('expiry') or ''
        return {
            'email': email,
            'authMethod': auth_method,
            'hasToken': bool(access_token or refresh_token),
            'expiry': expiry,
        }
    except Exception:
        return {}
