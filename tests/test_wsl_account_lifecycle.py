"""Owned-stdin release fixtures only; never starts WSL, Claude, or a model."""
import json
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from bridge.features.accounts.wsl_account_guard import account_guard
from bridge.features.accounts.wsl_accounts import WslAccounts
from bridge.features.accounts import wsl_account_worker as worker
from tests import test_wsl_account_boundaries as boundaries
from tests.test_wsl_accounts import install_target_toml


@unittest.skipUnless(os.name == 'posix', 'Native WSL account files require POSIX ownership and permissions')
class WslOwnedAccountLifecycleTests(unittest.TestCase):
    def setUp(self):
        boundaries.ClaudeWslDurableGuardTests.setUp(self)
        install_target_toml(self)
        self.home = Path(self.temp.name) / 'claude-config'
        self.home.mkdir()
        (self.home / 'settings.json').write_text('{"env":{"ANTHROPIC_API_KEY":"old-fixture"},"theme":"dark"}')
        (self.home / 'history.jsonl').write_text('{"message":"retained history"}\n')
        (self.home / 'draft.json').write_text('{"text":"unsent draft"}')
        self.row['configDir'] = str(self.home)
        from bridge.clients.claude.wsl import entry_id
        self.row['id'] = entry_id(self.row['distribution'], self.row['user'], self.row['configDir'])
        self.project = self.home / 'project'
        self.project.mkdir()
        from bridge.clients.claude import remote_worker
        native = str(uuid.uuid4())
        project_history = self.home / 'projects' / 'fixture'
        project_history.mkdir(parents=True)
        self.transcript = project_history / (native + '.jsonl')
        self.transcript.write_text('\n'.join(json.dumps(event) for event in [
            {'type': 'user', 'cwd': str(self.project), 'permissionMode': 'default',
             'message': {'content': 'retained native question'}},
            {'type': 'assistant', 'cwd': str(self.project),
             'message': {'model': 'original-model', 'content': [{'type': 'text', 'text': 'retained answer'}]}}
        ]) + '\n')
        with self.native_context():
            initial = remote_worker.command({'operation': 'account-continuation'})
        self.metadata = self.remote._row(self.row['id'], {**self.row, 'alias': self.row['id']}, initial['sessions'][0])
        self.sid = self.metadata['id']
        self.remote.index.clear()
        self.remote.index[self.sid] = dict(self.metadata)
        remote_patch = patch.object(self.remote, '_remote', side_effect=self.native_read)
        remote_patch.start()
        self.addCleanup(remote_patch.stop)
        self.exited = False
        self.process = Mock()
        self.process.poll.side_effect = lambda: 0 if self.exited else None
        self.process.wait.side_effect = self.finish
        self.owned = SimpleNamespace(process=self.process, condition=threading.Condition(),
                                     active=False, pending={}, error=None, events=[], catalog={}, model=None, permission='default',
                                     native=self.metadata['nativeId'], origin=self.metadata['origin'],
                                     cwd=self.metadata['cwd'], preflight={'status': 'ready', 'cwd': self.metadata['cwd']},
                                     close=Mock())
        self.remote.owned[self.sid] = self.owned
        self.addCleanup(self.remote.owned.clear)
        self.store = worker.AccountStore('claude', self.home, guard=lambda *_: [] if self.exited else [worker.ERROR_MESSAGES['runtime_running']])
        self.store.change({'action': 'save-api', 'name': 'New fixture', 'model': 'fixture-model',
                           'baseUrl': 'https://new.invalid', 'apiKey': 'new-fixture'})
        self.target = self.store.public()['accounts'][0]['id']
        self.accounts = WslAccounts(self.bridge)
        self.addCleanup(self.accounts.close)
        self.calls = []
        def target(provider, row, request):
            self.calls.append(dict(request))
            return self.store.public() if request['action'] == 'read' else self.store.change(request)
        self.call_patch = patch.object(self.accounts, '_call', side_effect=target)
        self.call_patch.start()
        self.addCleanup(self.call_patch.stop)
        self.running_patch = patch('bridge.features.accounts.wsl_accounts.wsl.running_distributions', return_value=['FixtureUbuntu'])
        self.running_patch.start()
        self.addCleanup(self.running_patch.stop)

    def native_context(self):
        from bridge.clients.claude import remote_worker
        scope = ExitStack()
        scope.enter_context(patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.home),
            'MOBILE_BRIDGE_WSL_NATIVE': '1'}, clear=True))
        scope.enter_context(patch.object(remote_worker.shutil, 'which', return_value='/fixture/claude'))
        scope.enter_context(patch.object(remote_worker, 'native_wsl_binary', return_value=True))
        scope.enter_context(patch.object(remote_worker, 'auth_status', return_value={
            'loggedIn': True, 'authMethod': 'api_key', 'apiProvider': 'custom'}))
        return scope

    def native_read(self, alias, request, **_):
        from bridge.clients.claude import remote_worker
        self.assertEqual(alias, self.row['id'])
        with self.native_context():
            return remote_worker.command(request)

    def finish(self, **_):
        self.exited = True
        return 0

    def request(self, **values):
        return {'provider': 'claude', 'entry': self.row['id'], 'action': 'switch',
                'id': self.target, 'expectedIdentity': self.store.public()['identity'], **values}

    def test_idle_owned_executor_released_before_switch_without_losing_thread_history_or_draft(self):
        retained = {name: (self.home / name).read_bytes() for name in ('history.jsonl', 'draft.json')}
        with self.assertRaisesRegex(ValueError, '仍运行'), account_guard(self.bridge, 'claude', self.row):
            pass  # Reproduces the old idle-owned blocker; inspection stays passive.
        result = self.accounts.change(self.request())
        self.assertEqual(result['activeId'], self.target)
        self.process.stdin.close.assert_called_once_with()
        self.process.wait.assert_called_once_with(timeout=5)
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()
        self.owned.close.assert_not_called()
        self.assertNotIn(self.sid, self.remote.owned)
        self.assertEqual(self.remote.index[self.sid]['nativeId'], self.metadata['nativeId'])
        self.assertEqual(self.remote.index[self.sid]['id'], self.sid)
        self.assertEqual({name: (self.home / name).read_bytes() for name in retained}, retained)
        self.assertEqual(json.loads((self.home / 'settings.json').read_text())['env']['ANTHROPIC_API_KEY'], 'new-fixture')
        self.assertEqual([call['action'] for call in self.calls], ['read', 'switch'])

    def test_explicit_resume_after_switch_uses_original_native_thread_and_updated_config_directory(self):
        from bridge.clients.claude import remote_worker
        result = self.accounts.change(self.request())
        self.assertFalse(result.get('continuationPending', False))
        verified = self.native_read(self.row['id'], {'operation': 'account-continuation'})
        profile, native = verified['profile'], verified['sessions'][0]
        self.assertNotEqual(profile['origin'], self.metadata['origin'])
        self.assertEqual(self.remote._get(self.sid)['id'], self.sid)
        request = {'native': native['nativeId'], 'origin': native['origin'], 'cwd': native['cwd'],
                   'model': native['model'], 'permissionMode': native['permissionMode'], 'scope': 'original-scope'}
        def inspect_exec(binary, arguments, environment):
            self.assertEqual(binary, profile['binary'])
            self.assertEqual(arguments[arguments.index('--resume') + 1], self.metadata['nativeId'])
            self.assertNotIn('--fork-session', arguments)
            self.assertNotIn('--session-id', arguments)
            self.assertEqual(environment['CLAUDE_CONFIG_DIR'], str(self.home))
            settings = json.loads((Path(environment['CLAUDE_CONFIG_DIR']) / 'settings.json').read_text())
            self.assertEqual(settings['env']['ANTHROPIC_API_KEY'], 'new-fixture')
        with self.native_context(), \
                patch.object(remote_worker, 'assert_session_closed') as closed, \
                patch.object(remote_worker, 'upload_root', return_value=self.home / 'uploads'), \
                patch.object(remote_worker.os, 'chdir') as chdir, \
                patch.object(remote_worker.os, 'execvpe', side_effect=inspect_exec) as execute, \
                patch('builtins.print'):
            remote_worker.resume(request)
        closed.assert_called_once_with(profile, self.metadata['nativeId'])
        chdir.assert_called_once_with(self.project)
        execute.assert_called_once()
        self.process.stdin.write.assert_not_called()  # Switching never replays user input.

    def test_stale_identity_or_unknown_account_does_not_close_owned_stdin(self):
        before = self.store.files()
        for values in ({'expectedIdentity': 'stale'}, {'id': 'missing'}):
            with self.assertRaises(ValueError):
                self.accounts.change(self.request(**values))
        self.process.stdin.close.assert_not_called()
        self.assertEqual(self.store.files(), before)

    def test_timeout_retains_original_files_and_never_forces_exit_or_dispatches_switch(self):
        before = self.store.files()
        self.process.wait.side_effect = subprocess.TimeoutExpired('fixture', 5)
        with self.assertRaisesRegex(ValueError, '尚未退出'):
            self.accounts.change(self.request())
        self.assertEqual(self.store.files(), before)
        self.assertEqual([call['action'] for call in self.calls], ['read'])
        self.assertIn(self.sid, self.remote.owned)
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_active_pending_unverified_owner_and_unknown_receipts_prevent_release(self):
        before = self.store.files()
        for attribute, value in (('active', True), ('pending', {'approval': {}}),
                                 ('origin', 'other-origin'), ('error', 'unknown write')):
            original = getattr(self.owned, attribute)
            setattr(self.owned, attribute, value)
            with self.assertRaises(ValueError):
                self.accounts.change(self.request())
            setattr(self.owned, attribute, original)
        boundaries.ClaudeWslDurableGuardTests.receipt(self, {'status': 'unknown'}, self.sid)
        with self.assertRaises(ValueError):
            self.accounts.change(self.request())
        self.process.stdin.close.assert_not_called()
        self.assertEqual(self.store.files(), before)

    def test_read_never_releases_executor_and_external_native_guard_still_blocks_writes(self):
        self.accounts.read('claude', self.row['id'])
        self.process.stdin.close.assert_not_called()
        self.store.guard = lambda *_: ['external native CLI remains running'] if self.exited else [worker.ERROR_MESSAGES['runtime_running']]
        before = self.store.files()
        with self.assertRaisesRegex(worker.AccountError, 'external native'):
            self.accounts.change(self.request())
        self.assertEqual(self.store.files(), before)
        self.assertEqual(self.remote.index[self.sid]['nativeId'], self.metadata['nativeId'])
        self.assertEqual(self.remote.index[self.sid]['id'], self.sid)

    def test_read_advertises_only_an_attempt_and_preserves_native_runtime_blockers(self):
        result = self.accounts.read('claude', self.row['id'])
        self.assertTrue(result['capabilities']['attemptSwitch'])
        self.assertFalse(result['capabilities']['switch'])
        self.assertIn(worker.ERROR_MESSAGES['runtime_running'], result['blockers'])
        self.process.stdin.close.assert_not_called()
        self.owned.active = True
        blocked = self.accounts.read('claude', self.row['id'])
        self.assertFalse(blocked['capabilities'].get('attemptSwitch', False))
        self.assertIn('此 WSL Claude Code 有任务或待处理请求', blocked['blockers'])

    def test_hard_configuration_blocker_does_not_advertise_switch_attempt(self):
        (self.home / 'managed-settings.json').write_text('{}')
        result = self.accounts.read('claude', self.row['id'])
        self.assertFalse(result['capabilities'].get('attemptSwitch', False))
        self.process.stdin.close.assert_not_called()

    def test_readonly_release_plan_cannot_close_stdin_even_if_invoked(self):
        with account_guard(self.bridge, 'claude', self.row, read_only=True, release_idle=True) as release:
            self.assertTrue(callable(release))
            with self.assertRaisesRegex(ValueError, '只读'):
                release()
        self.process.stdin.close.assert_not_called()

    def test_explicit_alias_survives_listing_and_restart_but_external_account_changes_stay_isolated(self):
        from bridge.clients.claude.wsl import WslClaudeCli
        result = self.accounts.change(self.request())
        self.assertFalse(result.get('continuationPending', False))
        self.assertEqual(result['draftContinuations'][0]['id'], self.sid)
        self.assertNotEqual(result['draftContinuations'][0]['previousAccountIdentity'],
                            result['draftContinuations'][0]['accountIdentity'])
        restored = WslClaudeCli(self.manager.claude_wsl)
        self.addCleanup(restored.close)
        restored._remote = self.native_read
        restored._refresh(restored.hosts.hosts())
        self.assertEqual(list(restored.index), [self.sid])
        self.assertEqual(restored._get(self.sid)['nativeId'], self.metadata['nativeId'])
        settings = json.loads((self.home / 'settings.json').read_text())
        settings['env']['ANTHROPIC_API_KEY'] = 'external-unapproved-fixture'
        (self.home / 'settings.json').write_text(json.dumps(settings))
        with self.assertRaises(ValueError):
            restored._get(self.sid)
        restored._refresh(restored.hosts.hosts())
        self.assertNotIn(self.sid, restored.index)
        self.assertEqual(len(restored.index), 1)
        self.assertEqual(next(iter(restored.index.values()))['nativeId'], self.metadata['nativeId'])
        persisted = restored.continuations_path.read_text()
        self.assertNotIn('new-fixture', persisted)
        self.assertNotIn('old-fixture', persisted)

    def test_post_switch_verification_failure_reports_pending_without_repeating_switch(self):
        original = self.native_read
        inspections = []
        def unavailable(alias, request, **options):
            if request['operation'] == 'account-continuation':
                inspections.append(True)
                if len(inspections) == 2:
                    raise ValueError('fixture post-read unavailable')
            return original(alias, request, **options)
        with patch.object(self.remote, '_remote', side_effect=unavailable):
            result = self.accounts.change(self.request())
        self.assertTrue(result['continuationPending'])
        self.assertEqual(result['activeId'], self.target)
        self.assertEqual([call['action'] for call in self.calls], ['read', 'switch'])
        self.assertNotIn('draftContinuations', result)
        self.assertEqual(self.remote.index[self.sid]['origin'], self.metadata['origin'])
        pending = json.loads(self.remote.continuations_path.read_text())['pending'][self.row['id']]
        self.assertEqual(pending['appliedIdentity'], result['identity'])
        self.assertEqual(pending['sessions'][self.sid]['nativeId'], self.metadata['nativeId'])

    def test_stale_native_profile_rejects_before_releasing_owned_pipe(self):
        before = self.store.files()
        self.remote.index[self.sid]['origin'] = 'unverified-origin'
        self.owned.origin = 'unverified-origin'
        with self.assertRaisesRegex(ValueError, '会话身份已变化'):
            self.accounts.change(self.request())
        self.process.stdin.close.assert_not_called()
        self.assertEqual(self.store.files(), before)

    def test_refresh_finishes_only_previously_applied_transaction_without_repeating_account_write(self):
        original = self.native_read
        inspections = []
        def unavailable(alias, request, **options):
            inspections.append(request['operation'])
            if len(inspections) == 2:
                raise ValueError('fixture temporary failure')
            return original(alias, request, **options)
        with patch.object(self.remote, '_remote', side_effect=unavailable):
            result = self.accounts.change(self.request())
        self.assertTrue(result['continuationPending'])
        before = self.store.files()
        recovered = self.accounts.read('claude', self.row['id'])
        self.assertFalse(recovered.get('continuationPending', False))
        self.assertEqual(recovered['draftContinuations'][0]['id'], self.sid)
        self.assertEqual(self.remote._get(self.sid)['nativeId'], self.metadata['nativeId'])
        self.assertEqual(self.store.files(), before)
        self.assertEqual(sum(call['action'] == 'switch' for call in self.calls), 1)
        self.assertNotIn(self.row['id'], self.remote.continuation_pending)

    def test_unapplied_pending_never_creates_alias_on_read(self):
        self.process.wait.side_effect = subprocess.TimeoutExpired('fixture', 5)
        with self.assertRaises(ValueError):
            self.accounts.change(self.request())
        self.exited = True
        self.remote.owned.clear()
        result = self.accounts.read('claude', self.row['id'])
        self.assertNotIn('draftContinuations', result)
        self.assertEqual(self.remote.continuations, {})

    def test_restore_and_same_account_noop_keep_original_thread_id(self):
        before = self.store.files()
        first = self.accounts.change(self.request())
        again = self.accounts.change(self.request())
        self.assertFalse(again.get('continuationPending', False))
        restored = self.accounts.change(self.request(id='native'))
        self.assertFalse(restored.get('continuationPending', False))
        self.assertEqual(self.store.files(), before)
        self.assertEqual(self.remote._get(self.sid)['id'], self.sid)
        self.assertEqual(self.remote.index[self.sid]['origin'], self.metadata['origin'])
        self.assertEqual(first['draftContinuations'][0]['id'], restored['draftContinuations'][0]['id'])

    def test_fresh_detail_preserves_authorized_draft_lineage_for_an_offline_client(self):
        initial = self.remote.call('detail', self.sid, {})
        self.assertNotIn('draftContinuation', initial)
        first_identity = initial['session']['draftAccountIdentity']
        self.accounts.change(self.request())
        middle = self.remote.call('detail', self.sid, {})
        self.assertEqual(middle['draftContinuation']['previousAccountIdentities'], [first_identity])
        second_identity = middle['session']['draftAccountIdentity']
        self.store.change({'action': 'save-api', 'name': 'Third fixture', 'model': 'third-model',
                           'baseUrl': 'https://third.invalid', 'apiKey': 'third-fixture'})
        third_id = self.store.store['accounts'][-1]['id']
        self.accounts.change(self.request(id=third_id))
        from bridge.clients.claude.wsl import WslClaudeCli
        restored = WslClaudeCli(self.manager.claude_wsl)
        self.addCleanup(restored.close)
        restored._remote = self.native_read
        restored._refresh(restored.hosts.hosts())
        detail = restored.call('detail', self.sid, {})
        mapping = detail['draftContinuation']
        self.assertEqual(mapping['id'], self.sid)
        self.assertEqual(mapping['entry'], self.row['id'])
        self.assertEqual(mapping['accountIdentity'], detail['session']['draftAccountIdentity'])
        self.assertEqual(mapping['previousAccountIdentities'], [first_identity, second_identity])
        self.assertNotIn(detail['session']['draftAccountIdentity'], mapping['previousAccountIdentities'])

    def test_history_file_replacement_cannot_inherit_an_authorized_alias(self):
        self.accounts.change(self.request())
        replacement = self.transcript.with_suffix('.replacement')
        replacement.write_bytes(self.transcript.read_bytes())
        replacement.replace(self.transcript)
        with self.assertRaisesRegex(ValueError, '身份已变化'):
            self.remote._get(self.sid)

    def test_manager_detail_and_switch_response_use_same_wrapped_draft_scope(self):
        from bridge.features.accounts.draft_identity import draft_identity
        before = self.manager.call('claude', 'detail', self.sid, {})
        previous = before['draftAccountIdentity']
        self.assertEqual(previous, draft_identity('claude', 'wsl-cli', self.row['id'], self.metadata['draftAccountIdentity']))
        switched = self.accounts.change(self.request())
        after = self.manager.call('claude', 'detail', self.sid, {})
        mapping = after['draftContinuation']
        self.assertEqual(mapping['accountIdentity'], after['draftAccountIdentity'])
        self.assertEqual(mapping['previousAccountIdentities'], [previous])
        self.assertEqual(switched['draftContinuations'][0]['previousAccountIdentity'], previous)
        self.assertEqual(switched['draftContinuations'][0]['accountIdentity'], after['draftAccountIdentity'])
        self.assertNotEqual(mapping['accountIdentity'], after['session']['draftAccountIdentity'])


@unittest.skipUnless(os.name == 'posix', 'Synthetic native Linux Claude process identities')
class NativeClaudeRuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        (boundaries.ROOT / '.tmp').mkdir(exist_ok=True)
        import tempfile
        self.temp = tempfile.TemporaryDirectory(dir=boundaries.ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.user_home = self.root / 'user'
        self.user_home.mkdir()
        self.config = self.user_home / '.claude'
        self.config.mkdir()
        self.binary = self.user_home / '.local/share/claude/versions/2.1.308'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'\x7fELFfixture-native-claude')
        self.link = self.user_home / '.local/bin/claude'
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to(self.binary)
        self.proc = self.root / 'proc'
        self.process = self.proc / '900003'
        self.process.mkdir(parents=True)
        (self.process / 'cmdline').write_bytes(os.fsencode(self.binary) + b'\0-p\0--resume\0fixture\0')
        (self.process / 'exe').symlink_to(self.binary)
        (self.process / 'environ').write_bytes(b'CLAUDE_CONFIG_DIR=' + os.fsencode(self.config) + b'\0')
        original = Path
        def selected(value):
            if str(value) == '/proc':
                return self.proc
            if str(value) == '/usr/local/bin/claude':
                return self.root / 'missing-global'
            return original(value)
        self.scope = ExitStack()
        self.addCleanup(self.scope.close)
        self.scope.enter_context(patch.object(worker, 'Path', side_effect=selected))
        self.scope.enter_context(patch.object(worker.shutil, 'which', return_value=str(self.link)))
        self.scope.enter_context(patch.object(worker.os.path, 'expanduser', return_value=str(self.user_home)))
        self.scope.enter_context(patch.dict(worker.os.environ, {}, clear=True))

    def test_resolved_version_elf_process_is_busy_for_its_exact_config_directory(self):
        self.assertEqual(worker.runtime_blockers('claude', self.config), [worker.ERROR_MESSAGES['runtime_running']])

    def test_same_native_binary_using_other_config_does_not_block_selected_profile(self):
        (self.process / 'environ').write_bytes(b'CLAUDE_CONFIG_DIR=' + os.fsencode(self.root / 'other-config') + b'\0')
        self.assertEqual(worker.runtime_blockers('claude', self.config), [])

    def test_unrelated_executable_with_same_version_basename_is_not_misclassified(self):
        unrelated = self.root / 'other' / '2.1.308'
        unrelated.parent.mkdir()
        unrelated.write_bytes(b'\x7fELFunrelated-executable')
        (self.process / 'exe').unlink()
        (self.process / 'exe').symlink_to(unrelated)
        self.assertEqual(worker.runtime_blockers('claude', self.config), [])

    def test_missing_executable_identity_on_still_live_process_never_authorizes_write(self):
        (self.process / 'exe').unlink()
        self.assertTrue(worker.runtime_blockers('claude', self.config))

    def test_older_installed_native_version_remains_busy_after_current_symlink_moves(self):
        newer = self.binary.with_name('2.1.309')
        newer.write_bytes(b'\x7fELFnewer-installed-native')
        self.link.unlink()
        self.link.symlink_to(newer)
        self.assertEqual(worker.runtime_blockers('claude', self.config), [worker.ERROR_MESSAGES['runtime_running']])

    def test_empty_argv_confirmed_zombie_without_executable_does_not_block_switch(self):
        (self.process / 'cmdline').write_bytes(b'')
        (self.process / 'stat').write_text('900003 (claude) Z 1 0 0')
        (self.process / 'exe').unlink()
        self.assertEqual(worker.runtime_blockers('claude', self.config), [])

    def test_empty_argv_live_process_with_unknown_executable_still_blocks_switch(self):
        (self.process / 'cmdline').write_bytes(b'')
        (self.process / 'stat').write_text('900003 (claude) S 1 0 0')
        (self.process / 'exe').unlink()
        self.assertTrue(worker.runtime_blockers('claude', self.config))

    def test_unrelated_nondumpable_process_does_not_require_executable_access(self):
        (self.process / 'cmdline').write_bytes(b'/usr/bin/unrelated-worker\0--session\0local\0')
        (self.process / 'comm').write_text('unrelated-worker')
        original = Path.stat
        def denied(path, *args, **kwargs):
            if path == self.process / 'exe':
                raise PermissionError(13, 'fixture non-dumpable process')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'stat', denied):
            self.assertEqual(worker.runtime_blockers('claude', self.config), [])

    def test_candidate_nondumpable_process_still_blocks_account_changes(self):
        original = Path.stat
        def denied(path, *args, **kwargs):
            if path == self.process / 'exe':
                raise PermissionError(13, 'fixture candidate identity unknown')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'stat', denied):
            self.assertTrue(worker.runtime_blockers('claude', self.config))

    def test_installed_native_comm_hint_still_requires_matching_executable_inode(self):
        (self.process / 'cmdline').write_bytes(b'opaque-process-title\0-p\0')
        (self.process / 'comm').write_text(self.binary.name)
        self.assertEqual(worker.runtime_blockers('claude', self.config), [worker.ERROR_MESSAGES['runtime_running']])
