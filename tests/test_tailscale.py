import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge.features.network import access, tailscale
from bridge.features.notifications.addresses import entry_urls
from bridge.app.desktop import Desktop

ROOT = Path(__file__).resolve().parents[1]
ENTRY = {**access.DEFAULTS, 'id': 'ts', 'name': '', 'enabled': True, 'accessMode': 'tailscale',
         'publicUrl': 'https://computer.example.ts.net', 'tailscaleNodeId': 'n123'}
STATUS = {'Version': '1.102.4', 'BackendState': 'Running',
          'Self': {'DNSName': 'computer.example.ts.net.', 'ID': 'n123'}}


class TailscaleTests(unittest.TestCase):
    def test_detect_is_read_only_and_returns_no_peer_or_account_details(self):
        raw = {**STATUS, 'Peer': {'secret': 'do not return'}, 'User': {'email': 'private@example.test'}}
        result = subprocess.CompletedProcess([], 0, json.dumps(raw), '')
        with patch.object(tailscale, 'executable', return_value='/tailscale'), patch.object(tailscale.subprocess, 'run', return_value=result) as run:
            state = tailscale.inspect(ENTRY)
        self.assertEqual(state['url'], ENTRY['publicUrl'])
        self.assertEqual(state['nodeId'], 'n123')
        self.assertNotIn('Peer', state)
        self.assertNotIn('User', state)
        self.assertEqual(run.call_args.args[0], ['/tailscale', 'status', '--json'])

    def test_missing_login_expiry_and_changed_identity(self):
        with patch.object(tailscale, 'executable', return_value=''):
            self.assertEqual(tailscale.inspect(ENTRY)['state'], 'missing')
        with patch.object(tailscale, 'executable', return_value='/tailscale'), patch.object(tailscale, 'command_json') as read:
            read.return_value = {**STATUS, 'BackendState': 'NeedsLogin', 'AuthURL': 'https://evil.test/login'}
            self.assertEqual(tailscale.inspect(ENTRY)['authUrl'], '')
            read.return_value = {**STATUS, 'Self': {**STATUS['Self'], 'KeyExpired': True}}
            with self.assertRaisesRegex(ValueError, '过期'):
                tailscale.inspect(ENTRY)
            read.return_value = {**STATUS, 'Self': {**STATUS['Self'], 'ID': 'nOther'}}
            with self.assertRaisesRegex(ValueError, '变化'):
                tailscale.require_identity(ENTRY)

    def test_validation_rejects_foreign_urls_bad_modes_ports_and_duplicate_entries(self):
        for change in ({'publicUrl': 'https://evil.test'}, {'publicUrl': 'https://example.ts.net.evil.test'},
                       {'tailscaleMode': 'reset'}, {'tailscalePort': True}, {'tailscalePort': 22},
                       {'tailscalePort': 8443}, {'tailscaleNodeId': 'one\ntwo'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                access.validate_connections({'connections': [{**ENTRY, **change}]})
        with self.assertRaisesRegex(ValueError, '一个 Tailscale'):
            access.validate_connections({'connections': [ENTRY, {**ENTRY, 'id': 'second', 'publicUrl': 'https://other.example.ts.net'}]})
        self.assertEqual(access.public_urls({'connections': [{**ENTRY, 'publicUrl': ''}]}), [])
        self.assertEqual(access.validate_connections({'connections': [{**ENTRY, 'publicUrl': '', 'tailscaleNodeId': ''}]})[0]['publicUrl'], '')

    def test_authorization_urls_are_exact_https_login_origin(self):
        good = 'https://login.tailscale.com/f/funnel?node=n123'
        self.assertEqual(tailscale.authorization_url(good), good)
        for bad in ('http://login.tailscale.com/a', 'https://login.tailscale.com.evil/a',
                    'https://user:pass@login.tailscale.com/a', 'https://login.tailscale.com:8443/a',
                    'https://login.tailscale.com/a\n', 'javascript:alert(1)'):
            self.assertEqual(tailscale.authorization_url(bad), '')

    def test_existing_foreground_and_background_ports_are_not_overwritten(self):
        for config in ({'TCP': {'443': {'HTTPS': True}}},
                       {'Foreground': {'another-owner': {'TCP': {'443': {'HTTPS': True}}}}},
                       {'Web': {'other.example.ts.net:443': {'Handlers': {'/': {'Text': 'hello'}}}}}):
            with self.subTest(config=config), tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
                tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
                with patch.object(tailscale, 'require_identity'), patch.object(tailscale, 'executable', return_value='/tailscale'), \
                     patch.object(tailscale, 'command_json', return_value=config), patch.object(tailscale.subprocess, 'Popen') as spawn:
                    tunnel.start()
                    tunnel.thread.join(2)
                    self.assertFalse(tunnel.thread.is_alive())
                    spawn.assert_not_called()
                    self.assertEqual(tunnel.last_status['state'], 'blocked')
                    tunnel.close()
        self.assertFalse(tailscale.port_in_use({'TCP': {'8443': {'HTTPS': True}}}, 443))

    def test_foreground_process_and_only_owned_process_are_stopped(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            # Actual child lifecycle, no Tailscale/network/account changes.
            helper = Path(folder)/'client.py'
            helper.write_text('import time\nprint("Available on the internet:", flush=True)\ntime.sleep(60)\n')
            import sys
            launch = subprocess.Popen
            calls = []
            def spawn(argv, **kwargs):
                calls.append(argv)
                return launch([sys.executable, '-B', str(helper)], **kwargs)
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            connected = threading.Event()
            original = tunnel.status
            def status(state, message, auth_url=''):
                original(state, message, auth_url)
                if state == 'connected':
                    connected.set()
            tunnel.status = status
            with patch.object(tailscale, 'require_identity'), patch.object(tailscale, 'executable', return_value='/tailscale'), \
                 patch.object(tailscale, 'command_json', return_value={}), patch.object(tailscale.subprocess, 'Popen', side_effect=spawn):
                try:
                    tunnel.start()
                    self.assertTrue(connected.wait(3))
                    process = tunnel.process
                finally:
                    tunnel.close()
            self.assertFalse(tunnel.thread.is_alive())
            self.assertIsNotNone(process.poll())
            self.assertEqual(calls, [['/tailscale', 'funnel', '--https=443', 'http://127.0.0.1:8787']])
            self.assertEqual(tunnel.last_status['state'], 'stopped')

    def test_credentials_stay_required_and_origin_is_removed_on_disable(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            desktop = Desktop(folder)
            desktop.status = lambda: {'running': False}
            value = desktop.snapshot()
            value['preferences'].update(codexHome=folder, connections=[ENTRY])
            value['auth']['mode'] = 'none'
            with self.assertRaisesRegex(ValueError, '登录验证'):
                desktop.save(value)
            value['auth']['mode'] = 'password'
            desktop.save(value)
            self.assertIn(ENTRY['publicUrl'], desktop.config()['origins'])
            with patch.object(tailscale, 'inspect', return_value={'state': 'ready', 'url': ENTRY['publicUrl']}):
                self.assertEqual(desktop.tailscale_setup({'id': 'ts'})['state'], 'ready')
            value['preferences']['connections'] = [{**ENTRY, 'enabled': False}]
            desktop.save(value)
            self.assertNotIn(ENTRY['publicUrl'], desktop.config()['origins'])

    def test_address_notifications_only_offer_connected_tailscale_entry(self):
        preferences = {'connections': [ENTRY]}
        self.assertEqual(entry_urls(preferences, [], '', {'ts': {'state': 'connecting'}}), [])
        self.assertEqual(entry_urls(preferences, [], '', {'ts': {'state': 'connected'}}), [ENTRY['publicUrl']])

    def test_fixed_entry_uses_verified_owner_when_loopback_port_is_shadowed(self):
        preferences = {**ENTRY, 'port': 8787}
        with patch.object(access, 'read_auth', return_value={'instanceId': 'actual'}) as read:
            access.check_entry(preferences, instance_id='actual')
            self.assertEqual(read.call_args.args, (ENTRY['publicUrl'],))
        with patch.object(access, 'read_auth', return_value={'instanceId': 'other'}), self.assertRaisesRegex(ValueError, '当前网关'):
            access.check_entry(preferences, instance_id='actual')

    def test_runtime_uses_private_listener_and_closes_owned_entry(self):
        import run
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            config = Path(folder)/'config.json'
            config.write_text(json.dumps({'auth': {'mode': 'password'}, 'origins': []}))
            main, private = MagicMock(), MagicMock()
            private.server_address = ('127.0.0.1', 45678)
            with patch.object(run.sys, 'argv', ['run.py', '--config', str(config)]), \
                 patch.object(run.sys, 'stdout', MagicMock()), patch.object(run, 'Bridge'), \
                 patch.object(run, 'Notifications'), patch.object(run, 'AddressNotifications'), \
                 patch.object(run, 'GatewayControl'), patch.object(run.signal, 'signal'), \
                 patch.object(run, 'GatewayServer', side_effect=[main, private]) as listeners, \
                 patch('bridge.features.network.discovery.ConnectionDiscovery') as discovery, \
                 patch('bridge.features.network.shared_relay.start', return_value=None), \
                 patch.object(tailscale, 'TailscaleTunnel') as tunnel:
                run.main(connections=[ENTRY])
            self.assertEqual(listeners.call_args_list[1].args[0], ('127.0.0.1', 0))
            self.assertIs(listeners.call_args_list[1].kwargs['shared'], main)
            self.assertEqual(tunnel.call_args.args[1], 45678)
            self.assertIs(private.connection_discovery, discovery.return_value)
            self.assertIs(private.notifications, main.notifications)
            tunnel.return_value.start.assert_called_once()
            tunnel.return_value.close.assert_called_once()
            private.shutdown.assert_called_once()
            private.server_close.assert_called_once()

    def test_shadowed_status_requires_a_live_private_control_response(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            desktop = Desktop(folder)
            record = {'pid': 42, 'instanceId': 'owner', 'notificationManagement': True}
            with patch('bridge.app.desktop.http.client.HTTPConnection', side_effect=OSError()), \
                 patch('bridge.app.desktop.read_record', return_value=record), \
                 patch('bridge.app.desktop.request_pairing', return_value={'states': {}}) as control:
                state = desktop.status()
                self.assertTrue(state['running'])
                self.assertEqual(state['instanceId'], 'owner')
                control.side_effect = ValueError('not alive')
                self.assertFalse(desktop.status()['running'])


if __name__ == '__main__':
    unittest.main()
