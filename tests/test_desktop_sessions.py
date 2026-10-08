import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.integrations.manager import DesktopSessions
from bridge.integrations.deepseek import DeepSeek, MARKER
import test_bridge

ROOT = Path(__file__).resolve().parents[1]


class Adapters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = DesktopSessions(self.root)
        self.addCleanup(self.manager.close)

    def test_receipts_survive_reopen_and_do_not_replay_uncertain_sends(self):
        adapter = Mock()
        adapter.call.side_effect = TimeoutError('unknown')
        self.manager.adapters['deepseek'] = adapter
        body = {'id': str(uuid.uuid4()), 'text': 'hello'}
        first = self.manager.call('deepseek', 'send', 'original-session', body)
        self.assertEqual(first['status'], 'unknown')
        self.assertEqual(self.manager.call('deepseek', 'send', 'original-session', body), first)
        self.assertEqual(adapter.call.call_count, 1)
        with self.assertRaises(ValueError):
            self.manager.call('claude', 'send', 'another-session', body)
        # A restarted gateway must retain the receipt, even if the desktop accepted
        # the first send but its acknowledgement was lost.
        second = DesktopSessions(self.root)
        try:
            other = Mock(); second.adapters['deepseek'] = other
            self.assertEqual(second.call('deepseek', 'send', 'original-session', body), first)
            other.call.assert_not_called()
        finally:
            second.close()

    def test_read_only_operations_preserve_backend_and_session(self):
        adapter = Mock(); self.manager.adapters['deepseek'] = adapter
        self.manager.call('deepseek', 'detail', 'session-one')
        adapter.call.assert_called_once_with('detail', 'session-one', {})
        with self.assertRaises(ValueError): self.manager.call('deepseek', 'eval', 'session-one')
        with self.assertRaises(ValueError): self.manager.call('deepseek', 'send', 'session-one', {'id':str(uuid.uuid4()),'text':''})

    def test_client_switch_requires_configuration_and_persists(self):
        adapter = Mock()
        adapter.call.side_effect = lambda action, *args: ({'connected': True, 'configured': True} if action == 'status' else
            {'sessions': [{'id': 'original', 'cwd': str(self.root)}]} if action == 'list' else {'models': [{'id': 'upstream/model'}]})
        self.manager.adapters['deepseek'] = adapter
        self.manager._start_deepseek = Mock()  # Native lifecycle has isolated coverage in test_client_lifecycle.
        self.assertFalse(self.manager.enabled('deepseek'))
        state = self.manager.toggle_client({'provider': 'deepseek', 'enabled': True})
        self.assertTrue(next(row for row in state['clients'] if row['id'] == 'deepseek')['enabled'])
        with self.assertRaises(ValueError):
            self.manager.toggle_client({'provider': 'claude', 'enabled': True})
        self.manager.toggle_client({'provider': 'claude', 'enabled': False})
        self.assertFalse(self.manager.enabled('claude'))
        self.assertTrue(json.loads(self.manager.config_path.read_text())['enabled']['deepseek'])
        adapter.call.side_effect = OSError('offline')
        state = self.manager.clients(refresh=True)
        row = next(row for row in state['clients'] if row['id'] == 'deepseek')
        self.assertTrue(row['enabled'])
        self.assertFalse(row['configured'])
        self.manager.toggle_client({'provider': 'deepseek', 'enabled': False})
        self.assertFalse(self.manager.enabled('deepseek'))

    def test_workspace_is_bound_to_provider_and_desktop_session(self):
        a, b = self.root/'one', self.root/'two'
        a.mkdir(); b.mkdir(); (a/'proof.txt').write_text('one'); (b/'proof.txt').write_text('two')
        self.manager.config['enabled'] = {'deepseek': True, 'claude': True}
        for provider, root in [('deepseek', a), ('claude', b)]:
            adapter = Mock(); adapter.call.return_value = {'sessions': [{'id': 'same-id', 'cwd': str(root)}]}
            self.manager.adapters[provider] = adapter
        one = self.manager.workspace_bridge('deepseek', 'same-id')
        two = self.manager.workspace_bridge('claude', 'same-id')
        self.assertNotEqual(one.identifier, two.identifier)
        self.assertEqual(one.workspace(None, 'preview', {'path': 'proof.txt'})['text'], 'one')
        self.assertEqual(two.workspace(None, 'preview', {'path': 'proof.txt'})['text'], 'two')
        with self.assertRaises(PermissionError):
            one.workspace(None, 'preview', {'path': '../two/proof.txt'})
        with self.assertRaises(ValueError):
            self.manager.workspace_bridge('deepseek', 'unknown')
        self.manager.config['enabled']['deepseek'] = False
        with self.assertRaises(ValueError):
            self.manager.workspace_bridge('deepseek', 'same-id')

    def test_install_remove_preserves_other_patches_and_rotates_token(self):
        home=self.root/'home'; patch=home/'profiles/desktop/cordis.patch.yml'
        patch.parent.mkdir(parents=True)
        original='- insert: []\n# user configuration\n';patch.write_text(original)
        adapter=DeepSeek(self.root/'adapter', home)
        adapter.install();adapter.install()
        self.assertEqual(patch.read_text().count(MARKER),1)
        config=adapter.directory/'connection.json';token=json.loads(config.read_text())['token']
        self.assertTrue(DeepSeek(self.root/'other-adapter',home).install()['reused'])
        patch.write_text(patch.read_text()+'# added later\n')
        adapter.uninstall()
        self.assertIn(original,patch.read_text());self.assertIn('# added later',patch.read_text())
        self.assertNotIn(MARKER,patch.read_text())
        self.assertNotEqual(json.loads(config.read_text())['token'],token)

    def test_claude_prepare_is_local_only_and_contains_both_surface_capabilities(self):
        self.assertFalse(self.manager.adapters['claude'].desktop)
        value=self.manager.control({'action':'prepare-claude'})
        self.assertIn('LocalAgentModeSessions',value['script'])
        self.assertIn('LocalSessions',value['script'])
        self.assertEqual(Path(value['scriptPath']).parent,Path(value['workspace']))
        self.assertNotIn('script', self.manager.control({'action':'status'}))


class HttpAdapters(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login
    def test_desktop_notifications_require_auth_csrf_and_keep_provider(self):
        self.server.desktop_sessions = Mock()
        self.server.notifications = Mock()
        self.server.notifications.policy.return_value = {'available': True, 'requests': 'inherit', 'completion': 'on'}
        route = '/api/desktop-sessions/claude/notifications?sessionId=native-id'
        self.assertEqual(self.request('GET', route)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', route, headers=headers)[0], 200)
        self.server.notifications.policy.assert_called_with('native-id', 'desktop:claude', None)
        value = {'requests': 'inherit', 'completion': 'on'}
        self.assertEqual(self.request('POST', route, value, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', route, value, headers)[0], 200)
        self.server.notifications.policy.assert_called_with('native-id', 'desktop:claude', value)
        self.server.desktop_sessions.call.assert_not_called()

    def test_desktop_accounts_web_is_read_switch_only(self):
        self.server.desktop_sessions = Mock()
        self.server.desktop_sessions.client_accounts.return_value = {'accounts': [], 'activeId': None}
        route = '/api/desktop-sessions/deepseek/accounts'
        self.assertEqual(self.request('GET', route)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', route, headers=headers)[0], 200)
        self.assertEqual(self.request('POST', route, {'action': 'import-current'}, headers)[0], 403)
        self.assertEqual(self.request('POST', route, {'action': 'switch', 'id': 'one'}, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', route, {'action': 'details', 'id': 'one'}, headers)[0], 200)
        self.server.desktop_sessions.client_accounts.assert_called_with('deepseek', {'action': 'details', 'operation': 'details', 'id': 'one'})
    def test_desktop_routes_auth_csrf_and_no_install_or_raw_rpc(self):
        self.server.desktop_sessions = Mock()
        self.server.desktop_sessions.call.return_value = {'connected':True,'sessions':[]}
        self.assertEqual(self.request('GET','/api/desktop-sessions/deepseek/list')[0],401)
        headers=self.login()
        self.assertEqual(self.request('GET','/api/desktop-sessions/deepseek/list',headers=headers)[0],200)
        self.assertEqual(self.request('POST','/api/desktop-sessions/deepseek/send',{'sessionId':'original','id':str(uuid.uuid4()),'text':'hi'}, {'Cookie':headers['Cookie']})[0],403)
        self.assertEqual(self.request('POST','/api/desktop-sessions/deepseek/install',{},headers)[0],404)
        self.assertEqual(self.request('POST','/api/desktop-sessions/claude/eval',{},headers)[0],404)
        self.assertEqual(self.request('GET','/api/desktop-sessions/deepseek/send',headers=headers)[0],404)
        body={'sessionId':'session-original','id':str(uuid.uuid4()),'text':'hi'}
        self.assertEqual(self.request('POST','/api/desktop-sessions/deepseek/send',body,headers)[0],200)
        self.server.desktop_sessions.call.assert_called_with('deepseek','send','session-original',{'id':body['id'],'text':'hi'})

    def test_native_process_recovery_is_not_exposed_to_authenticated_web_clients(self):
        self.server.desktop_sessions = Mock()
        self.server.desktop_sessions.toggle_client.return_value = {'clients': []}
        headers = self.login()
        for action in ('deepseek-recovery-preview', 'deepseek-recovery-confirm', 'recover-deepseek', 'quit-client'):
            route = '/api/desktop-sessions/deepseek/' + action
            self.assertEqual(self.request('GET', route, headers=headers)[0], 404)
            self.assertEqual(self.request('POST', route, {'token': 'fixture'}, headers)[0], 404)
        self.assertEqual(self.request('POST', '/api/clients', {
            'provider': 'deepseek', 'enabled': False, 'action': 'deepseek-recovery-confirm', 'token': 'fixture'
        }, headers)[0], 400)
        self.server.desktop_sessions.control.assert_not_called()
        self.server.desktop_sessions.call.assert_not_called()
        self.server.desktop_sessions.toggle_client.assert_not_called()


    def test_client_management_and_workspace_http_are_authenticated_and_scoped(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(temp.cleanup)
        root = Path(temp.name); (root/'only-here.txt').write_text('desktop workspace')
        manager = DesktopSessions(root); self.addCleanup(manager.close)
        self.server.desktop_sessions = manager
        self.server.bridge.for_host = lambda host: self.server.bridge
        adapter = Mock()
        adapter.call.side_effect = lambda action, *args: ({'connected': True, 'configured': True} if action == 'status' else
            {'sessions': [{'id': 'native-session', 'cwd': str(root)}]} if action == 'list' else {'models': [{'id':'upstream/model'}]})
        manager.adapters['deepseek'] = adapter
        manager._start_deepseek = Mock()
        inspector = patch('bridge.integrations.client_launch.inspect_client', return_value={'running': False})
        inspector.start(); self.addCleanup(inspector.stop)
        self.assertEqual(self.request('GET', '/api/clients')[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/clients', {'provider':'deepseek','enabled':True}, {'Cookie':headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', '/api/clients', {'provider':'deepseek','enabled':True}, headers)[0], 200)
        path = '/api/desktop-sessions/deepseek/workspace?sessionId=native-session'
        result = self.request('GET', path, headers=headers)
        self.assertEqual(result[0], 200)
        self.assertIn('only-here.txt', [row['name'] for row in result[2]['entries']])
        self.assertEqual(self.request('GET', path+'&path=..', headers=headers)[0], 403)
        self.assertNotEqual(self.request('GET', path.replace('native-session','other'), headers=headers)[0], 200)
        terminal = '/api/desktop-sessions/deepseek/terminal?sessionId=native-session'
        identifier = str(uuid.uuid4())
        result = self.request('POST', terminal, {'action':'open','id':identifier,'cols':80,'rows':24}, headers)
        self.assertEqual(result[0], 200)
        self.assertTrue(result[2]['running'])
        self.assertEqual(self.request('POST', terminal, {'action':'close','id':identifier}, headers)[0], 200)
        self.assertEqual(self.request('POST', '/api/clients', {'provider':'deepseek','enabled':False}, headers)[0], 200)
        self.assertNotEqual(self.request('GET', path, headers=headers)[0], 200)
