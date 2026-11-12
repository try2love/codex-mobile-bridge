"""Shared native writer identity and explicit-mutation routing contracts."""
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex import desktop_runtime as runtime
from bridge.clients.codex.app_server import AppServerUnavailable, AppServerUnknownResult, AppServerRequestNotSent
from bridge.features.sessions.create import CreationError, CreationUnavailable
from bridge.app.service import Bridge

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == 'nt', 'POSIX private runtime directory and sockets')
class DesktopRuntimeTests(unittest.TestCase):
    def test_windows_launcher_requires_frozen_entry_and_verified_msix_family(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            root = Path(folder); application = root/'app'/'Codex.exe'
            (root/'AppxManifest.xml').write_text('<fixture/>')
            with patch.object(runtime, 'sys', SimpleNamespace(platform='win32', frozen=True, executable=str(root/'gateway.exe'))), \
                 patch('bridge.platforms.windows.discovery_ext.application_user_model_id', return_value='OpenAI.Codex_fixture!App') as identity, \
                 patch('bridge.platforms.windows.discovery_ext.application_execution_alias', return_value=root/'alias.exe') as alias:
                self.assertEqual(runtime.launcher_settings(application), {'launcher': str((root/'gateway.exe').resolve()), 'sandboxFamily': 'OpenAI.Codex_fixture'})
                alias.return_value = None
                with self.assertRaisesRegex(ValueError, '启动别名'): runtime.launcher_settings(application)
                identity.return_value = ''
                with self.assertRaisesRegex(ValueError, '安装包身份'): runtime.launcher_settings(application)
            with patch.object(runtime, 'sys', SimpleNamespace(platform='win32', frozen=False)):
                with self.assertRaisesRegex(ValueError, '已编译'): runtime.launcher_settings(application)

    def test_windows_exited_generation_does_not_require_a_live_image_query(self):
        with patch.object(runtime, 'sys', SimpleNamespace(platform='win32')), \
             patch('bridge.platforms.windows.codex_daemon_env.recorded_process_absent', return_value=True) as absent, \
             patch.object(runtime, 'process_identity', side_effect=OSError('image already gone')) as identify:
            self.assertFalse(runtime.runtime_alive({'pid': 41, 'created': 1234}))
            absent.assert_called_once_with(41, {'processStartTime': '1234'})
            identify.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = runtime.private_directory(runtime.directory(self.home))
        self.cli = str(self.home / 'native-codex')
        self.application = str(self.home / 'native-app')
        self.config = {'version': 1, 'home': str(self.home), 'cli': self.cli, 'application': self.application}
        runtime._write(self.root / 'settings.json', json.dumps(self.config))

    def test_only_primary_server_transport_is_changed(self):
        arguments = ['-c', 'features.code_mode_host=true', 'app-server', '--analytics-default-enabled',
                     '-c', 'plugins.app.enabled=true', '--listen', 'stdio://']
        result = runtime.server_arguments(arguments, '/private/control.sock')
        self.assertEqual(result, arguments[:-2] + ['--listen', 'unix:///private/control.sock'])
        for arguments in (['--version'], ['app-server', '--print-startup-config'],
                          ['app-server', 'proxy', '--sock', '/elsewhere'], ['exec-server'],
                          ['app-server', '--listen', 'ws://localhost:1234'], ['app-server', '--new-option']):
            self.assertIsNone(runtime.server_arguments(arguments, '/private/control.sock'))

    def test_settings_reject_public_permissions_and_symlink(self):
        path = self.root / 'settings.json'
        path.chmod(0o644)
        with self.assertRaises(ValueError): runtime.settings(self.home)
        path.unlink(); path.symlink_to(self.home / 'missing')
        with self.assertRaises(ValueError): runtime.settings(self.home)

    def test_capability_probe_only_runs_native_help_and_rejects_missing_transport(self):
        with patch.object(runtime.subprocess, 'run', side_effect=[
                SimpleNamespace(stdout='--listen unix://PATH'), SimpleNamespace(stdout='--sock SOCKET')]) as run:
            runtime.validate_runtime(self.cli)
            self.assertEqual([call.args[0] for call in run.call_args_list],
                             [[self.cli, 'app-server', '--help'], [self.cli, 'app-server', 'proxy', '--help']])
        with patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(stdout='--stdio')):
            with self.assertRaises(ValueError): runtime.validate_runtime(self.cli)
        with patch.object(runtime.subprocess, 'run', side_effect=subprocess.TimeoutExpired('help', 10)):
            with self.assertRaisesRegex(ValueError, '原 App 保持不变'): runtime.validate_runtime(self.cli)

    @unittest.skipIf(os.name == 'nt', 'POSIX private lock')
    def test_initializers_and_runtime_generations_have_distinct_exclusive_locks(self):
        with runtime.initialization_lock(self.home):
            with self.assertRaisesRegex(ValueError, '共享接入'):
                with runtime.initialization_lock(self.home): pass
            with runtime.exclusive_lock(self.home, 'runtime'): pass

    def test_normal_exit_waits_for_adapter_cleanup_before_restart(self):
        entered = threading.Event()
        completed = threading.Event()
        errors = []
        def wait():
            entered.set()
            try:
                runtime.wait_stopped(self.home, timeout=2)
                completed.set()
            except Exception as exc:
                errors.append(exc)
        with runtime.exclusive_lock(self.home, 'runtime'):
            thread = threading.Thread(target=wait)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(completed.wait(.05))
        thread.join(3)
        self.assertFalse(errors)
        self.assertTrue(completed.is_set())

    def test_exit_wait_is_bounded_and_does_not_clear_unknown_or_live_writer(self):
        with runtime.exclusive_lock(self.home, 'runtime'):
            with self.assertRaisesRegex(ValueError, '退出中'):
                runtime.wait_stopped(self.home, timeout=.01)
        active = self.root / 'active.json'
        runtime._write(active, json.dumps({'ready': False, 'runtime': None}))
        with self.assertRaisesRegex(ValueError, '启动结果待核对'):
            runtime.wait_stopped(self.home)
        self.assertTrue(active.exists())
        runtime._write(active, json.dumps({'ready': True, 'runtime': {'pid': 3}}))
        with patch.object(runtime, 'runtime_alive', return_value=True):
            with self.assertRaisesRegex(ValueError, '尚未退出'):
                runtime.wait_stopped(self.home)
        self.assertTrue(active.exists())

    def test_desktop_start_waits_only_for_configured_shared_runtime(self):
        from bridge.clients import desktop_app
        app = desktop_app.DesktopApp(self.application, self.home)
        native = Mock()
        with patch.object(desktop_app.sys, 'platform', 'darwin'), \
             patch.object(desktop_app, '_native', return_value=native), \
             patch.object(runtime, 'launch_environment', return_value={}) as environment, \
             patch.object(runtime, 'wait_stopped') as wait:
            app.start()
            wait.assert_not_called()
            native.start.reset_mock()
            environment.return_value = {'CODEX_CLI_PATH': '/private/adapter'}
            wait.side_effect = ValueError('still exiting')
            with self.assertRaisesRegex(ValueError, 'still exiting'):
                app.start()
            wait.assert_called_once_with(self.home)
            native.start.assert_not_called()

    def setup_active(self):
        identities = {1: {'pid': 1, 'parent': 0, 'image': self.application, 'birth': [11, 0]},
                      2: {'pid': 2, 'parent': 1, 'image': '/gateway', 'birth': [12, 0]},
                      3: {'pid': 3, 'parent': 2, 'image': self.cli, 'birth': [13, 0]}}
        path = self.home / 'test.sock'
        server = socket.socket(socket.AF_UNIX); server.bind(str(path)); path.chmod(0o600)
        self.addCleanup(server.close)
        record = {'version': 1, 'ready': True, 'home': str(self.home), 'cli': self.cli, 'app': identities[1],
                  'adapter': identities[2], 'runtime': identities[3], 'socket': str(path), 'generation': str(uuid.uuid4())}
        runtime._write(self.root / 'active.json', json.dumps(record))
        return identities, path

    def test_connection_checks_owner_generation_before_and_after_handshake(self):
        identities, path = self.setup_active()
        client = Mock(); client.connect.return_value = {'codexHome': str(self.home)}
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, 'socket_path', return_value=path), \
             patch.object(runtime, 'process_identity', side_effect=lambda pid: dict(identities[pid])), \
             patch.object(runtime, 'CodexAppServer', return_value=client):
            self.assertIs(runtime.connect(self.home, self.cli), client)
            client.close.assert_not_called()
            def swapped():
                identities[3] = {**identities[3], 'birth': [99, 0]}
                return {'codexHome': str(self.home)}
            client.connect.side_effect = swapped
            with self.assertRaises(AppServerUnavailable): runtime.connect(self.home, self.cli)
            client.close.assert_called_once()

    def test_configured_but_offline_does_not_mean_unconfigured(self):
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, 'CodexAppServer') as client:
            with self.assertRaises(AppServerUnavailable): runtime.connect(self.home, self.cli)
            client.assert_not_called()
            (self.root / 'settings.json').unlink()
            self.assertIsNone(runtime.connect(self.home, self.cli))

    def test_native_protected_socket_alias_is_accepted_without_chmod(self):
        identities, path = self.setup_active()
        path.unlink()
        protected = self.home / 'daemon'; protected.mkdir(mode=0o700)
        digest = hashlib.sha256(os.fsencode(path.parent.resolve() / path.name)).hexdigest()
        target = protected / digest
        # Long worktree paths can exceed the Unix address limit. Bind a shorter
        # filename, then move the socket inode to the native hashed location.
        sock = socket.socket(socket.AF_UNIX); self.addCleanup(sock.close)
        short = self.home / 's'; sock.bind(str(short)); short.chmod(0o600); short.rename(target)
        path.symlink_to(target)
        client = Mock(); client.connect.return_value = {'codexHome': str(self.home)}
        with patch.object(runtime, '_daemon_directory', return_value=protected), \
             patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, 'socket_path', return_value=path), \
             patch.object(runtime, 'process_identity', side_effect=lambda pid: dict(identities[pid])), \
             patch.object(runtime, 'CodexAppServer', return_value=client), \
             patch.object(runtime.os, 'chmod', wraps=os.chmod) as chmod:
            self.assertIs(runtime.connect(self.home, self.cli), client)
            chmod.assert_not_called()
            target.chmod(0o666)
            with self.assertRaises(AppServerUnavailable): runtime.connect(self.home, self.cli)
            target.chmod(0o600); protected.chmod(0o755)
            with self.assertRaises(AppServerUnavailable): runtime.connect(self.home, self.cli)
            protected.chmod(0o700)
            path.unlink(); path.symlink_to(self.home / 's')
            with self.assertRaises(AppServerUnavailable): runtime.connect(self.home, self.cli)

    def test_socket_replacement_during_handshake_is_rejected(self):
        identities, path = self.setup_active()
        replacement = self.home / 'replacement'
        sock = socket.socket(socket.AF_UNIX); self.addCleanup(sock.close)
        sock.bind(str(replacement)); replacement.chmod(0o600)
        client = Mock()
        def replace():
            os.replace(replacement, path)
            return {'codexHome': str(self.home)}
        client.connect.side_effect = replace
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, 'socket_path', return_value=path), \
             patch.object(runtime, 'process_identity', side_effect=lambda pid: dict(identities[pid])), \
             patch.object(runtime, 'CodexAppServer', return_value=client):
            with self.assertRaises(AppServerUnavailable): runtime.connect(self.home, self.cli)
        client.close.assert_called_once()

    def test_starting_runtime_cannot_accept_a_metadata_write(self):
        self.setup_active()
        path = self.root / 'active.json'
        runtime._write(path, json.dumps({**runtime._read(path), 'ready': False}))
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, 'CodexAppServer') as client:
            with self.assertRaises(AppServerUnavailable): runtime.connect(self.home, self.cli)
            client.assert_not_called()

    def test_reparented_native_child_still_blocks_another_writer(self):
        identity = {'pid': 123, 'parent': 122, 'birth': [1, 2], 'started': [3, 4],
                    'image': '/native/CodexCLI.app/codex', 'uid': 501, 'ruid': 501}
        with patch.object(runtime, 'process_identity', return_value={**identity, 'parent': 1}):
            self.assertTrue(runtime.runtime_alive(identity))
        with patch.object(runtime, 'process_identity', return_value={**identity, 'birth': [9, 10]}):
            self.assertFalse(runtime.runtime_alive(identity))

    def test_starting_reservation_blocks_unknown_or_execed_child(self):
        with self.assertRaisesRegex(ValueError, '启动结果待核对'):
            runtime.require_inactive({'ready': False, 'runtime': None})
        identity = {'pid': 123, 'birth': [1, 2], 'started': [3, 4],
                    'image': '/bin/sh', 'uid': 501, 'ruid': 501}
        record = {'ready': False, 'runtime': identity}
        with patch.object(runtime, 'process_identity', return_value={**identity, 'image': '/native/codex', 'birth': [1, 3]}):
            with self.assertRaisesRegex(ValueError, '尚未退出'): runtime.require_inactive(record)
        with patch.object(runtime, 'process_identity', side_effect=ProcessLookupError):
            runtime.require_inactive(record)


class MetadataRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.bridge = SimpleNamespace(host='local', codex_home=Path(temporary.name),
                                      catalog_reader=SimpleNamespace(executable='/fixture-cli'))

    def call(self, operation, **options):
        return Bridge._desktop_metadata_operation(self.bridge, '/project', operation, 'unknown write', **options)

    def test_shared_writer_is_closed_only_as_a_connection_never_replaced(self):
        client = Mock(); client.request.return_value = {'id': 'child'}
        with patch.object(runtime, 'connect', return_value=client), \
             patch('bridge.features.sessions.create._runtime_operation') as spawn:
            self.assertEqual(self.call(lambda request: request('thread/fork', {'threadId': 'parent'})), {'id': 'child'})
            client.close.assert_called_once(); spawn.assert_not_called()

    def test_missing_owner_is_not_sent_unknown_mutation_is_never_replayed(self):
        with patch.object(runtime, 'connect', side_effect=AppServerUnavailable('unavailable')), \
             patch('bridge.features.sessions.create._runtime_operation') as spawn:
            operation = Mock()
            with self.assertRaises(CreationUnavailable): self.call(operation)
            operation.assert_not_called(); spawn.assert_not_called()
        client = Mock(); client.request.side_effect = AppServerUnknownResult('lost')
        with patch.object(runtime, 'connect', return_value=client), \
             patch('bridge.features.sessions.create._runtime_operation') as spawn:
            with self.assertRaises(CreationError): self.call(lambda request: request('thread/fork'))
            client.request.assert_called_once(); client.close.assert_called_once(); spawn.assert_not_called()

    def test_independent_home_never_uses_default_app_runtime(self):
        self.bridge.host = 'codex:independent'
        with patch.object(runtime, 'connect') as shared, \
             patch('bridge.features.sessions.create._runtime_operation', return_value='native') as fallback:
            self.assertEqual(self.call(Mock()), 'native')
            shared.assert_not_called()
            self.assertTrue(fallback.call_args.kwargs['isolated_home'])

    def test_explicit_not_sent_allows_only_first_metadata_request_retry(self):
        client = Mock()
        with patch.object(runtime, 'connect', return_value=client):
            client.request.side_effect = AppServerRequestNotSent('pipe unavailable')
            with self.assertRaises(CreationUnavailable): self.call(lambda request: request('thread/fork', {}))
            client.request.side_effect = [{'thread': {'id': 'child'}}, AppServerRequestNotSent('pipe unavailable')]
            def operation(request):
                request('thread/fork', {})
                request('thread/name/set', {})
            with self.assertRaises(CreationError) as failure: self.call(operation)
            self.assertNotIsInstance(failure.exception, CreationUnavailable)

    def test_uninitialized_mac_desktop_refuses_before_starting_another_writer(self):
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'initialization_lock', return_value=nullcontext()), \
             patch.object(runtime, 'connect', return_value=None), \
             patch('bridge.features.sessions.create._runtime_operation') as spawn:
            operation = Mock()
            with self.assertRaisesRegex(CreationUnavailable, '自动完成'):
                self.call(operation, require_shared=True)
            operation.assert_not_called()
            spawn.assert_not_called()

    def test_unconfigured_other_platform_retains_existing_metadata_runtime(self):
        with patch.object(runtime.sys, 'platform', 'linux'), \
             patch.object(runtime, 'connect', return_value=None), \
             patch('bridge.features.sessions.create._runtime_operation', return_value='native') as spawn:
            self.assertEqual(self.call(Mock(), require_shared=True), 'native')
            spawn.assert_called_once()

    def test_unconfigured_mac_history_metadata_keeps_existing_runtime_path(self):
        with patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'initialization_lock', return_value=nullcontext()), \
             patch.object(runtime, 'connect', return_value=None), \
             patch('bridge.features.sessions.create._runtime_operation', return_value='native') as spawn:
            self.assertEqual(self.call(Mock()), 'native')
            spawn.assert_called_once()

    def test_uninitialized_windows_desktop_refuses_before_starting_another_writer(self):
        with patch.object(runtime.sys, 'platform', 'win32'), \
             patch.object(runtime, 'initialization_lock', return_value=nullcontext()), \
             patch.object(runtime, 'connect', return_value=None), \
             patch('bridge.features.sessions.create._runtime_operation') as spawn:
            with self.assertRaisesRegex(CreationUnavailable, '自动完成'):
                self.call(Mock(), require_shared=True)
            spawn.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX shared-runtime lock')
    def test_another_gateway_cannot_initialize_during_a_metadata_write(self):
        client = Mock()
        def operation(request):
            with self.assertRaises(ValueError):
                with runtime.initialization_lock(self.bridge.codex_home): pass
            return 'written'
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, 'connect', return_value=client):
            self.assertEqual(self.call(operation), 'written')
        with runtime.initialization_lock(self.bridge.codex_home), patch.object(runtime.sys, 'platform', 'darwin'), \
             patch.object(runtime, 'connect') as connect:
            with self.assertRaises(CreationUnavailable): self.call(Mock())
            connect.assert_not_called()
