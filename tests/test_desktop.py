import copy
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bridge.features.network.interfaces as network_interfaces
from bridge.app.desktop import Desktop
from bridge.features.auth.auth import Auth, LoginRejected
from bridge.features.notifications.channels import settings

ROOT = Path(__file__).resolve().parents[1]
import bridge.features.network.interfaces as network_interfaces


class AddressTests(unittest.TestCase):
    def test_interface_addresses_do_not_require_hostname_dns(self):
        rows = [{'id': 'en0', 'name': 'en0', 'addresses': ['192.168.1.5']}]
        platform = SimpleNamespace(enumerate_interfaces=lambda: rows)
        with patch.object(network_interfaces, '_platform_module', return_value=platform):
            self.assertEqual(network_interfaces.eligible_lan_addresses(), ['192.168.1.5'])

    def test_unresolvable_enumeration_fails_closed_without_dns_guessing(self):
        platform = SimpleNamespace(enumerate_interfaces=lambda: [])
        with patch.object(network_interfaces, '_platform_module', return_value=platform):
            self.assertEqual(network_interfaces.eligible_lan_addresses(), [])


class DesktopTests(unittest.TestCase):
    def test_watch_display_reads_only_metadata_used_by_current_rows(self):
        with patch.object(self.desktop,'preferences',side_effect=AssertionError('no watches')), \
                patch('bridge.app.desktop.AppHosts',side_effect=AssertionError('unused remote hosts')), \
                patch('bridge.app.desktop.SessionStore',side_effect=AssertionError('unused local store')):
            self.assertEqual(self.desktop.notification_watches({'action':'list'}), {'watches':[]})
        home = self.directory/'display-home'
        home.mkdir()
        watches = self.directory/'notification-watches.json'
        watches.write_text(json.dumps([{'host':'remote','id':'one'}]))
        state = home/'.codex-global-state.json'
        def rename(label):
            state.write_text(json.dumps({'codex-managed-remote-connections':[
                {'hostId':'remote','alias':'fixture','displayName':label}]}))
        def read():
            return self.desktop.notification_watches({'action':'list'},preferences={'codexHome':str(home)})['watches'][0]['hostLabel']
        from bridge.clients.codex.remote import AppHosts
        with patch.object(AppHosts,'state',autospec=True,side_effect=AppHosts.state) as parse, \
                patch('bridge.app.desktop.SessionStore',side_effect=AssertionError('remote label only')):
            rename('first')
            self.assertEqual([read(),read()],['first','first'])
            rename('second')
            self.assertEqual(read(),'second')
            self.assertEqual(parse.call_count,2)

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

    def test_webhook_snapshot_masks_url_and_headers(self):
        from bridge.features.notifications.channels import save_settings
        save_settings(self.directory, {'webhookEnabled': True,
            'webhookUrl': 'https://receiver.example/secret?signature=private',
            'webhookHeaders': {'Authorization': 'Bearer secret'}})
        import json
        visible = self.desktop.snapshot()['notifications']
        self.assertEqual(visible['webhookUrlDisplay'], 'https://receiver.example')
        self.assertTrue(visible['hasWebhookUrl'] and visible['hasWebhookHeaders'])
        self.assertEqual(visible['webhookUrl'], '')
        self.assertEqual(visible['webhookHeaders'], {})
        self.assertNotIn('private', json.dumps(visible))
        self.assertNotIn('secret', json.dumps(visible))

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

    def test_password_change_can_keep_only_current_phone_bindings(self):
        value = self.value()
        old_config = self.desktop.config()['auth']
        old_password = next(line[3:] for line in (self.directory/'首次登录.txt').read_text(encoding='utf-8').splitlines()
                            if line.startswith('密码：'))
        auth = Auth(old_config, self.directory)
        kept, _ = auth.new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        revoked, _ = auth.new_session('192.0.2.2', 'BridgeMobile/0.1-Android')
        browser, _ = auth.new_session('192.0.2.3', 'Safari')
        auth.manage({'action': 'revoke', 'id': auth.key(revoked)})
        value['auth'].update(password='new-password-for-testing', revokeBoundDevices=False)
        self.desktop.save(value)
        with self.assertRaises(ValueError):
            auth.new_session('192.0.2.5', 'BridgeMobile/0.1-iOS')
        restarted = Auth(self.desktop.config()['auth'], self.directory)
        self.assertIsNotNone(restarted.get(kept))
        self.assertIsNone(restarted.get(revoked))
        self.assertIsNone(restarted.get(browser))
        with self.assertRaises(LoginRejected):
            restarted.login('admin', old_password, '192.0.2.4')
        self.assertIsNotNone(Auth(self.desktop.config()['auth'], self.directory).get(kept))

    def test_password_change_revokes_by_default_and_rejects_invalid_option(self):
        value = self.value()
        auth = Auth(self.desktop.config()['auth'], self.directory)
        phone, _ = auth.new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        value['auth'].update(password='new-password-for-testing', revokeBoundDevices='false')
        with self.assertRaises(ValueError):
            self.desktop.save(value)
        self.assertIsNotNone(Auth(self.desktop.config()['auth'], self.directory).get(phone))
        value['auth'].pop('revokeBoundDevices')
        self.desktop.save(value)
        self.assertIsNone(Auth(self.desktop.config()['auth'], self.directory).get(phone))

    def test_keep_binding_requires_stopped_gateway_before_any_write(self):
        value = self.value()
        value['auth'].update(password='new-password-for-testing', revokeBoundDevices=False)
        before = self.desktop.config_path.read_bytes()
        self.desktop.status = lambda: {'running': True, 'portOccupied': False}
        with self.assertRaises(ValueError):
            self.desktop.save(value)
        self.assertEqual(self.desktop.config_path.read_bytes(), before)

    def test_failed_config_write_restores_phone_bindings_and_can_retry(self):
        value = self.value()
        old_config = self.desktop.config()['auth']
        phone, _ = Auth(old_config, self.directory).new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        before_config = self.desktop.config_path.read_bytes()
        before_sessions = (self.directory/'auth-sessions.json').read_bytes()
        value['auth'].update(password='new-password-for-testing', revokeBoundDevices=False)
        from bridge.app.desktop import write_json
        with patch('bridge.app.desktop.write_json', side_effect=lambda path, data: (_ for _ in ()).throw(OSError('disk full')) if path == self.desktop.config_path else write_json(path, data)):
            with self.assertRaises(OSError):
                self.desktop.save(value)
        self.assertEqual(self.desktop.config_path.read_bytes(), before_config)
        self.assertEqual((self.directory/'auth-sessions.json').read_bytes(), before_sessions)
        self.assertIsNotNone(Auth(old_config, self.directory).get(phone))
        self.desktop.save(value)
        self.assertIsNotNone(Auth(self.desktop.config()['auth'], self.directory).get(phone))

    def test_stale_credential_migration_rejects_without_erasing_new_bindings(self):
        value = self.value()
        old_config = dict(self.desktop.config()['auth'])
        phone, _ = Auth(old_config, self.directory).new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        value['auth'].update(password='new-password-for-testing', revokeBoundDevices=False)
        self.desktop.save(value)
        current_config = dict(self.desktop.config()['auth'])
        current_sessions = (self.directory/'auth-sessions.json').read_bytes()
        with self.assertRaises(ValueError):
            Auth.preserve_trusted_on_password_change(old_config, {**old_config, 'hash': 'stale-new-hash'},
                                                     self.directory, lambda: self.fail('stale config published'))
        self.assertEqual((self.directory/'auth-sessions.json').read_bytes(), current_sessions)
        self.assertIsNotNone(Auth(current_config, self.directory).get(phone))

    def test_concurrent_password_saves_preserve_only_current_bindings(self):
        first = self.value()
        second = self.value()
        phone, _ = Auth(self.desktop.config()['auth'], self.directory).new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        first['auth'].update(password='first-new-password', revokeBoundDevices=False)
        second['auth'].update(password='second-new-password', revokeBoundDevices=False)
        entered = threading.Event()
        release = threading.Event()
        errors = []
        from bridge.app.desktop import password_record
        calls = 0

        def delayed_record(password):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('first save did not resume')
            return password_record(password)

        def save(desktop, value):
            try:
                desktop.save(value)
            except Exception as error:
                errors.append(error)

        with patch('bridge.app.desktop.password_record', side_effect=delayed_record):
            one = threading.Thread(target=save, args=(self.desktop, first))
            other = Desktop(self.directory)
            other.status = self.desktop.status
            two = threading.Thread(target=save, args=(other, second))
            one.start()
            self.assertTrue(entered.wait(5))
            two.start()
            release.set()
            one.join(10)
            two.join(10)
        self.assertFalse(one.is_alive() or two.is_alive())
        self.assertEqual(errors, [])
        self.assertIsNotNone(Auth(self.desktop.config()['auth'], self.directory).get(phone))

    def test_account_change_cannot_keep_phone_binding(self):
        value = self.value()
        auth = Auth(self.desktop.config()['auth'], self.directory)
        phone, _ = auth.new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        value['auth'].update(password='new-password-for-testing', username='other', revokeBoundDevices=False)
        self.desktop.save(value)
        self.assertIsNone(Auth(self.desktop.config()['auth'], self.directory).get(phone))

    def test_no_password_save_keeps_binding_and_mode_change_revokes_it(self):
        value = self.value()
        phone, _ = Auth(self.desktop.config()['auth'], self.directory).new_session('192.0.2.1', 'BridgeMobile/0.1-iOS')
        value['auth']['revokeBoundDevices'] = True
        self.desktop.save(value)
        self.assertIsNotNone(Auth(self.desktop.config()['auth'], self.directory).get(phone))
        value['auth'].update(mode='none', password='new-password-for-testing', revokeBoundDevices=False)
        self.desktop.save(value)
        self.assertIsNone(Auth(self.desktop.config()['auth'], self.directory).get(phone))

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

    def test_logs_redact_webhook_url_query_path_and_header_values(self):
        from bridge.features.notifications.channels import save_settings
        save_settings(self.directory, {'webhookUrl': 'https://receiver.example/private-path?signature=private-query',
            'webhookHeaders': {'Authorization': 'Bearer private-header'}})
        (self.directory/'gateway.log').write_text(
            'https://receiver.example/private-path?signature=private-query\n'
            '/private-path signature=private-query Bearer private-header\n')
        text = self.desktop.logs()['text']
        for secret in ('private-path', 'private-query', 'private-header'):
            self.assertNotIn(secret, text)

    def test_feishu_signature_secret_is_masked_in_snapshot_and_logs(self):
        from bridge.features.notifications.channels import save_settings, WebhookDeliveryError
        save_settings(self.directory, {'webhookUrl': 'https://open.feishu.cn/open-apis/bot/v2/hook/private',
            'webhookSignEnabled': True, 'webhookSignSecret': 'private-sign-secret'})
        visible = self.desktop.snapshot()['notifications']
        self.assertEqual(visible['webhookSignSecret'], '')
        self.assertTrue(visible['hasWebhookSignSecret'])
        self.assertEqual(visible['webhookResolvedProvider'], 'feishu')
        (self.directory/'gateway.log').write_text('private-sign-secret\n')
        self.assertNotIn('private-sign-secret', self.desktop.logs()['text'])
        with patch('bridge.app.desktop.publish_webhook', side_effect=WebhookDeliveryError('飞书未接受通知（19021：签名或时间无效）')):
            with self.assertRaisesRegex(ValueError, '19021'):
                self.desktop.test_notification({'channel': 'webhook'})

    def test_webhook_url_reveal_requires_explicit_private_credentials_request(self):
        from bridge.features.notifications.channels import save_settings
        url = 'https://discord.com/api/webhooks/123/private-token'
        save_settings(self.directory, {'webhookUrl': url})
        self.assertEqual(self.desktop.read_credentials({'channel': 'webhook'}), {'webhookUrl': url})
        self.assertNotIn('webhookUrl', self.desktop.read_credentials({}))
        self.assertEqual(self.desktop.snapshot()['notifications']['webhookUrl'], '')

    def test_named_webhook_desktop_snapshot_test_reveal_and_log_redaction(self):
        value = self.desktop.webhook_endpoint({'action': 'upsert', 'webhookName': '机器人',
            'webhookEnabled': True, 'webhookUrl': 'https://open.feishu.cn/open-apis/bot/v2/hook/private-path',
            'webhookSignEnabled': True, 'webhookSignSecret': 'private-sign',
            'webhookHeaders': {'Authorization': 'Bearer private-header'}})
        webhook_id = value['selectedWebhookId']
        visible = self.desktop.snapshot()['notifications']
        self.assertEqual(visible['webhookEndpoints'][0]['webhookId'], webhook_id)
        self.assertNotIn('private-path', str(visible))
        self.assertNotIn('private-sign', str(visible))
        self.assertEqual(self.desktop.read_credentials({'channel': 'webhook', 'webhookId': webhook_id}),
                         {'webhookUrl': 'https://open.feishu.cn/open-apis/bot/v2/hook/private-path'})
        with patch('bridge.app.desktop.publish_webhook') as sender:
            self.desktop.test_notification({'channel': 'webhook', 'webhookId': webhook_id})
            self.assertEqual(sender.call_args.args[0]['webhookUrl'],
                             'https://open.feishu.cn/open-apis/bot/v2/hook/private-path')
        (self.directory/'gateway.log').write_text('private-path private-sign private-header')
        text = self.desktop.logs()['text']
        for secret in ('private-path', 'private-sign', 'private-header'):
            self.assertNotIn(secret, text)
        self.desktop.webhook_endpoint({'action': 'delete', 'webhookId': webhook_id})
        self.assertEqual(self.desktop.snapshot()['notifications']['webhookEndpoints'], [])

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
