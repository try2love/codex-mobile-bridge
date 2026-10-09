"""Regressions for a desktop update removing the gateway's cached runtime."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.features.sessions import create
from bridge.clients.codex.catalog import Catalog, CatalogError
from bridge.app.service import Bridge

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.tmp').mkdir(exist_ok=True)


class RuntimeRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='runtime 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root / 'old' / 'codex.exe'
        self.new = self.root / 'new' / 'codex.exe'
        for executable in (self.old, self.new):
            executable.parent.mkdir()
            executable.touch()

    def test_automatic_runtime_refreshes_only_after_disappearance(self):
        with patch.object(Catalog, 'find_runtime', side_effect=[self.old, self.new]) as find:
            reader = Catalog(self.root)
            self.assertEqual(reader.executable, self.old)
            self.assertEqual(find.call_count, 1)
            self.old.unlink()
            self.assertEqual(reader.executable, self.new)
            self.assertEqual(reader.executable, self.new)
            self.assertEqual(find.call_count, 2)

    def test_runtime_installed_after_gateway_start_is_discovered(self):
        with patch.object(Catalog, 'find_runtime', side_effect=[None, self.new]):
            reader = Catalog(self.root)
            self.assertEqual(reader.executable, self.new)

    def test_explicit_runtime_is_not_silently_replaced(self):
        self.old.unlink()
        with patch.object(Catalog, 'find_runtime') as find:
            self.assertEqual(Catalog(self.root, self.old).executable, self.old)
            find.assert_not_called()

    def bridge(self):
        bridge = Bridge(self.root, self.root / 'data')
        self.addCleanup(bridge.close)
        bridge.hosts.projects = lambda: [{'key': 'local|p', 'host': 'local', 'cwd': str(self.root)}]
        bridge.ipc.connect = lambda: None
        return bridge

    def test_same_gateway_creates_once_after_runtime_update(self):
        tid, request_id = str(uuid.uuid4()), str(uuid.uuid4())
        script = self.root / 'rpc.py'
        log = self.root / 'calls.json'
        script.write_text('''import json, sys
from pathlib import Path
calls = []
for line in sys.stdin:
    request = json.loads(line)
    calls.append(request['method'])
    if 'id' in request:
        result = {'thread': {'id': ''' + repr(tid) + '''}} if request['method'] == 'thread/start' else {}
        print(json.dumps({'id': request['id'], 'result': result}), flush=True)
Path(''' + repr(str(log)) + ''').write_text(json.dumps(calls), encoding='utf-8')
''', encoding='utf-8')
        real_popen = subprocess.Popen
        def launch(argv, **kwargs):
            self.assertEqual(argv[0], str(self.new))
            return real_popen([sys.executable, str(script)], **kwargs)
        with patch.object(Catalog, 'find_runtime', return_value=self.old) as find:
            bridge = self.bridge()
            self.old.unlink()
            find.return_value = self.new
            with patch('bridge.features.sessions.create.subprocess.Popen', side_effect=launch) as popen, patch('bridge.app.service.open_in_desktop'):
                first = bridge.create_chat('local|p', 'Windows test', request_id)
                second = bridge.create_chat('local|p', 'Windows test', request_id)
            self.assertEqual(first['id'], tid)
            self.assertEqual(second['id'], tid)
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(json.loads(log.read_text(encoding='utf-8')).count('thread/start'), 1)

    def test_failed_spawn_can_retry_same_request_without_uncertain_ledger(self):
        with patch.object(Catalog, 'find_runtime', return_value=self.new):
            bridge = self.bridge()
        for error in (FileNotFoundError(2, 'missing'), PermissionError(13, 'denied')):
            with self.subTest(error=type(error).__name__):
                request_id = str(uuid.uuid4())
                with patch('bridge.features.sessions.create.subprocess.Popen', side_effect=error):
                    with self.assertRaises(create.CreationError):
                        bridge.create_chat('local|p', 'Retry', request_id)
                self.assertNotIn(request_id, bridge.creations)
                self.assertNotIn(request_id, json.loads(bridge.creations_path.read_text(encoding='utf-8')))
                tid = str(uuid.uuid4())
                with patch('bridge.app.service.create_empty', return_value=tid), patch('bridge.app.service.open_in_desktop'):
                    self.assertEqual(bridge.create_chat('local|p', 'Retry', request_id)['id'], tid)

    def test_catalog_spawn_failure_is_actionable_and_hides_console(self):
        with patch('bridge.clients.codex.catalog.subprocess.Popen', side_effect=FileNotFoundError(2, 'missing')) as popen:
            with self.assertRaisesRegex(CatalogError, 'Codex'):
                Catalog(self.root, self.old)._fetch(str(self.root))
        if os.name == 'nt':
            self.assertEqual(popen.call_args.kwargs.get('creationflags'), subprocess.CREATE_NO_WINDOW)

    def test_fork_probe_missing_runtime_is_definitely_unavailable(self):
        with patch('bridge.features.sessions.create.subprocess.run', side_effect=FileNotFoundError(2, 'missing')):
            with self.assertRaises(create.ForkUnavailable):
                create.fork_copy(self.old, self.root, str(self.root), str(uuid.uuid4()), 'turn', 'fork', {})
