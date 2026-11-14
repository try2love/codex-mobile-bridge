"""Reproducible Protobuf prerequisite for EasyTier; never downloads linker shims."""
import hashlib
import os
from pathlib import Path
import shutil
import urllib.request
import zipfile

PROTOC_SHA256 = '70381b116ab0d71cb6a5177d9b17c7c13415866603a0fd40d513dafe32d56c35'


def environment(root):
    env = dict(os.environ)
    if env.get('PROTOC') or shutil.which('protoc'):
        return env
    if os.name != 'nt':
        raise RuntimeError('Install protobuf-compiler (Linux) or protobuf (Homebrew) before building P2P')
    directory = Path(root) / '.local/p2p-tools/protoc-31.1'
    binary = directory / 'bin/protoc.exe'
    if not binary.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / 'protoc.zip'
        urllib.request.urlretrieve('https://github.com/protocolbuffers/protobuf/releases/download/v31.1/protoc-31.1-win64.zip', archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != PROTOC_SHA256:
            raise RuntimeError('Protobuf archive checksum mismatch')
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                if not (directory / entry.filename).resolve().is_relative_to(directory.resolve()):
                    raise RuntimeError('Unsafe Protobuf archive')
            source.extractall(directory)
    env['PROTOC'] = str(binary)
    return env
