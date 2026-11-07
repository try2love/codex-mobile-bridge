"""Shared instance metadata cannot redirect native profiles or default clients."""
import functools
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.clients.profiles import NativeProfile, resolve_profile
from bridge.features.clients.instances import Instances, instance_id
from bridge.features.endpoints.registry import Entries, local_entry_id

ROOT = Path(__file__).resolve().parents[1]


class ClientInstancesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.gateway = self.root/'gateway'
        self.default = self.home('default-claude')
        self.defaults = [resolve_profile('claude', self.default, platform='darwin')]
        self.resolver = Mock(side_effect=functools.partial(resolve_profile, 'claude', platform='darwin'))
        self.resolvers = {('claude', 'desktop-profile'): self.resolver,
                          ('deepseek', 'desktop-profile'): functools.partial(resolve_profile, 'deepseek', platform='linux')}
        self.registry = Instances(self.gateway, resolvers=self.resolvers, defaults=lambda: self.defaults)

    def home(self, name):
        path = self.root/name
        path.mkdir(parents=True)
        return path

    def request(self, home, family='claude', label='Independent'):
        return {'family': family, 'kind': 'desktop-profile', 'label': label, 'home': str(home)}

    def test_reads_do_not_create_native_or_gateway_directories_or_call_resolvers(self):
        self.assertEqual(self.registry.list(), [])
        self.assertIsNone(self.registry.resolve('claude'))
        self.assertIsNone(self.registry.resolve('zcode', 'zcode'))
        self.assertIsNone(self.registry.resolve('codex', 'desktop'))
        self.assertFalse(self.gateway.exists())
        self.resolver.assert_not_called()

    def test_registration_persists_only_metadata_and_keeps_gateway_owner_root_separate(self):
        home = self.home('other')
        before = set(home.iterdir())
        row = self.registry.register(self.request(home))
        self.assertEqual(row['id'], instance_id('claude', home))
        self.assertNotIn('connected', row)
        self.assertNotIn('configured', row)
        self.assertFalse(row['enabled'])
        target = self.registry.resolve('claude', row['id'], require_enabled=False)
        self.assertEqual(target.profile.home, home)
        self.assertEqual(target.gateway_root, self.gateway)
        self.assertEqual(target.state_root, self.gateway/'client-instances'/row['id'].replace(':', '-'))
        self.assertNotEqual(target.state_root, target.gateway_root)
        self.assertFalse(target.state_root.exists())
        self.assertEqual(set(home.iterdir()), before)
        self.assertFalse(Path(str(home)+'-3p').exists())
        self.assertEqual(Instances(self.gateway, resolvers=self.resolvers, defaults=lambda:self.defaults).list(), [row])
        if os.name != 'nt':
            self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.registry.directory.stat().st_mode & 0o777, 0o700)

    def test_unregistered_mismatched_disabled_and_zcode_entries_never_fall_back(self):
        row = self.registry.register(self.request(self.home('other')))
        for family, entry in [('claude', 'claude:'+'0'*32), ('deepseek', row['id']), ('claude',''), ('claude','zcode')]:
            with self.subTest(family=family, entry=entry), self.assertRaises(ValueError):
                self.registry.resolve(family, entry)
        changed = self.registry.configure(row['id'], False)
        self.assertFalse(changed['enabled'])
        with self.assertRaisesRegex(ValueError, '关闭'):self.registry.resolve('claude', row['id'])
        self.assertFalse(self.registry.resolve('claude', row['id'], require_enabled=False).enabled)
        with self.assertRaisesRegex(ValueError, 'ZCode.*默认'):
            self.registry.register(self.request(self.home('zcode'), family='zcode'))
        self.assertIsNone(self.registry.resolve('zcode'))

    def test_user_fields_cannot_supply_a_native_claim_or_change_the_resolver_home(self):
        home = self.home('other')
        for key in ('native', 'roots', 'stateRoot', 'gatewayRoot', 'enabled', 'id', 'verified'):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, '字段'):
                self.registry.register({**self.request(home), key: 'injected'})
        self.resolver.side_effect = None
        self.resolver.return_value = {'home': str(home), 'roots': {'userData': str(home)}}
        with self.assertRaisesRegex(ValueError, '声明'):self.registry.register(self.request(home))
        self.resolver.return_value = NativeProfile('deepseek', 'desktop-profile', home, {'runtimeHome':home})
        with self.assertRaisesRegex(ValueError, '匹配'):self.registry.register(self.request(home))
        self.resolver.return_value = resolve_profile('claude', self.default, platform='darwin')
        with self.assertRaisesRegex(ValueError, '改变'):self.registry.register(self.request(home))
        self.assertEqual(self.registry.list(), [])

    def test_default_and_registered_roots_include_aliases_and_native_sibling_directories(self):
        alias = self.root/'default-alias'
        alias.symlink_to(self.default, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '默认'):self.registry.register(self.request(alias))
        third = self.home('default-claude-3p')
        with self.assertRaisesRegex(ValueError, '默认'):self.registry.register(self.request(third))
        home = self.home('other')
        self.registry.register(self.request(home))
        alias = self.root/'other-alias';alias.symlink_to(home, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '已登记'):self.registry.register(self.request(alias))
        nested = home/'nested';nested.mkdir()
        with self.assertRaisesRegex(ValueError, '已登记'):self.registry.register(self.request(nested, family='deepseek'))
        sibling = self.home('other-3p')
        with self.assertRaisesRegex(ValueError, '已登记'):self.registry.register(self.request(sibling, family='deepseek'))

    def test_distinct_paths_with_same_file_identity_are_duplicates(self):
        from bridge.features.clients import instances
        home = self.home('one');second = self.home('mounted-alias')
        self.registry.register(self.request(home))
        identity = instances._directory_identity
        with patch.object(instances, '_directory_identity', side_effect=lambda p: identity(home if p == second else p)):
            with self.assertRaisesRegex(ValueError, '已登记'):
                self.registry.register(self.request(second, family='deepseek'))

    def test_replacing_registered_home_or_initialized_root_fails_without_fallback(self):
        for replace_home in (True, False):
            home = self.home('native-'+str(replace_home));third = Path(str(home)+'-3p');third.mkdir()
            row = self.registry.register(self.request(home))
            target = home if replace_home else third
            target.rename(target.with_name(target.name+'-old'));target.mkdir()
            with self.subTest(home=replace_home), self.assertRaisesRegex(ValueError, '身份已变化'):
                self.registry.resolve('claude', row['id'], require_enabled=False)

    def test_missing_native_roots_pin_only_on_explicit_request_and_then_reject_replacement(self):
        home = self.home('other');row = self.registry.register(self.request(home))
        code = home/'claude-code-config';code.mkdir()
        before = self.registry.path.read_bytes()
        target = self.registry.resolve('claude', row['id'], require_enabled=False)
        self.assertEqual(target.pending_roots, ('code',))
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertEqual(self.registry.pin_initialized_roots(row['id']).pending_roots, ())
        code.rename(home/'previous-code');code.mkdir()
        with self.assertRaisesRegex(ValueError, '身份已变化'):self.registry.resolve('claude', row['id'], require_enabled=False)

    def test_new_native_symlink_cannot_redirect_a_reserved_missing_root(self):
        home = self.home('other');row = self.registry.register(self.request(home))
        external = self.home('external');(home/'claude-code-config').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '身份已变化'):self.registry.resolve('claude', row['id'], require_enabled=False)
        with self.assertRaisesRegex(ValueError, '身份已变化'):self.registry.pin_initialized_roots(row['id'])

    def test_changed_native_declaration_and_changed_defaults_fail_closed(self):
        home = self.home('other');row = self.registry.register(self.request(home))
        self.defaults.append(resolve_profile('deepseek', home, platform='linux'))
        with self.assertRaisesRegex(ValueError, '默认'):self.registry.resolve('claude', row['id'], require_enabled=False)
        self.defaults.pop()
        self.resolver.side_effect = lambda p: NativeProfile('claude','desktop-profile',p,{'userData':p,'code':p/'new-code'})
        with self.assertRaisesRegex(ValueError, '声明已变化'):self.registry.resolve('claude', row['id'], require_enabled=False)

    def test_enable_state_has_one_source_and_preserves_other_runtime_configuration(self):
        row = self.registry.register(self.request(self.home('other')))
        target = self.registry.resolve('claude', row['id'], require_enabled=False)
        config = target.state_root/'desktop-sessions/settings.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'enabled': {'claude': True, 'deepseek': False}, 'claudeHome': str(target.profile.home), 'fixture': {'keep': True}}))
        self.assertTrue(self.registry.list('claude')[0]['enabled'])
        self.assertTrue(self.registry.resolve('claude', row['id']).enabled)
        registered = self.registry.path.read_bytes()
        self.assertNotIn('enabled', json.loads(registered)['entries'][0])
        self.registry.configure(row['id'], False)
        self.assertEqual(json.loads(config.read_text()), {'enabled': {'claude': False, 'deepseek': False}, 'claudeHome': str(target.profile.home), 'fixture': {'keep': True}})
        self.assertEqual(self.registry.path.read_bytes(), registered)
        config.unlink();config.symlink_to(self.registry.path)
        with self.assertRaisesRegex(ValueError, '符号链接'):self.registry.list()

    def test_bad_types_and_persisted_root_proofs_are_rejected(self):
        home = self.home('other')
        for kind in (None, {}, [], 1):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.registry.register({**self.request(home), 'kind': kind})
        self.registry.register(self.request(home))
        value = json.loads(self.registry.path.read_text())
        value['entries'][0]['roots']['home']['unexpected'] = True
        self.registry.path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, '身份记录'):self.registry.list()

    def test_state_directory_redirection_is_rejected_even_without_a_symlink_flag(self):
        row = self.registry.register(self.request(self.home('other')))
        target = self.registry.resolve('claude', row['id'], require_enabled=False)
        external = self.home('external-state')
        target.state_root.symlink_to(external, target_is_directory=True)
        # A Windows junction can redirect resolution without is_symlink().
        with patch.object(type(target.state_root), 'is_symlink', return_value=False):
            with self.assertRaisesRegex(ValueError, '符号链接'):
                self.registry.configure(row['id'], True)
        self.assertEqual(list(external.iterdir()), [])

    def test_an_unavailable_other_profile_does_not_block_current_resolution(self):
        from bridge.features.clients import instances
        one = self.registry.register(self.request(self.home('one')))
        two_home = self.home('two')
        self.registry.register(self.request(two_home, family='deepseek'))
        proof = instances._proof
        def checked(path, **options):
            if Path(path) == two_home or two_home in Path(path).parents:
                raise PermissionError('Other profile is inaccessible')
            return proof(path, **options)
        with patch.object(instances, '_proof', side_effect=checked):
            self.assertEqual(self.registry.resolve('claude', one['id'], require_enabled=False).id, one['id'])

    def test_invalid_native_directory_can_be_disabled_but_cannot_be_enabled(self):
        home = self.home('other')
        row = self.registry.register(self.request(home))
        self.registry.configure(row['id'], True)
        home.rename(self.root/'old');home.mkdir()
        self.assertFalse(self.registry.configure(row['id'], False)['enabled'])
        with self.assertRaisesRegex(ValueError, '身份已变化'):
            self.registry.configure(row['id'], True)

    def test_native_roots_cannot_overlap_the_gateway_private_store(self):
        self.gateway.mkdir()
        with self.assertRaisesRegex(ValueError, '网关资料'):self.registry.register(self.request(self.gateway))
        with self.assertRaisesRegex(ValueError, '网关资料'):self.registry.register(self.request(self.root))

    def test_registry_is_strict_and_write_failure_preserves_previous_record(self):
        row = self.registry.register(self.request(self.home('first')))
        before = self.registry.path.read_bytes()
        with patch('bridge.features.clients.instances.replace_session_file', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError):self.registry.register(self.request(self.home('second')))
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertEqual(self.registry.list(), [row])
        data = json.loads(before);data['entries'][0]['native'] = {'verified': True};self.registry.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, '身份无效'):self.registry.list()
        self.assertIsNone(self.registry.resolve('claude'), 'Default resolution does not depend on a broken independent store')

    def test_multiple_registry_objects_reload_before_atomic_mutation(self):
        other = Instances(self.gateway, resolvers=self.resolvers, defaults=lambda:self.defaults)
        first = self.registry.register(self.request(self.home('first')))
        second = other.register(self.request(self.home('second'), family='deepseek'))
        self.assertEqual({r['id'] for r in self.registry.list()}, {first['id'],second['id']})
        other.configure(first['id'], False)
        self.assertFalse(self.registry.list('claude')[0]['enabled'])

    def test_remove_only_disabled_registration_preserves_native_and_gateway_data(self):
        home = self.home('one')
        (home/'account.json').write_text('preserve native account')
        one = self.registry.register(self.request(home))
        two = self.registry.register(self.request(self.home('two'), family='deepseek'))
        state = self.registry.resolve('claude', one['id'], require_enabled=False).state_root
        state.mkdir(parents=True)
        (state/'receipts.json').write_text('preserve receipts')
        self.registry.configure(one['id'], True)
        with self.assertRaisesRegex(ValueError, '停用'):
            self.registry.remove('claude', one['id'])
        self.registry.configure(one['id'], False)
        home.rename(self.root/'moved-native')
        self.registry.remove('claude', one['id'])
        self.assertEqual(self.registry.list(), [two])
        self.assertEqual((state/'receipts.json').read_text(), 'preserve receipts')
        self.assertEqual((self.root/'moved-native/account.json').read_text(), 'preserve native account')
        for family, entry in [('claude', 'claude'), ('deepseek', one['id']), ('claude', one['id'])]:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.registry.remove(family, entry)


class DelegatedCodexInstancesTests(unittest.TestCase):
    def test_remove_disabled_management_owner_drains_work_and_forgets_only_registration(self):
        from bridge.app.service import Bridge
        from bridge.features.accounts.operation_gate import OperationGate
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root=Path(directory);home=root/'.codex-custom';home.mkdir();(home/'config.toml').write_text('model="fixture"')
            owner=SimpleNamespace(entry_gate=OperationGate(),submit_lock=threading.RLock(),create_lock=threading.RLock(),message_lock=threading.RLock(),
                submissions={},creations={},message_actions={},goal_commands={},terminals=SimpleNamespace(lock=threading.RLock(),jobs={},sessions={}),
                cli_sessions=SimpleNamespace(cli_connections=lambda:[]),side_chats=SimpleNamespace(lock=threading.RLock(),chats={}),
                accounts=SimpleNamespace(lock=threading.RLock(),enrollment=None))
            owner.assert_detachable=functools.partial(Bridge.assert_detachable,owner);owner.close=Mock()
            entries=Entries(root/'gateway',local_factory=lambda row,directory:owner)
            registry=Instances(root/'gateway',codex=entries)
            row=registry.register({'family':'codex','kind':'codex-local','home':str(home),'label':'Independent'})
            registry.configure(row['id'],False);self.assertIs(entries.get(row['id'],management=True),owner)
            owner.submissions['fixture']={'status':'unknown'}
            with self.assertRaisesRegex(ValueError,'待核对'):registry.remove('codex',row['id'])
            owner.submissions.clear();owner.terminals.jobs['fixture']=SimpleNamespace(done=threading.Event())
            with self.assertRaisesRegex(ValueError,'终端'):registry.remove('codex',row['id'])
            owner.terminals.jobs.clear()
            with patch.object(owner.entry_gate,'acquire',return_value=False) as acquire:
                with self.assertRaisesRegex(ValueError,'正在处理'):registry.remove('codex',row['id'])
                acquire.assert_called_once_with(timeout=3)
            owner.close.assert_not_called()
            registry.remove('codex',row['id']);owner.close.assert_called_once()
            self.assertEqual(registry.list(),[]);self.assertNotIn(row['id'],entries.bridges)
            self.assertTrue((home/'config.toml').is_file())
            with self.assertRaises(KeyError):entries.get(row['id'],management=True)
            self.assertEqual(Entries(root/'gateway').entries,[])

    def test_existing_codex_ids_and_storage_remain_and_no_factory_is_called(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root = Path(directory).resolve();default = root/'codex';default.mkdir();home = root/'other';home.mkdir()
            (home/'config.toml').write_text('model="fixture"')
            factory = Mock(side_effect=AssertionError('Registration cannot start a Codex runtime'))
            codex = Entries(root/'gateway', default, factory)
            self.addCleanup(codex.close)
            registry = Instances(root/'gateway', codex=codex)
            row = registry.register({'family':'codex','kind':'codex-local','label':'IDE','home':str(home)})
            self.assertEqual(row['id'], local_entry_id(home))
            target = registry.resolve('codex',row['id'])
            self.assertEqual(target.state_root,codex.directory/row['id'].replace(':','-'))
            self.assertFalse(registry.path.exists())
            self.assertEqual(registry.list(), [row])
            registry.configure(row['id'],False)
            with self.assertRaisesRegex(ValueError,'关闭'):registry.resolve('codex',row['id'])
            factory.assert_not_called()


if __name__ == '__main__':unittest.main()
