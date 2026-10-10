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

from bridge.clients.codex.remote import RemoteStore
from bridge.app.service import Bridge
from bridge.features.workspace.workspace import Workspace, MAX_TRANSFER, operate
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
        with patch('bridge.features.workspace.workspace.hashlib.sha256', return_value=SimpleNamespace(update=update, hexdigest=digest.hexdigest)):
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
        with patch('bridge.clients.codex.remote.subprocess.run', side_effect=run):
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

    def test_auth_host_path_symlink_and_live_size_limits_remain_enforced(self):
        self.assertEqual(self.get({'Range': 'bytes=0-9'})[0], 401)
        auth = self.login()
        self.assertEqual(self.get({**auth, 'Host': 'evil.test', 'Range': 'bytes=0-9'})[0], 403)
        self.assertEqual(self.get(auth, self.url.replace('example.bin', '../outside'))[0], 403)
        link = self.root / 'link'; link.symlink_to(self.file)
        # Windows rejects reparse points explicitly; POSIX rejects O_NOFOLLOW opens.
        status, _, body = self.get(auth, self.url.replace('example.bin', 'link'))
        self.assertEqual(status, 403 if os.name == 'nt' else 400)
        self.assertNotIn(self.data[:32], body)
        with self.file.open('wb') as stream: stream.truncate(MAX_TRANSFER + 1)
        self.assertEqual(self.get({**auth, 'Range': 'bytes=0-9'})[0], 400)
        self.server.bridge.artifact = lambda thread, identifier: {'path': self.file, 'name': 'example.bin', 'image': False}
        artifact = '/api/sessions/' + support.THREAD + '/files/' + 'a' * 64
        self.assertEqual(self.get({**auth, 'Range': 'bytes=0-9'}, artifact)[0], 400)
        from bridge.features.workspace.preferences import transfer_settings
        self.server.transfer_directory = self.root / 'policy'
        transfer_settings(self.server.transfer_directory, {'clickDownloadMiB': 60})
        status, headers, data = self.get({**auth, 'Range': 'bytes=0-9'}, artifact)
        self.assertEqual((status, data), (206, b'\0' * 10))
        self.assertIn(str(MAX_TRANSFER + 1), headers['Content-Range'])
        with self.file.open('wb') as stream: stream.truncate(61 * 1024 * 1024)
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
        self.assertEqual(status, 403 if os.name == 'nt' else 400)
        self.assertNotIn(b'forbidden', body)

class FileActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp')
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self):
        from bridge.features.workspace.workspace import _expire_archives
        _expire_archives(True)
        self.temp.cleanup()

    def test_large_file_explicit_download_and_live_threshold(self):
        from bridge.features.workspace.preferences import transfer_settings
        path = self.root / 'large.bin'
        with path.open('wb') as handle: handle.truncate(MAX_TRANSFER + 10)
        self.assertEqual(transfer_settings(self.root)['clickDownloadMiB'], 20)
        with self.assertRaisesRegex(ValueError, '限制'):
            with self.workspace.download('large.bin'): pass
        transfer_settings(self.root, {'clickDownloadMiB': 30})
        self.assertEqual(transfer_settings(self.root)['clickDownloadMiB'], 30)
        with self.workspace.download('large.bin', 'bytes=0-2', limit=None) as (meta, stream):
            self.assertEqual(stream.read(), b'\0' * 3); tag = meta['etag']
        with patch.object(self.workspace, '_download', side_effect=AssertionError('must reuse snapshot')):
            with self.workspace.download('large.bin', 'bytes=3-5', tag, limit=None) as (meta, stream):
                self.assertEqual(stream.read(), b'\0' * 3)
        with self.assertRaisesRegex(ValueError, '限制'):
            with self.workspace.download('large.bin', 'bytes=3-5', tag, limit=1): pass
        for value in ({'clickDownloadMiB': True}, {'clickDownloadMiB': 0}, {'clickDownloadMiB': 2, 'extra': True}):
            with self.assertRaises(ValueError): transfer_settings(self.root, value)

    def test_directory_archive_is_resumable_and_keeps_snapshot(self):
        import io, zipfile
        folder = self.root / 'reports'; folder.mkdir()
        (folder / 'empty').mkdir(); (folder / 'data.txt').write_text('contents')
        with self.workspace.archive('reports', 'bytes=0-31') as (meta, stream):
            data = stream.read(); tag = meta['etag']; size = meta['size']
        (folder / 'data.txt').write_text('new contents')
        with patch.object(self.workspace, '_archive_snapshot', side_effect=AssertionError('must not rebuild')):
            with self.workspace.archive('reports', 'bytes=32-', tag) as (meta, stream): data += stream.read()
        self.assertEqual(len(data), size)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read('reports/data.txt'), b'contents')
            self.assertIn('reports/empty/', archive.namelist())

    def test_archive_rejects_windows_reparse_attributes(self):
        folder = self.root / 'folder'; folder.mkdir(); (folder / 'file').write_text('test')
        original = os.stat
        def reparse(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == 'file' and kwargs.get('follow_symlinks') is False:
                return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
            return result
        original_lstat = Path.lstat
        def reparse_lstat(path, *args, **kwargs):
            result = original_lstat(path, *args, **kwargs)
            if path == folder / 'file':
                return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
            return result
        with patch('bridge.features.workspace.workspace.os.stat', side_effect=reparse), \
                patch.object(Path, 'lstat', reparse_lstat):
            with self.assertRaises(PermissionError):
                with self.workspace.archive('folder'): pass

    def test_archive_and_info_reject_links_and_outside_paths(self):
        (self.root / 'folder').mkdir(); (self.root / 'folder' / 'link').symlink_to(self.root.parent)
        with self.assertRaises(PermissionError):
            with self.workspace.archive('folder'): pass
        with self.assertRaises(PermissionError): self.workspace.info('../file')
        (self.root / 'linked').symlink_to(self.root / 'folder', target_is_directory=True)
        with self.assertRaises(OSError):
            with self.workspace.archive('linked'): pass

class WorkspaceReferenceTests(unittest.TestCase):
    def test_relative_large_references_stay_inside_own_workspace(self):
        from bridge.features.workspace.files import workspace_references
        with tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp') as directory:
            root = Path(directory); project = root / 'project'; project.mkdir()
            large = project / 'large.bin'
            with large.open('wb') as stream: stream.truncate(55 * 1024 * 1024)
            outside = root / 'outside.txt'; outside.write_text('private')
            (project / 'escape').symlink_to(outside)
            messages = [{'role': 'assistant', 'text': '[large](large.bin) [outside](../outside.txt) [link](escape) [remote](https://example.com/data)'}]
            rows = workspace_references(messages, str(project))
            self.assertEqual([row['path'] for row in rows], ['large.bin'])
            self.assertEqual(workspace_references(messages, ''), [])
            self.assertEqual(workspace_references([{'role': 'user', 'text': '[large](large.bin)'}], str(project)), [])


class TransferPolicyHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    request = support.HttpTests.request
    login = support.HttpTests.login
    setUp = DownloadHttpTests.setUp
    tearDown = DownloadHttpTests.tearDown
    get = DownloadHttpTests.get

    def test_live_policy_requires_auth_csrf_and_applies_without_restart(self):
        import json
        self.server.transfer_directory = self.root / 'policy'
        self.assertEqual(self.request('GET', '/api/file-transfer')[0], 401)
        auth = self.login()
        self.assertEqual(self.request('GET', '/api/file-transfer', headers=auth)[2]['clickDownloadMiB'], 20)
        self.assertEqual(self.request('POST', '/api/file-transfer', {'clickDownloadMiB': 1}, {'Cookie': auth['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', '/api/file-transfer', {'clickDownloadMiB': 1}, auth)[0], 200)
        self.assertEqual(self.request('GET', '/api/file-transfer', headers=auth)[2]['clickDownloadMiB'], 1)
        self.assertEqual(self.get(auth)[0], 400)
        self.assertEqual(self.get({**auth, 'Range': 'bytes=0-9'}, self.url+'&explicit=1')[0], 206)
        status, _, payload = self.get(auth, self.url+'&info=1')
        value = json.loads(payload)
        self.assertEqual(status, 200); self.assertTrue(value['locatable']); self.assertEqual(value['size'], len(self.data))
        self.assertEqual(value['clickDownloadMiB'], 1)
        self.assertEqual(self.get(auth, self.url.replace('example.bin', '../escape')+'&explicit=1')[0], 403)
