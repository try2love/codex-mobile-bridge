"""Independent home identities share the existing native Codex session flow."""
import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

from bridge.app.service import Bridge
from bridge.clients.codex.ipc import IPCError
from bridge.features.endpoints.registry import Entries, local_entry_id
from tests.test_cli_bridge_integration import CliBridgeFixture

ROOT = Path(__file__).resolve().parents[1]


class ProfileRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.default = self.home('.codex')
        self.other = self.home('.codex-vscode')
        self.factory = Mock()
        self.registry = Entries(self.root/'bridge', self.default, self.factory)
        self.addCleanup(self.registry.close)

    def home(self, name):
        path = self.root/name; path.mkdir(); (path/'config.toml').write_text('model="test"\n')
        return path

    def test_scan_and_save_are_read_only_and_duplicate_aliases_are_rejected(self):
        result = self.registry.local_profiles(scan=True)
        self.assertIn(str(self.other), [r['home'] for r in result['candidates']])
        self.assertNotIn(str(self.default), [r['home'] for r in result['candidates']])
        with self.assertRaisesRegex(ValueError, '默认'): self.registry.save_local(str(self.default), 'Duplicate')
        alias = self.root/'alias'; alias.symlink_to(self.other, target_is_directory=True)
        result = self.registry.save_local(str(alias), 'IDE')
        self.assertEqual(result['entry'], local_entry_id(self.other))
        with self.assertRaisesRegex(ValueError, '已登记'): self.registry.save_local(str(self.other), 'Duplicate')
        self.factory.assert_not_called()
        again = Entries(self.root/'bridge', self.default, self.factory)
        self.addCleanup(again.close)
        self.assertEqual(again.hosts(), {result['entry']: {'label': 'IDE', 'kind': 'codex-local'}})
        self.assertTrue(again.public()['entries'][0]['enabled'])

    def test_replaced_directory_cannot_redirect_an_existing_entry(self):
        entry = self.registry.save_local(str(self.other), 'IDE')['entry']
        self.other.rename(self.root/'old-home'); self.home('.codex-vscode')
        with self.assertRaisesRegex(ValueError, '身份已变化'): self.registry.get(entry)
        self.factory.assert_not_called()

    def test_external_sqlite_directory_is_not_silently_read_from_another_profile(self):
        (self.other/'config.toml').write_text('sqlite_home='+json.dumps(str(self.default)))
        with self.assertRaisesRegex(ValueError, 'sqlite_home'): self.registry.save_local(str(self.other), 'IDE')


class IdeProfileTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db, db:
            db.execute("UPDATE threads SET source='vscode', originator='codex_vscode'")
        self.bridge.host = 'codex:'+'1'*32
        self.bridge.entry_label = 'IDE fixture'
        absent = IPCError('desktop absent'); absent.__cause__ = FileNotFoundError()
        self.bridge.ipc.connect.side_effect = absent

    def test_ide_source_does_not_appear_as_desktop_or_cli(self):
        self.assertEqual([r['id'] for r in self.bridge.store.list(source='ide')], [self.thread_id])
        self.assertEqual(self.bridge.store.list(source='desktop'), [])
        self.assertEqual(self.bridge.store.list(source='cli'), [])
        self.assertEqual(self.bridge.store.get(self.thread_id)['origin'], 'ide')
        self.assertEqual(self.bridge.store.available_sources(), ['ide'])
        rows = self.bridge.list(local_only=True)
        self.assertEqual(rows[0]['host'], self.bridge.host)
        self.assertEqual(rows[0]['hostLabel'], 'IDE fixture')
        self.cli.attach.assert_not_called()

    def test_explicit_headless_attach_keeps_identity_and_local_filesystem(self):
        session = self.bridge.activate(self.thread_id)
        self.assertTrue(session.connected)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.assertEqual(session.origin, 'ide')
        self.assertEqual(session.runtime, 'cli')
        self.assertEqual(self.bridge.native_host, 'local')
        project = self.bridge.register_project(str(self.home), host=self.bridge.host, kind='cli')
        self.assertEqual(project['host'], self.bridge.host)
        self.assertEqual(self.bridge.project_host(project['key']), self.bridge.host)
        self.assertEqual(self.bridge.project_environments()[0]['host'], self.bridge.host)

    def test_owner_timeout_allows_only_explicit_native_reconnect(self):
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner.side_effect = IPCError('桌面读取超时；尚未取得实时会话状态。')
        session = self.bridge.session(self.thread_id, attach=False)
        self.bridge._attach(session)
        self.cli.attach.assert_not_called()
        self.assertEqual(session.desktop_owner_probe, 'unknown')
        self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_other_owner_failures_cannot_bypass_identity_checks(self):
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner.side_effect = IPCError('现有 Codex App 连接与此会话不匹配，未连接')
        with self.assertRaises(IPCError): self.bridge.activate(self.thread_id)
        self.cli.attach.assert_not_called()

    def test_shared_windows_pipe_refuses_a_cloned_uuid(self):
        default_store = Mock(); default_store.home = self.home
        registry = Entries(self.home/'registry', self.home)
        self.addCleanup(registry.close)
        with self.assertRaisesRegex(IPCError, '同 ID'):
            registry.check_ipc_scope(self.bridge.host, self.thread_id, default_store)
        default_store.get.side_effect = KeyError(self.thread_id)
        registry.check_ipc_scope(self.bridge.host, self.thread_id, default_store)

    def test_native_owner_release_blocks_late_snapshots_and_stale_generations(self):
        session = self.bridge.session(self.thread_id, attach=False)
        session.origin='ide'; session.connected=True; session.owner='ide-owner'; session.runtime='desktop'
        session.state=self.native_state.copy(); session.state['hostId']='local'
        self.bridge.ipc.client_id='current'; self.bridge.ipc.follow=Mock(); self.cli.cli_connections.return_value=[]
        row=self.bridge.cli_connections()[0]
        self.bridge.ipc.client_id='replacement'
        with self.assertRaisesRegex(ValueError, '变化'): row['_release'](False)
        self.bridge.ipc.follow.assert_not_called()
        self.bridge.ipc.client_id='current'
        result=row['_release'](False)
        self.assertEqual(result['status'], 'detached')
        self.assertTrue(self.bridge.released_cli.contains(self.thread_id))
        self.assertFalse(session.connected)
        self.bridge._attach(session)
        self.cli.attach.assert_not_called()
        self.bridge.ipc.follow.assert_called_once_with(self.thread_id,'ide-owner',False,host='local')


class ProfileCloseTests(ProfileRegistryTests):
    def test_disabling_drains_accepted_writes_and_blocks_stale_targets(self):
        import threading
        from unittest.mock import patch
        target=Bridge(self.other,self.root/'gateway',host=local_entry_id(self.other),codex_bin=self.root/'absent')
        self.addCleanup(target.close)
        entered,finish,closed=threading.Event(),threading.Event(),threading.Event()
        target.store.get=Mock(return_value={'cwd':str(self.other)})
        def write(*args):
            entered.set();self.assertTrue(finish.wait(3));return {'ok':True}
        with patch('bridge.app.service.workspace_operation',side_effect=write) as native:
            worker=threading.Thread(target=lambda:target.workspace(str(uuid.uuid4()),'upload',{}))
            stopper=threading.Thread(target=lambda:(target.close(),closed.set()))
            worker.start();self.assertTrue(entered.wait(2));stopper.start()
            self.assertFalse(closed.wait(.05));finish.set();worker.join(3);stopper.join(3)
            self.assertTrue(closed.is_set())
            with self.assertRaisesRegex(ValueError,'已关闭'):
                target.workspace(str(uuid.uuid4()),'upload',{})
            with self.assertRaisesRegex(ValueError,'已关闭'):
                target.register_project(str(self.other),host=target.host,kind='cli')
            self.assertEqual(native.call_count,1)

class IndependentAccountsTests(ProfileRegistryTests):
    def setUp(self):
        super().setUp()
        self.bridge = Bridge(self.default, self.root/'accounts-gateway', codex_bin=self.root/'absent')
        self.addCleanup(self.bridge.close)
        self.entry = self.bridge.entries.save_local(str(self.other), '独立 Codex')['entry']
        self.target = self.bridge.account_target(self.entry)

    def test_accounts_are_private_to_the_selected_instance(self):
        payload = {'action':'addApi','name':'A','baseUrl':'https://fixture.invalid/v1',
                   'apiKey':'synthetic-key','model':'test-model'}
        result = self.bridge.accounts_control({**payload, 'entry':self.entry})
        identifier = result['accounts'][0]['id']
        self.assertEqual(result['scope'], 'profile')
        self.assertEqual(result['codexHome'], str(self.other))
        self.assertEqual(self.bridge.accounts.public()['accounts'], [])
        self.assertNotEqual(self.target.accounts.root, self.bridge.accounts.root)
        self.assertEqual(self.target.accounts.monitor.notification_data_dir,self.bridge.data_dir)
        self.assertNotEqual(self.target.accounts.monitor.ledger_path,self.bridge.accounts.monitor.ledger_path)
        with self.assertRaises(ValueError):
            self.bridge.accounts_control({'action':'rename','id':identifier,'name':'Cross-scope'})
        self.assertFalse((self.default/'auth.json').exists())
        self.assertIsNone(result['desktopExecutable'])
        with self.assertRaisesRegex(ValueError, '默认 Codex Desktop'):
            self.bridge.accounts_control({'entry':self.entry,'action':'scanDesktop'})

    def test_disabled_and_unknown_accounts_never_fall_back_to_default(self):
        self.bridge.entries.configure(self.entry, False)
        self.assertEqual(self.bridge.accounts_control({'entry':self.entry})['scope'],'profile')
        with self.assertRaises(KeyError): self.bridge.for_host(self.entry)
        with self.assertRaises(KeyError): self.bridge.account_target('codex:'+'f'*32)
        with self.assertRaises(ValueError): self.bridge.account_target('not-a-client')

    def test_profile_switch_keeps_default_desktop_out_of_runtime_discovery(self):
        from unittest.mock import patch
        from bridge.features.accounts.cli_sync import LocalCliSync
        with patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop:
            self.assertEqual(LocalCliSync(self.target)._codex_runtime(), (False, None))
            desktop.assert_not_called()

    def test_active_native_turn_blocks_profile_switch_before_signalling(self):
        from unittest.mock import patch
        from bridge.features.accounts.cli_sync import LocalCliSync
        self.target.cli_sessions.daemon_status=Mock(return_value='running')
        self.target.cli_sessions.client=Mock()
        self.target.cli_sessions.client.connect.return_value={'codexHome':str(self.other)}
        self.target.cli_sessions.client.loaded_threads.return_value=['busy']
        self.target.cli_sessions.client.request.return_value={'thread':{'id':'busy','status':{'type':'active'}}}
        with patch('bridge.clients.codex.cli_session._managed_cli',return_value=self.root/'codex'), \
             patch('bridge.platforms.codex_daemon_env.read_daemon_environment',return_value=({},('generation',))), \
             patch('bridge.platforms.codex_daemon_env.stop_daemon_gracefully') as stop:
            with self.assertRaisesRegex(ValueError,'正在运行'):
                LocalCliSync(self.target).prepare_profile_switch()
            stop.assert_not_called()

    def test_switch_waiting_for_account_read_does_not_hold_status_lock(self):
        import threading
        from unittest.mock import patch
        manager=self.target.accounts
        manager.add_api({'name':'Lock fixture','baseUrl':'https://fixture.invalid/v1','apiKey':'synthetic','model':'test'})
        row=manager.index['accounts'][-1]
        manager.info.current=Mock(return_value={'id':None})
        errors=[];started=threading.Event();attempted=threading.Event()
        lock=threading.Lock()
        class AccountLock:
            def __enter__(inner):
                attempted.set();lock.acquire()
            def __exit__(inner,*args): lock.release()
        self.target.account.lock=AccountLock()
        def change():
            started.set()
            try:
                manager.switch({'id':row['id'],'requestId':str(uuid.uuid4()),'confirmed':True,'tasksConfirmed':True})
            except Exception as error: errors.append(error)
        with patch('bridge.features.accounts.cli_sync.LocalCliSync') as sync:
            sync.return_value.preflight.return_value={'desktopRunning':False}
            sync.return_value.apply.return_value={'mutated':False}
            with lock:
                worker=threading.Thread(target=change);worker.start();self.assertTrue(started.wait(1));self.assertTrue(attempted.wait(1))
                acquired=manager.lock.acquire(timeout=.2)
                if acquired: manager.lock.release()
            worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertTrue(acquired,'status reader must be able to finish while switch waits for account I/O')
