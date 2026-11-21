"""Connection credentials: profile-owned encryption on Mac, native vault elsewhere."""
import hashlib
import json
import sys
from pathlib import Path

from bridge.features.notifications.channels import read_json, write_json

FIELDS = ('password', 'passphrase', 'tunnelToken')


class LegacyCredentialsRequired(ValueError):
    pass


def local_values(data_dir, vault):
    vault.bind(Path(data_dir)/'connection-vault.json')
    raw = vault.read()
    if raw is None and any(value == 'gateway-v1' for value in read_json(Path(data_dir)/'connection-vault.json', {}).values()):
        raise ValueError('网关连接凭据文件缺失，已保留索引')
    values = json.loads(raw) if raw is not None else {}
    if not isinstance(values, dict) or any(not isinstance(row, dict) or set(row) - set(FIELDS)
            or any(not isinstance(value, str) for value in row.values()) for row in values.values()):
        raise ValueError('网关连接凭据格式不正确')
    return values


def migrate(data_dir, connection_id):
    if sys.platform != 'darwin':
        raise ValueError('当前系统无需迁移连接凭据')
    # Called only by the explicit desktop migration action, never startup/read.
    from bridge.features.accounts.local_vault import LocalVault
    vault = LocalVault(data_dir, 'connections')
    with vault.locked():
        if connection_id in local_values(data_dir, vault):
            return
        index = read_json(Path(data_dir)/'connection-vault.json', {})
        if not index.get(connection_id) or index[connection_id] == 'gateway-v1':
            raise ValueError('没有可迁移的旧连接凭据')
        value = backend().get_password(service(data_dir), connection_id)
        changes = json.loads(value or '{}')
        if not isinstance(changes, dict) or not changes or set(changes) - set(FIELDS):
            raise ValueError('未找到可确认的旧连接凭据，原记录已保留')
        _save_local(data_dir, connection_id, changes, vault)


def _save_local(data_dir, connection_id, changes, vault):
    if not isinstance(changes, dict) or set(changes) - set(FIELDS) - {'clear'} or any(
            not isinstance(changes.get(key, ''), str) or len(changes.get(key, '')) > 16384
            or '\x00' in changes.get(key, '') for key in FIELDS):
        raise ValueError('连接凭据格式不正确')
    stored = local_values(data_dir, vault)
    index_path = Path(data_dir)/'connection-vault.json'
    index = read_json(index_path, {})
    if any(value == 'gateway-v1' and key not in stored for key, value in index.items()):
        raise ValueError('网关连接凭据不完整，已停止覆盖')
    values = {} if changes.get('clear') else stored.get(connection_id, {})
    values.update({key: changes[key] for key in FIELDS if changes.get(key)})
    if values:
        stored[connection_id] = values
    else:
        stored.pop(connection_id, None)
    vault.write(json.dumps(stored).encode())
    if values:
        index[connection_id] = 'gateway-v1'
    else:
        index.pop(connection_id, None)
    vault.replace(index_path, (json.dumps(index, ensure_ascii=False, indent=2)+'\n').encode(), before_replace=vault.verify)
    vault.related[index_path] = vault.identity(index_path)


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
    index = read_json(Path(data_dir)/'connection-vault.json', {})
    if sys.platform == 'darwin':
        from bridge.features.accounts.local_vault import LocalVault
        try:
            values = local_values(data_dir, LocalVault(data_dir, 'connections'))
            if connection_id in values:
                return values[connection_id]
            if index.get(connection_id) == 'gateway-v1':
                raise ValueError('网关连接凭据文件缺失，请检查资料目录')
            if index.get(connection_id):
                raise LegacyCredentialsRequired('请在电脑端迁移旧连接凭据，或重新填写并保存')
            return {}
        except LegacyCredentialsRequired:
            raise
        except Exception as exc:
            raise ValueError('无法读取网关本地连接凭据，请检查资料目录') from exc
    if not index.get(connection_id):
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
    if sys.platform != 'darwin' and clear and not index.get(connection_id) and not any(changes.get(k) for k in FIELDS):
        return
    if sys.platform == 'darwin':
        from bridge.features.accounts.local_vault import LocalVault
        vault = LocalVault(data_dir, 'connections')
        try:
            with vault.locked():
                _save_local(data_dir, connection_id, changes, vault)
            return
        except Exception as exc:
            raise ValueError('无法保存网关本地连接凭据，请检查资料目录') from exc
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
