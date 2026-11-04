import tempfile
import unittest
from pathlib import Path

from bridge.clients.profiles import NativeProfile, inherited_account_environment, launch_parameters, resolve_profile

ROOT = Path(__file__).resolve().parents[1]


class NativeProfilesTests(unittest.TestCase):
    def test_dsh_uses_separate_data_and_singleton_roots_without_creating_them(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            home = Path(directory)/'独立 DSH'
            first = resolve_profile('deepseek', home, platform='darwin')
            second = resolve_profile('deepseek', home.with_name('另一个 DSH'), platform='darwin')
            for profile in (first, second):
                options = launch_parameters(profile, platform='darwin')
                self.assertEqual(options['environment'], {'DSH_HOME': str(profile.home)})
                self.assertEqual(options['arguments'], ('--user-data-dir='+str(profile.home/'desktop-user-data'),))
                self.assertEqual(profile.roots['runtimeHome'], profile.home)
                self.assertFalse(profile.home.exists())
            self.assertNotEqual(first.roots['userData'], second.roots['userData'])

    def test_claude_reserves_its_actual_custom_third_party_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            home = Path(directory).resolve()/'Claude second'
            profile = resolve_profile('claude', home, platform='darwin')
            self.assertEqual(profile.roots['thirdParty'], home.with_name('Claude second-3p'))
            self.assertEqual(profile.roots['code'], home/'claude-code-config')
            options = launch_parameters(profile, platform='darwin')
            self.assertEqual(options['environment'], {'CLAUDE_CONFIG_DIR': str(home/'claude-code-config')})
            self.assertNotIn('CLAUDE_USER_DATA_DIR', options['environment'])
            self.assertNotIn('HOME', options['environment'])
            self.assertNotIn('USERPROFILE', options['environment'])
            self.assertFalse(home.exists())

    def test_claude_native_3p_suffix_does_not_create_another_3p_suffix(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            home = Path(directory)/'custom-3p'
            profile = resolve_profile('claude', home, platform='darwin')
            self.assertEqual(profile.roots['thirdParty'], profile.roots['userData'])

    def test_windows_claude_scopes_both_third_party_and_legacy_migration_roots(self):
        import os
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            before = dict(os.environ)
            home = Path(directory).resolve()/'Claude second'
            profile = resolve_profile('claude', home, platform='win32')
            options = launch_parameters(profile, platform='win32')
            self.assertEqual(options['environment'], {'CLAUDE_CONFIG_DIR': str(home/'claude-code-config'),
                             'APPDATA': str(home/'windows-roaming'), 'LOCALAPPDATA': str(home/'windows-local')})
            self.assertEqual(profile.roots['thirdParty'], home/'windows-local/Claude-3p')
            self.assertTrue((profile.roots['roamingAppData']/'Claude-3p').is_relative_to(home))
            self.assertEqual(options['arguments'], ('--user-data-dir='+str(home),))
            self.assertNotIn('HOME', options['environment'])
            self.assertNotIn('USERPROFILE', options['environment'])
            self.assertNotIn('CLAUDE_USER_DATA_DIR', options['environment'])
            self.assertEqual(dict(os.environ), before)
            self.assertFalse(home.exists())

    def test_dsh_native_environment_is_the_same_contract_on_all_three_systems(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            for platform in ('darwin', 'win32', 'linux'):
                with self.subTest(platform=platform):
                    profile = resolve_profile('deepseek', Path(directory)/'dsh', platform=platform)
                    self.assertEqual(launch_parameters(profile, platform=platform)['environment'],
                                     {'DSH_HOME': str(profile.home)})

    def test_invalid_or_unsupported_profile_never_becomes_default_launch(self):
        for family, home in [('zcode', '/temporary/zcode'), ('codex', '/temporary/codex'),
                             ('deepseek', ''), ('deepseek', 'relative'), ('deepseek', '/bad\x00path')]:
            with self.subTest(family=family, home=home), self.assertRaises(ValueError):
                resolve_profile(family, home, platform='darwin')

    def test_launch_rejects_retargeted_root_mapping(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            profile = resolve_profile('deepseek', Path(directory)/'profile', platform='darwin')
            changed = NativeProfile(profile.family, profile.kind, profile.home,
                                    {**profile.roots, 'userData': Path(directory)/'other'})
            with self.assertRaisesRegex(ValueError, '不一致'):
                launch_parameters(changed, platform='darwin')

    def test_new_profiles_remove_native_account_inputs_without_removing_network_environment(self):
        for family, required in (
                ('deepseek', {'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'OPENAI_API_KEY',
                              'ANTHROPIC_AUTH_TOKEN', 'GOOGLE_APPLICATION_CREDENTIALS'}),
                ('claude', {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
                            'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_HOST_CREDS_FILE',
                            'CLAUDE_CODE_USE_BEDROCK'})):
            with self.subTest(family=family):
                names = set(inherited_account_environment(family))
                self.assertTrue(required.issubset(names))
                self.assertTrue(names.isdisjoint({'HOME', 'USERPROFILE', 'PATH', 'HTTP_PROXY',
                    'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'NODE_EXTRA_CA_CERTS'}))
                with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
                    profile = resolve_profile(family, Path(directory)/'profile', platform='darwin')
                    self.assertEqual(set(launch_parameters(profile, platform='darwin')['environmentUnset']), names)


if __name__ == '__main__':
    unittest.main()
