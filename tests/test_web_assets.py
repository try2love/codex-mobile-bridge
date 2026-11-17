"""Ordered asset assembly and caching without starting an HTTP server."""
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.api import assets


ROOT = Path(__file__).resolve().parents[1]


class WebAssetTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(folder.cleanup)
        self.web = Path(folder.name)
        assets._assembled.cache_clear()
        self.addCleanup(assets._assembled.cache_clear)

    def test_version_is_content_addressed(self):
        source = self.web / 'app.js'
        source.write_bytes(b'console.log("one")\n')
        route = {'/app.js': ('app.js', 'text/javascript; charset=utf-8')}
        with patch.dict(assets.STATIC, route):
            first = assets.current_version(self.web, '/app.js')
            self.assertRegex(first, r'^[0-9a-f]{64}$')
            self.assertEqual(first, assets.current_version(self.web, '/app.js'))
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            self.assertEqual(first, assets.current_version(self.web, '/app.js'))
            source.write_bytes(b'console.log("two")\n')
            self.assertNotEqual(first, assets.current_version(self.web, '/app.js'))

    def test_served_asset_rewrites_bare_and_tagged_references(self):
        (self.web / 'app.js').write_bytes(b'console.log(1)\n')
        (self.web / 'shell.css').write_bytes(b'.chat{color:red}\n')
        (self.web / 'index.html').write_bytes(
            b'<script src="/app.js?v=20260101"></script><link href="/shell.css">')
        routes = {
            '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
            '/shell.css': ('shell.css', 'text/css; charset=utf-8'),
            '/': ('index.html', 'text/html; charset=utf-8'),
        }
        with patch.dict(assets.STATIC, routes):
            html = assets.served_asset_bytes(self.web, '/').decode()
            self.assertIn('/app.js?v=' + assets.current_version(self.web, '/app.js'), html)
            self.assertIn('/shell.css?v=' + assets.current_version(self.web, '/shell.css'), html)
            self.assertNotIn('20260101', html)

    def test_binary_assets_are_not_rewritten(self):
        payload = b'\x89PNG\r\n\x1a\n/image.png"\x00'
        (self.web / 'icon.png').write_bytes(payload)
        with patch.dict(assets.STATIC, {'/icon.png': ('icon.png', 'image/png')}):
            self.assertEqual(assets.served_asset_bytes(self.web, '/icon.png'), payload)

    def test_parent_version_tracks_served_dependency_bytes_and_rollback(self):
        (self.web / 'app.js').write_bytes(b'const icon = "/icon.png";')
        icon = self.web / 'icon.png'
        icon.write_bytes(b'icon-v1')
        routes = {
            '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
            '/icon.png': ('icon.png', 'image/png'),
        }
        with patch.dict(assets.STATIC, routes):
            before = assets.served_asset_bytes(self.web, '/app.js')
            before_version = assets.current_version(self.web, '/app.js')
            self.assertEqual(before_version, hashlib.sha256(before).hexdigest())
            icon.write_bytes(b'icon-version-two')
            after = assets.served_asset_bytes(self.web, '/app.js')
            after_version = assets.current_version(self.web, '/app.js')
            self.assertNotEqual(before, after)
            self.assertNotEqual(before_version, after_version)
            self.assertEqual(after_version, hashlib.sha256(after).hexdigest())
            icon.write_bytes(b'icon-v1')
            self.assertEqual(before, assets.served_asset_bytes(self.web, '/app.js'))
            self.assertEqual(before_version, assets.current_version(self.web, '/app.js'))

    def test_source_changes_refresh_served_body_and_homepage_without_restart(self):
        source = self.web / 'app.js'
        source.write_bytes(b'console.log("one")\n')
        (self.web / 'index.html').write_bytes(b'<script src="/app.js"></script>')
        routes = {
            '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
            '/': ('index.html', 'text/html; charset=utf-8'),
        }
        with patch.dict(assets.STATIC, routes):
            previous_version = assets.current_version(self.web, '/app.js')
            previous_homepage = assets.served_asset_bytes(self.web, '/')
            self.assertEqual(source.read_bytes(), assets.served_asset_bytes(self.web, '/app.js'))
            source.write_bytes(b'console.log("two")\n')
            current = assets.current_version(self.web, '/app.js')
            self.assertNotEqual(previous_version, current)
            self.assertEqual(source.read_bytes(), assets.served_asset_bytes(self.web, '/app.js'))
            homepage = assets.served_asset_bytes(self.web, '/')
            self.assertNotEqual(previous_homepage, homepage)
            self.assertIn(('/app.js?v=' + current).encode(), homepage)
            self.assertNotIn(previous_version.encode(), homepage)

    def test_css_bundle_preserves_declared_order_and_exact_bytes(self):
        first = b'.chat { color: blue; }\n'
        second = '/* \u624b\u673a\u8986\u76d6 */\n.chat { color: red; }\n'.encode()
        (self.web / 'z-base.css').write_bytes(first)
        (self.web / 'a-mobile.css').write_bytes(second)
        route = (('z-base.css', 'a-mobile.css'), 'text/css; charset=utf-8')
        with patch.dict(assets.STATIC, {'/style.css': route}):
            self.assertEqual(assets.asset_bytes(self.web, '/style.css'), first + second)

    def test_bundle_reuses_content_reads_until_source_metadata_changes(self):
        first = self.web / 'base.css'
        second = self.web / 'mobile.css'
        first.write_bytes(b'.chat{color:red;}\n')
        second.write_bytes(b'.chat{width:1px;}\n')
        route = (('base.css', 'mobile.css'), 'text/css; charset=utf-8')
        original_read = Path.read_bytes
        expected = original_read(first) + original_read(second)
        with patch.dict(assets.STATIC, {'/style.css': route}), patch.object(
                Path, 'read_bytes', autospec=True, side_effect=original_read) as reads:
            self.assertEqual(assets.asset_bytes(self.web, '/style.css'), expected)
            self.assertEqual(reads.call_count, 2)
            self.assertEqual(assets.asset_bytes(self.web, '/style.css'), expected)
            self.assertEqual(reads.call_count, 2)

            old = second.stat()
            second.write_bytes(b'.chat{width:2px;}\n')
            os.utime(second, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
            self.assertEqual(second.stat().st_size, old.st_size)
            expected = original_read(first) + original_read(second)
            self.assertEqual(assets.asset_bytes(self.web, '/style.css'), expected)
            self.assertEqual(reads.call_count, 4)
            self.assertEqual(assets.asset_bytes(self.web, '/style.css'), expected)
            self.assertEqual(reads.call_count, 4)

            old = second.stat()
            second.write_bytes(b'.chat{width:200px;}\n')
            os.utime(second, ns=(old.st_atime_ns, old.st_mtime_ns))
            expected = original_read(first) + original_read(second)
            self.assertEqual(assets.asset_bytes(self.web, '/style.css'), expected)
            self.assertEqual(reads.call_count, 6)

    def test_extra_manifest_entries_cannot_publish_an_unlisted_file(self):
        web = self.web / 'web'
        web.mkdir()
        (web / 'private.css').write_text('private fixture', encoding='utf-8')
        (web / 'assets.json').write_text(json.dumps({
            '/private-fixture.css': 'private.css',
            '/features/private.css': 'private.css',
        }), encoding='utf-8')
        spec = importlib.util.spec_from_file_location('isolated_web_assets', assets.__file__)
        isolated = importlib.util.module_from_spec(spec)
        with patch('bridge.resources.project_root', return_value=self.web):
            spec.loader.exec_module(isolated)
        self.assertEqual(set(isolated.STATIC), set(assets.STATIC))
        for public_path in ('/private-fixture.css', '/features/private.css'):
            with self.subTest(public_path=public_path):
                self.assertNotIn(public_path, isolated.STATIC)
                with self.assertRaises(KeyError):
                    isolated.asset_bytes(web, public_path)


if __name__ == '__main__':
    unittest.main()
