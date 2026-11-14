from __future__ import annotations

import base64
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid
from pathlib import Path, PureWindowsPath

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.hook_ipc import HookIPCServer, _write_frame
from bridge.clients.antigravity.setup import PRE_TOOL_MATCHER, disable_plugin, ensure_plugin, inspect_plugin
from bridge.clients.antigravity.store import AntigravityStore, parse_timestamp, local_file_path
from bridge.clients.antigravity.cli_session import AntigravityCli
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.antigravity.model import format_tool_call, gateway_id, normalize_steps
from bridge.platforms import antigravity as native
from bridge.clients.discovery import discover_clients
from bridge.clients.manager import DesktopSessions


ROOT = Path(__file__).resolve().parents[1]
(ROOT/'.tmp').mkdir(exist_ok=True)
CID = '11111111-2222-4333-8444-555555555555'

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
    def test_long_single_turn_is_paged_without_losing_older_steps(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            home = Path(tmp)
            root = home/'.gemini/antigravity'
            steps = [{'step_index': 0, 'type': 'USER_INPUT', 'content': 'long turn'}]
            steps += [{'step_index': i, 'type': 'PLANNER_RESPONSE', 'content': str(i)}
                      for i in range(1, 601)]
            _write_transcript(root, CID, steps)
            store = AntigravityStore(home/'scratch', home=home)
            seen, before = set(), None
            while True:
                page = store.read_transcript_page(root, CID, limit=40, before=before, session_active=True)
                ids = {message['id'] for message in page['messages']}
                self.assertLessEqual(len(ids), 260)
                self.assertFalse(seen & ids)
                seen.update(ids)
                if before is not None:
                    self.assertTrue(all(turn['status'] == 'completed' for turn in page['turns']))
                if not page['history']['hasMore']:
                    break
                next_before = page['history']['before']
                self.assertTrue(before is None or int(next_before) < int(before))
                before = next_before
            self.assertEqual(len(seen), len(steps))
            self.assertIn('step-0-user', seen)

    def test_parse_timestamp_handles_nanoseconds(self):
        ts = parse_timestamp('2026-11-08T01:23:45.123456789Z')
        self.assertGreater(ts, 1700000000.0)
        ts_offset = parse_timestamp('2026-11-08T09:23:45.123456789+08:00')
        self.assertAlmostEqual(ts, ts_offset, places=3)

    def test_message_times_use_native_epoch_milliseconds_without_inventing_missing_times(self):
        messages, _, _ = normalize_steps([
            {'type': 'USER_INPUT', 'content': 'question', 'created_at': '2026-11-08T01:23:45.123456789Z'},
            {'type': 'PLANNER_RESPONSE', 'content': 'reply', 'created_at': '2026-11-08T09:23:46.123456789+08:00'},
            {'type': 'PLANNER_RESPONSE', 'content': 'old reply'},
        ])
        self.assertAlmostEqual(messages[0]['timestamp'], parse_timestamp('2026-11-08T01:23:45.123456Z') * 1000, places=2)
        self.assertEqual(messages[1]['timestamp'] - messages[0]['timestamp'], 1000)
        self.assertIsNone(messages[2]['timestamp'])

    def test_store_reads_summaries_subagents_and_hydrated_transcript(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
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
            self.assertEqual(sessions[0]['cwd'], r'\home\lks\aios\codex-mobile-bridge' if os.name == 'nt' else '/home/lks/aios/codex-mobile-bridge')
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
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
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


class AntigravitySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)/'home'
        self.directory = Path(self.temp.name)/'gateway'
        self.root = self.home/'.gemini/antigravity'
        self.row = (CID, 'Task', '', 0, '2026-11-08T02:00:00Z', '[]',
                    'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
                    '', '', 0, 0, 0, '')

    def adapter(self):
        _create_surface_db(self.root, [self.row])
        return Antigravity(self.directory, home=self.home)

    def test_file_uris_preserve_drive_unc_spaces_and_literal_percent(self):
        for path in (PureWindowsPath('C:/My Project/中文'), PureWindowsPath('//server/share/My Project')):
            self.assertEqual(local_file_path(path.as_uri(), windows=True), str(path))
        self.assertEqual(local_file_path('file:///tmp/a%2520b', windows=False), '/tmp/a%20b')
        self.assertEqual(local_file_path('file://elsewhere/tmp/data', windows=False), '')

    def test_construct_and_history_reads_do_not_start_hooks_or_send(self):
        with mock.patch('bridge.clients.antigravity.adapter.HookIPCServer') as hooks:
            adapter = self.adapter()
            hooks.assert_not_called()
        self.assertFalse(self.directory.exists())
        _write_transcript(self.root, CID, [{'step_index': 0, 'type': 'USER_INPUT', 'content': 'history'}])
        with mock.patch.object(adapter.cli, 'available', return_value=False), \
                mock.patch.object(adapter.cli, 'send_user_message') as send, \
                mock.patch.object(adapter.cli, 'start_cascade') as start:
            detail = adapter.call('detail', gateway_id('app', CID))
            self.assertEqual(detail['messages'][0]['text'], 'history')
            self.assertFalse(detail['capabilities']['send'])
            send.assert_not_called()
            start.assert_not_called()
        self.assertIsNone(adapter.hooks)

    def test_source_scoped_identity_cannot_select_other_surface(self):
        adapter = self.adapter()
        _create_surface_db(self.home/'.gemini/antigravity-ide', [self.row])
        self.assertIsNone(adapter.store.resolve_session(CID))
        self.assertEqual(adapter.store.resolve_session(gateway_id('ide', CID))['surfaceDirKey'], 'ide')

    def test_shared_accounts_preserve_native_source_and_never_read_secrets(self):
        adapter = self.adapter()
        # Login may be available before the first conversation creates history.
        with mock.patch.object(adapter.cli, 'available', side_effect=lambda surface: surface != 'ide'), \
                mock.patch.object(adapter.cli, 'user_status', side_effect=lambda surface: {
                    'email': surface+'@example.invalid', 'userId': surface,
                    'accessToken': 'must-not-leak', 'name': surface}) as status:
            value = adapter.accounts()
        self.assertEqual([row['surface'] for row in value['accounts']], ['app', 'cli'])
        self.assertEqual(len(set(value['activeIds'])), 2)
        self.assertTrue(value['readOnly'])
        self.assertFalse(value['canSwitch'])
        self.assertFalse(value['canImport'])
        self.assertNotIn('must-not-leak', json.dumps(value))
        self.assertEqual([call.args[0] for call in status.call_args_list], ['app', 'cli'])
        self.assertIn('IDE', value['message'])

    def test_shared_accounts_keep_other_sources_when_one_rpc_fails(self):
        adapter = self.adapter()
        def status(surface):
            if surface == 'app':
                raise BridgeUnavailable('native read failed')
            return {'email': 'same@example.invalid'} if surface == 'cli' else {}
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'user_status', side_effect=status):
            value = adapter.accounts()
        self.assertEqual(len(value['accounts']), 1)
        self.assertEqual(value['accounts'][0]['surface'], 'cli')
        self.assertIn('账号状态暂不可用', value['message'])
        self.assertIn('尚未登录', value['message'])

    def test_shared_accounts_manager_does_not_fall_through_to_deepseek_store(self):
        settings = self.directory/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        with mock.patch.object(manager.adapters['antigravity'].cli, 'available', return_value=False), \
                mock.patch.object(manager, '_account_store', side_effect=AssertionError('wrong account store')):
            value = manager.client_accounts('antigravity', {'operation': 'list'})
            self.assertEqual(value['accounts'], [])
            self.assertEqual(value['provider'], 'antigravity')
            self.assertFalse(value['canSwitch'])
            for operation in ('import-current', 'save-api'):
                with self.assertRaisesRegex(ValueError, '原客户端'):
                    manager.client_accounts('antigravity', {'operation': operation})

    def test_account_model_directory_groups_sources_without_history(self):
        adapter = self.adapter()
        with mock.patch.object(adapter.cli, 'available', side_effect=lambda surface: surface != 'ide'), \
                mock.patch.object(adapter, '_account', side_effect=lambda surface, refresh: {
                    'models': [{'id': 'same-model', 'name': surface}], 'notice': ''}) as read:
            value = adapter.call('account')
            self.assertEqual([row['surface'] for row in value['groups']], ['app', 'cli'])
            self.assertEqual([row['models'][0]['name'] for row in value['groups']], ['app', 'cli'])
            read.reset_mock()
            value = adapter.call('account', body={'surface': 'cli'})
            self.assertEqual(value['models'][0]['name'], 'cli')
            read.assert_called_once_with('cli', False)
            with self.assertRaisesRegex(ValueError, '来源无效'):
                adapter.call('account', body={'surface': 'other'})

    def test_account_model_source_failure_keeps_previous_shared_cache(self):
        settings = self.directory/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        with mock.patch.object(manager, '_account_model_identity', return_value='a'*64), \
                mock.patch.object(manager, '_schedule_account_model_refresh'), \
                mock.patch.object(manager, '_read', side_effect=lambda provider, action: adapter.call(action)), \
                mock.patch.object(adapter.cli, 'available', side_effect=lambda surface: surface != 'ide'), \
                mock.patch.object(adapter, '_account', return_value={'models': [{'id': 'known'}]}) as read:
            good = manager._account_models('antigravity', refresh=True)
            read.side_effect = lambda surface, refresh: (_ for _ in ()).throw(BridgeUnavailable('offline')) if surface == 'app' else {'models': [{'id': 'new'}]}
            partial = adapter.call('account')
            self.assertIn('原客户端模型暂不可用', partial['modelError'])
            self.assertEqual(partial['groups'][1]['models'][0]['id'], 'new')
            stale = manager._account_models('antigravity', refresh=True)
            self.assertEqual(stale['groups'], good['groups'])
            self.assertEqual(stale['cache']['updatedAt'], good['cache']['updatedAt'])
            self.assertEqual(stale['cache']['state'], 'stale')

    def test_cli_display_source_never_redirects_app_history_to_the_independent_cli_owner(self):
        cli_row = (*self.row[:7], 'CORTEX_TRAJECTORY_SOURCE_CLI', *self.row[8:])
        _create_surface_db(self.root, [cli_row])
        _create_surface_db(self.home/'.gemini/antigravity-cli', [cli_row])
        adapter = Antigravity(self.directory, home=self.home)
        app, cli = gateway_id('app', CID), gateway_id('cli', CID)
        self.assertNotEqual(app, cli)
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', return_value={}), \
                mock.patch.object(adapter.cli, 'send_user_message') as send, \
                mock.patch.object(adapter, '_account', return_value={'models': []}) as account:
            rows = {row['id']: row for row in adapter.call('list')['sessions']}
            self.assertEqual(rows[app]['backend'], 'cli')
            self.assertEqual(rows[app]['surfaceDirKey'], 'app')
            for sid, owner in ((app, 'app'), (cli, 'cli')):
                adapter.call('send', sid, {'text': owner})
                self.assertEqual(send.call_args.kwargs['surface'], owner)
                adapter.call('catalog', sid)
                self.assertEqual(account.call_args.args[0], owner)

    def test_offline_composer_reserves_shared_controls_without_granting_capabilities(self):
        adapter = self.adapter()
        with mock.patch.object(adapter.cli, 'available', return_value=False):
            row = adapter.call('list')['sessions'][0]
            self.assertTrue(row['composerCapabilities']['skills'])
            self.assertTrue(row['composerCapabilities']['plan'])
            self.assertFalse(row['capabilities']['skills'])
            self.assertFalse(row['capabilities']['plan'])
            self.assertFalse(row['capabilities']['send'])
            self.assertEqual(row['permissionLabel'], '电脑端处理')
            self.assertTrue(row['readOnly'])

    def test_management_keeps_source_model_and_permission_writes_scoped(self):
        _create_surface_db(self.root, [self.row])
        _create_surface_db(self.home/'.gemini/antigravity-cli', [self.row])
        settings = self.directory/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        with mock.patch.object(adapter.cli, 'available', side_effect=lambda surface: surface == 'cli'), \
                mock.patch.object(adapter, 'account_scope', return_value='native-account'), \
                mock.patch.object(adapter.cli, 'catalog_models', side_effect=lambda **kw: {
                    'models': [{'id': kw['surface'], 'name': kw['surface'], 'nativeModel': 'MODEL_NATIVE_'+kw['surface'].upper()}], 'userStatus': {}, 'notice': ''}):
            state = manager.control({'action': 'antigravity-management', 'operation': 'list'})
            sources = {row['id']: row for row in state['surfaces']}
            self.assertFalse(sources['app']['connected'])
            self.assertTrue(sources['app']['historyAvailable'])
            self.assertTrue(sources['cli']['connected'])
            self.assertFalse(sources['ide']['historyAvailable'])
            sid = gateway_id('cli', CID)
            request = {'action': 'antigravity-management', 'operation': 'catalog', 'surface': 'cli', 'sessionId': sid}
            catalog = manager.control(request)
            self.assertEqual(catalog['models'][0]['id'], 'cli')
            with mock.patch.object(manager, 'call', wraps=manager.call) as call:
                with self.assertRaisesRegex(ValueError, '来源不匹配'):
                    manager.control({**request, 'surface': 'app'})
                call.assert_not_called()
            result = manager.control({**request, 'operation': 'settings', 'id': str(uuid.uuid4()),
                'model': 'cli', 'modelCatalogIdentity': catalog['cache']['identity']})
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(adapter._settings, {sid: {'model': 'cli'}})
            result = manager.control({**request, 'operation': 'access', 'id': str(uuid.uuid4()), 'mode': 'mobile-ask'})
            self.assertNotEqual(result['status'], 'accepted')
            self.assertIn('启用 Antigravity', result['message'])

    def test_unknown_idle_and_unreadable_native_database_never_send(self):
        adapter = self.adapter()
        self.assertFalse(adapter.store.is_native_busy(self.root, CID))
        with self.assertRaises(ValueError):
            adapter.store.is_native_busy(self.root, str(uuid.uuid4()))
        conn = sqlite3.connect(self.root/'conversation_summaries.db')
        conn.execute('UPDATE conversation_summaries SET not_fully_idle=NULL')
        conn.commit(); conn.close()
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'send_user_message') as send:
            with self.assertRaises(ValueError):
                adapter.call('send', gateway_id('app', CID), {'text': 'hello'})
            send.assert_not_called()
        with mock.patch('bridge.clients.antigravity.store.sqlite3.connect', side_effect=sqlite3.OperationalError('locked')) as connect:
            self.assertEqual(adapter.store.list_sessions(), [])
            with self.assertRaises(sqlite3.OperationalError):
                adapter.store.is_native_busy(self.root, CID)
            self.assertEqual(connect.call_count, 2)
            self.assertFalse((self.directory/'scratch').exists())

    def test_creation_sends_only_after_exact_project_binding(self):
        adapter = Antigravity(self.directory, home=self.home)
        project = Path(self.temp.name)/'My Project'
        project.mkdir()
        request = {'directoryProject': {'surface': 'app', 'cwd': str(project)}, 'text': 'first'}
        for reply in ({'cascadeId': CID}, {'cascadeId': CID, 'projectEnvInfo': {'workspaceUris': ['file:///wrong']}}):
            with mock.patch.object(adapter.cli, 'call_ls_rpc', return_value=reply) as rpc, \
                    mock.patch.object(adapter.cli, 'send_user_message') as send:
                with self.assertRaises(BridgeUnavailable):
                    adapter.call('create', body=request)
                send.assert_not_called()
                self.assertEqual(rpc.call_count, 1)
                self.assertEqual(rpc.call_args.args[1]['workspace_uris'], [project.resolve().as_uri()])
        reply = {'cascadeId': CID, 'projectEnvInfo': {'workspaceUris': [project.resolve().as_uri()]}}
        with mock.patch.object(adapter.cli, 'call_ls_rpc', return_value=reply), \
                mock.patch.object(adapter.cli, 'send_user_message') as send:
            result = adapter.call('create', body=request)
            self.assertEqual(result['status'], 'accepted')
            send.assert_called_once_with(CID, 'first', model=None, surface='app')

    def test_model_catalog_is_native_only_and_surface_specific(self):
        adapter = self.adapter()
        status = {'cascadeModelConfigData': {'clientModelConfigs': [
            {'modelId': 'native-id', 'label': 'Native', 'modelOrAlias': {'model': 'NATIVE_ENUM'}}]}}
        with mock.patch.object(adapter.cli, 'user_status', return_value=status) as query:
            models = adapter.cli.catalog_models(surface='ide')
            self.assertEqual(models['models'][0]['id'], 'native-id')
            self.assertFalse(models['models'][0]['supportsEffort'])
            self.assertNotIn('usage', models)
            query.assert_called_once_with('ide')
        with mock.patch.object(adapter.cli, 'available', side_effect=lambda surface: surface == 'ide'), \
                mock.patch.object(adapter, '_account', return_value={'models': []}) as account:
            _create_surface_db(self.home/'.gemini/antigravity-ide', [self.row])
            adapter.call('account', body={'surface': 'ide'})
            account.assert_called_once_with('ide', False)

    def test_profile_hook_lock_has_one_owner_and_is_released(self):
        path = self.directory/'lock'
        first = native.profile_lease(path)
        try:
            with self.assertRaises(ValueError):
                native.profile_lease(path)
        finally:
            first.close()
        native.profile_lease(path).close()

    def test_rpc_revalidates_before_and_after_without_retry(self):
        cli = AntigravityCli(self.home)
        target = {'address': 'http://127.0.0.1:12345', 'token': 'fixture'}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"cascadeId":"id"}'
        with mock.patch('bridge.clients.antigravity.cli_session.endpoint', return_value=target), \
                mock.patch('bridge.clients.antigravity.cli_session.verify_endpoint', side_effect=[None, ValueError('changed')]) as verify, \
                mock.patch.object(cli._opener, 'open', return_value=response) as opened:
            with self.assertRaises(BridgeUnavailable):
                cli.call_ls_rpc('SendUserCascadeMessage', {'cascade_id': CID})
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(opened.call_count, 1)

    def test_native_endpoint_rejects_wrong_source_or_changed_identity(self):
        image = '/official/language_server'
        identity = {'pid': 123, 'uid': 1, 'image': image, 'birth': 123}
        args = ['--app_data_dir=antigravity-ide', '--csrf_token='+'a'*32]
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.object(native, '_candidates', return_value=[(123, args)]):
            with self.assertRaises(ValueError):
                native.endpoint('app', self.home)
        with mock.patch.object(native, '_identity', return_value={**identity, 'birth': 124}), \
                mock.patch.object(native, '_listener') as listener:
            with self.assertRaises(ValueError):
                native.verify_endpoint({'identity': identity, 'address': 'http://127.0.0.1:12345'})
            listener.assert_not_called()

    def test_uploaded_images_and_files_remain_native_path_references(self):
        from bridge.clients.uploads import DesktopUploads
        uploads = DesktopUploads(self.directory)
        rows = [{'id': 'image', 'image': 'image/png', 'localPath': '/tmp/photo.png', 'name': 'photo.png'},
                {'id': 'file', 'image': None, 'localPath': '/tmp/report.txt', 'name': 'report.txt'}]
        with mock.patch.object(uploads.store, 'resolve', return_value=rows):
            images, files = uploads.resolve('antigravity', CID, ['image', 'file'], native_files=True)
        self.assertEqual(images, [])
        self.assertEqual(Antigravity._prompt({'text': 'inspect', 'resolvedFiles': files}),
                         'inspect\n\n@/tmp/photo.png\n@/tmp/report.txt')
        self.assertNotEqual(uploads.scope('claude', CID), uploads.scope('antigravity', CID))

    def test_hook_injection_requires_verified_session_and_native_idle(self):
        server = HookIPCServer(self.directory, session_resolver=lambda payload: 'scoped' if payload.get('verified') else None)
        self.addCleanup(server.close)
        queue = server.enqueue_follow_up('scoped', 'queued')
        server.enqueue_follow_up('scoped', 'steer', mode='steer')
        self.assertEqual(server.dispatch_hook_event('post_invocation', {}), {})
        self.assertEqual(len(server.get_queued_items('scoped')), 2)
        result = server.dispatch_hook_event('post_invocation', {'verified': True})
        self.assertEqual(result['injectSteps'], [{'userMessage': 'steer'}])
        self.assertEqual(server.get_queued_items('scoped')[0]['id'], queue['id'])
        self.assertEqual(server.dispatch_hook_event('post_invocation', {'verified': True}), {})
        self.assertEqual(server.dispatch_hook_event('stop', {'verified': True, 'terminationReason': 'model_stop'}), {})
        self.assertEqual(len(server.get_queued_items('scoped')), 1)
        result = server.dispatch_hook_event('stop', {'verified': True, 'terminationReason': 'model_stop', 'fullyIdle': True})
        self.assertEqual(result['decision'], 'continue')
        self.assertEqual(server.get_queued_items('scoped'), [])
        self.assertEqual(server.dispatch_hook_event('stop', {'verified': True, 'terminationReason': 'model_stop', 'fullyIdle': True}), {})

    def test_hook_commands_support_frozen_gateway_and_stay_isolated(self):
        from bridge.clients.antigravity.setup import build_hooks_manifest
        info = ensure_plugin(self.directory, self.home)
        self.assertFalse((self.home/'.gemini/config/hooks.json').exists())
        self.assertTrue(inspect_plugin(self.directory, self.home)['enabled'])
        self.assertFalse(disable_plugin(self.directory, self.home)['enabled'])
        with mock.patch('bridge.clients.antigravity.setup.sys.frozen', True, create=True):
            manifest = build_hooks_manifest(self.directory, python_executable='/Gateway App/gateway')
            command = manifest['codex-mobile-bridge']['SessionStart'][0]['command']
            self.assertIn('antigravity-hook', command)
            self.assertNotIn('hook_client.py', command)

    def test_manager_unknown_create_receipt_does_not_replay(self):
        _create_surface_db(self.root, [self.row])
        settings = self.directory/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        project = Path(self.temp.name)/'project'; project.mkdir()
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'start_cascade', side_effect=BridgeUnavailable('binding unknown')) as start:
            request = {'id': str(uuid.uuid4()), 'projectKey': 'app:'+str(project), 'firstMessage': 'first'}
            with mock.patch.object(adapter.store, 'discover_projects', return_value=[{'key': request['projectKey'], 'cwd': str(project), 'surface': 'app'}]):
                first = manager.call('antigravity', 'create', body=request)
                second = manager.call('antigravity', 'create', body=request)
            self.assertEqual(first['status'], 'unknown')
            self.assertEqual(first, second)
            self.assertEqual(start.call_count, 1)

    def test_notifications_relay_and_public_icon_use_shared_boundaries(self):
        from bridge.api.assets import STATIC
        from bridge.features.notifications.providers import desktop_provider
        from bridge.features.notifications.push import payload
        from relay.protocol import validate
        self.assertIn('/client-icons/antigravity.png', STATIC)
        self.assertEqual(desktop_provider('desktop:antigravity'), 'antigravity')
        event = {'host': 'desktop:antigravity', 'threadId': CID, 'kind': 'completion', 'sequence': 1}
        self.assertEqual(payload({'kind': 'apns', 'origin': 'https://gateway'}, event)['provider'], 'antigravity')
        for method, action in [('GET', 'list'), ('GET', 'detail'), ('GET', 'catalog'), ('POST', 'send'), ('POST', 'create'), ('POST', 'projects/browse'), ('POST', 'projects/register'), ('POST', 'projects/trust')]:
            validate(method, '/api/desktop-sessions/antigravity/'+action)
        with self.assertRaises(PermissionError):
            validate('POST', '/api/desktop-sessions/antigravity/install')
        with self.assertRaises(PermissionError):
            validate('GET', '/api/desktop-sessions/antigravity/projects/browse')

    def test_source_model_cache_and_settings_identity_stay_separate(self):
        _create_surface_db(self.root, [self.row])
        _create_surface_db(self.home/'.gemini/antigravity-ide', [self.row])
        settings = self.directory/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        app, ide = gateway_id('app', CID), gateway_id('ide', CID)
        with mock.patch.object(adapter, 'account_scope', return_value='native-account'), \
                mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'catalog_models', side_effect=lambda **kw: {
                    'models': [{'id': kw['surface'], 'name': kw['surface'], 'nativeModel': 'MODEL_NATIVE_'+kw['surface'].upper()}], 'userStatus': {}, 'notice': ''}):
            app_models = manager._catalog_models('antigravity', app, {})
            ide_models = manager._catalog_models('antigravity', ide, {})
            self.assertNotEqual(app_models['cache']['identity'], ide_models['cache']['identity'])
            self.assertEqual(app_models['models'][0]['id'], 'app')
            self.assertEqual(ide_models['models'][0]['id'], 'ide')
            creation = manager._desktop_model_directory('antigravity', 'ide')
            self.assertEqual(creation['cache']['identity'], ide_models['cache']['identity'])
            with mock.patch.object(adapter, 'call', wraps=adapter.call) as call:
                with self.assertRaises(ValueError):
                    manager.call('antigravity', 'settings', ide, {'id': str(uuid.uuid4()),
                        'model': 'app', 'modelCatalogIdentity': app_models['cache']['identity']})
                call.assert_not_called()
            result = manager.call('antigravity', 'settings', ide, {'id': str(uuid.uuid4()),
                'model': 'ide', 'modelCatalogIdentity': ide_models['cache']['identity']})
            self.assertEqual(result['status'], 'accepted')

    def test_source_directory_reuses_native_mapping_and_retains_it_on_failure(self):
        settings = self.directory/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        with mock.patch.object(adapter, 'account_scope', return_value='account-a') as identity, \
                mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'catalog_models', return_value={
                    'models': [{'id': 'full-high-id', 'nativeModel': 'MODEL_NATIVE'}],
                    'userStatus': {}, 'notice': ''}) as read:
            first = manager._desktop_model_directory('antigravity', 'app')
            self.assertEqual(adapter.cli._model('full-high-id', 'app'), 'MODEL_NATIVE')
            self.assertEqual(read.call_count, 1)
            read.side_effect = BridgeUnavailable('temporary catalog failure')
            failed = manager._desktop_model_directory('antigravity', 'app', refresh=True)
            self.assertEqual(failed['models'], first['models'])
            self.assertEqual(failed['cache']['updatedAt'], first['cache']['updatedAt'])
            self.assertEqual(failed['cache']['state'], 'stale')
            self.assertTrue(failed['capabilities']['manualModels'])
            with self.assertRaisesRegex(ValueError, 'MODEL_'):
                adapter.cli._model('unknown-display-model', 'app')
            identity.return_value = 'account-b'
            changed = manager._desktop_model_directory('antigravity', 'app')
            self.assertNotEqual(changed['cache']['identity'], first['cache']['identity'])
            self.assertEqual(changed['models'], [])
            self.assertEqual(changed['cache']['state'], 'unavailable')
            self.assertTrue(changed['capabilities']['manualModels'])

    def test_unknown_account_preserves_history_but_cannot_save_manual_models(self):
        _create_surface_db(self.root, [self.row])
        settings = self.directory/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        sid = gateway_id('app', CID)
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'user_status', side_effect=BridgeUnavailable('account offline')):
            detail = manager.call('antigravity', 'detail', sid, {})
            self.assertEqual(detail['session']['id'], sid)
            self.assertEqual(detail['modelCatalogIdentity'], '')
            with self.assertRaises(BridgeUnavailable):
                manager.call('antigravity', 'settings', sid, {'id': str(uuid.uuid4()), 'model': 'MODEL_EXPLICIT'})
            self.assertEqual(adapter._settings, {})
        with mock.patch.object(adapter.cli, 'catalog_models', return_value={'models': []}):
            adapter.cli.model_catalog = None
            for value in ('auto', '', 'unknown'):
                with self.assertRaises(ValueError):
                    adapter.cli._model(value, 'app')

    def test_manual_native_model_is_exact_for_start_and_send(self):
        self.home.mkdir(parents=True, exist_ok=True)
        cli = AntigravityCli(self.home, model_catalog=mock.Mock(side_effect=AssertionError('no guessed catalog')))
        with mock.patch.object(cli, 'call_ls_rpc', return_value={'cascadeId': CID,
                'projectEnvInfo': {'workspaceUris': [self.home.as_uri()]}}) as call:
            cli.start_cascade(surface='ide', model='MODEL_EXPLICIT_525', cwd=str(self.home))
            self.assertEqual(call.call_args.args[1]['requested_model'], 'MODEL_EXPLICIT_525')
            cli.send_user_message(CID, 'fixture', model='MODEL_EXPLICIT_525', surface='ide')
            self.assertEqual(call.call_args.args[1]['cascade_config']['planner_config']['requested_model'],
                             {'model': 'MODEL_EXPLICIT_525'})
            self.assertEqual(call.call_count, 2)

    def test_model_scope_requires_current_selected_source_account(self):
        adapter = self.adapter()
        with mock.patch.object(adapter.cli, 'user_status', side_effect=lambda surface: {'email': 'account-a'} if surface == 'app' else {}) as read:
            self.assertTrue(adapter.account_scope('app'))
            read.assert_called_once_with('app')
            with self.assertRaisesRegex(BridgeUnavailable, '登录'):
                adapter.account_scope('ide')

    def test_long_multibyte_hook_path_uses_token_authenticated_tcp(self):
        from bridge.clients.antigravity.hook_client import _send_and_recv
        from bridge.clients.antigravity.hook_ipc import _verify_peer_uid
        import socket
        base = self.directory.parent
        count = max(1, (100 - len(os.fsencode(base/'hook.sock')) + 2) // 3)
        state = base/('中'*count)
        self.assertLess(len(str(state/'hook.sock')), 100)
        self.assertGreaterEqual(len(os.fsencode(state/'hook.sock')), 100)
        server = HookIPCServer(state, session_resolver=lambda payload: 'scoped')
        self.addCleanup(server.close)
        endpoint = json.loads(server.endpoint_file.read_text())
        self.assertEqual(endpoint['transport'], 'tcp')
        self.assertEqual(endpoint['host'], '127.0.0.1')
        result = _send_and_recv(endpoint, {'token': endpoint['token'], 'event': 'session_start', 'payload': {}}, 1)
        self.assertEqual(result, {})
        self.assertTrue(server.has_recent_heartbeat('scoped'))
        tcp = mock.Mock(family=socket.AF_INET)
        self.assertTrue(_verify_peer_uid(tcp))
        tcp.getsockopt.assert_not_called()

    def test_ask_question_options_custom_skip_cancel_send_guard_and_fallback(self):
        cid = 'aaaa1111-2222-4333-8444-555555555555'
        other_cid = 'bbbb1111-2222-4333-8444-666666666666'
        traj_id = 'traj-ask-01'
        _create_surface_db(self.root, [
            (cid, 'Ask Session', '', 1, '2026-11-08T02:00:00Z', '[]', 'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', '', 0, 0, 0, ''),
            (other_cid, 'Other Session', '', 1, '2026-11-08T02:00:00Z', '[]', 'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', '', 0, 0, 0, ''),
        ])
        _create_surface_db(self.home / '.gemini/antigravity-ide', [
            (cid, 'IDE Session', '', 1, '2026-11-08T02:00:00Z', '[]', 'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', '', 0, 0, 0, ''),
        ])
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)

        ask_step = {
            'type': 'CORTEX_STEP_TYPE_ASK_USER_QUESTION',
            'status': 'CORTEX_STEP_STATUS_WAITING',
            'askQuestion': {
                'questions': [
                    {
                        'question': 'Deploy target?',
                        'isMultiSelect': False,
                        'options': [{'id': '1', 'text': 'Staging'}, {'id': '2', 'text': 'Production'}],
                    },
                    {
                        'question': 'Select checks',
                        'isMultiSelect': True,
                        'options': [{'id': 'opt-unit', 'text': 'Unit tests'}, {'id': 'opt-e2e', 'text': 'E2E tests'}],
                    },
                ],
            },
        }
        rpc_calls = []
        state = {
            'trajectories': {
                cid: {
                    'trajectoryId': traj_id,
                    'status': 'CASCADE_RUN_STATUS_WAITING_FOR_USER',
                    'waitingSteps': [{'stepIndex': 3, 'step': {**ask_step, 'requestedInteraction': {'askQuestion': ask_step['askQuestion']}}}],
                }
            },
            'traj_steps': {cid: [{}, {}, {}, ask_step]},
        }

        def fake_rpc(method, body=None, *, surface='app'):
            rpc_calls.append((surface, method, body))
            if method == 'GetUserStatus':
                return {'userStatus': {'email': 'dev@example.com'}} if surface == 'app' else {}
            if method == 'GetAllCascadeTrajectories':
                return {'trajectorySummaries': state['trajectories'] if surface == 'app' else {}}
            if method == 'GetCascadeTrajectory':
                return {'trajectory': {'steps': state['traj_steps'].get((body or {}).get('cascadeId'), [])}}
            if method == 'HandleCascadeUserInteraction':
                return {}
            raise AssertionError(f'Unexpected RPC: {method}')

        sid = gateway_id('app', cid)
        other_sid = gateway_id('app', other_cid)
        ide_sid = gateway_id('ide', cid)
        req_id = f'agy-wait:app:{cid}:{traj_id}:3'

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            listing = adapter.call('list')
            sess = next(s for s in listing['sessions'] if s['id'] == sid)
            self.assertEqual(sess['status'], 'active')
            self.assertEqual(len(sess['requests']), 1)

            detail = adapter.call('detail', sid)
            req = detail['requests'][0]
            self.assertEqual(req['id'], req_id)
            self.assertTrue(req['needsInput'])
            self.assertTrue(req['canCancel'])
            self.assertEqual(req['cancelLabel'], '取消')
            self.assertTrue(req['exclusiveWriteIn'])
            self.assertTrue(all(q['canSkip'] for q in req['input']['questions']))

            with self.assertRaises(BridgeUnavailable):
                adapter.call('send', sid, {'text': 'cannot bypass waiting question'})

            with self.assertRaises(ValueError):
                adapter.call('respond', other_sid, {'requestId': req_id, 'decision': 'decline'})
            with self.assertRaises(ValueError):
                adapter.call('respond', ide_sid, {'requestId': req_id, 'decision': 'decline'})

            adapter.call('respond', sid, {
                'requestId': req_id,
                'decision': 'accept',
                'answers': {
                    '0': {'answers': ['Staging', 'Canary cluster']},
                    '1': {'answers': ['Unit tests', 'extra lint']},
                },
            })
            _, last_method, last_body = rpc_calls[-1]
            self.assertEqual(last_method, 'HandleCascadeUserInteraction')
            ask_resp = last_body['interaction']['askQuestion']
            self.assertFalse(ask_resp['cancelled'])
            self.assertEqual(ask_resp['responses'][0]['writeInResponse'], 'Canary cluster')
            self.assertNotIn('selectedOptionIds', ask_resp['responses'][0])
            self.assertEqual(ask_resp['responses'][1]['selectedOptionIds'], ['opt-unit'])
            self.assertEqual(ask_resp['responses'][1]['writeInResponse'], 'extra lint')

            adapter.call('respond', sid, {
                'requestId': req_id,
                'decision': 'accept',
                'answers': {'0': {'answers': ['Production']}, '1': {'answers': [], 'skipped': True}},
            })
            resps = rpc_calls[-1][2]['interaction']['askQuestion']['responses']
            self.assertEqual(resps[0]['selectedOptionIds'], ['2'])
            self.assertTrue(resps[1]['skipped'])

            adapter.call('respond', sid, {'requestId': req_id, 'decision': 'decline'})
            self.assertEqual(rpc_calls[-1][2]['interaction']['askQuestion'], {'cancelled': True})

            with adapter.cli._lock:
                adapter.cli._waiting_cache.clear()
                adapter.cli._trajectories_cache.clear()
            state['trajectories'] = {}
            with self.assertRaisesRegex(ValueError, '已过期'):
                adapter.call('respond', sid, {
                    'requestId': req_id,
                    'decision': 'accept',
                    'answers': {'0': {'answers': ['Staging']}, '1': {'answers': ['Unit tests']}},
                })
            self.assertEqual(rpc_calls[-1][1], 'GetAllCascadeTrajectories')

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=BridgeUnavailable('transient')):
            degraded = adapter.call('detail', sid)
            self.assertEqual(degraded['requests'], [])

    def test_subagent_and_sibling_waiting_interactions(self):
        parent_cid = 'cccc1111-2222-4333-8444-555555555555'
        sub_cid = 'dddd1111-2222-4333-8444-666666666666'
        _create_surface_db(self.root, [
            (parent_cid, 'Parent Conversation', '', 1, '2026-11-08T02:00:00Z', '[]', 'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', '', 0, 0, 0, ''),
        ])
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)

        rpc_calls = []
        waiting_steps = [
            {
                'stepIndex': 1,
                'step': {
                    'type': 'CORTEX_STEP_TYPE_RUN_COMMAND',
                    'status': 'CORTEX_STEP_STATUS_WAITING',
                    'requestedInteraction': {'runCommand': {}},
                    'runCommand': {'proposedCommandLine': 'pytest -q', 'cwd': '/tmp'},
                },
            },
            {
                'stepIndex': 2,
                'step': {
                    'type': 'CORTEX_STEP_TYPE_CODE_ACTION',
                    'status': 'CORTEX_STEP_STATUS_WAITING',
                    'requestedInteraction': {'filePermission': {'absolutePathUri': 'file:///tmp/secret.txt', 'blockReason': 'Outside workspace'}},
                },
            },
            {
                'stepIndex': 3,
                'step': {
                    'type': 'CORTEX_STEP_TYPE_MCP_TOOL',
                    'status': 'CORTEX_STEP_STATUS_WAITING',
                    'requestedInteraction': {
                        'elicitation': {
                            'serverName': 'linear',
                            'message': 'Provide filters',
                            'requestedSchemaJson': json.dumps({
                                'type': 'object',
                                'required': ['count'],
                                'properties': {
                                    'count': {'type': 'integer', 'title': 'Count'},
                                    'enabled': {'type': 'boolean', 'title': 'Enabled'},
                                },
                            }),
                        }
                    },
                },
            },
        ]

        def fake_rpc(method, body=None, *, surface='app'):
            rpc_calls.append((surface, method, body))
            if method == 'GetUserStatus':
                return {'userStatus': {'email': 'dev@example.com'}} if surface == 'app' else {}
            if method == 'GetAllCascadeTrajectories':
                return {
                    'trajectorySummaries': {
                        parent_cid: {'trajectoryId': 'traj-parent', 'waitingSteps': waiting_steps},
                        sub_cid: {
                            'trajectoryId': 'traj-sub',
                            'status': 'CASCADE_RUN_STATUS_WAITING_FOR_USER',
                            'trajectoryMetadata': {'rootConversationId': parent_cid, 'parentConversationId': parent_cid},
                            'waitingSteps': [{'stepIndex': 5, 'step': {
                                'type': 'CORTEX_STEP_TYPE_ASK_USER_QUESTION',
                                'status': 'CORTEX_STEP_STATUS_WAITING',
                                'requestedInteraction': {'askQuestion': {'questions': [{'question': 'Proceed?', 'options': [{'id': 'y', 'text': 'Yes'}]}]}},
                            }}],
                        },
                    }
                }
            if method == 'HandleCascadeUserInteraction':
                return {}
            raise AssertionError(f'Unexpected RPC: {method}')

        parent_sid = gateway_id('app', parent_cid)
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            detail = adapter.call('detail', parent_sid)
            reqs = {r['id']: r for r in detail['requests']}
            sub_req = reqs[f'agy-wait:app:{sub_cid}:traj-sub:5']
            self.assertTrue(sub_req['tool'].startswith('子智能体 · '))

            adapter.call('respond', parent_sid, {'requestId': sub_req['id'], 'decision': 'accept', 'answers': {'0': {'answers': ['Yes']}}})
            self.assertEqual(rpc_calls[-1][2]['cascadeId'], sub_cid)

            adapter.call('respond', parent_sid, {'requestId': f'agy-wait:app:{parent_cid}:traj-parent:1', 'decision': 'accept'})
            self.assertEqual(rpc_calls[-1][2]['interaction']['runCommand'], {'confirm': True, 'proposedCommandLine': 'pytest -q', 'submittedCommandLine': 'pytest -q'})

            adapter.call('respond', parent_sid, {'requestId': f'agy-wait:app:{parent_cid}:traj-parent:2', 'decision': 'accept_conversation'})
            self.assertEqual(rpc_calls[-1][2]['interaction']['filePermission']['scope'], 'PERMISSION_SCOPE_CONVERSATION')

            adapter.call('respond', parent_sid, {'requestId': f'agy-wait:app:{parent_cid}:traj-parent:3', 'decision': 'accept', 'answers': {'count': {'answers': ['5']}, 'enabled': {'answers': ['true']}}})
            self.assertEqual(json.loads(rpc_calls[-1][2]['interaction']['elicitation']['contentJson']), {'count': 5, 'enabled': True})

        server = HookIPCServer(self.directory, session_resolver=lambda _: parent_sid)
        self.addCleanup(server.close)
        server.set_permission_mode(parent_sid, 'mobile-ask')
        t = threading.Thread(target=lambda: server.dispatch_hook_event('pre_tool_use', {'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'echo hi'}}}), daemon=True)
        t.start()
        deadline = time.monotonic() + 2.0
        while not server.pending_approvals_for(parent_sid) and time.monotonic() < deadline:
            time.sleep(0.01)
        pending = server.pending_approvals_for(parent_sid)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['tool'], 'run_command')
        self.assertEqual(pending[0]['decisions'][0]['id'], 'accept')
        server.resolve_approval(parent_sid, pending[0]['id'], 'decline')
        t.join(timeout=2.0)

    def test_catalog_filters_disabled_exposes_real_quota_and_context_usage(self):
        adapter = self.adapter()
        status = {
            'userTier': {'name': 'Google AI Pro'},
            'planStatus': {'availablePromptCredits': 120, 'availableFlowCredits': 45},
            'cascadeModelConfigData': {
                'clientModelConfigs': [
                    {'modelId': 'm-good', 'label': 'Good', 'modelOrAlias': {'model': 'GOOD_ENUM'},
                     'quotaInfo': {'remainingFraction': 1.0, 'resetTime': '2026-11-14T12:30:00Z'}},
                    {'modelId': 'm-low', 'label': 'Low', 'modelOrAlias': {'model': 'LOW_ENUM'},
                     'quotaInfo': {'remainingFraction': 0.15, 'resetTime': '2026-11-14T12:30:00Z'}},
                    {'modelId': 'm-empty', 'label': 'Empty', 'modelOrAlias': {'model': 'EMPTY_ENUM'},
                     'quotaInfo': {'remainingFraction': 0.0, 'resetTime': '2026-11-14T12:30:00Z'}},
                    {'modelId': 'm-dis-flag', 'label': 'Flag', 'disabled': True, 'modelOrAlias': {'model': 'FLAG_ENUM'}},
                    {'modelId': 'm-unavail', 'label': 'Unavail', 'tagTitle': 'Unavailable', 'modelOrAlias': {'model': 'UNAV_ENUM'}},
                    {'modelId': 'm-legacy-disabled', 'label': 'Legacy', 'modelOrAlias': {'model': 'LEG_ENUM'}},
                ]
            },
        }
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'user_status', return_value=status):
            catalog = adapter.cli.catalog_models(surface='app')
            self.assertEqual([m['id'] for m in catalog['models']], ['m-good', 'm-low', 'm-empty'])
            self.assertNotIn('usage', catalog)
            by_id = {m['id']: m for m in catalog['models']}
            self.assertEqual(by_id['m-good']['quotaTag'], '剩余 100%')
            self.assertEqual(by_id['m-good']['quotaStatus'], 'good')
            self.assertNotIn('resetNote', by_id['m-good'])
            self.assertEqual(by_id['m-low']['quotaTag'], '剩余 15.0%')
            self.assertEqual(by_id['m-low']['quotaStatus'], 'warning')
            self.assertEqual(by_id['m-low']['resetNote'], adapter.cli._format_reset_note('2026-11-14T12:30:00Z'))
            self.assertEqual(by_id['m-empty']['quotaTag'], '已用尽')
            self.assertEqual(by_id['m-empty']['quotaStatus'], 'exhausted')
            summary = catalog['usageSummary']
            self.assertEqual(summary['tier'], 'Google AI Pro')
            self.assertEqual(len(summary['items']), 3)
            self.assertEqual(summary['credits'], '可用 Prompt 点数: 120 · Flow 点数: 45')
            account = adapter.call('account', body={'surface': 'app'})
            self.assertEqual(account['usageSummary'], summary)
            for blocked in ('m-dis-flag', 'm-unavail', 'm-legacy-disabled'):
                with self.assertRaises(ValueError):
                    adapter.cli._model(blocked, 'app')

        bare_status = {'cascadeModelConfigData': {'clientModelConfigs': [
            {'modelId': 'm-plain', 'label': 'Plain', 'modelOrAlias': {'model': 'PLAIN_ENUM'}}]}}
        with mock.patch.object(adapter.cli, 'user_status', return_value=bare_status):
            bare = adapter.cli.catalog_models(surface='app')
            self.assertNotIn('usageSummary', bare)
            self.assertEqual(bare['notice'], '')

        _write_transcript(self.root, CID, [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'hi', 'created_at': '2026-11-14T00:00:00Z'},
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'content': 'done',
             'input_tokens': 50, 'cache_read_tokens': 100, 'cache_write_tokens': 25, 'output_tokens': 12,
             'created_at': '2026-11-14T00:00:01Z'},
        ])
        with mock.patch.object(adapter.cli, 'available', return_value=False):
            detail = adapter.call('detail', gateway_id('app', CID))
        ctx = detail['session']['contextUsage']
        self.assertEqual(ctx['usedTokens'], 150)
        self.assertIsNone(ctx['contextWindow'])
        self.assertEqual(ctx['outputTokens'], 12)
        self.assertEqual(ctx['cache'], {'inputTokens': 50, 'cacheReadTokens': 100, 'cacheWriteTokens': 25})

    def test_model_normalization_rewound_hydration_and_read_image_safety(self):
        adapter = self.adapter()
        self.assertEqual(
            format_tool_call({'name': 'write_to_file', 'args': {'TargetFile': '/tmp/a.py', 'CodeContent': 'print(1)'}}),
            '写入文件：/tmp/a.py\nprint(1)',
        )
        session_dir = self.root / 'brain' / CID
        session_dir.mkdir(parents=True, exist_ok=True)
        img_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
        old_img = session_dir / 'old.png'
        new_img = session_dir / 'new.png'
        full_img = session_dir / 'full.png'
        old_img.write_bytes(img_bytes + b'old')
        new_img.write_bytes(img_bytes + b'new')
        full_img.write_bytes(img_bytes + b'full')
        sym_leaf = session_dir / 'sym.png'
        sym_leaf.symlink_to(new_img)
        real_sub = session_dir / 'real_sub'
        real_sub.mkdir()
        (real_sub / 'target.png').write_bytes(img_bytes)
        sym_dir = session_dir / 'sym_dir'
        sym_dir.symlink_to(real_sub)

        compact = [
            {'step_index': 0, 'type': 'USER_INPUT', 'content': 'go', 'created_at': '2026-11-14T00:00:00Z'},
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'status': 'FAILED', 'error': {'message': 'quota hit'},
             'tool_calls': [{'id': 'c1', 'name': 'run_command', 'args': {'CommandLine': 'shot'}}],
             'created_at': '2026-11-14T00:00:01Z'},
            {'step_index': 2, 'type': 'TOOL_RESPONSE', 'status': 'DONE', 'content': 'old short',
             'truncated_fields': ['content'], 'media': [{'mime_type': 'image/png', 'uri': str(old_img)}],
             'created_at': '2026-11-14T00:00:02Z'},
            # Rewind back to step 2
            {'step_index': 2, 'type': 'TOOL_RESPONSE', 'status': 'DONE', 'content': 'new short',
             'truncated_fields': ['content'], 'media': [{'mime_type': 'image/png', 'uri': str(new_img)}],
             'created_at': '2026-11-14T00:00:03Z'},
            {'step_index': 3, 'type': 'TOOL_RESPONSE', 'status': 'FAILED', 'error_message': 'tool err',
             'truncated_fields': ['media'], 'created_at': '2026-11-14T00:00:04Z'},
            {'step_index': 4, 'type': 'ERROR_MESSAGE', 'error': 'fatal step', 'created_at': '2026-11-14T00:00:05Z'},
            {'step_index': 5, 'type': 'CHECKPOINT', 'content': 'saved snapshot', 'created_at': '2026-11-14T00:00:06Z'},
            {'step_index': 6, 'type': 'USER_INPUT', 'content': 'symlink checks', 'created_at': '2026-11-14T00:00:07Z',
             'media': [
                 {'mime_type': 'image/png', 'uri': str(sym_leaf)},
                 {'mime_type': 'image/png', 'uri': str(sym_dir / 'target.png')},
                 {'mime_type': 'image/png', 'uri': str(session_dir / 'real_sub' / '..' / 'new.png')},
             ]},
        ]
        full = [
            compact[0],
            compact[1],
            {**compact[2], 'content': 'Old hydrated content'},
            {**compact[3], 'content': 'New hydrated content'},
            {**compact[4], 'media': [{'mime_type': 'image/png', 'uri': str(full_img)}]},
            compact[5],
            compact[6],
            compact[7],
        ]
        _write_transcript(self.root, CID, compact, full)
        page = adapter.store.read_transcript_page(self.root, CID, limit=40)
        texts = [m['text'] for m in page['messages']]
        self.assertIn('quota hit', texts)
        self.assertTrue(any('New hydrated content' in t for t in texts))
        self.assertFalse(any('Old hydrated content' in t for t in texts))
        self.assertIn('fatal step', texts)
        self.assertIn('saved snapshot', texts)
        self.assertTrue(any(m.get('attachments') and m['attachments'][0].get('type') == 'image' for m in page['messages']))
        self.assertTrue(any('tool err' in t for t in texts))

        self.assertEqual(base64.b64decode(adapter.store.read_image(self.root, CID, {'stepIndex': 2, 'mediaIndex': 0})['data']), img_bytes + b'new')
        self.assertEqual(base64.b64decode(adapter.store.read_image(self.root, CID, {'stepIndex': 3, 'mediaIndex': 0})['data']), img_bytes + b'full')
        for media_idx in (0, 1, 2):
            with self.assertRaises(ValueError):
                adapter.store.read_image(self.root, CID, {'stepIndex': 6, 'mediaIndex': media_idx})

    def test_task_read_only_boundary_and_hook_queue_reliability(self):
        from relay.protocol import validate as validate_relay
        with self.assertRaises(PermissionError):
            validate_relay('POST', f'/api/desktop-sessions/antigravity/stop-task?sessionId={gateway_id("app", CID)}', 10)
        self.assertTrue(re.fullmatch(PRE_TOOL_MATCHER, 'call_mcp_tool'))
        self.assertTrue(re.fullmatch(PRE_TOOL_MATCHER, 'send_command_input'))
        self.assertTrue(re.fullmatch(PRE_TOOL_MATCHER, 'browser_click'))
        self.assertTrue(re.fullmatch(PRE_TOOL_MATCHER, 'computer_act'))
        self.assertTrue(re.fullmatch(PRE_TOOL_MATCHER, 'script_run'))

        child_run = '22222222-3333-4444-8555-666666666666'
        child_done = '33333333-4444-4555-8666-777777777777'
        unrelated = '44444444-5555-4666-8777-888888888888'
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'conversation_summaries.db').unlink(missing_ok=True)
        _create_surface_db(self.root, [
            self.row,
            (child_run, 'Child Running', '', 1, '2026-11-14T00:00:01Z', '[]', 'CASCADE_RUN_STATUS_RUNNING',
             'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', CID, 1, 1, 0, 'Worker'),
            (child_done, 'Child Done', '', 1, '2026-11-14T00:00:00Z', '[]', 'CASCADE_RUN_STATUS_IDLE',
             'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', CID, 1, 0, 0, 'Worker'),
            (unrelated, 'Unrelated', '', 1, '2026-11-14T00:00:01Z', '[]', 'CASCADE_RUN_STATUS_RUNNING',
             'CORTEX_TRAJECTORY_SOURCE_CONVERSATION', '', '', 0, 1, 0, 'Other'),
        ])
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        tasks = adapter.store.list_tasks(self.root, CID)
        self.assertEqual([(t['id'], t['cancellable']) for t in tasks], [(child_run, False), (child_done, False)])
        sid = gateway_id('app', CID)
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'stop') as stop_mock:
            with self.assertRaises(ValueError):
                adapter.call('stop-task', sid, {'taskId': unrelated})
            with self.assertRaises(ValueError):
                adapter.call('stop-task', sid, {'taskId': child_done})
            adapter.hooks = mock.MagicMock()
            adapter.hooks.permission_mode.return_value = 'desktop'
            with self.assertRaises(ValueError):
                adapter.call('stop-task', sid, {'taskId': child_run})
            stop_mock.assert_not_called()
            adapter.hooks.request_stop.assert_not_called()

        resolved_count = 0
        def resolve_once(payload):
            nonlocal resolved_count
            resolved_count += 1
            return 'scoped-cid' if payload.get('verified') else None

        server = HookIPCServer(self.directory, session_resolver=resolve_once)
        self.addCleanup(server.close)
        q_steer = server.enqueue_follow_up('scoped-cid', 'steer 1', mode='steer')
        q_queue = server.enqueue_follow_up('scoped-cid', 'queue 2', mode='queue')
        client_sock, server_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(client_sock.close)
        _write_frame(client_sock, {'token': server.token, 'event': 'post_invocation', 'payload': {'verified': True}})
        client_sock.shutdown(socket.SHUT_WR)
        with mock.patch('bridge.clients.antigravity.hook_ipc._write_frame', side_effect=OSError('broken pipe')):
            server._handle_client(server_sock)
        self.assertEqual(resolved_count, 1)
        restored = server.get_queued_items('scoped-cid')
        self.assertEqual([item['id'] for item in restored], [q_queue['id']])
        reloaded = json.loads(server.queues_file.read_text(encoding='utf-8'))
        self.assertEqual([item['id'] for item in reloaded['scoped-cid']], [q_queue['id']])


