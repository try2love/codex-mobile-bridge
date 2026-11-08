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
OWNED_CONFIG = {
    'TCP': {'443': {'HTTPS': True}},
    'Web': {'computer.example.ts.net:443': {
        'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8787'}}}},
    'AllowFunnel': {'computer.example.ts.net:443': True},
}


class TailscaleTests(unittest.TestCase):
    def test_running_route_requires_consecutive_conflicts_before_blocking(self):
        for owned in (False, True):
            with self.subTest(owned=owned), tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
                tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
                process = MagicMock() if owned else None
                if process:
                    process.poll.return_value = None
                tunnel.ready.set()
                states = ['other', 'other', 'same', 'other', ValueError('incomplete'),
                          'other', 'other', 'other']
                with patch.object(tunnel, '_wait_for_route_check') as wait, \
                     patch.object(tunnel, '_route_state', side_effect=states) as read:
                    result = (tunnel._monitor_process(process, '/fixture') if owned
                              else tunnel._monitor_adopted('/fixture'))
                self.assertEqual(result, 'blocked')
                self.assertEqual(read.call_count, len(states))
                self.assertEqual(tunnel.last_status['state'], 'blocked')

    def test_stable_health_is_sparse_and_suspect_health_is_prompt(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            with patch.object(tunnel, '_route_state', side_effect=['same', 'other', 'same', 'none']), \
                 patch.object(tunnel, '_wait_for_route_check') as wait:
                self.assertEqual(tunnel._monitor_adopted('/fixture'), 'retry')
            self.assertEqual([call.args[0] for call in wait.call_args_list], [60, 60, 3, 60])

    def test_successful_foreground_connections_reset_retry_delay(self):
        class Reader:
            def __init__(self, target, args, **kwargs):
                self.target, self.args = target, args
            def start(self):
                self.target(*self.args)
            def join(self, **kwargs):
                pass
        class RetryWait:
            def __init__(self):
                self.delays = []
            def clear(self):
                pass
            def wait(self, delay):
                self.delays.append(delay)
                if len(self.delays) == 4:
                    tunnel.stopped.set()
                    return True
                return False
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            retry = RetryWait()
            tunnel.retry_wake = retry
            def spawn(*args, **kwargs):
                process = MagicMock()
                process.stdout = io.StringIO('Available within your tailnet\n')
                return process
            with patch.object(tailscale, 'require_identity'), \
                 patch.object(tailscale, 'executable', return_value='/fixture'), \
                 patch.object(tailscale, 'command_json', return_value={}), \
                 patch.object(tailscale.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(tailscale.threading, 'Thread', Reader), \
                 patch.object(tunnel, '_terminate'), \
                 patch.object(tunnel, '_monitor_process', return_value='retry'), \
                 patch.object(tunnel.stopped, 'wait', side_effect=retry.wait):
                tunnel._run()
            self.assertEqual(retry.delays, [3, 3, 3, 3])

    def test_network_change_interrupts_retry_wait(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            tunnel.network_changed()
            self.assertTrue(tunnel.retry_wake.wait(0))
            self.assertTrue(tunnel.wake.is_set())

    def test_failed_launches_back_off_until_network_change(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            delays = []
            def wait(delay):
                delays.append(delay)
                if len(delays) == 7:
                    tunnel.stopped.set()
            with patch.object(tailscale, 'require_identity', side_effect=ValueError('offline')), \
                 patch.object(tunnel.retry_wake, 'wait', side_effect=wait), \
                 patch.object(tailscale.subprocess, 'Popen') as spawn:
                tunnel._run()
            self.assertEqual(delays, [3, 6, 12, 24, 48, 60, 60])
            spawn.assert_not_called()

    def test_network_change_wakes_running_monitor_without_waiting_a_minute(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            waiting = threading.Event()
            original_wait = tunnel._wait_for_route_check
            def wait(interval):
                waiting.set()
                original_wait(interval)
            results = []
            with patch.object(tunnel, '_wait_for_route_check', side_effect=wait), \
                 patch.object(tunnel, '_route_state', return_value='none') as read:
                worker = threading.Thread(target=lambda: results.append(tunnel._monitor_adopted('/fixture')))
                worker.start()
                try:
                    self.assertTrue(waiting.wait(2))
                    read.assert_not_called()
                    tunnel.network_changed()
                    worker.join(2)
                    self.assertEqual(results, ['retry'])
                    read.assert_called_once_with('/fixture')
                finally:
                    tunnel.close()
                    worker.join(2)

    def test_health_read_failures_require_three_consecutive_checks(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            states = [ValueError('offline'), 'same'] + [ValueError('offline')] * 3
            with patch.object(tunnel, '_route_state', side_effect=states) as read, \
                 patch.object(tunnel, '_wait_for_route_check'):
                self.assertEqual(tunnel._monitor_adopted('/fixture'), 'retry')
            self.assertEqual(read.call_count, 5)

    def test_foreground_waits_for_authorization_without_polling_or_restarting(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            process = MagicMock()
            process.poll.return_value = None
            def wait(interval):
                self.assertIsNone(interval)
                tunnel.stopped.set()
            with patch.object(tunnel, '_wait_for_route_check', side_effect=wait) as waiting, \
                 patch.object(tunnel, '_route_state') as read:
                self.assertEqual(tunnel._monitor_process(process, '/fixture'), 'stopped')
            waiting.assert_called_once_with(None)
            read.assert_not_called()

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

    def test_matching_background_route_is_adopted_but_other_targets_are_not(self):
        self.assertEqual(tailscale.route_state(OWNED_CONFIG, ENTRY, 8787), 'same')
        different_target = json.loads(json.dumps(OWNED_CONFIG))
        different_target['Web']['computer.example.ts.net:443']['Handlers']['/']['Proxy'] = 'http://127.0.0.1:9999'
        self.assertEqual(tailscale.route_state(different_target, ENTRY, 8787), 'other')
        self.assertEqual(tailscale.route_state(
            different_target, ENTRY, 8787,
            target_validator=lambda value: value == 'http://127.0.0.1:9999'), 'same')
        private_rule = json.loads(json.dumps(OWNED_CONFIG))
        private_rule['AllowFunnel']['computer.example.ts.net:443'] = False
        self.assertEqual(tailscale.route_state(private_rule, ENTRY, 8787), 'other')
        private_entry = {**ENTRY, 'tailscaleMode': 'serve'}
        self.assertEqual(tailscale.route_state(private_rule, private_entry, 8787), 'same')
        self.assertEqual(tailscale.route_state(OWNED_CONFIG, private_entry, 8787), 'other')

    def test_matching_route_is_monitored_without_a_foreground_child(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            tunnel = tailscale.TailscaleTunnel(ENTRY, 8787, folder)
            connected = threading.Event()
            original = tunnel.status
            def status(state, message, auth_url=''):
                original(state, message, auth_url)
                if state == 'connected' and '接管' in message:
                    connected.set()
            tunnel.status = status
            with patch.object(tailscale, 'require_identity'), \
                 patch.object(tailscale, 'executable', return_value='/tailscale'), \
                 patch.object(tailscale, 'command_json', return_value=OWNED_CONFIG), \
                 patch.object(tailscale.subprocess, 'Popen') as spawn:
                try:
                    tunnel.start()
                    self.assertTrue(connected.wait(2))
                    spawn.assert_not_called()
                finally:
                    tunnel.close()
            self.assertFalse(tunnel.thread.is_alive())
            self.assertEqual(tunnel.last_status['state'], 'stopped')

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
