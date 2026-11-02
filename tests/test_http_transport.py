"""Transport evidence correlates hops without recording conversations or secrets."""
import http.client
import json
import socketserver
import unittest
from unittest.mock import patch

import test_bridge
from bridge.features.network.named_tunnel import transport_failure


class TransportTests(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_fast_cloudflare_asset_keeps_id_without_routine_info_records(self):
        headers = {'CF-Ray': 'abcdef0123456789-SIN'}
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        self.addCleanup(conn.close)
        with self.assertNoLogs('bridge.api.httpd', level='INFO'):
            conn.request('GET', '/style.css', headers=headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertRegex(response.getheader('X-Bridge-Request-Id'), r'^[a-f0-9]{16}$')
            self.assertTrue(response.read())

    def test_send_response_correlates_ray_without_private_request_data(self):
        headers = self.login()
        headers['CF-Ray'] = 'abcdef0123456789-SIN'
        self.server.bridge.send = lambda *a, **k: {'status': 'accepted'}
        with self.assertLogs('bridge.api.httpd', level='INFO') as logs:
            status, response, _ = self.request('POST', '/api/sessions/' + test_bridge.THREAD + '/send',
                {'id': 'ab7bd811-51aa-4a8c-b89f-4ad4d64c0ac0', 'text': 'PRIVATE_MESSAGE', 'mode': 'send'}, headers)
        self.assertEqual(status, 200)
        identifier = response['X-Bridge-Request-Id']
        self.assertRegex(identifier, r'^[a-f0-9]{16}$')
        self.assertEqual(len(logs.output), 2)
        text = '\n'.join(logs.output)
        self.assertEqual(text.count('id=' + identifier), 2)
        self.assertIn('ray=abcdef0123456789-SIN', text)
        self.assertIn('status=200 written=True', text)
        for private in ('PRIVATE_MESSAGE', test_bridge.THREAD, headers['Cookie'], headers['X-CSRF-Token']):
            self.assertNotIn(private, text)

    def test_invalid_ray_is_not_echoed_and_failed_write_is_distinct_from_acceptance(self):
        headers = self.login()
        headers['CF-Ray'] = 'PRIVATE_NOT_A_RAY'
        accepted = []
        self.server.bridge.send = lambda *a, **k: accepted.append(True) or {'status': 'accepted'}
        original = socketserver._SocketWriter.write
        def interrupted(writer, data):
            if data.startswith(b'{"status": "accepted"'):
                raise BrokenPipeError('PRIVATE_ERROR')
            return original(writer, data)
        with self.assertLogs('bridge.api.httpd', level='INFO') as logs, patch.object(socketserver._SocketWriter, 'write', interrupted):
            with self.assertRaises(http.client.IncompleteRead):
                connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
                self.addCleanup(connection.close)
                connection.request('POST', '/api/sessions/' + test_bridge.THREAD + '/send',
                    json.dumps({'id': 'ab7bd811-51aa-4a8c-b89f-4ad4d64c0ac0', 'text': 'PRIVATE_MESSAGE', 'mode': 'send'}),
                    {**headers, 'Origin': self.origin, 'Content-Type': 'application/json'})
                connection.getresponse().read()
        self.assertEqual(accepted, [True])
        text = '\n'.join(logs.output)
        self.assertIn('ray=-', text)
        self.assertIn('status=200 written=False', text)
        self.assertNotIn('PRIVATE_', text)


class TunnelDiagnostics(unittest.TestCase):
    def test_provider_output_only_exposes_allowlisted_labels(self):
        line = 'ERR Request failed error="Unable to reach the origin service: connection refused" connIndex=2 cfRay=abcdef0123456789-ATL dest=https://private.example/secret?token=SECRET config=SECRET'
        self.assertEqual(transport_failure(line), {'cause': 'origin_refused', 'connection': '2', 'ray': 'abcdef0123456789-ATL'})
        self.assertEqual(transport_failure('ERR secret=PRIVATE'), {'cause': 'other', 'connection': '-', 'ray': '-'})
        self.assertIsNone(transport_failure('INF Updated config=PRIVATE'))
        self.assertIsNone(transport_failure('ERR Failed to send ICMP reply PRIVATE'))


if __name__ == '__main__':
    unittest.main()
