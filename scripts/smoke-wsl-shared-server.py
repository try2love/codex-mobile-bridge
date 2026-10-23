#!/usr/bin/env python3
"""Linux-only isolated shared-server integration; requires explicit --codex.

Uses a new private CODEX_HOME, an owned app-server and a loopback model fixture.
Never connects to the user's daemon, account, Magpie, or real model service.
"""
import argparse
import copy
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge.clients.codex.shared_server import SharedServer
from bridge.features.endpoints.wsl import WslEntry
from bridge.features.sessions.store import SessionStore

ROOT = Path(__file__).resolve().parents[1]


def wait(predicate, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for isolated native fixture')


class Owner(SharedServer):
    METHODS = SharedServer.METHODS | {'thread/start', 'thread/unsubscribe'}

    def _request(self, method, params, timeout):
        if method == 'initialize':
            params = copy.deepcopy(params)
            params['clientInfo']['name'] = 'codex-tui'
        return super()._request(method, params, timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', required=True)
    args = parser.parse_args()
    requests, owner_events, bridge_events = [], [], []
    release = threading.Event()
    model_state = {'approvalSent': False}

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(value)
            index = len(requests)
            inputs = value.get('input', [])
            user = next((i for i in reversed(inputs) if isinstance(i, dict) and i.get('role') == 'user'), {})
            last_user = json.dumps(user)
            if 'Synthetic approval input' in last_user and not model_state['approvalSent']:
                model_state['approvalSent'] = True
                tools = [t.get('name') for t in value.get('tools', [])]
                name = next((n for n in ('shell_command', 'exec_command', 'shell') if n in tools), None)
                assert name, 'No native shell tool in isolated request: ' + repr(tools)
                command = {'command': 'printf WSL_APPROVAL_FIXTURE'} if name != 'exec_command' else {'cmd': 'printf WSL_APPROVAL_FIXTURE'}
                if name == 'shell':
                    command = {'command': ['sh', '-c', 'printf WSL_APPROVAL_FIXTURE']}
                command.update(sandbox_permissions='require_escalated', justification='Synthetic approval fixture')
                item = {'type': 'function_call', 'id': 'fc_fixture', 'call_id': 'call_fixture',
                        'name': name, 'arguments': json.dumps(command)}
            else:
                item = {'id': 'msg_fixture_' + str(index), 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'WSL_SHARED_OK', 'annotations': []}]}
            response = {'id': 'resp_fixture_' + str(index), 'object': 'response', 'created_at': 0,
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
                if 'Synthetic interrupt input' in last_user or 'Synthetic disconnect input' in last_user:
                    event = events[0]
                    self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                    self.wfile.flush()
                    release.wait(20)
                    events = events[1:]
                for event in events:
                    self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        # Unix sockets cannot be bound on WSL's Windows-mounted project volume.
        # Keep test data in .tmp; only the socket uses an owned Linux temp dir.
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='wsl-native-') as folder, tempfile.TemporaryDirectory(prefix='bridge-wsl-socket-') as sockets:
            root = Path(folder)
            home = root/'home'
            home.mkdir()
            config = home/'config.toml'
            config.write_text('model="fixture-model"\nmodel_provider="fixture"\napproval_policy="on-request"\n'
                'sandbox_mode="read-only"\ncli_auth_credentials_store="file"\n[analytics]\nenabled=false\n'
                '[model_providers.fixture]\nname="Loopback fixture"\nwire_api="responses"\n'
                'base_url="http://127.0.0.1:' + str(upstream.server_port) + '/v1"\n', encoding='utf-8')
            original_config = config.read_bytes()
            sock_path = Path(sockets)/'s.sock'
            log = (root/'runtime.log').open('wb')
            environment = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_HOME')}
            environment['CODEX_HOME'] = str(home)
            process = subprocess.Popen([args.codex, 'app-server', '--listen', 'unix://' + str(sock_path)],
                env=environment, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            owner = entry = None
            try:
                wait(sock_path.exists)
                def connect():
                    stream = socket.socket(socket.AF_UNIX)
                    stream.connect(str(sock_path))
                    return stream
                owner = Owner(connect(), owner_events.append).start()
                native = owner.request('thread/start', {'cwd': str(root)})['thread']
                sid = native['id']
                owner.request('turn/start', {'threadId': sid, 'input': [{'type': 'text', 'text': 'Synthetic owner seed'}]})
                wait(lambda: any(e.get('method') == 'turn/completed' for e in owner_events))
                row = {'id': 'wsl:isolated-fixture', 'distribution': 'isolated', 'user': 'fixture',
                       'home': str(home), 'label': 'Isolated WSL fixture'}
                entry = WslEntry(row, root/'bridge')
                entry.store = SessionStore(home, source='cli')
                entry.generation = 'isolated-generation'
                def event(value):
                    bridge_events.append(value)
                    entry._event(value, 'isolated-generation')
                entry.server = SharedServer(connect(), event).start()
                wait(lambda: any(r['id'] == sid for r in entry.store.list()))
                catalog = entry.catalog(sid, kind='models')
                assert catalog['models'] and all(model['id'] and model['name'] for model in catalog['models'])
                assert all(isinstance(model['efforts'], list) for model in catalog['models'])
                print('PASS: native model catalog has visible names, stable model IDs and reasoning-effort arrays.', flush=True)
                view = entry.view(sid)
                assert view['connected'] and view['provider'] == 'fixture'
                submission = str(uuid.uuid4())
                entry.send(sid, 'Synthetic shared input', submission)
                wait(lambda: sum(e.get('method') == 'turn/completed' for e in owner_events) >= 2)
                assert len(requests) == 2
                entry.send(sid, 'Synthetic shared input', submission)
                assert len(requests) == 2, 'Duplicate send reached model fixture'
                assert owner.request('thread/read', {'threadId': sid, 'includeTurns': False})['thread']['modelProvider'] == 'fixture'
                print('PASS: second connection sends to original loaded thread; original owner receives completion; duplicate does not execute.', flush=True)
                entry.send(sid, 'Synthetic approval input', str(uuid.uuid4()))
                session = entry.live[sid]
                try:
                    approval = wait(lambda: next(iter(session.state.get('requests', [])), None), 12)
                except AssertionError:
                    print(json.dumps({'ownerRequests': [e.get('method') for e in owner_events if 'id' in e],
                                      'bridgeRequests': [e.get('method') for e in bridge_events if 'id' in e]}), flush=True)
                    raise
                # This runtime offers accept/cancel, not decline. Respect its
                # actual choices rather than inventing a broader permission.
                entry.respond(sid, approval['id'], {'decision': 'cancel'})
                wait(lambda: len(requests) >= 3 and owner.request('thread/read', {'threadId': sid, 'includeTurns': False})['thread']['status']['type'] == 'idle')
                print('PASS: approval reaches bridge connection; offered cancel decision stops original turn without executing the command.', flush=True)
                entry.send(sid, 'Synthetic interrupt input', str(uuid.uuid4()))
                wait(lambda: len(requests) >= 4)
                entry.interrupt(sid)
                wait(lambda: owner.request('thread/read', {'threadId': sid, 'includeTurns': False})['thread']['status']['type'] == 'idle')
                entry.send(sid, 'Synthetic disconnect input', str(uuid.uuid4()))
                wait(lambda: len(requests) >= 5)
                entry.close()
                release.set()
                wait(lambda: owner.request('thread/read', {'threadId': sid, 'includeTurns': False})['thread']['status']['type'] == 'idle')
                latest = owner.request('thread/turns/list', {'threadId': sid, 'limit': 1, 'sortDirection': 'desc'})['data'][0]
                assert latest['status'] == 'completed', 'Closing bridge interrupted the original daemon task'
                assert config.read_bytes() == original_config
                assert not (home/'auth.json').exists()
                print('PASS: stop targets original turn; bridge disconnect preserves running task; config/provider/auth unchanged; loopback requests only.', flush=True)
                owner.close()
                process.terminate()
                process.wait(timeout=10)
                sock_path.unlink(missing_ok=True)
                process = subprocess.Popen([args.codex, 'app-server', '--listen', 'unix://' + str(sock_path)],
                    env=environment, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                wait(sock_path.exists)
                owner = Owner(connect(), owner_events.append).start()
                wait(lambda: owner.request('thread/read', {'threadId': sid, 'includeTurns': False})['thread']['status']['type'] == 'notLoaded')
                entry = WslEntry(row, root/'mobile-bridge')
                entry.store = SessionStore(home, source='cli')
                entry.generation = 'mobile-lifecycle'
                entry.server = SharedServer(connect(), lambda value: entry._event(value, 'mobile-lifecycle')).start()
                def read_local(alias, source, timeout=20):
                    result = subprocess.run([os.sys.executable, '-c', source], env=environment,
                        capture_output=True, text=True, timeout=timeout, check=True)
                    return json.loads(result.stdout)
                entry.read = read_local
                assert not entry.view(sid)['connected'], 'Passive read unexpectedly restored a thread'
                entry.activate(sid)
                assert entry.view(sid)['connected'], 'Explicit mobile open did not resume stored history'
                count = len(requests)
                entry.send(sid, 'Synthetic mobile resumed input', str(uuid.uuid4()))
                wait(lambda: len(requests) == count + 1 and entry.server.request('thread/read', {'threadId': sid, 'includeTurns': False})['thread']['status']['type'] == 'idle')
                print('PASS: unloaded original history resumes from mobile and receives the next turn on the same thread.', flush=True)
                request_id = str(uuid.uuid4())
                created = entry.create_chat(entry.host + '|independent', 'Synthetic mobile new chat', request_id)
                assert created['id'] != sid and entry.view(created['id'])['connected']
                assert any(r['id'] == created['id'] for r in entry.list())
                assert entry.create_chat(entry.host + '|independent', 'Synthetic mobile new chat', request_id)['id'] == created['id']
                entry.send(created['id'], 'Synthetic mobile new input', str(uuid.uuid4()))
                wait(lambda: len(requests) == count + 2 and entry.server.request('thread/read', {'threadId': created['id'], 'includeTurns': False})['thread']['status']['type'] == 'idle')
                assert config.read_bytes() == original_config and not (home/'auth.json').exists()
                print('PASS: mobile creates a durable WSL chat once, lists it only in WSL, and sends without opening a TUI.', flush=True)
                parent = entry.project_directories(entry.host, str(root.resolve()))
                selected = entry.project_directories(entry.host, parent['path'], name='chosen project', identity=parent['identity'])
                directory = Path(selected['path'])
                project = entry.register_project(str(directory.resolve()), False, entry.host)
                chosen = entry.create_chat(project['key'], 'Synthetic chosen project', str(uuid.uuid4()))
                assert directory.is_dir()
                assert entry.store.get(chosen['id'])['cwd'] == str(directory.resolve())
                assert entry.server.request('thread/read', {'threadId': chosen['id'], 'includeTurns': False})['thread']['cwd'] == str(directory.resolve())
                assert len(requests) == count + 2, 'Choosing a directory unexpectedly sent a model request'
                print('PASS: explicit project directory is created, revalidated and used as the durable native thread cwd without a model request.', flush=True)
            except Exception as error:
                if entry is not None:
                    pending = [r for session in entry.live.values() for r in session.state.get('requests', [])]
                    print('Synthetic pending scopes:', json.dumps([{'method': r['method'], 'availableDecisions': r.get('params', {}).get('availableDecisions')} for r in pending]), flush=True)
                if hasattr(error, 'rpc_error'):
                    print('Synthetic fixture RPC error:', json.dumps(error.rpc_error), flush=True)
                raise
            finally:
                release.set()
                if entry:
                    entry.close()
                if owner:
                    owner.close()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                log.close()
    finally:
        release.set()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    main()
