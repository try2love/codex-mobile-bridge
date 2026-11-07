#!/usr/bin/env python3
"""One native CLI first-message check against a loopback-only echo API."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bridge.clients.claude import cli_session
from bridge.clients.errors import BridgeUnavailable
from ssh_claude_native import MockAPI, wait_for


class ModelEcho(MockAPI):
    paths = []
    def do_POST(self):
        self.paths.append(urlsplit(self.path).path)
        if urlsplit(self.path).path != '/v1/messages':
            self.send_error(403); return
        super().do_POST()


def main():
    if not shutil.which('claude'):
        raise RuntimeError('An installed Claude CLI is required')
    with tempfile.TemporaryDirectory(prefix='creation-model-echo-', dir=ROOT/'.tmp') as folder:
        root = Path(folder); profile, cwd = root/'profile', root/'project'; profile.mkdir(); cwd.mkdir()
        ModelEcho.root=root; ModelEcho.bodies=[]; ModelEcho.credentials=[]; ModelEcho.paths=[]
        server=ThreadingHTTPServer(('127.0.0.1',0),ModelEcho)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        endpoint='http://127.0.0.1:'+str(server.server_port)
        (profile/'settings.json').write_text(json.dumps({'permissions':{'defaultMode':'plan'},
            'model':'fixture-not-selected','env':{'ANTHROPIC_API_KEY':'isolated-fixture-key','ANTHROPIC_BASE_URL':endpoint}}))
        environment={key:value for key,value in os.environ.items() if key in
                     ('PATH','SystemRoot','WINDIR','COMSPEC','TEMP','TMP','TMPDIR','LANG','LC_ALL','SHELL')}
        environment.update({'HOME':str(root),'USERPROFILE':str(root),'CLAUDE_CONFIG_DIR':str(profile),
            'HTTP_PROXY':endpoint,'HTTPS_PROXY':endpoint,'ALL_PROXY':endpoint,'NO_PROXY':'127.0.0.1,localhost',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1'})
        adapter=cli_session.ClaudeCli(root/'uploads',profile_resolver=cli_session._profile)
        verified=[]; original=cli_session._verify_creation_settings
        def settings_before_user(owned, selection):
            applied=cli_session._applied_settings(owned)['applied']
            print(json.dumps({'stage':'startup-settings','model':applied.get('model'),'effort':applied.get('effort'),
                              'loopbackMessages':len(ModelEcho.bodies)}),flush=True)
            original(owned, selection)
            assert not ModelEcho.bodies, 'A user message reached the API before settings were verified'
            verified.append({'model':applied['model'],'effort':applied['effort']})
        try:
            with patch.dict(os.environ,environment,clear=True),patch.object(cli_session,'_verify_creation_settings',settings_before_user):
                selected=cli_session._profile()
                assert selected
                try:
                    adapter.create(str(cwd),selected['origin'],'MUST_NOT_SEND',selection={
                        'model':'claude-sonnet-4-5-20250929','effort':'low','manualModel':True,'manualEffort':True})
                except BridgeUnavailable:
                    assert not ModelEcho.bodies and not verified
                else:
                    raise AssertionError('This native Sonnet 4.5 fixture must not silently accept an ignored effort')
                result=adapter.create(str(cwd),selected['origin'],'CREATION_MODEL_FIRST_MESSAGE',selection={
                    'model':'claude-sonnet-4-5-20250929','manualModel':True})
                assert wait_for(lambda:bool(ModelEcho.bodies),timeout=20)
                assert verified==[{'model':'claude-sonnet-4-5-20250929','effort':None}]
                assert len(ModelEcho.bodies)==1
                assert ModelEcho.bodies[0]['model']=='claude-sonnet-4-5-20250929'
                assert all(key=='isolated-fixture-key' for key in ModelEcho.credentials)
                assert result['status']=='ready'
                print(json.dumps({'nativeCli':True,'scope':'isolated-loopback-only','firstMessageCount':1,
                    'model':verified[0]['model'],'unsupportedEffortBlockedBeforeFirstMessage':True,
                    'realModelRequests':0}),flush=True)
        finally:
            adapter.close();server.shutdown();server.server_close()


if __name__=='__main__':main()
