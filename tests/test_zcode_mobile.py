"""The opt-in provider uses shared gateway contracts without a second owner."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.zcode.adapter import ZCodeDesktop, ZCodePrototype, project_snapshot
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
            'rows': {'totalCount': 0, 'window': []}, 'pendingInteractions': [], 'queue': {'items': []}}

    def transport(self, request):
        self.calls.append(request)
        if request['action'] == 'list':
            return {'sessions': [{'snapshot': self.snapshot, 'cwd': str(self.root)}]}
        return {'snapshot': self.snapshot} if request['action'] == 'detail' else {'models': [], 'capabilities': {'models': True}} if request['action'] == 'account' else {'status': 'accepted'}

    def test_legacy_registration_stays_detached_until_explicit_transport_is_ready(self):
        settings = self.root/'desktop-sessions/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'zcodePrototype': {**self.config, 'dataDirectory': str(self.root/'zcode')}, 'enabled': {'zcode': True}}))
        with patch('bridge.clients.manager.sys.platform', 'darwin'):
            manager = DesktopSessions(self.root)
        self.addCleanup(manager.close)
        self.assertIsInstance(manager.adapters['zcode'], ZCodeDesktop)
        self.assertEqual(manager.adapters['zcode'].home, self.root/'zcode')
        self.assertEqual(self.calls, [])
        manager.adapters['zcode'] = ZCodePrototype(self.root, self.config, self.transport)
        detail = manager.call('zcode', 'detail', 'sess_test')
        self.assertEqual(detail['session']['stopTarget'], {'executionId': 'execution'})
        self.assertEqual(detail['draftAccountIdentity'], '')
        self.assertEqual([r['action'] for r in self.calls], ['detail'])
        self.assertEqual(manager.call('zcode', 'list')['sessions'][0]['id'], 'sess_test')
        self.assertTrue(manager.call('zcode', 'account')['capabilities']['models'])

    def test_unknown_control_or_current_turn_does_not_authorize_idle_writes(self):
        self.snapshot.update(inputRouting={'mode': 'startNow'}, availability={'compact': {'allowed': True}})
        for control, queue, state in (
                (None, None, 'queued'), ({'activeWorks': None}, {'items': []}, 'completedSuccess'),
                ({'activeWorks': []}, None, 'completedSuccess'),
                ({'activeWorks': []}, {'items': None}, 'completedSuccess'),
                ({'activeWorks': []}, {'items': []}, 'futureState'),
                ({'activeWorks': []}, {'items': []}, 'completed'),
                ({'activeWorks': []}, {'items': []}, 'interrupted')):
            with self.subTest(control=control, queue=queue, state=state):
                self.snapshot.update(control=control, queue=queue,
                    rows={'totalCount': 1, 'window': [{'kind': 'turnHeader', 'rowId': 1, 'turnId': 'latest', 'state': state}]})
                detail = project_snapshot(self.snapshot, str(self.root))
                self.assertEqual(detail['session']['status'], 'unknown')
                self.assertFalse(detail['session']['runtimeKnown'])
                self.assertNotEqual(detail['session']['turnStatus'], 'completed')
                for key in ('send', 'queue', 'steer', 'compact'):
                    self.assertFalse(detail['capabilities'][key])
                self.assertFalse(any(detail['capabilities']['sendModes'].values()))
        adapter = ZCodePrototype(self.root, self.config, lambda request: {'snapshot': self.snapshot,
            'capabilities': {'send': True, 'compact': True, 'sendModes': {'send': True}}})
        detail = adapter.call('detail', 'sess_test')
        self.assertFalse(detail['capabilities']['send'])
        self.assertFalse(detail['capabilities']['compact'])
        self.assertFalse(any(detail['capabilities']['sendModes'].values()))

    def test_old_unknown_turn_does_not_hide_verified_current_idle_or_responses(self):
        self.snapshot.update(control={'phase': 'idle', 'activeWorks': []}, queue={'items': []},
            inputRouting={'mode': 'startNow'}, availability={'compact': {'allowed': True}},
            rows={'totalCount': 2, 'window': [
                {'kind': 'turnHeader', 'rowId': 1, 'turnId': 'old', 'state': 'futureState'},
                {'kind': 'turnHeader', 'rowId': 2, 'turnId': 'latest', 'state': 'completedSuccess'}]})
        detail = project_snapshot(self.snapshot, str(self.root))
        self.assertTrue(detail['session']['runtimeKnown'])
        self.assertEqual(detail['session']['status'], 'idle')
        self.assertEqual(detail['turns'][0]['status'], 'futureState')
        self.assertTrue(detail['capabilities']['sendModes']['send'])
        self.assertTrue(detail['capabilities']['compact'])
        self.snapshot['control'] = None
        self.snapshot['pendingInteractions'] = [{'interactionId': 'permission', 'kind': 'permission',
            'payload': {'options': [{'optionId': 'deny', 'label': 'Deny', 'kind': 'deny'}]}}]
        detail = project_snapshot(self.snapshot, str(self.root))
        self.assertEqual(detail['session']['requests'][0]['decisions'][0]['id'], 'deny')
        adapter = ZCodePrototype(self.root, self.config, self.transport)
        self.assertEqual(adapter.call('respond', 'sess_test', {'id': str(uuid.uuid4()),
            'requestId': 'permission', 'decision': 'deny'})['status'], 'accepted')

    def test_default_gateway_does_not_register_or_probe_zcode(self):
        manager = DesktopSessions(self.root)
        self.addCleanup(manager.close)
        self.assertNotIn('zcode', manager.adapters)
        self.assertNotIn('zcode', manager.navigation_clients())

    def test_unchanged_native_model_is_a_confirmed_shared_settings_result(self):
        ack = {'status': 'noop', 'reasonCode': 'config.unchanged', 'revisionAtDecision': 81}
        adapter = ZCodePrototype(self.root, self.config, lambda request: ack)
        result = adapter.call('settings', 'sess_test', {'id': str(uuid.uuid4()),
            'model': 'account:zai-start-plan/GLM-5.3-Flash', 'effort': 'low'})
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['nativeAck'], ack)
        ack['reasonCode'] = 'other.noop'
        result = adapter.call('settings', 'sess_test', {'id': str(uuid.uuid4()),
            'model': 'account:zai-start-plan/GLM-5.3-Flash', 'effort': 'low'})
        self.assertEqual(result['status'], 'noop')

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

    def test_unknown_turn_state_and_null_nested_objects_do_not_crash_snapshot(self):
        # 未知 turnHeader.state 不能抛 KeyError（HTTP 404），也不能伪造 completed/failed。
        self.snapshot['rows']['window'] = [
            {'kind': 'turnHeader', 'turnId': 't1', 'rowId': 1, 'state': 'queued'},
            {'kind': 'userInput', 'rowId': 2, 'entityId': 'u1', 'visibility': 'visible',
             'text': 'hi', 'actions': None},
        ]
        self.snapshot.update({'control': None, 'inputRouting': None, 'queue': None, 'usage': None,
                              'meta': None, 'config': None, 'availability': None})
        detail = project_snapshot(self.snapshot, str(self.root))
        self.assertEqual(detail['turns'], [{'turnId': 't1', 'sequence': 1, 'status': 'queued'}])
        self.assertEqual(detail['session']['title'], '')
        self.assertEqual(detail['session']['contextUsageStatus'], 'unavailable')

    def test_null_send_result_stays_accepted_and_invalid_respond_blocks_before_ipc(self):
        # 原生返回 result: null 时仍应记为 accepted；非法 respond 参数应在发出 IPC 前被拦截为 blocked。
        settings = self.root/'desktop-sessions/settings.json'
        settings.parent.mkdir(exist_ok=True)
        settings.write_text(json.dumps({'enabled': {'zcode': True}}))
        manager = DesktopSessions(self.root)
        self.addCleanup(manager.close)
        adapter = ZCodePrototype(self.root, self.config, lambda req: (self.calls.append(req), {'status': 'accepted', 'result': None})[1])
        manager.adapters['zcode'] = adapter

        sent = manager.call('zcode', 'send', 'sess_test', {'id': str(uuid.uuid4()), 'text': 'hello'})
        self.assertEqual(sent['status'], 'accepted')

        calls_before = len(self.calls)
        bad_decision = manager.call('zcode', 'respond', 'sess_test', {'id': str(uuid.uuid4()), 'requestId': 'r1'})
        bad_answers = manager.call('zcode', 'respond', 'sess_test', {
            'id': str(uuid.uuid4()), 'requestId': 'r1', 'decision': 'accept', 'answers': {'q': None}})
        self.assertEqual(bad_decision['status'], 'blocked')
        self.assertEqual(bad_answers['status'], 'blocked')
        self.assertEqual(len(self.calls), calls_before)
