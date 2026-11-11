"""Explicit force admission, identity, and receipts; no fixture daemon is killed."""

from contextlib import ExitStack, closing, nullcontext
import sqlite3
import threading
from types import SimpleNamespace
import uuid
from unittest.mock import Mock, patch

from bridge.app.lifecycle import private_json
from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.clients.codex.cli_session import CliSessions
from bridge.clients.codex.daemon_network import configuration_stamp
from bridge.clients.codex.ipc import IPCError
from tests.test_cli_bridge_integration import CliBridgeFixture


class CliForceFixture(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        self.cwd = str(self.home/'workspace')
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET cwd = ?', (self.cwd,))
        self.native_state['cwd'] = self.cwd
        self.cli = CliSessions(self.home, self.bridge._cli_state, self.bridge._cli_disconnected)
        self.bridge.cli_sessions = self.cli
        self.cli.daemon_status = Mock(return_value='stopped')
        self.cli.attach = Mock()
        self.bridge.activate = Mock(return_value=SimpleNamespace(connected=True))
        self.generation = (43321, 'fixture-old')
        self.cli._start_daemon = Mock(side_effect=lambda *_: setattr(self, 'generation', (43322, 'fixture-new')))
        self.probe = SimpleNamespace(
            connect=Mock(return_value={'codexHome': str(self.home)}), close=Mock(),
            request=Mock(return_value={'config': {'model_provider': 'openai'}}))
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch('bridge.features.sessions.cli_force.force_daemon_supported', return_value=True))
        self.patches.enter_context(patch('bridge.features.sessions.cli_force._managed_cli', return_value=self.home/'codex'))
        self.patches.enter_context(patch('bridge.features.sessions.cli_force.maintenance_lock', side_effect=lambda _: nullcontext()))
        read = lambda *_args, **_kwargs: ({}, self.generation)
        self.patches.enter_context(patch('bridge.features.sessions.cli_force.read_daemon_environment', side_effect=read))
        self.patches.enter_context(patch('bridge.platforms.codex_daemon_env.read_daemon_environment', side_effect=read))
        self.patches.enter_context(patch('bridge.platforms.codex_daemon_env.configuration_after_start', return_value=False))
        self.patches.enter_context(patch('bridge.features.sessions.cli_force.CodexAppServer', return_value=self.probe))
        self.force = self.patches.enter_context(patch('bridge.features.sessions.cli_force.force_daemon_exit',
                                                    side_effect=lambda _home, _exe, _identity, checkpoint: checkpoint()))

    def preview(self):
        return self.bridge.cli_network(self.thread_id, {'action': 'forcePreview'})

    def confirmation(self, preview):
        return {'action': 'force', 'confirmed': True, 'generation': preview['generation'],
                'operationId': preview['operationId'], 'requestId': str(uuid.uuid4())}

    def waiting(self, phase='waiting'):
        operation = str(uuid.uuid4())
        record = {'purpose': 'recovery', 'phase': phase, 'operationId': operation, 'threadId': self.thread_id,
                  'host': self.bridge.host, 'home': str(self.home.resolve()), 'generation': 'old-receipt-generation',
                  'configuration': configuration_stamp(self.home),
                  'confirmation': {'confirmed': True, 'generation': 'old-confirmation'}}
        private_json(self.bridge.data_dir/'cli-daemon-recovery.json', record)
        return record

class CliForceTests(CliForceFixture):
    def test_preview_is_read_only_and_force_upgrades_waiting_and_cancels_only_cli_queue(self):
        old = self.waiting()
        queued, unknown = self.thread_id + ':queued', self.thread_id + ':unknown'
        other = str(uuid.uuid4()) + ':other-home'
        self.bridge.submissions.update({queued: {'status': 'queued', 'text': 'preserve this text'},
            unknown: {'status': 'unknown', 'text': 'unknown native result'}, other: {'status': 'queued', 'text': 'unrelated'}})
        preview = self.preview()
        self.assertEqual(preview['operationId'], old['operationId'])
        self.assertEqual(preview['taskState'], 'unknown')
        self.assertTrue(preview['canForce'])
        self.assertEqual(preview['impact'], [{'sessionId': self.thread_id, 'connectionKey': None}])
        self.force.assert_not_called()
        self.probe.connect.assert_not_called()
        self.bridge.activate.assert_not_called()
        body = self.confirmation(preview)
        result = self.bridge.cli_network(self.thread_id, body)
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(result['cancelledQueued'], 1)
        self.assertEqual(self.bridge.submissions[queued], {'status': 'cancelled', 'text': 'preserve this text'})
        self.assertEqual(self.bridge.submissions[unknown]['status'], 'unknown')
        self.assertEqual(self.bridge.submissions[other]['status'], 'queued')
        self.force.assert_called_once()
        self.assertEqual([call.args[0] for call in self.probe.request.call_args_list], ['config/read'])
        self.cli._start_daemon.assert_called_once()
        self.bridge.activate.assert_called_once_with(self.thread_id,
            _expected_cli={'id': self.thread_id, 'cwd': self.cwd, 'modelProvider': 'openai'})
        superseded = self.bridge.cli_network(self.thread_id, {'action': 'status', 'operationId': old['operationId']})
        self.assertEqual(superseded['replacedBy'], body['requestId'])
        self.assertEqual(superseded['state'], 'superseded')
        self.assertTrue(self.bridge.cli_network(self.thread_id, body)['duplicate'])
        self.force.assert_called_once()

    def test_old_unknown_is_retained_and_new_force_does_not_require_native_rpc(self):
        original = self.waiting('resuming')
        self.probe.connect.side_effect = AppServerUnavailable('native RPC timed out')
        body = self.confirmation(self.preview())
        result = self.bridge.cli_network(self.thread_id, body)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(self.bridge.cli_force._receipts()[body['requestId']]['previousMaintenance'], original)
        self.cli._start_daemon.assert_not_called()
        self.bridge.activate.assert_not_called()
        self.force.assert_called_once()

    def test_force_requires_fresh_process_and_config_binding(self):
        body = self.confirmation(self.preview())
        self.generation = (43323, 'changed')
        with self.assertRaisesRegex(ValueError, '重新预览'):
            self.bridge.cli_network(self.thread_id, body)
        body = self.confirmation(self.preview())
        (self.home/'config.toml').write_text('model = "new"\n')
        with self.assertRaisesRegex(ValueError, '重新预览'):
            self.bridge.cli_network(self.thread_id, body)
        self.force.assert_not_called()
        self.assertFalse(self.bridge.cli_force.path.exists())

    def test_duplicate_unknown_force_only_observes_exit_and_never_starts_or_signals(self):
        def lost(_home, _exe, _generation, checkpoint):
            checkpoint()
            raise OSError('native termination result unknown')
        self.force.side_effect = lost
        body = self.confirmation(self.preview())
        self.assertEqual(self.bridge.cli_network(self.thread_id, body)['status'], 'unknown')
        with patch('bridge.features.sessions.cli_force.daemon_generation_absent', return_value=False):
            self.assertEqual(self.bridge.cli_network(self.thread_id, body)['status'], 'unknown')
        with patch('bridge.features.sessions.cli_force.daemon_generation_absent', return_value=True):
            result = self.bridge.cli_network(self.thread_id, body)
        self.assertEqual(result['status'], 'stopped')
        self.assertTrue(result['duplicate'])
        self.force.assert_called_once()
        self.cli._start_daemon.assert_not_called()
        self.bridge.activate.assert_not_called()

    def test_new_confirmed_intent_can_target_still_live_generation_after_unknown_force(self):
        def lost(_home, _exe, _generation, checkpoint):
            checkpoint()
            raise OSError('native termination result unknown')
        self.force.side_effect = lost
        first = self.confirmation(self.preview())
        self.assertEqual(self.bridge.cli_network(self.thread_id, first)['status'], 'unknown')
        second = self.confirmation(self.preview())
        self.assertEqual(second['operationId'], first['requestId'])
        self.assertNotEqual(first['generation'], second['generation'])
        self.force.side_effect = lambda _home, _exe, _generation, checkpoint: checkpoint()
        self.assertEqual(self.bridge.cli_network(self.thread_id, second)['status'], 'recovered')
        self.assertEqual(self.force.call_count, 2)
        self.assertEqual(self.bridge.cli_force._receipts()[first['requestId']]['result']['status'], 'unknown')

    def test_release_does_not_query_tasks_or_restart(self):
        self.probe.connect.side_effect = AssertionError('force release never probes native RPC')
        key = 'fixture-original-token'
        preview = self.bridge.cli_force.preview(self.thread_id, connection=key)
        result = self.bridge._force_cli_connection(self.thread_id, key, str(uuid.uuid4()), preview['generation'])
        self.assertEqual(result['status'], 'released')
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'released')
        self.assertTrue(self.bridge.released_cli.contains(self.thread_id))
        self.force.assert_called_once()
        self.cli._start_daemon.assert_not_called()

    def test_unknown_resume_requires_new_explicit_continuation_after_forced_exit(self):
        original = self.waiting('resuming')
        body = self.confirmation(self.preview())
        self.assertEqual(self.bridge.cli_network(self.thread_id, body)['status'], 'stopped')
        self.assertEqual(self.bridge._cli_recovery_record()['unresolvedResume'], original)
        with self.assertRaisesRegex(IPCError, '明确确认'):
            self.bridge._recover_cli_environment(self.thread_id)
        self.bridge.activate.assert_not_called()
        self.cli._start_daemon.assert_not_called()
        with patch('bridge.features.sessions.cli_force.daemon_generation_absent', return_value=False):
            self.assertFalse(self.bridge.cli_network(self.thread_id)['canRecover'])
        with patch('bridge.features.sessions.cli_force.daemon_generation_absent', return_value=True):
            preview = self.bridge.cli_network(self.thread_id)
            self.assertEqual(preview['action'], 'reconnect')
            self.assertTrue(preview['canRecover'])
            reconnect = {'action': 'reconnect', 'confirmed': True,
                         'generation': preview['generation'], 'requestId': str(uuid.uuid4())}
            result = self.bridge.cli_network(self.thread_id, reconnect)
        self.assertEqual(result['status'], 'recovered')
        self.assertNotIn('unresolvedResume', self.bridge._cli_recovery_record())
        self.assertNotIn('providerTarget', self.bridge._cli_recovery_record())
        self.cli._start_daemon.assert_called_once()
        self.bridge.activate.assert_called_once()
        plan, guard = self.bridge.activate.call_args.kwargs['_cli_recovery']
        self.assertEqual(plan, {'provider': 'openai',
            'before': {'id': self.thread_id, 'cwd': self.cwd, 'modelProvider': 'openai'}})
        self.assertTrue(callable(guard))
        self.assertEqual(self.bridge.cli_force._receipts()[body['requestId']]['previousMaintenance'], original)
        self.assertTrue(self.bridge.cli_network(self.thread_id, reconnect)['duplicate'])
        self.bridge.activate.assert_called_once()
        self.force.assert_called_once()
        with patch.object(self.bridge, 'cli_network', return_value={}), \
                patch.object(self.bridge, '_verify_cli_file_auth'):
            self.bridge._recover_cli_environment(self.thread_id, follow_account=True)

    def test_unknown_resume_force_release_preserves_block_for_later_ordinary_continue(self):
        original = self.waiting('resuming')
        key = 'fixture-release-key'
        preview = self.bridge.cli_force.preview(self.thread_id, connection=key)
        result = self.bridge._force_cli_connection(self.thread_id, key, str(uuid.uuid4()), preview['generation'])
        self.assertEqual(result['status'], 'released')
        self.assertEqual(self.bridge._cli_recovery_record()['unresolvedResume'], original)
        with self.assertRaisesRegex(IPCError, '明确确认'):
            self.bridge._recover_cli_environment(self.thread_id)
        self.cli._start_daemon.assert_not_called()

    def test_reconnect_proxy_failure_uses_real_start_lock_and_returns_unknown(self):
        original = self.waiting('resuming')
        forced = self.confirmation(self.preview())
        self.bridge.cli_network(self.thread_id, forced)
        self.bridge.activate = type(self.bridge).activate.__get__(self.bridge)
        del self.cli.attach
        self.cli.client.connect = Mock(side_effect=AppServerUnavailable('fixture proxy unavailable'))
        outcome, failures = [], []
        with patch('bridge.features.sessions.cli_force.daemon_generation_absent', return_value=True), \
                patch('bridge.clients.codex.cli_session._local_cli', return_value=self.home/'codex'), \
                patch('bridge.clients.codex.cli_session._managed_cli', return_value=self.home/'codex'):
            preview = self.bridge.cli_network(self.thread_id)
            body = {'action': 'reconnect', 'confirmed': True,
                    'generation': preview['generation'], 'requestId': str(uuid.uuid4())}
            def reconnect():
                try:
                    outcome.append(self.bridge.cli_network(self.thread_id, body))
                except BaseException as error:
                    failures.append(error)
            worker = threading.Thread(target=reconnect, daemon=True)
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive(), 'proxy failure reentered start_lock and deadlocked')
        self.assertFalse(failures)
        self.assertEqual(outcome[0]['status'], 'unknown')
        self.assertEqual(self.cli.client.connect.call_count, 1)
        self.cli._start_daemon.assert_called_once()
        self.assertIn('unresolvedResume', self.bridge._cli_recovery_record())
        self.assertEqual(self.bridge.cli_force._receipts()[forced['requestId']]['previousMaintenance'], original)
        self.force.assert_called_once()

    def test_remote_and_wrong_home_never_offer_force(self):
        self.cli.alias = 'remote-fixture'
        self.assertFalse(self.preview()['canForce'])
        self.cli.alias = None
        self.cli.home = self.home/'other-home'
        self.assertFalse(self.preview()['canForce'])
        self.force.assert_not_called()
