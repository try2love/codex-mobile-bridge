import json
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.accounts.cli_access import CliAccess
from bridge.features.accounts.cli_sync import LocalCliSync
from bridge.features.accounts.accounts import Accounts, private_json
from bridge.clients.claude.cli_session import ClaudeCli, ResumePreflightBlocked


class CliAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.native = self.root/'native'
        self.native.mkdir()
        self.profile = {'binary': '/fixture/claude', 'directory': self.native,
                        'origin': 'default:'+str(self.native)+':none'}
        self.accounts = SimpleNamespace(public=Mock(return_value={
            'accounts': [], 'activeId': None, 'activeVerified': False,
            'switch': {}, 'blockers': []}), gate=threading.RLock(), lock=threading.RLock(), check_ready=Mock())
        self.bridge = SimpleNamespace(data_dir=self.root, accounts=self.accounts,
                                      account=SimpleNamespace(lock=threading.RLock()))
        self.profile_patch = patch('bridge.features.accounts.cli_access._profile', return_value=self.profile)
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)
        self.manager = CliAccess(self.bridge)
        self.addCleanup(self.manager.close)

    def save_api(self, **changes):
        request = {'provider': 'claude', 'action': 'saveApi', 'name': 'Claude API',
                   'baseUrl': 'https://fixture.invalid', 'model': 'test-model',
                   'apiKey': 'private-fixture-key', 'authScheme': 'bearer', **changes}
        return self.manager.change(request)['savedId']

    def sync_patch(self, instance):
        module = types.ModuleType('bridge.features.accounts.cli_sync')
        module.LocalCliSync = Mock(return_value=instance)
        return patch.dict(sys.modules, {'bridge.features.accounts.cli_sync': module})

    def test_api_key_is_private_and_edit_without_reentering_key(self):
        identifier = self.save_api()
        directory = self.root/'cli-access'/'claude'/identifier
        self.assertEqual(json.loads((directory/'api.json').read_text())['key'], 'private-fixture-key')
        if sys.platform != 'win32':
            self.assertEqual((directory/'api.json').stat().st_mode & 0o077, 0)
        self.assertFalse((self.native/'settings.json').exists())
        self.assertNotIn('private-fixture-key', json.dumps(self.manager.public()))
        self.assertNotIn('private-fixture-key', (self.root/'cli-access'/'index.json').read_text())
        self.save_api(id=identifier, name='Updated', apiKey='')
        self.assertEqual(self.manager.public()['claude']['accounts'][0]['name'], 'Updated')
        self.assertEqual(self.manager.public()['claude']['accounts'][0]['authScheme'], 'bearer')
        self.assertEqual(json.loads((directory/'api.json').read_text())['key'], 'private-fixture-key')
        self.assertEqual(self.manager.index['claude'][0]['directory'], str(self.native))

    def test_api_can_be_saved_before_default_native_directory_exists(self):
        self.native.rmdir()
        identifier = self.save_api()
        self.assertFalse(self.native.exists(), 'saving API access must not initialize native settings')
        self.assertEqual(self.manager.index['claude'][0]['directory'], str(self.native))
        self.assertTrue((self.root/'cli-access'/'claude'/identifier/'api.json').is_file())

    def test_claude_models_probe_uses_saved_key_without_switching(self):
        identifier = self.save_api()
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['claude-a']) as models:
            result = self.manager.change({'provider': 'claude', 'action': 'models', 'id': identifier})
        models.assert_called_once_with('https://fixture.invalid/v1', 'private-fixture-key', headers={
            'Authorization': 'Bearer private-fixture-key', 'anthropic-version': '2023-06-01'})
        cache = result['probe'].pop('cache')
        self.assertIsInstance(cache['updatedAt'], float)
        self.assertEqual(cache['identity'], result['claude']['accounts'][0]['modelCatalogIdentity'])
        self.assertEqual(result['probe'], {'provider': 'claude', 'id': identifier,
                         'status': 'reachable', 'scope': 'models', 'models': ['claude-a'], 'modelCount': 1})
        self.assertIsNone(result['claude']['activeId'])
        self.assertNotIn('private-fixture-key', json.dumps(result))
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['claude-a']) as models:
            result = self.manager.change({'provider': 'claude', 'action': 'test', 'id': identifier})
        self.assertIsNone(result['probe']['models'])
        self.assertNotIn('cache', result['probe'])
        self.assertEqual(result['probe']['modelCount'], 1)
        models.assert_called_once()

    def test_codex_models_probe_reuses_saved_credentials_without_switching(self):
        row = {'id': 'a'*32, 'kind': 'api', 'baseUrl': 'https://codex.invalid/v1'}
        self.accounts.row = Mock(return_value=row)
        self.accounts.api_credentials = Mock(return_value=(row['baseUrl'], 'saved-private-key'))
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['codex-a']) as models:
            result = self.manager.change({'provider': 'codex', 'action': 'models', 'id': row['id']})
        self.assertGreaterEqual(self.accounts.api_credentials.call_count, 1)
        models.assert_called_once_with(row['baseUrl'], 'saved-private-key')
        self.assertEqual(result['probe']['models'], ['codex-a'])
        self.assertIsInstance(result['probe']['cache']['updatedAt'], float)
        self.assertNotIn('saved-private-key', json.dumps(result))
        self.assertFalse(self.manager.codex_switch)

    def test_codex_draft_models_validate_credentials_without_saving_or_switching(self):
        self.accounts.assert_editable = Mock()
        self.accounts.api_url = Accounts.api_url
        self.accounts.api_credentials = lambda value: Accounts.api_credentials(self.accounts, value)
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        request = {'provider': 'codex', 'action': 'modelsDraft', 'baseUrl': 'https://fixture.invalid/v1', 'apiKey': 'fake-draft-key'}
        with patch('bridge.features.accounts.api_drafts.model_ids', return_value=['model-a']) as models:
            self.assertEqual(self.manager.change(request), {'models': ['model-a']})
            models.assert_called_once_with(request['baseUrl'], request['apiKey'])
        self.assertFalse(self.manager.codex_switch)
        after = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        with patch('bridge.features.accounts.api_drafts.model_ids') as models:
            for invalid in ({'baseUrl': 'file:///etc/passwd'}, {'apiKey': ''}, {'apiKey': 'bad\nkey'}, {'provider': 'claude'}, {'confirmed': True}):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    self.manager.change({**request, **invalid})
            models.assert_not_called()

    def test_draft_models_cannot_send_a_saved_key_to_a_changed_url(self):
        self.accounts.assert_editable = Mock()
        self.accounts.api_url = Accounts.api_url
        self.accounts.row = Mock(return_value={'kind': 'api', 'baseUrl': 'https://original.invalid/v1'})
        self.accounts.api_credentials = Mock(return_value=('https://original.invalid/v1', 'fake-saved-key'))
        request = {'provider': 'codex', 'action': 'modelsDraft', 'id': 'saved', 'apiKey': '', 'baseUrl': 'https://different.invalid/v1'}
        with patch('bridge.features.accounts.api_drafts.model_ids', return_value=['model-a']) as models:
            with self.assertRaisesRegex(ValueError, '重新填写 API Key'):
                self.manager.change(request)
            self.accounts.api_credentials.assert_not_called()
            models.assert_not_called()
            self.manager.change({**request, 'baseUrl': 'https://original.invalid/v1/'})
            models.assert_called_once_with('https://original.invalid/v1', 'fake-saved-key')

    def test_models_probe_persists_and_explicit_refresh_retains_last_success(self):
        identifier = self.save_api()
        request = {'provider': 'claude', 'action': 'models', 'id': identifier}
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['first']) as upstream:
            first = self.manager.change(request)['probe']
            self.assertEqual(self.manager.change(request)['probe']['models'], ['first'])
        upstream.assert_called_once()
        restarted = CliAccess(self.bridge)
        self.addCleanup(restarted.close)
        with patch('bridge.features.accounts.cli_access.model_ids', side_effect=AssertionError('cached read queried upstream')):
            cached = restarted.change(request)['probe']
        self.assertEqual(cached['cache']['identity'], first['cache']['identity'])
        self.assertEqual(cached['cache']['updatedAt'], first['cache']['updatedAt'])
        with patch('bridge.features.accounts.cli_access.model_ids', side_effect=ValueError('upstream unavailable')):
            failed = restarted.change({**request, 'refresh': True})['probe']
        self.assertEqual(failed['models'], ['first'])
        self.assertEqual(failed['cache']['updatedAt'], first['cache']['updatedAt'])
        self.assertEqual(failed['cache']['state'], 'stale')
        self.assertTrue(failed['modelError'])
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['second']):
            refreshed = restarted.change({**request, 'refresh': True})['probe']
        self.assertEqual(refreshed['models'], ['second'])
        self.assertGreater(refreshed['cache']['updatedAt'], first['cache']['updatedAt'])

    def test_models_probe_rotates_identity_on_saved_key_and_refreshes_after_twelve_hours(self):
        identifier = self.save_api()
        request = {'provider': 'claude', 'action': 'models', 'id': identifier}
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['old']):
            first = self.manager.change(request)['probe']
        db = sqlite3.connect(self.manager.model_store.path)
        try:
            db.execute('UPDATE catalogs SET updated_at=0')
            db.commit()
        finally:
            db.close()
        with patch.object(self.manager, '_start_model_refresh') as background, \
             patch('bridge.features.accounts.cli_access.model_ids', side_effect=AssertionError('stale read queried upstream')):
            stale = self.manager.change(request)['probe']
        self.assertEqual(stale['cache']['state'], 'stale')
        background.assert_called_once_with('claude', identifier, first['cache']['identity'])
        self.save_api(id=identifier, apiKey='new-fixture-key')
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['new']) as upstream:
            changed = self.manager.change(request)['probe']
        upstream.assert_called_once()
        self.assertNotEqual(changed['cache']['identity'], first['cache']['identity'])
        self.assertEqual(changed['models'], ['new'])

    def test_connectivity_probe_rejects_credential_change_during_request(self):
        identifier = self.save_api()
        def changed(*args, **kwargs):
            self.save_api(id=identifier, apiKey='replacement-key')
            return ['old-key-model']
        with patch('bridge.features.accounts.cli_access.model_ids', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '接入已变化'):
                self.manager.change({'provider': 'claude', 'action': 'test', 'id': identifier})

    def test_concurrent_cold_model_reads_share_one_upstream_request(self):
        identifier = self.save_api()
        request = {'provider': 'claude', 'action': 'models', 'id': identifier}
        entered, release = threading.Event(), threading.Event()
        responses, errors = [], []
        def upstream(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return ['shared']
        def read():
            try:
                responses.append(self.manager.change(request)['probe'])
            except Exception as exc:
                errors.append(exc)
        with patch('bridge.features.accounts.cli_access.model_ids', side_effect=upstream) as fetch:
            first = threading.Thread(target=read)
            second = threading.Thread(target=read)
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            release.set()
            first.join(3)
            second.join(3)
        self.assertFalse(errors)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([value['models'] for value in responses], [['shared'], ['shared']])

    def test_close_cancels_scheduled_model_refresh(self):
        identifier = self.save_api()
        with patch('bridge.features.accounts.cli_access.model_ids', return_value=['first']):
            result = self.manager.change({'provider': 'claude', 'action': 'models', 'id': identifier})
        identity = result['probe']['cache']['identity']
        timer = self.manager.model_timers[identity]
        self.manager.close()
        self.assertTrue(timer.finished.is_set())
        self.assertEqual(self.manager.model_timers, {})

    def test_probe_rejects_native_and_extra_request_fields(self):
        identifier = self.save_api()
        with self.assertRaisesRegex(ValueError, '请求无效'):
            self.manager.change({'provider': 'claude', 'action': 'models', 'id': identifier,
                                 'apiKey': 'untrusted'})
        self.manager.index['claude'][0]['kind'] = 'native'
        with self.assertRaisesRegex(ValueError, '已保存的 API'):
            self.manager.change({'provider': 'claude', 'action': 'models', 'id': identifier})

    def test_missing_explicit_native_directory_cannot_be_initialized(self):
        self.native.rmdir()
        self.profile['origin'] = 'environment:'+str(self.native)+':none'
        with self.assertRaisesRegex(ValueError, '配置目录不可用'):
            self.save_api()
        self.assertFalse(self.native.exists())

    def test_switch_keeps_native_directory_and_requires_verification(self):
        identifier = self.save_api()
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.return_value = {'status': 'configured', 'authentication': 'configured'}
        with self.sync_patch(sync):
            result = self.manager.change({'provider': 'claude', 'action': 'switch',
                                          'id': identifier, 'confirmed': True})
        self.assertEqual(result['claude']['activeId'], identifier)
        self.assertEqual(self.manager.claude_profile()['directory'], self.native)
        self.assertTrue(self.manager.claude_profile()['managedApi'])
        self.assertEqual(result['claude']['verification']['status'], 'configured')
        sync.apply.assert_called_once()
        sync.verify.assert_called_once()

    def test_native_import_keeps_original_directory_and_history(self):
        history = self.native/'history.jsonl'
        history.write_text('original history')
        with patch('bridge.features.accounts.cli_access._auth_ready', return_value=True):
            self.manager.change({'provider': 'claude', 'action': 'importCurrent',
                                 'name': 'Original Claude'})
        row = self.manager.index['claude'][0]
        self.assertEqual(row['directory'], str(self.native.resolve()))
        self.assertEqual(history.read_text(), 'original history')
        self.assertEqual(sorted(path.name for path in self.native.iterdir()), ['history.jsonl'])
        self.assertEqual(self.manager.control({'action': 'list'})['claude']['accounts'][0]['kind'], 'native')
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        with self.sync_patch(sync):
            self.manager.change({'provider': 'claude', 'action': 'switch',
                                 'id': row['id'], 'confirmed': True})
        self.assertEqual(self.manager.claude_profile(), self.profile)

    def test_native_import_rejects_symlink_before_resolving(self):
        alias = self.root/'native-alias'
        try:
            alias.symlink_to(self.native, target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks are unavailable on this host')
        with patch('bridge.features.accounts.cli_access._auth_ready', return_value=True), \
             self.assertRaisesRegex(ValueError, '配置目录不可用'):
            self.manager.change({'provider': 'claude', 'action': 'importCurrent',
                                 'name': 'Alias', 'directory': str(alias)})
        self.assertFalse(self.manager.index['claude'])

    def test_failed_verification_restores_and_keeps_selected_profile(self):
        identifier = self.save_api()
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.side_effect = ValueError('verification failed')
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, 'verification failed'):
            self.manager.change({'provider': 'claude', 'action': 'switch',
                                 'id': identifier, 'confirmed': True})
        self.assertIsNone(self.manager.public()['claude']['activeId'])
        self.assertEqual(self.manager.claude_profile(), self.profile)
        sync.restore.assert_called_once()

    def test_native_baseline_survives_pointer_save_failure(self):
        api_id = self.save_api()
        self.manager.index['activeClaude'] = api_id
        self.manager._save()
        native_id = 'c'*32
        native_row = {'id': native_id, 'name': 'Original', 'kind': 'native',
                      'directory': str(self.native), 'originBase': 'native:'+str(self.native)}
        self.manager.index['claude'].append(native_row)
        private_json(self.native/'settings.json', {'env': {
            'ANTHROPIC_BASE_URL': 'https://managed.invalid',
            'ANTHROPIC_AUTH_TOKEN': 'managed-fixture-key'}})
        settings_before = (self.native/'settings.json').read_bytes()
        baseline = LocalCliSync(self.bridge)._claude_baseline(self.native.resolve())
        private_json(baseline, {'ANTHROPIC_API_KEY': 'original-fixture-key'})
        baseline_before = baseline.read_bytes()
        with patch('bridge.features.accounts.cli_sync.LocalCliSync._claude_auth',
                   return_value={'loggedIn': True}), \
             patch.object(self.manager, '_save', side_effect=[OSError('durable save failed'), None]):
            with self.assertRaisesRegex(OSError, 'durable save failed'):
                self.manager.change({'provider': 'claude', 'action': 'switch',
                                     'id': native_id, 'confirmed': True})
        self.assertEqual(self.manager.index['activeClaude'], api_id)
        self.assertEqual((self.native/'settings.json').read_bytes(), settings_before)
        self.assertEqual(baseline.read_bytes(), baseline_before)

    def test_busy_preflight_rejects_without_mutating_selection(self):
        identifier = self.save_api()
        sync = Mock()
        sync.preflight.side_effect = ValueError('busy')
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, 'busy'):
            self.manager.change({'provider': 'claude', 'action': 'switch',
                                 'id': identifier, 'confirmed': True})
        self.assertIsNone(self.manager.public()['claude']['activeId'])
        sync.snapshot.assert_not_called()

    def test_claude_switch_does_not_invert_cli_and_profile_locks(self):
        identifier = self.save_api()
        cli = ClaudeCli(profile_resolver=self.manager.claude_profile)
        self.bridge.desktop_sessions = SimpleNamespace(adapters={'claude': SimpleNamespace(cli=cli)})
        class ObservedLock:
            def __init__(self):
                self.inner = threading.RLock()
                self.switch_attempted = threading.Event()
            def __enter__(self):
                if threading.current_thread().name == 'switch':
                    self.switch_attempted.set()
                self.inner.acquire()
                return self
            def __exit__(self, *_):
                self.inner.release()
        cli.lock = ObservedLock()
        holding = threading.Event()
        release = threading.Event()
        poll_done = threading.Event()
        switch_done = threading.Event()
        failures = []
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.return_value = {'status': 'configured'}
        def poll():
            try:
                with cli.lock:
                    holding.set()
                    assert release.wait(2)
                    self.manager.claude_profile()
            except Exception as error:
                failures.append(error)
            finally:
                poll_done.set()
        def switch():
            try:
                self.manager.change({'provider': 'claude', 'action': 'switch',
                                     'id': identifier, 'confirmed': True})
            except Exception as error:
                failures.append(error)
            finally:
                switch_done.set()
        with self.sync_patch(sync):
            poll_thread = threading.Thread(target=poll, daemon=True)
            switch_thread = threading.Thread(target=switch, name='switch', daemon=True)
            poll_thread.start()
            self.assertTrue(holding.wait(2))
            switch_thread.start()
            self.assertTrue(cli.lock.switch_attempted.wait(2))
            release.set()
            self.assertTrue(poll_done.wait(2), 'poll deadlocked resolving profile')
            self.assertTrue(switch_done.wait(2), 'switch deadlocked behind CLI lock')
        self.assertFalse(failures)

    def test_resume_waits_until_claude_switch_transaction_finishes(self):
        identifier = self.save_api()
        cli = ClaudeCli(profile_resolver=self.manager.claude_profile)
        self.bridge.desktop_sessions = SimpleNamespace(adapters={'claude': SimpleNamespace(cli=cli)})
        applying = threading.Event()
        finish_apply = threading.Event()
        switch_done = threading.Event()
        resume_done = threading.Event()
        failures = []
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.return_value = {'status': 'configured'}
        def apply(*_):
            applying.set()
            assert finish_apply.wait(2)
        sync.apply.side_effect = apply
        def switch():
            try:
                self.manager.change({'provider': 'claude', 'action': 'switch',
                                     'id': identifier, 'confirmed': True})
            except Exception as error:
                failures.append(error)
            finally:
                switch_done.set()
        def resume():
            try:
                cli.call('resume', 'old-session', {})
            except ResumePreflightBlocked:
                pass  # The old origin is stale after the completed switch.
            except Exception as error:
                failures.append(error)
            finally:
                resume_done.set()
        with self.sync_patch(sync), patch.object(cli, '_get', return_value={
                'nativeId': 'old-session', 'origin': 'old-origin', 'archived': False}), \
                patch.object(cli, '_public', return_value={'permissionMode': 'default'}):
            switch_thread = threading.Thread(target=switch, daemon=True)
            switch_thread.start()
            self.assertTrue(applying.wait(2))
            resume_thread = threading.Thread(target=resume, daemon=True)
            resume_thread.start()
            self.assertFalse(resume_done.wait(.1), 'resume crossed in-progress settings write')
            finish_apply.set()
            self.assertTrue(switch_done.wait(2))
            self.assertTrue(resume_done.wait(2))
        self.assertFalse(failures)

    def test_codex_confirmed_request_reuses_accounts_transaction_and_identifier(self):
        import uuid
        row = {'id': 'saved', 'kind': 'api'}
        self.accounts.row = Mock(return_value=row)
        self.accounts.switch = Mock()
        sync = Mock()
        sync._codex_runtime.return_value = (False, None)
        request_id = str(uuid.uuid4())
        with self.sync_patch(sync):
            self.manager.change({'provider': 'codex', 'action': 'switch', 'id': 'saved',
                'confirmed': True, 'tasksConfirmed': True, 'requestId': request_id})
        self.accounts.switch.assert_called_once_with({'id': 'saved', 'requestId': request_id,
            'confirmed': True, 'tasksConfirmed': True})
        sync.apply.assert_not_called()
        sync.preflight.assert_not_called()

    def test_codex_receipt_does_not_manufacture_task_or_desktop_confirmation(self):
        row = {'id': 'saved', 'kind': 'api'}
        self.accounts.row = Mock(return_value=row)
        self.accounts.switch = Mock()
        sync = Mock()
        sync._codex_runtime.return_value = (True, Mock())
        with self.sync_patch(sync):
            for extra in ({}, {'tasksConfirmed': True}):
                with self.assertRaisesRegex(ValueError, '确认'):
                    self.manager.change({'provider': 'codex', 'action': 'switch', 'id': 'saved',
                        'confirmed': True, 'requestId': 'request', **extra})
        self.accounts.switch.assert_not_called()

    def test_codex_stopped_desktop_uses_same_saved_row_and_verifies(self):
        row = {'id': 'saved', 'kind': 'api', 'name': 'Codex API'}
        self.accounts.row = Mock(return_value=row)
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        with self.sync_patch(sync):
            self.manager.change({'provider': 'codex', 'action': 'switch',
                                 'id': 'saved', 'confirmed': True})
        sync.apply.assert_called_once_with('codex', row)
        sync.verify.assert_called_once_with('codex', row)

    def test_codex_prewrite_rejection_never_restores_stale_snapshot(self):
        row = {'id': 'saved', 'kind': 'api', 'name': 'Codex API'}
        self.accounts.row = Mock(return_value=row)
        config = self.root/'config.toml'
        config.write_text('before')
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.snapshot.side_effect = lambda _: {'files': {'config.toml': config.read_text()}}
        def reject_before_write(*_):
            # Another Desktop transaction has changed the shared files. The
            # CLI apply rejects before its own write and must not undo it.
            config.write_text('desktop-new')
            raise ValueError('Desktop switch in progress')
        sync.apply.side_effect = reject_before_write
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, 'Desktop switch'):
            self.manager.change({'provider': 'codex', 'action': 'switch',
                                 'id': 'saved', 'confirmed': True})
        self.assertEqual(config.read_text(), 'desktop-new')
        sync.restore.assert_not_called()
        sync.verify.assert_not_called()

    def test_codex_postapply_verification_restores_only_mutated_switch(self):
        row = {'id': 'saved', 'kind': 'api'}
        self.accounts.row = Mock(return_value=row)
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.apply.return_value = {'status': 'applied', 'mutated': True}
        sync.verify.side_effect = ValueError('verification failed')
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, 'verification failed'):
            self.manager.change({'provider': 'codex', 'action': 'switch',
                                 'id': 'saved', 'confirmed': True})
        sync.restore.assert_called_once()

    def test_codex_snapshot_through_verify_holds_desktop_transaction_gate(self):
        self.accounts.row = Mock(return_value={'id': 'saved', 'kind': 'api'})
        applying = threading.Event()
        finish = threading.Event()
        desktop_entered = threading.Event()
        switch_done = threading.Event()
        failures = []
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.apply.return_value = {'status': 'applied', 'mutated': True}
        def apply(*_):
            applying.set()
            assert finish.wait(2)
            return {'status': 'applied', 'mutated': True}
        sync.apply.side_effect = apply
        def switch():
            try:
                self.manager.change({'provider': 'codex', 'action': 'switch',
                                     'id': 'saved', 'confirmed': True})
            except Exception as error:
                failures.append(error)
            finally:
                switch_done.set()
        def desktop():
            with self.accounts.gate:
                desktop_entered.set()
        with self.sync_patch(sync):
            threading.Thread(target=switch, daemon=True).start()
            self.assertTrue(applying.wait(2))
            threading.Thread(target=desktop, daemon=True).start()
            self.assertFalse(desktop_entered.wait(.1))
            finish.set()
            self.assertTrue(switch_done.wait(2))
            self.assertTrue(desktop_entered.wait(2))
        self.assertFalse(failures)

    def test_codex_running_desktop_requires_explicit_restart_confirmation(self):
        self.accounts.row = Mock(return_value={'id': 'saved', 'kind': 'api'})
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': True}
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, '重启'):
            self.manager.change({'provider': 'codex', 'action': 'switch',
                                 'id': 'saved', 'confirmed': True})
        sync.snapshot.assert_not_called()
        self.assertIsNone(self.manager.codex_switch)


if __name__ == '__main__':
    unittest.main()
