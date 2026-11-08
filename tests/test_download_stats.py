import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('download_stats', Path(__file__).parents[1] / 'scripts/build-download-stats.py')
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


class DownloadStatsTests(unittest.TestCase):
    def release(self, names, **extra):
        return {'published_at': '2026-11-08T00:00:00Z', 'draft': False,
                'assets': [{'id': i, 'name': n, 'state': 'uploaded', 'download_count': i + 1}
                           for i, n in enumerate(names)], **extra}

    def test_counts_platform_packages_and_ios_source_without_metadata(self):
        names = ['Android.apk', 'iOS-source.zip', 'iOS-unsigned.ipa', 'Linux-amd64.deb',
                 'Linux-arm64.AppImage', 'macOS-arm64.dmg', 'macOS-x64.zip',
                 'Windows-x64-Setup.exe', 'Windows-x64.zip', 'Windows-x64.zip.blockmap',
                 'Android.apk.sha256', 'source.zip']
        release = self.release(['Codex-Mobile-Bridge-2.2.0-' + n for n in names]
                               + ['bridge-update.json', 'SHA256SUMS.txt', 'latest.yml'])
        self.assertEqual(stats.totals([release]), {'android': 1, 'ios': 5, 'linux': 9,
                                                  'macos': 13, 'windows': 17})

    def test_draft_unpublished_and_duplicate_assets(self):
        r = self.release(['Codex-Mobile-Bridge-2.2.0-preview.3-Android.apk'], prerelease=True)
        self.assertEqual(stats.totals([r, r])['android'], 1)
        self.assertEqual(stats.totals([{**r, 'draft': True}, {**r, 'published_at': None}])['android'], 0)
        r['assets'][0]['state'] = 'starter'
        self.assertEqual(stats.totals([r])['android'], 0)

    def test_outputs_only_public_aggregate_fields(self):
        release = self.release(['Codex-Mobile-Bridge-2.2.0-iOS.ipa'])
        release['body'] = 'private diagnostic must not be exported'
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)
            stats.write_badges([release], output)
            self.assertEqual(len(list(output.glob('*.json'))), 6)
            self.assertEqual(json.loads((output / 'ios.json').read_text())['message'], '1')
            self.assertNotIn('diagnostic', ''.join(p.read_text() for p in output.iterdir()))

    def test_paginated_releases_and_large_asset_lists(self):
        r = self.release(['bridge-update.json'] * 100, id=17)
        with patch.object(stats, 'fetch_pages', side_effect=[[r], []]) as fetch:
            self.assertEqual(stats.fetch_releases('owner/repo')[0]['assets'], [])
            self.assertEqual(fetch.call_args.args[0], 'repos/owner/repo/releases/17/assets')

    def test_fetches_all_pages_and_propagates_api_failure(self):
        with patch.object(stats, 'urlopen', side_effect=[
                io.BytesIO(json.dumps([{'id': i} for i in range(100)]).encode()),
                io.BytesIO(b'[{"id":100}]')]) as fetch:
            self.assertEqual(len(stats.fetch_pages('repos/owner/repo/releases')), 101)
            self.assertTrue(fetch.call_args.args[0].full_url.endswith('page=2'))
        with patch.object(stats, 'urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                stats.fetch_pages('repos/owner/repo/releases')

    def test_invalid_counts_fail_instead_of_publishing_zero(self):
        r = self.release(['Codex-Mobile-Bridge-2.2.0-Android.apk'])
        r['assets'][0]['download_count'] = -1
        with self.assertRaises(ValueError):
            stats.totals([r])


if __name__ == '__main__':
    unittest.main()
