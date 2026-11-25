"""An old updater can restart the gateway while legacy connections await consent."""
import json
import os
from pathlib import Path
import sys
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from bridge.app.desktop import Desktop
from bridge.features.accounts import secrets
from bridge.features.network import access, server_connection
from bridge.features.notifications.channels import write_json


@unittest.skipIf(os.name == 'nt', 'Mac legacy vault requires POSIX private-file checks')
class LegacyUpdateCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.entry = {**access.DEFAULTS, 'id': 'legacy-tunnel', 'name': 'Synthetic',
                      'accessMode': 'cloudflare', 'enabled': True, 'publicUrl': 'https://example.invalid'}
        write_json(self.directory/'desktop.json', {'codexHome': str(self.directory), 'lan': False,
                   'cloudflared': sys.executable, 'connections': [self.entry]})
        write_json(self.directory/'connection-vault.json', {self.entry['id']: True})
        self.original = {name: (self.directory/name).read_bytes() for name in ('desktop.json', 'connection-vault.json')}
        self.platform = patch.object(secrets.sys, 'platform', 'darwin')
        self.platform.start(); self.addCleanup(self.platform.stop)
        self.vault = patch.object(secrets, 'backend', side_effect=AssertionError('No automatic Keychain read'))
        self.vault.start(); self.addCleanup(self.vault.stop)

    def test_only_explicit_update_start_defers_legacy_credentials_without_changing_preferences(self):
        desktop = Desktop(self.directory)
        with patch.object(desktop, 'status', return_value={'running': False}), \
                patch('bridge.app.desktop.subprocess.Popen') as spawn:
            with self.assertRaises(secrets.LegacyCredentialsRequired):
                desktop.start()
            spawn.assert_not_called()
            spawn.return_value.pid = 123
            self.assertTrue(desktop.start({'deferLegacyCredentials': True})['started'])
            self.assertIn('--defer-legacy-credentials', spawn.call_args.args[0])
            self.assertEqual(json.loads(spawn.return_value.stdin.write.call_args.args[0]), {})
        for name, before in self.original.items():
            self.assertEqual((self.directory/name).read_bytes(), before)

    def test_pending_connection_has_explicit_status_and_other_errors_remain_failures(self):
        for mode in ('cloudflare', 'server'):
            row = {**self.entry, 'accessMode': mode, 'sshAuth': 'password'}
            with self.subTest(mode=mode):
                with self.assertRaises(secrets.LegacyCredentialsRequired):
                    server_connection.startup_credentials(row, self.directory)
                self.assertIsNone(server_connection.startup_credentials(row, self.directory, defer_legacy=True))
                status = json.loads((self.directory/('ssh-status-'+row['id']+'.json')).read_text())
                self.assertEqual(status['state'], 'credentials-required')
                self.assertEqual(status['pid'], os.getpid())
                self.assertIn('迁移', status['message'])
        with patch.object(server_connection, 'credentials', side_effect=ValueError('damaged credentials')):
            with self.assertRaisesRegex(ValueError, 'damaged credentials'):
                server_connection.startup_credentials(self.entry, self.directory, defer_legacy=True)
        self.assertEqual(server_connection.startup_credentials(self.entry, self.directory,
                         {'tunnelToken': 'synthetic'}, defer_legacy=True), {'tunnelToken': 'synthetic'})

    def test_old_helper_second_start_attaches_to_the_restored_gateway(self):
        desktop = Desktop(self.directory)
        with patch.object(desktop, 'status', return_value={'running': True}), \
                patch('bridge.app.desktop.subprocess.Popen') as spawn:
            self.assertFalse(desktop.start()['started'])
            spawn.assert_not_called()

    def test_non_mac_start_does_not_accept_the_legacy_deferral(self):
        desktop = Desktop(self.directory)
        with patch('bridge.app.desktop.sys.platform', 'linux'), \
                patch.object(desktop, 'status', return_value={'running': False}), \
                patch.object(server_connection, 'credentials', side_effect=secrets.LegacyCredentialsRequired('legacy')), \
                patch('bridge.app.desktop.subprocess.Popen') as spawn:
            with self.assertRaises(secrets.LegacyCredentialsRequired):
                desktop.start({'deferLegacyCredentials': True})
            spawn.assert_not_called()

    @unittest.skipUnless(sys.platform == 'darwin', 'actual macOS source gateway')
    def test_real_gateway_runs_with_pending_legacy_connection_and_preserves_saved_data(self):
        with socket.socket() as port_probe:
            port_probe.bind(('127.0.0.1', 0))
            port = port_probe.getsockname()[1]
        preferences = json.loads((self.directory/'desktop.json').read_text())
        preferences.update(port=port, localAccess=True, ipcPath=str(self.directory/'absent.sock'),
                           codexBin=str(self.directory/'absent-codex'))
        write_json(self.directory/'desktop.json', preferences)
        desktop = Desktop(self.directory)
        desktop.config()
        original = {name: (self.directory/name).read_bytes() for name in
                    ('desktop.json', 'config.json', 'connection-vault.json')}
        started = desktop.start({'deferLegacyCredentials': True})
        self.assertTrue(started['started'])
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if desktop.status()['running']:
                    status = json.loads((self.directory/'ssh-status-legacy-tunnel.json').read_text())
                    break
                time.sleep(.1)
            else:
                self.fail('Gateway failed to start: ' + (self.directory/'gateway.log').read_text())
            self.assertEqual(status['state'], 'credentials-required')
            self.assertEqual(status['pid'], started['pid'])
            self.assertFalse(desktop.start()['started'], 'old helper attaches without reading old credentials')
            self.assertFalse((self.directory/'.credential-vault/connections.enc').exists())
            for name, before in original.items():
                self.assertEqual((self.directory/name).read_bytes(), before)
        finally:
            desktop.stop()
            deadline = time.monotonic() + 10
            while desktop.status()['running'] and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertFalse(desktop.status()['running'], 'only this fixture gateway must stop')
