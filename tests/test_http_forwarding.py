import unittest

from bridge.features.network.cloud_connector import validate_request
from relay.protocol import validate, request_headers

SID = '12345678-1234-1234-1234-123456789abc'


class HttpForwardingTests(unittest.TestCase):
    def test_range_is_bounded_and_does_not_carry_credentials(self):
        headers = {'Range': 'bytes=4194304-8388607', 'If-Range': '"snapshot"'}
        self.assertEqual(request_headers('GET', headers), headers)
        for method, value in [
            ('POST', headers), ('GET', {'If-Range': '"tag"'}),
            ('GET', {'Range': 'bytes=0-'}), ('GET', {'Range': 'bytes=0-1,3-5'}),
            ('GET', {'Range': 'bytes=2-1'}), ('GET', {'Range': 'bytes=0-20971520'}),
            ('GET', {**headers, 'If-Range': 'W/"weak"'}),
            ('GET', {**headers, 'If-Range': '"unsafe\r\nvalue"'}),
            ('GET', {**headers, 'Cookie': 'untrusted'}),
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                request_headers(method, value)

    def test_native_business_routes_and_instances(self):
        for method, route in [
            ('GET', '/api/accounts?entry=codex%3Asecondary'),
            ('POST', '/api/accounts/switch?entry=codex%3Asecondary'),
            ('POST', '/api/projects/browse'),
            ('GET', '/api/desktop-sessions/zcode/list'),
            ('POST', '/api/desktop-sessions/claude/projects/register?entry=claude%3Asecond'),
            ('POST', '/api/desktop-sessions/deepseek/edit-message'),
            ('GET', '/api/notifications/channels/webhook'),
            ('POST', '/api/notifications/channels/webhook/endpoints'),
            ('POST', '/api/notifications/channels/ntfy/test'),
            ('GET', f'/api/sessions/{SID}/delete'),
            ('POST', f'/api/sessions/{SID}/compact'),
            ('GET', f'/api/sessions/{SID}/workspace/download?path=a&explicit=1'),
        ]:
            with self.subTest(method=method, route=route):
                validate(method, route)

    def test_all_forwarders_accept_unlisted_routes_and_methods(self):
        for method, path, size in [
            ('GET', '/api/a-future-feature?entry=opaque%3Aid', 0),
            ('GET', '/api/admin', 0), ('POST', '/api/cloud/register', 2),
            ('PUT', '/api/future-resource', 4), ('PATCH', '/api/accounts', 2),
            ('DELETE', '/api/new-resource', 2), ('OPTIONS', '/api/new-resource', 0),
            ('HEAD', '/api/new-resource', 0), ('GET', '/new-asset.js?v=next', 0),
            ('GET', f'/api/sessions/{SID}/events?after=1', 0),
        ]:
            with self.subTest(method=method, path=path):
                validate(method, path, size)
                validate_request({'kind': 'request', 'id': SID, 'method': method,
                                  'path': path, 'length': size, 'headers': {},
                                  'content_type': 'application/json'})

    def test_rejects_unrelated_destinations_and_malformed_framing(self):
        for method, path, length in [
            ('GET', 'https://example.test/api/clients', 0),
            ('GET', '/api/clients#fragment', 0),
            ('GET', '/api/../api/clients', 0),
            ('GET', '/api/%63lients', 0),
            ('GET', '/api/clients\x7f', 0),
            ('GET', '//example.test/api/clients', 0),
            ('GET', 'api/clients', 0),
            ('GET\r\nCookie: injected', '/api/clients', 0),
            ('', '/api/clients', 0),
            ('HEAD', '/api/clients', 1),
            ('GET', '/api/clients', 1),
            ('POST', '/api/clients', True),
            ('POST', '/api/clients', 20971521),
        ]:
            with self.subTest(path=path), self.assertRaises((PermissionError, ValueError)):
                validate(method, path, length)


if __name__ == '__main__':
    unittest.main()
