import json
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.lifecycle import private_json
from bridge.clients.claude.cli_session import _Owned, owned_connections
from bridge.clients.codex.release import release_thread
from bridge.clients.codex.app_server import AppServerUnavailable
from bridge.clients.errors import BridgeUnavailable
from bridge.features.sessions.cli_release import CliConnections, ReleasedSessions
from tests import test_wsl_entries as wsl_fixtures
from bridge.clients.codex.ipc import IPCError


class CliReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_manual_release_survives_restart_and_explicit_resume_clears_it(self):
        state = ReleasedSessions(self.temp.name, private_json)
        state.set('original-session', True)
        reopened = ReleasedSessions(self.temp.name, private_json)
        self.assertTrue(reopened.contains('original-session'))
        reopened.set('original-session', False)
        self.assertFalse(ReleasedSessions(self.temp.name, private_json).contains('original-session'))

    def test_idle_codex_unsubscribes_only_target_without_archiving_or_stopping_daemon(self):
        calls = []
        def request(method, params):
            calls.append((method, params))
            if method == 'thread/read':
                return {'thread': {'id': 'original', 'status': {'type': 'idle'}}}
            if method == 'thread/backgroundTerminals/list':
                return {'data': []}
            if method == 'thread/goal/get':
                return {'goal': None}
            if method == 'thread/unsubscribe':
                return {'status': 'unsubscribed'}
            self.fail(method)
        result = release_thread(request, 'original')
        self.assertEqual(result['status'], 'detached')
        self.assertEqual(calls[-2], ('thread/unsubscribe', {'threadId': 'original'}))
        self.assertEqual(calls[-1][0], 'thread/read')

    def test_busy_codex_wait_performs_no_native_mutation(self):
        request = Mock(side_effect=[{'thread': {'id': 's', 'status': {'type': 'active'}}}, {'data': []}, {'goal': None}])
        self.assertEqual(release_thread(request, 's'), {'status': 'waiting'})
        self.assertEqual([c.args[0] for c in request.call_args_list], ['thread/read', 'thread/backgroundTerminals/list', 'thread/goal/get'])

    def test_read_failure_before_release_is_retryable_without_a_write(self):
        request = Mock(side_effect=AppServerUnavailable('read timeout'))
        with self.assertRaises(AppServerUnavailable) as caught:
            release_thread(request, 'original')
        self.assertEqual(caught.exception.release_result['status'], 'failed')
        self.assertTrue(caught.exception.release_result['notSent'])
        self.assertEqual([call.args[0] for call in request.call_args_list], ['thread/read'])

    def test_confirmed_unsubscribe_survives_failed_unload_read(self):
        request = Mock(side_effect=[{'thread': {'id': 's', 'status': {'type': 'idle'}}},
                                   {'data': []}, {'goal': None}, {'status': 'unsubscribed'},
                                   AppServerUnavailable('read timeout')])
        result = release_thread(request, 's')
        self.assertEqual(result['status'], 'detached')
        self.assertTrue(result['unsubscribed'])
        self.assertEqual(sum(call.args[0] == 'thread/unsubscribe' for call in request.call_args_list), 1)

    def test_wait_upgrade_atomically_retires_old_worker_before_stop(self):
        release = Mock(return_value={'status': 'released'})
        row = {'key': 'a', 'sessionId': 's', '_release': release}
        manager = CliConnections(self.temp.name, lambda: [SimpleNamespace(cli_connections=lambda: [row])], private_json)
        self.addCleanup(manager.close)
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            old = manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'wait'})
            manager.jobs[old['id']]['results'][0]['status'] = 'releasing'
            with self.assertRaises(ValueError):
                manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'stop'})
            manager.jobs[old['id']]['results'][0]['status'] = 'waiting'
            new = manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'stop'})
        self.assertEqual(manager.jobs[old['id']]['results'][0]['status'], 'superseded')
        manager._run(manager.jobs[old['id']], {'a': row})
        release.assert_not_called()
        manager._run(manager.jobs[new['id']], {'a': row})
        release.assert_called_once_with(True)

    def test_unknown_can_be_read_only_verified_without_replaying_release(self):
        release, check = Mock(side_effect=TimeoutError('lost receipt')), Mock(return_value={'status': 'released'})
        row = {'key': 'a', 'sessionId': 's', '_release': release, '_check': check}
        rows = [row]
        manager = CliConnections(self.temp.name, lambda: [SimpleNamespace(cli_connections=lambda: rows)], private_json)
        self.addCleanup(manager.close)
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            old = manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'wait'})
        manager._run(manager.jobs[old['id']], {'a': row})
        self.assertEqual(manager.jobs[old['id']]['results'][0]['status'], 'unknown')
        rows.clear()  # Confirmed detached connections need not remain in listings.
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            verified = manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'check'})
        manager._run(manager.jobs[verified['id']], {'a': row})
        self.assertEqual(manager.jobs[old['id']]['results'][0]['status'], 'released')
        check.assert_called_once_with()
        release.assert_called_once_with(False)

    def test_native_unload_is_a_separate_action_only_after_confirmed_detach(self):
        release, check = Mock(return_value={'status': 'detached'}), Mock(return_value={'status': 'detached'})
        unload = Mock(return_value={'status': 'released'})
        row = {'key': 'a', 'sessionId': 's', 'canUnload': True, '_release': release, '_check': check, '_unload': unload}
        manager = CliConnections(self.temp.name, lambda: [SimpleNamespace(cli_connections=lambda: [row])], private_json)
        self.addCleanup(manager.close)
        with self.assertRaises(ValueError):
            manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'unload'})
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            first = manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'wait'})
        manager._run(manager.jobs[first['id']], {'a': row})
        unload.assert_not_called()
        with patch('bridge.features.sessions.cli_release.threading.Thread'):
            second = manager.submit({'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'unload'})
        manager._run(manager.jobs[second['id']], {'a': row})
        unload.assert_called_once_with(second['id'])
        release.assert_called_once_with(False)
        check.assert_not_called()
        self.assertEqual(manager.jobs[first['id']]['results'][0]['status'], 'released')

    def test_wrong_native_identity_is_not_released(self):
        request = Mock(return_value={'thread': {'id': 'other', 'status': {'type': 'idle'}}})
        with self.assertRaises(ValueError):
            release_thread(request, 'original', True)
        self.assertEqual(request.call_count, 1)

    def test_native_unload_is_confirmed_by_read_and_active_goal_waits(self):
        request = Mock(side_effect=[{'thread': {'id': 's', 'status': {'type': 'idle'}}},
                                   {'data': []}, {'goal': {'status': 'active'}}])
        self.assertEqual(release_thread(request, 's')['status'], 'waiting')
        self.assertFalse(any(call.args[0] == 'thread/unsubscribe' for call in request.call_args_list))
        request = Mock(side_effect=[{'thread': {'id': 's', 'status': {'type': 'idle'}}},
                                   {'data': []}, {'goal': {'status': 'active'}}, {'goal': {'status': 'paused'}},
                                   {'status': 'unsubscribed'}, {'thread': {'id': 's', 'status': {'type': 'notLoaded'}}}])
        self.assertEqual(release_thread(request, 's', stop=True)['status'], 'released')
        self.assertEqual(request.call_args_list[3].args, ('thread/goal/set', {'threadId': 's', 'status': 'paused'}))

    def test_pending_release_after_gateway_restart_is_unknown_without_replay(self):
        body = {'id': str(uuid.uuid4()), 'connections': ['a'], 'mode': 'wait',
                'results': [{'key': 'a', 'status': 'waiting'}]}
        private_json(Path(self.temp.name)/'cli-release-operations.json', {body['id']: body})
        source = Mock()
        manager = CliConnections(self.temp.name, source, private_json)
        self.addCleanup(manager.close)
        self.assertEqual(manager.jobs[body['id']]['results'][0]['status'], 'unknown')
        source.assert_not_called()

    def test_claude_wait_and_eof_release_keep_other_executor_running(self):
        processes = [subprocess.Popen([sys.executable, '-u', '-c', 'import sys;sys.stdin.read()'], stdin=subprocess.PIPE, text=True)
                     for _ in range(2)]
        def cleanup():
            for process in processes:
                if process.poll() is None:
                    process.stdin.close()
                    process.wait(timeout=3)
        self.addCleanup(cleanup)
        owned = _Owned.__new__(_Owned)
        owned.process, owned.condition = processes[0], threading.Condition()
        owned.active, owned.pending, owned.error, owned.finished = True, {}, None, False
        self.assertEqual(owned.release_connection(), {'status': 'waiting'})
        self.assertIsNone(processes[0].poll())
        owned.active = False
        self.assertEqual(owned.release_connection()['status'], 'released')
        self.assertIsNotNone(processes[0].poll())
        self.assertIsNone(processes[1].poll())

    def test_stale_claude_generation_cannot_release_reconnected_session(self):
        owned = SimpleNamespace(origin='profile', process=Mock(), connection_token='old', release_connection=Mock())
        owned.process.poll.return_value = None
        client = SimpleNamespace(lock=threading.RLock(), index={'sid': {'nativeId': 'native', 'origin': 'profile'}}, owned={'native': owned})
        old = owned_connections(client)[0]
        client.owned['native'] = object()
        with self.assertRaises(BridgeUnavailable):
            old['_release'](True)
        owned.release_connection.assert_not_called()

    def test_batch_uses_selected_snapshot_and_never_replays_unknown_result(self):
        done = threading.Event()
        success, failure = Mock(return_value={'status': 'released'}), Mock(side_effect=TimeoutError('lost receipt'))
        rows = [{'key': 'a', 'title': 'A', 'sessionId': 'a', '_release': success},
                {'key': 'b', 'title': 'B', 'sessionId': 'b', '_release': failure}]
        manager = CliConnections(self.temp.name, lambda: [SimpleNamespace(cli_connections=lambda: rows)], private_json)
        self.addCleanup(manager.close)
        original = manager._run
        def run(*args):
            try:
                original(*args)
            finally:
                done.set()
        manager._run = run
        body = {'id': str(uuid.uuid4()), 'connections': ['a', 'b'], 'mode': 'wait'}
        manager.submit(body)
        self.assertTrue(done.wait(3))
        result = manager.submit(body)
        self.assertEqual([r['status'] for r in result['results']], ['released', 'unknown'])
        self.assertEqual(success.call_count, 1)
        self.assertEqual(failure.call_count, 1)
        reopened = CliConnections(self.temp.name, lambda: [], private_json)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.submit(body), result)
        with self.assertRaises(ValueError):
            manager.submit({'id': str(uuid.uuid4()), 'connections': ['b'], 'mode': 'stop'})


class WslReleaseTests(unittest.TestCase):
    setUp = wsl_fixtures.WslSessionTests.setUp
    make_entry = wsl_fixtures.WslSessionTests.make_entry

    def test_release_blocks_automatic_reacquire_but_explicit_resume_is_allowed(self):
        self.entry.activate(self.identifier)
        runtime = self.entry.server
        original = runtime.request
        def request(method, params):
            if method == 'thread/backgroundTerminals/list':
                return {'data': []}
            if method == 'thread/unsubscribe':
                runtime.calls.append((method, params))
                return {'status': 'unsubscribed'}
            return original(method, params)
        runtime.request = request
        row = self.entry.cli_connections()[0]
        result = row['_release'](False)
        self.assertEqual(result['status'], 'detached')
        self.assertFalse(runtime.closed)
        self.assertFalse(self.entry.cli_connections())
        with self.assertRaisesRegex(IPCError, '释放'):
            self.entry._target(self.identifier)
        self.entry.activate(self.identifier)
        self.assertFalse(self.entry.released_cli.contains(self.identifier))
        with self.assertRaisesRegex(IPCError, '变化'):
            row['_release'](True)


if __name__ == '__main__':
    unittest.main()
