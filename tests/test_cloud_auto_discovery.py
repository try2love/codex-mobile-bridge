import asyncio
import base64
import copy
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.network.cloud_account import CloudError, scope
from bridge.features.network.cloud_connector import Connector
from bridge.features.network.cloud_crypto import new_key, public_key
from bridge.features.notifications.channels import read_json, write_json


class CloudAutomaticDiscovery(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.connector = Connector(self.directory, None)
        self.addCleanup(self.connector.notifications.close)
        self.config = {'origin': 'https://relay.example.test', 'account_id': str(uuid.uuid4()),
                       'gateway_id': str(uuid.uuid4()), 'login': 'user', 'enabled': True}
        write_json(self.connector.accounts.path, self.config)
        self.connector.config = self.config
        self.secret = {'control_private': base64.b64encode(new_key()).decode(),
                       'preview_private': base64.b64encode(new_key()).decode(), 'device_token': 't' * 43}
        self.connector.secret = self.secret
        self.connector.accounts.secret = Mock(return_value=self.secret)
        self.connector.policy_identity = self.connector.account_identity(self.config)
        self.connector.entitlement_deadline = None
        self.connector.ws = SimpleNamespace(closed=False)
        self.binding = str(uuid.uuid4())
        self.mobile = public_key(new_key())
        self.record = {'id': self.binding, 'gateway_id': self.config['gateway_id'],
                       'gateway_public_key': base64.b64encode(public_key(base64.b64decode(self.secret['control_private']))).decode(),
                       'installation_id': str(uuid.uuid4()), 'mobile_public_key': base64.b64encode(self.mobile).decode(),
                       'status': 'pending', 'device_name': 'Test phone', 'access_revision': 0, 'mobile_online': True}
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
        if path.endswith('/access'):
            if value['allowed'] and value['expected_revision'] != self.record['access_revision']:
                raise CloudError('state_conflict', 409)
            self.record.update(status='active' if value['allowed'] else 'disabled',
                               access_revision=self.record['access_revision'] + 1)
            return copy.deepcopy(self.record)
        raise AssertionError('unexpected request')

    def allow(self):
        proof = self.connector.record_proof(self.binding, self.mobile, self.proof)
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': self.record['access_revision'], 'safety_code': proof['safety_code']})
        self.assertTrue(result['remote_access_confirmed'])

    def test_directory_is_not_authority_and_does_not_require_an_online_gateway(self):
        self.connector.ws = None
        devices = self.connector.control({'action': 'devices'})['devices']
        self.assertEqual(len(devices), 1)
        self.assertFalse(devices[0]['allowed'])
        self.assertFalse(devices[0]['proof_verified'])
        self.assertIsNone(self.connector.pin(self.binding))
        self.assertTrue(all(call.args[2:3] != ('POST',) for call in self.connector.accounts.request.call_args_list))
        self.assertEqual(self.connector.accounts.status()['bindings'], [])

    def test_no_missing_or_incorrect_proof_can_allow_connection(self):
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': 0, 'safety_code': 'incorrect'})
        self.assertFalse(result['remote_access_confirmed'])
        self.assertIsNone(self.connector.pin(self.binding))
        self.connector.record_proof(self.binding, self.mobile, self.proof)
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': 0, 'safety_code': 'incorrect'})
        self.assertFalse(result['remote_access_confirmed'])
        self.assertEqual(self.record['status'], 'pending')

    def test_disable_keeps_trust_and_restore_needs_no_phone_request(self):
        self.allow()
        before = self.connector.accounts.trusted_pin(self.config, self.binding)
        blocked = []
        result = self.connector.accounts.set_binding_access({'binding_id': self.binding, 'allowed': False,
                                                             'expected_revision': 1}, lambda binding: blocked.append(self.connector.pin(binding)))
        self.assertEqual(blocked, [None])
        self.assertTrue(result['remote_access_confirmed'])
        self.assertEqual(self.connector.accounts.trusted_pin(self.config, self.binding), before)
        self.assertIsNone(self.connector.pin(self.binding))
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True, 'expected_revision': 2})
        self.assertTrue(result['allowed'])
        self.assertEqual(self.connector.pin(self.binding), before)
        self.assertFalse(any('pairing-requests' in call.args[1] for call in self.connector.accounts.request.call_args_list))

    def test_offline_close_remains_blocked_after_restart_without_losing_keys(self):
        self.allow()
        before = read_json(self.directory / 'cloud-bindings.json', {})
        self.connector.accounts.request = Mock(side_effect=CloudError('network_or_certificate_error'))
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': False, 'expected_revision': 1})
        self.assertFalse(result['remote_access_confirmed'])
        self.assertEqual(read_json(self.directory / 'cloud-bindings.json', {}), before)
        restored = Connector(self.directory, None)
        self.addCleanup(restored.notifications.close)
        restored.config = self.config
        restored.secret = self.secret
        restored.policy_identity = restored.account_identity(self.config)
        restored.entitlement_deadline = None
        self.assertIsNone(restored.pin(self.binding))
        self.assertEqual(restored.accounts.status()['bindings'][0]['sync_state'], 'unconfirmed')

    def test_vault_failure_cannot_prevent_the_local_close_fence(self):
        self.allow()
        self.connector.accounts.secret = Mock(side_effect=CloudError('native_vault_unavailable'))
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': False, 'expected_revision': 1})
        self.assertFalse(result['remote_access_confirmed'])
        self.assertIsNone(self.connector.pin(self.binding))
        key = scope(self.config['origin'], self.config['account_id'])
        self.assertFalse(self.connector.accounts.access.state(key, self.config['gateway_id'], self.binding)['allowed'])

    def test_storage_failure_still_blocks_the_running_gateway_and_is_not_acknowledged(self):
        self.allow()
        with patch.object(self.connector.accounts.access, 'save', side_effect=OSError('storage unavailable')):
            with self.assertRaisesRegex(CloudError, 'device_access_storage_error'):
                self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': False, 'expected_revision': 1})
        self.assertIsNone(self.connector.pin(self.binding))
        self.assertFalse(self.connector.control({'action': 'devices'})['devices'][0]['allowed'])

    def test_late_open_response_cannot_override_a_local_close(self):
        proof = self.connector.record_proof(self.binding, self.mobile, self.proof)
        reached = threading.Event()
        release = threading.Event()
        def delayed(origin, path, method='GET', value=None, token=''):
            response = self.request(origin, path, method, value, token)
            if method == 'POST' and value['allowed']:
                reached.set()
                if not release.wait(5): raise AssertionError('close was blocked by opening')
            return response
        self.connector.accounts.request = Mock(side_effect=delayed)
        output = []
        worker = threading.Thread(target=lambda: output.append(self.connector.control({
            'action': 'access', 'binding_id': self.binding, 'allowed': True,
            'expected_revision': 0, 'safety_code': proof['safety_code']})))
        worker.start()
        self.assertTrue(reached.wait(5))
        try:
            closed = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': False, 'expected_revision': 0})
            self.assertTrue(closed['remote_access_confirmed'])
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(output[0]['allowed'])
        self.assertIsNone(self.connector.pin(self.binding))
        self.assertEqual(self.record['status'], 'disabled')

    def test_unknown_opening_is_not_replayed_or_locally_accepted_by_directory_reads(self):
        proof = self.connector.record_proof(self.binding, self.mobile, self.proof)
        def lost(origin, path, method='GET', value=None, token=''):
            result = self.request(origin, path, method, value, token)
            if method == 'POST': raise CloudError('network_or_certificate_error')
            return result
        self.connector.accounts.request = Mock(side_effect=lost)
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': 0, 'safety_code': proof['safety_code']})
        self.assertFalse(result['allowed'])
        self.connector.accounts.request = Mock(side_effect=self.request)
        self.assertFalse(self.connector.control({'action': 'devices'})['devices'][0]['allowed'])
        self.assertIsNone(self.connector.pin(self.binding))
        self.assertTrue(all(call.args[2:3] != ('POST',) for call in self.connector.accounts.request.call_args_list))
        restored = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True, 'expected_revision': 1})
        self.assertTrue(restored['allowed'])

    def test_changed_directory_key_blocks_but_does_not_replace_a_pin(self):
        self.allow()
        pin = self.connector.accounts.trusted_pin(self.config, self.binding)
        self.record['mobile_public_key'] = base64.b64encode(public_key(new_key())).decode()
        device = self.connector.control({'action': 'devices'})['devices'][0]
        self.assertEqual(device['sync_state'], 'identity_changed')
        self.assertFalse(device['allowed'])
        self.assertEqual(self.connector.accounts.trusted_pin(self.config, self.binding), pin)

    def test_target_close_does_not_cancel_another_phones_channel(self):
        async def exercise():
            self.connector.server = SimpleNamespace(auth=SimpleNamespace(logout=Mock()))
            target = asyncio.create_task(asyncio.sleep(100))
            other = asyncio.create_task(asyncio.sleep(100))
            await asyncio.sleep(0)
            self.connector.channels = {'target': SimpleNamespace(binding=self.binding, task=target),
                                       'other': SimpleNamespace(binding=str(uuid.uuid4()), task=other)}
            self.connector.sessions = {self.binding: ('target-token', {})}
            await self.connector.block_binding(self.binding)
            self.assertTrue(target.cancelled())
            self.assertFalse(other.done())
            self.connector.server.auth.logout.assert_called_once_with('target-token')
            other.cancel()
            await asyncio.gather(other, return_exceptions=True)
        asyncio.run(exercise())
