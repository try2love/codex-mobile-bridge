"""Build and bundle the reviewed EasyTier helper on the target OS."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
crate = root / 'native/bridge-p2p'
spec = importlib.util.spec_from_file_location('p2p_build', crate / 'build_support.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
subprocess.run(['cargo', 'build', '--release', '--locked', '--manifest-path', str(crate / 'Cargo.toml')],
               env=support.environment(root), check=True)
destination = root / 'dist/p2p'
destination.mkdir(parents=True, exist_ok=True)
name = 'bridge-p2p.exe' if os.name == 'nt' else 'bridge-p2p'
shutil.copy2(crate / 'target/release' / name, destination / name)
shutil.copy2(crate / 'licenses/EasyTier-LGPL-3.0.txt', destination / 'EasyTier-LGPL-3.0.txt')
shutil.copy2(crate / 'licenses/GPL-3.0.txt', destination / 'GPL-3.0.txt')
subprocess.run([__import__('sys').executable, str(root/'scripts/package-p2p-source.py')], check=True)
print('P2P helper:', destination / name)
