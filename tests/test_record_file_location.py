from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, skipIf
from unittest.mock import AsyncMock, Mock, patch
import asyncio
import os
import tempfile

from bridge.clients.claude.adapter import Claude, metadata_record_path
from bridge.clients.claude.model import session
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions
from bridge.clients.antigravity.adapter import Antigravity

ROOT = Path(__file__).resolve().parents[1]


class RecordFileLocationTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def make_record(self, relative):
        path = self.root/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')
        return path

    def test_claude_metadata_requires_one_real_file_and_never_reads_its_contents(self):
        code = self.make_record('claude-code-sessions/account/org/native.json')
        cowork = self.make_record('local-agent-mode-sessions/account/org/agent/native.json')
        with patch.object(Path, 'read_text', side_effect=AssertionError('No transcript read')):
            self.assertEqual(metadata_record_path(str(self.root), 'code', 'native'), str(code))
            self.assertEqual(metadata_record_path(str(self.root), 'cowork', 'native'), str(cowork))
            self.assertIsNone(metadata_record_path(str(self.root), 'code', '../native'))
        self.make_record('claude-code-sessions/other/org/native.json')
        self.assertIsNone(metadata_record_path(str(self.root), 'code', 'native'))
        self.assertIsNone(metadata_record_path(str(self.root), 'code', 'missing'))

    @skipIf(os.name == 'nt', 'Symlink creation requires a Windows capability')
    def test_claude_record_scan_ignores_linked_account_directories(self):
        record = self.make_record('outside/org/native.json')
        base = self.root/'claude-code-sessions'; base.mkdir()
        (base/'account').symlink_to(record.parent.parent, target_is_directory=True)
        self.assertIsNone(metadata_record_path(str(self.root), 'code', 'native'))

    def test_claude_record_location_rechecks_native_identity_before_searching(self):
        record = self.make_record('claude-code-sessions/account/org/native.json')
        row = session('code', {'sessionId': 'native'})
        adapter = SimpleNamespace(index={row['id']: row}, status=lambda: {'connected': True},
                                  discovery={'dataHome': str(self.root)},
                                  desktop=SimpleNamespace(call=AsyncMock(return_value={'sessionId':'native'})))
        result = asyncio.run(Claude.dispatch(adapter, 'record-location', row['id'], {}))
        self.assertEqual(result, {'path':str(record)})
        adapter.desktop.call.assert_awaited_once_with('code','getSession','native')
        adapter.desktop.call.return_value = {'sessionId':'another'}
        with patch('bridge.clients.claude.adapter.metadata_record_path') as locate, self.assertRaises(BridgeUnavailable):
            asyncio.run(Claude.dispatch(adapter, 'record-location', row['id'], {}))
        locate.assert_not_called()

    def test_antigravity_keeps_the_selected_surface_and_real_summary_fallback(self):
        source = self.root/'agy-ide'; source.mkdir()
        summary = source/'conversation_summaries.db'; summary.write_bytes(b'fixture')
        store = Mock(); store.resolve_session.return_value = {'surfaceRoot': str(source), 'nativeId': 'native'}
        adapter = SimpleNamespace(store=store)
        self.assertEqual(Antigravity.record_path(adapter, 'gateway-id'), str(summary))
        log = source/'brain/native/.system_generated/logs/transcript.jsonl'; log.parent.mkdir(parents=True); log.write_text('{}')
        self.assertEqual(Antigravity.record_path(adapter, 'gateway-id'), str(log))
        store.resolve_session.assert_called_with('gateway-id')

    def test_host_browse_falls_back_without_granting_a_project_or_changing_cwd(self):
        manager = DesktopSessions(self.root/'gateway'); self.addCleanup(manager.close)
        record = self.make_record('records/native.json')
        manager.config['enabled'] = {'deepseek': True, 'claude': True}
        for provider, backend, cwd in [('deepseek','deepseek',''), ('claude','cowork','/virtual-machine/only')]:
            adapter = Mock(); adapter.call.return_value = {'sessions': [{'id': 'native', 'cwd': cwd, 'backend': backend, 'host': 'local'}]}
            adapter.record_path.return_value = str(record); manager.adapters[provider] = adapter
            bridge = manager.workspace_bridge(provider, 'native', host_files=True)
            listing = bridge.workspace('native', 'list', {'scope': 'host'})
            self.assertEqual(listing['path'], record.parent.as_posix()); self.assertIsNone(listing['projectPath'])
            self.assertEqual(manager.workspace_roots[(provider,'native')][1]['cwd'], cwd)
            manager.workspace_bridge(provider, 'native', host_files=True)
            adapter.record_path.assert_called_once_with('native')
            with self.assertRaises((ValueError, OSError)): manager.workspace_bridge(provider, 'native')
            with self.assertRaises(ValueError): bridge.workspace('native', 'upload', {'path':'new.txt','data':''})

    def test_remote_client_paths_are_never_mapped_to_gateway_files(self):
        manager = DesktopSessions(self.root/'gateway'); self.addCleanup(manager.close)
        manager.config['enabled'] = {'claude': True}
        adapter = Mock(); adapter.call.return_value = {'sessions': [{'id':'native','cwd':str(self.root),'host':'ssh:fixture'}]}; manager.adapters['claude'] = adapter
        with self.assertRaisesRegex(ValueError, '宿主机文件浏览'):
            manager.workspace_bridge('claude','native',host_files=True)
        adapter.record_path.assert_not_called()

    def test_explicit_host_directory_does_not_wait_for_an_unavailable_record_location(self):
        manager = DesktopSessions(self.root/'gateway'); self.addCleanup(manager.close)
        manager.config['enabled'] = {'deepseek': True}
        adapter = Mock(); adapter.call.return_value = {'sessions': [{'id':'native','cwd':'','host':'local'}]}
        adapter.record_path.side_effect = PermissionError('Record unavailable'); manager.adapters['deepseek'] = adapter
        bridge = manager.workspace_bridge('deepseek','native',host_files=True,path=str(self.root))
        result = bridge.workspace('native','list',{'scope':'host','path':str(self.root)})
        self.assertEqual(result['path'], self.root.as_posix()); self.assertIsNone(result['projectPath'])
        adapter.record_path.assert_not_called()
