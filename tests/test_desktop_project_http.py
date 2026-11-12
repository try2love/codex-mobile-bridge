import unittest
from unittest.mock import Mock
import test_bridge
from bridge.app.service import Bridge
from bridge.clients.codex.app_server import AppServerUnavailable


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

    def test_creation_model_catalog_is_authenticated_read_only_and_keeps_environment_scope(self):
        value={'models':[{'id':'model','efforts':['low']}],'cache':{'identity':'scope'}}
        self.manager.call.return_value=value
        path='/api/desktop-sessions/zcode/create-models?environment=desktop%7Clocal%7Cscope&projectKey=project&refresh=true'
        self.assertEqual(self.request('GET',path)[0],401)
        headers=self.login();self.assertEqual(self.request('GET',path,headers=headers)[2],value)
        self.manager.call.assert_called_once_with('zcode','create-models',None,{'environment':'desktop|local|scope','projectKey':'project','refresh':True})
        self.assertEqual(self.request('POST',path,{},headers)[0],404)

    def test_codex_creation_route_passes_model_with_the_original_idempotency_request(self):
        self.server.bridge.create_chat=Mock(return_value={'id':'created','host':'local'})
        fields={'id':'request','project':'project','title':'Title','model':'chosen','effort':'low','modelCatalogIdentity':'scope'}
        headers=self.login();status,_,_=self.request('POST','/api/sessions',fields,headers)
        self.assertEqual(status,200)
        self.server.bridge.create_chat.assert_called_once_with('project','Title','request',model_options={'model':'chosen','effort':'low','modelCatalogIdentity':'scope'})

    def test_cli_creation_runtime_failure_keeps_actionable_error(self):
        bridge = self.server.bridge
        bridge.host = 'local'
        bridge.projects = Mock(return_value=[{'key': 'local|p', 'host': 'local', 'cwd': '/p', 'type': 'cli'}])
        bridge.project_host = Mock(return_value='local')
        bridge.for_host = Mock(return_value=bridge)
        bridge.cli_sessions = Mock()
        message = '此 Codex CLI 缺少完整的后台运行组件。请安装或修复完整的 Codex CLI 后重试。'
        bridge.cli_sessions.creation_context.side_effect = AppServerUnavailable(message)
        bridge.creation_models = Bridge.creation_models.__get__(bridge)
        path = '/api/projects/models?project=local%7Cp&kind=cli'
        self.assertEqual(self.request('GET', path)[0], 401)
        status, _, value = self.request('GET', path, headers=self.login())
        self.assertEqual(status, 409)
        self.assertEqual(value['error'], message)
        bridge.cli_sessions.create.assert_not_called()
