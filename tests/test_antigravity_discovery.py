"""Official App/agy launch shapes and inventory reuse, without native clients."""
import hashlib
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from bridge.clients.antigravity.cli_session import AntigravityCli
from bridge.clients.discovery import discover_clients
from bridge.clients.manager import DesktopSessions
from bridge.platforms import antigravity as native

ROOT = Path(__file__).resolve().parents[1]


class AntigravityDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)/'home'
        self.home.mkdir()

    def cli(self, folder=None):
        path = (folder or self.home/'.gemini/bin')/'agy'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'isolated official-installer fixture')
        info = path.stat()
        (path.parent/'agy.version.json').write_text(json.dumps({
            'schemaVersion': 1, 'version': '1.5.3', 'size': info.st_size,
            'mtimeMs': info.st_mtime_ns / 1_000_000, 'ino': info.st_ino,
            'sha512': hashlib.sha512(path.read_bytes()).hexdigest(),
            'releaseBaseUrl': native.CLI_RELEASES[0]}))
        return path

    def bundle(self, surface='app'):
        root = self.home/('Antigravity IDE.app' if surface == 'ide' else 'Antigravity.app')
        (root/'Contents/MacOS').mkdir(parents=True)
        (root/'Contents/MacOS/Antigravity').write_bytes(b'fixture')
        identifier = 'com.google.antigravity' + ('-ide' if surface == 'ide' else '')
        (root/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': identifier, 'CFBundleExecutable': 'Antigravity'}))
        server = root/'Contents/Resources/bin/language_server'
        server.parent.mkdir(parents=True)
        server.write_bytes(b'fixture')
        return root, server

    def target(self, image, args, *, surface='app', changed=False, listener=True):
        identity = {'pid': 123, 'uid': 1, 'image': str(image), 'birth': [1, 2]}
        sequence = [identity, {**identity, 'birth': [2, 3]}] if changed else None
        with mock.patch.object(native.sys, 'platform', 'darwin'), \
                mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.object(native, '_candidates', return_value=[(123, args)]), \
                mock.patch.object(native, '_identity', return_value=identity, side_effect=sequence), \
                mock.patch.object(native, '_listener', return_value=listener) as listeners:
            value = native.endpoint(surface, self.home)
            return value, listeners.call_args.args[1]

    def test_official_app_and_ide_hub_use_their_native_ls_listener(self):
        _, image = self.bundle()
        log = self.home/'Library/Logs/Antigravity/language_server.log'
        log.parent.mkdir(parents=True)
        log.write_text('Language server listening on random port at 12345 for HTTP\n')
        args = ['--standalone', '--app_data_dir', 'antigravity', '--csrf_token', 'a'*32]
        value, port = self.target(image, args)
        self.assertEqual(port, 12345)
        self.assertEqual(value['surface'], 'app')

        # IDE 2.6 launches ~/.gemini/bin/agy --hub with the App data root.
        # Hub UI port 23456 is deliberately distinct from native LS port 12346.
        image = self.cli()
        log = self.home/'.gemini/antigravity/cli.log'
        log.parent.mkdir(parents=True)
        log.write_text('Language server listening on loopback port at 12346 for local HTTP (SDK/gRPC)\n')
        args = ['--hub', '--hub-port=23456', '--app_data_dir=antigravity', '--csrf_token='+'b'*36]
        value, port = self.target(image, args)
        self.assertEqual(port, 12346)
        self.assertNotIn('23456', value['address'])
        with self.assertRaises(ValueError):
            self.target(image, args, surface='ide')
        for options in ({'changed': True}, {'listener': False}):
            with self.assertRaises(ValueError):
                self.target(image, args, **options)

    def test_large_language_server_log_preserves_head_and_tail_ports(self):
        _, image = self.bundle()
        log = self.home/'Library/Logs/Antigravity/language_server.log'
        log.parent.mkdir(parents=True)
        args = ['--standalone', '--app_data_dir', 'antigravity', '--csrf_token', 'a'*32]
        # Desktop App overwrites log on startup with the port at line 6, then grows > 320 KB.
        padding = ('I1114 03:00:00.000000 1 runner.go:1] routine log line padding\n' * 6500)
        log.write_text(
            'Starting language server process with pid 123\n'
            'Language server listening on random port at 12345 for HTTP\n' + padding
        )
        self.assertGreater(log.stat().st_size, 320 * 1024)
        value, port = self.target(image, args)
        self.assertEqual(port, 12345)
        self.assertEqual(value['address'], 'http://127.0.0.1:12345')

        # Appending log > 320 KB with a stale port at the head and active port at the tail.
        log.write_text(
            'Language server listening on random port at 11111 for HTTP\n' + padding
            + 'Language server listening on random port at 22222 for HTTP\n'
        )
        identity = {'pid': 123, 'uid': 1, 'image': str(image), 'birth': [1, 2]}
        with mock.patch.object(native.sys, 'platform', 'darwin'), \
                mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.object(native, '_candidates', return_value=[(123, args)]), \
                mock.patch.object(native, '_identity', return_value=identity), \
                mock.patch.object(native, '_listener', side_effect=lambda _pid, p, _img: p == 22222):
            value = native.endpoint('app', self.home)
        self.assertEqual(value['address'], 'http://127.0.0.1:22222')

        # Port line straddling the 64 KB head chunk boundary still resolves.
        prefix = ('X' * 63 + '\n') * 1023 + 'X' * 40
        log.write_text(prefix + 'Language server listening on random port at 33333 for HTTP\n' + padding)
        self.assertGreater(log.stat().st_size, 320 * 1024)
        value, port = self.target(image, args)
        self.assertEqual(port, 33333)

    def test_log_growth_after_size_check_stays_within_snapshot(self):
        initial = b'Language server listening on random port at 12345 for HTTP\n'
        appended = b'x' * (2 * 1024 * 1024) + b'\nLanguage server listening on random port at 22222 for HTTP\n'

        class GrowingLog(io.BytesIO):
            def seek(self, offset, whence=0):
                if whence == 0 and offset == 0 and self.tell() == len(initial):
                    self.write(appended)
                return super().seek(offset, whence)

        log = mock.Mock()
        log.open.return_value = GrowingLog(initial)
        self.assertEqual(native._log_ports(log), {12345})

    def test_cli_installation_metadata_and_native_rpc_are_independently_verified(self):
        image = self.cli()
        self.assertEqual(native.installation(image)['surface'], 'cli')
        self.assertTrue(native._trusted_image(image))
        image.write_bytes(b'replaced native executable')
        self.assertIsNone(native.installation(image))
        self.assertFalse(native._trusted_image(image))
        cli = AntigravityCli(self.home)
        with mock.patch('bridge.clients.antigravity.cli_session.endpoint', side_effect=ValueError('no native listener')), \
                mock.patch.object(cli._opener, 'open') as http:
            self.assertFalse(cli.available())
            http.assert_not_called()

    def test_hub_backing_ls_uses_official_rpc_path_without_sending_to_hub_ui(self):
        cli = AntigravityCli(self.home)
        target = {'address': 'http://127.0.0.1:12346', 'token': 'fixture'}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with mock.patch('bridge.clients.antigravity.cli_session.endpoint', return_value=target), \
                mock.patch('bridge.clients.antigravity.cli_session.verify_endpoint') as verify, \
                mock.patch.object(cli._opener, 'open', return_value=response) as http:
            cli.send_user_message('native-id', 'fixture prompt', surface='app')
        request = http.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:12346/exa.language_server_pb.LanguageServerService/SendUserCascadeMessage')
        self.assertEqual(json.loads(request.data), {'cascade_id': 'native-id', 'items': [{'text': 'fixture prompt'}]})
        self.assertEqual(request.get_header('X-codeium-csrf-token'), 'fixture')
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(http.call_count, 1)

    def test_discovery_collects_official_app_ide_cli_and_saved_paths(self):
        app, _ = self.bundle()
        ide, _ = self.bundle('ide')
        cli = self.cli(self.home/'custom/bin')
        rows = discover_clients({'antigravityApplications': {'cli': str(cli)}},
            platform='darwin', home=self.home, env={}, applications=[self.home])
        found = rows['antigravity']['installations']
        self.assertEqual(set(found), {'app', 'ide', 'cli'})
        self.assertEqual(found['app']['application'], str(app))
        self.assertEqual(found['ide']['application'], str(ide))
        self.assertEqual(found['cli']['application'], str(cli))
        self.assertFalse(rows['antigravity']['historyAvailable'])
        self.assertFalse((self.home/'.gemini/antigravity/brain').exists())

    def test_shared_manual_save_adds_each_installation_without_a_second_profile(self):
        manager = DesktopSessions(self.home/'gateway')
        self.addCleanup(manager.close)
        app, _ = self.bundle()
        ide, _ = self.bundle('ide')
        cli = self.cli(self.home/'custom/bin')
        original = discover_clients
        with mock.patch.object(native.sys, 'platform', 'darwin'), \
                mock.patch.object(manager, '_discovery_preferences', side_effect=lambda: {
                'antigravityApplications': manager.config.get('antigravityApplications', {})}), \
                mock.patch('bridge.clients.discovery.discover_clients', side_effect=lambda p: original(
                    p, platform='darwin', home=self.home, env={}, applications=[])), \
                mock.patch.object(manager, 'clients', return_value={'clients': [{'id': 'antigravity'}]}):
            for path in (app, ide, cli):
                result = manager.control({'action': 'client-profiles', 'operation': 'save',
                    'family': 'antigravity', 'kind': 'default-application', 'application': str(path)})
                self.assertEqual(result['entry'], 'antigravity')
            self.assertEqual(set(manager.config['antigravityApplications']), {'app', 'ide', 'cli'})
            self.assertEqual(set(manager.config['discovered']['antigravity']['installations']), {'app', 'ide', 'cli'})
            self.assertFalse(manager.enabled('antigravity'))
            self.assertEqual(manager.instances.registry.list('antigravity'), [])
            self.assertEqual(manager.instances.children, {})
            self.assertIsNone(manager.adapters.get('antigravity'))
            with self.assertRaises(ValueError):
                manager.register_antigravity_application(str(self.home/'fake'))
            manager.config.setdefault('enabled', {})['antigravity'] = True
            with self.assertRaisesRegex(ValueError, '停用'):
                manager.register_antigravity_application(str(cli))

    def test_inventory_survives_restart_without_scanning_but_manual_scan_and_missing_path_refresh(self):
        binary = self.home/'native-app'
        binary.write_bytes(b'fixture')
        rows = {family: {'id': family, 'installed': False, 'executable': '',
                        'dataDirectory': str(self.home/family)} for family in ('codex', 'claude', 'deepseek')}
        rows['codex'].update(installed=True, executable=str(binary))
        directory = self.home/'gateway'
        with mock.patch('bridge.clients.discovery.discover_clients', return_value=rows) as discover:
            for iteration in range(2):
                manager = DesktopSessions(directory)
                manager.config['enabled'] = dict.fromkeys(('codex', 'claude', 'deepseek', 'zcode', 'antigravity'), False)
                manager.gateway_running = True
                with mock.patch.object(manager, '_discovery_preferences', return_value={}), \
                        mock.patch.object(manager, 'clients', return_value={'clients': []}):
                    manager.start_enabled()
                    manager.scan(inventory_only=True)
                    self.assertEqual(discover.call_count, 1)
                    if iteration:
                        manager.scan()
                        self.assertEqual(discover.call_count, 2)
                        binary.unlink()
                        manager.scan(inventory_only=True)
                        self.assertEqual(discover.call_count, 3)
                manager.close()


if __name__ == '__main__':
    unittest.main()
