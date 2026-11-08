"""Independent account transaction boundaries; synthetic target directories only."""
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bridge.features.accounts import wsl_account_worker as worker

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'Target worker requires POSIX permissions/UID; native WSL runtime is verified separately')
class WslAccountBoundaryTests(unittest.TestCase):
    def setUp(self):
        from tests.test_wsl_accounts import install_target_toml
        install_target_toml(self)
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        (self.home / 'auth.json').write_text('{"token":"native-fixture"}')
        (self.home / 'config.toml').write_text('model = "original"\n# keep original comment\n[history]\npersistence = "save-all"\n')
        self.store = worker.AccountStore('codex', self.home, guard=lambda *_: [])
        self.store.change({'action': 'save-api', 'name': 'Fixture', 'model': 'fixture-model',
                           'baseUrl': 'https://fixture.invalid/v1', 'apiKey': 'fixture-secret'})
        self.identifier = self.store.public()['accounts'][0]['id']

    def switch(self, **options):
        return self.store.change({'action': 'switch', 'id': self.identifier,
                                  'expectedIdentity': self.store.public()['identity'], **options})

    def test_stale_identity_rejects_before_creating_native_snapshot_or_touching_files(self):
        before = self.store.files()
        with self.assertRaises(worker.AccountError):
            self.switch(expectedIdentity='stale')
        self.assertEqual(self.store.files(), before)
        self.assertNotIn('native', self.store.store)

    def test_switch_public_metadata_and_native_restore_do_not_leak_secrets(self):
        before = self.store.files()
        result = self.switch()
        rendered = json.dumps(result)
        self.assertNotIn('fixture-secret', rendered)
        self.assertNotIn('native-fixture', rendered)
        self.assertFalse(result['activeVerified'])
        self.store.change({'action': 'switch', 'id': 'native', 'expectedIdentity': result['identity']})
        self.assertEqual(self.store.files(), before)

    def static_native(self, headers):
        native = ('# Native fixture\nmodel_provider="original_api"\nmodel="old-model"\n'
                  '[model_providers.original_api]\nname="Original"\n'
                  'base_url="https://old.invalid/v1"\nwire_api="responses"\n'
                  'experimental_bearer_token="old-static-private"\n' + headers +
                  '\n[history]\npersistence="save-all"\n')
        (self.home / 'config.toml').write_text(native)
        (self.home / 'auth.json').write_text('{"OPENAI_API_KEY":"unrelated-auth-json-private"}')
        return self.store.files()

    def test_selected_static_auth_switch_removes_both_auth_sources_and_restores_exact_native(self):
        before = self.static_native('http_headers={"x-openai-actor-authorization"="old-actor-private","Authorization"="old-header-private"}\n')
        sessions = self.home / 'sessions'
        sessions.mkdir()
        history = sessions / 'existing.jsonl'
        history.write_text('{"id":"original-thread","cwd":"/old-project","model_provider":"original_api"}\n')
        original_history = history.read_bytes()
        public = self.store.public()
        self.assertFalse(public['capabilities']['capture'])
        self.assertTrue(public['capabilities']['switch'])
        self.assertTrue(public['capabilities']['save'])
        self.assertTrue(public['capabilities']['import'])
        self.assertEqual(public['captureNotice'], worker.ERROR_MESSAGES['static_capture_notice'])
        result = self.switch()
        config = worker._toml_loads(self.store.files()['config.toml'].decode())
        definition = config['model_providers']['original_api']
        self.assertEqual(config['model_provider'], 'original_api')
        self.assertNotIn('experimental_bearer_token', definition)
        self.assertNotIn('http_headers', definition)
        self.assertEqual(definition['base_url'], 'https://fixture.invalid/v1')
        self.assertEqual(json.loads(self.store.files()['auth.json'])['OPENAI_API_KEY'], 'fixture-secret')
        self.assertNotIn(b'old-static-private', self.store.files()['config.toml'])
        self.assertNotIn(b'old-actor-private', self.store.files()['config.toml'])
        self.assertIsNone(result['captureNotice'])
        self.assertTrue(self.store.route_summary()['valid'])
        self.assertEqual(history.read_bytes(), original_history)
        self.store.change({'action': 'switch', 'id': 'native', 'expectedIdentity': result['identity']})
        self.assertEqual(self.store.files(), before)
        self.assertEqual(history.read_bytes(), original_history)

    def test_quoted_inline_static_auth_and_empty_bearer_are_removed(self):
        self.static_native('"http_headers"={"AUTHORIZATION"="old-header-private"}\n')
        source = (self.home / 'config.toml').read_text().replace(
            'experimental_bearer_token="old-static-private"', '"experimental_bearer_token"=""')
        (self.home / 'config.toml').write_text(source)
        self.switch()
        definition = worker._toml_loads(self.store.files()['config.toml'].decode())['model_providers']['original_api']
        self.assertNotIn('experimental_bearer_token', definition)
        self.assertNotIn('http_headers', definition)

    def test_static_auth_capture_rejects_without_snapshot_or_credentials_mutation(self):
        before = self.static_native('http_headers={"x-openai-actor-authorization"="old-actor-private"}\n')
        with patch.object(worker, '_write', side_effect=AssertionError('capture must not write')):
            with self.assertRaisesRegex(worker.AccountError, '不能保存为 auth.json'):
                self.store.change({'action': 'import-current'})
        self.assertEqual(self.store.files(), before)
        self.assertNotIn('native', self.store.store)
        rendered = json.dumps(self.store.public())
        self.assertNotIn('old-static-private', rendered)
        self.assertNotIn('old-actor-private', rendered)
        self.assertNotIn('unrelated-auth-json-private', rendered)

    def test_nested_selected_static_auth_headers_are_removed_without_dropping_other_tables(self):
        before = self.static_native('[model_providers.original_api.http_headers]\n'
                                    '"X-OpenAI-Actor-Authorization"="old-actor-private"\n'
                                    '"authorization"="old-header-private"\n')
        result = self.switch()
        config = worker._toml_loads(self.store.files()['config.toml'].decode())
        self.assertNotIn('http_headers', config['model_providers']['original_api'])
        self.assertNotIn('experimental_bearer_token', config['model_providers']['original_api'])
        self.assertEqual(config['history'], {'persistence': 'save-all'})
        self.store.change({'action': 'switch', 'id': 'native', 'expectedIdentity': result['identity']})
        self.assertEqual(self.store.files(), before)

    def test_unknown_headers_or_other_provider_static_auth_remain_blocked(self):
        for extra in ('http_headers={"X-Unknown"="fixture"}\n',
                      '[model_providers.other]\nexperimental_bearer_token="other-private"\n',
                      '[model_providers.other.http_headers]\nAuthorization="other-private"\n'):
            with self.subTest(extra=extra):
                before = self.static_native(extra)
                self.assertFalse(self.store.public()['capabilities']['switch'])
                with self.assertRaises(worker.AccountError):
                    self.switch()
                self.assertEqual(self.store.files(), before)
                self.assertNotIn('native', self.store.store)

    def test_managed_summary_rejects_reinjected_static_auth_even_with_matching_file_identity(self):
        self.switch()
        for field in ('experimental_bearer_token="injected-private"\n',
                      'http_headers={"x-openai-actor-authorization"="injected-private"}\n'):
            with self.subTest(field=field):
                original = self.store.files()['config.toml']
                (self.home / 'config.toml').write_bytes(original + field.encode())
                self.store.store['appliedIdentity'] = worker._identity(self.store.files())
                self.assertFalse(self.store.route_summary()['valid'])
                (self.home / 'config.toml').write_bytes(original)
                self.store.store['appliedIdentity'] = worker._identity(self.store.files())

    def test_selected_custom_provider_keeps_history_route_with_new_endpoint_and_key(self):
        native = ('model_provider = "original_api"\nmodel = "original-model"\n'
                  '[model_providers.original_api]\nname = "Original API"\n'
                  '# selected route comment\nbase_url = "https://original.invalid/v1"\n'
                  'wire_api = "responses"\nrequires_openai_auth = true\n'
                  '[model_providers.unrelated]\nname = "Public API"\n'
                  'base_url = "https://public.invalid/v1"\nrequest_max_retries = 3\n')
        (self.home / 'config.toml').write_text(native)
        (self.home / 'auth.json').write_text('{"auth_mode":"apikey","OPENAI_API_KEY":"original-private"}')
        # Saved session files and cwd are outside the credential transaction.
        sessions = self.home / 'sessions'
        sessions.mkdir()
        history = sessions / 'existing.jsonl'
        history.write_text('{"id":"same-thread","model_provider":"original_api","cwd":"/project"}\n')
        original_history = history.read_bytes()
        before = self.store.files()
        self.assertTrue(self.store.public()['capabilities']['switch'])
        result = self.switch()
        config = worker._toml_loads(self.store.files()['config.toml'].decode())
        self.assertEqual(config['model_provider'], 'original_api')
        self.assertEqual(config['model_providers']['original_api']['base_url'], 'https://fixture.invalid/v1')
        self.assertEqual(config['openai_base_url'], 'https://fixture.invalid/v1')
        self.assertEqual(json.loads(self.store.files()['auth.json'])['OPENAI_API_KEY'], 'fixture-secret')
        self.assertEqual(config['model_providers']['unrelated'], worker._toml_loads(native)['model_providers']['unrelated'])
        self.assertIn('[model_providers.unrelated]\nname = "Public API"\nbase_url = "https://public.invalid/v1"\nrequest_max_retries = 3',
                      self.store.files()['config.toml'].decode())
        self.assertEqual(history.read_bytes(), original_history)
        self.store.change({'action': 'save-api', 'name': 'Second', 'model': 'second-model',
                           'baseUrl': 'https://second.invalid/v1', 'apiKey': 'second-secret'})
        second = self.store.store['accounts'][-1]['id']
        result = self.switch(id=second)
        config = worker._toml_loads(self.store.files()['config.toml'].decode())
        self.assertEqual(config['model_provider'], 'original_api')
        self.assertEqual(config['model_providers']['original_api']['base_url'], 'https://second.invalid/v1')
        self.assertEqual(json.loads(self.store.files()['auth.json'])['OPENAI_API_KEY'], 'second-secret')
        self.store.change({'action': 'switch', 'id': 'native', 'expectedIdentity': result['identity']})
        self.assertEqual(self.store.files(), before)
        self.assertEqual(history.read_bytes(), original_history)

    def test_legacy_managed_shared_auth_output_can_restore_exact_native_snapshot(self):
        import base64
        before = self.store.files()
        self.store.store['native'] = {key: base64.b64encode(value).decode() if value is not None else None
                                      for key, value in before.items()}
        (self.home / 'config.toml').write_text(
            'model_provider="bridge_wsl_api"\n[model_providers.openai]\nrequires_openai_auth=true\n'
            '[model_providers.bridge_wsl_api]\nbase_url="https://fixture.invalid/v1"\nrequires_openai_auth=true\n')
        (self.home / 'auth.json').write_text('{"OPENAI_API_KEY":"fixture-secret"}')
        self.store.store.update(activeId=self.identifier, appliedIdentity=worker._identity(self.store.files()))
        self.assertTrue(self.store.protected(self.store.files()))
        public = self.store.public()
        self.assertFalse(public['capabilities']['switch'])
        self.assertTrue(public['capabilities']['restore'])
        self.switch(id='native')
        self.assertEqual(self.store.files(), before)

    def test_restore_capability_requires_snapshot_matching_identity_and_clear_runtime(self):
        self.assertFalse(self.store.public()['capabilities']['restore'])
        self.switch()
        self.assertTrue(self.store.public()['capabilities']['restore'])
        self.store.guard = lambda *_: ['fixture runtime unknown']
        self.assertFalse(self.store.public()['capabilities']['restore'])
        self.store.guard = lambda *_: ['fixture runtime running']
        self.assertFalse(self.store.public()['capabilities']['restore'])
        self.store.guard = lambda *_: []
        with (self.home / 'auth.json').open('a') as output:
            output.write(' ')
        self.assertFalse(self.store.public()['capabilities']['restore'])

    def test_custom_provider_external_edit_blocks_native_restore(self):
        (self.home / 'config.toml').write_text('model_provider="custom"\n[model_providers.custom]\nname="Custom"\nbase_url="https://original.invalid"\nrequires_openai_auth=true\n')
        self.switch()
        with (self.home / 'config.toml').open('a') as output:
            output.write('# external edit\n')
        before = self.store.files()
        with self.assertRaises(worker.AccountError):
            self.switch(id='native')
        self.assertEqual(self.store.files(), before)

    def test_runtime_release_candidate_requires_only_the_standard_running_client_reason(self):
        for reasons, expected in (([worker.ERROR_MESSAGES['runtime_running']], True),
                                  (['WSL 环境变量覆盖认证，请先在原终端处理'], False),
                                  (['WSL shell 配置包含认证覆盖，请使用原终端处理'], False),
                                  ([worker.ERROR_MESSAGES['runtime_unknown']], False), ([], False)):
            with self.subTest(reasons=reasons):
                self.store.guard = lambda *_: reasons
                self.assertEqual(self.store.public()['runtimeReleaseCandidate'], expected)

    def test_configuration_and_runtime_blockers_remain_distinct_and_external_edits_are_hard(self):
        self.store.guard = lambda *_: ['fixture runtime remains running']
        state = self.store.public()
        self.assertEqual(state['configurationBlockers'], [])
        self.assertEqual(state['runtimeBlockers'], ['fixture runtime remains running'])
        self.assertEqual(state['blockers'], state['runtimeBlockers'])
        self.store.guard = lambda *_: []
        self.switch()
        with (self.home / 'config.toml').open('a') as output:
            output.write('# external change\n')
        state = self.store.public()
        self.assertIn(worker.ERROR_MESSAGES['external_change'], state['configurationBlockers'])
        self.assertEqual(state['runtimeBlockers'], [])
        self.assertFalse(state['capabilities']['switch'])
        self.assertFalse(state['capabilities']['restore'])

    def test_busy_target_never_starts_a_native_transaction(self):
        before = self.store.files()
        self.store.guard = lambda *_: ['fixture runtime is still running']
        with patch.object(worker, '_write', side_effect=AssertionError('must not write')):
            with self.assertRaisesRegex(worker.AccountError, 'runtime'):
                self.switch()
        self.assertEqual(self.store.files(), before)
        self.assertNotIn('native', self.store.store)

    def test_external_edit_after_snapshot_blocks_write_and_retains_external_edit(self):
        calls = []
        def changing_guard(*_):
            calls.append(True)
            if len(calls) == 2:
                (self.home / 'config.toml').write_text('model = "external"\n')
            return []
        self.store.guard = changing_guard
        with self.assertRaises(worker.AccountError):
            self.store.switch({'id': self.identifier, 'expectedIdentity': worker._identity(self.store.files())})
        self.assertEqual((self.home / 'config.toml').read_text(), 'model = "external"\n')
        self.assertEqual((self.home / 'auth.json').read_text(), '{"token":"native-fixture"}')

    def test_partial_failure_rollback_preserves_external_writer(self):
        original_write = worker._write
        before = self.store.files()
        def write(path, value):
            if Path(path).name == 'auth.json':
                (self.home / 'config.toml').write_text('model = "concurrent-writer"\n')
                raise OSError('fixture failure')
            return original_write(path, value)
        with patch.object(worker, '_write', side_effect=write):
            with self.assertRaises(worker.AccountError):
                self.switch()
        self.assertEqual((self.home / 'config.toml').read_text(), 'model = "concurrent-writer"\n')
        self.assertEqual((self.home / 'auth.json').read_bytes(), before['auth.json'])

    def test_root_bracket_in_comment_or_string_preserves_valid_unrelated_toml(self):
        for prefix in ('# [retained comment]\n', 'model_reasoning_summary = "value[bracket"\n'):
            with self.subTest(prefix=prefix):
                (self.home / 'config.toml').write_text(prefix + 'model = "original"\n[history]\npersistence = "save-all"\n')
                before = self.store.files()
                after = self.store.prepared(self.store.row(self.identifier), before)
                actual = worker._toml_loads(after['config.toml'].decode())
                self.assertEqual(actual['model'], 'fixture-model')
                self.assertEqual(actual['history']['persistence'], 'save-all')
                self.assertIn(prefix, after['config.toml'].decode())

    def test_proven_other_config_home_process_does_not_block_selected_environment(self):
        proc = self.home / 'fixture-proc'
        process = proc / '900001'
        process.mkdir(parents=True)
        (process / 'cmdline').write_bytes(b'/usr/bin/codex\0app-server\0')
        (process / 'environ').write_bytes(b'CODEX_HOME=/home/fixture/other-codex\0')
        (process / 'exe').symlink_to('/usr/bin/codex')
        original_path = Path
        def selected_path(value):
            return proc if str(value) == '/proc' else original_path(value)
        with patch.object(worker, 'Path', side_effect=selected_path), \
                patch.object(worker.os.path, 'expanduser', return_value=str(self.home)), \
                patch.dict(worker.os.environ, {}, clear=True):
            self.assertEqual(worker.runtime_blockers('codex', self.home), [])
            (process / 'environ').write_bytes(b'CODEX_HOME=' + str(self.home).encode() + b'\0')
            self.assertTrue(worker.runtime_blockers('codex', self.home))

    def test_read_has_no_disk_mutation_or_process_start(self):
        before = {p.name: p.read_bytes() for p in self.home.iterdir() if p.is_file()}
        with patch('subprocess.Popen', side_effect=AssertionError('must not spawn')), \
                patch('subprocess.run', side_effect=AssertionError('must not spawn')):
            self.store.public()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.home.iterdir() if p.is_file()})


class WslAccountRoutingBoundaries(unittest.TestCase):
    def setUp(self):
        from bridge.features.accounts.wsl_accounts import WslAccounts
        from bridge.features.endpoints.registry import entry_id
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.row = {'distribution': 'FixtureUbuntu', 'user': 'fixture', 'home': '/home/fixture/.codex',
                    'label': 'Fixture', 'enabled': True}
        self.row['id'] = entry_id(self.row['distribution'], self.row['user'], self.row['home'])
        self.registry = SimpleNamespace(entries=[self.row], lock=threading.RLock(),
                                        directory=self.directory, bridges={})
        self.bridge = SimpleNamespace(data_dir=self.directory, entries=self.registry)
        self.accounts = WslAccounts(self.bridge)
        self.running = patch('bridge.features.accounts.wsl_accounts.wsl.running_distributions', return_value=['FixtureUbuntu'])
        self.running.start()
        self.addCleanup(self.running.stop)
        call_patch = patch.object(self.accounts, '_call', return_value={'available': True, 'accounts': [],
                                 'blockers': [], 'capabilities': {'save': True, 'switch': True, 'restore': True}})
        self.call = call_patch.start()
        self.addCleanup(call_patch.stop)

    def request(self, **values):
        return {'provider': 'codex', 'entry': self.row['id'], 'action': 'switch',
                'expectedIdentity': 'fixture-identity', 'id': 'saved', **values}

    def test_local_only_profiles_never_start_wsl_inventory(self):
        self.registry.entries = [{'id': 'codex:fixture', 'kind': 'codex-local', 'home': '/fixture'}]
        with patch('bridge.features.accounts.wsl_accounts.wsl.running_distributions') as inventory:
            self.assertEqual(self.accounts.environments('codex')['environments'], [])
            inventory.assert_not_called()

    def test_invalid_cross_provider_disabled_and_tampered_targets_never_dispatch(self):
        for entry in ('desktop', 'local', 'ssh:fixture', 'wsl:'+'f'*32, 'wsl-claude:'+'f'*32):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.accounts.change(self.request(entry=entry))
        self.row['enabled'] = False
        with self.assertRaises(ValueError):
            self.accounts.change(self.request())
        self.row['enabled'] = True
        self.row['home'] = '/home/fixture/other-home'
        with self.assertRaises(ValueError):
            self.accounts.change(self.request())
        self.call.assert_not_called()

    def test_authenticated_web_can_manage_saved_environment_credentials(self):
        requests = ({'action': 'save-api', 'name': 'Fixture', 'model': 'fixture',
                     'baseUrl': 'https://fixture.invalid', 'apiKey': 'private-fixture'},
                    {'action': 'rename', 'id': 'saved', 'name': 'New name'},
                    {'action': 'delete', 'id': 'saved'})
        for fields in requests:
            with self.subTest(action=fields['action']):
                self.accounts.change({'provider': 'codex', 'entry': self.row['id'], **fields})
        self.assertEqual(self.call.call_count, len(requests))
        for call in self.call.call_args_list:
            self.assertEqual(call.args[1]['id'], self.row['id'])

    def test_stopped_environment_read_remains_passive_and_change_never_dispatches(self):
        with patch('bridge.features.accounts.wsl_accounts.wsl.running_distributions', return_value=[]):
            result = self.accounts.read('codex', self.row['id'])
            self.assertFalse(result['available'])
            with self.assertRaises(ValueError):
                self.accounts.change(self.request())
        self.call.assert_not_called()

    def test_unknown_and_queued_receipts_are_preserved_and_block_only_their_environment(self):
        import copy
        for status in ('unknown', 'queued', 'pending'):
            with self.subTest(status=status):
                receipts = {'fixture-request': {'result': {'status': status}, 'body': 'retained draft'}}
                before = copy.deepcopy(receipts)
                endpoint = SimpleNamespace(write_lock=threading.RLock(), lock=threading.RLock(),
                                           receipts=receipts, server=None, live={})
                self.registry.bridges[self.row['id']] = endpoint
                with self.assertRaises(ValueError):
                    self.accounts.change(self.request())
                self.assertEqual(receipts, before)
        self.call.assert_not_called()
        self.registry.bridges = {'wsl:'+'a'*32: endpoint}
        self.accounts.change(self.request())
        self.call.assert_called_once()

    def test_read_masks_switch_and_restore_for_unknown_receipts_without_mutation(self):
        import copy
        for status in ('unknown', 'queued', 'pending'):
            with self.subTest(status=status):
                receipts = {'fixture': {'result': {'status': status}, 'body': 'retained'}}
                original = copy.deepcopy(receipts)
                self.registry.bridges[self.row['id']] = SimpleNamespace(
                    write_lock=threading.RLock(), lock=threading.RLock(), receipts=receipts, server=None, live={})
                self.call.return_value = {'available': True, 'accounts': [], 'blockers': [],
                                         'capabilities': {'save': True, 'switch': True, 'restore': True}}
                result = self.accounts.read('codex', self.row['id'])
                self.assertFalse(result['capabilities']['switch'])
                self.assertFalse(result['capabilities']['restore'])
                self.assertTrue(result['capabilities']['save'])
                self.assertEqual(receipts, original)
        self.assertTrue(all(call.args[-1] == {'action': 'read'} for call in self.call.call_args_list))

    def test_read_distinguishes_unresolved_receipts_from_failed_verification(self):
        path = self.directory / self.row['id'][4:] / 'operations.json'
        path.parent.mkdir()
        for original in (b'{"fixture":{"result":{"status":"unknown"}}}', b'invalid receipt json'):
            with self.subTest(original=original):
                path.write_bytes(original)
                self.call.return_value = {'available': True, 'accounts': [], 'blockers': [],
                                         'capabilities': {'save': True, 'switch': True, 'restore': True}}
                result = self.accounts.read('codex', self.row['id'])
                self.assertFalse(result['capabilities']['switch'])
                self.assertFalse(result['capabilities']['restore'])
                self.assertIn('无法核验' if original == b'invalid receipt json' else '结果待核对', result['blockers'][-1])
                self.assertNotIn('仍运行', result['blockers'][-1])
                self.assertEqual(path.read_bytes(), original)

    def test_successful_read_is_advisory_and_change_rechecks_receipts(self):
        result = self.accounts.read('codex', self.row['id'])
        self.assertTrue(result['capabilities']['restore'])
        self.registry.bridges[self.row['id']] = SimpleNamespace(
            write_lock=threading.RLock(), lock=threading.RLock(),
            receipts={'later': {'result': {'status': 'unknown'}}}, server=None, live={})
        self.call.reset_mock()
        with self.assertRaises(ValueError):
            self.accounts.change(self.request(id='native'))
        self.call.assert_not_called()

    def test_transport_uses_selected_user_private_stdin_and_never_retries_unknown_write(self):
        from bridge.features.accounts.wsl_accounts import WslAccounts
        with patch('bridge.features.accounts.wsl_accounts.wsl.read', side_effect=TimeoutError('fixture unknown')) as read:
            with self.assertRaises(TimeoutError):
                WslAccounts._call(self.accounts, 'codex', self.row,
                                  {'action': 'save-api', 'apiKey': 'fixture-private-secret'})
        read.assert_called_once()
        args, kwargs = read.call_args
        self.assertEqual(args[0], 'FixtureUbuntu')
        self.assertEqual(args[2], 'fixture')
        self.assertIn('fixture-private-secret', args[1])
        self.assertFalse(kwargs['allow_start'])
        # The platform transport's second argument is source stdin, never argv.

    def test_unexpected_target_exception_cannot_echo_secret_details(self):
        from bridge.features.accounts.wsl_accounts import WslAccounts
        import contextlib
        import io
        source = "class AccountError(ValueError): pass\ndef execute(request): raise RuntimeError('fixture-private-secret')\n"
        def execute_target(_distribution, source, _user, **_options):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(source, {'json': json})
            return json.loads(output.getvalue())
        with patch('bridge.features.accounts.wsl_accounts.worker_source', return_value=source), \
                patch('bridge.features.accounts.wsl_accounts.wsl.read', side_effect=execute_target):
            with self.assertRaises(ValueError) as error:
                WslAccounts._call(self.accounts, 'codex', self.row, {'action': 'read'})
        self.assertNotIn('fixture-private-secret', str(error.exception))

    def test_model_identity_change_during_refresh_cannot_restore_old_cached_models(self):
        from bridge.clients.codex.catalog import ModelStore
        digest = 'old-profile-digest'
        identity = ModelStore.key('wsl-saved-api', 'codex', self.row['id'], digest)
        self.accounts.models_store.write(identity, {'models': [{'id': 'old-account-model'}]})
        old = {'accounts': [{'id': 'saved', 'modelCatalogIdentity': digest}]}
        changed = {'accounts': [{'id': 'saved', 'modelCatalogIdentity': 'new-profile-digest'}]}
        state = {'changed': False}
        def target(_provider, _row, request):
            if request['action'] == 'read':
                return changed if state['changed'] else old
            state['changed'] = True
            return {'models': [], 'modelError': '模型接入已变化', 'identityChanged': True}
        self.call.side_effect = target
        try:
            result = self.accounts.change(self.request(action='models', refresh=True))
        except ValueError:
            return  # Explicit stale-identity rejection is the preferred outcome.
        self.assertEqual(result.get('models', []), [])
        self.assertNotEqual(result.get('cache', {}).get('state'), 'stale')

    def test_persisted_unknown_receipt_blocks_after_restart_without_rewriting_it(self):
        path = self.directory / self.row['id'][4:] / 'operations.json'
        path.parent.mkdir()
        original = b'{"fixture":{"result":{"status":"unknown"},"request":"retained"}}'
        path.write_bytes(original)
        with self.assertRaises(ValueError):
            self.accounts.change(self.request())
        self.assertEqual(path.read_bytes(), original)
        self.call.assert_not_called()


class ClaudeWslDurableGuardTests(unittest.TestCase):
    def setUp(self):
        from bridge.clients.manager import DesktopSessions
        from bridge.clients.claude.wsl import entry_id
        from bridge.features.accounts.wsl_account_guard import account_guard
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        with patch('bridge.clients.manager.sys.platform', 'win32'):
            self.manager = DesktopSessions(self.temp.name)
        self.addCleanup(self.manager.close)
        self.row = {'distribution': 'FixtureUbuntu', 'user': 'fixture', 'configDir': '/home/fixture/.claude',
                    'label': 'Fixture', 'enabled': True}
        self.row['id'] = entry_id(self.row['distribution'], self.row['user'], self.row['configDir'])
        self.manager.claude_wsl.entries = [self.row]
        self.remote = self.manager.adapters['claude'].remote_cli.wsl
        self.remote.index['fixture-session'] = {'host': self.row['id']}
        self.bridge = SimpleNamespace(desktop_sessions=self.manager, data_dir=self.temp.name)
        self.bridge.wsl_account_guard = lambda provider, row, **options: account_guard(self.bridge, provider, row, **options)
        self.guard = lambda: account_guard(self.bridge, 'claude', self.row)

    def receipt(self, result, sid='fixture-session'):
        import uuid
        with patch.object(self.manager, '_adapter_call', return_value=result):
            return self.manager.call('claude', 'send', sid, {'id': str(uuid.uuid4()), 'text': 'synthetic'})

    def test_exact_unknown_scope_persists_and_blocks_without_mutating_receipt(self):
        self.receipt({'status': 'unknown'})
        before = self.manager.db.execute('SELECT * FROM requests').fetchall()
        with self.assertRaises(ValueError), self.guard():
            pass
        self.assertEqual(before, self.manager.db.execute('SELECT * FROM requests').fetchall())
        self.assertEqual(self.manager.db.execute('SELECT provider,host,sid FROM request_scopes').fetchone(),
                         ('claude', self.row['id'], 'fixture-session'))

    def test_other_host_known_rejection_and_legacy_unknown_are_not_reclassified(self):
        self.remote.index['other-session'] = {'host': 'wsl-claude:' + 'f'*32}
        self.receipt({'status': 'unknown'}, 'other-session')
        self.receipt({'status': 'blocked'})
        self.manager.db.execute('INSERT INTO requests VALUES (?,?,?)',
                                ('legacy', 'irreversible-hash', '{"status":"unknown"}'))
        self.manager.db.commit()
        with self.guard():
            pass
        self.assertEqual(self.manager.db.execute('SELECT result FROM requests WHERE id="legacy"').fetchone()[0],
                         '{"status":"unknown"}')

    def test_local_and_missing_scope_keep_original_receipts_without_side_table(self):
        self.receipt({'status': 'accepted'}, 'local-or-unloaded-session')
        self.assertIsNone(self.manager.db.execute("SELECT name FROM sqlite_master WHERE name='request_scopes'").fetchone())
        with self.guard():
            pass

    def test_missing_optional_scope_metadata_does_not_create_new_preflight_failure(self):
        from bridge.features.accounts.wsl_account_guard import request_scope
        with patch.object(self.manager.directory_projects, 'get', side_effect=ValueError('fixture missing')):
            self.assertIsNone(request_scope(self.manager, 'claude', 'create', None, {'projectKey': 'fixture'}))
        self.assertIsNone(request_scope(self.manager, 'deepseek', 'send', 'fixture', {}))

    def test_active_pending_target_blocks_but_other_host_does_not(self):
        from unittest.mock import Mock
        owned = SimpleNamespace(process=Mock(), condition=threading.Condition(), active=True, pending={})
        owned.process.poll.return_value = None
        self.remote.owned['fixture-session'] = owned
        with self.assertRaises(ValueError), self.guard():
            pass
        self.remote.index['fixture-session']['host'] = 'wsl-claude:'+'f'*32
        with self.guard():
            pass
        self.remote.owned.clear()  # Synthetic owner has no native lifecycle.

    def test_read_checks_claude_unknown_receipts_without_changing_epochs_or_records(self):
        from bridge.features.accounts.wsl_accounts import WslAccounts
        accounts = WslAccounts(self.bridge)
        self.receipt({'status': 'unknown'})
        epochs = (dict(self.manager.read_epochs), dict(self.manager.read_changes),
                  dict(self.manager.binding_epochs), dict(self.manager.binding_changes))
        before = list(self.manager.db.iterdump())
        value = {'available': True, 'accounts': [], 'blockers': [],
                 'capabilities': {'save': True, 'switch': True, 'restore': True}}
        with patch.object(accounts, '_call', return_value=value) as call, \
                patch('bridge.features.accounts.wsl_accounts.wsl.running_distributions', return_value=['FixtureUbuntu']):
            result = accounts.read('claude', self.row['id'])
        self.assertFalse(result['capabilities']['switch'])
        self.assertFalse(result['capabilities']['restore'])
        self.assertEqual(epochs, (dict(self.manager.read_epochs), dict(self.manager.read_changes),
                                 dict(self.manager.binding_epochs), dict(self.manager.binding_changes)))
        self.assertEqual(before, list(self.manager.db.iterdump()))
        self.assertEqual(call.call_args.args[-1], {'action': 'read'})

    def test_read_waits_for_claude_manager_without_holding_registry(self):
        from bridge.features.accounts.wsl_accounts import WslAccounts
        accounts = WslAccounts(self.bridge)
        started, done = threading.Event(), threading.Event()
        errors = []
        value = {'available': True, 'accounts': [], 'blockers': [],
                 'capabilities': {'save': True, 'switch': True, 'restore': True}}
        def reading():
            started.set()
            try:
                accounts.change({'provider': 'claude', 'entry': self.row['id'], 'action': 'read'})
            except Exception as error:
                errors.append(error)
            finally:
                done.set()
        with patch.object(accounts, '_call', return_value=value), \
                patch('bridge.features.accounts.wsl_accounts.wsl.running_distributions', return_value=['FixtureUbuntu']):
            with self.manager._changing('claude', rebind=False):
                thread = threading.Thread(target=reading, daemon=True)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(done.wait(.05))
                self.assertTrue(self.manager.claude_wsl.lock.acquire(timeout=1))
                self.manager.claude_wsl.lock.release()
                self.assertTrue(self.remote.lock.acquire(timeout=1))
                self.remote.lock.release()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_switch_waits_for_manager_before_holding_registry_or_remote_locks(self):
        from bridge.features.accounts.wsl_accounts import WslAccounts
        accounts = WslAccounts(self.bridge)
        started, done = threading.Event(), threading.Event()
        errors = []
        def switching():
            started.set()
            try:
                with accounts._change_scope('claude', self.row['id'], 'switch'):
                    pass
            except Exception as error:
                errors.append(error)
            finally:
                done.set()
        with self.manager._changing('claude', rebind=False):
            thread = threading.Thread(target=switching, daemon=True)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(done.wait(.05))
            # Normal writes may still need registration while holding manager.
            self.assertTrue(self.manager.claude_wsl.lock.acquire(timeout=1))
            self.manager.claude_wsl.lock.release()
            self.assertTrue(self.remote.lock.acquire(timeout=1))
            self.remote.lock.release()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
