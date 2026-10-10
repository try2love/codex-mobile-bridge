#!/usr/bin/env python3
"""Build an unsigned device IPA and a tracked-source Xcode ZIP; never installs SDKs."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = ('mobile/ios', 'mobile/shared/web', 'web/client-icons',
                'mobile/README.md', 'mobile/NOTICE.md', 'mobile/licenses',
                'LICENSE', 'README.md')
REQUIRED_SOURCES = ('mobile/ios/BridgePreview.xcodeproj/project.pbxproj',
                    'mobile/ios/BridgePreview/Info.plist',
                    'mobile/shared/web/mobile-ui.js', 'mobile/shared/web/mobile-clipboard.js',
                    'web/client-icons/codex.png', 'web/client-icons/claude.png',
                    'web/client-icons/deepseek.png', 'mobile/README.md', 'LICENSE')


def source_archive(root, destination):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--', *SOURCE_PATHS],
                                    cwd=root).decode('utf-8').split('\0')
    names = sorted(name for name in names if name and 'xcuserdata' not in Path(name).parts)
    if not set(REQUIRED_SOURCES).issubset(names):
        raise RuntimeError('The tracked iOS source archive is missing project resources.')
    for name in names:
        file = root/name
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('Source archive only accepts regular tracked project files: '+name)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(root/name, name)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Invalid iOS source ZIP.')
    return len(names)


def verify_app(app, version, build):
    targets = [(app, 'io.github.try2love.codexbridge.preview'),
               (app/'PlugIns/BridgeActivity.appex', 'io.github.try2love.codexbridge.preview.activity'),
               (app/'PlugIns/BridgeNotifications.appex', 'io.github.try2love.codexbridge.preview.notifications')]
    for bundle, identifier in targets:
        info = plistlib.loads((bundle/'Info.plist').read_bytes())
        if (info['CFBundleIdentifier'] != identifier or info['CFBundleShortVersionString'] != version
                or info['CFBundleVersion'] != build):
            raise RuntimeError('iOS bundle identity/version mismatch: '+str(bundle))
        executable = bundle/info['CFBundleExecutable']
        if not executable.is_file() or not executable.stat().st_size:
            raise RuntimeError('Missing iOS executable: '+str(executable))
        if (bundle/'embedded.mobileprovision').exists() or (bundle/'_CodeSignature').exists():
            raise RuntimeError('Expected an unsigned iOS bundle: '+str(bundle))
        subprocess.run(['xcrun', 'lipo', str(executable), '-verify_arch', 'arm64'], check=True)
    for name in ('mobile-ui.js', 'mobile-clipboard.js', 'codex.png', 'claude.png', 'deepseek.png'):
        if not (app/name).is_file():
            raise RuntimeError('Missing iOS resource: '+name)
    info = plistlib.loads((app/'Info.plist').read_bytes())
    if info.get('BridgeReleaseVersion') != version:
        raise RuntimeError('iOS update channel version does not match the release.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true', help='Create the tracked Xcode source ZIP without Xcode.')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'dist/mobile-preview')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version']
    metadata = plistlib.loads((ROOT/'mobile/ios/BridgePreview/Info.plist').read_bytes())
    if metadata['BridgeReleaseVersion'] != version:
        raise RuntimeError('iOS metadata must match package.json before building.')
    prefix = 'Codex-Mobile-Bridge-'+version+'-iOS'
    if not args.source_only:
        if sys.platform != 'darwin':
            raise RuntimeError('The unsigned IPA requires macOS and Xcode with the iOS platform installed.')
        work_root = ROOT/'.tmp'
        work_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='ios-release-', dir=work_root) as temporary:
            work = Path(temporary)
            subprocess.run(['xcodebuild', '-project', str(ROOT/'mobile/ios/BridgePreview.xcodeproj'),
                            '-target', 'BridgePreview', '-configuration', 'Release', '-sdk', 'iphoneos',
                            'SYMROOT='+str(work/'products'), 'OBJROOT='+str(work/'intermediates'),
                            'CLANG_MODULE_CACHE_PATH='+str(work/'module-cache'),
                            'CODE_SIGNING_ALLOWED=NO', 'build'], cwd=ROOT, check=True)
            app = work/'products/Release-iphoneos/BridgePreview.app'
            verify_app(app, version, metadata['CFBundleVersion'])
            payload = work/'Payload'
            payload.mkdir()
            shutil.copytree(app, payload/app.name)
            ipa = output/(prefix+'-unsigned.ipa')
            subprocess.run(['ditto', '-c', '-k', '--norsrc', '--keepParent', str(payload), str(ipa)], check=True)
            with zipfile.ZipFile(ipa) as archive:
                if archive.testzip() is not None or 'Payload/BridgePreview.app/Info.plist' not in archive.namelist():
                    raise RuntimeError('Invalid unsigned IPA.')
            print(ipa)
            print('SHA-256', hashlib.sha256(ipa.read_bytes()).hexdigest())
    archive = output/(prefix+'-source.zip')
    count = source_archive(ROOT, archive)
    print(archive)
    print('Tracked source files:', count)
    print('SHA-256', hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
