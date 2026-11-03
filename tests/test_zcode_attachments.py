import base64
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bridge.clients.zcode.attachments import attachments, bind_project_files, read_image, remember_project_files
from bridge.clients.zcode.history import saved_detail
from bridge.features.workspace.files import workspace_references
from bridge.features.workspace.history_images import HistoryImages
from bridge.features.workspace.workspace import Workspace


class ZCodeAttachmentTests(unittest.TestCase):
    sid = 'sess_00000000-0000-4000-a000-000000000001'
    artifact = 'tool-result-00000000-0000-4000-a000-000000000002'
    command = '00000000-0000-4000-a000-000000000003'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.home = root / 'v2'
        self.home.mkdir()
        self.work = root / 'project'
        (self.work / 'assets').mkdir(parents=True)
        self.file = self.work / 'assets/icon.png'
        self.png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nmfkAAAAASUVORK5CYII=')
        self.file.write_bytes(self.png)
        self.ref = 'zcode-artifact://' + self.sid + '/' + self.artifact
        self.meta = {'fileName': 'icon.png', 'mime': 'image/png', 'bytes': len(self.png), 'ref': self.ref}
        self.intent = {'sourceCommandId': self.command, 'attachments': [self.meta]}
        with closing(sqlite3.connect(self.home / 'tasks-index.sqlite')) as db:
            db.execute('CREATE TABLE tasks(task_id,title,workspace_path,updated_at,archived,deleted,host_id,provider,workspace_identity,model)')
            db.execute("INSERT INTO tasks VALUES (?, 'Chat', ?, 1, 0, 0, 'local', 'glm', NULL, 'model')", (self.sid, str(self.work)))
            db.commit()
        self.transcript = root / 'cli/db/db.sqlite'
        self.transcript.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute('CREATE TABLE session(id, directory, time_deleted)')
            db.execute('INSERT INTO session VALUES (?,?,NULL)', (self.sid, str(self.work)))
            db.execute('CREATE TABLE message(id,session_id,data,sequence,time_created)')
            db.execute('INSERT INTO message VALUES (?,?,?,1,1)', ('m', self.sid, json.dumps({
                'role': 'user', 'semantics': {'uiVisibility': 'visible'}, 'metadata': {'conversationInputIntent': self.intent}})))
            db.execute('CREATE TABLE part(id,message_id,session_id,data,sequence,time_created)')
            db.execute('INSERT INTO part VALUES (?,?,?,?,1,1)', ('p', 'm', self.sid, json.dumps({'type': 'file', 'url': self.ref, 'mime': 'image/png'})))
            db.commit()
        self.blob = root / 'cli/artifacts' / self.sid / ('prompt-attachment-upload-staging-' + self.artifact + '.txt')
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(b'data:image/png;base64,' + base64.b64encode(self.png))

    def test_saved_image_only_message_and_live_image_have_same_private_preview(self):
        saved = saved_detail(self.home, self.sid)['messages']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['text'], '')
        self.assertEqual(saved[0]['attachments'], attachments(self.sid, [self.meta], self.command))
        images = HistoryImages()
        images.bind('zcode', self.sid, saved)
        public = saved[0]['attachments'][0]
        self.assertNotIn(self.ref, json.dumps(public))
        result, _ = images.preview('zcode', self.sid, public['desktopId'], lambda value: read_image(self.home, self.sid, value))
        self.assertEqual(result['previewBytes'], self.png)
        with closing(sqlite3.connect(self.transcript)) as db:
            db.execute('DELETE FROM part'); db.commit()
        with self.assertRaisesRegex(ValueError, '图片已不属于'):
            images.preview('zcode', self.sid, public['desktopId'], lambda value: read_image(self.home, self.sid, value))

    def test_native_image_cannot_open_cross_session_or_symlink(self):
        source = attachments(self.sid, [self.meta])[0]['_nativeImage']
        with self.assertRaises(ValueError):
            read_image(self.home, self.sid+'_other', source)
        self.blob.unlink()
        try:
            self.blob.symlink_to(self.file)
        except OSError:
            self.skipTest('symlink unavailable')
        with self.assertRaises((OSError, ValueError)):
            read_image(self.home, self.sid, source)

    def test_only_the_verified_project_reference_is_locatable(self):
        info = Workspace(self.work).references([{'path': 'assets/icon.png', 'absolutePath': str(self.file)}])
        with closing(sqlite3.connect(':memory:')) as db:
            remember_project_files(db, self.sid, self.command, info)
            # A separate phone upload bearing the exact same name is not a project file.
            message = {'role':'user', 'attachments':attachments(self.sid, [self.meta, self.meta], self.command)}
            bind_project_files(db, self.sid, [message], self.work)
            first, second = message['attachments']
            self.assertEqual((first['type'], first['path']), ('file', str(self.file)))
            self.assertIn('_nativeImage', first)
            self.assertNotIn('path', second)
            self.assertNotIn('_sourceCommand', first)
            files = workspace_references([message], str(self.work))
            self.assertEqual([(f['reference'],f['path']) for f in files], [(str(self.file),'assets/icon.png')])
            for sid, root in ((self.sid+'other', self.work), (self.sid, self.home)):
                message = {'role':'user', 'attachments':attachments(self.sid, [self.meta], self.command)}
                bind_project_files(db, sid, [message], root)
                self.assertNotIn('path', message['attachments'][0])
