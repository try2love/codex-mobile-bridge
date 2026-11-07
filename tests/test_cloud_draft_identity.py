import http.client
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from bridge.api.httpd import GatewayServer
from bridge.app.cloud import create_cloud_server


class CloudDraftIdentityTests(unittest.TestCase):
    def test_cloud_restart_preserves_draft_scope_without_sharing_login_authority(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as directory:
            for _ in range(2):
                gateway = GatewayServer(('127.0.0.1', 0), Mock(),
                    {'auth': {'mode': 'password'}, 'origins': []}, Path(__file__).resolve().parents[1] / 'web', directory)
                try:
                    identity = gateway.draft_identity
                    if _:
                        self.assertEqual(identity, previous)
                    previous = identity
                    cloud = create_cloud_server(gateway)
                    cloud.desktop_sessions = None
                    try:
                        self.assertEqual(cloud.draft_identity, identity)
                        self.assertIsNot(cloud.auth, gateway.auth)
                        self.assertNotEqual(cloud.auth.cookie_name, gateway.auth.cookie_name)
                        thread = threading.Thread(target=cloud.serve_forever, daemon=True)
                        thread.start()
                        token, _session = cloud.auth.new_session('127.0.0.1', 'approved fixture')
                        conn = http.client.HTTPConnection('127.0.0.1', cloud.server_port, timeout=2)
                        try:
                            conn.request('GET', '/api/auth', headers={'Cookie': cloud.auth.cookie_name + '=' + token})
                            reply = conn.getresponse()
                            self.assertEqual(reply.status, 200)
                            self.assertEqual(json.loads(reply.read())['draftIdentity'], identity)
                        finally:
                            conn.close()
                            cloud.shutdown()
                            thread.join(2)
                    finally:
                        cloud.server_close()
                finally:
                    gateway.server_close()
