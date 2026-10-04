import base64
import json
import os
import time
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import test_accounts
from bridge.account_models import model_ids
from bridge.accounts import private_json


class ImportTests(unittest.TestCase):
    setUp = test_accounts.AccountsTests.setUp
    tearDown = test_accounts.AccountsTests.tearDown

    def scan(self, config, env=None):
        rpc = Mock();rpc.request.return_value = {'config': config}
        with patch('bridge.accounts.ManagedRPC') as cls, patch.dict(os.environ, env or {}, clear=True):
            cls.return_value.__enter__.return_value = rpc
            return self.manager.scan({})

    def config(self, **provider):
        return {'model': 'model-a', 'model_providers': {'custom': {
            'base_url': 'https://fixture.invalid/v1', 'experimental_bearer_token': 'fixture-key', **provider}},
            'profiles': {'second': {'model_provider': 'custom', 'model': 'model-b'}}}

    def test_scan_import_profiles_and_deduplicate_without_changing_current_login(self):
        before = self.manager.snapshot_files()
        result = self.scan(self.config())
        rows = result['discovery']['candidates']
        self.assertEqual([r['model'] for r in rows], ['model-a', 'model-b'])
        self.assertNotIn('fixture-key', json.dumps(result))
        self.assertNotIn('discovery', self.manager.public())
        for row in rows:
            self.manager.control({'action': 'import', 'candidateId': row['id']})
            self.manager.control({'action': 'import', 'candidateId': row['id']})
        self.assertEqual(len(self.manager.index['accounts']), 2)
        self.assertIsNone(self.manager.index['activeId'])
        self.assertEqual(self.manager.snapshot_files(), before)
        self.assertTrue(all(r['imported'] for r in self.manager.desktop_status()['discovery']['candidates']))

    def test_official_file_import_does_not_return_tokens_or_overwrite_source(self):
        claims = base64.urlsafe_b64encode(json.dumps({'sub':'user', 'email':'fixture@example.test'}).encode()).decode().rstrip('=')
        auth = {'tokens': {'account_id':'org', 'id_token':'e30.'+claims+'.sig', 'access_token':'access-secret', 'refresh_token':'refresh-secret'}}
        private_json(self.home/'auth.json', auth)
        before = (self.home/'auth.json').read_bytes()
        row = self.scan({})['discovery']['candidates'][0]
        result = self.manager.import_account({'candidateId':row['id']})
        self.assertEqual(result['accounts'][0]['email'],'fixture@example.test')
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual((self.home/'auth.json').read_bytes(),before)
        saved = json.loads((self.manager.directory(result['accounts'][0]['id'])/'auth.json').read_text())
        self.assertEqual(saved['tokens'],auth['tokens'])
        self.manager.import_account({'candidateId':row['id']})
        self.assertEqual(len(self.manager.index['accounts']),1)

    def test_missing_environment_and_extra_headers_are_reported_without_partial_import(self):
        for definition in ({'env_key':'MISSING'}, {'http_headers':{'X-Extra':'fixture'}}, {'wire_api':'chat'}):
            row = self.scan(self.config(**definition))['discovery']['candidates'][0]
            self.assertFalse(row['canImport'])
            with self.assertRaises(ValueError):self.manager.import_account({'candidateId':row['id']})
        row = self.scan(self.config(env_key='API_TOKEN'), {'API_TOKEN':'env-secret'})['discovery']['candidates'][0]
        self.assertTrue(row['canImport'])
        self.manager.import_account({'candidateId':row['id']})
        saved = self.manager.index['accounts'][0]
        self.assertEqual(json.loads((self.manager.directory(saved['id'])/'api.json').read_text())['key'],'env-secret')

    def test_source_change_blocks_import_and_models_until_rescan(self):
        row = self.scan(self.config())['discovery']['candidates'][0]
        (self.home/'config.toml').write_text('# updated')
        for method in (self.manager.import_account,self.manager.models):
            with self.assertRaisesRegex(ValueError,'重新扫描'):method({'candidateId':row['id']})

    def test_models_use_saved_or_scanned_secret_but_return_only_model_ids(self):
        row = self.scan(self.config())['discovery']['candidates'][0]
        with patch('bridge.accounts.model_ids', return_value=['model-a']) as lookup:
            result=self.manager.models({'candidateId':row['id']})
            lookup.assert_called_once_with('https://fixture.invalid/v1','fixture-key')
            self.assertEqual(result['models'],['model-a'])
            self.assertNotIn('fixture-key',json.dumps(result))
        self.manager.import_account({'candidateId':row['id']})
        saved=self.manager.index['accounts'][0]
        with patch('bridge.accounts.model_ids', return_value=['model-a']) as lookup:
            self.manager.models({'id':saved['id'],'baseUrl':saved['baseUrl'],'apiKey':''})
            lookup.assert_called_once_with(saved['baseUrl'],'fixture-key')

    def test_keychain_notice_and_missing_model_can_be_completed_on_import(self):
        result=self.scan({'cli_auth_credentials_store':'keyring'})
        self.assertIsNotNone(result['discovery']['notice'])
        config=self.config();config.pop('model');config.pop('profiles')
        row=self.scan(config)['discovery']['candidates'][0]
        self.assertTrue(row['canImport'])
        with self.assertRaisesRegex(ValueError,'模型 ID'):self.manager.import_account({'candidateId':row['id']})
        result=self.manager.import_account({'candidateId':row['id'],'model':'selected-model'})
        self.assertEqual(result['accounts'][0]['model'],'selected-model')


class ModelHTTPTests(unittest.TestCase):
    def setUp(self):
        self.requests=[];self.status=200;self.body={'data':[{'id':'z'},{'id':'a'},{'id':'a'},{'id':'bad\nmodel'}]}
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append((self.path,self.headers.get('Authorization')))
                self.send_response(owner.status)
                if owner.status==302:self.send_header('Location','/credential-leak')
                body=json.dumps(owner.body).encode()
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Connection','close')
                self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        time.sleep(.05)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            try:
                with socket.create_connection(('127.0.0.1',self.server.server_port),timeout=.1):break
            except OSError:
                if time.monotonic()>=deadline:raise
                time.sleep(.02)
        self.url='http://127.0.0.1:'+str(self.server.server_port)+'/v1'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def test_real_http_uses_bearer_and_returns_sorted_unique_ids(self):
        self.assertEqual(model_ids(self.url,'fixture-key'),['a','z'])
        self.assertEqual(self.requests,[('/v1/models','Bearer fixture-key')])

    def test_redirect_is_not_followed_and_error_body_is_not_exposed(self):
        self.status=302;self.body={'error':'fixture-key'}
        with self.assertRaisesRegex(ValueError,'跳转') as error:model_ids(self.url,'fixture-key')
        self.assertNotIn('fixture-key',str(error.exception));self.assertEqual(len(self.requests),1)
        self.status=401
        with self.assertRaisesRegex(ValueError,'拒绝访问') as error:model_ids(self.url,'fixture-key')
        self.assertNotIn('fixture-key',str(error.exception))

    def test_empty_and_incompatible_lists_remain_manual_entry_cases(self):
        self.status=200
        for body in ({'data':[]},{'models':[]},[]):
            self.body=body
            with self.assertRaisesRegex(ValueError,'手动填写'):model_ids(self.url,'fixture-key')
