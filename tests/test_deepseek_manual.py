"""Manual bundles bind one authenticated local profile without native lifecycle."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
import zipfile
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from bridge.clients.deepseek.manual import ManualDeepSeek, export_package, package_files, discover_plugins
from bridge.clients.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]


class ManualPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root/'home/profiles/fixture/codex-mobile-bridge'
        self.directory.mkdir(parents=True, mode=0o700)
        self.package = self.root/'plugin'
        self.package.mkdir()
        for name, value in package_files().items():
            (self.package/name).write_bytes(value)
        self.binding = {'protocol': 1, 'home': str(self.root/'home'), 'profile': 'fixture',
                        'profileDirectory': str(self.directory.parent), 'directory': str(self.directory),
                        'instanceId': str(uuid.uuid4()), 'packageDirectory': str(self.package)}
        self.save('binding.json', self.binding)
        self.save('connection.json', {'token': 'a'*64, 'endpoint': str(self.directory/'endpoint.json'),
                                      'instanceId': self.binding['instanceId']})
        owner = self
        self.calls = []
        self.running_plugin = None
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append(request['action'])
                if self.headers.get('Authorization') != 'Bearer '+'a'*64:
                    self.send_response(403); self.end_headers(); return
                data = json.dumps({'connected': True, 'configured': True, 'bridgeRevision': (owner.running_plugin or {}).get('revision', 11),
                                   'plugin': owner.running_plugin, 'sessions': [], 'status': 'accepted', 'current': {'label': 'Fixture'}}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.save('endpoint.json', {'port': self.server.server_port, 'pid': os.getpid(), 'generation': 'fixture'})

    def close_server(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()

    def save(self, name, value):
        path = self.directory/name
        path.write_text(json.dumps(value)); path.chmod(0o600)

    def manager(self):
        manager = DesktopSessions(self.root/'gateway', gateway_running=True)
        manager.config['enabled'] = {'codex': False, 'claude': False, 'deepseek': False}
        self.addCleanup(manager.close)
        return manager

    def bind(self, manager):
        return manager.control({'action': 'bind-deepseek-plugin', 'file': str(self.directory/'binding.json')})

    def test_scan_profiles_without_executable_discovery_or_network(self):
        manager = self.manager()
        with patch('bridge.clients.deepseek.manual.DeepSeek.call', side_effect=AssertionError('scan must not connect')):
            candidates = manager.control({'action': 'scan-deepseek-plugins', 'home': str(self.root/'home')})['candidates']
        self.assertEqual(candidates, [{'profile': 'fixture', 'file': str(self.directory/'binding.json')}])
        self.assertNotIn('deepseekManualBinding', manager.config)
        self.assertEqual(self.calls, [])
        (self.root/'home/profiles/alias').symlink_to(self.directory.parent)
        self.assertEqual(len(discover_plugins(str(self.root/'home'))), 1)
        self.save('binding.json', {**self.binding, 'home': str(self.root/'unrelated')})
        self.assertEqual(discover_plugins(str(self.root/'home')), [])
        self.assertEqual(discover_plugins(str(self.root/'not-installed')), [])

    def test_export_contains_same_host_and_no_private_state(self):
        result = export_package(str(self.root))
        with zipfile.ZipFile(result['path']) as archive:
            self.assertEqual(len(archive.namelist()), 7)
            self.assertEqual(archive.read('dsh-codex-mobile-bridge/host.mjs'),
                             (ROOT/'bridge/clients/deepseek/host.mjs').read_bytes())
            self.assertFalse(any('connection' in name or 'binding' in name for name in archive.namelist()))
        with tarfile.open(result['tarPath']) as archive:
            self.assertEqual(set(archive.getnames()), {'package/'+name for name in package_files()})
            manifest = json.load(archive.extractfile('package/package.json'))
            self.assertEqual(manifest['engines']['dsh'], '0.2.1-rc.2')
        with self.assertRaises(FileExistsError): export_package(str(self.root))

    def modern(self, version='0.2.0', revision=11):
        plugin = {'name': 'dsh-codex-mobile-bridge', 'version': version, 'protocol': 1, 'revision': revision}
        manifest = json.loads((self.package/'package.json').read_text())
        manifest.update(version=version, dshBridge={'protocol': 1, 'revision': revision})
        (self.package/'package.json').write_text(json.dumps(manifest))
        self.binding['plugin'] = plugin
        self.save('binding.json', self.binding)
        self.running_plugin = plugin.copy()
        return plugin

    def test_compatible_patch_and_handshake_not_source_equality(self):
        self.modern('0.2.3')
        (self.package/'host.mjs').write_text('// independently installed compatible patch')
        adapter = ManualDeepSeek(self.directory/'binding.json')
        self.assertFalse(adapter.call('status')['updateRequired'])
        self.modern('0.2.4')
        self.assertTrue(adapter.call('status')['connected'])
        self.running_plugin['revision'] = 12
        before = self.calls.count('send')
        with self.assertRaisesRegex(Exception, '运行版本'): adapter.call('send')
        self.assertEqual(self.calls.count('send'), before)

    def test_unknown_protocol_and_release_family_rejected_before_traffic(self):
        for version, revision in [('0.3.0', 11), ('0.2.0', 10), ('0.2.0', 12)]:
            self.modern(version, revision)
            with self.assertRaisesRegex(ValueError, '不兼容'): ManualDeepSeek(self.directory/'binding.json')
        self.assertEqual(self.calls, [])

    def test_preview_is_read_only_and_confirmed_instance_is_rechecked(self):
        self.modern()
        manager = self.manager()
        preview = manager.control({'action': 'preview-deepseek-plugin', 'file': str(self.directory/'binding.json')})
        self.assertEqual(preview['profile'], 'fixture')
        self.assertNotIn('deepseekManualBinding', manager.config)
        self.assertEqual(self.calls, ['status'])
        with self.assertRaisesRegex(ValueError, '实例已改变'):
            manager.control({'action': 'bind-deepseek-plugin', 'file': preview['file'], 'instanceId': str(uuid.uuid4())})
        self.assertNotIn('deepseekManualBinding', manager.config)

    def test_bind_checks_source_identity_and_credentials_before_traffic(self):
        adapter = ManualDeepSeek(self.directory/'binding.json')
        self.assertEqual(adapter.call('status')['bindingMode'], 'manual')
        self.assertFalse(adapter.call('status')['nativeQuit'])
        self.save('connection.json', {'token': 'b'*64, 'endpoint': str(self.directory/'endpoint.json'),
                                      'instanceId': self.binding['instanceId']})
        before = len(self.calls)
        with self.assertRaisesRegex(ValueError, '凭据已改变'): adapter.call('send')
        self.assertEqual(len(self.calls), before)
        (self.package/'host.mjs').write_text('different plugin')
        with self.assertRaisesRegex(ValueError, '版本不匹配'): ManualDeepSeek(self.directory/'binding.json')

    def test_changed_instance_and_symlink_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '实例已改变'):
            ManualDeepSeek(self.directory/'binding.json', str(uuid.uuid4()))
        path = self.directory/'binding.json'; path.rename(path.with_suffix('.saved'))
        path.symlink_to(path.with_suffix('.saved'))
        with self.assertRaisesRegex(ValueError, '权限不安全'): ManualDeepSeek(path)

    def test_bind_requires_disabled_access_and_preserves_official_settings(self):
        manager = self.manager()
        manager.config.update(deepseekHome='/official-home', deepseekAdapterDirectory='/official-connector')
        manager.config['enabled']['deepseek'] = True
        with self.assertRaisesRegex(ValueError, '先仅停用'): self.bind(manager)
        self.assertEqual(self.calls, [])
        manager.config['enabled']['deepseek'] = False
        self.assertTrue(self.bind(manager)['bound'])
        self.assertEqual(manager.config['deepseekHome'], '/official-home')
        self.assertEqual(manager.config['deepseekAdapterDirectory'], '/official-connector')
        self.assertTrue(manager.adapters['deepseek'].receipts_path.is_file())
        self.assertFalse(manager.control({'action': 'unbind-deepseek-plugin'})['bound'])
        self.assertTrue((self.directory/'connection.json').is_file())

    def test_binding_closes_receipt_initialization_connection(self):
        manager = self.manager()
        original = sqlite3.connect
        opened = []
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            opened.append(connection)
            return connection
        with patch('bridge.clients.manager.sqlite3.connect', side_effect=connect):
            self.bind(manager)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute('SELECT 1')

    def test_scan_and_startup_never_launch_or_replace_manual_instance(self):
        manager = self.manager(); self.bind(manager)
        adapter = manager.adapters['deepseek']
        discovered = {p: {'id': p, 'installed': False, 'dataDirectory': '/other'} for p in ('codex','claude','deepseek')}
        with patch('bridge.clients.discovery.discover_clients', return_value=discovered), \
             patch('bridge.clients.lifecycle.inspect_clients', return_value={}), \
             patch('bridge.clients.lifecycle.launch_client') as launch:
            manager.scan()
            self.assertIs(manager.adapters['deepseek'], adapter)
            rows = manager.toggle_client({'provider': 'deepseek', 'enabled': True})['clients']
            row = next(row for row in rows if row['id']=='deepseek')
            self.assertTrue(row['connected']); self.assertFalse(row['canForceQuit'])
            manager.start_enabled()
            launch.assert_not_called()
            with self.assertRaisesRegex(ValueError, '仅支持停用'):
                manager.toggle_client({'provider': 'deepseek', 'enabled': False, 'quitDesktop': True})
            with self.assertRaisesRegex(ValueError, '不管理第三方'):
                manager.recover_deepseek({'action': 'deepseek-recovery-preview'})
        self.assertNotIn('quit', self.calls)

    def test_manual_accounts_are_read_only_and_notifications_use_same_adapter(self):
        manager = self.manager(); self.bind(manager)
        self.assertTrue(manager.client_accounts('deepseek', {})['readOnly'])
        for operation in ('save-api', 'switch', 'import'):
            with self.assertRaisesRegex(ValueError, '第三方客户端'):
                manager.client_accounts('deepseek', {'operation': operation})
        manager.config['enabled']['deepseek'] = True
        manager.notification_rows()
        self.assertIn('list', self.calls)
        self.assertFalse(any(action in self.calls for action in ('create','send','quit')))

    def test_restore_fails_closed_after_identity_change(self):
        manager = self.manager(); self.bind(manager)
        self.binding['instanceId'] = str(uuid.uuid4()); self.save('binding.json', self.binding)
        restored = DesktopSessions(self.root/'gateway', gateway_running=True)
        self.addCleanup(restored.close)
        self.assertIsNone(restored.adapters.get('deepseek'))
        with self.assertRaisesRegex(Exception, '无法验证'): restored.call('deepseek', 'list')
        self.assertIn('不会切换到其他实例', restored.deepseek_restore_error)
        with self.assertRaisesRegex(Exception, '无法验证'): restored.client_accounts('deepseek', {})


if __name__ == '__main__': unittest.main()
