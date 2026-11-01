"""Fetch a pinned official Windows tunnel binary after verifying its digest."""
import hashlib
import os
from pathlib import Path
import tempfile
import urllib.request

VERSION = '2026.9.3'
SHA256 = 'f096265ec2fcbe9bb6e2d64268db167ced3fcbb83d894bdb9e2fcdb26f2ea7e2'
URL = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/cloudflared-windows-amd64.exe'
TARGET = Path(__file__).resolve().parents[1] / 'vendor/bin/cloudflared.exe'


def main():
    if TARGET.is_file() and hashlib.sha256(TARGET.read_bytes()).hexdigest() == SHA256:
        print('cloudflared: verified existing binary')
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=TARGET.parent, delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(URL, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('cloudflared checksum mismatch; binary was not installed')
        os.replace(temporary, TARGET)
        print('cloudflared: official binary downloaded and verified')
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
