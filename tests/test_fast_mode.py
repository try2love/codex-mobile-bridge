import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bridge as support
from bridge.clients.codex.catalog import Catalog, CatalogError
from bridge.features.sessions.model import normalize_state
from bridge.clients.codex.ipc import IPCError

THREAD = support.THREAD
MODEL = {'id': 'official-model', 'efforts': ['high'], 'fastTier': 'priority', 'defaultServiceTier': 'priority'}


class FastCapabilityTests(unittest.TestCase):
    def capability(self, config=None, auth=None, requirements=None, fail=None, provider=None):
        calls = []
        values = {'config/read': {'config': config or {}},
                  'account/read': auth if auth is not None else {'account': {'type': 'chatgpt', 'email': 'private'}, 'requiresOpenaiAuth': True},
                  'configRequirements/read': {'requirements': requirements}}
        def request(method, params):
            calls.append((method, params))
            if method == fail: raise CatalogError('unsupported')
            return values[method]
        return Catalog.fast_mode(request, '/project', provider), calls

    def test_native_auth_policy_and_config_are_read_only_and_sanitized(self):
        result, calls = self.capability({'service_tier': 'priority', 'secret': 'private'})
        self.assertEqual(result, {'allowed': True, 'provider': 'openai', 'defaultServiceTier': 'priority'})
        self.assertEqual([m for m, _ in calls], ['config/read', 'account/read', 'configRequirements/read'])
        self.assertEqual(calls[0][1]['cwd'], '/project')
        self.assertFalse(calls[1][1]['refreshToken'])
        self.assertNotIn('private', str(result))

    def test_unscoped_api_providers_signed_out_policy_and_old_runtime_cannot_enable(self):
        for config in [{'model_provider': 'proxy'}, {'model_providers': {'openai': {'base_url': 'https://custom.test'}}},
                       {'profile': 'custom', 'profiles': {'custom': {'model_provider': 'local'}}}]:
            result, calls = self.capability(config)
            self.assertFalse(result['allowed']);self.assertEqual(len(calls), 1)
        for auth in [{'account': {'type': 'apiKey'}}, {'account': None}, {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': False}]:
            self.assertFalse(self.capability(auth=auth)[0]['allowed'])
        self.assertFalse(self.capability(requirements={'featureRequirements': {'fast_mode': False}})[0]['allowed'])
        self.assertFalse(self.capability({'features': {'fast_mode': False}})[0]['allowed'])
        self.assertFalse(self.capability(fail='configRequirements/read')[0]['allowed'])

    def test_explicit_native_catalog_scopes_custom_fast_and_keeps_policy_checks(self):
        config = {'model_provider': 'custom', 'model_catalog_json': 'models.json', 'service_tier': 'default',
                  'model_providers': {'custom': {'base_url': 'http://127.0.0.1:15721/v1', 'env_key': 'PRIVATE'}}}
        result, calls = self.capability(config, auth={'account': None})
        self.assertEqual(result, {'allowed': True, 'provider': 'custom', 'defaultServiceTier': 'default'})
        self.assertEqual([method for method, _ in calls], ['config/read', 'configRequirements/read'])
        self.assertFalse(self.capability(config, provider='other')[0]['allowed'])
        self.assertFalse(self.capability(config, requirements={'featureRequirements': {'fast_mode': False}})[0]['allowed'])
        self.assertFalse(self.capability({**config, 'features': {'fast_mode': False}})[0]['allowed'])
        self.assertFalse(self.capability(config, fail='configRequirements/read')[0]['allowed'])
        profile = {'profile': 'route', 'profiles': {'route': config}}
        self.assertEqual(self.capability(profile)[0]['provider'], 'custom')
        self.assertNotIn('PRIVATE', str(result))

    def test_route_speed_metadata_uses_visible_native_base_and_preserves_explicit_overrides(self):
        reader = Catalog('/unused', 'unused')
        models = [
            {'model': 'gpt-fixture', 'serviceTiers': [{'id': 'priority', 'name': 'Fast'}], 'defaultServiceTier': 'default'},
            {'model': 'route/gpt-fixture', 'serviceTiers': []},
            {'model': 'override/gpt-fixture', 'serviceTiers': [{'id': 'fast', 'name': 'Fast'}], 'defaultServiceTier': 'fast'},
            {'model': 'standard/gpt-fixture', 'serviceTiers': [{'id': 'default', 'name': 'Standard'}]},
            {'model': 'hidden', 'hidden': True, 'serviceTiers': [{'id': 'priority'}]},
            {'model': 'route/hidden'}, {'model': 'route/image'}, {'model': 'image', 'serviceTiers': []},
        ]
        def request(method, params):
            if method == 'config/read':
                return {'config': {'model_provider': 'custom', 'model_catalog_json': 'models.json'}}
            self.assertEqual(method, 'model/list')
            return {'data': copy.deepcopy(models), 'nextCursor': None}
        result = reader.read_models(request, '/project', 'custom')['models']
        self.assertEqual([m['model'] for m in result], [m['model'] for m in models])
        self.assertEqual(reader.fast_tier(result[1]), 'priority')
        self.assertEqual(result[1]['defaultServiceTier'], 'default')
        self.assertEqual(reader.fast_tier(result[2]), 'fast')
        self.assertEqual(result[2]['defaultServiceTier'], 'fast')
        for index in (3, 5, 6, 7):
            self.assertIsNone(reader.fast_tier(result[index]))

    def test_catalog_keeps_model_tier_ids_defaults_and_filters_unknown_capabilities(self):
        reader = Catalog('/unused', 'unused')
        reader._fetch = lambda cwd, provider=None: {'models': [
            {'model': 'one', 'serviceTiers': [{'id': 'priority', 'name': 'Fast'}, {'id': 'ultrafast', 'name': 'Ultrafast'}], 'defaultServiceTier': 'priority'},
            {'model': 'two', 'serviceTiers': [{'id': 'fast', 'name': 'Fast'}]},
            {'model': 'other', 'serviceTiers': []}], 'skillEntries': [], 'fastMode': {'allowed': True}}
        result = reader.get('.')
        self.assertEqual([m['fastTier'] for m in result['models']], ['priority', 'fast', None])
        self.assertEqual(result['models'][0]['defaultServiceTier'], 'priority')
        self.assertTrue(result['fastMode']['allowed'])

    def test_view_prefers_pending_settings_and_preserves_explicit_standard_or_null(self):
        state = {**support.state(), 'turns': [{'turnId': 'last', 'params': {'serviceTier': 'priority'}}]}
        self.assertEqual(normalize_state(state)['serviceTier'], 'priority')
        for value in ('default', None, 'ultrafast'):
            state['latestThreadSettings'] = {'serviceTier': value}
            self.assertEqual(normalize_state(state)['serviceTier'], value)
        self.assertNotIn('serviceTier', normalize_state(support.state()))



    def test_route_reasoning_metadata_uses_visible_base_and_preserves_explicit_efforts(self):
        reader = Catalog('/unused', 'unused')
        base = {'model': 'base', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}],
                'defaultReasoningEffort': 'high'}
        models = [base, {'model': 'route/base', 'supportedReasoningEfforts': []},
                  {'model': 'override/base', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}],
                   'defaultReasoningEffort': 'low'},
                  {'model': 'hidden', 'hidden': True, **{key: value for key, value in base.items() if key != 'model'}},
                  {'model': 'route/hidden'}]
        def request(method, params):
            if method == 'config/read':
                return {'config': {'model_provider': 'custom', 'model_catalog_json': 'models.json'}}
            return {'data': copy.deepcopy(models)}
        result = reader.read_models(request, '/project', 'custom')['models']
        self.assertEqual(result[1]['model'], 'route/base')
        self.assertEqual(result[1]['supportedReasoningEfforts'], base['supportedReasoningEfforts'])
        self.assertEqual(result[1]['defaultReasoningEffort'], 'high')
        self.assertEqual(result[2]['supportedReasoningEfforts'], [{'reasoningEffort': 'low'}])
        self.assertEqual(result[2]['defaultReasoningEffort'], 'low')
        self.assertNotIn('supportedReasoningEfforts', result[4])


class FastSettingsTests(unittest.TestCase):
    setUp = support.IntegrationTests.setUp
    tearDown = support.IntegrationTests.tearDown

    def prepare(self, tier='priority'):
        self.fixture.state.update(modelProvider='openai', latestModel='official-model', latestThreadSettings={'serviceTier': tier})
        self.catalog = {'models': [copy.deepcopy(MODEL)], 'skills': [], 'fastMode': {'allowed': True, 'defaultServiceTier': 'priority'}}
        self.reads = []
        def get(cwd, refresh=False, provider=None):
            self.reads.append(refresh);return copy.deepcopy(self.catalog)
        self.bridge.catalog_reader.get = get
        self.bridge.catalog_reader.get_kind = lambda kind, *a, **k: get(*a, **k)

    def calls(self):
        return [r for r in self.fixture.requests if r['method'] == 'thread-follower-update-thread-settings']

    def test_split_catalog_keeps_current_tier_and_provider_boundary(self):
        self.prepare('default')
        self.bridge.catalog_reader.get_kind = lambda *args, **kwargs: copy.deepcopy(self.catalog)
        value = self.bridge.catalog(THREAD, kind='models')
        self.assertTrue(value['fastMode']['allowed'])
        self.assertEqual(value['currentServiceTier'], 'default')
        self.fixture.state.update(modelProvider='custom')
        self.bridge.session(THREAD).state['modelProvider'] = 'custom'
        self.assertFalse(self.bridge.catalog(THREAD, kind='models')['fastMode']['allowed'])

    def test_enable_disable_rechecks_and_updates_original_owner_without_starting_turn(self):
        self.prepare('default')
        result = self.bridge.settings(THREAD, 'official-model', 'high', fast_mode=True)
        self.assertTrue(result['confirmed']);self.assertEqual(result['serviceTier'], 'priority')
        result = self.bridge.settings(THREAD, 'official-model', 'high', fast_mode=False)
        self.assertTrue(result['confirmed']);self.assertEqual(result['serviceTier'], 'default')
        self.assertEqual(self.reads, [True, True])
        self.assertEqual([r['params']['threadSettings']['serviceTier'] for r in self.calls()], ['priority', 'default'])
        for call in self.calls():
            self.assertEqual(call['targetClientId'], 'owner');self.assertNotIn('activeTurnId', call['params'])
            self.assertEqual(set(call['params']['threadSettings']), {'model', 'effort', 'serviceTier'})
        self.assertFalse(any('start-turn' in r['method'] for r in self.fixture.requests))
        self.assertEqual(self.bridge.view(THREAD)['serviceTier'], 'default')
        self.assertEqual(self.bridge.catalog(THREAD)['currentServiceTier'], 'default')

    def test_custom_route_fast_settings_preserve_provider_model_owner_and_active_turn(self):
        self.prepare('default')
        route = 'route/official-model'
        self.fixture.state.update(modelProvider='custom', latestModel=route)
        self.fixture.state['threadRuntimeStatus']['type'] = 'active'
        session = self.bridge.session(THREAD)
        with session.condition:
            session.state.update(modelProvider='custom', latestModel=route)
            session.state['threadRuntimeStatus']['type'] = 'active'
        self.catalog['models'][0]['id'] = route
        self.catalog['fastMode']['provider'] = 'custom'
        value = self.bridge.catalog(THREAD, kind='models')
        self.assertTrue(value['fastMode']['allowed'])
        self.assertEqual(value['fastMode']['provider'], 'custom')
        for enabled, tier in [(True, 'priority'), (False, 'default')]:
            result = self.bridge.settings(THREAD, route, 'high', fast_mode=enabled)
            self.assertTrue(result['confirmed'])
            self.assertEqual(result['serviceTier'], tier)
            call = self.calls()[-1]
            self.assertEqual(call['targetClientId'], 'owner')
            self.assertNotIn('activeTurnId', call['params'])
            self.assertEqual(call['params']['threadSettings'], {'model': route, 'effort': 'high', 'serviceTier': tier})
            self.assertEqual(self.bridge.view(THREAD)['provider'], 'custom')
            self.assertEqual(self.fixture.state['threadRuntimeStatus']['type'], 'active')
        self.catalog['fastMode']['provider'] = 'other'
        with self.assertRaises(ValueError):
            self.bridge.settings(THREAD, route, 'high', fast_mode=True)
        self.assertEqual(len(self.calls()), 2)
        self.assertFalse(any('start-turn' in r['method'] for r in self.fixture.requests))

    def test_remote_active_thread_changes_next_turn_and_omission_preserves_existing_tier(self):
        self.prepare('ultrafast');self.bridge.host = self.fixture.host = 'remote:test'
        self.fixture.state['threadRuntimeStatus']['type'] = 'active'
        self.bridge.settings(THREAD, 'official-model', 'high')
        self.assertNotIn('serviceTier', self.calls()[0]['params']['threadSettings'])
        self.assertEqual(self.bridge.view(THREAD)['serviceTier'], 'ultrafast')
        self.bridge.settings(THREAD, 'official-model', 'high', fast_mode=False)
        self.assertEqual(self.calls()[-1]['hostId'], 'remote:test')
        self.assertEqual(self.fixture.state['threadRuntimeStatus']['type'], 'active')

    def test_fresh_capabilities_and_thread_provider_block_unsupported_writes(self):
        self.prepare();self.bridge.catalog(THREAD)
        self.catalog['fastMode']['allowed'] = False
        with self.assertRaises(ValueError): self.bridge.settings(THREAD, 'official-model', 'high', fast_mode=True)
        self.catalog['fastMode']['allowed'] = True;self.catalog['models'][0]['fastTier'] = None
        with self.assertRaises(ValueError): self.bridge.settings(THREAD, 'official-model', 'high', fast_mode=True)
        self.catalog['models'][0]['fastTier'] = 'priority'
        session = self.bridge.session(THREAD)
        with session.condition: session.state['modelProvider'] = 'custom-api'
        with self.assertRaises(ValueError): self.bridge.settings(THREAD, 'official-model', 'high', fast_mode=False)
        with self.assertRaises(ValueError): self.bridge.settings(THREAD, 'official-model', 'high', fast_mode='yes')
        self.assertEqual(self.calls(), [])

    def test_rejected_settings_never_claim_success(self):
        self.prepare()
        with patch.object(self.bridge, '_call', return_value={'applied': False}):
            with self.assertRaises(IPCError): self.bridge.settings(THREAD, 'official-model', 'high', fast_mode=False)
        self.assertEqual(self.bridge.view(THREAD)['serviceTier'], 'priority')


class FastHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    setUp = support.HttpTests.setUp
    tearDown = support.HttpTests.tearDown
    request = support.HttpTests.request
    login = support.HttpTests.login

    def test_authenticated_settings_forward_boolean_only_and_preserve_legacy_omission(self):
        writes=[]
        self.server.bridge.settings=lambda *args, **kw: writes.append((args, kw)) or {'applied': True}
        headers=self.login();url='/api/sessions/'+THREAD+'/settings';body={'model':'official-model','effort':'high'}
        for value in (True,False):
            self.assertEqual(self.request('POST',url,{**body,'fastMode':value},headers)[0],200)
            self.assertEqual(writes[-1][1],{'fast_mode':value})
        self.assertEqual(self.request('POST',url,body,headers)[0],200);self.assertEqual(writes[-1][1],{})
        for value in (None,'true',1,{}):
            self.assertEqual(self.request('POST',url,{**body,'fastMode':value},headers)[0],400)
        self.assertEqual(len(writes),3)
