import base64
import struct
import tempfile
import unittest
import zlib
from unittest.mock import Mock

from bridge.features.network.cloud_account import CloudError, Controller


def image(width=64, height=64):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
    raw += chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * width) * height)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


class CloudSponsorshipTests(unittest.TestCase):
    def test_purchase_image_is_fetched_from_exact_server_without_account_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.secret = Mock(side_effect=AssertionError('must not access account credentials'))
            supplied = image()
            controller.request = Mock(return_value={'image': supplied})
            result = controller.control({'action': 'sponsorship', 'origin': 'https://relay.example.test'})
            self.assertEqual(result, {'image': supplied})
            controller.request.assert_called_once_with('https://relay.example.test', '/v1/sponsorship')
            controller.secret.assert_not_called()
            controller.request.return_value = {'image': image(65)}
            self.assertNotEqual(controller.control({'action': 'sponsorship', 'origin': 'https://relay.example.test'}), result)

    def test_invalid_or_excessive_images_fail_without_a_local_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            for supplied in [None, 'https://outside.example/image.png', 'data:image/svg+xml,<svg/>',
                             'data:image/png;base64,not png', image(1), image(1025), 'data:image/png;base64,' + 'A' * 65536]:
                controller.request = Mock(return_value={'image': supplied})
                with self.subTest(supplied=str(supplied)[:32]), self.assertRaisesRegex(CloudError, 'invalid_sponsorship_response'):
                    controller.control({'action': 'sponsorship', 'origin': 'https://relay.example.test'})
            controller.request = Mock(side_effect=CloudError('sponsorship_unavailable', 404))
            with self.assertRaisesRegex(CloudError, 'sponsorship_unavailable'):
                controller.control({'action': 'sponsorship', 'origin': 'https://relay.example.test'})

    def test_purchase_fetch_rejects_non_https_origins_before_networking(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.request = Mock()
            for origin in ['http://relay.example.test', 'https://user@relay.example.test', 'https://relay.example.test/path']:
                with self.subTest(origin=origin), self.assertRaises(ValueError):
                    controller.control({'action': 'sponsorship', 'origin': origin})
            controller.request.assert_not_called()
