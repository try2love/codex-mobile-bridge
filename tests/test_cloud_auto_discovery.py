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
                       'preview_private': base64.b64encode(new_key()).decode(), 'device_token': 't' * 43,
                       'access_token': 'account-token', 'access_deadline': 2**53 - 1}
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
            return {'device_discovery': 'account_directory_v2', 'pairing_method': 'desktop_qr_v1'}
        if path == '/v1/gateway/bindings':
            return {'bindings': [copy.deepcopy(self.record)]}
        if method == 'GET':
            return copy.deepcopy(self.record)
        if method == 'DELETE' and path == '/v1/bindings/' + self.binding:
            self.record.update(status='revoked', access_revision=self.record['access_revision'] + 1)
            return {'revoked': True}
        if path.endswith('/access'):
            if value['allowed'] and value['expected_revision'] != self.record['access_revision']:
                raise CloudError('state_conflict', 409)
            self.record.update(status='active' if value['allowed'] else 'disabled',
                               access_revision=self.record['access_revision'] + 1)
            return copy.deepcopy(self.record)
        raise AssertionError('unexpected request')

    def scan(self):
        proof = self.connector.record_proof(self.binding, self.mobile, self.proof)
        self.connector.record_proof(self.binding, self.mobile, {**self.proof, 'scan_token': proof['scan_token']})

    def allow(self):
        self.scan()
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': self.record['access_revision'], 'confirm_pairing': True})
        self.assertTrue(result['remote_access_confirmed'])

    def delete_value(self):
        return {'action': 'delete-device', 'origin': self.config['origin'], 'account_id': self.config['account_id'],
                'gateway_id': self.config['gateway_id'], 'binding_id': self.binding}

    def test_directory_is_not_authority_and_does_not_require_an_online_gateway(self):
        self.connector.ws = None
        devices = self.connector.control({'action': 'devices'})['devices']
        self.assertEqual(devices, [])
        self.assertIsNone(self.connector.pin(self.binding))
        self.assertTrue(all(call.args[2:3] != ('POST',) for call in self.connector.accounts.request.call_args_list))
        self.assertEqual(self.connector.accounts.status()['bindings'], [])

    def test_no_missing_or_incorrect_proof_can_allow_connection(self):
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': 0, 'confirm_pairing': True})
        self.assertFalse(result['remote_access_confirmed'])
        self.assertIsNone(self.connector.pin(self.binding))
        self.connector.record_proof(self.binding, self.mobile, self.proof)
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': 0, 'confirm_pairing': True})
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

    def test_delete_fences_the_target_before_permanent_revocation_and_leaves_other_phones_alone(self):
        self.allow()
        self.record['mobile_online'] = False
        accounts = self.connector.accounts
        key, gateway = scope(self.config['origin'], self.config['account_id']), self.config['gateway_id']
        other = str(uuid.uuid4())
        pin = accounts.trusted_pin(self.config, self.binding)
        write_json(self.directory / 'cloud-bindings.json', {key: {self.binding: pin, other: pin}})
        accounts.access.save(key, gateway, other, accounts.access.state(key, gateway, self.binding))
        def request(origin, path, method='GET', value=None, token=''):
            self.assertFalse(accounts.access.state(key, gateway, self.binding)['allowed'])
            self.assertIsNone(self.connector.pin(self.binding))
            return self.request(origin, path, method, value, token)
        accounts.request = Mock(side_effect=request)

        async def delete():
            self.connector.loop = asyncio.get_running_loop()
            self.connector.server = SimpleNamespace(auth=SimpleNamespace(logout=Mock()))
            target_task = asyncio.create_task(asyncio.sleep(100))
            other_task = asyncio.create_task(asyncio.sleep(100))
            self.connector.channels = {'target': SimpleNamespace(binding=self.binding, task=target_task),
                                       'other': SimpleNamespace(binding=other, task=other_task)}
            self.connector.sessions = {self.binding: ('target-token', {}), other: ('other-token', {})}
            try:
                self.assertEqual(await asyncio.to_thread(self.connector.control, self.delete_value()),
                                 {'deleted_binding_id': self.binding})
                self.assertTrue(target_task.cancelled())
                self.assertFalse(other_task.done())
                self.assertIn(other, self.connector.sessions)
                self.connector.server.auth.logout.assert_called_once_with('target-token')
            finally:
                self.connector.loop = None
                target_task.cancel()
                other_task.cancel()
                await asyncio.gather(target_task, other_task, return_exceptions=True)
        asyncio.run(delete())

        lookup, revoke = accounts.request.call_args_list
        self.assertEqual(lookup.args, (self.config['origin'], '/v1/gateway/bindings/' + self.binding))
        self.assertEqual(lookup.kwargs, {'token': self.secret['device_token']})
        self.assertEqual(revoke.args, (self.config['origin'], '/v1/bindings/' + self.binding, 'DELETE', None, 'account-token'))
        self.assertEqual(self.record['status'], 'revoked')
        self.assertIsNone(accounts.trusted_pin(self.config, self.binding))
        self.assertEqual(accounts.access.state(key, gateway, self.binding)['generation'], 0)
        self.assertEqual(self.connector.pin(other), pin)

    def test_delete_checks_local_scope_and_remote_binding_ownership(self):
        self.allow()
        accounts = self.connector.accounts
        accounts.request.reset_mock()
        for field, wrong_value in [('origin', 'https://other.example.test'), ('account_id', str(uuid.uuid4())), ('gateway_id', str(uuid.uuid4()))]:
            with self.subTest(field=field):
                with self.assertRaisesRegex(CloudError, 'credential_scope_mismatch'):
                    self.connector.control({**self.delete_value(), field: wrong_value})
        accounts.request.assert_not_called()
        self.assertIsNotNone(self.connector.pin(self.binding))

        for change in [{'gateway_id': str(uuid.uuid4())}, {'id': str(uuid.uuid4())}, {'status': 'pending'}]:
            with self.subTest(change=change):
                accounts.request = Mock(return_value={**self.record, **change})
                with self.assertRaisesRegex(CloudError, 'device_access_unavailable'):
                    accounts.control(self.delete_value())
                accounts.request.assert_called_once_with(self.config['origin'], '/v1/gateway/bindings/' + self.binding,
                                                         token=self.secret['device_token'])
                self.assertIsNotNone(accounts.trusted_pin(self.config, self.binding))

    def test_delete_with_an_unconfirmed_receipt_is_fenced_and_reconciled_by_reads_only(self):
        self.allow()
        accounts = self.connector.accounts
        def lose_receipt(origin, path, method='GET', value=None, token=''):
            result = self.request(origin, path, method, value, token)
            return {'revoked': 1} if method == 'DELETE' else result
        accounts.request = Mock(side_effect=lose_receipt)
        with self.assertRaisesRegex(CloudError, 'device_delete_unconfirmed'):
            accounts.control(self.delete_value())
        self.assertEqual(sum(call.args[2:3] == ('DELETE',) for call in accounts.request.call_args_list), 1)
        restored = Connector(self.directory, None)
        self.addCleanup(restored.notifications.close)
        restored.config = self.config
        restored.accounts.secret = accounts.secret
        restored.accounts.request = Mock(side_effect=self.request)
        self.assertIsNotNone(restored.accounts.trusted_pin(self.config, self.binding))
        self.assertIsNone(restored.pin(self.binding))
        self.assertEqual(restored.accounts.status()['bindings'][0]['sync_state'], 'unconfirmed')
        self.assertEqual(restored.control({'action': 'devices'}), {'devices': []})
        self.assertIsNone(restored.accounts.trusted_pin(self.config, self.binding))
        self.assertFalse(any(call.args[2:3] == ('DELETE',) for call in restored.accounts.request.call_args_list))

    def test_late_open_response_cannot_restore_a_deleted_binding(self):
        self.allow()
        reached, release = threading.Event(), threading.Event()
        def delay_open(origin, path, method='GET', value=None, token=''):
            result = self.request(origin, path, method, value, token)
            if method == 'POST' and value['allowed']:
                reached.set()
                if not release.wait(5):
                    raise AssertionError('deletion was blocked by opening')
            return result
        self.connector.accounts.request = Mock(side_effect=delay_open)
        output = []
        worker = threading.Thread(target=lambda: output.append(self.connector.control({
            'action': 'access', 'binding_id': self.binding, 'allowed': True, 'expected_revision': 1})))
        worker.start()
        try:
            self.assertTrue(reached.wait(5))
            self.assertEqual(self.connector.control(self.delete_value()), {'deleted_binding_id': self.binding})
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(output[0]['allowed'])
        self.assertEqual(self.record['status'], 'revoked')
        self.assertIsNone(self.connector.accounts.trusted_pin(self.config, self.binding))

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
        self.scan()
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
            'expected_revision': 0, 'confirm_pairing': True})))
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
        self.scan()
        def lost(origin, path, method='GET', value=None, token=''):
            result = self.request(origin, path, method, value, token)
            if method == 'POST': raise CloudError('network_or_certificate_error')
            return result
        self.connector.accounts.request = Mock(side_effect=lost)
        result = self.connector.control({'action': 'access', 'binding_id': self.binding, 'allowed': True,
                                         'expected_revision': 0, 'confirm_pairing': True})
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
