"""Gateway status must survive an unrelated loopback port forward."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.app.desktop import Desktop
from bridge.app.lifecycle import GatewayControl, read_record
from bridge.features.auth.auth import Auth
from bridge.features.auth.pairing import Pairing

ROOT = Path(__file__).resolve().parents[1]


class GatewayStatusTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        self.desktop = Desktop(self.home)
        self.desktop.preferences = lambda: {'port': 11451, 'localAccess': False}
        http = patch('bridge.app.desktop.http.client.HTTPConnection')
        self.http = http.start()
        self.addCleanup(http.stop)
        self.http.return_value.request.side_effect = ConnectionResetError('loopback SSH forward')

    def control(self):
        pairing = Pairing(Auth({'mode': 'none'}), set())
        control = GatewayControl(self.home)
        control.start(lambda: None, pairing.control, 'own-instance', notifications=lambda _: {})
        self.addCleanup(control.close)
        return control

    def http_instance(self, instance):
        self.http.return_value.request.side_effect = None
        self.http.return_value.getresponse.return_value = Mock(status=200,
            read=lambda _: json.dumps({'service': 'codex-mobile-bridge', 'instanceId': instance,
                                       'notifications': True}).encode())

    def test_live_private_control_confirms_gateway_when_loopback_is_shadowed(self):
        control = self.control()
        state = self.desktop.status()
        self.assertTrue(state['running'])
        self.assertFalse(state['portOccupied'])
        self.assertEqual(state['instanceId'], 'own-instance')
        self.assertEqual(state['pid'], control.record['pid'])
        self.assertTrue(state['supportsNotifications'])
        self.assertEqual(list(control.pairing_dir.iterdir()), [])

    def test_foreign_loopback_gateway_does_not_hide_live_own_gateway(self):
        self.control()
        self.http_instance('other-instance')
        self.assertTrue(self.desktop.status()['running'])
        self.assertEqual(self.desktop.status()['instanceId'], 'own-instance')

    def test_matching_http_health_needs_no_control_poll(self):
        self.control()
        self.http_instance('own-instance')
        with patch('bridge.app.desktop.request_pairing') as request:
            self.assertTrue(self.desktop.status()['running'])
            request.assert_not_called()

    def test_missing_control_record_cannot_become_running(self):
        with patch('bridge.app.desktop.request_pairing') as request:
            self.assertFalse(self.desktop.status()['running'])
            request.assert_not_called()

    def test_stale_record_and_foreign_http_never_become_owned(self):
        control = self.control()
        record = dict(control.record)
        control.close()
        (self.home/'gateway-control.json').write_text(json.dumps(record))
        self.assertFalse(self.desktop.status()['running'])
        self.http_instance('other-instance')
        state = self.desktop.status()
        self.assertFalse(state['running'])
        self.assertTrue(state['portOccupied'])

    def test_control_timeout_or_invalid_response_cannot_establish_readiness(self):
        self.control()
        for error, result in [(ValueError('timeout'), None), (None, {}), (None, {'states': {'x': 'active'}})]:
            with self.subTest(result=result), patch('bridge.app.desktop.request_pairing', side_effect=error, return_value=result):
                self.assertFalse(self.desktop.status()['running'])

    def test_replaced_control_record_invalidates_response(self):
        self.control()
        def replaced(*args, **kwargs):
            record = read_record(self.home/'gateway-control.json')
            record['instanceId'] = 'replacement'
            (self.home/'gateway-control.json').write_text(json.dumps(record))
            return {'states': {}}
        with patch('bridge.app.desktop.request_pairing', side_effect=replaced):
            self.assertFalse(self.desktop.status()['running'])


if __name__ == '__main__':
    unittest.main()
