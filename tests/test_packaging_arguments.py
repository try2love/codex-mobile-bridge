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
