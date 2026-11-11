"""Synthetic target filesystem and mocked pidfds. No real CLI or signals."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import tempfile
import threading
import unittest
import uuid
from unittest.mock import MagicMock, patch

from bridge.platforms.linux import codex_cli_control as control
from bridge.platforms.linux import codex_daemon_env as identity


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'synthetic POSIX filesystem')
class LinuxCliControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='lcc-', dir=ROOT/'.tmp')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.home = self.root/'h'
        self.home.mkdir(mode=0o700)
        self.state = self.home/'app-server-daemon'
        self.state.mkdir(mode=0o700)
        (self.state/'daemon.lock').touch(mode=0o600)
        self.image = self.root/'codex'
        self.image.write_bytes(b'Synthetic image, never executed')
        self.proc = self.root/'proc'
        (self.proc/'sys/kernel/random').mkdir(parents=True)
        self.boot = str(uuid.uuid4())
        (self.proc/'sys/kernel/random/boot_id').write_text(self.boot)
        self.pid, self.ticks = 34567, 1234
        self.process = self.proc/str(self.pid)
        self.process.mkdir()
        (self.process/'exe').symlink_to(self.image)
        (self.process/'status').write_text('Uid:\t' + '\t'.join([str(os.getuid())]*4) + '\n')
        (self.process/'cmdline').write_bytes(b'codex\0app-server\0--managed-daemon\0')
        self.write_birth(self.ticks)
        self.write_environment()
        (self.state/'daemon.pid').write_text(json.dumps({'pid': self.pid,
            'processIdentity': {'bootId': self.boot, 'startTicks': self.ticks}}))
        (self.state/'daemon.pid').chmod(0o600)
        folder = self.home/'app-server-control'
        folder.mkdir(mode=0o700)
        self.socket = folder/'app-server-control.sock'
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        previous = Path.cwd()
        os.chdir(ROOT)
        try:
            server.bind(str(self.socket.relative_to(ROOT)))
        finally:
            os.chdir(previous)
        self.socket.chmod(0o600)
        self.addCleanup(patch.stopall)
        patch.object(control, 'PROC', self.proc).start()
        real_path = Path
        def target_path(value):
            text = str(value)
            if text == '/proc' or text.startswith('/proc/'):
                return self.proc / text[len('/proc'):].lstrip('/')
            return real_path(value)
        patch.object(identity, 'Path', side_effect=target_path).start()
        patch.object(control.sys, 'platform', 'linux').start()
        patch.object(control, '_capable', return_value=True).start()
        self.peer = patch.object(control, '_peer').start()
        self.fd = 987654
        self.pidfd = patch.object(control.os, 'pidfd_open', return_value=self.fd, create=True).start()
        original_close = os.close
        patch.object(control.os, 'close', side_effect=lambda fd: None if fd == self.fd else original_close(fd)).start()
        self.send = patch.object(control.signal, 'pidfd_send_signal', create=True).start()
        self.wait = patch.object(control.select, 'select', return_value=([], [], [])).start()
        self.expected = self.inspect()['generation']

    def write_birth(self, ticks, state='S'):
        fields = [state] + ['0']*18 + [str(ticks)]
        (self.process/'stat').write_text(f'{self.pid} (codex fixture) ' + ' '.join(fields))

    def write_environment(self, home=None, extra=b''):
        (self.process/'environ').write_bytes(b'CODEX_HOME=' + os.fsencode(home or self.home) + b'\0' + extra)

    def inspect(self, version='0.164.0'):
        return control.inspect(self.home, self.image, self.socket, version)

    def dispatch(self, request_id=None, mode='graceful', expected=None):
        return control.dispatch(self.home, request_id or str(uuid.uuid4()), mode, expected or self.expected)

    def actual_signals(self):
        return [call.args[1] for call in self.send.call_args_list if call.args[1] != 0]

    def test_inspection_has_only_identity_and_creates_no_maintenance_files(self):
        self.write_environment(extra=b'OPENAI_API_KEY=synthetic-private\0UNRELATED=private\0')
        result = self.inspect()
        self.assertTrue(result['canRelease'])
        self.assertTrue(result['canForce'])
        self.assertEqual(result['generation'], self.expected)
        self.assertNotIn('private', json.dumps(result))
        self.assertFalse((self.home/'bridge-cli-maintenance').exists())
        self.send.assert_not_called()
        self.peer.assert_called_with(self.socket, self.pid)

    def test_legacy_record_identity_and_private_socket_layout(self):
        record = self.state/'daemon.pid'
        value = json.loads(record.read_text())
        value['linuxProcessIdentity'] = value.pop('processIdentity')
        record.rename(self.state/'app-server.pid')
        (self.state/'app-server.pid').write_text(json.dumps(value))
        self.assertEqual(self.inspect()['generation'], self.expected)

    def test_wrong_boot_birth_home_user_image_and_native_role_reject(self):
        record = self.state/'daemon.pid'
        original = record.read_text()
        value = json.loads(original)
        value['processIdentity']['bootId'] = str(uuid.uuid4())
        record.write_text(json.dumps(value))
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        record.write_text(original)
        self.write_birth(self.ticks + 1)
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        self.write_birth(self.ticks)
        self.write_environment(self.root)
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        self.write_environment()
        (self.process/'status').write_text('Uid:\t1\t1\t1\t1\n')
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        (self.process/'status').write_text('Uid:\t'+'\t'.join([str(os.getuid())]*4)+'\n')
        with self.assertRaises((control.ControlError, OSError)):
            control.inspect(self.home, __file__, self.socket, '0.164.0')
        (self.process/'cmdline').write_bytes(b'codex\0app-server\0')
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        self.assertEqual(self.actual_signals(), [])

    def test_native_record_and_control_socket_permissions_or_symlinks_reject(self):
        record = self.state/'daemon.pid'
        record.chmod(0o666)
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        record.chmod(0o600)
        self.socket.chmod(0o666)
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        self.socket.chmod(0o600)
        self.peer.side_effect = control.ControlError('peer changed')
        with self.assertRaises((control.ControlError, OSError)):
            self.inspect()
        self.peer.side_effect = None
        content = record.read_text()
        record.unlink()
        original = self.root/'record'
        original.write_text(content)
        record.symlink_to(original)
        with self.assertRaises(OSError):
            self.inspect()
        self.assertFalse((self.home/'bridge-cli-maintenance').exists())

    def test_live_process_scheduling_state_change_is_not_a_generation_change(self):
        original = identity._identity
        calls = 0
        def changing(pid):
            nonlocal calls
            calls += 1
            self.write_birth(self.ticks, 'R' if calls % 2 else 'S')
            return original(pid)
        with patch.object(identity, '_identity', side_effect=changing):
            self.assertEqual(self.inspect()['generation'], self.expected)
            identifier = str(uuid.uuid4())
            self.dispatch(identifier)
            self.assertEqual(control.check(self.home, identifier, self.expected)['status'], 'requested')

    def native_alias(self):
        private = self.root/('codex-daemon-' + str(os.getuid()))
        private.mkdir(mode=0o700)
        target = private/hashlib.sha256(os.fsencode(self.socket)).hexdigest()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        previous = Path.cwd()
        os.chdir(ROOT)
        try:
            server.bind(str(target.relative_to(ROOT)))
        finally:
            os.chdir(previous)
        target.chmod(0o600)
        self.socket.unlink()
        self.socket.symlink_to(target)
        return target

    def test_native_alias_returns_canonical_socket_identity(self):
        target = self.native_alias()
        real_path = Path
        with patch.object(control, 'Path', side_effect=lambda value: self.root if str(value) == '/tmp' else real_path(value)):
            snapshot = self.inspect()
            self.assertEqual(snapshot['generation']['socket'], str(target))
            self.assertEqual(snapshot['generation']['socketInode'], target.stat().st_ino)
            self.peer.assert_called_with(target, self.pid)
            self.assertEqual(control.inspect(self.home, self.image, target, '0.164.0'), snapshot)
            self.dispatch(expected=snapshot['generation'])
            self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_native_alias_requires_exact_protected_path_and_private_directory(self):
        target = self.native_alias()
        real_path = Path
        with patch.object(control, 'Path', side_effect=lambda value: self.root if str(value) == '/tmp' else real_path(value)):
            target.parent.chmod(0o755)
            with self.assertRaises(control.ControlError):
                self.inspect()
            target.parent.chmod(0o700)
            self.socket.unlink()
            with self.assertRaises(OSError):
                control.inspect(self.home, self.image, target, '0.164.0')
            wrong = target.with_name('another-home')
            self.socket.symlink_to(wrong)
            with self.assertRaises(control.ControlError):
                control.inspect(self.home, self.image, wrong, '0.164.0')
            self.assertEqual(self.actual_signals(), [])

    def test_alias_foreign_target_multihop_and_change_during_peer_reject(self):
        target = self.native_alias()
        real_path = Path
        def redirect(_value):
            return self.root if str(_value) == '/tmp' else real_path(_value)
        with patch.object(control, 'Path', side_effect=redirect):
            self.socket.unlink()
            self.socket.symlink_to(self.root/'foreign.sock')
            with self.assertRaises(control.ControlError):
                self.inspect()
            self.socket.unlink()
            self.socket.symlink_to(target)
            target.rename(target.with_name('actual.sock'))
            target.symlink_to(target.with_name('actual.sock'))
            with self.assertRaises(control.ControlError):
                self.inspect()
            target.unlink()
            target.with_name('actual.sock').rename(target)
            def replace_alias(*_):
                self.socket.unlink()
                self.socket.symlink_to(self.root/'foreign.sock')
            self.peer.side_effect = replace_alias
            with self.assertRaises(control.ControlError):
                self.inspect()

    def test_socket_replacement_during_peer_query_is_rejected(self):
        def replace_socket(*_):
            self.socket.rename(self.socket.with_name('old.sock'))
            self.socket.touch(mode=0o600)
        self.peer.side_effect = replace_socket
        with self.assertRaises(control.ControlError):
            self.inspect()
        self.assertEqual(self.actual_signals(), [])

    def test_corrupt_or_cross_scope_receipt_prevents_a_new_signal(self):
        identifier = str(uuid.uuid4())
        self.dispatch(identifier)
        path = self.home/'bridge-cli-maintenance/linux/requests.json'
        values = json.loads(path.read_text())
        values[identifier]['generation']['home'] = str(self.root)
        path.write_text(json.dumps(values))
        with self.assertRaises(control.ControlError):
            self.dispatch(str(uuid.uuid4()), mode='force')
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_wrong_confirmation_and_other_home_never_signal(self):
        wrong = {**self.expected, 'startTicks': self.ticks + 1}
        with self.assertRaises(control.ControlError):
            self.dispatch(expected=wrong)
        other = self.root/'other'
        other.mkdir(mode=0o700)
        with self.assertRaises(control.ControlError):
            control.dispatch(other, str(uuid.uuid4()), 'force', self.expected)
        self.assertEqual(self.actual_signals(), [])
        self.assertFalse((other/'bridge-cli-maintenance').exists())

    def test_graceful_once_then_explicit_force_once_and_duplicate_ids(self):
        identifier = str(uuid.uuid4())
        first = self.dispatch(identifier)
        self.assertEqual(first['status'], 'requested')
        self.assertFalse(first['duplicate'])
        self.assertTrue(self.dispatch(identifier)['duplicate'])
        duplicate_id = str(uuid.uuid4())
        duplicate = self.dispatch(duplicate_id)
        self.assertEqual(duplicate['canonicalRequestId'], identifier)
        with self.assertRaises(control.ControlError):
            self.dispatch(identifier, mode='force')
        force_id = str(uuid.uuid4())
        forced = self.dispatch(force_id, mode='force')
        self.assertEqual(forced['mode'], 'force')
        self.dispatch(str(uuid.uuid4()), mode='force')
        self.assertEqual(self.actual_signals(), [signal.SIGHUP, signal.SIGKILL])
        control.check(self.home, identifier, self.expected)
        self.assertEqual(self.actual_signals(), [signal.SIGHUP, signal.SIGKILL])

    def test_force_can_use_verified_identity_without_auth_or_known_version(self):
        unknown = self.inspect(version=None)['generation']
        with self.assertRaises(control.ControlError):
            self.dispatch(expected=unknown)
        self.write_environment(extra=b'OPENAI_API_KEY=synthetic\0OPENAI_BASE_URL=https://fixture.invalid\0')
        self.dispatch(mode='force', expected=unknown)
        self.assertEqual(self.actual_signals(), [signal.SIGKILL])

    def test_new_socket_or_version_cannot_repeat_the_same_process_signal(self):
        identifier = str(uuid.uuid4())
        self.dispatch(identifier)
        refreshed = {**self.expected, 'socketInode': self.expected['socketInode'] + 1, 'nativeVersion': '0.167.0'}
        result = self.dispatch(expected=refreshed)
        self.assertEqual(result['canonicalRequestId'], identifier)
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_lost_result_after_send_only_checks_persisted_unknown(self):
        identifier = str(uuid.uuid4())
        save = control._save
        calls = 0
        def lose_result(directory, records):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('synthetic result-write failure')
            save(directory, records)
        with patch.object(control, '_save', side_effect=lose_result):
            with self.assertRaises(OSError):
                self.dispatch(identifier)
        result = self.dispatch(identifier)
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['signalSent'])
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])
        self.write_birth(self.ticks + 1)
        self.assertEqual(control.check(self.home, identifier, self.expected)['status'], 'exited')
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_intent_durability_failure_prevents_signal_and_retry(self):
        identifier = str(uuid.uuid4())
        save = control._save
        def fail_after_commit(directory, records):
            save(directory, records)
            raise OSError('synthetic directory-fsync failure')
        with patch.object(control, '_save', side_effect=fail_after_commit):
            with self.assertRaises(OSError):
                self.dispatch(identifier)
        self.assertEqual(self.actual_signals(), [])
        self.assertEqual(self.dispatch(identifier)['status'], 'unknown')
        self.assertEqual(self.actual_signals(), [])

    def test_signal_failure_is_unknown_and_never_automatically_retried(self):
        identifier = str(uuid.uuid4())
        self.send.side_effect = lambda _fd, sig, *_: (_ for _ in ()).throw(PermissionError()) if sig else None
        result = self.dispatch(identifier)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['errorType'], 'PermissionError')
        self.dispatch(identifier)
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_check_missing_receipt_is_read_only_and_permission_failure_stays_unknown(self):
        identifier = str(uuid.uuid4())
        self.assertEqual(control.check(self.home, identifier, self.expected)['status'], 'notFound')
        self.assertFalse((self.home/'bridge-cli-maintenance').exists())
        self.dispatch(identifier)
        with patch.object(control, '_identity', side_effect=PermissionError):
            self.assertEqual(control.check(self.home, identifier, self.expected)['status'], 'requested')
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_check_exited_or_previous_boot_does_not_require_a_live_socket(self):
        identifier = str(uuid.uuid4())
        self.dispatch(identifier)
        self.socket.unlink()
        (self.proc/'sys/kernel/random/boot_id').write_text(str(uuid.uuid4()))
        self.assertEqual(control.check(self.home, identifier, self.expected)['status'], 'exited')
        self.peer.reset_mock()
        self.dispatch(identifier)
        self.peer.assert_not_called()
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_native_lock_or_parallel_dispatch_cannot_double_signal(self):
        import fcntl
        identifier = str(uuid.uuid4())
        with (self.state/'daemon.lock').open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.dispatch(identifier)
        reached, finish = threading.Event(), threading.Event()
        def hold_signal(_fd, value, *_):
            if value:
                reached.set()
                self.assertTrue(finish.wait(2))
        self.send.side_effect = hold_signal
        errors = []
        def first():
            try:
                self.dispatch(identifier)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=first)
        thread.start()
        try:
            self.assertTrue(reached.wait(2))
            with self.assertRaises(BlockingIOError):
                self.dispatch(str(uuid.uuid4()))
        finally:
            finish.set()
            thread.join(3)
        self.assertFalse(errors)
        self.dispatch(identifier)
        self.assertEqual(self.actual_signals(), [signal.SIGHUP])

    def test_json_interface_has_no_start_or_unrestricted_operations(self):
        request = {'action': 'inspect', 'home': str(self.home), 'executable': str(self.image),
                   'socket': str(self.socket), 'nativeVersion': '0.164.0'}
        self.assertEqual(control.run(request)['generation'], self.expected)
        for action in ('start', 'restart', 'stop', 'execute'):
            with self.assertRaises(control.ControlError):
                control.run({**request, 'action': action})
        with self.assertRaises(control.ControlError):
            control.run({**request, 'environment': {'OPENAI_API_KEY': 'synthetic'}})


@unittest.skipUnless(os.name == 'posix', 'POSIX peer identity')
class LinuxPeerCredentialTests(unittest.TestCase):
    def test_raw_socket_peer_checks_pid_and_uid_without_sending_bytes(self):
        channel = MagicMock()
        channel.__enter__.return_value = channel
        channel.getsockopt.return_value = struct.pack('3i', 123, os.getuid(), os.getgid())
        with patch.object(control.socket, 'SO_PEERCRED', 17, create=True), \
                patch.object(control.socket, 'socket', return_value=channel):
            control._peer(Path('/tmp/fixture.sock'), 123)
            channel.send.assert_not_called()
            channel.sendall.assert_not_called()
            channel.getsockopt.return_value = struct.pack('3i', 124, os.getuid(), os.getgid())
            with self.assertRaises(control.ControlError):
                control._peer(Path('/tmp/fixture.sock'), 123)
            channel.getsockopt.return_value = struct.pack('3i', 123, os.getuid()+1, os.getgid())
            with self.assertRaises(control.ControlError):
                control._peer(Path('/tmp/fixture.sock'), 123)


if __name__ == '__main__':
    unittest.main()
