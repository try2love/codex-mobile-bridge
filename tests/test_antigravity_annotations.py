import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.adapter import Antigravity
from bridge.clients.antigravity.model import gateway_id
from bridge.clients.antigravity.store import AntigravityStore, read_annotation_pbtxt
from bridge.clients.errors import BridgeUnavailable, ResumePreflightBlocked
from bridge.clients.manager import DesktopSessions
from bridge.features.sessions.create import ArchiveNotSent

ROOT = Path(__file__).resolve().parents[1]
CID = '11111111-2222-4333-8444-555555555555'


def _create_db(surface_root, rows, *, fts_archived=()):
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
        if fts_archived:
            conn.execute('CREATE TABLE conversations_fts (conversation_id TEXT, archived INTEGER)')
            conn.executemany(
                'INSERT INTO conversations_fts VALUES (?, ?)',
                [(cid, 1) for cid in fts_archived],
            )
        conn.commit()
    finally:
        conn.close()


class AntigravityAnnotationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.directory = Path(self.temp.name) / 'bridge'
        self.root = self.home / '.gemini' / 'antigravity'
        self.workspace = Path(self.temp.name) / 'project'
        self.workspace.mkdir(parents=True)
        self.row = (
            CID, 'Original Title', 'hello', 2, '2026-11-19T10:00:00Z',
            json.dumps([self.workspace.resolve().as_uri()]),
            'CASCADE_RUN_STATUS_IDLE', 'CORTEX_TRAJECTORY_SOURCE_CONVERSATION',
            '', '', 0, 0, 0, '',
        )
        _create_db(self.root, [self.row])

    def test_pbtxt_parser_handles_octal_utf8_escapes_nested_blocks_and_symlinks(self):
        ann_dir = self.root / 'annotations'
        ann_dir.mkdir(parents=True, exist_ok=True)
        # Octal-escaped UTF-8 for "中文" (\344\270\255\346\226\207) plus escaped quotes and nested block
        pbtxt = (
            'title: "\\344\\270\\255\\346\\226\\207 \\"Quote\\""\n'
            'archived: true\n'
            'last_user_view_time {\n'
            '  seconds: 1795000000\n'
            '  archived: false\n'
            '}\n'
        )
        (ann_dir / f'{CID}.pbtxt').write_text(pbtxt, encoding='utf-8')
        parsed = read_annotation_pbtxt(self.root, CID)
        self.assertEqual(parsed, {'title': '中文 "Quote"', 'archived': True})

        other = '22222222-3333-4444-8555-666666666666'
        target = Path(self.temp.name) / 'outside.pbtxt'
        target.write_text('title: "Leak"\narchived: true\n', encoding='utf-8')
        link = ann_dir / f'{other}.pbtxt'
        link.symlink_to(target)
        self.assertEqual(read_annotation_pbtxt(self.root, other), {})

    def test_store_reads_archived_from_pbtxt_and_fts_fallback(self):
        cid_fts = '22222222-3333-4444-8555-666666666666'
        cid_override = '33333333-4444-4555-8666-777777777777'
        (self.root / 'conversation_summaries.db').unlink()
        _create_db(
            self.root,
            [
                self.row,
                (cid_fts, 'FTS Archived', '', 1, '2026-11-19T09:00:00Z', '[]', 'CASCADE_RUN_STATUS_IDLE', '', '', '', 0, 0, 0, ''),
                (cid_override, 'Unarchived Override', '', 1, '2026-11-19T08:00:00Z', '[]', 'CASCADE_RUN_STATUS_IDLE', '', '', '', 0, 0, 0, ''),
            ],
            fts_archived=(cid_fts, cid_override),
        )
        ann_dir = self.root / 'annotations'
        ann_dir.mkdir(parents=True, exist_ok=True)
        (ann_dir / f'{CID}.pbtxt').write_text('title: "Annotated Name"\narchived: true\n', encoding='utf-8')
        (ann_dir / f'{cid_override}.pbtxt').write_text('archived: false\n', encoding='utf-8')

        store = AntigravityStore(self.directory / 'scratch', home=self.home)
        by_cid = {row['nativeId']: row for row in store.list_sessions()}
        self.assertEqual(by_cid[CID]['title'], 'Annotated Name')
        self.assertTrue(by_cid[CID]['archived'])
        self.assertTrue(by_cid[cid_fts]['archived'])
        self.assertFalse(by_cid[cid_override]['archived'])

    def test_offline_capabilities_disable_rename_and_archive_writes(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        with mock.patch.object(adapter.cli, 'available', return_value=False):
            listed = adapter.call('list')
            caps = listed['sessions'][0]['capabilities']
            self.assertFalse(caps['rename'])
            self.assertFalse(caps['archive'])
            self.assertFalse(caps['unarchive'])
            with self.assertRaises(ResumePreflightBlocked):
                adapter.call('rename', sid, {'title': 'New Name'})
            with self.assertRaises(ArchiveNotSent):
                adapter.call('archive', sid, {'archived': True})

    def test_manager_rename_archive_unarchive_and_disconnect_flow(self):
        settings = self.directory / 'desktop-sessions' / 'settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'antigravityHome': str(self.home), 'enabled': {'antigravity': True}}))
        manager = DesktopSessions(self.directory)
        self.addCleanup(manager.close)
        adapter = manager.adapters['antigravity']
        sid = gateway_id('app', CID)

        state = {'title': 'Original Title', 'archived': False, 'status': 'CASCADE_RUN_STATUS_IDLE'}
        rpc_calls = []

        def fake_rpc(method, payload=None, *, surface='app'):
            rpc_calls.append((method, payload, surface))
            if method == 'GetAllCascadeTrajectories':
                return {
                    'trajectorySummaries': {
                        CID: {
                            'status': state['status'],
                            'annotations': {'title': state['title'], 'archived': state['archived']},
                        }
                    }
                }
            if method == 'UpdateConversationAnnotations':
                ann = (payload or {}).get('annotations') or {}
                if 'title' in ann:
                    state['title'] = ann['title']
                if 'archived' in ann:
                    state['archived'] = ann['archived']
                return {}
            if method in ('CancelCascadeInvocation', 'ForceStopCascadeTree'):
                state['status'] = 'CASCADE_RUN_STATUS_IDLE'
                return {}
            return {}

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            # 1. Native rename through DesktopSessions / RenameCommands
            rename_id = str(uuid.uuid4())
            renamed = manager.call('antigravity', 'rename', sid, {'id': rename_id, 'title': 'Renamed Chat'})
            self.assertEqual(renamed['status'], 'confirmed')
            self.assertEqual(renamed['title'], 'Renamed Chat')
            self.assertEqual(state['title'], 'Renamed Chat')
            # Idempotent replay returns confirmed without re-calling RPC
            before_count = len(rpc_calls)
            replayed = manager.call('antigravity', 'rename', sid, {'id': rename_id, 'title': 'Renamed Chat'})
            self.assertEqual(replayed['status'], 'confirmed')
            self.assertFalse(any(m == 'UpdateConversationAnnotations' for m, _, _ in rpc_calls[before_count:]))

            # 2. Native archive through DesktopSessions / ArchiveCommands
            arch_id = str(uuid.uuid4())
            archived = manager.call('antigravity', 'archive', sid, {'id': arch_id, 'archived': True})
            self.assertEqual(archived['status'], 'confirmed')
            self.assertTrue(archived['archived'])
            self.assertTrue(state['archived'])

            # 3. Native unarchive preserves renamed title
            unarch_id = str(uuid.uuid4())
            unarchived = manager.call('antigravity', 'archive', sid, {'id': unarch_id, 'archived': False})
            self.assertEqual(unarchived['status'], 'confirmed')
            self.assertFalse(unarchived['archived'])
            self.assertEqual(state['title'], 'Renamed Chat')

            # 4. Active session rejects archive without disconnect, succeeds with disconnect=True (stops first)
            state['status'] = 'CASCADE_RUN_STATUS_RUNNING'
            with self.assertRaises(ValueError):
                manager.call('antigravity', 'archive', sid, {'id': str(uuid.uuid4()), 'archived': True})
            rpc_calls.clear()
            disc = manager.call('antigravity', 'archive', sid, {'id': str(uuid.uuid4()), 'archived': True, 'disconnect': True})
            self.assertEqual(disc['status'], 'confirmed')
            self.assertTrue(state['archived'])
            methods = [m for m, _, _ in rpc_calls]
            self.assertIn('ForceStopCascadeTree', methods)
            self.assertIn('UpdateConversationAnnotations', methods)
            self.assertLess(methods.index('ForceStopCascadeTree'), methods.index('UpdateConversationAnnotations'))

    def test_rename_readback_mismatch_raises_bridge_unavailable(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=lambda m, p=None, *, surface='app': (
                    {'trajectorySummaries': {CID: {'annotations': {'title': 'Still Old'}}}}
                    if m == 'GetAllCascadeTrajectories' else {}
                )):
            with self.assertRaises(BridgeUnavailable):
                adapter.call('rename', sid, {'title': 'Expected New'})

    def test_rename_falls_back_to_pbtxt_when_older_session_not_in_live_trajectories_map(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        sid = gateway_id('app', CID)
        ann_dir = self.root / 'annotations'
        ann_dir.mkdir(parents=True, exist_ok=True)

        def fake_rpc(method, payload=None, *, surface='app'):
            if method == 'UpdateConversationAnnotations':
                (ann_dir / f'{CID}.pbtxt').write_text('title: "Older Chat Renamed"\n', encoding='utf-8')
                return {}
            if method == 'GetAllCascadeTrajectories':
                return {'trajectorySummaries': {}}
            return {}

        with mock.patch.object(adapter.cli, 'available', return_value=True), \
                mock.patch.object(adapter.cli, 'call_ls_rpc', side_effect=fake_rpc):
            result = adapter.call('rename', sid, {'title': 'Older Chat Renamed'})
        self.assertEqual(result, {'status': 'confirmed', 'result': {'id': sid, 'title': 'Older Chat Renamed'}})

    def test_create_supports_initial_title_and_exposes_create_title(self):
        adapter = Antigravity(self.directory, home=self.home)
        self.addCleanup(adapter.close)
        envs = adapter.project_environments()
        self.assertTrue(all(env['createTitle'] is True for env in envs))
        new_cid = '99999999-8888-4777-8666-555555555555'
        calls = []
        with mock.patch.object(adapter.cli, 'start_cascade', return_value=new_cid), \
                mock.patch.object(adapter.cli, 'update_annotations', side_effect=lambda *a, **kw: calls.append(('ann', a, kw))), \
                mock.patch.object(adapter.cli, 'send_user_message', side_effect=lambda *a, **kw: calls.append(('send', a, kw))):
            result = adapter.call('create', body={
                'directoryProject': {'surface': 'app', 'cwd': str(self.workspace)},
                'firstMessage': 'hello world',
                'title': '  Sprint Plan  ',
            })
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(calls[0][0], 'ann')
        self.assertEqual(calls[0][1], (new_cid,))
        self.assertEqual(calls[0][2], {'title': 'Sprint Plan', 'surface': 'app'})
        self.assertEqual(calls[1][0], 'send')

        with mock.patch.object(adapter.cli, 'start_cascade') as start_mock:
            with self.assertRaises(ValueError):
                adapter.call('create', body={
                    'directoryProject': {'surface': 'app', 'cwd': str(self.workspace)},
                    'firstMessage': 'hello world',
                    'title': 'bad\ntitle',
                })
            start_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
