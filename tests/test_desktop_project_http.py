import unittest
from unittest.mock import Mock
import test_bridge


class DesktopProjectHTTP(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def setUp(self):
        test_bridge.HttpTests.setUp(self)
        self.manager = self.server.desktop_sessions = Mock()
        self.manager.project_directories.return_value = {'path': '/project', 'directories': []}
        self.manager.register_project.return_value = {'key': 'registered', 'cwd': '/project'}

    def test_zcode_uses_authenticated_shared_project_routes(self):
        base = '/api/desktop-sessions/zcode/projects/'
        browse = {'environment': 'native', 'path': '/project', 'hidden': False, 'name': None, 'identity': None}
        self.assertEqual(self.request('POST', base+'browse', browse)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', base+'browse', browse, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', base+'browse', browse, headers)[0], 200)
        self.manager.project_directories.assert_called_once_with('zcode', **browse)
        body = {'environment': 'native', 'path': '/project', 'createDirectory': False}
        status, _, result = self.request('POST', base+'register', body, headers)
        self.assertEqual(status, 200, result)
        self.manager.register_project.assert_called_once_with('zcode', 'native', '/project', False)
        self.assertEqual(self.request('POST', base+'register', {**body, 'cwd': '/other'}, headers)[0], 400)
        self.assertEqual(self.request('GET', base+'browse', headers=headers)[0], 405)
