import base64
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bridge.workspace import Workspace, operate, MAX_TRANSFER, MAX_PREVIEW
from bridge.service import Bridge
from bridge.remote import RemoteStore
import test_bridge as support


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp')
        self.root = Path(self.temp.name) / 'project'; self.root.mkdir()
        self.work = Workspace(self.root)
        (self.root / 'folder').mkdir(); (self.root / 'hello.txt').write_bytes('你好\nworkspace🙂'.encode('utf-8'))
        (self.root / '.hidden').write_text('hidden')

    def tearDown(self): self.temp.cleanup()

    def test_listing_hidden_search_and_paging(self):
        self.assertEqual([x['name'] for x in self.work.listing()['entries']], ['folder', 'hello.txt'])
        self.assertEqual(self.work.listing(hidden=True)['total'], 3)
        self.assertEqual(self.work.listing(search='HELLO')['entries'][0]['path'], 'hello.txt')
        for i in range(205): (self.root / ('page-%03d' % i)).touch()
        page = self.work.listing(search='page-'); self.assertEqual(len(page['entries']), 200)
        self.assertEqual(len(self.work.listing(search='page-', offset=page['nextOffset'])['entries']), 5)

    def test_read_text_image_binary_and_large_preview(self):
        self.assertEqual(self.work.read('hello.txt')['text'], '你好\nworkspace🙂')
        (self.root / 'image.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        self.assertEqual(self.work.read('image.png')['kind'], 'image')
        (self.root / 'binary').write_bytes(b'\0\xff')
        self.assertEqual(self.work.read('binary')['kind'], 'binary')
        (self.root / 'big.txt').write_bytes(b'x' * (MAX_PREVIEW + 1))
        self.assertNotIn('text', self.work.read('big.txt'))
        self.assertEqual(base64.b64decode(self.work.read('big.txt', download=True)['data']), b'x' * (MAX_PREVIEW + 1))

    def test_traversal_and_symlinks_never_escape(self):
        outside = Path(self.temp.name) / 'private'; outside.mkdir(); (outside / 'secret').write_text('private')
        (self.root / 'link').symlink_to(outside, target_is_directory=True)
        (self.root / 'secret-link').symlink_to(outside / 'secret')
        for path in ('../private/secret', '/etc/passwd', 'folder/../../private', 'C:\\private', 'file:stream', 'folder//file'):
            with self.assertRaises((ValueError, PermissionError)): operate(str(self.root), 'preview', {'path': path})
        for path in ('link/secret', 'secret-link'):
            with self.assertRaises((ValueError, PermissionError)): operate(str(self.root), 'download', {'path': path})
        with self.assertRaises((ValueError, PermissionError)): operate(str(self.root), 'upload', {'path': 'link/new', 'encoded': 'YQ=='})
        self.assertFalse((outside / 'new').exists())
        self.assertEqual(next(x['kind'] for x in self.work.listing()['entries'] if x['name'] == 'link'), 'blocked')

    def test_atomic_upload_retry_collision_and_git_boundary(self):
        data = base64.b64encode('文件🙂'.encode()).decode()
        self.assertFalse(self.work.upload('folder/new.txt', data)['existing'])
        self.assertTrue(self.work.upload('folder/new.txt', data)['existing'])
        with self.assertRaises(ValueError): self.work.upload('folder/new.txt', 'YQ==')
        self.assertEqual((self.root / 'folder/new.txt').read_text(encoding='utf-8'), '文件🙂')
        with self.assertRaises(PermissionError): self.work.upload('.git/config', data)
        with patch('bridge.workspace.os.link', side_effect=OSError('disk error')):
            with self.assertRaises(ValueError): operate(str(self.root), 'upload', {'path': 'failed', 'encoded': data})
        self.assertFalse((self.root / 'failed').exists()); self.assertEqual(list(self.root.rglob('.bridge-upload-*')), [])

    def test_empty_files_and_limits(self):
        self.work.upload('empty', ''); self.assertEqual(self.work.read('empty')['text'], '')
        with self.assertRaises(ValueError): self.work.upload('big', base64.b64encode(b'x' * (MAX_TRANSFER + 1)).decode())
        with (self.root / 'oversize').open('wb') as f: f.truncate(MAX_TRANSFER + 1)
        with self.assertRaises(ValueError): self.work.read('oversize', download=True)

    def test_file_browsing_uses_metadata_without_activating_desktop(self):
        bridge = SimpleNamespace(host='local', store=SimpleNamespace(get=lambda _: {'cwd': str(self.root)}))
        result = Bridge.workspace(bridge, support.THREAD, 'list', {})
        self.assertEqual(result['project'], 'project')

    def test_remote_operations_execute_on_remote_root_and_preserve_errors(self):
        remote = Path(self.temp.name) / 'remote'; remote.mkdir(); (remote / 'remote.txt').write_text('remote')
        store = RemoteStore('fixture-host'); store.get = lambda _: {'cwd': str(remote)}
        bridge = SimpleNamespace(host='remote:fixture', store=store)
        def execute(alias, source, timeout):
            self.assertEqual(alias, 'fixture-host'); output = io.StringIO()
            with contextlib.redirect_stdout(output): exec(compile(source, '<remote-workspace>', 'exec'), {})
            return json.loads(output.getvalue())
        with patch('bridge.service.ssh_read', side_effect=execute):
            self.assertEqual(Bridge.workspace(bridge, support.THREAD, 'preview', {'path': 'remote.txt'})['text'], 'remote')
            with self.assertRaises(ValueError): Bridge.workspace(bridge, support.THREAD, 'preview', {'path': '../project/hello.txt'})


class WorkspaceHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    request = support.HttpTests.request
    login = support.HttpTests.login

    def setUp(self):
        support.HttpTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp'); self.root = Path(self.temp.name)
        self.server.bridge.workspace = lambda thread, action, params: operate(str(self.root), action, params)
        self.path = '/api/sessions/' + support.THREAD + '/workspace'

    def tearDown(self): support.HttpTests.tearDown(self); self.temp.cleanup()

    def raw(self, path, data, headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request('POST', path, body=data, headers={'Origin': self.origin, **headers})
        response = connection.getresponse(); result = response.status, json.loads(response.read()); connection.close(); return result

    def test_auth_csrf_and_origin_required_for_upload(self):
        self.assertEqual(self.request('GET', self.path)[0], 401)
        auth = self.login(); target = self.path + '/upload?path=test.txt'
        self.assertEqual(self.raw(target, b'text', {'Cookie': auth['Cookie']})[0], 403)
        self.assertEqual(self.raw(target, b'text', {**auth, 'Origin': 'https://evil.example'})[0], 403)
        self.assertFalse((self.root / 'test.txt').exists())
        self.assertEqual(self.raw(target, b'text', auth)[0], 200)
        self.assertEqual(self.request('GET', self.path + '/preview?path=test.txt', headers=auth)[2]['text'], 'text')

    def test_authenticated_binary_download_and_no_overwrite(self):
        auth = self.login(); data = b'\0binary\xff'; target = self.path + '/upload?path=test.apk'
        self.assertEqual(self.raw(target, data, auth)[0], 200)
        self.assertEqual(self.raw(target, b'changed', auth)[0], 400)
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        connection.request('GET', self.path + '/download?path=test.apk', headers=auth)
        response = connection.getresponse(); self.assertEqual(response.status, 200)
        self.assertIn('attachment', response.getheader('Content-Disposition')); self.assertEqual(response.read(), data); connection.close()
        self.assertEqual(self.request('GET', self.path + '/download?path=../outside', headers=auth)[0], 403)

    def test_static_assets_and_invalid_host(self):
        auth = self.login()
        self.assertEqual(self.request('GET', self.path + '?host=wrong', headers=auth)[0], 404)
        self.assertEqual(self.request('POST', self.path, {}, auth)[0], 405)
