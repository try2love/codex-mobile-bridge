#!/usr/bin/env python3
"""Read the native runtime's effective config and a loopback-only model catalog."""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from bridge.catalog import Catalog


class Handler(BaseHTTPRequestHandler):
    calls=[]
    payload={'data':[{'id':'gemini-fixture'},{'id':'gemini-second'}]}
    def log_message(self,*args):pass
    def do_CONNECT(self):self.send_error(403)
    def do_GET(self):
        self.calls.append((self.path,self.headers.get('Authorization')))
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps(self.payload).encode())


root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(dir=root/'.tmp') as directory:
    home=Path(directory)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base='http://127.0.0.1:'+str(server.server_port)
    (home/'config.toml').write_text('cli_auth_credentials_store="file"\nmodel_provider="bridge_api"\nmodel="gemini-fixture"\n[model_providers.bridge_api]\nname="Fixture"\nbase_url='+json.dumps(base+'/v1')+'\nrequires_openai_auth=true\nwire_api="responses"\n')
    (home/'auth.json').write_text(json.dumps({'auth_mode':'apikey','OPENAI_API_KEY':'synthetic-catalog-key'}))
    env={key:base for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')}
    env.update(NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
    try:
        for payload in (Handler.payload, {'models':[{'slug':'gemini-fixture'},
                         {'slug':'gemini-second'}, {'slug':'hidden-fixture','visibility':'hide'}]}):
            Handler.payload=payload;Handler.calls.clear()
            with patch.dict(os.environ,env):
                for key in ('OPENAI_API_KEY','OPENAI_BASE_URL'):os.environ.pop(key,None)
                result=Catalog(home).get(home,refresh=True)
            ids=[model['id'] for model in result['models']]
            assert ids==['gemini-fixture','gemini-second'],ids
            assert ('/v1/models','Bearer synthetic-catalog-key') in Handler.calls
            assert result['modelSource']=='api' and not result['fastMode']['allowed']
            assert 'synthetic-catalog-key' not in json.dumps(result)
        print('PASS: OpenAI and CC Switch loopback responses produce the same public chat catalog, no real account or inference.')
    finally:
        server.shutdown();server.server_close()
