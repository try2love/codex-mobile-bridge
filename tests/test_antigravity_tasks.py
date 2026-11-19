import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.model import gateway_id
from bridge.clients.errors import ResumePreflightBlocked
from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]
CID = '11111111-2222-4333-8444-555555555555'
CHILD_RUN = '22222222-3333-4444-8555-666666666666'
CHILD_DONE = '33333333-4444-4555-8666-777777777777'
OTHER_CID = '44444444-5555-4666-8777-888888888888'


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


class AntigravityTasksTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.directory = Path(self.temp.name) / 'bridge'
        self.root = self.home / '.gemini' / 'antigravity'
        _create_db(self.root, [
            (CID, 'Parent Chat', '', 3, '2026-11-19T10:00:00Z', '[]', 'CASCADE_RUN_STATUS_RUNNING', '', '', '', 0, 1, 0, ''),
            (CHILD_RUN, 'Running Worker', '', 2, '2026-11-19T10:00:02Z', '[]', 'CASCADE_RUN_STATUS_RUNNING', '', '', CID, 1, 1, 0, 'research'),
            (CHILD_DONE, 'Finished Worker', '', 2, '2026-11-19T10:00:01Z', '[]', 'CASCADE_RUN_STATUS_IDLE', '', '', CID, 1, 0, 0, 'research'),
            (OTHER_CID, 'Other Session', '', 1, '2026-11-19T10:00:03Z', '[]', 'CASCADE_RUN_STATUS_RUNNING', '', '', '', 0, 1, 0, ''),
        ])

    def test_capabilities_expose_tasks_and_merge_live_background_tasks_for_owner_only(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        bg_reply = {
            'tasks': [
                {
                    'conversationId': CID,
                    'task': {
                        'taskId': f'{CID}/task-12',
                        'toolName': 'run_command',
                        'toolSummary': 'Run dev server',
                        'stepIndex': 12,
                        'isDaemon': True,
                    },
                },
                {
                    'conversationId': OTHER_CID,
                    'task': {
                        'taskId': f'{OTHER_CID}/task-99',
                        'toolName': 'run_command',
                        'toolSummary': 'Other session task',
                        'stepIndex': 99,
                    },
                },
            ]
        }

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=lambda m, p=None, *, surface='app': (
                    bg_reply if m == 'ListBackgroundTasks' else {}
                )):
            detail = adapter.call('detail', sid)
            self.assertTrue(detail['capabilities']['tasks'])
            tasks = adapter.call('tasks', sid)['tasks']
            self.assertEqual([t['id'] for t in tasks], ['bg:12', CHILD_RUN, CHILD_DONE])
            self.assertEqual(tasks[0]['title'], '[常驻] Run dev server')
            self.assertEqual([t['cancellable'] for t in tasks], [True, True, False])

        # Offline keeps tasks readable but never cancellable
        with mock.patch.object(adapter.cli, 'available', return_value=False):
            tasks = adapter.call('tasks', sid)['tasks']
            self.assertEqual([(t['id'], t['cancellable']) for t in tasks], [(CHILD_RUN, False), (CHILD_DONE, False)])

    def test_stop_task_stops_background_step_or_child_only_never_parent_or_unrelated(self):
        settings = self.directory / 'desktop-sessions' / 'settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        sid = gateway_id('app', CID)
        calls = []

        def fake_rpc(method, payload=None, *, surface='app'):
            calls.append((method, payload, surface))
            if method == 'ListBackgroundTasks':
                return {
                    'tasks': [{
                        'conversationId': CID,
                        'task': {'taskId': f'{CID}/task-12', 'toolSummary': 'Build', 'stepIndex': 12},
                    }]
                }
            return {}

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            # 1. Stop live background step via CancelCascadeSteps
            res = manager.call('antigravity', 'stop-task', sid, {'id': str(uuid.uuid4()), 'taskId': 'bg:12'})
            self.assertEqual(res['status'], 'accepted')
            self.assertIn(('CancelCascadeSteps', {'cascade_id': CID, 'step_indices': [12]}, 'app'), calls)

            # 2. Non-existent background step is blocked cleanly before CancelCascadeSteps
            calls.clear()
            blocked = manager.call('antigravity', 'stop-task', sid, {'id': str(uuid.uuid4()), 'taskId': 'bg:999'})
            self.assertEqual(blocked['status'], 'blocked')
            self.assertFalse(any(m == 'CancelCascadeSteps' for m, _, _ in calls))

            # 3. Stop verified running child subagent without affecting parent
            calls.clear()
            res_child = manager.call('antigravity', 'stop-task', sid, {'id': str(uuid.uuid4()), 'taskId': CHILD_RUN})
            self.assertEqual(res_child['status'], 'accepted')
            self.assertEqual(
                calls,
                [
                    ('CancelCascadeInvocation', {'cascade_id': CHILD_RUN, 'kill_background_tasks': True, 'notify_parent': True}, 'app'),
                    ('ForceStopCascadeTree', {'conversationId': CHILD_RUN}, 'app'),
                ],
            )

            # 4. Parent self-ID, unrelated conversation, and completed child are all blocked
            for forbidden in (CID, OTHER_CID, CHILD_DONE, 'invalid-id'):
                calls.clear()
                res_bad = manager.call('antigravity', 'stop-task', sid, {'id': str(uuid.uuid4()), 'taskId': forbidden})
                self.assertEqual(res_bad['status'], 'blocked')
                self.assertEqual(calls, [])

        # Offline stop-task fails preflight
        with mock.patch.object(adapter.cli, 'available', return_value=False):
            with self.assertRaises(ResumePreflightBlocked):
                adapter.call('stop-task', sid, {'taskId': CHILD_RUN})


if __name__ == '__main__':
    unittest.main()
