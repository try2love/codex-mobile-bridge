# Build with: powershell -File scripts/build_windows.ps1
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata, collect_data_files

root = Path(SPECPATH)
metadata = []
for dependency in ('paramiko', 'cryptography', 'bcrypt', 'PyNaCl', 'cffi', 'invoke', 'pycparser', 'customtkinter', 'pillow', 'darkdetect'):
    metadata += copy_metadata(dependency)
analysis = Analysis(
    [str(root / '固定域名入口.pyw')],
    pathex=[str(root / 'app'), str(root / 'vendor')],
    binaries=[(str(root / 'vendor' / 'bin' / 'cloudflared.exe'), 'bin')],
    datas=[
        (str(root / 'assets' / 'codex-mobile.ico'), 'assets'),
        (str(root / 'assets' / 'codex-mobile.png'), 'assets'),
        (str(root / 'app' / 'run.py'), 'app'),
        (str(root / 'app' / 'web'), 'app/web'),
        (str(root / 'app' / 'bridge' / 'store.py'), 'bridge'),
        (str(root / 'app' / 'bridge' / 'catalog.py'), 'bridge'),
        (str(root / 'app' / 'bridge' / 'creator.py'), 'bridge'),
        (str(root / 'app' / 'bridge' / 'runtime.py'), 'bridge'),
        (str(root / 'app' / 'bridge' / 'actions.py'), 'bridge'),
        (str(root / 'app' / 'LICENSE'), 'licenses/codex-mobile-bridge'),
        (str(root / 'LICENSE'), 'licenses'),
        (str(root / 'NOTICE.md'), 'licenses'),
        (str(root / 'licenses' / 'cloudflared-LICENSE'), 'licenses/cloudflared'),
        (str(root / 'licenses' / 'Python-LICENSE.txt'), 'licenses/python'),
        (str(root / 'vendor' / 'qrcodegen.py'), 'licenses/qrcodegen'),
    ] + metadata + collect_data_files('customtkinter'),
    hiddenimports=['bridge.' + path.stem for path in (root / 'app' / 'bridge').glob('*.py') if path.stem != '__init__']
                  + ['argparse', 'getpass', 'signal', 'logging', 'secrets', 'shutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name='CodexMobileAssistant',
    icon=str(root / 'assets' / 'codex-mobile.ico'),
    version=str(root / 'assets' / 'version_info.txt'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    runtime_tmpdir=r'%LOCALAPPDATA%\CodexMobileAssistant\Runtime',
    disable_windowed_traceback=False,
)
