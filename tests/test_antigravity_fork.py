import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.model import gateway_id, normalize_steps
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]
CID = '11111111-2222-4333-8444-555555555555'
CHILD_CID = '22222222-3333-4444-8555-666666666666'


def _write_transcript(surface_root, cid, steps):
    logs = surface_root / 'brain' / cid / '.system_generated' / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / 'transcript.jsonl').open('w', encoding='utf-8') as stream:
        for row in steps:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


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


class AntigravityForkTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.directory = Path(self.temp.name) / 'bridge'
        self.root = self.home / '.gemini' / 'antigravity'
        self.workspace = Path(self.temp.name) / 'project'
        self.workspace.mkdir(parents=True)
        _create_db(self.root, [(
            CID, 'Forkable Chat', 'hi', 8, '2026-11-19T10:00:00Z',
            json.dumps([self.workspace.resolve().as_uri()]),
            'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
            '', '', 0, 0, 0, '',
        )])
        self.steps = [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'Turn 1', 'created_at': '2026-11-19T10:00:00Z'},
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'content': 'Checking tool', 'tool_calls': [{'name': 'read_file', 'args': {'Path': 'a.py'}}], 'created_at': '2026-11-19T10:00:01Z'},
            {'step_index': 2, 'type': 'GENERIC', 'content': 'file body', 'created_at': '2026-11-19T10:00:02Z'},
            {'step_index': 3, 'type': 'PLANNER_RESPONSE', 'content': 'Turn 1 answer', 'created_at': '2026-11-19T10:00:03Z'},
            # Trailing non-planner step before next user input (like real AGY 2.33 transcripts)
            {'step_index': 4, 'type': 'GENERIC', 'content': '', 'created_at': '2026-11-19T10:00:04Z'},
            {'step_index': 5, 'type': 'USER_INPUT', 'content': 'Turn 2', 'created_at': '2026-11-19T10:01:00Z'},
            {'step_index': 6, 'type': 'PLANNER_RESPONSE', 'content': 'Turn 2 answer', 'created_at': '2026-11-19T10:01:02Z'},
        ]
        _write_transcript(self.root, CID, self.steps)

    def test_fork_message_id_attaches_only_to_final_assistant_of_completed_turns(self):
        messages, _, _ = normalize_steps(self.steps, session_active=False)
        assistants = [m for m in messages if m['role'] == 'assistant']
        # Intermediate commentary at step 1 must NOT carry forkMessageId
        self.assertEqual([a.get('forkMessageId') for a in assistants], [None, '4', '6'])

        active_msgs, _, _ = normalize_steps(self.steps, session_active=True)
        active_assistants = [m for m in active_msgs if m['role'] == 'assistant']
        # While Turn 2 is active, only completed Turn 1 carries forkMessageId
        self.assertEqual([a.get('forkMessageId') for a in active_assistants], [None, '4', None])

        # A completed turn with an intermediate RUNNING tool snapshot in compact logs still gets forkMessageId
        with_running_tool = [
            dict(step, status='RUNNING') if step['step_index'] == 2 else dict(step)
            for step in self.steps
        ]
        repaired_msgs, _, _ = normalize_steps(with_running_tool, session_active=False)
        repaired_assistants = [m for m in repaired_msgs if m['role'] == 'assistant']
        self.assertEqual([a.get('forkMessageId') for a in repaired_assistants], [None, '4', '6'])

    def test_paginated_history_preserves_exact_turn_boundary_fork_anchor(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        page = adapter.store.read_transcript_page(self.root, CID, before='5', session_active=False)
        assistants = [m for m in page['messages'] if m['role'] == 'assistant']
        self.assertEqual([a.get('forkMessageId') for a in assistants], [None, '4'])

    def test_fork_action_validates_anchor_and_preserves_manager_receipt_idempotency(self):
        settings = self.directory / 'desktop-sessions' / 'settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        sid = gateway_id('app', CID)
        calls = []

        def fake_rpc(method, payload=None, *, surface='app', expected_identity=None):
            calls.append((method, payload, surface))
            if method == 'GetAllCascadeTrajectories':
                return {'trajectorySummaries': {CID: {'status': 'CASCADE_RUN_STATUS_IDLE'}}}
            if method == 'ForkConversation':
                self.assertEqual(expected_identity, 'source')
                return {'newCascadeId': CHILD_CID, 'forkedAtStepIndex': payload['forkAtStepIndex'],
                        'projectEnvInfo': {'workspaceUris': [self.workspace.resolve().as_uri()]}}
            return {}

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'source_identity', return_value='source', create=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            detail = adapter.call('detail', sid)
            self.assertTrue(detail['capabilities']['fork'])

            # Mid-turn step 1 is blocked before hitting LS RPC (no unknown receipt left behind)
            blocked = manager.call('antigravity', 'fork', sid, {'id': str(uuid.uuid4()), 'messageId': '1'})
            self.assertEqual(blocked['status'], 'blocked')
            self.assertFalse(any(m == 'ForkConversation' for m, _, _ in calls))

            # Valid turn-end anchor forks cleanly and returns gateway-scoped child ID
            rid = str(uuid.uuid4())
            first = manager.call('antigravity', 'fork', sid, {'id': rid, 'messageId': '4'})
            self.assertEqual(first['status'], 'ready')
            self.assertEqual(first['result']['id'], gateway_id('app', CHILD_CID))
            fork_calls = [p for m, p, _ in calls if m == 'ForkConversation']
            self.assertEqual(len(fork_calls), 1)
            self.assertEqual(fork_calls[0], {
                'sourceCascadeId': CID,
                'forkAtStepIndex': 4,
                'targetForkWorkspace': 'FORK_TARGET_CURRENT_WORKSPACE',
                'sourceWorkspaceUris': [self.workspace.resolve().as_uri()],
            })

            # Duplicate receipt returns cached child without re-invoking ForkConversation
            replayed = manager.call('antigravity', 'fork', sid, {'id': rid, 'messageId': '4'})
            self.assertEqual(replayed['result']['id'], gateway_id('app', CHILD_CID))
            self.assertEqual(sum(1 for m, _, _ in calls if m == 'ForkConversation'), 1)

    def test_fork_requires_workspace_and_confirms_returned_project(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        with mock.patch.object(adapter.cli, 'call_ls_rpc') as rpc:
            with self.assertRaises(ValueError):
                adapter.cli.fork_conversation(CID, 4, cwd=str(self.workspace / 'missing'))
            rpc.assert_not_called()
        for result in (
            {'newCascadeId': CHILD_CID, 'projectEnvInfo': {'workspaceUris': ['file:///different']}},
            {'newCascadeId': CHILD_CID, 'new_cascade_id': CID},
            {'newCascadeId': CHILD_CID},
        ):
            with self.subTest(result=result), \
                    mock.patch.object(adapter.cli, 'source_identity', return_value='source', create=True), \
                    mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=[result, {}]) as rpc:
                with self.assertRaises(BridgeUnavailable):
                    adapter.cli.fork_conversation(CID, 4, cwd=str(self.workspace))
                writes = [c for c in rpc.call_args_list if c.args[0] == 'ForkConversation']
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0].kwargs['expected_identity'], 'source')

    def test_fork_rejects_changed_source_before_dispatch(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        target = {'address': 'http://127.0.0.1:9999', 'token': 'tok', 'identity': 'changed'}
        with mock.patch('bridge.clients.antigravity.cli_session.endpoint', return_value=target), \
                mock.patch.object(adapter.cli._opener, 'open') as request:
            with self.assertRaises(BridgeUnavailable):
                adapter.cli.fork_conversation(CID, 4, cwd=str(self.workspace), expected_identity='source')
            request.assert_not_called()

    def test_fork_maps_known_native_rejections_without_leaking_raw_error_body(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        body = json.dumps({
            'code': 'internal',
            'message': 'internal: forking is not supported for conversations that have invoked subagents (secret-token-xyz)',
        }).encode('utf-8')
        http_err = urllib.error.HTTPError(
            'http://127.0.0.1:9999/ForkConversation', 500, 'Internal Server Error', {}, io.BytesIO(body),
        )
        with mock.patch('bridge.clients.antigravity.cli_session.endpoint', return_value={'address': 'http://127.0.0.1:9999', 'token': 'tok', 'identity': {}}), \
                mock.patch('bridge.clients.antigravity.cli_session.verify_endpoint'), \
                mock.patch.object(adapter.cli._opener, 'open', side_effect=http_err):
            with self.assertRaises(ValueError) as ctx:
                adapter.cli.fork_conversation(CID, 4, cwd=str(self.workspace))
        self.assertIn('子代理', str(ctx.exception))
        self.assertNotIn('secret-token-xyz', str(ctx.exception))

        # Offline or active sessions cannot fork
        sid = gateway_id('app', CID)
        with mock.patch.object(adapter.cli, 'available', return_value=False):
            detail = adapter.call('detail', sid)
            self.assertFalse(detail['capabilities']['fork'])
            with self.assertRaises(BridgeUnavailable):
                adapter.call('fork', sid, {'messageId': '4'})

    def test_fork_inherits_parent_model_effort_and_work_mode_and_send_normalizes_empty_model(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        child_sid = gateway_id('app', CHILD_CID)
        adapter._update_session_settings(sid, model='MODEL_TEST', effort='high', work_mode='plan')
        def fake_rpc(method, payload=None, *, surface='app', expected_identity=None):
            if method == 'GetAllCascadeTrajectories':
                return {'trajectorySummaries': {CID: {'status': 'CASCADE_RUN_STATUS_IDLE'}}}
            if method == 'ForkConversation':
                return {'newCascadeId': CHILD_CID, 'forkedAtStepIndex': 4,
                        'projectEnvInfo': {'workspaceUris': [self.workspace.resolve().as_uri()]}}
            return {}
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'source_identity', return_value='source', create=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            res = adapter.call('fork', sid, {'messageId': '4'})
            self.assertEqual(res['result']['id'], child_sid)
        self.assertEqual(adapter._settings.get(child_sid), {
            'model': 'MODEL_TEST', 'effort': 'high', 'workMode': 'plan',
        })
        sent = {}
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', return_value={}), \
                mock.patch.object(adapter.cli, 'slash_commands', return_value=[]), \
                mock.patch.object(adapter.cli, 'send_user_message',
                                  side_effect=lambda cid, prompt, **kw: sent.update(kw)):
            adapter._update_session_settings(sid, model='', effort='', work_mode='default')
            adapter.call('send', sid, {'text': 'hello'})
        self.assertIsNone(sent['model'])
        self.assertNotIn('effort', sent)


if __name__ == '__main__':
    unittest.main()
