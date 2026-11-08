import unittest
from pathlib import Path

from bridge.clients.zcode.installation import renderer_url
from bridge.clients.zcode.transport import bind_workspace_registry, verified_target, expression


class ZCodeTransportTests(unittest.TestCase):
    def test_target_rejects_wrong_app_remote_redirect_and_ambiguity(self):
        application = str(Path('ZCode.app').absolute())
        target = {'type': 'page', 'url': renderer_url(application),
                  'webSocketDebuggerUrl': 'ws://127.0.0.1:17742/devtools/page/ABC'}
        self.assertEqual(verified_target([target], application, 17742), '/devtools/page/ABC')
        for url in ('ws://example.com:17742/devtools/page/ABC', 'ws://127.0.0.1:17743/devtools/page/ABC',
                    'ws://user@127.0.0.1:17742/devtools/page/ABC', 'ws://127.0.0.1:17742/devtools/page/ABC?redirect=1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                verified_target([{**target, 'webSocketDebuggerUrl': url}], application, 17742)
        with self.assertRaises(ValueError):
            verified_target([target, target], application, 17742)
        with self.assertRaises(ValueError):
            verified_target([{**target, 'url': 'file:///wrong'}], application, 17742)

    def test_registry_binding_is_unique_and_always_releases_handles(self):
        calls = []
        duplicates = False
        def rpc(method, params):
            calls.append((method, params))
            if method == 'Runtime.evaluate':
                return {'result': {'type': 'function', 'objectId': 'fn'}}
            if method == 'Runtime.callFunctionOn':
                return {'result': {'value': True}}
            if method == 'Runtime.releaseObjectGroup':
                return {}
            if params['objectId'] == 'fn':
                return {'internalProperties': [{'name': '[[Scopes]]', 'value': {'objectId': 'scopes'}}]}
            if params['objectId'] == 'scopes':
                return {'result': [{'value': {'description': 'Module', 'objectId': 'module'}}]}
            return {'result': [{'value': {'type': 'function', 'objectId': 'registry',
                                'description': 'v4.workspace_connection.reused v4.workspace_connection.created'}}] * (2 if duplicates else 1)}
        bind_workspace_registry(rpc, {})
        self.assertEqual(calls[-1][0], 'Runtime.releaseObjectGroup')
        self.assertEqual(sum(m == 'Runtime.callFunctionOn' for m, _ in calls), 1)
        duplicates = True
        with self.assertRaisesRegex(ValueError, '不唯一'):
            bind_workspace_registry(rpc, {})
        self.assertEqual(sum(m == 'Runtime.callFunctionOn' for m, _ in calls), 1)
        self.assertEqual(calls[-1][0], 'Runtime.releaseObjectGroup')

    def test_renderer_function_is_self_contained_and_request_is_json_encoded(self):
        source = expression({'text': "'` ${danger}\n中文"})
        self.assertNotIn('export async function', source)
        self.assertIn('async function nativeRequest', source)
        self.assertIn('\\n中文', source)
