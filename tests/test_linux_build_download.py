import base64
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

spec = importlib.util.spec_from_file_location('linux_bundle_fixture', Path(__file__).resolve().parents[1]/'scripts/bundle-cloudflared.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class LinuxBuildDownloadTests(unittest.TestCase):
    def test_linux_timeout_uses_same_upstream_tag(self):
        item = {'name': 'LICENSE', 'encoding': 'base64', 'content': base64.b64encode(b'upstream license').decode()+'\n'}
        with patch.object(bundle.sys, 'platform', 'linux'), patch.object(bundle, 'urlopen', side_effect=[
                URLError('TLS timeout'), io.BytesIO(json.dumps(item).encode())]) as opened:
            self.assertEqual(bundle.license_data('2026.9.3', 'LICENSE'), b'upstream license')
            self.assertEqual(opened.call_args.args[0].full_url,
                'https://api.github.com/repos/cloudflare/cloudflared/contents/LICENSE?ref=2026.9.3')

    def test_other_platforms_do_not_use_linux_fallback(self):
        for platform in ('darwin', 'win32'):
            with patch.object(bundle.sys, 'platform', platform), patch.object(bundle, 'urlopen', side_effect=URLError('timeout')) as opened:
                with self.assertRaises(URLError): bundle.license_data('2026.9.3', 'LICENSE')
                self.assertEqual(opened.call_count, 1)

    def test_missing_notice_and_invalid_response_are_not_silently_replaced(self):
        with patch.object(bundle.sys, 'platform', 'linux'):
            with patch.object(bundle, 'urlopen', side_effect=HTTPError('https://fixture', 404, 'missing', {}, None)) as opened:
                with self.assertRaises(HTTPError): bundle.license_data('2026.9.3', 'NOTICE')
                self.assertEqual(opened.call_count, 1)
            for item in ({'name': 'wrong'}, {'name':'LICENSE','encoding':'base64','content':'invalid!'}):
                with patch.object(bundle, 'urlopen', side_effect=[URLError('timeout'), io.BytesIO(json.dumps(item).encode())]):
                    with self.assertRaises(ValueError): bundle.license_data('2026.9.3', 'LICENSE')
