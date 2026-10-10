import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bridge.features.sessions.access import ThreadAccess


class ThreadAccessTests(unittest.TestCase):
    def bridge(self):
        return SimpleNamespace(codex_home='/fixture',catalog_reader=SimpleNamespace(executable='/runtime'))

    def test_rollback_restores_each_original_provider_model_and_effort(self):
        rpc=Mock();rpc.request.return_value={'modelProvider':'old-provider','model':'old-model'}
        with patch('bridge.features.sessions.access.ThreadAccessRPC') as factory:
            factory.return_value.__enter__.return_value=rpc
            ThreadAccess(self.bridge()).restore([{'id':'old','provider':'old-provider','model':'old-model','effort':'max'}])
        params=rpc.request.call_args_list[0].args[1]
        self.assertEqual(params['modelProvider'],'old-provider');self.assertEqual(params['model'],'old-model')
        self.assertEqual(params['config'],{'model_reasoning_effort':'max'})

    def test_owner_must_confirm_the_selected_route(self):
        rpc=Mock();rpc.request.return_value={'modelProvider':'wrong','model':'new'}
        with patch('bridge.features.sessions.access.ThreadAccessRPC') as factory:
            factory.return_value.__enter__.return_value=rpc
            with self.assertRaisesRegex(ValueError,'接入未能更新'):
                ThreadAccess(self.bridge()).restore([{'id':'old','provider':'old','model':'new'}])
