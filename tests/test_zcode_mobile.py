"""The opt-in provider uses shared gateway contracts without a second owner."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.zcode.adapter import ZCodePrototype, project_snapshot
from bridge.clients.uploads import DesktopUploads


class MobileContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = {'workspace': str(self.root), 'sessionId': 'sess_test', 'binding': {'owner': 'native'}}
        self.calls = []
        self.snapshot = {'sessionId': 'sess_test', 'revision': 1, 'seq': 2, 'logEpoch': 'epoch',
            'control': {'canStop': True, 'activeWorks': [{'foregroundExecutionId': 'execution'}]},
            'rows': {'totalCount': 0, 'window': []}, 'pendingInteractions': []}

    def transport(self, request):
        self.calls.append(request)
        return {'snapshot': self.snapshot} if request['action'] == 'detail' else {'status': 'accepted'}

    def test_opt_in_registers_in_shared_manager_without_native_activation(self):
        settings = self.root/'desktop-sessions/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'zcodePrototype': self.config, 'enabled': {'zcode': True}}))
        with patch('bridge.clients.manager.sys.platform', 'darwin'):
            manager = DesktopSessions(self.root)
        self.addCleanup(manager.close)
        manager.adapters['zcode'].transport = self.transport
        detail = manager.call('zcode', 'detail', 'sess_test')
        self.assertEqual(detail['session']['stopTarget'], {'executionId': 'execution'})
        self.assertEqual(detail['draftAccountIdentity'], '')
        self.assertEqual([r['action'] for r in self.calls], ['detail'])
        self.assertEqual(manager.call('zcode', 'list')['sessions'][0]['id'], 'sess_test')
        self.assertFalse(manager.call('zcode', 'account')['capabilities']['models'])

    def test_default_gateway_does_not_register_or_probe_zcode(self):
        manager = DesktopSessions(self.root)
        self.addCleanup(manager.close)
        self.assertNotIn('zcode', manager.adapters)
        self.assertNotIn('zcode', manager.navigation_clients())

    def test_project_file_uses_native_bytes_without_injected_prompt(self):
        (self.root/'file.md').write_text('native file')
        adapter = ZCodePrototype(self.root, self.config, self.transport)
        adapter.call('send', 'sess_test', {'id': str(uuid.uuid4()), 'text': 'inspect', 'mode': 'send',
            'resolvedReferences': [{'path': str(self.root/'file.md'), 'kind': 'file'}]})
        body = self.calls[0]['body']
        self.assertEqual(body['text'], 'inspect')
        self.assertEqual(body['files'][0]['name'], 'file.md')
        self.assertNotIn('resolvedReferences', body)

    def test_native_permission_choices_and_question_share_existing_card_contract(self):
        self.snapshot['pendingInteractions'] = [{'interactionId': 'permission', 'kind': 'permission',
            'payload': {'toolName': 'Bash', 'detail': {'command': 'test'}, 'options': [
                {'optionId': 'once', 'label': 'Allow once', 'kind': 'allowOnce'},
                {'optionId': 'always', 'label': 'Always allow', 'kind': 'allowAlways'},
                {'optionId': 'deny', 'label': 'Deny', 'kind': 'deny'}]}}]
        detail = project_snapshot(self.snapshot, str(self.root))
        self.assertEqual([v['id'] for v in detail['session']['requests'][0]['decisions']], ['once', 'deny'])
        adapter = ZCodePrototype(self.root, self.config, self.transport)
        adapter.call('respond', 'sess_test', {'id': str(uuid.uuid4()), 'requestId': 'permission', 'decision': 'deny'})
        self.assertEqual(self.calls[-1]['body']['answer'], {'optionId': 'deny'})
        adapter.call('respond', 'sess_test', {'id': str(uuid.uuid4()), 'requestId': 'question', 'decision': 'accept',
            'answers': {'Question?': {'answers': ['Yes']}}})
        self.assertEqual(self.calls[-1]['body']['answer']['content'], {'answers': {'Question?': 'Yes'}})

    def test_upload_files_require_capability_and_keep_session_isolation(self):
        uploads = DesktopUploads(self.root)
        identifier = str(uuid.uuid4())
        uploads.store.put(uploads.scope('zcode', 'sess_test'), identifier, 'file.md', b'native file')
        with self.assertRaises(ValueError):
            uploads.resolve('zcode', 'sess_test', [identifier])
        images, files = uploads.resolve('zcode', 'sess_test', [identifier], native_files=True)
        self.assertEqual(images, [])
        self.assertEqual(Path(files[0]['path']).read_bytes(), b'native file')
        with self.assertRaises(ValueError):
            uploads.resolve('zcode', 'other', [identifier], native_files=True)

    def test_upstream_failure_is_visible_without_auto_retry_or_false_running_state(self):
        self.snapshot['control'] = {'phase': 'error', 'activeWorks': [], 'lastError': {
            'code': '3009', 'message': 'native busy', 'at': 123, 'attribution': {'reason': 'rate_limited'}}}
        self.snapshot['inputRouting'] = {'mode': 'startNow'}
        detail = project_snapshot(self.snapshot, str(self.root))
        self.assertEqual(detail['session']['turnStatus'], 'failed')
        self.assertTrue(detail['capabilities']['send'])
        self.assertIn('3009', detail['messages'][-1]['text'])
        self.assertEqual(detail['messages'][-1]['role'], 'error')
        self.assertEqual(self.calls, [])
