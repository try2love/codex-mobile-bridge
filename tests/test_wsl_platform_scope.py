import builtins
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.app.service import Bridge
from bridge.clients.manager import DesktopSessions
from bridge.clients.claude.remote import RemoteClaudeCli


class WslPlatformScopeTests(unittest.TestCase):
    def test_non_windows_initialization_never_imports_wsl_modules(self):
        original_import = builtins.__import__
        def checked_import(name, *args, **kwargs):
            imported = args[2] if len(args) > 2 and args[2] else ()
            imports_windows_wsl = name == 'bridge.platforms.windows' and 'wsl' in imported
            if imports_windows_wsl or name.startswith(('bridge.features.endpoints.wsl', 'bridge.features.accounts.wsl',
                                'bridge.clients.claude.wsl', 'bridge.platforms.windows.wsl')):
                raise AssertionError('Unexpected WSL module: ' + name)
            return original_import(name, *args, **kwargs)
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
                root = Path(folder)
                (root/'codex').mkdir()
                with patch('sys.platform', platform), patch('builtins.__import__', side_effect=checked_import):
                    bridge = Bridge(root/'codex', root/'gateway')
                    manager = DesktopSessions(root/'clients', bridge=bridge)
                    try:
                        self.assertIsNotNone(bridge.entries)
                        self.assertIsNone(bridge.entries.wsl)
                        self.assertFalse(bridge.entries.public()['wslSupported'])
                        self.assertIsNone(bridge.wsl_accounts)
                        self.assertIsNone(manager.claude_wsl)
                        self.assertIsInstance(manager.adapters['claude'].remote_cli, RemoteClaudeCli)
                        with self.assertRaisesRegex(ValueError, '不支持 WSL'):
                            manager.scan_wsl()
                        with self.assertRaisesRegex(ValueError, '不支持 WSL'):
                            manager.toggle_client({'provider': 'wsl-claude:fixture', 'enabled': True})
                    finally:
                        manager.close()
                        bridge.close()
