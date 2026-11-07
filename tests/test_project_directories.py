"""Project selection is passive, scoped to a runtime, and revalidated on create."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.claude.adapter import Claude
from bridge.clients.claude.remote import RemoteClaudeCli
from bridge.clients.claude.cli_session import _profile, project_account_identity
from bridge.clients.errors import BridgeUnavailable
from bridge.features.sessions.create import project_directory

ROOT = Path(__file__).resolve().parents[1]


class ProjectDirectories(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manager = DesktopSessions(self.root)
        self.addCleanup(self.manager.close)
        self.manager.config['enabled'] = {'claude': True, 'deepseek': True}
        self.adapter = self.manager.adapters['claude']
        self.environment = {'surface': 'cli', 'host': 'local', 'hostLabel': '此电脑',
            'name': 'Claude Code', 'origin': 'original-account', 'scope': 'original-account',
            'createRequiresMessage': True}
        self.adapter.project_environments = Mock(side_effect=lambda host_id=None: [dict(self.environment)] if host_id in (None,self.environment['host']) else [])
        self.adapter.call = Mock(return_value={'projects': []})

    def env(self):
        return self.manager.call('claude', 'projects')['environments'][0]['key']

    def test_catalog_refresh_cannot_replay_unknown_creation(self):
        environment=self.env();project=self.manager.register_project('claude',environment,str(self.root))
        body={'id':str(uuid.uuid4()),'projectKey':project['key'],'firstMessage':'FIRST',
              'environment':environment,'model':'chosen','effort':'low','modelCatalogIdentity':'old'}
        self.manager.creation_models=Mock(return_value={'models':[{'id':'chosen','efforts':['low']}], 'cache':{'identity':'old'}})
        self.adapter.call=Mock(side_effect=RuntimeError('lost after dispatch'))
        first=self.manager.call('claude','create',body=body)
        self.assertEqual(first['status'],'unknown')
        second=self.manager.call('claude','create',body={**body,'modelCatalogIdentity':'refreshed','environment':'new-precondition'})
        self.assertEqual(second,first);self.assertEqual(self.adapter.call.call_count,1)

    def test_empty_runtime_can_browse_and_register_without_creating_or_calling_model(self):
        environment = self.env()
        self.adapter.call.reset_mock()
        listing = self.manager.project_directories('claude', environment, str(self.root))
        target = self.root/'new-project'
        with self.assertRaises(ValueError):
            self.manager.register_project('claude', environment, str(target))
        self.assertFalse(target.exists())
        project = self.manager.register_project('claude', environment, str(target), True)
        self.assertEqual(project['environment'], environment)
        self.assertEqual(project['cwd'], str(target))
        self.assertNotIn('origin', project)
        self.assertNotIn('directoryIdentity', project)
        self.assertEqual(listing['path'], str(self.root))
        self.adapter.call.assert_not_called()

    def test_create_uses_original_identity_and_does_not_replay_uncertain_result(self):
        environment = self.env()
        project = self.manager.register_project('claude', environment, str(self.root))
        self.adapter.call.reset_mock()
        self.adapter.call.side_effect = TimeoutError('unknown')
        request = {'id': str(uuid.uuid4()), 'projectKey': project['key'], 'firstMessage': 'Explicit message'}
        self.assertEqual(self.manager.call('claude', 'create', body=request)['status'], 'unknown')
        self.assertEqual(self.manager.call('claude', 'create', body=request)['status'], 'unknown')
        self.adapter.call.assert_called_once()
        body = self.adapter.call.call_args.args[2]
        self.assertEqual(body['directoryProject']['origin'], 'original-account')
        self.assertEqual(body['directoryProject']['directoryIdentity'], project_directory(str(self.root)))
        self.assertNotIn('directoryProject', request)

    def test_changed_account_or_directory_and_unregistered_payload_prevent_dispatch(self):
        environment = self.env()
        project = self.manager.register_project('claude', environment, str(self.root))
        self.environment.update(origin='another-account', scope='another-account')
        self.adapter.call.reset_mock()
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.manager.project_directories('claude', environment, str(self.root))
        result = self.manager.call('claude', 'create', body={'id': str(uuid.uuid4()), 'projectKey': project['key']})
        self.assertEqual(result['status'], 'unknown')
        self.adapter.call.assert_not_called()
        self.environment.update(origin='original-account', scope='original-account')
        self.manager.directory_projects.rows[project['key']]['directoryIdentity']['inode'] += 1
        self.manager.call('claude', 'create', body={'id': str(uuid.uuid4()), 'projectKey': project['key']})
        self.adapter.call.assert_not_called()
        with self.assertRaisesRegex(ValueError, '目录选择器'):
            self.manager.call('claude', 'create', body={'id': str(uuid.uuid4()), 'directoryProject': project})

    def test_disabled_runtime_cannot_browse_or_create_directories(self):
        environment = self.env()
        self.manager.config['enabled']['claude'] = False
        with self.assertRaisesRegex(ValueError, '已关闭'):
            self.manager.register_project('claude', environment, str(self.root/'never-created'), True)
        self.assertFalse((self.root/'never-created').exists())

    def test_trust_requires_registered_unchanged_directory_and_environment(self):
        environment = self.env()
        project = self.manager.register_project('claude', environment, str(self.root))
        self.adapter.call.reset_mock()
        self.manager.project_trust('claude', project['key'], False)
        args = self.adapter.call.call_args.args
        self.assertEqual(args[:2], ('project-trust', None))
        self.assertIs(args[2]['confirmed'], False)
        self.assertEqual(args[2]['directoryProject']['cwd'], str(self.root))
        self.adapter.call.reset_mock()
        for key, confirmed in [('unknown', True), (project['key'], 'true')]:
            with self.assertRaises(ValueError):
                self.manager.project_trust('claude', key, confirmed)
        self.environment['scope'] = 'changed-account'
        with self.assertRaises(ValueError):
            self.manager.project_trust('claude', project['key'], True)
        self.environment['scope'] = 'original-account'
        self.manager.directory_projects.rows[project['key']]['directoryIdentity']['inode'] += 1
        with self.assertRaises(ValueError):
            self.manager.project_trust('claude', project['key'], True)
        self.adapter.call.assert_not_called()

    def test_remote_browse_stays_on_selected_alias_and_passes_paths_as_encoded_data(self):
        self.environment.update(host='ssh:one', alias='fixture-alias', scope='fixture-alias|original-account')
        hosts = Mock(); hosts.hosts.return_value = {'ssh:one': {'alias': 'fixture-alias'}}
        self.adapter.hosts = hosts
        environment = self.env()
        literal = '/project/quoted " $(never-execute)'
        identity = {'cwd': literal, 'device': 7, 'inode': 11}
        with patch('bridge.clients.manager.ssh_read', return_value={'path': literal}) as remote:
            self.manager.project_directories('claude', environment, literal)
            self.assertEqual(remote.call_args.args[0], 'fixture-alias')
            self.assertNotIn(literal, remote.call_args.args[1])
        with patch('bridge.clients.manager.ssh_read', return_value=identity):
            project = self.manager.register_project('claude', environment, literal)
        self.assertEqual(project['host'], 'ssh:one')
        hosts.hosts.return_value = {'ssh:one': {'alias': 'different-host'}}
        with patch('bridge.clients.manager.ssh_read') as remote:
            with self.assertRaisesRegex(ValueError, '已变化'):
                self.manager.register_project('claude', environment, literal)
            remote.assert_not_called()

    def test_ssh_cli_environment_exists_without_any_session_or_project(self):
        hosts = Mock(); hosts.hosts.return_value = {'ssh:empty': {'alias': 'fixture', 'displayName': 'Empty'}}
        remote = RemoteClaudeCli(hosts)
        with patch('bridge.clients.claude.remote._remote', return_value={
                'profile': {'origin': 'ssh-profile'}, 'authReady': True, 'sessions': []}) as request:
            environments = remote.project_environments()
        self.assertEqual(environments[0]['host'], 'ssh:empty')
        self.assertEqual(environments[0]['origin'], 'ssh-profile')
        request.assert_called_once_with('fixture', {'operation': 'projects'}, timeout=12)


class ClaudeDirectoryCreation(unittest.IsolatedAsyncioTestCase):
    async def test_trust_uses_native_api_without_starting_and_rejects_other_surfaces(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temporary:
            adapter = Claude(Path(temporary))
            adapter.desktop = Mock(capabilities={'code': ['mobileProjectTrust']})
            adapter.desktop.call = AsyncMock(return_value={'trusted': True})
            with patch.object(adapter, 'status', return_value={'connected': True}):
                project = {'surface': 'code', 'host': 'local', 'cwd': temporary}
                await adapter.dispatch('project-trust', None, {'directoryProject': project, 'confirmed': True})
                adapter.desktop.call.assert_awaited_once_with('code', 'mobileProjectTrust', temporary, True)
                for change in [{'surface': 'cowork'}, {'surface': 'cli'}, {'host': 'ssh:one'}]:
                    with self.assertRaises(BridgeUnavailable):
                        await adapter.dispatch('project-trust', None, {'directoryProject': {**project, **change}, 'confirmed': True})

    async def test_new_directory_starts_selected_native_surface_without_listing_sessions(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        adapter = Claude(Path(temporary.name))
        adapter.desktop = Mock(connected=True, capabilities={'code': ['start'], 'cowork': ['start']})
        adapter.desktop.call = AsyncMock(return_value={'sessionId': 'new-native'})
        adapter.listing = AsyncMock(side_effect=AssertionError('No prior project is required'))
        with patch.object(adapter, 'status', return_value={'connected': True}):
            for surface in ('code', 'cowork'):
                await adapter.dispatch('create', None, {'title': 'New project', 'firstMessage': 'Explicit message',
                    'directoryProject': {'surface': surface, 'host': 'local', 'cwd': temporary.name}})
                kind, action, options = adapter.desktop.call.call_args.args
                self.assertEqual((kind, action), (surface, 'start'))
                self.assertEqual(options['message'], 'Explicit message')
                self.assertEqual(options.get('cwd') or options['userSelectedFolders'][0], temporary.name)
        adapter.listing.assert_not_called()


class ClaudeActualProfileIdentity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root/'native-profile'
        self.config.mkdir()
        (self.config/'settings.json').write_text('{}')
        self.manager = DesktopSessions(self.root)
        self.addCleanup(self.manager.close)
        self.manager.config['enabled'] = {'claude': True}
        self.adapter = self.manager.adapters['claude']
        patches = [patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config)}, clear=True),
                   patch('bridge.clients.claude.cli_session.shutil.which', return_value='/fixture/claude'),
                   patch.object(self.adapter, 'status', return_value={'connected': False}),
                   patch.object(self.adapter.cli, '_ready', return_value=True),
                   patch.object(self.adapter.remote_cli, 'project_environments', return_value=[])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.status = {'loggedIn': True, 'authMethod': 'api_key', 'apiProvider': 'firstParty'}
        self.auth = patch('bridge.clients.claude.remote_worker.auth_status', side_effect=lambda *args: dict(self.status))
        self.auth.start()
        self.addCleanup(self.auth.stop)

    def environment(self):
        return self.manager._project_environments('claude')[0]

    def test_actual_native_profile_api_key_and_endpoint_changes_invalidate_registered_directory(self):
        os.environ.update(ANTHROPIC_API_KEY='fixture-secret-A', ANTHROPIC_BASE_URL='https://api-one.invalid')
        original_profile = _profile()
        original = self.environment()
        project = self.manager.register_project('claude', original['key'], str(self.root))
        os.environ['ANTHROPIC_API_KEY'] = 'fixture-secret-B'
        self.assertEqual(_profile()['origin'], original_profile['origin'])
        changed = self.environment()
        self.assertNotEqual(changed['key'], original['key'])
        with patch.object(self.adapter, 'call') as dispatch:
            result = self.manager.call('claude', 'create', body={
                'id': str(uuid.uuid4()), 'projectKey': project['key'], 'firstMessage': 'Explicit message'})
            self.assertEqual(result['status'], 'unknown')
            self.assertIn('已变化', result['message'])
            dispatch.assert_not_called()
        os.environ['ANTHROPIC_BASE_URL'] = 'https://api-two.invalid'
        self.assertNotEqual(self.environment()['key'], changed['key'])
        saved = self.manager.directory_projects.path.read_text()
        for secret in ('fixture-secret-A', 'fixture-secret-B'):
            self.assertNotIn(secret, saved)
            self.assertNotIn(secret, json.dumps(self.manager._public_project(self.environment())))
        with patch('bridge.clients.claude.cli_session.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(BridgeUnavailable, '已变化'):
                self.adapter.cli.create(str(self.root), original_profile['origin'], 'Explicit message',
                    project_directory(str(self.root)), original['accountFingerprint'])
            spawn.assert_not_called()

    def test_actual_native_profile_stable_oauth_account_survives_token_refresh_but_not_account_switch(self):
        self.status.update(authMethod='oauth', accountId='account-A', email='a@example.invalid')
        credentials = self.config/'.credentials.json'
        credentials.write_text('{"accessToken":"old-token"}')
        original_profile = _profile()
        original = self.environment()
        credentials.write_text('{"accessToken":"refreshed-token"}')
        self.assertEqual(_profile()['origin'], original_profile['origin'])
        self.assertEqual(self.environment()['key'], original['key'])
        self.status.update(accountId='account-B', email='b@example.invalid')
        self.assertNotEqual(self.environment()['key'], original['key'])

    def test_managed_api_project_identity_ignores_ambient_anthropic_overrides(self):
        self.adapter.cli.profile_resolver = lambda: {**_profile(), 'managedApi': True}
        os.environ.update(ANTHROPIC_API_KEY='ambient-A', ANTHROPIC_BASE_URL='https://ambient-one.invalid')
        original = self.environment()
        os.environ.update(ANTHROPIC_API_KEY='ambient-B', ANTHROPIC_BASE_URL='https://ambient-two.invalid')
        self.assertEqual(self.environment()['key'], original['key'])
        self.assertEqual(project_account_identity(self.adapter.cli.profile_resolver()), original['accountFingerprint'])
