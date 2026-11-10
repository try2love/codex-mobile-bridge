import json
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.accounts.accounts import Accounts, private_bytes, private_json
from bridge.features.accounts.cli_sync import LocalCliSync


class LocalCliSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'codex'
        self.home.mkdir()
        (self.home/'config.toml').write_text('model="before"\n')
        (self.home/'auth.json').write_text('{"OPENAI_API_KEY":"before"}')
        client = SimpleNamespace(socket_path=str(self.root/'missing.sock'))
        cli = SimpleNamespace(lock=threading.RLock(), joined={}, attaching=set(), requests={}, client=client,
                              daemon_may_be_running=Mock(return_value=False))
        self.bridge = SimpleNamespace(codex_home=self.home, data_dir=self.root,
            catalog_reader=SimpleNamespace(executable=self.root/'codex-bin'), remote_bridges={},
            live={}, submissions={}, lock=threading.RLock(), cli_sessions=cli)
        self.accounts = Accounts(self.bridge)
        self.bridge.accounts = self.accounts
        self.sync = LocalCliSync(self.bridge)

    def test_stopped_desktop_switch_uses_saved_files_and_restores_on_failure(self):
        row = self.accounts.add_api({'name': 'Fixture', 'baseUrl': 'https://api.example/v1',
                                     'apiKey': 'fixture-key', 'model': 'fixture-model'})['accounts'][-1]
        before = self.accounts.snapshot_files()
        def prepare(saved, original):
            folder = self.root/'prepared'
            folder.mkdir(exist_ok=True)
            private_bytes(folder/'config.toml', b'model_provider="bridge_api"\nmodel="fixture-model"\n')
            private_json(folder/'auth.json', {'OPENAI_API_KEY': 'fixture-key'})
            return folder
        self.accounts.prepare = prepare
        with patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop, \
             patch.object(self.sync, 'verify', side_effect=ValueError('native mismatch')):
            desktop.return_value.processes.return_value = []
            with self.assertRaisesRegex(ValueError, 'native mismatch'):
                self.sync.apply('codex', row)
        self.assertEqual(self.accounts.snapshot_files(), before)
        self.assertIsNone(self.accounts.index.get('activeId'))
        with patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop, \
             patch.object(self.sync, 'verify', return_value={'status': 'verified'}):
            desktop.return_value.processes.return_value = []
            self.assertEqual(self.sync.apply('codex', row)['status'], 'applied')
        self.assertEqual(self.accounts.index['activeId'], row['id'])
        self.assertEqual(json.loads((self.home/'auth.json').read_text())['OPENAI_API_KEY'], 'fixture-key')

    def test_live_codex_daemon_blocks_shared_config_mutation(self):
        self.bridge.cli_sessions.daemon_may_be_running.return_value = True
        with patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop:
            desktop.return_value.processes.return_value = []
            with self.assertRaisesRegex(ValueError, '后台服务'):
                self.sync.preflight('codex')
        self.bridge.cli_sessions.daemon_may_be_running.assert_called_once_with()

    def test_api_runtime_accepts_only_verified_current_native_client_versions(self):
        client = Mock()
        self.bridge.cli_sessions.client = client
        with patch('bridge.clients.codex.cli_session._managed_cli', return_value=self.root/'codex-bin'), \
             patch('bridge.platforms.codex_daemon_env.read_daemon_environment', return_value=({}, [42, 123])):
            for agent, supported in (('codex-tui/0.164.0', True),
                    ('codex-mobile-bridge-network-verify/0.164.0 (Mac OS)', True),
                    ('codex-mobile-bridge-network-verify/0.165.0', False),
                    ('codex-mobile-bridge-unknown/0.164.0', False)):
                with self.subTest(agent=agent):
                    client.connect.return_value = {'codexHome': str(self.home), 'userAgent': agent}
                    if supported:
                        self.assertEqual(self.sync._api_runtime(), (client, [42, 123]))
                    else:
                        with self.assertRaisesRegex(ValueError, '协议无法核验'):
                            self.sync._api_runtime()

    def test_native_idle_check_rejects_active_background_and_unknown_states(self):
        for status, tasks in (('active', []), ('idle', [{'id': 'background'}]), ('unknown', [])):
            with self.subTest(status=status, tasks=bool(tasks)):
                def request(method, params, **kwargs):
                    if method == 'thread/read':
                        return {'thread': {'id': 'native-thread', 'status': {'type': status}}}
                    if method == 'thread/backgroundTerminals/list':
                        return {'data': tasks}
                    if method == 'thread/goal/get':
                        return {'goal': None}
                    raise AssertionError(method)
                client = Mock()
                client.connect.return_value = {'codexHome': str(self.home)}
                client.loaded_threads.return_value = ['native-thread']
                client.request.side_effect = request
                with self.assertRaises(ValueError):
                    LocalCliSync.check_idle_daemon(client, self.home)
                self.assertNotIn('account/login/start', [call.args[0] for call in client.request.call_args_list])

    def test_codex_api_verification_checks_both_effective_routes(self):
        row = self.accounts.add_api({'name': 'Fixture', 'baseUrl': 'https://api.example/v1',
                                     'apiKey': 'fixture-key', 'model': 'fixture-model'})['accounts'][-1]
        private_json(self.home/'auth.json', {'OPENAI_API_KEY': 'fixture-key'})
        config = {'model_provider': 'bridge_api', 'model': 'fixture-model',
                  'model_providers': {'bridge_api': {'base_url': row['baseUrl']}},
                  'openai_base_url': row['baseUrl']}
        def response(method, _):
            return {'config': config} if method == 'config/read' else {'account': {'type': 'apiKey'}}
        with patch('bridge.features.accounts.cli_sync.ManagedRPC') as rpc:
            rpc.return_value.__enter__.return_value.request.side_effect = response
            self.assertEqual(self.sync.verify('codex', row)['status'], 'verified')
            config['openai_base_url'] = 'https://other.invalid/v1'
            with self.assertRaisesRegex(ValueError, 'API 接入未生效'):
                self.sync.verify('codex', row)

    def test_claude_auth_requires_selected_native_config_directory(self):
        directory = self.root/'native-claude'
        directory.mkdir()
        status = {'loggedIn': True, 'authMethod': 'api_key',
                  'configDirectory': str(self.root/'other-claude')}
        result = SimpleNamespace(returncode=0, stdout=json.dumps(status))
        with patch('bridge.clients.claude.cli_session._profile', return_value={'binary': '/fixture/claude'}), \
             patch('bridge.clients.claude.cli_session.managed_api_env', return_value={}, create=True), \
             patch('bridge.features.accounts.cli_sync.subprocess.run', return_value=result) as run:
            with self.assertRaisesRegex(ValueError, '配置目录无法核验'):
                self.sync._claude_auth(directory, 'api_key')
            status['configDirectory'] = None
            result.stdout = json.dumps(status)
            with self.assertRaisesRegex(ValueError, '配置目录无法核验'):
                self.sync._claude_auth(directory, 'api_key')
            status['configDirectory'] = str(directory)
            result.stdout = json.dumps(status)
            self.assertEqual(self.sync._claude_auth(directory, 'api_key')['authMethod'], 'api_key')
        self.assertEqual(run.call_args.kwargs['env']['CLAUDE_CONFIG_DIR'], str(directory))

    def test_claude_auth_without_directory_field_requires_negative_control(self):
        directory = self.root/'native-claude'
        directory.mkdir()
        control_dirs = []
        control_logged_in = [False]
        def native_status(*_, **kwargs):
            self.assertEqual(kwargs['encoding'], 'utf-8')
            selected = kwargs['env']['CLAUDE_CONFIG_DIR']
            if selected == str(directory):
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    'loggedIn': True, 'authMethod': 'api_key'}))
            control_dirs.append(selected)
            return SimpleNamespace(returncode=0 if control_logged_in[0] else 1, stdout=json.dumps({
                'loggedIn': control_logged_in[0],
                'authMethod': 'api_key' if control_logged_in[0] else 'none'}))
        with patch('bridge.clients.claude.cli_session._profile', return_value={'binary': '/fixture/claude'}), \
             patch('bridge.clients.claude.cli_session.managed_api_env', return_value={}, create=True), \
             patch('bridge.features.accounts.cli_sync.subprocess.run', side_effect=native_status):
            self.assertEqual(self.sync._claude_auth(directory, 'api_key')['authMethod'], 'api_key')
            control_logged_in[0] = True
            with self.assertRaisesRegex(ValueError, '配置目录无法核验'):
                self.sync._claude_auth(directory, 'api_key')
        self.assertEqual(len(control_dirs), 2)
        self.assertTrue(all(not Path(path).exists() for path in control_dirs))

    def test_claude_auth_negative_control_rejects_unreadable_native_status(self):
        directory = self.root/'native-claude'
        directory.mkdir()
        selected = SimpleNamespace(returncode=0, stdout=json.dumps({
            'loggedIn': True, 'authMethod': 'api_key'}))
        from subprocess import TimeoutExpired
        failures = ('', '{', TimeoutExpired('claude', 10))
        with patch('bridge.clients.claude.cli_session._profile', return_value={'binary': '/fixture/claude'}), \
             patch('bridge.clients.claude.cli_session.managed_api_env', return_value={}, create=True):
            for failure in failures:
                with self.subTest(failure=type(failure).__name__), \
                     patch('bridge.features.accounts.cli_sync.subprocess.run', side_effect=[
                         selected, failure if isinstance(failure, Exception) else
                         SimpleNamespace(returncode=1, stdout=failure)]):
                    with self.assertRaisesRegex(ValueError, '认证状态无法核验'):
                        self.sync._claude_auth(directory, 'api_key')

    def test_claude_auth_negative_control_rejects_error_exit_with_logged_out_json(self):
        directory = self.root/'native-claude'
        directory.mkdir()
        selected = SimpleNamespace(returncode=0, stdout=json.dumps({
            'loggedIn': True, 'authMethod': 'api_key'}))
        failed_control = SimpleNamespace(returncode=2, stdout=json.dumps({
            'loggedIn': False, 'authMethod': 'none'}))
        with patch('bridge.clients.claude.cli_session._profile', return_value={'binary': '/fixture/claude'}), \
             patch('bridge.clients.claude.cli_session.managed_api_env', return_value={}, create=True), \
             patch('bridge.features.accounts.cli_sync.subprocess.run', side_effect=[selected, failed_control]):
            with self.assertRaisesRegex(ValueError, '配置目录无法核验'):
                self.sync._claude_auth(directory, 'api_key')

    def test_claude_auth_rejects_wrong_selected_method_before_control(self):
        directory = self.root/'native-claude'
        directory.mkdir()
        wrong = SimpleNamespace(returncode=0, stdout=json.dumps({
            'loggedIn': True, 'authMethod': 'oauth_token'}))
        with patch('bridge.clients.claude.cli_session._profile', return_value={'binary': '/fixture/claude'}), \
             patch('bridge.clients.claude.cli_session.managed_api_env', return_value={}, create=True), \
             patch('bridge.features.accounts.cli_sync.subprocess.run', return_value=wrong) as run:
            with self.assertRaisesRegex(ValueError, '未识别'):
                self.sync._claude_auth(directory, 'api_key')
        run.assert_called_once()

    def test_managed_claude_api_is_materialized_and_restorable(self):
        identifier = 'a'*32
        directory = self.root/'native-claude'
        directory.mkdir()
        private_json(directory/'settings.json', {'hooks': {'PreToolUse': []},
            'env': {'UNRELATED': 'keep', 'ANTHROPIC_API_KEY': 'native-key'}})
        original = (directory/'settings.json').read_bytes()
        secret = self.root/'cli-access'/'claude'/identifier/'api.json'
        private_json(secret, {'key': 'fixture-key'})
        row = {'id': identifier, 'kind': 'api', 'directory': str(directory),
               'secretPath': str(secret),
               'baseUrl': 'https://api.example', 'model': 'fixture-model', 'authScheme': 'bearer'}
        backup = self.sync.snapshot('claude')
        self.sync.apply('claude', row)
        with patch.object(self.sync, '_claude_auth', return_value={'loggedIn': True}) as auth:
            self.assertEqual(self.sync.verify('claude', row)['status'], 'configured')
        auth.assert_called_once_with(directory.resolve(), 'oauth_token')
        settings = json.loads((directory/'settings.json').read_text())
        self.assertEqual(settings['env']['ANTHROPIC_AUTH_TOKEN'], 'fixture-key')
        self.assertNotIn('ANTHROPIC_API_KEY', settings['env'])
        self.assertEqual(settings['env']['UNRELATED'], 'keep')
        self.assertEqual(settings['hooks'], {'PreToolUse': []})
        self.sync.restore('claude', backup)
        self.assertEqual((directory/'settings.json').read_bytes(), original)

    def test_claude_switch_closes_only_idle_gateway_owned_processes(self):
        idle = Mock()
        idle.process.poll.return_value = None
        idle.condition = threading.Condition()
        idle.active = False
        idle.pending = {}
        active = Mock()
        active.process.poll.return_value = None
        active.condition = threading.Condition()
        active.active = True
        active.pending = {}
        cli = SimpleNamespace(lock=threading.RLock(), owned={'idle': idle, 'active': active}, auth_cache='cached')
        self.bridge.desktop_sessions = SimpleNamespace(adapters={'claude': SimpleNamespace(cli=cli)})
        with self.assertRaisesRegex(ValueError, '先结束'):
            self.sync.preflight('claude')
        with self.assertRaisesRegex(ValueError, '先结束'):
            self.sync._close_claude_idle()
        idle.close.assert_not_called()
        active.close.assert_not_called()
        cli.owned.pop('active')
        self.sync.preflight('claude')
        self.sync._close_claude_idle()
        idle.close.assert_called_once_with()
        self.assertFalse(cli.owned)
        self.assertIsNone(cli.auth_cache)

    def test_first_api_switch_creates_missing_default_profile_then_rolls_back(self):
        identifier = 'b'*32
        directory = self.root/'.claude'
        secret = self.root/'cli-access'/'claude'/identifier/'api.json'
        private_json(secret, {'key': 'fixture-key'})
        row = {'id': identifier, 'kind': 'api', 'directory': str(directory),
               'secretPath': str(secret), 'baseUrl': 'https://fixture.invalid',
               'model': 'fixture-model', 'authScheme': 'x-api-key'}
        native = {'directory': directory, 'origin': 'default:'+str(directory)+':none',
                  'binary': '/fixture/claude'}
        with patch('bridge.features.accounts.cli_sync.Path.home', return_value=self.root), \
             patch('bridge.clients.claude.cli_session._profile', return_value=native), \
             patch.object(self.sync, '_claude_auth', return_value={'loggedIn': True}):
            self.assertEqual(self.sync.apply('claude', row)['status'], 'applied')
            self.assertTrue((directory/'settings.json').is_file())
            self.assertEqual(self.sync.verify('claude', row)['status'], 'configured')
            self.sync.restore('claude', {})
        self.assertFalse(directory.exists())
        row['directory'] = str(self.root/'different-missing')
        with self.assertRaisesRegex(ValueError, '配置目录不可用'):
            self.sync.apply('claude', row)

    def test_desktop_claude_import_requires_active_verified_api_snapshot(self):
        directory = self.root/'desktop-profile'
        directory.mkdir()
        config = {'inferenceGatewayBaseUrl': 'https://gateway.example',
                  'inferenceGatewayApiKey': 'fixture-secret',
                  'inferenceGatewayAuthScheme': 'x-api-key',
                  'inferenceModels': ['fixture-model']}
        digest = hashlib.sha256(json.dumps(config, sort_keys=True,
                                            separators=(',', ':')).encode()).hexdigest()
        private_json(directory/'gateway.json', {'config': config})
        private_json(directory/'manifest.json', {'fingerprint': digest})
        store = Mock()
        store.public.return_value = {'activeId': 'desktop-api', 'current': {'kind': 'api'}}
        store._account.return_value = ({'id': 'desktop-api', 'kind': 'api', 'name': 'Desktop API',
                                         'fingerprint': digest}, directory)
        imported = self.sync.import_desktop_claude('desktop-api', store)
        self.assertEqual(imported['authScheme'], 'x-api-key')
        self.assertEqual(imported['model'], 'fixture-model')
        store.public.return_value = {'activeId': 'other', 'current': {'kind': 'official'}}
        with self.assertRaisesRegex(ValueError, '官方登录不能复制'):
            self.sync.import_desktop_claude('desktop-api', store)


class ManagedApiSwitchTests(unittest.TestCase):
    def setUp(self):
        LocalCliSyncTests.setUp(self)
        self.bridge.account = SimpleNamespace(lock=threading.RLock())
        self.bridge.cli_sessions.daemon_status = Mock(return_value='running')
        self.first = self.accounts.add_api({'name': 'A', 'baseUrl': 'https://fixture.invalid/v1',
            'apiKey': 'fixture-A', 'model': 'fixture-model'})['accounts'][-1]
        self.target = self.accounts.add_api({'name': 'B', 'baseUrl': 'https://fixture.invalid/v1',
            'apiKey': 'fixture-B', 'model': 'fixture-model'})['accounts'][-1]
        private_json(self.home/'auth.json', {'OPENAI_API_KEY': 'fixture-A'})
        self.accounts.mark_active(self.first['id'])
        self.config = {'model_provider': 'bridge_api', 'model': 'fixture-model',
            'cli_auth_credentials_store': 'file', 'openai_base_url': self.target['baseUrl'],
            'model_providers': {'bridge_api': {'base_url': self.target['baseUrl'],
                'requires_openai_auth': True, 'wire_api': 'responses'}}}
        self.native_key = 'fixture-A'
        self.writes = []
        self.failure = None
        self.generation = [421, 1234, [12345, 1]]
        self.client = Mock()
        def request(method, params, **kwargs):
            if method == 'config/read':
                return {'config': self.config}
            if method == 'getAuthStatus':
                return {'authMethod': 'apikey', 'authToken': self.native_key if params['includeToken'] else None}
            if method == 'account/login/start':
                receipt = json.loads((self.accounts.root/'switch.json').read_text())
                self.assertEqual(receipt['phase'], 'authUnknown')
                self.assertEqual(json.loads((self.accounts.root/'requests.json').read_text())[receipt['requestId']], self.target['id'])
                self.writes.append(params['apiKey'])
                self.native_key = params['apiKey']
                private_json(self.home/'auth.json', {'OPENAI_API_KEY': params['apiKey']})
                if self.failure:
                    raise self.failure
                return {'type': 'apiKey'}
            raise AssertionError(method)
        self.client.request.side_effect = request
        for name, values in (('_codex_runtime', {'return_value': (False, None)}),
                             ('_api_runtime', {'side_effect': lambda: (self.client, self.generation)}),
                             ('check_idle_daemon', {'return_value': None})):
            patcher = patch.object(LocalCliSync, name, **values)
            patcher.start()
            self.addCleanup(patcher.stop)

    def value(self, **changes):
        import uuid
        return {'id': self.target['id'], 'confirmed': True, 'tasksConfirmed': True,
                'requestId': str(uuid.uuid4()), **changes}

    def test_same_provider_api_switch_receipt_precedes_write_and_marks_verified(self):
        value = self.value()
        self.accounts.switch(value)
        self.assertEqual(self.writes, ['fixture-B'])
        self.assertEqual(self.accounts.state['phase'], 'complete')
        self.assertEqual(self.accounts.index['activeId'], self.target['id'])
        self.assertEqual(self.bridge.cli_sessions.configuration_stamp, {
            'config.toml': hashlib.sha256((self.home/'config.toml').read_bytes()).hexdigest(),
            'auth.json': hashlib.sha256((self.home/'auth.json').read_bytes()).hexdigest()})
        self.accounts.switch(value)
        self.assertEqual(self.writes, ['fixture-B'])
        self.assertNotIn('fixture-B', (self.accounts.root/'switch.json').read_text())
        self.assertNotIn('fixture-B', json.dumps(self.accounts.public()))

    def test_unknown_effect_reconciles_only_from_same_native_cached_key(self):
        from bridge.clients.codex.app_server import AppServerUnknownResult
        self.failure = AppServerUnknownResult('lost response')
        value = self.value()
        with self.assertRaisesRegex(ValueError, '尚未核验'):
            self.accounts.switch(value)
        self.assertEqual(self.accounts.state['phase'], 'authUnknown')
        self.assertIsNone(self.accounts.public()['activeId'])
        self.assertEqual(self.accounts.index['activeId'], self.first['id'])
        self.accounts.switch(value)
        self.assertEqual(self.writes, ['fixture-B'])
        self.assertEqual(self.accounts.state['phase'], 'complete')
        self.assertEqual(self.accounts.index['activeId'], self.target['id'])

    def test_hot_baseline_does_not_absorb_a_later_unverified_config_change(self):
        verified_config = hashlib.sha256((self.home/'config.toml').read_bytes()).hexdigest()
        mark = self.accounts.mark_active
        def changed_after_verification(identifier):
            (self.home/'config.toml').write_text('model="external-change"\n')
            mark(identifier)
        self.accounts.mark_active = changed_after_verification
        self.accounts.switch(self.value())
        self.assertEqual(self.bridge.cli_sessions.configuration_stamp['config.toml'], verified_config)
        self.assertNotEqual(self.bridge.cli_sessions.configuration_stamp['config.toml'],
                            hashlib.sha256((self.home/'config.toml').read_bytes()).hexdigest())

    def test_disk_key_or_account_type_does_not_clear_unknown_or_replay(self):
        from bridge.clients.codex.app_server import AppServerUnknownResult
        self.failure = AppServerUnknownResult('lost response')
        value = self.value()
        with self.assertRaises(ValueError):
            self.accounts.switch(value)
        self.native_key = 'fixture-A'
        with self.assertRaisesRegex(ValueError, '尚未使用'):
            self.accounts.switch(value)
        self.assertEqual(self.writes, ['fixture-B'])
        self.assertEqual(self.accounts.state['phase'], 'authUnknown')
        with self.assertRaisesRegex(ValueError, '同一次'):
            self.accounts.switch(self.value())
        self.assertEqual(self.writes, ['fixture-B'])

    def test_unknown_rejects_changed_generation_and_survives_manager_restart(self):
        from bridge.clients.codex.app_server import AppServerUnknownResult
        self.failure = AppServerUnknownResult('lost response')
        value = self.value()
        with self.assertRaises(ValueError):
            self.accounts.switch(value)
        restarted = Accounts(self.bridge)
        self.assertEqual(restarted.state['phase'], 'authUnknown')
        self.generation = [422, 1235, [12346, 1]]
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.accounts.switch(value)
        self.assertEqual(self.writes, ['fixture-B'])

    def test_interruption_before_dispatch_cannot_replay(self):
        def interrupt(*args, **kwargs):
            raise SystemExit('dispatch interruption')
        self.client.request.side_effect = lambda method, params, **kwargs: (
            interrupt() if method == 'account/login/start' else
            {'config': self.config} if method == 'config/read' else
            {'authMethod': 'apikey', 'authToken': 'fixture-A' if params['includeToken'] else None})
        value = self.value()
        with self.assertRaises(SystemExit):
            self.accounts.switch(value)
        self.assertEqual(self.accounts.state['phase'], 'authUnknown')
        with self.assertRaisesRegex(ValueError, '尚未使用'):
            self.accounts.switch(value)
        methods = [call.args[0] for call in self.client.request.call_args_list]
        self.assertEqual(methods.count('account/login/start'), 1)

    def test_account_notification_before_login_response_does_not_hold_cli_reader_lock(self):
        request = self.client.request.side_effect
        delivered = threading.Event()
        def response(method, params, **kwargs):
            if method == 'account/login/start':
                def notification():
                    with self.bridge.cli_sessions.lock:
                        delivered.set()
                reader = threading.Thread(target=notification, daemon=True)
                reader.start()
                if not delivered.wait(1):
                    raise AssertionError('reader blocked before login response')
                reader.join(1)
            return request(method, params, **kwargs)
        self.client.request.side_effect = response
        self.accounts.switch(self.value())
        self.assertTrue(delivered.is_set())
        self.assertEqual(self.accounts.state['phase'], 'complete')

    def test_unknown_saved_credential_version_change_cannot_clear_or_replay(self):
        from bridge.clients.codex.app_server import AppServerUnknownResult
        self.failure = AppServerUnknownResult('lost response')
        value = self.value()
        with self.assertRaises(ValueError):
            self.accounts.switch(value)
        private_json(self.accounts.directory(self.target['id'])/'api.json', {'key': 'replacement'})
        self.native_key = 'replacement'
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.accounts.switch(value)
        self.assertEqual(self.writes, ['fixture-B'])
        self.assertNotIn('keyHash', json.dumps(self.accounts.public()))

    def test_hot_switch_requires_task_confirmation_and_blocks_unknown_work(self):
        with self.assertRaisesRegex(ValueError, '确认'):
            self.accounts.switch(self.value(tasksConfirmed=False))
        self.accounts.blockers = Mock(return_value=[{'title': 'Unknown', 'reason': 'unknown'}])
        with self.assertRaisesRegex(ValueError, '待发送'):
            self.accounts.switch(self.value())
        self.assertEqual(self.writes, [])

    def test_idle_joined_thread_keeps_its_connection_during_hot_sync(self):
        joined = {'idle-thread': {'status': 'idle'}}
        self.bridge.cli_sessions.joined = joined
        self.bridge.cli_sessions.requests = {'idle-thread': {}}
        self.accounts.switch(self.value())
        self.assertIs(self.bridge.cli_sessions.joined, joined)
        self.assertEqual(self.writes, ['fixture-B'])
        self.assertEqual(self.accounts.state['phase'], 'complete')

    def test_unsettled_cli_request_still_blocks_hot_sync(self):
        self.bridge.cli_sessions.requests = {'idle-thread': {'approval': {'id': 'approval'}}}
        with self.assertRaisesRegex(ValueError, '待处理请求'):
            self.accounts.switch(self.value())
        self.assertEqual(self.writes, [])

    def test_native_active_or_background_work_blocks_even_an_idle_joined_view(self):
        self.bridge.cli_sessions.joined = {'idle-thread': {'status': 'idle'}}
        for reason in ('运行中的任务', '有后台任务或状态未知', '状态未知'):
            with self.subTest(reason=reason), patch.object(LocalCliSync, 'check_idle_daemon', side_effect=ValueError(reason)):
                with self.assertRaisesRegex(ValueError, reason):
                    self.accounts.switch(self.value())
        self.assertEqual(self.writes, [])
        self.assertFalse((self.accounts.root/'requests.json').exists())

    def test_injection_change_cannot_use_hot_login(self):
        self.config['model_providers']['bridge_api']['env_key'] = 'NEW_ENV_KEY'
        self.assertFalse(self.sync.switch_live_api(self.target, self.value()['requestId']))
        self.assertEqual(self.writes, [])

    def test_recovery_target_verifies_file_source_and_only_known_aliases(self):
        target = self.sync.recovery_target(self.config)
        self.assertEqual(target['accountId'], self.first['id'])
        self.assertEqual(target['provider'], 'bridge_api')
        self.assertEqual(target['aliases'], ['bridge_api', 'openai'])
        self.assertEqual(target['environment'], {'OPENAI_API_KEY': None, 'OPENAI_BASE_URL': None,
                                                'CODEX_API_KEY': None, 'CODEX_ACCESS_TOKEN': None})
        self.assertNotIn('fixture-A', json.dumps(target))
        self.config['model_providers']['openai'] = {'base_url': 'https://other.invalid/v1'}
        self.assertEqual(self.sync.recovery_target(self.config)['aliases'], ['bridge_api'])
        self.config['model_providers']['bridge_api']['env_http_headers'] = {'Authorization': 'NEW_TOKEN'}
        with self.assertRaisesRegex(ValueError, '注入来源'):
            self.sync.recovery_target(self.config)

    def test_recovery_target_rejects_unselected_or_changed_managed_files(self):
        self.accounts.index['activeId'] = None
        self.assertIsNone(self.sync.recovery_target(self.config))
        self.accounts.index['activeId'] = self.first['id']
        private_json(self.home/'auth.json', {'OPENAI_API_KEY': 'disk-only'})
        self.assertIsNone(self.sync.recovery_target(self.config))

    def test_runtime_auth_compares_cached_key_not_disk_or_type(self):
        target = self.sync.recovery_target(self.config)
        self.assertTrue(self.sync.verify_runtime_auth(self.client, target))
        self.native_key = 'other-cached-key'
        with self.assertRaisesRegex(ValueError, '尚未使用'):
            self.sync.verify_runtime_auth(self.client, target)


if __name__ == '__main__':
    unittest.main()
