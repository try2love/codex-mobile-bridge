"""Native history survives a locked Desktop credential store; settings fail closed."""
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.clients.claude.accounts import ClaudeAccounts, ClaudeLoginDatabaseUnavailable
from bridge.clients.claude.setup import claude_data_home, developer_mode_enabled
from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]


class DesktopAccountBoundary(unittest.TestCase):
    def test_detail_and_settings_with_a_locked_official_account(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            root = Path(temp)
            profile = root/'profile'
            profile.mkdir()
            store = ClaudeAccounts(root/'accounts', profile)
            source = profile/'Cookies'
            db = sqlite3.connect(source, isolation_level=None)
            db.execute('CREATE TABLE cookies(host_key,name,value,encrypted_value,expires_utc)')
            db.executemany("INSERT INTO cookies VALUES('.claude.ai',?,'fixture',NULL,0)", [('sessionKey',), ('lastActiveOrg',)])
            manager = DesktopSessions(root/'gateway')
            self.addCleanup(manager.close)
            sid = 'fixture'
            session = {'id': sid, 'cwd': temp, 'backend': 'code', 'model': 'original'}
            manager.adapters['claude'].index[sid] = session
            def read(provider, action, *args):
                if action == 'list':
                    return {'sessions': [session]}
                self.assertEqual(action, 'detail')
                return {'session': session, 'messages': [{'role': 'assistant', 'text': 'history'}],
                        'capabilities': {'models': True, 'send': True}}
            with patch.object(manager, '_account_store', return_value=store), patch.object(manager, '_adapter_call', side_effect=read) as native:
                identity = manager.call('claude', 'detail', sid)['modelCatalogIdentity']
                self.assertTrue(identity)
                if os.name == 'nt':
                    import ctypes
                    db.close()
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.CreateFileW.restype = ctypes.c_void_p
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    handle = kernel.CreateFileW(str(source), 0x80000000, 0, None, 3, 0, None)
                    self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
                    unlock = lambda: kernel.CloseHandle(handle)
                else:
                    db.execute('BEGIN EXCLUSIVE')
                    unlock = lambda: db.execute('ROLLBACK')
                try:
                    self.assertEqual(len(manager.call('claude', 'list')['sessions']), 1)
                    detail = manager.call('claude', 'detail', sid)
                    self.assertEqual(detail['messages'][0]['text'], 'history')
                    self.assertEqual(detail['session']['model'], 'original')
                    self.assertTrue(detail['capabilities']['send'])
                    self.assertFalse(detail['capabilities']['models'])
                    self.assertEqual((detail['modelCatalogIdentity'], detail['draftAccountIdentity']), ('', ''))
                    self.assertIn('暂不可用', store.public()['notice'])
                    native.reset_mock()
                    for extra in ({}, {'modelCatalogIdentity': identity}):
                        with self.assertRaises(ClaudeLoginDatabaseUnavailable):
                            manager.call('claude', 'settings', sid, {'id': str(uuid.uuid4()), 'model': 'other', **extra})
                    native.assert_not_called()
                finally:
                    unlock()
                    db.close()
                self.assertEqual(manager.call('claude', 'detail', sid)['modelCatalogIdentity'], identity)

    def test_msix_only_redirects_the_exact_observed_roaming_profile(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            root = Path(temp)
            env = {'APPDATA':str(root/'Roaming'), 'LOCALAPPDATA':str(root/'Local')}
            nominal = root/'Roaming/Claude'
            exe = root/'WindowsApps/Claude_4.4212.0.0_x64__example/app/Claude.exe'
            store = root/'Local/Packages/Claude_example/LocalCache/Roaming/Claude'
            store.mkdir(parents=True)
            (store/'developer_settings.json').write_text(json.dumps({'allowDevTools': True}))
            for app, observed, expected in [(exe, nominal, store), (exe, root/'custom', root/'custom'),
                                            (root/'Classic/Claude.exe', nominal, nominal)]:
                with patch('bridge.clients.claude.setup._running_profiles', return_value={observed}):
                    self.assertEqual(claude_data_home(app, nominal, 'win32', env=env), expected)
            self.assertTrue(developer_mode_enabled(store))
