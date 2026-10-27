import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from bridge.catalog import Catalog
from bridge.ipc import DesktopIPC, IPCError
from bridge.lifecycle import GatewayControl, request_stop
from bridge.remote import AppHosts
from bridge.store import SessionStore
from bridge.transport import connect_stream, ipc_endpoint
from test_bridge import DesktopFixture, ROOT, THREAD


class TransportTests(unittest.TestCase):
    def test_missing_desktop_is_actionable(self):
        endpoint = (r'\\.\pipe\missing-' + uuid.uuid4().hex if os.name == 'nt'
                    else str(ROOT / '.tmp/missing.sock'))
        with self.assertRaisesRegex(IPCError, '\u684c\u9762 App'):
            DesktopIPC(endpoint).connect()

    def test_large_unicode_frames_and_reconnect(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            fixture = DesktopFixture(Path(folder))
            ipc = DesktopIPC(fixture.path)
            try:
                for _ in range(2):
                    ipc.connect()
                    payload = {'text': '\u4e2d\u6587\U0001f680' * 50000}
                    ipc.request('thread-follower-steer-turn', payload)
                    self.assertEqual(fixture.requests[-1]['params'], payload)
                    ipc.close()
                    deadline = time.monotonic() + 2
                    while ipc.socket and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertIsNone(ipc.socket)
            finally:
                ipc.close()
                fixture.close()

    def test_close_unblocks_pending_read(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            fixture = DesktopFixture(Path(folder))
            stream = connect_stream(fixture.path)
            finished = threading.Event()
            def read():
                try:
                    stream.recv(1)
                except OSError:
                    pass
                finally:
                    finished.set()
            worker = threading.Thread(target=read, daemon=True)
            worker.start()
            try:
                time.sleep(.05)
                stream.shutdown(socket.SHUT_RDWR)
                stream.close()
                self.assertTrue(finished.wait(2))
            finally:
                stream.close()
                fixture.close()
                worker.join(timeout=2)

    def test_platform_endpoint(self):
        home = Path.home() / '.codex'
        expected = r'\\.\pipe\codex-ipc' if os.name == 'nt' else str(home / 'ipc/ipc.sock')
        self.assertEqual(ipc_endpoint(home), expected)


class PlatformDataTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows path semantics')
    def test_local_paths_ignore_case_and_slash_but_remote_paths_do_not(self):
        hosts = AppHosts(ROOT)
        hosts.state = lambda: {'local-projects': {
            'a': {'id': 'a', 'name': 'Workspace', 'rootPaths': [r'C:\Work']},
            'b': {'id': 'b', 'name': 'Nested', 'rootPaths': ['C:/Work/Nested']}}}
        rows = hosts.decorate([{'id': 'x', 'cwd': r'c:\WORK\nested\src'}], 'local', 'PC')
        self.assertEqual(rows[0]['projectKey'], 'local|b')
        rows = hosts.decorate([{'id': 'x', 'cwd': r'C:\Work-other\MyProject'}], 'local', 'PC')
        self.assertEqual(rows[0]['projectName'], 'MyProject')
        hosts.state = lambda: {'remote-projects': [
            {'id': 'r', 'hostId': 'remote', 'remotePath': '/Work'}]}
        rows = hosts.decorate([{'id': 'x', 'cwd': '/work/src'}], 'remote', 'Server')
        self.assertNotEqual(rows[0]['projectKey'], 'remote|r')

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime discovery')
    def test_runtime_in_local_app_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            runtime = Path(folder) / 'OpenAI/Codex/bin/version/codex.exe'
            runtime.parent.mkdir(parents=True)
            runtime.touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': folder}):
                self.assertEqual(Catalog.find_runtime(), runtime)

    def test_utf8_history_and_global_state(self):
        import sqlite3
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='space \u4e2d\u6587 ') as folder:
            root = Path(folder)
            history = root / 'sessions/history.jsonl'
            history.parent.mkdir()
            text = '\u4e2d\u6587\U0001f680'
            history.write_text(json.dumps({'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'assistant', 'content': [{'text': text}]}},
                ensure_ascii=False) + '\n', encoding='utf-8')
            with sqlite3.connect(str(root / 'state_5.sqlite')) as db:
                db.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, originator TEXT, rollout_path TEXT)')
                db.execute('INSERT INTO threads VALUES(?,?,?,?,?)', (THREAD, text, str(root), 'Codex Desktop', str(history)))
            db.close()
            relative = root.relative_to(Path.cwd())
            value = SessionStore(relative).history(THREAD)
            self.assertEqual(value['turns'][0]['items'][0]['text'], text)
            (root / '.codex-global-state.json').write_text(json.dumps({'label': text}, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(AppHosts(root).state()['label'], text)


class LifecycleTests(unittest.TestCase):
    def test_help_supports_redirected_non_utf8_output(self):
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'run.py'), '--help'],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('手机网关', result.stdout.decode('utf-8'))

    @unittest.skipUnless(os.name == 'nt', 'Windows command launchers')
    def test_launchers_fall_back_when_py_has_no_interpreter(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='launcher ') as folder:
            root = Path(folder)
            (root / 'py.cmd').write_text('@exit /b 1\n', encoding='ascii')
            (root / 'python.cmd').write_text('@"' + sys.executable + '" %*\n', encoding='utf-8')
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ.get('PATH', os.defpath))
            for script in ('start.cmd', 'start-tunnel.cmd', 'stop.cmd'):
                with self.subTest(script=script):
                    result = subprocess.run(['cmd.exe', '/d', '/c', str(ROOT / script), '--help'],
                        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(b'--config', result.stdout)

    def test_stale_record_never_signals_a_process(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            path = Path(folder) / 'gateway-control.json'
            path.write_text(json.dumps({'pid': os.getpid(), 'token': 'old'}), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                request_stop(folder, timeout=.1)
            self.assertTrue(path.exists())
            self.assertFalse(path.with_name('gateway.stop').exists())

    def test_stop_request_must_match_instance(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            stopped = threading.Event()
            control = GatewayControl(folder)
            control.start(stopped.set)
            try:
                control.request_path.write_text(json.dumps({'pid': os.getpid(), 'token': 'old'}), encoding='utf-8')
                self.assertFalse(stopped.wait(.4))
                control.request_path.write_text(json.dumps(control.record), encoding='utf-8')
                self.assertTrue(stopped.wait(2))
            finally:
                control.close()
            self.assertFalse(control.path.exists())

    def test_gateway_process_start_auth_and_graceful_stop(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='gateway \u4e2d\u6587 ') as folder:
            root = Path(folder)
            config = root / 'config.json'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            with (root / 'output.log').open('wb') as log:
                process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'run.py'),
                    '--port', str(port), '--config', str(config), '--codex-home', str(root)],
                    env=dict(os.environ, PYTHONIOENCODING='cp1252'),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 15
                    while not (root / 'gateway-control.json').exists():
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail('Gateway failed to start: ' + (root / 'output.log').read_text(encoding='utf-8', errors='replace'))
                        time.sleep(.05)
                    self.assertIn('手机网关已启动', (root / 'output.log').read_text(encoding='utf-8'))
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                    conn.request('GET', '/api/auth')
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                    conn.close()
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                    conn.request('GET', '/api/sessions')
                    response = conn.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()
                    conn.close()
                    credentials = next(root.glob('*.txt')).read_text(encoding='utf-8')
                    self.assertEqual(json.loads(config.read_text(encoding='utf-8'))['auth']['username'], 'admin')
                    self.assertIn('\u8d26\u53f7', credentials)
                    result = subprocess.run([sys.executable, '-B', str(ROOT / 'stop.py'), '--config', str(config)],
                                            capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(process.wait(timeout=5), 0)
                    for name in ('gateway.pid', 'gateway-control.json', 'gateway.stop'):
                        self.assertFalse((root / name).exists())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
