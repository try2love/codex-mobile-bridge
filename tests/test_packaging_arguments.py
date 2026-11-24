"""Exercise the real builder argument handoff without compiling applications."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackagingArgumentsTests(unittest.TestCase):
    def test_generated_metadata_declares_the_runtime_data_format_for_version_switching(self):
        from bridge.features.updates import gateway
        tree = ast.parse((ROOT/'scripts/build-desktop.py').read_text())
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                   and node.module == 'bridge.features.updates.gateway']
        writer = tree.body[-1]
        program = compile(ast.Module(body=[*imports, writer], type_ignores=[]),
                          str(ROOT/'scripts/build-desktop.py'), 'exec')
        (ROOT/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            root = Path(tmp)
            (root/'dist').mkdir()
            (root/'package.json').write_text(json.dumps({'version': '2.2.2-preview.3'}))
            exec(program, {'root': root, 'json': json, 'sys': sys, 'platform': __import__('platform')})
            metadata = json.loads((root/'dist/update-version.json').read_text())
            self.assertEqual(metadata['dataFormat'], gateway.DATA_FORMAT)
            target = root/'Codex Mobile Bridge.app'
            resources = gateway.locations(target)[2]
            resources.mkdir(parents=True)
            (resources/'update-version.json').write_text(json.dumps(metadata))
            transition = {'intent': 'switch-version', 'from': metadata['version'],
                          'to': '2.2.1', 'dataFormat': gateway.DATA_FORMAT}
            payload = {'version': '2.2.1', 'transition': transition}
            self.assertEqual(gateway.validate_transition(target, payload), transition)
            del metadata['dataFormat']
            (resources/'update-version.json').write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, '当前资料格式尚未核验'):
                gateway.validate_transition(target, payload)

    def test_large_unicode_resource_list_crosses_stdin_unchanged(self):
        tree = ast.parse((ROOT/'scripts/build-desktop.py').read_text())
        call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == 'run'
                    and any(key.arg == 'input' for key in node.keywords))
        program = call.args[0].elts[2].value
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            folder = Path(tmp);package = folder/'PyInstaller';package.mkdir()
            (package/'__init__.py').write_text('')
            (package/'__main__.py').write_text('import json\nfrom pathlib import Path\ndef run(args):\n Path("result.json").write_text(json.dumps(args),encoding="utf-8")\n')
            args = ['--add-data', 'F:/中文 路径/资源/'+('x'*200)+':bridge/resources']*500 + ['desktop.py']
            self.assertGreater(len(subprocess.list2cmdline(args)), 32767)
            child = [sys.executable, '-c', program]
            self.assertLess(len(subprocess.list2cmdline(child)), 32767)
            subprocess.run(child, input=json.dumps(args), text=True, cwd=folder, check=True, timeout=10)
            self.assertEqual(json.loads((folder/'result.json').read_text()), args)


if __name__ == '__main__':
    unittest.main()
