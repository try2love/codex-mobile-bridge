"""The real-App acceptance must never run on a daily or self-hosted Windows PC."""
import importlib.util
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('official_acceptance', ROOT/'scripts/accept-windows-codex.py')
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class OfficialWindowsAcceptanceGuardTests(unittest.TestCase):
    def test_runtime_references_must_match_packaged_launcher_and_generated_pipe(self):
        launcher = ROOT/'fixture-runtime.exe'
        before = {'model_provider': 'isolated_acceptance', 'mcp_servers': {'node_repl': {'env': {
            'CODEX_CLI_PATH': str(ROOT/'original-runtime.exe'),
            'SKY_CUA_NATIVE_PIPE_DIRECTORY': r'\\.\pipe\codex-computer-use-00000000-0000-4000-8000-000000000001'}}}}
        after = copy.deepcopy(before)
        after['mcp_servers']['node_repl']['env'].update(CODEX_CLI_PATH=str(launcher),
            SKY_CUA_NATIVE_PIPE_DIRECTORY=r'\\.\pipe\codex-computer-use-00000000-0000-4000-8000-000000000002')
        self.assertEqual(len(acceptance.preserved_user_configuration(before, after, launcher)), 2)
        for field, value in [('CODEX_CLI_PATH', str(ROOT/'unrelated.exe')),
                             ('SKY_CUA_NATIVE_PIPE_DIRECTORY', r'\\remote\pipe\unrelated'),
                             ('SKY_CUA_NATIVE_PIPE_DIRECTORY', r'\\.\pipe\codex-computer-use-invalid')]:
            invalid = copy.deepcopy(after)
            invalid['mcp_servers']['node_repl']['env'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                acceptance.preserved_user_configuration(before, invalid, launcher)
        for value in ['other', None]:
            invalid = {**after, 'model_provider': value}
            with self.assertRaises(RuntimeError):
                acceptance.preserved_user_configuration(before, invalid, launcher)

    def test_native_bootstrap_only_adds_known_defaults_without_overwriting_configuration(self):
        before = {'model_provider': 'isolated_acceptance',
                  'plugins': {'pdf@openai-primary-runtime': {'enabled': True}}}
        after = {**before, 'marketplaces': {'openai-primary-runtime': {'source_type': 'local', 'source': 'fixture'}}}
        self.assertEqual(acceptance.native_bootstrap_additions(before, after), [
            'marketplaces.openai-primary-runtime.source', 'marketplaces.openai-primary-runtime.source_type'])
        invalid = [
            {**after, 'model_provider': 'other'},
            {**after, 'plugins': {'pdf@openai-primary-runtime': {'enabled': False}}},
            {**after, 'openai_base_url': 'https://example.invalid'},
            {**after, 'plugins': {'pdf@openai-primary-runtime': {'enabled': True},
                                 'documents@openai-primary-runtime': {'enabled': False}}},
        ]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(RuntimeError):
                acceptance.native_bootstrap_additions(before, config)

    def test_daily_non_windows_and_self_hosted_do_not_reach_download_or_files(self):
        for platform, actions, runner in [('win32', '', ''), ('win32', 'true', 'self-hosted'),
                                          ('darwin', 'true', 'github-hosted')]:
            with self.subTest(platform=platform, runner=runner), \
                    patch.object(acceptance.sys, 'platform', platform), \
                    patch.dict(os.environ, {'GITHUB_ACTIONS': actions, 'RUNNER_ENVIRONMENT': runner}), \
                    patch.object(acceptance.Path, 'mkdir') as mkdir, \
                    patch.object(acceptance.urllib.request, 'urlopen') as download:
                with self.assertRaisesRegex(RuntimeError, 'disposable GitHub-hosted'):
                    acceptance.main()
                mkdir.assert_not_called()
                download.assert_not_called()

    def test_only_disposable_windows_passes_entry_guard(self):
        with patch.object(acceptance.sys, 'platform', 'win32'), \
                patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}):
            acceptance.require_hosted_windows()
