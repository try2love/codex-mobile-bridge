import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.clients.codex.catalog import Catalog
from bridge.features.accounts.models import model_catalog, valid_model_id

ROOT = Path(__file__).resolve().parents[1]


class ApiModelAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.reader = Catalog(self.home, 'fixture-runtime')
        self.definition = {'base_url': 'https://fixture.invalid/v1',
                           'experimental_bearer_token': 'fixture-bearer',
                           'http_headers': {'x-openai-actor-authorization': 'fixture-actor'}}
        self.config = {'model_provider': 'api', 'model_providers': {'api': self.definition}}
        self.calls = []

    def request(self, method, params):
        self.calls.append(method)
        if method == 'model/list':
            return {'data': []}
        self.assertEqual(method, 'config/read')
        return {'config': self.config}

    def read(self, payload, environment=None):
        with patch('bridge.features.accounts.models.build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
            result = self.reader.read_models(self.request, str(self.home), environment=environment)
            return result, factory.return_value.open

    def test_actor_auth_is_sent_and_upstream_efforts_survive_public_catalog(self):
        result, opened = self.read({'models': [{'slug': 'routed-model', 'display_name': 'Routed model',
            'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'high'}],
            'default_reasoning_level': 'high'}]})
        self.assertNotIn('modelError', result)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header('X-openai-actor-authorization'), 'fixture-actor')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-bearer')
        self.reader._fetch = lambda *args: result
        public = self.reader.get_kind('models', self.home)
        self.assertEqual(public['models'][0]['efforts'], ['low', 'high'])
        self.assertEqual(public['models'][0]['defaultEffort'], 'high')
        self.assertEqual(public['models'][0]['name'], 'Routed model')
        self.assertNotIn('fixture-actor', json.dumps(public))
        self.assertNotIn('fixture-bearer', json.dumps(public))
        self.assertEqual(self.calls, ['config/read'])

    def test_unicode_provider_model_ids_are_valid_but_whitespace_and_controls_stay_rejected(self):
        model = 'xmy:日卡/deepseek-v4.1-flash'
        self.assertTrue(valid_model_id(model))
        self.assertTrue(valid_model_id('ccs-openai/gpt-6-astra'))
        for invalid in ('', ' route/model', 'route/model ', 'route/\tmodel', 'route/\nmodel', 'route/\x00model',
                        'route/\u202emodel', 'x' * 201):
            with self.subTest(value=invalid):
                self.assertFalse(valid_model_id(invalid))
        result, _ = self.read({'models': [{'slug': model, 'display_name': 'DeepSeek Flash'}]})
        self.assertNotIn('modelError', result)
        self.assertEqual([row['model'] for row in result['models']], [model])

    def test_static_and_env_headers_and_query_parameters_are_preserved(self):
        self.definition.update(env_http_headers={'X-tenant': 'CMB_TEST_TENANT'}, query_params={'api-version': 'fixture version'})
        result, opened = self.read({'data': [{'id': 'api-model', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]}]},
                                   environment={'CMB_TEST_TENANT': 'fixture-tenant'})
        self.assertNotIn('modelError', result)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, 'https://fixture.invalid/v1/models?api-version=fixture+version')
        self.assertEqual(request.get_header('X-tenant'), 'fixture-tenant')
        self.assertEqual(result['models'][0]['supportedReasoningEfforts'], [{'reasoningEffort': 'medium'}])

    def test_missing_env_auth_header_does_not_send_partial_credentials(self):
        self.definition['env_http_headers'] = {'X-key': 'CMB_MISSING_FIXTURE_HEADER'}
        with patch.dict(os.environ, {}, clear=True):
            result, opened = self.read({'data': [{'id': 'api-model'}]})
        opened.assert_not_called()
        self.assertIn('认证请求头', result['modelError'])

    def test_gateway_environment_cannot_supply_desktop_header_or_key(self):
        self.definition.pop('experimental_bearer_token')
        for field, value in (('env_http_headers', {'X-key': 'CMB_DESKTOP_ONLY'}),
                             ('env_key', 'CMB_DESKTOP_ONLY')):
            with self.subTest(field=field):
                self.definition.pop('env_http_headers', None)
                self.definition.pop('env_key', None)
                self.definition[field] = value
                with patch.dict(os.environ, {'CMB_DESKTOP_ONLY': 'gateway-A'}):
                    result, opened = self.read({'data': [{'id': 'api-model'}]})
                opened.assert_not_called()
                self.assertIn('modelError', result)
                self.assertNotIn('gateway-A', json.dumps(result))

    def test_explicit_verified_environment_uses_its_own_header(self):
        self.definition.pop('experimental_bearer_token')
        self.definition['env_http_headers'] = {'X-key': 'CMB_DESKTOP_ONLY'}
        with patch.dict(os.environ, {'CMB_DESKTOP_ONLY': 'gateway-A'}):
            result, opened = self.read({'data': [{'id': 'api-model'}]},
                                       environment={'CMB_DESKTOP_ONLY': 'verified-B'})
        self.assertNotIn('modelError', result)
        self.assertEqual(opened.call_args.args[0].get_header('X-key'), 'verified-B')

    def test_header_only_auth_does_not_require_an_unrelated_openai_key(self):
        self.definition.pop('experimental_bearer_token')
        self.config['model_provider'] = 'openai'
        self.config['model_providers'] = {'openai': self.definition}
        result, opened = self.read({'data': [{'id': 'header-model'}]})
        self.assertNotIn('modelError', result)
        self.assertIsNone(opened.call_args.args[0].get_header('Authorization'))

    def test_external_auth_command_is_never_executed_for_catalog(self):
        self.definition['auth'] = {'command': 'do-not-execute'}
        result, opened = self.read({'data': [{'id': 'model'}]})
        opened.assert_not_called()
        self.assertIn('额外认证', result['modelError'])

    def test_upstream_id_only_response_does_not_invent_reasoning_capabilities(self):
        result, _ = self.read({'data': [{'id': 'plain-model'}]})
        self.assertNotIn('modelError', result)
        self.reader._fetch = lambda *args: result
        public = self.reader.get_kind('models', self.home)
        self.assertEqual(public['models'][0]['efforts'], [])
        self.assertIsNone(public['models'][0]['defaultEffort'])

    def test_claude_explicit_effort_capabilities_exclude_unsupported_levels_and_retired_models(self):
        payload = {'data': [
            {'id': 'available', 'lifecycle': 'active', 'capabilities': {'effort': {'supported': True,
                'low': {'supported': True}, 'medium': {'supported': False}, 'high': {'supported': True},
                'xhigh': {'supported': False}, 'max': {'supported': True}}}},
            {'id': 'no-effort', 'capabilities': {'effort': {'supported': False, 'high': {'supported': True}}}},
            {'id': 'retired', 'lifecycle': 'retired', 'capabilities': {'effort': {'supported': True,
                'high': {'supported': True}}}}]}
        with patch('bridge.features.accounts.models.build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
            rows = model_catalog('https://fixture.invalid/v1', 'fixture-key')
        self.assertEqual([row['id'] for row in rows], ['available', 'no-effort'])
        self.assertEqual(rows[0]['efforts'], ['low', 'high', 'max'])
        self.assertEqual(rows[1]['efforts'], [])

    def test_native_efforts_only_enrich_exact_available_ids_and_preserve_upstream_metadata(self):
        payload = {'data': [
            {'id': 'exact', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]},
            {'id': 'plain'}, {'id': 'route/plain'}]}
        def metadata(method, params):
            return {'data': [{'model': 'exact', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]},
                {'model': 'plain', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}], 'defaultReasoningEffort': 'medium'},
                {'model': 'not-available', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]}
        with patch('bridge.features.accounts.models.build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
            result = self.reader.read_models(self.request, str(self.home), metadata_request=metadata)
        self.assertEqual([row['model'] for row in result['models']], ['exact', 'plain', 'route/plain'])
        self.assertEqual([row['supportedReasoningEfforts'] for row in result['models']],
                         [[{'reasoningEffort': 'low'}], [{'reasoningEffort': 'medium'}], []])

    def test_native_metadata_failure_keeps_successful_upstream_list(self):
        from bridge.clients.codex.catalog import CatalogError
        with patch('bridge.features.accounts.models.build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(b'{"data":[{"id":"plain"}]}')
            result = self.reader.read_models(self.request, str(self.home), metadata_request=lambda *a: (_ for _ in ()).throw(CatalogError('timeout')))
        self.assertNotIn('modelError', result)
        self.assertEqual(result['models'][0]['model'], 'plain')

    def test_invalid_header_is_rejected_without_echoing_the_secret(self):
        self.definition['http_headers'] = {'X-key': 'private\nfixture'}
        result, opened = self.read({'data': [{'id': 'plain'}]})
        opened.assert_not_called()
        self.assertIn('请求头格式无效', result['modelError'])
        self.assertNotIn('private', json.dumps(result))
