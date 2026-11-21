import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.lifecycle import private_json


ROOT = Path(__file__).resolve().parents[1]


class PrivateJsonTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory/'settings.json'
        self.original = b'{"original": true}\n'
        self.path.write_bytes(self.original)

    def test_windows_session_file_replacement_uses_user_owned_native_writer(self):
        from bridge.features.auth import auth
        contents = b'{"fixture":true}'
        write_private = Mock()
        native = SimpleNamespace(write_private=write_private)
        with patch.object(auth.os, 'name', 'nt'), \
                patch.dict(sys.modules, {'bridge.platforms.windows.local_service': native}):
            auth.replace_session_file(self.path, contents)
        write_private.assert_called_once_with(self.path, contents)

    def test_windows_gateway_json_writers_use_current_user_security(self):
        from bridge.app import lifecycle
        from bridge.features.accounts import accounts
        from bridge.features.notifications import channels
        from bridge.features.updates import gateway
        from bridge.features.network import shared_relay, runtime
        value = {'fixture': '私有设置'}
        writers = [(lifecycle, lifecycle.private_json), (accounts, accounts.private_json),
                   (channels, channels.write_json), (gateway, gateway.write_json),
                   (runtime, runtime._write_status),
                   (shared_relay, lambda path, value: shared_relay.save_config(path.parent, value, name=path.name))]
        for module, write in writers:
            native = SimpleNamespace(write_private=Mock())
            with self.subTest(module=module.__name__), \
                    patch.object(module, 'os', SimpleNamespace(name='nt')), \
                    patch.dict(sys.modules, {'bridge.platforms.windows.local_service': native}):
                write(self.path, value)
                native.write_private.assert_called_once()
                target, content = native.write_private.call_args.args
                self.assertEqual(target, self.path)
                self.assertEqual(json.loads(content), value)
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_stale_and_other_writer_temporary_files_are_never_promoted_or_removed(self):
        stale = self.path.with_suffix('.tmp')
        other = self.directory/'settings.json.other-writer.tmp'
        stale.write_bytes(b'{"stale": true}')
        other.write_bytes(b'partially written')
        for value in ({'new': 1}, {'new': 2}):
            private_json(self.path, value)
            self.assertEqual(json.loads(self.path.read_text()), value)
            self.assertEqual(stale.read_bytes(), b'{"stale": true}')
            self.assertEqual(other.read_bytes(), b'partially written')
            self.assertEqual(set(self.directory.iterdir()), {self.path, stale, other})

    def test_temporary_is_private_before_writing_and_synced_before_replace(self):
        if os.name == 'nt':
            from bridge.platforms.windows import local_service as native
            create, replace, fsync = native._open, os.replace, os.fsync
            created, synced = [], []
            expected = json.dumps({'new': '私有配置'}).encode('utf-8')
            def open_private(path, **options):
                handle = create(path, **options)
                native._check(handle, directory=False)
                self.assertEqual(self.path.read_bytes(), self.original)
                created.append(path)
                return handle
            def sync(fd):
                self.assertEqual(os.fstat(fd).st_size, len(expected))
                fsync(fd)
                synced.append(True)
            def install(temporary, target):
                self.assertEqual(created, [temporary])
                self.assertEqual(synced, [True])
                self.assertEqual(temporary.read_bytes(), expected)
                self.assertEqual(target, self.path)
                self.assertEqual(self.path.read_bytes(), self.original)
                return replace(temporary, target)
            with patch.object(native, '_open', side_effect=open_private), \
                    patch.object(native.os, 'fsync', side_effect=sync), \
                    patch.object(native.os, 'replace', side_effect=install):
                private_json(self.path, {'new': '私有配置'})
            self.assertEqual(self.path.read_bytes(), expected)
            self.assertEqual(list(self.directory.iterdir()), [self.path])
            return
        dump = json.dump
        replace = Path.replace
        fsync = os.fsync
        synced = []
        def write(value, stream):
            temporary, = [p for p in self.directory.iterdir() if p != self.path]
            self.assertEqual(temporary.parent, self.path.parent)
            self.assertEqual(temporary.read_bytes(), b'')
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(os.fstat(stream.fileno()).st_mode), 0o600)
            self.assertEqual(self.path.read_bytes(), self.original)
            return dump(value, stream)
        def sync(fd):
            temporary, = [p for p in self.directory.iterdir() if p != self.path]
            self.assertEqual(json.loads(temporary.read_text()), {'new': '私有配置'})
            fsync(fd)
            synced.append(True)
        def install(temporary, target):
            self.assertEqual(synced, [True])
            self.assertEqual(target, self.path)
            self.assertEqual(self.path.read_bytes(), self.original)
            return replace(temporary, target)
        with patch('bridge.app.lifecycle.json.dump', side_effect=write), \
             patch('bridge.app.lifecycle.os.fsync', side_effect=sync), \
             patch.object(Path, 'replace', autospec=True, side_effect=install):
            private_json(self.path, {'new': '私有配置'})
        self.assertEqual(json.loads(self.path.read_text()), {'new': '私有配置'})
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_replace_failure_preserves_old_target_cleans_own_file_and_allows_retry(self):
        other = self.directory/'settings.json.other-writer.tmp'
        other.write_bytes(b'other writer')
        target = ('bridge.platforms.windows.local_service.os.replace' if os.name == 'nt'
                  else 'pathlib.Path.replace')
        with patch(target, side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                private_json(self.path, {'new': True})
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(set(self.directory.iterdir()), {self.path, other})
        private_json(self.path, {'retried': True})
        self.assertEqual(json.loads(self.path.read_text()), {'retried': True})
        self.assertEqual(other.read_bytes(), b'other writer')

    def test_serialization_or_sync_failure_does_not_replace_target(self):
        for failure in ('serialization', 'sync'):
            with self.subTest(failure=failure):
                if failure == 'serialization':
                    with self.assertRaises(TypeError):
                        private_json(self.path, {'invalid': object()})
                else:
                    with patch('bridge.app.lifecycle.os.fsync', side_effect=OSError('disk failure')):
                        with self.assertRaises(OSError):
                            private_json(self.path, {'new': True})
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_keyboard_interrupt_cleans_partial_write(self):
        def interrupt(value, stream):
            stream.write('{"partial":')
            raise KeyboardInterrupt()
        target = ('bridge.platforms.windows.local_service.os.fsync' if os.name == 'nt'
                  else 'bridge.app.lifecycle.json.dump')
        failure = KeyboardInterrupt() if os.name == 'nt' else interrupt
        with patch(target, side_effect=failure):
            with self.assertRaises(KeyboardInterrupt):
                private_json(self.path, {'new': True})
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_process_interruption_leaves_evidence_but_does_not_block_future_writes(self):
        script = '''
import os
import sys
from pathlib import Path
from unittest.mock import patch
from bridge.app.lifecycle import private_json
def interrupt(value, stream):
    stream.write('{"interrupted":')
    stream.flush()
    os._exit(17)
if os.name == 'nt':
    from bridge.platforms.windows import local_service
    def interrupt_sync(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, b'{"interrupted":')
        os._exit(17)
    with patch.object(local_service.os, 'fsync', side_effect=interrupt_sync):
        private_json(Path(sys.argv[1]), {'new': True})
else:
    with patch('bridge.app.lifecycle.json.dump', side_effect=interrupt):
        private_json(Path(sys.argv[1]), {'new': True})
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(self.path)], cwd=ROOT,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr.decode())
        self.assertEqual(self.path.read_bytes(), self.original)
        orphan, = [p for p in self.directory.iterdir() if p != self.path]
        self.assertEqual(orphan.read_bytes(), b'{"interrupted":')
        private_json(self.path, {'recovered': True})
        self.assertEqual(json.loads(self.path.read_text()), {'recovered': True})
        self.assertEqual(orphan.read_bytes(), b'{"interrupted":')
