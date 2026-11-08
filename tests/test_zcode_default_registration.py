"""Manual discovery adds only the native default ZCode, never a second profile."""
import json
import plistlib
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock, patch

from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]


class ZCodeDefaultRegistration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root/'user'; self.home.mkdir()
        for target, value in [('bridge.clients.manager.sys.platform', 'darwin'),
                              ('bridge.clients.zcode.pipe_host.PipeHostClient.supported', False)]:
            mocked = patch(target, return_value=value) if isinstance(value, bool) else patch(target, value)
            mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch('pathlib.Path.home', return_value=self.home); mocked.start(); self.addCleanup(mocked.stop)
        self.manager = DesktopSessions(self.root/'gateway'); self.addCleanup(self.manager.close)
        self.manager.adapters['claude'].status = Mock(return_value={})
        self.manager.adapters['deepseek'].call = Mock(return_value={})
        self.manager._discovery_preferences = Mock(return_value={})
        self.manager._zcode_official_status = Mock(return_value={})
        self.discovery = patch('bridge.clients.discovery.discover_clients', return_value={}).start()
        self.addCleanup(patch.stopall)
        patch('bridge.clients.lifecycle.inspect_clients', return_value={'zcode': {'running': False}}).start()
        self.app = self.bundle('Portable ZCode.app')
        self.request = {'action': 'client-profiles', 'operation': 'save', 'family': 'zcode',
                        'kind': 'default-application', 'application': str(self.app)}

    def bundle(self, name, identifier='dev.zcode.app'):
        app = self.root/name
        (app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/MacOS/ZCode').write_bytes(b'fixture, never executable')
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': identifier,
            'CFBundleExecutable': 'ZCode', 'CFBundleShortVersionString': 'fixture'}))
        return app

    def capability(self):
        return next(row for row in self.manager.instances.capabilities() if row['family'] == 'zcode')

    def test_save_registers_default_row_and_persists_application_without_launch_or_new_home(self):
        self.assertTrue(self.capability()['supported'])
        with patch('bridge.clients.zcode.installation.launch', side_effect=AssertionError('launch')):
            result = self.manager.instance_control(self.request)
        self.assertEqual(result['entry'], 'zcode')
        self.assertEqual(result['instance']['id'], 'zcode')
        self.assertTrue(result['instance']['capabilities']['accounts'])
        self.assertFalse(result['instance']['enabled'])
        self.assertFalse(result['instance']['connected'])
        self.assertEqual(self.manager.adapters['zcode'].home, self.home/'.zcode/v2')
        self.assertFalse((self.home/'.zcode').exists())
        self.assertEqual(self.manager.instances.registry.list(), [])
        self.assertFalse(self.capability()['supported'])
        saved = json.loads(self.manager.config_path.read_text())
        self.assertEqual(saved['zcodeApplication'], str(self.app))
        self.assertFalse(saved['enabled']['zcode'])
        restored = DesktopSessions(self.root/'gateway')
        try:
            self.assertEqual(restored.adapters['zcode'].config['application'], str(self.app))
            self.assertEqual(restored.adapters['zcode'].home, self.home/'.zcode/v2')
            self.assertFalse(restored.enabled('zcode'))
        finally:
            restored.close()

    def test_fresh_discovery_prevents_stale_form_from_overwriting_existing_client(self):
        self.discovery.return_value = {'zcode': {'installed': True, 'application': '/existing/ZCode.app'}}
        with self.assertRaisesRegex(ValueError, '客户端已存在'):
            self.manager.instance_control(self.request)
        self.assertNotIn('zcodeApplication', self.manager.config)
        self.assertNotIn('zcode', self.manager.adapters)

    def test_exact_native_identity_is_required_and_no_other_installation_is_substituted(self):
        wrong = self.bundle('Other.app', 'example.other')
        with self.assertRaisesRegex(ValueError, '不是可用的 ZCode'):
            self.manager.instance_control({**self.request, 'application': str(wrong)})
        self.discovery.assert_called_once_with({})
        self.assertNotIn('zcodeApplication', self.manager.config)

    def test_invalid_old_path_can_be_reselected_while_disabled(self):
        self.manager.config['zcodeApplication'] = str(self.root/'removed.app')
        self.manager.config['discovered'] = {'zcode': {'installed': True, 'application': str(self.root/'removed.app')}}
        self.assertTrue(self.capability()['supported'])
        self.assertEqual(self.manager.instance_control(self.request)['entry'], 'zcode')

    def test_enabled_or_active_connection_cannot_be_replaced(self):
        self.manager.config.setdefault('enabled', {})['zcode'] = True
        with self.assertRaisesRegex(ValueError, '先停用'):
            self.manager.instance_control(self.request)
        self.manager.config['enabled']['zcode'] = False
        pipe = Mock(); pipe.owner.return_value = {'pid': 123}
        self.manager.zcode_pipe = pipe
        with self.assertRaisesRegex(ValueError, '活动连接'):
            self.manager.instance_control(self.request)
        pipe.start.assert_not_called()
        self.manager.zcode_pipe = None
        self.manager._zcode_official_status.return_value = {'connected': True}
        with self.assertRaisesRegex(ValueError, '活动连接'):
            self.manager.instance_control(self.request)
        self.assertNotIn('zcodeApplication', self.manager.config)

    def test_exact_fields_absolute_path_and_single_default_identity_are_enforced(self):
        for body in ({**self.request, 'home': str(self.root)}, {**self.request, 'label': 'Second'},
                     {**self.request, 'enabled': True}, {**self.request, 'application': 'ZCode.app'},
                     {**self.request, 'application': '/fake\nZCode.app'},
                     {**self.request, 'application': 'https://example.test/ZCode.app'},
                     {'action': 'client-profiles', 'operation': 'save', 'family': 'zcode',
                      'kind': 'desktop-profile', 'label': 'Second', 'home': str(self.root)}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.manager.instance_control(body)
        self.assertEqual(self.manager.instances.registry.list(), [])
        self.assertNotIn('zcode', self.manager.adapters)

    def test_supported_platform_contract_keeps_exact_application_and_native_default_home(self):
        # Native validators are platform-owned; this verifies only the common
        # registration contract on Mac, without executing Windows/Linux code.
        for platform, name in [('darwin', 'Portable ZCode.app'), ('win32', 'ZCode.exe'), ('linux', 'ZCode.AppImage')]:
            selected = self.root/name
            if not selected.exists(): selected.write_bytes(b'fixture')
            manager = DesktopSessions(self.root/('gateway-'+platform))
            try:
                manager._discovery_preferences = Mock(return_value={})
                manager._zcode_official_status = Mock(return_value={})
                manager._configure_zcode = Mock()
                manager.clients = Mock(return_value={'clients': [{'id': 'zcode', 'enabled': False}]})
                with patch('bridge.clients.manager.sys.platform', platform), \
                     patch('bridge.clients.zcode.installation.installation', return_value={'executable': str(selected)}) as validate:
                    result = manager.instance_control({**self.request, 'application': str(selected)})
                validate.assert_called_once_with(selected.resolve())
                self.assertEqual(result['entry'], 'zcode')
                self.assertEqual(manager.config['zcodeApplication'], str(selected.resolve()))
                self.assertEqual(manager.config['discovered']['zcode']['dataDirectory'], str(self.home/'.zcode/v2'))
                self.assertFalse(manager.enabled('zcode'))
                self.assertEqual(manager.instances.registry.list(), [])
            finally:
                manager.close()

    def test_windows_network_path_is_not_a_local_application(self):
        with patch('bridge.clients.manager.sys.platform', 'win32'), patch('bridge.clients.manager.Path', PureWindowsPath):
            with self.assertRaisesRegex(ValueError, '本机'):
                self.manager.instance_control({**self.request, 'application': r'\\server\share\ZCode.exe'})
        self.discovery.assert_not_called()

    def test_official_connection_gate_waits_only_for_a_saved_login_being_restored(self):
        from contextlib import nullcontext
        self.manager._configure_zcode = Mock()
        self.manager._changing = Mock(side_effect=lambda *args, **kwargs: nullcontext())
        for state in ({'loginSaved': True, 'restoring': False}, {'restoring': True}, {}):
            self.manager._zcode_official_status.return_value = state
            with self.assertRaisesRegex(ValueError, '请先在电脑网关'):
                self.manager._select_zcode_connection('official-remote')
        self.manager._zcode_official_status.return_value = {'loginSaved': True, 'restoring': True, 'configured': False}
        self.manager._select_zcode_connection('official-remote')
        self.assertEqual(self.manager.config['zcodeConnectionMode'], 'official-remote')
        self.assertFalse(self.manager._zcode_official_status()['configured'])

    def test_targeted_profile_capabilities_do_not_validate_unrelated_zcode_installation(self):
        self.manager.config['discovered'] = {'zcode': {'installed': True, 'application': str(self.app)}}
        with patch('bridge.clients.zcode.installation.installation', side_effect=AssertionError('unrelated ZCode validation')):
            for family in ('claude', 'deepseek'):
                self.assertEqual([row['family'] for row in self.manager.instances.capabilities(family)], [family])

    def test_scan_preferences_preserve_saved_default_override_and_other_homes(self):
        self.manager.config.update(zcodeApplication=str(self.app), claudeHome=str(self.root/'claude'))
        with patch('bridge.app.desktop.Desktop.preferences', return_value={'theme': 'dark', 'zcodeApplication': '/old'}):
            preferences = DesktopSessions._discovery_preferences(self.manager)
        self.assertEqual(preferences['zcodeApplication'], str(self.app))
        self.assertEqual(preferences['claudeHome'], str(self.root/'claude'))
        self.assertEqual(preferences['theme'], 'dark')
