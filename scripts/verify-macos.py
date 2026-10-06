#!/usr/bin/env python3
"""Verify the actual macOS distribution, including its bundled Python runtime.

This checks signature integrity, not Developer ID trust or notarization.
"""
import argparse
import json
import platform
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path


def verify(app, arch):
    metadata = json.loads((app/'Contents/Resources/update-version.json').read_text(encoding='utf-8'))
    if metadata.get('platform') != 'darwin' or metadata.get('arch') != arch:
        raise RuntimeError(f'Bundled gateway metadata does not match macOS {arch}: {metadata}')
    native_arch = {'arm64': 'arm64', 'x64': 'x86_64'}[arch]
    runtime = app/'Contents/Resources/gateway/codex-mobile-gateway'
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    cloudflared = runtime.parent/'_internal/cloudflared/cloudflared'
    if not (cloudflared.parent/'LICENSE').is_file():
        raise RuntimeError('Missing bundled cloudflared license')
    subprocess.run([str(cloudflared), '--version'], check=True, timeout=10)
    for executable in (app/'Contents/MacOS'/info['CFBundleExecutable'], runtime, cloudflared):
        subprocess.run(['lipo', str(executable), '-verify_arch', native_arch], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', '--verbose=2', str(runtime)], check=True)
    subprocess.run([str(runtime), '--help'], check=True, stdout=subprocess.DEVNULL, timeout=30)
    subprocess.run([sys.executable, str(Path(__file__).with_name('verify-https.py')), str(runtime)], check=True)
    print(f'PASS: macOS {arch}, app signature, nested code and bundled runtime; notarization not checked.')


def single_app(directory):
    apps = list(directory.glob('*.app'))
    if len(apps) != 1:
        raise RuntimeError('The distribution must contain exactly one top-level .app')
    return apps[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path, help='The distributed DMG, ZIP or an extracted .app')
    parser.add_argument('--arch', choices=('arm64', 'x64'),
                        default={'arm64': 'arm64', 'x86_64': 'x64'}.get(platform.machine()))
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    if sys.platform != 'darwin':
        parser.error('This check requires macOS')
    if not args.arch:
        parser.error('Specify the expected CPU architecture with --arch')
    if artifact.suffix == '.app':
        verify(artifact, args.arch)
        return
    if artifact.suffix not in ('.dmg', '.zip') or not artifact.is_file():
        parser.error('Expected an existing .app, DMG or ZIP')
    temporary = Path(__file__).resolve().parents[1]/'.tmp'
    temporary.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='macos-package-', dir=temporary) as directory:
        destination = Path(directory)
        if artifact.suffix == '.dmg':
            mount = destination/'mounted'
            mount.mkdir()
            subprocess.run(['hdiutil', 'attach', str(artifact), '-readonly', '-nobrowse',
                            '-mountpoint', str(mount)], check=True)
            try:
                applications = mount/'Applications'
                if not applications.is_symlink() or applications.readlink() != Path('/Applications'):
                    raise RuntimeError('The DMG must include an Applications shortcut')
                app = single_app(mount)
                subprocess.run(['ditto', str(app), str(destination/app.name)], check=True)
            finally:
                subprocess.run(['hdiutil', 'detach', str(mount)], check=True)
        else:
            subprocess.run(['ditto', '-x', '-k', str(artifact), directory], check=True)
        verify(single_app(destination), args.arch)


if __name__ == '__main__':
    main()
