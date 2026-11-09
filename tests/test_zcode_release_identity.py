"""Publisher authorization for the ad-hoc production Mac pipe components."""
import base64
import hashlib
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bridge.platforms.macos import release_identity as identity

ROOT = Path(__file__).resolve().parents[1]


class ReleaseKey(unittest.TestCase):
    def test_embedded_identity_matches_the_existing_release_key(self):
        embedded = serialization.load_pem_public_key(identity.PUBLIC_KEY)
        published = serialization.load_pem_public_key(
            (ROOT / 'desktop/features/updates/update-public-key.pem').read_bytes())
        self.assertEqual(
            embedded.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
            published.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


class PublishedComponents(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.directory.cleanup)
        self.app = Path(self.directory.name) / 'Bridge.app'
        self.broker = self.app / identity.BROKER
        self.helper = self.app / identity.STARTER
        for path, data in ((self.broker, b'broker'), (self.helper, b'starter')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': identity.PRODUCT}))
        (self.app / 'Contents/Resources/update-version.json').write_text(json.dumps({'version': '2.2.0'}))
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.payload = {'schema': 1, 'purpose': 'cmb-zcode-local-components', 'product': identity.PRODUCT,
                        'version': '2.2.0', 'components': {
                            'broker': {'sha256': hashlib.sha256(b'broker').hexdigest(), 'cdhashes': ['ab' * 20]},
                            'starter': {'sha256': hashlib.sha256(b'starter').hexdigest(), 'cdhashes': ['cd' * 20]}}}
        self.sign()
        self.public_patch = patch.object(identity, 'public_key_bytes', return_value=self.public)
        self.public_patch.start()
        self.addCleanup(self.public_patch.stop)

    def sign(self, key=None):
        payload = json.dumps(self.payload).encode()
        envelope = {'payload': base64.b64encode(payload).decode(),
                    'signature': base64.b64encode((key or self.key).sign(payload)).decode()}
        (self.app / identity.MANIFEST).write_text(json.dumps(envelope))

    def test_exact_publisher_components_and_running_hash_are_required(self):
        self.assertEqual(identity.read(self.broker, bytes.fromhex('ab' * 20)), self.payload)
        with self.assertRaises(PermissionError):
            identity.read(self.broker, bytes.fromhex('ff' * 20))
        for path in (self.broker, self.helper):
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.assertRaises(PermissionError):
                identity.read(self.broker)
            path.write_bytes(original)

    def test_wrong_signer_and_changed_metadata_are_rejected(self):
        self.sign(Ed25519PrivateKey.generate())
        with self.assertRaises(PermissionError):
            identity.read(self.broker)
        self.sign()
        for field, value in [('purpose', 'unrelated-signature'), ('product', 'copied.product'), ('version', '0.0.0')]:
            original = self.payload[field]
            self.payload[field] = value
            self.sign()
            with self.assertRaises(PermissionError):
                identity.read(self.broker)
            self.payload[field] = original
        self.sign()
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'copied.product'}))
        with self.assertRaises(PermissionError):
            identity.read(self.broker)

    def test_component_symlinks_and_missing_proof_are_rejected(self):
        elsewhere = self.app.parent / 'copy'
        elsewhere.write_bytes(self.helper.read_bytes())
        self.helper.unlink()
        self.helper.symlink_to(elsewhere)
        with self.assertRaises(PermissionError):
            identity.read(self.broker)
        (self.app / identity.MANIFEST).unlink()
        with self.assertRaises(PermissionError):
            identity.read(self.broker)

    def test_signed_hashes_cannot_inject_a_codesign_requirement(self):
        self.payload['components']['starter']['cdhashes'] = ['cd' * 20 + '" or true']
        self.sign()
        with self.assertRaises(PermissionError):
            identity.read(self.broker)

    def test_test_product_requires_its_own_signed_proof_and_same_product_peers(self):
        test_product = identity.PRODUCT + '.test'
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': test_product}))
        # A production proof must not authorize even identical Test components.
        with self.assertRaises(PermissionError):
            identity.read(self.broker)
        self.payload['product'] = test_product
        self.sign()
        self.assertEqual(identity.read(self.broker, bytes.fromhex('ab' * 20)), self.payload)
        with self.assertRaises(PermissionError):
            identity.read(self.broker, expected_product=identity.PRODUCT)
        self.assertEqual(identity.read(self.broker, expected_product=test_product), self.payload)


if __name__ == '__main__':
    unittest.main()
