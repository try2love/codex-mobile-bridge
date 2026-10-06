import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from bridge.mobile_events import MobileEvents
from bridge.mobile_push import MobilePush, InvalidPushToken, payload
from bridge.notifications import write_json

ROOT = Path(__file__).resolve().parents[1]
THREAD = '00000000-0000-4000-8000-000000000001'

class NativePushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name); self.feed = MobileEvents(self.path); self.sender = Mock()
        self.push = MobilePush(self.path, self.feed, self.sender); self.push.session_valid = lambda key: key == 'owner'
        write_json(self.path/'mobile-push.json', {'apns': {'bundleId': 'io.example.bridge'}})
        self.body = {'deviceId': THREAD, 'kind': 'apns', 'token': 'ab'*32}
    def event(self, completed=False):
        self.feed.append([str(self.feed.read()['cursor'])], {'id': THREAD, 'host': 'local'}, 'private title', 'private text', completed)
    def register(self): self.push.register(self.body, 'owner', 'https://codex.try2love.com')
    def test_skip_history_retry_persist_no_duplicate(self):
        self.event(); self.register(); self.push.drain(True); self.sender.assert_not_called()
        self.event(); self.sender.side_effect = OSError('secret provider failure'); self.push.drain(True)
        d=next(iter(self.push.devices.values())); self.assertEqual(d['cursor'],1); self.assertNotIn('secret',d['error'])
        d['next']=0;self.sender.side_effect=None;self.push.drain(True);self.push.drain(True)
        self.assertEqual(self.sender.call_count,2);self.assertEqual(next(iter(self.push.devices.values()))['cursor'],2)
        restored=MobilePush(self.path,self.feed,self.sender);self.assertEqual(restored.devices,self.push.devices)
    def test_revoked_device_never_delivers(self):
        self.register();self.event();self.push.session_valid=lambda key:False;self.push.drain(True)
        self.sender.assert_not_called();self.assertEqual(self.push.devices,{})
    def test_invalid_token_removed(self):
        self.register();self.event();self.sender.side_effect=InvalidPushToken();self.push.drain(True);self.assertEqual(self.push.devices,{})
    def test_off_and_unwatched_do_not_send(self):
        self.register();self.event();self.push.drain(False);self.push.drain(True,lambda event:False);self.sender.assert_not_called()
    def test_payload_privacy_and_activity_routing(self):
        self.event();event=self.feed.read()['events'][0];device={'origin':'https://codex.try2love.com','kind':'fcm','token':'sample'}
        result=payload(device,event);self.assertNotIn('private',str(result));self.assertEqual(result['message']['data']['thread'],THREAD)
        device['kind']='activity';self.assertEqual(payload(device,event)['aps']['content-state']['phase'],'waiting')
        event['kind']='completion';self.assertEqual(payload(device,event)['aps']['event'],'end')
    def test_validation_and_unregister(self):
        with self.assertRaises(ValueError):self.push.register({**self.body,'token':'bad'},'owner','https://codex.try2love.com')
        self.register();self.push.register({**self.body,'enabled':False},'owner','https://codex.try2love.com');self.assertEqual(self.push.devices,{})
    def test_activity_only_matches_selected_chat(self):
        self.push.register({**self.body,'kind':'activity','thread':THREAD,'host':'remote'},'owner','https://codex.try2love.com')
        self.event();self.push.drain(True);self.sender.assert_not_called()

    def test_http_registration_needs_csrf_and_trusted_origin(self):
        import http.client,json,threading
        from bridge.httpd import GatewayServer
        from bridge.auth import password_record
        from bridge.notifications import Notifications
        server=GatewayServer(('127.0.0.1',0),object(),{'auth':{'mode':'password','username':'test',**password_record('long-password')},'origins':['https://gateway.example']},ROOT/'web')
        server.notifications=Notifications(None,self.path)
        token,session=server.auth.new_session('127.0.0.1')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request(csrf='',origin='https://gateway.example',cookie=True):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Host':'gateway.example','Origin':origin,'Content-Type':'application/json','X-CSRF-Token':csrf}
            if cookie:headers['Cookie']='codex_mobile_session='+token
            c.request('POST','/api/mobile/push',json.dumps(self.body),headers);r=c.getresponse();status=r.status;r.read();c.close();return status
        try:
            self.assertEqual(request(cookie=False),401)
            self.assertEqual(request(),403)
            self.assertEqual(request(session['csrf'],'https://evil.example'),403)
            self.assertEqual(request(session['csrf']),200)
            server.auth.logout(token);self.assertEqual(request(session['csrf']),401)
        finally:server.shutdown();worker.join();server.server_close()

    def test_provider_uses_fixed_https_endpoints_and_does_not_follow_redirects(self):
        import sys,types
        from unittest.mock import patch
        from bridge.mobile_push import send_provider
        self.event();event=self.feed.read()['events'][0];device={'kind':'apns','token':'ab'*32,'origin':'https://codex.try2love.com'}
        key=self.path/'dummy-key';key.write_text('TEST ONLY')
        client=Mock();client.post.return_value.status_code=200
        factory=Mock();factory.return_value.__enter__=Mock(return_value=client);factory.return_value.__exit__=Mock(return_value=False)
        jwt=types.SimpleNamespace(encode=Mock(return_value='test-token'))
        with patch.dict(sys.modules,{'httpx':types.SimpleNamespace(Client=factory),'jwt':jwt}):
            send_provider({'apns':{'teamId':'test','keyId':'test','keyFile':str(key),'bundleId':'io.example.bridge'}},device,event)
        self.assertTrue(factory.call_args.kwargs['http2']);self.assertFalse(factory.call_args.kwargs['follow_redirects'])
        self.assertTrue(client.post.call_args.args[0].startswith('https://api.sandbox.push.apple.com/3/device/'))
        self.assertEqual(client.post.call_args.kwargs['headers']['apns-push-type'],'alert')
        self.assertNotIn('private',str(client.post.call_args.kwargs['json']))

    def test_live_activity_running_updates_coalesce(self):
        self.push.register({**self.body,'kind':'activity','thread':THREAD,'host':'local'},'owner','https://codex.try2love.com')
        self.push.update_activity({'id':THREAD,'host':'local'},'running')
        self.push.drain(True);self.push.drain(True)
        self.assertEqual(self.sender.call_count,1)
        config,device,event=self.sender.call_args.args
        self.assertEqual(payload(device,event)['aps']['content-state']['phase'],'running')
        self.push.update_activity({'id':THREAD,'host':'local'},'waiting');self.push.drain(True)
        self.assertEqual(self.sender.call_count,2)
