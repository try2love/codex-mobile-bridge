import copy
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
import io
import os
import subprocess
from contextlib import redirect_stdout
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.ipc import IPCError
from bridge.features.endpoints.wsl import WslEntry
from bridge.features.endpoints.registry import Entries, entry_id
from bridge.features.sessions.store import SessionStore
from bridge.platforms.windows import wsl

ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.entries = Entries(self.temp.name)
        self.info = {'user': 'fixture', 'homes': [{'home': '/home/fixture/.codex', 'daemon': None}]}

    def test_only_explicit_scan_probes_running_distributions(self):
        with patch.object(wsl, 'running_distributions', return_value=['Ubuntu']), patch.object(wsl, 'probe', return_value=self.info) as probe:
            self.entries.public()
            probe.assert_not_called()
            result = self.entries.scan()
            probe.assert_called_once_with('Ubuntu')
            row = result['entries'][1]
            self.assertFalse(row['enabled'])
            self.entries.configure(row['id'], True)
        restarted = Entries(self.temp.name)
        self.assertEqual(restarted.entries[0]['id'], entry_id('Ubuntu', 'fixture', '/home/fixture/.codex'))
        self.assertFalse(self.entries.bridges)  # Registration doesn't start anything.

    def test_unknown_id_or_phone_path_cannot_register_an_entry(self):
        for identifier in ('wsl:unregistered', '/home/arbitrary', 'local'):
            with self.assertRaises(ValueError):
                self.entries.configure(identifier, True)
        with self.assertRaises(KeyError):
            self.entries.get('wsl:unknown')
        self.assertFalse(self.entries.path.exists())

    def test_same_windows_and_wsl_configuration_home_cannot_be_registered_twice(self):
        self.entries.desktop_home = Path(self.temp.name).resolve()
        identifier = entry_id('Ubuntu', 'fixture', '/mnt/c/shared')
        self.entries.candidates[identifier] = {'id': identifier, 'distribution': 'Ubuntu', 'user': 'fixture',
            'home': '/mnt/c/shared', 'windowsHome': str(self.entries.desktop_home), 'label': 'shared'}
        with self.assertRaisesRegex(ValueError, '共用同一'):
            self.entries.configure(identifier, True)
        self.assertFalse(self.entries.path.exists())

    def test_disabling_closes_only_selected_byte_connection(self):
        with patch.object(wsl, 'running_distributions', return_value=['Ubuntu']), patch.object(wsl, 'probe', return_value=self.info):
            row = self.entries.scan()['entries'][1]
            self.entries.configure(row['id'], True)
            connection = Mock()
            self.entries.bridges[row['id']] = connection
            self.entries.configure(row['id'], False)
            connection.close.assert_called_once_with()
            with self.assertRaises(KeyError):
                self.entries.get(row['id'])

    def test_stopped_distribution_never_invokes_python(self):
        with patch.object(wsl, 'running_distributions', return_value=[]), patch.object(wsl.subprocess, 'run') as run:
            with self.assertRaises(OSError):
                wsl.read('Stopped Ubuntu', 'print("unexpected")')
            run.assert_not_called()

    def test_app_inventory_reuses_registered_identity_without_connecting(self):
        with patch.object(wsl, 'running_distributions', return_value=['Ubuntu']), patch.object(wsl, 'probe', return_value=self.info) as probe:
            candidate = self.entries.scan()['entries'][1]
            self.entries.configure(candidate['id'], True)
            probe.reset_mock()
            row = self.entries.applications()[0]
            self.assertEqual(row['id'], candidate['id'])
            self.assertTrue(row['name'].startswith('WSL Codex'))
            self.assertTrue(row['enabled'])
            self.assertTrue(row['distributionRunning'])
            self.assertFalse(row['connected'])  # A running distro is not daemon evidence.
            self.assertFalse(row['canQuitDesktop'])
            self.assertFalse(self.entries.bridges)
            probe.assert_not_called()
            restarted = Entries(self.temp.name)
            self.assertEqual(restarted.applications()[0]['id'], row['id'])


class CliStoreTests(unittest.TestCase):
    def test_cli_later_inputs_are_searchable_before_paging_and_do_not_rename_originals(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            home = Path(folder)
            with closing(sqlite3.connect(home/'state_5.sqlite')) as db:
                db.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, cwd TEXT, originator TEXT, source TEXT, archived INTEGER, updated_at INTEGER)')
                db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)', [
                    ('old', None, 'Original question', '/fixture/old', 'codex-tui', 'cli', 0, 1),
                    ('named', 'Custom name', 'First prompt', '/fixture/named', 'codex_mobile_bridge', 'vscode', 0, 2),
                    ('child', None, 'Child', '/fixture', 'codex_mobile_bridge', '{"subagent":{}}', 0, 3),
                    ('archived', None, 'Archived', '/fixture', 'codex-tui', 'cli', 1, 4),
                    *[('new-'+str(i), None, 'New', '/fixture', 'codex-tui', 'cli', 0, i+10) for i in range(120)]])
                db.commit()
            history = home/'history.jsonl'
            records = [{'session_id': 'old', 'text': 'First later input'},
                       {'session_id': 'old', 'text': 'Use ROS2'},
                       {'session_id': 'named', 'text': 'Use ROS2 for this too'},
                       {'session_id': 'child', 'text': 'Use ROS2'},
                       {'session_id': 'archived', 'text': 'Use ROS2'}]
            original = '\n'.join(json.dumps(r) for r in records)+'\ninvalid partial record\n'
            history.write_text(original, encoding='utf8')
            store = SessionStore(home, source='cli')
            self.assertEqual([r['id'] for r in store.list(query='ros2', limit=1)], ['named'])
            old = store.list(query='ros2', limit=1, offset=1)[0]
            self.assertEqual((old['id'], old['display_title'], old['title']), ('old', 'Use ROS2', 'Original question'))
            self.assertEqual(store.get('named')['display_title'], 'Custom name')
            self.assertEqual([r['id'] for r in store.list(query='ros2', archived=True)], ['archived'])
            self.assertEqual([r['id'] for r in store.list(query='First later')], ['old'])
            with history.open('a', encoding='utf8') as f: f.write(json.dumps({'session_id':'old','text':'Latest input'})+'\n')
            self.assertEqual(store.get('old')['display_title'], 'Latest input')
            self.assertEqual(store.get('old')['title'], 'Original question')
            self.assertTrue(history.read_text(encoding='utf8').startswith(original))
            desktop = SessionStore(home)
            self.assertFalse(desktop.list(query='ROS2'))

    def test_cli_source_is_explicit_and_subagents_remain_excluded(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            home = Path(folder)
            with closing(sqlite3.connect(home/'state_5.sqlite')) as db:
                db.execute('CREATE TABLE threads (id TEXT, originator TEXT, source TEXT, thread_source TEXT, archived INTEGER, updated_at INTEGER, cwd TEXT, title TEXT)')
                db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)', [
                    ('cli', 'codex-tui', 'cli', None, 0, 10, '/fixture', 'CLI'),
                    ('shared', 'codex-tui', 'vscode', None, 0, 11, '/fixture', 'Shared CLI'),
                    ('old', None, 'cli', None, 0, 12, '/fixture', 'Old'),
                    ('mobile', 'codex_mobile_bridge_wsl', 'vscode', None, 0, 12, '/fixture', 'Mobile'),
                    ('legacy-mobile', 'codex_mobile_bridge', 'vscode', None, 0, 12, '/fixture', 'Legacy mobile'),
                    ('legacy-child', 'codex_mobile_bridge', '{"subagent":{"thread_spawn":{}}}', None, 0, 12, '/fixture', 'Child'),
                    ('desktop', 'codex_work_desktop', 'vscode', None, 0, 13, '/fixture', 'Desktop'),
                    ('vscode', None, 'vscode', None, 0, 13, '/fixture', 'Extension'),
                    ('child', 'codex-tui', 'cli', 'subagent', 0, 14, '/fixture', 'Child'),
                    ('exec', 'codex_exec', 'exec', None, 0, 15, '/fixture', 'Exec')])
                db.commit()
            cli = SessionStore(home, source='cli')
            desktop = SessionStore(home, source='desktop')
            all_sessions = SessionStore(home)
            self.assertEqual({r['id'] for r in cli.list()},
                             {'cli', 'shared', 'old', 'mobile', 'legacy-mobile'})
            self.assertEqual({r['id'] for r in desktop.list()},
                             {'desktop', 'vscode', 'legacy-mobile'})
            self.assertEqual({r['id'] for r in all_sessions.list()},
                             {'cli', 'shared', 'old', 'mobile', 'legacy-mobile', 'desktop', 'vscode'})
            self.assertEqual(cli.get('legacy-mobile')['title'], 'Legacy mobile')
            self.assertEqual([r['id'] for r in cli.list(query='Legacy')], ['legacy-mobile'])
            for identifier in ('desktop', 'vscode', 'child', 'legacy-child', 'exec'):
                with self.assertRaises(KeyError):
                    cli.get(identifier)
            self.assertEqual(cli.get('shared')['originator'], 'codex-tui')
            with self.assertRaises(KeyError):
                desktop.get('cli')


class DaemonStartupTests(unittest.TestCase):
    def test_only_verified_vscode_stdio_server_can_coexist(self):
        home = Path('/home/fixture')
        context = {'Path': Path, 'home': home}
        exec(wsl._START_DAEMON.split('root=Path(requested_home)')[0], context)
        identify = context['vscode_app_server']
        executable = str(home/'.vscode-server/extensions/openai.chatgpt-26.1007.21434-linux-x64/bin/linux-x86_64/codex')
        parent = str(home/'.vscode-server/bin/abcdef1234/node')
        args = [b'codex', b'-c', b'example=true', b'app-server', b'--analytics-default-enabled']
        parent_args = [b'node', b'--type=extensionHost']
        self.assertTrue(identify(executable, args, parent, parent_args))
        for other in ([b'codex'], [b'codex', b'app-server', b'--no-daemon'],
                      args + [b'--listen', b'unix:///tmp/another'], args + [b'--managed-daemon']):
            self.assertFalse(identify(executable, other, parent, parent_args))
        self.assertFalse(identify('/usr/bin/codex', args, parent, parent_args))
        self.assertFalse(identify(executable, args, parent, [b'node']))
        self.assertFalse(identify(executable, args, '/usr/bin/node', parent_args))

    def test_native_start_uses_original_home_and_does_not_restart_or_override_provider(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            root = Path(folder).resolve()
            proc = root/'empty-proc'; proc.mkdir()
            executable = root/'codex'; executable.touch(); executable.chmod(0o700)
            def path(value): return proc if value == '/proc' else Path(value)
            run = Mock(return_value=SimpleNamespace(returncode=0))
            context = {'Path': path, 'root': root, 'requested_home': str(root), 'uid': root.stat().st_uid,
                       'home': root, 'inventory': {'homes': []}, 'os': os, 'subprocess': subprocess}
            with patch('shutil.which', return_value=str(executable)), patch.object(subprocess, 'run', run):
                exec(wsl._START_DAEMON, context)
            self.assertEqual([c.args[0][1:] for c in run.call_args_list],
                             [['app-server', 'daemon', 'start', '--help'], ['app-server', 'daemon', 'start']])
            self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(root))
            self.assertEqual(run.call_args.kwargs['cwd'], root)

    def test_another_home_daemon_is_never_retargeted_or_restarted(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            root = Path(folder).resolve()
            context = {'Path': Path, 'requested_home': str(root), 'uid': root.stat().st_uid, 'home': root,
                       'inventory': {'homes': [{'home': '/other/config', 'daemon': {'pid': 999}}]},
                       'os': os, 'subprocess': subprocess}
            with patch.object(subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'Another configuration'):
                    exec(wsl._START_DAEMON, context)
                run.assert_not_called()

    def test_only_explicit_start_can_wake_an_already_registered_distribution(self):
        with patch.object(wsl, '_distributions', return_value=['Ubuntu']) as registered:
            self.assertIn('Ubuntu', wsl.command('Ubuntu', 'fixture', allow_start=True))
            registered.assert_called_once_with(False)
        with patch.object(wsl, 'running_distributions', return_value=[]):
            with self.assertRaises(OSError): wsl.command('Ubuntu', 'fixture')
        with patch.object(wsl, '_distributions', return_value=[]):
            with self.assertRaises(OSError): wsl.command('NotRegistered', 'fixture', allow_start=True)


class Runtime:
    def __init__(self, identifier):
        self.closed = False
        self.calls = []
        self.responses = []
        self.fail_send = False
        self.thread = {'id': identifier, 'cwd': '/home/fixture/project', 'modelProvider': 'magpie',
                       'model': 'group/fixture', 'reasoningEffort': 'high', 'status': {'type': 'idle'},
                       'canAcceptDirectInput': True}
        self.turns = []

    def request(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'thread/read':
            return {'thread': copy.deepcopy(self.thread)}
        if method == 'thread/resume':
            return {'thread': copy.deepcopy(self.thread)}
        if method == 'thread/turns/list':
            return {'data': copy.deepcopy(self.turns), 'nextCursor': None}
        if method == 'turn/start':
            if self.fail_send:
                raise IPCError('Disconnected after bytes were sent')
            self.thread['status'] = {'type': 'active'}
            return {'turn': {'id': str(uuid.uuid4())}}
        if method == 'turn/interrupt':
            return {}
        if method == 'config/read':
            return {'config': {'model_provider': 'magpie', 'model': 'group/fixture', 'model_providers': {'magpie': {}}}}
        if method == 'model/list':
            return {'data': [{'model': 'group/fixture'}]}
        raise AssertionError('Unexpected operation: ' + method)

    def write(self, value):
        self.responses.append(copy.deepcopy(value))
        if hasattr(self, 'response_callback'):
            self.response_callback(value)

    def close(self):
        self.closed = True


class WslSessionTests(unittest.TestCase):
    def test_register_project_executes_on_selected_wsl_and_persists_identity(self):
        identity = {'cwd': '/home/fixture/new-project', 'device': 1, 'inode': 42}
        with patch.object(self.entry, 'read', return_value=identity) as read:
            project = self.entry.register_project('~/new-project', True, self.entry.host)
        self.assertEqual(project['host'], self.entry.host)
        self.assertEqual(project['directoryIdentity'], identity)
        self.assertIn('project_directory(**', read.call_args.args[1])
        self.assertEqual(read.call_args.args[0], self.row['distribution'])
        self.entry.store.list = lambda **k: []
        self.assertEqual(self.entry.projects()[0]['key'], project['key'])
        with self.assertRaises(ValueError): self.entry.register_project('/fixture', False, 'local')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.identifier = str(uuid.uuid4())
        self.row = {'id': entry_id('Ubuntu', 'fixture', '/home/fixture/.codex'), 'distribution': 'Ubuntu',
                    'user': 'fixture', 'home': '/home/fixture/.codex', 'label': 'WSL fixture'}
        self.entry = self.make_entry()
        self.addCleanup(self.entry.close)

    def make_entry(self):
        entry = WslEntry(self.row, self.temp.name)
        entry.server = Runtime(self.identifier)
        entry.generation = 'fixture-generation'
        entry.server.response_callback = lambda value: entry._event({'method': 'serverRequest/resolved',
            'params': {'threadId': self.identifier, 'requestId': value['id']}}, entry.generation)
        entry.store = SimpleNamespace(get=lambda identifier: {'id': identifier, 'cwd': '/home/fixture/project',
                                                              'model_provider': 'magpie'})
        return entry

    def test_reads_are_passive_and_explicit_send_subscribes_to_same_loaded_daemon(self):
        view = self.entry.view(self.identifier)
        self.assertTrue(view['connected'])
        self.assertEqual(view['provider'], 'magpie')
        self.assertFalse(view['capabilities']['permissions'])
        self.assertEqual(view['access']['source'], 'cli')
        self.assertEqual(view['access']['runtime'], 'wsl')
        self.assertTrue(view['access']['canSend'])
        self.assertFalse(view['access']['canChangePermissions'])
        self.assertFalse(any(m == 'thread/resume' for m, _ in self.entry.server.calls))
        identifier = str(uuid.uuid4())
        result = self.entry.send(self.identifier, 'synthetic input', identifier, work_mode='default')
        self.assertEqual(result['status'], 'accepted')
        writes = [p for m, p in self.entry.server.calls if m == 'turn/start']
        self.assertEqual(writes, [{'threadId': self.identifier,
            'input': [{'type': 'text', 'text': 'synthetic input'}], 'clientUserMessageId': identifier}])
        self.assertFalse(any(m in ('thread/start', 'config/batchWrite') for m, _ in self.entry.server.calls))
        self.assertEqual([p for m, p in self.entry.server.calls if m == 'thread/resume'],
                         [{'threadId': self.identifier, 'excludeTurns': True}])
        self.assertEqual(self.entry.send(self.identifier, 'synthetic input', identifier), result)
        self.assertEqual(sum(m == 'turn/start' for m, _ in self.entry.server.calls), 1)

    def test_fresh_not_loaded_evidence_blocks_even_when_display_says_connected(self):
        session = self.entry.session(self.identifier)
        self.assertTrue(session.connected)
        self.entry.server.thread['status'] = {'type': 'notLoaded'}
        self.entry.server.thread['canAcceptDirectInput'] = None
        with self.assertRaises(IPCError):
            self.entry.send(self.identifier, 'do not send', str(uuid.uuid4()))
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))
        self.assertFalse(self.entry.receipts_path.exists())

    def test_explicit_open_resumes_unloaded_history_but_passive_refresh_does_not(self):
        self.entry.server.thread.update(status={'type': 'notLoaded'}, canAcceptDirectInput=None)
        self.entry.subscriptions[self.identifier] = self.entry.generation
        self.entry.store.history = lambda *a, **k: {'id': self.identifier, 'turns': []}
        disconnected = self.entry.view(self.identifier)
        self.assertFalse(disconnected['connected'])
        self.assertTrue(disconnected['access']['readOnly'])
        self.assertFalse(disconnected['access']['canSend'])
        self.assertTrue(disconnected['access']['canReconnect'])
        self.assertFalse(any(m == 'thread/resume' for m, _ in self.entry.server.calls))
        original = self.entry.server.request
        def request(method, params):
            if method == 'thread/resume':
                self.entry.server.thread.update(status={'type': 'idle'}, canAcceptDirectInput=True)
            return original(method, params)
        self.entry.server.request = request
        self.assertTrue(self.entry.activate(self.identifier).connected)
        self.assertEqual([p for m, p in self.entry.server.calls if m == 'thread/resume'],
                         [{'threadId': self.identifier, 'excludeTurns': True}])
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))

    def test_restoring_history_cannot_silently_switch_to_a_different_default_provider(self):
        self.entry.server.thread.update(status={'type': 'notLoaded'}, canAcceptDirectInput=None,
                                        modelProvider='original-provider')
        with self.assertRaisesRegex(IPCError, '提供商与当前接入不同'):
            self.entry.activate(self.identifier)
        self.assertFalse(any(m in ('thread/resume', 'turn/start') for m, _ in self.entry.server.calls))

    def test_mobile_create_is_scoped_durable_and_never_repeats_an_uncertain_start(self):
        self.entry.store.list = lambda **k: [{'cwd': '/home/fixture/project'}]
        self.entry.read = lambda *a, **k: {'cwd': '/home/fixture/project'}
        original = self.entry.server.request
        created = str(uuid.uuid4())
        def request(method, params):
            if method == 'thread/start':
                self.entry.server.calls.append((method, copy.deepcopy(params)))
                self.entry.server.thread['id'] = created
                return {'thread': copy.deepcopy(self.entry.server.thread)}
            if method == 'thread/name/set': return {}
            return original(method, params)
        self.entry.server.request = request
        project = self.entry.host + '|/home/fixture/project'
        attempt = str(uuid.uuid4())
        first = self.entry.create_chat(project, 'Mobile', attempt)
        self.assertEqual(first['id'], created)
        self.assertEqual(self.entry.create_chat(project, 'Mobile', attempt)['id'], created)
        self.assertEqual(sum(m == 'thread/start' for m, _ in self.entry.server.calls), 1)
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))
        with self.assertRaises(ValueError): self.entry.create_chat('local|independent', 'Mobile', str(uuid.uuid4()))
        with self.assertRaises(ValueError): self.entry.create_chat(project, 'Changed', attempt)
        def unknown(method, params):
            if method == 'thread/start': raise IPCError('Unknown write')
            return request(method, params)
        self.entry.server.request = unknown
        uncertain = str(uuid.uuid4())
        with self.assertRaisesRegex(IPCError, '不会重复创建'): self.entry.create_chat(project, 'Unknown', uncertain)
        self.entry.server.request = Mock(side_effect=AssertionError('Must not replay native creation'))
        with self.assertRaisesRegex(IPCError, '不会重复创建'): self.entry.create_chat(project, 'Unknown', uncertain)

    def test_passive_connection_does_not_start_codex_and_explicit_connect_can_start_it(self):
        self.entry.server = None
        info = {'user': 'fixture', 'homes': [{'home': self.row['home'], 'daemon': None}]}
        ready = copy.deepcopy(info)
        ready['homes'][0]['daemon'] = {'socket': '/tmp/fixture', 'pid': 42}
        with patch.object(wsl, 'probe', return_value=info), patch.object(wsl, 'ensure_daemon', return_value=ready) as start, \
                patch.object(wsl, 'Stream'), patch('bridge.features.endpoints.wsl.SharedServer') as server:
            with self.assertRaises(IPCError): self.entry.runtime()
            start.assert_not_called()
            self.entry.connect()
            start.assert_called_once_with(self.row['distribution'], self.row['user'], self.row['home'])
            server.return_value.start.assert_called_once()

    def test_uncertain_send_survives_restart_and_is_never_replayed(self):
        submission = str(uuid.uuid4())
        self.entry.server.fail_send = True
        with self.assertRaisesRegex(IPCError, '不会重复发送'):
            self.entry.send(self.identifier, 'synthetic input', submission)
        restarted = self.make_entry()
        self.addCleanup(restarted.close)
        result = restarted.send(self.identifier, 'synthetic input', submission)
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(any(m == 'turn/start' for m, _ in restarted.server.calls))
        with self.assertRaises(ValueError):
            restarted.send(self.identifier, 'changed input', submission)

    def test_subagent_and_noninteractive_owner_cannot_receive_input(self):
        for field, value in [('parentThreadId', str(uuid.uuid4())), ('canAcceptDirectInput', False)]:
            with self.subTest(field=field):
                self.entry.server.thread[field] = value
                with self.assertRaises(IPCError):
                    self.entry.send(self.identifier, 'do not send', str(uuid.uuid4()))
                self.entry.server.thread.pop(field)
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))

    def test_subscription_setting_change_aborts_before_sending_or_persisting_receipt(self):
        original = self.entry.server.request
        def changed(method, params):
            result = original(method, params)
            if method == 'thread/resume':
                result['thread']['modelProvider'] = 'different'
            return result
        self.entry.server.request = changed
        with self.assertRaisesRegex(IPCError, '尚未发送'):
            self.entry.send(self.identifier, 'do not send', str(uuid.uuid4()))
        self.assertFalse(self.entry.receipts_path.exists())
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))

    def test_approval_is_bound_to_thread_connection_and_original_scope(self):
        self.entry.session(self.identifier)
        request = {'id': 'approval-1', 'method': 'item/commandExecution/requestApproval',
                   'params': {'threadId': self.identifier, 'command': 'fixture', 'availableDecisions': ['decline']}}
        self.entry._event(request, self.entry.generation)
        with self.assertRaises(ValueError):
            self.entry.respond(self.identifier, 'approval-1', {'decision': 'accept'})
        self.assertFalse(self.entry.server.responses)
        result = self.entry.respond(self.identifier, 'approval-1', {'decision': 'decline'})
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(self.entry.server.responses, [{'id': 'approval-1', 'result': {'decision': 'decline'}}])
        with self.assertRaises(ValueError):
            self.entry.respond(self.identifier, 'approval-1', {'decision': 'decline'})
        self.assertEqual(len(self.entry.server.responses), 1)
        self.entry._event(request, self.entry.generation)
        self.entry.generation = 'new-daemon'
        with self.assertRaises(ValueError):
            self.entry.respond(self.identifier, 'approval-1', {'decision': 'decline'})

    def test_catalog_uses_original_runtime_models_without_exporting_config(self):
        result = self.entry.catalog(self.identifier, kind='models')
        self.assertEqual([(row['id'], row['name'], row['efforts']) for row in result['models']], [('group/fixture', 'group/fixture', [])])
        self.assertNotIn('model_providers', result)
        self.assertFalse(any(m == 'turn/start' for m, _ in self.entry.server.calls))

    def test_catalog_normalizes_paged_native_models_and_keeps_group_ids_and_efforts(self):
        request = self.entry.server.request
        pages = iter([{'data': [None, {'id': 'internal', 'model': 'group/auto-gpt-6-1-sol',
                    'displayName': '分组模型', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}],
                    'defaultReasoningEffort': 'high'}], 'nextCursor': 'second'},
                      {'data': [{'id': 'group/id-only', 'displayName': ''}, {'model': 'hidden', 'hidden': True}]}])
        self.entry.server.request = lambda method, params: next(pages) if method == 'model/list' else request(method, params)
        result = self.entry.catalog(self.identifier, kind='models')
        self.assertEqual([(m['id'], m['name']) for m in result['models']],
                         [('group/auto-gpt-6-1-sol', '分组模型'), ('group/id-only', 'group/id-only')])
        self.assertEqual(result['models'][0]['efforts'], ['high'])
        self.assertEqual(result['models'][0]['defaultEffort'], 'high')

    def test_wsl_access_summary_is_external_and_never_exposes_desktop_accounts(self):
        value = self.entry.access_summary()
        self.assertEqual(value['scope'], 'wsl')
        self.assertEqual(value['current']['name'], 'Magpie')
        self.assertFalse(value['canSwitch'])
        self.assertEqual(value['accounts'], [])
        self.assertNotIn('model_providers', json.dumps(value))

    def test_actual_injected_workspace_source_runs_without_bridge_imports_and_rejects_escape(self):
        root = Path(self.temp.name)/'project'
        root.mkdir()
        (root/'fixture.txt').write_bytes(b'fixture')
        (root.parent/'outside.txt').write_bytes(b'private')
        self.entry.store = SimpleNamespace(get=lambda _: {'cwd': str(root)})
        def remote(alias, source, timeout):
            self.assertNotIn('from bridge.', source)
            output = io.StringIO()
            with redirect_stdout(output):
                exec(source, {'__name__': 'wsl_fixture_payload'})
            return json.loads(output.getvalue())
        self.entry.read = remote
        self.assertEqual(self.entry.workspace(self.identifier, 'info', {'path': 'fixture.txt'})['size'], 7)
        with self.assertRaises(ValueError):
            self.entry.workspace(self.identifier, 'info', {'path': '../outside.txt'})


if __name__ == '__main__':
    unittest.main()
