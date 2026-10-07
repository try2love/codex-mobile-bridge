"""Durable five-attempt blocking, browser sessions and opt-out security alerts."""
import concurrent.futures
import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.auth import Auth, LoginRejected
from bridge.httpd import GatewayServer
from bridge.notifications import save_settings
from bridge.security_notifications import SecurityNotifications

ROOT = Path(__file__).resolve().parents[1]
IP = '192.0.2.7'
CONFIG = {'mode': 'password', 'username': 'tester', 'salt': '00'*16, 'iterations': 1000,
          'hash': hashlib.pbkdf2_hmac('sha256', b'correct-password', bytes(16), 1000, dklen=32).hex(), 'sessionHours': 12}


class LoginSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.auth = Auth(CONFIG, self.directory)

    def reject_login(self, auth=None, address=IP):
        with self.assertRaises(LoginRejected) as failure:
            (auth or self.auth).login('tester', 'wrong-password', address)
        return failure.exception.status

    def test_fifth_failure_blocks_persistently_revokes_and_desktop_unblocks(self):
        token, _ = self.auth.login('tester', 'correct-password', IP)
        for remaining in range(4, 0, -1):
            self.assertEqual(self.reject_login()['attemptsRemaining'], remaining)
        restarted = Auth(CONFIG, self.directory)
        self.assertEqual(self.reject_login(restarted), {'attemptsRemaining': 0, 'attemptLimit': 5, 'blocked': True})
        restarted = Auth(CONFIG, self.directory)
        self.assertIsNone(restarted.get(token))
        with self.assertRaises(LoginRejected):
            restarted.login('tester', 'correct-password', IP)
        with self.assertRaises(PermissionError):
            restarted.new_session(IP)
        restarted.manage({'action': 'save', 'policy': {'allowlistEnabled': True, 'allowlist': [IP]}})
        self.assertFalse(restarted.permitted(IP))
        self.assertEqual(len(restarted.manage({})['autoBlocks']), 1)
        restarted.manage({'action': 'unblock', 'ip': IP})
        final = Auth(CONFIG, self.directory)
        self.assertEqual(final.login_status(IP)['attemptsRemaining'], 5)
        self.assertTrue(final.permitted(IP))
        final.login('tester', 'correct-password', IP)

    def test_success_resets_consecutive_failures_and_other_ips_are_independent(self):
        self.reject_login()
        self.assertEqual(self.auth.login_status('192.0.2.8')['attemptsRemaining'], 5)
        self.auth.login('tester', 'correct-password', IP)
        self.assertEqual(Auth(CONFIG, self.directory).login_status(IP)['attemptsRemaining'], 5)

    def test_parallel_requests_cannot_hash_more_than_five_passwords(self):
        original = hashlib.pbkdf2_hmac
        with patch('bridge.auth.hashlib.pbkdf2_hmac', wraps=original) as hashing:
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                list(executor.map(lambda _: self.reject_login(), range(12)))
            self.assertEqual(hashing.call_count, 5)
        self.assertEqual(self.auth.auto_blocks[IP]['attempts'], 5)

    def test_mobile_login_is_trusted_but_still_requires_password_and_revocation(self):
        for agent in ('BridgeMobile/0.1-iOS', 'BridgeMobile/0.1-Android'):
            with self.assertRaises(LoginRejected):
                self.auth.login('tester', 'wrong', IP, agent)
            token, row = self.auth.login('tester', 'correct-password', IP, agent, remember=False)
            self.assertEqual(row['expires'], 0)
            self.assertEqual(self.auth.login_status(IP)['attemptsRemaining'], 5)
            with patch('bridge.auth.time.time', return_value=row['created'] + 365 * 86400):
                self.assertIsNotNone(Auth(CONFIG, self.directory).get(token))
            self.auth.manage({'action': 'revoke', 'id': self.auth.key(token)})
            self.assertIsNone(Auth(CONFIG, self.directory).get(token))

    def test_remembered_browser_renews_on_use_and_expires_after_inactivity(self):
        with patch('bridge.auth.time.time', return_value=1000):
            token, _ = self.auth.login('tester', 'correct-password', IP, remember=True)
        client = {'ip': IP, 'peer': IP, 'source': 'direct'}
        with patch('bridge.auth.time.time', return_value=1000 + 6 * 86400):
            self.auth.get(token, client, 'Safari')
        with patch('bridge.auth.time.time', return_value=1000 + 8 * 86400):
            self.assertIsNotNone(Auth(CONFIG, self.directory).get(token))
        with patch('bridge.auth.time.time', return_value=1000 + 13 * 86400):
            self.assertIsNone(Auth(CONFIG, self.directory).get(token))

    def test_existing_valid_mobile_session_upgrades_but_expired_one_does_not(self):
        with patch('bridge.auth.time.time', return_value=1000):
            token, _ = self.auth.new_session(IP)
        client = {'ip': IP, 'peer': IP, 'source': 'direct'}
        with patch('bridge.auth.time.time', return_value=2000):
            self.assertIsNone(self.auth.get('not-authenticated', client, 'BridgeMobile/0.1-iOS'))
            self.assertEqual(self.auth.get(token, client, 'BridgeMobile/0.1-iOS')['expires'], 0)
        with patch('bridge.auth.time.time', return_value=3000):
            expired, _ = self.auth.new_session(IP)
        with patch('bridge.auth.time.time', return_value=3000 + 13 * 3600):
            self.assertIsNone(self.auth.get(expired, client, 'BridgeMobile/0.1-iOS'))

    def test_remember_cookie_lasts_seven_days_and_unchecked_is_session_cookie(self):
        with patch('bridge.auth.time.time', return_value=1000):
            token, row = self.auth.login('tester', 'correct-password', IP, remember=True)
            self.assertEqual(row['expires'], 1000+7*86400)
            self.assertEqual(self.auth.cookie_age(token), 7*86400)
            temporary, _ = self.auth.login('tester', 'correct-password', IP, remember=False)
            self.assertIsNone(self.auth.cookie_age(temporary))
        with patch('bridge.auth.time.time', return_value=1000+7*86400):
            self.assertIsNone(Auth(CONFIG, self.directory).get(token))
        self.assertNotIn('correct-password', (self.directory/'auth-sessions.json').read_text())
        with self.assertRaises(ValueError):
            self.auth.login('tester', 'correct-password', IP, remember='yes')

    def block(self):
        for _ in range(5):
            self.reject_login()

    def test_security_alert_is_once_per_block_and_never_contains_credentials(self):
        save_settings(self.directory, {'enabled': True, 'topic': 'fixture'})
        self.block()
        with patch('bridge.security_notifications.publish') as send:
            SecurityNotifications(self.directory).scan()
            SecurityNotifications(self.directory).scan()
            send.assert_called_once()
            self.assertIn(IP, send.call_args.args[2])
            self.assertNotIn('wrong-password', send.call_args.args[2])
            self.auth.manage({'action': 'unblock', 'ip': IP})
            self.block()
            SecurityNotifications(self.directory).scan()
            self.assertEqual(send.call_count, 2)

    def test_security_alert_switch_mutes_without_disabling_block(self):
        save_settings(self.directory, {'enabled': True, 'topic': 'fixture', 'securityEnabled': False})
        self.block()
        with patch('bridge.security_notifications.publish') as send:
            SecurityNotifications(self.directory).scan()
            self.assertFalse(self.auth.permitted(IP))
            save_settings(self.directory, {'securityEnabled': True})
            SecurityNotifications(self.directory).scan()
            send.assert_not_called()

    def test_security_alert_retries_failed_delivery_only(self):
        save_settings(self.directory, {'enabled': True, 'topic': 'fixture'})
        self.block()
        with patch('bridge.security_notifications.publish', side_effect=[OSError('offline'), None]) as send:
            notifier = SecurityNotifications(self.directory)
            with patch('bridge.security_notifications.time.time', return_value=1000):
                notifier.scan()
                notifier.scan()
            self.assertEqual(send.call_count, 1)
            with patch('bridge.security_notifications.time.time', return_value=1031):
                notifier.scan()
                notifier.scan()
            self.assertEqual(send.call_count, 2)


class LoginHttpTests(unittest.TestCase):
    def setUp(self):
        self.server = GatewayServer(('127.0.0.1', 0), None, {'auth': CONFIG, 'origins': ['https://entry.example']}, ROOT/'web')
        self.port = self.server.server_port
        self.origin = 'http://127.0.0.1:'+str(self.port)
        self.server.hosts.add('127.0.0.1:'+str(self.port))
        self.server.origins.add(self.origin)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        client.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None,
                       headers={'Origin': self.origin, 'Content-Type': 'application/json', **(headers or {})})
        response = client.getresponse()
        result = response.status, dict(response.getheaders()), json.loads(response.read())
        client.close()
        return result

    def test_live_remaining_attempts_reload_block_and_no_public_unblock(self):
        for remaining in range(4, -1, -1):
            status, _, value = self.request('/api/login', {'username': 'tester', 'password': 'wrong'})
            self.assertEqual(status, 403)
            self.assertEqual(value['loginStatus']['attemptsRemaining'], remaining)
        self.assertTrue(self.request('/api/auth')[2]['loginStatus']['blocked'])
        self.assertEqual(self.request('/api/login', {'username': 'tester', 'password': 'correct-password'})[0], 403)
        self.assertEqual(self.request('/api/devices', {'action': 'unblock', 'ip': '127.0.0.1'})[0], 403)
        self.server.auth.manage({'action': 'unblock', 'ip': '127.0.0.1'})
        self.assertEqual(self.request('/api/auth')[2]['loginStatus']['attemptsRemaining'], 5)

    def test_cookie_modes_and_auth_refresh(self):
        for remember in (True, False):
            status, headers, _ = self.request('/api/login', {'username': 'tester', 'password': 'correct-password', 'remember': remember})
            self.assertEqual(status, 200)
            self.assertIn('HttpOnly', headers['Set-Cookie'])
            self.assertEqual('Max-Age=' in headers['Set-Cookie'], remember)
            cookie = headers['Set-Cookie'].split(';')[0]
            _, refreshed, auth = self.request('/api/auth', headers={'Cookie': cookie})
            self.assertTrue(auth['authenticated'])
            self.assertEqual('Max-Age=' in refreshed['Set-Cookie'], remember)
            if remember:
                self.assertIn('Max-Age=604800', headers['Set-Cookie'])

    def test_mobile_http_stays_signed_in_and_success_clears_attempts(self):
        headers = {'User-Agent': 'BridgeMobile/0.1-Android'}
        self.assertFalse(self.request('/api/auth', headers=headers)[2]['authenticated'])
        for _ in range(3):
            self.request('/api/login', {'username': 'tester', 'password': 'wrong'}, headers)
        _, response, _ = self.request('/api/login', {'username': 'tester', 'password': 'correct-password'}, headers)
        cookie = response['Set-Cookie'].split(';')[0]
        self.assertIn('Max-Age=34560000', response['Set-Cookie'])
        with patch('bridge.auth.time.time', return_value=time.time() + 30 * 86400):
            status, _, value = self.request('/api/auth', headers={**headers, 'Cookie': cookie})
            self.assertEqual(status, 200)
            self.assertTrue(value['authenticated'])
            self.assertEqual(value['loginStatus']['attemptsRemaining'], 5)
        row = self.server.auth.manage({})['sessions'][0]
        self.server.auth.manage({'action': 'revoke', 'id': row['id']})
        self.assertFalse(self.request('/api/auth', headers={**headers, 'Cookie': cookie})[2]['authenticated'])

    def test_remembered_http_cookie_and_server_deadline_renew_together(self):
        start = time.time()
        _, response, _ = self.request('/api/login', {'username': 'tester', 'password': 'correct-password', 'remember': True})
        cookie = response['Set-Cookie'].split(';')[0]
        with patch('bridge.auth.time.time', return_value=start + 6 * 86400):
            _, response, value = self.request('/api/auth', headers={'Cookie': cookie})
            self.assertTrue(value['authenticated'])
            self.assertIn('Max-Age=604800', response['Set-Cookie'])
        with patch('bridge.auth.time.time', return_value=start + 8 * 86400):
            self.assertTrue(self.request('/api/auth', headers={'Cookie': cookie})[2]['authenticated'])

    def test_forwarded_ip_is_counted_but_direct_header_spoof_is_ignored(self):
        headers = {'Host': 'entry.example', 'Origin': 'https://entry.example', 'X-Forwarded-For': '192.0.2.99, '+IP}
        self.request('/api/login', {'password': 'wrong'}, headers)
        self.assertEqual(self.server.auth.login_status(IP)['attemptsRemaining'], 4)
        self.assertEqual(self.server.auth.login_status('192.0.2.99')['attemptsRemaining'], 5)
        self.request('/api/login', {'password': 'wrong'}, {'X-Forwarded-For': '192.0.2.99'})
        self.assertEqual(self.server.auth.login_status('127.0.0.1')['attemptsRemaining'], 4)
        self.assertEqual(self.server.auth.login_status('192.0.2.99')['attemptsRemaining'], 5)
