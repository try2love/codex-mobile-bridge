"""Offline SSH must not serialize unrelated local operations."""
from concurrent.futures import Future
from types import SimpleNamespace
import threading
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

from bridge.app.service import Bridge, LiveSession
from bridge.features.network import retry as retry_module
from bridge.clients.codex.remote import RemoteStore, RemoteUnavailable, ssh_read, ssh_connection, retry_ssh
from bridge.features.accounts.operation_gate import OperationGate
from bridge.features.network.retry import ConnectionRetry
from bridge.features.network.stdio import run_with_input


class SshIsolationTests(unittest.TestCase):
    def test_large_input_times_out_when_child_never_reads_stdin(self):
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_with_input([sys.executable, '-c', 'import time; time.sleep(30)'],
                           'x' * 1024 * 1024, timeout=.3, capture_output=True)
        self.assertLess(time.monotonic() - start, 5)

    def test_file_input_preserves_utf8_and_binary_output(self):
        value = '项目 / 空格\n'
        result = run_with_input([sys.executable, '-c',
            'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'], value,
            timeout=5, capture_output=True)
        self.assertEqual(result.stdout, value.encode('utf-8'))

    def setUp(self):
        self.connections = patch.dict(retry_module._connections, {}, clear=True)
        self.connections.start()
        self.addCleanup(self.connections.stop)

    def test_remote_activation_does_not_hold_local_account_gate(self):
        entered, release = threading.Event(), threading.Event()
        accounts = SimpleNamespace(gate=OperationGate(), check_ready=lambda: None)
        local, remote = Bridge.__new__(Bridge), Bridge.__new__(Bridge)
        local.host, remote.host = 'local', 'remote:fixture'
        local.accounts = remote.accounts = accounts
        local.released_cli = Mock()
        session = LiveSession('local-thread')
        session.connected = True
        local.ipc = Mock()
        local.ipc.endpoint_changed.return_value = False
        local.session = lambda *args, **kwargs: session

        def offline(*args, **kwargs):
            entered.set()
            release.wait(5)
            raise RemoteUnavailable('offline')

        remote.session = offline
        result = Future()
        def activate_remote():
            try:
                remote.activate('remote-thread')
            except RemoteUnavailable:
                pass
        def activate_local():
            try:
                result.set_result(local.activate('local-thread'))
            except BaseException as error:
                result.set_exception(error)
        worker = threading.Thread(target=activate_remote)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            reader = threading.Thread(target=activate_local)
            reader.start()
            self.assertTrue(result.result(timeout=.5).connected)
        finally:
            release.set()
            worker.join(2)
            if 'reader' in locals():
                reader.join(2)

    def test_offline_store_polling_does_not_repeat_transport(self):
        store = RemoteStore('fixture-offline')
        with patch('bridge.clients.codex.remote.subprocess.run', return_value=SimpleNamespace(returncode=255)) as read:
            for _ in range(5):
                with self.assertRaises(RemoteUnavailable):
                    store.list()
            self.assertEqual(read.call_count, 1)

    def test_first_slow_remote_scan_returns_without_holding_local_list(self):
        entered, release = threading.Event(), threading.Event()
        store = RemoteStore('fixture-slow')
        def read(*args, **kwargs):
            entered.set(); release.wait(3)
            return []
        try:
            with patch.object(store, 'call', side_effect=read):
                with self.assertRaises(RemoteUnavailable):
                    store.poll('list', {})
                self.assertTrue(entered.wait(1))
                for _ in range(10):
                    with self.assertRaises(RemoteUnavailable):
                        store.poll('list', {})
                self.assertEqual(store.call.call_count, 1)
                # No transport or store-wide request lock is acquired by polling.
                self.assertTrue(store.poll_lock.acquire(blocking=False))
                store.poll_lock.release()
                release.set()
        finally:
            release.set()

    def test_project_listing_returns_local_rows_while_ssh_scan_is_pending(self):
        entered, release = threading.Event(), threading.Event()
        remote = RemoteStore('fixture-project-list')
        bridge = Bridge.__new__(Bridge)
        bridge.host = 'local'
        bridge.hosts = SimpleNamespace(hosts=lambda: {'ssh': {'alias': remote.alias}}, projects=lambda: [])
        bridge.directory_projects = SimpleNamespace(list=lambda hosts: [])
        bridge.store = SimpleNamespace(list=lambda **kw: [{'origin': 'cli', 'cwd': '/fixture/local'}])
        bridge.for_host = lambda host: SimpleNamespace(store=remote)
        def read(*args, **kwargs):
            entered.set(); release.wait(3)
            return []
        try:
            with patch.object(remote, 'call', side_effect=read):
                start = time.monotonic()
                rows = bridge.projects()
                self.assertLess(time.monotonic() - start, .5)
                self.assertEqual([row['host'] for row in rows], ['local'])
                self.assertTrue(entered.wait(1))
        finally:
            release.set()

    def test_selected_remote_project_inventory_waits_only_for_that_host(self):
        bridge = Bridge.__new__(Bridge)
        bridge.host = 'local'
        bridge.hosts = SimpleNamespace(hosts=lambda: {'ssh': {}, 'other': {}}, projects=lambda: [])
        bridge.directory_projects = SimpleNamespace(list=lambda hosts: [])
        bridge.store = SimpleNamespace(list=Mock(side_effect=AssertionError('local scan')))
        remote = SimpleNamespace(list=Mock(return_value=[{'origin': 'cli', 'cwd': '/fixture/remote'}]))
        bridge.for_host = Mock(return_value=SimpleNamespace(store=remote))
        rows = bridge.projects(host='ssh')
        self.assertEqual([(row['host'], row['cwd']) for row in rows], [('ssh', '/fixture/remote')])
        bridge.for_host.assert_called_once_with('ssh')
        remote.list.assert_called_once_with(limit=500)
        with self.assertRaisesRegex(ValueError, '项目环境不存在'):
            bridge.projects(host='unknown')

    def test_manual_retry_bypasses_backoff_and_coalesces(self):
        alias = 'fixture-manual'
        connection = ssh_connection(alias)
        with patch('bridge.clients.codex.remote.subprocess.run', return_value=SimpleNamespace(returncode=255)):
            with self.assertRaises(RemoteUnavailable):
                ssh_read(alias, 'read')
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def probe(*args, **kwargs):
            entered.set(); release.wait(3)
            return {'available': True}
        original = connection._reachable
        def reachable(generation):
            original(generation); finished.set()
        try:
            with patch('bridge.clients.codex.remote._ssh_read', side_effect=probe) as read, patch.object(connection, '_reachable', side_effect=reachable):
                retry_ssh(alias)
                self.assertTrue(entered.wait(1))
                self.assertEqual(connection.status()['state'], 'checking')
                retry_ssh(alias)
                self.assertEqual(read.call_count, 1)
                release.set();self.assertTrue(finished.wait(1))
                self.assertEqual(connection.status()['failures'], 0)
        finally:
            release.set()

    def test_one_host_failure_does_not_cool_down_another(self):
        with patch('bridge.clients.codex.remote.subprocess.run', side_effect=[
                SimpleNamespace(returncode=255), SimpleNamespace(returncode=0, stdout='[]')]) as read:
            with self.assertRaises(RemoteUnavailable):
                ssh_read('offline', 'read')
            self.assertEqual(ssh_read('online', 'read'), [])
            self.assertEqual(read.call_count, 2)

    def test_native_script_error_does_not_mark_ssh_offline(self):
        with patch('bridge.clients.codex.remote.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(RemoteUnavailable):
                ssh_read('script-error', 'read')
        self.assertEqual(ssh_connection('script-error').status()['failures'], 0)

    def test_codex_and_claude_reads_share_host_backoff(self):
        from bridge.clients.claude.remote import _remote
        from bridge.clients.errors import BridgeUnavailable
        with patch('bridge.clients.codex.remote.subprocess.run', return_value=SimpleNamespace(returncode=255)) as read:
            with self.assertRaises(RemoteUnavailable):
                ssh_read('shared-offline', 'read')
            with self.assertRaises(BridgeUnavailable):
                _remote('shared-offline', {'action': 'list'})
            self.assertEqual(read.call_count, 1)

    def test_disconnected_local_desktop_keeps_reading_latest_saved_history(self):
        bridge = Bridge.__new__(Bridge)
        bridge.store = SimpleNamespace()
        bridge.closed = threading.Event()
        bridge.submit_lock = threading.Lock()
        bridge.submissions = {}
        session = LiveSession('saved-thread')
        with session.condition:
            session.set_history({'id': session.id, 'title': 'Before', 'turns': []})
        bridge._saved_history = Mock(return_value={'id': session.id, 'title': 'After', 'turns': []})
        bridge._refresh_saved_async(session)
        with session.condition:
            self.assertTrue(session.condition.wait_for(lambda: session.view()['title'] == 'After', timeout=.5))
        self.assertFalse(session.connected)
        bridge._saved_history.assert_called_once_with(session)


class ConnectionRetryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.retry = ConnectionRetry(RemoteUnavailable, OSError, clock=lambda: self.now, jitter=lambda a,b: 1)

    def fail_transport(self):
        with self.assertRaises(OSError), self.retry.attempt():
            raise OSError('offline')

    def test_exponential_backoff_cap_and_success_reset(self):
        for seconds in (30, 60, 120, 240, 480, 600, 600):
            self.fail_transport()
            self.assertEqual(self.retry.status()['retryAfterSeconds'], seconds)
            with self.assertRaises(RemoteUnavailable), self.retry.attempt():
                self.fail('transport must not run during cooldown')
            self.now += seconds
        with self.retry.attempt():
            pass
        self.assertEqual(self.retry.status()['state'], 'online')
        self.fail_transport()
        self.assertEqual(self.retry.status()['retryAfterSeconds'], 30)

    def test_due_probe_is_single_flight_across_readers(self):
        self.fail_transport();self.now += 30
        with self.retry.attempt():
            with self.assertRaises(RemoteUnavailable), self.retry.attempt():
                self.fail('second transport probe must not run')

    def test_failure_ignores_old_concurrent_success(self):
        slow = self.retry.attempt();slow.__enter__()
        self.fail_transport()
        slow.__exit__(None, None, None)
        self.assertEqual(self.retry.status()['state'], 'offline')

    def test_manual_success_ignores_an_older_inflight_failure(self):
        old = self.retry.attempt();old.__enter__()
        with self.retry.attempt(manual=True):
            pass
        error = OSError('old failure')
        old.__exit__(type(error), error, error.__traceback__)
        self.assertEqual(self.retry.status()['state'], 'online')


class OperationGateTests(unittest.TestCase):
    def test_timed_exclusive_timeout_leaves_host_and_later_retry_intact(self):
        gate = OperationGate()
        entered, release = threading.Event(), threading.Event()
        def operation():
            with gate.host('local'):
                entered.set()
                release.wait(2)
        worker = threading.Thread(target=operation)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.assertFalse(gate.acquire(timeout=.02))
            self.assertLess(time.monotonic() - started, .5)
            self.assertIsNone(gate.exclusive)
            self.assertEqual(gate.depth, 0)
            self.assertIn('local', gate.active)
        finally:
            release.set()
            worker.join(1)
        self.assertTrue(gate.acquire(timeout=.02))
        try:
            self.assertTrue(gate.acquire(timeout=0))
            gate.release()
            with gate.host('local'):
                self.assertEqual(gate.depth, 1)
        finally:
            gate.release()
        self.assertIsNone(gate.exclusive)
        self.assertFalse(gate.active)

    def test_global_mutation_waits_for_remote_then_excludes_all_hosts(self):
        gate = OperationGate()
        entered, release, requested, exclusive = [threading.Event() for _ in range(4)]
        def remote():
            with gate.host('remote'):
                entered.set();release.wait(3)
        def change():
            requested.set()
            with gate:
                with gate:
                    exclusive.set()
        reader = threading.Thread(target=remote);writer = threading.Thread(target=change)
        reader.start()
        try:
            self.assertTrue(entered.wait(1));writer.start();self.assertTrue(requested.wait(1))
            self.assertFalse(exclusive.wait(.05))
            with gate.host('local'):
                with gate.host('local'):
                    self.assertFalse(exclusive.is_set())
            release.set();self.assertTrue(exclusive.wait(1))
        finally:
            release.set();reader.join(2);writer.join(2)

    def test_exclusive_account_change_blocks_new_host_operation(self):
        gate=OperationGate();entered=threading.Event()
        def operate():
            with gate.host('remote'):entered.set()
        with gate:
            worker=threading.Thread(target=operate);worker.start()
            self.assertFalse(entered.wait(.05))
        worker.join(1);self.assertTrue(entered.is_set())
