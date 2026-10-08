import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.desktop import Desktop


ROOT = Path(__file__).resolve().parents[1]


class OfflineManagement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'codex'
        self.home.mkdir()
        (self.root/'desktop.json').write_text(json.dumps({
            'codexHome': str(self.home), 'codexBin': str(self.root/'absent-runtime'),
            'ipcPath': str(self.root/'absent-ipc'), 'lan': False, 'connections': []}))
        self.desktop = Desktop(self.root)
        self.addCleanup(self.desktop.close_local)
        self.discovered = {name: {'id': name, 'installed': False, 'dataDirectory': str(self.root/name)}
                           for name in ('codex', 'claude', 'deepseek')}
        discovery = patch('bridge.integrations.discovery.discover_clients', return_value=self.discovered)
        self.scan = discovery.start()
        self.addCleanup(discovery.stop)

    def add_api(self):
        return self.desktop.accounts({'action': 'addApi', 'name': 'Local fixture API',
                                     'baseUrl': 'https://upstream.invalid/v1',
                                     'apiKey': 'private-fixture-key', 'model': 'fixture-model'})

    def test_add_and_list_work_without_gateway_or_network_listener_and_survive_reopen(self):
        with patch('socket.socket.bind', side_effect=AssertionError('offline setup opened a listener')), \
                patch('bridge.desktop.subprocess.Popen', side_effect=AssertionError('offline setup launched a child')):
            result = self.add_api()
            identifier = result['accounts'][0]['id']
            first_owner = self.desktop.local_bridge
            self.assertEqual(self.desktop.accounts({'action': 'list'})['accounts'][0]['id'], identifier)
            self.assertIs(self.desktop.local_bridge, first_owner)
            self.assertNotIn('private-fixture-key', json.dumps(result))
            self.assertFalse((self.root/'gateway-control.json').exists())
            self.desktop.close_local()
            self.assertEqual(self.desktop.accounts({'action': 'list'})['accounts'][0]['id'], identifier)
            self.assertIsNot(self.desktop.local_bridge, first_owner)
            saved = json.loads((self.root/'accounts'/identifier/'api.json').read_text())
            self.assertEqual(saved['key'], 'private-fixture-key')

    def test_discovery_while_stopped_reuses_local_owner(self):
        self.add_api()
        owner = self.desktop.local_bridge
        result = self.desktop.desktop_sessions({'action': 'scan'})
        self.assertEqual({row['id'] for row in result['clients']}, {'codex', 'claude', 'deepseek'})
        self.assertTrue(all(not row['installed'] for row in result['clients']))
        self.assertIs(self.desktop.local_bridge, owner)
        self.assertTrue((self.root/'desktop-sessions/settings.json').exists())
        self.assertFalse((self.root/'gateway-control.json').exists())

    def test_private_stdio_stream_handles_sequential_requests_without_public_gateway(self):
        requests = [
            {'action': 'accounts', 'payload': {'action': 'addApi', 'name': 'Stream fixture',
                'baseUrl': 'https://upstream.invalid/v1', 'apiKey': 'private-stream-key', 'model': 'fixture'}},
            {'action': 'accounts', 'payload': {'action': 'list'}},
        ]
        result = subprocess.run([sys.executable, '-B', str(ROOT/'desktop.py'), 'management-stream',
                                 '--data-dir', str(self.root)],
                                input=''.join(json.dumps(request)+'\n' for request in requests),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(response['ok'] for response in responses), responses)
        self.assertEqual(responses[0]['result']['accounts'], responses[1]['result']['accounts'])
        self.assertNotIn('private-stream-key', result.stdout)
        self.assertFalse((self.root/'gateway-control.json').exists())

    def test_live_gateway_takes_ownership_and_private_commands_are_forwarded(self):
        self.add_api()
        owner = self.desktop.local_bridge
        (self.root/'gateway-control.json').write_text(json.dumps({'accountsManagement': True,
                                                                 'desktopSessionsManagement': True}))
        with patch.object(self.desktop, 'status', return_value={'running': True}), \
                patch('bridge.desktop.request_pairing', return_value={'fromGateway': True}) as forward, \
                patch.object(self.desktop, 'local_services', side_effect=AssertionError('duplicate owner')):
            self.assertEqual(self.desktop.accounts({'action': 'list'}), {'fromGateway': True})
            forward.assert_called_once_with(self.root, {'action': 'accounts', 'value': {'action': 'list'}}, timeout=100)
            self.assertTrue(owner.closed.is_set())
            self.assertIsNone(self.desktop.local_bridge)
            self.desktop.desktop_sessions({'action': 'clients'})
            self.assertEqual(forward.call_args.args[1]['action'], 'desktop-sessions')

    def test_stale_gateway_record_does_not_create_second_owner(self):
        (self.root/'gateway-control.json').write_text('{"accountsManagement":true}')
        with patch.object(self.desktop, 'status', return_value={'running': False}), \
                patch.object(self.desktop, 'local_services') as create:
            with self.assertRaisesRegex(ValueError, '状态暂不可用'):
                self.desktop.accounts({'action': 'list'})
            create.assert_not_called()

    def test_start_preserves_owner_and_blocks_during_login_or_switch(self):
        self.add_api()
        owner = self.desktop.local_bridge
        with patch.object(self.desktop, 'status', return_value={'running': False}), \
                patch('bridge.desktop.subprocess.Popen') as launch:
            owner.accounts.enrollment = {'phase': 'waiting'}
            with self.assertRaisesRegex(ValueError, '登录'):
                self.desktop.start()
            self.assertIs(self.desktop.local_bridge, owner)
            self.assertFalse(owner.closed.is_set())
            owner.accounts.enrollment = None
            owner.accounts.state['phase'] = 'applying'
            with self.assertRaisesRegex(ValueError, '切换'):
                self.desktop.start()
            self.assertIs(self.desktop.local_bridge, owner)
            launch.assert_not_called()
        owner.accounts.state['phase'] = 'idle'

    def test_async_login_state_lives_across_management_requests(self):
        entered, release = threading.Event(), threading.Event()
        rpc = Mock()
        rpc.__enter__ = Mock(return_value=rpc)
        rpc.__exit__ = Mock(return_value=False)
        rpc.request.return_value = {'authUrl': 'https://auth.openai.com/fixture', 'loginId': 'fixture'}
        def finish(cancel):
            entered.set()
            release.wait(3)
            return False
        rpc.login_finished.side_effect = finish
        with patch('bridge.accounts.ManagedRPC', return_value=rpc):
            self.desktop.accounts({'action': 'login', 'name': 'Official fixture'})
            self.assertTrue(entered.wait(2))
            owner = self.desktop.local_bridge
            try:
                result = self.desktop.accounts({'action': 'list'})
                self.assertEqual(result['enrollment']['phase'], 'waiting')
                self.assertIs(self.desktop.local_bridge, owner)
                self.desktop.accounts({'action': 'cancelLogin'})
                self.assertTrue(owner.accounts.login_cancel.is_set())
            finally:
                release.set()
            end = time.monotonic() + 2
            while owner.accounts.enrollment['phase'] != 'failed' and time.monotonic() < end:
                time.sleep(.01)
            self.assertEqual(self.desktop.accounts({'action': 'list'})['enrollment']['phase'], 'failed')


if __name__ == '__main__':
    unittest.main()
