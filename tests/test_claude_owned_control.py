import copy
import json
import os
from pathlib import Path
import tempfile
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from bridge.app.lifecycle import private_json
from bridge.clients.claude import owned_control
from bridge.clients.claude.cli_session import ClaudeCli, _Owned, owned_connections
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions
from bridge.features.sessions.cli_release import CliConnections


class NativeFixture:
    def __init__(self):
        self.current = {'pid': 412, 'parent': 20, 'uid': os.getuid(), 'ruid': os.getuid(),
                        'image': '/fixture/native-claude', 'birth': (34, 56), 'started': (78, 90), 'zombie': False}
        self.terminate = Mock(side_effect=self.exit)
        self.open = Mock(side_effect=lambda identity: copy.deepcopy(identity))
        self.close = Mock()

    def identity(self, pid):
        if self.current is None:
            raise ProcessLookupError('original process is gone')
        return copy.deepcopy(self.current)

    def exit(self, handle):
        self.current = None


@unittest.skipIf(os.name == 'nt', 'macOS owned processes require POSIX identity and private file modes')
class OwnedControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root/'receipts'
        self.native = NativeFixture()
        self.patch = patch.object(owned_control, 'Native', return_value=self.native)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.platform = patch.object(owned_control.sys, 'platform', 'darwin')
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.token, self.sid = str(uuid.uuid4()), str(uuid.uuid4())
        self.profile = {'executable': self.native.current['image'], 'directory': str(self.root/'profile'),
                        'device': 1, 'inode': 2, 'uid': os.getuid()}
        self.process = Mock(pid=412)
        self.process.poll.return_value = None

    def capture(self, catalog=None):
        return owned_control.OwnedControl.capture(self.process, self.token, 'fixture-profile', self.sid,
            str(self.root/'project'), self.profile, catalog or {}, self.directory)

    def row(self, control):
        return {key: control.binding[key] for key in ('key', 'sessionId', 'provider', 'host')}

    def owner(self, control):
        owned = _Owned.__new__(_Owned)
        owned.process = self.process
        owned.origin, owned.native, owned.cwd = 'fixture-profile', self.sid, str(self.root/'project')
        owned.connection_token, owned.local_control = self.token, control
        owned.spawn_profile, owned.eof_sent = self.profile, False
        owned.active, owned.pending, owned.error, owned.finished = False, {}, None, False
        owned.condition = threading.Condition()
        owned.control = Mock()
        return owned

    def client(self, control):
        client = ClaudeCli(control_directory=self.directory, profile_resolver=Mock(side_effect=AssertionError('profile read')))
        client.owned[self.sid] = self.owner(control)
        client.index[control.binding['sessionId']] = {'nativeId': self.sid, 'origin': 'fixture-profile'}
        return client

    def test_direct_spawn_binds_without_catalog_pid_and_restores_only_exact_saved_row(self):
        control = self.capture()
        self.assertTrue(control.preview()['canForce'])
        self.assertEqual(control.preview()['impact'][0]['connectionKey'], self.token)
        restored = owned_control.OwnedControl.restore(self.directory, self.row(control))
        self.assertEqual(restored.generation, control.generation)
        self.assertEqual(restored.binding['profile'], self.profile)
        for changed in ({'host': 'ssh:fixture'}, {'provider': 'codex'}, {'sessionId': 'other'}, {'key': str(uuid.uuid4())}):
            self.assertIsNone(owned_control.OwnedControl.restore(self.directory, self.row(control) | changed))
        self.native.terminate.assert_not_called()

    def test_catalog_pid_image_uid_and_launcher_exit_cannot_bind_foreign_process(self):
        for change, catalog in (({}, {'pid': 999}), ({'image': '/fixture/wrapper'}, {}),
                                ({'uid': os.getuid()+1}, {}), ({'ruid': os.getuid()+1}, {})):
            old = copy.deepcopy(self.native.current)
            self.native.current.update(change)
            with self.assertRaises(ValueError):
                self.capture(catalog)
            self.native.current = old
        self.process.poll.return_value = 0
        with self.assertRaises(ValueError):
            self.capture()
        self.assertFalse(self.directory.exists())

    def test_stale_confirmation_and_same_birth_image_change_do_not_signal(self):
        control = self.capture()
        with self.assertRaises(BridgeUnavailable) as rejected:
            control.force(str(uuid.uuid4()), 'stale')
        self.assertTrue(rejected.exception.release_result['notSent'])
        self.native.current['image'] = '/fixture/other'
        self.assertFalse(control.preview()['canForce'])
        self.assertEqual(control.force(str(uuid.uuid4()), control.generation)['status'], 'unknown')
        self.native.terminate.assert_not_called()
        self.assertIsNone(control.intent())

    def test_recycled_pid_is_original_gone_without_signalling_replacement(self):
        control = self.capture()
        self.native.current['birth'] = (34, 57)
        self.assertEqual(control.force(str(uuid.uuid4()), control.generation)['status'], 'released')
        self.native.terminate.assert_not_called()

    def test_force_is_once_across_operation_ids_and_restart(self):
        control = self.capture()
        operation = str(uuid.uuid4())
        result = control.force(operation, control.generation)
        self.assertEqual(result['status'], 'released')
        self.assertTrue(result['forceDispatched'])
        restored = owned_control.OwnedControl.restore(self.directory, self.row(control))
        for repeat in (operation, str(uuid.uuid4())):
            receipt = restored.force(repeat, restored.generation)
            self.assertEqual(receipt['status'], 'released')
            self.assertEqual(receipt['canonicalOperationId'], operation)
        self.native.terminate.assert_called_once()
        self.native.close.assert_called_once()

    def test_unknown_signal_or_durable_intent_failure_never_replays(self):
        control = self.capture()
        self.native.terminate.side_effect = PermissionError('signal result unknown')
        result = control.force(str(uuid.uuid4()), control.generation)
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(result['forceDispatched'])
        restored = owned_control.OwnedControl.restore(self.directory, self.row(control))
        self.assertFalse(restored.preview()['canForce'])
        self.assertEqual(restored.force(str(uuid.uuid4()), restored.generation)['status'], 'unknown')
        self.native.terminate.assert_called_once()

    def test_fsync_failure_after_intent_creation_is_only_checkable(self):
        control = self.capture()
        with patch.object(owned_control.os, 'fsync', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                control.force(str(uuid.uuid4()), control.generation)
        self.assertIsNotNone(control.intent())
        self.assertEqual(control.force(str(uuid.uuid4()), control.generation)['status'], 'unknown')
        self.native.terminate.assert_not_called()

    def test_private_storage_failure_preserves_normal_kernel_proof_but_no_force(self):
        self.directory.mkdir(mode=0o755)
        self.directory.chmod(0o755)
        control = self.capture()
        self.assertFalse(control.preview()['canForce'])
        with self.assertRaises(BridgeUnavailable):
            control.force(str(uuid.uuid4()), control.generation)
        self.native.current = None
        self.assertEqual(control.check()['status'], 'released')

    def test_unpersisted_binding_does_not_read_intent_or_break_messages_and_interrupt(self):
        with patch.object(owned_control, '_write_once', side_effect=PermissionError('private storage unavailable')):
            control = self.capture()
        owner = self.owner(control)
        owner.responses = {}
        owner.control = MethodType(_Owned.control, owner)
        owner.active = True
        frames = []
        def written(value):
            frame = json.loads(value)
            frames.append(frame)
            if frame['type'] == 'control_request':
                owner.responses[frame['request_id']] = {'subtype': 'success', 'response': {}}
                owner.active = False
        self.process.stdin.write.side_effect = written
        self.process.wait.return_value = 1
        self.process.stdin.close.side_effect = lambda: setattr(self.native, 'current', None)
        with patch.object(owned_control, '_read', side_effect=PermissionError('unreadable intent')) as reader:
            owner.write({'type': 'user', 'message': {'role': 'user', 'content': 'synthetic'}})
            owner.write({'type': 'control_response', 'response': {'subtype': 'success'}})
            self.assertEqual(owner.release_connection(stop=True)['status'], 'released')
            reader.assert_not_called()
        self.assertEqual([frame['type'] for frame in frames], ['user', 'control_response', 'control_request'])
        self.assertEqual(frames[-1]['request']['subtype'], 'interrupt')
        self.assertFalse(control.durable)
        self.native.terminate.assert_not_called()

    def test_persisted_binding_with_unreadable_intent_blocks_new_writes(self):
        control = self.capture()
        owner = self.owner(control)
        with patch.object(owned_control, '_read', side_effect=PermissionError('intent result unknown')):
            with self.assertRaises(PermissionError):
                owner.write({'type': 'user'})
        self.process.stdin.write.assert_not_called()

    def test_symlink_or_malformed_receipt_is_not_restored(self):
        control = self.capture()
        path = self.directory/(self.token+'.json')
        original = path.read_bytes()
        for value in ([], {}, {**control.binding, 'process': []}):
            path.write_text(json.dumps(value))
            self.assertIsNone(owned_control.OwnedControl.restore(self.directory, self.row(control)))
        path.write_bytes(original)
        path.chmod(0o644)
        self.assertIsNone(owned_control.OwnedControl.restore(self.directory, self.row(control)))
        path.unlink()
        target = self.root/'outside.json'
        target.write_bytes(original)
        path.symlink_to(target)
        self.assertIsNone(owned_control.OwnedControl.restore(self.directory, self.row(control)))

    def test_normal_eof_proves_native_exit_and_repeat_only_checks(self):
        control = self.capture()
        owner = self.owner(control)
        self.process.wait.return_value = 0
        self.assertEqual(owner.release_connection()['status'], 'unknown')
        self.process.stdin.close.assert_called_once()
        self.native.current = None
        self.assertEqual(owner.release_connection()['status'], 'released')
        self.process.stdin.close.assert_called_once()
        self.native.terminate.assert_not_called()

    def test_normal_wait_and_stop_keep_existing_task_protocol(self):
        owner = self.owner(self.capture())
        owner.active = True
        self.assertEqual(owner.release_connection(), {'status': 'waiting'})
        self.process.stdin.close.assert_not_called()
        def interrupted(request, timeout):
            self.assertEqual(request, {'subtype': 'interrupt'})
            owner.active = False
        owner.control.side_effect = interrupted
        self.process.wait.return_value = 1
        self.process.stdin.close.side_effect = lambda: setattr(self.native, 'current', None)
        self.assertEqual(owner.release_connection(stop=True)['status'], 'released')
        owner.control.assert_called_once()
        self.native.terminate.assert_not_called()

    def test_unbound_mac_exit_remains_unknown_and_other_platforms_keep_old_path(self):
        owner = self.owner(None)
        self.process.wait.return_value = 0
        self.assertEqual(owner.release_connection()['status'], 'unknown')
        owner.eof_sent, owner.spawn_profile = False, None
        self.assertEqual(owner.release_connection()['status'], 'released')
        client = self.client(self.capture())
        with patch.object(owned_control.sys, 'platform', 'linux'):
            self.assertEqual(client.cli_force_callbacks(self.row(client.owned[self.sid].local_control)), {})

    def test_shared_force_callbacks_bypass_unknown_tasks_and_do_not_remove_new_owner(self):
        control = self.capture()
        client = self.client(control)
        owner = client.owned[self.sid]
        owner.active, owner.pending, owner.error = True, {'permission': {}}, 'task state unavailable'
        with self.assertRaises(BridgeUnavailable):
            owner.release_connection()
        rows = owned_connections(client)
        manager = CliConnections(self.root, lambda: [client], private_json)
        self.addCleanup(manager.close)
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            old = manager.submit({'id': str(uuid.uuid4()), 'connections': [self.token], 'mode': 'wait'})
        manager._run(manager.jobs[old['id']], {self.token: rows[0]})
        preview = manager.submit({'mode': 'forcePreview', 'connections': [self.token]})
        self.assertTrue(preview['canForce'])
        body = {'id': str(uuid.uuid4()), 'connections': [self.token], 'mode': 'force', 'generation': preview['generation']}
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            job = manager.submit(body)
        manager._run(manager.jobs[job['id']], {self.token: rows[0]})
        self.assertEqual(manager.submit(body)['results'][0]['status'], 'released')
        self.assertEqual(manager.jobs[old['id']]['results'][0]['status'], 'unknown')
        self.assertEqual(manager.jobs[old['id']]['results'][0]['supersededBy'], job['id'])
        self.assertNotIn(self.sid, client.owned)
        newer = SimpleNamespace(connection_token=str(uuid.uuid4()))
        client.owned[self.sid] = newer
        # Saved callbacks and restored exact bindings cannot pop a new connection token.
        rows[0]['_check']()
        callbacks = client.cli_force_callbacks(self.row(control))
        callbacks['_force'](str(uuid.uuid4()), control.generation)
        self.assertIs(client.owned[self.sid], newer)
        self.native.terminate.assert_called_once()
        client.profile_resolver.assert_not_called()

    def test_manager_only_forwards_local_claude_rows(self):
        cli = SimpleNamespace(cli_force_callbacks=Mock(return_value={'_check': Mock()}))
        source = DesktopSessions.__new__(DesktopSessions)
        source.adapters = {'claude': SimpleNamespace(cli=cli)}
        row = {'provider': 'claude', 'host': 'local'}
        self.assertIn('_check', source.cli_force_callbacks(row))
        for changed in ({'provider': 'codex'}, {'host': 'ssh:fixture'}, {'host': 'wsl:fixture'}):
            self.assertEqual(source.cli_force_callbacks(row | changed), {})
        cli.cli_force_callbacks.assert_called_once_with(row)

    def test_configuration_projection_is_stable_per_verified_profile_not_native_generation(self):
        first = self.capture()
        client = self.client(first)
        row = client.cli_connections()[0]
        self.assertEqual(row['configurationLabel'], '本机 Claude CLI · '+self.profile['directory'])
        self.assertNotIn('scopeKey', row)
        self.assertNotIn('sharedHome', row)
        self.assertFalse(any(name.startswith('_scope_') for name in row))
        self.token, self.sid = str(uuid.uuid4()), str(uuid.uuid4())
        self.native.current['birth'] = (34, 57)
        second = self.capture()
        self.assertNotEqual(first.generation, second.generation)
        self.assertEqual(first.configuration(), second.configuration())
        for field, value in (('directory', str(self.root/'another-profile')), ('inode', 3), ('device', 9)):
            changed = copy.deepcopy(second.binding)
            changed['profile'][field] = value
            self.assertNotEqual(owned_control.OwnedControl(changed).configuration()['configurationKey'], row['configurationKey'])
        client.profile_resolver.assert_not_called()

    def test_missing_configuration_evidence_does_not_guess_a_group_or_enable_remote_scope(self):
        control = self.capture()
        for field in ('directory', 'device', 'inode', 'uid'):
            changed = copy.deepcopy(control.binding)
            changed['profile'].pop(field)
            self.assertEqual(owned_control.OwnedControl(changed).configuration(), {})
        client = self.client(control)
        for host in ('ssh:fixture', 'wsl:fixture'):
            self.assertEqual(client.cli_force_callbacks(self.row(control) | {'host': host}), {})

    def test_same_configuration_batch_wait_keeps_exact_owned_processes_and_other_profile(self):
        first = self.capture()
        client = self.client(first)
        controls = [first]
        for name in ('second', 'other-profile'):
            self.token, self.sid = str(uuid.uuid4()), str(uuid.uuid4())
            if name == 'other-profile':
                self.profile = {**self.profile, 'directory': str(self.root/'other-profile'), 'inode': 3}
            control = self.capture()
            controls.append(control)
            client.owned[self.sid] = self.owner(control)
            client.index[control.binding['sessionId']] = {'nativeId': self.sid, 'origin': 'fixture-profile'}
        rows = client.cli_connections()
        selected = [row['key'] for row in rows if row['configurationKey'] == rows[0]['configurationKey']]
        self.assertEqual(len(selected), 2)
        expected = {owner.connection_token: owner for owner in client.owned.values()}
        for owner in expected.values():
            owner.release_connection = Mock(return_value={'status': 'released'})
        manager = CliConnections(self.root, lambda: [client], private_json)
        self.addCleanup(manager.close)
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            job = manager.submit({'id': str(uuid.uuid4()), 'mode': 'wait', 'connections': selected})
        manager._run(manager.jobs[job['id']], {row['key']: row for row in rows})
        for key, owner in expected.items():
            if key in selected:
                owner.release_connection.assert_called_once_with(False)
            else:
                owner.release_connection.assert_not_called()
        self.assertEqual(len(client.owned), 1)
        self.native.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
