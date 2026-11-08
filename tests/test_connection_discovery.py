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

    def test_private_app_routes_are_scoped_to_one_origin_and_revoked_with_parent(self):
        save_config(self.tmp.name, {**self.cfg, 'enabled': False})
        lan, public = 'http://192.168.1.9:8788', 'https://test.example.org'
        self.gateway.origins.update((lan, public, 'http://8.8.8.8'))
        descriptor = self.discovery.mobile_routes(self.secret)
        self.assertEqual(set(descriptor['endpoints']), {lan, public, 'https://old.example.com'})
        device, nonce = descriptor['deviceId'], 'a' * 64
        token = 'cmbroute1_' + mac(self.secret, 'gateway-route-v1\n' + lan)
        key = self.auth.key(token)
        ident = hashlib.sha256(key.encode()).hexdigest()
        self.assertEqual(self.discovery.prove(ident, nonce, lan, device)['proof'], mac(key, '\n'.join((device,lan,nonce))))
        with self.assertRaises(PermissionError): self.discovery.prove(ident, nonce, public, device)
        with self.assertRaises(PermissionError): self.discovery.prove(ident, nonce, lan, 'f'*32)
        child = self.discovery.resolve_cookie(token, lan)
        self.assertEqual(self.auth.get(child)['csrf'], self.auth.get(self.secret)['csrf'])
        self.assertFalse(self.discovery.resolve_cookie(token, public))
        self.assertIsNone(self.auth.get(token))
        # A child session cannot issue credentials for newly added origins.
        extra = 'https://other.example.org'
        self.gateway.origins.add(extra)
        self.discovery.mobile_routes(child)
        self.assertNotIn(extra,self.auth.get(child)['routeBindings'])
        self.auth.logout(child)
        self.assertIsNone(self.auth.get(self.secret))
        self.assertFalse(self.discovery.resolve_cookie(token,lan))
        with self.assertRaises(PermissionError): self.discovery.prove(ident,nonce,lan,device)

    def test_restart_replaces_quick_url_and_scoped_phone_can_authorize_exact_new_origin(self):
        lan, old, new = 'http://192.168.1.7:8788','https://old.example.com','https://new.example.com'
        self.gateway.origins = {lan, old}
        published = [old,lan]
        self.discovery.published = lambda: published
        d = self.discovery.mobile_routes(self.secret)
        self.assertEqual(d['endpoints'], [old,lan])
        def derived(url): return 'cmbroute1_' + mac(self.secret, 'gateway-route-v1\n'+url)
        child=self.discovery.resolve_cookie(derived(lan),lan)
        self.gateway.origins.add(new);published[:]=[new,lan]
        updated=self.discovery.mobile_routes(child)
        self.assertEqual(updated['unbound'],[new]);self.assertNotIn(old,updated['endpoints'])
        digest=self.auth.key(derived(new))
        msg='\n'.join(('route-grant-v1',d['deviceId'],new,digest))
        claim={'deviceId':d['deviceId'],'anchor':old,'bindings':{new:{'digest':digest,'proof':mac(self.auth.key(derived(lan)),msg)}}}
        with self.assertRaises(PermissionError):self.discovery.renew(child,claim)
        claim['bindings'][new]['proof']=mac(self.auth.key(mac(self.secret,'gateway-route-renew-v1')),msg)
        self.discovery.renew(child,claim)
        actual=self.discovery.resolve_cookie(derived(new),new)
        self.assertEqual(self.auth.get(actual)['csrf'],self.auth.get(self.secret)['csrf'])
        self.assertFalse(self.discovery.resolve_cookie(derived(new),lan))
        self.assertFalse(self.discovery.resolve_cookie(derived(old),old))
