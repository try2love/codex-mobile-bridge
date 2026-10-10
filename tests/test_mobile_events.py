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
        self.assertEqual(loaded.read()['unread']['codex'], 206)
        loaded.acknowledge('codex', loaded.read()['streamId'], 5)
        self.assertEqual(MobileEvents(self.path).read()['unread']['codex'], 201)
        self.assertEqual(len(loaded.read(205)['events']), 1)
        with self.assertRaises(ValueError): loaded.read(-1)
        with self.assertRaises(ValueError): loaded.read(True)

    def test_summary_is_bounded_private_and_independent_of_incremental_cursor(self):
        feed = MobileEvents(self.path)
        for key, host, completed in [('a', 'local', True), ('b', 'desktop:claude', False),
                                     ('c', 'desktop:deepseek', False), ('d', 'desktop:claude', True)]:
            feed.append([key], {'id': THREAD, 'host': host}, 'Private title', 'Private body', completed)
        value = feed.read(999)
        self.assertEqual(value['events'], [])
        self.assertEqual({row['provider']: (row['kind'], row['sequence']) for row in value['summary']},
                         {'codex': ('completion', 1), 'claude': ('completion', 4), 'deepseek': ('request', 3)})
        self.assertNotIn('Private', json.dumps(value['summary']))
        self.assertNotIn(THREAD, json.dumps(value['summary']))
        self.assertEqual(MobileEvents(self.path).read(999)['summary'], value['summary'])

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
            self.assertEqual(request(cookie)[1]['summary'], [])
            server.auth.logout(token)
            self.assertEqual(request(cookie)[0], 401)
        finally:
            server.shutdown();worker.join();server.server_close()


class ServerUnreadTests(unittest.TestCase):
    setUp = MobileEventTests.setUp
    def test_gateway_ack_is_durable_provider_scoped_and_preserves_new_events(self):
        feed = MobileEvents(self.path)
        for host in ('local', 'desktop:claude', 'desktop:deepseek'):
            feed.append([host], {'id': THREAD, 'host': host}, 'Title', 'Body', False)
        viewed = feed.read()
        feed.append(['late'], {'id': THREAD, 'host': 'desktop:claude'}, 'Title', 'Body', False)
        feed.acknowledge('claude', viewed['streamId'], viewed['cursor'])
        self.assertEqual(feed.read()['unread'], {'codex': 1, 'claude': 1, 'deepseek': 1})
        self.assertEqual(len(feed.read()['events']), 4)  # Reading preserves inbox history.
        self.assertFalse(feed.read()['events'][1]['unread'])
        self.assertEqual(MobileEvents(self.path).read()['unread'], feed.read()['unread'])
        feed.acknowledge('claude', viewed['streamId'], 1)
        self.assertEqual(feed.read()['seen']['claude'], 3)  # An older phone cannot undo reading.
        for provider, stream, cursor in [('bad', viewed['streamId'], 3), ('claude', 'old-stream', 3), ('claude', viewed['streamId'], 99), ('claude', viewed['streamId'], True)]:
            with self.assertRaises(ValueError): feed.acknowledge(provider, stream, cursor)

    def test_snapshot_filters_current_preferences_and_enabled_apps_without_native_reads(self):
        from unittest.mock import Mock
        desktop = Mock(); desktop.enabled.side_effect = lambda p: p != 'deepseek'
        manager = Notifications(None, self.path, desktop_sessions=desktop)
        save_settings(self.path, {'mobileEnabled': True})
        manager.defaults({'requests': True, 'completion': True})
        for host in ('local', 'desktop:claude', 'desktop:deepseek'):
            for kind in (False, True): manager.mobile.append([host+str(kind)], {'id': THREAD, 'host': host}, 'Title', 'Body', kind)
        self.assertEqual(manager.mobile_snapshot()['unread'], {'codex': 2, 'claude': 2, 'deepseek': 0})
        manager.defaults({'requests': True, 'completion': False})
        self.assertEqual(manager.mobile_snapshot(999)['unread'], {'codex': 1, 'claude': 1, 'deepseek': 0})
        manager.policy(THREAD, 'local', {'requests': 'off', 'completion': 'off'})
        self.assertEqual(manager.mobile_snapshot()['unread']['codex'], 0)
        desktop.call.assert_not_called(); desktop.status.assert_not_called()

    def test_ack_endpoint_requires_csrf_and_returns_shared_counts(self):
        server = GatewayServer(('127.0.0.1', 0), object(), {'auth': {'mode': 'password', 'username': 'test', **password_record('long-password')}, 'origins': ['https://gateway.example']}, ROOT/'web')
        server.notifications = Notifications(None, self.path)
        save_settings(self.path, {'mobileEnabled': True})
        feed = server.notifications.mobile; feed.append(['a'], {'id': THREAD, 'host': 'local'}, 'Test', 'Body', False)
        token, session = server.auth.new_session('127.0.0.1')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        body = {'provider': 'codex', 'streamId': feed.read()['streamId'], 'through': 1}
        def request(csrf='', origin='https://gateway.example'):
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            c.request('POST', '/api/mobile/events/read', json.dumps(body), headers={'Host': 'gateway.example', 'Origin': origin, 'Cookie': 'codex_mobile_session='+token, 'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
            r = c.getresponse(); result = r.status, json.loads(r.read()); c.close(); return result
        try:
            self.assertEqual(request()[0], 403)
            self.assertEqual(request(session['csrf'], 'https://evil.example')[0], 403)
            code, value = request(session['csrf']); self.assertEqual(code, 200)
            self.assertEqual(value['unread']['codex'], 0)
            self.assertEqual(server.notifications.mobile_snapshot()['unread']['codex'], 0)
            from unittest.mock import Mock
            server.bridge = Mock(host_errors=[])
            server.bridge.list.side_effect = lambda **kw: (feed.append(['during-list'], {'id': THREAD, 'host': 'local'}, 'Late', 'Body', False), [])[1]
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            c.request('GET', '/api/sessions', headers={'Host': 'gateway.example', 'Cookie': 'codex_mobile_session='+token})
            response = c.getresponse(); self.assertEqual(response.status, 200); listing = json.loads(response.read()); c.close()
            self.assertEqual(listing['notificationRead']['cursor'], 1)
            feed.acknowledge('codex', listing['notificationRead']['streamId'], 1)
            other, _ = server.auth.new_session('127.0.0.1')
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            c.request('GET', '/api/mobile/events?after=2', headers={'Host': 'gateway.example', 'Cookie': 'codex_mobile_session='+other})
            response = c.getresponse(); self.assertEqual(response.status, 200); other_phone = json.loads(response.read()); c.close()
            self.assertEqual(other_phone['events'], [])
            self.assertEqual(other_phone['unread']['codex'], 1)

        finally:
            server.shutdown();worker.join();server.server_close()
