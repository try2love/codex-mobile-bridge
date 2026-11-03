"""Exercise shared gateway/phone operations without activating a user's desktop."""
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
