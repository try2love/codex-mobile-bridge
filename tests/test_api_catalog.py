import hashlib
import os
import json
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.catalog import Catalog, CatalogError

ROOT = Path(__file__).resolve().parents[1]


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

    def test_custom_provider_does_not_trust_unscoped_native_models(self):
        with patch('bridge.catalog.model_ids', return_value=['provider-fixture']) as upstream:
            result = self.reader.read_models(self.request, str(self.home), 'bridge_api')
        upstream.assert_called_once_with('https://fixture.invalid/v1', 'fixture-key')
        self.assertEqual([m['model'] for m in result['models']], ['provider-fixture'])
        self.assertNotIn('model/list', [method for method, _ in self.calls])
        self.assertEqual(result['modelSource'], 'api')

    def test_api_uses_upstream_ids_when_runtime_has_no_native_catalog(self):
        def request(method,params):
            self.calls.append((method,params))
            if method=='config/read':return {'config':self.config}
            if method=='account/read':return {'account':{'type':'apiKey'}}
            if method=='model/list':raise CatalogError('runtime has no catalog')
            raise AssertionError(method)
        with patch('bridge.catalog.model_ids',return_value=['gemini-fixture']) as upstream:
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
            with patch('bridge.catalog.model_ids',return_value=['gemini-fixture']) as upstream:
                self.reader.read_models(request,str(self.home),provider)
                upstream.assert_called_once_with(url,key)

    def test_upstream_failure_never_falls_back_to_gpt_and_keeps_manual_entry(self):
        def request(method,params):
            self.calls.append((method,params))
            if method=='config/read':return {'config':self.config}
            if method=='account/read':return {'account':{'type':'apiKey'}}
            if method=='model/list':raise CatalogError('runtime has no catalog')
            raise AssertionError(method)
        with patch('bridge.catalog.model_ids',side_effect=ValueError('无法连接上游，请检查 API 地址、网络和证书后重试')):
            result = self.reader.read_models(request,str(self.home),'bridge_api')
        self.assertEqual(result['models'],[])
        self.assertEqual(result['modelSource'],'api');self.assertIn('无法连接上游',result['modelError'])
        self.assertNotIn('model/list',[method for method,_ in self.calls])

    def test_official_account_retains_native_catalog(self):
        self.config={}
        def request(method,params):
            if method=='account/read':return {'account':{'type':'chatgpt'}}
            return self.request(method,params)
        with patch('bridge.catalog.model_ids') as upstream:
            result=self.reader.read_models(request,str(self.home),'openai')
        upstream.assert_not_called();self.assertEqual(result['models'][0]['model'],'gpt-fixture')

    def test_missing_env_key_and_extra_auth_do_not_send_partial_credentials(self):
        for definition in ({'env_key':'CMB_ABSENT_FIXTURE_KEY'}, {'http_headers':{'private':'fixture'}}):
            self.config['model_providers']['bridge_api'].update(definition)
            def request(method,params):
                if method=='config/read':return {'config':self.config}
                if method=='account/read':return {'account':{'type':'apiKey'}}
                if method=='model/list':raise CatalogError('runtime has no catalog')
                raise AssertionError(method)
            with patch('bridge.catalog.model_ids') as upstream:
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


class RemoteCatalogTests(unittest.TestCase):
    def test_remote_source_includes_helpers_and_keeps_provider_cache_separate(self):
        from bridge.remote import RemoteCatalog
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
        with patch('bridge.remote.ssh_read',side_effect=read):
            reader.get('/project',provider='first');reader.get('/project',provider='second');reader.get('/project',provider='first')
        self.assertEqual(len(sources),2)
        self.assertNotEqual(sources[0],sources[1])



    def test_remote_skill_queries_use_host_local_sqlite_and_ignore_provider(self):
        from bridge.remote import RemoteCatalog
        sources=[]
        def read(alias,source,timeout):
            sources.append(source)
            compile(source,'<remote-skill-catalog>','exec')
            return {'kind':'skills','skills':[],'selectedSkills':[],'errors':[],'total':0,'offset':0,'limit':200,'cache':{'state':'fresh'}}
        reader=RemoteCatalog('fixture-host')
        with patch('bridge.remote.ssh_read',side_effect=read):
            reader.get_kind('skills','/project',provider='first')
            reader.get_kind('skills','/project',provider='second')
            reader.get_kind('skills','/project',provider='second',query='fixture')
        self.assertEqual(len(sources),2)
        self.assertIn('skills-v2.sqlite3',sources[0])
        self.assertIn('allow_background_refresh=False',sources[0])
