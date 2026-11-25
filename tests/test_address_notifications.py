import http.server
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.features.notifications.addresses import AddressNotifications, ready_url, entry_urls
from bridge.features.notifications.channels import save_settings, save_webhook_endpoint, settings, read_json
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

    def test_webhook_gateway_event_names_follow_existing_address_state(self):
        save_settings(self.directory, {'enabled': False, 'webhookEnabled': True,
                                       'webhookUrl': 'https://receiver.example/hook'})
        with patch('bridge.features.notifications.addresses.publish_webhook') as webhook:
            self.manager.scan()
            self.manager.scan()
            self.url = B
            self.manager.scan()
            self.url = A
            self.manager.scan()
            self.assertEqual([call.kwargs['event'] for call in webhook.call_args_list],
                             ['gateway.started', 'gateway.address_changed', 'gateway.address_changed'])
            ids = [call.kwargs['event_id'] for call in webhook.call_args_list]
            self.assertEqual(len(set(ids)), 3)

    def test_address_notice_fans_out_to_named_webhooks(self):
        save_settings(self.directory, {'enabled': False, 'webhookEnabled': True,
                                       'webhookUrl': 'https://receiver.example/legacy'})
        save_webhook_endpoint(self.directory, {'action': 'upsert', 'webhookName': '飞书',
            'webhookEnabled': True, 'webhookUrl': 'https://open.feishu.cn/open-apis/bot/v2/hook/one'})
        save_webhook_endpoint(self.directory, {'action': 'upsert', 'webhookName': 'Telegram',
            'webhookEnabled': True, 'webhookUrl': 'https://api.telegram.org/botprivate/sendMessage',
            'webhookBodyTemplate': '{"chat_id":"123","text":"{{body}}"}'})
        with patch('bridge.features.notifications.addresses.publish_webhook') as webhook:
            self.manager.scan()
            self.manager.scan()
        self.assertEqual(webhook.call_count, 3)
        self.assertEqual(len(self.manager.state['deliveries']), 3)

    def test_webhook_address_retry_retains_event_id_after_restart(self):
        save_settings(self.directory, {'enabled': False, 'webhookEnabled': True,
                                       'webhookUrl': 'https://receiver.example/hook'})
        with patch('bridge.features.notifications.addresses.publish_webhook', side_effect=OSError('offline')) as webhook:
            self.manager.scan()
            first_id = webhook.call_args.kwargs['event_id']
        self.manager = AddressNotifications(self.directory, lambda: [self.url], 'instance-1')
        for record in self.manager.state['deliveries'].values():
            record['next'] = 0
        with patch('bridge.features.notifications.addresses.publish_webhook') as webhook:
            self.manager.scan()
            self.assertEqual(webhook.call_args.kwargs['event_id'], first_id)
            self.manager.scan()
            webhook.assert_called_once()
        self.manager = AddressNotifications(self.directory, lambda: [self.url], 'instance-2')
        with patch('bridge.features.notifications.addresses.publish_webhook') as webhook:
            self.manager.scan()
            self.assertNotEqual(webhook.call_args.kwargs['event_id'], first_id)

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

    def test_dynamic_lan_addresses_replace_stale_startup_hosts(self):
        preferences = {'port': 8787, 'lan': True, 'lanAddresses': ['192.168.5.22'], 'connections': []}
        hosts = ['127.0.0.1', 'localhost', '192.168.5.22']
        self.assertEqual(entry_urls(preferences, hosts), ['http://192.168.5.22:8787'])
        self.assertEqual(
            entry_urls(preferences, hosts, lan_addresses=['192.168.0.174']),
            ['http://192.168.0.174:8787'])

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

    def test_corrupt_state_is_preserved_and_startup_notice_recovers(self):
        valid = {'instanceId': 'instance-1', 'urls': [A], 'first': True,
                 'eventId': 'previous-event', 'deliveries': {}}
        invalid_states = [b'\0' * 256, b'', b'{"instanceId":', b'\xff', b'[' * 2000 + b']' * 2000,
                          b'null', b'[]', b'"state"', b'1',
                          json.dumps({**valid, 'urls': A}).encode(),
                          json.dumps({**valid, 'first': 'true'}).encode(),
                          json.dumps({**valid, 'deliveries': []}).encode(),
                          json.dumps({**valid, 'deliveries': {'target': None}}).encode(),
                          json.dumps({**valid, 'deliveries': {'target': {'attempts': True}}}).encode(),
                          json.dumps({**valid, 'deliveries': {'target': {'next': float('nan')}}}).encode(),
                          json.dumps({**valid, 'deliveries': {'target': {'delivered': False, 'next': 'later'}}}).encode()]
        for raw in invalid_states:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
                path = Path(directory)/'address-notifications.json'
                path.write_bytes(raw)
                save_settings(directory, {'enabled': True, 'topic': 'fixture', 'addressEnabled': True})
                with self.assertLogs('bridge.features.notifications.addresses', level='WARNING') as logs:
                    manager = AddressNotifications(directory, lambda: [A], 'instance-1')
                evidence, = path.parent.glob(path.name + '.corrupt-*')
                self.assertEqual(evidence.read_bytes(), raw)
                if os.name != 'nt':
                    self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o600)
                self.assertIn(str(evidence), '\n'.join(logs.output))
                with patch('bridge.features.notifications.addresses.publish') as send:
                    manager.scan();manager.scan()
                    send.assert_called_once()
                self.assertEqual(read_json(path, {})['urls'], [A])
                self.assertEqual(evidence.read_bytes(), raw)

    def test_failed_corrupt_state_preservation_disables_only_address_delivery(self):
        raw = b'\0' * 32
        self.manager.path.write_bytes(raw)
        with patch('bridge.features.notifications.addresses.tempfile.NamedTemporaryFile', side_effect=PermissionError('locked')), \
             self.assertLogs('bridge.features.notifications.addresses', level='WARNING'):
            manager = AddressNotifications(self.directory, lambda: [A], 'instance-1')
        with patch('bridge.features.notifications.addresses.publish') as send:
            manager.scan();manager.scan()
            send.assert_not_called()
        self.assertEqual(self.manager.path.read_bytes(), raw)

    def test_evidence_snapshot_does_not_move_concurrently_replaced_state(self):
        path = self.manager.path
        raw = b'{"secret":"not-for-logs",'
        path.write_bytes(raw)
        newer = {'instanceId': 'newer-instance', 'urls': [B], 'first': True, 'deliveries': {}}
        create = tempfile.NamedTemporaryFile
        def replace_then_preserve(*args, **kwargs):
            path.write_text(json.dumps(newer), encoding='utf-8')
            return create(*args, **kwargs)
        with patch('bridge.features.notifications.addresses.tempfile.NamedTemporaryFile', side_effect=replace_then_preserve), \
             self.assertLogs('bridge.features.notifications.addresses', level='WARNING') as logs:
            AddressNotifications(self.directory, lambda: [A], 'instance-1')
        evidence, = self.directory.glob(path.name + '.corrupt-*')
        self.assertEqual(evidence.read_bytes(), raw)
        self.assertEqual(read_json(path, {}), newer)
        self.assertNotIn('not-for-logs', '\n'.join(logs.output))

    def test_unreadable_state_does_not_block_construction_or_send(self):
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('locked')), \
             self.assertLogs('bridge.features.notifications.addresses', level='WARNING'):
            manager = AddressNotifications(self.directory, lambda: [A], 'instance-1')
        with patch('bridge.features.notifications.addresses.publish') as send:
            manager.scan()
            send.assert_not_called()

    def test_shared_json_reader_still_rejects_corrupt_delivery_ledger(self):
        path = self.directory/'notification-delivery.json'
        path.write_bytes(b'\0' * 32)
        with self.assertRaises(json.JSONDecodeError):
            read_json(path, {})
        self.assertEqual(path.read_bytes(), b'\0' * 32)

    def test_manual_lan_only_requires_running_gateway(self):
        desktop = Desktop(self.directory)
        with patch.object(desktop, 'snapshot', return_value={'notificationUrls': ['http://192.168.1.3:8787']}), \
             patch('bridge.features.notifications.addresses.publish') as send:
            desktop.test_notification({'channel': 'address'})
            self.assertEqual(send.call_args.args[3], 'http://192.168.1.3:8787')
