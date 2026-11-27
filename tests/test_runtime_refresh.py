"""Regressions for a desktop update removing the gateway's cached runtime."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.sessions import create
from bridge.clients.codex.catalog import Catalog, CatalogError
from bridge.clients.codex import catalog as catalog_module
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

    def test_known_installation_update_follows_automatic_runtime_without_clearing_model_caches(self):
        with patch.object(Catalog, 'find_runtime', return_value=self.old) as find:
            reader = Catalog(self.root)
            memory = {'models': ['fixture']}
            reader.cache['cached'] = memory
            reader.kind_caches['models']['cached'] = memory
            reader.model_provider_aliases['cached'] = 'openai'
            timer = reader.model_timers['cached'] = Mock()
            reader.model_store.write('cached', memory)
            persisted = reader.model_store.read('cached')
            self.assertTrue(reader.follow_runtime(self.old, self.new))
            self.assertTrue(self.old.is_file())
            self.assertEqual(reader.executable, self.new)
            self.assertTrue(reader._automatic_runtime)
            self.assertIs(reader.cache['cached'], memory)
            self.assertIs(reader.kind_caches['models']['cached'], memory)
            self.assertEqual(reader.model_provider_aliases['cached'], 'openai')
            self.assertEqual(reader.model_store.read('cached'), persisted)
            timer.cancel.assert_not_called()
            find.assert_called_once_with()

    def test_runtime_follow_requires_the_same_automatic_selection_and_an_existing_target(self):
        with patch.object(Catalog, 'find_runtime', return_value=self.old):
            reader = Catalog(self.root)
        self.assertFalse(reader.follow_runtime(self.new, self.new))
        self.assertFalse(reader.follow_runtime(self.old, self.root/'missing'))
        self.assertFalse(reader.follow_runtime(self.old, None))
        self.assertEqual(reader.executable, self.old)
        explicit = Catalog(self.root, self.old)
        self.assertFalse(explicit.follow_runtime(self.old, self.new))
        self.assertEqual(explicit.executable, self.old)
        with patch.object(Catalog, 'find_runtime', return_value=None):
            absent = Catalog(self.root)
        self.assertTrue(absent.follow_runtime(None, self.new))
        self.assertEqual(absent.executable, self.new)

    def test_inherited_runtime_is_dynamic_until_an_explicit_selection(self):
        current = [self.old]
        with patch.object(Catalog, 'find_runtime') as find:
            reader = Catalog(self.root, executable_getter=lambda: current[0])
            self.assertEqual(reader.executable, self.old)
            current[0] = str(self.new)
            self.assertEqual(reader.executable, self.new)
            current[0] = None
            self.assertIsNone(reader.executable)
            reader.executable = self.old
            current[0] = self.new
            self.assertEqual(reader.executable, self.old)
            self.assertFalse(reader.follow_runtime(self.old, self.new))
            find.assert_not_called()

    def test_failed_rediscovery_keeps_an_existing_runtime_and_model_cache(self):
        with patch.object(Catalog, 'find_runtime', side_effect=[self.old, None]), \
                patch.object(Catalog, '_windows_runtime_stamp', side_effect=[('old',), ('changed',), ('changed',)]):
            reader = Catalog(self.root)
            cached = {'models': ['kept']}
            reader.cache['catalog'] = cached
            self.assertEqual(reader.executable, self.old)
            self.assertIs(reader.cache['catalog'], cached)

    def test_unreadable_installation_target_keeps_the_current_runtime(self):
        with patch.object(Catalog, 'find_runtime', return_value=self.old):
            reader = Catalog(self.root)
        with patch.object(Path, 'is_file', side_effect=PermissionError('updater in progress')):
            self.assertFalse(reader.follow_runtime(self.old, self.new))
        self.assertEqual(reader.executable, self.old)

    def test_windows_version_root_change_refreshes_automatic_runtime_while_old_file_survives(self):
        local = self.root/'Local'
        root = local/'OpenAI/Codex/bin'
        old, new = root/'old/codex.exe', root/'new/codex.exe'
        old.parent.mkdir(parents=True); old.touch()
        windows = SimpleNamespace(name='nt', environ={'LOCALAPPDATA': str(local)})
        with patch.object(catalog_module, 'os', windows), \
                patch.object(Catalog, 'find_runtime', side_effect=[old, new]) as find:
            reader = Catalog(self.root)
            for _ in range(3):
                self.assertEqual(reader.executable, old)
            find.assert_called_once_with()
            before = root.stat()
            new.parent.mkdir(); new.touch()
            os.utime(root, ns=(before.st_atime_ns, before.st_mtime_ns+1_000_000))
            self.assertEqual(reader.executable, new)
            self.assertTrue(old.is_file())
            self.assertEqual(reader.executable, new)
            self.assertEqual(find.call_count, 2)

    def test_windows_root_watch_does_not_rescan_other_or_explicit_runtimes(self):
        local = self.root/'Local'; root = local/'OpenAI/Codex/bin'
        root.mkdir(parents=True)
        windows = SimpleNamespace(name='nt', environ={'LOCALAPPDATA': str(local)})
        with patch.object(catalog_module, 'os', windows), \
                patch.object(Catalog, 'find_runtime', return_value=self.old) as find:
            automatic = Catalog(self.root)
            selected = root/'selected/codex.exe'
            selected.parent.mkdir(); selected.touch()
            explicit = Catalog(self.root, selected)
            (root/'later').mkdir()
            for _ in range(3):
                self.assertEqual(automatic.executable, self.old)
                self.assertEqual(explicit.executable, selected)
            find.assert_called_once_with()

    def test_windows_version_root_change_uses_existing_local_discovery_without_powershell(self):
        local = self.root/'Local'; root = local/'OpenAI/Codex/bin'
        old, new = root/'old/codex.exe', root/'new/codex.exe'
        old.parent.mkdir(parents=True); old.touch()
        windows = SimpleNamespace(name='nt', environ={'LOCALAPPDATA': str(local)})
        with patch.object(catalog_module, 'os', windows), \
                patch.dict('os.environ', windows.environ), \
                patch.object(catalog_module, 'sys', SimpleNamespace(platform='win32')), \
                patch.object(catalog_module.subprocess, 'run', side_effect=AssertionError('No PowerShell')) as run:
            reader = Catalog(self.root)
            self.assertEqual(reader.executable, old)
            before = root.stat()
            new.parent.mkdir(); new.touch()
            latest = old.stat().st_mtime_ns+1_000_000_000
            os.utime(new, ns=(latest, latest))
            os.utime(root, ns=(before.st_atime_ns, before.st_mtime_ns+1_000_000))
            self.assertEqual(reader.executable, new)
            self.assertTrue(old.is_file())
            run.assert_not_called()

    def test_windows_empty_version_directory_is_rechecked_when_its_runtime_arrives(self):
        local = self.root/'Local'; root = local/'OpenAI/Codex/bin'
        old, new = root/'old/codex.exe', root/'new/codex.exe'
        old.parent.mkdir(parents=True); old.touch()
        windows = SimpleNamespace(name='nt', environ={'LOCALAPPDATA': str(local)})
        with patch.object(catalog_module, 'os', windows), \
                patch.dict('os.environ', windows.environ), \
                patch.object(catalog_module, 'sys', SimpleNamespace(platform='win32')), \
                patch.object(Catalog, 'find_runtime', wraps=Catalog.find_runtime) as find, \
                patch.object(catalog_module.subprocess, 'run', side_effect=AssertionError('No PowerShell')) as run:
            reader = Catalog(self.root)
            self.assertEqual(reader.executable, old)
            new.parent.mkdir()
            self.assertEqual(reader.executable, old)
            self.assertEqual(find.call_count, 2)
            before = root.stat()
            new.touch()
            latest = old.stat().st_mtime_ns+1_000_000_000
            os.utime(new, ns=(latest, latest))
            self.assertEqual(root.stat(), before)
            self.assertEqual(reader.executable, new)
            self.assertEqual(reader.executable, new)
            self.assertEqual(find.call_count, 3)
            self.assertTrue(old.is_file())
            new.write_bytes(b'replaced runtime')
            # A rewrite resets mtime; keep the new version newer than the old candidate.
            os.utime(new, ns=(latest+1_000_000_000, latest+1_000_000_000))
            self.assertEqual(root.stat(), before)
            self.assertEqual(reader.executable, new)
            self.assertEqual(reader.executable, new)
            self.assertEqual(find.call_count, 4)
            run.assert_not_called()

    def test_windows_runtime_query_errors_preserve_the_previous_stamp_runtime_and_caches(self):
        local = self.root/'Local'; root = local/'OpenAI/Codex/bin'
        old = root/'old/codex.exe'; old.parent.mkdir(parents=True); old.touch()
        windows = SimpleNamespace(name='nt', environ={'LOCALAPPDATA': str(local)})
        real_stat = Path.stat
        for location in ('root', 'runtime', 'listing'):
            for error in (PermissionError('denied'), OSError('query failed')):
                with self.subTest(location=location, error=type(error).__name__), \
                        patch.object(catalog_module, 'os', windows), \
                        patch.object(Catalog, 'find_runtime', side_effect=[old, self.new]) as find:
                    reader = Catalog(self.root)
                    cached = {'models': ['fixture']}
                    reader.cache['cached'] = cached
                    reader.kind_caches['models']['cached'] = cached
                    reader.model_store.write('cached', cached)
                    persisted = reader.model_store.read('cached')
                    stamp = reader._runtime_directory_stamp
                    def stat(path, *args, **kwargs):
                        if path == (root if location == 'root' else old):
                            raise error
                        return real_stat(path, *args, **kwargs)
                    target = 'iterdir' if location == 'listing' else 'stat'
                    failure = Mock(side_effect=error) if location == 'listing' else stat
                    with patch.object(Path, target, failure):
                        self.assertEqual(reader.executable, old)
                    self.assertIs(reader._runtime_directory_stamp, stamp)
                    self.assertIs(reader.cache['cached'], cached)
                    self.assertIs(reader.kind_caches['models']['cached'], cached)
                    self.assertEqual(reader.model_store.read('cached'), persisted)
                    find.assert_called_once_with()

    def test_windows_first_runtime_stamp_permission_error_keeps_the_discovered_runtime(self):
        local = self.root/'Local'; root = local/'OpenAI/Codex/bin'
        old = root/'old/codex.exe'; old.parent.mkdir(parents=True); old.touch()
        windows = SimpleNamespace(name='nt', environ={'LOCALAPPDATA': str(local)})
        real_stat = Path.stat
        def stat(path, *args, **kwargs):
            if path == root:
                raise PermissionError('initial directory query denied')
            return real_stat(path, *args, **kwargs)
        with patch.object(catalog_module, 'os', windows), \
                patch.object(Catalog, 'find_runtime', return_value=old) as find, \
                patch.object(Path, 'stat', stat):
            reader = Catalog(self.root)
            self.assertIsNone(reader._runtime_directory_stamp)
            self.assertEqual(reader.executable, old)
            find.assert_called_once_with()

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
            # Capability discovery has its own subprocess; this regression counts
            # creation runtimes and native thread/start calls only.
            with patch('bridge.features.sessions.create._native_project_support', return_value=False), \
                    patch('bridge.features.sessions.create.subprocess.Popen', side_effect=launch) as popen, \
                    patch('bridge.app.service.open_in_desktop'):
                first = bridge.create_chat('local|p', 'Windows test', request_id)
                second = bridge.create_chat('local|p', 'Windows test', request_id)
            self.assertEqual(first['id'], tid)
            self.assertEqual(second['id'], tid)
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(json.loads(log.read_text(encoding='utf-8')).count('thread/start'), 1)

    def test_same_gateway_uses_known_new_runtime_once_while_old_runtime_remains(self):
        with patch.object(Catalog, 'find_runtime', return_value=self.old):
            bridge = self.bridge()
        self.assertTrue(bridge.catalog_reader.follow_runtime(self.old, self.new))
        tid, request_id = str(uuid.uuid4()), str(uuid.uuid4())
        with patch('bridge.app.service.create_empty', return_value=tid) as create_empty, \
                patch('bridge.app.service.open_in_desktop'):
            self.assertEqual(bridge.create_chat('local|p', 'Updated runtime', request_id)['id'], tid)
            self.assertEqual(bridge.create_chat('local|p', 'Updated runtime', request_id)['id'], tid)
        self.assertTrue(self.old.is_file())
        create_empty.assert_called_once()
        self.assertEqual(create_empty.call_args.args[0], self.new)

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
