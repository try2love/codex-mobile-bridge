import os
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.antigravity.account_switch import switch_account
from bridge.clients.antigravity.cli_session import AntigravityCli
from bridge.clients.antigravity.hook_ipc import HookIPCServer
from bridge.clients.errors import BridgeUnavailable
from bridge.platforms import antigravity_lifecycle as native


class NativeIdleTests(unittest.TestCase):
    def setUp(self):
        self.cli = AntigravityCli()
        self.rpc = Mock()
        self.cli.call_ls_rpc = self.rpc

    def test_every_child_and_pending_state_blocks(self):
        for row in ({'status': 'CASCADE_RUN_STATUS_RUNNING'}, {}, None,
                    *({'status': 'CASCADE_RUN_STATUS_IDLE', key: value} for key, value in (
                        ('waitingSteps', [{}]), ('notFullyIdle', True), ('hasActiveChildren', True),
                        ('hasRunningSubagents', True), ('backgroundTaskCount', 1)))):
            self.rpc.return_value = {'trajectorySummaries': {
                'parent': {'status': 'CASCADE_RUN_STATUS_IDLE'}, 'child': row}}
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.cli.assert_idle('app', {'pid': 41})

    def test_missing_or_invalid_inventory_never_means_idle(self):
        for result in ({'error': 'unavailable'}, {'trajectorySummaries': None}, {'trajectorySummaries': []},
                       {'trajectorySummaries': {}, 'nextPageToken': 'more'}):
            self.rpc.return_value = result
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.cli.assert_idle('app', {'pid': 41})

    def test_explicit_complete_empty_inventory_and_idle_are_accepted(self):
        self.rpc.return_value = {}  # Native protobuf JSON omits an empty map.
        self.cli.assert_idle('app', {'pid': 41})
        for rows in ({}, {'chat': {'status': 'CASCADE_RUN_STATUS_IDLE', 'notFullyIdle': False}}):
            self.rpc.return_value = {'trajectorySummaries': rows}
            self.cli.assert_idle('ide', {'pid': 42})
        self.rpc.assert_called_with('GetAllCascadeTrajectories', {'exclude_subtrajectories': False},
                                    surface='ide', expected_identity={'pid': 42})

    def test_replaced_runtime_is_rejected_before_http_request(self):
        cli = AntigravityCli()
        cli._opener = Mock()
        with patch('bridge.clients.antigravity.cli_session.endpoint', return_value={'identity': {'pid': 99}}):
            with self.assertRaises(BridgeUnavailable):
                cli.assert_idle('app', {'pid': 41})
        cli._opener.open.assert_not_called()

    def test_only_json_empty_object_not_empty_http_body_can_mean_idle(self):
        target = {'identity': {'pid': 42}, 'address': 'http://127.0.0.1:1', 'token': 'fixture'}
        cli = AntigravityCli()
        cli._opener = Mock()
        with patch('bridge.clients.antigravity.cli_session.endpoint', return_value=target), \
                patch('bridge.clients.antigravity.cli_session.verify_endpoint'):
            cli._opener.open.return_value = nullcontext(SimpleNamespace(read=lambda _: b''))
            with self.assertRaises(BridgeUnavailable):
                cli.assert_idle('app', target['identity'])
            cli._opener.open.return_value = nullcontext(SimpleNamespace(read=lambda _: b'{}'))
            cli.assert_idle('app', target['identity'])

    def test_pending_hook_and_queue_are_preserved_when_switch_is_blocked(self):
        hooks = HookIPCServer.__new__(HookIPCServer)
        hooks._lock = threading.Lock()
        for pending, queued in (({'request': object()}, {}), ({}, {'chat': ['draft']})):
            hooks._pending, hooks._queues = pending.copy(), queued.copy()
            with self.assertRaises(ValueError):
                hooks.assert_account_idle()
            self.assertEqual(hooks._pending, pending)
            self.assertEqual(hooks._queues, queued)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.lock = threading.RLock()
        self.store.restore.return_value = {'accounts': [], 'activeIds': [], 'message': 'applied'}
        self.store.public.return_value = {'accounts': [], 'activeIds': []}
        self.adapter = SimpleNamespace(home=Path.home(),
                                       cli=Mock(_lock=threading.RLock(), assert_idle=Mock()),
                                       hooks=Mock(assert_account_idle=Mock()))
        self.plan = {'writers': {41, 42}, 'installation': {'surface': 'app'}}
        self.patches = [patch('bridge.clients.antigravity.account_switch.'+name) for name in
                        ('native', 'endpoint', 'verify_endpoint')]
        self.native, self.endpoint, self.verify = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.native.prepare.return_value = self.plan
        self.endpoint.return_value = {'identity': {'pid': 42}}

    def run_switch(self):
        return switch_account(self.store, self.adapter, {}, 'saved')

    def test_invalid_saved_account_does_not_exit_anything(self):
        self.store.validate_saved.side_effect = ValueError('invalid snapshot')
        with self.assertRaises(ValueError):
            self.run_switch()
        self.native.prepare.assert_not_called()
        self.native.stop.assert_not_called()

    def test_unowned_server_or_unknown_tasks_do_not_stop_or_restore(self):
        for failure in ('server', 'tasks', 'queue'):
            with self.subTest(failure=failure):
                self.endpoint.return_value = {'identity': {'pid': 99 if failure == 'server' else 42}}
                self.native.bind_server.side_effect = ValueError('unowned') if failure == 'server' else None
                self.adapter.cli.assert_idle.side_effect = ValueError('unknown') if failure == 'tasks' else None
                self.adapter.hooks.assert_account_idle.side_effect = ValueError('queue') if failure == 'queue' else None
                with self.assertRaises(ValueError):
                    self.run_switch()
        self.native.stop.assert_not_called()
        self.store.restore.assert_not_called()

    def test_exit_timeout_keeps_credentials_and_never_restarts(self):
        self.native.stop.side_effect = ValueError('still running')
        with self.assertRaises(ValueError):
            self.run_switch()
        self.store.restore.assert_not_called()
        self.native.restart.assert_not_called()

    def test_restore_failure_never_restarts_uncertain_credentials(self):
        self.store.restore.side_effect = ValueError('rollback retained')
        with self.assertRaises(ValueError):
            self.run_switch()
        self.native.restart.assert_not_called()

    def test_restart_failure_keeps_applied_snapshot_without_replay(self):
        self.native.restart.side_effect = OSError('launch refused')
        result = self.run_switch()
        self.assertIn('未确认', result['message'])
        self.store.restore.assert_called_once_with('saved')
        self.native.restart.assert_called_once_with(self.plan)
        self.assertEqual(result['activeIds'], [])

    def test_stopped_client_is_never_started_by_switch(self):
        self.native.prepare.return_value = None
        self.run_switch()
        self.native.stop.assert_not_called()
        self.native.restart.assert_not_called()
        self.store.restore.assert_called_once()

    def test_new_login_only_confirmed_by_native_metadata(self):
        self.assertIn('尚未确认', self.run_switch()['message'])
        self.store.public.return_value = {'accounts': [], 'activeIds': ['saved']}
        self.assertIn('确认当前登录', self.run_switch()['message'])

    def test_shared_manager_routes_switch_and_preserves_configuration(self):
        from bridge.clients.manager import DesktopSessions
        configuration = {'enabled': {'antigravity': True}, 'phoneBinding': 'keep', 'draft': 'keep'}
        manager = SimpleNamespace(adapters={'antigravity': self.adapter},
                                  account_stores={('antigravity', str(self.adapter.home)): self.store},
                                  client_lock=threading.RLock(), _changing=lambda *a: nullcontext(),
                                  _descriptor=Mock(return_value={'installations': {}}),
                                  client_cache={'old': True}, gateway_running=True, config=configuration.copy())
        value = DesktopSessions.client_accounts(manager, 'antigravity', {'operation': 'switch', 'id': 'saved'})
        self.assertEqual(value['provider'], 'antigravity')
        self.store.restore.assert_called_once_with('saved')
        self.native.stop.assert_called_once_with(self.plan)
        self.assertIsNone(manager.client_cache)
        self.assertEqual(manager.config, configuration)


class ProcessScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.directory.cleanup)
        self.exe = Path(self.directory.name).resolve()/'Antigravity'
        self.exe.write_bytes(b'fixture')
        self.install = {'application': str(self.exe), 'executable': str(self.exe), 'surface': 'app'}
        self.rows = {41: {'pid': 41, 'parent': 1, 'image': os.path.normcase(str(self.exe)), 'birth': 'one'},
                     42: {'pid': 42, 'parent': 41, 'image': 'language_server', 'birth': 'two'}}
        self.pids = [41, 42]
        self.platform = Mock()
        self.platform.commands.return_value = {41: str(self.exe)}
        patches = [patch.object(native, 'installation', return_value=self.install),
                   patch.object(native, 'login_processes', side_effect=lambda: list(self.pids)),
                   patch.object(native, 'identity', side_effect=lambda pid: dict(self.rows[pid])),
                   patch.object(native, 'desktop', return_value=self.platform)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.descriptor = {'installations': {'app': self.install}}

    def prepare(self):
        return native.prepare(self.descriptor)

    def test_cli_ide_or_unrelated_server_does_not_join_app_tree(self):
        for image in ('agy', 'Antigravity IDE', 'language_server_other'):
            self.rows[43] = {'pid': 43, 'parent': 1, 'image': image, 'birth': 'three'}
            self.pids = [41, 42, 43]
            with self.subTest(image=image), self.assertRaisesRegex(ValueError, '独立'):
                self.prepare()
        self.platform.quit_process.assert_not_called()
        self.platform.quit_application.assert_not_called()

    def test_custom_launch_arguments_and_duplicate_gui_are_blocked(self):
        self.platform.commands.return_value = {41: str(self.exe)+' --user-data-dir=custom'}
        with self.assertRaises(ValueError):
            self.prepare()
        self.rows[43] = {**self.rows[41], 'pid': 43, 'birth': 'three'}
        self.pids.append(43)
        self.platform.commands.return_value = {41: str(self.exe), 43: str(self.exe)}
        with self.assertRaises(ValueError):
            self.prepare()

    def test_cli_in_gui_terminal_is_not_part_of_account_restart(self):
        self.rows[43] = {'pid': 43, 'parent': 41, 'image': 'agy', 'birth': 'three'}
        self.pids.append(43)
        plan = self.prepare()
        with self.assertRaisesRegex(ValueError, '独立'):
            native.bind_server(plan, {'identity': self.rows[42]})
        self.platform.quit_process.assert_not_called()

    def test_only_explicit_embedded_hub_can_supply_gui_inventory(self):
        self.rows[42]['image'] = '/official/agy'
        plan = self.prepare()
        for args in (['agy'], ['agy', '--app_data_dir=antigravity'],
                     ['agy', '--hub', '--app_data_dir=antigravity-cli']):
            with patch.object(native, '_candidates', return_value=[(42, args)]), self.assertRaises(ValueError):
                native.bind_server(plan, {'identity': self.rows[42]})
        with patch.object(native, '_candidates', return_value=[(42, ['agy', '--hub', '--app_data_dir=antigravity'])]):
            native.bind_server(plan, {'identity': self.rows[42]})

    def test_reused_pid_and_new_writer_abort_before_quit(self):
        plan = self.prepare()
        self.rows[41]['birth'] = 'new-generation'
        with self.assertRaises(ValueError):
            native.stop(plan)
        self.rows[41]['birth'] = 'one'
        self.pids.append(44)
        with self.assertRaises(ValueError):
            native.stop(plan)
        self.platform.quit_process.assert_not_called()
        self.platform.quit_application.assert_not_called()

    def test_normal_exit_waits_without_helper_kill_or_duplicate_launch(self):
        plan = self.prepare()
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native.time, 'monotonic', side_effect=[0, 13]):
            with self.assertRaisesRegex(ValueError, '尚未正常退出'):
                native.stop(plan)
        self.platform.quit_application.assert_called_once_with(str(self.exe), pids=[41])
        self.platform.terminate.assert_not_called()
        self.platform.start.assert_not_called()

    def test_exit_then_restart_only_original_default_gui(self):
        plan = self.prepare()
        self.platform.quit_application.side_effect = lambda *a, **k: self.pids.clear()
        with patch.object(native.sys, 'platform', 'darwin'):
            native.stop(plan)
            native.restart(plan)
        self.platform.start.assert_called_once_with(self.exe, Path.home(), {})
        self.pids.append(41)
        with self.assertRaises(ValueError):
            native.restart(plan)
        self.assertEqual(self.platform.start.call_count, 1)

    def test_windows_only_requests_normal_main_window_close(self):
        plan = self.prepare()
        self.platform.quit_process.side_effect = lambda pid: self.pids.clear()
        with patch.object(native.sys, 'platform', 'win32'):
            native.stop(plan)
        self.platform.quit_process.assert_called_once_with(41)
        self.platform.terminate.assert_not_called()
        self.platform.start.assert_not_called()

    def test_linux_binds_the_generation_and_only_signals_the_gui(self):
        import signal
        plan = self.prepare()
        binder = Mock()
        binder.open.return_value = 17
        with patch.object(native.sys, 'platform', 'linux'), \
                patch('bridge.platforms.linux.force_exit.Native', return_value=binder), \
                patch.object(signal, 'pidfd_send_signal', create=True, side_effect=lambda *a: self.pids.clear()) as send:
            native.stop(plan)
        binder.open.assert_called_once_with(plan['processes'][41])
        send.assert_called_once_with(17, signal.SIGTERM)
        binder.close.assert_called_once_with(17)
        binder.terminate.assert_not_called()

    def test_changed_installation_blocks_restart(self):
        plan = self.prepare()
        self.pids.clear()
        self.exe.write_bytes(b'updated')
        with self.assertRaises(ValueError):
            native.restart(plan)
        self.platform.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
