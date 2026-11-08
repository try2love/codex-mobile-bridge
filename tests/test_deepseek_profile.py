"""Native profile preparation runs before a GUI can quarantine partial data."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.clients.deepseek.profile import initializer, prepare
from bridge.clients.deepseek.adapter import DeepSeek
from bridge.clients.deepseek.setup import insertion, MARKER
from bridge.clients.instances import prepare_deepseek_profile
from bridge.clients.profiles import resolve_profile

ROOT = Path(__file__).resolve().parents[1]


class NativeDshProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = self.root/'application/DSH.exe'
        self.package = self.app.parent/'resources/app'
        self.package.mkdir(parents=True)
        self.app.write_bytes(b'fixture app')
        (self.package/'package.json').write_text(json.dumps({'name': 'dsh-plugin-desktop',
            'version': 'arbitrary-future', 'exports': {'./profile': {'default': './lib/profile.js'}}}))
        (self.package/'lib').mkdir()
        (self.package/'lib/profile.js').write_text('export function ensureDesktopProfile() {}')
        self.profile = resolve_profile('deepseek', self.root/'new-home')
        self.profile.home.mkdir()
        self.native = self.profile.home/'profiles/desktop'
        self.guard = Mock()
        self.descriptor = {'executable': str(self.app), 'profileGuard': self.guard}
        self.platform = patch('bridge.clients.deepseek.profile.sys', SimpleNamespace(platform='win32'))
        self.platform.start(); self.addCleanup(self.platform.stop)
        flag = patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True)
        flag.start(); self.addCleanup(flag.stop)

    def native_success(self, *args, **kwargs):
        self.native.mkdir(parents=True, exist_ok=True)
        (self.native/'package.json').write_text(json.dumps({'name': 'dsh-profile-desktop',
            'dsh': {'profile': {'bundles': ['installed-native-template']}}}))
        for name in ('cordis.patch.yml', 'pnpm-workspace.yaml'):
            target = self.native/name
            if not target.exists():
                target.write_text('# native fixture\n')
        return SimpleNamespace(returncode=0, stdout=json.dumps({'prepared': True, 'directory': str(self.native)}).encode())

    def test_native_capability_prepares_manifest_without_overwriting_existing_patch(self):
        self.native.mkdir(parents=True)
        target = self.native/'cordis.patch.yml'; target.write_text('# retained\n')
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        with patch('subprocess.run', side_effect=self.native_success) as run, \
             patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'unused-default-key', 'NODE_OPTIONS': '--bad-option'}):
            prepare_deepseek_profile(self.profile, self.descriptor)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.assertEqual(run.call_args.args[0][0], str(self.app))
        self.assertEqual(run.call_args.args[0][-1], str(self.profile.home))
        self.assertEqual(run.call_args.kwargs['env']['ELECTRON_RUN_AS_NODE'], '1')
        self.assertNotIn('DEEPSEEK_API_KEY', run.call_args.kwargs['env'])
        self.assertNotIn('NODE_OPTIONS', run.call_args.kwargs['env'])
        self.assertEqual(self.guard.call_count, 2)
        self.assertFalse((self.profile.home/'.credentials.yaml').exists())

    def test_existing_native_manifest_does_not_run_initializer(self):
        self.native.mkdir(parents=True)
        (self.native/'package.json').write_text('{"existing":true}')
        with patch('subprocess.run') as run:
            self.assertFalse(prepare(self.profile, self.descriptor))
        run.assert_not_called()

    def test_unsupported_windows_initializer_fails_before_gui_or_partial_files(self):
        (self.package/'lib/profile.js').unlink()
        with patch('subprocess.run') as run, self.assertRaisesRegex(ValueError, '初始化入口'):
            prepare_deepseek_profile(self.profile, self.descriptor)
        run.assert_not_called()
        self.assertFalse(self.native.exists())

    def test_helper_cannot_escape_installed_package(self):
        outside = self.package.parent/'outside.js'; outside.write_text('fixture')
        (self.package/'package.json').write_text(json.dumps({'name':'dsh-plugin-desktop',
            'exports':{'./profile':'./../outside.js'}}))
        self.assertIsNone(initializer(self.app))

    def test_timeout_does_not_claim_prepared(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('helper',25)), \
             self.assertRaisesRegex(ValueError, '初始化超时'):
            prepare(self.profile, self.descriptor)
        self.assertFalse((self.native/'package.json').exists())

    def test_changed_app_or_changed_existing_patch_refuses_gui_start(self):
        def changed(*args, **kwargs):
            value = self.native_success(*args, **kwargs)
            self.app.write_bytes(b'updated app identity')
            return value
        with patch('subprocess.run', side_effect=changed), self.assertRaisesRegex(ValueError, '发生变化'):
            prepare(self.profile, self.descriptor)

    def test_receipt_must_name_exact_registered_profile(self):
        def wrong(*args, **kwargs):
            self.native_success(*args, **kwargs)
            return SimpleNamespace(returncode=0, stdout=b'{"prepared":true,"directory":"/wrong"}')
        with patch('subprocess.run', side_effect=wrong), self.assertRaisesRegex(ValueError, '未确认'):
            prepare(self.profile, self.descriptor)

    def test_install_into_native_empty_array_preserves_comments_and_roundtrips(self):
        self.native.mkdir(parents=True)
        target = self.native/'cordis.patch.yml'
        original = '# Native template\n# keep [] inside this comment\n[] # empty document\n'
        target.write_text(original)
        adapter = DeepSeek(self.root/'gateway/desktop-sessions/deepseek', self.profile.home)
        adapter.ensure_installed()
        installed = target.read_text()
        self.assertIn('# keep [] inside this comment', installed)
        self.assertIn('# empty document', installed)
        self.assertEqual([line.split('#', 1)[0].strip() for line in installed.splitlines()
                          if line.split('#', 1)[0].strip()], [adapter._line()])
        self.assertEqual(insertion(installed)[0], json.loads(adapter._line()[2:]))
        adapter.uninstall()
        self.assertEqual(target.read_text(), original)

    def test_install_does_not_rewrite_nonempty_user_yaml(self):
        self.native.mkdir(parents=True)
        target = self.native/'cordis.patch.yml'
        original = '- id: retained\n  config: !!js "do not execute"\n'
        target.write_text(original)
        adapter = DeepSeek(self.root/'gateway/desktop-sessions/deepseek', self.profile.home)
        adapter.ensure_installed()
        self.assertEqual(target.read_text().split('\n'+MARKER)[0], original)
