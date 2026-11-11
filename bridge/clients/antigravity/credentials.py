"""The native Go keyring contract; no plugin secrets or Electron profile data."""
import base64
import sys


class NativeCredentials:
    def __init__(self):
        if sys.platform == 'darwin':
            from bridge.platforms.macos import credential_vault
        elif sys.platform == 'win32':
            from bridge.platforms.windows import credential_vault
        elif sys.platform == 'linux':
            from bridge.platforms.linux import credential_vault
        else:
            raise ValueError('当前系统暂不支持 Antigravity 账号保存')
        self.vault = credential_vault

    def read(self):
        value = self.vault.read('gemini', 'antigravity')
        if value is None:
            return None
        if sys.platform == 'darwin':
            if value.startswith(b'go-keyring-base64:'):
                value = base64.b64decode(value[len(b'go-keyring-base64:'):], validate=True)
            elif value.startswith(b'go-keyring-encoded:'):
                value = bytes.fromhex(value[len(b'go-keyring-encoded:'):].decode('ascii'))
        return value.decode('utf-8')

    def write(self, value):
        content = value.encode('utf-8') if value is not None else None
        if content is not None and sys.platform == 'darwin':
            content = b'go-keyring-base64:'+base64.b64encode(content)
        self.vault.write('gemini', 'antigravity', content)
