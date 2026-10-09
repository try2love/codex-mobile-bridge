import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.integrations.client_launch import _process_state


class DshUtilityHostTests(unittest.TestCase):
    def state(self, commands, provider='deepseek'):
        app = SimpleNamespace(home=Path('fixture-home'))
        with patch('bridge.integrations.client_launch.sys.platform', 'win32'):
            return _process_state({'id': provider}, app, list(commands), commands)

    def test_windows_isolated_node_host_is_runtime_candidate(self):
        result = self.state({
            11: '"F:\\DSH\\DSH Desktop\\DSH Desktop.exe"',
            12: '"F:\\DSH\\DSH Desktop\\DSH Desktop.exe" --type=utility '
                '--utility-sub-type=node.mojom.NodeService --service-sandbox-type=none',
            13: '"F:\\DSH\\DSH Desktop\\DSH Desktop.exe" --type=utility '
                '--utility-sub-type=network.mojom.NetworkService',
            14: '"F:\\DSH\\DSH Desktop\\DSH Desktop.exe" --type=renderer',
        })
        self.assertEqual(result['mainPids'], [11])
        self.assertEqual(result['runtimePids'], [12])
        self.assertFalse(result['unknown'])

    def test_multiple_node_hosts_remain_ambiguous(self):
        command = 'DSH.exe --type=utility --utility-sub-type=node.mojom.NodeService'
        result = self.state({11: 'DSH.exe', 12: command, 13: command})
        self.assertEqual(result['runtimePids'], [12, 13])
        self.assertTrue(result['unknown'])

    def test_other_clients_node_utilities_are_owner_managed_children(self):
        result = self.state({11: 'Claude.exe', 12: 'Claude.exe --type=utility '
                             '--utility-sub-type=node.mojom.NodeService'}, provider='claude')
        self.assertEqual(result['runtimePids'], [])
        self.assertFalse(result['unknown'])

    def test_utility_type_and_subtype_must_match_whole_arguments(self):
        for command in (
            'DSH.exe --type=renderer --utility-sub-type=node.mojom.NodeService',
            'DSH.exe --type=utility --utility-sub-type=node.mojom.NodeServiceOther',
            'DSH.exe --type=utilityOther --utility-sub-type=node.mojom.NodeService',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.state({11: 'DSH.exe', 12: command})['runtimePids'], [])


if __name__ == '__main__':
    unittest.main()
