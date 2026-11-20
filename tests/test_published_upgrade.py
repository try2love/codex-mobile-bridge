"""Provenance failures must stop before downloading or installing packages."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('upgrade_prepare', ROOT/'scripts/prepare-upgrade-acceptance.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PublishedUpgradeProvenanceTests(unittest.TestCase):
    def rejected(self, message, *, source='a'*40, head=None, version='2.2.2-preview.2', changed='', conclusion='success'):
        run = {'head_sha': head or source, 'path': '.github/workflows/desktop.yml',
               'status': 'completed', 'conclusion': conclusion}
        with patch.dict(os.environ, {'SOURCE_REF': source, 'CANDIDATE_RUN_ID': '123'}), \
                patch.object(prepare.subprocess, 'run') as ancestry, \
                patch.object(prepare.subprocess, 'check_output', side_effect=[json.dumps({'version': version}).encode(), changed]), \
                patch.object(prepare, 'api', return_value=run), \
                patch.object(prepare.urllib.request, 'urlretrieve') as download:
            with self.assertRaisesRegex(SystemExit, message):
                prepare.main()
            download.assert_not_called()
            if len(source) == 40:
                ancestry.assert_called_once_with(['git', 'merge-base', '--is-ancestor', prepare.BASE, source], check=True)

    def test_branch_name_is_not_an_immutable_source(self):
        self.rejected('exact 40-character', source='release/v2.2.2-preview')

    def test_unrelated_run_head_is_rejected(self):
        self.rejected('successful Desktop builds', head='b'*40)

    def test_failed_build_cannot_supply_acceptance_artifacts(self):
        self.rejected('successful Desktop builds', conclusion='failure')

    def test_wrong_product_version_is_rejected(self):
        self.rejected('Unexpected frozen product version', version='2.2.1')
        self.rejected('Unexpected frozen product version', version='2.2.2-preview.1')

    def test_unbuilt_product_changes_are_rejected(self):
        self.rejected('differs from product', changed='bridge/app/service.py\n')
        self.rejected('differs from product', changed='tests/browser/unreviewed-fixture.spec.cjs\n')

    def test_preview2_guard_uses_the_actual_product_version_and_reviewed_ancestor(self):
        source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        subprocess.run(['git', 'merge-base', '--is-ancestor', prepare.BASE, source], cwd=ROOT, check=True)
        product = json.loads(subprocess.check_output(['git', 'show', source + ':package.json'], cwd=ROOT))
        self.assertEqual(product['version'], prepare.VERSION)
        self.assertEqual(prepare.VERSION, '2.2.2-preview.2')

    def test_intel_supplement_executes_sha_checkout_and_version_guards(self):
        workflow = (ROOT/'.github/workflows/mobile.yml').read_text()
        blocks = workflow.split("python3 - <<'PY'\n")[1:]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            code = compile(textwrap.dedent(block.split('\n          PY', 1)[0]), 'intel-source-guard', 'exec')
            source = 'a'*40
            def run(requested=source, checkout=source, version='2.2.2-preview.2'):
                with patch.dict(os.environ, {'SOURCE_REF': requested}), \
                        patch('subprocess.check_output', return_value=checkout+'\n'), \
                        patch('builtins.open', return_value=io.StringIO(json.dumps({'version': version}))):
                    exec(code, {})
            run()
            for invalid in ('release/v2.2.2-preview.2', 'a'*39, 'a'*41, 'A'*40):
                with self.assertRaisesRegex(SystemExit, '40-character'):
                    run(requested=invalid)
            with self.assertRaisesRegex(SystemExit, 'checkout'):
                run(checkout='b'*40)
            with self.assertRaisesRegex(SystemExit, 'version'):
                run(version='2.2.2-preview.1')

    def test_acceptance_platform_selector_preserves_all_or_selects_windows_only(self):
        workflow = (ROOT/'.github/workflows/upgrade-acceptance.yml').read_text()
        code = textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            output = Path(folder)/'matrix'
            for target, count in [('all', 5), ('Windows', 1), ('unrecognized', 0)]:
                with self.subTest(target=target):
                    output.unlink(missing_ok=True)
                    result = subprocess.run([sys.executable, '-B', '-'], input=code, text=True, capture_output=True,
                        env={**os.environ, 'TARGET_OS': target, 'GITHUB_OUTPUT': str(output)})
                    if not count:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(output.exists())
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    rows = json.loads(output.read_text().removeprefix('matrix='))['include']
                    self.assertEqual(len(rows), count)
                    self.assertEqual({row['artifact'] for row in rows}, {'desktop-Windows-x64'} if count == 1 else
                        {'desktop-macOS-arm64', 'desktop-macOS-x64', 'desktop-Windows-x64', 'desktop-Linux-x64', 'desktop-Linux-arm64'})
