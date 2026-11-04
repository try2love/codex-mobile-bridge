"""Native CLI title control and readback are shared by local, SSH and WSL owners."""
import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.claude.cli_session import ClaudeCli, _rename_cli, _saved, _supports_rename
from bridge.clients.claude.remote import RemoteClaudeCli, worker_source
from bridge.clients.errors import BridgeUnavailable, ResumePreflightBlocked


class ClaudeCliRenameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.native = str(uuid.uuid4())
        self.row = {'id': 'gateway-id', 'nativeId': self.native, 'origin': 'fixture',
                    'cwd': str(self.home), 'title': 'before', 'host': 'ssh:fixture', 'alias': 'fixture',
                    'contextEvents': [], 'model': 'model', 'permissionMode': 'default', 'archived': False}
        self.owned = SimpleNamespace(condition=threading.Condition(), native=self.native,
            cwd=self.row['cwd'], origin='fixture', catalog={'commands': [{'name': 'rename'}],
                'capabilities': ['ui_surface_v1']}, error=None, finished=False, active=False,
            pending={}, model=None, permission=None, process=Mock(poll=Mock(return_value=None)),
            write=Mock(side_effect=AssertionError('rename must not send a user message')))
        self.owned.control = Mock(side_effect=lambda request: self.row.update(title=request['title']))

    def test_existing_local_and_remote_owners_use_one_native_control_contract(self):
        local = ClaudeCli()
        local._get = lambda sid: dict(self.row)
        local.owned[self.native] = self.owned
        remote = RemoteClaudeCli(SimpleNamespace(hosts=lambda: {'ssh:fixture': {'alias': 'fixture'}}))
        remote.index['gateway-id'] = self.row
        remote.owned['gateway-id'] = self.owned
        remote._get = lambda sid: dict(self.row)
        for client in (local, remote):
            with self.subTest(client=type(client).__name__), patch('subprocess.Popen') as spawn:
                result = client.call('rename', 'gateway-id', {'title': ' 原生中文名称 '})
                self.assertEqual(result, {'status': 'confirmed', 'scope': 'native',
                                          'result': {'id': 'gateway-id', 'title': '原生中文名称'}})
                self.assertTrue(client._public(self.row)['capabilities']['rename'])
                self.owned.control.assert_called_with({'subtype': 'rename_session', 'title': '原生中文名称',
                                                       'source': 'host', 'session_id': self.native})
                self.owned.write.assert_not_called()
                spawn.assert_not_called()

    def test_cold_cli_and_unadvertised_control_do_not_start_or_dispatch(self):
        client = ClaudeCli()
        client._get = lambda sid: dict(self.row)
        with patch('subprocess.Popen') as spawn, self.assertRaises(BridgeUnavailable):
            client.call('rename', 'gateway-id', {'title': 'new'})
        spawn.assert_not_called()
        self.assertFalse(client._public(self.row)['capabilities']['rename'])
        for catalog in ({}, {'commands': [{'name': 'rename'}]}, {'capabilities': ['ui_surface_v1']}):
            self.assertFalse(_supports_rename(catalog))
        self.owned.catalog = {'commands': [{'name': 'rename'}]}
        with self.assertRaises(ResumePreflightBlocked):
            _rename_cli(self.owned, self.row, 'new', lambda: self.row)
        self.owned.control.assert_not_called()

    def test_native_identity_and_invalid_titles_are_rejected_before_control(self):
        for title in (None, '', 'x'*121, 'a\nb'):
            with self.subTest(title=title), self.assertRaises(ResumePreflightBlocked):
                _rename_cli(self.owned, self.row, title, lambda: self.row)
        self.owned.native = str(uuid.uuid4())
        with self.assertRaises(ResumePreflightBlocked):
            _rename_cli(self.owned, self.row, 'new', lambda: self.row)
        self.owned.control.assert_not_called()

    def test_readback_can_settle_but_never_repeats_native_control(self):
        self.owned.control = Mock()
        read = Mock(side_effect=[None, {**self.row, 'title': 'new'}])
        with patch('bridge.features.sessions.display_metadata.time.sleep'):
            self.assertEqual(_rename_cli(self.owned, self.row, 'new', read)['status'], 'confirmed')
        self.owned.control.assert_called_once()
        with self.assertRaisesRegex(BridgeUnavailable, '待核对'):
            _rename_cli(self.owned, self.row, 'new', lambda: {**self.row, 'title': 'new', 'cwd': '/changed'})
        self.assertEqual(self.owned.control.call_count, 2)

    def test_native_title_events_survive_local_and_standalone_remote_reads(self):
        path = self.home/(self.native+'.jsonl')
        events = [
            {'type': 'user', 'sessionId': self.native, 'cwd': str(self.home),
             'permissionMode': 'default', 'message': {'role': 'user', 'content': 'first message'}},
            {'type': 'custom-title', 'sessionId': self.native, 'customTitle': '原生中文标题'},
            {'type': 'custom-title', 'sessionId': str(uuid.uuid4()), 'customTitle': 'wrong target'},
            {'type': 'user', 'sessionId': self.native, 'cwd': str(self.home),
             'message': {'role': 'user', 'content': 'later message'}},
        ]
        path.write_text('\n'.join(json.dumps(row) for row in events)+'\n')
        before = path.read_bytes()
        self.assertEqual(_saved(path, 'fixture')['title'], '原生中文标题')
        source = worker_source()
        scope = {'__name__': 'isolated_worker'}
        exec(compile(source, 'isolated_worker', 'exec'), scope)
        with patch.dict(os.environ, {}, clear=True):
            row = scope['saved'](path, {'origin': 'fixture', 'directory': str(self.home), 'binary': '/fixture/claude'})
        self.assertEqual(row['title'], '原生中文标题')
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
