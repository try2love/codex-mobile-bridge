import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.features.sessions.create import CreationUnavailable, create_empty, rename_thread, open_in_desktop, CreationError
from bridge.clients.codex.remote import AppHosts
from bridge.app.service import Bridge

ROOT = Path(__file__).resolve().parents[1]


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.root = Path(self.temp.name)
        self.bridge = Bridge(self.root, self.root/'data')
        self.bridge.hosts.state = lambda: {'local-projects': {'p': {'id': 'p', 'name': 'Test', 'rootPaths': [str(self.root)]}}}
        self.bridge.ipc.connect = lambda: None
        self.tid = str(uuid.uuid4())

    def test_rename_persists_name_updates_live_view_and_validates_input(self):
        from bridge.app.service import LiveSession
        session = LiveSession(self.tid)
        session.state = {'id': self.tid, 'title': 'Old'}
        self.bridge.live[self.tid] = session
        with patch.object(self.bridge.store, 'get', return_value={'id': self.tid}), patch('bridge.app.service.rename_thread') as rename:
            result = self.bridge.rename(self.tid, '  New title  ')
            self.assertEqual(result['title'], 'New title')
            self.assertEqual(session.view()['title'], 'New title')
            self.assertEqual(rename.call_args.args[2:], (self.tid, 'New title'))
            for value in ('', ' ', 'x'*121, 'x\ny', 'x\x7fy', 'x\x85y', 'x\u2028y', 'x\u2029y', None):
                with self.assertRaises(ValueError):
                    self.bridge.rename(self.tid, value)
            self.assertEqual(rename.call_count, 1)
            rename.side_effect = CreationError('failed')
            with self.assertRaises(CreationError):
                self.bridge.rename(self.tid, 'Rejected')
            self.assertEqual(session.view()['title'], 'New title')

    def test_remote_rename_uses_remote_runtime_and_clears_list_cache(self):
        from bridge.clients.codex.remote import RemoteStore
        self.bridge.store = RemoteStore('test-host')
        self.bridge.store.cache['old'] = 'cached'
        title = 'quoted " title $()'
        with patch.object(self.bridge.store, 'get', return_value={'id': self.tid}), patch('bridge.app.service.ssh_read', return_value={'ok': True}) as remote:
            self.bridge.rename(self.tid, title)
            self.assertEqual(remote.call_args.args[0], 'test-host')
            self.assertIn('rename_thread(runtime, home', remote.call_args.args[1])
            self.assertNotIn(title, remote.call_args.args[1])
            self.assertFalse(self.bridge.store.cache)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def test_duplicate_creation_and_restart_reuse_original_thread(self):
        request = str(uuid.uuid4())
        with patch('bridge.app.service.create_empty', return_value=self.tid) as create, patch('bridge.app.service.open_in_desktop') as opened:
            first = self.bridge.create_chat('local|p', 'Phone test', request)
            second = self.bridge.create_chat('local|p', 'Phone test', request)
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(create.call_count, 1)
            self.assertEqual(opened.call_args.args, (self.tid, 'local'))
            self.bridge.close()
            self.bridge = Bridge(self.root, self.root/'data')
            self.bridge.hosts.projects = lambda: [{'key':'local|p','host':'local','cwd':str(self.root)}]
            self.assertEqual(self.bridge.create_chat('local|p', 'Phone test', request)['id'], self.tid)
            self.assertEqual(create.call_count, 1)
            with self.assertRaises(ValueError):self.bridge.create_chat('local|p', 'Other', request)

    def test_independent_chat_needs_no_saved_project_and_is_idempotent(self):
        self.bridge.hosts.state = lambda: {}
        request = str(uuid.uuid4())
        with patch('bridge.app.service.create_empty', return_value=self.tid) as create, patch('bridge.app.service.open_in_desktop'):
            first = self.bridge.create_chat('local|independent', 'No project', request)
            second = self.bridge.create_chat('local|independent', 'No project', request)
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(create.call_count, 1)
            directory = Path(create.call_args.args[2])
            self.assertTrue(directory.is_dir())
            self.assertEqual(directory, (self.root / 'data/independent-chats' / request).resolve())
        with patch('bridge.app.service.create_empty', side_effect=CreationError('unknown')) as create:
            request = str(uuid.uuid4())
            for _ in range(2):
                with self.assertRaises(CreationError): self.bridge.create_chat('local|independent', 'Unknown', request)
            self.assertEqual(create.call_count, 1)

    def test_independent_directory_failure_is_safe_to_retry(self):
        request = str(uuid.uuid4())
        with patch('bridge.features.sessions.create.Path.mkdir', side_effect=PermissionError('read only')):
            with self.assertRaises(CreationUnavailable): self.bridge.create_chat('local|independent', 'Retry', request)
        self.assertNotIn(request, self.bridge.creations)
        with patch('bridge.app.service.create_empty', return_value=self.tid) as create, patch('bridge.app.service.open_in_desktop'):
            self.bridge.create_chat('local|independent', 'Retry', request)
            self.assertEqual(create.call_count, 1)

    def test_unknown_result_is_not_replayed(self):
        request = str(uuid.uuid4())
        with patch('bridge.app.service.create_empty', side_effect=CreationError('timeout')) as create:
            with self.assertRaises(CreationError):self.bridge.create_chat('local|p', 'Test', request)
            with self.assertRaisesRegex(CreationError, '避免重复'):self.bridge.create_chat('local|p', 'Test', request)
            self.assertEqual(create.call_count, 1)
            self.assertNotIn('id', json.loads(self.bridge.creations_path.read_text())[request])

    def test_failed_desktop_open_keeps_created_id(self):
        with patch('bridge.app.service.create_empty', return_value=self.tid), patch('bridge.app.service.open_in_desktop', side_effect=OSError()):
            result = self.bridge.create_chat('local|p', 'Test', str(uuid.uuid4()))
            self.assertEqual(result['id'], self.tid)
            self.assertFalse(result['opened'])

    def test_blocked_desktop_open_reuses_created_id_after_retry_and_gateway_restart(self):
        request = str(uuid.uuid4())
        with patch('bridge.app.service.create_empty', return_value=self.tid) as create, \
             patch('bridge.app.service.open_in_desktop', side_effect=CreationError('桌面暂不可交互')) as opened:
            for _ in range(2):
                result = self.bridge.create_chat('local|p', 'Test', request)
                self.assertEqual(result['id'], self.tid)
                self.assertFalse(result['opened'])
            self.assertEqual(json.loads(self.bridge.creations_path.read_text())[request]['id'], self.tid)
            self.bridge.close()
            self.bridge = Bridge(self.root, self.root/'data')
            self.bridge.hosts.projects = lambda: [{'key': 'local|p', 'host': 'local', 'cwd': str(self.root)}]
            opened.side_effect = None
            result = self.bridge.create_chat('local|p', 'Test', request)
            self.assertEqual(result['id'], self.tid)
            self.assertTrue(result['opened'])
            create.assert_called_once()

    def test_only_saved_project_and_valid_title_are_accepted(self):
        with patch('bridge.app.service.create_empty') as create:
            for project,title,request in [('unknown','ok',str(uuid.uuid4())),('local|p',' ',str(uuid.uuid4())),('local|p','a'*121,str(uuid.uuid4())),('local|p','ok',None)]:
                with self.assertRaises(ValueError):self.bridge.create_chat(project,title,request)
            create.assert_not_called()

    def test_remote_creation_keeps_host_and_literal_payload(self):
        host='remote-ssh-discovered:test'
        self.bridge.hosts.state=lambda:{'remote-projects':[{'id':'r','hostId':host,'remotePath':'/remote/project'}],
                                      'codex-managed-remote-connections':[{'hostId':host,'alias':'test'}]}
        with patch('bridge.app.service.ssh_read', return_value={'id':self.tid}) as remote, patch('bridge.app.service.open_in_desktop') as opened:
            result=self.bridge.create_chat(host+'|r', 'quoted " title $()', str(uuid.uuid4()))
            self.assertEqual(result['host'],host)
            self.assertEqual(remote.call_args.args[0],'test')
            self.assertNotIn('quoted " title $()',remote.call_args.args[1])
            self.assertIn('create_empty(runtime, home',remote.call_args.args[1])
            opened.assert_called_once_with(self.tid,host)

    def test_runtime_only_initializes_creates_names_and_materializes(self):
        script=self.root/'runtime.py'
        log=self.root/'calls.json'
        script.write_text('''import json,sys
from pathlib import Path
calls=[]
for line in sys.stdin:
 r=json.loads(line);calls.append(r)
 if 'id' not in r:continue
 result={'thread':{'id':''' + repr(self.tid) + '''}} if r['method']=='thread/start' else {}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
Path(''' + repr(str(log)) + ''').write_text(json.dumps(calls))
''',encoding='utf-8')
        real_popen=subprocess.Popen
        def spawn(argv,**kwargs):
            self.assertEqual(argv,['runtime','app-server','--listen','stdio://'])
            if os.name == 'nt':
                self.assertEqual(kwargs.get('creationflags'),subprocess.CREATE_NO_WINDOW)
            return real_popen([sys.executable,str(script)],**kwargs)
        with patch('bridge.features.sessions.create.subprocess.Popen',side_effect=spawn):
            self.assertEqual(create_empty('runtime',self.root,str(self.root),'test'),self.tid)
        calls=json.loads(log.read_text())
        self.assertEqual([c['method'] for c in calls],['initialize','initialized','thread/start','thread/name/set','thread/read'])
        self.assertEqual(calls[-1]['params'],{'threadId':self.tid,'includeTurns':True})
        self.assertEqual(calls[2]['params'],{'cwd':str(self.root),'ephemeral':False})
        with patch('bridge.features.sessions.create.subprocess.Popen',side_effect=spawn):
            rename_thread('runtime', self.root, self.tid, 'Renamed')
        calls=json.loads(log.read_text())
        self.assertEqual([c['method'] for c in calls], ['initialize','initialized','thread/name/set'])
        self.assertEqual(calls[-1]['params'], {'threadId': self.tid, 'name': 'Renamed'})

    def test_deep_link_encodes_host_and_never_invokes_shell(self):
        with patch('bridge.features.sessions.create.sys.platform','darwin'),patch('bridge.features.sessions.create.subprocess.run') as run:
            open_in_desktop(self.tid,'remote:test & other')
            self.assertEqual(run.call_args.args[0],['open','codex://threads/'+self.tid+'?hostId=remote%3Atest+%26+other'])
            self.assertNotIn('shell',run.call_args.kwargs)

    @unittest.skipUnless(os.name == 'nt', 'Windows desktop URL handler')
    def test_windows_deep_link_uses_registered_handler(self):
        with patch('bridge.features.sessions.create.os.startfile') as opened:
            open_in_desktop(self.tid,'remote:test & other')
            opened.assert_called_once_with('codex://threads/'+self.tid+'?hostId=remote%3Atest+%26+other')
