import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from bridge.clients.manager import DesktopSessions
from bridge.clients.zcode.accounts import ZCodeAccounts


class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                cls.requests.append((self.command, self.path, dict(self.headers)))
                if self.path == '/redirect/models':
                    self.send_response(302); self.send_header('Location', '/must-not-receive-key'); self.end_headers(); return
                if self.path != '/v1/models':
                    self.send_response(404); self.end_headers(); return
                body = json.dumps({'data': [{'id': 'model-a'}, {'id': 'model-b'}]}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                cls.requests.append((self.command, self.path, dict(self.headers)))
                self.send_response(405); self.end_headers()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = 'http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(2)

    def setUp(self):
        self.requests.clear()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); home = root/'home'; home.mkdir()
        self.store = ZCodeAccounts(root/'saved', home, lambda: {})
        self.manager = object.__new__(DesktopSessions); self.manager.config = {}
        self.manager._account_store = lambda provider: self.store

    def probe(self, **fields):
        value = {'operation': 'models-draft', 'baseUrl': self.base+'/v1', 'apiKey': 'fixture-key',
                 'apiFormat': 'openai-responses', **fields}
        return self.manager.client_accounts('zcode', value)

    def test_draft_get_and_saved_blank_key_use_same_shared_probe_without_inference(self):
        result = self.probe()
        self.assertEqual(result, {'models': ['model-a', 'model-b']})
        self.assertEqual(self.requests[-1][:2], ('GET', '/v1/models'))
        self.assertEqual(self.requests[-1][2]['Authorization'], 'Bearer fixture-key')
        saved = self.store.save_api({'name': 'Fixture', 'baseUrl': self.base+'/v1', 'apiKey': 'saved-fixture-key',
                                     'apiFormat': 'openai-responses', 'models': ['model-a']})
        identifier = saved['accounts'][0]['id']
        result = self.probe(id=identifier, apiKey='')
        self.assertEqual(self.requests[-1][2]['Authorization'], 'Bearer saved-fixture-key')
        self.assertEqual(len(self.requests), 2); self.assertTrue(all(row[0]=='GET' for row in self.requests))
        self.assertNotIn('saved-fixture-key', json.dumps(result)); self.assertFalse((self.store.home/'provider_config.json').exists())
        self.assertNotIn('saved-fixture-key', json.dumps(self.store.edit(identifier)))
        count = len(self.requests)
        for changes in ({'baseUrl': self.base+'/other'}, {'apiFormat': 'openai-chat-completions'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '重新填写'):
                self.probe(id=identifier, apiKey='', **changes)
        self.assertEqual(len(self.requests), count)

    def test_native_anthropic_protocols_use_get_v1_models_with_matching_auth_only(self):
        result = self.probe(baseUrl=self.base, apiFormat='anthropic-messages')
        self.assertEqual(result['models'], ['model-a', 'model-b'])
        method, url, headers = self.requests[-1]
        self.assertEqual((method, url), ('GET', '/v1/models'))
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lowered['x-api-key'], 'fixture-key'); self.assertEqual(lowered['anthropic-version'], '2023-06-01')
        self.assertNotIn('authorization', lowered)
        result = self.manager.client_accounts('claude', {'operation': 'models-draft', 'baseUrl': self.base+'/v1',
                                                          'apiKey': 'bearer-fixture-key', 'authScheme': 'bearer'})
        self.assertEqual(self.requests[-1][:2], ('GET', '/v1/models'))
        self.assertEqual(self.requests[-1][2]['Authorization'], 'Bearer bearer-fixture-key')
        self.assertNotIn('X-api-key', self.requests[-1][2])

    def test_redirect_and_invalid_target_never_receive_reused_credentials(self):
        with self.assertRaisesRegex(ValueError, '跳转'):
            self.probe(baseUrl=self.base+'/redirect')
        self.assertEqual([row[1] for row in self.requests], ['/redirect/models'])
        count = len(self.requests)
        for fields in ({'baseUrl': 'https://user:secret@example.invalid'}, {'baseUrl': self.base+'/?secret=x'},
                       {'baseUrl': 'http://example.invalid'}, {'apiKey': 'invalid\nheader'}, {'apiFormat': 'unknown'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.probe(**fields)
        self.assertEqual(len(self.requests), count)

    def test_dsh_only_queries_the_official_api_and_never_accepts_custom_target(self):
        with patch('bridge.features.accounts.api_drafts.model_ids', return_value=['deepseek-model']) as read:
            value = self.manager.client_accounts('deepseek', {'operation': 'models-draft', 'apiKey': 'fixture-key'})
            self.assertEqual(value, {'models': ['deepseek-model']})
            read.assert_called_once_with('https://api.deepseek.com', 'fixture-key')
            with self.assertRaisesRegex(ValueError, '官方 API 地址'):
                self.manager.client_accounts('deepseek', {'operation': 'models-draft', 'baseUrl': self.base, 'apiKey': 'fixture-key'})
            self.assertEqual(read.call_count, 1)
        self.assertEqual(self.requests, [])
