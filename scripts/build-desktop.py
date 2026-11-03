#!/usr/bin/env python3
"""Build on the target OS; use a project-local venv with requirements-desktop.txt."""
import os
import json
import platform
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path
import certifi
root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root/'scripts/build-client-helpers.py')], check=True)
subprocess.run([sys.executable, str(root/'scripts/bundle-cloudflared.py')], check=True)
if sys.platform == 'win32':
    subprocess.run([sys.executable, str(root/'scripts/bundle-conpty.py')], check=True)
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', 'codex-mobile-gateway',
           '--distpath', str(root/'dist'), '--workpath', str(root/'.tmp/pyinstaller'), '--specpath', str(root/'.tmp'),
           '--add-data', str(root/'web')+':web']
command.extend(['--add-data', str(root/'dist/cloudflared')+':cloudflared', '--collect-all', 'paramiko', '--collect-all', 'keyring', '--collect-all', 'aiohttp'])
if sys.platform == 'darwin':
    command.extend(['--target-arch', platform.machine()])
if sys.platform == 'win32':
    for relative in ('conpty.dll', 'x86/OpenConsole.exe', 'x64/OpenConsole.exe', 'arm64/OpenConsole.exe'):
        source = root/'dist/conpty'/relative
        command.extend(['--add-binary', str(source)+':'+str(Path('conpty')/Path(relative).parent)])
    for name in ('LICENSE', 'version.json'):
        command.extend(['--add-data', str(root/'dist/conpty'/name)+':conpty'])
# Ship roots and their license explicitly; source users still need only stdlib.
command.extend(['--add-data', certifi.where()+':bridge/features/auth'])
command.extend([
    '--hidden-import', 'bridge.platforms.linux.interfaces',
    '--hidden-import', 'bridge.platforms.macos.interfaces',
    '--hidden-import', 'bridge.platforms.windows.interfaces',
])
certificate_package = distribution('certifi')
license_file = next(p for p in certificate_package.files if p.name == 'LICENSE')
command.extend(['--add-data', str(certificate_package.locate_file(license_file))+':licenses/certifi'])
# The SSH adapter intentionally injects these source modules into remote Python.
for relative in json.loads((root/'scripts/gateway-resources.json').read_text(encoding='utf-8')):
    source_file = root/relative
    command.extend(['--add-data', str(source_file)+':'+str(Path(relative).parent)])
helpers = {'darwin': ('claude-bridge-helper', 'zcode-bridge-helper'),
           'win32': ('claude-bridge-helper.exe', 'codex-quit-helper.exe')}
for name in helpers.get(sys.platform, ()):
    command.extend(['--add-binary', str(root/'dist/client-helpers'/name)+':client-helpers'])
command.append(str(root/'desktop.py'))
subprocess.run(command, cwd=root, check=True)
source = root/'dist/codex-mobile-gateway'
import shutil
shutil.rmtree(root/'dist/gateway', ignore_errors=True)
source.rename(root/'dist/gateway')
print('Gateway runtime ready:', root/'dist/gateway')

(root/'dist/update-version.json').write_text(json.dumps({'version': json.loads((root/'package.json').read_text(encoding='utf-8'))['version'], 'platform': sys.platform, 'arch': {'aarch64':'arm64', 'arm64':'arm64', 'AMD64':'x64', 'x86_64':'x64'}.get(platform.machine(), platform.machine())}), encoding='utf-8')
