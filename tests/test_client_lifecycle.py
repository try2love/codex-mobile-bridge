"""Native app lifecycle uses isolated process and desktop-state fixtures only."""
import tempfile
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.integrations.manager import DesktopSessions
from bridge.integrations.errors import BridgeUnavailable

ROOT = Path(__file__).resolve().parents[1]


class ClientLifecycleTests(unittest.TestCase):
    def setUp(self):
        platform = patch('bridge.integrations.manager.sys', SimpleNamespace(platform='darwin'))
        platform.start(); self.addCleanup(platform.stop)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.claude, self.dsh = Mock(), Mock()
        self.claude.status.return_value = {'connected': True}
        self.claude.call.side_effect = lambda action, *args: ({'connected': True, 'complete': True,
            'sessions': [{'id': 'one', 'status': 'idle', 'runtimeKnown': True, 'requests': []}]} if action == 'list' else {'models': [{'id': 'model'}]})
        self.dsh.reused = False
        self.dsh.home = self.root
        profile = self.root/'profiles/desktop/cordis.patch.yml'
        profile.parent.mkdir(parents=True); profile.write_text('')
        self.dsh.ensure_installed.return_value = {'installed': True}
        self.dsh.directory = self.root/'desktop-sessions/deepseek'
        self.dsh.call.side_effect = lambda action, *args: ({'connected': True, 'configured': True, 'bridgeRevision': 3}
            if action == 'status' else {'connected': True, 'complete': True, 'bridgeRevision': 3, 'sessions': []})
        self.accounts = SimpleNamespace(gate=threading.RLock(), lock=threading.RLock(),
            assert_editable=Mock(), idle=Mock(), index={}, runtime=None,
            info=SimpleNamespace(current=lambda: {'status': 'ready', 'kind': 'chatgpt'}), home=self.root)
        self.bridge = SimpleNamespace(accounts=self.accounts, assert_desktop_idle=Mock())
        self.bridge.for_host = lambda _: self.bridge
        for name, value in [('Claude', self.claude), ('DeepSeek', self.dsh)]:
            patcher = patch('bridge.integrations.manager.'+name, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.manager = DesktopSessions(self.root, self.bridge, gateway_running=True)
        self.addCleanup(self.manager.close)
        discovered = {}
        for provider in ('codex', 'claude', 'deepseek'):
            executable = self.root/provider; executable.write_bytes(b'fixture'); executable.chmod(0o700)
            discovered[provider] = {'id': provider, 'installed': True, 'executable': str(executable),
                                    'dataDirectory': str(self.root), 'desktopProfileReady': True}
        self.accounts.index['desktopExecutable'] = discovered['codex']['executable']
        self.manager.config.update(discovered=discovered, enabled=dict.fromkeys(discovered, True))
        self.running = True
        self.inspect = self.patch('inspect_client', side_effect=lambda _: {'running': self.running,
            'pids': [11, 12] if self.running else [], 'mainPids': [11] if self.running else [], 'runtimePids': [12] if self.running else [], 'unknown': False})
        self.inspect_batch = self.patch('inspect_clients', side_effect=lambda rows: {row['id']: self.inspect.side_effect(row) for row in rows})
        self.launch = self.patch('launch_client', side_effect=self.start)
        self.launch_dsh = self.patch('launch_deepseek', side_effect=self.start)
        self.stop = self.patch('stop_client', side_effect=self.finish)
        self.stop_dsh = self.patch('stop_deepseek', side_effect=lambda descriptor, adapter, **kwargs: self.stop(descriptor, **kwargs))
        self.manager._deepseek_endpoint = Mock(return_value={'port': 31313, 'pid': 12})
        self.manager.clients(refresh=True)

    def patch(self, name, **kwargs):
        patcher = patch('bridge.integrations.client_launch.'+name, create=True, **kwargs)
        value = patcher.start(); self.addCleanup(patcher.stop); return value

    def start(self, descriptor, **kwargs):
        self.running = True
        return {'running': True, 'launched': True, 'restartRequired': False}

    def finish(self, descriptor, **kwargs):
        self.running = False

    def test_codex_cached_runtime_reuses_scanned_gui_for_readiness_and_launch(self):
        self.accounts.index['desktopExecutable'] = ''
        self.accounts.runtime = self.root/'cache/hash/codex.exe'
        self.running = False
        with patch('bridge.desktop_app.DesktopApp.discover', return_value=''):
            rows = self.manager.toggle_client({'provider': 'codex', 'enabled': True})['clients']
        descriptor = self.launch.call_args.args[0]
        self.assertEqual(descriptor['executable'], self.manager.config['discovered']['codex']['executable'])
        self.assertTrue(descriptor['installed'])
        self.assertEqual(descriptor['dataDirectory'], str(self.accounts.home))
        codex = next(row for row in rows if row['id'] == 'codex')
        self.assertTrue(codex['configured'])
        self.assertTrue(codex['connected'])

    def test_explicit_codex_gui_keeps_precedence_even_when_missing(self):
        selected = self.root/'selected.exe'
        selected.write_bytes(b'fixture')
        self.accounts.index['desktopExecutable'] = str(selected)
        with patch('bridge.desktop_app.DesktopApp.discover') as discover:
            self.assertEqual(self.manager._descriptor('codex')['executable'], str(selected))
            selected.unlink()
            descriptor = self.manager._descriptor('codex')
            self.assertEqual(descriptor['executable'], str(selected))
            self.assertFalse(descriptor['installed'])
            discover.assert_not_called()

    def test_missing_scanned_codex_gui_does_not_report_configured(self):
        self.accounts.index['desktopExecutable'] = ''
        self.manager.config['discovered']['codex']['executable'] = str(self.root/'missing.exe')
        with patch('bridge.desktop_app.DesktopApp.discover', return_value=''):
            self.assertFalse(self.manager._descriptor('codex')['installed'])
            codex = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'codex')
        self.assertFalse(codex['configured'])
        self.assertFalse(codex['connected'])

    def toggle(self, provider, enabled):
        return self.manager.toggle_client({'provider': provider, 'enabled': enabled})

    def test_disable_access_keeps_desktop_and_terminal_tasks_running(self):
        process = Mock()
        self.manager.workspace_roots[('claude', 'one')] = str(self.root)
        import uuid
        thread = str(uuid.uuid5(uuid.NAMESPACE_URL, 'claude:one'))
        self.manager.terminals = SimpleNamespace(lock=threading.RLock(),
            sessions={('local', thread, 'term'): process}, jobs={}, close=Mock())
        self.claude.status.return_value = {'connected': False}
        self.dsh.call.side_effect = BridgeUnavailable('disconnected')
        with patch.object(self.manager, '_assert_idle') as idle, patch.object(self.manager, '_descriptor', side_effect=ValueError('missing app')):
            for provider in ('codex', 'claude', 'deepseek'):
                self.manager.toggle_client({'provider': provider, 'enabled': False, 'quitDesktop': False})
                self.assertFalse(self.manager.enabled(provider))
                with self.assertRaisesRegex(ValueError, '应用已关闭'):
                    self.manager.require_enabled(provider)
        idle.assert_not_called()
        self.accounts.assert_editable.assert_not_called()
        self.accounts.idle.assert_not_called()
        self.stop.assert_not_called()
        self.stop_dsh.assert_not_called()
        self.launch.assert_not_called()
        self.launch_dsh.assert_not_called()
        process.stop.assert_not_called()
        self.claude.cancel.assert_called_once_with()
        self.assertTrue(self.running)
        saved = json.loads(self.manager.config_path.read_text())
        self.assertEqual(saved['enabled'], dict.fromkeys(('codex', 'claude', 'deepseek'), False))

    def test_explicit_quit_keeps_busy_guard_and_enabled_preference(self):
        self.claude.call.side_effect = lambda *args: {'complete': True, 'sessions': [
            {'id': 'one', 'status': 'running', 'runtimeKnown': True, 'requests': []}]}
        with self.assertRaisesRegex(ValueError, '任务运行或等待'):
            self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.assertTrue(self.manager.enabled('claude'))
        self.stop.assert_not_called()
        self.claude.cancel.assert_not_called()

    def test_invalid_quit_choice_never_changes_state(self):
        for value in (None, 'false', 0, 1, []):
            with self.assertRaisesRegex(ValueError, '应用开关无效'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': value})
        with self.assertRaisesRegex(ValueError, '应用开关无效'):
            self.manager.toggle_client({'provider': 'claude', 'enabled': True, 'quitDesktop': False})
        self.assertTrue(self.manager.enabled('claude'))
        self.stop.assert_not_called()

    def test_windows_dsh_exit_uses_native_host_quit(self):
        with patch('bridge.integrations.manager.sys.platform', 'win32'):
            self.manager.toggle_client({'provider': 'deepseek', 'enabled': False, 'quitDesktop': True})
        self.stop_dsh.assert_called_once()
        self.assertIs(self.stop_dsh.call_args.args[1], self.dsh)
        self.assertFalse(self.manager.enabled('deepseek'))

    def test_windows_claude_exit_reports_native_limit_even_when_disconnected(self):
        self.claude.status.return_value = {'connected': False}
        with patch('bridge.integrations.manager.sys.platform', 'win32'), \
                patch.object(self.manager, '_assert_idle') as idle:
            with self.assertRaisesRegex(ValueError, 'Claude Desktop 不支持后台退出'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        idle.assert_not_called()
        self.claude.cancel.assert_not_called()
        self.stop.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))

    def test_offline_selection_never_launches_or_quits_clients(self):
        self.manager.gateway_running = False
        for provider in ('codex', 'claude', 'deepseek'):
            self.toggle(provider, False)
            self.assertFalse(self.manager.enabled(provider))
            self.toggle(provider, True)
            self.assertTrue(self.manager.enabled(provider))
        self.launch.assert_not_called()
        self.launch_dsh.assert_not_called()
        self.stop.assert_not_called()
        self.claude.connect.assert_not_called()

    def test_gateway_start_prepares_selected_claude_without_launching_or_native_input(self):
        self.manager.config['enabled'] = {'codex': False, 'claude': True, 'deepseek': False}
        with patch.object(self.manager, 'scan'):
            self.manager.start_enabled()
        self.launch.assert_not_called()
        self.launch_dsh.assert_not_called()
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_called_once_with()

    def test_one_start_failure_preserves_selection_and_does_not_block_other_apps(self):
        self.manager.config['enabled'] = {'codex': False, 'claude': True, 'deepseek': True}
        self.claude.reconnect.side_effect = ValueError('launch rejected')
        with patch.object(self.manager, 'scan'):
            self.manager.start_enabled()
        self.launch_dsh.assert_called_once()
        self.assertTrue(self.manager.enabled('claude'))
        self.assertEqual(self.manager.config['setup']['claude']['reason'], 'launch rejected')

    def test_offline_manager_never_starts_selected_apps(self):
        self.manager.gateway_running = False
        with patch.object(self.manager, 'scan') as scan:
            self.manager.start_enabled()
        scan.assert_not_called()
        self.launch.assert_not_called()
        self.launch_dsh.assert_not_called()

    def test_desktop_recovery_full_exit_and_restart_only_persist_after_success(self):
        recovery = Mock()
        self.manager.deepseek_recovery = recovery
        recovery.preview.return_value = {'token': 'private-preview', 'unknown': True}
        self.assertEqual(self.manager.control({'action': 'deepseek-recovery-preview', 'restart': False})['token'], 'private-preview')
        self.launch_dsh.assert_not_called()
        recovery.confirm.side_effect = ValueError('busy')
        with self.assertRaisesRegex(ValueError, 'busy'):
            self.manager.control({'action': 'deepseek-recovery-confirm', 'token': 'private-preview', 'confirmed': True})
        self.assertTrue(self.manager.enabled('deepseek'))
        recovery.confirm.side_effect = None
        recovery.confirm.return_value = False
        self.running = False
        self.manager.control({'action': 'deepseek-recovery-confirm', 'token': 'private-preview', 'confirmed': True})
        self.assertFalse(self.manager.enabled('deepseek'))
        self.launch_dsh.assert_not_called()
        recovery.confirm.return_value = True
        self.dsh.ensure_installed.return_value = {'updateRequired': True}
        with patch.object(self.manager, '_start_deepseek') as start:
            self.manager.control({'action': 'deepseek-recovery-confirm', 'token': 'private-preview', 'confirmed': True})
        self.dsh.update_existing.assert_called_once()
        start.assert_called_once()
        self.assertTrue(self.manager.enabled('deepseek'))

    def test_failed_recovery_launch_preserves_startup_preference(self):
        self.manager.config['enabled']['deepseek'] = False
        self.manager.deepseek_recovery = Mock()
        self.manager.deepseek_recovery.confirm.return_value = True
        with patch.object(self.manager, '_start_deepseek', side_effect=ValueError('launch failed')):
            with self.assertRaisesRegex(ValueError, 'launch failed'):
                self.manager.control({'action': 'deepseek-recovery-confirm', 'token': 'preview', 'confirmed': True})
        self.assertFalse(self.manager.enabled('deepseek'))

    def test_live_current_connector_clears_stale_failure_banner(self):
        self.manager.config.setdefault('setup', {})['deepseek'] = {'setupStatus': 'failed', 'reason': 'old failure'}
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'deepseek')
        self.assertEqual(row['setupStatus'], 'ready')
        self.assertNotEqual(row['reason'], 'old failure')
        self.assertTrue(row['mainRunning'])
        self.assertTrue(row['backgroundRunning'])
        self.assertEqual(row['backgroundCount'], 1)

    def test_native_multiple_hosts_show_recovery_without_claiming_disconnected(self):
        self.inspect.side_effect = lambda _: {'running': True, 'pids': [11, 12, 13], 'mainPids': [11], 'runtimePids': [12, 13], 'unknown': True}
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'deepseek')
        self.assertEqual(row['setupStatus'], 'recovery-required')
        self.assertTrue(row['connected'])
        self.assertEqual(row['backgroundCount'], 2)

    def test_account_switch_checks_idle_before_touching_credentials(self):
        store = Mock()
        self.manager._account_store = Mock(return_value=store)
        self.claude.call.side_effect = lambda action, *args: {'complete': True, 'sessions': [
            {'id': 'busy', 'status': 'active', 'runtimeKnown': True}]}
        with self.assertRaisesRegex(ValueError, '运行或等待'):
            self.manager.client_accounts('claude', {'operation': 'switch', 'id': 'other'})
        store.restore.assert_not_called()
        self.stop.assert_not_called()

    def test_account_switch_stops_then_restores_and_restarts(self):
        store = Mock()
        store.restore.return_value = 'transaction'
        store.public.return_value = {'accounts': [{'id': 'other'}], 'activeId': 'other'}
        self.manager._account_store = Mock(return_value=store)
        calls = []
        self.stop.side_effect = lambda *a, **kw: (calls.append('stop'), setattr(self, 'running', False))
        store.restore.side_effect = lambda identifier: calls.append('restore') or 'transaction'
        self.launch.side_effect = lambda *a: calls.append('launch') or {'running': True}
        result = self.manager.client_accounts('claude', {'operation': 'switch', 'id': 'other'})
        self.assertEqual(calls, ['stop', 'restore', 'launch'])
        self.assertTrue(result['canSwitch'])
        store.commit.assert_called_once_with('transaction')

    def test_failed_account_launch_restores_previous_profile(self):
        store = Mock()
        store.restore.return_value = 'transaction'
        store.public.return_value = {'activeIds': ['other'], 'accounts': []}
        self.manager._account_store = Mock(return_value=store)
        with patch.object(self.manager, '_launch_account_client', side_effect=[ValueError('launch failed'), None]) as launch:
            with self.assertRaisesRegex(ValueError, 'launch failed'):
                self.manager.client_accounts('claude', {'operation': 'switch', 'id': 'other'})
        store.rollback.assert_called_once_with('transaction')
        store.commit.assert_not_called()
        self.assertEqual(launch.call_count, 2)

    def test_account_changed_during_launch_rolls_back_before_commit(self):
        store = Mock()
        store.restore.return_value = 'transaction'
        store.public.side_effect = [
            {'activeIds': ['other'], 'accounts': []},
            {'activeIds': ['previous'], 'accounts': []},
        ]
        self.manager._account_store = Mock(return_value=store)
        with self.assertRaisesRegex(ValueError, '账号配置尚未生效'):
            self.manager.client_accounts('claude', {'operation': 'switch', 'id': 'other'})
        store.rollback.assert_called_once_with('transaction')
        store.commit.assert_not_called()
        self.assertEqual(self.stop.call_count, 2)
        self.assertEqual(self.launch.call_count, 2)

    def test_deepseek_import_reads_credentials_without_stopping_running_app(self):
        self.manager.gateway_running = False
        store = Mock()
        store.import_current.return_value = {'accounts': [], 'activeId': None}
        self.manager._account_store = Mock(return_value=store)
        self.dsh.call.side_effect = BridgeUnavailable('no live connection')
        self.manager.client_accounts('deepseek', {'operation': 'import-current', 'name': 'My API', 'kind': 'api'})
        store.import_current.assert_called_once_with(name='My API', kind='api')
        self.stop.assert_not_called()
        self.launch_dsh.assert_not_called()

    def test_importing_stopped_client_offline_does_not_launch_it(self):
        self.manager.gateway_running = False
        self.running = False
        store = Mock()
        store.import_current.return_value = {'accounts': [], 'activeId': None}
        self.manager._account_store = Mock(return_value=store)
        self.manager.client_accounts('claude', {'operation': 'import-current', 'name': 'My account'})
        store.import_current.assert_called_once_with(name='My account')
        self.launch.assert_not_called()

    def test_offline_running_client_cannot_be_snapshotted_without_live_idle_evidence(self):
        self.manager.gateway_running = False
        store = Mock()
        self.manager._account_store = Mock(return_value=store)
        with self.assertRaisesRegex(ValueError, '先退出客户端'):
            self.manager.client_accounts('claude', {'operation': 'import-current'})
        store.import_current.assert_not_called()
        self.stop.assert_not_called()

    def test_codex_toggle_stops_native_app_only_after_global_idle_check(self):
        self.toggle('codex', False)
        self.bridge.assert_desktop_idle.assert_called_once()
        self.accounts.assert_editable.assert_called_once()
        self.stop.assert_called_once()
        self.assertFalse(self.manager.enabled('codex'))

    def test_claude_toggle_cancels_autoconnect_and_stops_actual_app(self):
        self.toggle('claude', False)
        self.assertEqual(self.claude.cancel.call_count, 2)
        self.claude.cancel.assert_any_call(persist=False)
        self.claude.cancel.assert_any_call()
        self.stop.assert_called_once()
        self.assertFalse(self.manager.enabled('claude'))

    def test_stopped_verified_claude_enable_does_not_open_its_window(self):
        self.toggle('claude', False)
        self.claude.status.return_value = {'connected': False}
        self.assertTrue(next(r for r in self.manager.clients(refresh=True)['clients'] if r['id'] == 'claude')['configured'])
        self.toggle('claude', True)
        self.launch.assert_not_called()
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_called_once_with()

    def test_explicit_claude_connect_button_may_launch_and_initialize(self):
        self.running = False
        self.manager.control({'action': 'connect-claude'})
        self.launch.assert_called_once()
        self.claude.connect.assert_called_once_with()
        self.claude.reconnect.assert_not_called()

    def test_pending_interaction_blocks_stop_and_preserves_preference(self):
        self.claude.call.side_effect = lambda action, *args: ({'connected': True, 'complete': True,
            'sessions': [{'id': 'one', 'status': 'idle', 'runtimeKnown': True, 'requests': [{'id': 'question'}]}]} if action == 'list' else {'models': [{'id': 'model'}]})
        with self.assertRaisesRegex(ValueError, '任务运行或等待'):
            self.toggle('claude', False)
        self.stop.assert_not_called(); self.claude.cancel.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))

    def test_missing_surface_or_missing_runtime_state_cannot_prove_idle(self):
        for payload in ({'connected': True, 'complete': False, 'sessions': []},
                        {'connected': True, 'complete': True, 'sessions': [{'id': 'one', 'status': 'idle', 'runtimeKnown': False}]}):
            self.claude.call.side_effect = lambda *args: payload
            with self.assertRaisesRegex(ValueError, '无法确认'):
                self.toggle('claude', False)
        self.stop.assert_not_called()

    def test_unavailable_adapter_never_means_idle(self):
        self.dsh.call.side_effect = BridgeUnavailable('disconnected')
        with self.assertRaisesRegex(ValueError, '无法确认'):
            self.toggle('deepseek', False)
        self.stop.assert_not_called(); self.assertTrue(self.manager.enabled('deepseek'))

    def test_failed_native_stop_preserves_enabled_preference(self):
        self.stop.side_effect = ValueError('native refused exit')
        with self.assertRaisesRegex(ValueError, 'native refused'):
            self.toggle('deepseek', False)
        self.assertTrue(self.manager.enabled('deepseek'))

    def test_restart_claude_uses_same_busy_guard(self):
        self.claude.call.side_effect = lambda action, *args: ({'connected': True, 'complete': True,
            'sessions': [{'id': 'one', 'status': 'active', 'runtimeKnown': True, 'requests': []}]} if action == 'list' else {'models': [{'id': 'model'}]})
        with self.assertRaisesRegex(ValueError, '任务运行或等待'):
            self.manager.control({'action': 'restart-claude'})
        self.stop.assert_not_called(); self.claude.connect.assert_not_called()

    def test_deepseek_stopped_upgrade_launches_without_requiring_another_restart(self):
        self.running = False; self.dsh.reused = True
        self.dsh.ensure_installed.return_value = {'installed': True, 'updateRequired': True}
        self.manager.config['setup'] = {'deepseek': {'setupStatus': 'restart-required', 'requiredBridgeRevision': 3}}
        self.manager.connect_deepseek()
        self.dsh.update_existing.assert_called_once()
        self.launch_dsh.assert_called_once()
        self.stop.assert_not_called()

    def test_extra_harness_host_without_authoritative_state_blocks_exit(self):
        self.inspect.return_value = None
        self.inspect.side_effect = lambda _: {'running': True, 'pids': [11, 12, 13], 'mainPids': [11],
            'runtimePids': [12, 13], 'unknown': False}
        with self.assertRaisesRegex(ValueError, '无法确认'):
            self.toggle('deepseek', False)
        self.stop.assert_not_called()

    def test_busy_codex_not_currently_followed_blocks_exit(self):
        self.bridge.assert_desktop_idle.side_effect = ValueError('有任务运行或等待确认')
        with self.assertRaises(ValueError): self.toggle('codex', False)
        self.stop.assert_not_called(); self.assertTrue(self.manager.enabled('codex'))


    def test_failed_claude_stop_keeps_enabled_and_autoconnect_preference(self):
        self.claude.discovery = {'autoConnect': True}
        def cancel(persist=True):
            self.claude.status.return_value = {'connected': False}
            if persist:
                self.claude.discovery['autoConnect'] = False
        def reconnect():
            self.claude.status.return_value = {'connected': True}
        self.claude.cancel.side_effect = cancel
        self.claude.reconnect.side_effect = reconnect
        self.stop.side_effect = ValueError('native refused exit')
        with self.assertRaisesRegex(ValueError, 'native refused'):
            self.toggle('claude', False)
        self.claude.cancel.assert_called_once_with(persist=False)
        self.claude.reconnect.assert_called_once_with()
        self.claude.connect.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))
        self.assertTrue(self.claude.discovery['autoConnect'])
        self.assertTrue(self.claude.status()['connected'])

    def test_failed_claude_connection_restore_cannot_replace_native_stop_error(self):
        self.claude.discovery = {'autoConnect': True}
        original = ValueError('native refused exit')
        self.stop.side_effect = original
        self.claude.reconnect.side_effect = RuntimeError('connection restore failed')
        with self.assertRaises(ValueError) as raised:
            self.toggle('claude', False)
        self.assertIs(raised.exception, original)
        self.claude.cancel.assert_called_once_with(persist=False)
        self.claude.reconnect.assert_called_once_with()
        self.claude.connect.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))
        self.assertTrue(self.claude.discovery['autoConnect'])

    def test_failed_background_preparation_keeps_disabled(self):
        self.manager.config['enabled']['claude'] = False
        self.running = False
        self.claude.reconnect.side_effect = ValueError('mailbox unavailable')
        with self.assertRaisesRegex(ValueError, 'mailbox unavailable'):
            self.toggle('claude', True)
        self.assertFalse(self.manager.enabled('claude'))

    def test_live_auth_loss_invalidates_remembered_configuration(self):
        self.dsh.call.side_effect = lambda *args: {'connected': True, 'configured': False, 'bridgeRevision': 3}
        result = next(r for r in self.manager.clients(refresh=True)['clients'] if r['id'] == 'deepseek')
        self.assertFalse(result['configured'])
        self.assertNotIn('deepseek', self.manager.config['verified'])

    def test_codex_after_exit_stays_configured_but_is_not_connected(self):
        self.toggle('codex', False)
        row = next(r for r in self.manager.clients(refresh=True)['clients'] if r['id'] == 'codex')
        self.assertTrue(row['configured']); self.assertFalse(row['connected'])

    def test_pending_gateway_send_cannot_escape_the_shutdown_lock(self):
        import uuid
        entered, finish = threading.Event(), threading.Event()
        errors = []
        def stop(*args, **kwargs):
            entered.set(); finish.wait(2); self.running = False
        self.stop.side_effect = stop
        closing = threading.Thread(target=lambda: self.toggle('deepseek', False))
        closing.start(); self.assertTrue(entered.wait(2))
        def send():
            try: self.manager.call('deepseek', 'send', 'one', {'id': str(uuid.uuid4()), 'text': 'fixture only'})
            except ValueError as exc: errors.append(str(exc))
        sending = threading.Thread(target=send); sending.start()
        self.assertTrue(sending.is_alive())
        finish.set(); closing.join(2); sending.join(2)
        self.assertEqual(errors, ['此应用已关闭，请在应用管理中开启'])
        self.assertFalse(any(call.args[0] == 'send' for call in self.dsh.call.call_args_list))


    def test_codex_gateway_operation_rechecks_enabled_inside_account_gate(self):
        from bridge.accounts import operation
        self.accounts.check_ready = Mock()
        submitted = Mock()
        @operation
        def mutate(bridge): submitted()
        self.manager.config['enabled']['codex'] = False
        with self.assertRaisesRegex(ValueError, '应用已关闭'): mutate(self.bridge)
        submitted.assert_not_called()
        self.manager.config['enabled']['codex'] = True
        mutate(self.bridge); submitted.assert_called_once()

    def test_close_does_not_remove_a_newer_managers_codex_guard(self):
        replacement = lambda: None
        self.accounts.require_client_enabled = replacement
        self.manager.close()
        self.assertIs(self.accounts.require_client_enabled, replacement)


    def test_old_harness_idle_without_runtime_evidence_cannot_be_restarted(self):
        self.dsh.reused = True
        self.dsh.call.side_effect = lambda action, *args: ({'connected': True, 'configured': True,
            'bridgeRevision': 2, 'updateRequired': True} if action == 'status' else {'sessions': [{'id': 'old', 'status': 'idle'}]})
        with self.assertRaisesRegex(ValueError, '退出 Harness'):
            self.manager.connect_deepseek(restart=True)
        self.stop.assert_not_called(); self.dsh.update_existing.assert_not_called(); self.launch_dsh.assert_not_called()

    def test_upgrade_changes_source_only_after_verified_native_exit(self):
        self.dsh.reused = True
        self.manager.config['setup'] = {'deepseek': {'setupStatus': 'restart-required', 'requiredBridgeRevision': 3}}
        order = []
        self.stop.side_effect = lambda *args, **kwargs: (order.append('stop'), setattr(self, 'running', False))
        self.dsh.update_existing.side_effect = lambda: order.append('update')
        self.launch_dsh.side_effect = lambda *args, **kwargs: (order.append('launch') or self.start(*args, **kwargs))
        self.manager.connect_deepseek(restart=True)
        self.assertEqual(order, ['stop', 'update', 'launch'])


if __name__ == '__main__': unittest.main()
