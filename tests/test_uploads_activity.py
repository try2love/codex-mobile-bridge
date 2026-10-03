import base64
import copy
import http.client
import json
import time
import unittest
import uuid
from pathlib import Path
import hashlib
from unittest.mock import patch

import test_bridge as support
from bridge.uploads import Uploads, MAX_FILE
from bridge.ipc import IPCError
from bridge.remote import upload_file

THREAD = support.THREAD
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR7kAAAAASUVORK5CYII=')


class UploadActivityTests(unittest.TestCase):
    setUp = support.IntegrationTests.setUp
    tearDown = support.IntegrationTests.tearDown

    def upload(self, name='报告.txt', data=b'fixture attachment'):
        return self.bridge.upload(THREAD, str(uuid.uuid4()), name, data)

    def calls(self, method='thread-follower-start-turn'):
        return [r for r in self.fixture.requests if r['method'] == method]

    def test_multiple_files_and_image_only_use_original_owner_and_native_image_input(self):
        file, image = self.upload(), self.upload('photo.png', PNG)
        identifier = str(uuid.uuid4())
        self.assertEqual(self.fixture.requests, [])
        result = self.bridge.send(THREAD, '', identifier, attachments=[file['id'], image['id']])
        self.assertEqual(result['status'], 'accepted')
        call = self.calls()[0]
        self.assertEqual(call['targetClientId'], 'owner')
        turn = call['params']['turnStart']
        self.assertEqual(turn['request']['input'][1]['type'], 'localImage')
        self.assertEqual(Path(turn['request']['input'][1]['path']).read_bytes(), PNG)
        self.assertEqual(turn['request']['input'][0]['text'], '')
        self.assertNotIn('Attached files', turn['request']['input'][0]['text'])
        self.assertEqual(turn['context']['fileAttachments'], [
            {'path': self.bridge.uploads.get(THREAD, file['id'])['path'], 'label': '报告.txt'},
            {'path': self.bridge.uploads.get(THREAD, image['id'])['path'], 'label': 'photo.png'},
        ])
        self.assertTrue(turn['context']['inheritThreadSettings'])
        self.bridge.send(THREAD, '', identifier, attachments=[file['id'], image['id']])
        self.assertEqual(len(self.calls()), 1)
        with self.assertRaises(ValueError): self.bridge.send(THREAD, '', identifier, attachments=[file['id']])

    def test_queue_steer_and_goal_keep_attachments_and_objective(self):
        file = self.upload()
        self.fixture.state['threadRuntimeStatus']['type'] = 'active'
        session = self.bridge.session(THREAD)
        identifier = str(uuid.uuid4())
        self.bridge.send(THREAD, 'later', identifier, 'queue', attachments=[file['id']])
        self.assertEqual(self.bridge.view(THREAD)['submissions'][0]['attachments'], ['报告.txt'])
        self.bridge.send(THREAD, 'steer', str(uuid.uuid4()), 'steer', attachments=[file['id']])
        self.assertEqual(self.calls('thread-follower-steer-turn')[0]['params']['input'][0]['text'], 'steer')
        self.assertIn('报告.txt', json.dumps(self.calls('thread-follower-steer-turn')[0]['params']['restoreMessage']['context'], ensure_ascii=False))
        session.state['threadRuntimeStatus']['type'] = 'idle'
        key = THREAD+':'+identifier
        self.bridge._send_queued(session, key, self.bridge.submissions[key])
        self.assertEqual(self.calls()[0]['params']['turnStart']['context']['fileAttachments'][0]['label'], '报告.txt')
        with self.assertRaises(ValueError):
            self.bridge.send(THREAD, 'Exact objective', str(uuid.uuid4()), work_mode='goal', attachments=[file['id']])
        self.assertEqual(len(self.calls('thread-follower-start-turn')), 1)

    def test_upload_retry_and_host_chat_isolation(self):
        identifier = str(uuid.uuid4())
        first = self.bridge.upload(THREAD, identifier, 'file.txt', b'payload')
        self.assertEqual(first, self.bridge.upload(THREAD, identifier, 'file.txt', b'payload'))
        with self.assertRaises(ValueError): self.bridge.upload(THREAD, identifier, 'file.txt', b'changed')
        for store, thread in [(self.bridge.uploads, str(uuid.uuid4())), (Uploads(self.root/'another-host'), THREAD)]:
            with self.assertRaises(ValueError): store.resolve(thread, [identifier])
        for name in ('../private', 'x/y', 'x\\y', 'line\nbreak'):
            with self.assertRaises(ValueError): self.bridge.upload(THREAD, str(uuid.uuid4()), name, b'bad')
        with self.assertRaises(ValueError): self.upload(data=b'x'*(MAX_FILE+1))
        with self.assertRaises(ValueError): self.bridge.uploads.resolve(THREAD, [identifier]*2)

    def test_unknown_delivery_retains_same_file_and_does_not_resubmit(self):
        file = self.upload();identifier = str(uuid.uuid4())
        with patch.object(self.bridge, '_call', side_effect=IPCError('uncertain')) as call:
            with self.assertRaises(IPCError): self.bridge.send(THREAD, 'use file', identifier, attachments=[file['id']])
            self.assertEqual(self.bridge.send(THREAD, 'use file', identifier, attachments=[file['id']])['status'], 'unknown')
            self.assertEqual(call.call_count, 1)

    def test_remote_upload_is_idempotent_and_paths_are_not_local(self):
        received=[]
        def remote(*args):
            received.append(args);return '/remote/codex/uploads/file.png'
        self.bridge.uploads = Uploads(self.root/'remote-data', remote)
        self.bridge.host = self.fixture.host = 'remote:test'
        file = self.upload('image.png', PNG)
        self.bridge.send(THREAD, 'Read image', str(uuid.uuid4()), attachments=[file['id']])
        call = self.calls()[0]
        self.assertEqual(call['hostId'], 'remote:test')
        self.assertEqual(call['params']['turnStart']['request']['input'][1]['path'], '/remote/codex/uploads/file.png')
        with patch('bridge.remote.ssh_read', return_value={'path':'/remote/path'}) as ssh:
            self.assertEqual(upload_file('test-host', *received[0]), '/remote/path')
            self.assertEqual(ssh.call_args.args[0], 'test-host')
            compile(ssh.call_args.args[1], '<remote-upload>', 'exec')

    def test_activity_follows_visible_chats_without_history_reads_or_activation(self):
        self.bridge.list()
        with patch.object(self.bridge.store, 'history', side_effect=AssertionError('No history for list')), patch('bridge.service.open_in_desktop', side_effect=AssertionError('No navigation')):
            rows = self.bridge.activity([THREAD, str(uuid.uuid4())])
            self.assertEqual(len(rows), 1)
            session = self.bridge.live[THREAD]
            with session.condition:
                self.assertTrue(session.condition.wait_for(lambda: session.connected, timeout=3))
            self.fixture.state['threadRuntimeStatus']['type'] = 'active'
            self.fixture.state['turns'] = [{'turnId': 'run', 'status': 'inProgress', 'items': []}]
            self.fixture.revision += 1;self.fixture.snapshot()
            with session.condition: self.assertTrue(session.condition.wait_for(lambda: session.state['threadRuntimeStatus']['type']=='active', timeout=2))
            self.assertEqual(self.bridge.activity([THREAD])[0]['status'], 'active')
            self.bridge._disconnected()
            self.assertEqual(self.bridge.activity([THREAD])[0]['status'], 'unknown')

    def test_opening_activity_only_session_still_loads_saved_prefix(self):
        self.fixture.state['turnsPagination'] = {'hasLoadedOldest': False}
        self.bridge.list();self.bridge.activity([THREAD]);session=self.bridge.live[THREAD]
        with session.condition: self.assertTrue(session.condition.wait_for(lambda: session.connected, timeout=3))
        with patch.object(self.bridge.store, 'history', return_value={**support.state(), 'turns':[{'turnId':'old','status':'completed','items':[{'type':'agentMessage','id':'reply','text':'Saved prefix'}]}]}):
            self.bridge.session(THREAD, background=True)
            with session.condition: self.assertTrue(session.condition.wait_for(lambda: session.saved_view is not None, timeout=2))
        self.assertEqual(session.view()['turns'][0]['id'], 'old')

    def test_cold_activity_session_loads_history_without_waiting_for_subscription_retry(self):
        self.fixture.loaded = False
        self.bridge.list();self.bridge.activity([THREAD]);session=self.bridge.live[THREAD]
        session.retry_at = time.monotonic() + 15
        with patch.object(self.bridge.store, 'history', return_value=support.state()), patch('bridge.service.open_in_desktop', side_effect=AssertionError('No navigation')):
            self.bridge.session(THREAD, background=True)
            with session.condition: self.assertTrue(session.condition.wait_for(lambda: session.saved_view is not None, timeout=2))
        self.assertFalse(session.connected)

    def test_thumbnail_metadata_and_preview_variants_are_scoped(self):
        image=self.upload('photo.png',PNG)
        thumb=b'RIFF\x24\x00\x00\x00WEBPVP8 \x14\x00\x00\x00' + b'0'*12
        row=self.bridge.uploads.set_thumb(THREAD,image['id'],thumb,6,4)
        self.assertEqual(row['thumb'],'image/webp');self.assertEqual((row['thumbWidth'],row['thumbHeight']),(6,4))
        preview,variant=self.bridge.uploads.preview(THREAD,image['id'])
        self.assertEqual(variant,'thumb');self.assertEqual(Path(preview['previewPath']).read_bytes(),thumb)
        original,variant=self.bridge.uploads.preview(THREAD,image['id'],'original')
        self.assertEqual(variant,'original');self.assertEqual(Path(original['previewPath']).read_bytes(),PNG)
        with self.assertRaises(ValueError):self.bridge.uploads.set_thumb(THREAD,image['id'],b'not image',6,4)
        with self.assertRaises(ValueError):self.bridge.uploads.set_thumb(THREAD,image['id'],thumb,0,4)
        text=self.upload('report.txt')
        with self.assertRaises(ValueError):self.bridge.uploads.set_thumb(THREAD,text['id'],thumb,6,4)

    def test_thumbnail_with_user_thumb_filename_does_not_replace_original(self):
        original=b'RIFF\x24\x00\x00\x00WEBPVP8 \x14\x00\x00\x00' + b'a'*12
        thumbnail=b'RIFF\x24\x00\x00\x00WEBPVP8 \x14\x00\x00\x00' + b'b'*12
        image=self.upload('thumb.webp',original)
        row=self.bridge.uploads.set_thumb(THREAD,image['id'],thumbnail,6,4)
        row=self.bridge.uploads.get(THREAD,image['id'])
        internal=Path(row['localPath']).parent
        self.assertEqual(internal.name,'internal')
        self.assertEqual((internal/'original.webp').read_bytes(),original)
        self.assertEqual((internal/'thumb.webp').read_bytes(),thumbnail)
        original_preview,variant=self.bridge.uploads.preview(THREAD,image['id'],'original')
        self.assertEqual(variant,'original')
        self.assertEqual(Path(original_preview['previewPath']).read_bytes(),original)

    def test_upload_internal_directory_cannot_escape_upload_root(self):
        thread=str(uuid.uuid4());identifier=str(uuid.uuid4())
        folder=self.bridge.uploads.root/thread/identifier
        folder.mkdir(parents=True)
        outside=self.root/'outside-uploads'
        outside.mkdir()
        folder.rmdir();folder.symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):
            self.bridge.uploads.put(thread,identifier,'photo.png',PNG)
        self.assertFalse((outside/'internal').exists())

    def test_seven_day_gc_keeps_pending_attachments(self):
        import os,time
        old=self.upload('old.png',PNG);pending=self.upload('pending.txt')
        root=self.bridge.uploads.root
        for identifier in (old['id'],pending['id']):
            meta=root/THREAD/(identifier+'.json');row=json.loads(meta.read_text())
            row['createdAt']=time.time()-8*24*60*60;meta.write_text(json.dumps(row))
        removed=self.bridge.uploads.collect(protected=[pending['id']])
        self.assertEqual(removed,1)
        with self.assertRaises(ValueError):self.bridge.uploads.get(THREAD,old['id'])
        self.assertEqual(self.bridge.uploads.get(THREAD,pending['id'])['id'],pending['id'])

    def test_preview_and_path_mapping_are_scoped_to_uploaded_images(self):
        image = self.upload('photo.png', PNG)
        text = self.upload('report.txt')
        stored = self.bridge.uploads.get(THREAD, image['id'])
        preview,variant=self.bridge.uploads.preview(THREAD, image['id']);self.assertEqual(variant, 'fallback-original');self.assertEqual(preview['image'], 'image/png')
        with self.assertRaises(KeyError):
            self.bridge.uploads.preview(THREAD, text['id'])
        with self.assertRaises(ValueError):
            self.bridge.uploads.preview(str(uuid.uuid4()), image['id'])
        mapped = self.bridge.uploads.by_path(THREAD, [stored['localPath'], '/outside/private.png'])
        self.assertEqual(mapped[str(Path(stored['localPath']).resolve())]['id'], image['id'])

    def test_timeline_annotates_only_ledger_image_attachments(self):
        image = self.upload('photo.png', PNG)
        stored = self.bridge.uploads.get(THREAD, image['id'])
        desktop_image = self.root/'desktop-image.png'
        desktop_image.write_bytes(PNG)
        session = self.bridge.session(THREAD)
        self.fixture.state['turns'] = [{'turnId': 'turn', 'status': 'completed', 'items': [
            {'id': 'message', 'type': 'userMessage', 'clientId': 'upload-submission', 'content': [
                {'type': 'text', 'text': 'Read this'},
                {'type': 'localImage', 'path': stored['localPath']},
                {'type': 'localImage', 'path': str(desktop_image)},
            ]}, {'id': 'reply', 'type': 'agentMessage', 'text': 'Reply'},
            {'id': 'steer', 'type': 'steeringUserMessage', 'client_id': 'steer-submission', 'input': [
                {'type': 'text', 'text': 'Steer'},
            ]}]}]
        with session.condition:
            session.state = copy.deepcopy(self.fixture.state)
            session.changed()
        row = self.bridge.timeline_read(THREAD)['rows'][0]
        self.assertEqual(row['attachments'][0]['uploadId'], image['id'])
        self.assertEqual(row['attachments'][0]['mime'], 'image/png')
        self.assertEqual(row['attachments'][0]['name'], 'photo.png')
        self.assertNotIn('uploadId', row['attachments'][1])
        self.assertIn('desktopId', row['attachments'][1])
        desktop_id=row['attachments'][1]['desktopId']
        preview,variant=self.bridge.desktop_image_preview(THREAD,desktop_id)
        self.assertEqual(variant,'desktop');self.assertEqual(preview['previewMime'],'image/png')
        self.assertEqual(Path(preview['previewPath']).read_bytes(),PNG)
        for attachment in row['attachments']:
            self.assertNotIn('path', attachment)
        self.bridge.submissions[THREAD+':upload-submission'] = {
            'attachmentNames': ['photo.png', 'report.txt'], 'at': 1, 'status': 'accepted', 'text': 'Read this',
        }
        self.bridge.submissions[THREAD+':steer-submission'] = {
            'attachmentNames': ['steer.txt'], 'at': 2, 'status': 'accepted', 'text': 'Steer',
        }
        row = self.bridge.timeline_read(THREAD)['rows'][0]
        self.assertEqual([item.get('name') for item in row['attachments']], ['photo.png', 'desktop-image.png', 'report.txt'])
        self.assertEqual(self.bridge.timeline_read(THREAD)['rows'][1].get('attachments'), None)
        steer_row = self.bridge.timeline_read(THREAD)['rows'][2]
        self.assertEqual([item.get('name') for item in steer_row['attachments']], ['steer.txt'])


class UploadHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    setUp = support.HttpTests.setUp
    tearDown = support.HttpTests.tearDown
    request = support.HttpTests.request
    login = support.HttpTests.login

    def raw(self, headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        self.addCleanup(conn.close)
        conn.request('POST', '/api/sessions/'+THREAD+'/uploads?id='+str(uuid.uuid4())+'&name=image.png', body=PNG, headers={'Origin':self.origin,'Content-Type':'application/octet-stream', **headers})
        response=conn.getresponse();return response.status,json.loads(response.read())

    def test_raw_binary_upload_requires_login_csrf_and_preserves_bytes(self):
        received=[]
        self.server.bridge.upload=lambda *args: received.append(args) or {'id':args[1],'name':args[2],'size':len(args[3]),'image':'image/png'}
        self.assertEqual(self.raw({})[0],401)
        credentials=self.login()
        self.assertEqual(self.raw({'Cookie':credentials['Cookie']})[0],403)
        status,result=self.raw(credentials)
        self.assertEqual(status,200);self.assertEqual(result['size'],len(PNG));self.assertEqual(received[0][-1],PNG)
        self.assertEqual(len(received),1)

    def preview(self, path, headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        self.addCleanup(conn.close)
        conn.request('GET', path, headers=headers)
        response=conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_upload_preview_requires_login_and_serves_only_images(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=str(Path('.tmp').resolve())) as directory:
            image_path=Path(directory)/'photo.png';image_path.write_bytes(PNG)
            image_id=str(uuid.uuid4());text_id=str(uuid.uuid4())
            def preview_bridge(thread, identifier, variant='thumb'):
                if identifier != image_id:
                    raise KeyError('preview is image-only')
                return {'id':image_id,'name':'photo.png','image':'image/png','localPath':str(image_path),'previewPath':str(image_path),'previewMime':'image/png','previewSha256':'png'}, 'original'
            self.server.bridge.upload_preview=preview_bridge
            path='/api/sessions/'+THREAD+'/uploads/'
            credentials=self.login()
            self.assertEqual(self.preview(path+image_id+'/preview', {})[0], 401)
            status, headers, body = self.preview(path+image_id+'/preview', credentials)
            self.assertEqual(status,200);self.assertEqual(hashlib.sha256(body).hexdigest(),hashlib.sha256(PNG).hexdigest())
            self.assertEqual(headers['Content-Type'],'image/png')
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
            self.assertIn("filename*=UTF-8''photo.png", headers['Content-Disposition'])
            self.assertEqual(self.preview(path+text_id+'/preview', credentials)[0],404)

    def test_desktop_image_preview_requires_login_and_serves_exact_file(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=str(Path('.tmp').resolve())) as directory:
            image_path=Path(directory)/'desktop.png';image_path.write_bytes(PNG)
            image_id=hashlib.sha256(str(image_path.resolve()).encode()).hexdigest()
            def preview_bridge(thread,identifier):
                if identifier != image_id:raise KeyError('missing desktop image')
                return {'name':'desktop.png','previewPath':str(image_path),'previewMime':'image/png','previewSha256':'png'},'desktop'
            self.server.bridge.desktop_image_preview=preview_bridge
            path='/api/sessions/'+THREAD+'/desktop-images/'+image_id
            credentials=self.login()
            self.assertEqual(self.preview(path,{})[0],401)
            status,headers,body=self.preview(path,credentials)
            self.assertEqual(status,200);self.assertEqual(body,PNG);self.assertEqual(headers['Content-Type'],'image/png')
            self.assertIn('private',headers['Cache-Control']);self.assertIn('immutable',headers['Cache-Control'])
            self.assertEqual(self.preview(path.replace(image_id,'0'*64),credentials)[0],404)

    def upload_preview(self,path,headers,data=None,method='GET'):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3);self.addCleanup(conn.close)
        conn.request(method,path,body=data,headers=headers)
        response=conn.getresponse();return response.status,dict(response.getheaders()),response.read()

    def test_thumbnail_upload_and_cached_preview_require_auth(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=str(Path('.tmp').resolve())) as directory:
            image_path=Path(directory)/'photo.png';image_path.write_bytes(PNG)
            thumb_path=Path(directory)/'thumb.webp';thumb_path.write_bytes(b'RIFF\x24\x00\x00\x00WEBPVP8 \x14\x00\x00\x00'+b'0'*12)
            image_id=str(uuid.uuid4())
            self.server.bridge.upload=lambda *args: {'id':args[1],'name':args[2],'size':len(args[3]),'image':'image/png'}
            self.server.bridge.upload_thumb=lambda *args: {'id':args[1],'thumb':'image/webp','thumbWidth':args[3],'thumbHeight':args[4]}
            def fallback_bridge(thread,identifier,variant='thumb'):
                if identifier != image_id:raise KeyError('preview is image-only')
                return {'id':image_id,'name':'photo.png','image':'image/png','previewPath':str(image_path),'previewMime':'image/png','previewSha256':'original'}, 'fallback-original'
            def preview_bridge(thread,identifier,variant='thumb'):
                if identifier != image_id:
                    raise KeyError('preview is image-only')
                if variant == 'original':
                    return {'id':image_id,'name':'photo.png','image':'image/png','previewPath':str(image_path),'previewMime':'image/png','previewSha256':'original'}, 'original'
                return {'id':image_id,'name':'photo.png','image':'image/png','previewPath':str(thumb_path),'previewMime':'image/webp','previewSha256':'thumb'}, 'thumb'
            self.server.bridge.upload_preview=preview_bridge
            thumb=b'RIFF\x24\x00\x00\x00WEBPVP8 \x14\x00\x00\x00'+b'0'*12
            path='/api/sessions/'+THREAD+'/uploads/'+image_id+'/thumb?width=6&height=4'
            headers={'Origin':self.origin,'Content-Type':'image/webp'}
            self.assertEqual(self.upload_preview(path,{},method='POST')[0],403)
            credentials=self.login()
            fallback='/api/sessions/'+THREAD+'/uploads/'+image_id+'/preview'
            self.server.bridge.upload_preview=fallback_bridge
            status,headers,body=self.upload_preview(fallback,credentials)
            self.assertEqual(status,200);self.assertEqual(body,PNG)
            self.assertEqual(headers['X-Preview-Variant'],'fallback-original')
            self.assertEqual(headers['Cache-Control'],'private, no-store')
            self.server.bridge.upload_preview=preview_bridge
            status,_,body=self.upload_preview(path,{**credentials,'Origin':self.origin,'Content-Type':'image/webp'},data=thumb,method='POST')
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['thumb'],'image/webp')
            preview='/api/sessions/'+THREAD+'/uploads/'+image_id+'/preview'
            status,headers,body=self.upload_preview(preview,credentials)
            self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/webp');self.assertEqual(hashlib.sha256(body).hexdigest(),hashlib.sha256(thumb).hexdigest())
            self.assertIn('private',headers['Cache-Control']);self.assertIn('immutable',headers['Cache-Control'])
            etag=headers['ETag'];status,headers,body=self.upload_preview(preview,{**credentials,'If-None-Match':etag})
            self.assertEqual(status,304);self.assertEqual(body,b'')
            original=preview+'?variant=original'
            status,headers,body=self.upload_preview(original,credentials)
            self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/png');self.assertEqual(body,PNG)
