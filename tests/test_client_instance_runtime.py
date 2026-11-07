"""Independent native owners use isolated fixtures; no Apps or models run."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.profiles import resolve_profile
from bridge.clients.profile_process import scope_processes, require_owned

ROOT = Path(__file__).resolve().parents[1]


class InstanceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(self.manager.close)
        self.manager.instances.registry.defaults = lambda: ()
        self.binary = self.root/'app'
        self.binary.write_bytes(b'fixture')
        self.binary.chmod(0o700)
        self.manager.config['discovered'] = {
            family: {'id': family, 'installed': True, 'executable': str(self.binary),
                     'dataDirectory': str(self.root/'default')}
            for family in ('claude', 'deepseek')}
        p = patch('bridge.clients.lifecycle.inspect_clients', return_value={})
        p.start(); self.addCleanup(p.stop)

    def register(self, family, suffix):
        home = self.root/suffix
        home.mkdir()
        return self.manager.instance_control({'operation': 'save', 'family': family,
            'kind': 'desktop-profile', 'home': str(home), 'label': suffix})['instance']

    def test_registration_and_navigation_never_construct_execution_owner(self):
        row = self.register('deepseek', 'one')
        with patch('bridge.clients.manager.DesktopSessions', side_effect=AssertionError('constructed')):
            self.assertFalse(self.manager.enabled(row['id']))
            self.assertEqual(self.manager.instances.children, {})
            self.assertEqual(self.manager.instance_rows()[0]['id'], row['id'])
            self.assertNotIn(row['id'], self.manager.navigation_clients())
            self.assertFalse((self.manager.directory.parent/'client-instances'/row['id'].replace(':','-')/'desktop-sessions').exists())

    def test_two_native_owners_use_distinct_account_cache_upload_and_receipt_roots(self):
        a, b = self.register('deepseek', 'one'), self.register('deepseek', 'two')
        first = self.manager.resolve_instance('deepseek', a['id'], require_enabled=False)
        second = self.manager.resolve_instance('deepseek', b['id'], require_enabled=False)
        self.assertIsNot(first, second)
        self.assertIs(first, self.manager.resolve_instance('deepseek', a['id'], require_enabled=False))
        self.assertIsNone(first.bridge)
        self.assertEqual(set(first.adapters), {'deepseek'})
        self.assertEqual(first.adapters['deepseek'].home, self.root/'one')
        self.assertEqual(second.adapters['deepseek'].home, self.root/'two')
        self.assertNotEqual(first.directory, second.directory)
        self.assertIsNot(first.uploads, second.uploads)
        self.assertNotEqual(first._account_store('deepseek').directory, second._account_store('deepseek').directory)
        self.assertNotEqual(first._descriptor('deepseek')['profileLaunchReceipt'], second._descriptor('deepseek')['profileLaunchReceipt'])
        self.assertEqual(first.instance.gateway_root, self.manager.directory.parent)
        self.assertNotEqual(first.instance.gateway_root, first.instance.state_root)

    def test_enabled_source_is_same_child_settings_and_disabled_requires_explicit_route(self):
        row = self.register('deepseek', 'one')
        child = self.manager.resolve_instance('deepseek', row['id'], require_enabled=False)
        with self.assertRaises(ValueError):
            self.manager.resolve_instance('deepseek', row['id'])
        with patch.object(self.manager, 'clients', return_value={}), patch('bridge.clients.lifecycle.launch_client') as launch:
            self.manager.toggle_client({'provider': row['id'], 'enabled': True})
        launch.assert_not_called()
        self.assertTrue(self.manager.enabled(row['id']))
        self.assertTrue(child.enabled('deepseek'))
        self.assertIs(child, self.manager.resolve_instance('deepseek', row['id']))
        self.assertNotIn('deepseek', self.manager.config.get('enabled', {}))
        settings = json.loads(child.config_path.read_text())
        self.assertTrue(settings['enabled']['deepseek'])
        self.assertNotIn('enabled', self.manager.instances.registry._load()[0])

    def test_claude_cannot_borrow_default_cli_ssh_or_root_guard(self):
        with patch('bridge.clients.instances.sys', SimpleNamespace(platform='darwin')):
            row = self.register('claude', 'one')
        root_owner = object()
        root_guard = object()
        bridge = self.manager.bridge = SimpleNamespace(desktop_sessions=root_owner,
                                                     accounts=SimpleNamespace(require_client_enabled=root_guard))
        with patch('bridge.clients.instances.sys', SimpleNamespace(platform='darwin')), \
             patch('bridge.clients.claude.adapter.inspect_installation', return_value={'installed': True}):
            child = self.manager.resolve_instance('claude', row['id'], require_enabled=False)
        adapter = child.adapters['claude']
        self.assertIsNone(adapter.cli.profile_resolver())
        self.assertIsNone(adapter.hosts)
        self.assertFalse(adapter.remote_cli.available())
        self.assertEqual(adapter.cli.listing()['sessions'], [])
        self.assertIs(bridge.desktop_sessions, root_owner)
        self.assertIs(bridge.accounts.require_client_enabled, root_guard)
        self.assertIsNone(child.claude_wsl)
        self.assertIsNone(child.bridge)
        self.manager.bridge = None

    def test_explicit_bad_or_cross_family_entry_never_falls_back(self):
        row = self.register('deepseek', 'one')
        for family, entry in [('claude', row['id']), ('deepseek', 'deepseek:'+'0'*32),
                              ('deepseek', ''), ('zcode', 'zcode:'+'0'*32)]:
            with self.subTest(family=family, entry=entry), self.assertRaises(ValueError):
                self.manager.resolve_instance(family, entry, require_enabled=False)
        self.assertIs(self.manager, self.manager.resolve_instance('deepseek', require_enabled=False))

    def test_child_rechecks_directory_identity_before_routes(self):
        row = self.register('deepseek', 'one')
        child = self.manager.resolve_instance('deepseek', row['id'], require_enabled=False)
        (self.root/'one').rename(self.root/'previous')
        (self.root/'one').mkdir()
        with self.assertRaisesRegex(ValueError, '身份'):
            self.manager.resolve_instance('deepseek', row['id'], require_enabled=False)
        with self.assertRaisesRegex(ValueError, '身份'):
            child._descriptor('deepseek')

    def test_notifications_keep_family_icon_and_full_instance_host(self):
        row = self.register('deepseek', 'one')
        child = self.manager.resolve_instance('deepseek', row['id'], require_enabled=False)
        self.manager.instances.registry.configure(row['id'], True)
        self.manager.gateway_running = child.gateway_running = True
        child.adapters['deepseek'].call = Mock(return_value={'connected': True})
        child._read = Mock(return_value={'sessions': [{'id': 'same-session'}]})
        values = self.manager.notification_rows()
        self.assertEqual(values, [{'id': 'same-session', 'provider': 'deepseek', 'host': 'desktop:'+row['id']}])

    def test_native_account_and_adapter_follow_same_declared_third_party_root(self):
        from bridge.clients.claude.accounts import ClaudeAccounts
        profile = resolve_profile('claude', self.root/'claude-profile')
        profile.home.mkdir()
        (profile.home/'claude_desktop_config.json').write_text('{"deploymentMode":"3p"}')
        store = ClaudeAccounts(self.root/'saved-accounts', profile.home, explicit_home=True, native_profile=profile)
        self.assertEqual(store.home, profile.roots['userData'])
        self.assertEqual(store.threep, profile.roots['thirdParty'])
        self.assertEqual(store.mode_home, profile.roots['userData'])
        from bridge.clients.instances import _claude_mode_home
        self.assertEqual(_claude_mode_home(profile), store.threep)

    def test_dsh_first_enable_prepares_only_selected_connector_slot(self):
        from bridge.clients.instances import prepare_deepseek_profile
        profile = resolve_profile('deepseek', self.root/'fresh')
        profile.home.mkdir()
        prepare_deepseek_profile(profile)
        target = profile.home/'profiles/desktop/cordis.patch.yml'
        self.assertEqual(target.read_text(), '# Bridge native profile connector\n')
        target.write_text('# existing native settings\n')
        prepare_deepseek_profile(profile)
        self.assertEqual(target.read_text(), '# existing native settings\n')
        self.assertFalse((profile.home/'.credentials.yaml').exists())
        self.assertFalse(profile.roots['userData'].exists())

    def test_parent_status_tracks_child_connection_without_enabling_default(self):
        row = self.register('deepseek', 'one')
        child = self.manager.resolve_instance('deepseek', row['id'], require_enabled=False)
        self.manager.instances.registry.configure(row['id'], True)
        child.client_cache = None
        child.adapters['deepseek'].call = Mock(return_value={'connected': True, 'configured': True, 'bridgeRevision': 11})
        child.clients(refresh=True)
        state = self.manager.instance_rows()[0]
        self.assertTrue(state['enabled'])
        self.assertTrue(state['connected'])
        self.assertTrue(state['configured'])
        self.assertEqual(state['id'], row['id'])
        self.assertFalse(self.manager.enabled('deepseek'))

    def test_corrupt_registry_does_not_block_default_but_explicit_target_fails(self):
        row = self.register('deepseek', 'one')
        self.manager.instances.registry.path.write_text('{invalid')
        self.assertIs(self.manager, self.manager.resolve_instance('deepseek', require_enabled=False))
        self.assertEqual(self.manager.instance_rows(), [])
        self.assertIn('codex', self.manager.navigation_clients())
        self.assertTrue(self.manager._descriptor('deepseek')['excludeProfiles'])
        self.assertIsNotNone(self.manager.instances.error)
        with self.assertRaises(ValueError):
            self.manager.resolve_instance('deepseek', row['id'], require_enabled=False)

    def test_registration_rejects_profiles_inside_discovered_application(self):
        application = self.root/'installed'
        application.mkdir()
        executable = application/'DSH.exe'; executable.write_bytes(b'fixture')
        self.manager.config['discovered']['deepseek']['executable'] = str(executable)
        self.manager.config['discovered']['claude']['executable'] = ''
        self.manager.instances.registry.defaults = self.manager.instances.defaults
        forbidden = application/'new-profile'; forbidden.mkdir()
        with self.assertRaises(ValueError):
            self.manager.instance_control({'operation':'save', 'family':'deepseek',
                'kind':'desktop-profile', 'home':str(forbidden), 'label':'invalid installation root'})
        self.assertEqual(self.manager.instances.registry.list(), [])


class ProfileProcessTests(unittest.TestCase):
    def test_dsh_model_writes_use_stored_instance_route_not_shell_or_forged_body(self):
        from bridge.clients.instances import require_deepseek_account
        adapter = Mock()
        sources = [{'id': 'private-upstream', 'credentialSource': 'file', 'credentialRef': 'PRIVATE_API_KEY'},
                   {'id': 'default-upstream', 'credentialSource': 'user-env'}]
        adapter.call.return_value = {'credentialSourcesVersion': 1, 'current': {'id': 'default-upstream'}, 'sources': sources}
        manager = SimpleNamespace(adapters={'deepseek': adapter})
        with self.assertRaisesRegex(ValueError, '所选模型'):
            require_deepseek_account(manager, 'send', 'chat', {'model': 'private-upstream/forged'})
        adapter.call.return_value = {'credentialSourcesVersion': 1, 'current': {'id': 'private-upstream'}, 'sources': sources}
        require_deepseek_account(manager, 'send', 'chat', {})
        require_deepseek_account(manager, 'settings', 'chat', {'model': 'private-upstream/own-model'})
        adapter.call.return_value = {'current': {'id': 'private-upstream'},
                                    'sources': [{'id': 'private-upstream'}]}
        with self.assertRaisesRegex(ValueError, '独立 DSH'):
            require_deepseek_account(manager, 'send', 'chat', {})
        adapter.call.return_value = {'credentialSourcesVersion': 1, 'current': None, 'sources': []}
        with self.assertRaisesRegex(ValueError, '登录账号'):
            require_deepseek_account(manager, 'send', 'chat', {})
        adapter.call.return_value = {'session': {'requests': [{'id': 'approval', 'needsInput': False}]}}
        require_deepseek_account(manager, 'respond', 'chat', {'requestId': 'approval', 'decision': 'decline'})
        # Read/stop/pause must remain available even after credentials disappear.
        for action, body in [('detail', {}), ('stop', {}), ('stop-task', {}), ('goal', {'operation': 'pause'})]:
            require_deepseek_account(manager, action, 'chat', body)

    def test_dsh_private_account_store_does_not_treat_gateway_env_as_its_credential(self):
        from bridge.clients.deepseek.accounts import DeepSeekAccounts
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            root = Path(tmp)
            home = root/'home'; home.mkdir()
            credentials = home/'.credentials.yaml'
            credentials.write_text('{"version":1,"refs":{"DEEPSEEK_API_KEY":"fixture-private-key"},"records":{}}')
            credentials.chmod(0o600)
            with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'fixture-default-key'}):
                store = DeepSeekAccounts(root/'saved', home, inherit_environment=False)
                value = store.public()
                self.assertEqual(value['current']['routes'], ['deepseek-official'])
                self.assertTrue(value['activeIds'])
                store.import_current(kind='api')

    def test_native_launch_removes_only_explicit_inherited_account_inputs(self):
        import subprocess
        from bridge.platforms.posix.desktop import start as posix_start
        from bridge.platforms.windows.desktop import start as windows_start
        from bridge.platforms.macos.desktop import start as macos_start
        environment = {'ANTHROPIC_API_KEY': 'fixture-only', 'PATH': '/bin',
                       'HTTPS_PROXY': 'http://localhost:9', 'HOME': '/home/fixture'}
        with patch.dict('os.environ', environment, clear=True), patch('subprocess.Popen') as popen:
            posix_start(Path('/app'), Path('/profile'), {'CLAUDE_CONFIG_DIR': '/profile/code'},
                        new_instance=True, environment_unset=('ANTHROPIC_API_KEY',))
            passed = popen.call_args.kwargs['env']
            self.assertNotIn('ANTHROPIC_API_KEY', passed)
            self.assertEqual(passed['HOME'], '/home/fixture')
            self.assertEqual(passed['HTTPS_PROXY'], environment['HTTPS_PROXY'])
            self.assertEqual(passed['CLAUDE_CONFIG_DIR'], '/profile/code')
            with patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, create=True):
                windows_start(Path('/app'), Path('/profile'), {}, new_instance=True,
                              environment_unset=('anthropic_api_key',))
            self.assertNotIn('ANTHROPIC_API_KEY', popen.call_args.kwargs['env'])
            with patch('bridge.platforms.macos.desktop._bundle', return_value=Path('/App.app')), \
                 patch('subprocess.run') as run:
                macos_start(Path('/App.app/Contents/MacOS/App'), Path('/profile'), {},
                            new_instance=True, environment_unset=('ANTHROPIC_API_KEY',))
            self.assertIn('ANTHROPIC_API_KEY=', run.call_args.args[0])
            self.assertNotIn('ANTHROPIC_API_KEY', run.call_args.kwargs['env'])
            posix_start(Path('/app'), Path('/profile'), {})
            self.assertEqual(popen.call_args.kwargs['env']['ANTHROPIC_API_KEY'], 'fixture-only')

    def test_selects_one_native_main_and_its_children_without_foreign_default_or_profile(self):
        home = (ROOT/'.tmp/fixture-a').resolve()
        profile = resolve_profile('deepseek', home, platform='linux')
        commands = {10: '/app', 11: '/app --user-data-dir='+str(profile.roots['userData']),
                    12: '/app --type=utility --utility-sub-type=node.mojom.NodeService',
                    13: '/app --user-data-dir='+str(home.with_name('fixture-ab')/'desktop-user-data'),
                    14: '/app --type=utility', 15: '/app --type=renderer'}
        identities = {pid: {'parent': parent, 'start': str(pid)} for pid, parent in
                      [(10, 1), (11, 1), (12, 11), (13, 1), (14, 13), (15, 10)]}
        with patch('bridge.clients.deepseek.recovery._identities', return_value=identities):
            own, _ = scope_processes({'nativeProfile': profile}, list(commands), commands)
            default, _ = scope_processes({'id': 'deepseek', 'dataDirectory': '/default',
                'excludeProfiles': [str(home)]}, list(commands), commands)
        self.assertEqual(set(own), {11, 12})
        self.assertEqual(set(default), {10, 15})

    def test_matching_profile_without_launch_identity_is_not_attached(self):
        descriptor = {'nativeProfile': resolve_profile('deepseek', (ROOT/'.tmp/fixture').resolve(), platform='linux'),
                      'profileLaunchReceipt': '/not-present/receipt.json'}
        with self.assertRaisesRegex(ValueError, '不是由网关启动'):
            require_owned(descriptor, {'running': True, 'unknown': False, 'mainPids': [22]})

    def test_normal_quit_waits_only_for_verified_selected_processes(self):
        from bridge.clients.desktop_app import DesktopApp
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            app = DesktopApp(Path(tmp)/'binary', tmp)
            native = Mock()
            with patch('bridge.clients.desktop_app.sys', SimpleNamespace(platform='linux')), \
                 patch('bridge.clients.desktop_app._native', return_value=native), \
                 patch.object(app, 'processes', side_effect=[[11, 22], [11, 22], [22]]), \
                 patch('bridge.clients.desktop_app.time.sleep') as sleep:
                app.stop(gui_pids=[11], selected_pids=[11])
            native.quit_process.assert_called_once_with(11)
            sleep.assert_not_called()
