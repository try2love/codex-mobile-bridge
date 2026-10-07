import json
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
import uuid
from unittest.mock import patch
from types import SimpleNamespace

import test_bridge as support
from bridge.service import Bridge
from bridge.remote import RemoteStore


class SubagentTests(unittest.TestCase):
    def setUp(self):
        self.case=support.IntegrationTests();self.case.setUp();self.root=self.case.root;self.bridge=self.case.bridge
        (self.root/'sessions').mkdir()
    def tearDown(self):self.case.tearDown()
    def child(self,parent,title='Child',source=None,path=None):
        identifier=str(uuid.uuid4());log=path or self.root/'sessions'/(identifier+'.jsonl')
        if not path:
            records=[{'type':'event_msg','payload':{'type':'task_started','turn_id':'fixture-turn'}},
                     {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'text':'Result '+title}]}},
                     {'type':'event_msg','payload':{'type':'task_complete'}}]
            log.write_text('\n'.join(json.dumps(row) for row in records))
        value=source or {'subagent':{'thread_spawn':{'parent_thread_id':parent,'agent_nickname':title,'agent_path':'/root/'+title}}}
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',(identifier,title,'/workspace',2,0,None,json.dumps(value),str(log)))
        return identifier
    def test_tree_includes_descendants_excludes_other_roots(self):
        first=self.child(support.THREAD);second=self.child(first,'Nested');self.child(str(uuid.uuid4()),'Unrelated')
        self.child(support.THREAD,'Guardian',{'subagent':{'other':'guardian:'+support.THREAD}})
        rows=self.bridge.subagents(support.THREAD)['agents'];self.assertEqual([r['id'] for r in rows],[first,second]);self.assertEqual(rows[1]['parentId'],first)
        self.assertEqual(self.case.fixture.requests,[])
    def test_history_saved_state_and_no_activation(self):
        child=self.child(support.THREAD);view=self.bridge.subagents(support.THREAD,child)
        self.assertTrue(view['saved']);self.assertEqual(view['turns'][-1]['status'],'completed')
        self.assertEqual(view['turns'][-1]['messages'][0]['text'],'Result Child');self.assertEqual(self.case.fixture.requests,[])
        with self.assertRaises(KeyError):self.bridge.store.get(child)
    def test_rejects_unrelated_cycles_and_external_rollout(self):
        unrelated=self.child(str(uuid.uuid4()))
        with self.assertRaises(KeyError):self.bridge.subagents(support.THREAD,unrelated)
        bad=self.child(support.THREAD,path=self.root/'external.jsonl')
        with self.assertRaises(ValueError):self.bridge.subagents(support.THREAD,bad)
        child=self.child(support.THREAD)
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET source=? WHERE id=?',(json.dumps({'subagent':{'thread_spawn':{'parent_thread_id':child}}}),child))
        with self.assertRaises(KeyError):self.bridge.subagents(support.THREAD,child)
    def test_remote_delegates_with_original_parent(self):
        child=self.child(support.THREAD);remote=RemoteStore('fixture');bridge=SimpleNamespace(store=remote)
        with patch.object(remote,'call',side_effect=lambda method,args:getattr(self.bridge.store,method)(**args)) as call:
            self.assertEqual(Bridge.subagents(bridge,support.THREAD)['agents'][0]['id'],child)
            self.assertTrue(Bridge.subagents(bridge,support.THREAD,child)['saved'])
            self.assertEqual(call.call_args.args[1],{'thread_id':support.THREAD,'agent_id':child})


class SubagentHttpTests(unittest.TestCase):
    setUpClass=classmethod(support.HttpTests.setUpClass.__func__)
    setUp=support.HttpTests.setUp;tearDown=support.HttpTests.tearDown;request=support.HttpTests.request;login=support.HttpTests.login
    def test_auth_read_only_and_host(self):
        path='/api/sessions/'+support.THREAD+'/subagents';calls=[]
        self.server.bridge.subagents=lambda thread,agent:calls.append((thread,agent)) or {'agents':[]}
        self.assertEqual(self.request('GET',path)[0],401);auth=self.login()
        self.assertEqual(self.request('GET',path,headers=auth)[0],200)
        self.assertEqual(self.request('POST',path,{},auth)[0],405)
        self.assertEqual(self.request('GET',path+'?host=unknown',headers=auth)[0],404)
        self.assertEqual(calls,[(support.THREAD,None)])
