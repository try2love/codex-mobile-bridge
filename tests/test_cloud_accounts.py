import asyncio
import base64
import json
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bridge.features.network.cloud_account import CloudError, Controller, scope, verified_entitlement
from bridge.features.network.cloud_connector import Connector, validate_request
from bridge.features.network.cloud_crypto import exact_origin, new_key, public_key
from bridge.features.notifications.channels import read_json, save_settings, settings, write_json


class Vault:
    def __init__(self):
        self.values = {}

    def get_password(self, service, key):
        return self.values.get((service, key))

    def set_password(self, service, key, value):
        self.values[service, key] = value


class CloudAccountsTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.vault = Vault()
        self.controller = Controller(self.directory)
        self.controller._vault = lambda: (self.vault, 'test-only')
        self.config = {'origin': 'https://relay.example.test', 'account_id': str(uuid.uuid4()),
                       'gateway_id': str(uuid.uuid4()), 'login': 'local-user', 'enabled': True}
        write_json(self.controller.path, self.config)
        self.secret = {**self.config, 'access_token': 'test-access', 'refresh_token': 'test-refresh',
                       'device_token': 'test-device', 'access_deadline': time.time() + 600,
                       'control_private': base64.b64encode(new_key()).decode(),
                       'preview_private': base64.b64encode(new_key()).decode()}
        self.controller.save_secret(self.config, self.secret)

    def web_change(self, allowed, revision=0):
        return {'action': 'web-access', 'allowed': allowed, 'expected_revision': revision,
                **{key: self.config[key] for key in ('origin', 'account_id', 'gateway_id')}}

    def test_web_access_defaults_off_persists_and_never_uses_cloud_network(self):
        self.controller.request = Mock(side_effect=AssertionError('unexpected cloud request'))
        before = self.controller.path.read_bytes()
        self.assertEqual(self.controller.status()['web_access'], {'allowed': False, 'revision': 0, 'error': ''})
        result = self.controller.control(self.web_change(True))
        self.assertEqual(result['web_access'], {'allowed': True, 'revision': 1, 'error': ''})
        self.assertTrue(Controller(self.directory).web_access_state()['allowed'])
        for key, other in [('origin', 'https://another.example.test'), ('account_id', str(uuid.uuid4())), ('gateway_id', str(uuid.uuid4()))]:
            self.assertFalse(self.controller.web_access_state({**self.config, key: other})['allowed'])
        self.assertEqual(self.controller.path.read_bytes(), before, 'phone relay configuration must not change')
        self.controller.control(self.web_change(False, 1))
        self.assertFalse(Controller(self.directory).web_access_state()['allowed'])
        self.controller.request.assert_not_called()

    def test_web_access_rejects_stale_identity_revision_and_non_boolean(self):
        self.controller.control(self.web_change(True))
        for change in [{'allowed': 'false'}, {'allowed': 0}, {'expected_revision': True},
                       {'expected_revision': 0}, {'expected_revision': 2**53-1},
                       {'account_id': str(uuid.uuid4())}, {'gateway_id': str(uuid.uuid4())},
                       {'origin': 'https://elsewhere.example.test'}, {'unexpected': True}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.controller.control({**self.web_change(False, 1), **change})
        self.assertTrue(self.controller.web_access_state()['allowed'])
        for content in ['[]', 'invalid json', json.dumps({scope(self.config['origin'], self.config['account_id'])+':'+self.config['gateway_id']: {'allowed': 'true', 'revision': 1}})]:
            self.controller.web_access.path.write_text(content)
            self.assertFalse(self.controller.web_access_state()['allowed'])
            with self.assertRaisesRegex(CloudError, 'web_access_storage_error'):
                self.controller.control(self.web_change(True))

    def test_failed_web_disable_fences_runtime_without_touching_phone_channels(self):
        self.controller.control(self.web_change(True))
        connector = self.connector()
        connector.channels = {'phone': object()}
        generation = connector.policy_generation
        with patch.object(self.controller.web_access, 'save', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(CloudError, 'web_access_storage_error'):
                connector.control(self.web_change(False, 1))
        self.assertIn(connector.account_identity(self.config), connector.blocked_web_scopes)
        status = connector.control({'action': 'status'})
        self.assertFalse(status['web_access']['allowed'])
        self.assertEqual(status['web_access']['error'], 'web_access_storage_error')
        self.assertEqual(set(connector.channels), {'phone'})
        self.assertEqual(connector.policy_generation, generation)
        self.assertTrue(self.controller.web_access_state()['allowed'], 'failure must not claim persisted denial')
        connector.control(self.web_change(False, 1))
        self.assertNotIn(connector.account_identity(self.config), connector.blocked_web_scopes)
        self.assertFalse(self.controller.web_access_state()['allowed'])

    def entitlement(self, seconds=600, *, required=True, active=True):
        now = datetime.now(timezone.utc)
        return {'required': required, 'active': active, 'server_time': now.isoformat(),
                'expires_at': (now + timedelta(seconds=seconds)).isoformat() if required else None}

    def receipt(self):
        now = datetime.now(timezone.utc)
        return {'id': str(uuid.uuid4()), 'days': 30, 'created_at': now.isoformat(),
                'expires_at': (now + timedelta(days=30)).isoformat()}

    def connector(self):
        connector = Connector(self.directory, None)
        connector.accounts = self.controller
        self.addCleanup(connector.notifications.close)
        return connector

    def test_cloud_refresh_preserves_saved_notification_preferences(self):
        save_settings(self.directory, {
            'mobileEnabled': False, 'mobileAppLinks': False,
            'barkEnabled': True, 'barkKey': 'synthetic-bark-key',
            'includeTitle': False, 'addressEnabled': False,
        })
        path = self.directory / 'notifications.json'
        prior = path.read_bytes()
        self.controller.request = Mock(return_value=self.entitlement())
        self.controller.refresh_entitlement()
        self.assertEqual(path.read_bytes(), prior)
        self.controller.request = Mock(side_effect=CloudError('network_or_certificate_error'))
        with self.assertRaises(CloudError):
            self.controller.refresh_entitlement()
        restored = Controller(self.directory)
        restored._vault = self.controller._vault
        restored.request = Mock(return_value=self.entitlement())
        restored.refresh_entitlement()
        self.assertEqual(path.read_bytes(), prior)

    def test_cloud_refresh_does_not_persist_notification_defaults(self):
        self.assertTrue(settings(self.directory)['mobileEnabled'])
        self.assertTrue(settings(self.directory)['includeTitle'])
        self.controller.status()
        self.assertFalse((self.directory / 'notifications.json').exists())
        for entitlement in [self.entitlement(), self.entitlement(active=False), self.entitlement(required=False)]:
            with self.subTest(entitlement=entitlement):
                self.controller.request = Mock(return_value=entitlement)
                self.controller.refresh_entitlement()
                self.assertFalse((self.directory / 'notifications.json').exists())
        self.controller.request = Mock(side_effect=CloudError('network_or_certificate_error'))
        with self.assertRaises(CloudError):
            self.controller.refresh_entitlement()
        self.assertFalse((self.directory / 'notifications.json').exists())

    def test_cloud_refresh_ignores_legacy_inbox_migration_marker(self):
        path = self.directory / 'notifications.json'
        for marker in (False, True):
            with self.subTest(marker=marker):
                prior = {'mobileEnabled': False, 'cloudMobileDefaultApplied': marker,
                         'addressName': 'Synthetic gateway'}
                write_json(path, prior)
                self.controller.request = Mock(return_value=self.entitlement())
                self.controller.refresh_entitlement()
                self.assertEqual(read_json(path, {}), prior)

    def test_exact_origin_and_scope_isolation(self):
        for origin in ('https://relay.example.test:08443', 'https://relay.example.test:0',
                       'https://127.1', 'https://0177.0.0.1', 'https://0x7f000001',
                       'https://[0:0:0:0:0:0:0:1]', 'https://relay.example.test\n',
                       'https://relay.example.test\\other', 'https://relay.example.test/'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                exact_origin(origin)
        for origin in ('https://relay.example.test:8443', 'https://127.0.0.1', 'https://[::1]'):
            self.assertEqual(exact_origin(origin), origin)
        other = {**self.config, 'origin': 'https://selfhost.example.test'}
        self.assertNotEqual(scope(other['origin'], other['account_id']), scope(self.config['origin'], self.config['account_id']))
        with self.assertRaises(CloudError):
            self.controller.secret(other)
        with self.assertRaises(CloudError):
            self.controller.save_secret(other, self.secret)

    def test_refresh_attempt_is_persisted_and_same_request_recovers(self):
        self.secret['access_deadline'] = 0
        self.controller.save_secret(self.config, self.secret)
        def lost_response(origin, path, method, value, token=''):
            self.assertEqual(path, '/v1/auth/refresh')
            self.assertEqual(self.controller.secret()['refresh_token'], 'test-refresh')
            self.assertEqual(self.controller.secret()['refresh_request_id'], value['request_id'])
            raise CloudError('network_or_certificate_error')
        self.controller.request = Mock(side_effect=lost_response)
        with self.assertRaises(CloudError):
            self.controller.account_request('/v1/account/entitlement')
        with self.assertRaises(CloudError) as error:
            self.controller.account_request('/v1/account/entitlement')
        self.assertEqual(error.exception.code, 'network_or_certificate_error')
        self.assertEqual(self.controller.request.call_count, 2)
        self.assertEqual(self.controller.request.call_args_list[0].args[3], self.controller.request.call_args_list[1].args[3])

    def test_parallel_account_reads_rotate_once(self):
        self.secret['access_deadline'] = 0
        self.controller.save_secret(self.config, self.secret)
        rotations = []
        def request(origin, path, method='GET', value=None, token=''):
            if path == '/v1/auth/refresh':
                rotations.append(value['refresh_token'])
                time.sleep(.04)
                return {'account': {'id': self.config['account_id']}, 'access_token': 'new-access',
                        'refresh_token': 'new-refresh', 'expires_in': 600}
            self.assertEqual(token, 'new-access')
            return {'active': True}
        self.controller.request = request
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.controller.account_request('/v1/account/entitlement'))) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(results), 3)
        self.assertEqual(rotations, ['test-refresh'])

    def test_redemption_retry_reuses_id_without_storing_plaintext(self):
        calls = []
        receipt = self.receipt()
        entitlement = self.entitlement()
        def request(origin, path, method='GET', value=None, token=''):
            if path == '/v1/account/redeem':
                calls.append(value)
                if len(calls) == 1:
                    raise CloudError('network_or_certificate_error')
                return receipt
            self.assertEqual((path, method), ('/v1/account/entitlement', 'GET'))
            return entitlement
        self.controller.request = request
        with self.assertRaises(CloudError):
            self.controller.control({'action': 'redeem', 'code': 'test-only-code'})
        self.controller.control({'action': 'redeem', 'code': 'test-only-code'})
        self.assertEqual(calls[0]['request_id'], calls[1]['request_id'])
        self.assertNotIn('test-only-code', (self.directory / 'cloud-redemption.json').read_text())

    def test_logout_fails_closed_when_network_fails(self):
        self.controller.request = Mock(side_effect=CloudError('network_or_certificate_error'))
        result = self.controller.control({'action': 'logout'})
        self.assertTrue(result['logged_out'])
        self.assertFalse(result['configured'])
        self.assertFalse(result['remote_logout_confirmed'])
        self.assertEqual(result['logout_error'], 'network_or_certificate_error')
        self.assertEqual(self.controller.config(), {})
        self.assertFalse(self.controller.status()['configured'])
        for key in ('access_token', 'refresh_token', 'device_token'):
            self.assertEqual(self.controller.secret(self.config)[key], '')
        with self.assertRaises(ValueError):
            self.controller.control({'action': 'enabled', 'enabled': True})

    def test_logout_clears_local_authority_before_network_without_refreshing(self):
        connector = self.connector()
        connector.config = self.config
        connector.policy_identity = connector.account_identity(self.config)
        connector.entitlement_deadline = time.monotonic() + 100
        connector.ws = SimpleNamespace(closed=False)
        self.secret['access_deadline'] = 0
        self.secret['refresh_token'] = ''
        self.controller.save_secret(self.config, self.secret)
        self.assertTrue(connector.relay_current())

        def request(origin, path, method, value, token=''):
            self.assertFalse(connector.relay_current())
            self.assertEqual(self.controller.config(), {})
            for name in ('access_token', 'refresh_token', 'device_token'):
                self.assertEqual(self.controller.secret(self.config)[name], '')
            self.assertEqual((origin, path, method, value, token),
                             (self.config['origin'], '/v1/auth/logout', 'POST', {}, 'test-access'))
            return {'logged_out': True}

        self.controller.request = Mock(side_effect=request)
        result = connector.control({'action': 'logout'})
        self.assertTrue(result['logged_out'])
        self.assertTrue(result['remote_logout_confirmed'])
        self.assertEqual(result['logout_error'], '')
        self.assertEqual(result['credential_clear_error'], '')
        self.controller.request.assert_called_once()
        for name in ('control_private', 'preview_private', 'gateway_id'):
            self.assertEqual(self.controller.secret(self.config)[name], self.secret[name])

    def test_logout_with_missing_credentials_does_not_require_signing_in(self):
        self.vault.values.clear()
        self.controller.request = Mock()
        result = self.controller.control({'action': 'logout'})
        self.assertTrue(result['logged_out'])
        self.assertFalse(result['configured'])
        self.assertFalse(result['enabled'])
        self.assertFalse(result['remote_logout_confirmed'])
        self.assertEqual(result['logout_error'], 'login_required')
        self.assertEqual(result['credential_clear_error'], '')
        self.assertIsNone(result['entitlement'])
        self.assertEqual(result['bindings'], [])
        self.controller.request.assert_not_called()

    def test_logout_with_rejected_access_token_still_completes_locally(self):
        self.controller.request = Mock(side_effect=CloudError('authentication_required', 401))
        result = self.controller.control({'action': 'logout'})
        self.assertTrue(result['logged_out'])
        self.assertFalse(result['configured'])
        self.assertFalse(result['remote_logout_confirmed'])
        self.assertEqual(result['logout_error'], 'authentication_required')
        self.controller.request.assert_called_once_with(
            self.config['origin'], '/v1/auth/logout', 'POST', {}, 'test-access')

    def test_logout_with_unavailable_vault_fences_cloud_and_reports_cleanup_failure(self):
        for failure in ('backend', 'read', 'write'):
            with self.subTest(failure=failure):
                write_json(self.controller.path, self.config)
                self.controller._vault = lambda: (self.vault, 'test-only')
                self.vault.get_password = lambda service, key: self.vault.values.get((service, key))
                self.vault.set_password = lambda service, key, value: self.vault.values.__setitem__((service, key), value)
                if failure == 'backend':
                    self.controller._vault = Mock(side_effect=ValueError('vault unavailable'))
                elif failure == 'read':
                    self.vault.get_password = Mock(side_effect=OSError('vault unavailable'))
                else:
                    self.vault.set_password = Mock(side_effect=OSError('vault unavailable'))
                self.controller.request = Mock(return_value={'logged_out': True})
                result = self.controller.control({'action': 'logout'})
                self.assertTrue(result['logged_out'])
                self.assertFalse(result['configured'])
                self.assertEqual(self.controller.config(), {})
                self.assertEqual(result['credential_clear_error'], 'native_vault_unavailable')
                self.assertEqual(result['remote_logout_confirmed'], failure == 'write')
                self.assertEqual(self.controller.request.call_count, int(failure == 'write'))

    def test_logout_does_not_claim_success_when_local_configuration_cannot_be_cleared(self):
        before_vault = dict(self.vault.values)
        self.controller.request = Mock()
        with patch('bridge.features.network.cloud_account.write_json', side_effect=PermissionError('sharing denied')):
            with self.assertRaises(OSError):
                self.controller.control({'action': 'logout'})
        self.assertEqual(self.controller.config(), self.config)
        self.assertEqual(self.vault.values, before_vault)
        self.controller.request.assert_not_called()

    def test_logout_requires_remote_confirmation_and_repeated_local_logout_sends_nothing(self):
        self.controller.request = Mock(return_value={})
        result = self.controller.control({'action': 'logout'})
        self.assertFalse(result['configured'])
        self.assertTrue(result['logged_out'])
        self.assertFalse(result['remote_logout_confirmed'])
        self.assertEqual(result['logout_error'], 'logout_result_unconfirmed')
        again = self.controller.control({'action': 'logout'})
        self.assertTrue(again['logged_out'])
        self.assertFalse(again['configured'])
        self.controller.request.assert_called_once()

    def test_entitlement_read_enables_active_service_without_changing_keys_or_pins(self):
        write_json(self.controller.path, self.config)
        vault = dict(self.vault.values)
        value = self.entitlement()
        self.controller.request = Mock(return_value=value)
        result = self.controller.control({'action': 'entitlement'})
        self.controller.request.assert_called_once_with(self.config['origin'], '/v1/account/entitlement', 'GET', None, 'test-access')
        self.assertEqual(result, verified_entitlement(value))
        self.assertTrue(self.controller.config()['enabled'])
        self.assertTrue(self.controller.status()['enabled'])
        self.assertEqual(self.controller.status()['entitlement'], result)
        self.assertEqual(self.vault.values, vault)
        self.assertFalse((self.directory / 'cloud-bindings.json').exists())

    def test_inactive_expired_and_selfhost_service_follow_server_policy(self):
        for value, enabled in ((self.entitlement(active=False), False),
                               (self.entitlement(seconds=-1), False),
                               (self.entitlement(required=False), True),
                               (self.entitlement(required=False, active=False), False)):
            with self.subTest(value=value):
                self.controller.request = Mock(return_value=value)
                result = self.controller.refresh_entitlement()
                self.assertTrue(self.controller.config()['enabled'])
                self.assertEqual(result['active'], enabled)
                self.assertTrue(self.controller.status()['enabled'])

    def test_failed_or_malformed_entitlement_disables_without_offline_authority(self):
        valid = self.entitlement()
        for result in ({'active': True}, {**valid, 'required': 'false'},
                       {**valid, 'expires_at': None},
                       {**valid, 'server_time': '2030-01-01T00:00:00'},
                       {**valid, 'server_time': '0001-01-01T00:00:00+01:00'},
                       ValueError('malformed JSON'), CloudError('network_or_certificate_error')):
            with self.subTest(result=result):
                write_json(self.controller.path, self.config)
                self.controller.request = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
                with self.assertRaises(CloudError):
                    self.controller.refresh_entitlement()
                status = self.controller.status()
                self.assertTrue(status['enabled'])
                self.assertIsNone(status['entitlement'])
                self.assertTrue(status['entitlement_error'])

    def test_display_cache_and_runtime_are_scoped_and_cache_is_aged(self):
        self.controller.request = Mock(return_value=self.entitlement(seconds=10))
        self.controller.refresh_entitlement()
        path = self.directory / 'cloud-entitlement.json'
        cached = json.loads(path.read_text())
        write_json(self.directory / 'cloud-status.json', {
            'state': 'connected', 'scope': cached['scope'], 'gateway_id': self.config['gateway_id']})
        self.assertEqual(self.controller.status()['runtime']['state'], 'connected')
        for change in ({'scope': 'different'}, {'gateway_id': str(uuid.uuid4())},
                       {'checked_at': time.time() - 60}, {'checked_at': float('nan')}):
            with self.subTest(change=change):
                write_json(path, {**cached, **change})
                self.assertTrue(self.controller.status()['enabled'])
        write_json(path, cached)
        write_json(self.controller.path, {**self.config, 'gateway_id': str(uuid.uuid4())})
        self.assertTrue(self.controller.status()['enabled'])
        self.assertEqual(self.controller.status()['runtime']['state'], 'stopped')

    def test_confirmed_redemption_refreshes_service_once_and_returns_receipt(self):
        receipt, value = self.receipt(), self.entitlement()
        self.controller.request = Mock(side_effect=[receipt, value])
        result = self.controller.control({'action': 'redeem', 'code': 'synthetic-16-code'})
        self.assertTrue(result['redeemed'])
        self.assertEqual(result['id'], receipt['id'])
        self.assertEqual(result['refresh_error'], '')
        self.assertEqual(result['entitlement'], verified_entitlement(value))
        self.assertTrue(result['cloud']['enabled'])
        self.assertEqual([call.args[1:3] for call in self.controller.request.call_args_list],
                         [('/v1/account/redeem', 'POST'), ('/v1/account/entitlement', 'GET')])

    def test_confirmed_redemption_is_not_reported_as_failed_when_status_refresh_fails(self):
        receipt = self.receipt()
        self.controller.request = Mock(side_effect=[receipt, CloudError('network_or_certificate_error')])
        result = self.controller.control({'action': 'redeem', 'code': 'synthetic-16-code'})
        self.assertTrue(result['redeemed'])
        self.assertEqual(result['id'], receipt['id'])
        self.assertEqual(result['refresh_error'], 'network_or_certificate_error')
        self.assertIsNone(result['entitlement'])
        self.assertTrue(result['cloud']['enabled'])
        self.assertEqual(self.controller.request.call_count, 2)

    def test_unconfirmed_redemption_receipts_never_refresh_or_replay_the_write(self):
        receipt = self.receipt()
        for result in ({}, {'active': True}, {**receipt, 'id': 123},
                       {**receipt, 'days': True}, {**receipt, 'created_at': 'not-a-date'}):
            with self.subTest(result=result):
                self.controller.request = Mock(return_value=result)
                with self.assertRaises(CloudError) as caught:
                    self.controller.control({'action': 'redeem', 'code': 'synthetic-16-code'})
                self.assertEqual(caught.exception.code, 'redemption_result_unconfirmed')
                self.assertEqual(self.controller.request.call_count, 1)

    def test_confirmed_redemption_receipt_survives_a_local_status_read_error(self):
        self.controller.request = Mock(side_effect=[self.receipt(), self.entitlement()])
        self.controller.status = Mock(side_effect=CloudError('invalid_local_configuration'))
        result = self.controller.control({'action': 'redeem', 'code': 'synthetic-16-code'})
        self.assertTrue(result['redeemed'])
        self.assertEqual(result['refresh_error'], 'service_status_unavailable')
        self.assertIsNone(result['cloud'])
        self.assertEqual(self.controller.request.call_count, 2)

    def test_login_reads_service_but_never_grants_phone_approval_or_starts_runtime(self):
        for response in (self.entitlement(), CloudError('network_or_certificate_error')):
            with self.subTest(response=response):
                control_key = base64.b64encode(public_key(base64.b64decode(self.secret['control_private']))).decode()
                self.controller.request = Mock(side_effect=[{},
                    {'account': {'id': self.config['account_id'], 'login': self.config['login']},
                     'access_token': 'test-access', 'refresh_token': 'test-refresh', 'expires_in': 600},
                    {'account_id': self.config['account_id'], 'control_public_key': control_key, 'device_token': 'test-device'},
                    response])
                result = self.controller.login({'origin': self.config['origin'], 'login': self.config['login'], 'password': 'synthetic-password'})
                self.assertTrue(result['configured'])
                self.assertTrue(result['enabled'])
                self.assertEqual(self.controller.request.call_args_list[-1].args[1], '/v1/account/entitlement')
                self.assertEqual(self.controller.secret()['control_private'], self.secret['control_private'])
                self.assertFalse((self.directory / 'cloud-bindings.json').exists())

    def test_status_never_returns_credentials(self):
        encoded = json.dumps(self.controller.status())
        for key in ('access_token', 'refresh_token', 'device_token', 'control_private', 'preview_private'):
            self.assertNotIn(key, encoded)

    def test_email_code_operations_are_exactly_scoped_without_login_or_key_changes(self):
        origin = 'https://other.example.test:9443'
        before_config = self.controller.config()
        before_vault = dict(self.vault.values)
        for action, fields, expected, endpoint in (
            ('signup-code', {}, {}, 'registration-code'),
            ('signup-verify', {'verification_code': '012345'}, {'code': '012345'}, 'registration-verify'),
            ('register', {'password': 'signup-password-123', 'registration_token': 't' * 43},
             {'password': 'signup-password-123', 'registration_token': 't' * 43}, 'register'),
        ):
            with self.subTest(action=action):
                self.controller.request = Mock(side_effect=[{'mode': 'hosted', 'email_verification_methods': ['code'],
                                                             'registration_flow': 'email_code_password'}, {'accepted': True}])
                self.controller.control({'action': action, 'origin': origin, 'login': 'email@example.test', **fields})
                self.assertEqual(self.controller.request.call_args_list[0].args, (origin, '/v1/capabilities'))
                self.assertEqual(self.controller.request.call_args_list[1].args,
                                 (origin, '/v1/auth/' + endpoint, 'POST', {'login': 'email@example.test', **expected}))
                self.assertEqual(self.controller.config(), before_config)
                self.assertEqual(self.vault.values, before_vault)

    def test_old_link_servers_and_selfhost_cannot_silently_register_by_link(self):
        for capability, error in (({'mode': 'hosted'}, 'email_code_unsupported'),
                                  ({'mode': 'hosted', 'email_verification_methods': ['link']}, 'email_code_unsupported'),
                                  ({'mode': 'hosted', 'email_verification_methods': 'code'}, 'email_code_unsupported'),
                                  ({'mode': 'selfhost', 'email_verification_methods': []}, 'registration_disabled'),
                                  ({'mode': 'hosted', 'email_verification_methods': ['code']}, 'email_first_registration_unsupported')):
            with self.subTest(capability=capability):
                self.controller.request = Mock(return_value=capability)
                with self.assertRaises(CloudError) as caught:
                    self.controller.control({'action': 'register', 'origin': self.config['origin'], 'login': 'email@example.test', 'password': 'signup-password-123'})
                self.assertEqual(caught.exception.code, error)
                self.controller.request.assert_called_once_with(self.config['origin'], '/v1/capabilities')

    def test_invalid_email_codes_fail_before_network_and_unknown_verification_is_not_replayed(self):
        for code in ('', '12345', '1234567', '12345a', '\uff11\uff12\uff13\uff14\uff15\uff16'):
            with self.subTest(code=code):
                self.controller.request = Mock()
                with self.assertRaises(ValueError):
                    self.controller.control({'action': 'signup-verify', 'origin': self.config['origin'], 'login': 'email@example.test', 'verification_code': code})
                self.controller.request.assert_not_called()
        before = self.controller.path.read_bytes()
        self.controller.request = Mock(side_effect=[{'mode': 'hosted', 'email_verification_methods': ['code'],
                                                    'registration_flow': 'email_code_password'}, CloudError('network_or_certificate_error')])
        with self.assertRaises(CloudError):
            self.controller.control({'action': 'signup-verify', 'origin': self.config['origin'], 'login': 'email@example.test', 'verification_code': '012345'})
        self.assertEqual(self.controller.request.call_count, 2)
        self.assertEqual(self.controller.path.read_bytes(), before)

    def test_retired_email_activation_actions_send_nothing_and_change_no_credentials(self):
        before_config, before_vault = self.controller.config(), dict(self.vault.values)
        self.controller.request = Mock()
        for action in ('verification', 'verify-code'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.controller.control({'action': action, 'origin': self.config['origin'],
                                         'login': 'email@example.test', 'verification_code': '012345'})
        self.controller.request.assert_not_called()
        self.assertEqual(self.controller.config(), before_config)
        self.assertEqual(self.vault.values, before_vault)

    def test_registration_proof_is_required_without_persisting_it_or_replaying_completion(self):
        capability = {'mode': 'hosted', 'email_verification_methods': ['code'],
                      'registration_flow': 'email_code_password'}
        value = {'action': 'register', 'origin': self.config['origin'], 'login': 'signup@example.test',
                 'password': 'synthetic-password'}
        for token in ('', 't' * 42, 't' * 44, 't' * 42 + '!'):
            with self.subTest(token_length=len(token)):
                self.controller.request = Mock(return_value=capability)
                with self.assertRaises(CloudError) as caught:
                    self.controller.control({**value, 'registration_token': token})
                self.assertEqual(caught.exception.code, 'invalid_registration_token')
                self.controller.request.assert_called_once_with(self.config['origin'], '/v1/capabilities')
        before_config, before_vault = self.controller.config(), dict(self.vault.values)
        self.controller.request = Mock(side_effect=[capability, CloudError('network_or_certificate_error')])
        with self.assertRaises(CloudError):
            self.controller.control({**value, 'registration_token': 't' * 43})
        self.assertEqual(self.controller.request.call_count, 2)
        self.assertEqual(self.controller.config(), before_config)
        self.assertEqual(self.vault.values, before_vault)
        self.assertNotIn('registration_token', json.dumps(self.controller.status()))

    def test_pin_replacement_fails_before_device_proof_is_saved(self):
        connector = self.connector()
        connector.config = self.config
        connector.policy_identity = connector.account_identity(self.config)
        connector.entitlement_deadline = time.monotonic() + 100
        connector.ws = SimpleNamespace(closed=False)
        connector.secret = self.controller.secret(self.config)
        binding = str(uuid.uuid4())
        mobile = base64.b64encode(public_key(new_key())).decode()
        preview = base64.b64encode(public_key(new_key())).decode()
        connector.save_proofs({binding: {'binding_id': binding, 'mobile_public_key': mobile, 'preview_public_key': preview, 'installation_id': 'test-installation', 'safety_code': '12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90'}})
        key = scope(self.config['origin'], self.config['account_id'])
        write_json(self.directory / 'cloud-bindings.json', {key: {binding: {'mobile_public_key': mobile, 'preview_public_key': 'different', 'gateway_id': self.config['gateway_id']}}})
        self.controller.device_request = Mock(return_value={'status':'pending','gateway_id':self.config['gateway_id'],'installation_id':'test-installation','mobile_public_key':mobile})
        with self.assertRaises(PermissionError):
            connector.record_proof(binding, base64.b64decode(mobile), {'device_name': 'Test phone', 'preview_public_key': preview})
        self.assertEqual(self.controller.device_request.call_count, 1)

    def test_disconnect_drops_ephemeral_local_authority(self):
        connector = self.connector()
        connector.server = SimpleNamespace(auth=SimpleNamespace(logout=Mock()))
        connector.sessions = {'binding': ('local-cookie', {})}
        connector.clear_authority()
        connector.server.auth.logout.assert_called_once_with('local-cookie')
        self.assertEqual(connector.sessions, {})

    def test_connector_attempts_server_authorization_without_refreshing_qualification(self):
        connector = self.connector()
        write_json(self.controller.path, {**self.config, 'enabled': False})
        self.controller.request = Mock(side_effect=AssertionError('qualification must not be polled'))
        async def connect(remote):
            connector.closed.set()
        connector.connect = AsyncMock(side_effect=connect)
        asyncio.run(connector.run())
        connector.connect.assert_awaited_once()
        self.controller.request.assert_not_called()

    def test_display_query_failure_does_not_fence_a_live_connection(self):
        connector = self.connector()
        connector.config = self.config
        self.assertTrue(connector.relay_current())
        self.controller.request = Mock(side_effect=CloudError('network_or_certificate_error'))
        with self.assertRaises(CloudError):
            self.controller.refresh_entitlement()
        self.assertTrue(connector.relay_current())

    def test_connection_is_bound_to_current_server_account_and_gateway(self):
        connector = self.connector()
        connector.config = self.config
        for change in ({'origin': 'https://other.example.test'},
                       {'account_id': str(uuid.uuid4())}, {'gateway_id': str(uuid.uuid4())}):
            with self.subTest(change=change):
                write_json(self.controller.path, {**self.config, **change})
                self.assertFalse(connector.relay_current())
                self.assertIsNone(connector.pin(str(uuid.uuid4())))

    def test_periodic_watcher_only_observes_local_configuration(self):
        connector = self.connector()
        connector.config = self.config
        connector.ws = SimpleNamespace(closed=False)
        self.controller.request = Mock()
        connector.disconnect = AsyncMock()
        async def finish(seconds):
            connector.closed.set()
        with patch('bridge.features.network.cloud_connector.asyncio.sleep', finish):
            asyncio.run(connector.watch_policy())
        self.controller.request.assert_not_called()
        connector.disconnect.assert_not_awaited()

    def test_logout_fences_frames_waiting_for_the_send_lock(self):
        connector = self.connector()
        connector.config = self.config
        connector.ws = SimpleNamespace(closed=False, send_bytes=AsyncMock())
        async def send():
            connector.send_lock = asyncio.Lock()
            await connector.send_lock.acquire()
            queued = asyncio.create_task(connector.send_frame(2, str(uuid.uuid4()), str(uuid.uuid4()), b'synthetic-record'))
            await asyncio.sleep(0)
            write_json(self.controller.path, {})
            connector.send_lock.release()
            await queued
        with self.assertRaises(CloudError):
            asyncio.run(send())
        connector.ws.send_bytes.assert_not_awaited()

    def test_forwarding_allowlist_and_header_boundary(self):
        value = {'kind': 'request', 'id': str(uuid.uuid4()), 'method': 'GET',
                 'path': '/api/sessions/' + str(uuid.uuid4()) + '/events',
                 'content_type': 'application/json', 'headers': {}, 'length': 0}
        validate_request(value)
        validate_request({**value, 'path':'/api/mobile/events?after=0'})
        for change in ({'path': '//evil.example/api/sessions'}, {'path': '/api/admin'},
                       {'headers': {'Cookie': 'stolen'}}, {'length': True},
                       {'method': 'DELETE'}, {'path': '/api/sessions/x/events'}):
            with self.subTest(change=change), self.assertRaises((ValueError, PermissionError)):
                validate_request({**value, **change})


if __name__ == '__main__':
    unittest.main()
