"""CLI access at the bridge and authenticated HTTP boundaries, using only fixtures."""
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.api.httpd import GatewayServer
from bridge.app.service import Bridge, LiveSession
from bridge.clients.codex.app_server import AppServerUnavailable, AppServerUnknownResult
from bridge.clients.codex.cli_session import CliSessions
from bridge.clients.codex.ipc import IPCError
from bridge.features.sessions.goal import GoalUnavailable
from bridge.features.sessions.store import SessionStore
from bridge.features.auth.auth import password_record


ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.tmp').mkdir(exist_ok=True)


class CliBridgeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.home = Path(self.temp.name)
        self.thread_id = str(uuid.uuid4())
        self.private_text = 'Private CLI conversation text'
        rollout = self.home / 'sessions' / 'rollout.jsonl'
        rollout.parent.mkdir()
        rollout.write_text(json.dumps({'type': 'response_item', 'payload': {'type': 'message',
            'role': 'assistant', 'content': [{'type': 'text', 'text': self.private_text}]}}) + '\n')
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, '
                       'originator TEXT, source TEXT, rollout_path TEXT, model_provider TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)',
                       (self.thread_id, 'Private CLI title', '/private/user/project', 1, 0,
                        'codex_cli_rs', 'cli', str(rollout), 'openai'))
        self.native_state = {'id': self.thread_id, 'title': 'CLI fixture', 'cwd': '/private/user/project',
                             'modelProvider': 'openai', 'latestModel': 'test-model',
                             'threadRuntimeStatus': {'type': 'idle'}, 'turns': [], 'requests': []}
        self.cli = Mock()
        self.cli.available.return_value = True
        self.cli.attach.return_value = self.native_state
        self.cli.call.return_value = {'ok': True}
        self.bridge = Bridge(self.home, self.home / 'bridge-data', codex_bin=self.home / 'no-codex',
                             cli_sessions=self.cli)
        self.bridge.ipc.connect = Mock(side_effect=IPCError('desktop absent'))
        self.bridge.ipc.owner = Mock(side_effect=IPCError('no-client-found'))
        self.bridge.ipc.follow = Mock(side_effect=AssertionError('desktop follow'))

    def tearDown(self):
        deadline = time.monotonic() + 2
        while any(session.connecting for session in list(self.bridge.live.values())) and time.monotonic() < deadline:
            time.sleep(.01)
        self.bridge.close()
        self.temp.cleanup()

    def wait_for_background_read(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            session = self.bridge.live.get(self.thread_id)
            if session and not session.connecting and session.history_state is not None:
                return
            time.sleep(.01)
        self.fail('passive history read did not settle')


class CliBridgeTests(CliBridgeFixture):
    def use_ide_origin(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute("UPDATE threads SET originator='codex_vscode', source='vscode'")

    def test_cli_origin_ignores_existing_app_owner_and_resumes_cli_directly(self):
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner = Mock(return_value='desktop-owner')
        session = self.bridge.activate(self.thread_id)
        self.assertEqual((session.origin, session.runtime), ('cli', 'cli'))
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.owner.assert_not_called()
        self.bridge.ipc.follow.assert_not_called()

    def test_cli_native_failure_never_activates_or_reuses_app(self):
        self.cli.attach.side_effect = AppServerUnavailable('writer unavailable')
        with patch('bridge.app.service.open_in_desktop') as navigate:
            with self.assertRaisesRegex(IPCError, 'writer unavailable'):
                self.bridge.activate(self.thread_id)
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.owner.assert_not_called()
        navigate.assert_not_called()

    def test_cli_project_list_uses_target_path_names_without_connecting(self):
        local_cwd = r'\\?\C:\Work\LocalProject'
        remote_cwd = r'C:\Work\RemoteProject'
        posix_cwd = r'/work/Project\notes'
        self.bridge.store.list = Mock(return_value=[{'origin': 'cli', 'cwd': local_cwd}])
        self.bridge.hosts.state = Mock(return_value={
            'codex-managed-remote-connections': [{'hostId': 'ssh', 'alias': 'fixture', 'displayName': 'Server'}]})
        remote = Mock()
        remote.store.list.return_value = [{'origin': 'cli', 'cwd': remote_cwd},
                                        {'origin': 'cli', 'cwd': posix_cwd}]
        self.bridge.for_host = Mock(return_value=remote)
        projects = self.bridge.projects()
        self.assertEqual([(p['name'], p['cwd'], p['host']) for p in projects], [
            ('LocalProject', local_cwd, 'local'), ('RemoteProject', remote_cwd, 'ssh'),
            (r'Project\notes', posix_cwd, 'ssh')])
        self.assertTrue(all(p['type'] == 'cli' for p in projects))
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()

    def test_explicit_continue_reuses_inflight_absent_owner_probe(self):
        self.use_ide_origin()
        started, release, finished, waiting = (threading.Event() for _ in range(4))
        self.bridge.ipc.connect = Mock()
        def owner(*_args, **_kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('owner probe was not released')
            finished.set()
            raise IPCError('no-client-found')
        self.bridge.ipc.owner = Mock(side_effect=owner)
        def attach(*_args, **_kwargs):
            self.assertTrue(finished.is_set(), 'CLI attached before the owner probe completed')
            return self.native_state
        self.cli.attach.side_effect = attach
        session = self.bridge.session(self.thread_id, background=True)
        original_wait = session.condition.wait_for
        def await_probe(*args, **kwargs):
            waiting.set()
            return original_wait(*args, **kwargs)
        outcome = {}
        def activate():
            try:
                outcome['session'] = self.bridge.activate(self.thread_id)
            except BaseException as exc:
                outcome['error'] = exc
        worker = threading.Thread(target=activate)
        try:
            self.assertTrue(started.wait(5), 'background owner probe did not start')
            with patch.object(session.condition, 'wait_for', side_effect=await_probe):
                worker.start()
                self.assertTrue(waiting.wait(5), 'explicit continue did not wait for the in-flight probe')
                self.cli.attach.assert_not_called()
                self.bridge.ipc.owner.assert_called_once()
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(5)
        self.assertFalse(worker.is_alive(), 'explicit continue did not finish')
        if 'error' in outcome:
            raise outcome['error']
        self.assertIs(outcome['session'], session)
        self.assertEqual(session.runtime, 'cli')
        self.bridge.ipc.owner.assert_called_once()
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.bridge._cli_disconnected()
        self.bridge.activate(self.thread_id)
        self.assertEqual(self.bridge.ipc.owner.call_count, 2)
        self.assertEqual(self.cli.attach.call_count, 2)

    def test_explicit_continue_reuses_inflight_found_owner_without_snapshot(self):
        self.use_ide_origin()
        started = threading.Event()
        self.bridge.ipc.connect = Mock()
        def owner(*_args, **_kwargs):
            started.set()
            time.sleep(.08)
            return 'desktop-owner'
        self.bridge.ipc.owner = Mock(side_effect=owner)
        self.bridge.ipc.follow = Mock()
        original = self.bridge._attach
        with patch.object(self.bridge, '_attach', side_effect=lambda session: original(session, timeout=.08)):
            self.bridge.session(self.thread_id, background=True)
            self.assertTrue(started.wait(2))
            with self.assertRaisesRegex(IPCError, 'Codex App'):
                self.bridge.activate(self.thread_id)
        self.bridge.ipc.owner.assert_called_once()
        self.cli.attach.assert_not_called()

    def test_inflight_owner_wait_covers_one_complete_cold_probe(self):
        self.use_ide_origin()
        started, release = threading.Event(), threading.Event()
        self.bridge.ipc.connect = Mock()
        def owner(*_args, **_kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('owner probe was not released')
            raise IPCError('no-client-found')
        self.bridge.ipc.owner = Mock(side_effect=owner)
        session = self.bridge.session(self.thread_id, background=True)
        wait_for = session.condition.wait_for
        def await_probe(predicate, timeout=None):
            # socket 5 + initialize 6 + owner 7 + snapshot 1.5 seconds.
            self.assertGreaterEqual(timeout, 19.5)
            self.assertLessEqual(timeout, 20)
            release.set()
            return wait_for(predicate, timeout=timeout)
        try:
            self.assertTrue(started.wait(2))
            with patch.object(session.condition, 'wait_for', side_effect=await_probe):
                self.assertTrue(self.bridge.activate(self.thread_id).connected)
        finally:
            release.set()
        self.bridge.ipc.owner.assert_called_once()
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_overlapping_attach_and_background_continue_reuse_one_owner_probe(self):
        self.use_ide_origin()
        started, release, queued, waiting = (threading.Event() for _ in range(4))
        session = self.bridge.session(self.thread_id, attach=False)
        lock = session.attach_lock
        class ObservedLock:
            def __enter__(self):
                if lock.locked():
                    queued.set()
                lock.acquire()
            def __exit__(self, *_args):
                lock.release()
        session.attach_lock = ObservedLock()
        self.bridge.ipc.connect = Mock()
        def owner(*_args, **_kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('owner probe was not released')
            raise IPCError('no-client-found')
        self.bridge.ipc.owner = Mock(side_effect=owner)
        wait_for = session.condition.wait_for
        def await_probe(*args, **kwargs):
            waiting.set()
            return wait_for(*args, **kwargs)
        outcome = {}
        def activate():
            try:
                outcome['session'] = self.bridge.activate(self.thread_id)
            except BaseException as exc:
                outcome['error'] = exc
        reader = threading.Thread(target=self.bridge._attach, args=(session,))
        activation = threading.Thread(target=activate)
        try:
            reader.start()
            self.assertTrue(started.wait(2))
            self.bridge._refresh_async(session)
            self.assertTrue(queued.wait(2), 'background probe did not queue behind the existing read')
            for _ in range(3):
                self.bridge._refresh_async(session)
            with patch.object(session.condition, 'wait_for', side_effect=await_probe):
                activation.start()
                self.assertTrue(waiting.wait(2))
                self.cli.attach.assert_not_called()
                release.set()
                activation.join(2)
        finally:
            release.set()
            reader.join(2)
            if activation.ident is not None:
                activation.join(2)
        self.assertFalse(activation.is_alive())
        if 'error' in outcome:
            raise outcome['error']
        self.assertIs(outcome['session'], session)
        self.bridge.ipc.owner.assert_called_once()
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_close_wakes_explicit_continue_without_waiting_for_owner(self):
        self.use_ide_origin()
        started, release, waiting = (threading.Event() for _ in range(3))
        self.bridge.ipc.connect = Mock()
        def owner(*_args, **_kwargs):
            started.set()
            release.wait(5)
            raise IPCError('no-client-found')
        self.bridge.ipc.owner = Mock(side_effect=owner)
        session = self.bridge.session(self.thread_id, background=True)
        wait_for = session.condition.wait_for
        def await_probe(*args, **kwargs):
            waiting.set()
            return wait_for(*args, **kwargs)
        outcome = {}
        def activate():
            try:
                self.bridge.activate(self.thread_id)
            except IPCError as exc:
                outcome['error'] = str(exc)
        worker = threading.Thread(target=activate)
        try:
            self.assertTrue(started.wait(2))
            with patch.object(session.condition, 'wait_for', side_effect=await_probe):
                worker.start()
                self.assertTrue(waiting.wait(2))
                self.bridge.close()
                worker.join(.5)
                self.assertFalse(worker.is_alive(), 'closed gateway still waited for owner discovery')
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(2)
        self.assertIn('error', outcome)
        self.cli.attach.assert_not_called()

    def test_old_epoch_owner_result_cannot_be_reused_or_overwrite_new_epoch(self):
        self.use_ide_origin()
        started, release, queued, waiting = (threading.Event() for _ in range(4))
        session = self.bridge.session(self.thread_id, attach=False)
        lock = session.attach_lock
        class ObservedLock:
            def __enter__(self):
                if lock.locked():
                    queued.set()
                lock.acquire()
            def __exit__(self, *_args):
                lock.release()
        session.attach_lock = ObservedLock()
        self.bridge.ipc.connect = Mock()
        attempts = []
        def owner(*_args, **_kwargs):
            attempts.append(session.connection_epoch)
            if len(attempts) == 1:
                started.set()
                if not release.wait(5):
                    raise AssertionError('old owner probe was not released')
                raise IPCError('no-client-found')
            self.assertIsNone(session.desktop_owner_probe, 'old absence overwrote the new epoch')
            raise IPCError('new owner unknown')
        self.bridge.ipc.owner = Mock(side_effect=owner)
        wait_for = session.condition.wait_for
        def await_probe(*args, **kwargs):
            waiting.set()
            return wait_for(*args, **kwargs)
        outcome = {}
        def activate():
            try:
                outcome['session'] = self.bridge.activate(self.thread_id)
            except IPCError as exc:
                outcome['error'] = str(exc)
        reader = threading.Thread(target=self.bridge._attach, args=(session,))
        activation = threading.Thread(target=activate)
        try:
            reader.start()
            self.assertTrue(started.wait(2))
            self.bridge._invalidate(session, 'router disconnected', runtime='desktop',
                                    reason='desktop_ipc_disconnected')
            self.bridge._refresh_async(session, force=True)
            self.assertTrue(queued.wait(2))
            with patch.object(session.condition, 'wait_for', side_effect=await_probe):
                activation.start()
                self.assertTrue(waiting.wait(2))
                release.set()
                activation.join(2)
        finally:
            release.set()
            reader.join(2)
            if activation.ident is not None:
                activation.join(2)
        self.assertFalse(activation.is_alive())
        self.assertEqual(attempts, [0, 1])
        self.assertEqual(outcome, {'error': 'new owner unknown'})
        self.assertEqual(session.desktop_owner_probe, 'unknown')
        self.cli.attach.assert_not_called()

    def test_owner_wait_expiry_keeps_one_probe_pending_without_native_resume(self):
        self.use_ide_origin()
        started, release = threading.Event(), threading.Event()
        self.bridge.ipc.connect = Mock()
        def owner(*_args, **_kwargs):
            started.set()
            release.wait(5)
            raise IPCError('no-client-found')
        self.bridge.ipc.owner = Mock(side_effect=owner)
        session = self.bridge.session(self.thread_id, background=True)
        try:
            self.assertTrue(started.wait(2))
            with patch.object(session.condition, 'wait_for', return_value=False):
                for _ in range(2):
                    with self.assertRaisesRegex(IPCError, '桌面连接检查未完成'):
                        self.bridge.activate(self.thread_id)
            self.assertTrue(session.owner_probe_inflight)
            self.assertIsNone(session.desktop_owner_probe)
            self.bridge.ipc.owner.assert_called_once()
            self.cli.attach.assert_not_called()
        finally:
            release.set()

    def test_unknown_owner_result_does_not_enable_native_resume(self):
        self.use_ide_origin()
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner = Mock(side_effect=IPCError('owner discovery unavailable'))
        with self.assertRaisesRegex(IPCError, 'owner discovery unavailable'):
            self.bridge.activate(self.thread_id)
        self.assertEqual(self.bridge.live[self.thread_id].desktop_owner_probe, 'unknown')
        self.cli.attach.assert_not_called()

    def test_ready_ipc_absent_owner_continues_without_added_delay(self):
        self.use_ide_origin()
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner = Mock(side_effect=IPCError('no-client-found'))
        started = time.monotonic()
        self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.assertLess(time.monotonic() - started, 1)
        self.bridge.ipc.owner.assert_called_once()
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_missing_native_runtime_explains_how_to_enable_continuation(self):
        self.cli.available.return_value = False
        view = self.bridge.view(self.thread_id, attach=False)
        self.assertFalse(view['access']['canReconnect'])
        self.assertIn('Codex CLI', view['access']['reason'])
        self.assertIn('安装或更新', view['access']['reason'])
        self.cli.attach.assert_not_called()

    def test_passive_view_reads_saved_history_without_native_join_or_navigation(self):
        with patch('bridge.app.service.open_in_desktop') as navigation:
            view = self.bridge.view(self.thread_id, background=True)
            self.wait_for_background_read()
        self.assertFalse(view['connected'])
        self.assertEqual(view['access']['source'], 'cli')
        self.assertFalse(view['access']['canSend'])
        self.assertIn(self.private_text, str(self.bridge.view(self.thread_id, attach=False)))
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.owner.assert_not_called()
        self.bridge.ipc.follow.assert_not_called()
        navigation.assert_not_called()

    def test_full_history_uses_saved_rollout_without_attaching_either_runtime(self):
        with patch.object(self.bridge.store, 'history', wraps=self.bridge.store.history) as history:
            result = self.bridge.full_history(self.thread_id)
        self.assertEqual(result, {'loaded': True})
        self.assertTrue(any(call.kwargs.get('turn_limit') is None for call in history.call_args_list))
        self.assertIn(self.private_text, str(self.bridge.view(self.thread_id, attach=False)))
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.follow.assert_not_called()

    def test_passive_cli_timeline_follows_later_terminal_turns(self):
        self.bridge.view(self.thread_id, background=True)
        self.wait_for_background_read()
        first = self.bridge.timeline_read(self.thread_id)
        rollout = self.home / 'sessions' / 'rollout.jsonl'
        with rollout.open('a') as stream:
            for record in (
                {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'later'}},
                {'type': 'turn_context', 'payload': {'turn_id': 'later', 'model': 'actual-model'}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'text', 'text': 'later request'}]}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'text', 'text': 'later reply'}]}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'later'}},
            ):
                stream.write(json.dumps(record) + '\n')
        session = self.bridge.live[self.thread_id]
        owner_retry = session.retry_at
        self.assertGreater(owner_retry, time.monotonic())
        session.history_retry_at = 0
        self.bridge.timeline_read(self.thread_id)
        deadline = time.monotonic() + 2
        while session.history_refreshing and time.monotonic() < deadline:
            time.sleep(.01)
        later = self.bridge.timeline_read(self.thread_id, 'changes', after=first['sequence'], epoch=first['epoch'], start=first['before'])
        self.assertIn('later reply', [row['text'] for row in later['rows']])
        self.assertEqual(session.retry_at, owner_retry)
        self.assertIn(self.private_text, [row['text'] for row in self.bridge.timeline_read(self.thread_id)['rows']])
        sequence = later['sequence']
        saved_view = self.bridge.live[self.thread_id].saved_view
        session.history_retry_at = 0
        self.bridge.timeline_read(self.thread_id)
        deadline = time.monotonic() + 2
        while session.history_refreshing and time.monotonic() < deadline:
            time.sleep(.01)
        unchanged = self.bridge.timeline_read(self.thread_id, 'changes', after=sequence, epoch=first['epoch'], start=first['before'])
        self.assertIs(self.bridge.live[self.thread_id].saved_view, saved_view)
        self.assertEqual(unchanged['rows'], [])
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()

    def test_desktop_snapshot_with_same_id_cannot_take_over_cli(self):
        session = self.bridge.activate(self.thread_id)
        original = session.state
        desktop = {**self.native_state, 'title': 'Desktop copy', 'modelProvider': 'other-provider'}
        self.bridge._event({'method': 'thread-stream-state-changed', 'version': 11,
                            'sourceClientId': 'desktop-owner', 'params': {
                                'hostId': 'local', 'conversationId': self.thread_id,
                                'change': {'type': 'snapshot', 'revision': 1, 'conversationState': desktop}}})
        self.assertIs(session.state, original)
        self.assertEqual(session.owner, 'cli:' + self.thread_id)
        self.assertEqual(session.runtime, 'cli')
        self.assertTrue(session.connected)

    def desktop_owner(self, state=None):
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner = Mock(return_value='desktop-owner')
        snapshot = dict(self.native_state if state is None else state)
        snapshot.setdefault('hostId', 'local')
        def follow(thread_id, owner, enabled=True, **kwargs):
            if not enabled:
                return
            self.assertEqual((thread_id, owner, kwargs.get('host')), (self.thread_id, 'desktop-owner', 'local'))
            self.bridge._event({'method': 'thread-stream-state-changed', 'version': 11,
                                'sourceClientId': 'desktop-owner', 'params': {
                                    'hostId': 'local', 'conversationId': self.thread_id,
                                    'change': {'type': 'snapshot', 'revision': 1, 'conversationState': snapshot}}})
        self.bridge.ipc.follow = Mock(side_effect=follow)

    def test_existing_desktop_owner_of_ide_origin_is_followed_without_native_resume(self):
        self.use_ide_origin()
        self.cli.available.return_value = False
        self.desktop_owner()
        session = self.bridge.activate(self.thread_id)
        self.assertEqual(session.runtime, 'desktop')
        self.assertEqual(session.owner, 'desktop-owner')
        self.assertEqual(session.origin, 'ide')
        self.assertEqual(session.access['source'], 'ide')
        self.assertTrue(session.access['canReconnect'])
        self.assertTrue(session.access['canChangeModel'])
        self.assertTrue(session.access['canSelectSkills'])
        self.assertTrue(session.access['canUseModes'])
        self.assertTrue(session.access['canConfigure'])
        self.assertTrue(session.access['canChangePermissions'])
        self.cli.attach.assert_not_called()
        self.bridge.ipc.request = Mock(return_value={'result': {'ok': True}})
        self.bridge.send(self.thread_id, 'fixture message', str(uuid.uuid4()))
        self.assertEqual(self.bridge.ipc.request.call_args.args[0], 'thread-follower-start-turn')
        self.assertEqual(self.bridge.ipc.request.call_args.kwargs['target'], 'desktop-owner')
        self.cli.call.assert_not_called()
        self.bridge._cli_state(self.thread_id, self.native_state)
        self.bridge._cli_disconnected()
        self.assertEqual(session.runtime, 'desktop')
        self.assertTrue(session.connected)
        self.bridge._event({'method': 'client-status-changed', 'params': {
            'status': 'disconnected', 'clientId': 'desktop-owner'}})
        self.assertFalse(session.connected)

    def test_desktop_owner_disconnect_after_goal_has_history_only_window_then_rejoins(self):
        self.use_ide_origin()
        completed = {**self.native_state, 'threadGoal': {
            'objective': 'Fixture goal', 'status': 'complete'}}
        self.desktop_owner(completed)
        with self.assertLogs('bridge.app.service', level='INFO') as captured:
            session = self.bridge.activate(self.thread_id)
            self.assertEqual(session.runtime, 'desktop')
            deadline = time.monotonic() + 2
            while session.connecting and time.monotonic() < deadline:
                time.sleep(.01)
            self.bridge._event({'method': 'client-status-changed', 'params': {
                'status': 'disconnected', 'clientId': 'desktop-owner'}})
            disconnected = self.bridge.view(self.thread_id, attach=False)
            self.assertFalse(disconnected['connected'])
            self.assertTrue(disconnected['access']['readOnly'])
            self.assertEqual(session.desktop_owner_probe, None)
            self.bridge._refresh_async(session, force=True)
            deadline = time.monotonic() + 2
            while session.connecting and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(session.connecting)
            self.assertTrue(session.connected)
            self.assertEqual(session.runtime, 'desktop')
            self.cli.attach.assert_not_called()
        transitions = '\n'.join(captured.output)
        self.assertIn('cause=desktop_client_disconnected', transitions)
        self.assertEqual(transitions.count('cause=desktop_owner_attached'), 2)
        self.assertNotIn(self.private_text, transitions)
        self.assertNotIn('Fixture goal', transitions)

    def test_desktop_owner_identity_must_match_saved_cli_cwd_and_provider(self):
        self.use_ide_origin()
        for field, value in [('cwd', '/other/project'), ('modelProvider', 'other'), ('hostId', 'other')]:
            with self.subTest(field=field):
                self.desktop_owner({**self.native_state, field: value})
                session = self.bridge.session(self.thread_id, attach=True)
                self.assertFalse(session.connected)
                self.assertEqual(session.origin, 'ide')
                self.cli.attach.assert_not_called()
                self.bridge.live.pop(self.thread_id)

    def test_late_desktop_and_cli_events_cannot_switch_the_active_writer(self):
        session = self.bridge.activate(self.thread_id)
        self.assertEqual(session.runtime, 'cli')
        self.desktop_owner()
        self.bridge._event({'method': 'thread-stream-state-changed', 'version': 11,
                            'sourceClientId': 'desktop-owner', 'params': {'hostId': 'local',
                                'conversationId': self.thread_id, 'change': {'type': 'snapshot',
                                    'revision': 9, 'conversationState': self.native_state}}})
        self.assertEqual(session.runtime, 'cli')
        self.assertEqual(session.owner, 'cli:' + self.thread_id)

    def test_late_native_state_cannot_replace_a_discovered_desktop_owner(self):
        self.use_ide_origin()
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner = Mock(return_value='desktop-owner')
        self.bridge.ipc.follow = Mock()
        session = self.bridge.session(self.thread_id, attach=False)
        self.bridge._attach(session, timeout=0)
        self.assertEqual(session.desktop_owner_probe, 'found')
        self.bridge._cli_state(self.thread_id, self.native_state)
        self.assertFalse(session.connected)
        self.assertEqual(session.owner, 'desktop-owner')
        self.bridge._event({'method': 'thread-stream-state-changed', 'version': 11,
                            'sourceClientId': 'desktop-owner', 'params': {'hostId': 'local',
                                'conversationId': self.thread_id, 'change': {'type': 'snapshot',
                                    'revision': 1, 'conversationState': self.native_state}}})
        self.assertEqual(session.runtime, 'desktop')
        self.assertTrue(session.connected)
        self.bridge._disconnected()
        self.assertFalse(session.connected)

    def test_background_owner_probe_cannot_overtake_in_flight_native_resume(self):
        started,release=threading.Event(),threading.Event()
        def attach(thread_id, explicit=False):
            started.set()
            self.assertTrue(release.wait(3))
            return self.native_state
        self.cli.attach.side_effect=attach
        outcome=[]
        worker=threading.Thread(target=lambda: outcome.append(self.bridge.activate(self.thread_id)))
        worker.start();self.assertTrue(started.wait(3))
        session=self.bridge.live[self.thread_id]
        self.assertTrue(session.native_attaching)
        self.desktop_owner()
        late=threading.Thread(target=self.bridge._attach,args=(session,0))
        late.start()
        self.bridge._event({'method': 'thread-stream-state-changed', 'version': 11,
                            'sourceClientId': 'desktop-owner', 'params': {'hostId': 'local',
                                'conversationId': self.thread_id, 'change': {'type': 'snapshot',
                                    'revision': 1, 'conversationState': self.native_state}}})
        release.set();worker.join(3);late.join(3)
        self.assertFalse(worker.is_alive());self.assertFalse(late.is_alive())
        self.assertEqual(outcome, [session])
        self.assertEqual(session.runtime, 'cli')
        self.assertEqual(session.owner, 'cli:' + self.thread_id)
        self.bridge.ipc.owner.assert_not_called()

    def test_stale_native_state_after_disconnect_cannot_reclaim_writer(self):
        self.use_ide_origin()
        self.bridge.ipc.connect = Mock()
        session=self.bridge.activate(self.thread_id)
        self.bridge._cli_disconnected()
        self.assertFalse(session.connected)
        self.bridge._cli_state(self.thread_id, self.native_state)
        self.assertFalse(session.connected)
        self.desktop_owner()
        self.bridge._attach(session, timeout=0)
        self.assertEqual(session.runtime, 'desktop')
        self.assertEqual(session.owner, 'desktop-owner')

    def test_owner_discovery_timeout_without_found_owner_allows_explicit_native_attempt(self):
        self.bridge.ipc.connect=Mock()
        self.bridge.ipc.owner=Mock(side_effect=IPCError('桌面读取超时；尚未取得实时会话状态。'))
        self.bridge.ipc.follow=Mock(side_effect=AssertionError('unclaimed thread must not be followed'))
        session=self.bridge.activate(self.thread_id)
        self.assertEqual(session.runtime, 'cli')
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_found_owner_without_snapshot_never_falls_back_to_native(self):
        self.use_ide_origin()
        self.bridge.ipc.connect=Mock()
        self.bridge.ipc.owner=Mock(return_value='desktop-owner')
        self.bridge.ipc.follow=Mock()
        attach=self.bridge._attach
        with patch.object(self.bridge, '_attach', side_effect=lambda session: attach(session, timeout=0)):
            with self.assertRaisesRegex(IPCError, 'Codex App'):
                self.bridge.activate(self.thread_id)
        self.cli.attach.assert_not_called()

    def test_cli_history_ignores_available_app_owner_and_keeps_saved_history(self):
        self.desktop_owner()
        session = self.bridge.session(self.thread_id, attach=True)
        history = self.bridge.full_history(self.thread_id)
        self.assertEqual(history, {'loaded': True})
        self.assertFalse(session.connected)
        self.assertIn(self.private_text, str(self.bridge.view(self.thread_id, attach=False)))
        self.assertIn(self.private_text, str(self.bridge.timeline_read(self.thread_id, mode='page', limit=20)))
        self.cli.attach.assert_not_called()

    def test_bridge_preserves_native_request_ids_for_approval_and_input(self):
        self.bridge.activate(self.thread_id)
        session = self.bridge.live[self.thread_id]
        for request_id, method, response, expected_method, expected_payload in [
            (73, 'item/commandExecution/requestApproval', {'decision': 'decline'},
             'thread-follower-command-approval-decision', {'decision': 'decline'}),
            (74, 'item/tool/requestUserInput', {'answers': {'choice': ['continue']}},
             'thread-follower-submit-user-input',
             {'response': {'answers': {'choice': {'answers': ['continue']}}}}),
        ]:
            with self.subTest(method=method):
                params = {'questions': [{'id': 'choice'}]} if 'UserInput' in method else {'command': 'pwd'}
                with session.condition:
                    session.state['requests'] = [{'id': request_id, 'method': method, 'params': params}]
                self.bridge.respond(self.thread_id, str(request_id), response)
                self.assertEqual(self.cli.call.call_args.args[1], expected_method)
                self.assertEqual(self.cli.call.call_args.args[2],
                                 {'requestId': request_id, **expected_payload})

    def test_explicit_activate_and_send_route_to_cli_then_disconnect_is_scoped(self):
        with patch('bridge.app.service.open_in_desktop') as navigation:
            session = self.bridge.activate(self.thread_id)
            self.assertTrue(session.connected)
            self.assertEqual(session.runtime, 'cli')
            self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
            self.bridge.ipc.request = Mock(side_effect=AssertionError('desktop write'))
            result = self.bridge.send(self.thread_id, 'hello', str(uuid.uuid4()))
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(self.cli.call.call_args.args[1], 'thread-follower-start-turn')
            self.assertEqual(self.cli.call.call_args.args[2]['turnStart']['request']['input'][0]['text'], 'hello')
            self.bridge._disconnected()
            self.assertTrue(session.connected, 'desktop IPC loss must not drop CLI runtime')
            self.bridge._cli_disconnected()
            self.assertFalse(session.connected)
            navigation.assert_not_called()

    def test_joined_native_cli_model_skill_and_plan_use_same_daemon(self):
        session = self.bridge.activate(self.thread_id)
        self.cli.get_kind.return_value = {'models': [{'id': 'test-model', 'efforts': ['high']}],
                                          'modelSource': 'codex', 'fastMode': {'allowed': False}}
        self.cli.validate_skills.return_value = [{'id': 'fixture-skill', 'name': 'Fixture', 'path': '/skills/fixture'}]
        self.assertTrue(self.bridge.view(self.thread_id, attach=False)['access']['canChangeModel'])
        self.assertFalse(self.bridge.view(self.thread_id, attach=False)['access']['canConfigure'])
        self.assertTrue(self.bridge.view(self.thread_id, attach=False)['access']['canChangePermissions'])
        self.bridge.catalog(self.thread_id, kind='models')
        self.cli.get_kind.assert_called_with(self.thread_id, 'models', refresh=False, query='', offset=0, limit=200, ids=None)
        self.cli.call.return_value = {'applied': True}
        with session.condition:
            session.state['latestReasoningEffort'] = 'high'
        result = self.bridge.settings(self.thread_id, 'test-model', 'high')
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.cli.call.call_args.args[1], 'thread-follower-update-thread-settings')
        self.bridge.send(self.thread_id, 'planned', str(uuid.uuid4()), work_mode='plan', skills=['fixture-skill'])
        request = self.cli.call.call_args.args[2]['turnStart']['request']
        self.assertEqual(request['collaborationMode']['mode'], 'plan')
        self.assertIn({'type': 'skill', 'name': 'Fixture', 'path': '/skills/fixture'}, request['input'])
        self.cli.validate_skills.assert_called_with(self.thread_id, ['fixture-skill'])
        self.cli.get_kind.side_effect = lambda thread_id, kind, **kwargs: (
            {'models': [{'id': 'test-model'}]} if kind == 'models' else {'skills': [{'id': 'fixture-skill'}]})
        combined = self.bridge.catalog(self.thread_id)
        self.assertEqual(combined['models'][0]['id'], 'test-model')
        self.assertEqual(combined['skills'][0]['id'], 'fixture-skill')
        self.cli.get_kind.side_effect = None
        queued_id = str(uuid.uuid4())
        self.bridge.send(self.thread_id, 'queued plan', queued_id, mode='queue', work_mode='plan')
        self.bridge._send_queued(session, self.thread_id + ':' + queued_id,
                                 self.bridge.submissions[self.thread_id + ':' + queued_id])
        self.assertEqual(self.cli.call.call_args.args[2]['turnStart']['request']['collaborationMode']['mode'], 'plan')

    def test_passive_cli_catalog_does_not_join_native_runtime(self):
        self.bridge.catalog_reader.get_kind = Mock(return_value={'models': [], 'fastMode': {'allowed': False}})
        with self.assertRaisesRegex(ValueError, '继续此会话'):
            self.bridge.catalog(self.thread_id, kind='models')
        self.cli.attach.assert_not_called()
        self.cli.get_kind.assert_not_called()
        self.bridge.catalog_reader.get_kind.assert_not_called()

    def test_connected_cli_model_catalog_stays_on_native_daemon(self):
        session = self.bridge.activate(self.thread_id)
        with session.condition:
            session.state['latestReasoningEffort'] = 'high'
        self.bridge.ipc.connect.reset_mock()
        self.bridge.ipc.owner.reset_mock()
        self.bridge.ipc.follow.reset_mock()
        self.bridge.catalog_reader.get_kind = Mock(side_effect=AssertionError('desktop catalog read'))
        self.cli.get_kind.return_value = {'models': [{'id': 'test-model', 'efforts': ['high']}],
                                          'modelSource': 'codex'}
        result = self.bridge.catalog(self.thread_id, kind='models')
        self.assertEqual(result['currentModel'], 'test-model')
        self.assertEqual(result['currentEffort'], 'high')
        self.cli.get_kind.assert_called_once_with(
            self.thread_id, 'models', refresh=False, query='', offset=0, limit=200, ids=None)
        self.bridge.catalog_reader.get_kind.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.owner.assert_not_called()
        self.bridge.ipc.follow.assert_not_called()

    def test_native_permissions_use_daemon_policy_and_effective_state(self):
        session = self.bridge.activate(self.thread_id)
        facts = {'requirements': {}, 'config': {'guardian': True},
                 'profiles': {':workspace': True, ':danger-full-access': True},
                 'guardianEnabled': True}
        options = {'current': 'ask', 'options': [
            {'preset': preset, 'available': True, 'reason': None}
            for preset in ('ask', 'auto-review', 'full-access')]}
        self.cli.permission_options.return_value = (facts, options)
        self.assertEqual(self.bridge.linux_permission_options(self.thread_id), options)
        self.cli.permission_options.assert_called_with(self.thread_id)
        with self.assertRaisesRegex(ValueError, '确认完全访问权限'):
            self.bridge.permissions(self.thread_id, 'full-access')
        self.cli.call.side_effect = lambda thread_id, method, params, **kwargs: (
            self.bridge._cli_state(thread_id, {**self.native_state, 'currentPermissions': {
                'approvalPolicy': 'on-request', 'approvalsReviewer': 'auto_review',
                'activePermissionProfile': {'id': ':workspace'}}}) or {'applied': True})
        result = self.bridge.permissions(self.thread_id, 'auto-review')
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.bridge.view(self.thread_id, attach=False)['permissionMode'], 'auto-review')
        self.assertEqual(self.cli.call.call_args.args[2]['threadSettings'], {
            'permissions': ':workspace', 'approvalPolicy': 'on-request',
            'approvalsReviewer': 'auto_review'})
        self.assertFalse(self.bridge.view(self.thread_id, attach=False)['access']['canConfigure'])
        self.assertEqual(session.runtime, 'cli')

    def test_permission_runtime_mismatch_rejects_before_any_setting_write(self):
        session = self.bridge.activate(self.thread_id)
        with self.assertRaisesRegex(ValueError, '会话运行时已变化'):
            self.bridge.permissions(self.thread_id, 'ask', expected_runtime='desktop')
        self.cli.permission_options.assert_not_called()
        self.cli.call.assert_not_called()
        with self.assertRaisesRegex(ValueError, '会话运行时无效'):
            self.bridge.permissions(self.thread_id, 'ask', expected_runtime='other')
        with self.assertRaisesRegex(ValueError, '确认完全访问权限'):
            self.bridge.permissions(self.thread_id, 'full-access', expected_runtime='cli')
        with session.condition:
            session.runtime = 'desktop'
            session.owner = 'desktop-owner'
        self.bridge.ipc.request = Mock(side_effect=AssertionError('desktop setting write'))
        with self.assertRaisesRegex(ValueError, '会话运行时已变化'):
            self.bridge.permissions(self.thread_id, 'ask', expected_runtime='cli')
        self.bridge.ipc.request.assert_not_called()
        with session.condition:
            session.connected = False

    def test_native_goal_is_probed_and_routed_only_to_joined_daemon(self):
        session = self.bridge.activate(self.thread_id)
        self.cli.supports_goal.return_value = True
        self.cli.get_goal.return_value = None
        view = self.bridge.view(self.thread_id, attach=False)
        self.assertTrue(view['access']['canUseGoal'])
        self.assertTrue(view['goalRuntimeAvailable'])
        self.bridge._goal_call('get_goal', self.thread_id)
        self.cli.get_goal.assert_called_with(self.thread_id)
        self.bridge.goal.get_goal = Mock(side_effect=AssertionError('sidecar goal read'))
        self.bridge.timeline_read(self.thread_id)
        self.assertEqual(session.runtime, 'cli')
        self.bridge._cli_disconnected()
        self.assertIsNone(session.native_goal_supported)
        self.cli.get_goal.side_effect = GoalUnavailable('native unavailable')
        with self.assertRaises(GoalUnavailable):
            self.bridge._native_goal_state(self.thread_id, strict=True)

    def test_unsupported_native_goal_stays_unavailable_without_sidecar_fallback(self):
        self.bridge.activate(self.thread_id)
        self.cli.supports_goal.return_value = False
        view = self.bridge.view(self.thread_id, attach=False)
        self.assertFalse(view['access']['canUseGoal'])
        self.assertFalse(view['goalRuntimeAvailable'])
        self.bridge.goal.get_goal = Mock(side_effect=AssertionError('sidecar goal read'))
        with self.assertRaisesRegex(ValueError, '目标模式'):
            self.bridge.send(self.thread_id, 'objective', str(uuid.uuid4()), work_mode='goal')
        self.cli.set_goal.assert_not_called()

    def test_native_goal_create_pause_resume_cancel_is_durable_and_uses_one_daemon(self):
        session = self.bridge.activate(self.thread_id)
        self.bridge.send(self.thread_id, 'Plan first', str(uuid.uuid4()), work_mode='plan')
        with session.condition:
            session.state['latestCollaborationMode'] = {'mode': 'plan'}
        self.cli.supports_goal.return_value = True
        goal = {'value': None}
        self.cli.get_goal.side_effect = lambda thread_id: goal['value']
        def set_goal(thread_id, objective):
            goal['value'] = {'objective': objective, 'status': 'active'}
            return goal['value']
        def set_status(thread_id, status):
            goal['value'] = {**goal['value'], 'status': status}
            return goal['value']
        def clear_goal(thread_id):
            goal['value'] = None
            return {'cleared': True}
        self.cli.set_goal.side_effect = set_goal
        self.cli.set_goal_status.side_effect = set_status
        self.cli.clear_goal.side_effect = clear_goal
        self.bridge.goal.get_goal = Mock(side_effect=AssertionError('sidecar read'))
        self.bridge.goal.set_goal = Mock(side_effect=AssertionError('sidecar write'))
        created = str(uuid.uuid4())
        result = self.bridge.send(self.thread_id, 'Finish fixture', created, work_mode='goal')
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['activation']['status'], 'accepted')
        created_mode = self.cli.call.call_args.args[2]['turnStart']['request']['collaborationMode']
        self.assertEqual(created_mode['mode'], 'default')
        self.assertEqual(created_mode['settings']['model'], 'test-model')
        self.assertEqual(self.bridge.goal_commands[self.thread_id + ':' + created]['state'], 'confirmed')
        self.bridge.send(self.thread_id, 'Finish fixture', created, work_mode='goal')
        self.cli.set_goal.assert_called_once_with(self.thread_id, 'Finish fixture')
        paused = self.bridge.set_goal_status(self.thread_id, 'paused', str(uuid.uuid4()))
        self.assertTrue(paused['confirmed'])
        resumed = self.bridge.set_goal_status(self.thread_id, 'active', str(uuid.uuid4()))
        self.assertTrue(resumed['confirmed'])
        self.assertEqual(resumed['activation']['status'], 'accepted')
        resumed_mode = self.cli.call.call_args.args[2]['turnStart']['request']['collaborationMode']
        self.assertEqual(resumed_mode['mode'], 'default')
        self.assertEqual(resumed_mode['settings']['model'], 'test-model')
        cancelled = self.bridge.cancel_goal(self.thread_id, str(uuid.uuid4()))
        self.assertTrue(cancelled['confirmed'])
        self.cli.clear_goal.assert_called_once_with(self.thread_id)
        self.assertIsNone(self.bridge.view(self.thread_id, attach=False)['goal'])

    def test_desktop_goal_metadata_control_does_not_activate_owner(self):
        session = self.bridge.session(self.thread_id, attach=False)
        with session.condition:
            session.origin = 'desktop'
        self.bridge._native_goal_state = Mock(return_value={'objective': 'Fixture', 'status': 'paused'})
        self.bridge._goal_command = Mock(return_value={'status': 'cancelled', 'confirmed': True})
        with patch.object(self.bridge, '_target', side_effect=AssertionError('goal metadata activated owner')):
            result = self.bridge.cancel_goal(self.thread_id, str(uuid.uuid4()))
        self.assertTrue(result['confirmed'])
        self.cli.attach.assert_not_called()

    def test_uncertain_native_goal_write_is_never_replayed(self):
        self.bridge.activate(self.thread_id)
        self.cli.supports_goal.return_value = True
        self.cli.get_goal.return_value = None
        self.cli.set_goal.side_effect = GoalUnavailable('native result unknown')
        request_id = str(uuid.uuid4())
        with self.assertRaises(IPCError):
            self.bridge.send(self.thread_id, 'Fixture objective', request_id, work_mode='goal')
        self.assertEqual(self.bridge.goal_commands[self.thread_id + ':' + request_id]['state'], 'unknown')
        result = self.bridge.send(self.thread_id, 'Fixture objective', request_id, work_mode='goal')
        self.assertEqual(result['status'], 'unknown')
        self.cli.set_goal.assert_called_once_with(self.thread_id, 'Fixture objective')


    def test_unknown_native_send_is_not_replayed_after_disconnect(self):
        self.bridge.activate(self.thread_id)
        self.cli.call.side_effect = AppServerUnknownResult('write result unknown')
        submission_id = str(uuid.uuid4())
        with self.assertRaises(IPCError):
            self.bridge.send(self.thread_id, 'unknown write', submission_id)
        self.assertEqual(self.bridge.submissions[self.thread_id + ':' + submission_id]['status'], 'unknown')
        self.bridge._cli_disconnected()
        self.assertEqual(self.cli.call.call_count, 1)
        self.assertFalse(self.bridge.live[self.thread_id].connected)

    def test_restored_live_entry_uses_store_origin_before_activation(self):
        # A queued submission reconstructed at startup creates a LiveSession
        # before the first list or view request.
        self.bridge.live[self.thread_id] = LiveSession(self.thread_id)
        session = self.bridge.session(self.thread_id, attach=False)
        self.assertEqual(session.origin, 'cli')
        self.assertFalse(self.bridge.view(self.thread_id, attach=False)['access']['canConfigure'])



class RemoteCliGoalTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        self.bridge.host = 'remote:test'
        self.bridge.ipc.request = Mock(side_effect=AssertionError('desktop goal writer'))

    def test_joined_remote_cli_goal_uses_native_daemon_and_existing_lifecycle(self):
        session = self.bridge.activate(self.thread_id)
        self.assertEqual((session.origin, session.runtime, session.connected), ('cli', 'cli', True))
        self.cli.supports_goal.return_value = True
        goal = {'value': None}
        self.cli.get_goal.side_effect = lambda thread_id: goal['value']
        def set_goal(thread_id, objective):
            goal['value'] = {'objective': objective, 'status': 'active'}
            return goal['value']
        def set_status(thread_id, status):
            goal['value'] = {**goal['value'], 'status': status}
            return goal['value']
        self.cli.set_goal.side_effect = set_goal
        self.cli.set_goal_status.side_effect = set_status
        self.cli.clear_goal.side_effect = lambda thread_id: goal.update(value=None) or {'cleared': True}
        self.bridge.goal = Mock()
        self.bridge.goal.get_goal.side_effect = AssertionError('local sidecar read')
        self.bridge.goal.set_goal.side_effect = AssertionError('local sidecar write')

        view = self.bridge.view(self.thread_id, attach=False)
        self.assertTrue(view['access']['canUseGoal'])
        self.assertTrue(view['goalRuntimeAvailable'])
        created_id = str(uuid.uuid4())
        created = self.bridge.send(self.thread_id, 'Remote objective', created_id, work_mode='goal')
        self.assertEqual(created['activation']['status'], 'accepted')
        self.assertEqual(self.bridge.goal_commands[self.thread_id + ':' + created_id]['state'], 'confirmed')
        self.bridge.set_goal_status(self.thread_id, 'paused', str(uuid.uuid4()))
        resumed = self.bridge.set_goal_status(self.thread_id, 'active', str(uuid.uuid4()))
        self.assertEqual(resumed['activation']['status'], 'accepted')
        self.bridge.set_goal_status(self.thread_id, 'paused', str(uuid.uuid4()))
        self.cli.edit_goal.side_effect = lambda thread_id, objective, budget: goal.update(value={
            'objective': objective, 'status': 'paused', 'tokenBudget': budget}) or goal['value']
        self.bridge.edit_goal(self.thread_id, 'Revised remote objective', str(uuid.uuid4()))
        self.assertEqual(goal['value']['objective'], 'Revised remote objective')
        self.bridge.cancel_goal(self.thread_id, str(uuid.uuid4()))
        self.assertIsNone(self.bridge.view(self.thread_id, attach=False)['goal'])
        self.cli.set_goal.assert_called_once_with(self.thread_id, 'Remote objective')
        self.cli.clear_goal.assert_called_once_with(self.thread_id)
        self.cli.call.assert_called()

    def test_remote_desktop_owner_and_passive_cli_read_cannot_write_goal(self):
        passive = self.bridge.view(self.thread_id, attach=False)
        self.assertFalse(passive['access']['canUseGoal'])
        self.cli.attach.assert_not_called()
        self.cli.supports_goal.assert_not_called()
        session = self.bridge.session(self.thread_id, attach=False)
        with session.condition:
            session.connected = True
            session.runtime = 'desktop'
            session.owner = 'desktop-owner'
        self.assertFalse(self.bridge.view(self.thread_id, attach=False)['access']['canUseGoal'])
        with self.assertRaisesRegex(ValueError, 'SSH'):
            self.bridge.send(self.thread_id, 'Remote objective', str(uuid.uuid4()), work_mode='goal')
        with self.assertRaisesRegex(ValueError, 'SSH'):
            self.bridge.cancel_goal(self.thread_id, str(uuid.uuid4()))
        self.cli.set_goal.assert_not_called()
        self.cli.clear_goal.assert_not_called()
        self.cli.attach.assert_not_called()
        with session.condition:
            session.connected = False

    def test_unsupported_remote_runtime_never_uses_local_sidecar(self):
        self.bridge.activate(self.thread_id)
        self.cli.supports_goal.return_value = False
        self.bridge.goal = Mock()
        view = self.bridge.view(self.thread_id, attach=False)
        self.assertFalse(view['goalRuntimeAvailable'])
        self.assertFalse(view['access']['canUseGoal'])
        with self.assertRaisesRegex(ValueError, '目标模式'):
            self.bridge.send(self.thread_id, 'Remote objective', str(uuid.uuid4()), work_mode='goal')
        self.bridge.goal.set_goal.assert_not_called()
        self.cli.set_goal.assert_not_called()

    def test_uncertain_remote_goal_write_is_not_replayed(self):
        self.bridge.activate(self.thread_id)
        self.cli.supports_goal.return_value = True
        self.cli.get_goal.return_value = None
        self.cli.set_goal.side_effect = GoalUnavailable('remote response unknown')
        request_id = str(uuid.uuid4())
        with self.assertRaises(IPCError):
            self.bridge.send(self.thread_id, 'Remote objective', request_id, work_mode='goal')
        self.assertEqual(self.bridge.goal_commands[self.thread_id + ':' + request_id]['state'], 'unknown')
        self.bridge.goal_commands = json.loads(self.bridge.goal_commands_path.read_text())
        retry = self.bridge.send(self.thread_id, 'Remote objective', request_id, work_mode='goal')
        self.assertEqual(retry['status'], 'unknown')
        self.cli.set_goal.assert_called_once_with(self.thread_id, 'Remote objective')

class CliDiagnosticsHttpTests(CliBridgeFixture):
    @classmethod
    def setUpClass(cls):
        cls.password = password_record('integration password')

    def setUp(self):
        super().setUp()
        config = {'auth': {'mode': 'password', 'username': 'admin', **self.password}, 'origins': []}
        self.server = GatewayServer(('127.0.0.1', 0), self.bridge, config, ROOT / 'web')
        self.port = self.server.server_address[1]
        self.origin = 'http://127.0.0.1:' + str(self.port)
        self.server.origins.add(self.origin)
        self.server.hosts.add('127.0.0.1:' + str(self.port))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        request_headers = {'Origin': self.origin, 'Content-Type': 'application/json', **(headers or {})}
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers=request_headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), json.loads(response.read())
        conn.close()
        return result

    def test_diagnostics_requires_login_and_exports_only_aggregate_status(self):
        route = '/api/sessions/diagnostics?thread=' + self.thread_id
        self.assertEqual(self.request('GET', route)[0], 401)
        status, headers, login = self.request('POST', '/api/login',
            {'username': 'admin', 'password': 'integration password'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        status, headers, report = self.request('GET', route, headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        local = report['hosts'][0]
        self.assertEqual(local['target']['origin'], 'cli')
        self.assertEqual(local['target']['read'], 'ready')
        self.assertFalse(local['target']['archived'])
        self.assertNotIn(self.private_text, str(report))
        self.assertNotIn('Private CLI title', str(report))
        self.assertNotIn('/private/user', str(report))
        self.assertNotIn(self.thread_id, str(report))
        self.assertEqual(self.request('GET', '/api/sessions/diagnostics?extra=1',
                                      headers={'Cookie': cookie})[0], 400)
        self.assertEqual(self.request('GET', '/api/sessions/diagnostics?thread=not-a-uuid',
                                      headers={'Cookie': cookie})[0], 400)

    def test_reconnect_route_requires_csrf_and_only_explicit_activate_joins_cli(self):
        status, headers, login = self.request('POST', '/api/login',
            {'username': 'admin', 'password': 'integration password'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        route = '/api/sessions/' + self.thread_id
        self.assertEqual(self.request('GET', route, headers={'Cookie': cookie})[0], 200)
        self.wait_for_background_read()
        self.cli.attach.assert_not_called()
        reconnect = route + '/reconnect'
        self.assertEqual(self.request('POST', reconnect, {'activate': True},
                                      headers={'Cookie': cookie})[0], 403)
        auth = {'Cookie': cookie, 'X-CSRF-Token': login['csrf']}
        status, _, result = self.request('POST', reconnect, {'activate': True}, headers=auth)
        self.assertEqual(status, 200)
        self.assertTrue(result['connected'])
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_cli_release_requires_login_and_csrf_and_does_not_activate(self):
        route = '/api/cli-connections'
        self.assertEqual(self.request('GET', route)[0], 401)
        _, headers, login = self.request('POST', '/api/login',
            {'username': 'admin', 'password': 'integration password'})
        cookie = headers['Set-Cookie'].split(';')[0]
        manager = Mock()
        manager.view.return_value = {'connections': [], 'operations': []}
        manager.submit.return_value = {'results': []}
        self.server.cli_connections = manager
        body = {'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'wait'}
        self.assertEqual(self.request('POST', route, body, headers={'Cookie': cookie})[0], 403)
        manager.submit.assert_not_called()
        self.assertEqual(self.request('POST', route, body,
            headers={'Cookie': cookie, 'X-CSRF-Token': login['csrf']})[0], 200)
        manager.submit.assert_called_once_with(body)
        self.cli.attach.assert_not_called()


class CliApprovalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.thread_id = str(uuid.uuid4())
        self.adapter = CliSessions(ROOT / '.tmp' / 'fixture-codex-home', lambda *_: None, lambda: None)
        self.process = object()
        self.adapter.client.process = self.process
        self.adapter.client.is_loaded = Mock(return_value=True)
        self.adapter.client.request = Mock(return_value={'thread': {
            'id': self.thread_id, 'cwd': '/fixture/work', 'modelProvider': 'openai',
            'status': {'type': 'active'}}})
        self.adapter.client.respond = Mock()
        self.adapter.joined[self.thread_id] = self.process
        self.adapter.metadata[self.thread_id] = {'cwd': '/fixture/work', 'modelProvider': 'openai'}

    def test_approval_and_user_input_reply_to_original_jsonrpc_id(self):
        for request_id, method, desktop_method, payload, expected in [
            (73, 'item/commandExecution/requestApproval', 'thread-follower-command-approval-decision',
             {'decision': 'decline'}, {'decision': 'decline'}),
            (74, 'item/tool/requestUserInput', 'thread-follower-submit-user-input',
             {'response': {'answers': {'choice': {'answers': ['continue']}}}},
             {'answers': {'choice': {'answers': ['continue']}}}),
        ]:
            with self.subTest(method=method), patch.object(self.adapter, '_queue_refresh'):
                self.adapter._server_request({'id': request_id, 'method': method,
                                              'params': {'threadId': self.thread_id}})
                self.assertEqual(self.adapter.call(self.thread_id, desktop_method,
                                                  {'requestId': request_id, **payload}), {'submitted': True})
                self.adapter.client.respond.assert_called_with(request_id, expected)
                self.assertNotIn(request_id, self.adapter.requests[self.thread_id])

    def test_disconnect_after_uncertain_approval_does_not_replay_response(self):
        request_id = 91
        self.adapter._server_request({'id': request_id, 'method': 'item/tool/requestUserInput',
                                      'params': {'threadId': self.thread_id}})
        self.adapter.client.respond.side_effect = AppServerUnknownResult('response may have been sent')
        with self.assertRaises(AppServerUnknownResult):
            self.adapter.call(self.thread_id, 'thread-follower-submit-user-input',
                              {'requestId': request_id, 'response': {'answers': {'choice': {'answers': ['yes']}}}})
        self.assertNotIn(request_id, self.adapter.requests[self.thread_id])
        # Even before the delayed disconnect callback, an unknown response
        # must not remain actionable or be replayed by an explicit repeat.
        with self.assertRaises(AppServerUnavailable):
            self.adapter.call(self.thread_id, 'thread-follower-submit-user-input',
                              {'requestId': request_id, 'response': {'answers': {'choice': {'answers': ['yes']}}}})
        self.adapter._disconnect()
        with self.assertRaises(AppServerUnavailable):
            self.adapter.call(self.thread_id, 'thread-follower-submit-user-input',
                              {'requestId': request_id, 'response': {'answers': {'choice': {'answers': ['yes']}}}})
        self.assertEqual(self.adapter.client.respond.call_count, 1)


class CliModelAttributionTests(unittest.TestCase):
    def test_native_snapshot_uses_recorded_model_for_each_turn(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            home = Path(folder)
            thread_id = str(uuid.uuid4())
            rollout = home / 'sessions' / 'rollout.jsonl'
            rollout.parent.mkdir()
            records = []
            for turn_id, model in [('old', 'model-old'), ('new', 'model-new')]:
                records.extend([
                    {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn_id}},
                    {'type': 'turn_context', 'payload': {'turn_id': turn_id, 'model': model}},
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': turn_id}]}},
                    {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': turn_id}},
                ])
            rollout.write_text(''.join(json.dumps(record) + '\n' for record in records))
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as db, db:
                db.execute('CREATE TABLE threads(id TEXT, cwd TEXT, originator TEXT, source TEXT, rollout_path TEXT, model_provider TEXT)')
                db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)',
                           (thread_id, '/fixture/work', 'codex_cli_rs', 'cli', str(rollout), 'openai'))
            adapter = CliSessions(home, lambda *_: None, lambda: None)
            process = object()
            adapter.client.process = process
            adapter.joined[thread_id] = process
            adapter.metadata[thread_id] = {'cwd': '/fixture/work', 'modelProvider': 'openai', 'model': 'model-current'}
            adapter.client.request = Mock(return_value={'thread': {
                'id': thread_id, 'cwd': '/fixture/work', 'modelProvider': 'openai', 'model': 'model-current',
                'turns': [{'id': 'old', 'status': 'completed', 'items': [{'id': 'a', 'type': 'agentMessage', 'text': 'old'}]},
                          {'id': 'new', 'status': 'completed', 'items': [{'id': 'b', 'type': 'agentMessage', 'text': 'new'}]}]}})
            state = adapter.refresh(thread_id)
            self.assertEqual([turn.get('params', {}).get('model') for turn in state['turns']],
                             [None, None])
            session = LiveSession(thread_id)
            session.runtime = 'cli'
            with session.condition:
                session.set_history(SessionStore(home).history(thread_id))
            session.state = state
            session.connected = True
            self.assertEqual([turn['model'] for turn in session.view()['turns']],
                             ['model-old', 'model-new'])
            session.saved_view['turns'][1]['model'] = None
            session.state['turns'][1]['params'] = {'model': 'native-per-turn'}
            self.assertEqual(session.view()['turns'][1]['model'], 'native-per-turn')
            session.saved_view['turns'][1]['model'] = 'model-new'
            adapter._event({'method': 'turn/started', 'params': {'threadId': thread_id,
                            'turn': {'id': 'live', 'status': 'inProgress'}}})
            adapter._event({'method': 'thread/settings/updated', 'params': {'threadId': thread_id,
                            'threadSettings': {'model': 'future-model'}}})
            adapter.client.request.return_value['thread']['model'] = 'future-model'
            adapter._event({'method': 'turn/completed', 'params': {'threadId': thread_id,
                            'turn': {'id': 'live', 'status': 'completed'}}})
            native = adapter.client.request.return_value['thread']
            native['turns'].append({'id': 'live', 'status': 'completed', 'items': [
                {'id': 'c', 'type': 'agentMessage', 'text': 'live'}]})
            adapter.refresh(thread_id)
            adapter.refresh(thread_id)
            session.state = adapter.states[thread_id]
            self.assertEqual([turn['model'] for turn in session.view()['turns']],
                             ['model-old', 'model-new', 'model-current'])
            adapter._event({'method': 'turn/started', 'params': {'threadId': thread_id,
                            'turn': {'id': 'later', 'status': 'inProgress'}}})
            adapter._event({'method': 'thread/settings/updated', 'params': {'threadId': thread_id,
                            'threadSettings': {'model': 'next-model'}}})
            adapter._event({'method': 'turn/completed', 'params': {'threadId': thread_id,
                            'turn': {'id': 'later', 'status': 'completed'}}})
            native['turns'].append({'id': 'later', 'status': 'completed', 'items': [
                {'id': 'd', 'type': 'agentMessage', 'text': 'later'}]})
            adapter.refresh(thread_id)
            adapter.refresh(thread_id)
            session.state = adapter.states[thread_id]
            self.assertEqual([turn['model'] for turn in session.view()['turns']],
                             ['model-old', 'model-new', 'model-current', 'future-model'])
            native['turns'][-1]['model'] = 'native-per-turn'
            adapter.refresh(thread_id)
            session.state = adapter.states[thread_id]
            self.assertEqual([turn['model'] for turn in session.view()['turns']],
                             ['model-old', 'model-new', 'model-current', 'native-per-turn'])


class CliCreationProtocolTests(unittest.TestCase):
    def test_explicit_create_starts_cli_thread_without_turn_or_resume(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            home = Path(folder)
            thread_id = str(uuid.uuid4())
            cli = CliSessions(home, lambda *_: None, lambda: None)
            cli.client.process = object()
            cli._connect_explicit = Mock(return_value={'codexHome': str(home)})
            thread = {'id': thread_id, 'cwd': str(home), 'modelProvider': 'fixture',
                      'model': 'fixture-model', 'status': {'type': 'idle'}, 'turns': []}
            def request(method, params, **kwargs):
                if method == 'thread/start': return {'thread': thread}
                if method == 'thread/read': return {'thread': thread}
                return {}
            cli.client.request = Mock(side_effect=request)
            result = cli.create(str(home), 'Fixture CLI')
            self.assertEqual(result['id'], thread_id)
            self.assertEqual(result['modelProvider'], 'fixture')
            methods = [call.args[0] for call in cli.client.request.call_args_list]
            self.assertEqual(methods, ['thread/start', 'thread/name/set', 'thread/read'])
            self.assertEqual(cli.client.request.call_args_list[0].args[1],
                             {'cwd': str(home), 'ephemeral': False, 'threadSource': 'cli'})


if __name__ == '__main__':
    unittest.main()
