"""Cloud packaging contracts; no native helpers, builds or credentials are used."""
import ast
import importlib
import runpy
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'scripts/build-desktop.py'


class CloudRuntimeDependenciesTests(unittest.TestCase):
    def test_build_collects_encryption_package_by_import_name(self):
        tree = ast.parse(BUILD.read_text(encoding='utf-8'))
        declaration = next(node for node in tree.body if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == 'runtime_packages'
                                   for target in node.targets))
        packages = ast.literal_eval(declaration.value)
        self.assertEqual(packages['noise'], 'noise.connection')
        self.assertEqual(packages['aiohttp'], 'aiohttp')
        self.assertEqual(packages['keyring'], 'keyring')
        self.assertNotIn('noiseprotocol', packages)
        collect = next(node for node in tree.body if isinstance(node, ast.For)
                       and isinstance(node.iter, ast.Name) and node.iter.id == 'runtime_packages')
        self.assertEqual(ast.unparse(collect.body[0]), "command.extend(['--collect-all', package])")

    def test_missing_runtime_dependencies_fail_before_native_helpers(self):
        certifi = types.ModuleType('certifi')
        for missing in ('paramiko', 'keyring', 'aiohttp', 'noise.connection'):
            with self.subTest(module=missing):
                def load(module):
                    if module == missing:
                        raise ModuleNotFoundError('missing test dependency')
                    return types.ModuleType(module)

                with patch.dict(sys.modules, {'certifi': certifi}), \
                        patch.object(subprocess, 'run') as run, \
                        patch.object(importlib, 'import_module', side_effect=load):
                    with self.assertRaises(SystemExit) as result:
                        runpy.run_path(str(BUILD), run_name='__main__')
                    self.assertIn(missing, str(result.exception))
                    self.assertIn('requirements-desktop.txt', str(result.exception))
                    run.assert_not_called()

    def test_desktop_requirements_include_the_pinned_cloud_runtime(self):
        desktop = (ROOT / 'requirements-desktop.txt').read_text(encoding='utf-8')
        relay = (ROOT / 'requirements-relay.txt').read_text(encoding='utf-8')
        self.assertIn('-r requirements-relay.txt', desktop)
        self.assertIn('keyring==25.6.0', desktop)
        self.assertIn('aiohttp==3.13.3', relay)
        self.assertIn('noiseprotocol==0.3.1', relay)
        workflow = (ROOT / '.github/workflows/desktop.yml').read_text(encoding='utf-8')
        self.assertIn("'requirements-*.txt'", workflow)


if __name__ == '__main__':
    unittest.main()
