import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from bridge.terminal import CommandJob, RemoteCommandJob, TerminalManager, MAX_OUTPUT


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp');self.root=Path(self.temp.name);self.manager=TerminalManager()
    def tearDown(self):
        self.manager.close();self.temp.cleanup()
    def identifier(self):return str(uuid.uuid4())
    def test_output_exit_and_cwd(self):
        key=self.identifier();self.manager.start('owner','thread',key,self.root,"pwd; printf '你好\\n'; exit 7")
        job=self.manager.get('owner','thread',key);self.assertTrue(job.done.wait(5));result=job.read()
        self.assertIn(str(self.root),result['output']);self.assertIn('你好',result['output']);self.assertEqual(result['exitCode'],7)
        self.assertEqual(job.read(result['cursor'])['output'],'')
    def test_start_idempotent_and_scoped(self):
        key=self.identifier();command='echo run >> runs.txt'
        self.manager.start('owner','thread',key,self.root,command)
        self.manager.get('owner','thread',key).done.wait(5)
        self.manager.start('owner','thread',key,self.root,command)
        self.assertEqual((self.root/'runs.txt').read_text(),'run\n')
        with self.assertRaises(ValueError):self.manager.start('owner','thread',key,self.root,'echo changed')
        for owner,thread in [('other','thread'),('owner','other')]:
            with self.assertRaises(KeyError):self.manager.get(owner,thread,key)
    def test_output_limit_and_incremental_unicode(self):
        job=CommandJob(self.root,shlex.quote(sys.executable)+" -c "+shlex.quote("import sys;sys.stdout.write('中'*300000)"))
        self.assertTrue(job.done.wait(8));result=job.read()
        self.assertEqual(len(result['output']),MAX_OUTPUT);self.assertTrue(result['reset']);self.assertTrue(result['truncated']);self.assertNotIn('�',result['output'])
    def test_stop_and_gateway_close(self):
        key=self.identifier();self.manager.start('owner','thread',key,self.root,'sleep 90')
        job=self.manager.get('owner','thread',key);job.stop();self.assertTrue(job.done.wait(4));self.assertFalse(job.read()['running'])
        key=self.identifier();self.manager.start('owner','thread',key,self.root,'sleep 90');job=self.manager.get('owner','thread',key)
        self.manager.close();self.assertTrue(job.done.is_set())
    def test_invalid_command_and_parallel_limit(self):
        for command in ('','\0',None,'x'*16001):
            with self.assertRaises(ValueError):self.manager.start('owner','thread',self.identifier(),self.root,command)
        for _ in range(4):self.manager.start('owner','thread',self.identifier(),self.root,'sleep 90')
        with self.assertRaises(ValueError):self.manager.start('owner','thread',self.identifier(),self.root,'echo no')
    def test_timeout(self):
        with patch('bridge.terminal.MAX_SECONDS',.05):
            job=CommandJob(self.root,'sleep 90');self.assertTrue(job.done.wait(4));self.assertIn('上限',job.read()['message'])
    def test_remote_protocol_output_and_stop(self):
        original=subprocess.Popen
        def launch(args,**kwargs):
            self.assertEqual(args[:2],['ssh','-T']);self.assertIn('StrictHostKeyChecking=yes',args)
            return original([sys.executable,'-u','-c',shlex.split(args[-1])[-1]],**kwargs)
        with patch('bridge.terminal.subprocess.Popen',side_effect=launch):
            job=RemoteCommandJob('fixture',self.root,"printf remote; sleep 90")
            deadline=time.monotonic()+5
            while not job.read()['output'] and time.monotonic()<deadline:time.sleep(.02)
            self.assertIn('remote',job.read()['output']);job.stop();self.assertTrue(job.done.wait(5))
    def test_remote_normal_exit_reaps_transport_cleanly(self):
        original=subprocess.Popen
        with patch('bridge.terminal.subprocess.Popen', side_effect=lambda args,**kwargs:original([sys.executable,'-u','-c',shlex.split(args[-1])[-1]],**kwargs)):
            job=RemoteCommandJob('fixture',self.root,'printf completed')
            self.assertTrue(job.done.wait(5));self.assertEqual(job.code,0)
            self.assertEqual(job.process.returncode,0);self.assertEqual(job.read()['output'],'completed')

    def test_remote_disconnect_cleans_command(self):
        from bridge import terminal
        source=Path(terminal.__file__).read_text()+"\nserve_remote("+repr(str(self.root))+", 'sleep 90')"
        process=subprocess.Popen([sys.executable,'-u','-c',source],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        event=json.loads(process.stdout.readline());self.assertTrue(event['running']);process.stdin.close()
        process.wait(timeout=5);process.stdout.close();process.stderr.close();self.assertEqual(process.returncode,0)


import test_bridge as support
class TerminalHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    setUp = support.HttpTests.setUp
    tearDown = support.HttpTests.tearDown
    request = support.HttpTests.request
    login = support.HttpTests.login
    def test_terminal_auth_csrf_and_method(self):
        path='/api/sessions/'+support.THREAD+'/terminal'
        self.assertEqual(self.request('GET',path)[0],401)
        auth=self.login();calls=[]
        self.server.bridge.terminal=lambda thread,owner,action,params:calls.append((thread,owner,action,params)) or {'ok':True}
        self.assertEqual(self.request('GET',path,headers=auth)[0],200);self.assertEqual(calls[-1][2],'info')
        body={'action':'start','id':str(uuid.uuid4()),'command':'echo fixture'}
        self.assertEqual(self.request('POST',path,body,{'Cookie':auth['Cookie']})[0],403)
        self.assertEqual(self.request('POST',path,body,{**auth,'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('POST',path,{**body,'cwd':'/outside'},auth)[0],400)
        self.assertEqual(self.request('POST',path,body,auth)[0],200);self.assertEqual(calls[-1][2],'start')
        self.assertEqual(self.request('GET',path+'?host=wrong',headers=auth)[0],404)
        for body in ({'action':'open','id':str(uuid.uuid4()),'cols':80,'rows':24},
                     {'action':'input','id':str(uuid.uuid4()),'inputId':str(uuid.uuid4()),'data':'pwd\r'},
                     {'action':'resize','id':str(uuid.uuid4()),'cols':100,'rows':30},
                     {'action':'close','id':str(uuid.uuid4())}):
            self.assertEqual(self.request('POST',path,body,{'Cookie':auth['Cookie']})[0],403)
            self.assertEqual(self.request('POST',path,body,auth)[0],200)
            self.assertEqual(calls[-1][2],body['action'])
        self.assertEqual(self.request('GET',path+'?mode=pty&id='+str(uuid.uuid4()),headers=auth)[0],200)
        self.assertEqual(calls[-1][2],'poll')
