"""Mac profile credentials never need application-signature Keychain consent."""
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge.features.accounts import secrets
from bridge.features.accounts.local_vault import LocalVault
from bridge.features.notifications.channels import write_json
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(AESGCM and os.name != 'nt', 'Mac private-vault tests need the existing cryptography dependency and POSIX permissions')
class MacCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.platform = patch.object(secrets.sys, 'platform', 'darwin')
        self.platform.start();self.addCleanup(self.platform.stop)

    def test_rebuild_restore_clear_and_no_keychain_calls(self):
        with patch.object(secrets, 'backend', side_effect=AssertionError('no Keychain')):
            secrets.save(self.directory, 'one', {'password': 'synthetic-private-secret'})
            key = (self.directory/'.credential-vault/key').read_bytes()
            self.assertEqual(secrets.read(self.directory, 'one'), {'password': 'synthetic-private-secret'})
            secrets.save(self.directory, 'one', {'passphrase': 'phrase'})
            self.assertEqual((self.directory/'.credential-vault/key').read_bytes(), key)
            self.assertEqual((self.directory/'.credential-vault').stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.directory/'.credential-vault/key').stat().st_mode & 0o777, 0o600)
            cipher = (self.directory/'.credential-vault/connections.enc').read_bytes()
            self.assertNotIn(b'synthetic-private-secret', cipher)
            secrets.save(self.directory, 'one', {'clear': True})
            self.assertEqual(secrets.read(self.directory, 'one'), {})

    def test_legacy_read_never_probes_keychain_and_only_explicit_migration_does(self):
        write_json(self.directory/'connection-vault.json', {'one': True})
        vault = MagicMock();vault.get_password.return_value = json.dumps({'password': 'legacy-fixture'})
        with patch.object(secrets, 'backend', return_value=vault):
            with self.assertRaises(secrets.LegacyCredentialsRequired):secrets.read(self.directory, 'one')
            vault.get_password.assert_not_called()
            secrets.migrate(self.directory, 'one');secrets.migrate(self.directory, 'one')
            self.assertEqual(secrets.read(self.directory, 'one'), {'password': 'legacy-fixture'})
            self.assertEqual(vault.get_password.call_count, 1)
            vault.delete_password.assert_not_called()

    def test_missing_key_and_tamper_cannot_replace_existing_data(self):
        secrets.save(self.directory, 'one', {'password': 'fixture'})
        file = self.directory/'.credential-vault/connections.enc';original = file.read_bytes()
        broken = bytearray(original);broken[-1] ^= 1;file.write_bytes(broken)
        with self.assertRaises(ValueError):secrets.read(self.directory, 'one')
        with self.assertRaises(ValueError):secrets.save(self.directory, 'two', {'password': 'replacement'})
        self.assertEqual(file.read_bytes(), broken)
        file.write_bytes(original);key = self.directory/'.credential-vault/key';key.unlink()
        with self.assertRaises(ValueError):secrets.save(self.directory, 'two', {'password': 'replacement'})
        self.assertFalse(key.exists());self.assertEqual(file.read_bytes(), original)

    def test_nonce_purpose_and_concurrent_updates(self):
        vault = LocalVault(self.directory, 'connections')
        first, second = vault.encrypt(b'fixture'), vault.encrypt(b'fixture')
        self.assertNotEqual(first, second)
        with self.assertRaises(Exception):LocalVault(self.directory, 'other').decrypt(first)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i:secrets.save(self.directory, str(i), {'password': str(i)}), range(12)))
        for i in range(12):self.assertEqual(secrets.read(self.directory, str(i)), {'password': str(i)})

    def test_symlink_and_public_key_permissions_are_refused(self):
        secrets.save(self.directory, 'one', {'password': 'fixture'})
        key = self.directory/'.credential-vault/key';key.chmod(0o644)
        with self.assertRaises(ValueError):secrets.read(self.directory, 'one')
        key.chmod(0o600);old = key.with_suffix('.old');key.rename(old);key.symlink_to(old)
        with self.assertRaises(ValueError):secrets.read(self.directory, 'one')

    def test_missing_legacy_item_preserves_its_index_and_does_not_create_a_key(self):
        write_json(self.directory/'connection-vault.json', {'one': True})
        vault = MagicMock();vault.get_password.return_value = None
        with patch.object(secrets, 'backend', return_value=vault), self.assertRaises(ValueError):
            secrets.migrate(self.directory, 'one')
        self.assertEqual(json.loads((self.directory/'connection-vault.json').read_text()), {'one': True})
        self.assertFalse((self.directory/'.credential-vault/key').exists())

    def test_desktop_private_read_exposes_migration_only_and_public_snapshot_hides_local_secrets(self):
        from bridge.app.desktop import Desktop
        from test_connection_setup import entry
        desktop = Desktop(self.directory);desktop.status = lambda:{'running':False}
        value = desktop.snapshot();value['preferences'].update(codexHome=str(self.directory),connections=[entry()])
        desktop.save(value);write_json(self.directory/'connection-vault.json', {'test-server':True})
        with patch.object(secrets, 'backend', side_effect=AssertionError('No automatic Keychain')):
            self.assertEqual(desktop.read_credentials({'id':'test-server'}), {'migrationRequired':True})
            desktop.connection_credentials({'id':'test-server','secrets':{'password':'private-fixture'}})
            self.assertEqual(desktop.read_credentials({'id':'test-server'}), {'password':'private-fixture'})
            self.assertNotIn('private-fixture',json.dumps(desktop.snapshot()))

    def test_missing_ciphertext_cannot_silently_create_a_partial_replacement(self):
        secrets.save(self.directory, 'one', {'password':'original'})
        file = self.directory/'.credential-vault/connections.enc';file.unlink()
        with self.assertRaises(ValueError):secrets.save(self.directory, 'two', {'password':'replacement'})
        self.assertFalse(file.exists())
        self.assertEqual(json.loads((self.directory/'connection-vault.json').read_text()), {'one':'gateway-v1'})

    def test_third_party_cipher_key_and_index_changes_are_not_overwritten(self):
        for change in ('cipher','key','index'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=self.directory) as directory:
                secrets.save(directory, 'one', {'password':'original'})
                vault = LocalVault(directory,'connections');secrets.local_values(directory,vault)
                file = Path(directory)/'.credential-vault/connections.enc'
                target = {'cipher':file,'key':file.with_name('key'),'index':Path(directory)/'connection-vault.json'}[change]
                foreign = b'foreign-cipher' if change == 'cipher' else os.urandom(32) if change == 'key' else b'{}'
                target.write_bytes(foreign)
                with self.assertRaises(Exception):vault.write(b'{}')
                self.assertEqual(target.read_bytes(),foreign)
