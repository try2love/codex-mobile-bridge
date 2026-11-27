#!/usr/bin/env python3
"""Exercise hosted Desktop control with a single native writer and loopback model.

Requires an explicit installed --codex. Uses only isolated data and synthetic
credentials; it does not launch, stop or access the user's Desktop or daemon.
The fixture's parent process represents the App; GUI integration is separate.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.clients.codex import desktop_runtime as runtime
from bridge.clients.codex.desktop_host import serve
from bridge.clients.codex.ipc import DesktopIPC, validate_stream_state
from bridge.app.service import Bridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex')
    parser.add_argument('--adapter')
    parser.add_argument('--response-bytes', type=int, default=0,
                        help='Additional loopback response bytes for large-history acceptance')
    args = parser.parse_args()
    if not 0 <= args.response_bytes <= 8 * 1024 * 1024:
        parser.error('--response-bytes must be between 0 and 8 MiB')
    if args.adapter:
        config = runtime._read(Path(args.adapter))
        first = json.loads(sys.stdin.buffer.readline())
        with runtime.exclusive_lock(config['home'], 'runtime'):
            return serve(config, runtime.process_identity(os.getppid()), None, ['app-server'], first)
    cli = Path(args.codex or '')
    if not cli.is_absolute() or not cli.is_file():
        parser.error('Provide the absolute installed --codex path')

    calls = []
    approval_sent = False
    hold, entered = threading.Event(), threading.Event()

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_CONNECT(self):
            self.send_error(403)

        def do_POST(self):
            nonlocal approval_sent
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(data)
            inputs = data.get('input', [])
            last_user = next((item for item in reversed(inputs) if item.get('role') == 'user'), {})
            text = json.dumps(last_user)
            item = {'id': 'msg_fixture_' + str(len(calls)), 'type': 'message', 'role': 'assistant',
                    'status': 'completed', 'content': [{'type': 'output_text', 'text': 'HOSTED_OK' + 'x' * args.response_bytes, 'annotations': []}]}
            if 'Synthetic approval' in text and not approval_sent:
                approval_sent = True
                tools = [tool.get('name') for tool in data.get('tools', [])]
                name = next((name for name in ('shell_command', 'exec_command', 'shell') if name in tools), None)
                assert name, tools
                command = {'cmd': 'printf HOSTED_APPROVAL_FIXTURE'} if name == 'exec_command' else {
                    'command': ['sh', '-c', 'printf HOSTED_APPROVAL_FIXTURE'] if name == 'shell' else 'printf HOSTED_APPROVAL_FIXTURE'}
                command.update(sandbox_permissions='require_escalated', justification='Synthetic approval fixture')
                item = {'type': 'function_call', 'id': 'fc_approval', 'call_id': 'call_approval',
                        'name': name, 'arguments': json.dumps(command)}
            response = {'id': 'resp_fixture_' + str(len(calls)), 'object': 'response', 'created_at': 0,
                        'status': 'completed', 'model': 'fixture-model', 'output': [item],
                        'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
            events = [{'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
                      {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
                      {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                      {'type': 'response.completed', 'response': response}]
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            try:
                if 'Synthetic hold' in text:
                    entered.set()
                    self.wfile.write(('event: response.created\ndata: ' + json.dumps(events.pop(0)) + '\n\n').encode())
                    self.wfile.flush()
                    hold.wait(20)
                for event in events:
                    self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    (ROOT / '.tmp').mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='hosted-native-') as folder:
            root = Path(folder); home = root / 'home'; home.mkdir()
            config_file = home / 'config.toml'
            config_file.write_text('model="fixture-model"\nmodel_provider="fixture"\n'
                'approval_policy="on-request"\nsandbox_mode="read-only"\ncli_auth_credentials_store="file"\n'
                '[analytics]\nenabled=false\n[model_providers.fixture]\nname="Loopback fixture"\n'
                'wire_api="responses"\nbase_url="http://127.0.0.1:' + str(upstream.server_port) + '/v1"\n')
            original = config_file.read_bytes()
            settings = {'version': runtime.VERSION, 'home': str(home), 'cli': str(cli),
                        'application': runtime.process_identity(os.getpid())['image']}
            private = runtime.private_directory(runtime.directory(home))
            configuration = private / 'settings.json'
            runtime._write(configuration, json.dumps(settings))
            env = {k: v for k, v in os.environ.items() if k not in
                ('CODEX_HOME', 'CODEX_SQLITE_HOME', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_API_KEY')}
            env.update(CODEX_HOME=str(home), HTTP_PROXY='http://127.0.0.1:' + str(upstream.server_port),
                       HTTPS_PROXY='http://127.0.0.1:' + str(upstream.server_port), NO_PROXY='127.0.0.1,localhost')
            log = (root / 'native.log').open('wb')
            process = subprocess.Popen([sys.executable, '-B', __file__, '--adapter', str(configuration)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env, text=True)
            messages = queue.Queue()
            def read():
                for line in process.stdout:
                    messages.put(json.loads(line))
            threading.Thread(target=read, daemon=True).start()
            counter = 0
            def request(method, params):
                nonlocal counter
                counter += 1
                process.stdin.write(json.dumps({'id': counter, 'method': method, 'params': params}) + '\n')
                process.stdin.flush()
                while True:
                    value = messages.get(timeout=20)
                    if value.get('id') == counter:
                        if 'error' in value:
                            raise AssertionError(value['error'])
                        return value['result']
            def completed():
                while True:
                    value = messages.get(timeout=20)
                    if value.get('method') == 'turn/completed':
                        return value['params']['turn']
            bridge = None
            try:
                request('initialize', {'clientInfo': {'name': 'Codex Desktop', 'version': '1'},
                                      'capabilities': {'experimentalApi': True}})
                thread_id = request('thread/start', {'cwd': str(root)})['thread']['id']
                request('turn/start', {'threadId': thread_id, 'input': [{'type': 'text', 'text': 'Synthetic seed'}]})
                assert completed()['status'] == 'completed'
                before = runtime._read(private / 'active.json')['runtime']
                bridge = Bridge(home, root / 'gateway', codex_bin=str(cli))
                try:
                    session = bridge.session(thread_id)
                    bridge._attach(session, timeout=10)
                    assert session.connected, session.error
                except Exception:
                    print('Native fixture diagnostics:', (root / 'native.log').read_text(errors='replace')[-5000:])
                    if thread_id in bridge.live:
                        current = bridge.live[thread_id]
                        print('Bridge connection:', {'connected': current.connected, 'owner': current.owner,
                                                     'origin': current.origin, 'error': current.error})
                    raise
                with session.condition:
                    assert session.condition.wait_for(lambda: session.connected, 10), session.error
                validate_stream_state(99, session.state)
                assert 'HOSTED_OK' in json.dumps(session.state), session.state
                assert session.state['modelProvider'] == 'fixture'
                receipt = bridge.send(thread_id, 'Synthetic phone input', str(uuid.uuid4()))
                assert receipt['status'] in ('accepted', 'sent'), receipt
                assert completed()['status'] == 'completed'
                bridge.ipc.close()
                assert process.poll() is None
                assert runtime._read(private / 'active.json')['runtime'] == before
                session.connected = False
                bridge._attach(session, timeout=10)
                assert session.connected, session.error
                assert len(calls) == 2, len(calls)
                current = bridge.ipc.request('native-metadata', {'method': 'thread/read',
                    'params': {'threadId': thread_id, 'includeTurns': False}})['result']['result']['thread']
                assert current['historyMode'] == 'paginated'
                with session.condition:
                    assert session.condition.wait_for(lambda: session.state['threadRuntimeStatus']['type'] == 'idle', 10)
                    turn_id = session.state['turns'][-1]['turnId']
                bridge._call(session, 'thread-follower-edit-last-user-turn', {
                    'turnId': turn_id, 'message': 'Synthetic edited input'})
                assert completed()['status'] == 'completed'
                assert len(calls) == 3, len(calls)
                with session.condition:
                    assert session.condition.wait_for(lambda: session.state['threadRuntimeStatus']['type'] == 'idle', 10)
                receipt = bridge.send(thread_id, 'Synthetic hold', str(uuid.uuid4()))
                assert entered.wait(10), 'Native turn did not reach loopback fixture'
                with session.condition:
                    assert session.condition.wait_for(lambda: any(turn.get('status') == 'inProgress'
                        for turn in session.state['turns']), 10)
                bridge.interrupt(thread_id)
                interrupted = completed()
                assert interrupted['status'] == 'interrupted'
                hold.set()
                settled = time.monotonic()
                with session.condition:
                    assert session.condition.wait_for(lambda:
                        session.state['threadRuntimeStatus']['type'] == 'idle' and
                        any(turn['turnId'] == interrupted['id'] and turn['status'] == 'interrupted'
                            for turn in session.state['turns']), 5), 'Bridge did not observe native interruption within 5s'
                print('Native interruption observed by Bridge in', round((time.monotonic() - settled) * 1000, 1), 'ms')
                receipt = bridge.send(thread_id, 'Synthetic approval', str(uuid.uuid4()))
                with session.condition:
                    assert session.condition.wait_for(lambda: bool(session.state.get('requests')), 10), session.error
                    approval = session.state['requests'][0]
                assert approval['method'] == 'item/commandExecution/requestApproval', approval['method']
                assert 'cancel' in approval['params'].get('availableDecisions', [])
                bridge.respond(thread_id, approval['id'], {'decision': 'cancel'})
                assert completed()['status'] in ('completed', 'interrupted')
                assert config_file.read_bytes() == original
                print('PASS: production Bridge reads paginated history, sends once, observes native completion, '
                      'reconnects, edits, interrupts and cancels a native approval on the same App-owned runtime; '
                      'configuration unchanged, loopback model only.')
            finally:
                hold.set()
                if bridge is not None:
                    bridge.close()
                process.stdin.close()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate(); process.wait(timeout=10)
                process.stdout.close(); log.close()
                if process.returncode:
                    print((root / 'native.log').read_text(errors='replace')[-4000:])
                assert not (private / 'active.json').exists(), 'Native writer did not release its record'
    finally:
        upstream.shutdown(); upstream.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
