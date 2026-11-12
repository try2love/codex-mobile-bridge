"""Explicit shared Desktop initialization without starting a real App."""
import os
import threading
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import test_accounts
from bridge.features.accounts import accounts as accounts_module
from bridge.clients.codex import desktop_runtime
from bridge.clients.errors import TaskStateUnavailable, TaskAuditIncomplete


class DesktopRuntimeAccountsTests(unittest.TestCase):
    tearDown = test_accounts.AccountsTests.tearDown

    def setUp(self):
        test_accounts.AccountsTests.setUp(self)
        self.bridge.host = 'local'
        self.bridge.closed = threading.Event()
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
            'launcher_settings': patch.object(desktop_runtime, 'launcher_settings', return_value={}),
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
        def ready(app, *, native_runtime=False):
            if native_runtime:
                client = desktop_runtime.connect(self.home, self.manager.runtime)
                if client is None:
                    raise ValueError('shared runtime unavailable')
                client.close()
        self.manager.wait_ready.side_effect = ready
        self.value = {'action': 'initializeDesktopRuntime', 'confirmed': True, 'tasksConfirmed': True, 'requestId': str(uuid.uuid4())}

    def run_initialization(self):
        self.manager.control(self.value)
        test_accounts.wait_for_account_worker(self)

    def test_desktop_ipc_before_native_handshake_does_not_fail_or_restart(self):
        from bridge.clients.codex.app_server import AppServerUnavailable
        self.manager.wait_ready = MethodType(accounts_module.Accounts.wait_ready, self.manager)
        client = Mock()
        self.mocks['connect'].side_effect = [AppServerUnavailable('handshake pending'), client]
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 1]), sleep=Mock())
        with patch.object(accounts_module, 'time', clock):
            self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'complete')
        self.assertEqual(self.manager.desktop_preparation['state'], 'ready')
        self.assertEqual(self.mocks['connect'].call_count, 2)
        client.close.assert_called_once()
        self.app.start.assert_called_once()
        self.mocks['stop'].assert_called_once()
        self.assertEqual(desktop_runtime.preparation(self.home)['state'], 'complete')

    def test_native_handshake_timeout_preserves_pending_receipt_without_relaunch(self):
        from bridge.clients.codex.app_server import AppServerUnavailable
        self.manager.wait_ready = MethodType(accounts_module.Accounts.wait_ready, self.manager)
        self.mocks['connect'].side_effect = AppServerUnavailable('handshake pending')
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 36]), sleep=Mock())
        with patch.object(accounts_module, 'time', clock):
            self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertEqual(desktop_runtime.preparation(self.home)['state'], 'pending')
        self.app.start.assert_called_once()
        self.mocks['stop'].assert_called_once()
        self.manager.control(self.value)
        self.app.start.assert_called_once()

    def test_startup_reuses_shared_owner_without_task_audit_or_restart(self):
        original = dict(self.manager.state)
        self.assertFalse(self.manager.prepare_desktop_runtime(self.app))
        self.assertEqual(self.manager.desktop_preparation['state'], 'ready')
        self.mocks['connect'].return_value.close.assert_called_once()
        self.bridge.assert_desktop_idle.assert_not_called()
        self.mocks['inspect'].assert_not_called()
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()
        self.assertEqual(self.manager.state, original)

    def test_startup_cold_launch_refreshes_configuration_before_start(self):
        from bridge.clients.codex.app_server import AppServerUnavailable
        self.mocks['connect'].side_effect = [AppServerUnavailable('old launcher is unavailable'), Mock()]
        self.mocks['inspect'].side_effect = [self.stopped] * 3
        order = []
        self.mocks['configure'].side_effect = lambda *args: order.append('configure')
        self.app.start.side_effect = lambda: order.append('start')
        self.assertFalse(self.manager.prepare_desktop_runtime(self.app))
        self.assertEqual(order, ['configure', 'start'])
        self.mocks['stop'].assert_not_called()
        self.assertEqual(self.manager.desktop_preparation['state'], 'ready')
        self.bridge.assert_desktop_idle.assert_called_once_with(allow_stopped_app=True, desktop_only=True,
            desktop_executable=self.app.executable, timeout=3)

    def test_startup_busy_and_unknown_tasks_defer_without_blocking_account_state(self):
        self.mocks['connect'].return_value = None
        original = dict(self.manager.state)
        for error in (ValueError('busy'), TaskStateUnavailable('unknown'), TaskAuditIncomplete('not checked')):
            with self.subTest(error=error):
                self.mocks['inspect'].side_effect = [self.running]
                self.bridge.assert_desktop_idle.side_effect = error
                self.assertTrue(self.manager.prepare_desktop_runtime(self.app))
                self.assertEqual(self.manager.state, original)
                self.assertEqual(self.manager.desktop_preparation['state'], 'waiting')
                self.assertEqual(self.manager.desktop_preparation.get('taskStateUnavailable', False), isinstance(error, (TaskStateUnavailable, TaskAuditIncomplete)))
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()
        self.app.start.assert_not_called()

    def test_cold_start_audits_the_discovered_app_when_cli_is_installed_separately(self):
        from bridge.app.service import Bridge
        from bridge.clients.codex.ipc import IPCError
        self.manager.index['desktopExecutable'] = ''
        self.mocks['connect'].side_effect = [None, Mock()]
        self.mocks['inspect'].side_effect = [self.stopped] * 3
        self.bridge.hosts = SimpleNamespace(lifecycle_hosts=lambda: {})
        self.bridge.store = SimpleNamespace(lifecycle_threads=lambda: [])
        self.bridge.accounts = self.manager
        self.bridge.assert_desktop_idle.side_effect = lambda **kwargs: Bridge.assert_desktop_idle(self.bridge, **kwargs)
        self.app.processes.return_value = []
        missing = IPCError('Desktop is not running')
        missing.__cause__ = FileNotFoundError('desktop IPC is absent')
        with patch('bridge.app.service.DesktopIPC') as ipc, \
                patch('bridge.app.service.DesktopApp', return_value=self.app) as desktop:
            desktop.discover.return_value = ''
            ipc.return_value.connect.side_effect = missing
            self.assertFalse(self.manager.prepare_desktop_runtime(self.app))
            desktop.assert_called_once_with(self.app.executable, self.home)
            desktop.discover.assert_not_called()
        self.assertEqual(self.manager.desktop_preparation['state'], 'ready')
        self.app.processes.assert_called_once()
        self.app.start.assert_called_once()
        self.mocks['stop'].assert_not_called()

    def test_startup_never_repeats_an_uncertain_launch(self):
        self.mocks['connect'].return_value = None
        self.app.start.side_effect = ValueError('launch result unknown')
        self.assertFalse(self.manager.prepare_desktop_runtime(self.app))
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertTrue(self.manager.state['automatic'])
        self.assertFalse(self.manager.prepare_desktop_runtime(self.app))
        self.app.start.assert_called_once()
        self.mocks['stop'].assert_called_once()

    def test_startup_defers_another_gateway_lock_and_honors_cancellation(self):
        self.mocks['initialize_lock'].side_effect = desktop_runtime.DesktopRuntimeBusy('another gateway')
        self.assertTrue(self.manager.prepare_desktop_runtime(self.app))
        self.assertEqual(self.manager.desktop_preparation['state'], 'waiting')
        self.assertFalse(self.manager.prepare_desktop_runtime(self.app, cancelled=lambda: True))
        self.mocks['stop'].assert_not_called()
        self.app.start.assert_not_called()

    def test_another_gateway_cannot_replay_an_uncertain_launch(self):
        self.mocks['connect'].return_value = None
        self.app.start.side_effect = ValueError('launch result unknown')
        self.manager.prepare_desktop_runtime(self.app)
        other_bridge = SimpleNamespace(**vars(self.bridge))
        other_bridge.data_dir = self.root / 'other-gateway'
        other = accounts_module.Accounts(other_bridge)
        other.prepare_desktop_runtime(self.app)
        self.assertEqual(other.desktop_preparation['state'], 'failed')
        self.assertEqual(desktop_runtime.preparation(self.home)['state'], 'pending')
        self.app.start.assert_called_once()

    def test_a_verified_shared_owner_resolves_a_pending_launch(self):
        request_id = str(uuid.uuid4())
        desktop_runtime.record_preparation(self.home, request_id)
        self.manager.prepare_desktop_runtime(self.app)
        self.assertEqual(desktop_runtime.preparation(self.home), {
            'version': desktop_runtime.VERSION, 'home': str(self.home.resolve()),
            'requestId': request_id, 'state': 'complete'})
        self.assertEqual(self.manager.desktop_preparation['state'], 'ready')
        self.app.start.assert_not_called()

    def test_shutdown_during_configuration_does_not_launch_or_replay(self):
        self.mocks['connect'].return_value = None
        self.mocks['configure'].side_effect = lambda *args: self.bridge.closed.set()
        self.manager.prepare_desktop_runtime(self.app)
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertEqual(desktop_runtime.preparation(self.home)['state'], 'pending')
        self.bridge.closed.clear()
        self.manager.prepare_desktop_runtime(self.app)
        self.app.start.assert_not_called()
        self.mocks['configure'].assert_called_once()

    def test_disable_during_normal_stop_does_not_configure_or_launch(self):
        cancelled = threading.Event()
        self.mocks['connect'].return_value = None
        self.manager.wait_stopped.side_effect = cancelled.set
        self.manager.prepare_desktop_runtime(self.app, cancelled=cancelled.is_set)
        self.assertEqual(desktop_runtime.preparation(self.home)['state'], 'pending')
        self.mocks['configure'].assert_not_called()
        self.app.start.assert_not_called()

    def test_unsupported_shared_transport_keeps_ordinary_cold_launch(self):
        self.mocks['connect'].return_value = None
        self.mocks['launcher_settings'].side_effect = ValueError('compiled gateway required')
        self.mocks['inspect'].side_effect = [self.stopped] * 3
        self.manager.prepare_desktop_runtime(self.app)
        self.app.start.assert_called_once()
        self.mocks['configure'].assert_not_called()
        self.mocks['stop'].assert_not_called()
        self.assertEqual(self.manager.desktop_preparation['state'], 'failed')
        self.assertEqual(self.manager.state['phase'], 'complete')

    def test_unsupported_transport_never_restarts_an_app_or_ignores_shared_settings(self):
        self.mocks['connect'].return_value = None
        self.mocks['validate_runtime'].side_effect = ValueError('unsupported runtime')
        self.manager.prepare_desktop_runtime(self.app)
        self.assertEqual(self.manager.desktop_preparation['state'], 'failed')
        self.mocks['settings'].return_value = {'configured': True}
        self.mocks['inspect'].side_effect = [self.stopped] * 3
        self.manager.prepare_desktop_runtime(self.app)
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()
        self.app.start.assert_not_called()

    def test_explicit_unknown_retry_accepts_automatic_audit_evidence(self):
        self.manager.desktop_preparation = {'state': 'waiting', 'taskStateUnavailable': True}
        self.bridge.assert_desktop_idle.side_effect = TaskStateUnavailable('unknown')
        self.value['acknowledgeUnavailable'] = True
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'complete')

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
        with patch.object(accounts_module, 'sys', SimpleNamespace(platform='linux')):
            with self.assertRaisesRegex(ValueError, '不支持'):
                self.manager.control(self.value)
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def test_explicit_initialization_scans_installed_app_when_runtime_is_separate(self):
        self.manager.index['desktopExecutable'] = ''
        self.mocks['app_factory'].discover.return_value = ''
        self.mocks['app_factory'].scan.return_value = str(self.app.executable)
        self.run_initialization()
        self.mocks['app_factory'].scan.assert_called_once_with(self.manager.runtime, self.home)
        self.app.validate.assert_called_once_with(self.manager.runtime)
        self.assertEqual(self.manager.state['phase'], 'complete')

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
        self.bridge.assert_desktop_idle.assert_called_once_with(allow_stopped_app=False, desktop_only=True,
            desktop_executable=self.app.executable)
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

    def test_unavailable_task_audit_offers_explicit_normal_restart(self):
        self.bridge.assert_desktop_idle.side_effect = TaskStateUnavailable('tasks unavailable')
        self.run_initialization()
        self.assertIs(self.manager.state.get('taskStateUnavailable'), True)
        self.mocks['stop'].assert_not_called()
        self.mocks['inspect'].side_effect = [self.running, self.running, self.stopped]
        self.value = {**self.value, 'requestId': str(uuid.uuid4()), 'acknowledgeUnavailable': True}
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'complete')
        self.assertNotIn('taskStateUnavailable', self.manager.state)
        self.assertEqual(self.bridge.assert_desktop_idle.call_count, 2)
        self.mocks['stop'].assert_called_once()
        self.assertEqual(self.manager.snapshot_files(), self.original)

    def test_incomplete_task_audit_is_exposed_for_explicit_retry(self):
        self.bridge.assert_desktop_idle.side_effect = TaskAuditIncomplete('audit timeout')
        self.run_initialization()
        self.assertIs(self.manager.state.get('taskStateUnavailable'), True)
        self.assertIn('audit timeout', self.manager.state['error'])
        self.mocks['stop'].assert_not_called()

    def test_unavailable_confirmation_requires_failed_audit_and_never_skips_busy_tasks(self):
        with self.assertRaisesRegex(ValueError, '核验'):
            self.manager.control({**self.value, 'acknowledgeUnavailable': True})
        self.bridge.assert_desktop_idle.side_effect = TaskStateUnavailable('tasks unavailable')
        self.run_initialization()
        self.bridge.assert_desktop_idle.side_effect = ValueError('known busy task')
        self.value = {**self.value, 'requestId': str(uuid.uuid4()), 'acknowledgeUnavailable': True}
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertIn('known busy task', self.manager.state['error'])
        self.assertNotIn('taskStateUnavailable', self.manager.state)
        self.mocks['stop'].assert_not_called()

    def test_unavailable_confirmation_rechecks_pending_operations_after_audit(self):
        self.bridge.assert_desktop_idle.side_effect = TaskStateUnavailable('tasks unavailable')
        self.run_initialization()
        def unavailable_with_pending(**kwargs):
            self.bridge.submissions['chat:pending'] = {'status': 'unknown', 'title': 'Pending chat'}
            raise TaskStateUnavailable('tasks unavailable')
        self.bridge.assert_desktop_idle.side_effect = unavailable_with_pending
        self.value = {**self.value, 'requestId': str(uuid.uuid4()), 'acknowledgeUnavailable': True}
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'failed')
        self.assertIn('Pending chat', self.manager.state['error'])
        self.mocks['stop'].assert_not_called()

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

    def test_unverified_windows_launcher_keeps_app_running_before_worker(self):
        self.mocks['launcher_settings'].side_effect = ValueError('package identity unavailable')
        with self.assertRaisesRegex(ValueError, 'package identity'):
            self.manager.control(self.value)
        self.assertIsNone(self.manager.thread)
        self.mocks['stop'].assert_not_called()
        self.app.start.assert_not_called()

    def test_windows_initialization_uses_native_birth_and_original_home_checks(self):
        self.mocks['identity'].return_value = {'pid': 41, 'image': str(self.app.executable), 'created': 12345, 'session': 1, 'parent': 10}
        with patch.object(accounts_module, 'sys', SimpleNamespace(platform='win32')), \
             patch('bridge.platforms.windows.codex_daemon_env.read_process_environment', return_value=({'CODEX_HOME': str(self.home)}, None)) as environment, \
             patch('bridge.platforms.windows.desktop.commands', return_value={41: str(self.app.executable)}):
            self.run_initialization()
            self.assertEqual(self.manager.state['phase'], 'complete')
            self.assertTrue(self.manager.desktop_runtime_status()['supported'])
            self.assertEqual(environment.call_args.args[1], {'processStartTime': '12345'})
            self.assertIn('USERPROFILE', environment.call_args.args[3])
        self.mocks['stop'].assert_called_once()
        self.mocks['launcher_settings'].assert_called_once_with(self.app.executable)

    def test_pending_gateway_submission_blocks_initialization_before_worker(self):
        self.bridge.submissions['chat:submission'] = {'status': 'unknown', 'title': 'Pending chat'}
        with self.assertRaisesRegex(ValueError, 'Pending chat'):
            self.manager.control(self.value)
        self.assertIsNone(self.manager.thread)
        self.mocks['stop'].assert_not_called()
        self.mocks['configure'].assert_not_called()

    def native_cli(self, bridge=None):
        bridge = bridge or self.bridge
        identifier = 'native-cli-fixture'
        process = Mock(poll=Mock(return_value=None))
        cli = bridge.cli_sessions
        cli.home = self.home
        cli.alias = None
        cli.client.process = process
        cli.client.server_info = {'codexHome': str(self.home)}
        cli.joined[identifier] = process
        cli.metadata = {identifier: {'connectionToken': 'native-generation'}}
        session = SimpleNamespace(runtime='cli', owner='cli:' + identifier, connected=True,
                                  desktop_owner_probe='absent',
                                  view=lambda: {'connected': True, 'status': 'active', 'title': 'Native CLI'})
        bridge.live[identifier] = session
        return session

    def test_initialization_preserves_independent_native_cli_but_account_switch_stays_blocked(self):
        self.native_cli()
        self.bridge.cli_sessions.daemon_may_be_running.return_value = True
        self.run_initialization()
        self.assertEqual(self.manager.state['phase'], 'complete')
        self.assertEqual(len(self.manager.blockers()), 1)
        with self.assertRaisesRegex(ValueError, 'Native CLI'):
            self.manager.idle()
        self.bridge.cli_sessions.joined.clear()
        self.assertIsNone(self.manager._native_cli_connection(self.bridge, 'native-cli-fixture', self.bridge.live['native-cli-fixture']))

    def test_initialization_never_exempts_cli_labels_with_unknown_or_desktop_ownership(self):
        session = self.native_cli()
        cli = self.bridge.cli_sessions
        original_process = cli.client.process
        for changes in ({'owner': None}, {'owner': 'desktop-owner'}, {'runtime': 'desktop'},
                        {'process': Mock(poll=Mock(return_value=None))},
                        {'home': str(self.root/'other-home')}):
            with self.subTest(changes=list(changes)):
                session.runtime, session.owner, session.desktop_owner_probe = 'cli', 'cli:native-cli-fixture', 'absent'
                cli.client.process = original_process
                cli.client.server_info = {'codexHome': str(self.home)}
                if 'process' in changes:
                    cli.client.process = changes['process']
                elif 'home' in changes:
                    cli.client.server_info = {'codexHome': changes['home']}
                else:
                    for key, value in changes.items():
                        setattr(session, key, value)
                self.assertIsNone(self.manager._native_cli_connection(self.bridge, 'native-cli-fixture', session))
        for probe in (None, 'unknown', 'absent', 'found'):
            session.desktop_owner_probe = probe
            with self.assertRaises(ValueError):
                self.manager.idle()
        self.mocks['stop'].assert_not_called()

    def test_unknown_submission_is_retained_even_with_independent_native_cli(self):
        self.native_cli()
        self.bridge.submissions['native-cli-fixture:uncertain'] = {'status': 'unknown'}
        self.assertEqual([row['reason'] for row in self.manager.blockers()], ['running', 'unknown'])

    def test_remote_native_cli_is_independent_but_remote_desktop_owner_is_not(self):
        cli = SimpleNamespace(lock=threading.RLock(), client=SimpleNamespace(), joined={})
        remote = SimpleNamespace(host='remote:fixture', codex_home=self.home, lock=threading.RLock(),
                                 live={}, submissions={}, cli_sessions=cli)
        session = self.native_cli(remote)
        cli.alias, cli.remote_home = 'fixture', Path('/remote/profile')
        cli.client.server_info = {'codexHome': '/remote/profile'}
        self.bridge.remote_bridges['remote:fixture'] = remote
        self.assertIsNotNone(self.manager._native_cli_connection(remote, 'native-cli-fixture', session))
        self.assertEqual(len(self.manager.blockers()), 1)
        session.runtime, session.owner = 'desktop', 'remote-app-owner'
        self.assertIsNone(self.manager._native_cli_connection(remote, 'native-cli-fixture', session))

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
        self.bridge.assert_desktop_idle.assert_called_once_with(allow_stopped_app=True, desktop_only=True,
            desktop_executable=self.app.executable)
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

    @unittest.skipIf(os.name == 'nt', 'macOS shared runtime uses POSIX file locks')
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
