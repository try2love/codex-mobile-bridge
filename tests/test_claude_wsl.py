"""WSL transport parity without launching a real Claude/model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from bridge.clients.claude.remote import RemoteClaudeCli
from bridge.clients.claude.remote_worker import native_wsl_binary, profile
from bridge.clients.claude.wsl import ClaudeCliRemotes, WslClaudeCli, WslClaudeEntries, entry_id
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions, WslClaudeWorkspace

ROOT = Path(__file__).resolve().parents[1]


class WslClaudeContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.entries = WslClaudeEntries(self.root)
        self.identifier = entry_id('Ubuntu', 'alice', '/home/alice/.claude')
        self.row = {'id': self.identifier, 'distribution': 'Ubuntu', 'user': 'alice',
                    'configDir': '/home/alice/.claude', 'label': 'WSL · Ubuntu · alice'}
        self.entries.candidates[self.identifier] = self.row
        self.entries.configure(self.identifier, True)

    def test_identity_separates_distribution_user_and_config(self):
        values = {self.identifier, entry_id('Debian', 'alice', '/home/alice/.claude'),
                  entry_id('Ubuntu', 'bob', '/home/alice/.claude'),
                  entry_id('Ubuntu', 'alice', '/home/alice/work-profile')}
        self.assertEqual(len(values), 4)
        restored = WslClaudeEntries(self.root)
        self.assertEqual(restored.selected(self.identifier)['configDir'], self.row['configDir'])

    def test_stopped_distribution_passive_listing_cannot_launch_worker(self):
        cli = WslClaudeCli(self.entries)
        self.addCleanup(cli.close)
        with patch('bridge.clients.claude.wsl.wsl.running_distributions', return_value=[]), \
             patch('bridge.clients.claude.wsl.subprocess.run') as process:
            self.assertFalse(cli.available())
            self.assertEqual(cli.listing()['sessions'], [])
            self.assertEqual(cli.projects(), [])
            self.assertEqual(cli.project_environments(), [])
            self.assertEqual(self.entries.applications()[0]['running'], False)
            process.assert_not_called()

    def test_stopped_distribution_archive_never_launches_worker(self):
        cli = WslClaudeCli(self.entries)
        self.addCleanup(cli.close)
        native = {'nativeId': str(uuid.uuid4()), 'origin': 'fixture', 'cwd': '/work'}
        row = cli._row(self.identifier, {**self.row, 'alias': self.identifier}, native)
        cli.index[row['id']] = row
        with patch('bridge.clients.claude.wsl.wsl.running_distributions', return_value=[]), \
             patch('bridge.clients.claude.wsl.subprocess.run') as process:
            with self.assertRaises(ValueError): cli.call('archive', row['id'], {'archived': True})
            process.assert_not_called()

    def test_command_preserves_distro_user_config_and_native_guard(self):
        cli = WslClaudeCli(self.entries)
        with patch('bridge.clients.claude.wsl.wsl.command', return_value=['wsl.exe', '--exec', 'python3']) as command:
            args = cli._command(self.identifier)
        command.assert_called_once_with('Ubuntu', 'alice')
        self.assertLess(len(subprocess.list2cmdline(args)), 30000)
        # Exercise the real loader using Python, without running worker main.
        import shlex
        login_command = shlex.split(args[-1])[-1]
        loader = shlex.split(login_command)[-1]
        import base64, zlib
        encoded = loader.split('b64decode(', 1)[1].split(')', 1)[0]
        source = zlib.decompress(base64.b64decode(__import__('ast').literal_eval(encoded))).decode()
        self.assertIn('os.environ["CLAUDE_CONFIG_DIR"]=\'/home/alice/.claude\'', source)
        self.assertIn('os.environ["MOBILE_BRIDGE_WSL_NATIVE"]="1"', source)
        self.assertIn('def archive_paths(', source)
        self.assertNotIn('from bridge.', source)

    def test_wsl_read_rechecks_selected_identity_and_retains_native_errors(self):
        cli = WslClaudeCli(self.entries)
        cli._command = Mock(return_value=['fixture'])
        with patch('bridge.clients.claude.wsl.subprocess.run', return_value=Mock(returncode=0,
                stdout=json.dumps({'ok': True, 'value': {'ready': True}}))) as run:
            self.assertEqual(cli._remote(self.identifier, {'operation': 'auth'}), {'ready': True})
            self.assertEqual(json.loads(run.call_args.kwargs['input']), {'operation': 'auth'})
        with patch('bridge.clients.claude.wsl.subprocess.run', side_effect=OSError()):
            with self.assertRaises(BridgeUnavailable):
                cli._remote(self.identifier, {'operation': 'list'})
        self.entries.configure(self.identifier, False)
        with self.assertRaises(ValueError):
            WslClaudeCli(self.entries)._command(self.identifier)

    def test_same_native_session_isolated_from_ssh_and_other_wsl_accounts(self):
        native = {'nativeId': str(uuid.uuid4()), 'origin': 'account', 'cwd': '/work'}
        host = {**self.row, 'alias': self.identifier}
        wsl_row = WslClaudeCli(self.entries)._row(self.identifier, host, native)
        ssh_row = RemoteClaudeCli()._row(self.identifier, host, native)
        self.assertNotEqual(wsl_row['id'], ssh_row['id'])
        self.assertEqual((wsl_row['runtime'], wsl_row['distribution'], wsl_row['user']), ('wsl', 'Ubuntu', 'alice'))
        host['alias'] = 'other'
        changed = WslClaudeCli(self.entries)._row('other', host, native)
        self.assertNotEqual(wsl_row['id'], changed['id'])

    def test_combined_remotes_share_lock_and_keep_owned_lookup(self):
        remotes = ClaudeCliRemotes(None, self.entries)
        self.addCleanup(remotes.close)
        remotes.ssh.index['ssh'] = {'id': 'ssh'}
        remotes.wsl.index['wsl'] = {'id': 'wsl'}
        self.assertIs(remotes.ssh.lock, remotes.wsl.lock)
        self.assertEqual(set(remotes.index), {'ssh', 'wsl'})
        remotes.wsl.call = Mock(return_value={'status': 'accepted'})
        self.assertEqual(remotes.call('settings', 'wsl', {'model': 'fixture'}), {'status': 'accepted'})
        remotes.wsl.call.assert_called_once_with('settings', 'wsl', {'model': 'fixture'})

    def test_scan_is_explicit_and_only_probes_running_distros(self):
        with patch('bridge.clients.claude.wsl.wsl.running_distributions', return_value=['Ubuntu']), \
             patch('bridge.clients.claude.wsl.wsl.read', return_value={'user': 'alice', 'profiles': [
                 {'configDir': '/home/alice/other'}]}) as read:
            result = self.entries.scan()
        self.assertEqual(result['errors'], [])
        self.assertEqual(read.call_args.args[0], 'Ubuntu')
        other = entry_id('Ubuntu', 'alice', '/home/alice/other')
        self.assertIn(other, self.entries.candidates)
        self.assertFalse(any(row['id'] == other for row in self.entries.entries))

    def test_windows_interop_binaries_and_shims_are_not_native(self):
        shim = self.root/'claude'
        shim.write_text('#!/bin/sh\nexec /mnt/c/Users/fixture/claude.exe "$@"\n')
        self.assertFalse(native_wsl_binary(shim))
        shim.write_bytes(b'MZfixture')
        self.assertFalse(native_wsl_binary(shim))
        self.assertFalse(native_wsl_binary('/mnt/c/Users/fixture/npm/claude'))
        shim.write_bytes(b'\x7fELFfixture')
        self.assertTrue(native_wsl_binary(shim))
        with patch.dict(os.environ, {'MOBILE_BRIDGE_WSL_NATIVE': '1'}), \
             patch('bridge.clients.claude.remote_worker.shutil.which', return_value='/mnt/c/npm/claude'), \
             patch('bridge.clients.claude.remote_worker.Path.home', return_value=self.root), \
             patch('bridge.clients.claude.remote_worker.auth_fingerprint') as auth:
            self.assertIsNone(profile())
            auth.assert_not_called()

    def test_wsl_only_dispatch_excludes_local_and_unknown_sessions(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            manager = DesktopSessions(self.root/'manager')
        self.addCleanup(manager.close)
        manager.claude_wsl.entries = [{**self.row, 'enabled': True}]
        manager.config['enabled'] = {'claude': False}
        remote = manager.adapters['claude'].remote_cli.wsl
        remote.listing = Mock(return_value={'connected': True, 'sessions': [{'id': 'wsl'}], 'complete': True})
        manager.adapters['claude'].cli.listing = Mock(side_effect=AssertionError('local CLI must not be read'))
        self.assertTrue(manager.enabled('claude'))
        self.assertEqual(manager._adapter_call('claude', 'list', None, {})['sessions'], [{'id': 'wsl'}])
        with self.assertRaises(ValueError):
            manager._adapter_call('claude', 'catalog', 'unknown', {})

    def test_native_worker_falls_back_past_windows_path_shim(self):
        binary = self.root/'.local/bin/claude'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'\x7fELFfixture')
        binary.chmod(0o700)
        with patch.dict(os.environ, {'MOBILE_BRIDGE_WSL_NATIVE': '1'}), \
             patch('bridge.clients.claude.remote_worker.Path.home', return_value=self.root), \
             patch('bridge.clients.claude.remote_worker.shutil.which', return_value='/mnt/c/npm/claude'), \
             patch('bridge.clients.claude.remote_worker.auth_fingerprint', return_value='fixture'):
            self.assertEqual(profile()['binary'], str(binary.resolve()))

    def test_explicit_scan_discovers_claude_but_internal_scan_does_not(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            manager = DesktopSessions(self.root/'manager')
        self.addCleanup(manager.close)
        manager.clients = Mock(return_value={'clients': []})
        manager.claude_wsl.scan = Mock(return_value={'errors': []})
        discovered = {name: {'installed': False} for name in ('claude', 'deepseek')}
        with patch('bridge.clients.manager.sys.platform', 'win32'), \
             patch('bridge.app.desktop.Desktop') as desktop, \
             patch('bridge.clients.discovery.discover_clients', return_value=discovered):
            desktop.return_value.preferences.return_value = {}
            manager.scan(scan_wsl=False)
            manager.claude_wsl.scan.assert_not_called()
            manager.scan()
            manager.claude_wsl.scan.assert_called_once_with()

    def test_wsl_only_selection_cannot_offer_or_run_native_reconnect(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            manager = DesktopSessions(self.root/'manager', gateway_running=True)
        self.addCleanup(manager.close)
        manager.claude_wsl.entries = [{**self.row, 'enabled': True}]
        manager.config['enabled'] = {'claude': False}
        manager.config['discovered'] = {'claude': {'installed': True, 'executable': 'fixture'}}
        manager._descriptor = Mock(return_value={'id': 'claude', 'executable': 'fixture'})
        manager._claude_reconnect_supported = Mock(return_value=True)
        manager.adapters['claude'].status = Mock(return_value={'connected': False, 'cliAvailable': True,
            'automaticConnection': 'native-console', 'setupState': 'not-scanned'})
        manager.adapters['deepseek'].call = Mock(return_value={'connected': False})
        native = {'running': True, 'mainPids': [42], 'runtimePids': [], 'unknown': False}
        with patch('bridge.clients.lifecycle.inspect_clients', return_value={'claude': native}), \
             patch('bridge.clients.claude.wsl.wsl.running_distributions', return_value=['Ubuntu']):
            row = next(row for row in manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
            self.assertTrue(row['enabled'])
            self.assertFalse(row['nativeEnabled'])
            self.assertFalse(row['canReconnect'])
            self.assertFalse(row['canInitializeDesktop'])
            self.assertFalse(row['canStartDesktop'])
            with self.assertRaisesRegex(ValueError, 'Claude Desktop 接入已关闭'):
                manager.reconnect_claude()
            manager.config['enabled']['claude'] = True
            row = next(row for row in manager.clients(refresh=True)['clients'] if row['id'] == 'claude')
            self.assertTrue(row['canInitializeDesktop'])
            self.assertTrue(row['canReconnect'])

    def test_wsl_task_does_not_block_unrelated_native_lifecycle(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            manager = DesktopSessions(self.root/'manager')
        self.addCleanup(manager.close)
        adapter = manager.adapters['claude']
        adapter.cli.busy = Mock(return_value=False)
        adapter.remote_cli.wsl.busy = Mock(return_value=True)
        adapter.remote_cli.ssh.busy = Mock(return_value=False)
        self.assertTrue(adapter.remote_cli.busy())
        self.assertFalse(manager._claude_native_cli_busy())
        adapter.remote_cli.ssh.busy.return_value = True
        self.assertTrue(manager._claude_native_cli_busy())

    def test_wsl_model_cache_identity_is_scoped_and_revoked_with_entry(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            manager = DesktopSessions(self.root/'manager')
        self.addCleanup(manager.close)
        manager.claude_wsl.entries = [{**self.row, 'enabled': True}]
        remote = manager.adapters['claude'].remote_cli.wsl
        host = {**self.row, 'alias': self.identifier}
        raw = {'nativeId': str(uuid.uuid4()), 'origin': 'account', 'cwd': '/work', 'projectConfig': 'settings'}
        row = remote._row(self.identifier, host, raw)
        remote.index[row['id']] = row
        with patch('bridge.clients.claude.wsl.wsl.running_distributions', return_value=['Ubuntu']):
            identity = manager._model_identity('claude', row['id'])
            changed = {**raw, 'origin': 'other-account'}
            other = remote._row(self.identifier, host, changed)
            remote.index[other['id']] = other
            self.assertNotEqual(identity, manager._model_identity('claude', other['id']))
            owned = Mock(origin=raw['origin'], error=None, model_list_unsupported=True)
            owned.process.poll.return_value = None
            owned.close = Mock()
            remote.owned[row['id']] = owned
            self.assertTrue(manager._model_live('claude', row['id']))
            self.assertTrue(manager._model_refresh_unsupported('claude', row['id']))
            manager.claude_wsl.entries[0]['enabled'] = False
            with self.assertRaises(BridgeUnavailable):
                manager._cached_model_result('claude', row['id'], identity,
                    (0, {'models': [{'id': 'fixture'}], 'capabilities': {'models': True}}))

    def test_registered_cli_project_creates_without_desktop_connection(self):
        from bridge.clients.claude.adapter import Claude
        adapter = Claude(self.root/'adapter', auto_connect=False)
        self.addCleanup(adapter.close)
        adapter.create_cli = Mock(return_value={'status': 'ready', 'result': {'id': 'new'}})
        project = {'surface': 'cli', 'host': self.identifier, 'cwd': '/work/new'}
        body = {'directoryProject': project, 'firstMessage': 'fixture'}
        self.assertEqual(adapter.call('create', body=body)['result']['id'], 'new')
        adapter.create_cli.assert_called_once_with(project, body)

    def test_remote_facade_passes_initial_selection_only_to_the_selected_wsl_or_ssh(self):
        facade=object.__new__(ClaudeCliRemotes);facade.ssh=Mock();facade.wsl=Mock()
        selection={'model':'claude-sonnet-4-5-20250929'}
        facade.create(self.identifier,'alias','origin','/project','FIRST',selection=selection)
        facade.wsl.create.assert_called_once_with(self.identifier,'alias','origin','/project','FIRST',selection=selection)
        facade.ssh.create.assert_not_called()
        facade.project_environments(host_id=self.identifier)
        facade.wsl.project_environments.assert_called_once_with(host_id=self.identifier)
        facade.ssh.project_environments.assert_not_called()

    def test_wsl_directory_operations_use_selected_native_transport(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            manager = DesktopSessions(self.root/'manager')
        self.addCleanup(manager.close)
        manager.claude_wsl.read = Mock(return_value={'cwd': '/work/new'})
        environment = {'host': self.identifier}
        manager._project_alias = Mock(side_effect=AssertionError('must not use SSH'))
        result = manager._project_directory(environment, '/work/new', True)
        self.assertEqual(result['cwd'], '/work/new')
        source = manager.claude_wsl.read.call_args.args[1]
        self.assertIn('project_directory(**', source)
        import ast, base64
        encoded = source.rsplit("b64decode(", 1)[1].split(')', 1)[0]
        arguments = json.loads(base64.b64decode(ast.literal_eval(encoded)))
        self.assertEqual(arguments, {'value': '/work/new', 'create': True, 'expected': None})


if __name__ == '__main__':
    unittest.main()
