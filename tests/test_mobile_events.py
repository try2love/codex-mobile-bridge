import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.features.auth.auth import password_record
from bridge.api.httpd import GatewayServer
from bridge.features.notifications.events import MobileEvents
from bridge.features.notifications.channels import Notifications, save_settings, settings

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

    def test_notification_defaults_preserve_existing_opt_outs(self):
        initial = settings(self.path)
        self.assertTrue(initial['mobileEnabled'])
        self.assertTrue(initial['includeTitle'])
        for channel in ('enabled', 'barkEnabled', 'pushplusEnabled', 'webhookEnabled'):
            self.assertFalse(initial[channel])
        save_settings(self.path, {'mobileEnabled': False, 'includeTitle': False})
        self.assertFalse(settings(self.path)['mobileEnabled'])
        self.assertFalse(settings(self.path)['includeTitle'])
        save_settings(self.path, {'addressName': 'Test gateway'})
        self.assertFalse(settings(self.path)['mobileEnabled'])
        self.assertFalse(settings(self.path)['includeTitle'])
        # A legacy partial config receives defaults only for fields it never saved.
        (self.path / 'notifications.json').write_text('{"mobileEnabled": false}')
        self.assertFalse(settings(self.path)['mobileEnabled'])
        self.assertTrue(settings(self.path)['includeTitle'])

    def test_fresh_gateway_default_delivers_title_to_mobile_inbox(self):
        manager = Notifications(None, self.path)
        self.addCleanup(manager.close)
        config = settings(self.path)
        row = {'id': THREAD, 'host': 'local'}
        target = manager._targets(config)['mobile']
        key = manager._delivery_key(row, 'decision', target=target)
        self.assertTrue(manager._send_keys(config, row, 'Test chat', 'mobile', target, [key], False))
        self.assertEqual(manager.mobile.read()['events'][0]['body'], 'Test chat')

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

    def test_inbox_body_uses_only_the_opted_in_chat_title(self):
        manager = Notifications(None, self.path, lambda: ['https://gateway.example'])
        manager.defaults({'requests': True, 'completion': True})
        row = {'id': THREAD, 'host': 'local'}
        title = '聊天标题' * 40
        for completed in (False, True):
            for include_title in (False, True):
                with self.subTest(completed=completed, include_title=include_title):
                    config = save_settings(self.path, {'mobileEnabled': True, 'includeTitle': include_title})
                    target = manager._targets(config)['mobile']
                    key = manager._delivery_key(row, f'{completed}:{include_title}', target=target)
                    self.assertTrue(manager._send_keys(config, row, title, 'mobile', target, [key], completed))
                    event = manager.mobile.read()['events'][-1]
                    generic = ('有 1 次运行已完成，请打开聊天查看。' if completed
                               else '有 1 项请求等待处理，请打开聊天查看。')
                    self.assertEqual(event['body'], title[:120] if include_title else generic)
                    self.assertEqual(event['title'], 'Codex 运行已完成' if completed else 'Codex 需要你的确认')

    def test_inbox_delivery_preserves_global_policy_and_hides_private_title(self):
        manager = Notifications(None, self.path, lambda: ['https://gateway.example'])
        config = save_settings(self.path, {'mobileEnabled': True, 'includeTitle': False})
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


class LongPollTests(unittest.TestCase):
    setUp = MobileEventTests.setUp

    def manager(self):
        save_settings(self.path, {'mobileEnabled': True})
        manager = Notifications(None, self.path)
        self.addCleanup(manager.close)
        return manager

    def start_wait(self, manager, baseline=None, seconds=1):
        baseline = baseline or manager.mobile_snapshot()
        entered = threading.Event()
        original = manager.mobile.wait
        def wait(version, timeout):
            entered.set()
            original(version, timeout)
        result = []
        with patch.object(manager.mobile, 'wait', side_effect=wait):
            worker = threading.Thread(target=lambda: result.append(manager.mobile_snapshot(
                baseline['cursor'], wait=seconds, stream=baseline['streamId'], revision=baseline['revision'])))
            worker.start()
            self.assertTrue(entered.wait(.5))
        self.addCleanup(worker.join, 2)
        return worker, result

    def test_idle_deadline_and_legacy_immediate_capability(self):
        manager = self.manager()
        baseline = manager.mobile_snapshot()
        self.assertEqual(baseline['longPoll'], 60)
        started = time.monotonic()
        value = manager.mobile_snapshot(0, wait=.08, stream=baseline['streamId'], revision=baseline['revision'])
        self.assertGreaterEqual(time.monotonic() - started, .07)
        self.assertGreaterEqual(value['timing']['waitMs'], 70)
        self.assertEqual({k: v for k, v in value.items() if k != 'timing'},
                         {k: v for k, v in baseline.items() if k != 'timing'})

    def test_event_wakes_every_waiter_without_holding_the_feed_lock(self):
        manager = self.manager()
        pending = [self.start_wait(manager) for _ in range(4)]
        manager.mobile.append(['new'], {'id': THREAD, 'host': 'local'}, 'Test', 'Body', False)
        for worker, result in pending:
            worker.join(.5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result[0]['cursor'], 1)
            self.assertGreaterEqual(result[0]['events'][0]['ageMs'], 0)
            self.assertLess(result[0]['events'][0]['ageMs'], 500)

    def test_acknowledgement_and_policy_changes_wake_without_new_events(self):
        manager = self.manager()
        manager.mobile.append(['new'], {'id': THREAD, 'host': 'local'}, 'Test', 'Body', False)
        worker, result = self.start_wait(manager)
        manager.mobile.acknowledge('codex', manager.mobile.checkpoint()['streamId'], 1)
        worker.join(.5)
        self.assertEqual(result[0]['unread']['codex'], 0)
        worker, result = self.start_wait(manager)
        manager.defaults({'requests': False, 'completion': False})
        worker.join(.5)
        self.assertEqual(result[0]['summary'], [])

    def test_change_between_snapshot_and_wait_is_observed(self):
        manager = self.manager()
        baseline = manager.mobile_snapshot()
        original = manager.mobile.wait
        def race(version, timeout):
            manager.mobile.append(['race'], {'id': THREAD, 'host': 'local'}, 'Test', 'Body', False)
            original(version, timeout)
        with patch.object(manager.mobile, 'wait', side_effect=race):
            value = manager.mobile_snapshot(0, wait=1, stream=baseline['streamId'], revision=baseline['revision'])
        self.assertEqual(value['cursor'], 1)

    def test_stale_revision_stream_and_ahead_cursor_return_immediately(self):
        manager = self.manager()
        baseline = manager.mobile_snapshot()
        with patch.object(manager.mobile, 'wait', side_effect=AssertionError('Unexpected wait')):
            for after, stream, revision in [(0, 'replaced', baseline['revision']), (1, baseline['streamId'], baseline['revision']),
                                             (0, baseline['streamId'], '0' * 32)]:
                self.assertEqual(manager.mobile_snapshot(after, wait=60, stream=stream, revision=revision)['cursor'], 0)

    def test_external_settings_keep_the_existing_refresh_bound(self):
        manager = self.manager()
        baseline = manager.mobile_snapshot()
        def external_save(version, timeout):
            self.assertLessEqual(timeout, 15)
            save_settings(self.path, {'mobileEnabled': False})
        with patch.object(manager.mobile, 'wait', side_effect=external_save):
            result = manager.mobile_snapshot(0, wait=60, stream=baseline['streamId'], revision=baseline['revision'])
        self.assertFalse(result['enabled'])
        self.assertNotEqual(result['revision'], baseline['revision'])

    def test_close_wakes_pending_requests(self):
        manager = self.manager()
        worker, result = self.start_wait(manager, seconds=60)
        manager.close()
        worker.join(.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['events'], [])

    def test_http_validates_wait_and_rechecks_revocation_after_wait(self):
        server = GatewayServer(('127.0.0.1', 0), object(), {'auth': {'mode': 'password', 'username': 'test',
            **password_record('long-password')}, 'origins': ['https://gateway.example']}, ROOT/'web')
        server.notifications = manager = self.manager()
        token, _ = server.auth.new_session('127.0.0.1')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(query):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request('GET', '/api/mobile/events?' + query, headers={'Host': 'gateway.example',
                'Origin': 'https://gateway.example', 'Cookie': 'codex_mobile_session=' + token})
            response = connection.getresponse()
            value = response.status, json.loads(response.read())
            connection.close()
            return value
        try:
            for query in ('wait=61', 'wait=-1', 'wait=abc', 'wait=100000000000', 'revision=bad', 'streamId='):
                self.assertEqual(request(query)[0], 400)
            code, baseline = request('after=0')
            self.assertEqual(code, 200)
            entered = threading.Event()
            original = manager.mobile.wait
            def revoke(version, timeout):
                entered.set()
                server.auth.logout(token)
                manager.close()
                original(version, timeout)
            with patch.object(manager.mobile, 'wait', side_effect=revoke):
                self.assertEqual(request('after=0&wait=60&streamId=' + baseline['streamId']
                    + '&revision=' + baseline['revision'])[0], 401)
            self.assertTrue(entered.is_set())
        finally:
            server.shutdown()
            worker.join()
            server.server_close()


class ServerUnreadTests(unittest.TestCase):
    setUp = MobileEventTests.setUp
    def test_gateway_ack_is_durable_provider_scoped_and_preserves_new_events(self):
        feed = MobileEvents(self.path)
        for host in ('local', 'desktop:claude', 'desktop:deepseek'):
            feed.append([host], {'id': THREAD, 'host': host}, 'Title', 'Body', False)
        viewed = feed.read()
        feed.append(['late'], {'id': THREAD, 'host': 'desktop:claude'}, 'Title', 'Body', False)
        feed.acknowledge('claude', viewed['streamId'], viewed['cursor'])
        self.assertEqual(feed.read()['unread'], {'codex': 1, 'claude': 1, 'deepseek': 1, 'zcode': 0, 'antigravity': 0})
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
        self.assertEqual(manager.mobile_snapshot()['unread'], {'codex': 2, 'claude': 2, 'deepseek': 0, 'zcode': 0, 'antigravity': 0})
        manager.defaults({'requests': True, 'completion': False})
        self.assertEqual(manager.mobile_snapshot(999)['unread'], {'codex': 1, 'claude': 1, 'deepseek': 0, 'zcode': 0, 'antigravity': 0})
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
            def list_sessions(**kwargs):
                feed.append(['during-list'], {'id': THREAD, 'host': 'local'}, 'Late', 'Body', False)
                rows = []
                return (rows, []) if kwargs.get('include_sources') else rows
            server.bridge.list.side_effect = list_sessions
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
