import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.cli_session import make_waiting_request_id
from bridge.clients.antigravity.model import gateway_id
from bridge.clients.errors import BridgeUnavailable
from test_antigravity_adapter import CID, ROOT, _create_surface_db


class WaitingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / 'home'
        _create_surface_db(self.home / '.gemini/antigravity', [
            (CID, 'Waiting test', '', 0, '2026-11-08T02:00:00Z', '[]',
             'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', '', 0, 0, 0, '')])
        self.adapter = Antigravity(Path(folder.name) / 'gateway', home=self.home)
        self.addCleanup(self.adapter.close)
        self.summary = {'trajectoryId': 'trajectory', 'status': 'CASCADE_RUN_STATUS_IDLE'}
        self.summaries = {CID: self.summary}
        self.writes = []
        self.fail_read = False
        self.available = mock.patch.object(self.adapter.cli, 'available', return_value=True)
        self.rpc = mock.patch.object(self.adapter.cli, 'call_ls_rpc', side_effect=self.call_rpc)
        self.available.start(); self.rpc.start()
        self.addCleanup(self.available.stop); self.addCleanup(self.rpc.stop)

    def call_rpc(self, method, body=None, *, surface='app'):
        if method == 'GetAllCascadeTrajectories':
            if self.fail_read:
                raise BridgeUnavailable('temporary read failure')
            return {'trajectorySummaries': self.summaries}
        if method == 'HandleCascadeUserInteraction':
            self.writes.append(body)
            return {}
        if method == 'GetUserStatus':
            return {'userStatus': {'email': 'fixture@example.test'}}
        raise AssertionError(method)

    def wait(self):
        self.summary['waitingSteps'] = [{'stepIndex': 2, 'step': {
            'status': 'CORTEX_STEP_STATUS_WAITING',
            'requestedInteraction': {'runCommand': {}},
            'runCommand': {'proposedCommandLine': 'fixture command'}}}]
        return make_waiting_request_id('app', CID, 'trajectory', 2)

    def test_send_rechecks_new_waiting_after_cached_idle(self):
        self.adapter.cli.waiting_state_by_cid()
        self.wait()
        with mock.patch.object(self.adapter.cli, 'send_user_message') as send:
            with self.assertRaises(BridgeUnavailable):
                self.adapter.call('send', gateway_id('app', CID), {'text': 'must not send'})
            send.assert_not_called()

    def test_read_failure_does_not_authorize_send_from_idle_database(self):
        self.fail_read = True
        with mock.patch.object(self.adapter.cli, 'send_user_message') as send:
            with self.assertRaises(BridgeUnavailable):
                self.adapter.call('send', gateway_id('app', CID), {'text': 'must not send'})
            send.assert_not_called()

    def test_response_rechecks_removed_request_instead_of_cached_metadata(self):
        rid = self.wait()
        self.adapter.cli.waiting_state_by_cid()
        self.summary.pop('waitingSteps')
        with self.assertRaises(ValueError):
            self.adapter.cli.respond_interaction(CID, rid, 'accept')
        self.assertEqual(self.writes, [])

    def test_response_read_failure_never_uses_stale_metadata(self):
        rid = self.wait()
        self.adapter.cli.waiting_state_by_cid()
        self.fail_read = True
        with self.assertRaises(BridgeUnavailable):
            self.adapter.cli.respond_interaction(CID, rid, 'accept')
        self.assertEqual(self.writes, [])

    def test_response_rechecks_parent_relationship(self):
        rid = self.wait()
        self.summary['trajectoryMetadata'] = {'parentConversationId': 'old-parent'}
        self.adapter.cli.waiting_state_by_cid()
        self.summary['trajectoryMetadata'] = {'parentConversationId': 'new-parent'}
        with self.assertRaises(ValueError):
            self.adapter.cli.respond_interaction('old-parent', rid, 'accept')
        self.assertEqual(self.writes, [])

    def test_live_child_activity_marks_parent_busy(self):
        for field, value in [('notFullyIdle', True), ('hasActiveChildren', True),
                             ('hasRunningSubagents', True), ('backgroundTaskCount', 1)]:
            with self.subTest(field=field):
                self.summaries = {'child': {'trajectoryId': 'child-trajectory', field: value,
                                           'trajectoryMetadata': {'rootConversationId': CID}}}
                _, active = self.adapter.cli.waiting_state_by_cid(max_age=0)
                self.assertIn(CID, active)

    def test_current_request_still_responds_once(self):
        rid = self.wait()
        self.adapter.cli.respond_interaction(CID, rid, 'accept')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]['cascadeId'], CID)

    def test_refresh_removes_expired_requests_only_for_its_surface(self):
        rid = self.wait()
        self.adapter.cli.waiting_state_by_cid(surface='ide')
        ide_rid = rid.replace(':app:', ':ide:')
        self.adapter.cli.waiting_state_by_cid()
        self.summary.pop('waitingSteps')
        self.adapter.cli.waiting_state_by_cid(max_age=0)
        self.assertNotIn(rid, self.adapter.cli._waiting_cache)
        self.assertIn(ide_rid, self.adapter.cli._waiting_cache)

    def test_current_partial_waiting_step_can_read_full_step(self):
        rid = self.wait()
        full_step = self.summary['waitingSteps'][0].pop('step')
        with mock.patch.object(self.adapter.cli, '_collect_step_fallback', return_value=full_step):
            self.adapter.cli.respond_interaction(CID, rid, 'accept')
        self.assertEqual(len(self.writes), 1)
