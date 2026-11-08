import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import bridge.features.network.addresses as network
from bridge.app.desktop import Desktop
from bridge.features.network.runtime import DynamicListenerManager, RuntimeNetworkMonitor, notify_route_rechecks

ROOT = Path(__file__).resolve().parents[1]


ROWS = [
    {'id': 'en0', 'name': 'Wi-Fi', 'addresses': ['192.168.1.7']},
    {'id': 'bridge', 'name': 'Bridge', 'addresses': ['192.168.1.7', '10.0.0.2']},
    {'id': 'VMnet8', 'name': 'VMnet8', 'addresses': ['192.168.55.1']},
    {'id': 'bridge100', 'name': 'bridge100', 'addresses': ['10.211.55.2']},
    {'id': 'public', 'name': 'public', 'addresses': ['203.0.113.7', '100.64.0.1']},
]


class FakeServer:
    def __init__(self, address, *args, **kwargs):
        self.address = address[0]
        self.closed = False

    def serve_forever(self, **kwargs):
        pass

    def shutdown(self):
        self.closed = True

    def server_close(self):
        self.server_closed = True


class ListenerTests(unittest.TestCase):
    def test_route_recheck_failure_does_not_skip_other_routes(self):
        failed = MagicMock(side_effect=RuntimeError('fixture'))
        quick_tunnel = MagicMock()
        with self.assertLogs('bridge.features.network.runtime', level='ERROR'):
            notify_route_rechecks([failed, quick_tunnel])
        failed.assert_called_once_with()
        quick_tunnel.assert_called_once_with()

    def setUp(self):
        self.root = type('Root', (), {})()
        self.root.origins = {'http://9.0.0.1:8787', 'https://fixed.example'}
        self.root.hosts = {'9.0.0.1:8787', 'fixed.example'}
        self.manager = DynamicListenerManager(self.root, 8787, None, {}, '/web', FakeServer)

    def tearDown(self):
        self.manager.close()

    def test_selected_adapter_listeners_follow_addresses_and_own_only_http_origins(self):
        active, error = self.manager.apply(['192.168.1.7'])
        self.assertEqual(active, ['192.168.1.7'])
        self.assertEqual(error, '')
        self.assertIn('http://192.168.1.7:8787', self.root.origins)
        self.assertNotIn('http://9.0.0.1:8787', self.root.origins)
        self.assertIn('https://fixed.example', self.root.origins)
        active, _ = self.manager.apply(['192.168.1.8'])
        self.assertEqual(active, ['192.168.1.8'])
        self.assertNotIn('http://192.168.1.7:8787', self.root.origins)
        self.assertIn('http://192.168.1.8:8787', self.root.origins)
        self.assertEqual([entry[0].address for entry in self.manager.listeners.values()],
                         ['192.168.1.8'])

    def test_dynamic_lan_update_preserves_local_http_origins(self):
        self.root.origins.update({'http://127.0.0.1:8787', 'http://localhost:8787'})
        self.root.hosts.update({'127.0.0.1:8787', 'localhost:8787'})
        active, _ = self.manager.apply(['192.168.1.7'])
        self.assertEqual(active, ['192.168.1.7'])
        self.assertEqual(
            self.root.origins,
            {'http://192.168.1.7:8787', 'http://127.0.0.1:8787',
             'http://localhost:8787', 'https://fixed.example'})
        self.assertEqual(
            self.root.hosts,
            {'192.168.1.7:8787', '127.0.0.1:8787', 'localhost:8787', 'fixed.example'})

    def test_no_change_keeps_listener_and_does_not_mutate_shared_allowlists(self):
        self.manager.apply(['192.168.1.7'])
        entry = self.manager.listeners['192.168.1.7']
        with patch.object(self.manager, '_set_origins') as publish, \
                patch.object(self.manager, '_start') as start:
            self.manager.apply(['192.168.1.7'])
            publish.assert_not_called()
            start.assert_not_called()
        self.assertIs(self.manager.listeners['192.168.1.7'], entry)

    def test_lan_change_preserves_entries_added_by_other_owners(self):
        self.manager.apply(['192.168.1.7'])
        self.root.origins.update({'https://new.trycloudflare.com', 'http://localhost:9000'})
        self.root.hosts.update({'new.trycloudflare.com', 'localhost:9000'})
        self.manager.apply(['192.168.1.8'])
        self.assertIn('https://new.trycloudflare.com', self.root.origins)
        self.assertIn('http://localhost:9000', self.root.origins)
        self.assertIn('new.trycloudflare.com', self.root.hosts)
        self.assertIn('localhost:9000', self.root.hosts)

    def test_bind_failure_never_publishes_or_expands_the_failed_address(self):
        original = self.manager.server_factory
        def fail_new(address, *args, **kwargs):
            if address[0] == '192.0.2.9':
                raise OSError('unavailable')
            return original(address, *args, **kwargs)
        self.manager.server_factory = fail_new
        active, _ = self.manager.apply(['192.168.1.7'])
        active, error = self.manager.apply(['192.0.2.9'])
        self.assertEqual(active, ['192.168.1.7'])
        self.assertIn('192.0.2.9', error)
        self.assertNotIn('http://192.0.2.9:8787', self.root.origins)

    def test_failed_close_keeps_listener_active_for_retry(self):
        original = self.manager.server_factory
        entries = []
        def factory(address, *args, **kwargs):
            server = original(address, *args, **kwargs)
            entries.append(server)
            return server
        self.manager.server_factory = factory
        self.manager.apply(['192.168.1.7'])
        entries[0].shutdown = lambda: (_ for _ in ()).throw(OSError('busy'))
        self.manager.apply(['192.168.1.8'])
        active, error = self.manager.apply(['192.168.1.8'])
        self.assertEqual(sorted(active), ['192.168.1.7', '192.168.1.8'])
        self.assertIn('192.168.1.7', error)
        entries[0].shutdown = lambda: None
        active, error = self.manager.apply(['192.168.1.8'])
        self.assertEqual(active, ['192.168.1.8'])
        self.assertEqual(error, '')


class FakeRuntimeManager:
    def __init__(self):
        self.applied = []
        self.active = []
        self.closed = False

    def apply(self, addresses):
        self.applied.append(list(addresses))
        self.active = sorted(set(addresses))
        return list(self.active), ''

    def addresses(self):
        return list(self.active)

    def error_message(self):
        return ''

    def close(self):
        self.closed = True


class RuntimeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.folder.cleanup)
        self.manager = FakeRuntimeManager()
        self.rows = [{'id': 'en0', 'name': 'Wi-Fi', 'addresses': ['192.168.1.7']}]
        self.route = {'interface': 'en0', 'gateway': '192.168.1.1'}
        self.now = 0
        self.monitor = RuntimeNetworkMonitor(
            self.folder.name, {'lan': True, 'lanInterfaces': ['en0']},
            8787, self.manager, 'instance', enumerator=lambda: self.rows,
            route_enumerator=lambda rows: self.route, clock=lambda: self.now,
            enabled=True, watcher_factory=lambda callback: None)
        self.addCleanup(self.monitor.close)

    def test_changes_wait_for_stability_and_publish_only_actual_addresses(self):
        self.assertEqual(self.monitor.scan(initial=True), ['192.168.1.7'])
        self.rows[0]['addresses'] = ['192.168.1.8']
        self.now = 2
        self.assertEqual(self.monitor.scan(), ['192.168.1.7'])
        status = self.monitor.status()
        self.assertFalse(status['stable'])
        self.assertEqual(status['pendingAddresses'], ['192.168.1.8'])
        self.assertEqual(self.manager.applied, [['192.168.1.7']])
        self.now = 5
        self.assertEqual(self.monitor.scan(), ['192.168.1.8'])
        self.assertTrue(self.monitor.status()['stable'])

    def test_multiple_addresses_compare_as_one_stable_set(self):
        self.monitor.preferences['lanInterfaces'] = None
        self.rows[0]['addresses'] = ['192.168.1.2', '192.168.1.10']
        self.monitor.scan(initial=True)
        self.now = 30
        self.monitor.scan()
        self.assertEqual(len(self.manager.applied), 1)
        self.assertTrue(self.monitor.status()['stable'])

    def test_same_ip_renewal_does_not_reapply_or_rewrite_status(self):
        with patch('bridge.features.network.runtime._write_status') as write:
            self.monitor.scan(initial=True)
            for self.now in (2, 50, 100):
                self.monitor.scan()
            self.assertEqual(write.call_count, 1)
        self.assertEqual(self.manager.applied, [['192.168.1.7']])

    def test_lan_only_change_does_not_request_tunnel_recovery(self):
        self.monitor.scan(initial=True)
        callbacks = []
        self.monitor.network_changed = lambda: callbacks.append(True)
        self.rows[0]['addresses'] = ['192.168.1.8']
        self.now = 2
        self.monitor.scan()
        self.now = 5
        self.monitor.scan()
        self.assertEqual(self.monitor.addresses(), ['192.168.1.8'])
        self.assertEqual(callbacks, [])

    def test_stable_default_route_change_requests_recovery_once(self):
        self.monitor.scan(initial=True)
        callbacks = []
        self.monitor.network_changed = lambda: callbacks.append(True)
        self.route['gateway'] = '192.168.0.1'
        self.now = 2
        self.monitor.scan()
        self.assertEqual(callbacks, [])
        self.now = 5
        self.monitor.scan()
        self.monitor.scan()
        self.assertEqual(callbacks, [True])

    def test_offline_selected_adapter_does_not_fallback_and_recovers(self):
        self.monitor.scan(initial=True)
        self.rows[0]['addresses'] = []
        self.rows.append({'id': 'eth0', 'name': 'Ethernet', 'addresses': ['192.168.2.7']})
        self.now = 2
        self.monitor.scan()
        self.now = 5
        self.assertEqual(self.monitor.scan(), [])
        self.rows[0]['addresses'] = ['192.168.1.8']
        self.now = 6
        self.monitor.scan()
        self.now = 9
        self.assertEqual(self.monitor.scan(), ['192.168.1.8'])

    def test_short_disconnect_does_not_close_working_listener(self):
        self.monitor.scan(initial=True)
        self.rows[0]['addresses'] = []
        self.now = 1
        self.monitor.scan()
        self.rows[0]['addresses'] = ['192.168.1.7']
        self.now = 2
        self.monitor.scan()
        self.now = 10
        self.monitor.scan()
        self.assertEqual(self.manager.applied, [['192.168.1.7']])

    def test_bind_retry_backs_off_and_resets_for_different_address(self):
        self.monitor.scan(initial=True)
        attempts = []
        def fail(addresses):
            attempts.append((self.now, list(addresses)))
            return self.manager.addresses(), 'occupied'
        self.manager.apply = fail
        self.manager.error_message = lambda: 'occupied'
        self.rows[0]['addresses'] = ['192.168.1.8']
        for self.now in (1, 4, 5, 6, 7, 8, 9, 10):
            self.monitor.scan()
        self.assertEqual([time for time, _ in attempts], [4, 6, 10])
        self.assertEqual(self.monitor.status()['addresses'], ['192.168.1.7'])
        self.rows[0]['addresses'] = ['192.168.1.9']
        self.now = 11
        self.monitor.scan()
        self.now = 14
        self.monitor.scan()
        self.assertEqual(attempts[-1], (14, ['192.168.1.9']))

    def test_continuous_churn_returns_to_slow_fallback(self):
        self.monitor.scan(initial=True)
        self.rows[0]['addresses'] = ['192.168.1.8']
        self.now = 1
        self.monitor.scan()
        self.assertEqual(self.monitor._wait_interval(), 2)
        for self.now in range(2, 15):
            self.rows[0]['addresses'] = ['192.168.1.' + str(10 + self.now)]
            self.monitor.scan()
        self.assertEqual(self.monitor._wait_interval(), 30)

    def test_disabled_default_initializes_once_without_watcher_or_route_queries(self):
        self.monitor.enabled = False
        with patch.object(self.monitor, 'route_enumerator') as route, \
                patch.object(self.monitor, 'watcher_factory') as watcher:
            self.monitor.scan(initial=True)
            self.monitor.start()
            self.rows[0]['addresses'] = ['192.168.1.8']
            self.monitor.scan()
            self.monitor.scan(initial=True)
            route.assert_not_called()
            watcher.assert_not_called()
        self.assertIsNone(self.monitor.worker)
        self.assertEqual(self.manager.applied, [['192.168.1.7']])
        self.assertFalse(self.monitor.status()['enabled'])

    def test_enable_disable_closes_watcher_not_listeners_or_bindings(self):
        self.monitor.enabled = False
        self.monitor.scan(initial=True)
        scanned = threading.Event()
        original = self.monitor.enumerator
        self.monitor.enumerator = lambda: (scanned.set(), original())[1]
        callbacks, closed, cancelled = [], [], []
        class Watcher:
            def close(self): closed.append(True)
        def watch(callback):
            callbacks.append(callback)
            return Watcher()
        self.monitor.watcher_factory = watch
        self.monitor.network_cancelled = lambda: cancelled.append(True)
        self.assertTrue(self.monitor.set_enabled(True)['enabled'])
        self.assertTrue(scanned.wait(2))
        self.assertFalse(self.monitor.set_enabled(False)['enabled'])
        self.assertEqual(closed, [True])
        self.assertEqual(cancelled, [True])
        self.assertFalse(self.manager.closed)
        self.assertEqual(self.monitor.addresses(), ['192.168.1.7'])
        self.assertIsNone(self.monitor.worker)
        scanned.clear()
        callbacks[0]()
        self.monitor.scan()
        self.assertFalse(scanned.is_set())

    def test_disabled_during_enumeration_cannot_apply_late_scan(self):
        self.monitor.scan(initial=True)
        self.rows[0]['addresses'] = ['192.168.1.8']
        self.now = 1
        self.monitor.scan()
        self.now = 5
        def disable_during_read():
            self.monitor.set_enabled(False)
            return self.rows
        self.monitor.enumerator = disable_during_read
        self.monitor.scan()
        self.assertEqual(self.monitor.addresses(), ['192.168.1.7'])

    def test_disable_while_worker_enumerates_discards_inflight_change(self):
        self.monitor.enabled = False
        self.monitor.scan(initial=True)
        started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        def slow_enumerate():
            started.set()
            release.wait(3)
            return [{'id': 'en0', 'name': 'Wi-Fi', 'addresses': ['192.168.1.8']}]
        self.monitor.enumerator = slow_enumerate
        self.monitor.network_cancelled = cancelled.set
        self.monitor.set_enabled(True)
        self.assertTrue(started.wait(2))
        disabled = threading.Thread(target=self.monitor.set_enabled, args=(False,))
        disabled.start()
        try:
            self.assertTrue(cancelled.wait(2))
            release.set()
            disabled.join(3)
            self.assertFalse(disabled.is_alive())
            self.assertEqual(self.monitor.addresses(), ['192.168.1.7'])
            self.assertFalse(self.monitor.enabled)
            self.assertIsNone(self.monitor.worker)
        finally:
            release.set()
            disabled.join(3)

    def test_enable_does_not_claim_success_while_previous_worker_is_stopping(self):
        self.monitor.enabled = False
        self.monitor.scan(initial=True)
        self.monitor.worker = MagicMock(is_alive=lambda: True)
        self.monitor._stop.set()
        try:
            with self.assertRaisesRegex(ValueError, 'still stopping'):
                self.monitor.set_enabled(True)
            self.assertFalse(self.monitor.enabled)
        finally:
            self.monitor.worker = None

    def test_diagnostic_write_failure_does_not_prevent_disabling(self):
        self.monitor.scan(initial=True)
        cancelled = []
        self.monitor.network_cancelled = lambda: cancelled.append(True)
        with patch('bridge.features.network.runtime._write_status', side_effect=OSError('disk full')), \
                self.assertLogs('bridge.features.network.runtime', level='WARNING'):
            self.assertFalse(self.monitor.set_enabled(False)['enabled'])
        self.assertEqual(cancelled, [True])
        self.assertEqual(self.monitor.addresses(), ['192.168.1.7'])

    def test_automatic_mode_ignores_virtual_adapters(self):
        self.monitor.preferences['lanInterfaces'] = None
        self.rows = [dict(row) for row in ROWS]
        self.assertEqual(self.monitor.scan(initial=True), ['192.168.1.7'])


class EntryTests(unittest.TestCase):
    def test_dynamic_addresses_filter_even_when_legacy_addresses_remain(self):
        prefs = {'port': 8787, 'lan': True, 'lanAddresses': ['192.0.2.9'], 'connections': []}
        self.assertEqual(network.bindings({'lan': True, 'lanInterfaces': ['en0']}, ROWS),
                         ['127.0.0.1', '192.168.1.7'])
        from bridge.features.notifications.addresses import entry_urls
        self.assertEqual(
            entry_urls(prefs, ['127.0.0.1', 'localhost', '192.168.1.7', '192.0.2.9'],
                       lan_addresses=['192.168.1.7']),
            ['http://192.168.1.7:8787'])


class DesktopRuntimeTests(unittest.TestCase):
    def test_running_snapshot_uses_gateway_adapter_state_not_a_stale_saved_ip(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            desktop = Desktop(folder)
            desktop.status = lambda: {'running': True, 'pid': 100, 'instanceId': 'live',
                                      'supportsNotifications': True}
            (Path(folder)/'desktop.json').write_text(json.dumps({
                'port': 8787, 'lan': True, 'lanInterfaces': ['en0'],
                'lanAddresses': ['192.0.2.9'], 'codexHome': folder,
            }), encoding='utf-8')
            (Path(folder)/'network-status.json').write_text(json.dumps({
                'pid': 100, 'instanceId': 'live', 'mode': 'selected',
                'selectedInterfaces': ['en0'],
                'interfaces': [{'id': 'en0', 'name': 'Wi-Fi', 'addresses': ['192.168.1.8']}],
                'addresses': ['192.168.1.8'],
            }), encoding='utf-8')
            with patch('bridge.app.desktop.interfaces.enumerate_interfaces',
                       return_value=[{'id': 'en0', 'name': 'Wi-Fi', 'addresses': ['192.0.2.9']}]):
                value = desktop.snapshot()
            self.assertIn('http://192.168.1.8:8787/', value['urls'])
            self.assertNotIn('http://192.0.2.9:8787/', value['urls'])
            self.assertEqual(value['notificationUrls'], ['http://192.168.1.8:8787'])
            self.assertEqual(value['networkStatus']['addresses'], ['192.168.1.8'])
