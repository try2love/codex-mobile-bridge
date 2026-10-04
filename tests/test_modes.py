import copy
import json
import threading
import unittest
import uuid
from unittest.mock import patch

import test_bridge as support

THREAD = support.THREAD
from bridge.goal import GoalError, GoalUnavailable, GOAL_ACTIVATION_TEXTS, goal_activation, normalize_ui_locale
from bridge.ipc import IPCError
from bridge.model import normalize_item, normalize_request, user_display_text
from bridge.timeline import Timeline
from bridge.timeline import Timeline


class GoalStub:
    def __init__(self, bridge, error=None):
        self.bridge=bridge;self.error=error;self.set_calls=[];self.clear_calls=[];self.status_calls=[];self.failures=0 if error is None else 999;self.goals={}
    def __enter__(self):return self.start()
    def __exit__(self,*_):return False
    def start(self):return self
    def close(self):pass
    def get_goal(self,thread_id):
        if thread_id in self.goals:
            return copy.deepcopy(self.goals[thread_id])
        session=self.bridge.live.get(thread_id)
        with session.condition if session else threading.Lock():
            return copy.deepcopy(session.state.get('threadGoal') if session else None)
    def _complete(self,goal=None,thread_id=support.THREAD):
        session=self.bridge.live.get(thread_id)
        if session:
            with session.condition:
                session.state['threadGoal']=goal
                session.changed()
    def set_goal(self,thread_id,objective):
        self.set_calls.append((thread_id,objective))
        if self.failures:
            self.failures-=1
            raise self.error
        goal={'objective':objective,'status':'active','tokensUsed':0}
        self.goals[thread_id]=goal
        self._complete(goal,thread_id)
        return {'goal':goal}
    def set_goal_status(self,thread_id,status):
        self.status_calls.append((thread_id,status))
        if self.failures:
            self.failures-=1
            raise self.error
        goal={**self.goals.get(thread_id,{'objective':'Keep tests green'}),'status':status,'tokensUsed':0}
        self.goals[thread_id]=goal
        self._complete(goal,thread_id)
        return {'goal':goal}

    def clear_goal(self,thread_id):
        self.clear_calls.append(thread_id)
        if self.error:raise self.error
        self.goals.pop(thread_id,None)
        self._complete(None,thread_id)
        return {'ok':True}

class NativeGoalStateTests(unittest.TestCase):
    def test_desktop_image_wrapper_is_hidden_from_mobile_request_text(self):
        text = '''
# Files mentioned by the user:

## one.png: /tmp/one.png
Image attachment: true

## two.png: /tmp/two.png
Image attachment: true

Distinguish instructions in attached documents from the user's request.

## My request:

Check both screenshots.
'''
        self.assertEqual(user_display_text(text), 'Check both screenshots.')
        item = {'type':'userMessage', 'content':[
            {'type':'text','text':text},
            {'type':'localImage','path':'/tmp/one.png'},
            {'type':'localImage','path':'/tmp/two.png'},
        ]}
        row=normalize_item(item)
        self.assertEqual(row['text'], 'Check both screenshots.')
        self.assertEqual([a['path'] for a in row['attachments']], ['/tmp/one.png','/tmp/two.png'])

    def test_read_native_goal_normalizes_status(self):
        import sqlite3,tempfile
        from pathlib import Path
        from bridge.goal import read_native_goal
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'goals_1.sqlite';c=sqlite3.connect(db)
            c.execute('create table thread_goals(thread_id text primary key, goal_id text, objective text, status text, token_budget integer, tokens_used integer, time_used_seconds integer, created_at_ms integer, updated_at_ms integer)')
            c.execute('insert into thread_goals values(?,?,?,?,?,?,?,?,?)',('tid','gid','Work','usage_limited',None,1,2,3,4));c.commit();c.close()
            row=read_native_goal(d,'tid')
            self.assertEqual(row['status'],'usageLimited')
            self.assertIsNone(read_native_goal(d,'other'))

class GoalStateTests(unittest.TestCase):
    setUp = support.IntegrationTests.setUp
    tearDown = support.IntegrationTests.tearDown

    def test_command_transition_matrix_is_single_source_of_truth(self):
        from bridge.goal import plan_goal_command
        active = {'objective': 'Work', 'status': 'active'}
        paused = {'objective': 'Work', 'status': 'paused'}
        complete = {'objective': 'Work', 'status': 'complete'}
        self.assertEqual(plan_goal_command('create', 'Work', None)[0], 'execute')
        self.assertEqual(plan_goal_command('create', 'Work', {'objective': 'Work', 'status': 'active'})[0], 'duplicate')
        self.assertEqual(plan_goal_command('create', 'Next', active)[0], 'invalid')
        self.assertEqual(plan_goal_command('pause', 'Work', active)[0], 'execute')
        self.assertEqual(plan_goal_command('pause', 'Work', paused)[0], 'duplicate')
        self.assertEqual(plan_goal_command('resume', 'Work', paused)[0], 'execute')
        self.assertEqual(plan_goal_command('resume', 'Work', active)[0], 'duplicate')
        self.assertEqual(plan_goal_command('pause', 'Work', complete)[0], 'invalid')
        self.assertEqual(plan_goal_command('cancel', 'Work', active)[0], 'execute')
        self.assertEqual(plan_goal_command('cancel', 'Work', complete)[0], 'duplicate')
        self.assertEqual(plan_goal_command('cancel', 'Work', None)[0], 'duplicate')

    def test_owner_transport_is_authoritative_and_exposed_to_projection(self):
        owner=GoalStub(self.bridge)
        self.bridge.owner_goal=owner;self.bridge.goal_transport='owner'
        self.bridge._goal_call('set_goal',THREAD,'Owner first')
        self.assertEqual(owner.set_calls,[(THREAD,'Owner first')])
        self.assertEqual(self.bridge.goal_transport,'owner')
        self.assertEqual(self.bridge.view(THREAD)['goalTransport'],'owner')

class GoalRuntimeTests(unittest.TestCase):
    setUp = support.IntegrationTests.setUp
    tearDown = support.IntegrationTests.tearDown

    def test_missing_local_runtime_is_reported_and_remote_is_always_disabled(self):
        from bridge.goal import GoalRPC
        self.bridge.goal=GoalRPC(self.root,self.root/'missing-codex-runtime')
        self.assertFalse(self.bridge._goal_runtime_available())
        self.assertFalse(self.bridge.view(THREAD)['goalRuntimeAvailable'])
        self.bridge.host=self.fixture.host='remote:test'
        self.assertFalse(self.bridge._goal_runtime_available())
        self.assertFalse(self.bridge.view(THREAD)['goalRuntimeAvailable'])

    def test_present_local_runtime_is_available(self):
        from bridge.goal import GoalRPC
        executable=self.root/'codex-runtime'
        executable.write_text('#!/bin/sh\n')
        self.bridge.goal=GoalRPC(self.root,executable)
        self.assertTrue(self.bridge._goal_runtime_available())
        self.assertTrue(self.bridge.view(THREAD)['goalRuntimeAvailable'])

class ModeTests(unittest.TestCase):
    def setUp(self):
        support.IntegrationTests.setUp(self)
        self.desktop_opens=[]
        self.bridge._open_desktop=lambda thread_id,host:self.desktop_opens.append((thread_id,host))
        self.history_loads=0
        def refresh(_session):self.history_loads+=1
        self.bridge._refresh_goal_snapshot=refresh
    tearDown = support.IntegrationTests.tearDown

    def upload_png(self):
        import base64
        return self.bridge.upload(THREAD, str(uuid.uuid4()), 'photo.png', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR7kAAAAASUVORK5CYII='))

    def calls(self):
        return [r for r in self.fixture.requests if r['method'] == 'thread-follower-start-turn']

    def live(self, **changes):
        session = self.bridge.session(THREAD)
        with session.condition:
            session.state.update(changes)
            session.changed()
        return session

    def plan(self):
        request = {'id': 'implement-plan:turn', 'method': 'item/plan/requestImplementation',
                   'params': {'turnId': 'turn', 'planContent': '# Plan\n\nReply OK.'}}
        self.fixture.state['requests'] = [request]
        return request

    def test_plan_mode_is_sent_to_original_owner_and_preserves_model_effort_policy(self):
        self.fixture.state['latestReasoningEffort'] = 'high'
        self.bridge.send(THREAD, 'Make a plan', str(uuid.uuid4()), work_mode='plan')
        call = self.calls()[0]
        self.assertEqual(call['targetClientId'], 'owner')
        self.assertEqual(call['params']['conversationId'], THREAD)
        request = call['params']['turnStart']['request']
        self.assertEqual(request['collaborationMode'], {'mode': 'plan', 'settings': {
            'model': 'same-model', 'reasoning_effort': 'high', 'developer_instructions': None}})
        self.assertTrue(call['params']['turnStart']['context']['inheritThreadSettings'])
        for key in ['modelProvider', 'approvalPolicy', 'sandboxPolicy', 'permissions']:
            self.assertNotIn(key, request)

    def test_mode_is_part_of_idempotency_and_legacy_send_inherits(self):
        key = str(uuid.uuid4())
        self.bridge.send(THREAD, 'hello', key, work_mode='plan')
        self.bridge.send(THREAD, 'hello', key, work_mode='plan')
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'hello', key, work_mode='default')
        self.bridge.send(THREAD, 'legacy', str(uuid.uuid4()))
        self.assertNotIn('collaborationMode', self.calls()[-1]['params']['turnStart']['request'])
        self.assertEqual(len(self.calls()), 2)

    def test_remote_mode_keeps_host_and_native_version(self):
        self.bridge.host = self.fixture.host = 'remote:test'
        self.bridge.send(THREAD, 'Plan remotely', str(uuid.uuid4()), work_mode='plan')
        self.assertEqual(self.calls()[0]['hostId'], 'remote:test')
        self.assertEqual(self.calls()[0]['version'], 3)

    def test_queued_mode_survives_desktop_mode_change(self):
        self.fixture.state['threadRuntimeStatus'] = {'type': 'active'}
        key = str(uuid.uuid4())
        self.bridge.send(THREAD, 'plan later', key, 'queue', work_mode='plan')
        self.assertEqual(self.calls(), [])
        session = self.live(threadRuntimeStatus={'type': 'idle'}, latestCollaborationMode={'mode': 'default'}, latestModel='new-model')
        entry = self.bridge.submissions[THREAD + ':' + key]
        self.bridge._send_queued(session, THREAD + ':' + key, entry)
        settings = self.calls()[0]['params']['turnStart']['request']['collaborationMode']
        self.assertEqual(settings['mode'], 'plan')
        self.assertEqual(settings['settings']['model'], 'new-model')

    def test_goal_uses_limited_native_rpc_and_confirms_from_snapshot(self):
        key=str(uuid.uuid4())
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        result=self.bridge.send(THREAD, 'Keep tests green', key, work_mode='goal')
        self.assertEqual(result['status'], 'accepted');self.assertTrue(result['confirmed'])
        self.assertEqual(stub.set_calls, [(THREAD, 'Keep tests green')])
        self.assertEqual(self.desktop_opens, [(THREAD, 'local')])
        self.assertEqual(self.history_loads, 1)
        self.assertEqual(stub.clear_calls, [])
        calls=self.calls()
        self.assertEqual(len(calls), 1)
        activation_call=calls[0]['params']['turnStart']['request']
        self.assertEqual(activation_call['input'][0]['text'], '目标已创建。请立即开始执行当前目标，不要重复创建目标。')
        self.assertEqual(result['activation']['status'], 'accepted')
        view=self.bridge.view(THREAD)
        self.assertEqual(view['goal']['status'], 'active')
        self.assertIsNone(view['goalSubmission'])
        # A newer native command must supersede a stale prompt-era submission.
        self.bridge.submissions[THREAD+':'+'legacy'] = {
            'text':'Legacy objective','mode':'send','skills':[],'status':'accepted',
            'at':0,'workMode':'goal','goalConfirmed':True,
        }
        self.assertIsNone(self.bridge.view(THREAD)['goalSubmission'])
        self.assertEqual(self.bridge.goal_commands[THREAD+':'+key]['state'], 'confirmed')
        # The same request id is idempotent and does not call app-server again.
        duplicate=self.bridge.send(THREAD, 'Keep tests green', key, work_mode='goal')
        self.assertEqual(duplicate['status'], 'accepted');self.assertTrue(duplicate['confirmed'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['activation']['status'], 'accepted')
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(stub.set_calls), 1)
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'A different objective', str(uuid.uuid4()), work_mode='goal')

    def test_goal_activation_follows_requested_locale(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD, 'Keep tests green', str(uuid.uuid4()), work_mode='goal',
                         ui_locale='en-US')
        request=self.calls()[-1]['params']['turnStart']['request']
        self.assertEqual(request['input'][0]['text'],GOAL_ACTIVATION_TEXTS['en-US']['create'])
        self.assertEqual(normalize_ui_locale('en'), 'en-US')
        self.assertEqual(normalize_ui_locale('unknown'), 'zh-CN')

    def test_goal_create_serially_sends_activation_turn(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        result=self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        calls=self.calls()
        self.assertEqual(len(calls),1)
        request=calls[0]['params']['turnStart']['request']
        self.assertEqual(request['clientUserMessageId'],result['activation']['id'])
        self.assertEqual(request['input'][0]['text'],'目标已创建。请立即开始执行当前目标，不要重复创建目标。')

    def test_bare_native_goal_result_still_sends_activation(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        goal={'threadId':THREAD,'objective':'Keep tests green','status':'active',
              'createdAt':1791057183,'updatedAt':1791057183}
        session=self.bridge.session(THREAD)
        response={'status':'active','confirmed':True,'result':goal}
        activation=self.bridge._send_goal_activation(session,response,'create')
        calls=self.calls()
        self.assertEqual(activation['status'],'accepted')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['params']['turnStart']['request']['input'][0]['text'],
                         '目标已创建。请立即开始执行当前目标，不要重复创建目标。')

    def test_goal_activation_failure_is_explicit_and_does_not_silently_send(self):
        from unittest.mock import patch
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        with patch.object(type(self.bridge),'_dispatch',side_effect=OSError('offline')):
            with self.assertRaises(IPCError) as caught:
                self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        self.assertIn('启动消息发送失败',str(caught.exception))
        self.assertEqual(self.calls(),[])
        self.assertEqual(stub.set_calls,[(THREAD,'Keep tests green')])

    def test_goal_resume_sends_resume_activation(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        create_text='目标已创建。请立即开始执行当前目标，不要重复创建目标。'
        resume_text='目标已恢复。请继续执行当前目标。'
        self.assertEqual(self.calls()[-1]['params']['turnStart']['request']['input'][0]['text'],create_text)
        self.fixture.requests.clear()
        self.bridge.set_goal_status(THREAD,'paused',str(uuid.uuid4()))
        self.assertEqual(self.calls(),[])
        self.bridge.set_goal_status(THREAD,'active',str(uuid.uuid4()))
        calls=self.calls()
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['params']['turnStart']['request']['input'][0]['text'],resume_text)

    def test_activation_messages_are_hidden_from_mobile_timeline(self):
        from bridge.model import normalize_item
        goal={'objective':'Keep tests green','status':'active'}
        activation_id,_=goal_activation(THREAD,goal,'create')
        self.bridge.goal_commands[THREAD+':'+activation_id] = {
            'action':'create','state':'confirmed','objective':goal['objective'],
            'response':{'status':'active','confirmed':True,'result':{'goal':goal}},
        }
        self.assertEqual(self.bridge._goal_activation_ids(THREAD),{activation_id})
        item={'type':'userMessage','id':'activation','clientUserMessageId':activation_id,
              'content':[{'type':'text','text':'目标已创建。请立即开始执行当前目标，不要重复创建目标。'}]}
        row=normalize_item(item)
        timeline=Timeline()
        timeline.update({'sequence':1,'goalActivationIds':[activation_id],
                         'turns':[{'id':'turn','messages':[row]}]})
        self.assertEqual(timeline.rows,[])
        self.assertIsNone(timeline.meta['latestUserTurnId'])

    def test_manual_activation_text_is_not_hidden_without_activation_id(self):
        text=GOAL_ACTIVATION_TEXTS['zh-CN']['create']
        row=normalize_item({'type':'userMessage','id':'manual','content':[
            {'type':'text','text':text}]})
        timeline=Timeline()
        timeline.update({'sequence':1,'goalActivationIds':['other-id'],
                         'turns':[{'id':'turn','messages':[row]}]})
        self.assertEqual(len(timeline.rows),1)
        self.assertEqual(timeline.rows[0]['text'],text)

    def test_cancel_goal_cancels_queued_activation_before_dispatch(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.fixture.state['threadRuntimeStatus']={'type':'active'}
        result=self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        activation=result['activation']
        self.assertEqual(activation['status'],'queued')
        activation_key=THREAD+':'+activation['id']
        self.assertEqual(self.bridge.submissions[activation_key]['status'],'queued')
        self.assertEqual(self.calls(),[])
        self.bridge.cancel_goal(THREAD,str(uuid.uuid4()))
        self.assertEqual(self.bridge.submissions[activation_key]['status'],'cancelled')
        self.live(threadRuntimeStatus={'type':'idle'})
        self.bridge._send_queued(self.bridge.live[THREAD],activation_key,
                                 self.bridge.submissions[activation_key])
        self.assertEqual(self.calls(),[])

    def test_queued_activation_guard_dispatches_matching_active_goal(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.fixture.state['threadRuntimeStatus']={'type':'active'}
        result=self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        activation=result['activation'];activation_key=THREAD+':'+activation['id']
        self.assertEqual(self.bridge.submissions[activation_key]['status'],'queued')
        self.live(threadRuntimeStatus={'type':'idle'})
        self.bridge._send_queued(self.bridge.live[THREAD],activation_key,
                                 self.bridge.submissions[activation_key])
        self.assertEqual(len(self.calls()),1)
        self.assertEqual(self.bridge.submissions[activation_key]['status'],'accepted')

    def test_goal_rejects_attachments_skills_and_remote_host(self):
        self.bridge.goal=GoalStub(self.bridge)
        image=self.upload_png()
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'Objective', str(uuid.uuid4()), attachments=[image['id']], work_mode='goal')
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'Objective', str(uuid.uuid4()), skills=['skill'], work_mode='goal')
        self.bridge.goal=None
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'Objective', str(uuid.uuid4()), work_mode='goal')
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.bridge.submissions, {})

    def test_goal_rpc_error_is_not_replayed(self):
        stub=GoalStub(self.bridge,error=GoalError('native goal timeout'));stub.failures=999
        self.bridge.goal=stub
        key=str(uuid.uuid4())
        with self.assertRaises(IPCError):
            self.bridge.send(THREAD, 'Keep tests green', key, work_mode='goal')
        self.assertEqual(len(stub.set_calls), 1)
        self.assertEqual(self.bridge.goal_commands[THREAD+':'+key]['state'], 'unknown')
        result=self.bridge.send(THREAD, 'Keep tests green', key, work_mode='goal')
        self.assertEqual(result, {'status': 'unknown', 'confirmed': False, 'duplicate': True, 'id': key,
                                  'result': None, 'activation': None})
        self.assertEqual(len(stub.set_calls), 1)

    def test_goal_unavailable_is_retried_once(self):
        stub=GoalStub(self.bridge,error=GoalUnavailable('transient'))
        stub.failures=1
        self.bridge.goal=stub
        key=str(uuid.uuid4())
        result=self.bridge.send(THREAD,'Keep tests green',key,work_mode='goal')
        self.assertEqual(result['status'],'accepted');self.assertTrue(result['confirmed'])
        self.assertEqual(stub.set_calls,[(THREAD,'Keep tests green'),(THREAD,'Keep tests green')])
        self.assertEqual(self.desktop_opens,[(THREAD,'local')])

    def test_goal_unavailable_does_not_leave_pending_request(self):
        stub=GoalStub(self.bridge,error=GoalUnavailable('offline'));stub.failures=999;self.bridge.goal=stub
        key=str(uuid.uuid4())
        with self.assertRaises(IPCError):
            self.bridge.send(THREAD, 'Keep tests green', key, work_mode='goal')
        self.assertEqual(self.bridge.goal_commands[THREAD+':'+key]['state'], 'unknown')
        submission=self.bridge.view(THREAD)['goalSubmission']
        self.assertEqual(submission, {'id':key,'objective':'Keep tests green','status':'unknown'})

    def test_cancelled_goal_hides_stale_snapshot_and_second_cancel_is_idempotent(self):
        key=str(uuid.uuid4())
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD,'Keep tests green',key,work_mode='goal')
        result=self.bridge.cancel_goal(THREAD,str(uuid.uuid4()))
        self.assertEqual(result['status'],'cancelled')
        # Simulate an owner snapshot that did not observe the native clear.
        session=self.live(threadGoal={'objective':'Keep tests green','status':'paused'})
        view=self.bridge.view(THREAD)
        self.assertIsNone(view['goal']);self.assertIsNone(view['goalSubmission'])
        result=self.bridge.cancel_goal(THREAD,str(uuid.uuid4()))
        self.assertEqual(result,{'status':'cancelled','confirmed':True,'duplicate':True,'result':{'ok':True}})
        self.assertEqual(len(stub.clear_calls),1)
        self.assertIsNone(self.bridge.view(THREAD)['goal'])

    def test_goal_pause_and_resume_use_limited_status_rpc(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        result=self.bridge.set_goal_status(THREAD,'paused',str(uuid.uuid4()))
        self.assertEqual(result['status'],'paused');self.assertTrue(result['confirmed'])
        self.assertEqual(stub.status_calls,[(THREAD,'paused')])
        self.assertEqual(self.bridge.view(THREAD)['goal']['status'],'paused')
        result=self.bridge.set_goal_status(THREAD,'active',str(uuid.uuid4()))
        self.assertEqual(result['status'],'active');self.assertTrue(result['confirmed'])
        self.assertEqual(stub.status_calls,[(THREAD,'paused'),(THREAD,'active')])

    def test_goal_transitions_reject_invalid_native_states(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD,'Another objective',str(uuid.uuid4()),work_mode='goal')
        self.assertEqual(stub.set_calls,[(THREAD,'Keep tests green')])
        self.bridge.set_goal_status(THREAD,'active',str(uuid.uuid4()))
        self.assertEqual(stub.status_calls,[])
        self.bridge.set_goal_status(THREAD,'paused',str(uuid.uuid4()))
        self.bridge.set_goal_status(THREAD,'paused',str(uuid.uuid4()))
        self.assertEqual(stub.status_calls,[(THREAD,'paused')])

    def test_cancel_works_for_nonterminal_limited_goal_and_complete_is_terminal(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        self.live(threadGoal={'objective':'Keep tests green','status':'usageLimited'})
        self.bridge.cancel_goal(THREAD,str(uuid.uuid4()))
        self.assertEqual(stub.clear_calls,[THREAD])
        stub.clear_calls.clear()
        self.live(threadGoal={'objective':'Keep tests green','status':'complete'})
        self.bridge.cancel_goal(THREAD,str(uuid.uuid4()))
        self.assertEqual(stub.clear_calls,[])

    def test_pause_is_serialized_and_one_operation_is_a_native_duplicate(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD,'Keep tests green',str(uuid.uuid4()),work_mode='goal')
        requests=[str(uuid.uuid4()),str(uuid.uuid4())]
        barrier=threading.Barrier(2)
        def pause(request_id):
            barrier.wait()
            try:self.bridge.set_goal_status(THREAD,'paused',request_id)
            except Exception:pass
        threads=[threading.Thread(target=pause,args=(request_id,)) for request_id in requests]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(stub.status_calls,[(THREAD,'paused')])

    def test_cancellation_idempotence_is_scoped_to_thread(self):
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        other='0'*8+'-'+THREAD[9:]
        self.bridge.goal_commands[THREAD+':'+str(uuid.uuid4())] = {
            'action':'cancel','state':'confirmed','objective':'Keep tests green','at':2,
            'response':{'status':'cancelled','confirmed':True},
        }
        from bridge.service import LiveSession
        other_session=LiveSession(other)
        self.bridge.live[other]=other_session
        with other_session.condition:
            other_session.state={**self.fixture.state,'id':other,'threadGoal':{'objective':'Keep tests green','status':'active'}}
        self.bridge._goal_command(other_session,other,str(uuid.uuid4()),'cancel',objective='Keep tests green')
        self.assertEqual(stub.clear_calls[-1],THREAD.replace(THREAD[:8],other[:8]))

    def test_goal_cancel_clears_active_goal_and_marks_request_cancelled(self):
        key=str(uuid.uuid4())
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD, 'Keep tests green', key, work_mode='goal')
        result=self.bridge.cancel_goal(THREAD, str(uuid.uuid4()))
        self.assertEqual(result['status'], 'cancelled');self.assertTrue(result['confirmed'])
        self.assertEqual(stub.clear_calls, [THREAD])
        self.assertEqual(self.desktop_opens[-1], (THREAD, 'local'))
        self.assertGreaterEqual(self.history_loads, 1)
        self.assertIsNone(self.bridge.view(THREAD)['goal'])
        self.assertEqual(self.bridge.goal_commands[THREAD+':'+key]['action'], 'create')
        result=self.bridge.cancel_goal(THREAD, str(uuid.uuid4()))
        self.assertEqual(result, {'status': 'cancelled', 'confirmed': True, 'duplicate': True, 'result': {'ok': True}})

    def test_unknown_cancel_is_reconciled_without_native_replay(self):
        key=str(uuid.uuid4())
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        self.bridge.send(THREAD,'Keep tests green',key,work_mode='goal')
        stub.goals.pop(THREAD,None)
        session=self.bridge.live.get(THREAD)
        with session.condition:
            session.state['threadGoal']=None
            session.changed()
        unknown=str(uuid.uuid4())
        self.bridge.goal_commands[THREAD+':'+unknown] = {
            'action':'cancel','workMode':'goal','objective':'Keep tests green','status':None,
            'state':'unknown','sent':True,'at':2,
            'fingerprint':json.dumps({'action':'cancel','objective':'Keep tests green','status':None},
                                     ensure_ascii=False,sort_keys=True),
            'error':'目标操作已发送，但状态尚未确认',
        }
        result=self.bridge.cancel_goal(THREAD, str(uuid.uuid4()))
        self.assertEqual(result, {'status':'cancelled','confirmed':True,'duplicate':True,
                                  'reconciled':True,'result':None})
        self.assertEqual(self.bridge.goal_commands[THREAD+':'+unknown]['state'],'confirmed')
        self.assertTrue(self.bridge.goal_commands[THREAD+':'+unknown]['response']['reconciled'])
        self.assertEqual(stub.clear_calls,[])

    def test_legacy_completed_goal_request_does_not_block_native_goal(self):
        key=str(uuid.uuid4())
        self.bridge.submissions[THREAD+':'+key] = {
            'text':'Legacy objective', 'mode':'send', 'skills':[], 'status':'accepted',
            'at':0, 'workMode':'goal', 'goalConfirmed':False,
        }
        self.live(turns=[{'turnId':'legacy','status':'completed','params':{'clientUserMessageId':key,'input':[{'type':'text','text':'legacy prompt'}]},'items':[]}])
        view=self.bridge.view(THREAD)
        self.assertEqual(view['goalSubmission']['status'],'unconfirmed')
        self.assertIsNone(view['goal'])
        stub=GoalStub(self.bridge);self.bridge.goal=stub
        result=self.bridge.send(THREAD,'New native objective',str(uuid.uuid4()),work_mode='goal')
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(stub.set_calls,[(THREAD,'New native objective')])

    def test_stale_owner_goal_does_not_create_mobile_dead_end(self):
        import sqlite3
        db=sqlite3.connect(self.root/'goals_1.sqlite');db.execute('create table thread_goals(thread_id text primary key)');db.commit();db.close()
        key=str(uuid.uuid4())
        self.bridge.goal_commands[THREAD+':'+key] = {
            'action':'create','workMode':'goal','objective':'Deleted elsewhere','status':'active',
            'state':'confirmed','sent':True,'at':0,'confirmedAt':0,
            'response':{'status':'active','confirmed':True},
        }
        session=self.live(threadGoal={'objective':'Deleted elsewhere','status':'active'})
        view=self.bridge.view(THREAD)
        self.assertIsNone(view['goal'])
        self.assertIsNone(view['goalSubmission'])

    def test_goal_validation_blocks_replacement_and_steering(self):
        self.bridge.goal=GoalStub(self.bridge)
        for mode,work,text in [('send','bad','a'),('steer','plan','a'),('queue','goal','a'),('send','goal','a'*4001)]:
            with self.assertRaises(ValueError):
                self.bridge.send(THREAD, text, str(uuid.uuid4()), mode, work_mode=work)
        self.fixture.state['threadGoal'] = {'objective': 'old', 'status': 'paused'}
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'new', str(uuid.uuid4()), work_mode='goal')
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.bridge.submissions, {})

    def test_missing_model_fails_before_submission_ledger(self):
        self.fixture.state['latestModel'] = ''
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'plan', str(uuid.uuid4()), work_mode='plan')
        self.assertEqual(self.bridge.submissions, {})

    def test_plan_request_renders_content_but_user_verification_stays_protected(self):
        request = self.plan()
        normalized = normalize_request(request)
        self.assertTrue(normalized['supported'])
        self.assertEqual(normalized['params']['planContent'], request['params']['planContent'])
        request['params']['_meta'] = {'openai/userVerification': True}
        self.assertFalse(normalize_request(request)['supported'])

    def test_plan_implementation_uses_content_and_default_mode_once(self):
        request = self.plan()
        result = self.bridge.respond(THREAD, request['id'], {'action': 'implement'})
        self.assertEqual(result['status'], 'accepted')
        payload = self.calls()[0]['params']['turnStart']['request']
        self.assertTrue(payload['input'][0]['text'].endswith(request['params']['planContent']))
        self.assertEqual(payload['collaborationMode']['mode'], 'default')
        self.live(requests=[])
        self.assertTrue(self.bridge.respond(THREAD, request['id'], {'action': 'implement'})['duplicate'])
        self.assertEqual(len(self.calls()), 1)
        with self.assertRaises(ValueError):
            self.bridge.respond(THREAD, request['id'], {'action': 'revise', 'text': 'different'})

    def test_plan_revision_keeps_plan_mode_and_rejects_empty_feedback(self):
        request = self.plan()
        with self.assertRaises(ValueError):
            self.bridge.respond(THREAD, request['id'], {'action': 'revise', 'text': ' '})
        self.bridge.respond(THREAD, request['id'], {'action': 'revise', 'text': 'Make it shorter'})
        payload = self.calls()[0]['params']['turnStart']['request']
        self.assertEqual(payload['input'][0]['text'], 'Make it shorter')
        self.assertEqual(payload['collaborationMode']['mode'], 'plan')

    def test_stale_plan_cannot_start_a_turn(self):
        with self.assertRaises(ValueError):
            self.bridge.respond(THREAD, 'implement-plan:stale', {'action': 'implement'})
        self.assertEqual(self.calls(), [])

    def test_uncertain_plan_execution_is_not_replayed(self):
        request = self.plan()
        self.bridge.session(THREAD)
        with patch.object(self.bridge, '_call', side_effect=IPCError('timeout')) as call:
            with self.assertRaises(IPCError):
                self.bridge.respond(THREAD, request['id'], {'action': 'implement'})
            result = self.bridge.respond(THREAD, request['id'], {'action': 'implement'})
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
