import base64
import json
import os
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
from bridge.clients.antigravity.hook_ipc import HookIPCServer
from bridge.clients.antigravity.setup import disable_plugin, ensure_plugin, inspect_plugin
from bridge.clients.antigravity.store import AntigravityStore, parse_timestamp, local_file_path
from bridge.clients.antigravity.cli_session import AntigravityCli
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.antigravity.model import gateway_id, normalize_steps
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
                    'models': [{'id': kw['surface'], 'name': kw['surface']}], 'userStatus': {}, 'notice': ''}):
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
                    'models': [{'id': kw['surface'], 'name': kw['surface']}], 'userStatus': {}, 'notice': ''}):
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

    def test_long_multibyte_hook_path_uses_token_authenticated_tcp(self):
        from bridge.clients.antigravity.hook_client import _send_and_recv
        from bridge.clients.antigravity.hook_ipc import _verify_peer_uid
        import socket
        state = self.directory/('中文'*6)
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
