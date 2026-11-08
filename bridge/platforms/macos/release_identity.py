"""Publisher proof for production Mac pipe binaries without an Apple certificate."""
import base64
import hashlib
import json
import plistlib
import re
from pathlib import Path

PRODUCT = 'io.github.try2love.codexmobilebridge'
BROKER = Path('Contents/Resources/gateway/codex-mobile-gateway')
STARTER = Path('Contents/Helpers/CMBZCodePipeStarter.app/Contents/MacOS/CMBZCodePipeStarter')
MANIFEST = Path('Contents/Resources/zcode-pipe-identity.json')
# Embedded in the frozen executable: peer checks must not trust a replaceable key file.
PUBLIC_KEY = b'-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEArRSY0ge22lRD1mk7Hopkq5aGCOVXQdN4I1xcOQpR1Bs=\n-----END PUBLIC KEY-----\n'


def public_key_bytes():
    # The same public identity as the desktop updater; no private key is shipped.
    return PUBLIC_KEY


def read(image, running_hash=None):
    """Verify a complete installed pair, then bind it to the kernel's live CDHash."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        image = Path(image)
        if tuple(image.parts[-4:]) != BROKER.parts:
            raise ValueError('Unexpected broker layout')
        app = image.parents[3]
        if app.suffix != '.app' or app.resolve(strict=True) != app:
            raise ValueError('Unexpected application path')
        proof = app / MANIFEST
        if proof.resolve(strict=True) != proof or proof.stat().st_size > 16384:
            raise ValueError('Invalid publisher proof')
        envelope = json.loads(proof.read_bytes())
        payload = base64.b64decode(envelope['payload'], validate=True)
        signature = base64.b64decode(envelope['signature'], validate=True)
        key = serialization.load_pem_public_key(public_key_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError('Unexpected publisher key')
        key.verify(signature, payload)
        record = json.loads(payload)
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        version = json.loads((app / 'Contents/Resources/update-version.json').read_bytes())
        if (not isinstance(record, dict) or record.get('schema') != 1 or record.get('purpose') != 'cmb-zcode-local-components'
                or record.get('product') != PRODUCT or info.get('CFBundleIdentifier') != PRODUCT
                or not record.get('version') or record['version'] != version.get('version')):
            raise ValueError('Publisher proof belongs to another product or version')
        for name, relative in (('broker', BROKER), ('starter', STARTER)):
            component = record['components'][name]
            hashes = component['cdhashes']
            if (not isinstance(hashes, list) or not 1 <= len(hashes) <= 2
                    or any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{40}', h) for h in hashes)):
                raise ValueError('Invalid component code identities')
            file = app / relative
            if file.resolve(strict=True) != file:
                raise ValueError('Component escaped the application')
            digest = hashlib.sha256()
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != component['sha256']:
                raise ValueError('Component does not match its publisher proof')
        if running_hash is not None and running_hash.hex() not in record['components']['broker']['cdhashes']:
            raise ValueError('Running broker does not match its publisher proof')
        return record
    except (OSError, ValueError, TypeError, KeyError, InvalidSignature, plistlib.InvalidFileException) as error:
        raise PermissionError('Invalid published ZCode component identity') from error
