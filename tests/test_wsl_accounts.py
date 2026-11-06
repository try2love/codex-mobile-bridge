"""Target-only account fixtures; no native CLI/network/account operations."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bridge.features.accounts import wsl_account_worker as worker


def install_target_toml(testcase):
    """Direct worker fixtures use the same bundled parser as target payloads."""
    if sys.version_info >= (3, 11):
        return
    from bridge.features.accounts.wsl_account_payload import worker_source
    context = {'__file__': worker.__file__}
    with patch.dict(sys.modules, {'tomllib': None, 'tomli': None}):
        exec(worker_source(), context)
    parser = patch.dict(worker.__dict__, {'_WSL_TOML_LOADS': context['_WSL_TOML_LOADS']})
    parser.start()
    testcase.addCleanup(parser.stop)


@unittest.skipUnless(os.name == 'posix', 'Target worker requires POSIX permissions/UID; native WSL runtime is verified separately')
class WslAccountWorkerTests(unittest.TestCase):
    def setUp(self):
        install_target_toml(self)
        (Path(__file__).resolve().parents[1]/'.tmp').mkdir(exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix='wsl-account-', dir=Path(__file__).resolve().parents[1]/'.tmp'))
        self.home = self.temp/'profile'
        self.home.mkdir()
        (self.home/'settings.json').write_text('{"theme":"dark","env":{"OTHER":"keep"}}')
        (self.home/'.credentials.json').write_text('{"oauth":"original-private"}')
        self.before = {p.name: p.read_bytes() for p in self.home.iterdir()}
        self.store = worker.AccountStore('claude', str(self.home), guard=lambda *_: [])

    def tearDown(self):
        shutil.rmtree(self.temp)

    def save(self, **changes):
        value = {'action': 'save-api', 'name': 'fixture', 'baseUrl': 'https://api.invalid/v1',
                 'apiKey': 'fixture-private-key', 'model': 'fixture-model', **changes}
        self.store.change(value)
        return self.store.store['accounts'][-1]['id']

    def test_reusing_a_saved_key_requires_the_same_endpoint_and_scheme(self):
        identifier = self.save()
        for fields in ({'baseUrl':'https://different.invalid'}, {'authScheme':'bearer'}):
            with self.subTest(fields=fields),self.assertRaises(worker.AccountError):
                self.save(id=identifier, apiKey='', **fields)
        self.save(id=identifier, apiKey='', name='Renewed')
        self.assertEqual(self.store.row(identifier)['key'], 'fixture-private-key')

    def switch(self, identifier):
        return self.store.change({'action': 'switch', 'id': identifier,
                                  'expectedIdentity': self.store.public()['identity']})

    def test_claude_api_preserves_history_settings_and_restores_exact_native_bytes(self):
        identifier = self.save()
        result = self.switch(identifier)
        self.assertEqual(result['activeId'], identifier)
        self.assertFalse(result['activeVerified'])
        settings = json.loads((self.home/'settings.json').read_bytes())
        self.assertEqual(settings['theme'], 'dark')
        self.assertEqual(settings['env']['OTHER'], 'keep')
        self.assertEqual(settings['env']['ANTHROPIC_API_KEY'], 'fixture-private-key')
        self.assertEqual((self.home/'.credentials.json').read_bytes(), self.before['.credentials.json'])
        self.assertNotIn('fixture-private-key', json.dumps(result))
        self.switch('native')
        for name, data in self.before.items():
            self.assertEqual((self.home/name).read_bytes(), data)

    def test_enrollment_does_not_mutate_native_or_spawn(self):
        with patch('subprocess.run', side_effect=AssertionError('no process')):
            self.save()
            self.store.change({'action': 'import-current'})
        for name, data in self.before.items():
            self.assertEqual((self.home/name).read_bytes(), data)
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.public()['accounts'][0]['id'], 'native')

    def test_first_switch_exact_rollback_allows_later_manual_retry(self):
        identifier = self.save()
        write = worker._write
        failed = [False]
        def fail_second_file(path, value):
            if path.name == '.credentials.json' and not failed[0]:
                failed[0] = True
                raise OSError('fixture write failure')
            return write(path, value)
        with patch.object(worker, '_write', side_effect=fail_second_file), self.assertRaises(worker.AccountError):
            self.switch(identifier)
        restored = worker.AccountStore('claude', str(self.home), guard=lambda *_: [])
        self.assertEqual(restored.files(), self.before)
        self.assertEqual(restored.public()['activeId'], 'native')
        # No implicit retry; a fresh explicit request using current identity succeeds.
        restored.change({'action': 'switch', 'id': identifier,
                         'expectedIdentity': restored.public()['identity']})
        self.assertEqual(restored.public()['activeId'], identifier)

    def test_first_switch_external_writer_during_failure_still_blocks_manual_retry(self):
        identifier = self.save()
        write = worker._write
        def fail_with_external_change(path, value):
            if path.name == '.credentials.json':
                (self.home/'settings.json').write_text('{"external":"retained"}')
                raise OSError('fixture write failure')
            return write(path, value)
        with patch.object(worker, '_write', side_effect=fail_with_external_change), self.assertRaises(worker.AccountError):
            self.switch(identifier)
        restored = worker.AccountStore('claude', str(self.home), guard=lambda *_: [])
        with self.assertRaisesRegex(worker.AccountError, '外部修改'):
            restored.change({'action': 'switch', 'id': identifier,
                             'expectedIdentity': restored.public()['identity']})
        self.assertEqual((self.home/'settings.json').read_text(), '{"external":"retained"}')
        self.assertEqual((self.home/'.credentials.json').read_bytes(), self.before['.credentials.json'])

    def test_external_edit_after_native_capture_blocks_switch(self):
        identifier = self.save()
        self.store.change({'action': 'import-current'})
        (self.home/'settings.json').write_text('{"theme":"external"}')
        with self.assertRaises(worker.AccountError):
            self.switch(identifier)
        self.assertEqual(json.loads((self.home/'settings.json').read_bytes())['theme'], 'external')

    def test_edit_active_profile_rejects_without_changing_credentials_identity_or_snapshot(self):
        identifier = self.save()
        self.switch(identifier)
        files = self.store.files()
        saved = self.store.path.read_bytes()
        for changes in ({'apiKey': 'new-key'}, {'baseUrl': 'https://other.invalid'}, {'model': 'other-model'}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(worker.AccountError, '先切换到其他接入'):
                    self.save(id=identifier, **changes)
                self.assertEqual(self.store.files(), files)
                self.assertEqual(self.store.path.read_bytes(), saved)
                self.assertEqual(self.store.public()['activeId'], identifier)
        self.store.change({'action': 'rename', 'id': identifier, 'name': 'New display name'})
        self.assertEqual(self.store.public()['activeId'], identifier)
        self.switch('native')
        native = self.store.files()
        self.save(id=identifier, apiKey='new-key')
        self.assertEqual(self.store.files(), native)
        self.switch(identifier)
        self.assertEqual(json.loads((self.home/'settings.json').read_bytes())['env']['ANTHROPIC_API_KEY'], 'new-key')

    def test_native_snapshot_cannot_be_deleted_or_renamed(self):
        identifier = self.save()
        self.switch(identifier)
        original = self.store.path.read_bytes()
        files = self.store.files()
        for operation in ({'action': 'delete', 'id': 'native'},
                          {'action': 'rename', 'id': 'native', 'name': 'Changed native'}):
            with self.subTest(action=operation['action']), self.assertRaises(worker.AccountError):
                self.store.change(operation)
            self.assertEqual(self.store.path.read_bytes(), original)
            self.assertEqual(self.store.files(), files)

    def test_helper_managed_profile_blocks_switch_but_can_save_api(self):
        (self.home/'settings.json').write_text('{"apiKeyHelper":"external-helper"}')
        identifier = self.save()
        self.assertTrue(self.store.public()['blockers'])
        with self.assertRaises(worker.AccountError):
            self.switch(identifier)

    def test_symlink_native_file_and_world_readable_private_store_rejected(self):
        identifier = self.save()
        self.store.root.chmod(0o755)
        with self.assertRaises(worker.AccountError):
            worker.AccountStore('claude', str(self.home))
        self.store.root.chmod(0o700)
        (self.home/'settings.json').unlink()
        (self.home/'settings.json').symlink_to(self.home/'.credentials.json')
        with self.assertRaises(worker.AccountError):
            self.store.public()

    def test_invalid_secret_url_and_stale_switch_fail_before_native_change(self):
        for url in ('http://api.invalid', 'https://key:secret@api.invalid', 'https://api.invalid?q=secret'):
            with self.assertRaises(worker.AccountError):
                self.save(baseUrl=url)
        identifier = self.save()
        with self.assertRaises(worker.AccountError):
            self.store.change({'action': 'switch', 'id': identifier, 'expectedIdentity': 'stale'})
        self.assertNotIn('native', self.store.store)

    def test_worker_runs_without_bridge_package_in_isolated_target_interpreter(self):
        self.save()
        source = Path(worker.__file__).read_text()
        source += '\nstore=AccountStore("claude", '+repr(str(self.home))+', guard=lambda *_: [])\nprint(json.dumps(store.public()))\n'
        result = subprocess.run([sys.executable, '-I', '-'], input=source, text=True,
                                capture_output=True, cwd=self.temp, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['available'])
        self.assertNotIn('fixture-private-key', result.stdout)

    def test_bundled_parser_runs_isolated_without_installed_toml_and_preserves_native_policy(self):
        from bridge.features.accounts.wsl_account_payload import worker_source
        directory = self.temp/'codex'
        directory.mkdir()
        native = ('model = "original"\nforced_login_method = "chatgpt"\nservice_tier = "fast"\n'
                  '# [preserved bracket comment]\n[history]\npersistence = "save-all"\n'
                  '[model_providers.openai]\nname = "Original OpenAI"\nrequires_openai_auth = true\n')
        (directory/'config.toml').write_text(native)
        prefix = 'import sys\nsys.modules["tomllib"]=None\nsys.modules["tomli"]=None\n'
        source = prefix + worker_source()
        source += '\nstore=AccountStore("codex", '+repr(str(directory))+', guard=lambda *_: [])\n'
        source += 'store.change({"action":"save-api","name":"fixture","baseUrl":"https://fixture.invalid","model":"fixture-model","apiKey":"private-fixture"})\n'
        source += 'identifier=store.store["accounts"][0]["id"]\nstore.change({"action":"switch","id":identifier,"expectedIdentity":store.public()["identity"]})\n'
        source += 'config=_toml_loads((store.directory/"config.toml").read_text())\nprint(json.dumps({"forced":config["forced_login_method"],"tier":config.get("service_tier"),"history":config["history"],"oldProvider":config["model_providers"]["openai"]}))\n'
        interpreters = [sys.executable]
        old_python = shutil.which('python3')
        if old_python and old_python != sys.executable:
            interpreters.append(old_python)
        for interpreter in interpreters:
            with self.subTest(interpreter=interpreter):
                # Restore fixture between interpreter runs, including private store.
                (directory/'config.toml').write_text(native)
                (directory/'auth.json').unlink(missing_ok=True)
                shutil.rmtree(directory/'.codex-mobile-bridge-accounts', ignore_errors=True)
                result = subprocess.run([interpreter, '-I', '-S', '-'], input=source, text=True,
                                        capture_output=True, cwd=self.temp, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('private-fixture', result.stdout)
                value = json.loads(result.stdout)
                self.assertEqual(value['forced'], 'api')
                self.assertIsNone(value['tier'])
                self.assertEqual(value['history']['persistence'], 'save-all')
                self.assertEqual(value['oldProvider'], {'name': 'Original OpenAI', 'requires_openai_auth': True,
                                 'base_url': 'https://fixture.invalid', 'wire_api': 'responses', 'supports_websockets': False})

    def test_complex_codex_routing_or_other_history_auth_remains_readonly(self):
        directory = self.temp/'codex-routing'
        directory.mkdir()
        configs = ('model_provider="custom"\n[model_providers.custom]\nrequires_openai_auth=true\n[model_providers.openai]\nbase_url="https://old.invalid"\n',
                   'model_provider = "custom"\n[model_providers.custom]\nbase_url="https://old.invalid"\nenv_key="CUSTOM_API_KEY"\n',
                   '[model_providers.openai]\nhttp_headers={"X-Unknown-Authorization"="Bearer old"}\n',
                   '[model_providers.old_history]\nrequires_openai_auth=true\nbase_url="https://old.invalid"\n',
                   'model_provider="custom"\n[model_providers.custom]\nwire_api="chat"\n',
                   'profile="work"\n[profiles.work]\nmodel="example"\n',
                   'cli_auth_credentials_store="keyring"\n')
        for text in configs:
            with self.subTest(config=text):
                (directory/'config.toml').write_text(text)
                store = worker.AccountStore('codex', str(directory), guard=lambda *_: [])
                store.change({'action': 'save-api', 'name': 'new', 'baseUrl': 'https://new.invalid',
                              'model': 'fixture', 'apiKey': 'new-private'})
                self.assertFalse(store.public()['capabilities']['switch'])
                with self.assertRaises(worker.AccountError):
                    store.change({'action': 'switch', 'id': store.store['accounts'][-1]['id'],
                                  'expectedIdentity': store.public()['identity']})
                self.assertEqual((directory/'config.toml').read_text(), text)
                self.assertFalse((directory/'auth.json').exists())

    def test_api_url_matches_native_policy_and_claude_model_path_normalizes_v1(self):
        identifier = self.save(baseUrl='https://api.anthropic.com')
        row = self.store.row(identifier)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _): return b'{"data":[{"id":"claude-fixture","display_name":"Claude Fixture","supported_reasoning_levels":["low","high"]}]}'
        opener = unittest.mock.Mock()
        opener.open.return_value = Response()
        from bridge.features.accounts.models import model_catalog
        with patch('bridge.features.accounts.models.build_opener', return_value=opener), \
                patch.object(worker, 'model_catalog', model_catalog, create=True):
            value = worker.fetch_models(self.store, {'id': identifier, 'modelIdentity': worker._account_identity(row)})
        self.assertEqual(value['models'][0]['id'], 'claude-fixture')
        self.assertEqual(value['models'][0]['name'], 'Claude Fixture')
        self.assertEqual(value['models'][0]['efforts'], ['low', 'high'])
        self.assertEqual(opener.open.call_args.args[0].full_url, 'https://api.anthropic.com/v1/models')
        for url in ('http://localhost:8000/v1', 'http://127.0.0.1:8000', 'http://[::1]:8000'):
            self.save(baseUrl=url)
        for url in ('http://remote.invalid', 'https://api.invalid:bad', 'https://api.invalid/a b'):
            with self.assertRaises(worker.AccountError):
                self.save(baseUrl=url)

    def test_cosmetic_rename_retains_account_and_model_identity(self):
        identifier = self.save()
        first = self.switch(identifier)
        digest = next(row['modelCatalogIdentity'] for row in first['accounts'] if row['id'] == identifier)
        after = self.store.change({'action': 'rename', 'id': identifier, 'name': 'New display label'})
        self.assertEqual(after['activeId'], identifier)
        self.assertEqual(next(row['modelCatalogIdentity'] for row in after['accounts'] if row['id'] == identifier), digest)

    def test_generic_execute_wire_error_does_not_return_credentials(self):
        identifier = self.save()
        with patch.object(worker, 'runtime_blockers', return_value=[]):
            value = worker.execute({'provider': 'claude', 'directory': str(self.home), 'action': 'read'})
        self.assertNotIn('fixture-private-key', json.dumps(value))


class WslAccountModelCacheTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        import threading
        from bridge.features.accounts.wsl_accounts import WslAccounts
        from bridge.features.endpoints.registry import entry_id
        (Path(__file__).resolve().parents[1]/'.tmp').mkdir(exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix='wsl-models-', dir=Path(__file__).resolve().parents[1]/'.tmp'))
        self.row = {'distribution': 'Fixture', 'user': 'fixture', 'home': '/home/fixture/.codex', 'label': 'fixture'}
        self.row['id'] = entry_id('Fixture', 'fixture', self.row['home'])
        registry = SimpleNamespace(entries=[self.row], lock=threading.RLock(), directory=self.temp, bridges={})
        self.accounts = WslAccounts(SimpleNamespace(data_dir=self.temp, entries=registry))
        self.request = {'provider': 'codex', 'entry': self.row['id'], 'action': 'models', 'id': 'saved'}
        self.digest = 'fixture-key-digest'
        self.running = patch('bridge.features.accounts.wsl_account_models.wsl.running_distributions', return_value=['Fixture'])
        self.running.start()
        self.call = patch.object(self.accounts, '_call', side_effect=self.target).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(lambda: shutil.rmtree(self.temp))
        self.addCleanup(self.accounts.close)

    def target(self, provider, row, request):
        if request['action'] == 'read':
            return {'accounts': [{'id': 'saved', 'modelCatalogIdentity': self.digest}]}
        return {'models': [{'id': 'new-model', 'name': 'New Model', 'efforts': ['high']}]}

    def cache(self, age=0):
        import time
        from bridge.clients.codex.catalog import ModelStore
        identity = ModelStore.key('wsl-saved-api', 'codex', self.row['id'], self.digest)
        self.accounts.models_store.write(identity, {'models': [{'id': 'cached-model'}]})
        with closing(self.accounts.models_store._connect()) as db, db:
            db.execute('UPDATE catalogs SET updated_at=? WHERE identity=?', (time.time()-age, identity))
        return identity

    def test_cache_fixture_closes_sqlite_connection(self):
        import sqlite3
        connect = self.accounts.models_store._connect
        connections = []
        def tracked_connect():
            db = connect()
            connections.append(db)
            self.addCleanup(db.close)
            return db
        with patch.object(self.accounts.models_store, '_connect', side_effect=tracked_connect):
            self.cache()
        for db in connections:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed'):
                db.execute('SELECT 1')

    def test_close_during_cache_read_cannot_publish_cached_result(self):
        self.cache()
        read = self.accounts.models_store.read
        def closing_read(identity):
            record = read(identity)
            self.accounts.close()
            return record
        with patch.object(self.accounts.models_store, 'read', side_effect=closing_read), \
                patch('bridge.features.accounts.wsl_account_models.threading.Timer') as timer:
            with self.assertRaisesRegex(ValueError, '停止'):
                self.accounts.change(self.request)
            timer.assert_not_called()

    def test_stale_cache_returns_immediately_and_schedules_refresh(self):
        from bridge.clients.codex.catalog import MODEL_REFRESH_SECONDS
        self.cache(MODEL_REFRESH_SECONDS+60)
        with patch('bridge.features.accounts.wsl_account_models.threading.Timer') as timer:
            result = self.accounts.change(self.request)
        self.assertEqual(result['models'], [{'id': 'cached-model'}])
        self.assertEqual(result['cache']['state'], 'stale')
        self.assertEqual([call.args[2]['action'] for call in self.call.call_args_list], ['read'])
        self.assertEqual(timer.call_args.args[0], 0)
        timer.return_value.start.assert_called_once()

    def test_fresh_cache_timer_is_twelve_hour_bound_and_close_cancels_it(self):
        from bridge.clients.codex.catalog import MODEL_REFRESH_SECONDS
        self.cache()
        with patch('bridge.features.accounts.wsl_account_models.threading.Timer') as timer:
            result = self.accounts.change(self.request)
            self.assertEqual(result['cache']['state'], 'fresh')
            self.assertAlmostEqual(timer.call_args.args[0], MODEL_REFRESH_SECONDS, delta=2)
            callback = timer.call_args.args[1]
            self.accounts.close()
            timer.return_value.cancel.assert_called_once()
            before = self.call.call_count
            callback()
            self.assertEqual(self.call.call_count, before)

    def test_timer_does_not_query_models_for_stopped_or_changed_target(self):
        self.cache()
        with patch('bridge.features.accounts.wsl_account_models.threading.Timer') as timer:
            self.accounts.change(self.request)
            callback = timer.call_args.args[1]
            self.digest = 'changed-credential'
            callback()
            self.assertEqual([call.args[2]['action'] for call in self.call.call_args_list], ['read', 'read'])
            with patch('bridge.features.accounts.wsl_account_models.wsl.running_distributions', return_value=[]):
                callback()
            self.assertEqual(self.call.call_count, 2)

    def test_close_discards_inflight_network_result_without_cache_publication(self):
        import threading
        from bridge.clients.codex.catalog import ModelStore
        entered, release = threading.Event(), threading.Event()
        errors = []
        def target(provider, row, request):
            if request['action'] == 'models':
                entered.set()
                self.assertTrue(release.wait(5))
            return self.target(provider, row, request)
        self.call.side_effect = target
        def refresh():
            try:
                self.accounts.change({**self.request, 'refresh': True})
            except ValueError as error:
                errors.append(error)
        thread = threading.Thread(target=refresh)
        thread.start()
        self.assertTrue(entered.wait(3))
        self.accounts.close()
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        identity = ModelStore.key('wsl-saved-api', 'codex', self.row['id'], self.digest)
        self.assertIsNone(self.accounts.models_store.read(identity))

    def test_concurrent_refresh_is_singleflight_and_registry_controls_remain_available(self):
        import threading
        from concurrent.futures import Future
        entered, release = threading.Event(), threading.Event()
        joined = threading.Event()
        class ObservedFuture(Future):
            def result(self, timeout=None):
                joined.set()
                return super().result(timeout)
        future_patch = patch('bridge.features.accounts.wsl_account_models.Future', ObservedFuture)
        future_patch.start()
        self.addCleanup(future_patch.stop)
        calls = []
        def target(provider, row, request):
            if request['action'] == 'models':
                calls.append(request)
                entered.set()
                self.assertTrue(release.wait(5))
            return self.target(provider, row, request)
        self.call.side_effect = target
        results = []
        def refresh():
            results.append(self.accounts.change({**self.request, 'refresh': True}))
        first = threading.Thread(target=refresh)
        second = threading.Thread(target=refresh)
        first.start()
        self.assertTrue(entered.wait(3))
        second.start()
        registry = self.accounts._registry('codex')
        self.assertTrue(registry.lock.acquire(timeout=1))
        registry.lock.release()
        self.assertTrue(self.accounts.lock.acquire(timeout=1))
        self.accounts.lock.release()
        # Snapshot reads precede joining the Future; counting them does not
        # prove that the second caller is waiting on the in-flight request.
        try:
            self.assertTrue(joined.wait(3))
        finally:
            release.set()
        first.join(5); second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['models'], results[1]['models'])



if __name__ == '__main__':
    unittest.main()
