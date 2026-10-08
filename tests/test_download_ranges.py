import hashlib
import http.client
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bridge.remote import RemoteStore
from bridge.service import Bridge
from bridge.workspace import Workspace, MAX_TRANSFER, operate
import test_bridge as support


class DownloadSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp')
        self.root = Path(self.temp.name)
        self.data = bytes(range(256)) * 4097
        self.path = self.root / 'example.bin'
        self.path.write_bytes(self.data)

    def tearDown(self): self.temp.cleanup()

    def test_snapshot_retains_only_requested_range_and_survives_source_write(self):
        with Workspace(self.root).download('example.bin', 'bytes=65530-65600') as (meta, stream):
            self.assertEqual(meta['etag'], '"sha256-' + hashlib.sha256(self.data).hexdigest() + '"')
            self.assertEqual(os.fstat(stream.fileno()).st_size, 71)
            self.path.write_bytes(b'changed')
            self.assertEqual(stream.read(), self.data[65530:65601])

    def test_concurrent_write_during_hash_is_rejected(self):
        digest = hashlib.sha256()
        modified = False
        def update(data):
            nonlocal modified
            digest.update(data)
            if not modified:
                modified = True
                with self.path.open('ab') as writer: writer.write(b'x')
        with patch('bridge.workspace.hashlib.sha256', return_value=SimpleNamespace(update=update, hexdigest=digest.hexdigest)):
            with self.assertRaisesRegex(ValueError, '变化|限制'):
                with Workspace(self.root).download('example.bin', 'bytes=0-9'): pass

    def test_ssh_stream_executes_secure_workspace_source_and_transfers_only_range(self):
        store = RemoteStore('fixture-host'); store.get = lambda _: {'cwd': str(self.root)}
        bridge = SimpleNamespace(host='remote:fixture', store=store)
        original_run = subprocess.run
        def run(command, **kwargs):
            self.assertIn('StrictHostKeyChecking=yes', command)
            self.assertIn('BatchMode=yes', command)
            self.assertNotIn('capture_output', kwargs)
            return original_run([sys.executable, '-'], **kwargs)
        with patch('bridge.remote.subprocess.run', side_effect=run):
            with Bridge.workspace(bridge, support.THREAD, 'download-stream',
                    {'path': 'example.bin', 'range_header': 'bytes=20-39'}) as (meta, stream):
                etag = meta['etag']
                self.assertEqual(meta['status'], 206)
                self.assertEqual(stream.read(), self.data[20:40])
                self.assertLess(os.fstat(stream.fileno()).st_size, 1024)
            self.path.write_bytes(b'new content')
            with Bridge.workspace(bridge, support.THREAD, 'download-stream',
                    {'path': 'example.bin', 'range_header': 'bytes=2-3', 'if_range': etag}) as (meta, stream):
                self.assertEqual(meta['status'], 200)
                self.assertEqual(stream.read(), b'new content')
            with self.assertRaises(PermissionError):
                with Bridge.workspace(bridge, support.THREAD, 'download-stream', {'path': '../outside'}): pass


class DownloadHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    request = support.HttpTests.request
    login = support.HttpTests.login

    def setUp(self):
        support.HttpTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp')
        self.root = Path(self.temp.name)
        self.server.bridge.workspace = lambda thread, action, params: operate(self.root, action, params)
        self.data = bytes(range(256)) * 8200
        self.file = self.root / 'example.bin'; self.file.write_bytes(self.data)
        self.url = '/api/sessions/' + support.THREAD + '/workspace/download?path=example.bin'

    def tearDown(self):
        support.HttpTests.tearDown(self)
        self.temp.cleanup()

    def get(self, headers, url=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.request('GET', url or self.url, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_chunk_reconstruction_pause_and_resume_with_strong_validator(self):
        auth = self.login(); output = bytearray(); etag = None
        for offset in range(0, len(self.data), 1024 * 1024):
            status, headers, data = self.get({**auth, 'Range': 'bytes=%d-%d' % (offset, offset + 1024 * 1024 - 1),
                                            **({'If-Range': etag} if etag else {})})
            self.assertEqual(status, 206)
            self.assertEqual(headers['Accept-Ranges'], 'bytes')
            self.assertEqual(int(headers['Content-Length']), len(data))
            self.assertEqual(headers['Content-Range'], 'bytes %d-%d/%d' % (offset, offset + len(data) - 1, len(self.data)))
            self.assertIn('example.bin', headers['Content-Disposition'])
            self.assertEqual(headers['ETag'], etag or '"sha256-' + hashlib.sha256(self.data).hexdigest() + '"')
            etag = headers['ETag']; output.extend(data)
        self.assertEqual(bytes(output), self.data)

    def test_same_size_same_mtime_changed_content_returns_full_200(self):
        auth = self.login(); _, headers, _ = self.get({**auth, 'Range': 'bytes=0-9'})
        info = self.file.stat(); changed = b'z' * len(self.data)
        self.file.write_bytes(changed); os.utime(self.file, ns=(info.st_atime_ns, info.st_mtime_ns))
        status, updated, body = self.get({**auth, 'Range': 'bytes=10-19', 'If-Range': headers['ETag']})
        self.assertEqual(status, 200)
        self.assertNotEqual(updated['ETag'], headers['ETag'])
        self.assertNotIn('Content-Range', updated)
        self.assertEqual(body, changed)

    def test_suffix_open_ended_invalid_multiple_and_unsatisfiable_ranges(self):
        auth = self.login()
        for value, code, expected in [('bytes=-9', 206, self.data[-9:]),
                ('bytes=%d-' % (len(self.data)-7), 206, self.data[-7:]),
                ('bytes=9-2', 416, b''), ('bytes=-0', 416, b''),
                ('bytes=%d-' % len(self.data), 416, b''),
                ('bytes=0-1,4-5', 200, self.data), ('invalid', 200, self.data)]:
            with self.subTest(value=value):
                status, headers, body = self.get({**auth, 'Range': value})
                self.assertEqual(status, code); self.assertEqual(body, expected)
                if code == 416: self.assertEqual(headers['Content-Range'], 'bytes */' + str(len(self.data)))
        _, headers, _ = self.get(auth)
        for validator in ('W/' + headers['ETag'], 'Wed, 21 Oct 2015 07:28:00 GMT', 'invalid'):
            self.assertEqual(self.get({**auth, 'Range': 'bytes=0-1', 'If-Range': validator})[0], 200)

    def test_empty_file_supports_zero_length_or_unsatisfiable_response(self):
        self.file.write_bytes(b''); auth = self.login()
        self.assertEqual(self.get(auth)[2], b'')
        status, headers, data = self.get({**auth, 'Range': 'bytes=0-1048575'})
        self.assertEqual((status, headers['Content-Range'], data), (416, 'bytes */0', b''))

    def test_auth_host_path_symlink_and_original_size_limits_remain_enforced(self):
        self.assertEqual(self.get({'Range': 'bytes=0-9'})[0], 401)
        auth = self.login()
        self.assertEqual(self.get({**auth, 'Host': 'evil.test', 'Range': 'bytes=0-9'})[0], 403)
        self.assertEqual(self.get(auth, self.url.replace('example.bin', '../outside'))[0], 403)
        link = self.root / 'link'; link.symlink_to(self.file)
        self.assertEqual(self.get(auth, self.url.replace('example.bin', 'link'))[0], 400)
        with self.file.open('wb') as stream: stream.truncate(MAX_TRANSFER + 1)
        self.assertEqual(self.get({**auth, 'Range': 'bytes=0-9'})[0], 400)
        self.server.bridge.artifact = lambda thread, identifier: {'path': self.file, 'name': 'example.bin', 'image': False}
        artifact = '/api/sessions/' + support.THREAD + '/files/' + 'a' * 64
        status, headers, data = self.get({**auth, 'Range': 'bytes=0-9'}, artifact)
        self.assertEqual((status, data), (206, b'\0' * 10))
        self.assertIn(str(MAX_TRANSFER + 1), headers['Content-Range'])
        with self.file.open('wb') as stream: stream.truncate(50 * 1024 * 1024 + 1)
        self.assertEqual(self.get({**auth, 'Range': 'bytes=0-9'}, artifact)[0], 400)

    def test_artifact_parent_symlink_swap_cannot_escape(self):
        folder = self.root / 'folder'; folder.mkdir()
        outside = self.root / 'outside'; outside.mkdir()
        (folder / 'secret').write_bytes(b'allowed'); (outside / 'secret').write_bytes(b'forbidden')
        def artifact(thread, identifier):
            target = folder / 'secret'
            target.unlink(); folder.rmdir(); folder.symlink_to(outside, target_is_directory=True)
            return {'path': target, 'name': 'secret', 'image': False}
        self.server.bridge.artifact = artifact
        status, _, body = self.get({**self.login(), 'Range': 'bytes=0-3'},
                '/api/sessions/' + support.THREAD + '/files/' + 'a' * 64)
        self.assertEqual(status, 400)
        self.assertNotIn(b'forbidden', body)
