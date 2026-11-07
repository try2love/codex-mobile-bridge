"""The PDF reader's 20 MiB budget is enforced on every download request."""
import http.client
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_bridge as support
from bridge.app.service import Bridge
from bridge.clients.codex.remote import RemoteStore, payload
from bridge.features.workspace.preferences import transfer_settings
from bridge.features.workspace.workspace import MAX_TRANSFER, operate
from relay.protocol import validate

ROOT = Path(__file__).resolve().parents[1]


class PdfPreviewLimitTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    setUp = support.HttpTests.setUp
    tearDown = support.HttpTests.tearDown
    login = support.HttpTests.login
    request = support.HttpTests.request

    def range_request(self, path, auth):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request('GET', path, headers={'Origin': self.origin, 'Cookie': auth['Cookie'], 'Range': 'bytes=0-1023'})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_growth_after_info_cannot_cross_preview_budget_or_explicit_override(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            root = Path(folder)
            pdf = root / 'sample.pdf'
            with pdf.open('wb') as stream:
                stream.truncate(MAX_TRANSFER - 1024)
            self.server.transfer_directory = root
            transfer_settings(root, {'clickDownloadMiB': 100})
            self.server.bridge.workspace = lambda thread, action, params: operate(root, action, params)
            auth = self.login()
            url = '/api/sessions/' + support.THREAD + '/workspace/download?path=sample.pdf'
            status, _, info = self.request('GET', url + '&info=1', headers=auth)
            self.assertEqual(status, 200)
            self.assertEqual(info['size'], MAX_TRANSFER - 1024)
            self.assertEqual(info['clickDownloadMiB'], 100)
            status, headers, data = self.range_request(url + '&preview=1', auth)
            self.assertEqual((status, len(data)), (206, 1024))
            self.assertEqual(headers['Content-Range'], 'bytes 0-1023/%d' % (MAX_TRANSFER - 1024))
            with pdf.open('r+b') as stream:
                stream.truncate(MAX_TRANSFER + 1024)
            self.assertEqual(self.range_request(url + '&preview=1', auth)[0], 400)
            self.assertEqual(self.range_request(url + '&preview=1&explicit=1', auth)[0], 400)
            self.assertEqual(self.range_request(url + '&preview=1&archive=1&explicit=1', auth)[0], 400)
            status, headers, data = self.range_request(url, auth)
            self.assertEqual((status, len(data)), (206, 1024))
            self.assertEqual(headers['Content-Range'], 'bytes 0-1023/%d' % (MAX_TRANSFER + 1024))

    def test_ssh_and_relay_keep_bounded_download_parameters(self):
        store = RemoteStore('fixture-host')
        store.get = lambda thread: {'cwd': '/synthetic/project'}
        bridge = SimpleNamespace(host='remote:fixture', local=False, store=store)
        params = {'path': 'sample.pdf', 'limit': MAX_TRANSFER, 'range_header': 'bytes=0-1023', 'if_range': ''}
        with patch('bridge.app.service.ssh_download', return_value='synthetic-stream') as download:
            self.assertEqual(Bridge.workspace(bridge, support.THREAD, 'download-stream', params), 'synthetic-stream')
        self.assertIn(payload({'root': '/synthetic/project', 'action': 'download-stream', 'params': params}), download.call_args.args[1])
        path = '/api/sessions/' + support.THREAD + '/workspace/download?path=sample.pdf&preview=1'
        validate('GET', path)
        for provider in ('claude', 'deepseek'):
            validate('GET', '/api/desktop-sessions/' + provider + '/workspace/download?sessionId=synthetic&path=sample.pdf&preview=1')
        try:
            from relay.server import Relay
        except ModuleNotFoundError:
            return
        device = 'a' * 32
        request = SimpleNamespace(match_info={'device': device}, path_qs='/d/' + device + path)
        self.assertEqual(object.__new__(Relay).api_path(request), path)
