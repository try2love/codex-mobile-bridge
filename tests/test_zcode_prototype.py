import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.zcode.adapter import ZCodePrototype, project_files, project_snapshot
from bridge.platforms.macos.loopback import listener_identity

ROOT = Path(__file__).resolve().parents[1]


class ZCodePrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.config = {'workspace': str(self.project), 'sessionId': 'sess_test', 'binding': {'owner': 'one'}}

    def manager(self, transport):
        manager = DesktopSessions(self.root / 'state')
        self.addCleanup(manager.close)
        manager.adapters['zcode'] = ZCodePrototype(self.root, self.config, transport=transport)
        manager.config['enabled'] = {'zcode': True}
        manager.locks['zcode'] = threading.RLock()
        for mapping in (manager.read_epochs, manager.read_changes, manager.binding_epochs, manager.binding_changes):
            mapping['zcode'] = 0
        return manager

    def test_full_access_without_checkbox_is_blocked_before_native_dispatch(self):
        transport = Mock(side_effect=AssertionError("native dispatch must not occur"))
        manager = self.manager(transport)
        body = {'id': str(uuid.uuid4()), 'mode': 'yolo', 'confirmed': False}
        receipt = manager.call('zcode', 'access', 'sess_test', body)
        self.assertEqual(receipt['status'], 'blocked')
        self.assertEqual(manager.call('zcode', 'access', 'sess_test', body), receipt)
        transport.assert_not_called()

    def test_shared_receipts_survive_reconnect_and_never_replay_unknown_send(self):
        transport = Mock(side_effect=TimeoutError('accepted but receipt lost'))
        first = self.manager(transport)
        body = {'id': str(uuid.uuid4()), 'text': 'one message'}
        result = first.call('zcode', 'send', 'sess_test', body)
        self.assertEqual(result['status'], 'unknown')
        recovered_transport = Mock()
        recovered = self.manager(recovered_transport)
        self.assertEqual(recovered.call('zcode', 'send', 'sess_test', body), result)
        recovered_transport.assert_not_called()
        self.assertEqual(transport.call_count, 1)
        with self.assertRaisesRegex(ValueError, '相同请求 ID'):
            recovered.call('zcode', 'send', 'sess_test', {**body, 'text': 'different'})

    def test_project_reference_revalidates_shared_boundary(self):
        (self.project / 'file.md').write_text('content')
        outside = self.root / 'outside.md'; outside.write_text('private')
        (self.project / 'escape.md').symlink_to(outside)
        self.assertEqual(project_files(self.project, ['file.md'])[0]['name'], 'file.md')
        for path in ['../outside.md', 'escape.md', str(outside)]:
            with self.assertRaises((ValueError, PermissionError, OSError)):
                project_files(self.project, [path])
        with self.assertRaises(ValueError):
            project_files(self.project, ['file.md', 'file.md'])

    def test_wrong_session_and_unbound_write_are_not_dispatched(self):
        transport = Mock()
        adapter = ZCodePrototype(self.root, self.config, transport)
        with self.assertRaises(ValueError): adapter.call('stop', 'another-session', {'id': str(uuid.uuid4())})
        adapter.config.pop('binding')
        with self.assertRaisesRegex(Exception, '显式接入'): adapter.call('send', 'sess_test', {'id': str(uuid.uuid4()), 'text': 'hi'})
        transport.assert_not_called()

    def test_explicit_attach_does_not_require_the_stale_binding(self):
        transport = Mock(return_value={'binding': {'owner': 'new'}})
        adapter = ZCodePrototype(self.root, self.config, transport)
        adapter.call('attach', 'sess_test')
        self.assertIsNone(transport.call_args[0][0]['binding'])
        self.assertEqual(adapter.config['binding'], {'owner': 'one'})

    def test_project_root_replacement_is_rejected(self):
        self.project.rename(self.root / 'original')
        outside = self.root / 'replacement'; outside.mkdir()
        (outside / 'file.md').write_text('different project')
        self.project.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '项目目录已变化'):
            project_files(str(self.project), ['file.md'])

    @patch('bridge.clients.zcode.adapter.Workspace')
    def test_attachment_total_is_bounded_before_native_transfer(self, workspace):
        workspace.return_value.root = self.project
        workspace.return_value.read.return_value = {'name': 'file', 'size': 12 * 1024 * 1024, 'data': ''}
        with self.assertRaisesRegex(ValueError, '总量'):
            project_files(self.project, ['one', 'two'])

    def test_projection_keeps_stable_ids_native_attachments_and_partial_history(self):
        snapshot = {'sessionId': 'sess_test', 'revision': 1, 'seq': 2, 'logEpoch': 'epoch',
            'control': {'canStop': False, 'activeWorks': []}, 'pendingInteractions': [],
            'rows': {'totalCount': 3, 'window': [
                {'rowId': 1, 'kind': 'userInput', 'visibility': 'visible', 'text': 'file',
                 'attachments': [{'fileName': 'file.md', 'mime': 'text/markdown', 'bytes': 2, 'ref': 'native'}]},
                {'rowId': 2, 'kind': 'assistantText', 'visibility': 'hidden', 'text': 'hidden'}]}}
        first = project_snapshot(snapshot, str(self.project))
        snapshot['seq'] = 3
        self.assertEqual(first['messages'], project_snapshot(snapshot, str(self.project))['messages'])
        self.assertEqual(first['messages'][0]['attachments'][0]['nativeRef'], 'native')
        self.assertTrue(first['history']['hasMore'])

    def test_context_usage_uses_current_native_window_not_cumulative_tokens(self):
        snapshot = {'sessionId': 'sess_test', 'revision': 1, 'seq': 2, 'logEpoch': 'epoch',
                    'control': {}, 'pendingInteractions': [], 'rows': {'totalCount': 0, 'window': []},
                    'config': {'model': 'Flash'}, 'usage': {'contextWindow': {
                        'usedTokens': 28522, 'maxTokens': 1000000}, 'cumulative': {'inputTokens': 9000000}}}
        session = project_snapshot(snapshot, str(self.project))['session']
        self.assertEqual(session['contextUsage'], {'usedTokens': 28522, 'contextWindow': 1000000,
                                                 'measurement': 'native', 'model': 'Flash'})
        self.assertEqual(session['contextUsageStatus'], 'available')
        snapshot['usage']['contextWindow'] = None
        session = project_snapshot(snapshot, str(self.project))['session']
        self.assertIsNone(session['contextUsage'])
        self.assertEqual(session['contextUsageStatus'], 'unavailable')
        snapshot['usage']['contextWindow'] = {'usedTokens': 0, 'maxTokens': 1000000}
        self.assertEqual(project_snapshot(snapshot, str(self.project))['session']['contextUsage']['usedTokens'], 0)

    def test_new_project_binding_and_file_reads_do_not_use_the_anchor_directory(self):
        new = self.root / 'new-project'; new.mkdir(); (new / 'test.md').write_text('new project')
        (self.project / 'test.md').write_text('old project')
        transport = Mock(return_value={'status': 'accepted', 'result': {'id': 'sess_new'},
                                      'workspace': str(new), 'binding': {'owner': 'new-project'}})
        adapter = ZCodePrototype(self.root, self.config, transport)
        result = adapter.call('create', body={'id': str(uuid.uuid4()), 'projectKey': 'registered',
                              'firstMessage': 'test', 'directoryProject': {'cwd': str(new)}})
        self.assertNotIn('binding', result)
        transport.return_value = {'status': 'accepted'}
        adapter.call('send', 'sess_new', {'id': str(uuid.uuid4()), 'text': 'read', 'projectFiles': ['test.md']})
        request = transport.call_args[0][0]
        self.assertEqual(request['workspace'], str(new))
        self.assertEqual(request['binding'], {'owner': 'new-project'})
        import base64
        self.assertEqual(base64.b64decode(request['body']['files'][0]['data']), b'new project')
        self.assertEqual(adapter.config['binding'], {'owner': 'one'})

    def test_new_project_requires_explicit_binding_after_adapter_restart(self):
        new = str(self.root / 'new-project')
        transport = Mock(return_value={'sessions': [{'id': 'sess_new', 'cwd': new}]})
        adapter = ZCodePrototype(self.root, self.config, transport)
        adapter.call('list')
        transport.reset_mock()
        with self.assertRaisesRegex(Exception, '显式接入'):
            adapter.call('send', 'sess_new', {'id': str(uuid.uuid4()), 'text': 'test'})
        transport.assert_not_called()

    def test_delivery_feedback_uses_native_admission_instead_of_requested_mode(self):
        ack = {'status': 'accepted', 'result': {'type': 'inputAccepted', 'delivery': 'queue'}}
        transport = Mock(return_value=ack)
        adapter = ZCodePrototype(self.root, self.config, transport)
        body = {'id': str(uuid.uuid4()), 'text': 'guide', 'mode': 'steer'}
        self.assertEqual(adapter.call('send', 'sess_test', body)['status'], 'queued')
        self.assertEqual(transport.call_args[0][0]['body']['mode'], 'steer')
        ack['result']['delivery'] = 'startNow'
        self.assertEqual(adapter.call('send', 'sess_test', {**body, 'mode': 'queue'})['status'], 'accepted')

    @patch('bridge.platforms.macos.loopback.Native')
    @patch('bridge.platforms.macos.loopback.subprocess.run')
    @patch('os.getuid', return_value=501, create=True)
    def test_endpoint_rejects_non_loopback_or_wrong_process(self, _uid, run, native):
        run.return_value = Mock(returncode=0, stdout='p123\nn*:17742\n')
        with self.assertRaises(ValueError): listener_identity(17742, '/Applications/ZCode.app/Contents/MacOS/ZCode')
        native.assert_not_called()
        run.return_value.stdout = 'p123\nn127.0.0.1:17742\n'
        native.return_value.identity.return_value = {'uid': -1, 'zombie': False, 'image': '/wrong'}
        with self.assertRaises(ValueError): listener_identity(17742, '/Applications/ZCode.app/Contents/MacOS/ZCode')
        native.return_value.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
