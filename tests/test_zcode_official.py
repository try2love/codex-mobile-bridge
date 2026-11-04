import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions
from bridge.clients.zcode.official import ZCodeOfficial


ROOT = Path(__file__).resolve().parents[1]


class OfficialZCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        self.project.mkdir()
        self.owner = {'epoch': 'connection-one', 'device': 'selected-desktop'}
        self.binding = {'clientId': 'official-browser', 'connectionId': 'remote-handshake',
                        'runtime': {'identity': 'original-workspace-runtime', 'workspaceKey': str(self.project)}}
        self.snapshot = {'sessionId': 'sess_test', 'revision': 3, 'seq': 4, 'logEpoch': 'log-one',
                         'config': {'provider': 'account:official', 'model': 'Flash', 'thought': 'low'},
                         'control': {'activeWorks': []}, 'pendingInteractions': [],
                         'rows': {'totalCount': 0, 'window': []}, 'inputRouting': {'mode': 'startNow'}}
        self.client = Mock()
        self.client.owner.side_effect = lambda: dict(self.owner) if self.owner else None
        self.client.status.side_effect = lambda: {'configured': True, 'connected': bool(self.owner),
                                                  'owner': dict(self.owner) if self.owner else None}
        self.client.call.side_effect = self.native
        self.adapter = ZCodeOfficial(self.root, {'dataDirectory': self.root/'native'}, self.client)

    def native(self, request):
        if request['action'] == 'rename':
            return {'status': 'confirmed', 'scope': 'native',
                    'result': {'id': request['sessionId'], 'title': request['body']['title']}}
        return {'application': {'ready': True, 'canCreate': True},
                'target': {'workspace': str(self.project)},
                'attach': {'binding': self.binding, 'subscriptionId': 'original-session-subscription'},
                'detail': {'snapshot': self.snapshot},
                'send': {'status': 'accepted'},
                'account': {'provider': 'official-account'},
                'list': {'sessions': [{'id': 'sess_test', 'cwd': str(self.project), 'nativeRename': True}]}}[request['action']]

    def connect_session(self):
        self.adapter.connect_application()
        self.adapter.call('resume', 'sess_test', {'id': str(uuid.uuid4())})

    def test_passive_status_does_not_connect_or_activate_a_session(self):
        self.assertFalse(self.adapter.status()['sessionAccess'])
        self.assertFalse(self.adapter.inventory_status()['connected'])
        with self.assertRaises(BridgeUnavailable):
            self.adapter.call('list')
        self.client.connect.assert_not_called()
        self.client.call.assert_not_called()
        self.assertIsNone(self.adapter.pipe)
        self.assertIsNone(self.adapter.port)
        self.assertIsNone(self.adapter._window_app())

    def test_application_connection_lists_and_reads_account_without_acquiring_chat(self):
        self.adapter.connect_application()
        self.assertTrue(self.adapter.inventory_status()['sessionAccess'])
        self.assertTrue(self.adapter.inventory_status()['connected'])
        self.assertFalse(self.adapter.inventory_status()['retryable'])
        self.assertTrue(self.adapter.inventory_status()['canCreate'])
        self.assertEqual(self.adapter.call('list')['sessions'][0]['id'], 'sess_test')
        self.assertEqual(self.adapter.call('account')['provider'], 'official-account')
        self.assertEqual([c.args[0]['action'] for c in self.client.call.call_args_list], ['application', 'list', 'account'])
        self.assertEqual(self.adapter.application_owner, self.owner)
        self.assertNotIn('pid', self.adapter.application_owner)

    def test_cold_application_connect_grants_only_device_startup_wait(self):
        self.adapter.connect_application(wait=True)
        self.client.close.assert_called_once()
        self.client.connect.assert_called_once_with(wait=True)
        self.assertEqual([c.args[0]['action'] for c in self.client.call.call_args_list], ['application'])

    def test_shared_attach_projection_and_file_send_use_the_selected_epoch(self):
        self.connect_session()
        detail = self.adapter.call('detail', 'sess_test')
        self.assertEqual(detail['session']['model'], 'account:official/Flash')
        self.assertEqual(detail['session']['effort'], 'low')
        self.assertEqual(detail['session']['connectionState'], 'ready')
        (self.project/'file.md').write_text('native attachment')
        rid = str(uuid.uuid4())
        result = self.adapter.call('send', 'sess_test', {'id': rid, 'text': 'unchanged user message',
                                                       'projectFiles': ['file.md']})
        self.assertEqual(result['status'], 'accepted')
        request = self.client.call.call_args.args[0]
        self.assertEqual(request['expectedProcess'], self.owner)
        self.assertEqual(request['binding'], self.binding)
        self.assertEqual(request['body']['text'], 'unchanged user message')
        self.assertEqual(request['body']['files'][0]['name'], 'file.md')
        self.assertTrue(request['expiresAt'] > 0)

    def test_native_rename_uses_application_identity_without_acquiring_a_conversation(self):
        self.adapter.connect_application()
        self.assertTrue(self.adapter.call('list')['sessions'][0]['capabilities']['rename'])
        self.client.call.reset_mock()
        rid = str(uuid.uuid4())
        result = self.adapter.call('rename', 'sess_test', {'id': rid, 'title': ' 原生标题 '})
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['result'], {'id': 'sess_test', 'title': '原生标题'})
        self.assertEqual([call.args[0]['action'] for call in self.client.call.call_args_list], ['target', 'rename'])
        request = self.client.call.call_args.args[0]
        self.assertEqual(request['expectedProcess'], self.owner)
        self.assertEqual(request['body']['id'], rid)
        self.assertIsNone(self.adapter.sid)
        self.assertIsNone(self.adapter.binding)

    def test_changed_relay_or_device_blocks_old_binding_before_dispatch(self):
        self.connect_session()
        self.client.call.reset_mock()
        original = dict(self.owner)
        for owner in ({**original, 'epoch': 'another-connection'},
                      {**original, 'device': 'another-desktop'}, None):
            self.owner = owner
            self.assertFalse(self.adapter.status()['sessionAccess'])
            with self.assertRaises(BridgeUnavailable):
                self.adapter.call('send', 'sess_test', {'id': str(uuid.uuid4()), 'text': 'do not dispatch'})
        self.client.call.assert_not_called()

    def test_explicit_reconnect_renews_same_session_without_resending_input(self):
        self.connect_session()
        self.owner = {**self.owner, 'epoch': 'connection-two'}
        self.binding = {**self.binding, 'connectionId': 'new-handshake'}
        self.client.call.reset_mock()
        self.assertTrue(self.adapter.reconnect_existing())
        self.assertEqual(self.adapter.sid, 'sess_test')
        self.assertEqual(self.adapter.binding['connectionId'], 'new-handshake')
        self.assertEqual([c.args[0]['action'] for c in self.client.call.call_args_list], ['application', 'target', 'attach'])

    def test_disconnect_only_closes_relay_and_rebind_does_not_disconnect(self):
        self.connect_session()
        self.adapter.release_window()
        self.client.close.assert_not_called()
        self.client.call.reset_mock()
        with patch('bridge.clients.zcode.adapter.listener_identity', side_effect=AssertionError('no CDP')):
            self.adapter.disconnect()
        self.client.close.assert_called_once()
        self.client.call.assert_not_called()
        self.assertIsNone(self.adapter.process)
        self.assertIsNone(self.adapter.sid)
        self.assertEqual(self.adapter.bindings, {})

    def test_local_renderer_credentials_cannot_be_reused_as_relay_binding(self):
        adapter = ZCodeOfficial(self.root, {'dataDirectory': self.root/'native', 'sessionId': 'sess_old',
                                'workspace': str(self.project), 'binding': self.binding,
                                'process': {'pid': 123}, 'debugFallbackPort': 17742}, self.client)
        self.assertIsNone(adapter.sid)
        self.assertIsNone(adapter.process)
        self.assertIsNone(adapter.binding)
        self.assertIsNone(adapter.port)
        self.client.call.assert_not_called()

    def test_shared_unknown_receipt_prevents_replay_after_official_reconnect(self):
        self.connect_session()
        manager = DesktopSessions(self.root/'state')
        self.addCleanup(manager.close)
        manager.adapters['zcode'] = self.adapter
        manager.config['enabled'] = {'zcode': True}
        manager.locks['zcode'] = threading.RLock()
        for mapping in (manager.read_epochs, manager.read_changes, manager.binding_epochs, manager.binding_changes):
            mapping['zcode'] = 0
        self.client.call.side_effect = TimeoutError('accepted, response lost')
        self.client.call.reset_mock()
        body = {'id': str(uuid.uuid4()), 'text': 'exactly once'}
        result = manager.call('zcode', 'send', 'sess_test', body)
        self.assertEqual(result['status'], 'unknown')
        self.client.call.side_effect = self.native
        self.assertTrue(self.adapter.reconnect_existing())
        self.client.call.reset_mock()
        self.assertEqual(manager.call('zcode', 'send', 'sess_test', body), result)
        self.client.call.assert_not_called()


class OfficialManagerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temp.cleanup)
        self.manager = DesktopSessions(Path(temp.name))
        self.addCleanup(self.manager.close)
        self.manager.adapters['zcode'] = Mock()
        self.manager.locks['zcode'] = threading.RLock()
        self.manager.pending_resumes['zcode'] = {}
        for mapping in (self.manager.read_epochs, self.manager.read_changes,
                        self.manager.binding_epochs, self.manager.binding_changes):
            mapping['zcode'] = 0
        self.manager.config['discovered'] = {'zcode': {'installed': True, 'application': '/Fixture/ZCode.app',
                                                       'dataDirectory': str(Path(temp.name)/'native')}}

    def test_selecting_official_reuses_adapter_and_never_launches_native_app(self):
        m = self.manager
        with patch('bridge.clients.zcode.official.ZCodeOfficial') as factory, patch.object(m, 'clients', return_value={'clients': [{'id':'zcode','selectable':True}]}), patch('bridge.clients.lifecycle.launch_client') as launch, patch.object(m, '_zcode_official_status', return_value={'configured': True}):
            m.control({'action': 'zcode-official', 'enabled': True})
            self.assertEqual(m.config['zcodeConnectionMode'], 'official-remote')
            self.assertNotIn('zcodeOfficialRemote', m.config)
            self.assertTrue(m.config['enabled']['zcode'])
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[1]['dataDirectory'], m.config['discovered']['zcode']['dataDirectory'])
            self.assertEqual(str(m.zcode_pipe.application), '/Fixture/ZCode.app')
            launch.assert_not_called()
            factory.return_value.config = factory.call_args.args[1]
            m._configure_zcode()
            factory.assert_called_once()

    def test_unconfigured_remote_selection_preserves_previous_adapter_and_mode(self):
        m = self.manager
        previous = m.adapters['zcode']
        with patch.object(m, '_zcode_official_status', return_value={'configured': False}):
            with self.assertRaisesRegex(ValueError, '配置并登录'):
                m.toggle_client({'provider': 'zcode', 'enabled': True, 'connectionMode': 'official-remote'})
        self.assertIs(m.adapters['zcode'], previous)
        previous.disconnect.assert_not_called()
        previous.release_window.assert_not_called()
        self.assertNotIn('zcodeConnectionMode', m.config)

    def test_switch_to_local_retains_saved_official_auth_and_does_not_stop_app(self):
        m = self.manager
        m.config['zcodeOfficialRemote'] = True
        m.config['zcodeCompatibility'] = True
        m.zcode_pipe = Mock()
        m.zcode_pipe.supported.return_value = True
        previous = m.adapters['zcode']
        with patch.object(m, '_configure_zcode') as configure, patch.object(m, '_stop_client') as stop:
            m._select_zcode_connection('private-pipe')
        previous.disconnect.assert_called_once()
        configure.assert_called_once()
        stop.assert_not_called()
        self.assertEqual(m.config['zcodeConnectionMode'], 'private-pipe')
        self.assertNotIn('zcodeCompatibility', m.config)

    def test_public_connection_modes_do_not_contain_remote_credentials_or_link(self):
        from bridge.clients.zcode.connections import connection_modes
        value = connection_modes({'zcodeOfficialRemote': True}, installed=True, pipe_supported=True,
                                 official={'configured': True, 'connected': True, 'jwt': 'secret', 'link': 'private-link'}, official_supported=True)
        self.assertEqual(value['connectionMode'], 'official-remote')
        self.assertTrue(all(row['available'] for row in value['connectionModes']))
        self.assertTrue(value['connectionModes'][1]['connected'])
        self.assertNotIn('secret', str(value))
        self.assertNotIn('private-link', str(value))

    def test_phone_disconnect_closes_only_relay_not_original_app(self):
        m = self.manager
        m.config['zcodeOfficialRemote'] = True
        adapter = m.adapters['zcode']
        with patch.object(m, 'clients', return_value={'clients': []}), patch.object(m, '_stop_client') as stop:
            m.toggle_client({'provider': 'zcode', 'enabled': False, 'quitDesktop': False})
        adapter.disconnect.assert_called_once()
        stop.assert_not_called()
        self.assertFalse(m.config['enabled']['zcode'])

    def test_official_mode_cold_starts_original_app_without_private_transport(self):
        m = self.manager
        m.gateway_running = True
        m.config['zcodeConnectionMode'] = 'official-remote'
        adapter = m.adapters['zcode']
        descriptor = {'id': 'zcode', 'officialRemote': True, 'debugFallbackPort': None}
        rows = {'clients': [{'id': 'zcode', 'selectable': True}]}
        with patch.object(m, 'clients', return_value=rows), patch.object(m, '_descriptor', return_value=descriptor), \
                patch('bridge.clients.lifecycle.inspect_client', return_value={'running': False, 'unknown': False, 'mainPids': []}), \
                patch('bridge.clients.lifecycle.launch_client', return_value={'running': True, 'launched': True}) as launch, \
                patch.object(m, '_start_zcode_connection') as pipe:
            m.toggle_client({'provider': 'zcode', 'enabled': True})
        launch.assert_called_once_with(descriptor)
        adapter.connect_application.assert_called_once_with(None, wait=True)
        pipe.assert_not_called()
        self.assertTrue(m.config['enabled']['zcode'])

    def test_official_mode_preserves_a_running_original_app(self):
        m = self.manager
        m.gateway_running = True
        m.config['zcodeConnectionMode'] = 'official-remote'
        with patch.object(m, 'clients', return_value={'clients': [{'id': 'zcode', 'selectable': True}]}), \
                patch.object(m, '_descriptor', return_value={'id': 'zcode', 'officialRemote': True}), \
                patch('bridge.clients.lifecycle.inspect_client', return_value={'running': True, 'unknown': False, 'mainPids': [77]}), \
                patch('bridge.clients.lifecycle.launch_client') as launch:
            m.toggle_client({'provider': 'zcode', 'enabled': True})
        launch.assert_not_called()
        m.adapters['zcode'].connect_application.assert_called_once_with(None, wait=False)


if __name__ == '__main__':
    unittest.main()
