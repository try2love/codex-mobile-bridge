import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.features.auth.auth import Auth
from bridge.features.network.discovery import ConnectionDiscovery, mac
from bridge.features.network.shared_relay import save_config


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.cfg = {'enabled': True, 'url': 'https://relay.example.com', 'deviceId': 'a' * 32, 'deviceToken': 'fixture-device-secret'}
        save_config(self.tmp.name, self.cfg)
        self.auth = Auth({'mode': 'password', 'sessionHours': 0})
        self.secret, _ = self.auth.new_session('127.0.0.1', 'BridgeMobile/0.1-Android')
        self.gateway = SimpleNamespace(auth=self.auth, origins={'http://127.0.0.1:1', 'https://old.example.com'})
        self.discovery = ConnectionDiscovery(self.tmp.name, self.gateway)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lookup_never_discloses_original_credentials_and_address_change_keeps_identity(self):
        descriptor = self.discovery.descriptor(self.secret)
        first = self.discovery.snapshot()
        self.assertEqual(first['endpoints'], ['https://old.example.com'])
        self.assertNotIn(self.secret, str(first)); self.assertNotIn(self.auth.key(self.secret), str(first))
        self.assertNotIn(descriptor['discoveryKey'], str(first))
        self.assertIn(hashlib.sha256(descriptor['discoveryKey'].encode()).hexdigest(), first['grants'])
        self.gateway.origins = {'https://new.example.com'}
        self.assertEqual(self.discovery.descriptor(self.secret), descriptor)
        self.assertEqual(self.discovery.snapshot()['endpoints'], ['https://new.example.com'])

    def test_proof_requires_live_binding_and_binds_device_destination_and_nonce(self):
        key = self.auth.key(self.secret); identifier = hashlib.sha256(key.encode()).hexdigest(); nonce = '1' * 64
        proof = self.discovery.prove(identifier, nonce, 'https://old.example.com')
        self.assertEqual(proof['proof'], mac(key, self.cfg['deviceId'] + '\nhttps://old.example.com\n' + nonce))
        self.assertNotEqual(proof, self.discovery.prove(identifier, '2' * 64, 'https://old.example.com'))
        with self.assertRaises(PermissionError): self.discovery.prove(identifier, nonce, 'https://attacker.example')
        with self.assertRaises(PermissionError): self.discovery.prove('0' * 64, nonce, 'https://old.example.com')
        self.auth.logout(self.secret)
        self.assertIsNone(self.discovery.descriptor(self.secret))
        self.assertEqual(self.discovery.snapshot()['grants'], [])
        with self.assertRaises(PermissionError): self.discovery.prove(identifier, nonce, 'https://old.example.com')

    def test_disabling_relay_disables_lookup_and_identity_proofs(self):
        save_config(self.tmp.name, {**self.cfg, 'enabled': False})
        self.assertIsNone(self.discovery.descriptor(self.secret)); self.assertIsNone(self.discovery.snapshot())
        with self.assertRaises(PermissionError): self.discovery.prove('0' * 64, '1' * 64, 'https://old.example.com')
