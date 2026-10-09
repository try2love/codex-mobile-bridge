#!/usr/bin/env python3
"""Exercise the bundled runtime against localhost with synthetic credentials only."""
import json
import os
import queue
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.features.accounts.accounts import Accounts, ManagedRPC
from bridge.clients.codex.catalog import Catalog


class TurnRPC(ManagedRPC):
    METHODS = ManagedRPC.METHODS | {'thread/start', 'thread/resume', 'thread/name/set', 'turn/start'}


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.calls.append(('blocked-external', self.path, None))
        self.send_error(403)

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.calls.append((self.path, self.headers.get('Authorization'), raw))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        response = {'id':'resp_fixture', 'object':'response', 'status':'completed', 'output':[],
                    'usage':{'input_tokens':1, 'output_tokens':0, 'total_tokens':1}}
        self.wfile.write(('data: '+json.dumps({'type':'response.completed','response':response})+'\n\n').encode())


def turn(rpc, identifier):
    before = len(Handler.calls)
    result = rpc.request('turn/start', {'threadId':identifier, 'input':[{'type':'text','text':'Routing fixture only.'}]})
    deadline = time.monotonic()+20
    while time.monotonic()<deadline:
        message = rpc.messages.get(timeout=max(.01,deadline-time.monotonic()))
        if message and message.get('method') == 'turn/completed':
            assert message['params']['turn']['status'] == 'completed', message['params']['turn'].get('error')
            break
    else:
        raise AssertionError('Turn did not complete')
    calls = Handler.calls[before:]
    assert any(path == '/v1/responses' and auth == 'Bearer synthetic-not-a-secret' for path,auth,_ in calls), [(p,a) for p,a,_ in calls]
    assert not any(path == 'blocked-external' for path,_,_ in calls), 'Unexpected external connection'


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(dir=root/'.tmp') as directory:
        folder = Path(directory)
        home = folder/'home';home.mkdir()
        runtime = Catalog.find_runtime()
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        base = 'http://127.0.0.1:'+str(server.server_port)
        # Any unintended external HTTP(S) connection is trapped locally.
        env = {'HTTP_PROXY':base,'HTTPS_PROXY':base,'ALL_PROXY':base,'http_proxy':base,'https_proxy':base,
               'all_proxy':base,'NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost'}
        try:
            with patch.dict(os.environ,env):
                for key in ('OPENAI_API_KEY','OPENAI_BASE_URL'):
                    os.environ.pop(key,None)
                (home/'config.toml').write_text('cli_auth_credentials_store="file"\nmodel="gpt-5.4"\nopenai_base_url='+json.dumps(base+'/v1')+'\n')
                (home/'auth.json').write_text(json.dumps({'auth_mode':'apikey','OPENAI_API_KEY':'synthetic-not-a-secret'}))
                bridge = SimpleNamespace(codex_home=home,data_dir=folder,catalog_reader=SimpleNamespace(executable=runtime),
                    account=SimpleNamespace(lock=threading.Lock()),lock=threading.RLock(),live={},remote_bridges={},submissions={})
                accounts = Accounts(bridge)
                # Persist an existing OpenAI-provider chat using the same local mock, never a real model.
                with TurnRPC(home,runtime) as rpc:
                    old = rpc.request('thread/start',{'cwd':str(folder),'modelProvider':'openai'})['thread']['id']
                    rpc.request('thread/name/set',{'threadId':old,'name':'Routing fixture'})
                    turn(rpc,old)
                row = accounts.add_api({'name':'Synthetic API','baseUrl':base+'/v1','apiKey':'synthetic-not-a-secret','model':'gpt-5.4'})['accounts'][0]
                prepared = accounts.prepare(row,accounts.snapshot_files())
                for name in ('config.toml','auth.json'):
                    if (prepared/name).exists():(home/name).write_bytes((prepared/name).read_bytes())
                    else:(home/name).unlink(missing_ok=True)
                with TurnRPC(home,runtime) as rpc:
                    auth = rpc.request('account/read',{'refreshToken':False})['account']
                    assert auth and auth['type']=='apiKey', 'API mode must have native API-key login state'
                    for provider in (None,'openai'):
                        params = {'cwd':str(folder),'model':'gpt-5.4','approvalPolicy':'never'}
                        if provider:params['modelProvider']=provider
                        identifier = rpc.request('thread/start',params)['thread']['id']
                        turn(rpc,identifier)
                    resumed = rpc.request('thread/resume',{'threadId':old,'model':'gpt-5.4','modelProvider':'openai','approvalPolicy':'never'})
                    assert resumed['thread']['modelProvider']=='openai'
                    turn(rpc,old)
                assert 'synthetic-not-a-secret' not in (home/'config.toml').read_text()
                print('PASS: native API login; new default and OpenAI-provider chats plus resumed OpenAI chat sent authenticated /v1/responses to localhost. No real credentials or desktop restart.')
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__':
    main()
