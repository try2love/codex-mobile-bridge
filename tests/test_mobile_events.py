import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.features.auth.auth import password_record
from bridge.api.httpd import GatewayServer
from bridge.features.notifications.events import MobileEvents
from bridge.features.notifications.channels import Notifications, save_settings

ROOT = Path(__file__).resolve().parents[1]
THREAD = '00000000-0000-4000-8000-000000000001'


class MobileEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_durable_bounded_feed_idempotence_and_cursor(self):
        feed = MobileEvents(self.path)
        row = {'id': THREAD, 'host': 'remote'}
        feed.append(['a'], row, 'Title', 'Body', False)
        feed.append(['a'], row, 'Title', 'Body', False)
        self.assertEqual(feed.read()['cursor'], 1)
        loaded = MobileEvents(self.path)
        self.assertEqual(loaded.read(), feed.read())
        for i in range(205):
            loaded.append([str(i)], row, 'Done', 'Body', True)
        self.assertEqual(len(loaded.read()['events']), 200)
        self.assertEqual(len(loaded.read(205)['events']), 1)
        with self.assertRaises(ValueError): loaded.read(-1)
        with self.assertRaises(ValueError): loaded.read(True)

    def test_inbox_delivery_preserves_global_policy_and_hides_private_title(self):
        manager = Notifications(None, self.path, lambda: ['https://gateway.example'])
        config = save_settings(self.path, {'mobileEnabled': True})
        row = {'id': THREAD, 'host': 'local'}
        self.assertTrue(manager.policy(THREAD, 'local')['available'])
        self.assertFalse(manager.defaults()['completion'])
        target = manager._targets(config)['mobile']
        key = manager._delivery_key(row, 'request', target=target)
        self.assertTrue(manager._send_keys(config, row, 'SECRET CHAT TITLE', 'mobile', target, [key], False))
        self.assertNotIn('SECRET', json.dumps(manager.mobile.read()))
        self.assertFalse(manager._send_keys(config, row, 'SECRET CHAT TITLE', 'mobile', target, [key], False))
        manager.policy(THREAD, 'local', {'requests': 'off', 'completion': 'off'})
        self.assertFalse(manager._send_keys(config, row, '', 'mobile', target, ['new'], False))
        self.assertEqual(len(manager.mobile.read()['events']), 1)
        save_settings(self.path, {'mobileEnabled': False})
        self.assertFalse(manager.policy(THREAD, 'local')['available'])

    def test_native_link_is_opt_in_and_encodes_origin_and_host(self):
        manager = Notifications(None, self.path, lambda: ['https://gateway.example'])
        config = save_settings(self.path, {})
        self.assertTrue(manager.click_url(config, THREAD, 'remote').startswith('https://'))
        config = save_settings(self.path, {'mobileAppLinks': True})
        link = manager.click_url(config, THREAD, 'host & other')
        self.assertIn('codexbridge://open?origin=https%3A%2F%2Fgateway.example', link)
        self.assertIn('host=host%20%26%20other', link)

    def test_http_requires_session_origin_and_revocation_and_honors_disable(self):
        server = GatewayServer(('127.0.0.1', 0), object(), {'auth': {'mode': 'password', 'username': 'test', **password_record('long-password')}, 'origins': ['https://gateway.example']}, ROOT/'web')
        server.notifications = Notifications(None, self.path)
        save_settings(self.path, {'mobileEnabled': True})
        server.notifications.mobile.append(['a'], {'id': THREAD, 'host': 'local'}, 'Test', 'Body', False)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def request(cookie='', origin='https://gateway.example'):
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            c.request('GET','/api/mobile/events',headers={'Host':'gateway.example','Origin':origin,'Cookie':cookie})
            r=c.getresponse(); result=r.status,json.loads(r.read());c.close();return result
        try:
            self.assertEqual(request()[0], 401)
            token, _ = server.auth.new_session('127.0.0.1')
            cookie = 'codex_mobile_session='+token
            self.assertEqual(request(cookie, 'https://evil.example')[0], 403)
            code, value = request(cookie)
            self.assertEqual(code, 200);self.assertEqual(len(value['events']), 1)
            save_settings(self.path, {'mobileEnabled': False})
            self.assertEqual(request(cookie)[1]['events'], [])
            server.auth.logout(token)
            self.assertEqual(request(cookie)[0], 401)
        finally:
            server.shutdown();worker.join();server.server_close()
