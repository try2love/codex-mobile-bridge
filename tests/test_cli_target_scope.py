"""Target Linux scope wiring; no real remote host or native signal."""
import copy
from contextlib import closing, nullcontext
import json
import sqlite3
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from bridge.app.lifecycle import private_json
from bridge.clients.codex.app_server import CodexAppServer
from bridge.clients.codex.linux_control import LinuxCliControl, worker_source, shared_cli_probe
from bridge.clients.codex.remote import RemoteStore, ssh_cli_probe
from bridge.clients.codex.ipc import IPCError
from bridge.features.sessions.cli_maintenance import CliTasksPending
from tests.test_cli_force import CliForceFixture


class LinuxControlTransportTests(unittest.TestCase):
    def value(self, *, initialized=True):
        return {'platform':'linux','generation':{'uid':1000,'user':'fixture','home':'/srv/custom',
                'pid':19,'socket':'/tmp/codex-daemon-1000/fixture','executable':'/srv/private/codex'},
                'canForce':True,'canRelease':False,'configuration':{'auth.json':'hash'},
                'initialize':{'codexHome':'/srv/custom','userAgent':'codex-mobile-bridge-target-check/0.164.0'} if initialized else None}

    def test_injected_worker_runs_without_installed_bridge(self):
        source = 'import importlib.util\nassert importlib.util.find_spec("bridge") is None\n'
        source += worker_source({'action':'unsupported'})
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable,'-I','-S','-c',source], cwd=directory,
                                    capture_output=True, text=True, check=True)
        reply = json.loads(result.stdout)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['errorType'], 'ControlError')

    def test_linux_capability_uses_target_os_on_windows_gateway_and_cache_is_passive(self):
        read = Mock(return_value={'ok':True,'result':self.value()})
        control = LinuxCliControl(lambda:'/srv/custom', read)
        self.assertIsNone(control.cached())
        read.assert_not_called()
        with patch('bridge.clients.codex.daemon_network.sys.platform','win32'):
            value = control.inspect()
        self.assertTrue(value['canRelease'])
        self.assertEqual(value['generation']['nativeVersion'],'0.164.0')
        control.cached()
        read.assert_called_once()

    def test_unavailable_initialize_does_not_hide_verified_force_identity(self):
        control = LinuxCliControl(lambda:'/srv/custom', Mock(return_value={'ok':True,'result':self.value(initialized=False)}))
        result = control.inspect()
        self.assertTrue(result['canForce'])
        self.assertFalse(result['canRelease'])
        self.assertIsNone(result['generation']['nativeVersion'])

    def test_closed_endpoint_read_stays_a_bounded_control_error(self):
        control = LinuxCliControl(lambda:'/srv/custom', Mock(side_effect=IPCError('endpoint closed')))
        with self.assertRaisesRegex(ValueError,'不会重发'):
            control.inspect()
        self.assertIsNone(control.cached())

    def test_explicit_remote_probe_uses_target_home_socket_and_executable(self):
        value = self.value()
        with patch('bridge.clients.codex.app_server.CodexAppServer') as client:
            with ssh_cli_probe('fixture-alias',value):
                pass
        args = client.call_args
        self.assertEqual(args.args[0],value['generation']['socket'])
        self.assertFalse(args.kwargs['check_socket'])
        command = args.kwargs['command']
        self.assertEqual(command[0],'ssh')
        self.assertIn('StrictHostKeyChecking=yes',command)
        self.assertIn('/srv/custom',command[-1])
        self.assertIn('/srv/private/codex',command[-1])
        self.assertIn('--sock',command[-1])

    def test_posix_control_path_is_not_reinterpreted_as_windows(self):
        with patch('bridge.clients.codex.app_server.Path',PureWindowsPath):
            client = CodexAppServer('/tmp/target.sock',command=['ssh','fixture'],check_socket=False)
        self.assertEqual(client.socket_path,'/tmp/target.sock')

    def test_saved_identity_read_uses_explicit_target_home(self):
        store = RemoteStore('fixture')
        with patch('bridge.clients.codex.remote.ssh_read',return_value={'id':'saved'}) as read:
            store.call('get',{'thread_id':'saved'},home='/srv/remote/home')
        source = read.call_args.args[1]
        self.assertIn("SessionStore(Path('/srv/remote/home'))",source)

    def test_shared_probe_preserves_unsupported_rpc_for_idle_goal_check(self):
        error = IPCError('unsupported')
        error.rpc_error = {'code':-32601}
        server = SimpleNamespace(start=Mock(),close=Mock(),server_info={'codexHome':'/srv/custom'},
                                 request=Mock(side_effect=error))
        with patch('bridge.clients.codex.shared_server.SharedServer',return_value=server):
            with shared_cli_probe(object()) as probe:
                from bridge.clients.codex.app_server import AppServerUnsupported
                with self.assertRaises(AppServerUnsupported):
                    probe.request('thread/goal/get',{'threadId':'original'},replay_safe=True)
        server.close.assert_called_once()

    def test_ssh_cold_start_pins_selected_posix_home(self):
        from bridge.clients.codex.cli_session import _ssh_daemon_start_command
        command = _ssh_daemon_start_command('fixture', '/srv/remote/custom')
        self.assertIn('CODEX_HOME=/srv/remote/custom',command[-1])
        self.assertIn('daemon start',command[-1])


class TargetScopeTests(CliForceFixture):
    def setUp(self):
        super().setUp()
        self.value = LinuxControlTransportTests().value()
        self.value['canRelease'] = True
        self.value['generation']['nativeVersion'] = '0.164.0'
        self.control = SimpleNamespace(inspect=Mock(side_effect=lambda:copy.deepcopy(self.value)),
            cached=Mock(side_effect=lambda:copy.deepcopy(self.value)),
            dispatch=Mock(return_value={'status':'requested'}), check=Mock(return_value={'status':'requested'}))
        self.pending, self.cancel_queued, self.done = Mock(return_value=False), Mock(return_value=1), Mock()
        self.probe.connect.return_value = self.value['initialize']
        self.probe.loaded_threads = Mock(return_value=[])
        self.scope = self.bridge.cli_scope
        self.scope.target = SimpleNamespace(control=self.control, label='SSH fixture',enabled=lambda:True,
            guard=nullcontext, saved=lambda sid,home:self.cli.store.get(sid),
            impact=lambda home=None:[{'sessionId':self.thread_id,'connectionKey':'remote-key'}],
            pending=self.pending, cancel_queued=self.cancel_queued, released=self.done,
            probe=lambda value:closing(self.probe))

    def preview(self):
        return self.scope.preview(self.thread_id,'remote-key')

    def test_wait_then_dispatch_once_and_poll_only_original_receipt(self):
        preview = self.preview()
        operation = str(uuid.uuid4())
        self.pending.return_value = True
        self.assertEqual(self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])['status'],'waiting')
        self.control.dispatch.assert_not_called()
        self.pending.return_value = False
        self.assertEqual(self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])['status'],'waiting')
        self.control.dispatch.assert_called_once_with(operation,'graceful',self.value['generation'])
        self.assertEqual(self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])['status'],'waiting')
        self.control.dispatch.assert_called_once()
        self.control.check.return_value = {'status':'exited'}
        self.assertEqual(self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])['status'],'released')
        self.done.assert_called_once()
        self.control.dispatch.assert_called_once()

    def test_force_upgrades_unknown_without_task_rpc_or_business_replay(self):
        preview, first = self.preview(), str(uuid.uuid4())
        self.control.dispatch.side_effect = ValueError('transport result unknown')
        self.assertEqual(self.scope.release(self.thread_id,'remote-key',first,preview['generation'])['status'],'unknown')
        self.pending.return_value = True
        self.probe.connect.side_effect = AssertionError('force never checks tasks')
        preview, second = self.preview(), str(uuid.uuid4())
        self.control.dispatch.side_effect = None
        result = self.scope.force(self.thread_id,'remote-key',second,preview['generation'])
        self.assertTrue(result['forceDispatched'])
        self.assertEqual(self.control.dispatch.call_count,2)
        previous = self.bridge._cli_recovery_record()['scopeBefore']
        self.assertEqual((previous['scopeRequestId'],previous['phase']),(first,'draining'))
        self.scope.force(self.thread_id,'remote-key',second,preview['generation'])
        self.assertEqual(self.control.dispatch.call_count,2)
        self.cancel_queued.assert_called_once()

    def test_stale_force_preview_preserves_original_wait(self):
        old = self.waiting()
        preview = self.preview()
        self.value['generation']['pid'] += 1
        with self.assertRaisesRegex(ValueError,'重新预览'):
            self.scope.force(self.thread_id,'remote-key',str(uuid.uuid4()),preview['generation'])
        self.assertEqual(self.bridge._cli_recovery_record(),old)
        self.control.dispatch.assert_not_called()

    def test_config_drift_ends_wait_and_allows_new_confirmation(self):
        preview, operation = self.preview(), str(uuid.uuid4())
        self.pending.return_value = True
        self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])
        self.value['configuration']['auth.json'] = 'changed'
        with self.assertRaisesRegex(ValueError,'重新预览'):
            self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'],'failed')
        self.assertTrue(self.preview()['canRelease'])
        self.control.dispatch.assert_not_called()

    def test_cancel_at_last_target_identity_check_prevents_transport_dispatch(self):
        preview, operation = self.preview(), str(uuid.uuid4())
        calls = 0
        def inspect():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.scope.cancel(operation)
            return copy.deepcopy(self.value)
        self.control.inspect.side_effect = inspect
        with self.assertRaisesRegex(ValueError,'取消'):
            self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])
        self.control.dispatch.assert_not_called()

    def test_passive_scope_metadata_does_not_probe_remote(self):
        first = self.scope.descriptor()['scopeKey']
        self.value['generation']['pid'] += 1
        self.assertEqual(first,self.scope.descriptor()['scopeKey'])
        self.control.inspect.assert_not_called()
        self.value['generation']['home'] = '/different/home'
        self.assertNotEqual(first,self.scope.descriptor()['scopeKey'])

    def test_force_remains_available_when_version_read_becomes_unknown(self):
        preview = self.preview()
        self.value['generation']['nativeVersion'] = None
        self.value['canRelease'] = False
        self.value['initialize'] = None
        self.control.dispatch.return_value = {'status':'exited'}
        result = self.scope.force(self.thread_id,'remote-key',str(uuid.uuid4()),preview['generation'])
        self.assertEqual(result['status'],'released')
        self.control.dispatch.assert_called_once()
        self.probe.connect.assert_not_called()

    def test_explicit_remote_continue_after_release_skips_local_recovery(self):
        record = {'purpose':'release','phase':'released','targetGeneration':self.value['generation']}
        private_json(self.bridge.data_dir/'cli-daemon-recovery.json',record)
        with patch('bridge.clients.codex.cli_session._managed_cli',side_effect=AssertionError('local home must not be inspected')):
            self.assertIsNone(self.bridge._recover_cli_environment(self.thread_id,follow_account=True))
        record['phase'] = 'draining'
        private_json(self.bridge.data_dir/'cli-daemon-recovery.json',record)
        with self.assertRaisesRegex(IPCError,'待核验'):
            self.bridge._recover_cli_environment(self.thread_id,follow_account=True)

    def test_disconnected_and_restarted_unknown_submission_blocks_default_scope(self):
        other = str(uuid.uuid4())
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db, db:
            db.execute('INSERT INTO threads SELECT ?,title,cwd,updated_at,archived,originator,source,rollout_path,model_provider FROM threads LIMIT 1',(other,))
        self.bridge.submissions[other+':'+str(uuid.uuid4())] = {'status':'unknown','text':'original unconfirmed write'}
        self.bridge.origins[other] = 'cli'
        self.cli.cli_connections = Mock(return_value=[])
        self.assertIn({'sessionId':other,'connectionKey':None},self.bridge._target_cli_impact())
        self.scope.target.pending = self.bridge._target_cli_pending
        preview, operation = self.preview(), str(uuid.uuid4())
        self.assertEqual(self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])['status'],'waiting')
        self.control.dispatch.assert_not_called()
        self.bridge.origins.clear()  # A new gateway cannot assume missing ownership is safe.
        self.assertTrue(self.bridge._target_cli_pending())
        self.assertEqual(self.scope.release(self.thread_id,'remote-key',operation,preview['generation'])['status'],'waiting')
        self.control.dispatch.assert_not_called()
        self.assertEqual(next(iter(self.bridge.submissions.values()))['status'],'unknown')

    def test_restart_force_cancels_only_proven_cli_queue_and_preserves_text(self):
        cli_id, app_id = str(uuid.uuid4()), str(uuid.uuid4())
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db, db:
            db.execute('INSERT INTO threads SELECT ?,title,cwd,updated_at,archived,originator,source,rollout_path,model_provider FROM threads LIMIT 1',(cli_id,))
            db.execute("INSERT INTO threads SELECT ?,title,cwd,updated_at,archived,'Codex Desktop','desktop',rollout_path,model_provider FROM threads LIMIT 1",(app_id,))
        cli_key, app_key = cli_id+':'+str(uuid.uuid4()), app_id+':'+str(uuid.uuid4())
        self.bridge.submissions.update({cli_key:{'status':'queued','text':'keep CLI body'},
                                        app_key:{'status':'queued','text':'keep App body'}})
        self.bridge.origins.clear()
        self.cli.cli_connections = Mock(return_value=[])
        self.scope.target.impact = self.bridge._target_cli_impact
        self.scope.target.pending = self.bridge._target_cli_pending
        self.scope.target.cancel_queued = self.bridge._target_cli_cancel_queued
        preview = self.preview()
        self.assertIn({'sessionId':cli_id,'connectionKey':None},preview['impact'])
        self.assertNotIn(app_id,{row['sessionId'] for row in preview['impact']})
        result = self.scope.force(self.thread_id,'remote-key',str(uuid.uuid4()),preview['generation'])
        self.assertEqual(result['cancelledQueued'],1)
        self.assertEqual(self.bridge.submissions[cli_key],{'status':'cancelled','text':'keep CLI body'})
        self.assertEqual(self.bridge.submissions[app_key],{'status':'queued','text':'keep App body'})

    def test_queue_added_during_source_lookup_is_durably_paused(self):
        entered, proceed = threading.Event(), threading.Event()
        old_key, new_key = self.thread_id+':old', str(uuid.uuid4())+':new'
        self.bridge.submissions[old_key] = {'status':'queued','text':'old body'}
        results = []
        def saved(sid, home):
            entered.set()
            if not proceed.wait(2):
                raise AssertionError('source lookup was not released')
            return {'origin':'cli'}
        with patch.object(self.scope.target,'saved',side_effect=saved):
            worker = threading.Thread(target=lambda:results.append(self.bridge._target_cli_cancel_queued(
                self.value['generation']['home'],'force-request','scope-key')))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.bridge.submit_lock:
                    self.bridge.submissions[new_key] = {'status':'queued','text':'concurrent retained body'}
            finally:
                proceed.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results,[{'cancelledQueued':1,'unresolvedQueued':[new_key]}])
        self.assertEqual(self.bridge.submissions[new_key]['cliRelease']['requestId'],'force-request')
        self.assertEqual(json.loads(self.bridge.ledger_path.read_text())[new_key]['text'],'concurrent retained body')

    def test_send_admitted_before_force_cannot_commit_after_cancel_and_new_connection_can_send(self):
        from bridge.app.service import LiveSession
        session = LiveSession(self.thread_id)
        session.connected, session.origin, session.runtime, session.owner = True, 'cli', 'cli', 'cli'
        session.state = copy.deepcopy(self.native_state)
        session.state['threadRuntimeStatus'] = {'type':'active'}
        self.bridge.live[self.thread_id] = session
        process = SimpleNamespace(poll=lambda:0,stdin=None,stdout=None)
        self.cli.client.process = process
        self.cli.joined[self.thread_id] = process
        self.cli.metadata[self.thread_id] = {'connectionToken':'remote-key'}
        self.scope.target.cancel_queued = self.bridge._target_cli_cancel_queued
        for native_status in ('requested','exited'):
            with self.subTest(native_status=native_status):
                self.bridge.submissions.clear()
                private_json(self.bridge.data_dir/'cli-daemon-recovery.json',{})
                self.control.dispatch.return_value = {'status':native_status}
                self.cli.metadata[self.thread_id]['connectionToken'] = 'remote-key'
                admitted, proceed = threading.Event(), threading.Event()
                errors, submission = [], str(uuid.uuid4())
                verify = self.bridge._verify_cli_submission_account
                def pause_after_admission(current):
                    verify(current)
                    admitted.set()
                    if not proceed.wait(2):
                        raise AssertionError('admitted sender was not released')
                def send():
                    try:
                        self.bridge.send(self.thread_id,'retain new draft',submission,mode='queue')
                    except Exception as error:
                        errors.append(error)
                with patch.object(self.bridge,'_target',return_value=session), \
                        patch.object(self.bridge,'_verify_cli_submission_account',side_effect=pause_after_admission), \
                        patch.object(self.bridge,'_dispatch') as dispatch:
                    worker = threading.Thread(target=send)
                    worker.start()
                    try:
                        self.assertTrue(admitted.wait(2))
                        preview = self.preview()
                        self.scope.force(self.thread_id,'remote-key',str(uuid.uuid4()),preview['generation'])
                    finally:
                        proceed.set()
                        worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(len(errors),1)
                    self.assertIsInstance(errors[0],IPCError)
                    self.assertNotIn(self.thread_id+':'+submission,self.bridge.submissions)
                    dispatch.assert_not_called()
                if native_status == 'exited':
                    # A fresh explicit connection has a new native process/token;
                    # old release history does not block this user's new input.
                    replacement = SimpleNamespace(poll=lambda:0,stdin=None,stdout=None)
                    self.cli.client.process = self.cli.joined[self.thread_id] = replacement
                    self.cli.metadata[self.thread_id]['connectionToken'] = 'new-explicit-token'
                    with patch.object(self.bridge,'_target',return_value=session):
                        self.assertEqual(self.bridge.send(self.thread_id,'fresh explicit draft',str(uuid.uuid4()),mode='queue')['status'],'queued')

    def test_unresolved_queue_guard_survives_restart_and_does_not_block_new_key(self):
        from bridge.app.service import LiveSession
        missing = str(uuid.uuid4())
        old_key = missing+':'+str(uuid.uuid4())
        self.bridge.submissions[old_key] = {'status':'queued','text':'unresolved old body','mode':'queue'}
        self.scope.target.cancel_queued = self.bridge._target_cli_cancel_queued
        preview = self.preview()
        self.scope.force(self.thread_id,'remote-key',str(uuid.uuid4()),preview['generation'])
        marker = self.bridge.submissions[old_key]['cliRelease']
        self.assertEqual(marker['home'],self.value['generation']['home'])
        self.bridge.submissions = json.loads(self.bridge.ledger_path.read_text())
        session = LiveSession(missing)
        session.connected = True
        session.runtime, session.origin = 'cli', 'cli'
        session.state = {'threadRuntimeStatus':{'type':'idle'}}
        self.bridge.live[missing] = session
        with patch.object(self.bridge,'_dispatch') as dispatch:
            self.bridge._send_queued(session,old_key,self.bridge.submissions[old_key])
            dispatch.assert_not_called()
            new_key = missing+':'+str(uuid.uuid4())
            self.bridge.submissions[new_key] = {'status':'queued','text':'new explicit body','mode':'queue'}
            self.bridge._send_queued(session,new_key,self.bridge.submissions[new_key])
            dispatch.assert_called_once()
            self.assertEqual(dispatch.call_args.args[1],new_key)
        self.assertEqual(self.bridge.submissions[old_key]['status'],'queued')
        self.assertEqual(self.bridge.submissions[old_key]['text'],'unresolved old body')
        with patch.object(self.bridge,'session',side_effect=AssertionError('cancel needs no native identity')):
            self.bridge.cancel_queued(missing,old_key.split(':',1)[1])
        self.assertEqual(self.bridge.submissions[old_key]['status'],'cancelled')

    def test_later_cli_proof_cancels_old_key_but_app_proof_keeps_app_path(self):
        for origin in ('cli','desktop'):
            sid = str(uuid.uuid4())
            key = sid+':'+str(uuid.uuid4())
            self.bridge.submissions[key] = {'status':'queued','text':'retained',
                'cliRelease':{'requestId':str(uuid.uuid4()),'home':self.value['generation']['home'],'scopeKey':'old-scope'}}
            with patch.object(self.scope.target,'saved',return_value={'origin':origin}):
                blocked = self.bridge._settle_scope_queued(self.bridge.session(self.thread_id,attach=False),key)
            self.assertEqual(blocked,origin=='cli')
            self.assertEqual(self.bridge.submissions[key]['status'],'cancelled' if origin=='cli' else 'queued')
            self.assertEqual(self.bridge.submissions[key]['text'],'retained')
            if origin=='desktop':
                self.assertNotIn('cliRelease',self.bridge.submissions[key])
