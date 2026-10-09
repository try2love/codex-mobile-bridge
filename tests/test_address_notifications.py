import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.features.notifications.addresses import AddressNotifications, ready_url, entry_urls
from bridge.features.notifications.channels import save_settings, settings, read_json
from bridge.app.desktop import Desktop

ROOT = Path(__file__).resolve().parents[1]
A = 'https://first-entry.trycloudflare.com'
B = 'https://second-entry.trycloudflare.com'


class AddressNotificationTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.url = A
        self.manager = AddressNotifications(self.directory, lambda: [self.url] if self.url else [], 'instance-1')
        save_settings(self.directory, {'enabled': True, 'topic': 'fixture', 'addressEnabled': True,
                                      'addressName': 'Home computer', 'token': 'never-in-body',
                                      'clickBase': 'https://obsolete.example'})

    def test_defaults_enabled_and_explicit_disable_preserved(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            self.assertTrue(settings(directory)['addressEnabled'])
            save_settings(directory, {'addressEnabled': False})
            self.assertFalse(settings(directory)['addressEnabled'])
        for value in ({'addressEnabled': 'true'}, {'addressName': 'x\ny'}, {'addressName': 'x'*81}):
            with self.assertRaises(ValueError):
                save_settings(self.directory, value)

    def test_first_ready_duplicate_restart_and_changed_url(self):
        with patch('bridge.features.notifications.addresses.publish') as send:
            self.url = ''
            self.manager.scan(); send.assert_not_called()
            self.url = A
            self.manager.scan()
            self.assertIn('Home computer', send.call_args.args[2])
            self.assertNotIn('never-in-body', ''.join(send.call_args.args[1:]))
            self.assertEqual(send.call_args.args[3], A)
            self.manager.scan();self.assertEqual(send.call_count, 1)
            self.manager = AddressNotifications(self.directory, lambda: [self.url] if self.url else [], 'instance-1')
            self.manager.scan();self.assertEqual(send.call_count, 1)
            self.manager = AddressNotifications(self.directory, lambda: [self.url], 'instance-2')
            self.manager.scan();self.assertEqual(send.call_count, 2)
            self.assertIn('网关已启动', send.call_args.args[1])
            self.url = B
            self.manager.scan();self.assertEqual(send.call_count, 3)
            self.assertEqual(send.call_args.args[3], B)
            self.assertFalse(self.manager.state['first'])

    def test_channels_retry_independently_and_persist_backoff(self):
        save_settings(self.directory, {'barkEnabled': True, 'barkKey': 'bark-secret',
                                      'pushplusEnabled': True, 'pushplusToken': 'push-secret'})
        with patch('bridge.features.notifications.addresses.publish', side_effect=RuntimeError('secret')), \
             patch('bridge.features.notifications.addresses.publish_bark') as bark, \
             patch('bridge.features.notifications.addresses.publish_pushplus') as push, \
             patch('bridge.features.notifications.addresses.time.time', return_value=100):
            self.manager.scan();bark.assert_called_once();push.assert_called_once()
        self.assertNotIn('secret', self.manager.path.read_text(encoding='utf-8'))
        self.manager = AddressNotifications(self.directory, lambda: [self.url] if self.url else [], 'instance-1')
        with patch('bridge.features.notifications.addresses.publish') as ntfy, \
             patch('bridge.features.notifications.addresses.publish_bark') as bark, \
             patch('bridge.features.notifications.addresses.publish_pushplus') as push, \
             patch('bridge.features.notifications.addresses.time.time', return_value=105):
            self.manager.scan();ntfy.assert_not_called()
        with patch('bridge.features.notifications.addresses.publish') as ntfy, \
             patch('bridge.features.notifications.addresses.publish_bark') as bark, \
             patch('bridge.features.notifications.addresses.publish_pushplus') as push, \
             patch('bridge.features.notifications.addresses.time.time', return_value=111):
            self.manager.scan();ntfy.assert_called_once();bark.assert_not_called();push.assert_not_called()

    def test_latest_address_replaces_retry_and_stop_suppresses_delivery(self):
        with patch('bridge.features.notifications.addresses.publish', side_effect=RuntimeError()):self.manager.scan()
        self.url = ''
        with patch('bridge.features.notifications.addresses.publish') as send:
            self.manager.scan();send.assert_not_called()
            self.url = B
            self.manager.scan();self.assertEqual(send.call_args.args[3], B)
            self.manager.close();self.url = A
            self.manager.scan();self.assertEqual(send.call_count, 1)

    def test_turning_off_or_replacing_destination_cancels_old_retry(self):
        with patch('bridge.features.notifications.addresses.publish', side_effect=RuntimeError()):self.manager.scan()
        save_settings(self.directory, {'addressEnabled': False})
        with patch('bridge.features.notifications.addresses.publish') as send:
            self.manager.scan();send.assert_not_called()
            save_settings(self.directory, {'addressEnabled': True, 'topic': 'replacement'})
            self.manager.scan();send.assert_called_once()
            self.assertEqual(send.call_args.args[0]['topic'], 'replacement')
            self.assertEqual(len(self.manager.state['deliveries']), 1)

    def test_address_changes_during_send_no_old_link_to_next_channel(self):
        save_settings(self.directory, {'barkEnabled': True, 'barkKey': 'fixture'})
        def change(*args):self.url = B
        with patch('bridge.features.notifications.addresses.publish', side_effect=change), \
             patch('bridge.features.notifications.addresses.publish_bark') as bark:
            self.manager.scan();bark.assert_not_called()
            self.manager.scan();self.assertEqual(bark.call_args.args[3], B)

    def test_local_http_delivery_contains_latest_click_without_credentials(self):
        messages = []
        class Receiver(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                messages.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.end_headers();self.wfile.write(b'{}')
            def log_message(self, *args):pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            save_settings(self.directory, {'server': 'http://127.0.0.1:'+str(server.server_port)})
            self.manager.scan()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]['click'], A)
            self.assertNotIn('never-in-body', json.dumps(messages))
        finally:server.shutdown();server.server_close();thread.join()

    def test_manual_test_uses_current_entry_without_touching_delivery_ledger(self):
        desktop = Desktop(self.directory)
        with patch.object(desktop, 'snapshot', return_value={'notificationUrls': [A, 'http://192.168.1.3:8787']}), \
             patch('bridge.features.notifications.addresses.publish') as send:
            desktop.test_notification({'channel': 'address'})
            self.assertEqual(send.call_args.args[3], A)
            self.assertIn('http://192.168.1.3:8787', send.call_args.args[2])
            self.assertFalse(self.manager.path.exists())
        with patch.object(desktop, 'snapshot', return_value={'notificationUrls': []}):
            with self.assertRaises(ValueError):desktop.test_notification({'channel': 'address'})

    def test_ready_url_requires_live_tunnel_and_correct_gateway(self):
        tunnel = SimpleNamespace(url=A, ready=threading.Event(), closed=threading.Event(),
                                 broken=threading.Event(), finished=threading.Event())
        with patch('bridge.features.notifications.addresses.http.client.HTTPConnection') as connection:
            response=connection.return_value.getresponse.return_value
            response.status=200;response.read.return_value=b'{"instanceId":"ours"}'
            self.assertEqual(ready_url(tunnel, 8787, 'ours'), '')
            connection.assert_not_called()
            tunnel.ready.set()
            self.assertEqual(ready_url(tunnel, 8787, 'ours'), A)
            self.assertEqual(ready_url(tunnel, 8787, 'another'), '')
            tunnel.finished.set()
            self.assertEqual(ready_url(tunnel, 8787, 'ours'), '')

    def test_enabled_entries_and_selected_lan_only(self):
        preferences = {'port': 8787, 'lan': True, 'lanAddresses': ['192.168.1.3'],
                       'connections': [
                           {'id': 'server', 'accessMode': 'server', 'enabled': True, 'publicUrl': 'https://server.example'},
                           {'id': 'nas', 'accessMode': 'nas', 'enabled': True, 'publicUrl': 'https://nas.example'},
                           {'id': 'off', 'accessMode': 'nas', 'enabled': False, 'publicUrl': 'https://off.example'}]}
        hosts = ['127.0.0.1', 'localhost', '::1', '0.0.0.0', '192.168.1.3', '192.168.55.1']
        self.assertEqual(entry_urls(preferences, hosts), ['https://nas.example', 'http://192.168.1.3:8787'])
        self.assertEqual(entry_urls(preferences, hosts, A, {'server': {'state': 'connected'}}),
                         ['https://server.example', 'https://nas.example', A, 'http://192.168.1.3:8787'])
        preferences['lan'] = False
        self.assertEqual(entry_urls(preferences, hosts, '', {'server': {'state': 'retrying'}}), ['https://nas.example'])

    def test_lan_and_fixed_addresses_resend_each_start_and_delayed_tunnel_updates(self):
        urls = ['https://server.example', 'http://192.168.1.3:8787']
        current = lambda: urls[:]
        with patch('bridge.features.notifications.addresses.publish') as send:
            manager = AddressNotifications(self.directory, current, 'first-run')
            manager.scan();manager.scan()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[3], urls[0])
            self.assertTrue(all(url in send.call_args.args[2] for url in urls))
            manager = AddressNotifications(self.directory, current, 'second-run')
            manager.scan();manager.scan()
            self.assertEqual(send.call_count, 2)
            urls.insert(1, A)
            manager.scan();manager.scan()
            self.assertEqual(send.call_count, 3)
            self.assertIn(A, send.call_args.args[2])
            self.assertIn('访问入口已更新', send.call_args.args[1])

    def test_old_ledger_does_not_suppress_new_start(self):
        self.manager.path.write_text(json.dumps({'url': A, 'deliveries': {}}))
        with patch('bridge.features.notifications.addresses.publish') as send:
            manager = AddressNotifications(self.directory, lambda: [A], 'new-run')
            manager.scan();send.assert_called_once()

    def test_manual_lan_only_requires_running_gateway(self):
        desktop = Desktop(self.directory)
        with patch.object(desktop, 'snapshot', return_value={'notificationUrls': ['http://192.168.1.3:8787']}), \
             patch('bridge.features.notifications.addresses.publish') as send:
            desktop.test_notification({'channel': 'address'})
            self.assertEqual(send.call_args.args[3], 'http://192.168.1.3:8787')
