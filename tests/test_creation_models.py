import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bridge.clients.claude.adapter import Claude
from bridge.clients.manager import DesktopSessions
from bridge.features.sessions.create import create_empty, CreationError
from bridge.features.sessions.creation_models import validate
from bridge.clients.codex.catalog import Catalog
from bridge.app.service import Bridge


class CreationChoices(unittest.TestCase):
    def test_independent_codex_entry_never_routes_empty_project_to_default_account(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);home=root/'separate-home';home.mkdir();bridge=Bridge(home,root/'entry-data',host='codex:separate')
            self.addCleanup(bridge.close)
            bridge.hosts.state=lambda:{}
            bridge.cli_sessions.creation_context=Mock(return_value={'model_provider':'separate','model':'chosen','model_reasoning_effort':'low'})
            catalog={'models':[{'id':'chosen','efforts':['low']}],'cache':{'identity':'separate-cache'}}
            bridge._native_models=Mock(return_value=catalog)
            original=bridge.for_host
            def own(host):
                self.assertEqual(host,'codex:separate');return original(host)
            bridge.for_host=own
            result=bridge.creation_models('codex:separate|independent')
            self.assertEqual(result['currentModel'],'chosen');self.assertEqual(bridge.project_host('local|independent'),'codex:separate')
            self.assertTrue(str(bridge.cli_sessions.creation_context.call_args.args[0]).startswith(str(bridge.data_dir.resolve())))
            selected={'model':'chosen','effort':'low','modelCatalogIdentity':'separate-cache'};identifier=str(uuid.uuid4());attempt=str(uuid.uuid4())
            def create(cwd,title,**options):
                self.assertTrue(Path(cwd).is_relative_to(bridge.data_dir.resolve()));self.assertEqual(options['model'],'chosen');self.assertEqual(options['effort'],'low')
                options['on_start']();options['on_created'](identifier)
                return {'id':identifier,'cwd':cwd,'modelProvider':'separate','turns':[],'requests':[]}
            bridge.cli_sessions.create=Mock(side_effect=create)
            self.assertEqual(bridge.create_chat('codex:separate|independent','Separate',attempt,model_options=selected)['id'],identifier)
            self.assertEqual(bridge.create_chat('codex:separate|independent','Separate',attempt,model_options={**selected,'modelCatalogIdentity':'refreshed'})['id'],identifier)
            self.assertEqual(bridge.cli_sessions.create.call_count,1)

    def test_project_config_changes_invalidate_catalog_without_reusing_global_account_models(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);home=root/'home';cwd=root/'project';home.mkdir();cwd.mkdir()
            reader=Catalog(home,'fixture',allow_background_refresh=False)
            reader._fetch=Mock(side_effect=[{'models':[{'model':'first'}]}, {'models':[{'model':'project-model'}]}, {'models':[{'model':'parent-model'}]}])
            first=reader.get_kind('models',str(cwd));reader.get_kind('models',str(cwd));self.assertEqual(reader._fetch.call_count,1)
            (cwd/'.codex').mkdir();(cwd/'.codex/config.toml').write_text('model_provider="project"\n')
            second=reader.get_kind('models',str(cwd));self.assertNotEqual(first['cache']['identity'],second['cache']['identity']);self.assertEqual(second['models'][0]['id'],'project-model')
            (root/'.codex').mkdir();(root/'.codex/config.toml').write_text('model_provider="parent"\n')
            third=reader.get_kind('models',str(cwd));self.assertNotEqual(second['cache']['identity'],third['cache']['identity'])
            bridge=object.__new__(Bridge);bridge.host='local';bridge.codex_home=home
            bridge.projects=lambda host=None:[{'key':'local|project','host':'local','cwd':str(cwd),'type':'desktop'}]
            bridge.project_host=lambda key:'local';bridge.for_host=lambda host:bridge;bridge.catalog_reader=reader
            bridge.accounts=Mock();bridge.accounts.current_models.side_effect=AssertionError('Global account catalog must not override cwd')
            self.assertEqual(bridge.creation_models('local|project')['models'][0]['id'],'parent-model')
            bridge.accounts.current_models.assert_not_called();self.assertEqual(reader._fetch.call_count,3)

    def test_identity_model_and_effort_are_checked_without_guessing_defaults(self):
        catalog = {'models': [{'id': 'chosen', 'efforts': ['low', 'high']}], 'cache': {'identity': 'account-a'}}
        self.assertEqual(validate({}, catalog), {})
        request = {'model': 'chosen', 'effort': 'low', 'modelCatalogIdentity': 'account-a'}
        self.assertEqual(validate(request, catalog), {'model': 'chosen', 'effort': 'low'})
        for change in ({'modelCatalogIdentity': 'account-b'}, {'model': 'other'}, {'effort': 'max'}, {'effort': None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate({**request, **change}, catalog)
        with self.assertRaises(ValueError):validate({'model':'chosen','modelCatalogIdentity':'account-a'}, catalog)

    def test_codex_start_configures_empty_thread_without_any_turn(self):
        calls = []; identifier = str(uuid.uuid4())
        def rpc(method, params):
            calls.append((method, params))
            if method == 'thread/start':
                return {'thread': {'id': identifier}, 'model': 'fixture-model', 'reasoningEffort': 'low'}
            if method == 'thread/read':return {'thread': {'id': identifier}}
            return {}
        with patch('bridge.features.sessions.create._runtime_operation', side_effect=lambda a,b,c,run:run(rpc)):
            result = create_empty('fixture', Path('.'), '.', 'Name', model='fixture-model', effort='low')
        self.assertEqual(result, identifier)
        self.assertEqual(calls[0], ('thread/start', {'cwd':'.','ephemeral':False,'model':'fixture-model','config':{'model_reasoning_effort':'low'}}))
        self.assertNotIn('turn/start', [method for method,_ in calls])
        with patch('bridge.features.sessions.create._runtime_operation', side_effect=lambda a,b,c,run:run(rpc)):
            with self.assertRaisesRegex(CreationError, '尚未确认'):
                create_empty('fixture', Path('.'), '.', 'Name', model='different-model', effort='low')


class ClaudeCreationChoices(unittest.IsolatedAsyncioTestCase):
    async def test_code_and_cowork_start_include_choice_with_the_very_first_message(self):
        with tempfile.TemporaryDirectory() as directory:
            for surface in ('code','cowork'):
                adapter = Claude(Path(directory)/surface)
                adapter.desktop = Mock(capabilities={surface:['start']})
                adapter.desktop.call = AsyncMock(return_value={'sessionId':'created'})
                with patch.object(adapter,'status',return_value={'connected':True}), \
                     patch.object(adapter,'_start_desktop_project',new_callable=AsyncMock,return_value={'status':'ready'}) as start:
                    await adapter.dispatch('create',None,{'title':'Fixture','firstMessage':'FIRST',
                        'model':'claude-sonnet-4-5-20250929','effort':'low',
                        'directoryProject':{'surface':surface,'cwd':directory}})
                options = start.call_args.args[1]
                self.assertEqual(options['message'],'FIRST');self.assertEqual(options['model'],'claude-sonnet-4-5-20250929');self.assertEqual(options['effort'],'low')
                adapter.desktop.call.assert_not_awaited()


class CreationCatalogScope(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.manager=DesktopSessions(Path(temporary.name));self.addCleanup(self.manager.close)
        self.manager.config['enabled']={'claude':True}
        self.adapter=self.manager.adapters['claude']
        self.manager._adapter_call=Mock(side_effect=AssertionError('Creation model reads must not scan unrelated hosts'))

    def test_native_catalog_uses_existing_account_cache_and_selected_surface(self):
        env={'key':'scope','surface':'code','host':'local'}
        self.manager._project_environment=Mock(return_value=env)
        self.adapter.index={'prior':{'backend':'code','cwd':'/fixture'}}
        catalog={'models':[{'id':'code-model'},{'id':'cowork-model'}],
            'groups':[{'surface':'code','models':[{'id':'code-model','efforts':['low']}]},
                      {'surface':'cowork','models':[{'id':'cowork-model'}]}],
            'cache':{'identity':'account-cache','updatedAt':123}}
        self.manager._account_models=Mock(return_value=catalog)
        request={'environment':'scope','projectKey':'code:/fixture'}
        result=self.manager.creation_models('claude',request)
        self.assertEqual(result['models'],catalog['groups'][0]['models'])
        self.assertEqual(result['cache'],catalog['cache'])
        self.manager._account_models.assert_called_once_with('claude',refresh=False)
        self.manager.creation_models('claude',{**request,'refresh':True})
        self.manager._account_models.assert_called_with('claude',refresh=True)
        self.manager._adapter_call.assert_not_called()

    def test_cli_catalog_reuses_same_scope_store_without_starting_or_claiming_a_session(self):
        env={'key':'scope','surface':'cli','host':'local','origin':'account-a'}
        self.manager._project_environment=Mock(return_value=env)
        self.manager.directory_projects.get=Mock(return_value={'provider':'claude','environment':'scope','cwd':'/fixture','origin':'account-a'})
        self.manager._model_identity=Mock(return_value='a'*64)
        self.manager.model_store.write('a'*64,{'models':[{'id':'from-existing-cache','efforts':['low']}]})
        self.manager._catalog_models=Mock(side_effect=AssertionError('No CLI owner may be started for discovery'))
        self.adapter.cli.create=Mock(side_effect=AssertionError('No create during model read'))
        result=self.manager.creation_models('claude',{'environment':'scope','projectKey':'registered','refresh':True})
        self.assertEqual(result['models'][0]['id'],'from-existing-cache')
        self.assertEqual(result['cache']['identity'],'a'*64);self.assertEqual(result['cache']['state'],'stale')
        self.manager._model_identity.return_value='b'*64
        fresh=self.manager.creation_models('claude',{'environment':'scope','projectKey':'registered'})
        self.assertEqual(fresh['models'],[]);self.assertTrue(fresh['capabilities']['manualModels'])
        self.adapter.cli.create.assert_not_called();self.manager._catalog_models.assert_not_called()

    def test_local_environment_selection_never_asks_an_offline_remote_cli(self):
        self.adapter.status=Mock(return_value={'connected':True})
        self.adapter.desktop=Mock(capabilities={'code':['start'],'cowork':['start']})
        self.adapter.cli.profile_resolver=lambda:None
        self.adapter.remote_cli.project_environments=Mock(side_effect=AssertionError('unrelated SSH request'))
        self.assertEqual([row['surface'] for row in self.adapter.project_environments(host_id='local')],['code','cowork'])
        self.adapter.remote_cli.project_environments.assert_not_called()
