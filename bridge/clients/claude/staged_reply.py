"""Receive one encrypted local reply from Claude when its trusted folder has no chat."""
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def _directory(path):
    return path.is_dir() and not path.is_symlink() and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400


def _replace(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        tmp = Path(output.name)
        try:
            output.write(data)
        except BaseException:
            output.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class StagedReply:
    def __init__(self, profile, token):
        self.root = Path(profile) / 'pending-uploads'
        tag = hashlib.sha256(('cmb-reply-name:' + token).encode()).hexdigest()[:32]
        self.filename = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-cmb-' + tag + r'\.bin\Z')
        self.cipher = AESGCM(hashlib.sha256(('cmb-reply-key:' + token).encode()).digest())
        self.lock = threading.Lock()
        self.next_scan = 0
        self.folder = None
        self.next_discovery = 0
        self.scanned = {}

    def _folders(self):
        if not _directory(self.root) or any(p.is_symlink() for p in self.root.parents):
            return
        if self.folder and _directory(self.folder) and _directory(self.folder.parent):
            yield self.folder
        if time.monotonic() < self.next_discovery:
            return
        self.next_discovery = time.monotonic() + 1
        if self.root != self.folder:
            yield self.root
        # Restricted Desktop profiles stage under account UUID / organization UUID.
        for account in self.root.iterdir():
            if re.fullmatch(r'[0-9a-fA-F-]{36}', account.name) and _directory(account):
                for org in account.iterdir():
                    if org != self.folder and re.fullmatch(r'[0-9a-fA-F-]{36}', org.name) and _directory(org):
                        yield org

    def collect(self, destination, generation, limit, pack):
        if time.monotonic() < self.next_scan or not self.lock.acquire(blocking=False):
            return
        try:
            self.next_scan = time.monotonic() + .25
            for folder in self._folders():
                modified = folder.stat().st_mtime_ns
                if self.scanned.get(folder) == modified:
                    continue
                complete = True
                for path in folder.iterdir():
                    if not self.filename.fullmatch(path.name):
                        continue
                    info = path.lstat()
                    if not _regular(info) or info.st_size > limit + 28:
                        continue
                    if info.st_size <= 28:
                        complete = False
                        continue
                    with path.open('rb') as stream:
                        opened = os.fstat(stream.fileno())
                        if not os.path.samestat(info, opened):
                            continue
                        raw = stream.read(limit + 29)
                    try:
                        data = self.cipher.decrypt(raw[:12], raw[12:], None)
                        value = json.loads(json.loads(data)['payload'])
                    except (InvalidTag, ValueError, KeyError, TypeError):
                        complete = False
                        continue
                    if (isinstance(value, dict) and value.get('generation') == generation and
                            re.fullmatch(r'[0-9a-f]{32}', str(value.get('receipt', '')))):
                        # The ordinary reader and the renderer both verify this signed
                        # packet. Atomic replacement also acknowledges its receipt.
                        _replace(destination, data)
                        ack = pack({'generation': generation, 'receipt': value['receipt']})
                        _replace(destination.with_name('staged-ack.json'), ack.encode())
                        self.folder = folder
                        if os.path.samestat(info, path.lstat()):
                            path.unlink()
                        return
                if complete:
                    self.scanned[folder] = modified
        except OSError:
            pass  # A sharing conflict leaves the same packet available for the next read.
        finally:
            self.lock.release()
