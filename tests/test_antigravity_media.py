import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.hook_ipc import HookIPCServer
from bridge.clients.antigravity.model import gateway_id
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.uploads import DesktopUploads

ROOT = Path(__file__).resolve().parents[1]
CID = '11111111-2222-4333-8444-555555555555'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8XcAAAAASUVORK5CYII=')
PNG_URL = 'data:image/png;base64,' + base64.b64encode(PNG).decode('ascii')


def _create_db(surface_root, rows):
    surface_root.mkdir(parents=True, exist_ok=True)
    db = surface_root / 'conversation_summaries.db'
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            'CREATE TABLE conversation_summaries ('
            'conversation_id TEXT PRIMARY KEY, title TEXT, preview TEXT, step_count INTEGER, '
            'last_modified_time TEXT, workspace_uris TEXT, status TEXT, source TEXT, '
            'project_id TEXT, parent_conversation_id TEXT, nesting_depth INTEGER, '
            'not_fully_idle INTEGER, killed INTEGER, agent_name TEXT)'
        )
        conn.executemany(
            'INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            rows,
        )
        conn.commit()
    finally:
        conn.close()


class AntigravityNativeMediaAndScopeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.directory = Path(self.temp.name) / 'bridge'
        self.root = self.home / '.gemini' / 'antigravity'
        self.workspace = Path(self.temp.name) / 'project'
        self.workspace.mkdir(parents=True)
        self.ref_file = self.workspace / 'main.py'
        self.ref_file.write_text('print(1)\n', encoding='utf-8')
        self.ref_dir = self.workspace / 'src'
        self.ref_dir.mkdir()
        _create_db(self.root, [(
            CID, 'Media Chat', '', 1, '2026-11-19T10:00:00Z',
            json.dumps([self.workspace.resolve().as_uri()]),
            'CASCADE_RUN_STATUS_IDLE', '', '', '', 0, 0, 0, '',
        )])

    def test_send_transmits_native_media_and_path_scope_items(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        sent = []

        def fake_rpc(method, payload=None, *, surface='app'):
            if method == 'SendUserCascadeMessage':
                sent.append(payload)
                return {}
            if method == 'GetAllCascadeTrajectories':
                return {'trajectorySummaries': {CID: {'status': 'CASCADE_RUN_STATUS_IDLE'}}}
            return {}

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, '_model', return_value=None), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            res = adapter.call('send', sid, {
                'text': 'Check screenshot and files',
                'resolvedImages': [{'url': PNG_URL, 'name': 'screen.png'}],
                'resolvedReferences': [
                    {'path': str(self.ref_file), 'reference': 'main.py'},
                    {'path': str(self.ref_dir), 'reference': 'src'},
                ],
                'resolvedFiles': [{'id': 'f1', 'path': '/tmp/log.txt', 'name': 'log.txt'}],
            })
        self.assertEqual(res['status'], 'accepted')
        self.assertEqual(len(sent), 1)
        payload = sent[0]
        self.assertEqual(payload['media'], [{
            'mime_type': 'image/png',
            'inline_data': base64.b64encode(PNG).decode('ascii'),
            'description': 'screen.png',
            'display_name': 'screen.png',
        }])
        self.assertEqual(payload['items'], [
            {'item': {'file': {'absolute_uri': self.ref_file.resolve().as_uri()}}},
            {'item': {'directory': {'absolute_uri': self.ref_dir.resolve().as_uri()}}},
            {'text': ' Check screenshot and files\n\n@/tmp/log.txt'},
        ])

    def test_send_image_only_without_text_uses_native_media(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        sent = []

        def fake_rpc(method, payload=None, *, surface='app'):
            if method == 'SendUserCascadeMessage':
                sent.append(payload)
            return {}

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, '_model', return_value=None), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            res = adapter.call('send', sid, {
                'text': '',
                'resolvedImages': [{'url': PNG_URL, 'name': 'only.png'}],
            })
        self.assertEqual(res['status'], 'accepted')
        self.assertEqual(sent[0]['items'], [{'text': ''}])
        self.assertEqual(len(sent[0]['media']), 1)
        self.assertEqual(sent[0]['media'][0]['display_name'], 'only.png')

    def test_busy_hook_queue_rejects_images_instead_of_silently_dropping(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        with mock.patch.object(HookIPCServer, '_start_server'):
            adapter.hooks = HookIPCServer(self.directory / 'hooks')
        sid = gateway_id('app', CID)
        adapter.hooks.dispatch_hook_event('session_start', {'conversationId': sid})

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.store, 'is_native_busy', return_value=True), \
                mock.patch.object(adapter.cli, 'waiting_state_by_cid', return_value=({}, {CID})):
            with self.assertRaisesRegex(BridgeUnavailable, '图片附件'):
                adapter.call('send', sid, {
                    'text': 'look',
                    'mode': 'queue',
                    'resolvedImages': [{'url': PNG_URL, 'name': 'screen.png'}],
                })
            # Text + workspace reference queued while busy still retains @path reference text
            res = adapter.call('send', sid, {
                'text': 'check later',
                'mode': 'queue',
                'resolvedReferences': [{'path': str(self.ref_file), 'reference': 'main.py'}],
            })
            self.assertEqual(res['status'], 'queued')
            queued = adapter.hooks.get_queued_items(sid)
            self.assertIn(str(self.ref_file), queued[0]['text'])

    def test_uploads_resolve_splits_images_and_files_for_antigravity(self):
        uploads = DesktopUploads(self.directory / 'uploads')
        scope = uploads.scope('antigravity', CID)
        img_id = '11111111-1111-4111-8111-111111111111'
        txt_id = '22222222-2222-4222-8222-222222222222'
        uploads.store.put(scope, img_id, 'pic.png', PNG)
        uploads.store.put(scope, txt_id, 'note.txt', b'hello text')
        images, files = uploads.resolve('antigravity', CID, [img_id, txt_id], native_files=True)
        self.assertEqual(images, [{'url': PNG_URL, 'name': 'pic.png'}])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['name'], 'note.txt')
        self.assertEqual(Path(files[0]['path']).read_bytes(), b'hello text')

    def test_native_media_keeps_signature_and_upload_size_limits(self):
        for url in (PNG_URL.replace('image/png', 'image/svg+xml'),
                    PNG_URL.replace('image/png', 'image/jpeg'),
                    'data:image/png;base64,aGVsbG8='):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Antigravity._build_media({'resolvedImages': [{'url': url}]})
        with mock.patch('bridge.clients.antigravity.adapter.MAX_FILE', len(PNG) - 1), self.assertRaises(ValueError):
            Antigravity._build_media({'resolvedImages': [{'url': PNG_URL}]})
        with mock.patch('bridge.clients.antigravity.adapter.MAX_TOTAL', len(PNG)), self.assertRaises(ValueError):
            Antigravity._build_media({'resolvedImages': [{'url': PNG_URL}, {'url': PNG_URL}]})
        media, _ = Antigravity._build_media({'resolvedImages': [{'url': PNG_URL}, {'url': PNG_URL}]})
        self.assertEqual(len(media), 2)

    def test_stale_or_redirected_reference_is_rejected_before_native_send(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('outside')
        self.ref_file.unlink()
        for changed in ('missing', 'symlink'):
            if changed == 'symlink':
                self.ref_file.symlink_to(outside)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                adapter._build_scope_items({'resolvedReferences': [{'path': str(self.ref_file), 'kind': 'file'}]})
            with self.subTest(changed=changed), \
                    mock.patch.object(adapter.cli, 'available', return_value=True), \
                    mock.patch.object(adapter.cli, 'send_user_message') as send:
                with self.assertRaises(ValueError):
                    adapter.call('send', gateway_id('app', CID), {
                        'text': 'read it', 'resolvedReferences': [{'path': str(self.ref_file), 'kind': 'file'}],
                    })
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
