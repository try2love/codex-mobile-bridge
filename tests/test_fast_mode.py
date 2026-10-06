import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bridge as support
from bridge.catalog import Catalog, CatalogError
from bridge.model import normalize_state
from bridge.ipc import IPCError

THREAD = support.THREAD
MODEL = {'id': 'official-model', 'efforts': ['high'], 'fastTier': 'priority', 'defaultServiceTier': 'priority'}


class FastCapabilityTests(unittest.TestCase):
    def capability(self, config=None, auth=None, requirements=None, fail=None):
        calls = []
        values = {'config/read': {'config': config or {}},
                  'account/read': auth if auth is not None else {'account': {'type': 'chatgpt', 'email': 'private'}, 'requiresOpenaiAuth': True},
                  'configRequirements/read': {'requirements': requirements}}
        def request(method, params):
            calls.append((method, params))
            if method == fail: raise CatalogError('unsupported')
            return values[method]
        return Catalog.fast_mode(request, '/project'), calls

    def test_native_auth_policy_and_config_are_read_only_and_sanitized(self):
        result, calls = self.capability({'service_tier': 'priority', 'secret': 'private'})
        self.assertEqual(result, {'allowed': True, 'defaultServiceTier': 'priority'})
        self.assertEqual([m for m, _ in calls], ['config/read', 'account/read', 'configRequirements/read'])
        self.assertEqual(calls[0][1]['cwd'], '/project')
        self.assertFalse(calls[1][1]['refreshToken'])
        self.assertNotIn('private', str(result))

    def test_api_custom_providers_signed_out_policy_and_old_runtime_cannot_enable(self):
        for config in [{'model_provider': 'proxy'}, {'model_providers': {'openai': {'base_url': 'https://custom.test'}}},
                       {'profile': 'custom', 'profiles': {'custom': {'model_provider': 'local'}}}]:
            result, calls = self.capability(config)
            self.assertFalse(result['allowed']);self.assertEqual(len(calls), 1)
        for auth in [{'account': {'type': 'apiKey'}}, {'account': None}, {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': False}]:
            self.assertFalse(self.capability(auth=auth)[0]['allowed'])
        self.assertFalse(self.capability(requirements={'featureRequirements': {'fast_mode': False}})[0]['allowed'])
        self.assertFalse(self.capability({'features': {'fast_mode': False}})[0]['allowed'])
        self.assertFalse(self.capability(fail='configRequirements/read')[0]['allowed'])

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
