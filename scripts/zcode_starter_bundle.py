"""Prepare or verify the packaged Mac gateway's private ZCode launcher."""
import argparse
import hashlib
import json
import plistlib
import re
import subprocess
import tempfile
from pathlib import Path

NAME = 'CMBZCodePipeStarter'
IDENTIFIER = 'io.github.try2love.codexmobilebridge.zcode-pipe-starter'
TEST_ID = 'io.github.try2love.codexmobilebridge.test'
PRODUCT_ID = 'io.github.try2love.codexmobilebridge'
HASH_KEY = 'CMBGatewayBrokerCDHashes'
SOURCE = Path(__file__).resolve().parents[1]/'bridge/platforms/macos/zcode-pipe-starter.m'


def helper_path(app):
    return app/'Contents/Helpers'/(NAME+'.app')


def code_hashes(binary, identifier):
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', '-R=identifier "'+identifier+'"',
                    str(binary)], check=True)
    arches = subprocess.check_output(['/usr/bin/lipo', '-archs', str(binary)], text=True).split()
    if not arches or set(arches) - {'arm64', 'x86_64'}:
        raise ValueError('Unexpected Mac gateway architecture')
    hashes = []
    for arch in arches:
        result = subprocess.run(['/usr/bin/codesign', '--display', '--arch', arch, '--verbose=4', str(binary)],
                                capture_output=True, text=True, check=True)
        match = re.search(r'^CDHash=([\da-f]{40})$', result.stderr, re.MULTILINE)
        if not match:
            raise ValueError(f'Cannot identify the signed {arch} gateway')
        hashes.append(bytes.fromhex(match[1]))
    return arches, hashes


def broker_hashes(app):
    return code_hashes(app/'Contents/Resources/gateway/codex-mobile-gateway', 'codex-mobile-gateway')


def prepare(app):
    """Call after the broker's final signing pass and before signing the outer bundle."""
    if plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleIdentifier'] not in (TEST_ID, PRODUCT_ID):
        raise ValueError('Unexpected gateway application identifier')
    helper = helper_path(app)
    if helper.exists() or helper.is_symlink():
        raise ValueError('Build into a fresh gateway package')
    arches, hashes = broker_hashes(app)
    flags = [option for arch in arches for option in ('-arch', arch)]
    info = {'CFBundleIdentifier': IDENTIFIER, 'CFBundleExecutable': NAME, 'CFBundleName': NAME,
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
            'LSUIElement': True, HASH_KEY: hashes}
    helper.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=helper.parent) as temporary:
        staged = Path(temporary)/helper.name
        executable = staged/'Contents/MacOS'/NAME
        executable.parent.mkdir(parents=True)
        subprocess.run(['/usr/bin/clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-Os', *flags,
                        '-framework', 'AppKit', '-framework', 'Security', str(SOURCE), '-o', str(executable)],
                       check=True)
        (staged/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        staged.rename(helper)


def verify(app):
    """Verify the final helper and its binding to every architecture in the real broker."""
    helper = helper_path(app)
    arches, hashes = broker_hashes(app)
    info = plistlib.loads((helper/'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleIdentifier') != IDENTIFIER or info.get('CFBundleExecutable') != NAME
            or info.get(HASH_KEY) != hashes or info.get('LSUIElement') is not True):
        raise ValueError('Mac launcher does not match the final signed gateway')
    subprocess.run(['/usr/bin/lipo', str(helper/'Contents/MacOS'/NAME), '-verify_arch', *arches], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', '-R=identifier "'+IDENTIFIER+'"',
                    str(helper)], check=True)


def identity(app):
    """Unsigned component description; the release hook authorizes it separately."""
    verify(app)
    product = plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleIdentifier']
    if product not in (PRODUCT_ID, TEST_ID):
        raise ValueError('Unexpected gateway application identifier')
    broker = app/'Contents/Resources/gateway/codex-mobile-gateway'
    starter = helper_path(app)/'Contents/MacOS'/NAME
    components = {}
    for name, binary, identifier in (('broker', broker, 'codex-mobile-gateway'),
                                     ('starter', starter, IDENTIFIER)):
        _, hashes = code_hashes(binary, identifier)
        components[name] = {'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                            'cdhashes': [digest.hex() for digest in hashes]}
    version = json.loads((app/'Contents/Resources/update-version.json').read_text())['version']
    return {'schema': 1, 'purpose': 'cmb-zcode-local-components', 'product': product,
            'version': version, 'components': components}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'verify', 'identity'])
    parser.add_argument('app', type=Path)
    args = parser.parse_args()
    if args.operation == 'identity':
        print(json.dumps(identity(args.app)))
    else:
        (prepare if args.operation == 'prepare' else verify)(args.app)
