"""Home-scoped release and old-receipt isolation with owned SQLite fixtures."""
from contextlib import closing
import sqlite3
import threading
from types import SimpleNamespace
import uuid
from unittest.mock import Mock, patch

from bridge.app.lifecycle import private_json
from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.features.sessions.cli_maintenance import CliTasksPending
from bridge.features.sessions.cli_release import CliConnections
from tests.test_cli_force import CliForceFixture


class CliScopeTests(CliForceFixture):
    def setUp(self):
        super().setUp()
        self.other = str(uuid.uuid4())
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db, db:
            db.execute('INSERT INTO threads SELECT ?,title,cwd,updated_at,archived,originator,source,rollout_path,model_provider FROM threads LIMIT 1', (self.other,))
        self.cli.metadata.update({self.thread_id: {'connectionToken': 'key-a'}, self.other: {'connectionToken': 'key-b'}})
        self.probe.connect.return_value = {'codexHome': str(self.home), 'userAgent': 'codex/0.167.0'}
        def request(method, params, **_kwargs):
            if method == 'config/read':
                return {'config': {'model_provider': 'openai'}}
            if method == 'thread/read':
                return {'thread': {'id': params['threadId'], 'cwd': self.cwd, 'modelProvider': 'openai'}}
            raise AssertionError(method)
        self.probe.request.side_effect = request
        self.patches.enter_context(patch('bridge.features.sessions.cli_scope.CodexAppServer', return_value=self.probe))
        self.patches.enter_context(patch('bridge.clients.codex.app_server.CodexAppServer', return_value=self.probe))
        self.patches.enter_context(patch('bridge.features.sessions.cli_scope.read_daemon_environment',
                                         side_effect=lambda *_a, **_k: ({}, self.generation)))
        self.patches.enter_context(patch('bridge.features.sessions.cli_scope.maintenance_lock', side_effect=lambda _: closing(SimpleNamespace(close=lambda: None))))
        self.idle = self.patches.enter_context(patch('bridge.features.accounts.cli_sync.LocalCliSync.check_idle_daemon'))
        self.stop = self.patches.enter_context(patch('bridge.platforms.codex_daemon_env.stop_daemon_gracefully'))
        self.scope = self.bridge.cli_scope

    def rows(self):
        return [{'key': key, 'sessionId': sid, 'provider': 'codex', 'host': self.bridge.host,
                 **self.bridge.cli_force_callbacks({'key': key, 'sessionId': sid, 'provider': 'codex', 'host': self.bridge.host})}
                for sid, key in ((self.thread_id, 'key-a'), (self.other, 'key-b'))]

    def manager(self):
        directory = self.home/'release-manager'
        directory.mkdir(exist_ok=True)
        manager = CliConnections(directory, lambda: [SimpleNamespace(cli_connections=self.rows)], private_json)
        self.addCleanup(manager.close)
        return manager

    def test_scope_is_stable_across_generation_but_separates_host_home_and_user(self):
        first = self.scope.descriptor()['scopeKey']
        preview = self.scope.preview(self.thread_id, 'key-a')['generation']
        self.generation = (43322, 'new-generation')
        self.assertEqual(first, self.scope.descriptor()['scopeKey'])
        self.assertNotEqual(preview, self.scope.preview(self.thread_id, 'key-a')['generation'])
        with patch('bridge.features.sessions.cli_force.daemon_scope_user', return_value='uid:other'):
            self.assertNotEqual(first, self.scope.descriptor()['scopeKey'])
        self.bridge.host = 'codex:another-host'
        self.assertNotEqual(first, self.scope.descriptor()['scopeKey'])

    def test_all_scope_has_whole_home_impact_and_only_one_native_drain(self):
        manager = self.manager()
        descriptor = self.scope.descriptor()
        preview = manager.submit({'mode': 'scopePreview', 'scopeKey': descriptor['scopeKey'], 'all': True})
        self.assertEqual(preview['connections'], ['key-a', 'key-b'])
        self.assertTrue(preview['canRelease'])
        self.assertEqual(len(preview['impact']), 2)
        self.assertEqual(len(manager.view()['scopes']), 1)
        self.probe.request.assert_not_called()
        self.stop.assert_not_called()
        self.bridge.activate.assert_not_called()
        body = {'id': str(uuid.uuid4()), 'mode': 'scopeWait', 'scopeKey': preview['scopeKey'],
                'connections': preview['connections'], 'generation': preview['generation']}
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            job = manager.submit(body)
        manager._run_scope(manager.jobs[job['id']], manager.callbacks)
        self.assertEqual([row['status'] for row in manager.jobs[job['id']]['results']], ['released', 'released'])
        self.stop.assert_called_once()
        self.assertTrue(self.stop.call_args.kwargs['graceful_only'])
        self.assertTrue(self.bridge.released_cli.contains(self.thread_id))
        self.assertTrue(self.bridge.released_cli.contains(self.other))
        self.assertEqual(manager.submit(body)['results'][0]['status'], 'released')
        self.stop.assert_called_once()

    def test_single_selection_still_discloses_other_connection(self):
        manager = self.manager()
        preview = manager.submit({'mode': 'scopePreview', 'scopeKey': self.scope.descriptor()['scopeKey'], 'connections': ['key-a']})
        self.assertEqual(preview['connections'], ['key-a'])
        self.assertEqual({row['connectionKey'] for row in preview['impact']}, {'key-a', 'key-b'})
        self.stop.assert_not_called()

    def test_busy_and_unknown_tasks_wait_without_native_mutation_then_idle_releases(self):
        preview = self.scope.preview(self.thread_id, 'key-a')
        operation = str(uuid.uuid4())
        self.idle.side_effect = CliTasksPending('busy fixture')
        self.assertEqual(self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])['status'], 'waiting')
        self.stop.assert_not_called()
        self.idle.side_effect = AppServerUnavailable('native task check unavailable')
        self.assertEqual(self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])['status'], 'waiting')
        self.idle.side_effect = None
        self.assertEqual(self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])['status'], 'released')
        self.stop.assert_called_once()

    def test_changed_configuration_ends_wait_without_native_action(self):
        preview = self.scope.preview(self.thread_id, 'key-a')
        operation = str(uuid.uuid4())
        self.idle.side_effect = CliTasksPending('busy fixture')
        self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])
        (self.home/'config.toml').write_text('model="changed"')
        with self.assertRaisesRegex(ValueError, '重新预览'):
            self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'failed')
        self.stop.assert_not_called()

    def test_released_old_receipt_does_not_disconnect_new_connection(self):
        preview = self.scope.preview(self.thread_id, 'key-a')
        self.scope.release(self.thread_id, 'key-a', str(uuid.uuid4()), preview['generation'])
        session = self.bridge.session(self.thread_id, attach=False, background=True)
        session.connected, session.runtime = True, 'cli'
        self.cli.metadata[self.thread_id] = {'connectionToken': 'new-token'}
        self.bridge.released_cli.set(self.thread_id, False)
        result = self.scope.check('key-a')
        self.assertEqual(result['status'], 'released')
        self.scope.record_released(result)
        self.assertTrue(session.connected)
        self.assertFalse(self.bridge.released_cli.contains(self.thread_id))
        self.stop.assert_called_once()

    def test_scope_previews_reject_keys_from_other_scope(self):
        manager = self.manager()
        with self.assertRaisesRegex(ValueError, '不属于当前范围'):
            manager.submit({'mode': 'scopePreview', 'scopeKey': 'other-home', 'connections': ['key-a']})
        self.stop.assert_not_called()

    def test_wait_cancel_and_explicit_force_upgrade_keep_unknown_business_receipt(self):
        manager = self.manager()
        scope_key = self.scope.descriptor()['scopeKey']
        preview = manager.submit({'mode': 'scopePreview', 'scopeKey': scope_key, 'connections': ['key-a']})
        body = {'mode': 'scopeWait', 'id': str(uuid.uuid4()), 'scopeKey': scope_key,
                'connections': preview['connections'], 'generation': preview['generation']}
        self.idle.side_effect = CliTasksPending('busy fixture')
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            waiting = manager.submit(body)
        self.scope.release(self.thread_id, 'key-a', waiting['id'], manager.jobs[waiting['id']]['scopeGeneration'])
        self.bridge.submissions[self.thread_id + ':unknown'] = {'status': 'unknown', 'text': 'retained'}
        preview = manager.submit({'mode': 'scopePreview', 'scopeKey': scope_key, 'connections': ['key-b']})
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            forced = manager.submit({'mode': 'scopeForce', 'id': str(uuid.uuid4()), 'scopeKey': scope_key,
                'connections': preview['connections'], 'generation': preview['generation']})
        manager._run_scope(manager.jobs[forced['id']], manager.callbacks)
        self.force.assert_called_once()
        self.stop.assert_not_called()
        self.assertEqual(manager.jobs[waiting['id']]['results'][0]['status'], 'superseded')
        self.assertEqual(self.bridge.submissions[self.thread_id + ':unknown']['status'], 'unknown')

    def test_cancel_wait_leaves_writer_running(self):
        self.idle.side_effect = CliTasksPending('busy fixture')
        preview = self.scope.preview(self.thread_id, 'key-a')
        operation = str(uuid.uuid4())
        self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])
        self.assertEqual(self.scope.cancel(operation)['status'], 'cancelled')
        with self.assertRaisesRegex(ValueError, '取消'):
            self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])
        self.stop.assert_not_called()

    def test_scope_confirmation_rejects_changed_selected_keys(self):
        manager = self.manager()
        preview = manager.submit({'mode': 'scopePreview', 'scopeKey': self.scope.descriptor()['scopeKey'], 'all': True})
        with patch.object(manager, '_connections', return_value=self.rows()[:1]):
            with self.assertRaisesRegex(ValueError, '变化'):
                manager.submit({'mode': 'scopeWait', 'id': str(uuid.uuid4()), 'scopeKey': preview['scopeKey'],
                                'connections': preview['connections'], 'generation': preview['generation']})
        self.stop.assert_not_called()
        self.assertEqual(manager.jobs, {})

    def test_manager_restart_observes_unknown_exit_without_replaying(self):
        manager = self.manager()
        preview = manager.submit({'mode': 'scopePreview', 'scopeKey': self.scope.descriptor()['scopeKey'], 'all': True})
        self.stop.side_effect = OSError('normal exit acknowledgment unknown')
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            job = manager.submit({'mode': 'scopeWait', 'id': str(uuid.uuid4()), 'scopeKey': preview['scopeKey'],
                                  'connections': preview['connections'], 'generation': preview['generation']})
        manager._run_scope(manager.jobs[job['id']], manager.callbacks)
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'draining')
        reopened = self.manager()
        self.assertTrue(all(row['status'] == 'unknown' for row in reopened.jobs[job['id']]['results']))
        for absent in (False, True):
            with patch('bridge.features.sessions.cli_scope.daemon_generation_absent', return_value=absent), \
                    patch('bridge.features.sessions.cli_release.threading.Thread'):
                check = reopened.submit({'mode': 'check', 'id': str(uuid.uuid4()), 'connections': preview['connections']})
            with patch('bridge.features.sessions.cli_scope.daemon_generation_absent', return_value=absent):
                reopened._run(reopened.jobs[check['id']], reopened.callbacks)
            self.assertTrue(all(row['status'] == ('released' if absent else 'unknown')
                                for row in reopened.jobs[check['id']]['results']))
        self.stop.assert_called_once()

    def test_cancel_at_final_idle_checkpoint_never_sends_exit(self):
        preview = self.scope.preview(self.thread_id, 'key-a')
        operation = str(uuid.uuid4())
        def idle(*_):
            if self.idle.call_count == 2:
                self.scope.cancel(operation)
        self.idle.side_effect = idle
        with self.assertRaisesRegex(ValueError, '取消|变化'):
            self.scope.release(self.thread_id, 'key-a', operation, preview['generation'])
        self.assertEqual(self.bridge._cli_recovery_record()['phase'], 'cancelled')
        self.stop.assert_not_called()

    def test_concurrent_duplicate_confirmation_creates_only_one_worker(self):
        manager = self.manager()
        preview = manager.submit({'mode': 'scopePreview', 'scopeKey': self.scope.descriptor()['scopeKey'], 'all': True})
        body = {'mode': 'scopeWait', 'id': str(uuid.uuid4()), 'scopeKey': preview['scopeKey'],
                'connections': preview['connections'], 'generation': preview['generation']}
        barrier, results, errors = threading.Barrier(2), [], []
        original = manager._scope_preview
        def preview_together(value):
            result = original(value)
            barrier.wait(timeout=2)
            return result
        def submit():
            try:
                results.append(manager.submit(body))
            except BaseException as error:
                errors.append(error)
        workers = [threading.Thread(target=submit) for _ in range(2)]
        with patch.object(manager, '_scope_preview', side_effect=preview_together), \
                patch('bridge.features.sessions.cli_release.threading.Thread') as background:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(3)
                self.assertFalse(worker.is_alive())
            background.assert_called_once()
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.stop.assert_not_called()

    def test_public_cancel_during_read_and_rejection_after_dispatch_checkpoint(self):
        for stage in ('read', 'exit'):
            with self.subTest(stage=stage):
                manager = self.manager()
                entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
                def pause(*_args, **_kwargs):
                    entered.set()
                    if not resume.wait(3):
                        raise AssertionError('fixture wait was not released')
                self.idle.side_effect = pause if stage == 'read' else None
                self.stop.reset_mock()
                self.stop.side_effect = pause if stage == 'exit' else None
                preview = manager.submit({'mode': 'scopePreview', 'scopeKey': self.scope.descriptor()['scopeKey'], 'all': True})
                original = self.scope.release
                def release(*args):
                    try:
                        return original(*args)
                    finally:
                        finished.set()
                with patch.object(self.scope, 'release', side_effect=release):
                    job = manager.submit({'mode': 'scopeWait', 'id': str(uuid.uuid4()), 'scopeKey': preview['scopeKey'],
                                          'connections': preview['connections'], 'generation': preview['generation']})
                    try:
                        self.assertTrue(entered.wait(2))
                        if stage == 'read':
                            cancelled = manager.submit({'mode': 'scopeCancel', 'id': job['id']})
                            self.assertTrue(all(row['status'] == 'cancelled' for row in cancelled['results']))
                        else:
                            with self.assertRaisesRegex(ValueError, '退出阶段'):
                                manager.submit({'mode': 'scopeCancel', 'id': job['id']})
                    finally:
                        resume.set()
                        self.assertTrue(finished.wait(3))
                self.assertEqual(self.stop.call_count, 0 if stage == 'read' else 1)
                manager.close()

    def test_public_cancel_waiting_for_other_maintenance_preserves_that_receipt(self):
        for phase in ('waiting', 'resuming'):
            with self.subTest(phase=phase):
                other = self.waiting(phase)
                manager, attempted = self.manager(), threading.Event()
                preview = manager.submit({'mode': 'scopePreview', 'scopeKey': self.scope.descriptor()['scopeKey'], 'all': True})
                original = self.scope.release
                def release(*args):
                    try:
                        return original(*args)
                    finally:
                        attempted.set()
                with patch.object(self.scope, 'release', side_effect=release):
                    job = manager.submit({'mode': 'scopeWait', 'id': str(uuid.uuid4()), 'scopeKey': preview['scopeKey'],
                                          'connections': preview['connections'], 'generation': preview['generation']})
                    self.assertTrue(attempted.wait(2))
                    result = manager.submit({'mode': 'scopeCancel', 'id': job['id']})
                self.assertTrue(all(row['status'] == 'cancelled' for row in result['results']))
                self.assertEqual(self.bridge._cli_recovery_record(), other)
                self.stop.assert_not_called()
                manager.close()
