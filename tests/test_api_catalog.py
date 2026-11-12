import hashlib
import os
import json
import io
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.clients.codex.catalog import Catalog, CatalogError
from bridge.features.accounts.models import model_ids

ROOT = Path(__file__).resolve().parents[1]


class ModelResponseTests(unittest.TestCase):
    def read(self, value):
        with patch('bridge.features.accounts.models.build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(json.dumps(value).encode())
            return model_ids('https://fixture.invalid/v1', 'fixture-key')

    def test_openai_and_cc_switch_catalog_formats(self):
        self.assertEqual(self.read({'data': [{'id': 'gpt-5.5'}]}), ['gpt-5.5'])
        self.assertEqual(self.read({'models': [
            {'slug': 'gpt-5.5', 'visibility': 'list'},
            {'slug': 'hidden', 'visibility': 'hide'},
            {'slug': '../invalid model'}, {'slug': 'gpt-5.5'}]}), ['gpt-5.5'])

    def test_empty_cc_switch_catalog_explains_no_available_models(self):
        with self.assertRaisesRegex(ValueError, '未返回可用模型'):
            self.read({'models': []})

    def test_existing_formats_keep_id_semantics(self):
        self.assertEqual(self.read([{'id': 'array-model'}]), ['array-model'])
        self.assertEqual(self.read({'data': [{'id': 'openai-model'}],
                                    'models': [{'slug': 'native-model'}]}), ['openai-model'])
        with self.assertRaisesRegex(ValueError, '未返回可用模型'):
            self.read({'data': [], 'models': [{'slug': 'native-model'}]})


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_mac_without_desktop_uses_installed_path_cli(self):
        with patch('bridge.clients.codex.catalog.sys.platform', 'darwin'), \
             patch('bridge.clients.codex.catalog.os.name', 'posix'), \
             patch('bridge.clients.codex.catalog.Path.is_file', return_value=False), \
             patch('bridge.clients.codex.catalog.shutil.which', return_value='/installed/bin/codex') as which:
            self.assertEqual(str(Catalog.find_runtime()), '/installed/bin/codex')
        which.assert_called_once_with('codex')

    def test_missing_cli_and_desktop_remains_unavailable(self):
        with patch('bridge.clients.codex.catalog.sys.platform', 'darwin'), \
             patch('bridge.clients.codex.catalog.os.name', 'posix'), \
             patch('bridge.clients.codex.catalog.Path.is_file', return_value=False), \
             patch('bridge.clients.codex.catalog.shutil.which', return_value=None):
            self.assertIsNone(Catalog.find_runtime())


class ApiCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.home = Path(self.tmp.name)
        self.reader = Catalog(self.home, 'fixture-runtime')
        (self.home/'auth.json').write_text(json.dumps({'auth_mode':'apikey','OPENAI_API_KEY':'fixture-key'}))
        self.config = {'model_provider':'bridge_api','model_reasoning_effort':'ultra','model_providers':{'bridge_api':{
            'name':'Fixture','base_url':'https://fixture.invalid/v1','requires_openai_auth':True}}}
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, params):
        self.calls.append((method,params))
        if method == 'config/read':return {'config':self.config}
        if method == 'account/read':return {'account':{'type':'apiKey'}}
        if method == 'model/list':return {'data':[{'model':'gpt-fixture'}], 'nextCursor':None}
        raise AssertionError(method)

    def test_custom_provider_never_adds_unscoped_native_models(self):
        with patch('bridge.clients.codex.catalog.model_catalog', return_value=[{'id': 'provider-fixture', 'name': 'provider-fixture', 'efforts': []}]) as upstream:
            result = self.reader.read_models(self.request, str(self.home), 'bridge_api')
        upstream.assert_called_once_with('https://fixture.invalid/v1', 'fixture-key')
        self.assertEqual([m['model'] for m in result['models']], ['provider-fixture'])
        self.assertEqual(result['modelSource'], 'api')

    def test_explicit_catalog_uses_runtime_without_api_credentials_and_preserves_routes(self):
        for catalog_path in ('models.json', str(self.home/'models.json')):
            with self.subTest(catalog_path=catalog_path):
                self.config['model_catalog_json'] = catalog_path
                (self.home/'auth.json').write_text(json.dumps({'auth_mode': 'chatgpt'}))
                calls = []
                route = {'model': 'ccs-provider/gpt-fixture', 'displayName': 'Fixture (provider)',
                         'supportedReasoningEfforts': [{'reasoningEffort': 'high'}],
                         'defaultReasoningEffort': 'high'}
                def request(method, params):
                    calls.append((method, params))
                    if method == 'config/read':
                        return {'config': self.config}
                    self.assertEqual(method, 'model/list')
                    self.assertFalse(params['includeHidden'])
                    return ({'data': [route], 'nextCursor': 'page-2'} if params['cursor'] is None
                            else {'data': [{'model': 'second-model'}], 'nextCursor': None})
                with patch('bridge.clients.codex.catalog.model_catalog') as upstream:
                    result = self.reader.read_models(request, str(self.home), 'bridge_api')
                upstream.assert_not_called()
                self.assertEqual(result['models'], [route, {'model': 'second-model'}])
                self.assertEqual(result['modelSource'], 'codex')
                self.assertEqual(result['currentEffort'], 'ultra')
                self.assertEqual([method for method, _ in calls], ['config/read', 'model/list', 'model/list'])

    def test_explicit_catalog_does_not_replace_a_different_chat_provider(self):
        self.config['model_catalog_json'] = 'models.json'
        self.config['model_provider'] = 'other_provider'
        with patch('bridge.clients.codex.catalog.model_catalog', return_value=[{'id': 'provider-fixture', 'name': 'provider-fixture', 'efforts': []}]) as upstream:
            result = self.reader.read_models(self.request, str(self.home), 'bridge_api')
        upstream.assert_called_once_with('https://fixture.invalid/v1', 'fixture-key')
        self.assertEqual(result['modelSource'], 'api')

    def test_explicit_profile_catalog_failure_stays_visible_without_upstream_fallback(self):
        self.config.update(profile='routed', profiles={'routed': {'model_catalog_json': 'missing.json'}})
        def request(method, params):
            if method == 'config/read':
                return {'config': self.config}
            raise CatalogError('Configured model catalog could not be read')
        with patch('bridge.clients.codex.catalog.model_catalog') as upstream:
            with self.assertRaisesRegex(CatalogError, 'catalog could not be read'):
                self.reader.read_models(request, str(self.home), 'bridge_api')
        upstream.assert_not_called()

    def test_native_upstream_uses_the_same_public_catalog_as_openai(self):
        results = []
        for payload in ({'data': [{'id': 'provider-fixture'}]},
                        {'models': [{'slug': 'provider-fixture'}, {'slug': 'hidden', 'visibility': 'hide'}]}):
            with patch('bridge.features.accounts.models.build_opener') as factory:
                factory.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
                results.append(self.reader.read_models(self.request, str(self.home), 'bridge_api'))
        self.assertEqual(results[0], results[1])
        self.assertEqual([m['model'] for m in results[1]['models']], ['provider-fixture'])
        self.assertNotIn('fixture-key', json.dumps(results))

    def test_api_uses_upstream_ids_when_runtime_has_no_native_catalog(self):
        def request(method,params):
            self.calls.append((method,params))
            if method=='config/read':return {'config':self.config}
            if method=='account/read':return {'account':{'type':'apiKey'}}
            if method=='model/list':raise CatalogError('runtime has no catalog')
            raise AssertionError(method)
        with patch('bridge.clients.codex.catalog.model_catalog',return_value=[{'id': 'gemini-fixture', 'name': 'gemini-fixture', 'efforts': []}]) as upstream:
            result = self.reader.read_models(request,str(self.home),'bridge_api')
        upstream.assert_called_once_with('https://fixture.invalid/v1','fixture-key')
        self.assertEqual([m['model'] for m in result['models']],['gemini-fixture'])
        self.assertEqual(result['modelSource'],'api');self.assertEqual(result['currentEffort'],'ultra')
        self.assertNotIn('fixture-key',json.dumps(result))

    def test_resumed_openai_provider_and_project_profile_resolve_their_own_endpoint(self):
        self.config.update(openai_base_url='https://openai-alias.invalid/v1',profile='project',profiles={
            'project':{'model_provider':'other','model_providers':{'other':{
                'base_url':'https://project.invalid/v1','experimental_bearer_token':'project-key'}}}})
        for provider,url,key in [('openai','https://openai-alias.invalid/v1','fixture-key'),(None,'https://project.invalid/v1','project-key')]:
            calls=[]
            def request(method,params,calls=calls):
                calls.append((method,params))
                if method=='config/read':return {'config':self.config}
                if method=='account/read':return {'account':{'type':'apiKey'}}
                if method=='model/list':raise CatalogError('no runtime catalog')
                raise AssertionError(method)
            with patch('bridge.clients.codex.catalog.model_catalog',return_value=[{'id': 'gemini-fixture', 'name': 'gemini-fixture', 'efforts': []}]) as upstream:
                self.reader.read_models(request,str(self.home),provider)
                upstream.assert_called_once_with(url,key)

    def test_upstream_failure_never_falls_back_to_gpt_and_keeps_manual_entry(self):
        def request(method,params):
            self.calls.append((method,params))
            if method=='config/read':return {'config':self.config}
            if method=='account/read':return {'account':{'type':'apiKey'}}
            if method=='model/list':raise CatalogError('runtime has no catalog')
            raise AssertionError(method)
        with patch('bridge.clients.codex.catalog.model_catalog',side_effect=ValueError('无法连接上游，请检查 API 地址、网络和证书后重试')):
            result = self.reader.read_models(request,str(self.home),'bridge_api')
        self.assertEqual(result['models'],[])
        self.assertEqual(result['modelSource'],'api');self.assertIn('无法连接上游',result['modelError'])
        self.assertNotIn('model/list',[method for method,_ in self.calls])

    def test_official_account_retains_native_catalog(self):
        self.config={}
        def request(method,params):
            if method=='account/read':return {'account':{'type':'chatgpt'}}
            return self.request(method,params)
        with patch('bridge.clients.codex.catalog.model_catalog') as upstream:
            result=self.reader.read_models(request,str(self.home),'openai')
        upstream.assert_not_called();self.assertEqual(result['models'][0]['model'],'gpt-fixture')

    def test_chatgpt_with_route_override_keeps_native_gateway_catalog(self):
        for config,environment in (
                ({'openai_base_url':'http://127.0.0.1:3425/backend-api/codex'},{}),
                ({'profile':'gateway','profiles':{'gateway':{'openai_base_url':'https://proxy.test/backend-api/codex'}}},{}),
                ({},{'OPENAI_BASE_URL':'http://127.0.0.1:3425/backend-api/codex'})):
            with self.subTest(config=config,environment=environment):
                self.config=config;self.calls.clear()
                def request(method,params):
                    if method=='account/read':
                        self.calls.append((method,params))
                        return {'account':{'type':'chatgpt'},'requiresOpenaiAuth':True}
                    return self.request(method,params)
                with patch.dict(os.environ,environment),patch('bridge.clients.codex.catalog.model_catalog') as upstream:
                    result=self.reader.read_models(request,str(self.home))
                upstream.assert_not_called()
                self.assertEqual(result['modelSource'],'codex')
                self.assertEqual(result['models'][0]['model'],'gpt-fixture')
                self.assertNotIn('modelError',result)
                self.assertIn(('account/read',{'refreshToken':False}),self.calls)

    def test_openai_route_override_with_key_uses_api_catalog_and_never_native_fallback(self):
        self.config={'openai_base_url':'https://proxy.test/v1'}
        with patch('bridge.clients.codex.catalog.model_catalog',return_value=[{'id': 'provider-fixture', 'name': 'provider-fixture', 'efforts': []}]) as upstream:
            result=self.reader.read_models(self.request,str(self.home))
        upstream.assert_called_once_with('https://proxy.test/v1','fixture-key')
        self.assertEqual(result['modelSource'],'api')
        self.assertEqual([row['model'] for row in result['models']], ['provider-fixture'])

    def test_explicit_openai_api_definition_keeps_its_catalog_despite_chatgpt_credentials(self):
        self.config={'model_providers':{'openai':{'base_url':'https://proxy.test/v1','experimental_bearer_token':'gateway-key'}}}
        def request(method,params):
            if method=='account/read':raise AssertionError('API route must not use leftover ChatGPT identity')
            return self.request(method,params)
        with patch('bridge.clients.codex.catalog.model_catalog',return_value=[{'id': 'provider-fixture', 'name': 'provider-fixture', 'efforts': []}]) as upstream:
            result=self.reader.read_models(request,str(self.home))
        upstream.assert_called_once_with('https://proxy.test/v1','gateway-key')
        self.assertEqual(result['modelSource'],'api')

    def test_extra_auth_openai_definitions_never_use_native_catalog_or_default_endpoint(self):
        for field,value in (('aws',{'region':'fixture'}),('auth',{'command':'fixture'})):
            with self.subTest(field=field):
                self.config={'model_providers':{'openai':{field:value}}}
                self.calls.clear()
                def request(method,params):
                    if method=='account/read' or method=='model/list':
                        raise AssertionError('explicit API configuration must not use native account or catalog')
                    return self.request(method,params)
                with patch('bridge.clients.codex.catalog.model_catalog') as upstream:
                    result=self.reader.read_models(request,str(self.home))
                upstream.assert_not_called()
                self.assertEqual(result['modelSource'],'api')
                self.assertEqual(result['models'],[])
                self.assertIn('额外认证',result['modelError'])

    def test_missing_env_key_and_extra_auth_do_not_send_partial_credentials(self):
        for definition in ({'env_key':'CMB_ABSENT_FIXTURE_KEY'}, {'env_http_headers':{'private':'CMB_ABSENT_FIXTURE_HEADER'}}):
            self.config['model_providers']['bridge_api'].update(definition)
            def request(method,params):
                if method=='config/read':return {'config':self.config}
                if method=='account/read':return {'account':{'type':'apiKey'}}
                if method=='model/list':raise CatalogError('runtime has no catalog')
                raise AssertionError(method)
            with patch('bridge.clients.codex.catalog.model_catalog') as upstream:
                result=self.reader.read_models(request,str(self.home),'bridge_api')
            upstream.assert_not_called();self.assertEqual(result['models'],[]);self.assertTrue(result['modelError'])

    def test_get_preserves_runtime_current_model_and_effort(self):
        raw={'models':[{'model':'glm-5.3-flash','displayName':'glm-5.3-flash','supportedReasoningEfforts':[{'reasoningEffort':'ultra'}]}],
             'skillEntries':[],'skills':[],'modelSource':'codex','currentModel':'glm-5.3-flash','currentEffort':'ultra'}
        self.reader._fetch=lambda cwd,provider=None: raw
        result=self.reader.get(self.home,provider='custom')
        self.assertEqual(result['currentModel'],'glm-5.3-flash')
        self.assertEqual(result['currentEffort'],'ultra')
        self.assertEqual(result['models'][0]['id'],'glm-5.3-flash')
        self.assertEqual(result['models'][0]['efforts'],['ultra'])

    def test_legacy_skill_cache_returns_immediately_and_refreshes_in_background(self):
        cached=[{'id':'fixture-skill','name':'fixture-skill','description':'fixture','path':'/tmp/fixture/SKILL.md','scope':'user','displayName':'Fixture'}]
        self.reader.skill_store.legacy=lambda: cached
        seen={}
        def fetch(cwd, provider=None, kind='catalog', request_timeout=90):
            seen['kind']=kind; seen['request_timeout']=request_timeout
            raise CatalogError('runtime stalled')
        self.reader._fetch=fetch
        result=self.reader.get_kind('skills', self.home)
        self.assertEqual(result['skills'],cached)
        self.assertEqual(result['cache']['state'],'legacy')
        for _ in range(50):
            if seen: break
            time.sleep(.01)
        self.assertEqual(seen, {'kind':'skills','request_timeout':90})

    def test_provider_is_part_of_catalog_cache_identity(self):
        calls=[]
        def fetch(cwd,provider=None):
            calls.append(provider)
            return {'models':[{'model':provider}], 'skillEntries':[], 'modelSource':'api'}
        self.reader._fetch=fetch
        self.assertEqual(self.reader.get(self.home,provider='first')['models'][0]['id'],'first')
        self.assertEqual(self.reader.get(self.home,provider='second')['models'][0]['id'],'second')
        self.reader.get(self.home,provider='first');self.assertEqual(calls,['first','second'])

    def test_catalog_helper_preserves_project_context_when_directory_is_missing(self):
        server = self.home/'fixture-catalog.py'
        record = self.home/'requests.jsonl'
        server.write_text('''import json, os, sys
from pathlib import Path
record = Path(os.environ['CODEX_HOME'])/'requests.jsonl'
with record.open('w', encoding='utf-8') as log:
    log.write(json.dumps({'processCwd': os.getcwd(), 'home': os.environ['CODEX_HOME']}) + '\\n')
    for line in sys.stdin:
        message = json.loads(line)
        log.write(json.dumps(message) + '\\n'); log.flush()
        if 'id' not in message:
            continue
        method = message['method']
        result = {'config': {'model_provider': 'fixture-provider', 'model_catalog_json': 'fixture.json'}} if method == 'config/read' else (
            {'data': [{'model': 'fixture-model'}]} if method == 'model/list' else {})
        print(json.dumps({'id': message['id'], 'result': result}), flush=True)
''', encoding='utf-8')
        project = self.home/'project'
        project.mkdir()
        original_popen = subprocess.Popen
        def launch(argv, **kwargs):
            return original_popen([sys.executable, '-u', str(server)], **kwargs)
        for cwd in (project, project/'removed'/'nested'):
            with self.subTest(cwd=cwd), patch('bridge.clients.codex.catalog.subprocess.Popen', side_effect=launch):
                result = self.reader._fetch(str(cwd), provider='fixture-provider', request_timeout=3)
                self.assertEqual(result['models'], [{'model': 'fixture-model'}])
                self.assertEqual(result['_defaultProvider'], 'fixture-provider')
                rows = [json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]
                self.assertEqual(Path(rows[0]['processCwd']), project)
                self.assertEqual(rows[0]['home'], str(self.home))
                configs = [row['params']['cwd'] for row in rows[1:] if row.get('method') == 'config/read']
                self.assertTrue(configs)
                self.assertEqual(set(configs), {str(cwd)})
                skills = [row['params']['cwds'] for row in rows[1:] if row.get('method') == 'skills/list']
                self.assertEqual(skills, [[str(cwd)]])
                self.assertFalse((project/'removed').exists())

    def test_catalog_helper_does_not_retry_permission_denied(self):
        with patch('bridge.clients.codex.catalog.subprocess.Popen', side_effect=PermissionError('denied')) as launch:
            with self.assertRaises(CatalogError) as error:
                self.reader._fetch(str(self.home))
            self.assertIsInstance(error.exception.__cause__, PermissionError)
            launch.assert_called_once()
            self.assertEqual(Path(launch.call_args.kwargs['cwd']), self.home)

    def test_model_read_does_not_wait_for_inherited_stdout_writer(self):
        release = self.home/'release-writer'
        server = self.home/'fixture-app-server.py'
        child = (f'from pathlib import Path\nimport time\n'
                 f'while not Path({str(release)!r}).exists():\n    time.sleep(0.02)\n')
        server.write_text('''import json, subprocess, sys
subprocess.Popen([sys.executable, '-c', ''' + repr(child) + '''],
                 stdout=sys.stdout, stderr=subprocess.DEVNULL)
config = {'model_provider': 'bridge_api', 'model_providers': {'bridge_api': {
    'base_url': 'https://fixture.invalid/v1', 'experimental_bearer_token': 'fixture-key'}}}
for line in sys.stdin:
    message = json.loads(line)
    if 'id' in message:
        result = {'config': config} if message['method'] == 'config/read' else {}
        print(json.dumps({'id': message['id'], 'result': result}), flush=True)
''', encoding='utf-8')
        original_popen = subprocess.Popen
        processes, results, errors = [], [], []
        def launch(argv, **kwargs):
            process = original_popen([sys.executable, '-u', str(server)], **kwargs)
            processes.append(process)
            return process
        def read():
            try:
                results.append(self.reader.get_kind('models', self.home, provider='bridge_api'))
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=read)
        with patch('bridge.clients.codex.catalog.subprocess.Popen', side_effect=launch), \
                patch('bridge.clients.codex.catalog.model_catalog', return_value=[
                    {'id': 'fixture-model', 'name': 'Fixture model', 'efforts': ['high']}]) as upstream:
            worker.start()
            try:
                worker.join(timeout=5)
                completed_before_release = not worker.is_alive()
            finally:
                release.touch()
                worker.join(timeout=5)
            self.assertTrue(completed_before_release, 'model read waited for inherited stdout writer')
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results[0]['models'][0]['id'], 'fixture-model')
            upstream.assert_called_once_with('https://fixture.invalid/v1', 'fixture-key')
            self.assertEqual(len(processes), 1)
            for _ in range(100):
                if processes[0].stdout.closed:
                    break
                time.sleep(.02)
            self.assertTrue(processes[0].stdout.closed, 'reader did not release stdout after writer exit')

    def test_sqlite_skill_buckets_search_pagination_and_provider_independence(self):
        calls=[]
        shared_root=self.home/'shared';shared_root.mkdir()
        def make(name,root):
            path=root/(name+'-skill')/'SKILL.md';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(f'---\nname: {name}\n---\nFixture {name}')
            return {'name':name,'displayName':name.upper(),'description':f'fixture {name}','path':str(path),'scope':'user','enabled':True}
        shared=[make('shared-'+str(index),shared_root) for index in range(2)]
        def fetch(cwd,provider=None,kind='catalog',request_timeout=90):
            calls.append((cwd,provider))
            prefix='project-' if str(cwd).endswith('one') else 'other-'
            rows=shared+[make(prefix+str(index),Path(cwd)) for index in range(10)]
            return {'skillEntries':[{'skills':rows,'errors':[]}],'models':[]}
        self.reader._fetch=fetch
        one=self.home/'one';one.mkdir();two=self.home/'two';two.mkdir()
        first=self.reader.get_kind('skills',one,provider='first',limit=5)
        self.assertEqual(first['total'],12);self.assertEqual(len(first['skills']),5);self.assertEqual(first['cache']['state'],'fresh')
        repeated=self.reader.get_kind('skills',one,provider='second',query='shared',limit=5)
        self.assertEqual(repeated['total'],2);self.assertEqual([row['name'] for row in repeated['skills']],['shared-0','shared-1'])
        second=self.reader.get_kind('skills',two,provider='first',limit=5)
        self.assertTrue(any(row['name'].startswith('other-') for row in second['skills']))
        self.assertEqual(len(calls),2)
        db=self.reader.skill_store._connect()
        try:
            self.assertEqual(db.execute('select count(*) from skill_buckets').fetchone()[0],2)
            self.assertEqual(db.execute('select count(*) from skill_records').fetchone()[0],22)
        finally:db.close()

    def test_sqlite_corruption_rebuilds(self):
        self.reader.skill_store.root.mkdir(parents=True,exist_ok=True)
        self.reader.skill_store.path.write_text('not sqlite')
        # A valid subsequent write recreates the database.
        path=self.home/'safe';path.mkdir();skill_path=path/'SKILL.md';skill_path.write_text('safe')
        self.reader._fetch=lambda cwd,provider=None,kind='catalog',request_timeout=90:{'skillEntries':[{'skills':[{'name':'safe','path':str(skill_path),'scope':'user'}]}],'models':[]}
        result=self.reader.get_kind('skills',path)
        self.assertEqual([row['name'] for row in result['skills']],['safe'])
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.reader.skill_store.path.stat().st_mode),0o600)

    def test_selected_skill_validation_rejects_changed_content_until_refresh(self):
        root=self.home/'project';root.mkdir();path=root/'SKILL.md';path.write_text('original')
        row={'name':'sample','path':str(path),'scope':'project'}
        def fetch(cwd,provider=None,kind='catalog',request_timeout=90):
            return {'skillEntries':[{'skills':[row]}],'models':[]}
        self.reader._fetch=fetch
        result=self.reader.get_kind('skills',root,ids=[result_id] if (result_id:=hashlib.sha256(str(path).encode()).hexdigest()) else [])
        identifier=result['skills'][0]['id']
        self.assertEqual(result['selectedSkills'][0]['id'],identifier)
        self.assertEqual([row['id'] for row in self.reader.validate_skills(root,[identifier])],[identifier])
        path.write_text('changed')
        with self.assertRaises(ValueError):self.reader.skill_store.validate(root,[identifier])
        row={'name':'sample','path':str(path),'scope':'project'}
        self.assertEqual([item['id'] for item in self.reader.validate_skills(root,[identifier],refresh=True)],[identifier])


    def test_relative_skill_paths_are_normalized_to_their_cwd(self):
        root=self.home/'project';root.mkdir()
        skill_path=root/'skills/relative/SKILL.md';skill_path.parent.mkdir(parents=True);skill_path.write_text('fixture')
        raw={'skillEntries':[{'skills':[{'name':'relative','path':'skills/relative/SKILL.md','scope':'project'}]}]}
        self.reader._fetch=lambda cwd,provider=None,kind='catalog',request_timeout=90:raw
        result=self.reader.get_kind('skills',root,refresh=True)
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['skills'][0]['path'],str(root/'skills/relative/SKILL.md'))
        self.assertEqual(result['skills'][0]['id'],hashlib.sha256(result['skills'][0]['path'].encode()).hexdigest())
        self.assertEqual(self.reader.skill_store.validate(root,[result['skills'][0]['id']])[0]['path'],result['skills'][0]['path'])

    def test_large_sqlite_catalog_queries_one_page_and_searches_by_bucket(self):
        rows=[{'name':('needle-' if index==4999 else 'skill-')+str(index),
               'path':str(self.home/f'project-{index}'/'SKILL.md'),'scope':'project'} for index in range(5000)]
        self.reader.skill_store.write(self.home,rows)
        page=self.reader.get_kind('skills',self.home,limit=200)
        self.assertEqual(len(page['skills']),200);self.assertEqual(page['total'],5000)
        needle=self.reader.get_kind('skills',self.home,query='needle-4999',limit=200)
        self.assertEqual(needle['total'],1);self.assertEqual(needle['skills'][0]['name'],'needle-4999')

    def test_stale_bucket_returns_without_blocking_and_explicit_refresh_waits_for_runtime(self):
        root=self.home/'project';root.mkdir();path=root/'SKILL.md';path.write_text('fixture')
        calls=[]
        def fetch(cwd,provider=None,kind='catalog',request_timeout=90):
            calls.append(kind);return {'skillEntries':[{'skills':[{'name':'sample','path':str(path)}]}],'models':[]}
        self.reader._fetch=fetch
        self.reader.get_kind('skills',root)
        db=self.reader.skill_store._connect()
        try:
            with db:db.execute('update skill_buckets set refreshed_at=0')
        finally:db.close()
        self.reader.kind_caches['skills'].clear();self.reader.allow_background_refresh=False
        self.reader._fetch=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('stale read must not block'))
        stale=self.reader.get_kind('skills',root)
        self.assertEqual(stale['cache']['state'],'stale');self.assertEqual(len(stale['skills']),1)
        self.reader._fetch=fetch;calls.clear()
        refreshed=self.reader.get_kind('skills',root,refresh=True)
        self.assertEqual(refreshed['cache']['state'],'fresh');self.assertEqual(calls,['skills'])



    def test_failed_model_results_are_retried_and_successes_share_the_backend_cache(self):
        for kind in ('models', 'catalog'):
            with self.subTest(kind=kind):
                self.reader.invalidate_models()
                read = (lambda **kw: self.reader.get_kind('models', str(self.home), **kw)) if kind == 'models' else (
                    lambda **kw: self.reader.get(str(self.home), **kw))
                with patch.object(self.reader, '_fetch', side_effect=[
                        {'modelSource': 'api', 'models': [], 'modelError': 'Temporary failure', 'skillEntries': []},
                        {'modelSource': 'api', 'models': [{'id': 'recovered'}], 'skillEntries': []}]) as fetch:
                    first, second, third = read(), read(), read()
                self.assertEqual(first['modelError'], 'Temporary failure')
                self.assertEqual(second['models'][0]['name'], 'recovered')
                self.assertEqual(third, second)
                self.assertEqual(fetch.call_count, 2)

    def test_a_failed_explicit_refresh_keeps_the_last_successful_model_catalog(self):
        for kind in ('models', 'catalog'):
            with self.subTest(kind=kind):
                self.reader.invalidate_models()
                read = (lambda **kw: self.reader.get_kind('models', str(self.home), provider='model-test', **kw)) if kind == 'models' else (
                    lambda **kw: self.reader.get(str(self.home), **kw))
                with patch.object(self.reader, '_fetch', side_effect=[
                        {'models': [{'model': 'old'}], 'skillEntries': []}, CatalogError('Temporary failure'),
                        {'models': [{'model': 'new'}], 'skillEntries': []}]) as fetch:
                    read()
                    if kind == 'models':
                        failed = read(refresh=True)
                        self.assertEqual(failed['models'][0]['id'], 'old')
                        self.assertIn('Temporary failure', failed['modelError'])
                        self.assertEqual(read()['models'][0]['id'], 'old')
                        result = read(refresh=True)
                        self.assertEqual(fetch.call_count, 3)
                    else:
                        with self.assertRaises(CatalogError):
                            read(refresh=True)
                        result = read()
                        self.assertEqual(fetch.call_count, 3)
                self.assertEqual(result['models'][0]['id'], 'new')

    def test_model_cache_survives_reader_restart_and_separates_credentials(self):
        raw = {'models': [{'model': 'first', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}],
               'modelSource': 'codex'}
        with patch.object(self.reader, '_fetch', return_value=raw) as fetch:
            first = self.reader.get_kind('models', str(self.home), provider='openai')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first['cache']['identity'], self.reader.model_identity(str(self.home), 'openai'))
        restarted = Catalog(self.home, 'fixture-runtime')
        with patch.object(restarted, '_fetch', side_effect=AssertionError('cached read contacted runtime')):
            cached = restarted.get_kind('models', str(self.home), provider='openai')
            self.assertEqual(cached['models'], first['models'])
            self.assertEqual(cached['cache']['identity'], first['cache']['identity'])
        data = restarted.model_store.path.read_bytes()
        self.assertNotIn(b'fixture-key', data)
        (self.home/'auth.json').write_text(json.dumps({'OPENAI_API_KEY': 'changed-key'}))
        with patch.object(restarted, '_fetch', return_value={'models': [{'model': 'second'}]}) as fetch:
            changed = restarted.get_kind('models', str(self.home), provider='openai')
            self.assertEqual(changed['models'][0]['id'], 'second')
            self.assertNotEqual(changed['cache']['identity'], first['cache']['identity'])
        fetch.assert_called_once()

    def test_model_result_is_rejected_if_account_changes_during_fetch(self):
        old_identity = self.reader.model_identity(str(self.home), 'openai')
        def switched(*args):
            (self.home/'auth.json').write_text(json.dumps({'OPENAI_API_KEY': 'new-key'}))
            return {'models': [{'model': 'old-account'}]}
        with patch.object(self.reader, '_fetch', side_effect=switched):
            with self.assertRaisesRegex(CatalogError, '接入已变化'):
                self.reader.get_kind('models', str(self.home), provider='openai')
        self.assertIsNone(self.reader.model_store.read(old_identity))

    def test_expired_model_cache_returns_before_background_refresh(self):
        with patch.object(self.reader, '_fetch', return_value={'models': [{'model': 'old'}]}):
            self.reader.get_kind('models', str(self.home), provider='openai')
        db = self.reader.model_store._connect()
        try:
            db.execute('UPDATE catalogs SET updated_at=0')
            db.commit()
        finally:
            db.close()
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            self.assertTrue(release.wait(2))
            return {'models': [{'model': 'new'}]}
        with patch.object(self.reader, '_fetch', side_effect=slow) as fetch:
            first = self.reader.get_kind('models', str(self.home), provider='openai')
            second = self.reader.get_kind('models', str(self.home), provider='openai')
            self.assertEqual([m['id'] for m in first['models']], ['old'])
            self.assertEqual(second['cache']['state'], 'stale')
            self.assertTrue(entered.wait(2))
            release.set()
            for _ in range(100):
                if not self.reader.model_refreshing:
                    break
                time.sleep(.01)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.reader.get_kind('models', str(self.home), provider='openai')['models'][0]['id'], 'new')


class RemoteCatalogTests(unittest.TestCase):
    def test_remote_source_includes_helpers_and_keeps_provider_cache_separate(self):
        from bridge.clients.codex.remote import RemoteCatalog
        sources=[]
        def read(alias,source,timeout):
            self.assertEqual(alias,'fixture-host');sources.append(source)
            # Compile and load the exact helper bundle without starting a runtime or SSH.
            definitions=source[:source.rindex('\nimport shutil\nhome=')]
            namespace={'__file__':'<stdin>'}
            exec(compile(definitions,'<remote-catalog>','exec'),namespace)
            self.assertTrue(callable(namespace['model_ids']))
            self.assertTrue(callable(namespace['client_context']))
            compile(source,'<remote-catalog>','exec')
            return {'models':[],'skills':[],'modelSource':'api'}
        reader=RemoteCatalog('fixture-host')
        with patch('bridge.clients.codex.remote.ssh_read',side_effect=read):
            reader.get('/project',provider='first');reader.get('/project',provider='second');reader.get('/project',provider='first')
        self.assertEqual(len(sources),2)
        self.assertNotEqual(sources[0],sources[1])



    def test_remote_skill_queries_use_host_local_sqlite_and_ignore_provider(self):
        from bridge.clients.codex.remote import RemoteCatalog
        sources=[]
        def read(alias,source,timeout):
            sources.append(source)
            compile(source,'<remote-skill-catalog>','exec')
            return {'kind':'skills','skills':[],'selectedSkills':[],'errors':[],'total':0,'offset':0,'limit':200,'cache':{'state':'fresh'}}
        reader=RemoteCatalog('fixture-host')
        with patch('bridge.clients.codex.remote.ssh_read',side_effect=read):
            reader.get_kind('skills','/project',provider='first')
            reader.get_kind('skills','/project',provider='second')
            reader.get_kind('skills','/project',provider='second',query='fixture')
        self.assertEqual(len(sources),2)
        self.assertIn('skills-v2.sqlite3',sources[0])
        self.assertIn('allow_background_refresh=False',sources[0])
