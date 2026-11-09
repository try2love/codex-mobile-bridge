import base64
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid
from pathlib import Path

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.hook_ipc import HookIPCServer
from bridge.clients.antigravity.setup import disable_plugin, ensure_plugin, inspect_plugin
from bridge.clients.antigravity.store import AntigravityStore, parse_timestamp
from bridge.clients.discovery import discover_clients
from bridge.clients.manager import DesktopSessions


def _create_surface_db(surface_root: Path, rows: list[tuple]) -> None:
    surface_root.mkdir(parents=True, exist_ok=True)
    db_path = surface_root / 'conversation_summaries.db'
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            '''
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                preview TEXT,
                step_count INTEGER,
                last_modified_time TEXT,
                workspace_uris TEXT,
                status TEXT,
                source TEXT,
                project_id TEXT,
                parent_conversation_id TEXT,
                nesting_depth INTEGER,
                not_fully_idle INTEGER,
                killed INTEGER,
                agent_name TEXT
            )
            '''
        )
        conn.executemany(
            '''
            INSERT INTO conversation_summaries (
                conversation_id, title, preview, step_count, last_modified_time,
                workspace_uris, status, source, project_id, parent_conversation_id,
                nesting_depth, not_fully_idle, killed, agent_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _write_transcript(
    surface_root: Path,
    conversation_id: str,
    compact_steps: list[dict],
    full_steps: list[dict] | None = None,
) -> Path:
    logs_dir = surface_root / 'brain' / conversation_id / '.system_generated' / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    compact_path = logs_dir / 'transcript.jsonl'
    compact_path.write_text(
        '\n'.join(json.dumps(step, ensure_ascii=False) for step in compact_steps) + '\n',
        encoding='utf-8',
    )
    if full_steps is not None:
        full_path = logs_dir / 'transcript_full.jsonl'
        full_path.write_text(
            '\n'.join(json.dumps(step, ensure_ascii=False) for step in full_steps) + '\n',
            encoding='utf-8',
        )
    return logs_dir


class AntigravityStoreTest(unittest.TestCase):
    def test_parse_timestamp_handles_nanoseconds(self):
        ts = parse_timestamp('2026-11-08T01:23:45.123456789Z')
        self.assertGreater(ts, 1700000000.0)
        ts_offset = parse_timestamp('2026-11-08T09:23:45.123456789+08:00')
        self.assertAlmostEqual(ts, ts_offset, places=3)

    def test_store_reads_summaries_subagents_and_hydrated_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            scratch = Path(tmp) / 'scratch'
            app_root = home / '.gemini' / 'antigravity'
            ide_root = home / '.gemini' / 'antigravity-ide'
            cid_main = '11111111-2222-4333-8444-555555555555'
            cid_sub = '66666666-7777-4888-8999-aaaaaaaaaaaa'
            cid_ide = 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff'

            _create_surface_db(
                app_root,
                [
                    (
                        cid_main,
                        'Main Desktop Session',
                        'Preview text',
                        3,
                        '2026-11-08T02:00:00.987654321Z',
                        json.dumps(['file:///home/lks/aios/codex-mobile-bridge']),
                        'CASCADE_RUN_STATUS_IDLE',
                        'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
                        '',
                        '',
                        0,
                        0,
                        0,
                        '',
                    ),
                    (
                        cid_sub,
                        'Codebase Researcher',
                        '',
                        2,
                        '2026-11-08T01:20:00Z',
                        json.dumps(['file:///home/lks/aios/codex-mobile-bridge']),
                        'CASCADE_RUN_STATUS_RUNNING',
                        'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
                        '',
                        cid_main,
                        1,
                        1,
                        0,
                        'research',
                    ),
                ],
            )
            _create_surface_db(
                ide_root,
                [
                    (
                        cid_ide,
                        'IDE Refactor Task',
                        '',
                        1,
                        '2026-11-08T01:30:00Z',
                        json.dumps(['file:///home/lks/aios']),
                        'CASCADE_RUN_STATUS_IDLE',
                        'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
                        '',
                        '',
                        0,
                        0,
                        0,
                        '',
                    )
                ],
            )

            img_dir = app_root / 'brain' / cid_main
            img_dir.mkdir(parents=True, exist_ok=True)
            img_path = img_dir / 'preview.png'
            img_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
            img_path.write_bytes(img_bytes)

            compact_steps = [
                {
                    'step_index': 0,
                    'type': 'USER_INPUT',
                    'status': 'DONE',
                    'created_at': '2026-11-08T01:00:01Z',
                    'content': '請幫我檢查架構',
                    'media': [{'mime_type': 'image/png', 'uri': str(img_path)}],
                },
                {
                    'step_index': 1,
                    'type': 'PLANNER_RESPONSE',
                    'status': 'DONE',
                    'created_at': '2026-11-08T01:00:05Z',
                    'thinking': 'short truncated...',
                    'content': 'short content...',
                    'truncated_fields': ['thinking', 'content'],
                    'tool_calls': [
                        {
                            'id': 'call-1',
                            'name': 'run_command',
                            'args': {'CommandLine': 'git status', 'toolSummary': 'Git status check'},
                        }
                    ],
                },
                {
                    'step_index': 2,
                    'type': 'TOOL_RESPONSE',
                    'status': 'DONE',
                    'created_at': '2026-11-08T01:00:06Z',
                    'content': 'On branch main\nnothing to commit',
                },
            ]
            full_steps = [
                compact_steps[0],
                {
                    **compact_steps[1],
                    'thinking': '完整思考內容：先檢查目錄結構與 IPC 邊界',
                    'content': '完整回答內容：架構檢查完畢，可以接入 Antigravity。',
                },
                compact_steps[2],
            ]
            _write_transcript(app_root, cid_main, compact_steps, full_steps)

            store = AntigravityStore(scratch, home=home)
            sessions = store.list_sessions()
            self.assertEqual([row['nativeId'] for row in sessions], [cid_main, cid_ide])
            self.assertEqual(sessions[0]['backend'], 'app')
            self.assertEqual(sessions[0]['cwd'], '/home/lks/aios/codex-mobile-bridge')
            self.assertEqual(sessions[1]['backend'], 'ide')

            tasks = store.list_tasks(app_root, cid_main)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]['id'], cid_sub)
            self.assertEqual(tasks[0]['status'], 'running')

            page = store.read_transcript_page(app_root, cid_main, limit=40)
            self.assertFalse(page['history']['hasMore'])
            messages = page['messages']
            roles = [m['role'] for m in messages]
            self.assertEqual(roles, ['user', 'reasoning', 'activity', 'assistant'])
            self.assertEqual(messages[1]['text'], '完整思考內容：先檢查目錄結構與 IPC 邊界')
            self.assertEqual(messages[3]['text'], '完整回答內容：架構檢查完畢，可以接入 Antigravity。')
            self.assertEqual(messages[2]['title'], 'Ran Git status check')
            self.assertIn('git status', messages[2]['text'])
            self.assertIn('On branch main', messages[2]['text'])

            attachments = messages[0].get('attachments') or []
            self.assertEqual(len(attachments), 1)
            img_res = store.read_image(app_root, cid_main, {'stepIndex': 0, 'mediaIndex': 0})
            self.assertEqual(base64.b64decode(img_res['data']), img_bytes)
            self.assertEqual(img_res['mediaType'], 'image/png')

    def test_transcript_branch_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            app_root = home / '.gemini' / 'antigravity'
            cid = '22222222-3333-4444-5555-666666666666'
            steps = [
                {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': '2026-11-08T01:00:00Z', 'content': 'Test 0'},
                {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'created_at': '2026-11-08T01:00:01Z', 'content': 'Reply 0'},
                {'step_index': 2, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': '2026-11-08T01:00:02Z', 'content': 'Old 2'},
                {'step_index': 3, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'created_at': '2026-11-08T01:00:03Z', 'content': 'Old 3'},
                # Rewind/branch at step 2
                {'step_index': 2, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': '2026-11-08T01:00:10Z', 'content': 'New 2'},
                {'step_index': 3, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'created_at': '2026-11-08T01:00:11Z', 'content': 'New 3'},
            ]
            _write_transcript(app_root, cid, steps)
            store = AntigravityStore(Path(tmp) / 'scratch', home=home)
            page = store.read_transcript_page(app_root, cid, limit=40)
            messages = [m['text'] for m in page['messages'] if m.get('text')]
            self.assertEqual(messages, ['Test 0', 'Reply 0', 'New 2', 'New 3'])

    def test_model_effort_resolution_and_decoration(self):
        from bridge.clients.antigravity.cli_session import AntigravityCli
        from bridge.clients.antigravity.adapter import Antigravity
        with tempfile.TemporaryDirectory() as tmp:
            cli = AntigravityCli()
            m_low = cli.resolve_model_enum('gemini-3.8-flash', 'low')
            self.assertEqual(m_low, 'MODEL_PLACEHOLDER_M320')
            m_high = cli.resolve_model_enum('gemini-3.8-flash', 'high')
            self.assertEqual(m_high, 'MODEL_PLACEHOLDER_M318')
            m_argon = cli.resolve_model_enum('gemini-4-argon', 'medium')
            self.assertEqual(m_argon, 'MODEL_PLACEHOLDER_M418')

            adapter = Antigravity(Path(tmp))
            decorated = adapter._decorate_session({
                'id': 'sess-1',
                'nativeId': 'cid-1',
                'title': 'Test Session',
                'status': 'idle',
            })
            # Effort must be lowercase for WebUI options matching
            self.assertEqual(decorated['effort'], 'high')
            self.assertEqual(decorated['reasoningEffort'], 'high')
            self.assertEqual(decorated['effortLabel'], 'High')

    def test_create_session_flow_and_prompt_composition(self):
        from bridge.clients.antigravity.adapter import Antigravity
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Antigravity(Path(tmp))
            # Test prompt composition across text, firstMessage, prompt
            self.assertEqual(adapter._compose_prompt({'text': 'hello'}), 'hello')
            self.assertEqual(adapter._compose_prompt({'firstMessage': 'hello first'}), 'hello first')
            self.assertEqual(adapter._compose_prompt({'prompt': 'hello prompt'}), 'hello prompt')

            # Mock CLI methods to verify parameter forwarding in create
            recorded = {}
            def mock_start(source=None, model=None, effort=None):
                recorded['start'] = {'source': source, 'model': model, 'effort': effort}
                return 'cid-test-create-123'
            def mock_send(cid, prompt, model=None, effort=None):
                recorded['send'] = {'cid': cid, 'prompt': prompt, 'model': model, 'effort': effort}
                return {}

            adapter.cli.start_cascade = mock_start
            adapter.cli.send_user_message = mock_send

            res = adapter.call('create', None, {
                'firstMessage': 'TESTA',
                'model': 'gemini-3.8-flash',
                'effort': 'high',
            })
            self.assertEqual(res['status'], 'accepted')
            self.assertIn('result', res)
            self.assertEqual(res['result']['id'], res['id'])
            self.assertEqual(recorded['start']['source'], 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT')
            self.assertEqual(recorded['start']['model'], 'gemini-3.8-flash')
            self.assertEqual(recorded['send']['prompt'], 'TESTA')
            self.assertEqual(recorded['send']['model'], 'gemini-3.8-flash')
            self.assertEqual(recorded['send']['effort'], 'high')




class HookIPCAndPluginTest(unittest.TestCase):
    def test_plugin_setup_and_disable_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            state_dir = Path(tmp) / 'state'
            global_hooks = home / '.gemini' / 'config' / 'hooks.json'
            info = ensure_plugin(state_dir, home=home, enabled=True)
            self.assertTrue(info['enabled'])
            self.assertFalse(global_hooks.exists())
            plugin_dir = Path(info['pluginDir'])
            self.assertTrue((plugin_dir / 'plugin.json').is_file())
            self.assertTrue((plugin_dir / 'hooks.json').is_file())
            manifest = json.loads((plugin_dir / 'hooks.json').read_text(encoding='utf-8'))
            self.assertTrue(manifest['codex-mobile-bridge']['enabled'])
            self.assertIn('PreToolUse', manifest['codex-mobile-bridge'])
            self.assertIn('PostInvocation', manifest['codex-mobile-bridge'])

            disabled = disable_plugin(state_dir, home=home)
            self.assertFalse(disabled['enabled'])
            self.assertFalse(inspect_plugin(state_dir, home=home)['enabled'])

    def test_queue_action_operations(self):
        from bridge.clients.manager import WRITES
        self.assertIn('queue-action', WRITES)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            app_root = home / '.gemini' / 'antigravity'
            cid = '33333333-4444-5555-6666-777777777777'
            steps = [
                {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': '2026-11-08T01:00:00Z', 'content': 'Test 0'},
                {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'created_at': '2026-11-08T01:00:01Z', 'content': 'Reply 0'},
            ]
            _write_transcript(app_root, cid, steps)
            from bridge.clients.antigravity.adapter import Antigravity
            adapter = Antigravity(Path(tmp), home=home)
            from bridge.clients.antigravity.model import gateway_id
            sid = gateway_id('app', cid)
            entry = adapter.hooks.enqueue_follow_up(cid, 'Hello queued world', mode='queue')
            qid = entry['id']

            # Test steer
            res_steer = adapter.call('queue-action', sid, {'queueId': qid, 'operation': 'steer'})
            self.assertEqual(res_steer['status'], 'accepted')
            self.assertEqual(res_steer['item']['mode'], 'steer')

            # Test edit
            res_edit = adapter.call('queue-action', sid, {'queueId': qid, 'operation': 'edit'})
            self.assertEqual(res_edit['status'], 'accepted')
            self.assertEqual(res_edit['text'], 'Hello queued world')

            # Re-enqueue and test cancel
            entry2 = adapter.hooks.enqueue_follow_up(cid, 'To be cancelled', mode='queue')
            qid2 = entry2['id']
            res_cancel = adapter.call('queue-action', sid, {'queueId': qid2, 'operation': 'cancel'})
            self.assertEqual(res_cancel['status'], 'accepted')
            self.assertTrue(res_cancel['removed'])

    def test_hook_client_fail_open_and_live_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / 'ag-state'
            hook_script = Path(__file__).resolve().parents[1] / 'bridge' / 'clients' / 'antigravity' / 'hook_client.py'

            # 1. Fail-open when server is not running
            t0 = time.monotonic()
            proc = subprocess.run(
                [sys.executable, '-B', str(hook_script), 'pre_tool_use', '--state-dir', str(state_dir)],
                input=json.dumps({'conversationId': 'cid-1', 'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'ls'}}}),
                text=True,
                capture_output=True,
                timeout=3.0,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(json.loads(proc.stdout), {'decision': 'ask'})
            self.assertLess(elapsed_ms, 500.0)

            # 2. Live roundtrip over HookIPCServer
            server = HookIPCServer(state_dir)
            try:
                cid = '11111111-2222-4333-8444-555555555555'

                # Default mode ('desktop') -> returns {"decision": "ask"} and records heartbeat
                proc_desktop = subprocess.run(
                    [sys.executable, '-B', str(hook_script), 'pre_tool_use', '--state-dir', str(state_dir)],
                    input=json.dumps({'conversationId': cid, 'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'pwd'}}}),
                    text=True,
                    capture_output=True,
                    timeout=3.0,
                )
                self.assertEqual(json.loads(proc_desktop.stdout), {'decision': 'ask'})
                self.assertTrue(server.has_recent_heartbeat(cid))

                # Auto-allow mode -> returns {"decision": "allow"}
                server.set_permission_mode(cid, 'auto-allow')
                proc_auto = subprocess.run(
                    [sys.executable, '-B', str(hook_script), 'pre_tool_use', '--state-dir', str(state_dir)],
                    input=json.dumps({'conversationId': cid, 'toolCall': {'name': 'write_file', 'args': {'Path': '/tmp/a.txt'}}}),
                    text=True,
                    capture_output=True,
                    timeout=3.0,
                )
                self.assertEqual(json.loads(proc_auto.stdout), {'decision': 'allow'})

                # Mobile-ask mode -> blocks until resolve_approval is called
                server.set_permission_mode(cid, 'mobile-ask')
                result_holder = {}

                def _run_mobile_ask():
                    res = subprocess.run(
                        [sys.executable, '-B', str(hook_script), 'pre_tool_use', '--state-dir', str(state_dir)],
                        input=json.dumps({'conversationId': cid, 'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'rm -rf /tmp/x'}}}),
                        text=True,
                        capture_output=True,
                        timeout=5.0,
                    )
                    result_holder['stdout'] = res.stdout

                worker = threading.Thread(target=_run_mobile_ask)
                worker.start()
                pending = []
                for _ in range(40):
                    pending = server.pending_approvals_for(cid)
                    if pending:
                        break
                    time.sleep(0.025)
                self.assertEqual(len(pending), 1)
                req_id = pending[0]['id']
                server.resolve_approval(cid, req_id, 'decline')
                worker.join(timeout=3.0)
                decision_payload = json.loads(result_holder['stdout'])
                self.assertEqual(decision_payload['decision'], 'deny')

                # 1) Default queue mode ('queue' / '完成后发送'):
                #    PostInvocation must NOT pop it (avoids interrupting mid-turn),
                #    while Stop pops it when the current turn finishes.
                server.enqueue_follow_up(cid, '任務結束後再處理這條訊息', mode='queue')
                proc_post_queue = subprocess.run(
                    [sys.executable, '-B', str(hook_script), 'post_invocation', '--state-dir', str(state_dir)],
                    input=json.dumps({'conversationId': cid, 'hook': 'PostInvocation'}),
                    text=True,
                    capture_output=True,
                    timeout=3.0,
                )
                self.assertEqual(json.loads(proc_post_queue.stdout), {})

                proc_stop_queue = subprocess.run(
                    [sys.executable, '-B', str(hook_script), 'stop', '--state-dir', str(state_dir)],
                    input=json.dumps({'conversationId': cid, 'hook': 'Stop', 'terminationReason': 'COMPLETED'}),
                    text=True,
                    capture_output=True,
                    timeout=3.0,
                )
                stop_payload = json.loads(proc_stop_queue.stdout)
                self.assertEqual(stop_payload['decision'], 'continue')
                self.assertIn('任務結束後再處理這條訊息', stop_payload['reason'])

                # 2) Steer mode ('steer' / '补充当前任务'):
                #    PostInvocation immediately pops and injects it at the next step boundary.
                server.enqueue_follow_up(cid, '請順便跑一下單元測試', mode='steer')
                proc_post_steer = subprocess.run(
                    [sys.executable, '-B', str(hook_script), 'post_invocation', '--state-dir', str(state_dir)],
                    input=json.dumps({'conversationId': cid, 'hook': 'PostInvocation'}),
                    text=True,
                    capture_output=True,
                    timeout=3.0,
                )
                post_payload = json.loads(proc_post_steer.stdout)
                self.assertEqual(post_payload['terminationBehavior'], 'force_continue')
                self.assertEqual(
                    post_payload['injectSteps'],
                    [{'userMessage': '請順便跑一下單元測試'}],
                )
            finally:
                server.close()


class AntigravityDesktopManagerIntegrationTest(unittest.TestCase):
    def test_discovery_and_manager_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            data_dir = Path(tmp) / 'bridge-data'
            app_root = home / '.gemini' / 'antigravity'
            cid = '11111111-2222-4333-8444-555555555555'
            _create_surface_db(
                app_root,
                [
                    (
                        cid,
                        'Antigravity Mobile Bridge Test',
                        '',
                        2,
                        '2026-11-08T02:00:00Z',
                        json.dumps(['file:///home/lks/aios/codex-mobile-bridge']),
                        'CASCADE_RUN_STATUS_IDLE',
                        'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
                        '',
                        '',
                        0,
                        0,
                        0,
                        '',
                    )
                ],
            )
            _write_transcript(
                app_root,
                cid,
                [
                    {
                        'step_index': 0,
                        'type': 'USER_INPUT',
                        'status': 'DONE',
                        'created_at': '2026-11-08T01:00:01Z',
                        'content': 'Hello Antigravity',
                    },
                    {
                        'step_index': 1,
                        'type': 'PLANNER_RESPONSE',
                        'status': 'DONE',
                        'created_at': '2026-11-08T01:00:03Z',
                        'content': 'Hello from Antigravity 2.0!',
                    },
                ],
            )

            discovered = discover_clients(home=home)
            self.assertIn('antigravity', discovered)
            self.assertTrue(discovered['antigravity']['installed'])

            with mock.patch('bridge.clients.discovery.discover_clients', return_value=discovered):
                manager = DesktopSessions(data_dir)
                try:
                    manager.scan()
                    clients_payload = manager.clients()
                    ag_client = next((c for c in clients_payload['clients'] if c['id'] == 'antigravity'), None)
                    self.assertIsNotNone(ag_client)
                    self.assertFalse(ag_client['canQuitDesktop'])
                    self.assertFalse(ag_client['canForceQuit'])

                    ag_state_dir = data_dir / 'desktop-sessions' / 'antigravity'

                    # Enable Antigravity mobile access
                    toggled = manager.toggle_client({'provider': 'antigravity', 'enabled': True})
                    ag_enabled = next((c for c in toggled['clients'] if c['id'] == 'antigravity'), None)
                    self.assertTrue(ag_enabled['enabled'])
                    self.assertTrue(inspect_plugin(ag_state_dir, home=home)['enabled'])

                    # List sessions & read detail
                    listing = manager.call('antigravity', 'list')
                    self.assertEqual(len(listing['sessions']), 1)
                    sid = listing['sessions'][0]['id']

                    detail = manager.call('antigravity', 'detail', sid)
                    self.assertEqual(detail['session']['id'], sid)
                    self.assertTrue(detail['capabilities']['queue'])
                    self.assertTrue(detail['capabilities']['steer'])
                    self.assertEqual(len(detail['messages']), 2)
                    self.assertEqual(detail['messages'][1]['text'], 'Hello from Antigravity 2.0!')

                    # Read & change permission mode to mobile-ask
                    access_read = manager.call('antigravity', 'access', sid)
                    self.assertEqual(access_read['mode'], 'desktop')
                    access_res = manager.call(
                        'antigravity',
                        'access',
                        sid,
                        {'id': str(uuid.uuid4()), 'mode': 'mobile-ask'},
                    )
                    self.assertEqual(access_res['mode'], 'mobile-ask')

                    # Disable Antigravity mobile access
                    manager.toggle_client({'provider': 'antigravity', 'enabled': False})
                    self.assertFalse(inspect_plugin(ag_state_dir, home=home)['enabled'])
                finally:
                    manager.close()


if __name__ == '__main__':
    unittest.main()
