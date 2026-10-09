import copy
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bridge.app.desktop import Desktop
from bridge.features.notifications.channels import settings

ROOT = Path(__file__).resolve().parents[1]


class AddressTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX interface enumeration')
    def test_interface_addresses_do_not_require_hostname_dns(self):
        from run import addresses
        with patch('run.Path.exists', return_value=True), patch('run.subprocess.run') as command, \
                patch('run.socket.getaddrinfo', side_effect=AssertionError('Hostname DNS must not block settings')):
            command.return_value.stdout = 'lo0:\n\tinet 127.0.0.1 netmask 0xff000000\nen0:\n\tinet 192.168.1.5 netmask 0xffffff00\n'
            self.assertEqual(addresses(), ['127.0.0.1', '192.168.1.5', 'localhost'])

    def test_dns_fallback_without_interface_command(self):
        from run import addresses
        with patch('run.Path.exists', return_value=False), patch('run.shutil.which', return_value=None), \
                patch('run.socket.getaddrinfo') as lookup:
            lookup.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.6', 0))]
            self.assertEqual(addresses(), ['127.0.0.1', '192.168.1.6', 'localhost'])


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.directory = Path(self.temp.name)
        self.desktop = Desktop(self.directory)
        self.desktop.status = lambda: {'running': False, 'portOccupied': False, 'supportsNotifications': False}

    def tearDown(self):
        self.temp.cleanup()

    def value(self):
        snapshot = self.desktop.snapshot()
        snapshot['preferences']['codexHome'] = str(self.directory)
        return snapshot

    def test_defaults_preserve_8787_and_private_credentials(self):
        snapshot = self.value()
        self.assertEqual(snapshot['preferences']['port'], 8787)
        self.assertEqual(snapshot['auth']['mode'], 'password')
        self.assertFalse(snapshot['notifications']['enabled'])
        self.assertFalse(snapshot['notifications']['barkEnabled'])
        self.assertNotIn('hash', snapshot['auth'])
        self.assertTrue((self.directory/'首次登录.txt').is_file())

    def test_save_roundtrip_and_secrets_are_not_returned(self):
        value = self.value()
        old_hash = self.desktop.config()['auth']['hash']
        value['notifications'].update(topic='private', token='private-token', enabled=True,
                                      barkEnabled=True, barkKey='private-bark-key', pushplusEnabled=True, pushplusToken='private-pushplus')
        self.desktop.save(value)
        snapshot = self.desktop.snapshot()
        self.assertEqual(snapshot['notifications']['token'], '')
        self.assertTrue(snapshot['notifications']['hasToken'])
        self.assertEqual(settings(self.directory)['token'], 'private-token')
        self.assertEqual(snapshot['notifications']['barkKey'], '')
        self.assertTrue(snapshot['notifications']['hasBarkKey'])
        self.assertEqual(snapshot['notifications']['pushplusToken'], '')
        self.assertTrue(snapshot['notifications']['hasPushplusToken'])
        self.desktop.save(snapshot)
        self.assertEqual(settings(self.directory)['pushplusToken'], 'private-pushplus')
        self.assertEqual(settings(self.directory)['barkKey'], 'private-bark-key')
        self.assertEqual(self.desktop.config()['auth']['hash'], old_hash)
        value['auth']['password'] = 'a new password for test'
        self.desktop.save(value)
        self.assertFalse((self.directory/'首次登录.txt').exists())
        self.assertNotEqual(self.desktop.config()['auth']['hash'], old_hash)

    def test_login_duration_roundtrip_and_invalid_save_is_atomic(self):
        value = self.value()
        self.assertEqual(value['auth']['sessionHours'], 12)
        value['auth']['sessionHours'] = 0
        self.assertEqual(self.desktop.save(value)['auth']['sessionHours'], 0)
        old = self.desktop.config_path.read_bytes()
        value['auth']['sessionHours'] = -1
        value['notifications']['barkKey'] = 'must-not-save'
        with self.assertRaises(ValueError):
            self.desktop.save(value)
        self.assertEqual(self.desktop.config_path.read_bytes(), old)
        self.assertFalse(settings(self.directory)['barkKey'])

    def test_notification_tests_use_selected_channel_and_hide_service_errors(self):
        with patch('bridge.app.desktop.publish') as ntfy, patch('bridge.app.desktop.publish_bark') as bark:
            self.assertIn('Bark', self.desktop.test_notification({'channel': 'bark'})['message'])
            self.assertFalse(ntfy.called)
            bark.assert_called_once()
            self.desktop.test_notification()
            ntfy.assert_called_once()
            with self.assertRaises(ValueError): self.desktop.test_notification({'channel': 'unknown'})
        with patch('bridge.app.desktop.publish_bark', side_effect=RuntimeError('private-bark-key')):
            with self.assertRaisesRegex(ValueError, '^Bark 测试失败') as error:
                self.desktop.test_notification({'channel': 'bark'})
            self.assertNotIn('private-bark-key', str(error.exception))

    def test_logs_redact_both_notification_secrets(self):
        value = self.value()
        value['notifications'].update(token='private-token', barkKey='private-bark-key')
        self.desktop.save(value)
        (self.directory/'gateway.log').write_text('example private-token private-bark-key\n')
        text = self.desktop.logs()['text']
        self.assertNotIn('private-token', text)
        self.assertNotIn('private-bark-key', text)
        self.assertEqual(text.count('[REDACTED]'), 2)

    def test_running_network_settings_cannot_disconnect_phone(self):
        value = self.value()
        self.desktop.save(value)
        self.desktop.status = lambda: {'running': True, 'portOccupied': False, 'supportsNotifications': True}
        value['preferences']['port'] = 9999
        with self.assertRaisesRegex(ValueError, '先停止'): self.desktop.save(value)
        self.assertEqual(self.desktop.preferences()['port'], 8787)

    def test_validation_does_not_partially_change_gateway_config(self):
        value = self.value();before = self.desktop.config()
        for modify in [lambda v:v['auth'].update(password='short'), lambda v:v.update(origins=['http://example.com']), lambda v:v['notifications'].update(server='file:///secret')]:
            invalid = copy.deepcopy(value);modify(invalid)
            with self.assertRaises(ValueError): self.desktop.save(invalid)
            self.assertEqual(self.desktop.config(), before)

    def test_arguments_are_preserved_as_separate_values(self):
        value = self.value();value['preferences'].update(ipcPath='pipe with spaces', codexBin='/path with spaces/codex', tunnel=False)
        self.desktop.save(value)
        argv = self.desktop.argv()
        self.assertEqual(argv[argv.index('--port')+1], '8787')
        self.assertEqual(argv[argv.index('--codex-bin')+1], '/path with spaces/codex')
        self.assertNotIn('--tunnel', argv)
        self.assertIn('--lan', argv)

    def test_running_gateway_is_adopted_without_duplicate_spawn(self):
        self.desktop.status = lambda: {'running': True}
        with patch('subprocess.Popen') as spawn:
            self.assertFalse(self.desktop.start()['started'])
            self.assertFalse(spawn.called)

class CloudflareSetupTests(unittest.TestCase):
    def test_blank_saved_path_rediscovers_and_missing_program_never_spawns(self):
        from bridge.features.notifications.channels import write_json
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            data = Path(directory)
            desktop = Desktop(data)
            desktop.status = lambda: {'running': False}
            write_json(data/'desktop.json', {'cloudflared': '', 'codexHome': directory, 'tunnel': True})
            with patch('bridge.app.desktop.shutil.which', return_value=''), patch('bridge.app.desktop.sys.platform', 'fixture'), patch('bridge.app.desktop.sys._MEIPASS', str(data/'absent-bundle'), create=True):
                with patch('bridge.app.desktop.subprocess.Popen') as spawn:
                    with self.assertRaisesRegex(ValueError, '一键安装'): desktop.start()
                    spawn.assert_not_called()
                (data/'bin').mkdir()
                executable = data/'bin'/('cloudflared.exe' if os.name == 'nt' else 'cloudflared')
                executable.write_text('fixture', encoding='utf8');executable.chmod(0o700)
                self.assertEqual(desktop.preferences()['cloudflared'], str(executable))

    def test_quick_tunnel_status_is_scoped_to_running_gateway(self):
        from bridge.features.notifications.channels import write_json
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            data = Path(directory);desktop = Desktop(data)
            desktop.status = lambda: {'running': True, 'pid': 100}
            write_json(data/'cloudflare-status.json', {'pid': 99, 'state': 'ready'})
            self.assertEqual(desktop.snapshot()['quickTunnel'], {})
            write_json(data/'cloudflare-status.json', {'pid': 100, 'state': 'failed', 'message': 'failed'})
            self.assertEqual(desktop.snapshot()['quickTunnel']['state'], 'failed')
            desktop.status = lambda: {'running': False, 'pid': None}
            self.assertEqual(desktop.snapshot()['quickTunnel'], {})

    def test_quick_tunnel_url_is_hidden_when_tunnel_is_reconnecting(self):
        from bridge.features.notifications.channels import write_json
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            data = Path(directory)
            desktop = Desktop(data)
            desktop.status = lambda: {'running': True, 'pid': 100}
            write_json(data/'desktop.json', {'tunnel': True, 'cloudflared': ''})
            (data/'外网地址.txt').write_text('https://old.trycloudflare.com\n', encoding='utf-8')
            write_json(data/'cloudflare-status.json', {'pid': 100, 'state': 'ready', 'message': ''})
            self.assertIn('https://old.trycloudflare.com', desktop.snapshot()['urls'])
            write_json(data/'cloudflare-status.json', {'pid': 100, 'state': 'reconnecting', 'message': ''})
            self.assertNotIn('https://old.trycloudflare.com', desktop.snapshot()['urls'])
