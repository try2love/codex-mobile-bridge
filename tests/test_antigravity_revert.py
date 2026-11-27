import json
import unittest
import uuid
from unittest import mock

import test_bridge
import test_antigravity_fork
from test_antigravity_fork import CID, _write_transcript
from bridge.clients.antigravity.model import gateway_id, revert_message_id
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions


class AntigravityRevertTests(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def setUp(self):
        test_antigravity_fork.AntigravityForkTests.setUp(self)
        test_bridge.HttpTests.setUp(self)
        self.addCleanup(test_bridge.HttpTests.tearDown, self)
        settings = self.directory / 'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        self.manager = self.server.desktop_sessions = DesktopSessions(self.directory)
        self.addCleanup(self.manager.close)
        self.adapter = self.manager.adapters['antigravity']
        self.sid = gateway_id('app', CID)
        self.calls = []
        self.failure = False
        def rpc(method, payload=None, *, surface='app', expected_identity=None):
            if method == 'GetAllCascadeTrajectories':
                return {'trajectorySummaries': {CID: {'status': 'CASCADE_RUN_STATUS_IDLE'}}}
            if method == 'RevertToCascadeStep':
                self.assertEqual(expected_identity, 'native-owner')
                self.calls.append(payload)
                if self.failure:
                    raise BridgeUnavailable('unconfirmed')
                return {'metadata': {'revertedUris': []}}
            return {}
        for name, kwargs in [('available', {'return_value': True}), ('source_identity', {'return_value': 'native-owner'}),
                             ('call_ls_rpc', {'side_effect': rpc})]:
            patcher = mock.patch.object(self.adapter.cli, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def payload(self, **changes):
        return {'id': str(uuid.uuid4()), 'stepIndex': 5, 'messageId': revert_message_id(self.steps[5]), **changes}

    def test_http_auth_csrf_manager_receipt_native_default_and_duplicate(self):
        path = '/api/desktop-sessions/antigravity/revert'
        payload = {**self.payload(), 'sessionId': self.sid}
        self.assertEqual(self.request('POST', path, payload)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, payload, {'Cookie': headers['Cookie']})[0], 403)
        status, _, result = self.request('POST', path, payload, headers)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.calls, [{'cascadeId': CID, 'stepIndex': 5, 'conversationOnly': True}])
        self.assertEqual(self.request('POST', path, payload, headers)[2]['status'], 'accepted')
        self.assertEqual(len(self.calls), 1)
        detail = self.adapter.call('detail', self.sid)
        message = next(m for m in detail['messages'] if m.get('revertStepIndex') == 5)
        self.assertEqual(message['revertMessageId'], payload['messageId'])

    def test_explicit_file_revert_and_unknown_outcome_never_replayed(self):
        self.failure = True
        payload = self.payload(conversationOnly=False)
        first = self.manager.call('antigravity', 'revert', self.sid, payload)
        self.assertEqual(first['status'], 'unknown')
        self.assertFalse(self.calls[0]['conversationOnly'])
        self.assertEqual(self.manager.call('antigravity', 'revert', self.sid, payload)['status'], 'unknown')
        with self.assertRaisesRegex(ValueError, '待确认'):
            self.manager.call('antigravity', 'revert', self.sid, self.payload())
        self.assertEqual(len(self.calls), 1)

    def test_changed_or_wrong_step_busy_and_queue_never_reach_write(self):
        for changes in ({'stepIndex': 1}, {'stepIndex': 99}, {'messageId': '0' * 64}):
            result = self.manager.call('antigravity', 'revert', self.sid, self.payload(**changes))
            self.assertEqual(result['status'], 'blocked')
        with mock.patch.object(self.adapter.store, 'is_native_busy', return_value=True):
            self.assertEqual(self.manager.call('antigravity', 'revert', self.sid, self.payload())['status'], 'blocked')
        payload = self.payload()
        self.steps[5]['content'] = 'replacement prompt'
        _write_transcript(self.root, CID, self.steps)
        self.assertEqual(self.manager.call('antigravity', 'revert', self.sid, payload)['status'], 'blocked')
        with mock.patch.object(self.adapter, '_session', return_value={}), \
                mock.patch.object(self.adapter, 'hooks') as hooks:
            hooks.has_pending_approvals.return_value = False
            hooks.get_queued_items.return_value = [{'id': 'queued'}]
            self.assertEqual(self.manager.call('antigravity', 'revert', self.sid, self.payload())['status'], 'blocked')
        self.assertFalse(self.calls)

    def test_manager_rejects_unknown_fields_and_non_agy_provider(self):
        for changes in ({'stepIndex': True}, {'conversationOnly': 'false'}, {'extra': True}):
            with self.assertRaises(ValueError):
                self.manager.call('antigravity', 'revert', self.sid, self.payload(**changes))
        self.assertFalse(self.calls)
