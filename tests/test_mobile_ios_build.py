import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_mobile_ios', ROOT/'scripts/build-mobile-ios.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class IOSReleaseBuildTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='ios-source-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def sources(self):
        for name in builder.REQUIRED_SOURCES:
            file = self.root/name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('tracked fixture', encoding='utf-8')
        return list(builder.REQUIRED_SOURCES)

    def test_source_zip_uses_tracked_inputs_and_preserves_project_relative_resources(self):
        names = self.sources()
        private = self.root/'mobile/ios/secret.p12'
        private.write_text('untracked secret', encoding='utf-8')
        names.append('mobile/ios/BridgePreview.xcodeproj/xcuserdata/local.xcuserstate')
        with patch.object(builder.subprocess, 'check_output', return_value=('\0'.join(names)+'\0').encode()) as listing:
            archive = self.root/'source.zip'
            self.assertEqual(builder.source_archive(self.root, archive), len(builder.REQUIRED_SOURCES))
        self.assertEqual(listing.call_args.args[0][:4], ['git', 'ls-files', '-z', '--'])
        with zipfile.ZipFile(archive) as result:
            self.assertEqual(set(result.namelist()), set(builder.REQUIRED_SOURCES))
            self.assertNotIn('mobile/ios/secret.p12', result.namelist())

    def test_source_zip_requires_all_project_resources(self):
        names = self.sources()[1:]
        with patch.object(builder.subprocess, 'check_output', return_value=('\0'.join(names)+'\0').encode()):
            with self.assertRaisesRegex(RuntimeError, 'missing project resources'):
                builder.source_archive(self.root, self.root/'source.zip')

    def test_source_zip_rejects_links(self):
        names = self.sources()
        with patch.object(builder.subprocess, 'check_output', return_value=('\0'.join(names)+'\0').encode()), \
                patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'regular tracked project files'):
                builder.source_archive(self.root, self.root/'source.zip')

    def test_unsigned_app_requires_matching_extensions_resources_and_device_architecture(self):
        app = self.root/'BridgePreview.app'
        bundles = [(app, 'io.github.try2love.codexbridge.preview'),
                   (app/'PlugIns/BridgeActivity.appex', 'io.github.try2love.codexbridge.preview.activity'),
                   (app/'PlugIns/BridgeNotifications.appex', 'io.github.try2love.codexbridge.preview.notifications')]
        for bundle, identifier in bundles:
            bundle.mkdir(parents=True)
            info = {'CFBundleIdentifier': identifier, 'CFBundleShortVersionString': '2.0.0',
                    'CFBundleVersion': '27', 'CFBundleExecutable': bundle.stem, 'BridgeReleaseVersion': '2.0.0'}
            (bundle/'Info.plist').write_bytes(plistlib.dumps(info))
            (bundle/bundle.stem).write_bytes(b'fixture executable')
        for name in ('mobile-ui.js', 'mobile-clipboard.js', 'codex.png', 'claude.png', 'deepseek.png'):
            (app/name).write_bytes(b'fixture resource')
        with patch.object(builder.subprocess, 'run') as native:
            builder.verify_app(app, '2.0.0', '27')
            self.assertEqual(native.call_count, 3)
            for call in native.call_args_list:
                self.assertEqual(call.args[0][-2:], ['-verify_arch', 'arm64'])
            with self.assertRaisesRegex(RuntimeError, 'identity/version mismatch'):
                builder.verify_app(app, '2.0.0', '28')
            (app/'embedded.mobileprovision').write_bytes(b'private signing profile')
            with self.assertRaisesRegex(RuntimeError, 'unsigned iOS bundle'):
                builder.verify_app(app, '2.0.0', '27')


if __name__ == '__main__':
    unittest.main()
