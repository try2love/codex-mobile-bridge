"""Cold local CLI continuation uses an installed executable without an App."""
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.clients.codex.ipc import IPCError
from tests.test_cli_bridge_integration import CliBridgeFixture
from tests.test_codex_cli_session import FakeClient


class InstalledLocalCliTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        self.bridge.close()
        self.installed = self.home / 'installed-bin' / 'codex'
        self.installed.parent.mkdir()
        self.installed.write_text('#!/bin/sh\nexit 0\n')
        self.installed.chmod(0o700)
        self.path_cli = patch('bridge.clients.codex.cli_session.shutil.which', return_value=str(self.installed))
        self.path_cli.start()
        self.addCleanup(self.path_cli.stop)
        self.bridge = Bridge(self.home, self.home / 'bridge-data')
        self.bridge.ipc.connect = Mock(side_effect=AssertionError('CLI must not connect App'))
        self.native = self.bridge.cli_sessions
        self.fake = FakeClient(self.home)
        self.fake.loaded = False
        self.fake.thread.update(id=self.thread_id, cwd=self.native_state['cwd'], modelProvider='openai')
        original_request = self.fake.request
        def request(method, params, **kwargs):
            result = original_request(method, params, **kwargs)
            if method == 'thread/resume':
                result['modelProvider'] = self.fake.thread['modelProvider']
            return result
        self.fake.request = request
        self.native.client = self.fake

    def test_default_local_entry_can_continue_with_only_a_path_cli(self):
        self.assertFalse((self.home / 'packages').exists())
        self.assertFalse((self.home / 'app-server-control').exists())
        self.assertTrue(self.native.available())
        self.fake.connect = Mock(side_effect=[AppServerUnavailable('no socket'), self.fake.server_info])
        report = {'status': 'started', 'socketPath': self.fake.socket_path}
        with patch('bridge.clients.codex.cli_session.subprocess.run', return_value=
                subprocess.CompletedProcess([], 0, json.dumps(report))) as run, \
                patch.dict('os.environ', {'CODEX_SQLITE_HOME': '/default/sqlite'}):
            session = self.bridge.activate(self.thread_id)
        self.assertEqual((session.origin, session.runtime, session.connected), ('cli', 'cli', True))
        self.assertEqual(run.call_args.args[0], [str(self.installed), 'app-server', 'daemon', 'start'])
        self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(self.home))
        self.assertEqual(run.call_args.kwargs['env']['CODEX_SQLITE_HOME'], '/default/sqlite')
        self.assertEqual([params for method, params in self.fake.sent if method == 'thread/resume'],
                         [{'threadId': self.thread_id, 'excludeTurns': True}])
        self.assertFalse(any(method in ('thread/start', 'turn/start') for method, _ in self.fake.sent))
        self.bridge.ipc.connect.assert_not_called()

    def test_installed_cli_with_another_home_cannot_resume(self):
        self.fake.server_info['codexHome'] = str(self.home / 'another-home')
        with self.assertRaisesRegex(IPCError, 'another home'):
            self.bridge.activate(self.thread_id)
        self.assertEqual(self.fake.sent, [])
        self.bridge.ipc.connect.assert_not_called()

    def test_explicit_runtime_path_is_used_without_path_or_desktop_discovery(self):
        configured = Bridge(self.home, self.home / 'configured-data', codex_bin=self.installed)
        self.addCleanup(configured.close)
        native = configured.cli_sessions
        report = {'status': 'started', 'socketPath': native.client.socket_path}
        with patch('bridge.clients.codex.cli_session.shutil.which', return_value=None), \
             patch('bridge.clients.codex.cli_session.Catalog.find_runtime', side_effect=AssertionError('No desktop fallback')), \
             patch('bridge.clients.codex.cli_session.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 0, json.dumps(report))) as run:
            native._start_daemon()
        self.assertEqual(native.configured_cli, self.installed)
        self.assertEqual(run.call_args.args[0], [str(self.installed), 'app-server', 'daemon', 'start'])

    def test_only_an_independent_home_clears_the_default_database_environment(self):
        extra = self.home / 'independent-home'
        extra.mkdir()
        independent = Bridge(extra, extra / 'bridge-data', host='codex:' + '1' * 32,
                             codex_bin=self.installed)
        self.addCleanup(independent.close)
        for native, expected in ((self.native, '/default/sqlite'), (independent.cli_sessions, None)):
            with self.subTest(home=str(native.home)), patch.dict('os.environ', {'CODEX_SQLITE_HOME': '/default/sqlite'}):
                report = {'status': 'started', 'socketPath': native.client.socket_path}
                with patch('bridge.clients.codex.cli_session.subprocess.run', return_value=
                        subprocess.CompletedProcess([], 0, json.dumps(report))) as run:
                    native._start_daemon()
                self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(native.home))
                self.assertEqual(run.call_args.kwargs['env'].get('CODEX_SQLITE_HOME'), expected)
                socket = Path(native.client.socket_path)
                socket.parent.mkdir(parents=True, exist_ok=True)
                socket.touch()
                with patch('bridge.clients.codex.cli_session.subprocess.run', return_value=
                        subprocess.CompletedProcess([], 0, '{"status":"running"}')) as run:
                    self.assertEqual(native.daemon_status(), 'running')
                self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(native.home))
                self.assertEqual(run.call_args.kwargs['env'].get('CODEX_SQLITE_HOME'), expected)

    def test_installed_daemon_start_must_confirm_this_home_socket(self):
        self.fake.connect = Mock(side_effect=AppServerUnavailable('no socket'))
        report = {'status': 'started', 'socketPath': str(self.home / 'another-home' / 'control.sock')}
        with patch('bridge.clients.codex.cli_session.subprocess.run', return_value=
                subprocess.CompletedProcess([], 0, json.dumps(report))) as run:
            with self.assertRaisesRegex(IPCError, 'another home'):
                self.bridge.activate(self.thread_id)
        run.assert_called_once()
        self.assertEqual(self.fake.sent, [])
        self.bridge.ipc.connect.assert_not_called()
