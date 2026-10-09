"""Authentication lifetime, durable revocation and local-only access management."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.features.auth.auth import Auth, access_policy, session_hours
from bridge.app.desktop import Desktop
from bridge.app.lifecycle import GatewayControl
from bridge.features.auth.pairing import Pairing

ROOT = Path(__file__).resolve().parents[1]


class AuthTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.directory = Path(self.temp.name)
        self.config = {'mode': 'none', 'sessionHours': 0}

    def tearDown(self):
        self.temp.cleanup()

    def auth(self):
        return Auth(self.config, self.directory)

    def test_permanent_login_survives_restart_without_storing_bearer(self):
        auth = self.auth()
        token, session = auth.new_session('192.0.2.7', 'iPhone Safari')
        self.assertEqual(session['expires'], 0)
        raw = (self.directory / 'auth-sessions.json').read_text()
        self.assertNotIn(token, raw)
        with patch('bridge.features.auth.auth.time.time', return_value=session['created'] + 50 * 365 * 86400):
            restarted = self.auth()
            self.assertEqual(restarted.get(token)['csrf'], session['csrf'])
            self.assertEqual(restarted.cookie_age(token), 400 * 86400)
        row = restarted.manage({})['sessions'][0]
        self.assertEqual(row['ip'], '192.0.2.7')
        self.assertEqual(row['userAgent'], 'iPhone Safari')
        self.assertNotIn('csrf', row)
        self.assertIsNone(restarted.get(row['id']))
        if os.name != 'nt':
            self.assertEqual((self.directory / 'auth-sessions.json').stat().st_mode & 0o777, 0o600)

    def test_finite_deadline_does_not_slide_on_visit_or_restart(self):
        self.config['sessionHours'] = 2
        with patch('bridge.features.auth.auth.time.time', return_value=1000):
            token, session = self.auth().new_session('192.0.2.1')
            self.assertEqual(session['expires'], 8200)
        with patch('bridge.features.auth.auth.time.time', return_value=4000):
            auth = self.auth()
            auth.get(token, {'ip': '192.0.2.2', 'peer': '192.0.2.2', 'source': 'direct'}, 'Safari')
            self.assertEqual(auth.cookie_age(token), 4200)
            self.assertEqual(auth.get(token)['expires'], 8200)
        with patch('bridge.features.auth.auth.time.time', return_value=8200):
            self.assertIsNone(self.auth().get(token))

    def test_default_and_invalid_durations(self):
        self.assertEqual(Auth({'mode': 'none'}).hours, 12)
        for value in (-1, True, 1.5, '12', None, 87601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                session_hours(value)
        self.assertEqual(session_hours(0), 0)

    def test_logout_and_local_revoke_persist(self):
        auth = self.auth()
        first, _ = auth.new_session('192.0.2.1')
        second, _ = auth.new_session('192.0.2.2')
        auth.logout(first)
        auth.manage({'action': 'revoke', 'id': auth.key(second)})
        self.assertIsNone(self.auth().get(first))
        self.assertIsNone(self.auth().get(second))

    def test_auth_setting_changes_invalidate_logins_but_keep_ip_rules(self):
        for field, value in [('username', 'changed'), ('hash', 'changed'), ('mode', 'password')]:
            self.config = {'mode': 'none', 'sessionHours': 0}
            auth = self.auth()
            auth.manage({'action': 'save', 'policy': {'blocklist': ['192.0.2.9']}})
            token, _ = auth.new_session('192.0.2.1')
            self.config[field] = value
            changed = self.auth()
            self.assertIsNone(changed.get(token))
            self.assertFalse(changed.permitted('192.0.2.9'))
            self.config = {'mode': 'none', 'sessionHours': 0}
            self.assertIsNone(self.auth().get(token))

    def test_duration_change_keeps_trusted_device_but_password_change_revokes(self):
        self.config['sessionHours'] = 12
        token, _ = self.auth().new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        self.config['sessionHours'] = 1
        self.assertIsNotNone(self.auth().get(token))
        self.config['hash'] = 'changed-password'
        self.assertIsNone(self.auth().get(token))

    def test_block_ip_revokes_all_matching_sessions_and_denies_relogin(self):
        auth = self.auth()
        first, _ = auth.new_session('192.0.2.7')
        second, _ = auth.new_session('192.0.2.7')
        other, _ = auth.new_session('192.0.2.8')
        result = auth.manage({'action': 'block', 'id': auth.key(first)})
        self.assertEqual(result['policy']['blocklist'], ['192.0.2.7'])
        restarted = self.auth()
        self.assertIsNone(restarted.get(first))
        self.assertIsNone(restarted.get(second))
        self.assertIsNotNone(restarted.get(other))
        with self.assertRaises(PermissionError):
            restarted.login('', '', '192.0.2.7')

    def test_allowlist_blacklist_priority_and_ipv6(self):
        auth = self.auth()
        token, _ = auth.new_session('192.0.2.8')
        auth.manage({'action': 'save', 'policy': {'allowlistEnabled': True,
                    'allowlist': ['2001:db8:0::1', '192.0.2.7'], 'blocklist': ['192.0.2.7']}})
        self.assertTrue(auth.permitted('2001:db8::1'))
        self.assertFalse(auth.permitted('192.0.2.7'))
        self.assertFalse(auth.permitted('192.0.2.8'))
        self.assertIsNone(self.auth().get(token))
        for policy in ({'allowlistEnabled': True}, {'allowlist': ['bad']}, {'blocklist': [123]},
                       {'trustedProxies': ['::1%eth0']}, {'allowlistEnabled': 'true'}):
            with self.assertRaises(ValueError):
                access_policy(policy)

    def test_client_headers_cannot_override_an_untrusted_peer(self):
        auth = self.auth()
        headers = {'Host': 'example.com', 'X-Forwarded-For': '192.0.2.77', 'CF-Connecting-IP': '192.0.2.88'}
        self.assertEqual(auth.client('192.0.2.1', headers, True)['ip'], '192.0.2.1')
        self.assertEqual(auth.client('127.0.0.1', headers, False)['ip'], '127.0.0.1')
        self.assertEqual(auth.client('127.0.0.1', headers, True)['ip'], '192.0.2.77')
        headers['X-Forwarded-For'] = '192.0.2.99, 192.0.2.7'
        self.assertEqual(auth.client('127.0.0.1', headers, True)['ip'], '192.0.2.7')
        auth.manage({'action': 'save', 'policy': {'trustedProxies': ['192.0.2.1']}})
        self.assertEqual(auth.client('192.0.2.1', headers, True)['ip'], '192.0.2.7')
        headers['Host'] = 'test.trycloudflare.com'
        self.assertEqual(auth.client('127.0.0.1', headers, True)['ip'], '192.0.2.88')
        self.assertEqual(auth.client('::ffff:192.0.2.7', {}, False)['ip'], '192.0.2.7')
        headers = {'Host': 'example.com', 'X-Forwarded-For': 'invalid'}
        self.assertEqual(auth.client('127.0.0.1', headers, True)['source'], 'proxy')

    def test_corrupt_persistent_policy_fails_closed(self):
        (self.directory / 'auth-sessions.json').write_text('{not json')
        with self.assertRaises(ValueError):
            self.auth()

    def test_device_management_uses_existing_private_control_channel(self):
        auth = self.auth()
        token, _ = auth.new_session('192.0.2.7')
        control = GatewayControl(self.directory)
        control.start(lambda: None, Pairing(auth, set()).control, 'test-instance', auth)
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': True}
        try:
            rows = desktop.devices({'action': 'list'})['sessions']
            self.assertEqual(len(rows), 1)
            desktop.devices({'action': 'block', 'id': rows[0]['id']})
            self.assertIsNone(auth.get(token))
            self.assertFalse(auth.permitted('192.0.2.7'))
        finally:
            control.close()

    def test_last_seen_is_persisted_at_most_once_per_minute(self):
        auth = self.auth()
        with patch('bridge.features.auth.auth.time.time', return_value=1000):
            token, _ = auth.new_session('192.0.2.7', 'Safari')
        client = {'ip': '192.0.2.7', 'peer': '192.0.2.7', 'source': 'direct'}
        with patch.object(auth, 'persist', wraps=auth.persist) as write:
            with patch('bridge.features.auth.auth.time.time', return_value=1030):
                auth.get(token, client, 'Safari')
                write.assert_not_called()
            with patch('bridge.features.auth.auth.time.time', return_value=1061):
                auth.get(token, client, 'Safari')
                write.assert_called_once()
        self.assertEqual(self.auth().manage({})['sessions'][0]['lastSeen'], 1061)
