"""Synthetic target-side checks for the SSH native CLI model helper."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.clients.codex.remote import RemoteCliModels, remote_model_catalog, remote_model_identity
from bridge.resources import source_text


ROOT = Path(__file__).resolve().parents[1]


class RemoteCliModelsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name) / 'codex-home'
        self.cwd = Path(self.folder.name) / 'project'
        self.cwd.mkdir()
        executable = self.home / 'packages/app-server-daemon/current/bin/codex'
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o700)
        (self.home / 'auth.json').write_text('{"fixture":"private-auth"}')
        self.config = {'model_provider': 'fixture', 'model_providers': {
            'fixture': {'base_url': 'https://fixture.invalid/v1', 'env_key': 'FIXTURE_API_KEY'}}}
        self.value = {'home': str(self.home), 'cwd': str(self.cwd), 'provider': 'fixture',
                      'config': self.config, 'account': {'type': 'apiKey'},
                      'nativeModels': [{'model': 'route/model-a', 'displayName': 'Native A',
                                        'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]}

    def worker(self, operation, daemon_source, catalog_source, **changes):
        source = '__file__ = ' + repr(str(Path(self.folder.name) / 'payload.py')) + '\n'
        source += source_text('features/auth/tls.py') + '\n'
        source += source_text('features/accounts/models.py').replace(
            'from bridge.features.auth.tls import client_context', '') + '\n'
        source += source_text('clients/codex/configuration.py') + '\n'
        source += source_text('clients/codex/catalog.py').replace(
            'from bridge.features.accounts.models import model_catalog', '').replace(
            'from bridge.clients.codex.configuration import magpie_managed', '') + '\n'
        source += source_text('clients/codex/remote_models.py') + '\n'
        source += daemon_source + '\n' + catalog_source + '\n'
        source += 'print(json.dumps(remote_model_worker(' + repr({**self.value, **changes, 'operation': operation}) + ')))\n'
        script = Path(self.folder.name) / 'payload.py'
        script.write_text(source, encoding='utf-8')
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script)], cwd=self.folder.name,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout), result.stdout

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'SSH CLI model helper requires a Linux or macOS target')
    def test_identity_only_does_not_query_upstream_or_return_daemon_secrets(self):
        daemon = "def read_daemon_environment(home, executable, names):\n assert names == {'FIXTURE_API_KEY'}\n return ({'FIXTURE_API_KEY':'target-secret'}, (7, 42, executable))"
        catalog = "def model_catalog(*args, **kwargs): raise AssertionError('upstream request during identity read')"
        result, output = self.worker('identity', daemon, catalog)
        self.assertEqual(len(result['identity']), 64)
        self.assertNotIn('target-secret', output)
        self.assertNotIn('private-auth', output)

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'SSH CLI model helper requires a Linux or macOS target')
    def test_upstream_models_keep_exact_ids_and_native_effort_metadata(self):
        daemon = "def read_daemon_environment(home, executable, names): return ({'FIXTURE_API_KEY':'target-secret'}, (7, 42, executable))"
        catalog = "def model_catalog(base, key, **options):\n assert key == 'target-secret'\n return [{'id':'route/model-a','name':'Upstream A','efforts':[]}]"
        result, output = self.worker('models', daemon, catalog)
        self.assertEqual(result['modelSource'], 'api')
        self.assertEqual([row['id'] for row in result['models']], ['route/model-a'])
        self.assertEqual(result['models'][0]['efforts'], ['high'])
        self.assertNotIn('target-secret', output)

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'SSH CLI model helper requires a Linux or macOS target')
    def test_process_change_discards_models_and_unreadable_daemon_fails_closed(self):
        daemon = "count=0\ndef read_daemon_environment(home, executable, names):\n global count\n count+=1\n return ({'FIXTURE_API_KEY':'target-secret'}, (7, count, executable))"
        catalog = "def model_catalog(*args, **kwargs): return [{'id':'route/model-a','name':'A','efforts':[]}]"
        result, _ = self.worker('models', daemon, catalog)
        self.assertEqual(result, {'error': 'SSH 原生 CLI 守护进程已变化，请重新连接后刷新模型'})
        result, _ = self.worker('models', "def read_daemon_environment(*args): raise OSError('private environment')", catalog)
        self.assertEqual(result, {'error': '无法核验 SSH 原生 CLI 守护进程的启动环境，请重新连接后刷新模型'})

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'SSH CLI model helper requires a Linux or macOS target')
    def test_environment_selection_respects_auth_file_and_static_credentials(self):
        no_environment = "def read_daemon_environment(*args): raise AssertionError('unneeded environment read')"
        catalog = "def model_catalog(*args, **kwargs): raise AssertionError('identity read queried upstream')"
        result, _ = self.worker('identity', no_environment, catalog,
                                config={'model_provider': 'fixture', 'model_providers': {
                                    'fixture': {'base_url': 'https://fixture.invalid/v1',
                                                'experimental_bearer_token': 'static'}}})
        self.assertEqual(len(result['identity']), 64)
        (self.home / 'auth.json').write_text('{"OPENAI_API_KEY":"saved-key"}')
        result, output = self.worker('identity', no_environment, catalog, provider='openai',
                                     config={'model_provider': 'openai', 'openai_base_url': 'https://fixture.invalid/v1'})
        self.assertEqual(len(result['identity']), 64)
        self.assertNotIn('saved-key', output)
        (self.home / 'auth.json').write_text('{}')
        daemon = "def read_daemon_environment(home, executable, names):\n assert names == {'OPENAI_API_KEY', 'OPENAI_BASE_URL'}\n return ({'OPENAI_API_KEY':'target-key', 'OPENAI_BASE_URL':'https://target.invalid/v1'}, (7,42,executable))"
        result, output = self.worker('identity', daemon, catalog, provider='openai',
                                     config={'model_provider': 'openai'})
        self.assertEqual(len(result['identity']), 64)
        self.assertNotIn('target-key', output)

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'SSH CLI model helper requires a Linux or macOS target')
    def test_native_catalog_cannot_replace_upstream_api_models(self):
        daemon = "def read_daemon_environment(home, executable, names): return ({}, (7, 42, executable))"
        catalog = "def model_catalog(*args, **kwargs): raise AssertionError('native catalog should not query upstream')"
        result, _ = self.worker('models', daemon, catalog, provider='openai',
                                config={'model_provider': 'openai', 'model_catalog_json': '/native/models.json'})
        self.assertEqual(result, {'error': '当前原生 CLI 接入不是 API 模型目录，请手动填写模型 ID'})

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'SSH CLI model helper requires a Linux or macOS target')
    def test_openai_custom_query_auth_reads_target_key_and_base_without_account(self):
        daemon = "def read_daemon_environment(home, executable, names):\n assert names == {'OPENAI_API_KEY', 'OPENAI_BASE_URL'}\n return ({'OPENAI_API_KEY':'target-key', 'OPENAI_BASE_URL':'https://target.invalid/v1'}, (7,42,executable))"
        catalog = "def model_catalog(base, key, **options):\n assert base == 'https://target.invalid/v1' and key == 'target-key'\n return [{'id':'target/model', 'name':'Target', 'efforts':['low','high']}]"
        result, output = self.worker('models', daemon, catalog, provider='openai', account={},
                                     config={'model_providers': {'openai': {'query_params': {'version': 'fixture'}}}})
        self.assertEqual(result['models'][0]['efforts'], ['low', 'high'])
        self.assertNotIn('target-key', output)

    def test_non_posix_ssh_target_is_explicitly_unsupported(self):
        daemon = "sys.platform = 'win32'\ndef read_daemon_environment(*args): raise AssertionError('unsupported target read')"
        catalog = "def model_catalog(*args, **kwargs): raise AssertionError('unsupported target HTTP')"
        result, _ = self.worker('identity', daemon, catalog)
        self.assertEqual(result, {'error': 'SSH 原生 CLI 模型查询暂不支持此系统'})

    def test_wrapper_sends_encoded_snapshot_and_exposes_only_public_result(self):
        identity = 'a' * 64
        with patch.object(RemoteCliModels, '_source', return_value='import json\n'), \
             patch('bridge.clients.codex.remote.ssh_read', return_value={'identity': identity}) as read:
            self.assertEqual(remote_model_identity('fixture-host', self.home, self.cwd,
                                                   'fixture', self.config, {'type': 'apiKey'}), identity)
        source = read.call_args.args[1]
        self.assertNotIn('fixture.invalid', source)
        self.assertIn('remote_model_worker(', source)
        self.assertEqual(read.call_args.kwargs['timeout'], 20)
        with patch.object(RemoteCliModels, '_source', return_value='import json\n'), \
             patch('bridge.clients.codex.remote.ssh_read', return_value={
                 'identity': identity, 'models': [{'id': 'route/model-a'}], 'modelSource': 'api'}) as read:
            result = remote_model_catalog('fixture-host', str(self.home), str(self.cwd),
                                          'fixture', self.config, {'type': 'apiKey'})
        self.assertEqual(result['models'][0]['id'], 'route/model-a')
        self.assertEqual(read.call_args.kwargs['timeout'], 120)


if __name__ == '__main__':
    unittest.main()
