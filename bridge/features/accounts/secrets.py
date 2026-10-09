"""Connection credentials in the native OS vault; no plaintext fallback."""
import hashlib
import json
import sys
from pathlib import Path

from bridge.features.notifications.channels import read_json, write_json

FIELDS = ('password', 'passphrase', 'tunnelToken')


def backend():
    try:
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
        elif sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            from keyring.backends.SecretService import Keyring
        return Keyring()
    except Exception as exc:
        raise ValueError('系统凭据存储不可用，请解锁钥匙串或启用 Secret Service；不会明文保存密码。') from exc


def service(data_dir):
    return 'io.github.try2love.codexmobilebridge.' + hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:24]


def read(data_dir, connection_id):
    if not read_json(Path(data_dir)/'connection-vault.json', {}).get(connection_id):
        return {}
    try:
        value = backend().get_password(service(data_dir), connection_id)
        return json.loads(value) if value else {}
    except Exception as exc:
        raise ValueError('无法读取连接凭据，请解锁系统凭据存储后重试。') from exc


def save(data_dir, connection_id, changes):
    if not isinstance(changes, dict) or set(changes) - set(FIELDS) - {'clear'}:
        raise ValueError('连接凭据格式不正确')
    for key in FIELDS:
        value = changes.get(key, '')
        if not isinstance(value, str) or len(value) > 16384 or '\x00' in value:
            raise ValueError('连接凭据格式不正确')
    clear = changes.get('clear', False)
    if not isinstance(clear, bool):
        raise ValueError('清除凭据开关格式不正确')
    index_path = Path(data_dir)/'connection-vault.json'
    index = read_json(index_path, {})
    if clear and not index.get(connection_id) and not any(changes.get(k) for k in FIELDS):
        return
    vault = backend()
    try:
        old = vault.get_password(service(data_dir), connection_id)
        values = {} if clear else json.loads(old or '{}')
        values.update({key: changes[key] for key in FIELDS if changes.get(key)})
        if values:
            vault.set_password(service(data_dir), connection_id, json.dumps(values))
        elif old:
            vault.delete_password(service(data_dir), connection_id)
        if values:
            index[connection_id] = True
        else:
            index.pop(connection_id, None)
        write_json(index_path, index)
    except Exception as exc:
        raise ValueError('无法保存连接凭据，请解锁系统凭据存储后重试。') from exc
