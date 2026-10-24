"""Project display uses the execution host's path syntax without changing cwd."""
import copy
import unittest
from pathlib import Path

from bridge.clients.codex.remote import AppHosts


class ProjectGroupingTests(unittest.TestCase):
    def setUp(self):
        self.hosts = AppHosts(Path('unused-fixture-home'))
        self.saved = {
            'local-projects': {'local': {'id': 'local', 'name': 'Local', 'rootPaths': ['D:/Work/Project']}},
            'remote-projects': [{'id': 'remote', 'hostId': 'ssh', 'label': 'Remote',
                                 'remotePath': 'C:/Work/Project'}],
        }
        self.hosts.state = lambda: self.saved

    def decorate(self, cwd, host='local', identifier='fixture'):
        return self.hosts.decorate([{'id': identifier, 'cwd': cwd, 'origin': 'desktop'}], host, host)[0]

    def test_local_and_ssh_windows_prefixes_separators_case_and_subdirectories(self):
        before = copy.deepcopy(self.saved)
        for host, drive, project in [('local', 'D', 'local'), ('ssh', 'C', 'remote')]:
            for cwd in [f'{drive}:\\WORK\\project', f'{drive.lower()}:/work/PROJECT/',
                        f'\\\\?\\{drive}:\\Work\\Project', f'//?/{drive}:/Work/Project/child',
                        f'\\\\.\\{drive}:\\Work\\Project\\child']:
                with self.subTest(host=host, cwd=cwd):
                    row = self.decorate(cwd, host)
                    self.assertEqual(row['projectKey'], host + '|' + project)
                    self.assertEqual(row['cwd'], cwd)
                    self.assertEqual(row['id'], 'fixture')
                    self.assertEqual(row['host'], host)
                    self.assertEqual(row['origin'], 'desktop')
        self.assertEqual(self.saved, before)

    def test_sibling_and_independent_directories_remain_separate(self):
        for cwd in ['D:/Work/Project2', 'D:/Other/independent']:
            self.assertNotEqual(self.decorate(cwd)['projectKey'], 'local|local')

    def test_longest_root_and_all_saved_roots_are_considered(self):
        self.saved['local-projects']['local']['rootPaths'].append('E:/Second')
        self.saved['local-projects']['nested'] = {'id': 'nested', 'name': 'Nested',
                                                  'rootPaths': ['D:/Work/Project/child']}
        self.assertEqual(self.decorate('\\\\?\\D:\\Work\\Project\\child\\src')['projectKey'], 'local|nested')
        self.assertEqual(self.decorate('\\\\?\\E:\\SECOND\\src')['projectKey'], 'local|local')

    def test_explicit_membership_wins_and_never_crosses_hosts(self):
        self.saved['local-projects']['assigned'] = {'id': 'assigned', 'name': 'Assigned', 'rootPaths': ['E:/Other']}
        self.saved['thread-project-assignments'] = {'fixture': {'projectId': 'assigned', 'hostId': 'local'}}
        self.assertEqual(self.decorate('D:/Work/Project')['projectKey'], 'local|assigned')
        self.assertEqual(self.decorate('C:/Work/Project', 'ssh')['projectKey'], 'ssh|remote')
        self.assertNotEqual(self.decorate('D:/Work/Project', 'other')['projectKey'], 'local|local')

    def test_unc_namespaces_match_without_touching_posix_case(self):
        for host in ['local', 'ssh']:
            expected = 'local|local' if host == 'local' else 'ssh|remote'
            if host == 'local':
                self.saved['local-projects']['local']['rootPaths'] = ['//server/share/Project']
            else:
                self.saved['remote-projects'][0]['remotePath'] = '//server/share/Project'
            for cwd in ['\\\\SERVER\\share\\project\\child', '\\\\?\\UNC\\SERVER\\share\\project']:
                with self.subTest(host=host, cwd=cwd):
                    self.assertEqual(self.decorate(cwd, host)['projectKey'], expected)
        self.saved['local-projects']['local']['rootPaths'] = ['/work/Project']
        self.saved['remote-projects'][0]['remotePath'] = '/work/Project'
        for host in ['local', 'ssh']:
            expected = 'local|local' if host == 'local' else 'ssh|remote'
            self.assertEqual(self.decorate('/work/Project/src', host)['projectKey'], expected)
            self.assertNotIn(self.decorate('/work/project/src', host)['projectKey'], ['local|local', 'ssh|remote'])


if __name__ == '__main__':
    unittest.main()
