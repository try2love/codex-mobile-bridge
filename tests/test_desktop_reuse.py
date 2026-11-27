"""Reuse rejects unverified source, jobs and package contents."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reuse', ROOT/'scripts/reuse-desktop-artifacts.py')
reuse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse)


class DesktopReuseTests(unittest.TestCase):
    def setUp(self):
        self.source = 'a'*40
        self.name = 'desktop-Linux-x64'
        self.run = {'id': 123, 'head_sha': self.source, 'path': '.github/workflows/desktop.yml', 'status': 'completed', 'conclusion': 'cancelled'}
        self.job = {'id': 8, 'name': reuse.PLATFORMS[self.name][0], 'status': 'completed', 'conclusion': 'success', 'head_sha': self.source}
        self.artifact = {'id': 9, 'name': self.name, 'expired': False, 'size_in_bytes': 100, 'digest': 'sha256:'+'b'*64,
                         'workflow_run': {'id': 123, 'head_sha': self.source}}

    def validate(self, changed=()):
        return reuse.validate(self.run, self.source, [self.job], [self.artifact], self.name, changed)

    def test_successful_platform_can_be_reused_from_cancelled_overall_run(self):
        self.validate(['tests/test_bridge.py', 'tests/test_desktop_idle.py', 'tests/test_hosted_desktop.py'])

    def test_windows_path_and_utf8_fixture_repairs_preserve_product_packages(self):
        self.validate(['tests/test_antigravity_reading.py', 'tests/test_retained_index_history.py',
                       'tests/test_history_control_separation.py', 'tests/test_progressive_loading.py'])

    def test_workflow_scopes_rebuild_and_keeps_default_all(self):
        workflow = (ROOT/'.github/workflows/desktop.yml').read_text()
        self.assertIn('options: [all, intel, mac-windows-reuse, windows-reuse, mac-arm-reuse]', workflow)
        self.assertIn('default: all', workflow)
        self.assertIn('jobs?filter=all', (ROOT/'scripts/reuse-desktop-artifacts.py').read_text())
        self.assertIn('Verify exact selected rebuild source', workflow)
        self.assertEqual(set(reuse.PLATFORMS), {'desktop-macOS-arm64', 'desktop-macOS-x64', 'desktop-Linux-x64', 'desktop-Linux-arm64', 'desktop-Windows-x64'})
        self.assertIn('inputs.reuse_mac_arm_source_ref', workflow)
        self.assertIn('name: reuse-provenance-${{ matrix.artifact }}', workflow)

    def test_mac_override_is_atomic_and_applies_only_to_original_mac_job(self):
        source = 'b'*40
        self.assertEqual(reuse.reuse_source('desktop-macOS-arm64', '1', self.source, '2', source), ('2', source))
        self.assertEqual(reuse.reuse_source('desktop-Linux-x64', '1', self.source, '2', source), ('1', self.source))
        for run, sha in [('2', ''), ('', source), ('2', 'branch')]:
            with self.assertRaises(SystemExit):
                reuse.reuse_source('desktop-macOS-arm64', '1', self.source, run, sha)
        with self.assertRaisesRegex(SystemExit, 'did not succeed'):
            reuse.validate(self.run, self.source, [{**self.job, 'name':'reuse (desktop-macOS-arm64)'}],
                           [self.artifact], 'desktop-macOS-arm64', [])

    def test_product_diff_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, 'product changes'):
            self.validate(['bridge/app/service.py'])

    def test_wrong_source_or_workflow_is_rejected(self):
        for key, value in [('head_sha', 'c'*40), ('path', '.github/workflows/tests.yml'), ('status', 'in_progress')]:
            with self.subTest(key=key):
                old = self.run[key]
                self.run[key] = value
                with self.assertRaisesRegex(SystemExit, 'Unexpected upstream'):
                    self.validate()
                self.run[key] = old

    def test_failed_platform_job_is_rejected(self):
        self.job['conclusion'] = 'failure'
        with self.assertRaisesRegex(SystemExit, 'did not succeed'):
            self.validate()

    def test_invalid_artifact_is_rejected(self):
        for key, value in [('expired', True), ('digest', ''), ('size_in_bytes', 0)]:
            old = self.artifact[key]
            self.artifact[key] = value
            with self.assertRaisesRegex(SystemExit, 'Invalid or expired'):
                self.validate()
            self.artifact[key] = old

    def test_artifact_digest_and_complete_package_set_are_required(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            root = Path(folder)
            archive = root/'upstream.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                for suffix in reuse.PLATFORMS[self.name][1]:
                    package.writestr('Codex-Mobile-Bridge-'+reuse.prepare.VERSION+'-'+suffix, b'original bytes')
            self.artifact['digest'] = 'sha256:'+hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(len(reuse.extract(archive, self.artifact, self.name, root/'valid')), 2)
            self.artifact['digest'] = 'sha256:'+'0'*64
            with self.assertRaisesRegex(SystemExit, 'digest mismatch'):
                reuse.extract(archive, self.artifact, self.name, root/'bad')
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('../unexpected', b'bad')
            self.artifact['digest'] = 'sha256:'+hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(SystemExit, 'Incomplete'):
                reuse.extract(archive, self.artifact, self.name, root/'incomplete')
