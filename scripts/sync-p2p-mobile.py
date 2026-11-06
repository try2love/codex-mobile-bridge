"""Copy the reviewed shared crate into the standalone mobile repository with hashes."""
import argparse
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--app', type=Path, default=root.parent / 'codex-mobile-bridge-app')
args = parser.parse_args()
source = root / 'native/bridge-p2p'
destination = args.app / 'third_party/bridge-p2p'
files = ['Cargo.toml', 'Cargo.lock', 'src/lib.rs', 'src/bootstrap.rs', 'build_support.py', 'vendor/thunk-rs/Cargo.toml', 'vendor/thunk-rs/src/lib.rs']
files += [p.relative_to(source).as_posix() for p in (source / 'vendor/pnet_datalink').rglob('*') if p.is_file()]
files += ['LICENSE', 'README.md']
files += [p.relative_to(source).as_posix() for p in (source / 'licenses').glob('*') if p.is_file()]
manifest = {}
for name in files:
    data = (source / name).read_bytes().replace(b'\r\n', b'\n')
    target = destination / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)
    manifest[name] = hashlib.sha256(data).hexdigest()
snapshot = (json.dumps({
    'source': 'codex-mobile-bridge/native/bridge-p2p',
    'easytier': '8428a89d2dabc94c97d370ec607c6ca142473626',
    'sha256': manifest,
}, indent=2) + '\n').encode('utf-8')
manifest_path = destination / 'source.json'
if not manifest_path.exists() or manifest_path.read_bytes() != snapshot:
    manifest_path.write_bytes(snapshot)
print('Shared P2P snapshot synchronized:', destination)
