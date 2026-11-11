import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.codex.app_server import AppServerReadTimeout, AppServerUnavailable
from bridge.clients.codex.cli_session import CliSessions
from test_codex_app_server import FixtureProcess


class CliProxyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.processes = []
        self.disconnects = []
        def factory(*args, **kwargs):
            process = FixtureProcess(*args, **kwargs)
            def thread(params):
                return {'id': params['threadId'], 'cwd': '/project', 'modelProvider': 'original',
                        'status': {'type': 'idle'}, 'turns': []}
            process.results.update({
                'thread/read': lambda params: {'thread': thread(params)},
                'thread/resume': lambda params: {'thread': thread(params), 'cwd': '/project', 'modelProvider': 'original'},
                'thread/goal/get': {'goal': None}})
            self.processes.append(process)
            return process
        self.sessions = CliSessions(Path(self.temp.name), lambda *_: None,
                                    lambda *_: self.disconnects.append(True), alias='fixture', process_factory=factory)
        self.sessions.lifecycle = None
        self.sessions._start_daemon = Mock(side_effect=AssertionError('must not restart daemon'))
        self.client = self.sessions.client
        self.sessions._runtime_home(self.client.connect())
        self.processes[0].drop.add('thread/loaded/list')
        request = self.client.request
        def bounded(method, params=None, **kwargs):
            return request(method, params, **{**kwargs, 'timeout': .1})
        self.bounded = patch.object(self.client, 'request', side_effect=bounded)
        self.bounded.start()

    def tearDown(self):
        self.bounded.stop()
        self.client.close()
        self.temp.cleanup()

    def test_resume_replaces_only_stalled_proxy_and_retains_native_identity(self):
        state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual((state['id'], state['modelProvider']), ('thread-a', 'original'))
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(self.processes[0].ended)
        self.assertEqual(self.disconnects, [True])
        methods = [m['method'] for p in self.processes for m in p.seen]
        self.assertEqual(methods.count('thread/resume'), 1)
        self.assertNotIn('turn/start', methods)
        self.sessions._start_daemon.assert_not_called()

    def test_changed_runtime_home_is_rejected_before_resume(self):
        factory = self.client.process_factory
        def changed(*args, **kwargs):
            process = factory(*args, **kwargs)
            process.results['initialize'] = {'codexHome': '/other/.codex'}
            return process
        self.client.process_factory = changed
        with self.assertRaisesRegex(AppServerUnavailable, 'another home'):
            self.sessions.attach('thread-a', explicit=True)
        self.assertFalse(any(m['method'] == 'thread/resume' for p in self.processes for m in p.seen))
        self.assertEqual(str(self.sessions.remote_home), '/fixture/.codex')

    def test_second_timeout_stops_without_resume_or_daemon_start(self):
        factory = self.client.process_factory
        def stalled(*args, **kwargs):
            process = factory(*args, **kwargs)
            process.drop.add('thread/loaded/list')
            return process
        self.client.process_factory = stalled
        with self.assertRaises(AppServerReadTimeout):
            self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(len(self.processes), 2)
        self.assertFalse(any(m['method'] == 'thread/resume' for p in self.processes for m in p.seen))
        self.sessions._start_daemon.assert_not_called()

    def test_old_cleanup_finishes_before_concurrent_new_join(self):
        attempted, connected = threading.Event(), threading.Event()
        errors, competitors = [], []
        original_connect, original_disconnect = self.client.connect, self.sessions._disconnect
        def connect(*args, **kwargs):
            if threading.current_thread().name == 'second-join':
                attempted.set()
                result = original_connect(*args, **kwargs)
                connected.set()
                return result
            return original_connect(*args, **kwargs)
        def join():
            try:
                self.sessions.attach('thread-b', explicit=True)
            except Exception as error:
                errors.append(error)
        def disconnect():
            worker = threading.Thread(target=join, name='second-join', daemon=True)
            competitors.append(worker)
            worker.start()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(connected.is_set())
            original_disconnect()
        with patch.object(self.client, 'connect', side_effect=connect), patch.object(self.sessions, '_disconnect', side_effect=disconnect):
            self.sessions.attach('thread-a', explicit=True)
            for worker in competitors:
                worker.join(2)
                self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(set(self.sessions.joined), {'thread-a', 'thread-b'})
        self.assertTrue(all(p is self.client.process for p in self.sessions.joined.values()))
        self.assertEqual(len(self.processes), 2)
