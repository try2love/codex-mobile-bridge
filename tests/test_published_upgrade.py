"""Provenance failures must stop before downloading or installing packages."""
import importlib.util
import hashlib
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
    def rejected(self, message, *, source='a'*40, head=None, version='2.2.2-preview.4', changed='', conclusion='success'):
        run = {'head_sha': head or source, 'path': '.github/workflows/desktop.yml',
               'status': 'completed', 'conclusion': conclusion}
        with patch.dict(os.environ, {'SOURCE_REF': source, 'CANDIDATE_RUN_ID': '123'}), \
                patch.object(prepare.subprocess, 'run') as ancestry, \
                patch.object(prepare.subprocess, 'check_output', side_effect=[json.dumps({'version': version}).encode(), changed, changed]), \
                patch.object(prepare, 'api', return_value=run), \
                patch.object(prepare.urllib.request, 'urlretrieve') as download:
            with self.assertRaisesRegex(SystemExit, message):
                prepare.main()
            download.assert_not_called()
            if len(source) == 40:
                ancestry.assert_called_once_with(['git', 'merge-base', '--is-ancestor', prepare.BASE, source], check=True)

    def test_branch_name_is_not_an_immutable_source(self):
        self.rejected('exact 40-character', source='release/v2.2.2-preview')

    def test_source_outside_reviewed_preview3_history_stops_before_version_or_api_reads(self):
        with patch.dict(os.environ, {'SOURCE_REF': 'a'*40}), \
                patch.object(prepare.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'git')), \
                patch.object(prepare.subprocess, 'check_output') as source, \
                patch.object(prepare, 'api') as api, \
                patch.object(prepare.urllib.request, 'urlretrieve') as download:
            with self.assertRaises(subprocess.CalledProcessError):
                prepare.main()
            source.assert_not_called()
            api.assert_not_called()
            download.assert_not_called()

    def test_build_head_with_product_changes_is_rejected(self):
        self.rejected('differs from product', head='b'*40, changed='bridge/app/service.py\n')

    def test_failed_build_cannot_supply_acceptance_artifacts(self):
        self.rejected('successful Desktop builds', conclusion='failure')

    def test_wrong_product_version_is_rejected(self):
        self.rejected('Unexpected frozen product version', version='2.2.1')
        self.rejected('Unexpected frozen product version', version='2.2.2-preview.1')
        self.rejected('Unexpected frozen product version', version='2.2.2-preview.2')
        self.rejected('Unexpected frozen product version', version='2.2.2-preview.3')

    def test_unbuilt_product_changes_are_rejected(self):
        self.rejected('differs from product', changed='bridge/app/service.py\n')
        self.rejected('differs from product', changed='tests/browser/unreviewed-fixture.spec.cjs\n')

    def test_signing_source_guard_checks_build_and_checkout_without_downloading_baselines(self):
        source = 'a'*40
        run = {'head_sha': source, 'path': '.github/workflows/desktop.yml',
               'status': 'completed', 'conclusion': 'success'}
        with patch.dict(os.environ, {'SOURCE_REF': source, 'CANDIDATE_RUN_ID': '123', 'SOURCE_ONLY': 'true'}), \
                patch.object(prepare.subprocess, 'run') as ancestry, \
                patch.object(prepare.subprocess, 'check_output', side_effect=[json.dumps({'version': prepare.VERSION}).encode(), '.github/workflows/desktop.yml\n']), \
                patch.object(prepare, 'api', return_value=run) as api, \
                patch.object(prepare.urllib.request, 'urlretrieve') as download:
            prepare.main()
            ancestry.assert_called_once()
            api.assert_called_once_with('/actions/runs/123')
            download.assert_not_called()
        with patch.dict(os.environ, {'SOURCE_ONLY': 'true'}):
            self.rejected('differs from product', changed='desktop/main.cjs\n')

    def test_preview4_guard_uses_actual_product_version_and_full_history_acceptance_checkout(self):
        product = json.loads((ROOT/'package.json').read_text(encoding='utf-8'))
        self.assertEqual(product['version'], prepare.VERSION)
        self.assertEqual(prepare.VERSION, '2.2.2-preview.4')
        self.assertEqual(prepare.BASELINES, (('2.2.1', 'old', False), ('2.2.2-preview.1', 'preview1', True),
                                           ('2.2.2-preview.2', 'preview2', True), ('2.2.2-preview.3', 'preview3', True),
                                           ('2.2.2-preview.4', 'preview4', True)))
        self.assertEqual(prepare.BASE, 'd79a89c0b58f212323dac2a28b8d99baa32bd281')
        workflow = (ROOT/'.github/workflows/upgrade-acceptance.yml').read_text(encoding='utf-8')
        self.assertIn('fetch-depth: 0', workflow)

    def test_intel_supplement_executes_sha_checkout_and_version_guards(self):
        workflow = (ROOT/'.github/workflows/mobile.yml').read_text(encoding='utf-8')
        blocks = workflow.split("python3 - <<'PY'\n")[1:]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            code = compile(textwrap.dedent(block.split('\n          PY', 1)[0]), 'intel-source-guard', 'exec')
            source = 'a'*40
            def run(requested=source, checkout=source, version='2.2.2-preview.4'):
                with patch.dict(os.environ, {'SOURCE_REF': requested}), \
                        patch('subprocess.check_output', return_value=checkout+'\n'), \
                        patch('builtins.open', return_value=io.StringIO(json.dumps({'version': version}))):
                    exec(code, {})
            run()
            for invalid in ('release/v2.2.2-preview.4', 'a'*39, 'a'*41, 'A'*40):
                with self.assertRaisesRegex(SystemExit, '40-character'):
                    run(requested=invalid)
            with self.assertRaisesRegex(SystemExit, 'checkout'):
                run(checkout='b'*40)
            with self.assertRaisesRegex(SystemExit, 'version'):
                run(version='2.2.2-preview.1')
            with self.assertRaisesRegex(SystemExit, 'version'):
                run(version='2.2.2-preview.3')

    @unittest.skipUnless(hasattr(hashlib, 'file_digest'), 'Published package downloads use Python 3.13')
    def test_official_preview4_includes_original_zip_and_digest_without_dropping_older_baselines(self):
        source, repository, suffix = 'a'*40, 'try2love/codex-mobile-bridge', 'Windows-x64-Setup.exe'
        run = {'head_sha': source, 'path': '.github/workflows/desktop.yml', 'status': 'completed',
               'conclusion': 'success', 'html_url': 'https://github.com/' + repository + '/actions/runs/123'}
        releases, content = {}, {}
        for version, _, prerelease in prepare.BASELINES:
            names = ['bridge-update.json', 'Codex-Mobile-Bridge-' + version + '-' + suffix]
            if version in ('2.2.1', prepare.VERSION):
                names.append('Codex-Mobile-Bridge-' + version + '-Windows-x64.zip')
            prefix = 'https://github.com/' + repository + '/releases/download/v' + version + '/'
            for name in names:
                content[prefix + name] = ('Synthetic official bytes ' + version + ' ' + name).encode()
            content[prefix + 'SHA256SUMS.txt'] = ''.join(
                hashlib.sha256(content[prefix + name]).hexdigest() + '  ' + name + '\n' for name in names).encode()
            releases['/releases/tags/v' + version] = {'draft': False, 'prerelease': prerelease,
                'html_url': 'https://github.com/' + repository + '/releases/tag/v' + version,
                'assets': [{'name': name, 'browser_download_url': prefix + name} for name in ['SHA256SUMS.txt', *names]]}
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            work = Path(folder)/'published-upgrade'
            def download(url, destination):
                destination.write_bytes(content[url])
            with patch.dict(os.environ, {'SOURCE_REF': source, 'CANDIDATE_RUN_ID': '123',
                    'GITHUB_REPOSITORY': repository, 'OLD_ASSET_SUFFIX': suffix}), \
                    patch.object(prepare.subprocess, 'run'), \
                    patch.object(prepare.subprocess, 'check_output', side_effect=[json.dumps({'version': prepare.VERSION}).encode(), '']), \
                    patch.object(prepare, 'api', side_effect=lambda route: run if route == '/actions/runs/123' else releases[route]), \
                    patch.object(prepare, 'Path', return_value=work), \
                    patch.object(prepare.urllib.request, 'urlretrieve', side_effect=download) as downloads:
                prepare.main()
            provenance = json.loads((work/'provenance.json').read_text())
            self.assertEqual([row['version'] for row in provenance['baselines']], [row[0] for row in prepare.BASELINES])
            self.assertEqual(provenance['source'], source)
            self.assertEqual(len(downloads.call_args_list), 17)
            self.assertEqual({call.args[0] for call in downloads.call_args_list}, set(content))
            for version, directory, _ in prepare.BASELINES:
                package = 'Codex-Mobile-Bridge-' + version + '-' + suffix
                self.assertEqual((work/directory/package).read_bytes(), content[
                    'https://github.com/' + repository + '/releases/download/v' + version + '/' + package])
                self.assertTrue((work/directory/'release.json').exists())
            name = 'Codex-Mobile-Bridge-' + prepare.VERSION + '-Windows-x64.zip'
            original = (work/'preview4'/name).read_bytes()
            self.assertEqual(provenance['baselines'][-1]['sha256'][name], hashlib.sha256(original).hexdigest())

    def test_acceptance_platform_selector_preserves_all_or_selects_windows_only(self):
        workflow = (ROOT/'.github/workflows/upgrade-acceptance.yml').read_text(encoding='utf-8')
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
                    rows = json.loads(output.read_text(encoding='utf-8').removeprefix('matrix='))['include']
                    self.assertEqual(len(rows), count)
                    self.assertEqual({row['artifact'] for row in rows}, {'desktop-Windows-x64'} if count == 1 else
                        {'desktop-macOS-arm64', 'desktop-macOS-x64', 'desktop-Windows-x64', 'desktop-Linux-x64', 'desktop-Linux-arm64'})
