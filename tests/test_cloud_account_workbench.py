"""Authorized cloud account requests use the same public HTTP operations as direct web."""
import http.client
import json
from pathlib import Path
import tempfile
from threading import BoundedSemaphore, RLock, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from bridge.app.cloud import create_cloud_server
from bridge.features.accounts.cli_access import CliAccess
from bridge.features.network.cloud_account_routes import authorize_account_route
from bridge.features.network.cloud_connector import validate_request
from relay.protocol import validate as shared_validate


class CloudAccountWorkbenchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bridge, self.manager = Mock(), Mock()
        self.enabled = {'codex', 'claude', 'deepseek', 'zcode'}
        self.manager.native_enabled.side_effect = self.enabled.__contains__
        self.manager.require_enabled.side_effect = lambda entry: self._require(entry)
        self.bridge.data_dir = self.root
        self.bridge.account.lock = self.bridge.accounts.lock = RLock()
        self.bridge.accounts.public.return_value = {'accounts': [], 'activeVerified': False, 'activeId': None,
                                                   'switch': {}, 'blockers': [], 'sshHosts': [{'host': 'fixture-ssh'}]}
        self.bridge.cli_access = CliAccess(self.bridge)
        self.addCleanup(self.bridge.cli_access.close)
        self.manager.client_accounts.return_value = {'accounts': []}
        self.server = create_cloud_server(SimpleNamespace(
            bridge=self.bridge, desktop_sessions=self.manager, web_dir=Path(__file__).resolve().parents[1]/'web',
            auth=SimpleNamespace(config={'mode': 'password'}),
            slots=BoundedSemaphore(8), terminal_sockets=BoundedSemaphore(1)))
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token, session = self.server.auth.new_session('127.0.0.1', 'Noise-approved mobile')
        self.csrf = session['csrf']

    def _require(self, entry):
        if entry not in self.enabled:
            raise ValueError('disabled')

    def request(self, method, path, value=None, *, csrf=None):
        body = json.dumps(value).encode() if value is not None else b''
        request = {'kind':'request','id':str(uuid4()),'method':method,'path':path,'content_type':'application/json',
                   'headers':{},'length':len(body)}
        validate_request(request)
        authorize_account_route(self.manager, method, path, body)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request(method, path, body=body, headers={'Cookie':self.server.auth.cookie_name+'='+self.token,
                'Origin':'http://127.0.0.1:'+str(self.server.server_port),
                'X-CSRF-Token': self.csrf if csrf is None else csrf,'Content-Type':'application/json'})
            result = conn.getresponse()
            return result.status, json.loads(result.read())
        finally:
            conn.close()

    def test_native_and_independent_desktop_accounts_reach_only_selected_manager(self):
        for family in ('claude', 'deepseek', 'zcode'):
            base = '/api/desktop-sessions/'+family+'/accounts'
            self.assertEqual(self.request('GET', base)[0], 200)
            self.assertEqual(self.request('POST', base, {'action':'switch','id':'saved'})[0], 200)
            self.manager.client_accounts.assert_called_with(family, {'action':'switch','operation':'switch','id':'saved'})
            if family != 'zcode':
                entry = family+':'+'a'*32
                self.enabled.add(entry)
                scoped = self.manager.resolve_instance.return_value
                scoped.client_accounts.return_value = {'accounts':[]}
                self.assertEqual(self.request('GET', base+'?entry='+entry)[0], 200)
                scoped.client_accounts.assert_called_with(family, {'action':'list','operation':'list'})
                self.enabled.remove(entry)
                with self.assertRaises(PermissionError):
                    self.request('GET', base+'?entry='+entry)
            self.enabled.remove(family)
            with self.assertRaises(PermissionError):
                self.request('POST', base, {'action':'switch','id':'saved'})

    def test_codex_switch_and_retry_retain_csrf_host_and_entry_scope(self):
        self.assertEqual(self.request('GET','/api/accounts')[1]['sshHosts'],[{'host':'fixture-ssh'}])
        self.assertEqual(self.request('POST','/api/accounts/ssh-retry',{'host':'fixture-ssh'},csrf='wrong')[0],403)
        self.bridge.retry_ssh.assert_not_called()
        self.assertEqual(self.request('POST','/api/accounts/ssh-retry',{'host':'fixture-ssh'})[0],202)
        self.bridge.retry_ssh.assert_called_once_with('fixture-ssh')
        entry='codex:'+32*'a'
        self.enabled.add(entry)
        target = self.bridge.account_target.return_value
        target.accounts.switch.return_value = {'switch':{'phase':'pending'}}
        payload={'id':'alternative','requestId':str(uuid4()),'confirmed':True,'tasksConfirmed':True}
        self.assertEqual(self.request('POST','/api/accounts/switch?entry='+entry,payload)[0],202)
        target.accounts.switch.assert_called_once_with(payload)
        self.bridge.accounts.switch.assert_not_called()
        self.assertEqual(self.request('POST','/api/accounts/ssh-retry?entry='+entry,{'host':'fixture-ssh'})[0],400)
        self.assertEqual(self.bridge.retry_ssh.call_count,1)
        self.enabled.remove(entry)
        with self.assertRaises(PermissionError):
            self.request('POST','/api/accounts/switch?entry='+entry,payload)

    def test_cli_save_persists_key_only_on_host_and_never_observes_disabled_agent(self):
        self.enabled.intersection_update({'claude'})
        native = self.root/'native'
        native.mkdir()
        profile = {'binary':'/fixture/claude','directory':native,'origin':'default:'+str(native)+':none'}
        with patch('bridge.features.accounts.cli_access._profile',return_value=profile):
            status, view = self.request('GET','/api/cli-access')
            self.assertEqual((status,set(view)),(200,{'claude'}))
            create={'action':'saveApi','provider':'claude','name':'Fixture','baseUrl':'https://fixture.invalid',
                    'model':'fixture-model','apiKey':'private-fixture-secret','authScheme':'bearer'}
            status, view = self.request('POST','/api/cli-access',create)
            self.assertEqual(status,200)
            self.assertNotIn('codex',view)
            self.assertNotIn(create['apiKey'],json.dumps(view))
            keyfile = self.root/'cli-access'/'claude'/view['savedId']/'api.json'
            self.assertEqual(json.loads(keyfile.read_text())['key'],create['apiKey'])
            self.assertEqual(self.request('POST','/api/cli-access',{**create, 'id':view['savedId'],
                                  'apiKey':'','baseUrl':'https://other.invalid'})[0],400)
            with self.assertRaises(PermissionError):
                self.request('POST','/api/cli-access',{**create,'provider':'codex'})
            self.bridge.accounts.public.assert_not_called()
        self.enabled.clear()
        with self.assertRaises(PermissionError):
            self.request('GET','/api/cli-access')

    def test_wsl_read_and_write_use_the_registered_provider_and_original_target(self):
        entry = 'wsl-claude:'+32*'c'
        target = self.bridge.wsl_accounts
        target.read.return_value = target.change.return_value = {'accounts':[]}
        self.assertEqual(self.request('GET','/api/wsl-accounts?provider=claude&entry='+entry)[0],200)
        target.read.assert_called_once_with('claude',entry)
        self.manager.claude_wsl.selected.assert_called_once_with(entry)
        body = {'provider':'claude','entry':entry,'action':'switch','id':'fixture','expectedIdentity':{'fixture':True}}
        self.assertEqual(self.request('POST','/api/wsl-accounts',body)[0],200)
        target.change.assert_called_once_with(body,desktop=False)
        self.manager.claude_wsl.selected.side_effect = ValueError('disabled')
        with self.assertRaises(PermissionError):
            self.request('POST','/api/wsl-accounts',body)
        self.assertEqual(target.change.call_count,1)
        with self.assertRaises(PermissionError):
            self.request('POST','/api/wsl-accounts',{**body, 'provider':'codex'})
        with self.assertRaises(PermissionError):
            self.request('GET','/api/wsl-accounts?provider=codex&entry=wsl:'+32*'d')

    def test_account_routes_reject_malformed_or_unrelated_targets_and_leave_shared_policy_unchanged(self):
        for path in ('/api/accounts?entry=local','/api/accounts?entry=desktop&entry=desktop','//evil.test/api/accounts',
                     '/api/accounts/export','/api/desktop-sessions/claude/accounts?entry=deepseek%3A'+'a'*32,
                     '/api/wsl-accounts?provider=claude&entry=wsl%3A'+'a'*32):
            with self.subTest(path=path), self.assertRaises((ValueError,PermissionError)):
                self.request('GET',path)
        for path,method in (('/api/accounts','GET'),('/api/accounts/switch','POST'),
                            ('/api/desktop-sessions/zcode/accounts','GET')):
            with self.subTest(path=path),self.assertRaises(PermissionError):
                shared_validate(method,path,0)
