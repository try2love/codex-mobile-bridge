#!/usr/bin/env python3
"""Fetch exactly one locked upstream binary at build time, never at app startup."""
import hashlib
import base64
import io
import json
import platform
import sys
import tarfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]


def license_data(version, name):
    url = f'https://raw.githubusercontent.com/cloudflare/cloudflared/{version}/{name}'
    try:
        with urlopen(url, timeout=30) as response:
            return response.read(2*1024*1024)
    except HTTPError:
        raise
    except OSError:
        # Some Linux build networks reach GitHub but time out on its raw host.
        # Keep the same repository/tag and certificate validation on fallback.
        if sys.platform != 'linux':
            raise
        url = f'https://api.github.com/repos/cloudflare/cloudflared/contents/{name}?ref={version}'
        with urlopen(Request(url, headers={'User-Agent': 'Codex-Mobile-Bridge-build'}), timeout=30) as response:
            item = json.loads(response.read(3*1024*1024))
        if item.get('name') != name or item.get('encoding') != 'base64':
            raise ValueError('Unexpected cloudflared license response')
        return base64.b64decode(item['content'].replace('\n', ''), validate=True)


def bundle():
    lock = json.loads((ROOT/'scripts/cloudflared-lock.json').read_text())
    arch = {'x86_64': 'amd64', 'AMD64': 'amd64', 'arm64': 'arm64', 'aarch64': 'arm64'}[platform.machine()]
    system = {'darwin': 'darwin', 'win32': 'windows', 'linux': 'linux'}[sys.platform]
    suffix = '.tgz' if system == 'darwin' else '.exe' if system == 'windows' else ''
    name = f'cloudflared-{system}-{arch}{suffix}'
    expected = lock['assets'][name]
    cache = ROOT/'.tmp/cloudflared'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache/name
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        url = f'https://github.com/cloudflare/cloudflared/releases/download/{lock["version"]}/{name}'
        with urlopen(Request(url, headers={'User-Agent': 'Codex-Mobile-Bridge-build'}), timeout=180) as response:
            data = response.read(128*1024*1024)
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('cloudflared SHA-256 mismatch')
        archive.write_bytes(data)
    data = archive.read_bytes()
    if suffix == '.tgz':
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            member = next(m for m in tar if m.name in ('cloudflared', './cloudflared') and m.isfile())
            data = tar.extractfile(member).read()
    target = ROOT/'dist/cloudflared'
    target.mkdir(parents=True, exist_ok=True)
    executable = target/('cloudflared.exe' if system == 'windows' else 'cloudflared')
    executable.write_bytes(data)
    executable.chmod(0o755)
    for name in ('LICENSE', 'NOTICE'):
        cached = cache/(lock['version']+'-'+name)
        if not cached.exists():
            try:
                cached.write_bytes(license_data(lock['version'], name))
            except HTTPError as exc:
                if name == 'NOTICE' and exc.code == 404:
                    continue
                raise
        (target/name).write_bytes(cached.read_bytes())
    (target/'version.json').write_text(json.dumps({'version': lock['version'], 'platform': sys.platform, 'arch': arch,
                                                 'sha256': hashlib.sha256(data).hexdigest()}))
    print('Bundled cloudflared:', executable, len(data), 'bytes')
    return executable


if __name__ == '__main__':
    bundle()
