import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.codex.catalog import Catalog
from bridge.clients.codex.configuration import magpie_managed
import test_accounts as fixtures

ROOT = Path(__file__).resolve().parents[1]


class MagpieTests(unittest.TestCase):
    def test_native_provider_catalog_and_openai_override_modes(self):
        for config in (
            {'model_provider': 'magpie'},
            {'model_providers': {'magpie': {'base_url': 'https://fixture.invalid/v1'}}},
            {'model_catalog_json': '/fixture/magpie-models.json'},
            {'model_catalog_json': 'C:\\fixture\\magpie-models.json'},
            {'openai_base_url': 'http://127.0.0.1:3425/v1'},
            {'profile': 'work', 'profiles': {'work': {'model_provider': 'magpie'}}},
        ):
            with self.subTest(config=config):
                self.assertTrue(magpie_managed(config=config))
        self.assertFalse(magpie_managed(config={'model_provider': 'bridge_api', 'openai_base_url': 'https://fixture.invalid/v1'}))

    def test_model_catalog_is_read_by_native_runtime_without_reimport_or_credentials(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            reader = Catalog(folder, '/fixture/codex')
            calls = []
            def request(method, params):
                calls.append(method)
                if method == 'config/read':
                    return {'config': {'model_provider': 'magpie', 'model_providers': {'magpie': {}},
                                       'model_catalog_json': '/fixture/magpie-models.json'}}
                self.assertEqual(method, 'model/list')
                return {'data': [{'model': 'group/fixture', 'displayName': 'Fixture routing group'}]}
            with patch('bridge.clients.codex.catalog.model_catalog',
                       side_effect=AssertionError('must use native catalog')):
                result = reader.read_models(request, '/fixture')
            self.assertEqual(calls, ['config/read', 'model/list'])
            self.assertEqual(result['modelSource'], 'magpie')
            self.assertEqual(result['models'][0]['model'], 'group/fixture')
            self.assertNotIn('model_providers', result)

    def fixture(self):
        value = fixtures.AccountsTests('runTest')
        value.setUp()
        self.addCleanup(value.tearDown)
        value.manager.info.current = Mock(return_value={'status': 'ready', 'kind': 'api', 'name': 'fixture'})
        return value

    def test_external_switch_prepare_and_recovery_never_write_original_files(self):
        fixture = self.fixture()
        (fixture.home/'config.toml').write_text('model_provider="magpie"\n[model_providers.magpie]\nname="Magpie"\n', encoding='utf-8')
        before = fixture.manager.snapshot_files()
        with patch('bridge.features.accounts.accounts.DesktopApp') as desktop:
            for action in (lambda: fixture.manager.switch({}), lambda: fixture.manager.prepare({}, before),
                           lambda: fixture.manager.recover({'confirmed': True})):
                with self.assertRaisesRegex(ValueError, 'Magpie'):
                    action()
            desktop.assert_not_called()
        self.assertEqual(fixture.manager.snapshot_files(), before)
        public = fixture.manager.public()
        fixture.manager.info.current.assert_not_called()
        self.assertFalse(public['canSwitch'])
        self.assertEqual(public['current']['kind'], 'external')

    def test_manual_external_mode_persists_without_modifying_codex_config(self):
        fixture = self.fixture()
        before = fixture.manager.snapshot_files()
        fixture.manager.control({'action': 'management', 'mode': 'external'})
        self.assertTrue(fixture.manager.management()['external'])
        fixture.manager.control({'action': 'management', 'mode': 'auto'})
        self.assertFalse(fixture.manager.management()['external'])
        self.assertEqual(fixture.manager.snapshot_files(), before)

    def test_external_edit_during_failed_switch_is_preserved_instead_of_rolled_back(self):
        fixture = self.fixture()
        row = fixture.api()
        fixture.manager.prepare = fixture.prepare
        external = 'model_provider="magpie"\n[model_providers.magpie]\nname="externally updated"\n'
        def changed(*args):
            (fixture.home/'config.toml').write_text(external, encoding='utf-8')
            raise ValueError('verification failed after external edit')
        fixture.manager.wait_ready = Mock(side_effect=changed)
        fixture.manager.restore_files = Mock(side_effect=AssertionError('must preserve external edits'))
        fixture.switch(row)
        fixtures.wait_for_account_worker(fixture)
        self.assertEqual(fixture.manager.state['phase'], 'interrupted')
        self.assertEqual((fixture.home/'config.toml').read_text(encoding='utf-8'), external)
        fixture.manager.restore_files.assert_not_called()


if __name__ == '__main__':
    unittest.main()
