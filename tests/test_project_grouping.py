"""Native memberships and registered project roots determine chat list groups."""
import sqlite3
import io
import json
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bridge.clients.codex.remote import AppHosts, RemoteStore
from bridge.features.sessions.store import SessionStore, project_root_key


class ProjectGroupingTests(unittest.TestCase):
    def test_unassigned_chats_share_one_group_regardless_of_directory_or_host_platform(self):
        directories = ['D:/Work/Project/child', 'D:/Other', r'\\?\D:\Work\Other',
                       r'\\server\share\Project', '/work/Project', '/work/project', '']
        for host in ['local', 'ssh']:
            rows = [{'id': str(index), 'cwd': cwd, 'origin': 'desktop', 'model_provider': 'fixture'}
                    for index, cwd in enumerate(directories)]
            self.hosts.decorate(rows, host, host)
            self.assertEqual({row['projectKey'] for row in rows}, {host + '|unassigned'})
            self.assertEqual({row['projectName'] for row in rows}, {'未归类'})
            self.assertEqual([row['cwd'] for row in rows], directories)
            self.assertTrue(all(row['model_provider'] == 'fixture' and row['origin'] == 'desktop' for row in rows))

    def test_empty_native_membership_matches_a_registered_local_root(self):
        row = {'id': 'fixture', 'cwd': 'D:/Work/Project', '_nativeProject': None}
        self.hosts.decorate([row], 'local', 'Local')
        self.assertEqual(row['projectKey'], 'local|local')

    def test_native_assignment_wins_over_saved_legacy_mapping_and_cwd(self):
        row = {'id': 'fixture', 'cwd': 'D:/Work/Project', '_nativeProject': {'id': 'native', 'name': 'Native'}}
        self.hosts.decorate([row], 'local', 'Local')
        self.assertEqual(row['projectKey'], 'local|native')
        self.assertEqual(row['projectName'], 'Native')
        self.assertNotIn('_nativeProject', row)

    def test_explicit_projectless_marker_keeps_thread_outside_inferred_project(self):
        self.saved['projectless-thread-ids'] = ['fixture']
        row = {'id': 'fixture', 'cwd': 'D:/Work/Project', '_nativeProject': None}
        self.hosts.decorate([row], 'local', 'Local')
        self.assertEqual(row['projectKey'], 'local|unassigned')
        self.assertEqual(row['projectName'], '未归类')

    def test_empty_native_membership_preserves_existing_assignment_during_migration(self):
        self.saved['thread-project-assignments'] = {'fixture': {'projectKind': 'local', 'projectId': 'local'}}
        row = {'id': 'fixture', 'cwd': 'D:/Other', '_nativeProject': None}
        self.hosts.decorate([row], 'local', 'Local')
        self.assertEqual(row['projectKey'], 'local|local')
        self.assertEqual(row['projectName'], 'Local')


    def test_native_assignment_wins_over_stale_projectless_marker(self):
        self.saved['projectless-thread-ids'] = ['fixture']
        row = {'id': 'fixture', 'cwd': 'D:/Work/Project', '_nativeProject': {'id': 'native', 'name': 'Native'}}
        self.hosts.decorate([row], 'local', 'Local')
        self.assertEqual(row['projectKey'], 'local|native')

    def test_confirmed_native_removal_overrides_old_migration_assignment(self):
        self.saved['thread-project-assignments'] = {'fixture': {'projectId': 'local'}}
        row = {'id': 'fixture', 'cwd': 'D:/Work/Project', '_nativeProject': None}
        self.hosts.decorate([row], 'local', 'Local', {'fixture'})
        self.assertEqual(row['projectKey'], 'local|unassigned')

    def test_native_and_existing_memberships_share_one_group_during_migration(self):
        self.saved['app-server-project-id-by-legacy-project-id-by-host'] = {self.scope: {'local': 'native'}}
        self.saved['thread-project-assignments'] = {'older-chat': {'projectId': 'local'}}
        rows = [{'id': 'native-chat', 'cwd': 'D:/Other', '_nativeProject': {'id': 'native', 'name': 'Local'}},
                {'id': 'older-chat', 'cwd': 'D:/Work/Project', '_nativeProject': None}]
        self.hosts.decorate(rows, 'local', 'Local')
        self.assertEqual([row['projectKey'] for row in rows], ['local|local', 'local|local'])

    def setUp(self):
        self.hosts = AppHosts(Path('/fixture'))
        self.scope = 'local:' + str(self.hosts.home.resolve())
        self.saved = {
            'local-projects': {'local': {'id': 'local', 'name': 'Local', 'rootPaths': ['D:/Work/Project']}},
            'remote-projects': [{'id': 'remote', 'hostId': 'ssh', 'label': 'Remote',
                                 'remotePath': 'C:/Work/Project'}],
        }
        self.hosts.state = lambda: self.saved

    def decorate(self, cwd, host='local', identifier='fixture'):
        row={'id': identifier, 'cwd': cwd, 'origin': 'desktop'}
        return self.hosts.decorate([row], host, host)[0]

    def test_legacy_secondary_root_must_identify_one_project(self):
        path = 'E:/Second'
        self.saved['local-projects']['local']['rootPaths'].append(path)
        self.assertEqual(self.decorate(path)['projectKey'], 'local|local')
        self.saved['local-projects']['duplicate'] = {'id': 'duplicate', 'name': 'Local', 'rootPaths': [path]}
        self.assertEqual(self.decorate(path)['projectKey'], 'local|unassigned')

    def test_windows_root_spellings_share_a_group_but_canonical_collisions_are_ambiguous(self):
        for path in [r'd:\WORK\Project', r'\\?\D:\Work\Project', '/D:/Work/Project/']:
            self.assertEqual(self.decorate(path)['projectKey'], 'local|local')
        self.saved['local-projects']['local']['rootPaths'].append(r'\\server\Share\Workspace')
        self.assertEqual(self.decorate(r'\\?\UNC\server\share\workspace')['projectKey'], 'local|local')
        self.assertEqual(self.decorate('/mnt/d/Work/Project')['projectKey'], 'local|unassigned')
        self.assertEqual(self.decorate(r'\\?\D:\Work\Project\child')['projectKey'], 'local|unassigned')
        self.saved['local-projects']['duplicate'] = {'id': 'duplicate', 'name': 'Duplicate',
                                                    'rootPaths': [r'\\?\d:\work\PROJECT']}
        self.assertEqual(self.decorate('D:/Work/Project')['projectKey'], 'local|unassigned')

    def test_posix_root_ignores_trailing_slash_but_preserves_case_and_backslash(self):
        self.saved['local-projects']['local']['rootPaths'] = ['/work/Project']
        self.assertEqual(self.decorate('/work/Project/')['projectKey'], 'local|local')
        for path in ['/work/project', r'/work\Project', '/work/Project/child', '/work/Other/../Project']:
            self.assertEqual(self.decorate(path)['projectKey'], 'local|unassigned')

    def test_double_slash_roots_follow_the_local_execution_platform(self):
        self.saved['local-projects']['local']['rootPaths'] = ['//work/Project']
        for platform in ('darwin', 'linux', 'win32'):
            with self.subTest(platform=platform), patch('sys.platform', platform):
                self.assertEqual(self.decorate('//work/Project/')['projectKey'], 'local|local')
                expected = 'local|local' if platform == 'win32' else 'local|unassigned'
                self.assertEqual(self.decorate('//work/project')['projectKey'], expected)

    def test_windows_explicit_unc_is_unambiguous_on_posix(self):
        with patch('sys.platform', 'linux'):
            self.assertEqual(project_root_key(r'\\server\Share\Project'),
                             project_root_key(r'\\?\UNC\SERVER\share\project'))
            self.assertNotEqual(project_root_key('//server/share/project'),
                                project_root_key(r'\\server\share\project'))

    def test_native_root_matching_uses_execution_platform_and_preserves_cwd(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            home = Path(directory)
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as db, db:
                db.executescript("""
                    CREATE TABLE threads(id TEXT, cwd TEXT, source TEXT, archived INTEGER, updated_at INTEGER);
                    INSERT INTO threads VALUES ('different', '//work/project', 'vscode', 0, 1),
                        ('exact', '//work/Project/', 'vscode', 0, 2);
                    CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT);
                    INSERT INTO projects VALUES ('native', 'Native');
                    CREATE TABLE project_roots(project_id TEXT, path TEXT);
                    INSERT INTO project_roots VALUES ('native', '//work/Project');
                """)
            for platform in ('darwin', 'linux', 'win32'):
                with self.subTest(platform=platform), patch('sys.platform', platform):
                    rows = SessionStore(home).list(project_roots=True)
                    result = AppHosts(home).decorate(rows, 'local', 'Local')
                    self.assertEqual(result[0]['projectKey'], 'local|native')
                    self.assertEqual(result[1]['projectKey'],
                                     'local|native' if platform == 'win32' else 'local|unassigned')
                    self.assertEqual([row['cwd'] for row in result], ['//work/Project/', '//work/project'])

    def test_ssh_root_platform_is_independent_of_gateway_platform(self):
        self.saved['remote-projects'][0]['remotePath'] = '//work/Project'
        for gateway, target in [('win32', 'linux'), ('darwin', 'win32')]:
            with self.subTest(gateway=gateway, target=target), patch('sys.platform', gateway):
                rows = [{'id': 'fixture', 'cwd': '//work/project', '_projectPlatform': target}]
                result = self.hosts.decorate(rows, 'ssh', 'SSH')[0]
                self.assertEqual(result['projectKey'], 'ssh|remote' if target == 'win32' else 'ssh|unassigned')
                self.assertNotIn('_projectPlatform', result)

    def test_remote_list_transports_target_platform_in_the_same_read(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            home = Path(directory)
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as db, db:
                db.executescript("""
                    CREATE TABLE threads(id TEXT, cwd TEXT, source TEXT, archived INTEGER, updated_at INTEGER);
                    INSERT INTO threads VALUES ('fixture', '//work/project', 'vscode', 0, 1);
                """)
            self.saved['remote-projects'][0]['remotePath'] = '//work/Project'
            for gateway, target in [('win32', 'linux'), ('linux', 'win32')]:
                def run_target(alias, source, **kwargs):
                    output = io.StringIO()
                    with patch('sys.platform', target), redirect_stdout(output):
                        exec(source, {})
                    return json.loads(output.getvalue())
                with self.subTest(gateway=gateway, target=target), patch('sys.platform', gateway), \
                     patch('bridge.clients.codex.remote.ssh_read', side_effect=run_target) as read:
                    rows = RemoteStore('fixture').call('list', {'project_scope': True}, home=home)
                    self.assertEqual(rows[0]['_projectPlatform'], target)
                    result = self.hosts.decorate(rows, 'ssh', 'SSH')[0]
                    self.assertEqual(result['projectKey'], 'ssh|remote' if target == 'win32' else 'ssh|unassigned')
                    self.assertNotIn('_projectPlatform', result)
                    read.assert_called_once()

    def test_native_and_legacy_root_match_requires_current_config_mapping(self):
        self.saved['app-server-project-id-by-legacy-project-id-by-host'] = {'local:/other': {'local': 'native'}}
        def row():
            return {'id': 'fixture', 'cwd': 'D:/Work/Project',
                    '_nativeRootProjects': [{'id': 'native', 'name': 'Native'}]}
        self.assertEqual(self.hosts.decorate([row()], 'local', 'Local')[0]['projectKey'], 'local|unassigned')
        self.saved['app-server-project-id-by-legacy-project-id-by-host'][self.scope] = {'local': 'native'}
        result = self.hosts.decorate([row()], 'codex:fixture', 'Fixture')[0]
        self.assertEqual((result['projectKey'], result['projectName']), ('codex:fixture|local', 'Native'))
        self.assertNotIn('_nativeRootProjects', result)

    def test_local_root_yields_to_explicit_workspace_native_id_and_other_host(self):
        fields = [{'workspaceKind': 'projectless'}, {'project_id': 'unknown-native-project'}]
        for extra in fields:
            row = {'id': 'fixture', 'cwd': 'D:/Work/Project', **extra}
            self.assertEqual(self.hosts.decorate([row], 'local', 'Local')[0]['projectKey'], 'local|unassigned')
        self.saved['thread-project-membership-host-ids'] = {'fixture': 'ssh'}
        self.assertEqual(self.decorate('D:/Work/Project')['projectKey'], 'local|unassigned')
        self.saved['thread-project-membership-host-ids'] = {}
        self.saved['thread-project-assignments'] = {'fixture': {'projectId': 'local', 'projectKind': 'remote'}}
        self.assertEqual(self.decorate('D:/Work/Project')['projectKey'], 'local|unassigned')

    def test_native_store_lists_its_registered_secondary_root_without_legacy_state(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            home = Path(directory)
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as db, db:
                db.executescript("""
                    CREATE TABLE threads(id TEXT, cwd TEXT, source TEXT, archived INTEGER, updated_at INTEGER, project_id TEXT);
                    INSERT INTO threads VALUES ('fixture', '/native/secondary/', 'vscode', 0, 4, NULL),
                        ('child', '/native/secondary/subdir', 'vscode', 0, 3, NULL),
                        ('explicit', '/native/secondary', 'vscode', 0, 2, 'other'),
                        ('windows', '/C:/NATIVE/Windows/', 'vscode', 0, 1, NULL);
                    CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT);
                    INSERT INTO projects VALUES ('native', 'Native project'), ('other', 'Other project');
                    CREATE TABLE project_roots(project_id TEXT, position INTEGER, path TEXT);
                    INSERT INTO project_roots VALUES ('native', 0, '/native/primary'),
                        ('native', 1, '/native/secondary'), ('native', 2, 'C:/Native/Windows');
                """)
            rows = SessionStore(home).list(project_roots=True)
            results = AppHosts(home).decorate(rows, 'local', 'Local')
            self.assertEqual([row['projectKey'] for row in results],
                             ['local|native', 'local|unassigned', 'local|other', 'local|native'])
            self.assertTrue(all('_nativeRootProjects' not in row for row in results))

    def test_ssh_alias_keeps_legacy_key_for_the_execution_home_only(self):
        self.saved['app-server-project-id-by-legacy-project-id-by-host'] = {'ssh:/home/user/.codex': {'remote': 'native'}}
        def row(home):
            return {'id': 'fixture', 'cwd': 'C:/Work/Project', '_nativeProjectHome': home,
                    '_nativeProject': {'id': 'native', 'name': 'Native'}}
        exact, other = self.hosts.decorate([row('/home/user/.codex'), row('/home/user/.alternate')], 'ssh', 'SSH')
        self.assertEqual((exact['projectKey'], other['projectKey']), ('ssh|remote', 'ssh|native'))
        self.assertNotIn('_nativeProjectHome', exact)
        self.assertEqual(self.decorate(r'C:\Work\Project', 'ssh')['projectKey'], 'ssh|unassigned')


    def test_unlabelled_remote_windows_project_uses_directory_name(self):
        self.saved['codex-managed-remote-connections'] = [{'hostId': 'ssh', 'alias': 'fixture'}]
        self.saved['remote-projects'][0].update(remotePath=r'C:\Work\Project')
        self.saved['remote-projects'][0].pop('label')
        self.assertEqual(self.hosts.projects()[1]['name'], 'Project')
        self.saved['thread-project-assignments'] = {'fixture': {'projectId': 'remote', 'hostId': 'ssh'}}
        self.assertEqual(self.decorate(r'C:\Work\Project', 'ssh')['projectName'], 'Project')
        self.saved['remote-projects'][0]['label'] = 'Custom project name'
        self.assertEqual(self.hosts.projects()[1]['name'], 'Custom project name')
        self.assertEqual(self.decorate(r'C:\Work\Project', 'ssh')['projectName'], 'Custom project name')


    def test_explicit_membership_wins_and_never_crosses_hosts(self):
        self.saved['local-projects']['assigned'] = {'id': 'assigned', 'name': 'Assigned', 'rootPaths': ['E:/Other']}
        self.saved['thread-project-assignments'] = {'fixture': {'projectId': 'assigned', 'hostId': 'local'}}
        self.assertEqual(self.decorate('D:/Work/Project')['projectKey'], 'local|assigned')
        self.assertEqual(self.decorate('C:/Work/Project', 'ssh')['projectKey'], 'ssh|unassigned')
        self.assertNotEqual(self.decorate('D:/Work/Project', 'other')['projectKey'], 'local|local')

    def test_ssh_registered_root_matches_native_app_without_explicit_membership(self):
        self.saved['remote-projects'][0]['remotePath'] = '/work/project'
        row = {'id': 'new-ssh-chat', 'cwd': '/work/project', '_nativeProject': None,
               'origin': 'desktop', 'model_provider': 'original'}
        self.hosts.decorate([row], 'ssh', 'SSH')
        self.assertEqual(row['projectKey'], 'ssh|remote')
        self.assertEqual(row['projectName'], 'Remote')
        self.assertEqual((row['cwd'], row['origin'], row['model_provider']),
                         ('/work/project', 'desktop', 'original'))

    def test_ssh_root_fallback_never_guesses_parent_case_or_other_host(self):
        self.saved['remote-projects'][0]['remotePath'] = '/work/project'
        for cwd, host in [('/work/project/subdir', 'ssh'), ('/work/Project', 'ssh'),
                          ('/work/project-old', 'ssh'), ('/work/project', 'other'),
                          ('/work/project', 'local')]:
            with self.subTest(cwd=cwd, host=host):
                self.assertEqual(self.decorate(cwd, host)['projectKey'], host + '|unassigned')

    def test_ssh_explicit_projectless_and_confirmed_removal_override_root(self):
        cwd = 'C:/Work/Project'
        self.saved['projectless-thread-ids'] = ['fixture']
        self.assertEqual(self.decorate(cwd, 'ssh')['projectKey'], 'ssh|unassigned')
        self.saved['projectless-thread-ids'] = []
        row = {'id': 'fixture', 'cwd': cwd, '_nativeProject': None}
        self.hosts.decorate([row], 'ssh', 'SSH', {'fixture'})
        self.assertEqual(row['projectKey'], 'ssh|unassigned')
        row = {'id': 'projectless', 'cwd': cwd, 'workspaceKind': 'projectless'}
        self.hosts.decorate([row], 'ssh', 'SSH')
        self.assertEqual(row['projectKey'], 'ssh|unassigned')

    def test_ssh_native_and_explicit_memberships_override_matching_root(self):
        self.saved['remote-projects'].append({'id': 'other-project', 'hostId': 'ssh',
                                             'label': 'Moved', 'remotePath': '/other'})
        self.saved['thread-project-assignments'] = {
            'fixture': {'projectKind': 'remote', 'hostId': 'ssh', 'projectId': 'other-project'}}
        self.assertEqual(self.decorate('C:/Work/Project', 'ssh')['projectKey'], 'ssh|other-project')
        row = {'id': 'fixture', 'cwd': 'C:/Work/Project', '_nativeProject': {'id': 'native', 'name': 'Native'}}
        self.hosts.decorate([row], 'ssh', 'SSH')
        self.assertEqual(row['projectKey'], 'ssh|native')

    def test_removed_ssh_project_does_not_leave_a_cached_root_assignment(self):
        self.assertEqual(self.decorate('C:/Work/Project', 'ssh')['projectKey'], 'ssh|remote')
        self.saved['remote-projects'] = []
        self.assertEqual(self.decorate('C:/Work/Project', 'ssh')['projectKey'], 'ssh|unassigned')


if __name__ == '__main__':
    unittest.main()
