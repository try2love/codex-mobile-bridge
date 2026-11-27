"""Profile-owned authenticated credentials, independent of the application signature."""
import os
import hashlib
import json
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

MAGIC = b'CMB1'


class LocalVault:
    def __init__(self, directory, purpose):
        self.directory = Path(directory)/'.credential-vault'
        self.purpose = purpose
        self.preimage = None
        self.read_bound = False
        self.key_image = None
        self.related = {}

    def folder(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('网关凭据目录权限不正确，请检查当前用户的私有目录')
        return self.directory

    def private_read(self, path, *, with_identity=False):
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('网关凭据文件权限不正确')
            value = stream.read()
            identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, hashlib.sha256(value).digest())
            return (value, identity) if with_identity else value

    def identity(self, path):
        try:
            return self.private_read(path, with_identity=True)[1]
        except FileNotFoundError:
            return None

    def bind(self, path):
        identity = self.identity(path)
        if path in self.related and self.related[path] != identity:
            raise ValueError('网关凭据索引已被其他进程修改，已停止覆盖')
        self.related[path] = identity

    def verify(self):
        self.assert_key()
        if self.identity(self.directory/(self.purpose+'.enc')) != self.preimage:
            raise ValueError('网关凭据已被其他进程修改，已停止覆盖')
        for path, expected in self.related.items():
            if self.identity(path) != expected:
                raise ValueError('网关凭据索引已被其他进程修改，已停止覆盖')

    def assert_key(self):
        if self.key_image != self.identity(self.directory/'key'):
            raise ValueError('网关凭据密钥已变化，已停止覆盖')

    def replace(self, path, value, *, before_replace=None):
        temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            if before_replace is not None:
                before_replace()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def key(self, *, create=False):
        directory = self.folder()
        path = directory/'key'
        if create and not path.exists():
            auth_file = self.directory.parent/'zcode-official-auth.enc'
            index_file = self.directory.parent/'connection-vault.json'
            indexed = index_file.exists() and any(value == 'gateway-v1' for value in json.loads(index_file.read_text()).values())
            if any(directory.glob('*.enc')) or indexed or auth_file.exists() and auth_file.read_bytes()[:3] not in (b'v10', b'v11'):
                raise ValueError('网关凭据密钥缺失，已保留原密文')
            temporary = directory/('key.'+uuid.uuid4().hex+'.tmp')
            try:
                self.replace(temporary, os.urandom(32))
                try:
                    os.link(temporary, path)  # Publish a complete key without replacing a competing creator.
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        value, identity = self.private_read(path, with_identity=True)
        if self.key_image is not None and identity != self.key_image:
            raise ValueError('网关凭据密钥已变化，已停止覆盖')
        self.key_image = identity
        if len(value) != 32:
            raise ValueError('网关凭据密钥无法读取，已保留原文件')
        return value

    def encrypt(self, value):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        return MAGIC + nonce + AESGCM(self.key(create=True)).encrypt(nonce, value, MAGIC+b':'+self.purpose.encode())

    def decrypt(self, value):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if len(value) < 32 or not value.startswith(MAGIC):
            raise ValueError('网关凭据格式无法确认，已保留原文件')
        return AESGCM(self.key()).decrypt(value[4:16], value[16:], MAGIC+b':'+self.purpose.encode())

    @contextmanager
    def locked(self):
        import fcntl
        with os.fdopen(os.open(self.folder()/(self.purpose+'.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def read(self):
        path = self.folder()/(self.purpose+'.enc')
        try:
            value, identity = self.private_read(path, with_identity=True)
        except FileNotFoundError:
            value, identity = None, None
        if self.read_bound and self.preimage != identity:
            raise ValueError('网关凭据已被其他进程修改，已停止覆盖')
        self.preimage = identity
        self.read_bound = True
        return self.decrypt(value) if value is not None else None

    def write(self, value):
        path = self.folder()/(self.purpose+'.enc')
        if not self.read_bound:
            self.read()
        encrypted = self.encrypt(value)
        self.replace(path, encrypted, before_replace=self.verify)
        self.preimage = self.identity(path)
