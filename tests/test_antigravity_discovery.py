"""Official App/agy launch shapes and inventory reuse, without native clients."""
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import sys
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

    def test_darwin_identity_keeps_the_existing_native_signature(self):
        identity = {'pid': 123, 'uid': 1000, 'image': '/Applications/Antigravity.app/Contents/MacOS/Antigravity', 'zombie': False}
        with mock.patch.object(native.sys, 'platform', 'darwin'), \
                mock.patch.object(native.os, 'getuid', return_value=1000, create=True), \
                mock.patch('bridge.platforms.macos.force_exit.Native') as force_exit:
            force_exit.return_value.identity.return_value = identity
            self.assertIs(native._identity(123), identity)
        force_exit.return_value.identity.assert_called_once_with(123)

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
        def write_log(value):
            log.write_bytes(value.encode('utf-8'))
        args = ['--standalone', '--app_data_dir', 'antigravity', '--csrf_token', 'a'*32]
        # Desktop App overwrites log on startup with the port at line 6, then grows > 320 KB.
        padding = ('I1114 03:00:00.000000 1 runner.go:1] routine log line padding\n' * 6500)
        write_log(
            'Starting language server process with pid 123\n'
            'Language server listening on random port at 12345 for HTTP\n' + padding
        )
        self.assertGreater(log.stat().st_size, 320 * 1024)
        value, port = self.target(image, args)
        self.assertEqual(port, 12345)
        self.assertEqual(value['address'], 'http://127.0.0.1:12345')

        # Appending log > 320 KB with a stale port at the head and active port at the tail.
        write_log(
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
        write_log(prefix + 'Language server listening on random port at 33333 for HTTP\n' + padding)
        self.assertGreater(log.stat().st_size, 320 * 1024)
        value, port = self.target(image, args)
        self.assertEqual(port, 33333)

    def test_env_token_discovery_keeps_listener_and_process_generation_checks(self):
        _, image = self.bundle()
        log = self.home/'Library/Logs/Antigravity/language_server.log'
        log.parent.mkdir(parents=True)
        log.write_text('Language server listening on random port at 24568 for HTTP\n')
        args = ['--standalone', '--app_data_dir', 'antigravity']
        with mock.patch('bridge.platforms.macos.codex_daemon_env._environment',
                        return_value={'ANTIGRAVITY_SERVER_CSRF_TOKEN': 'm'*36}) as environment:
            value, port = self.target(image, args)
            self.assertEqual(value['token'], 'm'*36)
            self.assertEqual(port, 24568)
            environment.assert_called_once_with(123, ('ANTIGRAVITY_SERVER_CSRF_TOKEN',))
            for options in ({'changed': True}, {'listener': False}):
                with self.assertRaises(ValueError):
                    self.target(image, args, **options)
            environment.return_value = {'ANTIGRAVITY_SERVER_CSRF_TOKEN': 'short!'}
            with self.assertRaises(ValueError):
                self.target(image, args)
            environment.reset_mock()
            value, _ = self.target(image, args + ['--csrf_token', 'a'*32])
            self.assertEqual(value['token'], 'a'*32)
            environment.assert_not_called()

    def test_linux_env_token_reads_only_selected_values_from_stable_same_user(self):
        proc = self.home/'proc'
        process = proc/'123'
        process.mkdir(parents=True)
        uid = process.stat().st_uid
        stat = '123 (language_server) S 1 ' + ' '.join(['0']*17) + ' 999 0'
        (process/'stat').write_text(stat)
        (process/'environ').write_bytes(b'OTHER=private\0ANTIGRAVITY_SERVER_CSRF_TOKEN=' + b'c'*36 + b'\0')
        def path(value):
            return proc if str(value) == '/proc' else Path(value)
        with mock.patch.object(native, 'Path', path), \
                mock.patch.object(native.sys, 'platform', 'linux'), \
                mock.patch.object(native.os, 'getuid', return_value=uid, create=True):
            self.assertEqual(native._csrf_token(123, []), 'c'*36)
            self.assertEqual(native._proc_env(123), {})
            with mock.patch.object(native.os, 'getuid', return_value=uid+1):
                self.assertIsNone(native._csrf_token(123, []))
            (process/'environ').write_bytes(b'x'*65537)
            self.assertIsNone(native._csrf_token(123, []))
            (process/'environ').write_bytes(b'ANTIGRAVITY_SERVER_CSRF_TOKEN=' + b'c'*36 + b'\0')
            with mock.patch.object(Path, 'read_text', side_effect=[stat, stat.replace('999', '1000')]):
                self.assertIsNone(native._csrf_token(123, []))

    def test_windows_missing_argument_does_not_read_an_unverified_environment(self):
        with mock.patch.object(native.sys, 'platform', 'win32'), \
                mock.patch.object(native, '_proc_env') as environment:
            self.assertIsNone(native._csrf_token(123, []))
            self.assertEqual(native._csrf_token(123, ['--csrf_token', 'w'*32]), 'w'*32)
            environment.assert_not_called()

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

    @unittest.skipUnless(os.name == 'posix', 'stable application symlink')
    def test_registered_stable_symlink_follows_update_without_changing_the_configuration_home(self):
        app, _ = self.bundle()
        updated = self.home/'updated/Antigravity.app'
        shutil.copytree(app, updated)
        selected = self.home/'Stable Antigravity.app'; selected.symlink_to(app, target_is_directory=True)
        manager = DesktopSessions(self.home/'gateway'); self.addCleanup(manager.close)
        original_home = str(self.home/'existing-configuration'); manager.config['antigravityHome'] = original_home
        original = discover_clients
        with mock.patch.object(native.sys, 'platform', 'darwin'), \
             mock.patch.object(manager, '_discovery_preferences', side_effect=lambda: {
                 'antigravityApplications': manager.config.get('antigravityApplications', {}),
                 'antigravityHome': manager.config['antigravityHome']}), \
             mock.patch('bridge.clients.discovery.discover_clients', side_effect=lambda preferences: original(
                 preferences, platform='darwin', home=self.home, env={}, applications=[])), \
             mock.patch.object(manager, 'clients', side_effect=lambda **_: {'clients': list(manager.config['discovered'].values())}), \
             mock.patch('bridge.clients.desktop_app.process_inventory', side_effect=lambda paths: {
                 Path(path): {'pids': [], 'commands': {}} for path in paths}), \
             mock.patch('bridge.clients.lifecycle.launch_client', side_effect=AssertionError('launch')):
            manager.register_antigravity_application(str(selected))
            self.assertEqual(manager.config['discovered']['antigravity']['application'], str(app.resolve()))
            original_directory = manager.config['discovered']['antigravity']['dataDirectory']
            selected.unlink(); selected.symlink_to(updated, target_is_directory=True)
            manager.scan(inventory_only=True)
        row = manager.config['discovered']['antigravity']
        self.assertEqual(row['application'], str(updated.resolve()))
        self.assertEqual(row['installations']['app']['executable'], str(updated/'Contents/MacOS/Antigravity'))
        self.assertEqual(row['dataDirectory'], original_directory)
        self.assertEqual(manager.config['antigravityHome'], original_home)
        self.assertEqual(json.loads(manager.config_path.read_text())['antigravityApplications']['app'], str(selected))

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

    @unittest.skipUnless(os.name == 'posix', 'synthetic POSIX /proc ownership and symlinks')
    @mock.patch('bridge.platforms.antigravity.sys.platform', 'linux')
    def test_linux_appimage_installation_and_extract_runtime_verification(self):
        from bridge.platforms.linux import appimage as linux_appimage
        from bridge.platforms.linux import desktop as linux_desktop

        appimage_file = self.home/'Antigravity.AppImage'
        appimage_file.write_bytes(b'\x7fELF\x02\x01\x01\x00AI\x02fixture')
        appimage_file.chmod(0o755)
        desktop_entry = {
            'name': 'Antigravity',
            'exec': 'AppRun --no-sandbox %U',
            'type': 'Application',
            'startupwmclass': 'Antigravity',
            'x-appimage-version': '2.33.0',
        }
        # 仅在 Type 2 且内置桌面元数据匹配官方 Antigravity 时才接受 AppImage 安装包
        with mock.patch.object(linux_appimage, 'is_type2', return_value=True), \
                mock.patch.object(linux_appimage, 'metadata', side_effect=lambda p, m: desktop_entry if m == 'antigravity.desktop' else None):
            info = native.installation(appimage_file, platform='linux')
            self.assertEqual(info['surface'], 'app')
            self.assertEqual(info['version'], '2.33.0')
            self.assertEqual(info['packageType'], 'appimage')

            desktop_entry_spoof = {**desktop_entry, 'name': 'EvilTool'}
            with mock.patch.object(linux_appimage, 'metadata', return_value=desktop_entry_spoof):
                self.assertIsNone(native.installation(appimage_file, platform='linux'))

        if sys.platform != 'linux':
            return

        # 构造模拟 /proc 进程树：10(AppImage) -> 11(extracted antigravity) -> 12(extracted language_server)，以及 13(伪造 APPIMAGE 环境变量)
        proc = Path(self.temp.name)/'proc'
        extract_dir = str(Path(self.temp.name)/'appimage_extracted_fixture123')
        extracted = Path(extract_dir)
        extracted.mkdir()
        (extracted/'antigravity').write_bytes(b'fixture Antigravity binary')
        (extracted/'resources').mkdir()
        payload = json.dumps({'name':'antigravity','productName':'Antigravity','version':'2.33.0'}).encode()
        index = json.dumps({'files':{'package.json':{'size':len(payload),'offset':'0'}}}).encode()
        (extracted/'resources/app.asar').write_bytes(
            struct.pack('<4I',4,8+len(index),4+len(index),len(index))+index+payload)
        for pid, ppid, exe_target, env_bytes, cmdline in [
            (10, 1, str(appimage_file), b'', f'{appimage_file}\0'),
            (11, 10, f'{extract_dir}/antigravity (deleted)', b'', f'{extract_dir}/antigravity\0--ozone-platform=wayland\0'),
            (12, 11, f'{extract_dir}/resources/bin/language_server (deleted)',
             f'APPIMAGE={appimage_file}\x00APPDIR={extract_dir}\x00'.encode(),
             f'{extract_dir}/resources/bin/language_server\0--standalone\0--app_data_dir\0antigravity\0--csrf_token\0fixturetoken123456\0'),
            (13, 999, '/tmp/evil_extract/resources/bin/language_server (deleted)',
             f'APPIMAGE={appimage_file}\x00APPDIR=/tmp/evil_extract\x00'.encode(),
             '/tmp/evil_extract/resources/bin/language_server\0--standalone\0--app_data_dir\0antigravity\0--csrf_token\0fixturetoken123456\0'),
            (999, 1, str(self.home/'unrelated'), b'', '/bin/sh\0'),
        ]:
            (self.home/'unrelated').write_bytes(b'x') if pid == 999 else None
            pdir = proc/str(pid)
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir/'stat').write_text(f'{pid} (proc) S {ppid} ' + ' '.join(['0'] * 17) + f' {pid}0 0')
            (pdir/'exe').symlink_to(exe_target)
            (pdir/'environ').write_bytes(env_bytes)
            (pdir/'cmdline').write_bytes(cmdline.encode())

        log = self.home/'.config/Antigravity/logs/language_server.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('Language server listening on random port at 45678 for HTTP\n')

        def map_proc(value):
            value = str(value)
            if value == '/proc':
                return proc
            if value.startswith('/proc/'):
                return proc/value[len('/proc/'):]
            return Path(value)
        original_readlink = os.readlink
        def map_readlink(value, *args):
            return original_readlink(map_proc(value), *args) if str(value).startswith('/proc/') else original_readlink(value, *args)
        with mock.patch.object(linux_appimage, 'is_type2', return_value=True), \
                mock.patch.object(linux_appimage, 'metadata', side_effect=lambda p, m: desktop_entry if m == 'antigravity.desktop' else None), \
                mock.patch('bridge.platforms.linux.desktop.Path', side_effect=map_proc), \
                mock.patch('bridge.platforms.linux.force_exit.Path', side_effect=map_proc), \
                mock.patch('bridge.platforms.linux.force_exit.os.readlink', side_effect=map_readlink), \
                mock.patch.object(native, 'Path', side_effect=map_proc), \
                mock.patch.object(native, '_identity', side_effect=lambda pid: {
                    'pid': pid, 'parent': {10: 1, 11: 10, 12: 11, 13: 999, 14: 1}[pid],
                    'uid': os.getuid(), 'ruid': os.getuid(), 'birth': [pid, 1],
                    'image': {10: str(appimage_file), 11: f'{extract_dir}/antigravity (deleted)',
                              12: f'{extract_dir}/resources/bin/language_server (deleted)',
                              13: '/tmp/evil_extract/resources/bin/language_server (deleted)'}[pid],
                    'zombie': False}), \
                mock.patch('bridge.platforms.antigravity.Path.home', return_value=self.home), \
                mock.patch.object(native, '_listener', side_effect=lambda pid, port, _img: pid == 12 and port == 45678):
            # Shared inventory keeps the historical absolute image path but strips
            # the kernel marker; only Antigravity's verifier receives that marker.
            default_images = {int(p.name): str(image) for p, image in linux_desktop._process_rows()
                              if int(p.name) in (11, 12, 13)}
            self.assertEqual(default_images, {
                11: f'{extract_dir}/antigravity',
                12: f'{extract_dir}/resources/bin/language_server',
                13: '/tmp/evil_extract/resources/bin/language_server',
            })
            self.assertTrue(native._trusted_image(f'{extract_dir}/resources/bin/language_server (deleted)', pid=12))
            self.assertFalse(native._trusted_image('/tmp/evil_extract/resources/bin/language_server (deleted)', pid=13))
            self.assertEqual([pid for pid, _ in native._candidates()], [12])

            ep = native.endpoint('app', self.home)
            self.assertEqual(ep['address'], 'http://127.0.0.1:45678')
            self.assertEqual(ep['identity']['pid'], 12)
            native.verify_endpoint(ep)

            # 验证 FUSE 挂载模式下即使无直接 AppImage 父进程，也可通过 _mounts + runtime_candidates 核验
            fuse_dir = extracted
            fuse_proc = proc/'14'
            fuse_proc.mkdir(parents=True, exist_ok=True)
            (fuse_proc/'stat').write_text('14 (proc) S 1 ' + ' '.join(['0'] * 17) + ' 140 0')
            (fuse_proc/'exe').symlink_to(f'{fuse_dir}/resources/bin/language_server')
            (fuse_proc/'environ').write_bytes(f'APPIMAGE={appimage_file}\x00APPDIR={fuse_dir}\x00'.encode())
            with mock.patch.object(linux_appimage, '_mounts', return_value={fuse_dir}), \
                    mock.patch.object(linux_appimage, 'runtime_candidates', return_value=[{'executable': f'{fuse_dir}/antigravity'}]):
                self.assertTrue(native._trusted_image(f'{fuse_dir}/resources/bin/language_server', pid=14))
                self.assertFalse(native._trusted_image(f'{fuse_dir}/resources/bin/language_server', pid=13))

    @unittest.skipUnless(os.name == 'posix', 'synthetic POSIX /proc ownership and symlinks')
    @mock.patch('bridge.platforms.antigravity.sys.platform', 'linux')
    def test_extract_and_run_without_unsquashfs_requires_bound_image_and_verified_payload(self):
        from bridge.platforms.linux import appimage as linux_appimage
        from bridge.platforms.linux import desktop as linux_desktop

        image = self.home/'Applications/Antigravity.AppImage'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'fixture Type 2 Antigravity AppImage')
        image.chmod(0o755)
        appdir = Path(self.temp.name)/'appimage_extracted_fixture'
        appdir.mkdir()
        (appdir/'AppRun').write_text('#!/bin/sh\n')
        (appdir/'AppRun').chmod(0o755)
        binary = appdir/'antigravity'
        binary.write_bytes(b'fixture Antigravity GUI')
        binary.chmod(0o755)
        meta = json.dumps({'name': 'antigravity', 'productName': 'Antigravity',
                           'version': '2.33.0'}).encode()
        index = json.dumps({'files': {'package.json': {'size': len(meta), 'offset': '0'}}}).encode()
        (appdir/'resources').mkdir()
        (appdir/'resources/app.asar').write_bytes(
            struct.pack('<4I', 4, 8+len(index), 4+len(index), len(index)) + index + meta)
        server = appdir/'resources/bin/language_server'
        server.parent.mkdir(parents=True)
        server.write_bytes(b'fixture server')
        raw_server = str(server)+' (deleted)'

        proc = Path(self.temp.name)/'extract-proc'
        rows = {
            30: (1, str(image)),
            31: (30, str(appdir/'AppRun')),
            32: (31, str(binary)+' (deleted)'),
            33: (32, raw_server),
            34: (32, str(server)+' (deleted)'),
            35: (32, raw_server),
        }
        log = self.home/'.config/Antigravity/logs/language_server.log'
        log.parent.mkdir(parents=True)
        log.write_text('Language server listening on random port at 45678 for HTTP\n')
        for pid, (parent, executable) in rows.items():
            root = proc/str(pid)
            root.mkdir(parents=True)
            (root/'stat').write_text(f'{pid} (fixture) S {parent} '+' '.join(['0']*17)+f' {pid}0 0')
            (root/'exe').symlink_to(executable)
            app_env = f'APPIMAGE={image}\0APPDIR={appdir}\0'.encode()
            spoof_env = f'APPIMAGE={image}\0APPDIR={Path(self.temp.name)/"unrelated"}\0'.encode()
            wrong_image_env = f'APPIMAGE={self.home/"other.AppImage"}\0APPDIR={appdir}\0'.encode()
            (root/'environ').write_bytes(app_env if pid == 33 else spoof_env if pid == 34 else wrong_image_env if pid == 35 else b'')
            (root/'cmdline').write_bytes(
                b'language_server\0--standalone\0--app_data_dir\0antigravity\0--csrf_token\0fixture-token-12345678\0')

        def map_proc(value):
            value = str(value)
            if value == '/proc':
                return proc
            if value.startswith('/proc/'):
                return proc/value[len('/proc/'):]
            return Path(value)
        with mock.patch.object(linux_appimage, 'is_type2', return_value=True), \
                mock.patch.object(linux_appimage, '_parser', return_value=None), \
                mock.patch.object(native, 'Path', side_effect=map_proc), \
                mock.patch('bridge.platforms.linux.force_exit.Path', side_effect=map_proc), \
                mock.patch('bridge.platforms.linux.desktop.Path', side_effect=map_proc), \
                mock.patch.object(native, '_identity', side_effect=lambda pid: {
                    'pid': pid, 'uid': os.getuid(), 'parent': rows[pid][0],
                    'birth': [pid, 1], 'image': rows[pid][1], 'zombie': False}), \
                mock.patch.object(native, '_listener', return_value=True), \
                mock.patch.object(linux_desktop, '_process_rows', return_value=[
                    (proc/'33', Path(raw_server)), (proc/'34', Path(rows[34][1]))]), \
                mock.patch('bridge.platforms.antigravity.Path.home', return_value=self.home):
            self.assertIsNone(native._appimage_metadata(image))
            self.assertTrue(native._trusted_image(raw_server, pid=33))
            self.assertFalse(native._trusted_image(rows[34][1], pid=34))
            self.assertFalse(native._trusted_image(raw_server, pid=35))
            discovered = native.installation(image, platform='linux')
            self.assertEqual(discovered['packageType'], 'appimage')
            self.assertEqual(discovered['surface'], 'app')
            inventory = discover_clients(platform='linux', home=self.home, env={'PATH': ''})
            self.assertIn('antigravity', inventory)
            self.assertEqual(inventory['antigravity']['installations']['app']['executable'], str(image))
            missing_appdir = appdir.with_name(appdir.name+'-removed')
            appdir.rename(missing_appdir)
            self.assertFalse(native._trusted_image(raw_server, pid=33))
            self.assertIsNone(native.installation(image, platform='linux'))
            missing_appdir.rename(appdir)
            archive = appdir/'resources/app.asar'
            verified_archive = archive.read_bytes()
            archive.write_bytes(b'forged payload without an Antigravity manifest')
            self.assertFalse(native._trusted_image(raw_server, pid=33))
            archive.write_bytes(verified_archive)
            (proc/'33'/'environ').write_bytes(spoof_env)
            self.assertFalse(native._trusted_image(raw_server, pid=33))
            self.assertIsNone(native.installation(image, platform='linux'))
            (proc/'33'/'environ').write_bytes(app_env)
            self.assertEqual(native.installation(image, platform='linux')['surface'], 'app')
            self.assertIn('antigravity', discover_clients(platform='linux', home=self.home, env={'PATH': ''}))
            endpoint = native.endpoint('app', self.home)
            self.assertEqual(endpoint['identity']['pid'], 33)

    @mock.patch('bridge.platforms.antigravity.sys.platform', 'linux')
    def test_linux_unpacked_asar_and_fuse_without_unsquashfs_and_desktop_autodiscovery(self):
        from bridge.platforms.linux import appimage as linux_appimage

        def make_asar(folder, pkg_name='antigravity', product_name='Antigravity', version='2.33.0'):
            folder.mkdir(parents=True, exist_ok=True)
            exe = folder/'antigravity'
            exe.write_bytes(b'\x7fELFfixture')
            exe.chmod(0o755)
            meta = json.dumps({'name': pkg_name, 'productName': product_name, 'version': version}).encode()
            idx = json.dumps({'files': {'package.json': {'size': len(meta), 'offset': '0'}}}).encode()
            (folder/'resources/bin').mkdir(parents=True, exist_ok=True)
            (folder/'resources/app.asar').write_bytes(struct.pack('<4I', 4, 8+len(idx), 4+len(idx), len(idx)) + idx + meta)
            ls = folder/'resources/bin/language_server'
            ls.write_bytes(b'\x7fELFserver')
            return exe, ls

        # 1. 解压目录名称并非 "antigravity"（例如自定义压缩包目录）时，凭 app.asar + 主程序验证即可信任
        custom_root = Path(self.temp.name)/'downloads/custom-unpack'
        custom_exe, custom_ls = make_asar(custom_root)
        info = native.installation(custom_exe, platform='linux')
        self.assertEqual(info, {'application': str(custom_exe), 'executable': str(custom_exe), 'version': '2.33.0', 'surface': 'app'})
        self.assertTrue(native._trusted_image(custom_ls))

        fake_root = Path(self.temp.name)/'downloads/fake-unpack'
        _, fake_ls = make_asar(fake_root, pkg_name='unrelated-app', product_name='Unrelated')
        self.assertFalse(native._trusted_image(fake_ls))

        if sys.platform != 'linux':
            return

        # 2. FUSE 挂载模式下即使系统缺少 unsquashfs（_parser=None），也能完成冷安装识别与运行时信任校验
        fuse_mount = Path(self.temp.name)/'.mount_AntigFuse'
        fuse_exe, fuse_ls = make_asar(fuse_mount, version='2.33.0')
        (fuse_mount/'antigravity.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Antigravity\nStartupWMClass=Antigravity\nExec=AppRun %U\nX-AppImage-Version=2.33.0\n'
        )
        appimage_file = self.home/'Applications/Antigravity.AppImage'
        appimage_file.parent.mkdir(parents=True, exist_ok=True)
        appimage_file.write_bytes(b'\x7fELF\x02\x01\x01\x00AI\x02fuse')
        appimage_file.chmod(0o755)
        launcher = self.home/'Applications/antigravity-launcher.sh'
        launcher.write_text('#!/bin/sh\nexit 88\n')
        launcher.chmod(0o755)
        entries = self.home/'.local/share/applications'
        entries.mkdir(parents=True, exist_ok=True)
        (entries/'antigravity.desktop').write_text(f'[Desktop Entry]\nName=Antigravity\nType=Application\nExec="{launcher}" %F\n')

        proc = Path(self.temp.name)/'proc-fuse'
        pdir = proc/'44'
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir/'stat').write_text('44 (language_server) S 1 ' + ' '.join(['0'] * 17) + ' 440 0')
        (pdir/'environ').write_bytes(f'APPIMAGE={appimage_file}\x00APPDIR={fuse_mount}\x00'.encode())
        map_proc = lambda value: proc if str(value) == '/proc' else Path(value)

        with mock.patch.object(linux_appimage, '_parser', return_value=None), \
                mock.patch.object(linux_appimage, 'is_type2', return_value=True), \
                mock.patch.object(linux_appimage, '_mounts', return_value={fuse_mount}), \
                mock.patch.object(linux_appimage, 'runtime_candidates', return_value=[{'executable': str(fuse_exe)}]), \
                mock.patch.object(native, 'Path', side_effect=map_proc):
            app_info = native.installation(appimage_file, platform='linux')
            self.assertEqual(app_info['packageType'], 'appimage')
            self.assertEqual(app_info['version'], '2.33.0')
            self.assertTrue(native._trusted_image(fuse_ls, pid=44))

            # 3. Linux 开箱自动发现通过 .desktop + 同级启动脚本找到 AppImage，且绝不执行启动脚本
            discovered = discover_clients(
                platform='linux',
                home=self.home,
                env={'PATH': '', 'XDG_DATA_HOME': str(self.home/'.local/share'), 'XDG_DATA_DIRS': str(self.home/'empty')},
            )
            self.assertIn('antigravity', discovered)
            self.assertEqual(discovered['antigravity']['installations']['app']['executable'], str(appimage_file))
            self.assertEqual(discovered['antigravity']['version'], '2.33.0')


if __name__ == '__main__':
    unittest.main()
