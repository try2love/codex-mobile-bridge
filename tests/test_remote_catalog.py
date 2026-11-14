"""SSH catalog behavior with isolated target runtimes and controlled reads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from bridge.clients.codex.catalog import CatalogError
from bridge.clients.codex.remote import RemoteCatalog, RemoteUnavailable, payload

ROOT = Path(__file__).resolve().parents[1]


class RemoteCatalogRegressionTests(unittest.TestCase):
    def test_remote_account_change_keeps_structured_identity_error(self):
        from bridge.clients.codex.catalog import CatalogIdentityChanged
        reader = RemoteCatalog('fixture')
        self.addCleanup(reader.stop_model_refresh)
        source = "import json\nclass CatalogIdentityChanged(Exception):\n code='model_identity_changed'\nclass Reader:\n def get_kind(self,*args,**kwargs): raise CatalogIdentityChanged('account changed')\nreader=Reader()\n"
        def read(alias, script, timeout):
            process = subprocess.run([sys.executable, '-I', '-S', '-B', '-'], input=script, text=True, capture_output=True, check=True)
            return json.loads(process.stdout)
        with patch.object(reader, '_source', return_value=source), patch('bridge.clients.codex.remote.ssh_read', side_effect=read):
            with self.assertRaises(CatalogIdentityChanged) as error:
                reader.get_kind('models', '/fixture')
            self.assertEqual(error.exception.code, 'model_identity_changed')
            self.assertEqual(reader.kind_caches['models'], {})

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'POSIX SSH runtime fixture')
    def test_app_managed_runtime_without_path_install_reads_models_and_skills(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            home = Path(folder) / '.codex'
            executable = home / 'packages/app-server-daemon/current/bin/codex'
            executable.parent.mkdir(parents=True)
            executable.write_text('#!' + sys.executable + "\n" + r"""
import json, sys, os
from pathlib import Path
allowed = {'initialize', 'initialized', 'config/read', 'account/read', 'getAuthStatus', 'model/list', 'skills/list', 'configRequirements/read'}
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    assert method in allowed, method
    if method == 'getAuthStatus': assert message['params'] == {'includeToken': False, 'refreshToken': False}
    with (Path(os.environ['CODEX_HOME']) / 'fixture-methods').open('a') as log: log.write(method + '\n')
    if 'id' not in message:
        continue
    result = {'initialize': {'userAgent': 'fixture'}, 'config/read': {'config': {}},
              'account/read': {'account': {'type': 'chatgpt'}}, 'getAuthStatus': {'authMethod': 'chatgpt', 'authToken': None, 'requiresOpenaiAuth': True},
              'model/list': {'data': [{'model': 'fixture-model', 'displayName': 'Fixture'}]},
              'skills/list': {'data': [{'skills': [{'name': 'fixture-skill', 'path': '/fixture/SKILL.md'}]}]},
              'configRequirements/read': {}}[method]
    print(json.dumps({'id': message['id'], 'result': result}), flush=True)
""")
            executable.chmod(0o700)
            reader = RemoteCatalog('fixture')
            self.addCleanup(reader.stop_model_refresh)
            def read(alias, source, timeout):
                # Exercise the generated target helper, without SSH or user state.
                env = {**os.environ, 'HOME': folder, 'CODEX_HOME': str(home), 'PATH': folder}
                process = subprocess.run([sys.executable, '-I', '-S', '-B', '-'], input=source,
                                         text=True, capture_output=True, cwd=folder, env=env, timeout=10)
                if process.returncode:
                    raise RemoteUnavailable('SSH 读取失败，请检查电脑上该主机的 SSH 连接') from RuntimeError(process.stderr)
                return json.loads(process.stdout)
            with patch('bridge.clients.codex.remote.ssh_read', side_effect=read):
                models = reader.get_kind('models', folder)
                skills = reader.get_kind('skills', folder)
            self.assertEqual([m['id'] for m in models['models']], ['fixture-model'])
            self.assertEqual([s['name'] for s in skills['skills']], ['fixture-skill'])
            methods = (home / 'fixture-methods').read_text().splitlines()
            self.assertIn('getAuthStatus', methods)
            self.assertNotIn('account/read', methods, 'Catalog does not need workspace routing discovery')
            self.assertTrue(models['fastMode']['allowed'])

    def test_slow_model_read_does_not_block_skills(self):
        reader = RemoteCatalog('fixture')
        started, release, ready = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def read(alias, source, timeout):
            if 'reader.get_kind(' + payload('models') in source:
                started.set(); release.wait(3)
                return {'models': []}
            return {'skills': [], 'cache': {'state': 'fresh'}}
        def request(kind):
            try:
                reader.get_kind(kind, '/fixture')
                if kind == 'skills': ready.set()
            except Exception as exc:
                errors.append(exc)
        with patch('bridge.clients.codex.remote.ssh_read', side_effect=read):
            model = threading.Thread(target=request, args=('models',)); model.start()
            self.assertTrue(started.wait(1))
            skill = threading.Thread(target=request, args=('skills',)); skill.start()
            independent = ready.wait(.5)
            release.set(); model.join(2); skill.join(2)
        reader.stop_model_refresh()
        self.assertFalse(errors)
        self.assertTrue(independent, 'Skill lookup waited for the unrelated model request')

    def test_stale_skills_return_before_refresh_completes(self):
        reader = RemoteCatalog('fixture')
        refreshing, release, returned = threading.Event(), threading.Event(), threading.Event()
        answers, errors, refreshes = [], [], []
        def read(alias, source, timeout):
            if 'refresh=' + payload(True) in source.rsplit('\nprint(', 1)[-1]:
                refreshes.append(source); refreshing.set(); release.wait(3)
                return {'skills': [{'name': 'fresh'}], 'cache': {'state': 'fresh'}}
            return {'skills': [{'name': 'cached'}], 'cache': {'state': 'stale'}}
        def request():
            try:
                answers.append(reader.get_kind('skills', '/fixture'))
                returned.set()
            except Exception as exc: errors.append(exc)
        with patch('bridge.clients.codex.remote.ssh_read', side_effect=read):
            first = threading.Thread(target=request); first.start()
            self.assertTrue(refreshing.wait(1))
            immediate = returned.wait(.5)
            returned.clear()
            second = threading.Thread(target=request); second.start()
            cached_during_refresh = returned.wait(.5)
            release.set(); first.join(2); second.join(2)
            # The refreshing worker must settle before restoring the SSH adapter.
            for worker in threading.enumerate():
                if worker.name == 'ssh-skill-refresh': worker.join(2)
        reader.stop_model_refresh()
        self.assertFalse(errors)
        self.assertTrue(immediate, 'Cached Skill list waited for the forced refresh')
        self.assertTrue(cached_during_refresh, 'Cached Skills remained blocked by background refresh')
        self.assertEqual([answer['skills'] for answer in answers], [[{'name': 'cached'}]] * 2)
        self.assertEqual(reader.get_kind('skills', '/fixture')['skills'], [{'name': 'fresh'}])
        self.assertEqual(len(refreshes), 1)

    def test_failed_refresh_keeps_cached_skills_and_stop_prevents_new_refresh(self):
        reader = RemoteCatalog('fixture')
        calls = []
        def read(alias, source, timeout):
            calls.append(source)
            if 'refresh=' + payload(True) in source.rsplit('\nprint(', 1)[-1]:
                raise RemoteUnavailable('offline fixture')
            return {'skills': [{'name': 'cached'}], 'cache': {'state': 'stale'}}
        with patch('bridge.clients.codex.remote.ssh_read', side_effect=read):
            first = reader.get_kind('skills', '/fixture')
            for worker in threading.enumerate():
                if worker.name == 'ssh-skill-refresh': worker.join(2)
            self.assertEqual(reader.get_kind('skills', '/fixture'), first)
            self.assertEqual(len(calls), 2)
            reader.stop_model_refresh()
            reader.kind_caches['skills'].clear()
            reader.get_kind('skills', '/fixture')
            self.assertEqual(len(calls), 3)
            self.assertFalse(reader.skill_refreshing)

    def test_ssh_runtime_retains_legacy_and_path_fallbacks(self):
        from bridge.clients.codex.catalog import Catalog
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            home = Path(folder) / '.codex'
            state = home / 'app-server-daemon'; state.mkdir(parents=True)
            paths = [home / 'packages/standalone/current/codex',
                     Path(folder) / '.local/bin/codex', Path(folder) / 'path-bin/codex']
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture'); path.chmod(0o700)
            with patch('pathlib.Path.home', return_value=Path(folder)), patch('shutil.which', return_value=None) as which:
                self.assertEqual(Catalog.find_ssh_runtime(home), str(paths[1]))
                (state / 'app-server.pid').write_text('synthetic')
                self.assertEqual(Catalog.find_ssh_runtime(home), str(paths[0]))
                (state / 'daemon.pid').write_text('synthetic')
                self.assertEqual(Catalog.find_ssh_runtime(home), str(paths[1]), 'Never select stale standalone when dedicated daemon is present')
                which.return_value = str(paths[2])
                self.assertEqual(Catalog.find_ssh_runtime(home), str(paths[2]))

    def test_ssh_api_key_mode_keeps_upstream_identity_and_rejects_unknown_auth(self):
        from bridge.clients.codex.catalog import Catalog
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            reader = Catalog(folder, 'fixture', allow_background_refresh=False, auth_status_only=True)
            (Path(folder) / 'auth.json').write_text(json.dumps({'OPENAI_API_KEY': 'fixture-key'}))
            def request(method, params):
                if method == 'config/read': return {'config': {'openai_base_url': 'https://fixture.invalid/v1'}}
                if method == 'account/read':
                    return Catalog.account_from_auth_status({'authMethod': 'apikey', 'authToken': None, 'requiresOpenaiAuth': True})
                raise AssertionError('Unexpected identity or native fallback request: ' + method)
            with patch('bridge.clients.codex.catalog.model_catalog', return_value=[{'id': 'api-model', 'name': 'API', 'efforts': ['high']}]) as upstream:
                value = reader.read_models(request, folder, 'openai')
                upstream.assert_called_once_with('https://fixture.invalid/v1', 'fixture-key')
                self.assertEqual(value['modelSource'], 'api')
                self.assertEqual(value['models'][0]['model'], 'api-model')
            with patch('bridge.clients.codex.catalog.model_catalog', side_effect=ValueError('upstream unavailable')):
                value = reader.read_models(request, folder, 'openai')
                self.assertEqual(value['models'], [])
                self.assertIn('upstream unavailable', value['modelError'])
            for response in ({}, {'authMethod': 'unknown'}):
                with self.assertRaises(CatalogError): Catalog.account_from_auth_status(response)
