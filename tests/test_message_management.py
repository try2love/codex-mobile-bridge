import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge, LiveSession
from bridge.features.endpoints.wsl import WslEntry, Session

ROOT = Path(__file__).resolve().parents[1]


class MessageManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.thread = str(uuid.uuid4())
        self.ids = [str(uuid.uuid4()) for _ in range(5)]

    def native(self):
        b = Bridge.__new__(Bridge)
        b.host = 'local'; b.accounts = None; b.question_skips = None
        b.lock = threading.RLock(); b.submit_lock = threading.Lock(); b.goal_lock = threading.RLock()
        b.live = {self.thread: LiveSession(self.thread)}; b.goal_commands = {}
        b.ledger_path = Path(self.temp.name)/'submissions.json'
        b.submissions = {self.thread+':'+i: {'text': i, 'status': 'queued', 'mode': 'queue', 'at': n}
                         for n, i in enumerate(self.ids[:3])}
        b._management_identity = Mock(return_value='account-a')
        b.session = Mock(side_effect=AssertionError('Must not attach/read saved history'))
        b._dispatch = Mock(side_effect=lambda session,key,entry: b.submissions[key].update(status='accepted'))
        b.live[self.thread].connected = True
        b.live[self.thread].state = {'threadRuntimeStatus': {'type': 'idle'}}
        return b

    def wsl(self):
        b = WslEntry.__new__(WslEntry)
        b.host='wsl:fixture'; b.lock=threading.RLock(); b.write_lock=threading.RLock()
        b.closed=threading.Event(); b._draft_identity_pending=set(); b.live={self.thread:Session(self.thread)}
        b.receipts_path=Path(self.temp.name)/'operations.json'
        b.receipts={self.thread+':send:'+i: {'entry':{'text':i,'mode':'queue'},'result':{'id':i,'status':'queued'}} for i in self.ids[:3]}
        b._management_identity=Mock(return_value='account-a')
        b.session=Mock(side_effect=lambda identifier,attach=False,**options: b.live[identifier])
        b._target=Mock(return_value=(None,{'status':{'type':'idle'}}))
        def send(identifier,text,submission_id,**options):
            b.receipts[identifier+':send:'+submission_id]['result']['status']='accepted'
        b._send=Mock(side_effect=send)
        return b

    def action(self,b,action,**fields):
        return b.manage_messages(self.thread, {'action':action,'version':b.message_management(self.thread)['version'],
                                              'expectedAccount':'account-a',**fields})

    def test_receipts_are_exact_local_only_and_unknown_never_replayed(self):
        b=self.native();key=self.thread+':'+self.ids[0];b.submissions[key]['status']='unknown'
        result=b.message_management(self.thread,[self.ids[0],self.ids[4]])
        self.assertEqual([r['status'] for r in result['receipts']],['unknown','notFound'])
        self.assertEqual(result['unknown'][0]['text'],self.ids[0]);b.session.assert_not_called()
        self.action(b,'ignore',id=self.ids[0]);b._dispatch.assert_not_called()
        self.assertEqual(b.submissions[key]['status'],'ignored')
        with self.assertRaises(ValueError):self.action(b,'ignore',id=self.ids[4])
        self.assertNotIn(self.thread+':'+self.ids[4],b.submissions)

    def test_native_reorder_persists_actual_dispatch_and_stale_snapshot_cannot_send(self):
        b=self.native();first=self.thread+':'+self.ids[0];old=copy.deepcopy(b.submissions[first])
        request={'action':'reorder','version':b.message_management(self.thread)['version'], 'expectedAccount':'account-a','ids':list(reversed(self.ids[:3]))}
        b.manage_messages(self.thread,request)
        with self.assertRaises(ValueError): b.manage_messages(self.thread,request)
        b.submissions=json.loads(b.ledger_path.read_text(encoding='utf-8')) # restart ledger
        self.assertEqual(list(b.submissions)[0],self.thread+':'+self.ids[2])
        b._send_queued(b.live[self.thread],first,old);b._dispatch.assert_not_called()
        head=next(iter(b.submissions));b._send_queued(b.live[self.thread],head,b.submissions[head])
        self.assertEqual(b._dispatch.call_args.args[1],head)

    def test_withdraw_preserves_ledger_fields_and_stale_account_rejected(self):
        b=self.native();key=self.thread+':'+self.ids[0]
        b.submissions[key].update(skills=['s'],attachments=['upload'],attachmentNames=['report.txt'],workMode='plan')
        before=b.message_management(self.thread)
        body={'action':'withdraw','version':before['version'],'expectedAccount':'account-b','id':self.ids[0]}
        with self.assertRaises(ValueError):b.manage_messages(self.thread,body)
        body['expectedAccount']='account-a';result=b.manage_messages(self.thread,body)
        self.assertNotIn('editor',result)
        self.assertEqual(b.submissions[key]['attachments'],['upload']);self.assertEqual(b.submissions[key]['workMode'],'plan')
        self.assertEqual(b.submissions[key]['status'],'cancelled')
        with self.assertRaises(ValueError):b.manage_messages(self.thread,body)
        b._send_queued(b.live[self.thread],key,{});b._dispatch.assert_not_called()

    def test_hidden_goal_slots_and_other_thread_unchanged(self):
        b=self.native();hidden=self.thread+':'+self.ids[1];b.submissions[hidden]['goalActivation']={'objective':'goal'}
        other=str(uuid.uuid4())+':'+self.ids[4];b.submissions[other]={'status':'queued','text':'other'}
        self.action(b,'reorder',ids=[self.ids[2],self.ids[0]])
        self.assertEqual(list(b.submissions),[self.thread+':'+self.ids[2],hidden,self.thread+':'+self.ids[0],other])
        self.assertEqual(b.submissions[other]['text'],'other')

    def test_wsl_reorder_persisted_and_dispatch_locks_head(self):
        b=self.wsl();self.action(b,'reorder',ids=list(reversed(self.ids[:3])))
        b.receipts=json.loads(b.receipts_path.read_text(encoding='utf-8'));b._dispatch_queued(self.thread)
        self.assertEqual(b._send.call_args.args[2],self.ids[2])
        self.action(b,'withdraw',id=self.ids[1]);b._dispatch_queued(self.thread)
        self.assertEqual(b._send.call_args.args[2],self.ids[0])

    def test_reorder_waits_for_dispatch_and_rejects_changed_generation(self):
        b=self.native();session=b.live[self.thread]
        body={'action':'withdraw','id':self.ids[0],'expectedAccount':'account-a','version':b.message_management(self.thread)['version']}
        started=threading.Event();done=threading.Event();errors=[]
        def edit():
            started.set()
            try:b.manage_messages(self.thread,body)
            except ValueError as error:errors.append(error)
            finally:done.set()
        with session.action_lock:
            t=threading.Thread(target=edit);t.start();self.assertTrue(started.wait(1));self.assertFalse(done.wait(.05))
            key=self.thread+':'+self.ids[0];b._send_queued(session,key,b.submissions[key])
        t.join(2);self.assertFalse(t.is_alive());self.assertEqual(len(errors),1)
        self.assertEqual(b.submissions[key]['status'],'accepted')

    def test_aba_reorder_does_not_revalidate_old_token_and_cancelled_receipt_stays_small(self):
        b=self.native();old=b.message_management(self.thread)['version']
        self.action(b,'reorder',ids=list(reversed(self.ids[:3])))
        self.action(b,'reorder',ids=self.ids[:3])
        self.assertNotEqual(b.message_management(self.thread)['version'],old)
        with self.assertRaises(ValueError):
            b.manage_messages(self.thread,{'action':'withdraw','id':self.ids[0],'version':old,'expectedAccount':'account-a'})
        self.action(b,'withdraw',id=self.ids[0])
        b.submissions=json.loads(b.ledger_path.read_text(encoding='utf-8'))
        receipt=b.message_management(self.thread,[self.ids[0]])['receipts'][0]
        self.assertEqual(receipt['status'],'cancelled');self.assertNotIn('editor',receipt)

    def test_account_switch_invalidates_version_without_touching_queue(self):
        b=self.native();old=b.message_management(self.thread)
        b._management_identity.return_value='account-b'
        self.assertNotEqual(old['version'],b.message_management(self.thread)['version'])
        with self.assertRaises(ValueError):
            b.manage_messages(self.thread,{'action':'withdraw','version':old['version'],'expectedAccount':'account-a','id':self.ids[0]})
        self.assertEqual(len(b.message_management(self.thread)['queue']),3)

    def test_wsl_identity_query_is_cached_only_and_probe_source_is_read_only(self):
        b=self.wsl();del b._management_identity
        b.live[self.thread].state['modelProvider']='openai'
        b._draft_identity_cache={};b._draft_identity_pending=set();b.row={'distribution':'Fixture'}
        b.read=Mock(return_value={'identity':'opaque-target-owner'})
        self.assertEqual(b._management_identity(self.thread),'')
        b.read.assert_not_called()
        b._probe_draft_identity('openai')
        source=b.read.call_args.args[1];compile(source,'wsl-account-digest','exec')
        self.assertIn('codex_account_identity(home,',source)
        self.assertNotIn('turn/start',source);self.assertNotIn('thread/resume',source)
        identity=b.message_management(self.thread)['draftAccountIdentity'];self.assertEqual(len(identity),64)
        b.read.assert_called_once()
        b._draft_identity_cache['openai']=(time.monotonic()-31,identity)
        self.assertEqual(b._management_identity(self.thread),'');b.read.assert_called_once()

    def test_goal_create_receipt_is_not_misreported_as_absent_or_exposed_for_replay(self):
        b=self.native();identifier=self.ids[4]
        b.goal_commands[self.thread+':'+identifier]={'action':'create','state':'confirmed'}
        result=b.message_management(self.thread,[identifier])
        self.assertEqual(result['receipts'][0]['status'],'accepted')
        b.goal_commands[self.thread+':'+identifier]['state']='unknown'
        result=b.message_management(self.thread,[identifier]);self.assertEqual(result['receipts'][0]['status'],'unknown')
        self.assertNotIn(identifier,[r['id'] for r in result['unknown']])
        with self.assertRaises(ValueError):self.action(b,'ignore',id=identifier)

    def test_persistence_failure_restores_pre_mutation_memory(self):
        b=self.native();before=copy.deepcopy(b.submissions)
        b._save_ledger=Mock(side_effect=OSError('disk unavailable'))
        with self.assertRaises(OSError):self.action(b,'withdraw',id=self.ids[0])
        self.assertEqual(b.submissions,before)
        w=self.wsl();before=copy.deepcopy(w.receipts)
        with patch('bridge.features.endpoints.wsl.private_json',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.action(w,'withdraw',id=self.ids[0])
        self.assertEqual(w.receipts,before)

    def test_native_edit_retains_slot_metadata_restart_and_real_dispatch_uses_new_body(self):
        b=self.native();identifier=self.ids[1];key=self.thread+':'+identifier
        b.submissions[key].update(target={'owner':'same'},attachments=['upload'],workMode='plan',
                                  skills=[{'id':'skill','name':'Same skill','path':'/same/skill'}],permissions={'mode':'same'})
        original=copy.deepcopy(b.submissions[key]);order=list(b.submissions)
        edit_id=str(uuid.uuid4());before=b.message_management(self.thread)
        request={'action':'edit','id':identifier,'text':'edited正文','editId':edit_id,
                 'version':before['version'],'expectedAccount':'account-a'}
        result=b.manage_messages(self.thread,request)
        self.assertEqual(list(b.submissions),order)
        self.assertEqual({k:v for k,v in b.submissions[key].items() if k not in ('text','_editId','_managementRevision')},{k:v for k,v in original.items() if k != 'text'})
        with self.assertRaises(ValueError):b.manage_messages(self.thread,request)
        b.submissions=json.loads(b.ledger_path.read_text(encoding='utf-8'))
        self.assertEqual(b.message_management(self.thread,[identifier])['receipts'][0]['edit'],
                         {'id':edit_id,'text':'edited正文','status':'applied'})
        session=b.live[self.thread];session.runtime='desktop';session.state['latestModel']='same-model'
        b.uploads=SimpleNamespace(resolve=Mock(return_value=[]));b._call=Mock(return_value={})
        b._dispatch=Bridge._dispatch.__get__(b)
        # First queued slot is still first; dispatch both using pre-edit snapshots.
        first=self.thread+':'+self.ids[0];b._send_queued(session,first,b.submissions[first])
        b._send_queued(session,key,original)
        request_body=b._call.call_args.args[2]['turnStart']['request']
        self.assertEqual(request_body['input'][0]['text'],'edited正文')
        self.assertEqual(request_body['clientUserMessageId'],identifier)
        self.assertEqual(request_body['input'][1]['path'],'/same/skill')
        b.uploads.resolve.assert_called_with(self.thread,['upload'])
        self.assertEqual(b.message_management(self.thread,[identifier])['receipts'][0]['edit']['id'],edit_id)
        with self.assertRaises(ValueError):self.action(b,'edit',id=identifier,text='late',editId=str(uuid.uuid4()))

    def test_native_old_send_body_retry_cannot_overwrite_edited_queue(self):
        b=self.native();identifier=self.ids[0]
        self.action(b,'edit',id=identifier,text='new body',editId=str(uuid.uuid4()))
        b.uploads=SimpleNamespace(resolve=Mock(return_value=[]));b._target=Mock(return_value=b.live[self.thread])
        b._access=Mock(return_value={'canUseModes':True,'canSelectSkills':True});b._resolve_skills=Mock(return_value=[])
        with self.assertRaises(ValueError):b.send(self.thread,identifier,identifier,mode='queue')
        self.assertEqual(b.submissions[self.thread+':'+identifier]['text'],'new body');b._dispatch.assert_not_called()

    def test_wsl_edit_updates_fingerprint_keeps_slot_and_real_send_after_restart(self):
        b=self.wsl();identifier=self.ids[0];key=self.thread+':send:'+identifier
        order=list(b.receipts);edit_id=str(uuid.uuid4())
        b.receipts[key]['entry']['target']={'cwd':'/original','modelProvider':'openai'}
        b.receipts[key]['entry']['attachments']=[]
        b.receipts[key]['entry'].update(skills=[],workMode=None,goalActivation=None)
        self.action(b,'edit',id=identifier,text='new WSL正文',editId=edit_id)
        self.assertEqual(list(b.receipts),order)
        b.receipts=json.loads(b.receipts_path.read_text(encoding='utf-8'));b.data_dir=Path(self.temp.name)
        b.uploads=SimpleNamespace(resolve=Mock(return_value=[]));b._resolve_skills=Mock(return_value=[])
        b.row={'distribution':'Fixture','home':'/home/fixture/.codex'}
        b.read=Mock(return_value={'managed':False});b._managed_routes={};b.generation=0
        runtime=SimpleNamespace(request=Mock(return_value={'turn':{'id':'turn'}}))
        b._target=Mock(return_value=(runtime,{'status':{'type':'idle'},'cwd':'/original','modelProvider':'openai'}))
        b._subscribe=Mock();b._send=WslEntry._send.__get__(b)
        with self.assertRaises(ValueError):b._send(self.thread,identifier,identifier,mode='queue')
        runtime.request.assert_not_called()
        b._dispatch_queued(self.thread)
        b.read.assert_called_once()
        self.assertEqual(runtime.request.call_args.args[1]['input'][0]['text'],'new WSL正文')
        self.assertEqual(b.receipts[key]['result']['status'],'accepted')
        receipt=b.message_management(self.thread,[identifier])['receipts'][0]
        self.assertEqual(receipt['edit'],{'id':edit_id,'text':'new WSL正文','status':'applied'})
        with self.assertRaises(ValueError):self.action(b,'edit',id=identifier,text='late',editId=str(uuid.uuid4()))

    def test_edit_racing_dispatch_rejects_without_requeue_or_overwriting(self):
        b=self.native();session=b.live[self.thread];identifier=self.ids[0];key=self.thread+':'+identifier
        request={'action':'edit','id':identifier,'text':'too late','editId':str(uuid.uuid4()),
                 'version':b.message_management(self.thread)['version'],'expectedAccount':'account-a'}
        started=threading.Event();errors=[]
        def edit():
            started.set()
            try:b.manage_messages(self.thread,request)
            except ValueError as error:errors.append(error)
        with session.action_lock:
            worker=threading.Thread(target=edit);worker.start();self.assertTrue(started.wait(1))
            b._send_queued(session,key,b.submissions[key])
        worker.join(2);self.assertFalse(worker.is_alive());self.assertEqual(len(errors),1)
        self.assertEqual(b.submissions[key]['status'],'accepted');self.assertEqual(b.submissions[key]['text'],identifier)

    def test_remote_identity_get_deduplicates_missing_and_expired_digest_probes_without_attach(self):
        for expired in (False,True):
            with self.subTest(expired=expired):
                b=self.native();del b._management_identity
                b.host='ssh-fixture';b.cli_sessions=SimpleNamespace(alias='fixture',remote_home=Path('/remote/.codex'))
                b.live[self.thread].state['modelProvider']='openai'
                b._draft_remote_lock=threading.Lock();b._draft_remote_pending=set();b._draft_remote_cache={}
                if expired:b._draft_remote_cache[(b.host,'fixture','/remote/.codex','openai')]=(time.monotonic()-31,'old')
                with patch('bridge.app.service.threading.Thread') as thread:
                    first=b.message_management(self.thread);second=b.message_management(self.thread)
                    self.assertEqual(first['draftAccountIdentity'],'');self.assertTrue(first['identityPending'])
                    self.assertTrue(second['identityPending']);thread.assert_called_once();thread.return_value.start.assert_called_once()
                b.session.assert_not_called()
                w=self.wsl();del w._management_identity
                w.live[self.thread].state['modelProvider']='openai';w._draft_identity_cache={};w._draft_identity_pending=set()
                if expired:w._draft_identity_cache['openai']=(time.monotonic()-31,'old')
                with patch('bridge.features.endpoints.wsl.threading.Thread') as thread:
                    first=w.message_management(self.thread);second=w.message_management(self.thread)
                    self.assertEqual(first['draftAccountIdentity'],'');self.assertTrue(first['identityPending'])
                    self.assertTrue(second['identityPending']);thread.assert_called_once();thread.return_value.start.assert_called_once()
                w.session.assert_not_called();w._target.assert_not_called()

    def test_edit_rejects_unknown_hidden_and_non_body_fields(self):
        b=self.native();key=self.thread+':'+self.ids[0];b.submissions[key]['status']='unknown'
        with self.assertRaises(ValueError):self.action(b,'edit',id=self.ids[0],text='changed',editId=str(uuid.uuid4()))
        b.submissions[key]['status']='queued';b.submissions[key]['goalActivation']={'objective':'goal'}
        with self.assertRaises(ValueError):self.action(b,'edit',id=self.ids[0],text='changed',editId=str(uuid.uuid4()))
        b.submissions[key].pop('goalActivation')
        with self.assertRaises(ValueError):self.action(b,'edit',id=self.ids[0],text='changed',editId=str(uuid.uuid4()),workMode='default')
        self.assertEqual(b.submissions[key]['text'],self.ids[0])

    def test_wsl_dispatch_and_edit_serialize_and_dispatched_head_rejects_edit(self):
        b=self.wsl();identifier=self.ids[0];entered=threading.Event();release=threading.Event();done=threading.Event();errors=[]
        request={'action':'edit','id':identifier,'text':'too late','editId':str(uuid.uuid4()),
                 'version':b.message_management(self.thread)['version'],'expectedAccount':'account-a'}
        def target(*args):
            entered.set();self.assertTrue(release.wait(2));return None,{'status':{'type':'idle'}}
        b._target.side_effect=target
        dispatch=threading.Thread(target=lambda:b._dispatch_queued(self.thread));dispatch.start();self.assertTrue(entered.wait(1))
        def edit():
            try:b.manage_messages(self.thread,request)
            except ValueError as error:errors.append(error)
            finally:done.set()
        worker=threading.Thread(target=edit);worker.start();self.assertFalse(done.wait(.05));release.set()
        dispatch.join(2);worker.join(2);self.assertFalse(worker.is_alive());self.assertFalse(dispatch.is_alive())
        self.assertEqual(len(errors),1);self.assertEqual(b.receipts[self.thread+':send:'+identifier]['entry']['text'],identifier)
        self.assertEqual(b.receipts[self.thread+':send:'+identifier]['result']['status'],'accepted')

    def test_probe_completes_between_identity_snapshot_and_pending_flag_next_get_recovers(self):
        for b in (self.native(),self.wsl()):
            with self.subTest(engine=type(b).__name__):
                # Deterministically complete the probe after the response's initial
                # identity read but before its final pending-state read.
                b.host='remote-fixture'
                b._draft_remote_pending=set()
                b._draft_identity_pending=set()
                b._management_identity=Mock(side_effect=['','fresh-account','fresh-account'])
                pending=b.message_management(self.thread)
                self.assertEqual(pending['draftAccountIdentity'],'')
                self.assertTrue(pending['identityPending'])
                self.assertFalse(pending['capabilities']['edit'])
                ready=b.message_management(self.thread)
                self.assertEqual(ready['draftAccountIdentity'],'fresh-account')
                self.assertFalse(ready['identityPending'])
                self.assertTrue(ready['capabilities']['edit'])
                self.assertNotEqual(pending['version'],ready['version'])
                self.assertEqual(b._management_identity.call_args_list[1].kwargs,{})
                b.session.assert_not_called()

    def test_account_unavailable_blocks_capabilities_and_mutations(self):
        b=self.native();b._management_identity.return_value=''
        self.assertFalse(any(b.message_management(self.thread)['capabilities'].values()))
        with self.assertRaises(ValueError):self.action(b,'withdraw',id=self.ids[0])


if __name__=='__main__': unittest.main()
