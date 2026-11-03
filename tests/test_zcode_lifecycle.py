"""Exercise shared gateway/phone operations without activating a user's desktop."""
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.discovery import discover_clients
from bridge.clients.manager import DesktopSessions
from bridge.clients.zcode.adapter import ZCodeDesktop


class ZCodeLifecycleTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1]/'.tmp'
        scratch.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        bundle = self.root/'Apps/ZCode.app/Contents'
        (bundle/'MacOS').mkdir(parents=True)
        (bundle/'MacOS/ZCode').touch()
        (bundle/'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'dev.zcode.app', 'CFBundleExecutable': 'ZCode',
            'CFBundleShortVersionString': '3.17.2'}))
        row = discover_clients(home=self.root, platform='darwin', env={}, applications=[bundle.parent.parent])['zcode']
        self.assertEqual(row['application'], str(bundle.parent))
        settings = self.root/'desktop-sessions/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'discovered': {'zcode': row}}))
        self.platform = patch('bridge.clients.manager.sys.platform', 'darwin')
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.manager = DesktopSessions(self.root, gateway_running=True)
        self.addCleanup(self.manager.close)
        self.adapter = self.manager.adapters['zcode']
        self.assertIs(type(self.adapter), ZCodeDesktop)
        self.state = {'running': True, 'mainPids': [77], 'pids': [77], 'runtimePids': [], 'unknown': False}
        for name, value in [('inspect_clients', {'zcode': self.state}), ('inspect_client', self.state)]:
            p = patch('bridge.clients.lifecycle.'+name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def toggle(self, enabled, **options):
        return self.manager.toggle_client({'provider': 'zcode', 'enabled': enabled, **options})

    def test_gateway_starts_private_pipe_by_default_and_never_opens_a_port(self):
        from bridge.clients.zcode.pipe_host import PipeHostClient
        process = {'pid': 77, 'birth': [1, 2], 'started': [3, 4]}
        row = self.manager.config['discovered']['zcode']
        fake = Mock(spec=PipeHostClient)
        fake.application = row['application']
        fake.supported.return_value = True
        fake.call.return_value = {'ready': True}
        fake.start.return_value = process
        fake.owner.side_effect = lambda: process if fake.start.called else None
        self.manager.zcode_pipe = fake
        self.manager._configure_zcode()
        empty = {**self.state, 'running': False, 'pids': [], 'mainPids': []}
        with patch('bridge.clients.lifecycle.inspect_client', side_effect=[empty, self.state]), \
                patch('bridge.clients.lifecycle.launch_client') as ordinary, \
                patch('bridge.clients.zcode.adapter.listener_identity') as port:
            status = self.toggle(True)
        client = next(r for r in status['clients'] if r['id'] == 'zcode')
        self.assertTrue(client['pipeReady'] and client['pipeSupported'])
        self.assertFalse(client['compatibilityLaunch'])
        fake.start.assert_called_once_with()
        self.assertTrue(self.manager.adapters['zcode'].config['privatePipe'])
        ordinary.assert_not_called()
        port.assert_not_called()

    def test_pipe_error_or_unknown_tasks_do_not_authorize_compatibility_or_relaunch(self):
        from bridge.clients.zcode.pipe_host import PipeHostClient
        fake = Mock(spec=PipeHostClient)
        fake.application = self.manager.config['discovered']['zcode']['application']
        fake.supported.return_value = True
        fake.owner.return_value = None
        self.manager.zcode_pipe = fake
        self.manager._configure_zcode()
        with patch('bridge.clients.lifecycle.stop_client') as stop, \
                patch('bridge.clients.zcode.adapter.listener_identity') as port:
            with self.assertRaisesRegex(ValueError, '无法确认任务状态'):
                self.toggle(True, initializeDesktop=True, connectionMode='private-pipe')
            fake.owner.side_effect = PermissionError('identity mismatch')
            with self.assertRaises(PermissionError):
                self.toggle(True, initializeDesktop=True, connectionMode='private-pipe', acknowledgeUnavailable=True)
            fake.start.assert_not_called()
            stop.assert_not_called()
            port.assert_not_called()
            self.assertNotIn('zcodeCompatibility', self.manager.config)

    def test_saved_prototype_only_offers_retry_for_a_verified_reachable_owner(self):
        installation = self.manager.config['discovered']['zcode']
        home = Path(installation['dataDirectory'])
        home.mkdir(parents=True, exist_ok=True)
        (home / 'tasks-index.sqlite').touch()
        self.manager.config['zcodePrototype'] = {'application': installation['application'],
            'dataDirectory': str(home), 'workspace': str(self.root), 'sessionId': 'sess_test',
            'port': 17742, 'process': {'pid': 1}, 'binding': {'owner': 'retired'}}
        self.manager._configure_zcode()
        self.manager.config.setdefault('enabled', {})['zcode'] = True
        def row():
            return next(x for x in self.manager.clients(refresh=True)['clients'] if x['id'] == 'zcode')
        with patch('bridge.clients.zcode.adapter.listener_identity', side_effect=ValueError('no listener')) as listener:
            current = row()
            self.assertTrue(current['running'] and current['historyAvailable'])
            self.assertFalse(current['connected'] or current['retryable'])
            self.assertEqual(current['setupStatus'], 'history-only')
            listener.assert_called_once()
            with patch('bridge.clients.zcode.adapter.installation', return_value={'version': '3.18.0', 'executable': 'test'}):
                self.assertFalse(row()['retryable'])
                self.assertEqual(listener.call_count, 1)
            with patch('bridge.clients.zcode.adapter.installation', return_value={'version': '3.17.1', 'executable': 'test'}):
                listener.side_effect = ValueError('no verified listener')
                self.assertFalse(row()['retryable'])
                listener.side_effect = None
                listener.return_value = {'pid': 2}
                expired = row()
                self.assertTrue(expired['retryable'])
                self.assertFalse(expired['connected'])

    def test_initialization_requires_consent_restarts_normally_and_verifies_the_native_app(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        owner = {'pid': 82, 'birth': [3, 4]}

        def stop(descriptor, *, state):
            self.assertIs(state, self.state)
            self.state.update(running=False, mainPids=[], pids=[])

        def launch(descriptor):
            self.assertEqual(descriptor['debugFallbackPort'], 17742)
            self.state.update(running=True, mainPids=[82], pids=[82])
            return {'running': True, 'launched': True}

        with patch.object(ZCodePrototype, 'compatibility_owner', return_value=None), \
                patch('bridge.clients.lifecycle.stop_client', side_effect=stop) as quit_app, \
                patch('bridge.clients.lifecycle.launch_client', side_effect=launch) as start, \
                patch.object(ZCodePrototype, '_live_process', return_value=owner), \
                patch('bridge.clients.zcode.transport.request_native', return_value={'ready': True}) as native:
            with self.assertRaisesRegex(ValueError, '无法确认任务状态'):
                self.toggle(True, initializeDesktop=True)
            quit_app.assert_not_called()
            start.assert_not_called()
            self.assertNotIn('zcodeCompatibility', self.manager.config)
            value = self.toggle(True, initializeDesktop=True, acknowledgeUnavailable=True)
            row = next(row for row in value['clients'] if row['id'] == 'zcode')
            self.assertTrue(row['compatibilityLaunch'])
            self.assertIs(json.loads(self.manager.config_path.read_text())['zcodeCompatibility'], True)
            self.assertEqual(row['setupStatus'], 'session-access')
            self.assertFalse(row['connected'] or row['canInitializeDesktop'])
            self.assertEqual([call.args[0]['action'] for call in native.call_args_list], ['application'])
            self.assertIsNone(self.manager.adapters['zcode'].sid)
            quit_app.assert_called_once()
            start.assert_called_once()

    def test_failed_or_occupied_connection_does_not_persist_or_restart_a_verified_live_owner(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        with patch('bridge.clients.lifecycle.stop_client') as stop, \
                patch('bridge.clients.lifecycle.launch_client') as start, \
                patch.object(ZCodePrototype, 'compatibility_owner') as port, \
                patch.object(ZCodePrototype, '_live_process', return_value={'pid': 77}), \
                patch('bridge.clients.zcode.transport.request_native', return_value={}) as native:
            port.side_effect = ValueError('端口已被占用')
            with self.assertRaisesRegex(ValueError, '占用'):
                self.toggle(True, initializeDesktop=True, acknowledgeUnavailable=True)
            native.assert_not_called()
            port.side_effect = None
            port.return_value = {'pid': 77}
            with self.assertRaisesRegex(ValueError, '原 App 未确认连接'):
                self.toggle(True, initializeDesktop=True)
            native.assert_called_once()
            start.assert_not_called()
            stop.assert_not_called()
            self.assertIs(self.manager.adapters['zcode'], self.adapter)
            self.assertNotIn('zcodeCompatibility', self.manager.config)

    def test_initialization_does_not_override_known_work_or_an_unsupported_version(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        with patch('bridge.clients.lifecycle.stop_client') as stop, patch('bridge.clients.lifecycle.launch_client') as start, \
                patch.object(ZCodePrototype, 'compatibility_owner', return_value=None), \
                patch.object(self.adapter, 'status', return_value={'connected': False, 'sessionAccess': True}), \
                patch.object(self.adapter, 'call', return_value={'complete': False, 'sessions': [{'status': 'running'}]}):
            with self.assertRaisesRegex(ValueError, '有任务运行'):
                self.toggle(True, initializeDesktop=True, acknowledgeUnavailable=True)
            with patch('bridge.clients.zcode.installation.installation', return_value={'version': '0.0', 'executable': '/unsupported'}):
                value = self.manager.clients(refresh=True)
                self.assertFalse(next(row for row in value['clients'] if row['id']=='zcode')['canInitializeDesktop'])
                with self.assertRaisesRegex(ValueError, '不支持'):
                    self.toggle(True, initializeDesktop=True, acknowledgeUnavailable=True)
            start.assert_not_called()
            stop.assert_not_called()
            self.assertNotIn('zcodeCompatibility', self.manager.config)

    def test_saved_compatibility_cold_starts_but_never_restarts_an_ordinary_instance_automatically(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        self.manager.control({'action': 'zcode-compatibility', 'enabled': True})
        self.state.update(running=False, mainPids=[], pids=[])
        def launch(descriptor):
            self.assertEqual(descriptor['debugFallbackPort'], 17742)
            self.state.update(running=True, mainPids=[88], pids=[88])
            return {'running': True, 'launched': True}
        with patch.object(ZCodePrototype, 'compatibility_owner', return_value=None), \
                patch.object(ZCodePrototype, 'connect_application') as handshake, \
                patch('bridge.clients.lifecycle.stop_client') as stop, \
                patch('bridge.clients.lifecycle.launch_client', side_effect=launch) as start:
            self.manager.toggle_client({'provider':'zcode','enabled':True}, startup=True)
            handshake.assert_called_once_with(88, wait=True)
            with self.assertRaisesRegex(ValueError, '选择本机兼容连接'):
                self.manager.toggle_client({'provider':'zcode','enabled':True}, startup=True)
            stop.assert_not_called()
            start.assert_called_once()

    def test_compatibility_is_a_local_explicit_next_launch_preference(self):
        from bridge.clients.lifecycle import launch_client
        from bridge.clients.zcode.adapter import ZCodePrototype
        with patch('bridge.clients.lifecycle.stop_client') as stop, patch('bridge.platforms.macos.desktop.start') as start:
            result = self.manager.control({'action': 'zcode-compatibility', 'enabled': True})
            self.assertTrue(next(row for row in result['clients'] if row['id'] == 'zcode')['compatibilityLaunch'])
            self.assertIsInstance(self.manager.adapters['zcode'], ZCodePrototype)
            descriptor = self.manager._descriptor('zcode')
            with patch('bridge.clients.desktop_app.DesktopApp.processes', return_value=[77]):
                self.assertFalse(launch_client(descriptor)['launched'])
            start.assert_not_called()
            with patch('bridge.clients.desktop_app.DesktopApp.processes', side_effect=[[], [78]]):
                self.assertTrue(launch_client(descriptor)['launched'])
            self.assertEqual(start.call_args.kwargs['arguments'],
                             ('--remote-debugging-address=127.0.0.1', '--remote-debugging-port=17742'))
            self.manager.control({'action': 'zcode-compatibility', 'enabled': False})
            self.assertIs(type(self.manager.adapters['zcode']), ZCodeDesktop)
            self.assertIsNone(self.manager._descriptor('zcode')['debugFallbackPort'])
            stop.assert_not_called()

    def test_normal_start_and_phone_only_disconnect_dont_open_ports_or_create_chats(self):
        with patch('bridge.clients.lifecycle.launch_client', return_value={'running': True, 'launched': True}) as launch, \
                patch.object(self.adapter, 'call') as call, patch('bridge.clients.lifecycle.stop_client') as stop:
            result = self.toggle(True)
            row = next(row for row in result['clients'] if row['id'] == 'zcode')
            self.assertTrue(row['running'] and row['enabled'] and row['canForceQuit'])
            self.assertFalse(row['connected'] or row['capabilities']['create'])
            launch.assert_called_once()
            self.toggle(False, quitDesktop=False)
            self.toggle(False)  # v2.2.0-preview.1: disabling ZCode only removed phone access.
            stop.assert_not_called()
            call.assert_not_called()

    def test_normal_quit_requires_confirmation_for_unknown_and_rejects_known_work(self):
        with patch('bridge.clients.lifecycle.stop_client') as stop:
            with self.assertRaisesRegex(ValueError, '无法确认任务状态'):
                self.toggle(False, quitDesktop=True)
            self.toggle(False, quitDesktop=True, acknowledgeUnavailable=True)
            stop.assert_called_once()
            stop.reset_mock()
            with patch.object(self.adapter, 'status', return_value={'connected': True}), \
                    patch.object(self.adapter, 'call', return_value={'sessions': [{'status': 'active'}]}):
                with self.assertRaisesRegex(ValueError, '有任务运行'):
                    self.toggle(False, quitDesktop=True, acknowledgeUnavailable=True)
            stop.assert_not_called()

    def test_force_requires_explicit_consent_and_keeps_identity_check(self):
        with patch('bridge.clients.lifecycle.force_stop_client') as force:
            with self.assertRaisesRegex(ValueError, '强制结束请求无效'):
                self.toggle(False, quitDesktop=True, forceDesktop=True)
            force.assert_not_called()
            self.toggle(False, quitDesktop=True, forceDesktop=True, forceConfirmed=True)
            self.assertEqual(force.call_args.kwargs['state'], self.state)

    def test_matched_running_app_is_not_restarted_or_given_debug_flags(self):
        from bridge.clients.lifecycle import launch_client
        descriptor = self.manager._descriptor('zcode')
        with patch('bridge.clients.desktop_app.DesktopApp.processes', side_effect=[[], [77]]), \
                patch('bridge.platforms.macos.desktop.start') as start:
            self.assertTrue(launch_client(descriptor)['launched'])
            args, kwargs = start.call_args
            self.assertEqual(args, (Path(descriptor['executable']), Path(descriptor['executable']).parent, {}))
            self.assertEqual(kwargs, {})
        with patch('bridge.clients.desktop_app.DesktopApp.processes', return_value=[77]), \
                patch('bridge.platforms.macos.desktop.start') as start:
            self.assertFalse(launch_client(descriptor)['launched'])
            start.assert_not_called()
