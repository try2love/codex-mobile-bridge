import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.clients.claude.adapter import Claude
from bridge.clients.claude.mailbox import CONNECTOR_REVISION


class ClaudeBackgroundReconnectTests(unittest.TestCase):
    def setUp(self):
        platform = patch('bridge.clients.claude.adapter.sys.platform', 'win32')
        platform.start(); self.addCleanup(platform.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'claude'
        self.directory.mkdir()
        self.discovery = {'installed': True, 'executable': str(self.directory/'Claude.exe'),
                          'dataHome': str(self.directory/'profile'), 'automaticConnection': 'native-console',
                          'autoConnect': True}
        (self.directory/'discovery.json').write_text(json.dumps(self.discovery))
        self.native = patch('bridge.clients.claude.adapter.native_action').start()
        self.running = patch('bridge.clients.claude.adapter.running_app').start()
        self.addCleanup(patch.stopall)

    def adapter(self, **options):
        adapter = Claude(self.directory, **options)
        self.addCleanup(lambda: adapter.close() if adapter.loop is None or not adapter.loop.is_closed() else None)
        return adapter

    def heartbeat(self, adapter):
        desktop = adapter.desktop
        response = {'generation': desktop.generation, 'timestamp': time.time(), 'connected': True,
                    'connectorRevision': CONNECTOR_REVISION, 'surfaces': {'code': ['getAll']}}
        (self.directory/'response.json').write_text(desktop.pack(response), encoding='utf-8')

    def test_automatic_constructor_prepares_mailbox_without_native_initialization(self):
        with patch.object(Claude, 'connect') as connect:
            adapter = self.adapter()
        connect.assert_not_called()
        self.native.assert_not_called(); self.running.assert_not_called()
        self.assertIsNotNone(adapter.desktop)
        self.assertIsNone(adapter.setup_thread)
        self.assertEqual(adapter.status()['setupState'], 'needs-initialization')
        self.assertTrue(adapter.discovery['autoConnect'])

    def test_polling_and_repeated_background_enable_keep_generation_stable(self):
        adapter = self.adapter(auto_connect=False)
        adapter.reconnect()
        generation = adapter.desktop.generation
        for _ in range(3):
            adapter.status(); adapter.reconnect()
            self.assertEqual(adapter.desktop.generation, generation)
        self.native.assert_not_called(); self.running.assert_not_called()
        self.assertFalse(adapter.status()['connected'])

    def test_gateway_handoff_reconnects_current_signed_renderer_without_foreground_work(self):
        adapter = self.adapter(auto_connect=False); adapter.reconnect(); self.heartbeat(adapter)
        self.assertTrue(adapter.status()['connected'])
        generation, token = adapter.desktop.generation, adapter.desktop.token
        adapter.close()
        with patch.object(Claude, 'connect') as connect:
            other = self.adapter()
        connect.assert_not_called()
        self.assertNotEqual(other.desktop.generation, generation)
        self.assertEqual(other.desktop.token, token)
        self.assertFalse(other.status()['connected'])
        self.heartbeat(other)
        self.assertTrue(other.status()['connected'])
        self.assertEqual(other.status()['setupState'], 'connected')
        self.native.assert_not_called(); self.running.assert_not_called()

    def test_background_reconnect_after_cancel_publishes_one_fresh_generation(self):
        adapter = self.adapter(auto_connect=False); adapter.reconnect()
        before = adapter.desktop.generation
        adapter.cancel(); adapter.reconnect()
        self.assertNotEqual(adapter.desktop.generation, before)
        self.assertFalse(adapter.setup_cancel.is_set())
        self.assertTrue(adapter.discovery['autoConnect'])
        self.native.assert_not_called(); self.running.assert_not_called()


if __name__ == '__main__':
    unittest.main()
