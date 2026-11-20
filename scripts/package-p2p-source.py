"""Bundle corresponding source and rebuild instructions for the LGPL helper."""
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
crate = root / 'native/bridge-p2p'
metadata = json.loads(subprocess.check_output(['cargo','metadata','--locked','--format-version','1','--manifest-path',str(crate/'Cargo.toml')],text=True,cwd=root))
upstream = next(package for package in metadata['packages'] if package['name'] == 'easytier')
source = Path(upstream['manifest_path']).parent.parent
names = subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
names += [p.relative_to(root).as_posix() for p in crate.rglob('*') if p.is_file() and 'target' not in p.relative_to(crate).parts]
names += ['scripts/build-p2p.py','scripts/sync-p2p-mobile.py','scripts/package-p2p-source.py']
output = root/'dist/p2p/bridge-p2p-corresponding-source.tar.gz'
output.parent.mkdir(parents=True,exist_ok=True)
with tarfile.open(output,'w:gz') as archive:
    for name in sorted(set(names)-{''}):
        path=root/name
        if path.is_file(): archive.add(path,arcname='bridge-desktop/'+name)
    for path in source.rglob('*'):
        if path.is_file() and '.git' not in path.relative_to(source).parts:
            archive.add(path,arcname='easytier/'+path.relative_to(source).as_posix())
print('P2P corresponding source:',output)
