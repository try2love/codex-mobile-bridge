import tempfile
import unittest
from pathlib import Path

from bridge.features.auth.auth import Auth
from bridge.features.auth.device_info import describe


ROOT = Path(__file__).resolve().parents[1]


class DeviceInfoTests(unittest.TestCase):
    def test_edge_is_not_mislabeled_as_chrome_and_frozen_macos_is_not_claimed_as_actual_version(self):
        info = describe('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36 Edg/158.0.0.0')
        self.assertEqual(info, {'kind': 'browser', 'browser': 'Edge', 'browserVersion': '158.0.0.0', 'platform': 'macOS'})

    def test_native_model_version_and_build_are_separate_from_legacy_protocol_marker(self):
        self.assertEqual(describe('BridgeMobile/0.1-Android'), {'kind': 'app', 'platform': 'Android'})
        info = describe('BridgeMobile/2.2.2-preview.2026112307-iOS BridgeModel/iPhone18%2C3 BridgeOS/26.7.0 BridgeBuild/81')
        self.assertEqual(info['model'], 'iPhone18,3')
        self.assertEqual(info['appVersion'], '2.2.2-preview.2026112307')
        self.assertEqual(info['build'], '81')

    def test_legacy_background_request_and_restart_preserve_richer_device_details(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            auth = Auth({'mode': 'none'}, Path(temp))
            token, session = auth.new_session('192.0.2.2', 'BridgeMobile/0.1-Android')
            client = {'ip': '192.0.2.2', 'source': 'direct'}
            auth.get(token, client, 'BridgeMobile/2.2.2-preview.4-Android BridgeModel/Google%20Pixel%207 BridgeOS/16 BridgeBuild/81')
            rich = session['clientInfo'].copy()
            auth.get(token, client, 'BridgeMobile/0.1-Android')
            self.assertEqual(session['clientInfo'], rich)
            auth.close()
            reopened = Auth({'mode': 'none'}, Path(temp))
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.manage({})['sessions'][0]['clientInfo'], rich)

    def test_legacy_browser_record_is_formatted_without_rebinding(self):
        auth = Auth({'mode': 'none'})
        self.addCleanup(auth.close)
        _, session = auth.new_session('192.0.2.3', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/158.0 Safari/537.36')
        del session['clientInfo']
        self.assertEqual(auth.manage({})['sessions'][0]['clientInfo']['platform'], 'Windows')

    def test_control_characters_and_unbounded_metadata_are_not_displayed(self):
        info = describe('BridgeMobile/2.2.2-Android BridgeModel/Phone%0A%09%00Model BridgeBuild/81')
        self.assertEqual(info['model'], 'PhoneModel')
        self.assertNotIn('model', describe('BridgeMobile/2.2.2-Android BridgeModel/'+'x'*200+' BridgeBuild/81'))
