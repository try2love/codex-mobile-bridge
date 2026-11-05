"""Disabled access leaves native tasks alone and does no background discovery."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge, LiveSession
from bridge.clients.manager import DesktopSessions
from bridge.features.notifications.channels import Notifications
from bridge.clients.codex.catalog import Catalog
from bridge.features.accounts.monitor import AccountMonitor
import test_bridge as support


class DisabledObserversTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.manager = DesktopSessions(self.temp.name)
        self.addCleanup(self.manager.close)

    def test_disabled_status_does_not_probe_native_or_cli_adapters(self):
        for provider in ('claude', 'deepseek', 'zcode'):
            self.manager.adapters[provider] = Mock()
        for _ in range(5):
            self.assertTrue(all(not row['connected'] for row in self.manager.status().values()))
        for adapter in self.manager.adapters.values():
            adapter.status.assert_not_called()
            adapter.call.assert_not_called()
        self.manager.config['enabled'] = {'deepseek': True}
        self.manager.adapters['deepseek'].call.return_value = {'connected': True}
        self.assertTrue(self.manager.status()['deepseek']['connected'])

    def test_disabled_model_timers_do_not_query_or_rearm(self):
        self.manager.gateway_running = True
        self.manager.adapters['zcode'] = Mock()
        for provider in self.manager.adapters:
            self.assertFalse(self.manager._model_live(provider, 'test'))
            self.assertFalse(self.manager._account_model_live(provider))
            self.manager._schedule_model_refresh(provider, 'test', provider, 0)
            self.manager._schedule_account_model_refresh(provider, provider, 0)
        self.assertEqual(self.manager.model_timers, {})
        self.manager.adapters['zcode'].status.assert_not_called()

    def test_already_scheduled_model_refresh_is_discarded_after_disable(self):
        self.manager.gateway_running = True
        self.manager.config['enabled'] = {'deepseek': True}
        self.manager._account_models = Mock()
        with patch('bridge.clients.manager.threading.Timer') as timer:
            self.manager._schedule_account_model_refresh('deepseek', 'account-one', 0)
            due = timer.call_args.args[1]
            self.manager.config['enabled']['deepseek'] = False
            due()
            self.manager._account_models.assert_not_called()
            self.assertEqual(self.manager.model_timers, {})
            self.assertEqual(timer.call_count, 1)

    def test_disabled_manual_model_refresh_remains_available_without_scheduling(self):
        self.manager.gateway_running = True
        self.manager._account_model_identity = Mock(return_value='fixture-account')
        self.manager._read = Mock(return_value={'models': [{'id': 'manual-model'}]})
        value = self.manager._account_models('deepseek', refresh=True)
        self.assertEqual(value['models'][0]['id'], 'manual-model')
        self.manager._read.assert_called_once_with('deepseek', 'account')
        self.assertEqual(self.manager.model_timers, {})

    def test_disabled_account_monitor_does_not_query_quota_or_updates(self):
        monitor = AccountMonitor.__new__(AccountMonitor)
        monitor.closed = threading.Event()
        monitor.preferences = {'desktopUpdate': True}
        monitor.manager = SimpleNamespace(bridge=SimpleNamespace(observation_enabled=lambda: False))
        with patch('bridge.features.accounts.monitor.channels') as destinations:
            monitor.scan()
            destinations.assert_not_called()

    def test_codex_catalog_suspends_background_only_and_keeps_verified_cache(self):
        enabled = True
        catalog = Catalog(self.temp.name, Path(self.temp.name)/'fixture-runtime',
                          observation_enabled=lambda: enabled)
        self.addCleanup(catalog.stop_model_refresh)
        catalog.model_store.write('identity', {'models': [{'id': 'fixture-model'}]})
        catalog.model_identity = Mock(return_value='identity')
        catalog.get_kind = Mock()
        with patch('bridge.clients.codex.catalog.threading.Timer') as timer:
            catalog._schedule_model_refresh('/project', 'provider', 'identity', 0)
            due = timer.call_args.args[1]
            enabled = False
            due()
            catalog._start_model_refresh('/project', 'provider', 'identity')
            catalog._start_skill_refresh('/project', 'provider', 'skills')
            catalog.model_identity.assert_not_called()
            catalog.get_kind.assert_not_called()
            self.assertEqual(catalog.model_store.read('identity')[1]['models'][0]['id'], 'fixture-model')
            enabled = True
            catalog._schedule_model_refresh('/project', 'provider', 'identity', 0)
            self.assertEqual(timer.call_count, 2)

    def test_disabled_codex_discovery_preserves_enabled_independent_entries(self):
        bridge = Bridge.__new__(Bridge)
        bridge.host = 'local'
        bridge.observation_enabled = lambda: False
        bridge.hosts = Mock()
        bridge.ipc = Mock()
        child = Mock()
        child.notification_candidates.return_value = [{'host': 'codex:independent', 'id': 'test'}]
        bridge.entries = SimpleNamespace(hosts=lambda: ['codex:independent'], get=lambda _: child)
        self.assertEqual(bridge.notification_candidates(), child.notification_candidates.return_value)
        bridge.hosts.hosts.assert_not_called()
        bridge.ipc.connect.assert_not_called()

    def test_disabled_maintenance_neither_reconnects_nor_dispatches_queued_writes(self):
        bridge = Bridge.__new__(Bridge)
        bridge.observation_enabled = lambda: False
        bridge.closed = Mock()
        bridge.closed.wait.side_effect = [False, True]
        bridge.accounts = Mock()
        bridge.ipc = Mock()
        bridge._send_queued = Mock()
        bridge._refresh_async = Mock()
        bridge._maintain()
        bridge.accounts.check_ready.assert_not_called()
        bridge._send_queued.assert_not_called()
        bridge._refresh_async.assert_not_called()

    def test_suspend_disconnects_followers_without_closing_cli_or_original_app(self):
        bridge = Bridge.__new__(Bridge)
        bridge.ipc = Mock(connect_lock=threading.RLock())
        bridge.remote_bridges = {'ssh:test': Mock()}
        bridge.cli_sessions = Mock()
        bridge.suspend_observation()
        bridge.ipc.close.assert_called_once_with()
        bridge.remote_bridges['ssh:test'].suspend_observation.assert_called_once_with()
        bridge.cli_sessions.close.assert_not_called()

    def test_disabled_cached_watch_is_not_selected_and_independent_watch_remains(self):
        notifications = Notifications.__new__(Notifications)
        notifications.desktop_sessions = self.manager
        notifications.policies = lambda: {'requests': True, 'completion': True, 'chats': {}}
        notifications.watches = lambda: []
        notifications.candidates = {('local', 'one'): {'host': 'local', 'id': 'one'},
                                    ('codex:independent', 'two'): {'host': 'codex:independent', 'id': 'two'}}
        notifications._effective = lambda row, *args: {**row, 'notifyOnRequest': True}
        self.manager.config['enabled'] = {'codex': False}
        self.manager.bridge = SimpleNamespace(accounts=SimpleNamespace(), entries=SimpleNamespace(enabled=lambda _: True))
        self.assertEqual([row['id'] for row in notifications._selected()], ['two'])


class CodexObserverRecoveryTests(unittest.TestCase):
    setUp = support.IntegrationTests.setUp
    tearDown = support.IntegrationTests.tearDown

    def test_disable_and_reenable_preserve_original_owner_and_history(self):
        enabled = True
        self.bridge.observation_enabled = lambda: enabled
        session = self.bridge.session(support.THREAD)
        self.assertTrue(session.connected)
        original_state = session.state.copy()
        enabled = False
        self.bridge.suspend_observation()
        self.assertFalse(session.connected)
        before = len(self.fixture.requests)
        self.bridge.notification_candidates()
        self.bridge._attach(session)
        self.bridge._refresh_async(session, force=True)
        self.assertEqual(len(self.fixture.requests), before)
        self.assertEqual(session.state, original_state)
        self.assertIsNone(self.bridge.ipc.socket)
        enabled = True
        session.retry_at = 0
        self.bridge._attach(session, timeout=3)
        self.assertTrue(session.connected)
        self.assertEqual(session.state['modelProvider'], 'custom-api')
        self.assertEqual(session.state['id'], support.THREAD)
        self.assertFalse(any(request['method'] == 'thread-follower-start-turn' for request in self.fixture.requests))


if __name__ == '__main__':
    unittest.main()
