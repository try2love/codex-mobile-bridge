import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plugin_source_check', ROOT/'scripts/check-dsh-plugin-source.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class SourceLockTests(unittest.TestCase):
    def test_pinned_commit_and_drift(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root = Path(directory)
            upstream = root/'source'
            upstream.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(upstream), *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            (upstream/'host.mjs').write_text('original')
            (upstream/'package.json').write_text('{"version":"0.2.0"}')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
            commit = git('rev-parse', 'HEAD')
            plugin = root/'bridge/clients/deepseek/plugin'
            plugin.mkdir(parents=True)
            (plugin/'package.json').write_text('{"version":"0.2.0"}')
            (root/'host.mjs').write_text('original')
            (plugin.parent/'plugin-source.json').write_text(json.dumps({'commit':commit,'files':{'host.mjs':'host.mjs'},'manifestFields':['version']}))
            (upstream/'host.mjs').write_text('uncommitted drift ignored')
            self.assertEqual(checker.verify(upstream, root), commit)
            (root/'host.mjs').write_text('unexpected bundled drift')
            with self.assertRaisesRegex(ValueError, 'source drift'):
                checker.verify(upstream, root)
            (root/'host.mjs').write_text('original')
            (plugin/'package.json').write_text('{"version":"0.3.0"}')
            with self.assertRaisesRegex(ValueError, 'manifest drift'):
                checker.verify(upstream, root)
            lock_path = plugin.parent/'plugin-source.json'
            lock = json.loads(lock_path.read_text())
            lock['manifestOverrides'] = {'engines': {'upstream': None, 'bundled': {'dsh': '*'}}}
            lock_path.write_text(json.dumps(lock))
            (plugin/'package.json').write_text('{"version":"0.2.0","engines":{"dsh":"*"}}')
            self.assertEqual(checker.verify(upstream, root), commit)
            (plugin/'package.json').write_text('{"version":"0.2.0","engines":{"dsh":"1.0.0"}}')
            with self.assertRaisesRegex(ValueError, 'override drift'):
                checker.verify(upstream, root)
            (plugin/'package.json').write_text('{"version":"0.2.0","engines":{"dsh":"*"}}')
            lock['manifestOverrides']['engines']['upstream'] = {'dsh': 'invented-origin'}
            lock_path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, 'override drift'):
                checker.verify(upstream, root)
