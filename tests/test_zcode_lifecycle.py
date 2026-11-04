"""Exercise shared gateway/phone operations without activating a user's desktop."""
import json
import plistlib
import tempfile
import threading
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
            'CFBundleShortVersionString': '4.3.7'}))
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

    def test_verified_private_start_becomes_ready_from_status_refresh_after_the_request_budget(self):
        from bridge.clients.zcode.local_pipe_host import LocalPipeClient
        row = self.manager.config['discovered']['zcode']
        process = {'pid': 77, 'birth': 'original'}
        pipe = Mock(spec=LocalPipeClient)
        pipe.application = row['application']
        pipe.supported.return_value = True
        pipe.owner.side_effect = lambda: process if pipe.start.called else None
        pipe.start.return_value = process
        pipe.call.side_effect = [{'ready': False, 'stage': 'window'}, {'ready': True, 'canCreate': True},
                                 {'ready': False, 'stage': 'handshake'}]
        self.manager.zcode_pipe = pipe
        self.manager._configure_zcode()
        native = {**self.state, 'running': False, 'mainPids': [], 'pids': []}
        now = [100.]
        get = lambda report: next(c for c in report['clients'] if c['id'] == 'zcode')
        with patch('sys.platform', 'linux'), patch('bridge.clients.zcode.installation.installation', return_value=row), \
                patch('bridge.clients.manager.time.monotonic', side_effect=lambda: now[0]), \
                patch('bridge.clients.lifecycle.inspect_client', side_effect=[native, self.state, self.state]), \
                patch('bridge.clients.lifecycle.launch_client') as ordinary:
            pending = get(self.toggle(True))
            self.assertEqual(pending['connectionState'], 'connecting')
            self.assertTrue(pending['enabled'])
            self.assertFalse(pending['pipeReady'] or pending['capabilities']['create'] or pending['connected'])
            self.manager.clients(refresh=True)
            self.assertEqual(pipe.call.call_count, 1)
            now[0] += 40
            done = threading.Event()
            refresh = self.manager._refresh_clients_status
            def observe():
                try:
                    refresh()
                finally:
                    done.set()
            with patch.object(self.manager, '_refresh_clients_status', side_effect=observe):
                self.assertTrue(self.manager.clients_status()['pending'])
                self.assertTrue(done.wait(2))
                ready = get(self.manager.clients_status())
            self.assertEqual(ready['setupStatus'], 'session-access')
            self.assertTrue(ready['pipeReady'] and ready['capabilities']['create'])
            checks = [c.args[0] for c in pipe.call.call_args_list]
            self.assertEqual([req['action'] for req in checks], ['application', 'application'])
            self.assertEqual(checks[0]['body']['startupId'], checks[1]['body']['startupId'])
            # Start a fresh observation on the same holder, then stop phone access.
            self.assertEqual(get(self.toggle(True))['connectionState'], 'connecting')
            self.toggle(False, quitDesktop=False)
            now[0] += 60
            self.assertFalse(get(self.manager.clients(refresh=True))['enabled'])
            self.assertEqual(pipe.call.call_count, 3)
            pipe.start.assert_called_once_with()
            pipe.request_quit.assert_not_called()
            ordinary.assert_not_called()

    def test_application_observation_expires_or_rejects_a_different_owner_without_relaunch(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        owner = {'pid': 77, 'birth': 'one'}
        pipe = Mock()
        pipe.owner.return_value = owner
        pipe.call.return_value = {'ready': False, 'stage': 'services'}
        adapter = ZCodePrototype(self.root, {'privatePipe': True}, pipe=pipe)
        now = [0.]
        with patch('bridge.clients.zcode.adapter.time.monotonic', side_effect=lambda: now[0]):
            adapter.start_private_application(owner)
            now[0] = 181
            self.assertEqual(adapter.observe_application_start()['connectionState'], 'timeout')
            self.assertEqual(pipe.call.call_count, 1)
            adapter.start_private_application(owner)
            now[0] += 5
            pipe.owner.return_value = {'pid': 77, 'birth': 'two'}
            self.assertEqual(adapter.observe_application_start()['connectionState'], 'error')
            self.assertEqual(pipe.call.call_count, 2)
            self.assertIsNone(adapter.application_owner)
            pipe.owner.return_value = owner
            pipe.call.side_effect = ValueError('ZCode 原 App 会话服务协议不兼容')
            with self.assertRaisesRegex(ValueError, '协议不兼容'):
                adapter.start_private_application(owner)
            self.assertIsNone(adapter.application_owner)
            pipe.start.assert_not_called()

    def test_cold_probes_share_one_native_deadline_and_an_existing_process_is_tried_once(self):
        from bridge.clients.errors import BridgeUnavailable
        from bridge.clients.zcode.adapter import ZCodePrototype
        now = [10.0]  # The initial locate/launch already used ten seconds.
        owner = {'pid': 77}
        requests = []
        def elapsed(seconds):
            now[0] += seconds
        def probe(body):
            requests.append(body.get('expiresAt'))
            if len(requests) == 1:
                elapsed(12)
                raise TimeoutError('renderer starting')
            if len(requests) == 2:
                return {'ready': True}
            raise EOFError('closed')
        cfg = self.manager.config['discovered']['zcode']
        adapter = ZCodePrototype(self.root, {**cfg, 'debugFallbackPort': 17742}, transport=probe)
        with patch.object(adapter, '_live_process', return_value=owner), \
                patch('bridge.clients.zcode.adapter.time.monotonic', side_effect=lambda: now[0]), \
                patch('bridge.clients.zcode.adapter.time.time', side_effect=lambda: now[0]), \
                patch('bridge.clients.zcode.adapter.time.sleep', side_effect=elapsed):
            adapter.connect_application(77, wait=True, deadline=30)
            self.assertEqual(requests, [22000, 30000])
            with self.assertRaisesRegex(BridgeUnavailable, 'closed'):
                adapter.connect_application(77)
            self.assertEqual(requests, [22000, 30000, None])

    def test_app_inventory_projects_and_creation_need_no_bound_conversation(self):
        from bridge.clients.zcode.adapter import ZCodePrototype
        owner = {'pid': 77}
        cfg = self.manager.config['discovered']['zcode']
        native = Mock()
        adapter = ZCodePrototype(self.root, {**cfg, 'debugFallbackPort': 17742}, transport=native)
        self.manager.adapters['zcode'] = adapter
        self.manager.config.setdefault('enabled', {})['zcode'] = True

        def inventory():
            return next(row for row in self.manager.clients(refresh=True)['clients'] if row['id'] == 'zcode')

        with patch.object(adapter, '_live_process', return_value=owner), patch.object(adapter, '_window_app', return_value=None):
            self.assertEqual(inventory()['capabilities'], {'notifications': True, 'create': False, 'accounts': True})
            native.return_value = {'ready': True, 'canCreate': True}
            adapter.connect_application(77)
            self.assertTrue(inventory()['capabilities']['create'])
            self.assertIsNone(adapter.sid)
            native.return_value = {'projects': [{'key': 'p', 'cwd': '/project'}]}
            self.assertEqual(adapter.call('projects')['projects'], [{'key': 'p', 'cwd': '/project'}])
            rid = 'fc220000-4000-4000-8000-000000000023'
            body = {'id': rid, 'projectKey': 'p', 'firstMessage': 'test'}
            native.return_value = {'status': 'accepted', 'result': {'id': 'sess_'+rid},
                                   'workspace': '/project', 'binding': {'clientId': 'same-client'}}
            created = adapter.call('create', body=body)
            self.assertEqual(created['result']['id'], adapter.sid)
            self.assertEqual(native.call_args.args[0], {'action': 'create', 'expectedProcess': owner, 'body': body})
            native.reset_mock()
            self.assertTrue(inventory()['capabilities']['create'])
            native.assert_not_called()  # No large chat read to paint two entry points.
            # An original cwd without a native project ID is still selectable through
            # the existing bound-workspace entry, never sent as an invented project ID.
            body = {**body, 'projectKey': '/project', 'id': 'fc220000-4000-4000-8000-000000000024'}
            native.return_value = {'status': 'rejected'}
            adapter.call('create', body=body)
            self.assertEqual(native.call_args.args[0]['workspace'], '/project')
            self.assertEqual(native.call_args.args[0]['sessionId'], created['result']['id'])
            with patch.object(adapter, '_live_process', return_value={'pid': 88}):
                self.assertFalse(inventory()['capabilities']['create'])
                self.assertTrue(inventory()['capabilities']['notifications'])

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

    def test_retired_port_configuration_never_reopens_or_probes_a_listener(self):
        installation = self.manager.config['discovered']['zcode']
        home = Path(installation['dataDirectory'])
        home.mkdir(parents=True, exist_ok=True)
        (home/'tasks-index.sqlite').touch()
        self.manager.config['zcodePrototype'] = {**installation, 'workspace': str(self.root),
            'sessionId': 'sess_test', 'port': 17742, 'process': {'pid': 1}, 'binding': {'owner': 'retired'}}
        self.manager.config['zcodeCompatibility'] = True
        self.manager._configure_zcode()
        self.manager.config.setdefault('enabled', {})['zcode'] = True
        with patch('bridge.clients.zcode.adapter.listener_identity') as listener:
            current = next(x for x in self.manager.clients(refresh=True)['clients'] if x['id'] == 'zcode')
            self.assertTrue(current['running'] and current['historyAvailable'])
            self.assertFalse(current['compatibilitySupported'] or current['compatibilityLaunch'])
            self.assertEqual(current['connectionMode'], 'private-pipe')
            self.assertIsNone(self.manager._descriptor('zcode')['debugFallbackPort'])
            listener.assert_not_called()
        with self.assertRaisesRegex(ValueError, '已移除'):
            self.manager.control({'action': 'zcode-compatibility', 'enabled': True})

    def test_private_initialization_restarts_normally_only_after_task_confirmation(self):
        from bridge.clients.zcode.pipe_host import PipeHostClient
        pipe = Mock(spec=PipeHostClient)
        pipe.application = self.manager.config['discovered']['zcode']['application']
        pipe.supported.return_value = True
        owner = {'pid': 82, 'birth': [3, 4]}
        pipe.owner.side_effect = lambda: owner if pipe.start.called else None
        pipe.call.return_value = {'ready': True, 'canCreate': True}
        self.manager.zcode_pipe = pipe
        self.manager._configure_zcode()

        def stop(descriptor, *, state):
            self.assertIs(state, self.state)
            self.state.update(running=False, mainPids=[], pids=[])

        def start():
            self.state.update(running=True, mainPids=[82], pids=[82])
            return owner

        pipe.start.side_effect = start
        with patch('bridge.clients.lifecycle.stop_client', side_effect=stop) as quit_app, \
                patch('bridge.clients.lifecycle.launch_client') as ordinary:
            with self.assertRaisesRegex(ValueError, '无法确认任务状态'):
                self.toggle(True, initializeDesktop=True, connectionMode='private-pipe')
            quit_app.assert_not_called()
            pipe.start.assert_not_called()
            value = self.toggle(True, initializeDesktop=True, connectionMode='private-pipe', acknowledgeUnavailable=True)
            row = next(row for row in value['clients'] if row['id'] == 'zcode')
            self.assertTrue(row['pipeReady'] and row['capabilities']['create'])
            self.assertEqual(json.loads(self.manager.config_path.read_text())['zcodeConnectionMode'], 'private-pipe')
            self.assertFalse(row['compatibilityLaunch'] or row['canInitializeDesktop'])
            self.assertIsNone(self.manager.adapters['zcode'].sid)
            quit_app.assert_called_once()
            pipe.start.assert_called_once()
            ordinary.assert_not_called()

    def test_failed_private_handshake_keeps_live_owner_and_does_not_open_a_port(self):
        from bridge.clients.zcode.pipe_host import PipeHostClient
        pipe = Mock(spec=PipeHostClient)
        pipe.application = self.manager.config['discovered']['zcode']['application']
        pipe.supported.return_value = True
        pipe.owner.return_value = {'pid': 77}
        pipe.call.return_value = {}
        self.manager.zcode_pipe = pipe
        self.manager._configure_zcode()
        with patch('bridge.clients.lifecycle.stop_client') as stop, \
                patch('bridge.clients.lifecycle.launch_client') as start, \
                patch('bridge.clients.zcode.adapter.listener_identity') as listener:
            with self.assertRaisesRegex(ValueError, '原 App 未确认连接'):
                self.toggle(True, initializeDesktop=True, connectionMode='private-pipe')
            self.assertIsNone(self.manager.adapters['zcode'].application_owner)
            self.assertNotIn('zcodeCompatibility', self.manager.config)
            pipe.start.assert_not_called()
            start.assert_not_called()
            stop.assert_not_called()
            listener.assert_not_called()

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
