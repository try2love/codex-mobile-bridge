"""Explicit shared Desktop initialization without starting a real App."""
import unittest
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_accounts
from bridge.features.accounts import accounts as accounts_module
from bridge.clients.codex import desktop_runtime


class DesktopRuntimeAccountsTests(unittest.TestCase):
    tearDown = test_accounts.AccountsTests.tearDown

    def setUp(self):
        test_accounts.AccountsTests.setUp(self)
        self.bridge.host = 'local'
        self.bridge.assert_desktop_idle = Mock()
        self.manager.public = lambda: {'switch': dict(self.manager.state), 'desktopRuntime': self.manager.desktop_runtime_status()}
        self.manager.invalidate = Mock()
        self.running = {'running': True, 'pids': [41], 'mainPids': [41], 'runtimePids': [], 'unknown': False}
        self.stopped = {**self.running, 'running': False, 'pids': [], 'mainPids': []}
        self.identity = {'pid': 41, 'image': str(self.app.executable), 'birth': [100, 1], 'started': [1000, 0]}
        self.real_initialization_lock = desktop_runtime.initialization_lock
        patches = {
            'platform': patch.object(accounts_module, 'sys', SimpleNamespace(platform='darwin')),
            'app_factory': patch.object(accounts_module, 'DesktopApp', return_value=self.app),
            'settings': patch.object(desktop_runtime, 'settings', return_value=None),
            'validate_runtime': patch.object(desktop_runtime, 'validate_runtime'),
            'configure': patch.object(desktop_runtime, 'configure'),
            'connect': patch.object(desktop_runtime, 'connect', return_value=Mock()),
            'initialize_lock': patch.object(desktop_runtime, 'initialization_lock', side_effect=lambda home: nullcontext()),
            'identity': patch.object(desktop_runtime, 'process_identity', return_value=self.identity),
            'inspect': patch('bridge.clients.lifecycle.inspect_client', side_effect=[self.running, self.running, self.stopped]),
            'stop': patch('bridge.clients.lifecycle.stop_client'),
            'environment': patch('bridge.platforms.macos.codex_daemon_env.read_process_environment', return_value=({'CODEX_HOME': str(self.home)}, None)),
            'commands': patch('bridge.platforms.macos.desktop.commands', return_value={41: str(self.app.executable)}),
        }
        self.mocks = {}
        for key, value in patches.items():
            self.mocks[key] = value.start()
            self.addCleanup(value.stop)
        self.value = {'action': 'initializeDesktopRuntime', 'confirmed': True, 'tasksConfirmed': True, 'requestId': str(uuid.uuid4())}

    def run_initialization(self):
        self.manager.control(self.value)
        test_accounts.wait_for_account_worker(self)

    def test_requires_confirmation_and_rejects_independent_or_other_platform(self):
        for key in ('confirmed', 'tasksConfirmed'):
            with self.assertRaisesRegex(ValueError, '确认'):
                self.manager.control({**self.value, key: False})
        with self.assertRaisesRegex(ValueError, '标识'):
            self.manager.control({**self.value, 'requestId': 'invalid'})
        self.bridge.host = 'codex:independent'
        with self.assertRaisesRegex(ValueError, '独立配置'):
            self.manager.control(self.value)
        self.assertFalse(self.manager.desktop_runtime_status()['supported'])
        self.bridge.host = 'local'
        with patch.object(accounts_module, 'sys', SimpleNamespace(platform='win32')):
            with self.assertRaisesRegex(ValueError, '不支持'):
                self.manager.control(self.value)
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def test_native_idle_identity_normal_restart_and_shared_handshake_are_required(self):
        order = []
        self.mocks['validate_runtime'].side_effect = lambda cli: order.append('capabilities')
        self.bridge.assert_desktop_idle.side_effect = lambda **kw: order.append('idle')
        self.mocks['stop'].side_effect = lambda *args, **kw: order.append('stop')
        self.mocks['configure'].side_effect = lambda *args: order.append('configure')
        self.app.start.side_effect = lambda: order.append('start')
        self.run_initialization()
        self.assertEqual(order, ['capabilities', 'idle', 'stop', 'configure', 'start'])
        self.mocks['validate_runtime'].assert_called_once_with(self.manager.runtime)
        self.assertEqual(self.manager.state['phase'], 'complete')
        self.assertEqual(self.manager.state['operation'], 'desktopRuntime')
        self.bridge.assert_desktop_idle.assert_called_once_with(allow_stopped_app=False)
        self.assertEqual(self.mocks['identity'].call_count, 4)
        self.mocks['configure'].assert_called_once_with(self.home, self.app.executable, self.manager.runtime)
        self.mocks['connect'].assert_called_once_with(self.home, self.manager.runtime)
        self.mocks['connect'].return_value.close.assert_called_once()
        self.assertEqual(self.manager.snapshot_files(), self.original)
        self.manager.control(self.value)
        self.assertEqual(self.mocks['stop'].call_count, 1)

    def test_unknown_or_busy_native_tasks_do_not_stop_or_configure(self):
        self.bridge.assert_desktop_idle.side_effect = ValueError('native tasks unknown')
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertIn('native tasks unknown', self.manager.state['error'])
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()
        self.app.start.assert_not_called()

    def test_unsupported_runtime_keeps_app_running_before_preparing_or_worker(self):
        self.mocks['validate_runtime'].side_effect = ValueError('shared connection is unsupported')
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            self.manager.control(self.value)
        self.assertEqual(self.manager.state['phase'], 'idle')
        self.assertIsNone(self.manager.thread)
        self.assertFalse((self.manager.root/'switch.json').exists())
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()
        self.app.start.assert_not_called()

    def test_pending_gateway_submission_blocks_initialization_before_worker(self):
        self.bridge.submissions['chat:submission'] = {'status': 'unknown', 'title': 'Pending chat'}
        with self.assertRaisesRegex(ValueError, 'Pending chat'):
            self.manager.control(self.value)
        self.assertIsNone(self.manager.thread)
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def test_interrupted_initialization_allows_explicit_retry_without_account_rollback(self):
        self.manager.phase('starting', operation='desktopRuntime', requestId=self.value['requestId'])
        restarted = accounts_module.Accounts(self.bridge)
        self.assertEqual(restarted.state['phase'], 'failed')
        self.assertIn('重新初始化', restarted.state['error'])
        restarted.assert_editable()
        self.mocks['stop'].assert_not_called()
        self.app.start.assert_not_called()

    def test_multiple_app_instances_are_rejected_without_quit(self):
        self.mocks['inspect'].side_effect = None
        self.mocks['inspect'].return_value = {**self.running, 'mainPids': [41, 42]}
        self.run_initialization()
        self.assertIn('唯一', self.manager.state['error'])
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def test_identity_change_after_task_check_cannot_quit_reused_pid(self):
        changed = {**self.identity, 'birth': [101, 1]}
        self.mocks['identity'].side_effect = [self.identity, self.identity, changed, changed]
        self.run_initialization()
        self.assertIn('进程已变化', self.manager.state['error'])
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def test_stopped_app_is_started_only_after_fresh_idle_check(self):
        self.mocks['inspect'].side_effect = None
        self.mocks['inspect'].return_value = self.stopped
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'complete')
        self.bridge.assert_desktop_idle.assert_called_once_with(allow_stopped_app=True)
        self.mocks['stop'].assert_not_called()
        self.app.start.assert_called_once()

    def test_different_home_sqlite_root_or_electron_profile_cannot_quit_native_app(self):
        for environment in ({'CODEX_HOME': str(self.root/'other')},
                {'CODEX_HOME': str(self.home), 'CODEX_SQLITE_HOME': str(self.root/'other')},
                {'CODEX_HOME': str(self.home), 'CODEX_ELECTRON_USER_DATA_PATH': str(self.home)}):
            with self.subTest(environment=environment):
                self.value['requestId'] = str(uuid.uuid4())
                self.mocks['inspect'].side_effect = None
                self.mocks['inspect'].return_value = self.running
                self.mocks['environment'].return_value = environment, None
                self.run_initialization()
                self.assertIn('其他配置目录', self.manager.state['error'])
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def test_user_data_dir_argument_or_unreadable_environment_cannot_quit_native_app(self):
        self.mocks['commands'].return_value = {41: str(self.app.executable)+' "--user-data-dir=/fixture/isolated"'}
        self.run_initialization()
        self.assertIn('其他配置目录', self.manager.state['error'])
        self.mocks['stop'].assert_not_called()
        self.value['requestId'] = str(uuid.uuid4())
        self.mocks['commands'].return_value = {41: str(self.app.executable)}
        self.mocks['environment'].side_effect = OSError('permission unavailable')
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.mocks['stop'].assert_not_called()

    def test_cross_gateway_lock_rejection_does_not_stop_app(self):
        self.mocks['initialize_lock'].side_effect = ValueError('another gateway is initializing')
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.mocks['inspect'].assert_not_called()
        self.mocks['stop'].assert_not_called()

    def test_failed_shared_handshake_keeps_settings_without_automatic_restart(self):
        self.mocks['configure'].side_effect = lambda *args: setattr(self.mocks['settings'], 'return_value', {'version': 1})
        self.mocks['connect'].side_effect = ValueError('native connection unavailable')
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertTrue(self.manager.desktop_runtime_status()['configured'])
        self.app.start.assert_called_once()
        self.mocks['stop'].assert_called_once()
        self.assertEqual(self.manager.snapshot_files(), self.original)
        self.manager.control(self.value)
        self.app.start.assert_called_once()

    def test_unreadable_settings_are_reported_without_opening_a_connection(self):
        self.mocks['settings'].side_effect = ValueError('invalid settings')
        status = self.manager.desktop_runtime_status()
        self.assertTrue(status['supported'])
        self.assertFalse(status['configured'])
        self.assertIn('无法读取', status['error'])
        self.mocks['connect'].assert_not_called()

    def test_switch_lock_conflict_does_not_change_accounts_or_exit(self):
        self.mocks['initialize_lock'].side_effect = ValueError('another gateway is initializing')
        with self.assertRaisesRegex(ValueError, 'another gateway'):
            self.manager.switch({**self.value, 'id': '1'*32})
        self.assertIsNone(self.manager.thread)
        self.assertEqual(self.manager.snapshot_files(), self.original)
        self.app.stop.assert_not_called()
        self.app.start.assert_not_called()

    def test_switch_worker_lock_conflict_does_not_prepare_or_exit(self):
        self.mocks['initialize_lock'].side_effect = ValueError('another gateway is initializing')
        with patch.object(self.manager, '_apply_switch') as apply:
            self.manager._switch({}, self.app)
        apply.assert_not_called()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertEqual(self.manager.snapshot_files(), self.original)
        self.app.stop.assert_not_called()

    def test_recovery_lock_conflict_preserves_backup_and_native_app(self):
        self.mocks['initialize_lock'].side_effect = ValueError('another gateway is initializing')
        backup = {'desktopExecutable': str(self.app.executable), 'files': self.original, 'activeId': None}
        accounts_module.private_json(self.manager.root/'rollback.json', backup)
        self.manager.phase('interrupted')
        self.manager.recover({'confirmed': True})
        test_accounts.wait_for_account_worker(self)
        self.assertEqual(self.manager.state['phase'], 'interrupted')
        self.assertTrue((self.manager.root/'rollback.json').exists())
        self.assertEqual(self.manager.snapshot_files(), self.original)
        self.app.stop.assert_not_called()
        self.app.start.assert_not_called()

    def test_unconfigured_mac_switch_hands_lock_to_worker_without_self_conflict(self):
        self.mocks['initialize_lock'].side_effect = self.real_initialization_lock
        row = {'id': '1'*32, 'kind': 'api', 'name': 'Fixture'}
        self.manager.index['accounts'] = [row]
        self.manager.info.current = Mock(return_value={'id': None})
        def apply(selected, app):
            with self.assertRaisesRegex(ValueError, '共享接入仍在使用或退出中'):
                with self.real_initialization_lock(self.home):
                    pass
            self.manager.phase('complete')
        with patch.object(self.manager, '_apply_switch', side_effect=apply) as operation:
            self.manager.switch({**self.value, 'id': row['id']})
            test_accounts.wait_for_account_worker(self)
        operation.assert_called_once_with(row, self.app)
        self.assertEqual(self.manager.state['phase'], 'complete')
        self.assertEqual(self.mocks['initialize_lock'].call_count, 2)

    def test_independent_home_and_other_platform_keep_existing_lifecycle_path(self):
        self.bridge.host = 'codex:independent'
        with self.manager.desktop_runtime_lifecycle():
            pass
        self.bridge.host = 'local'
        with patch.object(accounts_module, 'sys', SimpleNamespace(platform='linux')):
            with self.manager.desktop_runtime_lifecycle():
                pass
        self.mocks['initialize_lock'].assert_not_called()

    def test_native_runtime_exit_is_awaited_only_after_ipc_disconnect(self):
        order = []
        socket = Mock()
        socket.close.side_effect = lambda: order.append('ipc-open')
        def connect(*args, **kwargs):
            if not order:
                return socket
            order.append('ipc-closed')
            raise OSError('closed')
        with patch.object(accounts_module, 'connect_stream', side_effect=connect), \
                patch.object(accounts_module.time, 'sleep'), \
                patch.object(desktop_runtime, 'wait_stopped', side_effect=lambda home: order.append('runtime-exited')) as wait:
            accounts_module.Accounts.wait_stopped(self.manager)
        self.assertEqual(order, ['ipc-open', 'ipc-closed', 'runtime-exited'])
        wait.assert_called_once_with(self.home)

    def test_runtime_exit_timeout_does_not_apply_accounts_or_start_rollback(self):
        folder = self.manager.root/'prepared'
        folder.mkdir(parents=True)
        (folder/'config.toml').write_text('model="new"\n')
        (folder/'auth.json').write_text('{"OPENAI_API_KEY":"fixture"}')
        self.manager.prepare = Mock(return_value=folder)
        self.manager.wait_stopped = lambda: accounts_module.Accounts.wait_stopped(self.manager)
        with patch.object(accounts_module, 'connect_stream', side_effect=OSError('closed')), \
                patch.object(desktop_runtime, 'wait_stopped', side_effect=ValueError('runtime still exiting')), \
                patch.object(self.manager, 'restore_files') as restore:
            self.manager._switch({'id': '1'*32, 'kind': 'api'}, self.app)
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertEqual(self.manager.snapshot_files(), self.original)
        self.app.stop.assert_called_once()
        self.app.start.assert_not_called()
        restore.assert_not_called()

    def test_runtime_exit_timeout_blocks_explicit_recovery_before_file_restore(self):
        backup = {'desktopExecutable': str(self.app.executable), 'files': self.original, 'activeId': None}
        accounts_module.private_json(self.manager.root/'rollback.json', backup)
        self.manager.phase('interrupted')
        self.manager.wait_stopped = lambda: accounts_module.Accounts.wait_stopped(self.manager)
        with patch.object(accounts_module, 'connect_stream', side_effect=OSError('closed')), \
                patch.object(desktop_runtime, 'wait_stopped', side_effect=ValueError('runtime still exiting')), \
                patch.object(self.manager, 'restore_files') as restore:
            self.manager.recover({'confirmed': True})
            test_accounts.wait_for_account_worker(self)
        self.assertEqual(self.manager.state['phase'], 'interrupted')
        self.assertTrue((self.manager.root/'rollback.json').exists())
        self.app.stop.assert_called_once()
        self.app.start.assert_not_called()
        restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()
