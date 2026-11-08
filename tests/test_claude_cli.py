import asyncio
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bridge.clients.claude import model
from bridge.clients.claude.adapter import Claude
from bridge.clients.claude.cli_session import ClaudeCli, ClaudeControlRejected, ResumePreflightBlocked, _Owned, _skill_catalog, _model_catalog, _cli_catalog, _cli_selection, _set_cli_settings, _auth_ready, _events, _saved, _permission_prompt_args
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions
from bridge.clients.uploads import DesktopUploads
from bridge.platforms.windows import claude_process


class ClaudeCliContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root/'project'
        self.cwd.mkdir()
        self.config = self.root/'config'
        (self.config/'sessions').mkdir(parents=True)
        folder = self.config/'projects'/'fixture'
        folder.mkdir(parents=True)
        self.native = str(uuid.uuid4())
        self.sid = model.gateway_id('cli:fixture:account', self.native)
        self.path = folder/(self.native + '.jsonl')
        self.path.write_text('\n'.join(json.dumps(event) for event in [
            {'type': 'user', 'cwd': str(self.cwd), 'message': {'role': 'user', 'content': 'Fixture request'}},
            {'type': 'mode', 'mode': 'plan'},
            {'type': 'assistant', 'cwd': str(self.cwd), 'message': {'role': 'assistant', 'model': 'fixture-model',
                                                                  'content': [{'type': 'text', 'text': 'Fixture answer'}]}},
        ]) + '\n')
        self.profile = {'binary': '/fixture/claude', 'directory': self.config, 'origin': 'fixture:account'}
        self.profile_patch = patch('bridge.clients.claude.cli_session._profile', return_value=self.profile)
        self.auth_patch = patch('bridge.clients.claude.cli_session._auth_ready', return_value=True)
        self.prompt_patch = patch('bridge.clients.claude.cli_session._permission_prompt_args',
                                  return_value=['--permission-prompts', 'host'])
        self.profile_patch.start(); self.auth_patch.start(); self.prompt_patch.start()
        self.addCleanup(self.profile_patch.stop); self.addCleanup(self.auth_patch.stop)
        self.addCleanup(self.prompt_patch.stop)
        self.cli = ClaudeCli(self.root/'uploads')

    @unittest.skipUnless(os.name == 'posix', 'Real Claude file archive requires POSIX ownership and fsync semantics')
    def test_confirmed_archive_releases_owned_executor_before_file_and_writer_checks(self):
        from bridge.clients.claude import file_archive
        owned = SimpleNamespace(origin=self.profile['origin'], native=self.native, error=None,
            process=Mock(), condition=threading.Condition(), pending={}, active=True, model=None, permission=None, catalog={})
        owned.process.poll.return_value = None
        owned.release_for_archive = Mock(side_effect=lambda: owned.process.poll.configure_mock(return_value=0))
        self.cli.owned[self.native] = owned
        before = self.path.read_bytes()
        def stopped(*args):
            self.assertEqual(owned.process.poll(), 0)
        with patch.object(file_archive, 'supported', return_value=True), \
             patch('bridge.clients.claude.cli_session.assert_session_closed', side_effect=stopped), \
             patch.object(file_archive, 'assert_no_cli', side_effect=stopped):
            state = self.cli.call('archive-state', self.sid, {})
            self.assertFalse(state['archiveSafe'])
            owned.release_for_archive.assert_not_called()
            with self.assertRaises(BridgeUnavailable):
                self.cli.call('archive', self.sid, {'archived': True})
            result = self.cli.call('archive', self.sid, {'archived': True, 'disconnect': True})
            self.assertTrue(result['archived'])
            owned.release_for_archive.assert_called_once_with()
            self.assertFalse(self.path.exists())
            self.cli.call('archive', self.sid, {'archived': False})
            self.assertEqual(self.path.read_bytes(), before)
            owned.release_for_archive.assert_called_once_with()
        self.cli.owned.clear()

    def test_saved_list_and_detail_are_passive_and_closed(self):
        with patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            listing = self.cli.listing()
            detail = self.cli.call('detail', self.sid, {})
        popen.assert_not_called()
        self.assertEqual([row['id'] for row in listing['sessions']], [self.sid])
        self.assertEqual(detail['session']['connectionState'], 'closed')
        self.assertEqual(detail['session']['cwd'], str(self.cwd))
        self.assertEqual(detail['session']['permissionMode'], 'plan')
        self.assertTrue(detail['capabilities']['resume'])
        self.assertFalse(detail['capabilities']['send'])

    def test_synthetic_assistant_model_does_not_replace_real_model(self):
        with self.path.open('a') as target:
            target.write(json.dumps({'type': 'assistant', 'cwd': str(self.cwd),
                                     'message': {'model': '<synthetic>', 'content': []}}) + '\n')
        self.assertEqual(self.cli.listing()['sessions'][0]['model'], 'fixture-model')

    def test_synthetic_only_history_resumes_without_forcing_model(self):
        events = self.path.read_text().replace('"model": "fixture-model"', '"model": "<synthetic>"')
        self.path.write_text(events)
        self.assertEqual(self.cli.listing()['sessions'][0]['model'], '')
        process = Mock()
        process.poll.return_value = None
        with patch('bridge.clients.claude.cli_session.subprocess.Popen', return_value=process) as popen, \
             patch('bridge.clients.claude.cli_session._Owned') as owner_type:
            owner = owner_type.return_value
            owner.origin = self.profile['origin']
            owner.process = process
            owner.catalog = {'current_permission_mode': 'plan'}
            owner.pending = {}
            owner.active = False
            owner.error = None
            owner.control.return_value = owner.catalog
            self.assertEqual(self.cli.call('resume', self.sid, {})['status'], 'ready')
        self.assertNotIn('--model', popen.call_args.args[0])

    def test_saved_context_uses_latest_native_assistant_reading(self):
        event = {'type': 'assistant', 'cwd': str(self.cwd), 'message': {'id': 'last', 'role': 'assistant',
                 'model': 'fixture-model', 'usage': {'input_tokens': 12,
                 'cache_creation_input_tokens': 30, 'cache_read_input_tokens': 40}}}
        with self.path.open('a') as target:
            target.write(json.dumps(event)+'\n')
        listed = self.cli.listing()['sessions'][0]
        self.assertEqual(listed['contextUsage']['usedTokens'], 82)
        self.assertIsNone(listed['contextUsage']['contextWindow'])
        self.assertEqual(self.cli.call('detail', self.sid, {})['session']['contextUsage']['usedTokens'], 82)

    def test_live_context_supersedes_saved_reading_and_compaction_clears_it(self):
        self.cli.listing()
        owner = SimpleNamespace(origin=self.profile['origin'], process=Mock(), error=None,
                                condition=threading.Condition(), pending={}, active=True,
                                events=[{'type': 'assistant', 'message': {'id': 'live',
                                         'usage': {'input_tokens': 7, 'cache_read_input_tokens': 11}}}],
                                model=None, permission=None, catalog={})
        owner.process.poll.return_value = None
        self.cli.owned[self.native] = owner
        self.assertEqual(self.cli.call('detail', self.sid, {})['session']['contextUsage']['usedTokens'], 18)
        owner.events.append({'type': 'system', 'subtype': 'compact_boundary'})
        result = self.cli.call('detail', self.sid, {})['session']
        self.assertIsNone(result['contextUsage'])
        self.assertEqual(result['contextUsageStatus'], 'unavailable')

    def test_closed_adapter_status_never_queries_auth(self):
        self.cli.close()
        with patch('bridge.clients.claude.cli_session._auth_ready',
                   side_effect=AssertionError('closed adapter started auth')):
            self.assertEqual(self.cli.status(), {'cliAvailable': False, 'cliAuthReady': False})

    def test_display_status_does_not_wait_for_cli_auth(self):
        entered, release = threading.Event(), threading.Event()
        def slow_auth(profile):
            entered.set()
            release.wait(2)
            return True
        with patch('bridge.clients.claude.cli_session._auth_ready', side_effect=slow_auth) as auth:
            start = time.monotonic()
            try:
                self.assertEqual(self.cli.status(), {'cliAvailable': True, 'cliAuthReady': False})
                self.assertLess(time.monotonic()-start, .2)
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.cli.status(), {'cliAvailable': True, 'cliAuthReady': False})
                auth.assert_called_once()
            finally:
                release.set()
            for _ in range(100):
                if self.cli.status()['cliAuthReady']:
                    break
                time.sleep(.01)
            self.assertTrue(self.cli.status()['cliAuthReady'])
            auth.assert_called_once()

    def test_display_status_does_not_reuse_expired_or_other_profile_auth(self):
        entered, release = threading.Event(), threading.Event()
        def slow_auth(profile):
            entered.set()
            release.wait(2)
            return False
        with patch('bridge.clients.claude.cli_session._auth_ready', side_effect=slow_auth):
            self.cli.auth_cache = (self.profile['origin'], time.time()-1, True)
            try:
                self.assertEqual(self.cli.status(), {'cliAvailable': True, 'cliAuthReady': False})
                self.assertTrue(entered.wait(1))
            finally:
                release.set()
            for _ in range(100):
                with self.cli.lock:
                    if not self.cli.auth_refreshing:
                        break
                time.sleep(.01)
            self.assertEqual(self.cli.status(), {'cliAvailable': True, 'cliAuthReady': False})

            other = {**self.profile, 'origin': 'other:account'}
            self.cli.profile_resolver = lambda: other
            self.cli.auth_cache = (self.profile['origin'], time.time()+15, True)
            self.assertEqual(self.cli.status(), {'cliAvailable': True, 'cliAuthReady': False})
            for _ in range(100):
                with self.cli.lock:
                    if not self.cli.auth_refreshing:
                        break
                time.sleep(.01)
            self.assertEqual(self.cli.status(), {'cliAvailable': True, 'cliAuthReady': False})

    def test_fresh_auth_ignores_display_cache(self):
        self.cli.auth_cache = (self.profile['origin'], time.time()+15, True)
        with patch('bridge.clients.claude.cli_session._auth_ready', return_value=False) as auth:
            self.assertFalse(self.cli._ready(self.profile, fresh=True))
        auth.assert_called_once_with(self.profile)

    def test_desktop_readiness_does_not_scan_cli_history(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.status.return_value = {'connected': True, 'installed': True}
        adapter.discovery = {'installed': True}
        adapter.directory = self.root/'adapter'
        adapter.desktop_listing.return_value = {'sessions': [{'id': 'desktop-session'}]}
        manager.config.setdefault('enabled', {})['claude'] = True
        manager.adapters['claude'] = adapter
        manager.adapters['deepseek'] = Mock()
        manager.adapters['deepseek'].call.return_value = {'connected': False}
        manager.config['discovered'] = {'claude': {'installed': True, 'executable': '/fixture/claude'},
                                        'deepseek': {'installed': False}}
        with patch.object(manager, '_read', side_effect=lambda provider, action, *args:
                          {'models': ['fixture-model']} if action == 'catalog' else
                          self.fail('Desktop readiness scanned merged CLI history')), \
             patch('bridge.clients.lifecycle.inspect_clients', return_value={}):
            rows = manager.clients(refresh=True)['clients']
        adapter.desktop_listing.assert_called_once_with()
        self.assertTrue(next(row for row in rows if row['id'] == 'claude')['configured'])

    def test_known_desktop_catalog_does_not_probe_cli_history(self):
        adapter = Claude(self.root/'adapter', auto_connect=False)
        self.addCleanup(adapter.close)
        session = {'sessionId': 'desktop-session', 'cwd': str(self.cwd), 'isRunning': False}
        saved = model.session('code', session)
        adapter.index[saved['id']] = saved
        catalog = {'models': [{'id': 'desktop-model'}]}
        adapter.desktop = Mock(capabilities={'code': []})
        adapter.desktop.call = AsyncMock(side_effect=[session, catalog])
        with patch.object(adapter, 'status', return_value={'connected': True}), \
             patch.object(adapter.remote_cli, 'contains', side_effect=AssertionError('SSH CLI history probe')), \
             patch.object(adapter.cli, 'contains', side_effect=AssertionError('local CLI history probe')):
            result = asyncio.run(adapter.dispatch('catalog', saved['id'], {}))
        self.assertEqual(result['models'], catalog['models'])
        self.assertEqual(adapter.desktop.call.await_args_list[0].args, ('code', 'getSession', 'desktop-session'))

    def test_tool_only_first_user_record_keeps_listing_readable(self):
        events = self.path.read_text().splitlines()
        first = json.loads(events[0])
        first['message']['content'] = [{'type': 'tool_result', 'content': 'fixture'}]
        events[0] = json.dumps(first)
        self.path.write_text('\n'.join(events) + '\n')
        self.assertEqual(self.cli.listing()['sessions'][0]['title'], 'Claude Code 会话')

    def test_utf8_history_keeps_chinese_filename_cwd_title_and_body(self):
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
            self.assertEqual(_events(path), [event])
        self.assertEqual(reads, ['utf-8'])
        saved_path = self.root/'中文项目'/(self.native + '.jsonl')
        saved_path.parent.mkdir()
        saved_path.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        saved = _saved(saved_path, self.profile['origin'])
        self.assertEqual((saved['title'], saved['cwd']), ('中文标题', '/项目/中文目录'))

    def test_create_accepts_durably_saved_user_uuid_without_replay_event(self):
        process = Mock()
        process.poll.return_value = None
        with patch('bridge.clients.claude.cli_session.subprocess.Popen', return_value=process) as popen, \
             patch('bridge.clients.claude.cli_session._Owned') as owner_type:
            owner = owner_type.return_value
            owner.condition = threading.Condition()
            owner.error = None
            owner.finished = False
            owner.catalog = {}
            owner.control.return_value = {}

            def save_user(frame):
                native = owner_type.call_args.args[2]
                path = self.path.parent/(native + '.jsonl')
                path.write_text(json.dumps({'type': 'user', 'uuid': frame['uuid'],
                                            'cwd': str(self.cwd), 'permissionMode': 'default',
                                            'message': frame['message']}) + '\n')

            owner.write.side_effect = save_user
            created = self.cli.create(str(self.cwd), self.profile['origin'], 'First request')
        self.assertEqual(created['status'], 'ready')
        self.assertEqual(popen.call_args.kwargs['encoding'], 'utf-8')
        owner.write.assert_called_once()

    def test_resume_uses_original_config_cwd_model_permission_and_no_prompt(self):
        process = Mock()
        process.poll.return_value = None
        process.stdout = iter(())
        process.stdin = Mock()
        with patch('bridge.clients.claude.cli_session.subprocess.Popen', return_value=process) as popen, \
             patch('bridge.clients.claude.cli_session._Owned') as owner_type:
            owner = owner_type.return_value
            owner.origin = self.profile['origin']
            owner.process = process
            owner.catalog = {'current_permission_mode': 'plan'}
            owner.pending = {}
            owner.active = False
            owner.model = None
            owner.permission = 'plan'
            owner.error = None
            owner.events = []
            owner.control.return_value = {'current_permission_mode': 'plan'}
            self.assertEqual(self.cli.call('resume', self.sid, {})['status'], 'ready')
            self.assertEqual(self.cli.call('resume', self.sid, {})['status'], 'ready')
            owner.control.assert_called_once_with({'subtype': 'initialize', 'hooks': None}, timeout=30)
            args, kwargs = popen.call_args
            self.assertEqual(kwargs['cwd'], str(self.cwd.resolve()))
            self.assertEqual(kwargs['encoding'], 'utf-8')
            self.assertEqual(kwargs['env']['CLAUDE_CONFIG_DIR'], str(self.config))
            self.assertEqual(args[0][args[0].index('--permission-mode') + 1], 'plan')
            self.assertEqual(args[0][args[0].index('--model') + 1], 'fixture-model')
            self.assertIn('--resume', args[0])
            scope = str((self.root/'uploads'/DesktopUploads.scope('claude', self.sid)).resolve())
            self.assertEqual(args[0][args[0].index('--add-dir') + 1], scope)
            self.assertNotIn('Fixture request', args[0])
            self.assertEqual(popen.call_count, 1)

    def test_managed_api_ignores_ambient_anthropic_overrides(self):
        self.profile['managedApi'] = True
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'wrong', 'ANTHROPIC_BASE_URL': 'https://wrong.invalid',
                                     'ANTHROPIC_AUTH_TOKEN': 'wrong',
                                     'CLAUDE_CODE_USE_BEDROCK': '1',
                                     'CLAUDE_CODE_USE_ANTHROPIC_AWS': '1',
                                     'CLAUDE_CODE_USE_FOUNDRY': '1',
                                     'CLAUDE_CODE_USE_MANTLE': '1',
                                     'CLAUDE_CODE_USE_VERTEX': '1',
                                     'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST': '1',
                                     'KEEP_ME': 'yes'}), \
             patch('bridge.clients.claude.cli_session.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"loggedIn": true}'
            self.assertTrue(_auth_ready(self.profile))
            environment = run.call_args.kwargs['env']
            self.assertNotIn('ANTHROPIC_API_KEY', environment)
            self.assertNotIn('ANTHROPIC_BASE_URL', environment)
            self.assertNotIn('ANTHROPIC_AUTH_TOKEN', environment)
            for key in ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_ANTHROPIC_AWS',
                        'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_USE_MANTLE',
                        'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST'):
                self.assertNotIn(key, environment)
            self.assertEqual(environment['CLAUDE_CONFIG_DIR'], str(self.config))
            self.assertEqual(environment['KEEP_ME'], 'yes')
            process = Mock();process.poll.return_value = None;process.stdout = iter(())
            with patch('bridge.clients.claude.cli_session.subprocess.Popen', return_value=process) as popen, \
                 patch('bridge.clients.claude.cli_session._Owned') as owner_type:
                owner = owner_type.return_value
                owner.origin = self.profile['origin'];owner.process = process
                owner.catalog = {'current_permission_mode': 'plan'}
                owner.pending = {};owner.active = False;owner.error = None
                owner.control.return_value = {'current_permission_mode': 'plan'}
                self.cli.call('resume', self.sid, {})
                environment = popen.call_args.kwargs['env']
                self.assertNotIn('ANTHROPIC_API_KEY', environment)
                self.assertNotIn('ANTHROPIC_BASE_URL', environment)
                self.assertNotIn('ANTHROPIC_AUTH_TOKEN', environment)
                for key in ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_ANTHROPIC_AWS',
                            'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_USE_MANTLE',
                            'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST'):
                    self.assertNotIn(key, environment)
                self.assertEqual(environment['CLAUDE_CONFIG_DIR'], str(self.config))
                self.assertEqual(environment['KEEP_ME'], 'yes')

    def test_external_writer_guard_blocks_before_spawning(self):
        with patch('bridge.clients.claude.cli_session.assert_session_closed',
                   side_effect=BridgeUnavailable('external writer')) as guard, \
             patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            with self.assertRaises(BridgeUnavailable):
                self.cli.call('resume', self.sid, {})
        guard.assert_called_once_with(self.config, self.native)
        popen.assert_not_called()

    def test_account_switch_blocks_resume_and_unknown_permission_does_not_guess(self):
        self.cli.listing()
        with patch('bridge.clients.claude.cli_session._profile', return_value={**self.profile,
                   'directory': self.root/'other-config', 'origin': 'fixture:other'}), \
             patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            self.assertFalse(self.cli.contains(self.sid))
            with self.assertRaises(ValueError):
                self.cli.call('resume', self.sid, {})
            popen.assert_not_called()
        self.path.write_text(self.path.read_text().replace('{"type": "mode", "mode": "plan"}\n', ''))
        with patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            detail = self.cli.call('detail', self.sid, {})
            self.assertFalse(detail['capabilities']['resume'])
            self.assertIn('权限模式', detail['notice'])
            with self.assertRaises(BridgeUnavailable):
                self.cli.call('resume', self.sid, {})
            popen.assert_not_called()

    def test_resume_receipt_is_at_most_once(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.return_value = {'status': 'ready'}
        manager.adapters['claude'] = adapter
        body = {'id': str(uuid.uuid4())}
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'ready')
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'ready')
        adapter.call.assert_called_once_with('resume', self.sid, body)

    def test_uncertain_create_is_not_replayed_with_same_receipt(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.side_effect = BridgeUnavailable('create result uncertain')
        manager.adapters['claude'] = adapter
        body = {'id': str(uuid.uuid4()), 'projectKey': 'cli:fixture', 'firstMessage': 'First request'}
        self.assertEqual(manager.call('claude', 'create', None, body)['status'], 'unknown')
        self.assertEqual(manager.call('claude', 'create', None, body)['status'], 'unknown')
        adapter.call.assert_called_once_with('create', None, body)

    def test_cli_create_routes_selected_project_without_desktop_connection(self):
        hosts = Mock()
        hosts.hosts.return_value = {}
        hosts.projects.return_value = [{'host': 'local', 'cwd': str(self.cwd)}]
        adapter = Claude(self.root/'adapter', auto_connect=False, hosts=hosts)
        self.addCleanup(adapter.close)
        adapter.cli.profile_resolver = lambda: self.profile
        with patch.object(adapter.cli, '_ready', return_value=True), \
             patch.object(adapter.remote_cli, 'projects', return_value=[]), \
             patch.object(adapter.cli, 'create', return_value={'status': 'ready', 'result': {'id': 'created'}}) as create:
            projects = adapter.call('projects')['projects']
            project = next(row for row in projects if row['surface'] == 'cli')
            self.assertNotIn('origin', project)
            self.assertEqual(adapter.call('create', body={
                'projectKey': project['key'], 'firstMessage': 'Native first request'})['result']['id'], 'created')
        create.assert_called_once_with(str(self.cwd), self.profile['origin'], 'Native first request')

    def test_file_only_send_uses_receipt_and_does_not_replay(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.return_value = {'status': 'accepted'}
        manager.adapters['claude'] = adapter
        body = {'id': str(uuid.uuid4()), 'text': '',
                'resolvedFiles': [{'name': 'note.txt', 'path': str(self.root/'note.txt')}]}
        self.assertEqual(manager.call('claude', 'send', self.sid, body)['status'], 'accepted')
        self.assertEqual(manager.call('claude', 'send', self.sid, body)['status'], 'accepted')
        adapter.call.assert_called_once_with('send', self.sid, body)

    def test_preflight_block_can_retry_with_new_receipt_after_session_closes(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.side_effect = [ResumePreflightBlocked('external writer'), {'status': 'ready'}]
        manager.adapters['claude'] = adapter
        first = {'id': str(uuid.uuid4())}
        second = {'id': str(uuid.uuid4())}
        self.assertEqual(manager.call('claude', 'resume', self.sid, first)['status'], 'blocked')
        self.assertEqual(manager.call('claude', 'resume', self.sid, first)['status'], 'blocked')
        self.assertEqual(manager.call('claude', 'resume', self.sid, second)['status'], 'ready')
        self.assertEqual(adapter.call.call_count, 2)

    def test_uncertain_resume_is_not_replayed_with_same_receipt(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.side_effect = BridgeUnavailable('initialize result uncertain')
        manager.adapters['claude'] = adapter
        body = {'id': str(uuid.uuid4())}
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'unknown')
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'unknown')
        adapter.call.assert_called_once()

    def test_desktop_quit_guard_sees_owned_cli_task(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.cli.busy.return_value = True
        manager.adapters['claude'] = adapter
        self.assertEqual(manager._claude_quit_evidence(), {'busy': True, 'unknown': False})
        with self.assertRaisesRegex(ValueError, 'Claude Code 任务'):
            manager._assert_idle('claude', {'unknown': False, 'mainPids': []})

    def test_only_owned_process_receives_send_controls_and_approval(self):
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=False,
                     model=None, permission='plan', events=[])
        owner.pending = {}
        owner.catalog = {'models': [{'value': 'fixture-model', 'displayName': 'Fixture'}]}
        owner.condition = threading.Condition()
        owner.error = None
        owner.control.return_value = {'mode': 'default'}
        owner.control.side_effect = lambda request: (
            {'mode': request['mode']} if request['subtype'] == 'set_permission_mode' else
            {'applied': {'model': 'fixture-model'}} if request['subtype'] == 'get_settings' else {})
        self.cli.owned[self.native] = owner
        self.assertEqual(self.cli.call('send', self.sid, {'text': 'new fixture message'})['status'], 'accepted')
        self.assertTrue(self.cli.busy())
        self.assertFalse(self.cli.call('detail', self.sid, {})['capabilities']['send'])
        with self.assertRaises(BridgeUnavailable):
            self.cli.call('send', self.sid, {'text': 'overlapping fixture'})
        owner.write.assert_called_with({'type': 'user', 'message': {'role': 'user', 'content': 'new fixture message'}})
        self.cli.call('settings', self.sid, {'model': 'fixture-model'})
        owner.control.assert_any_call({'subtype': 'set_model', 'model': 'fixture-model'})
        self.cli.call('access', self.sid, {'mode': 'default'})
        owner.control.assert_called_with({'subtype': 'set_permission_mode', 'mode': 'default'})
        self.assertEqual(owner.permission, 'default')
        owner.pending['request-1'] = {'request': {'tool_name': 'Bash', 'input': {'command': 'fixture'}}}
        self.cli.call('respond', self.sid, {'requestId': 'request-1', 'decision': 'decline'})
        self.assertNotIn('request-1', owner.pending)
        owner.write.assert_called_with({'type': 'control_response', 'response': {'subtype': 'success',
                                       'request_id': 'request-1', 'response': {'behavior': 'deny',
                                                                             'message': '用户拒绝了本次操作'}}})
        self.cli.call('stop', self.sid, {})
        owner.control.assert_called_with({'subtype': 'interrupt'})

    def test_catalog_uses_only_native_loaded_user_project_and_plugin_commands(self):
        catalog = {'commands': [{'name': 'review', 'description': 'User skill'},
                    {'name': 'sample:check', 'description': 'Enabled plugin skill'},
                    {'name': 'compact', 'builtin': True}, {'name': 'invalid name'}]}
        names = {row['id']: row for row in _skill_catalog(catalog)}
        self.assertEqual(set(names), {'review', 'sample:check'})
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=False,
                     model=None, permission='plan', events=[])
        owner.pending = {}
        owner.catalog = catalog
        owner.condition = threading.Condition()
        owner.error = None
        self.cli.owned[self.native] = owner
        self.assertTrue(self.cli.call('detail', self.sid, {})['capabilities']['skills'])
        self.assertEqual({row['id'] for row in self.cli.call('catalog', self.sid, {})['skills']}, set(names))
        with self.assertRaisesRegex(ValueError, '不可用'):
            self.cli.call('send', self.sid, {'text': 'try', 'plugins': ['private']})
        self.cli.call('send', self.sid, {'text': 'review this', 'plugins': ['sample:check']})
        owner.write.assert_called_with({'type': 'user', 'message': {'role': 'user',
                                       'content': '/sample:check review this'}})

    def test_native_permission_choices_require_explicit_bypass_selection(self):
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=False,
                     model=None, permission='plan', events=[])
        owner.pending = {}
        owner.catalog = {}
        owner.condition = threading.Condition()
        owner.error = None
        owner.control.side_effect = lambda request: (setattr(owner, 'permission', request['mode']) or {'mode': request['mode']})
        self.cli.owned[self.native] = owner
        options = self.cli.call('access', self.sid, {})['options']
        self.assertEqual({option['value'] for option in options},
                         {'default', 'acceptEdits', 'plan', 'dontAsk', 'auto', 'bypassPermissions'})
        with self.assertRaisesRegex(ValueError, '明确确认'):
            self.cli.call('access', self.sid, {'mode': 'bypassPermissions'})
        owner.control.assert_not_called()
        self.cli.call('access', self.sid, {'mode': 'bypassPermissions', 'confirmed': True})
        self.assertEqual(self.cli.call('access', self.sid, {})['mode'], 'bypassPermissions')

    def test_attachment_only_send_uses_native_image_block_and_local_file_reference(self):
        process = Mock(); process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=False,
                     model=None, permission='plan', events=[])
        owner.pending = {}; owner.condition = threading.Condition(); owner.error = None; owner.catalog = {}
        self.cli.owned[self.native] = owner
        self.assertTrue(self.cli.call('detail', self.sid, {})['capabilities']['attachments'])
        file = self.root/'private note.txt'; file.write_text('fixture bytes')
        self.cli.call('send', self.sid, {'text': '', 'resolvedImages': [
            {'name': 'photo.png', 'url': 'data:image/png;base64,aGVsbG8='}],
            'resolvedFiles': [{'name': file.name, 'path': str(file)}]})
        content = owner.write.call_args.args[0]['message']['content']
        self.assertEqual(content[1], {'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/png', 'data': 'aGVsbG8='}})
        self.assertEqual(json.loads(content[0]['text'].split('\n', 1)[1])[0]['path'], str(file))
        self.assertNotIn('fixture bytes', json.dumps(content))

    def test_question_approval_forwards_answers_to_owned_tool(self):
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=True,
                     model=None, permission='default', events=[], catalog={})
        owner.pending = {'question-1': {'request': {'tool_name': 'AskUserQuestion',
                         'input': {'questions': [{'question': 'Fixture choice?', 'options': []}]}}}}
        owner.condition = threading.Condition()
        owner.error = None
        self.cli.owned[self.native] = owner
        self.cli.call('respond', self.sid, {'requestId': 'question-1', 'decision': 'accept',
                                           'answers': {'Fixture choice?': {'answers': ['Option A']}}})
        response = owner.write.call_args.args[0]['response']['response']
        self.assertEqual(response['updatedInput']['answers'], {'Fixture choice?': 'Option A'})

    def test_ambiguous_approval_write_cannot_be_replayed(self):
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=True,
                     model=None, permission='default', events=[], catalog={})
        owner.pending = {'request-1': {'request': {'tool_name': 'Bash', 'input': {}}}}
        owner.condition = threading.Condition()
        owner.error = None
        owner.write.side_effect = BridgeUnavailable('write uncertain')
        self.cli.owned[self.native] = owner
        with self.assertRaises(BridgeUnavailable):
            self.cli.call('respond', self.sid, {'requestId': 'request-1', 'decision': 'accept'})
        with self.assertRaisesRegex(ValueError, '已处理或过期'):
            self.cli.call('respond', self.sid, {'requestId': 'request-1', 'decision': 'accept'})
        owner.write.assert_called_once()

    def test_native_init_identity_mismatch_terminates_owned_process(self):
        process = Mock()
        process.poll.return_value = None
        process.stdout = iter([json.dumps({'type': 'system', 'subtype': 'init',
                                           'session_id': str(uuid.uuid4()), 'cwd': str(self.cwd)})])
        owned = _Owned(process, self.profile['origin'], self.native, str(self.cwd))
        with owned.condition:
            owned.condition.wait_for(lambda: owned.error is not None, timeout=1)
        self.assertIn('身份', owned.error)
        if sys.platform == 'win32':
            # An unbound Windows child is not ours to terminate; the next test covers a bound child.
            process.terminate.assert_not_called()
        else:
            process.terminate.assert_called_once()

    def test_windows_init_identity_mismatch_closes_bound_native_child(self):
        lines = queue.Queue()

        def stream():
            while True:
                line = lines.get()
                if line is None:
                    return
                yield line

        process = Mock()
        process.poll.return_value = None
        process.stdout = stream()
        owned = _Owned(process, self.profile['origin'], self.native, str(self.cwd))
        owned.native_process = (1234, 5678)
        with patch('bridge.clients.claude.cli_session.sys.platform', 'win32'), \
             patch.object(claude_process, 'terminate_matching_process', return_value=True) as terminate:
            lines.put(json.dumps({'type': 'system', 'subtype': 'init',
                                  'session_id': str(uuid.uuid4()), 'cwd': str(self.cwd)}))
            with owned.condition:
                self.assertTrue(owned.condition.wait_for(lambda: owned.error is not None, timeout=1))
            lines.put(None)
            with owned.condition:
                self.assertTrue(owned.condition.wait_for(lambda: owned.finished, timeout=1))
        terminate.assert_called_once_with(1234, 5678)

    def test_native_cancel_and_terminal_result_clear_pending_approval(self):
        for ending in ({'type': 'control_cancel_request', 'request_id': 'pending'},
                       {'type': 'result', 'subtype': 'error_during_execution'}):
            process = Mock()
            process.stdout = iter([json.dumps({'type': 'control_request', 'request_id': 'pending',
                         'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {}}}),
                         json.dumps(ending)])
            owned = _Owned(process, self.profile['origin'], self.native, str(self.cwd))
            with owned.condition:
                self.assertTrue(owned.condition.wait_for(lambda: owned.finished, timeout=1))
            self.assertFalse(owned.pending)
            self.assertFalse(owned.active)
            self.assertEqual([event['type'] for event in owned.events],
                             ['result'] if ending['type'] == 'result' else [])

    def test_windows_owned_close_terminates_exact_native_child(self):
        process = Mock()
        process.pid = 5555
        process.stdout = iter(())
        process.poll.return_value = None
        owned = _Owned(process, self.profile['origin'], self.native, str(self.cwd))
        owned.catalog = {'pid': 1234}
        with patch('bridge.clients.claude.cli_session.sys.platform', 'win32'), \
             patch.object(claude_process, 'owned_child_start_filetime', return_value=5678) as bind, \
             patch.object(claude_process, 'terminate_matching_process', return_value=True) as terminate:
            owned.bind_native_process()
            owned.close()
        bind.assert_called_once_with(1234, 5555)
        terminate.assert_called_once_with(1234, 5678)
        process.terminate.assert_called_once()


class ClaudeCliVersionContract(unittest.TestCase):
    def test_native_legacy_stdio_routing_and_newer_host_routing(self):
        with patch('bridge.clients.claude.cli_session.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '2.1.168 (Claude Code)\n'
            self.assertEqual(_permission_prompt_args('claude.cmd', {}), [])
            run.return_value.stdout = '2.1.287 (Claude Code)\n'
            self.assertEqual(_permission_prompt_args('claude.cmd', {}),
                             ['--permission-prompts', 'host'])
            self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')

    def test_installed_cli_effort_control_is_session_only_and_read_back(self):
        binary = shutil.which('claude')
        if not binary:
            self.skipTest('Claude CLI is not installed')
        version = subprocess.run([binary, '--version'], capture_output=True, text=True,
                                 encoding='utf-8', timeout=5).stdout
        found = re.match(r'(\d+)\.(\d+)\.(\d+)', version)
        if not found or tuple(map(int, found.groups())) < (2, 1, 287):
            self.skipTest('Claude CLI version predates the observed effort control protocol')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = root/'claude'; config.mkdir()
            env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP')
                   if key in os.environ}
            env.update(HOME=str(root), USERPROFILE=str(root), CLAUDE_CONFIG_DIR=str(config),
                       DISABLE_AUTOUPDATER='1', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
            process = subprocess.Popen([binary, '-p', '--input-format', 'stream-json',
                                        '--output-format', 'stream-json', '--verbose'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                       cwd=root, env=env)
            incoming = queue.Queue()
            def read_frames():
                for line in process.stdout:
                    incoming.put(line)
            reader = threading.Thread(target=read_frames, daemon=True)
            reader.start()
            responses = {}
            try:
                for label, request in [('initialize', {'subtype': 'initialize', 'hooks': None}),
                                       ('set', {'subtype': 'apply_flag_settings',
                                                'settings': {'effortLevel': 'low'}}),
                                       ('read', {'subtype': 'get_settings'})]:
                    rid = str(uuid.uuid4())
                    process.stdin.write(json.dumps({'type': 'control_request', 'request_id': rid,
                                                    'request': request})+'\n')
                    process.stdin.flush()
                    while True:
                        event = json.loads(incoming.get(timeout=10))
                        if event.get('type') == 'control_response' and event['response'].get('request_id') == rid:
                            responses[label] = event['response']
                            break
            finally:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=2)
                reader.join(timeout=2)
                process.stdin.close()
                process.stdout.close()
            self.assertEqual(responses['set']['subtype'], 'success')
            self.assertEqual(responses['read']['response']['applied']['effort'], 'low')
            self.assertTrue(responses['initialize']['response']['models'])
            self.assertFalse((config/'settings.json').exists())


class ClaudeCliModelContract(unittest.TestCase):
    def setUp(self):
        self.native_models = [
            {'value': 'default', 'resolvedModel': 'claude-opus-5-5',
             'displayName': 'Default (recommended)', 'supportsEffort': True,
             'supportedEffortLevels': ['low', 'medium', 'high', 'xhigh', 'max']},
            {'value': 'opus', 'resolvedModel': 'claude-opus-5-5', 'displayName': 'Opus',
             'supportsEffort': True, 'supportedEffortLevels': ['low', 'medium', 'high']},
            {'value': 'sonnet', 'resolvedModel': 'claude-sonnet-5-5', 'displayName': 'Sonnet',
             'supportsEffort': True, 'supportedEffortLevels': ['low', 'medium', 'high']},
            {'value': 'haiku', 'resolvedModel': 'claude-haiku-4-5-20251001', 'displayName': 'Haiku'},
        ]
        self.state = {'model': 'claude-opus-5-5', 'effort': 'medium', 'flag': None}
        self.calls = []

        def control(request):
            self.calls.append(request)
            subtype = request['subtype']
            if subtype == 'get_settings':
                flag = self.state['flag']
                return {'applied': {'model': self.state['model'], 'effort': self.state['effort']},
                        'sources': [{'source': 'flagSettings', 'settings': {'effortLevel': flag}}]
                        if flag else []}
            if subtype == 'list_models':
                return {'models': self.native_models}
            if subtype == 'set_model':
                choice = request['model']
                self.state['model'] = next((row['resolvedModel'] for row in self.native_models
                                            if row['value'] == choice), choice)
                return {}
            if subtype == 'apply_flag_settings':
                self.state['flag'] = request['settings']['effortLevel']
                self.state['effort'] = self.state['flag'] or 'medium'
                return {}
            raise AssertionError(request)

        self.owner = SimpleNamespace(catalog={'models': list(self.native_models)}, model=None,
                                     control=control)

    def test_control_distinguishes_native_rejection_from_missing_ack(self):
        owner = object.__new__(_Owned)
        owner.condition = threading.Condition()
        owner.process = Mock()
        owner.process.poll.return_value = None
        owner.write = Mock()
        owner.responses = {'fixed': {'subtype': 'error', 'request_id': 'fixed',
                           'error': 'Unsupported control request subtype: list_models'}}
        with patch('bridge.clients.claude.cli_session.uuid.uuid4', return_value='fixed'):
            with self.assertRaises(ClaudeControlRejected) as rejected:
                owner.control({'subtype': 'list_models'})
        self.assertEqual(rejected.exception.reason, 'Unsupported control request subtype: list_models')
        owner.responses = {}
        owner.process.poll.return_value = 1
        with patch('bridge.clients.claude.cli_session.uuid.uuid4', return_value='fixed'):
            with self.assertRaises(BridgeUnavailable) as missing:
                owner.control({'subtype': 'set_model', 'model': 'sonnet'})
        self.assertNotIsInstance(missing.exception, ClaudeControlRejected)

    def test_concrete_ids_and_supported_efforts_from_native_rows(self):
        rows = _model_catalog(self.owner.catalog)
        self.assertEqual([row['id'] for row in rows],
                         ['default', 'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-4-5-20251001'])
        self.assertIn('claude-opus-5-5', rows[0]['name'])
        self.assertEqual(rows[2]['efforts'], ['auto', 'low', 'medium', 'high'])
        self.assertEqual(rows[2]['defaultEffort'], 'auto')
        self.assertEqual(rows[3]['efforts'], [])
        self.assertEqual(_cli_catalog(self.owner, {'model': 'historical'})['currentModel'], 'claude-opus-5-5')
        self.assertTrue(_cli_catalog(self.owner, {'model': 'historical'})['capabilities']['effort'])

    def test_refresh_reads_native_model_list_and_updates_snapshot(self):
        self.native_models = self.native_models[:2]
        result = _cli_catalog(self.owner, {'model': 'historical'}, refresh=True)
        self.assertEqual(len(result['models']), 2)
        self.assertEqual(len(self.owner.catalog['models']), 2)
        self.assertEqual(self.calls[0], {'subtype': 'list_models'})

    def test_empty_catalog_manual_model_and_effort_are_confirmed_by_native_settings(self):
        self.owner.catalog = {'models': []}
        result = _cli_catalog(self.owner, {'model': 'historical'})
        self.assertTrue(result['capabilities']['manualModels'])
        self.assertTrue(result['capabilities']['manualEffort'])
        _set_cli_settings(self.owner, {'model': 'manual-model', 'effort': 'high', 'manualEffort': True})
        self.assertEqual(self.state['model'], 'manual-model')
        self.assertEqual(self.state['effort'], 'high')

    def test_model_and_effort_change_are_checked_against_applied_settings(self):
        _set_cli_settings(self.owner, {'model': 'claude-sonnet-5-5', 'effort': 'high'})
        self.assertEqual(self.state['model'], 'claude-sonnet-5-5')
        self.assertEqual(self.state['effort'], 'high')
        self.assertEqual(self.owner.model, 'claude-sonnet-5-5')
        self.assertIn({'subtype': 'set_model', 'model': 'claude-sonnet-5-5'}, self.calls)
        self.assertIn({'subtype': 'apply_flag_settings', 'settings': {'effortLevel': 'high'}}, self.calls)
        _set_cli_settings(self.owner, {'model': 'default', 'effort': 'auto'})
        self.assertEqual(_cli_catalog(self.owner, {'model': 'historical'})['currentModel'], 'claude-opus-5-5')
        self.assertIn({'subtype': 'set_model', 'model': 'default'}, self.calls)
        self.assertIn({'subtype': 'apply_flag_settings', 'settings': {'effortLevel': None}}, self.calls)

    def test_rejected_effort_and_unconfirmed_model_do_not_claim_success(self):
        with self.assertRaisesRegex(ValueError, '不支持'):
            _set_cli_settings(self.owner, {'model': 'claude-haiku-4-5-20251001', 'effort': 'high'})
        self.assertEqual(self.calls, [])
        original = self.owner.control
        def ignored_switch(request):
            if request['subtype'] == 'set_model':
                self.calls.append(request)
                return {}
            return original(request)
        self.owner.control = ignored_switch
        with self.assertRaisesRegex(BridgeUnavailable, '未确认'):
            _set_cli_settings(self.owner, {'model': 'claude-sonnet-5-5'})
        self.assertIsNone(self.owner.model)

    def test_legacy_initialize_models_keep_confirmed_set_model_without_list_models(self):
        calls = []
        applied = {'model': 'claude-sonnet-4-6', 'effort': 'max'}
        def control(request):
            calls.append(request)
            if request['subtype'] == 'get_settings':
                return {'applied': dict(applied), 'sources': []}
            if request['subtype'] == 'set_model':
                applied['model'] = 'claude-haiku-4-5-20251001'
                return {}
            if request['subtype'] == 'list_models':
                raise ClaudeControlRejected('Unsupported control request subtype: list_models')
            if request['subtype'] == 'apply_flag_settings':
                applied['effort'] = request['settings']['effortLevel']
                return {}
            return {}
        owner = SimpleNamespace(catalog={'models': [
            {'value': 'sonnet', 'displayName': 'Sonnet', 'supportsEffort': True,
             'supportedEffortLevels': ['low', 'medium', 'high', 'max']},
            {'value': 'haiku', 'displayName': 'Haiku'}]}, model=None, control=control)
        public = {'model': 'sonnet'}
        result = _cli_catalog(owner, public)
        self.assertEqual([row['id'] for row in result['models']], ['sonnet', 'haiku'])
        self.assertEqual(result['currentModel'], 'claude-sonnet-4-6')
        self.assertEqual(result['currentEffort'], 'max')
        self.assertEqual(result['models'][0]['efforts'], ['auto', 'low', 'medium', 'high', 'max'])
        self.assertEqual(result['capabilities'], {'models': True, 'manualModels': True, 'manualEffort': True, 'effort': True, 'skills': True})
        self.assertEqual(calls, [{'subtype': 'get_settings'}])
        with self.assertRaisesRegex(BridgeUnavailable, '不支持刷新模型目录'):
            _cli_catalog(owner, public, refresh=True)
        self.assertEqual(calls[-1], {'subtype': 'list_models'})
        count = len(calls)
        with self.assertRaisesRegex(BridgeUnavailable, '不支持刷新模型目录'):
            _cli_catalog(owner, public, refresh=True)
        self.assertEqual(len(calls), count)
        with self.assertRaisesRegex(ValueError, '不支持此思考强度'):
            _set_cli_settings(owner, {'model': 'haiku', 'effort': 'high'})
        _set_cli_settings(owner, {'model': 'haiku'})
        self.assertIn({'subtype': 'set_model', 'model': 'haiku'}, calls)
        self.assertEqual(_cli_selection(owner, public),
                         {'currentModel': 'claude-haiku-4-5-20251001', 'currentEffort': 'max'})

    def test_unconfirmed_model_refresh_does_not_mark_protocol_unsupported(self):
        def control(request):
            if request['subtype'] == 'list_models':
                raise BridgeUnavailable('Claude Code 控制请求未确认')
            return {'applied': {'model': 'fixture-model'}}
        owner = SimpleNamespace(catalog={'models': [{'value': 'fixture-model'}]}, model=None,
                                control=control)
        with self.assertRaisesRegex(BridgeUnavailable, '未确认'):
            _cli_catalog(owner, {'model': 'fixture-model'}, refresh=True)
        self.assertFalse(getattr(owner, 'model_list_unsupported', False))

    def test_legacy_set_model_without_success_ack_does_not_update_selection(self):
        def control(request):
            if request['subtype'] == 'get_settings':
                return {'applied': {'model': 'claude-sonnet-4-6'}}
            raise BridgeUnavailable('Claude Code 控制请求未确认')
        owner = SimpleNamespace(catalog={'models': [{'value': 'haiku'}]}, model=None,
                                control=Mock(side_effect=control))
        with self.assertRaisesRegex(BridgeUnavailable, '未确认'):
            _set_cli_settings(owner, {'model': 'haiku'})
        self.assertIsNone(owner.model)
        self.assertEqual([call.args[0]['subtype'] for call in owner.control.call_args_list],
                         ['get_settings', 'set_model'])

    def test_legacy_effort_flag_ack_does_not_claim_ineffective_level(self):
        state = {'model': 'claude-sonnet-4-6', 'effort': 'max', 'flag': None}
        def control(request):
            subtype = request['subtype']
            if subtype == 'get_settings':
                return {'applied': {'model': state['model'], 'effort': state['effort']},
                        'sources': [{'source': 'flagSettings', 'settings': {'effortLevel': state['flag']}}]}
            if subtype == 'set_model':
                return {}
            if subtype == 'apply_flag_settings':
                state['flag'] = request['settings']['effortLevel']
                return {}
            raise AssertionError(request)
        owner = SimpleNamespace(catalog={'models': [{'value': 'sonnet', 'supportsEffort': True,
                  'supportedEffortLevels': ['low', 'medium', 'high', 'max']}]}, model=None, control=control)
        with self.assertRaisesRegex(BridgeUnavailable, '未确认'):
            _set_cli_settings(owner, {'model': 'sonnet', 'effort': 'high'})
        self.assertEqual(state['flag'], 'high')
        self.assertEqual(state['effort'], 'max')
        self.assertIsNone(owner.model)

    def test_legacy_alias_and_effort_are_confirmed_by_ack_and_applied_level(self):
        state = {'model': 'old-model', 'effort': 'medium'}
        def control(request):
            if request['subtype'] == 'get_settings':
                return {'applied': dict(state), 'sources': []}
            if request['subtype'] == 'set_model':
                state['model'] = 'claude-sonnet-4-6'
                return {}
            if request['subtype'] == 'apply_flag_settings':
                state['effort'] = request['settings']['effortLevel']
                return {}
            raise AssertionError(request)
        owner = SimpleNamespace(catalog={'models': [{'value': 'sonnet', 'supportsEffort': True,
                  'supportedEffortLevels': ['low', 'medium', 'high']}]}, model=None, control=control)
        _set_cli_settings(owner, {'model': 'sonnet', 'effort': 'high'})
        self.assertEqual(owner.model, 'claude-sonnet-4-6')
        self.assertEqual(_cli_selection(owner, {'model': 'old-model'}),
                         {'currentModel': 'claude-sonnet-4-6', 'currentEffort': 'high'})
