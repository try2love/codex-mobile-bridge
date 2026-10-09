"""Regressions for native goal lifecycle and uncertain-delivery boundaries."""
import copy
import json
import threading
import unittest
import uuid
from unittest.mock import patch

import test_modes as support
from test_modes import GoalStub, THREAD
import test_bridge as bridge_support
from bridge.features.sessions.goal import GoalUnavailable
from bridge.clients.codex.ipc import IPCError


class GoalControlTests(unittest.TestCase):
    setUp = support.ModeTests.setUp
    tearDown = support.ModeTests.tearDown
    calls = support.ModeTests.calls
    live = support.ModeTests.live

    def create(self, text='Work'):
        if not isinstance(self.bridge.goal, GoalStub):
            self.bridge.goal = GoalStub(self.bridge)
        return self.bridge.send(THREAD, text, str(uuid.uuid4()), work_mode='goal')

    def test_pause_does_not_wait_for_history_or_activate_desktop(self):
        self.create()
        with patch.object(self.bridge, '_refresh_goal_snapshot', side_effect=AssertionError('full history requested')), patch.object(self.bridge, '_target', side_effect=AssertionError('activation requested')):
            result = self.bridge.set_goal_status(THREAD, 'paused', str(uuid.uuid4()))
        self.assertTrue(result['confirmed'])

    def test_cold_goal_pause_needs_no_history_or_desktop_owner(self):
        self.create()
        stub = self.bridge.goal
        stub._complete = lambda *args: None
        self.bridge.live.pop(THREAD)
        with patch.object(self.bridge, '_target', side_effect=AssertionError('activation requested')), patch.object(self.bridge.store, 'history', side_effect=AssertionError('history requested')):
            result = self.bridge.set_goal_status(THREAD, 'paused', str(uuid.uuid4()))
        self.assertTrue(result['confirmed'])
        self.assertIsNone(self.bridge.live[THREAD].state)

    def test_two_resume_cycles_dispatch_distinct_turns_and_retry_once(self):
        self.create()
        ids=[]
        for _ in range(2):
            self.bridge.set_goal_status(THREAD,'paused',str(uuid.uuid4()))
            request_id=str(uuid.uuid4())
            response=self.bridge.set_goal_status(THREAD,'active',request_id)
            ids.append(response['activation']['id'])
            self.bridge.set_goal_status(THREAD,'active',request_id)
        self.assertNotEqual(*ids)
        self.assertEqual(len(self.calls()),3)

    def test_cancel_recreated_identical_objective_reaches_native_again(self):
        for _ in range(2):
            self.create()
            response=self.bridge.cancel_goal(THREAD,str(uuid.uuid4()))
            self.assertTrue(response['confirmed'])
            self.assertIsNone(self.bridge.goal.get_goal(THREAD))
        self.assertEqual(len(self.bridge.goal.clear_calls),2)
        self.assertEqual(len(self.calls()),2)

    def test_pause_cancels_queued_activation_then_resume_gets_new_turn(self):
        self.fixture.state['threadRuntimeStatus']={'type':'active'}
        response=self.create()
        key=THREAD+':'+response['activation']['id']
        self.bridge.set_goal_status(THREAD,'paused',str(uuid.uuid4()))
        self.assertEqual(self.bridge.submissions[key]['status'],'cancelled')
        session=self.live(threadRuntimeStatus={'type':'idle'})
        self.bridge._send_queued(session,key,self.bridge.submissions[key])
        self.assertEqual(self.calls(),[])
        self.bridge.set_goal_status(THREAD,'active',str(uuid.uuid4()))
        self.assertEqual(len(self.calls()),1)

    def test_native_pause_or_unreadable_state_never_dispatches_queued_turn(self):
        self.fixture.state['threadRuntimeStatus']={'type':'active'}
        response=self.create();key=THREAD+':'+response['activation']['id']
        session=self.live(threadRuntimeStatus={'type':'idle'})
        with patch.object(self.bridge,'_native_goal_state',side_effect=GoalUnavailable('offline')):
            with self.assertRaises(GoalUnavailable):
                self.bridge._send_queued(session,key,self.bridge.submissions[key])
        self.assertEqual(self.bridge.submissions[key]['status'],'queued')
        self.bridge.goal.goals[THREAD]['status']='paused'
        self.bridge._send_queued(session,key,self.bridge.submissions[key])
        self.assertEqual(self.calls(),[])
        self.assertEqual(self.bridge.submissions[key]['status'],'cancelled')

    def test_write_disconnect_is_never_replayed_or_applied_to_new_goal(self):
        self.create();stub=self.bridge.goal
        calls=[]
        def clear(thread):
            calls.append(thread)
            stub.goals[thread]={'objective':'New desktop goal','status':'active'}
            raise GoalUnavailable('response lost after clear')
        request_id=str(uuid.uuid4())
        with patch.object(stub,'clear_goal',side_effect=clear):
            with self.assertRaises(IPCError):self.bridge.cancel_goal(THREAD,request_id)
            response=self.bridge.cancel_goal(THREAD,request_id)
        self.assertEqual(len(calls),1)
        self.assertEqual(response['status'],'unknown')
        self.assertEqual(stub.get_goal(THREAD)['objective'],'New desktop goal')

    def test_sent_record_reconciles_after_reload_without_resending_native_write(self):
        response=self.create();request_id=response['id'];key=THREAD+':'+request_id
        command=self.bridge.goal_commands[key]
        command['state']='sent';command.pop('response')
        self.bridge._save_goal_commands()
        self.bridge.goal_commands=json.loads(self.bridge.goal_commands_path.read_text())
        result=self.bridge.send(THREAD,'Work',request_id,work_mode='goal')
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.bridge.goal_commands[key]['state'],'confirmed')
        self.assertEqual(len(self.bridge.goal.set_calls),1)
        self.assertEqual(len(self.calls()),1)

    def test_new_duplicate_request_does_not_start_an_extra_turn(self):
        self.create();self.create()
        self.bridge.set_goal_status(THREAD,'active',str(uuid.uuid4()))
        self.assertEqual(len(self.calls()),1)

    def test_edit_requires_pause_preserves_budget_and_waits_for_explicit_resume(self):
        self.create();stub=self.bridge.goal
        with self.assertRaisesRegex(ValueError,'先暂停'):
            self.bridge.edit_goal(THREAD,'Revised',str(uuid.uuid4()))
        self.bridge.set_goal_status(THREAD,'paused',str(uuid.uuid4()))
        stub.goals[THREAD]['tokenBudget']=12345
        request_id=str(uuid.uuid4())
        self.bridge.edit_goal(THREAD,'Revised',request_id)
        self.bridge.edit_goal(THREAD,'Revised',request_id)
        goal=stub.get_goal(THREAD)
        self.assertEqual((goal['objective'],goal['status'],goal['tokenBudget']),('Revised','paused',12345))
        self.assertEqual(len(self.calls()),1)
        self.bridge.set_goal_status(THREAD,'active',str(uuid.uuid4()))
        self.assertEqual(len(self.calls()),2)

    def test_stale_phone_actions_cannot_modify_replacement_goal(self):
        self.create();old=copy.deepcopy(self.bridge.goal.get_goal(THREAD))
        self.bridge.goal.goals[THREAD]={'objective':'From desktop','status':'active'}
        with self.assertRaisesRegex(ValueError,'发生变化'):
            self.bridge.cancel_goal(THREAD,str(uuid.uuid4()),expected=old)
        self.assertEqual(self.bridge.goal.clear_calls,[])

    def test_locale_change_during_retry_reuses_original_activation(self):
        request_id=str(uuid.uuid4());self.bridge.goal=GoalStub(self.bridge)
        self.bridge.send(THREAD,'Work',request_id,work_mode='goal',ui_locale='en-US')
        self.bridge.send(THREAD,'Work',request_id,work_mode='goal',ui_locale='zh-CN')
        self.assertEqual(len(self.calls()),1)

    def test_read_failure_cannot_confirm_unknown_cancel(self):
        self.create();key=THREAD+':'+str(uuid.uuid4())
        self.bridge.goal_commands[key]={'action':'cancel','objective':'Work','state':'unknown'}
        with patch.object(self.bridge,'_native_goal_state',side_effect=GoalUnavailable('offline')):
            self.bridge._reconcile_goal_commands(THREAD)
        self.assertEqual(self.bridge.goal_commands[key]['state'],'unknown')

class GoalHttpTests(unittest.TestCase):
    setUpClass = classmethod(bridge_support.HttpTests.setUpClass.__func__)
    setUp = bridge_support.HttpTests.setUp
    tearDown = bridge_support.HttpTests.tearDown
    request = bridge_support.HttpTests.request
    login = bridge_support.HttpTests.login

    def test_edit_status_and_clear_require_auth_csrf_and_preserve_expected_goal(self):
        from unittest.mock import Mock
        expected={'objective':'Work','status':'paused','createdAt':1}
        payload={'id':str(uuid.uuid4()),'objective':'Revised','status':'active','expected':expected,'uiLocale':'en-US'}
        self.server.bridge.edit_goal=Mock(return_value={'confirmed':True})
        self.server.bridge.set_goal_status=Mock(return_value={'confirmed':True})
        self.server.bridge.cancel_goal=Mock(return_value={'confirmed':True})
        headers=self.login()
        for action in ('edit','status','cancel'):
            route='/api/sessions/'+THREAD+'/goal/'+action
            self.assertEqual(self.request('POST',route,payload)[0],401)
            self.assertEqual(self.request('POST',route,payload,{'Cookie':headers['Cookie']})[0],403)
            self.assertEqual(self.request('POST',route,payload,headers)[0],200)
        self.server.bridge.edit_goal.assert_called_once_with(THREAD,'Revised',payload['id'],expected=expected)
        self.server.bridge.set_goal_status.assert_called_once_with(THREAD,'active',payload['id'],'en-US',expected=expected)
        self.server.bridge.cancel_goal.assert_called_once_with(THREAD,payload['id'],expected=expected)

    def test_unavailable_goal_is_reported_as_recoverable(self):
        from unittest.mock import Mock
        self.server.bridge.cancel_goal=Mock(side_effect=GoalUnavailable('offline'))
        code,_,body=self.request('POST','/api/sessions/'+THREAD+'/goal/cancel',{'id':str(uuid.uuid4())},self.login())
        self.assertEqual(code,409);self.assertEqual(body['error'],'offline')
