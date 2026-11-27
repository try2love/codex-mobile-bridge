import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.model import gateway_id, normalize_steps
from bridge.clients.errors import BridgeUnavailable

ROOT = Path(__file__).resolve().parents[1]
CID = '11111111-2222-4333-8444-555555555555'


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


class AntigravityContextWindowAndTurnTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.directory = Path(self.temp.name) / 'bridge'
        self.root = self.home / '.gemini' / 'antigravity'
        _create_db(self.root, [(
            CID, 'Context Chat', 'hi', 3, '2026-11-19T10:00:00Z', '[]',
            'CASCADE_RUN_STATUS_IDLE', '', '', '', 0, 0, 0, '',
        )])

    def test_detail_enriches_context_window_when_live_and_falls_back_gracefully(self):
        _write_transcript(self.root, CID, [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'hello', 'created_at': '2026-11-19T10:00:00Z'},
            {
                'step_index': 1, 'type': 'PLANNER_RESPONSE', 'content': 'world',
                'input_tokens': 2500, 'cache_read_tokens': 97500, 'output_tokens': 300,
                'created_at': '2026-11-19T10:00:05Z',
            },
        ])
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        gen_meta = {
            'generatorMetadata': [
                {'chatModel': {'chatStartMetadata': {'contextWindowMetadata': {'maxContextTokens': 128000, 'estimatedTokensUsed': 10000}}}},
                {'chatModel': {'chatStartMetadata': {'contextWindowMetadata': {'maxContextTokens': 256000, 'estimatedTokensUsed': 100000}}}},
            ]
        }

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=lambda m, p=None, *, surface='app': (
                    gen_meta if m == 'GetCascadeTrajectoryGeneratorMetadata' else {}
                )):
            detail = adapter.call('detail', sid)
            ctx = detail['session']['contextUsage']
            self.assertEqual(ctx['usedTokens'], 100000)
            self.assertEqual(ctx['contextWindow'], 256000)
            self.assertEqual(ctx['outputTokens'], 300)

        # Repeated detail poll with unchanged stepCount reuses cache
        rpc_calls = []
        def counted_rpc(m, p=None, *, surface='app'):
            rpc_calls.append(m)
            return gen_meta if m == 'GetCascadeTrajectoryGeneratorMetadata' else {}

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=counted_rpc):
            detail = adapter.call('detail', sid)
            self.assertEqual(detail['session']['contextUsage']['contextWindow'], 256000)
            self.assertNotIn('GetCascadeTrajectoryGeneratorMetadata', rpc_calls)

        # RPC failure after cache invalidation must not break detail view
        adapter.cli._invalidate_trajectories_cache('app')
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=lambda m, p=None, *, surface='app': (
                    (_ for _ in ()).throw(BridgeUnavailable('down'))
                    if m == 'GetCascadeTrajectoryGeneratorMetadata' else {}
                )):
            detail = adapter.call('detail', sid)
            self.assertIsNone(detail['session']['contextUsage']['contextWindow'])

        # Transcript without token counters still synthesizes contextUsage from estimatedTokensUsed
        adapter.cli._invalidate_trajectories_cache('app')
        _write_transcript(self.root, CID, [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'hello', 'created_at': '2026-11-19T10:00:00Z'},
        ])
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=lambda m, p=None, *, surface='app': (
                    gen_meta if m == 'GetCascadeTrajectoryGeneratorMetadata' else {}
                )):
            detail = adapter.call('detail', sid)
            self.assertEqual(detail['session']['contextUsage'], {
                'usedTokens': 100000,
                'contextWindow': 256000,
                'measurement': 'session',
            })

    def test_normalize_steps_emits_system_messages_without_inferred_turn_clocks(self):
        steps = [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'Ask', 'created_at': '2026-11-19T10:00:00Z'},
            {'step_index': 1, 'type': 'SYSTEM_MESSAGE', 'content': 'Workspace reminder', 'created_at': '2026-11-19T10:00:01Z'},
            {'step_index': 2, 'type': 'PLANNER_RESPONSE', 'content': 'Working...', 'created_at': '2026-11-19T10:00:02Z'},
            {'step_index': 3, 'type': 'PLANNER_RESPONSE', 'content': 'Final answer', 'created_at': '2026-11-19T10:00:10Z'},
        ]
        messages, turns, _ = normalize_steps(steps, session_active=False)
        self.assertEqual(len(turns), 1)
        sys_msg = next(m for m in messages if m['id'] == 'step-1-system')
        self.assertEqual(sys_msg['role'], 'activity')
        self.assertEqual(sys_msg['kind'], 'context')
        self.assertEqual(sys_msg['title'], '系统消息')
        self.assertEqual(sys_msg['text'], 'Workspace reminder')

        assistants = [m for m in messages if m['role'] == 'assistant']
        self.assertEqual([a['phase'] for a in assistants], ['commentary', 'final_answer'])
        for msg in messages:
            self.assertEqual(msg['turnId'], 'turn-0')
            self.assertEqual(msg['turnStatus'], 'completed')
            self.assertNotIn('turnStartedAt', msg)
            self.assertNotIn('turnCompletedAt', msg)

        active_msgs, _, _ = normalize_steps(steps, session_active=True)
        for msg in active_msgs:
            self.assertEqual(msg['turnStatus'], 'inProgress')
            self.assertNotIn('turnCompletedAt', msg)
        self.assertEqual([m['phase'] for m in active_msgs if m['role'] == 'assistant'], ['commentary', 'commentary'])

        # Idle native state supersedes an old RUNNING compact snapshot, without inventing clocks.
        with_running_step = [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'Ask', 'created_at': '2026-11-19T10:00:00Z'},
            {'step_index': 1, 'type': 'GENERIC', 'status': 'RUNNING', 'content': 'bg', 'created_at': '2026-11-19T10:00:01Z'},
            {'step_index': 2, 'type': 'PLANNER_RESPONSE', 'content': 'Done', 'created_at': '2026-11-19T10:00:06Z'},
        ]
        idle_msgs, idle_turns, _ = normalize_steps(with_running_step, session_active=False)
        final = next(m for m in idle_msgs if m['role'] == 'assistant')
        self.assertEqual(final['turnStatus'], 'completed')
        self.assertEqual(final['phase'], 'final_answer')
        self.assertEqual(idle_turns[0]['status'], final['turnStatus'])
        self.assertNotIn('turnStartedAt', final)
        self.assertNotIn('turnCompletedAt', final)

    def test_partial_missing_or_reversed_step_clocks_never_become_turn_duration(self):
        variants = [
            [{'step_index': 20, 'type': 'PLANNER_RESPONSE', 'content': 'Partial answer',
              'created_at': '2026-11-19T10:00:10Z'}],
            [{'step_index': 0, 'type': 'USER_INPUT', 'content': 'Missing clock'},
             {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'content': 'Answer'}],
            [{'step_index': 0, 'type': 'USER_INPUT', 'content': 'Ask', 'created_at': '2026-11-19T10:00:10Z'},
             {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'content': 'Answer', 'created_at': '2026-11-19T10:00:00Z'}],
        ]
        for steps in variants:
            with self.subTest(steps=steps):
                messages, turns, _ = normalize_steps(steps)
                for item in messages + turns:
                    self.assertNotIn('turnStartedAt', item)
                    self.assertNotIn('turnCompletedAt', item)

    def test_failed_turn_status_matches_messages_without_success_phase(self):
        messages, turns, _ = normalize_steps([
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'Ask'},
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'status': 'FAILED', 'content': 'Failed response'},
        ])
        self.assertEqual(turns[0]['status'], 'failed')
        for message in messages:
            self.assertEqual(message['turnStatus'], 'failed')
            self.assertNotEqual(message.get('phase'), 'final_answer')


if __name__ == '__main__':
    unittest.main()
