import base64
import http.client
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

import test_bridge
from bridge.clients.uploads import DesktopUploads

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8XcAAAAASUVORK5CYII=')


class DesktopUploadHTTP(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def setUp(self):
        test_bridge.HttpTests.setUp(self)
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.server.desktop_uploads = DesktopUploads(temporary.name)
        self.manager = self.server.desktop_sessions = Mock()
        self.manager.call.return_value = {'session': {'id': 'native'}}

    def binary(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        connection.request(method, path, data, {'Origin': self.origin, **(headers or {})})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_authenticated_upload_preview_and_resolve_are_scoped_to_provider_and_session(self):
        identifier = str(uuid.uuid4())
        route = '/api/desktop-sessions/claude/uploads'
        path = route+'?sessionId=code%3Anative&id='+identifier+'&name=photo.png'
        self.assertEqual(self.binary('POST', path, PNG)[0], 401)
        headers = self.login()
        self.assertEqual(self.binary('POST', path, PNG, {'Cookie': headers['Cookie']})[0], 403)
        result = self.binary('POST', path, PNG, headers)
        self.assertEqual(result[0], 200, result[2])
        self.assertEqual(json.loads(result[2])['image'], 'image/png')
        preview = route+'/'+identifier+'/preview?sessionId=code%3Anative'
        self.assertEqual(self.binary('GET', preview, headers=headers)[2], PNG)
        status,saved_headers,saved=self.binary('GET',preview+'&download=1',headers=headers)
        self.assertEqual(status,200);self.assertEqual(saved,PNG)
        self.assertTrue(saved_headers['Content-Disposition'].startswith('attachment;'))
        for alternate in (preview.replace('claude', 'deepseek'), preview.replace('native', 'other')):
            self.assertEqual(self.binary('GET', alternate, headers=headers)[0], 400)
        body = {'id': str(uuid.uuid4()), 'sessionId': 'code:native', 'text': 'Inspect', 'attachments': [identifier]}
        self.assertEqual(self.request('POST', '/api/desktop-sessions/claude/send', body, headers)[0], 200)
        args = self.manager.call.call_args.args
        self.assertEqual(args[:3], ('claude', 'send', 'code:native'))
        self.assertEqual(args[3]['resolvedImages'], [{'url': 'data:image/png;base64,'+base64.b64encode(PNG).decode(), 'name': 'photo.png'}])
        self.assertNotIn('attachments', args[3])

    def test_native_history_preview_remains_authenticated_and_read_only(self):
        from bridge.features.workspace.history_images import HistoryImages
        images = HistoryImages()
        rows = [{'attachments':[{'_nativeImage':{'event':'e','part':0}}]}]
        images.bind('claude','code:native',rows)
        identifier = rows[0]['attachments'][0]['desktopId']
        self.manager.image_preview.side_effect = lambda provider, sid, image: images.preview(provider,sid,image,
            lambda _: {'mediaType':'image/png','data':base64.b64encode(PNG).decode()})
        route = '/api/desktop-sessions/claude/images/'+identifier+'?sessionId='
        self.assertEqual(self.binary('GET',route+'code%3Anative')[0],401)
        headers = self.login()
        status, _, raw = self.binary('GET',route+'code%3Anative',headers=headers)
        self.assertEqual(status,200);self.assertEqual(raw,PNG)
        status,saved_headers,saved=self.binary('GET',route+'code%3Anative&download=1',headers=headers)
        self.assertEqual(status,200);self.assertEqual(saved,PNG)
        self.assertTrue(saved_headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(self.binary('GET',route+'other&download=1',headers=headers)[0],400)
        self.assertEqual(self.binary('GET',route+'other',headers=headers)[0],400)
        self.assertEqual(self.request('POST',route+'code%3Anative',{},headers)[0],405)

    def test_clients_cannot_inject_local_paths_or_resolved_content(self):
        headers = self.login()
        for key in ('resolvedImages', 'resolvedFiles'):
            body = {'sessionId': 'code:native', 'id': str(uuid.uuid4()), 'text': 'Inspect', key: [{'path': '/etc/passwd'}]}
            self.assertEqual(self.request('POST', '/api/desktop-sessions/claude/send', body, headers)[0], 400)
        self.manager.call.assert_not_called()

    def test_file_attachments_do_not_silently_disappear(self):
        headers = self.login();identifier = str(uuid.uuid4())
        path = '/api/desktop-sessions/deepseek/uploads?sessionId=native&id='+identifier+'&name=note.txt'
        self.assertEqual(self.binary('POST', path, b'example', headers)[0], 200)
        result = self.request('POST', '/api/desktop-sessions/deepseek/send', {'sessionId': 'native', 'id': str(uuid.uuid4()), 'text': 'Inspect', 'attachments': [identifier]}, headers)
        self.assertEqual(result[0], 400)
        self.assertIn('文件传输', result[2]['error'])

    def test_local_claude_cli_resolves_private_file_and_image_only_from_ledger(self):
        self.manager.call.return_value = {'session': {'id': 'native', 'backend': 'cli', 'host': 'local'}}
        headers = self.login()
        identifiers = [str(uuid.uuid4()), str(uuid.uuid4())]
        route = '/api/desktop-sessions/claude/uploads?sessionId=code%3Anative&id='
        for identifier, name, data in zip(identifiers, ('note.txt', 'photo.png'), (b'private fixture', PNG)):
            self.assertEqual(self.binary('POST', route+identifier+'&name='+name, data, headers)[0], 200)
        body = {'id': str(uuid.uuid4()), 'sessionId': 'code:native', 'text': '', 'attachments': identifiers}
        self.assertEqual(self.request('POST', '/api/desktop-sessions/claude/send', body, headers)[0], 200)
        resolved = self.manager.call.call_args.args[3]
        self.assertEqual(len(resolved['resolvedImages']), 1)
        self.assertEqual(resolved['resolvedFiles'][0]['name'], 'note.txt')
        self.assertEqual(Path(resolved['resolvedFiles'][0]['path']).read_bytes(), b'private fixture')
        self.assertNotIn('attachments', resolved)

    def test_remote_claude_cli_file_is_resolved_from_ledger_before_transport(self):
        self.manager.call.return_value = {'session': {'id': 'native', 'backend': 'cli', 'host': 'remote'}}
        headers = self.login(); identifier = str(uuid.uuid4())
        route = '/api/desktop-sessions/claude/uploads?sessionId=code%3Anative&id='+identifier+'&name=note.txt'
        self.assertEqual(self.binary('POST', route, b'private fixture', headers)[0], 200)
        result = self.request('POST', '/api/desktop-sessions/claude/send',
                              {'id': str(uuid.uuid4()), 'sessionId': 'code:native', 'text': '', 'attachments': [identifier]}, headers)
        self.assertEqual(result[0], 200)
        resolved = self.manager.call.call_args.args[3]['resolvedFiles'][0]
        self.assertEqual(resolved['id'], identifier)
        self.assertEqual(Path(resolved['path']).read_bytes(), b'private fixture')

    def test_missing_conversation_does_not_write_an_attachment(self):
        self.manager.call.return_value = {'session': None}
        headers = self.login();identifier = str(uuid.uuid4())
        path = '/api/desktop-sessions/claude/uploads?sessionId=missing&id='+identifier+'&name=photo.png'
        self.assertEqual(self.binary('POST', path, PNG, headers)[0], 400)
        self.assertFalse(self.server.desktop_uploads.store.root.exists())

    def test_attachment_store_unavailable_is_reported_as_service_unavailable(self):
        self.server.desktop_uploads = None
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/desktop-sessions/claude/send', {'sessionId':'code:native','text':'Hi','attachments':[]}, headers)[0], 503)
