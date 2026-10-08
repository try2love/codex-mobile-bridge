import concurrent.futures
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.auth import Auth, password_record
from bridge.desktop import Desktop
from bridge.httpd import GatewayServer
from bridge.lifecycle import GatewayControl, read_record, request_pairing
from bridge.pairing import Pairing, phone_origin

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.tmp').mkdir(exist_ok=True)
ORIGIN = 'http://192.0.2.1:8787'


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.auth = Auth({'mode': 'none'})
        self.pairing = Pairing(self.auth, {ORIGIN, 'https://example.com'})

    def grant(self, origin=ORIGIN):
        return self.pairing.control({'action': 'create', 'url': origin})

    def redeem(self, grant, origin=ORIGIN):
        return self.pairing.exchange(grant['url'].split('#pair=')[1], origin, 'client')

    def test_replay_refresh_origin_and_session_lifetime(self):
        first = self.grant()
        with self.assertRaises(PermissionError): self.redeem(first, 'https://example.com')
        session_token, session = self.redeem(first)
        self.assertAlmostEqual(session['expires'] - time.time(), 43200, delta=2)
        with self.assertRaises(PermissionError): self.redeem(first)
        self.assertEqual(self.pairing.control({'action': 'status', 'id': first['id']})['state'], 'used')
        old = self.grant(); fresh = self.grant()
        with self.assertRaises(PermissionError): self.redeem(old)
        self.redeem(fresh)
        self.assertIsNotNone(self.auth.get(session_token))

    def test_expiry_revoke_restart_and_hash_storage(self):
        grant = self.grant()
        raw = grant['url'].split('#pair=')[1]
        self.assertNotIn(raw, str(self.pairing.grants))
        with patch('bridge.pairing.time.time', return_value=grant['expires']+1):
            with self.assertRaises(PermissionError): self.redeem(grant)
            self.assertEqual(self.pairing.control({'action': 'status', 'id': grant['id']})['state'], 'expired')
        fresh = self.grant()
        self.pairing.control({'action': 'revoke', 'id': fresh['id']})
        with self.assertRaises(PermissionError): self.redeem(fresh)
        restarted = Pairing(self.auth, {ORIGIN})
        with self.assertRaises(PermissionError): restarted.exchange(raw, ORIGIN, 'new-client')

    def test_mobile_pairing_trusts_device_and_resets_password_failures(self):
        self.auth.failures['192.0.2.7'] = 3
        grant = self.grant()
        token, row = self.pairing.exchange(grant['url'].split('#pair=')[1], ORIGIN,
                                          '192.0.2.7', 'BridgeMobile/0.1-iOS')
        self.assertEqual(row['expires'], 0)
        self.assertEqual(self.auth.login_status('192.0.2.7')['attemptsRemaining'], 5)
        self.auth.manage({'action': 'revoke', 'id': self.auth.key(token)})
        self.assertIsNone(self.auth.get(token))

    def test_concurrent_redemption_has_exactly_one_winner(self):
        grant = self.grant()
        def redeem(_):
            try: self.redeem(grant); return True
            except PermissionError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(redeem, range(12))), 1)

    def test_multiple_entries_independent_and_guesses_limited(self):
        lan, public = self.grant(), self.grant('https://example.com')
        self.pairing.control({'action': 'revoke', 'id': lan['id']})
        self.redeem(public, 'https://example.com')
        for _ in range(8):
            with self.assertRaises(PermissionError): self.pairing.exchange('x'*43, ORIGIN, 'attacker')
        with self.assertRaisesRegex(PermissionError, '5'): self.pairing.exchange('x'*43, ORIGIN, 'attacker')
        for url in ['http://localhost:8787', 'http://127.0.0.1', 'http://[::1]', 'https://a.localhost', 'https://evil.test', ORIGIN+'/?token=abc']:
            with self.assertRaises(ValueError): self.grant(url)

    def test_local_control_requests_are_private_and_cleaned(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as name:
            control = GatewayControl(name)
            control.start(lambda: None, self.pairing.control, 'instance-test')
            try:
                grant = request_pairing(name, {'action': 'create', 'url': ORIGIN})
                self.redeem(grant)
                status = request_pairing(name, {'action': 'status', 'ids': [grant['id']]})
                self.assertEqual(status['states'][grant['id']], 'used')
                self.assertEqual(list(control.pairing_dir.iterdir()), [])
                self.assertNotIn(grant['url'], Path(name, 'gateway-control.json').read_text())
                import os
                if os.name != 'nt': self.assertEqual(control.pairing_dir.stat().st_mode & 0o777, 0o700)
            finally: control.close()
            self.assertFalse(control.pairing_dir.exists())

    def test_desktop_checks_actual_instance_before_minting(self):
        desktop = Desktop(ROOT/'.tmp/pairing-controller-test')
        desktop.snapshot = lambda: {'runtime': {'running': True, 'instanceId': 'expected'}, 'urls': ['https://example.com/']}
        with patch('bridge.access.read_auth', return_value={'instanceId': 'other'}), patch('bridge.desktop.request_pairing') as request:
            with self.assertRaisesRegex(ValueError, '当前网关'): desktop.pairing({'action': 'create', 'url': 'https://example.com/'})
            request.assert_not_called()
        with patch('bridge.access.read_auth', return_value={'instanceId': 'expected'}), patch('bridge.desktop.request_pairing', return_value={}) as request:
            desktop.pairing({'action': 'create', 'url': 'https://example.com/'})
            request.assert_called_once()


class PairingHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = {'mode': 'password', 'username': 'test', **password_record('test-password-long')}

    def setUp(self):
        self.server = GatewayServer(('127.0.0.1', 0), object(), {'auth': self.password, 'origins': [ORIGIN, 'https://example.com']}, ROOT/'web')
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True); self.worker.start()

    def tearDown(self):
        self.server.shutdown(); self.worker.join(); self.server.server_close()

    def request(self, path, body=None, origin=ORIGIN, cookie=None, host='192.0.2.1:8787', user_agent=''):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Host': host}
        if origin: headers['Origin'] = origin
        if cookie: headers['Cookie'] = cookie
        if user_agent: headers['User-Agent'] = user_agent
        if body is not None: headers['Content-Type'] = 'application/json'
        connection.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse(); data = json.loads(response.read()); code = response.status; cookies = response.getheader('Set-Cookie'); connection.close()
        return code, data, cookies

    def test_mobile_qr_login_survives_time_restart_and_missing_background_ua(self):
        # Exercise actual issuance, HTTP redemption and cookie renewal. Only
        # the isolated server clock advances; no real device/account is used.
        for platform in ('Android', 'iOS'):
            for origin, host in ((ORIGIN, '192.0.2.1:8787'), ('https://example.com', 'example.com')):
                with self.subTest(platform=platform, origin=origin), tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
                    self.server.auth = Auth(self.password, directory)
                    self.server.pairing = Pairing(self.server.auth, {origin})
                    ua = 'Mozilla/5.0 BridgeMobile/0.1-' + platform
                    grant = self.server.pairing.control({'action': 'create', 'url': origin})
                    code, _, cookie = self.request('/api/pair', {'token': grant['url'].split('#pair=')[1]}, origin=origin, host=host, user_agent=ua)
                    self.assertEqual(code, 200)
                    self.assertIn('Max-Age=34560000', cookie)
                    self.assertEqual('Secure' in cookie, origin.startswith('https:'))
                    cookie = cookie.split(';')[0]
                    token = cookie.split('=', 1)[1]
                    created = self.server.auth.get(token)['created']
                    for delay in (12 * 3600 + 1, 30 * 86400, 401 * 86400):
                        with patch('bridge.auth.time.time', return_value=created + delay):
                            self.server.auth = Auth(self.password, directory)
                            for agent in ('', ua):
                                code, value, refreshed = self.request('/api/auth', origin=origin, host=host, cookie=cookie, user_agent=agent)
                                self.assertEqual(code, 200)
                                self.assertTrue(value['authenticated'])
                                self.assertTrue(value['trustedDevice'])
                                self.assertIn('Max-Age=34560000', refreshed)
                    self.server.auth.manage({'action': 'revoke', 'id': Auth.key(token)})
                    self.server.auth = Auth(self.password, directory)
                    self.assertFalse(self.request('/api/auth', origin=origin, host=host, cookie=cookie, user_agent=ua)[1]['authenticated'])
                    self.assertFalse(self.request('/api/auth', origin=origin, host=host, user_agent=ua)[1]['authenticated'])

    def test_legacy_mobile_cookie_upgrades_only_while_still_valid(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            self.server.auth = Auth(self.password, directory)
            token, session = self.server.auth.new_session('127.0.0.1', 'Legacy mobile browser')
            cookie = Auth.COOKIE + '=' + token
            ua = 'Mozilla/5.0 BridgeMobile/0.1-Android'
            with patch('bridge.auth.time.time', return_value=session['created'] + 11 * 3600):
                self.server.auth = Auth(self.password, directory)
                _, value, refreshed = self.request('/api/auth', cookie=cookie, user_agent=ua)
                self.assertTrue(value['trustedDevice'])
                self.assertIn('Max-Age=34560000', refreshed)
            with patch('bridge.auth.time.time', return_value=session['created'] + 401 * 86400):
                self.server.auth = Auth(self.password, directory)
                self.assertTrue(self.request('/api/auth', cookie=cookie, user_agent=ua)[1]['authenticated'])

            expired, row = self.server.auth.new_session('127.0.0.1', 'Legacy mobile browser')
            with patch('bridge.auth.time.time', return_value=row['expires'] + 1):
                self.assertFalse(self.request('/api/auth', cookie=Auth.COOKIE+'='+expired, user_agent=ua)[1]['authenticated'])

    def test_exchange_cookie_csrf_and_password_path(self):
        self.assertEqual(self.request('/api/sessions')[0], 401)
        grant = self.server.pairing.control({'action': 'create', 'url': ORIGIN})
        token = grant['url'].split('#pair=')[1]
        self.assertEqual(self.request('/api/pair', {'token': token}, origin=None)[0], 403)
        self.assertEqual(self.request('/api/pair', {'token': token}, origin='https://example.com')[0], 403)
        code, data, cookie = self.request('/api/pair', {'token': token})
        self.assertEqual(code, 200); self.assertTrue(data['csrf']); self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Strict', cookie)
        cookie = cookie.split(';')[0]
        self.assertTrue(self.request('/api/auth', cookie=cookie)[1]['authenticated'])
        self.assertEqual(self.request('/api/pair', {'token': token})[0], 403)
        # Neither unauthenticated nor signed-in HTTP clients can mint grants.
        self.assertEqual(self.request('/api/pair/create', {}, cookie=cookie)[0], 403)
        self.assertEqual(self.request('/api/login', {'username': 'test', 'password': 'incorrect'})[0], 403)
        self.assertEqual(self.request('/api/login', {'username': 'test', 'password': 'test-password-long'})[0], 200)
        public = self.server.pairing.control({'action': 'create', 'url': 'https://example.com'})
        code, _, cookie = self.request('/api/pair', {'token': public['url'].split('#pair=')[1]}, origin='https://example.com', host='example.com')
        self.assertEqual(code, 200); self.assertIn('Secure', cookie)
