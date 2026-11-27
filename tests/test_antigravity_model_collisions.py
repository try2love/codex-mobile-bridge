import unittest

from bridge.clients.antigravity.cli_session import AntigravityCli


class ModelCollisions(unittest.TestCase):
    def test_same_label_and_effort_preserve_each_native_model(self):
        cli = AntigravityCli()
        status = {'cascadeModelConfigData': {'clientModelConfigs': [
            {'modelId': 'opaque-a', 'label': 'Example (high)', 'modelOrAlias': {'model': 'MODEL_A'}},
            {'modelId': 'opaque-b', 'label': 'Example (high)', 'modelOrAlias': {'model': 'MODEL_B'}},
            {'modelId': 'opaque-c', 'label': 'Example (low)', 'modelOrAlias': {'model': 'MODEL_C'}},
        ]}}
        for rows in (status['cascadeModelConfigData']['clientModelConfigs'],
                     list(reversed(status['cascadeModelConfigData']['clientModelConfigs']))):
            with self.subTest(order=[row['modelId'] for row in rows]):
                catalog = cli._build_catalog_from_status({'cascadeModelConfigData': {'clientModelConfigs': rows}})
                self.assertEqual({row['id'] for row in catalog['models']}, {'opaque-a', 'opaque-b', 'opaque-c'})
                for original in rows:
                    native, canonical, effort, _ = cli._resolve_from_catalog(original['modelId'], None, catalog)
                    self.assertEqual(native, original['modelOrAlias']['model'])
                    self.assertEqual(canonical, original['modelId'])
                    self.assertEqual(effort, '')

    def test_unambiguous_efforts_still_group(self):
        cli = AntigravityCli()
        rows = [{'modelId': f'opaque-{effort}', 'label': f'Example ({effort})',
                 'modelOrAlias': {'model': 'MODEL_' + effort.upper()}} for effort in ('low', 'high')]
        catalog = cli._build_catalog_from_status({'cascadeModelConfigData': {'clientModelConfigs': rows}})
        self.assertEqual(len(catalog['models']), 1)
        self.assertEqual(catalog['models'][0]['efforts'], ['low', 'high'])
