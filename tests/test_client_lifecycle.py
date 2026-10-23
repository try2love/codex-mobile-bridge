"""Native app lifecycle uses isolated process and desktop-state fixtures only."""
import tempfile
import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.manager import DesktopSessions, DesktopWorkspace
from bridge.clients.errors import BridgeUnavailable

ROOT = Path(__file__).resolve().parents[1]


class ClientLifecycleTests(unittest.TestCase):
    def test_wsl_is_an_independent_managed_app_and_never_uses_native_desktop_lifecycle(self):
        from bridge.features.endpoints.registry import Entries
        from bridge.platforms.windows import wsl
        entries = self.bridge.entries = Entries(self.root)
        self.addCleanup(entries.close)
        self.manager.config.setdefault('enabled', {})['codex'] = False
        info = {'user': 'fixture', 'homes': [{'home': '/home/fixture/.codex'}]}
        with patch.object(wsl, 'running_distributions', return_value=['Ubuntu']), patch.object(wsl, 'probe', return_value=info), \
                patch('bridge.clients.lifecycle.inspect_clients', return_value={}) as inspect, \
                patch('bridge.clients.lifecycle.launch_client') as launch, patch('bridge.clients.lifecycle.stop_client') as stop:
            result = self.manager.scan_wsl()
            row = next(row for row in result['clients'] if row.get('kind') == 'wsl-cli')
            self.assertFalse(row['enabled'])
            enabled = self.manager.control({'action': 'toggle-client', 'provider': row['id'], 'enabled': True})
            self.assertTrue(next(item for item in enabled['clients'] if item['id'] == row['id'])['enabled'])
            self.manager.require_enabled(row['id'])
            with self.assertRaises(ValueError):
                self.manager.require_enabled('codex')
            with self.assertRaisesRegex(ValueError, '原 WSL 终端'):
                self.manager.toggle_client({'provider': row['id'], 'enabled': False, 'quitDesktop': True})
            self.assertTrue(entries.enabled(row['id']))
            self.manager.toggle_client({'provider': row['id'], 'enabled': False, 'quitDesktop': False})
            self.assertFalse(entries.enabled(row['id']))
            self.assertTrue(all(not descriptor['id'].startswith('wsl:') for args in inspect.call_args_list for descriptor in args.args[0]))
            launch.assert_not_called()
            stop.assert_not_called()

    def setUp(self):
        platform = patch('bridge.clients.manager.sys', SimpleNamespace(platform='darwin'))
        platform.start(); self.addCleanup(platform.stop)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.claude, self.dsh = Mock(), Mock()
        self.claude.status.return_value = {'connected': True}
        self.claude.desktop_listing.return_value = {'connected': True, 'complete': True, 'coverage': {'code': True, 'cowork': True}, 'sessions': [
            {'id': 'one', 'status': 'idle', 'runtimeKnown': True, 'requests': []}]}
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
            patcher = patch('bridge.clients.manager.'+name, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.manager = DesktopSessions(self.root, self.bridge, gateway_running=True)
        self.addCleanup(self.manager.close)
        # Lifecycle store mocks do not model credential files or their locks.
        self.manager._account_model_identity = Mock(return_value='fixture-account')
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
        patcher = patch('bridge.clients.lifecycle.'+name, create=True, **kwargs)
        value = patcher.start(); self.addCleanup(patcher.stop); return value

    def start(self, descriptor, **kwargs):
        self.running = True
        return {'running': True, 'launched': True, 'restartRequired': False}

    def finish(self, descriptor, **kwargs):
        self.running = False

    def test_client_status_returns_pending_without_waiting_for_startup_lock(self):
        manager = self.manager
        previous = manager.client_snapshot
        manager.client_cache = None
        entered, release = threading.Event(), threading.Event()
        def hold_startup():
            with manager.client_lock:
                entered.set()
                release.wait(3)
        holder = threading.Thread(target=hold_startup)
        holder.start(); self.assertTrue(entered.wait(1))
        with patch.object(manager, 'clients', wraps=manager.clients) as refresh:
            try:
                started = time.monotonic()
                first = manager.clients_status()
                self.assertLess(time.monotonic() - started, 0.2)
                self.assertTrue(first['pending'])
                self.assertEqual(first['clients'], previous['clients'])
                self.assertTrue(manager.client_refreshing)
                manager.client_snapshot = None
                cold = manager.clients_status()
                self.assertEqual(cold, {'clients': [], 'pending': True})
                self.assertTrue(manager.client_refreshing)
            finally:
                release.set(); holder.join(2)
            deadline = time.monotonic() + 3
            while manager.client_refreshing and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(refresh.call_count, 1)
            self.assertTrue(refresh.call_args.kwargs['refresh'])
        ready = manager.clients_status()
        self.assertFalse(ready['pending'])
        self.assertEqual(len(ready['clients']), 3)

    def test_client_status_failed_refresh_is_visible_and_can_retry(self):
        manager = self.manager
        manager.client_cache = None
        manager.client_snapshot = None
        original = manager.clients
        with patch.object(manager, 'clients', side_effect=RuntimeError('fixture failure')):
            self.assertTrue(manager.clients_status()['pending'])
            deadline = time.monotonic() + 2
            while manager.client_refreshing and time.monotonic() < deadline:
                time.sleep(0.01)
            failed = manager.clients_status()
            self.assertFalse(failed['pending'])
            self.assertIn('refreshError', failed)
        manager.client_refresh_retry_at = 0
        with patch.object(manager, 'clients', wraps=original) as refresh:
            self.assertTrue(manager.clients_status()['pending'])
            deadline = time.monotonic() + 3
            while manager.client_refreshing and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(refresh.call_count, 1)
        self.assertFalse(manager.clients_status()['pending'])
        manager.closed.set()
        with self.assertRaises(BridgeUnavailable):
            manager.clients_status()

    def test_client_status_does_not_publish_a_refresh_after_close(self):
        manager = self.manager
        manager.client_cache = None
        manager.client_snapshot = None
        entered, release = threading.Event(), threading.Event()
        original = manager.clients
        def delayed_refresh(refresh=False):
            entered.set()
            release.wait(2)
            return original(refresh=refresh)
        with patch.object(manager, 'clients', side_effect=delayed_refresh):
            self.assertTrue(manager.clients_status()['pending'])
            self.assertTrue(entered.wait(1))
            manager.closed.set()
            release.set()
            deadline = time.monotonic() + 3
            while manager.client_refreshing and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertIsNone(manager.client_snapshot)
        with self.assertRaises(BridgeUnavailable):
            manager.clients_status()

    def test_client_status_cache_never_decides_enable_write(self):
        manager = self.manager
        manager.gateway_running = False
        manager.client_cache = (time.monotonic(), {'clients': [{'id': 'claude', 'selectable': False}]})
        with patch.object(manager, 'clients', wraps=manager.clients) as read:
            value = manager.toggle_client({'provider': 'claude', 'enabled': True})
        self.assertTrue(any(call.kwargs.get('refresh') is True for call in read.call_args_list))
        self.assertTrue(next(row for row in value['clients'] if row['id'] == 'claude')['enabled'])

    def test_codex_cached_runtime_reuses_scanned_gui_for_readiness_and_launch(self):
        self.accounts.index['desktopExecutable'] = ''
        self.accounts.runtime = self.root/'cache/hash/codex.exe'
        self.running = False
        with patch('bridge.clients.desktop_app.DesktopApp.discover', return_value=''):
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
        with patch('bridge.clients.desktop_app.DesktopApp.discover') as discover:
            self.assertEqual(self.manager._descriptor('codex')['executable'], str(selected))
            selected.unlink()
            descriptor = self.manager._descriptor('codex')
            self.assertEqual(descriptor['executable'], str(selected))
            self.assertFalse(descriptor['installed'])
            discover.assert_not_called()

    def test_missing_scanned_codex_gui_does_not_report_configured(self):
        self.accounts.index['desktopExecutable'] = ''
        self.manager.config['discovered']['codex']['executable'] = str(self.root/'missing.exe')
        with patch('bridge.clients.desktop_app.DesktopApp.discover', return_value=''):
            self.assertFalse(self.manager._descriptor('codex')['installed'])
            codex = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'codex')
        self.assertFalse(codex['configured'])
        self.assertFalse(codex['connected'])

    def test_unconfigured_codex_switch_matches_default_enabled_preference(self):
        self.manager.config['enabled'].pop('codex')
        self.accounts.info.current = lambda: {'status': 'signedOut'}
        codex = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'codex')
        self.assertFalse(codex['configured'])
        self.assertEqual(codex['enabled'], self.manager.enabled('codex'))

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

    def test_all_disabled_preferences_survive_restart_without_launching_a_client(self):
        for provider in ('codex', 'claude', 'deepseek'):
            self.manager.toggle_client({'provider': provider, 'enabled': False, 'quitDesktop': False})
        reopened = DesktopSessions(self.root, self.bridge, gateway_running=True)
        try:
            with patch.object(reopened, 'scan') as scan, patch.object(reopened, 'toggle_client') as toggle:
                reopened.start_enabled()
            scan.assert_called_once_with()
            toggle.assert_not_called()
            self.assertEqual([row['enabled'] for row in reopened.clients(refresh=True)['clients']], [False] * 3)
            self.assertTrue(self.running)
        finally:
            reopened.close()

    def test_explicit_quit_keeps_busy_guard_and_enabled_preference(self):
        self.claude.desktop_listing.return_value = {'complete': True, 'sessions': [
            {'id': 'one', 'status': 'running', 'runtimeKnown': True, 'requests': []}]}
        with self.assertRaisesRegex(ValueError, '任务运行或等待'):
            self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.assertTrue(self.manager.enabled('claude'))
        self.stop.assert_not_called()
        self.claude.cancel.assert_not_called()

    def test_disable_rejects_workspaces_resolved_before_request_body_arrives(self):
        for provider in ('claude', 'deepseek'):
            with self.subTest(provider=provider):
                workspace = DesktopWorkspace(self.manager, provider, 'one', str(self.root))
                self.manager.toggle_client({'provider': provider, 'enabled': False, 'quitDesktop': False})
                with patch('bridge.features.workspace.workspace.operate') as operate, patch('bridge.app.service.Bridge.terminal') as terminal:
                    for action in ('upload', 'git-action'):
                        with self.assertRaisesRegex(ValueError, '应用已关闭'):
                            workspace.workspace(workspace.identifier, action, {})
                    for action in ('start', 'open', 'input', 'resize', 'close', 'stop'):
                        with self.assertRaisesRegex(ValueError, '应用已关闭'):
                            workspace.terminal(workspace.identifier, 'fixture', action, {})
                    operate.assert_not_called()
                    terminal.assert_not_called()

    def test_disable_waits_for_accepted_workspace_mutation(self):
        workspace = DesktopWorkspace(self.manager, 'claude', 'one', str(self.root))
        entered, release, toggling, disabled = (threading.Event() for _ in range(4))
        failures = []

        def mutate():
            entered.set()
            if not release.wait(3):
                raise TimeoutError('fixture mutation was not released')
            return {'saved': True}

        def run_write():
            try:
                workspace.workspace(workspace.identifier, 'upload', {})
            except Exception as error:
                failures.append(error)

        def disable():
            toggling.set()
            try:
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': False})
            except Exception as error:
                failures.append(error)
            finally:
                disabled.set()

        with patch('bridge.features.workspace.workspace.operate', side_effect=lambda *_: mutate()):
            writer = threading.Thread(target=run_write)
            toggler = threading.Thread(target=disable)
            writer.start()
            try:
                self.assertTrue(entered.wait(2))
                toggler.start()
                self.assertTrue(toggling.wait(2))
                self.assertFalse(disabled.wait(.15))
            finally:
                release.set()
                writer.join(3)
                if toggler.ident is not None:
                    toggler.join(3)
        self.assertFalse(writer.is_alive())
        self.assertFalse(toggler.is_alive())
        self.assertEqual(failures, [])
        self.assertFalse(self.manager.enabled('claude'))

    def test_invalid_quit_choice_never_changes_state(self):
        for value in (None, 'false', 0, 1, []):
            with self.assertRaisesRegex(ValueError, '应用开关无效'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': value})
        with self.assertRaisesRegex(ValueError, '应用开关无效'):
            self.manager.toggle_client({'provider': 'claude', 'enabled': True, 'quitDesktop': False})
        self.assertTrue(self.manager.enabled('claude'))
        self.stop.assert_not_called()

    def test_explicit_force_bypasses_tasks_but_not_native_identity_checks(self):
        native = Mock(side_effect=self.finish)
        self.claude.status.return_value = {'connected': False}
        self.dsh.call.side_effect = BridgeUnavailable('offline')
        with patch('bridge.clients.lifecycle.force_stop_client', native), \
                patch('bridge.clients.manager.sys.platform', 'win32'), \
                patch.object(self.manager, 'clients', return_value={'clients': []}), \
                patch.object(self.manager, '_assert_idle', side_effect=AssertionError('force is an explicit task interruption')) as idle:
            for provider in ('codex', 'claude', 'deepseek'):
                self.running = True
                self.manager.toggle_client({'provider': provider, 'enabled': False, 'quitDesktop': True, 'forceDesktop': True, 'forceConfirmed': True})
                self.assertFalse(self.manager.enabled(provider))
                self.assertEqual(native.call_args.args[0]['id'], provider)
                self.assertIn('state', native.call_args.kwargs)
        self.assertEqual(native.call_count, 3)
        self.assertTrue(self.manager.gateway_running)
        self.assertFalse(self.manager.closed.is_set())
        idle.assert_not_called(); self.stop.assert_not_called(); self.stop_dsh.assert_not_called()
        self.claude.cancel.assert_any_call(persist=False)
        self.claude.cancel.assert_any_call()

    def test_force_failure_keeps_enabled_state_and_reports_original_error(self):
        native = Mock(side_effect=ValueError('fixture identity changed'))
        with patch('bridge.clients.lifecycle.force_stop_client', native), \
                patch('bridge.clients.manager.sys.platform', 'win32'), \
                patch('bridge.clients.manager.private_json') as save:
            with self.assertRaisesRegex(ValueError, 'fixture identity changed'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True, 'forceDesktop': True, 'forceConfirmed': True})
        self.assertTrue(self.manager.enabled('claude'))
        save.assert_not_called(); self.stop.assert_not_called()
        self.claude.reconnect.assert_called_once_with()

    def test_force_requires_explicit_confirmed_disabled_desktop_choice(self):
        base = {'provider': 'claude', 'enabled': False, 'quitDesktop': True, 'forceDesktop': True, 'forceConfirmed': True}
        cases = [{**base, 'forceDesktop': value} for value in (None, 'true', 1, 0, [])]
        cases += [{**base, 'enabled': True}, {**base, 'quitDesktop': False}, {**base, 'forceConfirmed': False}, {k:v for k,v in base.items() if k != 'forceConfirmed'},
                  {'provider': 'claude', 'enabled': False, 'forceDesktop': True, 'forceConfirmed': True}]
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            for value in cases:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.manager.toggle_client(value)
            self.manager.gateway_running = False
            with self.assertRaisesRegex(ValueError, '强制结束请求无效'):
                self.manager.toggle_client(base)
            self.manager.gateway_running = True
        with patch('bridge.clients.manager.sys.platform', 'unsupported'), self.assertRaisesRegex(ValueError, '强制结束请求无效'):
            self.manager.toggle_client(base)
        self.assertTrue(self.manager.enabled('claude'))
        self.stop.assert_not_called()

    def test_normal_quit_never_uses_force_path(self):
        native = Mock(side_effect=AssertionError('normal quit must not force'))
        with patch('bridge.clients.lifecycle.force_stop_client', native), \
                patch('bridge.clients.manager.sys.platform', 'win32'), \
                patch.object(self.manager, 'clients', return_value={'clients': []}):
            self.manager.toggle_client({'provider': 'codex', 'enabled': False, 'quitDesktop': True})
        native.assert_not_called(); self.stop.assert_called_once()

    def test_force_choice_only_advertised_for_supported_running_gateway(self):
        self.assertTrue(all(row.get('canForceQuit') for row in self.manager.clients(refresh=True)['clients']))
        with patch('bridge.clients.manager.sys.platform', 'win32'), \
                patch('bridge.clients.manager.windows_session.status', return_value={'state': 'locked', 'interactive': False}):
            self.assertTrue(all(row['canForceQuit'] for row in self.manager.clients(refresh=True)['clients']))
            self.manager.gateway_running = False
            self.assertTrue(all(not row['canForceQuit'] for row in self.manager.clients(refresh=True)['clients']))

    def test_windows_dsh_exit_uses_native_host_quit(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            self.manager.toggle_client({'provider': 'deepseek', 'enabled': False, 'quitDesktop': True})
        self.stop_dsh.assert_called_once()
        self.assertIs(self.stop_dsh.call_args.args[1], self.dsh)
        self.assertFalse(self.manager.enabled('deepseek'))

    def test_windows_claude_explicit_quit_uses_native_confirmation_when_disconnected(self):
        self.claude.status.return_value = {'connected': False}
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.stop.assert_called_once()
        self.claude.cancel.assert_any_call(persist=False)
        self.assertFalse(self.manager.enabled('claude'))

    def test_windows_claude_implicit_quit_and_account_stop_still_need_idle_evidence(self):
        self.claude.status.return_value = {'connected': False}
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            with self.assertRaisesRegex(ValueError, '无法确认'):
                self.toggle('claude', False)
            with self.assertRaisesRegex(ValueError, '无法确认'):
                self.manager._stop_client('claude', self.manager._descriptor('claude'), self.inspect(None))
        self.stop.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))

    def test_windows_claude_incomplete_list_cannot_hide_known_busy_tasks(self):
        self.claude.call.side_effect = lambda *args, **kwargs: {'complete': False, 'sessions': [
            {'id': 'one', 'status': 'running', 'runtimeKnown': True}]}
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            with self.assertRaisesRegex(ValueError, '任务运行或等待'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.stop.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))

    def test_windows_claude_exit_probe_timeout_uses_native_confirmation_without_readiness_scan(self):
        def call(action, *args, **kwargs):
            self.assertEqual(action, 'list')
            self.assertEqual(kwargs, {'timeout': 8})
            raise TimeoutError('stale connector')
        self.claude.call.side_effect = call
        with patch('bridge.clients.manager.sys.platform', 'win32'), \
                patch.object(self.manager, 'clients', return_value={'clients': []}) as clients:
            self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.stop.assert_called_once()
        clients.assert_called_once_with()
        self.assertFalse(self.manager.enabled('claude'))

    def test_windows_claude_unknown_processes_block_native_confirmation(self):
        self.claude.status.return_value = {'connected': False}
        state = {**self.inspect(None), 'unknown': True}
        self.inspect.side_effect = lambda _: state
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            with self.assertRaisesRegex(ValueError, '无法确认'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.stop.assert_not_called()

    def test_windows_claude_pending_native_confirmation_preserves_enabled(self):
        self.claude.status.return_value = {'connected': False}
        self.stop.side_effect = ValueError('请在电脑端处理 Claude 的任务或保存提示后重试')
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            with self.assertRaisesRegex(ValueError, '任务或保存提示'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.assertTrue(self.manager.enabled('claude'))
        self.claude.reconnect.assert_called_once_with()
        self.claude.connect.assert_not_called()

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

    def test_gateway_start_requests_selected_claude_background_launch(self):
        self.manager.config['enabled'] = {'codex': False, 'claude': True, 'deepseek': False}
        with patch.object(self.manager, 'scan'), patch('bridge.clients.manager.sys.platform', 'win32'):
            self.manager.start_enabled()
        self.launch.assert_not_called()
        self.launch_dsh.assert_not_called()
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_called_once_with(launch=True)

    def test_one_start_failure_preserves_selection_and_does_not_block_other_apps(self):
        self.manager.config['enabled'] = {'codex': False, 'claude': True, 'deepseek': True}
        self.claude.reconnect.side_effect = ValueError('launch rejected')
        with patch.object(self.manager, 'scan'), patch('bridge.clients.manager.sys.platform', 'win32'):
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
        self.claude.desktop_listing.return_value = {'complete': True, 'sessions': [
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
        self.assertEqual(result['modelCatalogIdentity'], 'fixture-account')
        store.commit.assert_called_once_with('transaction')

    def test_optional_claude_cli_sync_reports_partial_failure_after_desktop_verified(self):
        store = Mock()
        store.restore.return_value = 'transaction'
        store.public.return_value = {'accounts': [{'id': 'other', 'kind': 'api', 'saved': True}],
                                     'activeId': 'other'}
        self.manager._account_store = Mock(return_value=store)
        self.bridge.cli_access = Mock()
        self.bridge.cli_access.change.side_effect = ValueError('CLI profile rejected')
        with patch('bridge.features.accounts.cli_sync.LocalCliSync.preflight',
                   return_value={'sharedWithDesktop': False}):
            result = self.manager.client_accounts('claude', {'operation': 'switch', 'id': 'other',
                                                              'syncLocalCli': True})
        self.assertEqual(result['activeId'], 'other')
        self.assertEqual(result['cliSync']['status'], 'failed')
        self.assertEqual(result['cliSync']['desktopStatus'], 'verified')
        store.rollback.assert_not_called()
        store.commit.assert_called_once_with('transaction')

    def test_optional_claude_cli_sync_reports_both_targets_verified(self):
        store = Mock()
        store.restore.return_value = 'transaction'
        store.public.return_value = {'accounts': [{'id': 'other', 'kind': 'api', 'saved': True}],
                                     'activeId': 'other'}
        self.manager._account_store = Mock(return_value=store)
        self.bridge.cli_access = Mock()
        self.bridge.cli_access.change.side_effect = [
            {'savedId': 'cli-profile'}, {'claude': {'activeId': 'cli-profile'}}]
        with patch('bridge.features.accounts.cli_sync.LocalCliSync.preflight',
                   return_value={'sharedWithDesktop': False}):
            result = self.manager.client_accounts('claude', {'operation': 'switch', 'id': 'other',
                                                              'syncLocalCli': True})
        self.assertEqual(result['activeId'], 'other')
        self.assertEqual(result['cliSync']['status'], 'verified')
        self.assertEqual(self.bridge.cli_access.change.call_args_list[1].args[0]['id'], 'cli-profile')

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

    def test_saved_account_crud_does_not_stop_or_reconfigure_native_client(self):
        store = Mock()
        store.save_api.return_value = store.rename.return_value = store.remove.return_value = {'accounts': []}
        store.edit.return_value = {'id': 'saved', 'name': 'Saved', 'kind': 'api', 'api': {'hasKey': True}}
        self.manager._account_store = Mock(return_value=store)
        for provider in ('claude', 'deepseek'):
            draft = {'operation': 'save-api', 'name': 'Saved', 'apiKey': 'private-key'}
            self.manager.client_accounts(provider, draft)
            store.save_api.assert_called_with(draft)
            self.assertTrue(self.manager.client_accounts(provider, {'operation': 'edit', 'id': 'saved'})['api']['hasKey'])
            self.manager.client_accounts(provider, {'operation': 'rename', 'id': 'saved', 'name': 'Renamed'})
            store.rename.assert_called_with('saved', 'Renamed')
            self.manager.client_accounts(provider, {'operation': 'remove', 'id': 'saved'})
            store.remove.assert_called_with('saved')
        self.stop.assert_not_called()
        self.launch.assert_not_called()
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
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            self.toggle('claude', True)
        self.launch.assert_not_called()
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_called_once_with(launch=True)

    def test_macos_stopped_claude_enable_launches_before_connecting(self):
        self.running = False
        self.manager.toggle_client({'provider': 'claude', 'enabled': True})
        self.launch.assert_called_once()
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_called_once_with()

    def test_macos_gateway_start_launches_selected_claude(self):
        self.running = False
        self.manager.config['enabled'] = {'codex': False, 'claude': True, 'deepseek': False}
        with patch.object(self.manager, 'scan'):
            self.manager.start_enabled()
        self.launch.assert_called_once()
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_called_once_with()

    def test_cli_only_cold_status_does_not_start_desktop(self):
        self.claude.status.return_value = {'connected': False, 'cliAvailable': True,
                                           'cliAuthReady': False}
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform), patch('bridge.clients.manager.sys.platform', platform):
                row = next(row for row in self.manager.toggle_client(
                    {'provider': 'claude', 'enabled': True})['clients'] if row['id'] == 'claude')
                self.assertTrue(row['selectable'])
                self.launch.assert_not_called()
                self.claude.connect.assert_not_called()
                self.claude.reconnect.assert_not_called()

    def test_claude_desktop_launch_capability_requires_confirmed_stopped_supported_app(self):
        self.running = False
        self.claude.status.return_value = {'connected': False, 'installed': True,
                                           'automaticConnection': 'native-console', 'cliAvailable': True}
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertTrue(row['canStartDesktop'])
        self.assertFalse(row['canInitializeDesktop'])
        self.manager.config['enabled']['claude'] = False
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertTrue(row['canStartDesktop'])
        self.manager.config['enabled']['claude'] = True
        self.manager.gateway_running = False
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertFalse(row['canStartDesktop'])
        self.manager.gateway_running = True
        self.running = True
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertFalse(row['canStartDesktop'])
        self.assertTrue(row['canInitializeDesktop'])
        self.running = False
        self.inspect.side_effect = lambda _: {'running': False, 'mainPids': [], 'runtimePids': [], 'unknown': True}
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertFalse(row['canStartDesktop'])
        self.assertFalse(row['canInitializeDesktop'])
        self.inspect.side_effect = lambda _: {'running': False, 'mainPids': [], 'runtimePids': [], 'unknown': False}
        self.claude.status.return_value['automaticConnection'] = 'unavailable'
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertFalse(row['canStartDesktop'])
        self.manager.config['discovered']['claude']['installed'] = False
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertFalse(row['canStartDesktop'])

    def test_missing_native_claude_state_hides_desktop_actions(self):
        self.claude.status.return_value = {'connected': False, 'installed': True,
                                           'automaticConnection': 'native-console', 'cliAvailable': True}
        self.inspect_batch.side_effect = lambda _: {}
        row = next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
        self.assertFalse(row['canStartDesktop'])
        self.assertFalse(row['canInitializeDesktop'])
        self.assertFalse(row['canReconnect'])

    def test_macos_account_recovery_only_restores_existing_connection(self):
        self.manager._launch_account_client('claude', self.manager._descriptor('claude'))
        self.launch.assert_called_once()
        self.claude.reconnect.assert_called_once_with()
        self.claude.connect.assert_not_called()

    def test_macos_account_recovery_without_enabled_gateway_never_connects(self):
        self.manager.gateway_running = False
        self.manager._launch_account_client('claude', self.manager._descriptor('claude'))
        self.launch.assert_called_once()
        self.claude.reconnect.assert_not_called()
        self.claude.connect.assert_not_called()

    def test_linux_claude_enable_does_not_claim_supported_connection(self):
        with patch('bridge.clients.manager.sys.platform', 'linux'):
            with self.assertRaisesRegex(ValueError, '不支持 Claude'):
                self.manager.toggle_client({'provider': 'claude', 'enabled': True})
        self.launch.assert_not_called()
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_not_called()

    def test_explicit_mobile_initialization_only_calls_native_setup(self):
        self.manager.toggle_client({'provider': 'claude', 'enabled': True, 'initializeDesktop': True})
        self.claude.connect.assert_called_once_with()
        self.claude.reconnect.assert_not_called()
        self.launch.assert_not_called()
        self.stop.assert_not_called()

    def test_mobile_initialization_rejects_invalid_flag_combinations(self):
        for provider, enabled, initialize in [('claude', True, 1), ('claude', True, 'true'),
                ('claude', False, True), ('claude', False, False), ('deepseek', True, True), ('codex', True, False)]:
            with self.subTest(provider=provider, enabled=enabled, initialize=initialize):
                with self.assertRaisesRegex(ValueError, '初始化请求'):
                    self.manager.toggle_client({'provider': provider, 'enabled': enabled, 'initializeDesktop': initialize})
        self.claude.connect.assert_not_called()
        self.claude.reconnect.assert_not_called()
        self.launch.assert_not_called()
        self.stop.assert_not_called()

    def test_explicit_false_initialization_keeps_background_route(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            self.manager.toggle_client({'provider': 'claude', 'enabled': True, 'initializeDesktop': False})
        self.claude.reconnect.assert_called_once_with(launch=True)
        self.claude.connect.assert_not_called()

    def test_claude_pending_states_keep_their_reason_and_actual_running_status(self):
        for phase in ('starting', 'connecting', 'needs-initialization', 'failed'):
            self.claude.status.return_value = {'connected': False, 'setupState': phase, 'reason': 'current progress'}
            row = next(r for r in self.manager.clients(refresh=True)['clients'] if r['id'] == 'claude')
            self.assertEqual(row['connectionState'], 'error' if phase == 'failed' else phase)
            self.assertEqual(row['reason'], 'current progress')
            self.assertTrue(row['running'])
            self.assertEqual(row['retryable'], phase == 'failed')

    def test_claude_pending_cache_expires_in_one_second(self):
        self.claude.status.return_value = {'connected': False, 'setupState': 'connecting', 'reason': 'waiting'}
        with patch('bridge.clients.manager.time.monotonic', return_value=10):
            self.manager.clients(refresh=True)
        self.claude.status.return_value = {'connected': False, 'setupState': 'needs-initialization', 'reason': 'initialize'}
        with patch('bridge.clients.manager.time.monotonic', return_value=12):
            row = next(r for r in self.manager.clients()['clients'] if r['id'] == 'claude')
        self.assertEqual(row['connectionState'], 'needs-initialization')

    def test_claude_stopped_process_does_not_claim_pending_connection(self):
        self.running = False
        self.claude.status.return_value = {'connected': False, 'setupState': 'needs-initialization'}
        row = next(r for r in self.manager.clients(refresh=True)['clients'] if r['id'] == 'claude')
        self.assertFalse(row['running'])
        self.assertEqual(row['connectionState'], 'error')
        self.assertTrue(row['retryable'])

    def test_claude_launch_waits_for_process_and_does_not_cache_false_failure(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform), patch('bridge.clients.manager.sys.platform', platform):
                self.running = False
                self.claude.status.return_value = {'connected': False, 'setupState': 'connecting',
                                                   'reason': '正在连接 Claude'}
                with patch('bridge.clients.manager.time.monotonic', return_value=10):
                    first = self.manager.clients(refresh=True)
                row = next(r for r in first['clients'] if r['id'] == 'claude')
                self.assertEqual(row['connectionState'], 'starting')
                self.assertFalse(row['retryable'])
                self.assertFalse(row['connected'])
                self.running = True
                self.claude.status.return_value = {'connected': True, 'setupState': 'connected'}
                # A launch must retain the short pending cache instead of the
                # 15-second idle/error cache hiding its signed connection.
                with patch('bridge.clients.manager.time.monotonic', return_value=12):
                    ready = next(r for r in self.manager.clients()['clients'] if r['id'] == 'claude')
                self.assertEqual(ready['connectionState'], 'connected')
                self.assertTrue(ready['connected'])

    def test_claude_launch_failure_remains_terminal_without_a_process(self):
        self.running = False
        self.claude.status.return_value = {'connected': False, 'setupState': 'failed',
                                           'reason': 'native launch failed'}
        row = next(r for r in self.manager.clients(refresh=True)['clients'] if r['id'] == 'claude')
        self.assertEqual(row['connectionState'], 'error')
        self.assertEqual(row['reason'], 'native launch failed')
        self.assertTrue(row['retryable'])

    def test_explicit_claude_connect_button_may_launch_and_initialize(self):
        self.running = False
        self.claude.status.return_value = {'connected': False, 'setupState': 'needs-initialization'}
        self.manager.control({'action': 'connect-claude'})
        self.launch.assert_called_once()
        self.claude.connect.assert_called_once_with()
        self.claude.reconnect.assert_not_called()

    def test_pending_interaction_blocks_stop_and_preserves_preference(self):
        self.claude.desktop_listing.return_value = {'complete': True,
            'sessions': [{'id': 'one', 'status': 'idle', 'runtimeKnown': True, 'requests': [{'id': 'question'}]}]}
        with self.assertRaisesRegex(ValueError, '任务运行或等待'):
            self.toggle('claude', False)
        self.stop.assert_not_called(); self.claude.cancel.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))

    def test_missing_surface_or_missing_runtime_state_cannot_prove_idle(self):
        for payload in ({'connected': True, 'complete': False, 'sessions': []},
                        {'connected': True, 'complete': True, 'sessions': [{'id': 'one', 'status': 'idle', 'runtimeKnown': False}]}):
            self.claude.desktop_listing.return_value = payload
            with self.assertRaisesRegex(ValueError, '无法确认'):
                self.toggle('claude', False)
        self.stop.assert_not_called()

    def test_mac_claude_quit_uses_fresh_desktop_inventory_not_incomplete_cli_history(self):
        self.claude.call.side_effect = lambda action, *args: ({'complete': False, 'sessions': [
            {'id': 'unrelated-cli-history', 'backend': 'cli', 'status': 'idle', 'runtimeKnown': False}]} if action == 'list' else {'models': [{'id': 'model'}]})
        self.claude.desktop_listing.reset_mock()
        with patch.object(self.manager, 'clients', return_value={'clients': []}):
            self.manager.toggle_client({'provider': 'claude', 'enabled': False, 'quitDesktop': True})
        self.claude.desktop_listing.assert_called_once_with()
        self.assertFalse(any(call.args[0] == 'list' for call in self.claude.call.call_args_list))
        self.stop.assert_called_once()
        self.assertFalse(self.manager.enabled('claude'))

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
        self.claude.desktop_listing.return_value = {'complete': True,
            'sessions': [{'id': 'one', 'status': 'active', 'runtimeKnown': True, 'requests': []}]}
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

    def test_stopped_compatible_dsh_connector_updates_before_launch(self):
        self.running = False; self.dsh.reused = True
        self.dsh.ensure_installed.return_value = {'installed': True, 'reused': True, 'updateRequired': False}
        self.dsh.call.side_effect = BridgeUnavailable('stopped')
        order = []
        self.dsh.update_existing.side_effect = lambda: order.append('update')
        self.launch_dsh.side_effect = lambda *args: order.append('launch') or {'running': True}
        self.manager._start_deepseek()
        self.assertEqual(order, ['update', 'launch'])
        self.stop.assert_not_called()
        self.stop_dsh.assert_not_called()

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
        with patch('bridge.clients.manager.sys.platform', 'win32'):
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
        from bridge.features.accounts.accounts import operation
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

    def dsh_row(self, refresh=True):
        return next(row for row in self.manager.clients(refresh=refresh)['clients'] if row['id'] == 'deepseek')

    def test_dsh_waiting_connection_explains_progress_despite_remembered_configuration(self):
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        self.manager._start_deepseek()
        row = self.dsh_row()
        self.assertTrue(row['configured'])
        self.assertFalse(row['connected'])
        self.assertEqual(row['connectionState'], 'connecting')
        self.assertEqual(row['setupStatus'], 'connecting')
        self.assertIn('等待', row['reason'])
        self.assertFalse(row['retryable'])

    def test_dsh_slow_native_launch_remains_starting_until_process_is_observed(self):
        self.running = False
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        self.launch_dsh.side_effect = lambda *_: {'running': False, 'launched': True}
        self.manager._start_deepseek()
        self.assertEqual(self.dsh_row()['connectionState'], 'starting')
        self.running = True
        self.assertEqual(self.dsh_row()['connectionState'], 'connecting')
        self.stop.assert_not_called()

    def test_dsh_launch_failure_is_persisted_and_retryable(self):
        self.running = False
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        self.launch_dsh.side_effect = lambda *_: {'running': False, 'launched': False}
        with self.assertRaisesRegex(ValueError, '尚未启动'):
            self.manager._start_deepseek()
        row = self.dsh_row()
        self.assertEqual(row['connectionState'], 'error')
        self.assertTrue(row['retryable'])
        self.assertIn('尚未启动', row['reason'])
        self.assertEqual(json.loads(self.manager.config_path.read_text())['setup']['deepseek']['connectionState'], 'error')

    def test_dsh_connection_timeout_can_retry_and_late_connection_recovers(self):
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        with patch('bridge.clients.manager.time.time', return_value=1000):
            self.manager._start_deepseek()
        with patch('bridge.clients.manager.time.time', return_value=1061):
            row = self.dsh_row()
            self.assertEqual(row['connectionState'], 'timeout')
            self.assertTrue(row['retryable'])
            self.assertIn('重试', row['reason'])
            self.manager._start_deepseek()
            self.assertEqual(self.dsh_row()['connectionState'], 'connecting')
        self.dsh.call.side_effect = lambda *_: {'connected': True, 'configured': True, 'bridgeRevision': 3}
        row = self.dsh_row()
        self.assertEqual(row['connectionState'], 'connected')
        self.assertFalse(row['retryable'])
        self.assertNotIn('connectionDeadline', self.manager.config['setup']['deepseek'])
        self.stop.assert_not_called()

    def test_dsh_exit_during_connection_reports_error_without_claiming_connected(self):
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        self.manager._start_deepseek()
        self.running = False
        row = self.dsh_row()
        self.assertEqual(row['connectionState'], 'error')
        self.assertFalse(row['connected'])
        self.assertTrue(row['retryable'])
        self.assertIn('退出', row['reason'])

    def test_windows_dsh_cold_start_allows_seventy_seconds_then_retry_and_late_connection(self):
        self.running = False
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        with patch('bridge.clients.manager.sys', SimpleNamespace(platform='win32')), \
             patch('bridge.clients.manager.windows_session.status', return_value={'state': 'locked', 'interactive': False, 'reason': 'fixture'}):
            with patch('bridge.clients.manager.time.time', return_value=1000):
                self.manager._start_deepseek()
            self.assertEqual(self.manager.config['setup']['deepseek']['connectionDeadline'], 1120)
            with patch('bridge.clients.manager.time.time', return_value=1070):
                row = self.dsh_row()
                self.assertEqual(row['connectionState'], 'connecting')
                self.assertFalse(row['retryable']); self.assertFalse(row['connected'])
                self.launch_dsh.assert_called_once()
            with patch('bridge.clients.manager.time.time', return_value=1121):
                row = self.dsh_row()
                self.assertEqual(row['connectionState'], 'timeout'); self.assertTrue(row['retryable'])
                self.manager._start_deepseek()
                self.assertEqual(self.dsh_row()['connectionState'], 'connecting')
                self.assertEqual(self.manager.config['setup']['deepseek']['connectionDeadline'], 1241)
            self.dsh.call.side_effect = lambda *_: {'connected': True, 'configured': False, 'bridgeRevision': 3}
            row = self.dsh_row()
            self.assertEqual(row['connectionState'], 'connected')
            self.assertEqual(row['setupStatus'], 'unconfigured'); self.assertFalse(row['retryable'])
            self.assertNotIn('connectionDeadline', self.manager.config['setup']['deepseek'])
        self.stop.assert_not_called()

    def test_windows_dsh_resumed_wait_without_deadline_uses_same_budget(self):
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        self.manager.config['setup'] = {'deepseek': {'setupStatus': 'connecting', 'connectionState': 'connecting'}}
        with patch('bridge.clients.manager.sys', SimpleNamespace(platform='win32')), \
             patch('bridge.clients.manager.windows_session.status', return_value={'state': 'locked', 'interactive': False, 'reason': 'fixture'}), \
             patch('bridge.clients.manager.time.time', return_value=1000):
            self.assertEqual(self.dsh_row()['connectionState'], 'connecting')
        self.assertEqual(self.manager.config['setup']['deepseek']['connectionDeadline'], 1120)

    def test_dsh_pending_cache_refreshes_before_the_normal_fifteen_seconds(self):
        self.dsh.call.side_effect = BridgeUnavailable('endpoint not ready')
        self.manager._start_deepseek()
        with patch('bridge.clients.manager.time.monotonic', return_value=100):
            self.assertFalse(self.dsh_row()['connected'])
        self.dsh.call.side_effect = lambda *_: {'connected': True, 'configured': True, 'bridgeRevision': 3}
        with patch('bridge.clients.manager.time.monotonic', return_value=101.1):
            self.assertTrue(self.dsh_row(refresh=False)['connected'])

    def test_dsh_current_connector_clears_stale_restart_even_without_account(self):
        self.manager.config['setup'] = {'deepseek': {'setupStatus': 'restart-required', 'requiredBridgeRevision': 3}}
        self.dsh.call.side_effect = lambda *_: {'connected': True, 'configured': False, 'bridgeRevision': 3}
        row = self.dsh_row()
        self.assertEqual(row['setupStatus'], 'unconfigured')
        self.assertEqual(row['connectionState'], 'connected')
        self.assertFalse(row['configured'])
        self.assertNotIn('requiredBridgeRevision', self.manager.config['setup']['deepseek'])

    def test_dsh_real_connector_update_is_not_hidden_by_live_transport(self):
        self.manager.config['setup'] = {'deepseek': {'setupStatus': 'restart-required', 'requiredBridgeRevision': 3}}
        self.dsh.call.side_effect = lambda *_: {'connected': True, 'configured': True, 'bridgeRevision': 2, 'updateRequired': True}
        row = self.dsh_row()
        self.assertEqual(row['setupStatus'], 'restart-required')
        self.assertFalse(row['configured'])
        self.assertIn('requiredBridgeRevision', self.manager.config['setup']['deepseek'])

    def test_dsh_only_boolean_live_confirmation_means_connected(self):
        self.dsh.call.side_effect = lambda *_: {'connected': 'pending', 'configured': True, 'bridgeRevision': 3}
        self.assertFalse(self.dsh_row()['connected'])


if __name__ == '__main__': unittest.main()
