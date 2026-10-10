import base64
import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.accounts.account import Account
from bridge.features.accounts.accounts import Accounts, ManagedRPC, operation, private_bytes, private_json
from bridge.clients.desktop_app import DesktopApp
from bridge.app.service import Bridge

ROOT = Path(__file__).resolve().parents[1]


def wait_for_account_worker(test):
    if test.manager.thread:
        # Windows durable file writes can outlast the former three-second bound.
        test.manager.thread.join(30)
        test.assertFalse(test.manager.thread.is_alive(), 'account worker did not finish within 30 seconds')


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.root = Path(self.tmp.name)
        self.home = self.root/'home'
        self.home.mkdir()
        (self.home/'config.toml').write_text('# existing config\nmodel="original"\n')
        (self.home/'auth.json').write_text('{"tokens":{"account_id":"original"}}')
        (self.home/'history.jsonl').write_text('preserve history')
        self.bridge = SimpleNamespace(codex_home=self.home, data_dir=self.root,
            catalog_reader=SimpleNamespace(executable=self.root/'runtime', cache={'old': 1}),
            remote_bridges={}, live={}, submissions={}, lock=threading.RLock(),
            account=Account(self.home,self.root,executable=self.root/'runtime'), ipc=Mock(), _disconnected=Mock())
        self.manager = Accounts(self.bridge)
        self.bridge.accounts = self.manager
        self.manager.index['desktopExecutable'] = '/fixture/Desktop'
        self.app = Mock()
        self.app.executable = Path('/fixture/Desktop')
        self.manager.wait_stopped = Mock()
        self.manager.wait_ready = Mock()
        self.original = self.manager.snapshot_files()

    def tearDown(self):
        wait_for_account_worker(self)
        self.tmp.cleanup()

    def api(self, **changes):
        return self.manager.add_api({'name':'Test API','baseUrl':'https://fixture.invalid/v1',
                                    'apiKey':'secret-fixture','model':'test-model', **changes})['accounts'][-1]

    def switch(self, row, **values):
        value={'id':row['id'],'requestId':str(uuid.uuid4()),'confirmed':True,'tasksConfirmed':True,**values}
        with patch('bridge.features.accounts.accounts.DesktopApp',return_value=self.app):
            self.manager.switch(value)
        return value

    def test_scan_desktop_action_finds_store_gui_with_separate_cached_cli(self):
        runtime = self.root/'Local/OpenAI/Codex/bin/version/codex.exe'
        runtime.parent.mkdir(parents=True); runtime.write_bytes(b'MZ-runtime'); runtime.chmod(0o700)
        gui = self.root/'Store/OpenAI.Codex_1/app/ChatGPT.exe'
        gui.parent.mkdir(parents=True); gui.write_bytes(b'MZ-desktop'); gui.chmod(0o700)
        self.bridge.catalog_reader.executable = runtime
        environment = {'LOCALAPPDATA': str(self.root/'Local'), 'APPDATA': str(self.root/'Roaming'),
                       'ProgramFiles': str(self.root/'Program Files'), 'USERPROFILE': str(self.home)}
        inventory = {'packages': [{'Name': 'OpenAI.Codex', 'InstallLocation': str(gui.parent.parent)}]}
        with patch('bridge.clients.desktop_app.sys.platform', 'win32'), patch.dict(os.environ, environment, clear=True), \
                patch('bridge.clients.discovery.windows_installations', return_value=inventory) as scan:
            result = self.manager.control({'action': 'scanDesktop'})
        scan.assert_called_once_with()
        self.assertEqual(result['desktopExecutable'], str(gui.resolve()))
        self.assertEqual(json.loads((self.manager.root/'index.json').read_text())['desktopExecutable'], str(gui.resolve()))
        self.assertEqual(self.manager.snapshot_files(), self.original)

    def prepare(self, row, before):
        folder=self.manager.root/'prepared';folder.mkdir(exist_ok=True)
        private_bytes(folder/'config.toml',b'model_provider="bridge_api"\nmodel="test-model"\n')
        return folder

    def test_chat_catalog_and_account_details_use_the_same_selected_upstream(self):
        row=self.api(name='Gemini access');self.manager.mark_active(row['id'])
        self.manager.info.entries={}
        with patch('bridge.features.accounts.accounts.model_ids', return_value=['gemini-flash','gemini-pro']) as upstream:
            details=self.manager.api_models(row, refresh=True)
            chat=self.manager.current_models()
        self.assertEqual(chat['models'],details)
        self.assertEqual(chat['modelSource'],'api');self.assertEqual(chat['modelAccessName'],'Gemini access')
        self.assertEqual(upstream.call_count,1)
        self.assertNotIn('secret-fixture',json.dumps(chat))

    def test_changed_live_api_credentials_do_not_query_saved_account_models(self):
        row=self.api();self.manager.mark_active(row['id'])
        private_json(self.home/'auth.json',{'OPENAI_API_KEY':'changed-fixture-key'})
        with patch('bridge.features.accounts.accounts.model_ids') as upstream:
            self.assertIsNone(self.manager.current_models(refresh=True))
        upstream.assert_not_called()

    def test_account_switch_invalidates_split_model_catalog(self):
        from bridge.clients.codex.catalog import Catalog
        reader = Catalog(self.home, executable=self.root/'runtime')
        self.bridge.catalog_reader = reader
        raw = {'models': [{'model':'gpt-6-astra'}], 'modelSource':'api'}
        with patch.object(reader, '_fetch', side_effect=lambda *args: raw):
            self.assertEqual(reader.get_kind('models', self.home)['models'][0]['efforts'], [])
            raw = {'models': [{'model':'gpt-6-astra', 'supportedReasoningEfforts':[{'reasoningEffort':'low'}]}], 'modelSource':'codex'}
            self.manager.invalidate()
            result = reader.get_kind('models', self.home)
        self.assertEqual(result['modelSource'], 'codex')
        self.assertEqual(result['models'][0]['efforts'], ['low'])

    def test_metadata_never_returns_secrets_and_edit_keeps_key(self):
        row=self.api()
        self.assertNotIn('secret-fixture',json.dumps(self.manager.desktop_status()))
        self.assertNotIn('desktopExecutable',self.manager.public())
        self.api(id=row['id'],apiKey='',name='Renamed',model='other-model')
        self.assertEqual(len(self.manager.public()['accounts']),1)
        self.assertEqual(json.loads((self.manager.directory(row['id'])/'api.json').read_text())['key'],'secret-fixture')
        if os.name!='nt':
            self.assertEqual((self.manager.directory(row['id'])/'api.json').stat().st_mode&0o777,0o600)
        self.assertEqual(self.manager.snapshot_files(),self.original)

    def test_rejects_bad_addresses_keys_ids_and_active_edit(self):
        for url in ['http://remote.invalid','https://key@api.example/v1','https://api.example/?key=private','file:///tmp/a']:
            with self.assertRaises(ValueError):self.api(baseUrl=url)
        with self.assertRaises(ValueError):self.api(apiKey='x\ny')
        with self.assertRaises(ValueError):self.manager.directory('../escape')
        row=self.api();self.manager.mark_active(row['id'])
        with self.assertRaises(ValueError):self.api(id=row['id'])
        with self.assertRaises(ValueError):self.manager.control({'action':'delete','id':row['id']})

    def test_success_preserves_history_and_duplicate_request_never_restarts(self):
        row=self.api();self.manager.prepare=self.prepare
        value=self.switch(row);wait_for_account_worker(self)
        self.assertEqual(self.manager.state['phase'],'complete')
        self.assertEqual(self.manager.public()['activeId'],row['id'])
        self.assertFalse((self.home/'auth.json').exists())
        self.assertEqual((self.home/'history.jsonl').read_text(),'preserve history')
        self.assertFalse((self.manager.root/'rollback.json').exists())
        self.assertFalse((self.manager.root/'prepared').exists())
        self.app.start.assert_called_once();self.manager.switch(value);self.app.start.assert_called_once()
        other=self.api(name='Second')
        with self.assertRaises(ValueError):self.manager.switch({**value,'id':other['id']})

    def test_failed_verification_restores_exact_original(self):
        row=self.api();self.manager.prepare=self.prepare
        original_restore=self.manager.restore_files
        def restore(snapshot):
            self.assertFalse(self.bridge.account.lock.acquire(blocking=False))
            original_restore(snapshot)
        self.manager.restore_files=restore
        self.manager.wait_ready=Mock(side_effect=[ValueError('fixture failure'),None])
        self.switch(row);wait_for_account_worker(self)
        self.assertEqual(self.manager.state['phase'],'restored')
        self.assertEqual(self.manager.snapshot_files(),self.original)
        self.assertIsNone(self.manager.index['activeId'])
        self.assertNotIn('fixture failure',json.dumps(self.manager.public()))

    def test_switch_and_failed_switch_do_not_load_saved_chats_or_query_models(self):
        from bridge.features.notifications.channels import read_json
        for failure in (False, True):
            with self.subTest(failure=failure):
                row=self.api(name='Target '+str(failure));self.manager.prepare=self.prepare
                self.bridge.store=Mock()
                self.bridge.store.list.side_effect=AssertionError('switch must not enumerate saved chats')
                self.bridge.store.history.side_effect=AssertionError('switch must not read saved history')
                self.bridge.catalog_reader.get_kind=Mock(side_effect=AssertionError('switch must not load a model catalog'))
                original=self.manager.snapshot_files();phases=[];events=[]
                phase=Accounts.phase.__get__(self.manager)
                def record_phase(name,**values):
                    phases.append(name);phase(name,**values)
                self.manager.phase=record_phase
                self.app.stop.side_effect=lambda **kwargs: events.append('stop')
                def start():
                    backup=read_json(self.manager.root/'rollback.json',{})
                    self.assertNotIn('threads',backup)
                    self.assertNotIn('threadsApplying',backup)
                    events.append('start')
                self.app.start.side_effect=start
                self.manager.wait_ready=Mock(side_effect=[ValueError('fixture failure'),None] if failure else None)
                with patch.object(self.manager,'api_models',side_effect=AssertionError('switch must not request upstream models')), patch('bridge.features.sessions.access.ThreadAccessRPC') as runtime:
                    self.switch(row);wait_for_account_worker(self)
                self.assertEqual(self.manager.state['phase'],'restored' if failure else 'complete')
                self.assertEqual(events,['stop','start','stop','start'] if failure else ['stop','start'])
                self.assertTrue(all(call.kwargs == {'provider': 'codex'} for call in self.app.stop.call_args_list))
                self.assertNotIn('migrating',phases)
                self.bridge.store.list.assert_not_called();self.bridge.store.history.assert_not_called()
                self.bridge.catalog_reader.get_kind.assert_not_called();runtime.assert_not_called()
                if failure:self.assertEqual(self.manager.snapshot_files(),original)
                self.assertFalse((self.manager.root/'rollback.json').exists())
                self.manager.phase=phase

    def test_restart_during_thread_migration_requires_recovery(self):
        self.manager.phase('migrating',completed=1,total=2)
        restarted=Accounts(self.bridge)
        self.assertEqual(restarted.state['phase'],'interrupted')
        with self.assertRaises(ValueError):restarted.check_ready()

    def test_manual_recovery_restores_saved_thread_routes_before_relaunch(self):
        records=[{'id':'old','provider':'original','model':'old-model','effort':'max'}]
        private_json(self.manager.root/'rollback.json',{'files':self.original,'activeId':None,
                     'desktopExecutable':'/fixture/Desktop','threads':records,'threadsApplying':True})
        self.manager.state={'phase':'interrupted'}
        events=[]
        self.app.start.side_effect=lambda: events.append('start')
        def restore(saved):
            self.assertEqual(saved,records);self.assertEqual(self.manager.snapshot_files(),self.original)
            events.append('restore')
        with patch('bridge.features.accounts.accounts.DesktopApp',return_value=self.app), patch('bridge.features.sessions.access.ThreadAccess') as migration:
            migration.return_value.restore.side_effect=restore
            self.manager.recover({'confirmed':True});wait_for_account_worker(self)
        self.assertEqual(events,['restore','start']);self.assertEqual(self.manager.state['phase'],'restored')

    def test_failed_recovery_stays_blocked_across_gateway_restart(self):
        row=self.api();self.manager.prepare=self.prepare
        self.manager.wait_ready=Mock(side_effect=ValueError('failed'))
        self.switch(row);wait_for_account_worker(self)
        self.assertEqual(self.manager.state['phase'],'interrupted')
        restarted=Accounts(self.bridge)
        with self.assertRaises(ValueError):restarted.check_ready()
        self.assertTrue((self.manager.root/'rollback.json').exists())
        self.manager.wait_ready=Mock()
        original_restore=self.manager.restore_files
        def restore(snapshot):
            self.assertFalse(self.bridge.account.lock.acquire(blocking=False))
            original_restore(snapshot)
        self.manager.restore_files=restore
        with patch('bridge.features.accounts.accounts.DesktopApp',return_value=self.app):
            self.manager.recover({'confirmed':True});wait_for_account_worker(self)
        self.assertEqual(self.manager.state['phase'],'restored')
        self.assertEqual(self.manager.snapshot_files(),self.original)

    def test_busy_and_queued_tasks_block_before_shutdown(self):
        row=self.api()
        for status in ['queued','unknown']:
            self.bridge.submissions={'one':{'status':status}}
            with self.assertRaises(ValueError):self.switch(row)
        self.bridge.submissions={};self.bridge.live={'one':SimpleNamespace(view=lambda:{'status':'active','connected':True,'title':'Busy fixture'})}
        with self.assertRaises(ValueError):self.switch(row)
        self.app.stop.assert_not_called()

    def test_disconnected_active_history_and_connected_error_do_not_block(self):
        self.bridge.live={
            'old':SimpleNamespace(view=lambda:{'status':'active','connected':False}),
            'failed':SimpleNamespace(view=lambda:{'status':'systemError','connected':True})}
        self.manager.idle()

    def test_real_pending_request_is_a_blocker_even_if_runtime_is_idle(self):
        self.bridge.live={'waiting':SimpleNamespace(view=lambda:{'status':'idle','connected':True,
                                          'title':'Approval fixture','requests':[{'id':'approval'}]})}
        with self.assertRaisesRegex(ValueError,'Approval fixture'):self.manager.idle()

    def test_mutations_and_second_switch_are_blocked_during_switch(self):
        row=self.api();self.manager.state={'phase':'verifying'}
        class Target:
            accounts=self.manager
            @operation
            def send(self):raise AssertionError('must not execute')
        with self.assertRaises(ValueError):Target().send()
        with self.assertRaises(ValueError):self.switch(row)
        with self.assertRaises(ValueError):self.api()
        self.assertEqual(self.manager.snapshot_files(),self.original)

    def test_switch_requires_explicit_restart_and_task_confirmation(self):
        row=self.api()
        for flag in ['confirmed','tasksConfirmed']:
            with self.assertRaises(ValueError):self.switch(row,**{flag:False})
        self.app.stop.assert_not_called()

    def test_native_exit_edits_are_retained_before_switch_and_rollback(self):
        row=self.api();self.manager.prepare=Mock(side_effect=self.prepare)
        def stop(**kwargs):
            if self.app.stop.call_count==1:
                private_bytes(self.home/'config.toml',b'# saved during exit\n')
        self.app.stop.side_effect=stop
        self.manager.wait_ready=Mock(side_effect=[ValueError(),None])
        self.switch(row);wait_for_account_worker(self)
        self.assertEqual((self.home/'config.toml').read_bytes(),b'# saved during exit\n')
        self.assertEqual(self.manager.prepare.call_count,2)

    def test_refresh_token_is_saved_before_leaving_active_account(self):
        identifier=uuid.uuid4().hex
        self.manager.index.update(activeId=identifier,accounts=[{'id':identifier,'kind':'chatgpt','name':'Official','accountId':'original','tokenOwner':['original','fixture-user','fixture@example.test']}])
        token = 'e30.'+base64.urlsafe_b64encode(json.dumps({'sub':'fixture-user','email':'fixture@example.test'}).encode()).decode().rstrip('=')+'.fixture'
        latest=json.dumps({'tokens':{'account_id':'original','refresh_token':'rotated-fixture','id_token':token}}).encode()
        private_bytes(self.home/'auth.json',latest)
        self.manager.retain_current(self.manager.snapshot_files())
        self.assertEqual((self.manager.directory(identifier)/'auth.json').read_bytes(),latest)

    def test_api_switch_allows_native_openai_and_bridge_provider_chats(self):
        row=self.api();self.manager.mark_active(row['id']);self.bridge.host='local'
        self.bridge.ipc.request.return_value={'result':{'ok':True}}
        for provider in ('openai','bridge_api'):
            session=SimpleNamespace(id='fixture',owner='desktop',view=lambda:{'provider':provider})
            self.assertEqual(Bridge._call(self.bridge,session,'thread-follower-start-turn',{}),{'ok':True})
        self.assertEqual(self.bridge.ipc.request.call_count,2)

    def test_old_provider_is_delegated_to_original_desktop_owner_after_switch(self):
        row=self.api();self.manager.mark_active(row['id'])
        self.bridge.host='local'
        self.bridge.ipc.request.return_value={'result':{'ok':True}}
        session=SimpleNamespace(id='fixture',owner='original-desktop',view=lambda:{'provider':'other-custom'})
        self.assertEqual(Bridge._call(self.bridge,session,'thread-follower-start-turn',{}), {'ok':True})
        self.assertEqual(self.bridge.ipc.request.call_args.kwargs['target'], 'original-desktop')

    def test_preparation_failure_leaves_live_files_and_gui_untouched(self):
        row=self.api();self.manager.prepare=Mock(side_effect=ValueError('private upstream error'))
        self.switch(row);wait_for_account_worker(self)
        self.assertEqual(self.manager.state['phase'],'failed')
        self.app.stop.assert_not_called()
        self.assertEqual(self.manager.snapshot_files(),self.original)

    def test_official_preparation_keeps_saved_api_chats_on_official_auth(self):
        row={'id':uuid.uuid4().hex,'kind':'chatgpt','email':'fixture@example.test'}
        saved=b'{"tokens":{"account_id":"official-fixture"}}'
        private_bytes(self.manager.directory(row['id'])/'auth.json',saved)
        rpc=Mock()
        rpc.request.side_effect=lambda method, params: {
            'config/read':{'config':{'model_provider':'bridge_api'}},
            'config/batchWrite':{'status':'ok'},
            'account/read':{'account':{'type':'chatgpt','email':row['email']}}
        }[method]
        with patch('bridge.features.accounts.accounts.ManagedRPC') as runtime:
            runtime.return_value.__enter__.return_value=rpc
            prepared=self.manager.prepare(row,self.original)
        edits=next(call.args[1]['edits'] for call in rpc.request.call_args_list if call.args[0]=='config/batchWrite')
        changes={edit['keyPath']:edit['value'] for edit in edits}
        alias=changes['model_providers.bridge_api']
        self.assertIsInstance(alias,dict,'saved bridge_api chats still need a provider definition')
        self.assertEqual(alias['name'],'OpenAI')
        self.assertTrue(alias['requires_openai_auth'])
        for stale in ('base_url','env_key','experimental_bearer_token','http_headers','env_http_headers'):
            self.assertNotIn(stale,alias)
        self.assertEqual(changes['model_provider'],'openai')
        self.assertIsNone(changes['openai_base_url'])
        self.assertIsNone(changes['model_providers.openai'])
        self.assertEqual((prepared/'auth.json').read_bytes(),saved)
        self.assertEqual(self.manager.snapshot_files(),self.original)
        self.assertTrue(all(not call.args[0].startswith(('thread/','turn/')) for call in rpc.request.call_args_list))

    @unittest.skipIf(os.name=='nt','POSIX symlink')
    def test_live_symlinks_are_not_overwritten(self):
        (self.home/'auth.json').unlink();(self.home/'auth.json').symlink_to(self.home/'history.jsonl')
        with self.assertRaises(ValueError):self.manager.snapshot_files()
        with self.assertRaises(ValueError):private_bytes(self.home/'auth.json',b'bad')
        self.assertEqual((self.home/'history.jsonl').read_text(),'preserve history')


class DesktopAppTests(unittest.TestCase):
    def test_windows_native_scan_still_rejects_runtime_as_desktop(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            runtime = Path(folder)/'bin/version/codex.exe'
            runtime.parent.mkdir(parents=True); runtime.write_bytes(b'MZ-runtime'); runtime.chmod(0o700)
            with patch('bridge.clients.desktop_app.sys.platform', 'win32'), \
                    patch('bridge.clients.discovery.discover_clients', return_value={
                        'codex': {'installed': True, 'executable': str(runtime)}}):
                with self.assertRaisesRegex(ValueError, '命令行运行时'):
                    DesktopApp.scan(runtime, folder)

    def test_runtime_and_script_cannot_be_used_as_gui(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            path=Path(folder)/'launcher';path.write_bytes(b'#!/bin/sh\n');path.chmod(0o700)
            with self.assertRaises(ValueError):DesktopApp(path,folder).validate(path)
            with self.assertRaises(ValueError):DesktopApp(path,folder).validate(Path(folder)/'runtime')

    def test_macos_relaunch_uses_launch_services_and_preserves_codex_home(self):
        # This launch fixture models macOS paths without resolving them on the host OS.
        app = DesktopApp.__new__(DesktopApp)
        app.executable = PurePosixPath('/Applications/Fixture Codex.app/Contents/MacOS/Fixture')
        app.home = PurePosixPath('/fixture/codex home')
        with patch('bridge.clients.desktop_app.sys.platform', 'darwin'), patch('bridge.platforms.macos.desktop.subprocess.run') as run, patch('bridge.platforms.macos.desktop.subprocess.Popen') as direct:
            app.start()
        args = run.call_args.args[0]
        self.assertEqual(args, ['/usr/bin/open', '-g', '-a', '/Applications/Fixture Codex.app', '--env', 'CODEX_HOME=/fixture/codex home'])
        self.assertTrue(run.call_args.kwargs['check'])
        direct.assert_not_called()

    def test_app_bundle_input_resolves_its_actual_executable(self):
        import plistlib
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            bundle=Path(folder)/'ChatGPT.app';(bundle/'Contents/MacOS').mkdir(parents=True)
            (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'ActualDesktop'}))
            path=bundle/'Contents/MacOS/ActualDesktop';path.write_bytes(b'\xcf\xfa');path.chmod(0o700)
            app=DesktopApp(bundle,folder);app.validate(Path(folder)/'runtime')
            self.assertEqual(app.executable,path.resolve())
            app.validate(None)
            with patch('bridge.clients.desktop_app.DesktopApp.discover', return_value=str(path)):
                self.assertEqual(DesktopApp.scan(None,folder),str(path.resolve()))

    def test_macos_bundle_discovery_uses_plist_executable(self):
        import plistlib
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            bundle=Path(folder)/'Codex.app';(bundle/'Contents/MacOS').mkdir(parents=True)
            runtime=bundle/'Contents/Resources/codex-cli/bin/codex'
            (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'ActualGUI'}))
            executable=bundle/'Contents/MacOS/ActualGUI';executable.touch()
            self.assertEqual(DesktopApp.discover(runtime),str(executable))

    def test_linux_runtime_is_never_discovered_as_gui(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            runtime=Path(folder)/'resources/codex';runtime.parent.mkdir();runtime.touch()
            with patch('bridge.clients.desktop_app.sys.platform','linux'):
                self.assertEqual(DesktopApp.discover(runtime),'')
                gui=Path(folder)/'chatgpt';gui.touch()
                self.assertEqual(DesktopApp.discover(runtime),str(gui))

class EnrollmentTests(unittest.TestCase):
    setUp = AccountsTests.setUp
    tearDown = AccountsTests.tearDown

    def test_login_success_keeps_tokens_private_and_never_touches_active_home(self):
        manager=self.manager; finished=threading.Event()
        class RPC:
            def __init__(self,home,runtime):self.home=home
            def __enter__(self):return self
            def __exit__(self,*args):finished.set()
            def request(self,method,params):
                if method=='account/login/start':
                    return {'loginId':'fixture','authUrl':'https://auth.openai.com/authorize?state=fixture'}
                if method=='account/read':return {'account':{'type':'chatgpt','email':'fixture@example.test','planType':'pro'}}
                raise AssertionError(method)
            def login_finished(self,cancel):
                token='e30.'+base64.urlsafe_b64encode(json.dumps({'sub':'fixture-user','email':'fixture@example.test'}).encode()).decode().rstrip('=')+'.fixture'
                private_json(self.home/'auth.json',{'tokens':{'account_id':'fixture-id','refresh_token':'never-display','id_token':token}})
                return True
        with patch('bridge.features.accounts.accounts.ManagedRPC',RPC):
            manager.login({'name':'Official'})
            self.assertTrue(finished.wait(2))
        self.assertEqual(manager.desktop_status()['enrollment']['phase'],'complete')
        self.assertEqual(manager.public()['accounts'][0]['email'],'fixture@example.test')
        self.assertNotIn('never-display',json.dumps(manager.desktop_status()))
        self.assertNotIn('authUrl',json.dumps(manager.public()))
        self.assertEqual(manager.snapshot_files(),self.original)

    def test_cancelling_login_does_not_publish_or_leave_credentials(self):
        entered=threading.Event();finished=threading.Event();manager=self.manager
        class RPC:
            def __init__(self,home,runtime):self.home=home
            def __enter__(self):return self
            def __exit__(self,*args):finished.set()
            def request(self,method,params):
                if method=='account/login/start':return {'loginId':'fixture','authUrl':'https://auth.openai.com/authorize'}
                if method=='account/login/cancel':return {}
                raise AssertionError(method)
            def login_finished(self,cancel):
                entered.set();cancel.wait(2);return False
        with patch('bridge.features.accounts.accounts.ManagedRPC',RPC):
            manager.login({'name':'Cancel'})
            self.assertTrue(entered.wait(2))
            folder=manager.directory(manager.enrollment['id'])
            manager.control({'action':'cancelLogin'})
            self.assertTrue(finished.wait(2))
            # The completion signal closes RPC; wait for the enrollment cleanup.
            import time
            end=time.monotonic()+2
            while folder.exists() and time.monotonic()<end:time.sleep(.01)
        self.assertFalse(folder.exists())
        self.assertEqual(manager.public()['accounts'],[])
