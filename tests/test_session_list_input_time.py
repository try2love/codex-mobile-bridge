import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.features.sessions.store import SessionStore
from bridge.clients.manager import DesktopSessions


class InputTime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home/'sessions/chat.jsonl'
        self.path.parent.mkdir()
        self.path.touch()
        self.store = SessionStore(self.home)
        db = sqlite3.connect(self.home/'state_5.sqlite')
        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT,updated_at INT,archived INT,originator TEXT,source TEXT,rollout_path TEXT)')
        db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)', ('one','Chat','/project',9999999999,0,'Codex Desktop','vscode',str(self.path)))
        db.commit();db.close()

    def append(self, kind, payload, stamp='2026-11-22T03:04:05Z'):
        with self.path.open('a') as stream:
            stream.write(json.dumps({'type':kind,'payload':payload,'timestamp':stamp})+'\n')

    def read(self):
        return self.store.list()[0]['lastUserInputAt']

    def test_native_user_time_is_independent_of_activity_and_assistant_output(self):
        self.append('response_item', {'type':'message','role':'user','content':[{'type':'input_text','text':'injected instructions'}]}, '2026-11-22T02:00:00Z')
        self.assertIsNone(self.read())
        self.append('event_msg', {'type':'user_message','message':'input'})
        self.append('response_item', {'type':'message','role':'assistant','content':[{'text':'x'*70000}]}, '2026-11-22T09:00:00Z')
        self.assertEqual(self.read(),'2026-11-22T03:04:05Z')
        with patch('bridge.features.sessions.store.json.loads', side_effect=AssertionError('unchanged log reparsed')):
            self.assertEqual(self.read(),'2026-11-22T03:04:05Z')
        self.append('event_msg', {'type':'user_message','thread_id':'other'}, '2026-11-22T10:00:00Z')
        self.assertEqual(self.read(),'2026-11-22T03:04:05Z')
        self.append('event_msg', {'type':'item_completed','thread_id':'one','item':{'type':'UserMessage'},'completed_at_ms':1795316700000})
        self.assertEqual(self.read(),1795316700000)

    def test_replacement_missing_clock_and_untrusted_paths_do_not_reuse_old_time(self):
        self.append('event_msg', {'type':'user_message'})
        self.assertIsNotNone(self.read())
        self.append('event_msg', {'type':'user_message'}, None)
        self.assertIsNone(self.read())
        replacement=self.path.with_suffix('.replacement')
        replacement.write_text(json.dumps({'type':'event_msg','payload':{'type':'user_message'},'timestamp':'2025-01-01T00:00:00Z'})+'\n')
        os.replace(replacement,self.path)
        self.assertEqual(self.read(),'2025-01-01T00:00:00Z')
        self.assertIsNone(self.store.last_user_input(str(self.home/'outside.jsonl'),'one'))
        self.path.unlink()
        self.assertIsNone(self.read())


class OtherClientInputTime(unittest.TestCase):
    def test_existing_async_detail_cache_uses_user_clock_and_binding_scope(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as temp, \
             closing(DesktopSessions(temp)) as manager:
            adapter=Mock();manager.adapters['deepseek']=adapter
            binding=manager.binding_epochs['deepseek']
            detail={'session':{},'messages':[{'id':'native:0','role':'user','timestamp':'2026-11-22T03:04:05Z'},{'id':'native:1','role':'user'},{'role':'assistant','timestamp':'2026-11-22T09:00:00Z'}]}
            def thread(*args,**kwargs):return SimpleNamespace(start=kwargs['target'])
            with patch('bridge.clients.manager.threading.Thread', side_effect=thread), patch.object(manager,'_read',return_value=detail) as read:
                rows=[{'id':'one','updatedAt':100}]
                manager._list_activity('deepseek',adapter,binding,rows)
                manager._list_activity('deepseek',adapter,binding,rows)
                self.assertEqual(rows[0]['lastUserInputAt'],'2026-11-22T03:04:05Z')
                self.assertEqual(read.call_count,1)
                rows=[{'id':'one','updatedAt':200}]
                manager._list_activity('deepseek',adapter,binding,rows)
                manager._list_activity('deepseek',adapter,binding,rows)
                self.assertEqual(rows[0]['lastUserInputAt'],'2026-11-22T03:04:05Z')
                rows=[{'id':'one','updatedAt':200,'lastUserInputAt':'2026-11-22T10:00:00Z'}]
                manager._list_activity('deepseek',adapter,binding,rows)
                self.assertEqual(rows[0]['lastUserInputAt'],'2026-11-22T10:00:00Z')
                manager.binding_epochs['deepseek']+=1
                rows=[{'id':'one','updatedAt':200}]
                manager._list_activity('deepseek',adapter,manager.binding_epochs['deepseek'],rows)
                self.assertNotIn('lastUserInputAt',rows[0])
