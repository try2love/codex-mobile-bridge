import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.integrations.deepseek import DeepSeek, MARKER
from bridge.integrations.errors import BridgeUnavailable
from bridge.integrations.deepseek_setup import insertion, legacy_account_configured


class DeepSeekMigrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]/'.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root/'dsh'
        self.profile = self.home/'profiles/desktop/cordis.patch.yml'
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text('- id: user-setting\n  config: !!js "do not evaluate"\n')
        self.old = DeepSeek(self.root/'desktop-lab/desktop-sessions/deepseek', self.home)
        self.old.ensure_installed()
        self.new_path = self.root/'new-preview/desktop-sessions/deepseek'
        self.new = DeepSeek(self.new_path, self.home)

    def normalize(self):
        text = self.profile.read_text()
        entry = json.loads(self.old._line()[2:])
        flow = '- '+json.dumps(entry, ensure_ascii=False, indent=2)
        flow = flow.replace('mobile-host.mjs', 'mobile-\\\n              host.mjs')
        flow = flow.replace('/desktop-lab/', '/desktop-\\\n                  lab/')
        self.profile.write_text(text.split(MARKER)[0]+MARKER+'\n'+flow+'\n- id: later-user-setting\n  config: true\n')

    def legacy(self):
        content = b'// known earlier bridge adapter fixture\n'
        (self.old.directory/'mobile-host.mjs').write_bytes(content)
        self.hash_patch = patch('bridge.integrations.deepseek_setup.LEGACY_SOURCE', hashlib.sha256(content).hexdigest())
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def grant(self, *, issuer='https://platform.deepseek.com', token='private-test-token', kind='grant'):
        path = self.home/'.credentials.yaml'
        path.write_text('version: 1\nrecords:\n  deepseek-account-platform/default:\n    kind: '+kind+
                        '\n    payload:\n      version: 1\n      token: '+json.dumps(token)+
                        '\n      issuer: '+issuer+'\n')
        path.chmod(0o600)
        return path

    def test_normalized_same_owner_install_and_uninstall_preserve_user_settings(self):
        self.normalize()
        before = self.profile.read_text()
        self.assertEqual(insertion(before)[0], json.loads(self.old._line()[2:]))
        self.assertTrue(self.old.installation_status()['installed'])
        self.assertFalse(self.old.ensure_installed()['changed'])
        self.assertEqual(self.profile.read_text(), before)
        self.old.uninstall()
        after = self.profile.read_text()
        self.assertNotIn(MARKER, after)
        self.assertIn('config: !!js "do not evaluate"', after)
        self.assertIn('- id: later-user-setting\n  config: true', after)

    def test_known_old_adapter_reuses_endpoint_and_shared_ledger_without_any_write(self):
        self.legacy()
        self.normalize()
        endpoint = self.old.directory/'endpoint.json'
        endpoint.write_text('{"port":32123,"pid":4321}')
        snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.old.directory.iterdir()}
        snapshot[self.profile] = (self.profile.read_bytes(), self.profile.stat().st_mtime_ns)
        result = self.new.ensure_installed()
        self.assertTrue(result['reused'])
        self.assertTrue(result['legacyProtocol'])
        self.assertTrue(result['protocolReady'])
        self.assertTrue(result['restartRequired'])
        self.assertEqual(self.new.directory, self.old.directory)
        self.assertEqual(self.new.receipts_path, self.old.directory.parent/'requests.sqlite')
        self.assertFalse(self.new_path.exists())
        self.assertEqual(snapshot, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in snapshot})
        self.assertTrue(self.new.ensure_installed()['reused'])

    def test_restore_saved_directory_is_checked_against_actual_insertion(self):
        self.assertTrue(self.new.use_existing(self.old.directory)['reused'])
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.new.use_existing(self.root/'unrelated')

    def test_revision_three_without_quit_is_reused_without_rewriting_live_source(self):
        source = self.old.directory/'mobile-host.mjs'
        content = b'// known revision 3 before native quit\n'
        source.write_bytes(content)
        credentials = (self.old.directory/'connection.json').read_bytes()
        (self.old.directory/'endpoint.json').write_text('{"port":32123,"pid":4321}')
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(json.dumps({'connected': True, 'bridgeRevision': 3,
                                             'configured': True}).encode())
        with patch('bridge.integrations.deepseek_setup.PREVIOUS_QUIT_SOURCES',
                   (hashlib.sha256(content).hexdigest(),)), \
                patch('bridge.integrations.deepseek.urllib.request.build_opener', return_value=Opener()):
            for adapter in (self.new, self.old):
                with self.subTest(directory=adapter.directory):
                    installed = adapter.ensure_installed()
                    self.assertTrue(installed['reused'])
                    self.assertFalse(installed['legacyProtocol'])
                    self.assertFalse(installed['updateRequired'])
                    status = adapter.call('status')
                    self.assertTrue(status['connected'])
                    self.assertFalse(status['updateRequired'])
                    self.assertNotIn('nativeQuit', status)
                    self.assertEqual(source.read_bytes(), content)
                    self.assertEqual((self.old.directory/'connection.json').read_bytes(), credentials)

    def test_changed_or_foreign_source_and_wrong_installation_home_fail_closed(self):
        before = self.profile.read_bytes()
        source = self.old.directory/'mobile-host.mjs'
        original_source = source.read_bytes()
        source.write_text('// not our recognized adapter')
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.new.ensure_installed()
        source.write_bytes(original_source)
        record = self.old.directory/'installation.json'
        saved = json.loads(record.read_text())
        record.write_text(json.dumps({**saved, 'home': str(self.root/'some-other-dsh')}))
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.new.ensure_installed()
        self.assertEqual(self.profile.read_bytes(), before)
        self.assertFalse(self.new_path.exists())

    def test_malformed_duplicate_and_unknown_yaml_are_not_repaired_as_owned(self):
        for value in [MARKER+'\n- !!js "anything"\n', MARKER+'\n- {"insert": []\n',
                      MARKER+'\n- {"insert": [], "insert": []}\n',
                      MARKER+'\n- {}\n'+MARKER+'\n- {}\n']:
            self.profile.write_text(value)
            with self.assertRaises(ValueError):
                self.new.ensure_installed()
            self.assertEqual(self.profile.read_text(), value)
        self.assertFalse(self.new_path.exists())

    def test_legacy_ready_requires_matching_live_catalog_and_stored_grant(self):
        self.legacy()
        self.grant()
        self.new.ensure_installed()
        (self.old.directory/'endpoint.json').write_text('{"port":32123,"pid":4321}')
        responses = {'status': {'connected': True}, 'list': {'sessions': [{'id': 'native-one'}]},
                     'catalog': {'models': [{'id': 'deepseek-account/deepseek-flash'}]}}
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(json.dumps(responses[json.loads(request.data)['action']]).encode())
        with patch('bridge.integrations.deepseek.urllib.request.build_opener', return_value=Opener()):
            status = self.new.call('status')
            self.assertTrue(status['configured'])
            self.assertTrue(status['updateRequired'])
            self.assertEqual(status['configurationEvidence'], 'stored-account-and-catalog')
            self.assertNotIn('private-test-token', json.dumps(status))
            responses['catalog']['models'] = [{'id': 'other/model'}]
            self.assertFalse(self.new.call('status')['configured'])
            self.grant(token='')
            self.assertFalse(self.new.call('status')['configured'])

    def test_loaded_revision_and_missing_detail_capabilities_do_not_claim_modern_controls(self):
        self.new.ensure_installed()
        (self.old.directory/'endpoint.json').write_text('{"port":32123,"pid":4321}')
        responses = {'status': {'connected': True, 'configured': True}, 'detail': {
            'session': {'id': 'native-one'}, 'records': [{'event': {'seq': 1, 'type': 'assistant/message',
            'data': {'message': {'content': [{'type': 'text', 'text': 'old history'}]}}}}]}}
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(json.dumps(responses[json.loads(request.data)['action']]).encode())
        with patch('bridge.integrations.deepseek.urllib.request.build_opener', return_value=Opener()):
            self.assertTrue(self.new.call('status')['updateRequired'])
            detail = self.new.call('detail', 'native-one')
            self.assertEqual(detail['messages'][0]['text'], 'old history')
            self.assertFalse(detail['capabilities']['attachments'])
            self.assertFalse(detail['capabilities']['send'])
            self.assertIn('更新', detail['notice'])
            responses['status']['bridgeRevision'] = 3
            self.assertFalse(self.new.call('status')['updateRequired'])

    def test_credential_metadata_check_never_accepts_empty_foreign_or_executable_yaml(self):
        self.grant()
        self.assertTrue(legacy_account_configured(self.home))
        for kwargs in ({'issuer': 'https://another.example'}, {'token': ''}, {'kind': 'api-key'}):
            self.grant(**kwargs)
            self.assertFalse(legacy_account_configured(self.home))
        self.grant().write_text('version: 1\nrecords: !!js "never execute"')
        self.assertFalse(legacy_account_configured(self.home))

    def test_manager_restores_only_a_verified_saved_connector_directory(self):
        from bridge.integrations.manager import DesktopSessions
        data = self.root/'preview-manager'
        settings = data/'desktop-sessions/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'deepseekHome': str(self.home),
                                        'deepseekAdapterDirectory': str(self.old.directory)}))
        manager = DesktopSessions(data)
        try:
            self.assertEqual(manager.adapters['deepseek'].directory, self.old.directory)
            self.assertTrue(manager.adapters['deepseek'].reused)
            self.assertEqual(manager.adapters['deepseek'].receipts_path, self.old.directory.parent/'requests.sqlite')
        finally:
            manager.close()
        settings.write_text(json.dumps({'deepseekHome': str(self.home),
                                        'deepseekAdapterDirectory': str(self.root/'unrelated-directory')}))
        manager = DesktopSessions(data)
        try:
            self.assertEqual(manager.adapters['deepseek'].directory, settings.parent/'deepseek')
            self.assertFalse(manager.adapters['deepseek'].reused)
            self.assertEqual(manager.config['setup']['deepseek']['setupStatus'], 'failed')
            manager.config['enabled'] = {'deepseek': True}
            manager.adapters['deepseek'].call = Mock(return_value={'connected': True, 'configured': True})
            with self.assertRaisesRegex(ValueError, '请重新扫描'):
                manager.call('deepseek', 'list')
            with self.assertRaisesRegex(ValueError, '请重新扫描'):
                manager.require_enabled('deepseek')
            row = next(row for row in manager.clients()['clients'] if row['id'] == 'deepseek')
            self.assertFalse(row['configured'])
            self.assertFalse(row['connected'])
            manager.adapters['deepseek'].call.assert_not_called()
        finally:
            manager.close()

    def test_connected_recovered_scan_skips_all_launch_and_restart_operations(self):
        from bridge.integrations.manager import DesktopSessions
        self.normalize()
        data = self.root/'scan-manager'
        manager = DesktopSessions(data)
        discovered = {provider: {'id': provider, 'installed': provider == 'deepseek',
                                'dataDirectory': str(self.home), 'executable': '/fixture/Harness',
                                'desktopProfileReady': True} for provider in ('codex', 'claude', 'deepseek')}
        manager.config['setup'] = {'deepseek': {'setupStatus': 'restart-required', 'restartConnected': True}}
        manager.deepseek_restore_error = '已有 Harness 接入无法验证，请重新扫描'
        try:
            with patch('bridge.integrations.discovery.discover_clients', return_value=discovered), \
                    patch('bridge.desktop.Desktop.preferences', return_value={}), \
                    patch.object(DeepSeek, 'call', return_value={'connected': True, 'configured': True, 'bridgeRevision': 3}), \
                    patch('bridge.integrations.client_launch.launch_deepseek') as launch:
                result = manager.scan()
            launch.assert_not_called()
            row = next(row for row in result['clients'] if row['id'] == 'deepseek')
            self.assertTrue(row['configured'])
            self.assertFalse(row['enabled'])
            self.assertEqual(row['setupStatus'], 'ready')
            self.assertEqual(row['reason'], '接入配置已就绪')
            self.assertIsNone(manager.deepseek_restore_error)
            saved = json.loads(manager.config_path.read_text())
            self.assertEqual(saved['deepseekAdapterDirectory'], str(self.old.directory))
            self.assertNotIn('restartEndpoint', saved['setup']['deepseek'])
        finally:
            manager.close()
        reopened = DesktopSessions(data)
        try:
            self.assertTrue(reopened.adapters['deepseek'].reused)
            self.assertEqual(reopened.adapters['deepseek'].directory, self.old.directory)
        finally:
            reopened.close()


    def test_old_source_is_reported_as_needing_upgrade_without_scan_writes(self):
        self.legacy()
        before = (self.old.directory/'mobile-host.mjs').read_bytes()
        result = self.new.ensure_installed()
        self.assertTrue(result['updateRequired'])
        self.assertTrue(result['restartRequired'])
        self.assertEqual((self.old.directory/'mobile-host.mjs').read_bytes(), before)

    def test_explicit_update_keeps_credentials_receipts_profile_and_endpoint(self):
        self.legacy()
        self.normalize()
        self.grant()
        (self.old.directory/'endpoint.json').write_text('{"port":32123,"pid":4321}')
        ledger = self.old.directory.parent/'requests.sqlite'
        ledger.write_bytes(b'unchanged receipt fixture')
        self.new.ensure_installed()
        unchanged = [self.profile, self.home/'.credentials.yaml', ledger,
                     self.old.directory/'connection.json', self.old.directory/'installation.json',
                     self.old.directory/'endpoint.json']
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in unchanged}
        result = self.new.update_existing()
        self.assertTrue(result['changed'])
        self.assertTrue(result['restartRequired'])
        self.assertEqual((self.old.directory/'mobile-host.mjs').read_bytes(),
                         Path(__file__).resolve().parents[1].joinpath('bridge/integrations/deepseek-host.mjs').read_bytes())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in unchanged})
        self.assertEqual(self.new.receipts_path, ledger)
        self.assertFalse(self.new_path.exists())
        self.assertFalse(self.new.update_existing()['changed'])

    def test_explicit_update_rechecks_ownership_before_overwriting(self):
        self.legacy()
        self.new.ensure_installed()
        target = self.old.directory/'mobile-host.mjs'
        target.write_text('// user replaced source after scan')
        before = target.read_bytes()
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.new.update_existing()
        self.assertEqual(target.read_bytes(), before)

    def migration_manager(self):
        from bridge.integrations.manager import DesktopSessions
        manager = DesktopSessions(self.root/'upgrade-manager', gateway_running=True)
        self.addCleanup(manager.close)
        discovered = {provider: {'id': provider, 'installed': provider == 'deepseek',
                                'dataDirectory': str(self.home), 'executable': '/fixture/Harness',
                                'desktopProfileReady': True} for provider in ('codex', 'claude', 'deepseek')}
        for target, options in [
                ('bridge.integrations.discovery.discover_clients', {'return_value': discovered}),
                ('bridge.desktop.Desktop.preferences', {'return_value': {}}),
                ('bridge.integrations.client_launch.inspect_client', {'return_value': {'running': True, 'pids': [11, 12], 'mainPids': [11], 'runtimePids': [12], 'unknown': False}}),
                ('bridge.integrations.client_launch.stop_deepseek', {'return_value': None}),
                ('bridge.integrations.client_launch.stop_client', {'return_value': None})]:
            mocked = patch(target, **options); mocked.start(); self.addCleanup(mocked.stop)
        manager.config['discovered'] = discovered
        manager._deepseek_endpoint = Mock(return_value={'port': 32323, 'pid': 12})
        return manager

    def test_legacy_scan_offers_confirmed_restart_without_launching_or_claiming_ready(self):
        self.legacy()
        manager = self.migration_manager()
        before = (self.old.directory/'mobile-host.mjs').read_bytes()
        with patch.object(DeepSeek, 'call', return_value={'connected': True, 'configured': True}), \
                patch('bridge.integrations.client_launch.launch_deepseek') as launch:
            result = manager.scan()
            row = next(row for row in result['clients'] if row['id'] == 'deepseek')
            self.assertEqual(row['setupStatus'], 'restart-required')
            self.assertFalse(row['configured'])
            self.assertFalse(row['enabled'])
            self.assertIn('更新', row['reason'])
            launch.assert_not_called()
        self.assertEqual((self.old.directory/'mobile-host.mjs').read_bytes(), before)

    def test_confirmed_restart_updates_source_and_waits_for_new_loaded_revision(self):
        self.legacy()
        manager = self.migration_manager()
        live = {'connected': True, 'configured': True, 'updateRequired': True, 'bridgeRevision': 3}
        def call(adapter, action, *args):
            return dict(live) if action == 'status' else {'complete': True, 'bridgeRevision': 3, 'sessions': []}
        with patch.object(DeepSeek, 'call', autospec=True, side_effect=call), \
                patch('bridge.integrations.client_launch.launch_deepseek') as launch:
            manager.scan()
            manager.connect_deepseek(restart=True)
            launch.assert_called_once()
            self.assertNotIn('restart', launch.call_args.kwargs)
            self.assertEqual((self.old.directory/'mobile-host.mjs').read_bytes(),
                             Path(__file__).resolve().parents[1].joinpath('bridge/integrations/deepseek-host.mjs').read_bytes())
            row = next(row for row in manager.scan()['clients'] if row['id'] == 'deepseek')
            self.assertEqual(row['setupStatus'], 'restart-required')
            self.assertFalse(row['configured'])
            live.update(bridgeRevision=3, updateRequired=False)
            row = next(row for row in manager.clients(refresh=True)['clients'] if row['id'] == 'deepseek')
            self.assertEqual(row['setupStatus'], 'ready')
            self.assertTrue(row['configured'])

    def test_failed_launch_retains_pending_upgrade_without_rewriting_credentials(self):
        self.legacy()
        manager = self.migration_manager()
        before = (self.old.directory/'connection.json').read_bytes()
        def call(adapter, action, *args):
            return {'connected': True, 'configured': True, 'updateRequired': True, 'bridgeRevision': 3} if action == 'status' else {'complete': True, 'bridgeRevision': 3, 'sessions': []}
        with patch.object(DeepSeek, 'call', autospec=True, side_effect=call), \
                patch('bridge.integrations.client_launch.launch_deepseek', side_effect=OSError('fixture launch failed')):
            manager.scan()
            with self.assertRaisesRegex(OSError, 'launch failed'):
                manager.connect_deepseek(restart=True)
            state = json.loads(manager.config_path.read_text())['setup']['deepseek']
            self.assertEqual(state['requiredBridgeRevision'], 3)
            self.assertEqual(state['setupStatus'], 'restart-required')
            self.assertEqual((self.old.directory/'connection.json').read_bytes(), before)
            row = next(row for row in manager.clients(refresh=True)['clients'] if row['id'] == 'deepseek')
            self.assertFalse(row['configured'])
            self.assertEqual(row['setupStatus'], 'restart-required')

    def test_confirmed_restart_refuses_active_tasks_or_pending_questions_before_upgrade(self):
        self.legacy()
        manager = self.migration_manager()
        self.new.ensure_installed()
        manager.adapters['deepseek'] = self.new
        source = self.old.directory/'mobile-host.mjs'
        before = source.read_bytes()
        for session in [{'status': 'active'}, {'status': 'idle', 'requests': [{'id': 'pending'}]}]:
            with patch.object(self.new, 'call', side_effect=lambda action: {'connected': True, 'bridgeRevision': 3} if action == 'status' else {'complete': True, 'bridgeRevision': 3, 'sessions': [session]}), \
                    patch('bridge.integrations.client_launch.launch_deepseek') as launch:
                with self.assertRaisesRegex(ValueError, '运行或等待确认'):
                    manager.connect_deepseek(restart=True)
                launch.assert_not_called()
                self.assertEqual(source.read_bytes(), before)


    def test_gateway_activation_updates_stopped_legacy_connector_and_loads_on_first_start(self):
        self.legacy()
        manager = self.migration_manager()
        with patch('bridge.integrations.client_launch.inspect_client', return_value={'running': False, 'pids': [], 'mainPids': [], 'runtimePids': [], 'unknown': False}), \
                patch.object(DeepSeek, 'call', side_effect=BridgeUnavailable('stopped')), \
                patch('bridge.integrations.client_launch.launch_deepseek', return_value={'running': True}) as launch:
            manager.scan()
            launch.assert_not_called()
            manager.connect_deepseek()
            launch.assert_called_once()
            self.assertNotIn('restart', launch.call_args.kwargs)
            self.assertEqual((self.old.directory/'mobile-host.mjs').read_bytes(),
                             Path(__file__).resolve().parents[1].joinpath('bridge/integrations/deepseek-host.mjs').read_bytes())
            self.assertEqual(manager.config['setup']['deepseek']['requiredBridgeRevision'], 3)


    def test_passive_discovery_does_not_create_missing_profile_or_connector(self):
        home = self.root/'not-initialized'
        adapter = DeepSeek(self.root/'uncreated-connector', home)
        result = adapter.discover_existing()
        self.assertFalse(result['installed']); self.assertFalse(result['desktopProfileReady'])
        self.assertFalse(home.exists()); self.assertFalse(adapter.directory.exists())

    def test_passive_discovery_reuses_known_old_connector_without_writes(self):
        self.legacy()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.old.directory.iterdir()}
        result = self.new.discover_existing()
        self.assertTrue(result['installed']); self.assertTrue(result['updateRequired'])
        self.assertEqual(self.new.directory, self.old.directory)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_passive_discovery_of_unconfigured_profile_does_not_install(self):
        profile = self.home/'profiles/desktop/cordis.patch.yml'
        profile.write_text('- id: user-settings\n  config: true\n')
        before = profile.read_bytes()
        result = self.new.discover_existing()
        self.assertFalse(result['installed']); self.assertTrue(result['desktopProfileReady'])
        self.assertEqual(profile.read_bytes(), before)
        self.assertFalse(self.new.directory.exists())

    def test_owned_but_replaced_connector_source_is_not_overwritten(self):
        source = self.old.directory/'mobile-host.mjs'
        source.write_text('// user edited connector')
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.old.ensure_installed()
        self.assertEqual(source.read_text(), '// user edited connector')

    def test_loaded_current_connector_clears_stale_in_memory_upgrade_flag(self):
        self.legacy(); self.new.use_existing(self.old.directory)
        self.assertTrue(self.new.legacy_protocol)
        current = Path(__file__).resolve().parents[1]/'bridge/integrations/deepseek-host.mjs'
        (self.old.directory/'mobile-host.mjs').write_bytes(current.read_bytes())
        (self.old.directory/'endpoint.json').write_text('{"port":32123,"pid":4321}')
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(b'{"connected":true,"configured":true,"bridgeRevision":3}')
        with patch('bridge.integrations.deepseek.urllib.request.build_opener', return_value=Opener()):
            self.assertFalse(self.new.call('status')['updateRequired'])
        self.assertFalse(self.new.legacy_protocol)


if __name__ == '__main__':
    unittest.main()
