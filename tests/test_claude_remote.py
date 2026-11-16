import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.claude import remote, remote_worker
from bridge.clients.claude.remote import RemoteClaudeCli
from bridge.clients.uploads import DesktopUploads
from bridge.clients.errors import BridgeUnavailable
from bridge.resources import source_text


class RemoteWorkerContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root/'remote-profile'; self.config.mkdir()
        self.cwd = self.root/'remote-project'; self.cwd.mkdir()
        self.native = str(uuid.uuid4())
        folder = self.config/'projects'/'fixture'; folder.mkdir(parents=True)
        self.path = folder/(self.native+'.jsonl')
        self.path.write_text('\n'.join(json.dumps(event) for event in [
            {'type': 'user', 'cwd': str(self.cwd), 'permissionMode': 'plan',
             'message': {'role': 'user', 'content': 'remote fixture'}},
            {'type': 'assistant', 'cwd': str(self.cwd),
             'message': {'role': 'assistant', 'model': 'fixture-model',
                         'content': [{'type': 'text', 'text': 'fixture response'}]}}])+'\n')
        (self.config/'sessions').mkdir()
        bindir = self.root/'bin'; bindir.mkdir()
        binary = bindir/('claude.cmd' if os.name == 'nt' else 'claude')
        if os.name == 'nt':
            binary.write_text('@echo off\r\nif "%1"=="auth" echo {"loggedIn":true}\r\n')
        else:
            binary.write_text('#!/bin/sh\nif [ "$1" = auth ]; then printf \'{"loggedIn":true}\\n\'; fi\n')
            binary.chmod(0o700)
        self.env = {**os.environ, 'HOME': str(self.root), 'CLAUDE_CONFIG_DIR': str(self.config),
                    'PYTHONIOENCODING': 'utf-8',
                    'PATH': str(bindir)+os.pathsep+os.environ.get('PATH', '')}
        self.binary = binary

    def run_worker(self, request):
        # Run the exact standalone payload without Windows' command-line size limit.
        worker = self.root/'standalone-worker.py'
        worker.write_text(remote.worker_source(), encoding='utf-8')
        process = subprocess.run([sys.executable, '-u', str(worker)],
                                 input=json.dumps(request)+'\n', text=True, encoding='utf-8', capture_output=True,
                                 env={**self.env, 'PYTHONPATH': ''}, cwd=self.root, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_payload_embeds_archive_helper_and_resource_is_explicitly_bundled(self):
        source = remote.worker_source()
        self.assertNotIn('from bridge.', source)
        self.assertIn('def archive_paths(', source)
        resources = json.loads((Path(__file__).resolve().parents[1]/'scripts/gateway-resources.json').read_text())
        self.assertIn('bridge/clients/claude/file_archive.py', resources)

    def test_standalone_passive_history_and_host_profile(self):
        listed = self.run_worker({'operation': 'list'})['value']
        self.assertEqual(len(listed['sessions']), 1)
        profile = listed['profile']
        self.assertEqual(profile['binary'], str(self.binary.resolve()))
        self.assertIn(str(self.config), profile['origin'])
        self.assertNotIn('events', listed['sessions'][0])
        detail = self.run_worker({'operation': 'detail', 'origin': profile['origin'],
                                  'native': self.native})['value']
        history = self.run_worker({'operation': 'history', 'origin': profile['origin'],
                                   'native': self.native, 'scope': 'test-host-session',
                                   'historyLimit': 40})['value']
        self.assertEqual(len(history['events']), 2)
        self.assertEqual(detail['cwd'], str(self.cwd))
        self.assertTrue(self.run_worker({'operation': 'auth', 'origin': profile['origin']})['value']['ready'])
        project_probe = self.run_worker({'operation': 'projects'})['value']
        self.assertEqual(project_probe['profile']['origin'], profile['origin'])
        self.assertTrue(project_probe['authReady'])
        self.assertEqual(len(project_probe['sessions']), 1)
        self.assertFalse((self.config/'mobile-bridge').exists())

    def test_resume_preflight_reports_missing_original_directory_without_creating_it(self):
        profile = self.run_worker({'operation': 'list'})['value']['profile']
        request = {'operation': 'resume-preflight', 'origin': profile['origin'], 'native': self.native,
                   'cwd': str(self.cwd), 'model': 'fixture-model', 'permissionMode': 'plan'}
        self.assertTrue(self.run_worker(request)['value']['ready'])
        self.cwd.rmdir()
        blocked = self.run_worker(request)['value']
        self.assertFalse(blocked['ready'])
        self.assertIn('原项目目录不存在', blocked['notice'])
        self.assertIn(str(self.cwd), blocked['notice'])
        self.assertFalse(self.cwd.exists())
        self.assertFalse((self.config/'mobile-bridge').exists())

    def test_create_and_resume_keep_stdio_permissions_without_new_optional_flag(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(remote_worker.os, 'chdir'), \
             patch('builtins.print'), \
             patch.object(remote_worker, 'pid_domain', return_value='fixture-remote-host'), \
             patch.object(remote_worker.os, 'execvpe') as execute:
            profile = remote_worker.profile()
            request = {'origin': profile['origin'], 'native': self.native,
                       'cwd': str(self.cwd), 'model': 'fixture-model',
                       'permissionMode': 'plan', 'scope': str(uuid.uuid4())}
            for operation in (remote_worker.create, remote_worker.resume):
                with self.subTest(operation=operation.__name__):
                    operation(request)
                    binary, arguments, env = execute.call_args.args
                    self.assertEqual(binary, profile['binary'])
                    self.assertNotIn('--permission-prompts', arguments)
                    self.assertEqual(arguments[arguments.index('--permission-prompt-tool') + 1], 'stdio')
                    self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.config))

    def test_resume_preflight_preserves_unknown_permission_and_rejects_changed_settings(self):
        profile = self.run_worker({'operation': 'list'})['value']['profile']
        request = {'operation': 'resume-preflight', 'origin': profile['origin'], 'native': self.native,
                   'cwd': str(self.cwd), 'model': 'fixture-model', 'permissionMode': 'plan'}
        self.assertFalse(self.run_worker({**request, 'cwd': str(self.root)})['ok'])
        events = [json.loads(line) for line in self.path.read_text().splitlines()]
        events[0].pop('permissionMode')
        self.path.write_text('\n'.join(json.dumps(event) for event in events)+'\n')
        blocked = self.run_worker({**request, 'permissionMode': 'default'})['value']
        self.assertFalse(blocked['ready'])
        self.assertIn('权限模式', blocked['notice'])

    def test_resume_preflight_worker_only_runs_readonly_auth_or_version_diagnostics(self):
        calls = []
        def diagnostic(arguments, **kwargs):
            self.assertEqual(arguments[0], str(self.binary.resolve()))
            self.assertIn(arguments[1:], (['auth', 'status', '--json'], ['--version']))
            self.assertEqual(kwargs['env']['CLAUDE_CONFIG_DIR'], str(self.config))
            calls.append(arguments)
            return SimpleNamespace(returncode=0, stdout='2.1.292\n' if arguments[1:] == ['--version']
                                   else '{"loggedIn":true}')
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(remote_worker.subprocess, 'run', side_effect=diagnostic), \
             patch.object(remote_worker.subprocess, 'Popen', side_effect=AssertionError('worker started a chat')) as popen, \
             patch.object(remote_worker.os, 'execvpe', side_effect=AssertionError('worker resumed a chat')) as execute:
            profile = remote_worker.profile()
            request = {'operation': 'resume-preflight', 'origin': profile['origin'], 'native': self.native,
                       'cwd': str(self.cwd), 'model': 'fixture-model', 'permissionMode': 'plan'}
            self.assertTrue(remote_worker.command(request)['ready'])
            self.cwd.rmdir()
            blocked = remote_worker.command(request)
            self.assertFalse(blocked['ready'])
            self.assertIn('原项目目录不存在', blocked['notice'])
            self.assertFalse(self.cwd.exists())
            events = [json.loads(line) for line in self.path.read_text().splitlines()]
            events[0].pop('permissionMode')
            self.path.write_text('\n'.join(json.dumps(event) for event in events)+'\n')
            unknown = remote_worker.command({**request, 'permissionMode': 'default'})
            self.assertFalse(unknown['ready'])
            self.assertIn('权限模式', unknown['notice'])
            popen.assert_not_called()
            execute.assert_not_called()
        self.assertTrue(calls)
        self.assertFalse((self.config/'mobile-bridge').exists())

    def test_worker_reads_utf8_chinese_history_independent_of_host_locale(self):
        path = self.root/'中文记录.jsonl'
        event = {'type': 'user', 'cwd': '/项目/中文目录',
                 'message': {'role': 'user', 'content': '中文标题\n正文内容'}}
        path.write_text(json.dumps(event, ensure_ascii=False)+'\n', encoding='utf-8')
        original = Path.open
        reads = []

        def opening(target, *args, **kwargs):
            if target == path:
                reads.append(kwargs.get('encoding'))
            return original(target, *args, **kwargs)

        with patch.object(Path, 'open', opening):
            self.assertEqual(remote_worker.events(path), [event])
        self.assertEqual(reads, ['utf-8'])
        saved_path = self.root/'中文项目'/(self.native + '.jsonl')
        saved_path.parent.mkdir()
        saved_path.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        saved = remote_worker.saved(saved_path, {'origin': 'fixture', 'directory': str(self.config)},
                                    include_events=True)
        self.assertEqual((saved['title'], saved['cwd']), ('中文标题', '/项目/中文目录'))
        self.assertEqual(saved['events'], [event])

    def test_standalone_history_pages_and_file_change_invalidate_cursor(self):
        with self.path.open('a') as target:
            for index in range(100):
                target.write(json.dumps({'type': 'assistant', 'uuid': f'a{index}', 'cwd': str(self.cwd),
                    'message': {'role': 'assistant', 'content': str(index)}}) + '\n')
        profile = self.run_worker({'operation': 'list'})['value']['profile']
        request = {'operation': 'history', 'origin': profile['origin'], 'native': self.native,
                   'scope': 'test-host-session', 'historyLimit': 40}
        recent = self.run_worker(request)['value']
        older = self.run_worker({**request, 'historyBefore': recent['history']['before']})['value']
        self.assertEqual(len(recent['events']), 40)
        self.assertEqual(recent['events'][0]['uuid'], 'a60')
        self.assertEqual(older['events'][-1]['uuid'], 'a59')
        with self.path.open('a') as target:
            target.write(json.dumps({'type': 'assistant', 'uuid': 'new', 'cwd': str(self.cwd),
                                     'message': {'role': 'assistant', 'content': 'new'}}) + '\n')
        self.assertFalse(self.run_worker({**request, 'historyBefore': recent['history']['before']})['ok'])

    def test_standalone_long_history_does_not_return_full_events(self):
        with self.path.open('a') as target:
            filler = json.dumps({'type': 'progress', 'data': 'x' * 900}) + '\n'
            target.write(filler * 24000)
            target.write(json.dumps({'type': 'assistant', 'uuid': 'tail', 'cwd': str(self.cwd),
                                     'message': {'role': 'assistant', 'content': 'tail'}}) + '\n')
        profile = self.run_worker({'operation': 'list'})['value']['profile']
        self.assertEqual(len(self.run_worker({'operation': 'list'})['value']['sessions']), 1)
        history = self.run_worker({'operation': 'history', 'origin': profile['origin'],
                                   'native': self.native, 'scope': 'test-host-session',
                                   'historyLimit': 1})['value']
        self.assertEqual([event['uuid'] for event in history['events']], ['tail'])
        self.assertGreater(self.path.stat().st_size, 20_000_000)

    def test_remote_page_reads_valid_final_line_and_ignores_saved_approval(self):
        with self.path.open('a') as target:
            target.write(json.dumps({'type': 'control_request', 'request_id': 'old-approval'}) + '\n')
            target.write(json.dumps({'type': 'assistant', 'uuid': 'last', 'cwd': str(self.cwd),
                                     'message': {'role': 'assistant', 'content': 'done'}}))
        result = remote_worker.history_page(self.path, 'fixture')
        self.assertEqual(result['events'][-1]['uuid'], 'last')
        self.assertFalse(any(event.get('type') == 'control_request' for event in result['events']))

    def test_history_uses_consistent_file_identity_for_path_and_descriptor(self):
        # Windows Python versions can expose different stat/fstat identities.
        stat = self.path.stat()
        path = Mock(wraps=self.path)
        path.stat.return_value = SimpleNamespace(st_dev=stat.st_dev, st_ino=stat.st_ino + 1,
            st_size=stat.st_size, st_mtime_ns=stat.st_mtime_ns, st_ctime_ns=stat.st_ctime_ns)
        recent = remote_worker.history_page(path, 'fixture', limit=1)
        older = remote_worker.history_page(path, 'fixture', before=recent['history']['before'])
        self.assertEqual(len(recent['events']), 1)
        self.assertEqual(len(older['events']), 1)
        self.assertEqual(older['events'][0]['message']['role'], 'user')

    def test_upload_is_private_immutable_and_bound_to_remote_profile(self):
        profile = self.run_worker({'operation': 'list'})['value']['profile']
        scope, identifier = str(uuid.uuid4()), str(uuid.uuid4())
        data = b'remote upload bytes'
        request = {'operation': 'upload', 'origin': profile['origin'], 'scope': scope,
                   'id': identifier, 'name': 'note.txt', 'sha256': hashlib.sha256(data).hexdigest(),
                   'data': __import__('base64').b64encode(data).decode()}
        first = self.run_worker(request)['value']['path']
        self.assertEqual(Path(first).read_bytes(), data)
        self.assertEqual(self.run_worker(request)['value']['path'], first)
        if os.name != 'nt':
            self.assertEqual(Path(first).stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.run_worker({**request, 'origin': 'other'})['ok'])

    def test_linux_registry_pid_start_and_domain_are_checked(self):
        found = {'directory': str(self.config)}
        pid = 32123
        registry = self.config/'sessions'/(str(pid)+'.json')
        entry = {'sessionId': self.native, 'pid': pid, 'procStart': '123456',
                 'pidDomain': 'linux:machine:pid:[123]'}
        registry.write_text(json.dumps(entry))
        with patch.object(remote_worker.sys, 'platform', 'linux'), \
             patch.object(remote_worker, 'pid_domain', return_value=entry['pidDomain']):
            with patch.object(remote_worker, 'linux_process_start', return_value='123456'):
                with self.assertRaisesRegex(ValueError, '其他终端运行'):
                    remote_worker.assert_session_closed(found, self.native)
            for token in (None, '123457'):
                with patch.object(remote_worker, 'linux_process_start', return_value=token):
                    remote_worker.assert_session_closed(found, self.native)
            entry['procStart'] = 'Wed Oct 14 10:00:00 2026'
            registry.write_text(json.dumps(entry))
            with self.assertRaisesRegex(ValueError, '无法核验'):
                remote_worker.assert_session_closed(found, self.native)
        entry['pidDomain'] = 'linux:other:pid:[456]'
        registry.write_text(json.dumps(entry))
        with patch.object(remote_worker, 'pid_domain', return_value='linux:machine:pid:[123]'):
            with self.assertRaisesRegex(ValueError, '无法核验'):
                remote_worker.assert_session_closed(found, self.native)

    def test_linux_domain_uses_machine_and_pid_namespace(self):
        original = remote_worker.sys.platform
        with patch.object(remote_worker.sys, 'platform', 'linux'), \
             patch.object(remote_worker.Path, 'read_text', return_value='machine-id\n'), \
             patch.object(remote_worker.os, 'readlink', return_value='pid:[123]'):
            self.assertEqual(remote_worker.pid_domain(), 'linux:machine-id:pid:[123]')
        self.assertEqual(remote_worker.sys.platform, original)

    def test_remote_origin_tracks_account_without_rotating_stable_identity(self):
        credentials = self.config/'.credentials.json'
        credentials.write_text('{"accessToken":"first"}')
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(remote_worker, 'auth_status', return_value={'loggedIn': True}):
            first = remote_worker.profile()['origin']
            credentials.write_text('{"accessToken":"second"}')
            self.assertNotEqual(remote_worker.profile()['origin'], first)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(remote_worker, 'auth_status', return_value={'loggedIn': True,
                                                                      'email': 'fixture@example.test'}):
            first = remote_worker.profile()['origin']
            credentials.write_text('{"accessToken":"rotated"}')
            self.assertEqual(remote_worker.profile()['origin'], first)
            os.environ['ANTHROPIC_API_KEY'] = 'different-account-key'
            self.assertNotEqual(remote_worker.profile()['origin'], first)


    def test_saved_error_only_history_has_no_recorded_model(self):
        user = json.loads(self.path.read_text().splitlines()[0])
        errors = [
            {'type': 'assistant', 'isApiErrorMessage': True,
             'message': {'role': 'assistant', 'model': 'error-placeholder', 'content': 'rate limited'}},
            {'type': 'assistant',
             'message': {'role': 'assistant', 'model': '<synthetic>', 'content': 'rate limited'}},
        ]
        for error in errors:
            with self.subTest(error=error):
                self.path.write_text('\n'.join(json.dumps(event) for event in (user, error))+'\n')
                before = self.path.read_bytes()
                row = remote_worker.saved(self.path, {'directory': str(self.config), 'origin': 'fixture-origin'})
                self.assertEqual(row['model'], '')
                self.assertEqual(row['nativeId'], self.native)
                self.assertEqual(self.path.read_bytes(), before)

    def test_saved_api_error_preserves_previous_real_model(self):
        original = self.path.read_text()
        errors = [
            {'type': 'assistant', 'isApiErrorMessage': True,
             'message': {'role': 'assistant', 'model': 'error-placeholder', 'content': 'rate limited'}},
            {'type': 'assistant',
             'message': {'role': 'assistant', 'model': '<synthetic>', 'content': 'rate limited'}},
        ]
        for error in errors:
            with self.subTest(error=error):
                self.path.write_text(original+json.dumps(error)+'\n')
                before = self.path.read_bytes()
                row = remote_worker.saved(self.path, {'directory': str(self.config), 'origin': 'fixture-origin'})
                self.assertEqual(row['model'], 'fixture-model')
                self.assertEqual(row['nativeId'], self.native)
                self.assertEqual(self.path.read_bytes(), before)


    @unittest.skipUnless(os.name == 'posix', 'Remote archive storage runs on verified POSIX targets')
    def test_archive_roundtrip_keeps_native_identity_and_archived_history_discoverable(self):
        found = {'binary': str(self.binary), 'directory': str(self.config), 'origin': 'synthetic-origin'}
        before = self.path.read_bytes()
        with patch.object(remote_worker, 'profile', return_value=found), \
             patch.object(remote_worker, 'archive_supported', return_value=True), \
             patch.object(remote_worker, 'archive_assert_no_cli'), \
             patch.object(remote_worker, 'assert_session_closed') as guard:
            result = remote_worker.command({'operation': 'archive', 'native': self.native,
                                            'origin': found['origin'], 'archived': True})
            self.assertTrue(result['archived'])
            self.assertTrue(result['archiveSafe'])
            self.assertEqual(guard.call_count, 2)
            self.assertTrue(all(call.args == (found,) for call in guard.call_args_list))
            self.assertFalse(self.path.exists())
            rows = remote_worker.sessions(found)
            self.assertEqual([row['nativeId'] for row in rows], [self.native])
            self.assertTrue(rows[0]['archived'])
            stored = remote_worker.session_path(found, self.native)
            self.assertEqual(stored.read_bytes(), before)
            detail = remote_worker.command({'operation': 'detail', 'native': self.native, 'origin': found['origin']})
            self.assertTrue(detail['archived'])
            restored = remote_worker.command({'operation': 'archive', 'native': self.native,
                                              'origin': found['origin'], 'archived': False})
            self.assertFalse(restored['archived'])
            self.assertEqual(self.path.read_bytes(), before)

    def test_remote_archive_rejects_unproven_capability_unknown_profile_registry_and_live_census(self):
        found = {'binary': str(self.binary), 'directory': str(self.config), 'origin': 'synthetic-origin'}
        request = {'operation': 'archive', 'native': self.native, 'origin': found['origin'], 'archived': True}
        before = self.path.read_bytes()
        with patch.object(remote_worker, 'profile', return_value=found), \
             patch.object(remote_worker, 'archive_supported', return_value=False):
            with self.assertRaises(ValueError): remote_worker.command(request)
        with patch.object(remote_worker, 'profile', return_value=found), \
             patch.object(remote_worker, 'archive_supported', return_value=True), \
             patch.object(remote_worker, 'assert_session_closed', side_effect=ValueError('unknown registry')):
            with self.assertRaises(ValueError): remote_worker.command(request)
        with patch.object(remote_worker, 'profile', return_value=found), \
             patch.object(remote_worker, 'archive_supported', return_value=True), \
             patch.object(remote_worker, 'archive_assert_no_cli', side_effect=ValueError('live writer')), \
             patch.object(remote_worker, 'assert_session_closed'):
            with self.assertRaisesRegex(ValueError, 'live writer'): remote_worker.command(request)
        self.assertEqual(self.path.read_bytes(), before)

    def test_incomplete_archive_keeps_other_remote_sessions_visible(self):
        bad = str(uuid.uuid4());bundle = self.config/'bridge-archives'/bad;bundle.mkdir(parents=True)
        (bundle/'manifest.json').write_text('{}')
        found = {'binary': str(self.binary), 'directory': str(self.config), 'origin': 'synthetic-origin'}
        with patch.object(remote_worker, 'profile', return_value=found), \
             patch.object(remote_worker, 'archive_supported', return_value=False):
            listing = remote_worker.command({'operation': 'list'})
            self.assertFalse(listing['complete'])
            rows = {row['nativeId']: row for row in listing['sessions']}
            self.assertIn(self.native, rows)
            self.assertTrue(rows[bad]['archived'])
            self.assertTrue(rows[bad]['archiveIncomplete'])
            self.assertFalse(rows[bad]['runtimeKnown'])
            self.assertFalse(rows[bad]['capabilities']['unarchive'])
            self.assertEqual(remote_worker.session(found, self.native)['nativeId'], self.native)
            with self.assertRaises(ValueError): remote_worker.session(found, bad)

    def test_native_binary_proof_is_rechecked_immediately_before_remote_write(self):
        found = {'binary': str(self.binary), 'directory': str(self.config), 'origin': 'synthetic-origin'}
        before = self.path.read_bytes()
        with patch.object(remote_worker, 'profile', return_value=found), \
             patch.object(remote_worker, 'archive_supported', side_effect=[True, False]) as supported, \
             patch.object(remote_worker, 'archive_assert_no_cli'), \
             patch.object(remote_worker, 'assert_session_closed'), \
             patch.object(remote_worker, 'archive_file_change') as change:
            with self.assertRaisesRegex(ValueError, '二进制归档能力已变化'):
                remote_worker.command({'operation': 'archive', 'native': self.native,
                                       'origin': found['origin'], 'archived': True})
            self.assertEqual(supported.call_count, 2)
            change.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    @unittest.skipUnless(os.name == 'posix', 'Remote archive storage runs on verified POSIX targets')
    def test_explicit_remote_archive_state_verifies_archived_content(self):
        found = {'binary': str(self.binary), 'directory': str(self.config), 'origin': 'synthetic-origin'}
        request = {'operation': 'archive', 'native': self.native, 'origin': found['origin'], 'archived': True}
        with patch.object(remote_worker, 'profile', return_value=found), \
             patch.object(remote_worker, 'archive_supported', return_value=True), \
             patch.object(remote_worker, 'archive_assert_no_cli'), \
             patch.object(remote_worker, 'assert_session_closed'):
            remote_worker.command(request)
            stored = remote_worker.session_path(found, self.native)
            stored.write_bytes(stored.read_bytes()+b'\n')
            # Passive discovery remains metadata-only; an explicit mutation state
            # must verify bytes before reporting a safe archived result.
            self.assertTrue(remote_worker.sessions(found)[0]['archived'])
            with self.assertRaises(ValueError):
                remote_worker.command({**request, 'operation': 'archive-state'})

    def test_full_profile_guard_rejects_another_live_native_session(self):
        other = str(uuid.uuid4());pid = 4321
        (self.config/'sessions'/(str(pid)+'.json')).write_text(json.dumps({
            'pid': pid, 'sessionId': other, 'procStart': '123456', 'pidDomain': 'linux:fixture:pid:[123]'}))
        found = {'directory': str(self.config)}
        with patch.object(remote_worker.sys, 'platform', 'linux'), \
             patch.object(remote_worker, 'pid_domain', return_value='linux:fixture:pid:[123]'), \
             patch.object(remote_worker, 'linux_process_start', return_value='123456'):
            with self.assertRaises(ValueError): remote_worker.assert_session_closed(found)



class RemoteAdapterContract(unittest.TestCase):
    def setUp(self):
        self.native = str(uuid.uuid4())
        self.hosts = Mock()
        self.hosts.hosts.return_value = {'ssh:test': {'alias': 'fixture-loopback', 'displayName': 'Test host'}}
        self.cli = RemoteClaudeCli(self.hosts)
        self.addCleanup(self.cli.close)
        self.row = {'nativeId': self.native, 'origin': 'default:/remote/.claude:abc',
                    'cwd': '/remote/project', 'model': 'fixture-model', 'permissionMode': 'plan',
                    'permissionKnown': True, 'updatedAt': 1, 'title': 'remote fixture'}

    def test_confirmed_remote_archive_stops_matching_owner_before_remote_writer_checks(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], self.row)
        sid = row['id']; self.cli.index[sid] = row
        owned = SimpleNamespace(origin=row['origin'], native=row['nativeId'], error=None,
            process=Mock(), condition=threading.Condition(), pending={}, active=True, model=None, permission=None, catalog={})
        owned.process.poll.return_value = None
        owned.release_for_archive = Mock(side_effect=lambda: owned.process.poll.configure_mock(return_value=0))
        self.cli.owned[sid] = owned
        def remote_call(alias, request):
            if request['operation'] == 'archive':
                self.assertEqual(owned.process.poll(), 0)
            return {**self.row, 'archived': request['operation'] == 'archive', 'archiveSafe': True,
                    'capabilities': {'archive': True, 'unarchive': True}}
        with patch.object(self.cli, '_remote', side_effect=remote_call):
            self.assertFalse(self.cli.call('archive-state', sid, {})['archiveSafe'])
            owned.release_for_archive.assert_not_called()
            with self.assertRaises(BridgeUnavailable):self.cli.call('archive', sid, {'archived': True})
            self.assertTrue(self.cli.call('archive', sid, {'archived': True, 'disconnect': True})['archived'])
            owned.release_for_archive.assert_called_once_with()
        self.cli.owned.clear()

    def test_ssh_json_uses_utf8_for_unicode_requests(self):
        captured = {}
        def read(command, **options):
            captured['bytes'] = options['stdin'].read()
            return SimpleNamespace(returncode=0, stdout='{"ok":true,"value":{}}')
        with patch.object(remote, '_command', return_value=['ssh', 'fixture']), \
             patch.object(remote.subprocess, 'run', side_effect=read) as run:
            self.assertEqual(remote._remote('fixture', {'cwd': '/项目'}), {})
        self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')
        self.assertEqual(json.loads(captured['bytes']), {'cwd': '/项目'})
        self.assertTrue(run.call_args.kwargs['stdin'].closed)

    def test_host_and_profile_scope_and_passive_listing(self):
        with patch.object(remote, '_remote', return_value={'sessions': [self.row]}) as request, \
             patch.object(remote.subprocess, 'Popen') as popen:
            self.cli._refresh(self.hosts.hosts())
            listing = self.cli.listing()
        popen.assert_not_called()
        sid = listing['sessions'][0]['id']
        self.assertEqual(listing['sessions'][0]['host'], 'ssh:test')
        self.assertEqual(request.call_args.args[0], 'fixture-loopback')
        self.assertNotEqual(sid, remote.model.gateway_id('cli:'+self.row['origin'], self.native))
        self.assertTrue(self.cli.contains(sid))
        self.hosts.hosts.return_value['ssh:test']['alias'] = 'different'
        with self.assertRaisesRegex(ValueError, '主机配置已变化'):
            self.cli._get(sid)

    def test_detail_uses_remote_native_history_usage(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], self.row)
        self.cli.index[row['id']] = row
        events = [{'type': 'assistant', 'message': {'id': 'last', 'model': 'remote-model',
                   'usage': {'input_tokens': 4, 'cache_creation_input_tokens': 5,
                             'cache_read_input_tokens': 6}}}]
        def response(_alias, request, **_kwargs):
            if request['operation'] == 'resume-preflight':
                return {'ready': True}
            if request['operation'] == 'history':
                return {'events': events, 'history': {'before': None, 'hasMore': False}, 'stamp': [1, 2, 3, 4]}
            return {**self.row, 'contextEvents': events, 'stamp': [1, 2, 3, 4]}
        with patch.object(remote, '_remote', side_effect=response):
            result = self.cli.call('detail', row['id'], {})
        self.assertEqual(result['session']['contextUsage']['usedTokens'], 15)
        self.assertEqual(result['session']['contextUsage']['model'], 'remote-model')

    def test_detail_page_uses_cached_metadata_and_passes_cursor(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'],
                            {**self.row, 'stamp': [1, 2, 3, 4]})
        self.cli.index[row['id']] = row
        calls = []
        def response(_alias, request, **_kwargs):
            calls.append(request)
            if request['operation'] == 'resume-preflight':
                return {'ready': False}
            if request['operation'] == 'detail':
                self.fail('Unchanged metadata should not be rescanned')
            return {'events': [{'type': 'assistant', 'uuid': 'a1',
                                'message': {'role': 'assistant', 'content': 'page'}}],
                    'history': {'before': 'older', 'hasMore': True}, 'stamp': [1, 2, 3, 4]}
        with patch.object(remote, '_remote', side_effect=response):
            result = self.cli.call('detail', row['id'], {'historyBefore': 'cursor', 'historyLimit': 10})
        self.assertEqual(result['history'], {'before': 'older', 'hasMore': True})
        self.assertEqual(calls[0]['operation'], 'history')
        self.assertEqual((calls[0]['historyBefore'], calls[0]['historyLimit']), ('cursor', 10))

    def test_live_detail_uses_recent_page_and_only_the_unpersisted_stream_tail(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'],
                            {**self.row, 'stamp': [1, 2, 3, 4]})
        sid = row['id']
        self.cli.index[sid] = row
        events = [{'type': 'assistant', 'uuid': f'a{index}',
                   'message': {'role': 'assistant', 'content': str(index)}} for index in range(1000)]
        result = lambda rid: {'type': 'result', 'uuid': rid, 'subtype': 'success',
                              'is_error': False, 'num_turns': 1}
        live = events[:1] + [result('R1')] + events[1:] + [result('R2'),
            {'type': 'assistant', 'uuid': 'new', 'message': {'role': 'assistant', 'content': 'not saved'}}]
        owned = SimpleNamespace(origin=row['origin'], process=Mock(), error=None,
            condition=threading.Condition(), events=live, pending={}, active=False,
            catalog={}, model=None, permission=None)
        owned.process.poll.return_value = None
        self.cli.owned[sid] = owned
        self.addCleanup(self.cli.owned.clear)
        def response(_alias, request, **_kwargs):
            self.assertEqual(request['operation'], 'history')
            page = events[-80:-40] if request['historyBefore'] else events[-40:]
            return {'events': page, 'history': {'before': 'older', 'hasMore': True}, 'stamp': row['stamp']}
        with patch.object(self.cli, '_remote', side_effect=response):
            recent = self.cli.call('detail', sid, {'historyLimit': 40})
            older = self.cli.call('detail', sid, {'historyBefore': 'older', 'historyLimit': 40})
        self.assertEqual([message['eventId'] for message in recent['messages']],
                         [f'a{index}' for index in range(960, 1000)] + ['new'])
        self.assertEqual([message['eventId'] for message in older['messages']],
                         [f'a{index}' for index in range(920, 960)])
        self.assertEqual([turn['turnId'] for turn in recent['turns']], ['R1', 'R2'])
        self.assertEqual(older['turns'], [])
        self.assertIs(owned.events, live)

    def test_detail_exposes_remote_directory_preflight_before_resume(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], {**self.row, 'stamp': [1, 2, 3, 4]})
        self.cli.index[row['id']] = row
        notice = '原项目目录不存在：/remote/project，请在远端恢复该目录后重试'
        def response(_alias, request, **_kwargs):
            if request['operation'] == 'resume-preflight':
                self.assertEqual((request['native'], request['cwd'], request['permissionMode']),
                                 (self.native, '/remote/project', 'plan'))
                return {'ready': False, 'notice': notice}
            if request['operation'] == 'history':
                return {'events': [], 'history': {'before': None, 'hasMore': False}, 'stamp': row['stamp']}
            return {**self.row, 'stamp': row['stamp']}
        with patch.object(remote, '_remote', side_effect=response), patch.object(remote.subprocess, 'Popen') as popen:
            result = self.cli.call('detail', row['id'], {})
        self.assertEqual(result['notice'], notice)
        self.assertFalse(result['capabilities']['resume'])
        popen.assert_not_called()

    def test_live_ssh_catalog_and_settings_use_same_native_controls(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], self.row)
        self.cli.index[row['id']] = row
        process = Mock()
        process.poll.return_value = None
        applied = {'model': 'remote-old', 'effort': 'medium'}
        models = [{'value': 'default', 'resolvedModel': 'remote-current', 'displayName': 'Default'},
                  {'value': 'sonnet', 'resolvedModel': 'remote-sonnet', 'displayName': 'Sonnet',
                   'supportsEffort': True, 'supportedEffortLevels': ['low', 'high']}]
        controls = []
        def control(request):
            controls.append(request)
            if request['subtype'] == 'get_settings':
                return {'applied': dict(applied), 'sources': []}
            if request['subtype'] == 'list_models':
                return {'models': models}
            if request['subtype'] == 'set_model':
                applied['model'] = request['model']
            if request['subtype'] == 'apply_flag_settings':
                applied['effort'] = request['settings']['effortLevel']
            return {}
        owned = SimpleNamespace(origin=row['origin'], process=process, error=None,
                                condition=threading.Condition(), active=False, pending={},
                                model=None, model_selection=None, permission=None, events=[],
                                catalog={'models': []}, control=control, close=Mock())
        self.cli.owned[row['id']] = owned
        catalog = self.cli.call('catalog', row['id'], {'refresh': True})
        self.assertEqual(catalog['models'][1]['id'], 'remote-sonnet')
        self.assertEqual(catalog['models'][1]['efforts'], ['auto', 'low', 'high'])
        self.assertEqual(controls[0], {'subtype': 'list_models'})
        self.cli.call('settings', row['id'], {'model': 'remote-sonnet', 'effort': 'high'})
        self.assertEqual(owned.model, 'remote-sonnet')
        self.assertIn({'subtype': 'apply_flag_settings', 'settings': {'effortLevel': 'high'}}, controls)

    def test_project_discovery_uses_one_bounded_host_probe(self):
        self.hosts.projects.return_value = [{'host': 'ssh:test', 'cwd': '/remote/project'}]
        with patch.object(remote, '_remote', return_value={
                'profile': {'origin': self.row['origin']}, 'authReady': True,
                'sessions': [self.row]}) as request:
            projects = self.cli.projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]['cwd'], '/remote/project')
        request.assert_called_once_with('fixture-loopback', {'operation': 'projects'}, timeout=12)

    def test_offline_host_refresh_does_not_block_cached_list_or_live_controls(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], self.row)
        self.cli.index[row['id']] = row
        owned = SimpleNamespace(origin=row['origin'], process=Mock(), error=None,
                                condition=threading.Condition(), active=True, pending={},
                                model=None, permission=None, events=[], catalog={},
                                control=Mock(return_value={}), close=Mock())
        owned.process.poll.return_value = None
        self.cli.owned[row['id']] = owned
        self.cli.host_snapshot = self.hosts.hosts()
        self.cli.last_refresh = __import__('time').monotonic()-11
        pause = threading.Event()
        entered = threading.Event()
        def offline(*_args, **_kwargs):
            entered.set()
            pause.wait(2)
            raise BridgeUnavailable('offline')
        try:
            with patch.object(remote, '_remote', side_effect=offline):
                start = __import__('time').monotonic()
                listed = self.cli.listing()
                self.assertLess(__import__('time').monotonic()-start, .25)
                self.assertEqual(listed['sessions'][0]['id'], row['id'])
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.cli.contains(row['id']))
                self.assertEqual(self.cli.call('stop', row['id'], {}), {'status': 'accepted'})
                owned.control.assert_called_once_with({'subtype': 'interrupt'})
        finally:
            pause.set()

    def test_remote_upload_replaces_gateway_path_before_native_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)/'local.txt'; local.write_bytes(b'ledger-verified bytes')
            self.row.update(id='session-id', alias='fixture-loopback')
            identifier = str(uuid.uuid4())
            with patch.object(remote, '_remote', return_value={'path': '/remote/private/original.txt'}) as transfer:
                result = self.cli._upload_files(self.row, [{'id': identifier, 'name': 'local.txt', 'path': str(local)}])
            self.assertEqual(result, [{'name': 'local.txt', 'path': '/remote/private/original.txt'}])
            self.assertNotIn(str(local), str(result))
            self.assertEqual(transfer.call_args.args[1]['operation'], 'upload')
            self.assertEqual(transfer.call_args.args[1]['scope'], DesktopUploads.scope('claude', 'session-id'))

    def test_disconnected_send_has_unknown_receipt_and_is_not_replayed(self):
        sid = 'session-id'
        row = {**self.row, 'id': sid, 'alias': 'fixture-loopback'}
        owned = SimpleNamespace(origin=row['origin'], process=Mock(), error=None,
                                condition=threading.Condition(), active=False, pending={},
                                model=None, permission=None, events=[], catalog={})
        owned.process.poll.return_value = None
        owned.close = Mock()
        owned.write = Mock(side_effect=BridgeUnavailable('Claude Code 写入结果不明，请检查会话'))
        self.cli.owned[sid] = owned
        body = {'text': 'send once', 'resolvedFiles': [], 'resolvedImages': []}
        with patch.object(self.cli, '_get', return_value=row):
            with self.assertRaisesRegex(BridgeUnavailable, '结果不明'):
                self.cli.call('send', sid, body)
            with self.assertRaisesRegex(BridgeUnavailable, '请等待'):
                self.cli.call('send', sid, body)
        owned.write.assert_called_once()
        owned.process.poll.return_value = 1


    def test_archive_rechecks_host_and_owned_executor_and_propagates_native_state(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], self.row)
        sid = row['id'];self.cli.index[sid] = row
        with patch.object(self.cli, '_remote', return_value={**self.row, 'archived': True,
                         'capabilities': {'archive': True, 'unarchive': True}, 'archiveSafe': True}) as request:
            result = self.cli.call('archive', sid, {'archived': True})
            self.assertTrue(result['archived'])
            self.assertEqual(request.call_args.args, ('fixture-loopback', {'operation': 'archive',
                             'native': self.native, 'origin': self.row['origin'], 'archived': True}))
            self.assertEqual(result['id'], sid)
            self.cli.owned[sid] = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), origin='foreign', native=self.native, error=None)
            request.reset_mock()
            with self.assertRaises(BridgeUnavailable): self.cli.call('archive-state', sid, {})
            request.assert_not_called()
            self.cli.owned.clear()
            self.hosts.hosts.return_value = {}
            with self.assertRaises(ValueError): self.cli.call('archive', sid, {'archived': False})
            request.assert_not_called()

    def test_archive_identity_change_is_rejected(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], self.row);sid = row['id'];self.cli.index[sid] = row
        with patch.object(self.cli, '_remote', return_value={**self.row, 'origin': 'another-profile'}):
            with self.assertRaisesRegex(ValueError, '身份已变化'): self.cli.call('archive-state', sid, {})

    def test_incomplete_archive_rows_preserve_unknown_runtime_and_list_completeness(self):
        broken = {**self.row, 'archived': True, 'archiveIncomplete': True, 'runtimeKnown': False,
                  'capabilities': {'archive': False, 'unarchive': False}}
        with patch.object(self.cli, '_remote', return_value={'profile': {'origin': self.row['origin']},
                         'sessions': [broken], 'complete': False}):
            self.cli._refresh(self.hosts.hosts())
        listing = self.cli.listing()
        self.assertFalse(listing['complete'])
        self.assertFalse(listing['sessions'][0]['runtimeKnown'])
        self.assertEqual(listing['sessions'][0]['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
