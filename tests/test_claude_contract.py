import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bridge.integrations.claude import Claude
from bridge.integrations import claude_model
from bridge.integrations.errors import BridgeUnavailable
from bridge.integrations.manager import DesktopSessions
from bridge.integrations.mailbox import FileDesktop, MAX_REQUEST

ROOT = Path(__file__).resolve().parents[1]


class ClaudeContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(self.temp.cleanup)
        self.adapter = Claude(Path(self.temp.name)/'adapter')
        self.rows = {'code': {'sessionId': 'code-native', 'cwd': '/project', 'model': 'api/code', 'effort': 'high', 'permissionMode': 'default', 'isRunning': True},
                     'cowork': {'sessionId': 'cowork-native', 'cwd': '/vm/work', 'userSelectedFolders': ['/host/project'], 'model': 'api/cowork'}}
        self.calls = []
        self.adapter.desktop = Mock(connected=True, capabilities={kind: ['getAll', 'sendMessage', 'setModel', 'setEffort', 'setPermissionMode', 'getSupportedCommands', 'start'] for kind in self.rows})
        self.adapter.desktop.call = AsyncMock(side_effect=self.call)

    async def call(self, kind, method, *args, **kwargs):
        self.calls.append((kind, method, args))
        if method == 'getAll': return [dict(self.rows[kind])]
        if method == 'getSession': return dict(self.rows[kind])
        if method == 'getTranscript': return [{'type': 'assistant', 'message': {'role': 'assistant', 'model': 'actual-model', 'content': 'answer'}}]
        if method == 'mobileCatalog': return {'models': [{'id': self.rows[kind]['model'], 'name': 'Model', 'efforts': ['low', 'high'], 'defaultEffort': 'high'}], 'capabilities': {'models': True, 'effort': True}, 'skills': []}
        if method == 'setEffort': self.rows[kind]['effort'] = args[1]
        if method == 'setModel': self.rows[kind]['model'] = args[1]
        if method == 'setPermissionMode': self.rows[kind]['permissionMode'] = args[1]; return True
        if method == 'start': return {'sessionId': 'new-native'}
        if method == 'mobileAccount': return {'provider': 'claude', 'readOnly': True, 'canManage': False, 'current': {'kind': 'api', 'label': 'Fixture'}}

    def sid(self, kind='code'): return claude_model.gateway_id(kind, self.rows[kind]['sessionId'])

    async def test_effort_only_update_does_not_require_resubmitting_model(self):
        await self.adapter.dispatch('settings', self.sid(), {'effort': 'low'})
        self.assertEqual(self.rows['code']['effort'], 'low')
        self.assertFalse(any(method == 'setModel' for _, method, _ in self.calls))

    async def test_images_and_request_uuid_reach_correct_surface_positions(self):
        image = {'url': 'data:image/png;base64,aGVsbG8=', 'name': 'fixture.png'}
        identifier = '82e2abf7-2265-4d21-aea4-19fcc076b6e9'
        await self.adapter.dispatch('send', self.sid(), {'text': 'hi', 'id': identifier, 'mode': 'steer', 'resolvedImages': [image]})
        args = next(args for kind, method, args in self.calls if method == 'sendMessage')
        self.assertEqual(args[2], [{'mimeType': 'image/png', 'base64': 'aGVsbG8=', 'filename': 'fixture.png'}])
        self.assertEqual(args[5], 'now'); self.assertEqual(args[7], identifier)

    async def test_cowork_image_only_uuid_and_no_steering(self):
        body = {'text': '', 'id': str(uuid.uuid4()), 'resolvedImages': [{'url': 'data:image/jpeg;base64,aGVsbG8='}]}
        await self.adapter.dispatch('send', self.sid('cowork'), body)
        args = next(args for kind, method, args in self.calls if method == 'sendMessage')
        self.assertEqual(args, ('cowork-native', '', [{'mimeType': 'image/jpeg', 'base64': 'aGVsbG8='}], None, body['id']))
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'Cowork'):
            await self.adapter.dispatch('send', self.sid('cowork'), {**body, 'mode': 'steer'})
        self.assertFalse(any(method == 'sendMessage' for _, method, _ in self.calls))

    async def test_invalid_effort_cannot_partially_change_model(self):
        async def call(kind, method, *args, **kwargs):
            if method == 'mobileCatalog':
                return {'models': [{'id': 'another'}], 'capabilities': {'models': True}}
            return await self.call(kind, method, *args, **kwargs)
        self.adapter.desktop.call.side_effect = call
        with self.assertRaisesRegex(ValueError, '思考强度'):
            await self.adapter.dispatch('settings', self.sid('cowork'), {'model': 'another', 'effort': ''})
        self.assertFalse(any(method in ('setEffort', 'setModel') for _, method, _ in self.calls))

    async def test_effort_reset_uses_native_surface_semantics(self):
        await self.adapter.dispatch('settings', self.sid(), {'effort': ''})
        self.assertIsNone(self.rows['code']['effort'])
        await self.adapter.dispatch('settings', self.sid('cowork'), {'effort': ''})
        self.assertEqual(self.rows['cowork']['effort'], 'high')

    async def test_create_requires_message_and_uses_cowork_folders(self):
        projects = await self.adapter.dispatch('projects', None, {})
        self.assertTrue(projects['createRequiresMessage'])
        project = next(p for p in projects['projects'] if p['surface'] == 'cowork')
        self.assertEqual(project['cwd'], '/host/project')
        with self.assertRaises(ValueError): await self.adapter.dispatch('create', None, {'projectKey': project['key']})
        await self.adapter.dispatch('create', None, {'projectKey': project['key'], 'title': 'Test', 'firstMessage': 'Hello'})
        args = next(args for kind, method, args in self.calls if method == 'start')[0]
        self.assertEqual(args['message'], 'Hello'); self.assertEqual(args['userSelectedFolders'], ['/host/project']); self.assertNotIn('cwd', args)

    async def test_account_has_no_session_dependency(self):
        value = await self.adapter.dispatch('account', None, {})
        self.assertEqual(value['current']['label'], 'Fixture')
        self.assertEqual(self.calls, [('code', 'mobileAccount', ())])

    async def test_permissions_are_observed_after_change(self):
        value = await self.adapter.dispatch('access', self.sid(), {})
        self.assertEqual(value['mode'], 'default')
        self.assertTrue(any(o['value'] == 'plan' for o in value['options']))
        await self.adapter.dispatch('access', self.sid(), {'mode': 'plan'})
        self.assertEqual(self.rows['code']['permissionMode'], 'plan')

    async def test_permission_rejection_or_unobserved_change_is_not_accepted(self):
        for accepted in (False, True):
            async def call(kind, method, *args, **kwargs):
                if method == 'setPermissionMode': return accepted
                return await self.call(kind, method, *args, **kwargs)
            self.adapter.desktop.call.side_effect = call
            with self.assertRaisesRegex(BridgeUnavailable, '未确认权限设置'):
                await self.adapter.dispatch('access', self.sid(), {'mode': 'plan'})

    def test_null_cowork_folders_are_empty(self):
        self.assertEqual(claude_model.session('cowork', {'sessionId': 'id', 'userSelectedFolders': None})['folders'], [])

    def test_shutdown_state_requires_real_runtime_evidence(self):
        for raw, known in [({}, False), ({'isRunning': False}, True), ({'turnRunning': False}, True),
                           ({'isRunning': 'false'}, False), ({'lifecycleState': 'running'}, True)]:
            self.assertIs(claude_model.session('code', {'sessionId': 'id', **raw})['runtimeKnown'], known)
        self.assertEqual(claude_model.session('cowork', {'sessionId': 'id', 'turnRunning': False, 'isRunning': True})['status'], 'active')

    async def test_detail_distinguishes_mode_and_actual_message_model(self):
        value = await self.adapter.dispatch('detail', self.sid('cowork'), {})
        self.assertEqual(value['session']['mode'], 'cowork')
        self.assertFalse(value['capabilities']['steer'])
        self.assertEqual(value['messages'][0]['model'], 'actual-model')

    async def test_batched_list_and_detail_need_only_two_mailbox_round_trips(self):
        self.adapter.desktop.capabilities['code'] += ['mobileList', 'mobileDetail']
        async def call(kind, method, *args, **kwargs):
            self.calls.append((kind, method, args))
            if method == 'mobileList': return {surface: [row] for surface, row in self.rows.items()}
            if method == 'mobileDetail': return {'session': self.rows[kind], 'transcript': []}
            raise AssertionError('unnecessary unbatched read: '+method)
        self.adapter.desktop.call.side_effect = call
        listing = await self.adapter.dispatch('list', None, {})
        detail = await self.adapter.dispatch('detail', self.sid(), {})
        self.assertEqual([method for _, method, _ in self.calls], ['mobileList', 'mobileDetail'])
        self.assertTrue(listing['complete'])
        self.assertEqual(listing['coverage'], {'code': True, 'cowork': True})
        self.assertEqual(detail['session']['id'], self.sid())

    async def test_missing_surface_never_claims_complete_activity_coverage(self):
        self.adapter.desktop.capabilities['cowork'] = []
        value = await self.adapter.dispatch('list', None, {})
        self.assertFalse(value['complete'])
        self.assertEqual(value['coverage'], {'code': True, 'cowork': False})


class DesktopActionContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(self.temp.cleanup)
        self.manager = DesktopSessions(Path(self.temp.name)); self.addCleanup(self.manager.close)
        self.adapter = Mock()
        self.adapter.call.return_value = {'status': 'accepted'}
        self.manager.adapters['claude'] = self.adapter

    def test_account_and_permission_reads_do_not_claim_mutation_receipts(self):
        self.manager.call('claude', 'account')
        self.manager.call('claude', 'access', 'session')
        self.assertEqual(self.manager.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)
        self.assertEqual(self.adapter.call.call_count, 2)

    def test_permission_changes_are_at_most_once(self):
        body = {'id': str(uuid.uuid4()), 'mode': 'plan'}
        self.manager.call('claude', 'access', 'session', body)
        self.manager.call('claude', 'access', 'session', body)
        self.assertEqual(self.adapter.call.call_count, 1)
        with self.assertRaisesRegex(ValueError, '相同请求 ID'):
            self.manager.call('claude', 'access', 'session', {**body, 'mode': 'default'})

    def test_image_only_send_accepted_but_empty_send_never_dispatched(self):
        body = {'id': str(uuid.uuid4()), 'text': ''}
        for extra in ({}, {'resolvedImages': []}):
            with self.assertRaises(ValueError): self.manager.call('claude', 'send', 'session', {**body, **extra})
        self.adapter.call.assert_not_called()
        self.manager.call('claude', 'send', 'session', {**body, 'resolvedImages': [{'url': 'data:image/png;base64,aGVsbG8='}]})
        self.assertEqual(self.adapter.call.call_count, 1)

    async def test_oversize_request_keeps_last_readable_file(self):
        desktop = FileDesktop(Path(self.temp.name), 'test-token')
        path = Path(self.temp.name)/'request.json'
        original = path.read_bytes()
        request = {'type': 'request', 'args': ['文字' * 32]}
        exact_size = len(desktop.pack(request).encode('utf-8'))
        with patch('bridge.integrations.mailbox.MAX_REQUEST', exact_size - 1):
            with self.assertRaisesRegex(ValueError, '10 MiB'): desktop.write(request)
        self.assertEqual(path.read_bytes(), original)
        with patch('bridge.integrations.mailbox.MAX_REQUEST', exact_size): desktop.write(request)
        self.assertEqual(len(path.read_bytes()), exact_size)
        self.assertEqual(MAX_REQUEST, 10485760)
