import base64
import tempfile
import copy
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bridge.features.network.cloud_account import CloudError, scope
from bridge.features.network.cloud_connector import Connector
from bridge.features.network.cloud_crypto import new_key, public_key, pairing_safety_code
from bridge.features.notifications.channels import read_json, write_json


class DeviceProofs(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.connector = Connector(self.directory, None)
        self.addCleanup(self.connector.notifications.close)
        self.config = {'origin': 'https://relay.example.test', 'account_id': str(uuid.uuid4()),
                       'gateway_id': str(uuid.uuid4()), 'login': 'test', 'enabled': True}
        write_json(self.connector.accounts.path, self.config)
        self.connector.config = self.config
        self.connector.secret = {'control_private': base64.b64encode(new_key()).decode(),
                                 'preview_private': base64.b64encode(new_key()).decode(), 'device_token': 't' * 43}
        self.connector.accounts.secret = Mock(return_value=self.connector.secret)
        self.connector.policy_identity = self.connector.account_identity(self.config)
        self.connector.entitlement_deadline = None
        self.connector.ws = SimpleNamespace(closed=False)
        self.binding = str(uuid.uuid4())
        self.mobile = public_key(new_key())
        self.record = {'id': self.binding, 'gateway_id': self.config['gateway_id'], 'installation_id': str(uuid.uuid4()),
                       'gateway_public_key': base64.b64encode(public_key(base64.b64decode(self.connector.secret['control_private']))).decode(),
                       'mobile_public_key': base64.b64encode(self.mobile).decode(), 'status': 'pending', 'device_name': 'Test phone', 'access_revision': 0}
        self.proof = {'kind': 'pair', 'request_id': self.binding, 'device_name': 'Test phone',
                      'preview_public_key': base64.b64encode(public_key(new_key())).decode()}
        self.connector.accounts.request = Mock(side_effect=self.request)
        self.connector.accounts.device_request = Mock(side_effect=lambda path, method='GET', value=None:
            self.request(self.config['origin'], path, method, value))

    def request(self, origin, path, method='GET', value=None, token=''):
        if path == '/v1/capabilities':
            return {'device_discovery': 'account_directory_v1', 'binding_access': 'desktop_switch_v1'}
        if path == '/v1/gateway/bindings':
            return {'bindings': [copy.deepcopy(self.record)]}
        if method == 'GET':
            return copy.deepcopy(self.record)
        if path == '/v1/gateway/bindings/' + self.binding + '/access':
            self.record.update(status='active' if value['allowed'] else 'disabled', access_revision=self.record['access_revision'] + 1)
            return copy.deepcopy(self.record)
        raise AssertionError('unexpected cloud operation')

    def allow(self, code):
        return self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                       'expected_revision': self.record['access_revision'], 'safety_code': code})

    def test_verified_proofs_are_durable_but_are_not_permission(self):
        result = self.connector.record_proof(self.binding, self.mobile, self.proof)
        self.assertRegex(result['safety_code'], r'^[0-9A-F]{8}( [0-9A-F]{8}){7}$')
        devices = self.connector.control({'action': 'devices'})['devices']
        self.assertTrue(devices[0]['proof_verified'])
        self.assertFalse(devices[0]['allowed'])
        self.assertNotIn('deadline', self.connector.saved_proofs()[self.binding])
        restored = Connector(self.directory, None)
        self.addCleanup(restored.notifications.close)
        restored.config, restored.secret = self.config, self.connector.secret
        restored.ws = SimpleNamespace(closed=False)
        restored.policy_identity = restored.account_identity(self.config)
        restored.entitlement_deadline = None
        restored.accounts.device_request = self.connector.accounts.device_request
        restored.accounts.secret = self.connector.accounts.secret
        restored.accounts.request = self.connector.accounts.request
        self.assertEqual(restored.control({'action': 'devices'})['devices'], devices)
        self.assertEqual(self.connector.accounts.status()['bindings'], [])

    def test_no_server_record_or_missing_encrypted_proof_can_grant_access(self):
        self.connector.accounts.device_request = Mock(return_value={**self.record, 'mobile_public_key': base64.b64encode(public_key(new_key())).decode()})
        with self.assertRaises(PermissionError):
            self.connector.record_proof(self.binding, self.mobile, self.proof)
        self.assertFalse(self.allow('incorrect')['allowed'])
        self.assertEqual(self.connector.accounts.status()['bindings'], [])

    def test_approval_rechecks_the_exact_device_and_safety_code(self):
        result = self.connector.record_proof(self.binding, self.mobile, self.proof)
        self.assertFalse(self.allow('incorrect')['allowed'])
        self.assertEqual(self.connector.accounts.status()['bindings'], [])
        self.assertTrue(self.allow(result['safety_code'])['allowed'])
        self.assertEqual(self.connector.accounts.status()['bindings'][0]['device_name'], 'Test phone')

    def test_retired_cloud_actions_are_rejected_without_network_or_data_changes(self):
        result = self.connector.record_proof(self.binding, self.mobile, self.proof)
        before = self.connector.saved_proofs()
        self.connector.accounts.request.reset_mock()
        self.connector.accounts.device_request.reset_mock()
        for action in ('candidates', 'approve', 'reject', 'revoke'):
            with self.assertRaisesRegex(ValueError, 'unsupported cloud operation'):
                self.connector.control({'action': action, 'binding_id': self.binding, 'safety_code': result['safety_code']})
        self.connector.accounts.request.assert_not_called()
        self.connector.accounts.device_request.assert_not_called()
        self.assertEqual(self.connector.accounts.status()['bindings'], [])
        self.assertEqual(self.connector.saved_proofs(), before)

    def test_changed_preview_or_server_binding_cannot_reuse_a_pending_identity(self):
        result = self.connector.record_proof(self.binding, self.mobile, self.proof)
        with self.assertRaises(PermissionError):
            self.connector.record_proof(self.binding, self.mobile, {**self.proof, 'preview_public_key': base64.b64encode(public_key(new_key())).decode()})
        self.record['status'] = 'revoked'
        self.assertFalse(self.allow(result['safety_code'])['allowed'])

    def test_saved_keys_without_an_access_grant_never_authorize(self):
        key = scope(self.config['origin'], self.config['account_id'])
        write_json(self.directory / 'cloud-bindings.json', {key: {self.binding: {
            'gateway_id': self.config['gateway_id'], 'mobile_public_key': self.record['mobile_public_key'],
            'preview_public_key': self.proof['preview_public_key']}}})
        self.assertIsNone(self.connector.pin(self.binding))
        self.assertFalse(self.connector.accounts.status()['bindings'][0]['allowed'])

    def test_safety_code_matches_both_devices_and_is_bound_to_all_keys(self):
        private = bytes(range(1, 33))
        other = bytes(range(33, 65))
        desktop, mobile = public_key(private), public_key(other)
        dpreview, mpreview = public_key(b'D' * 32), public_key(b'M' * 32)
        gateway = '11111111-1111-4111-8111-111111111111'
        binding = '22222222-2222-4222-8222-222222222222'
        left = pairing_safety_code(self.config['origin'], gateway, binding, private, mobile, desktop, mobile, dpreview, mpreview)
        right = pairing_safety_code(self.config['origin'], gateway, binding, other, desktop, desktop, mobile, dpreview, mpreview)
        self.assertEqual(left, right)
        self.assertNotEqual(left, pairing_safety_code(self.config['origin'], gateway, binding, private, mobile, desktop, mobile, mpreview, dpreview))
        self.assertEqual(left, '49B47ECB D96CF844 161D3BED 6460DFBF 159F38A3 09DE41DF 6EBB82D3 A96A5E78')
