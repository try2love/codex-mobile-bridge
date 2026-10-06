import copy
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from bridge import access
from bridge.desktop import Desktop
from bridge.notifications import Notifications, write_json
from bridge.ssh_tunnel import SSHTunnel, command
from bridge.tunnel import QuickTunnel
import run

ROOT = Path(__file__).resolve().parents[1]


class LineStream:
    def __init__(self, lines, gate):
        self.lines = lines
        self.gate = gate

    def __iter__(self):
        yield from self.lines
        self.gate.wait()


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.directory = Path(self.temp.name)
        self.desktop = Desktop(self.directory)
        self.desktop.status = lambda: {'running': False, 'pid': None}
        self.value = self.desktop.snapshot()
        self.value['preferences'].update(codexHome=str(self.directory))

    def tearDown(self):
        self.temp.cleanup()

    def server(self):
        value = copy.deepcopy(self.value)
        value['preferences']['connections'] = [{**access.DEFAULTS, 'id':'server', 'name':'Server', 'enabled':True, 'accessMode':'server', 'publicUrl':'https://codex.example.com/', 'sshTarget':'my-server'}]
        return value

    def test_legacy_quick_settings_roundtrip_keeps_lan_and_port(self):
        write_json(self.directory/'desktop.json', {'tunnel': True, 'port': 8787, 'lan': True})
        self.assertEqual(self.desktop.preferences()['connections'][0]['accessMode'], 'quick')
        value = self.desktop.snapshot()
        value['preferences']['codexHome'] = str(self.directory)
        self.desktop.save(value)
        self.assertTrue(self.desktop.preferences()['tunnel'])
        self.assertEqual(self.desktop.preferences()['port'], 8787)

    def test_fixed_url_allowlist_export_and_mode_switch(self):
        value = self.server()
        value['origins'] = ['https://another.example.com']
        self.desktop.save(value)
        state = self.desktop.snapshot()
        self.assertEqual(state['urls'][0], 'https://codex.example.com/')
        self.assertEqual(self.desktop.config()['publicUrl'], 'https://codex.example.com')
        self.assertIn('https://codex.example.com', state['origins'])
        self.assertEqual(self.desktop.config()['connections'][0]['sshTarget'], 'my-server')
        self.assertNotIn('--tunnel', self.desktop.argv())
        path = self.desktop.export_deployment({'id':'server'})['path']
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), {'compose.yaml', 'Caddyfile', '部署说明.md', 'DEPLOYMENT_EN.md'})
            self.assertIn('127.0.0.1:18787', archive.read('Caddyfile').decode())
            self.assertNotIn('首次登录.txt', archive.namelist())
        state['preferences']['connections'] = []
        self.desktop.save(state)
        self.assertEqual(self.desktop.config()['origins'], ['https://another.example.com'])
        self.assertEqual(self.desktop.config()['publicUrl'], '')
        self.assertNotIn('--ssh-target', self.desktop.argv())
        self.assertEqual(self.desktop.preferences()['port'], 8787)

    def test_invalid_input_never_partially_saves(self):
        before = self.desktop.config()
        for changes in [dict(publicUrl='https://a.example/path'), dict(publicUrl='https://x;evil'),
                        dict(publicUrl='https://user:password@a.example'), dict(publicUrl='http://a.example'),
                        dict(publicUrl='https://example.com:8443'), dict(sshTarget='-oProxyCommand=evil'),
                        dict(sshTarget='a; echo bad'), dict(sshRemotePort=True), dict(sshRemotePort=22)]:
            value = self.server()
            value['preferences']['connections'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.desktop.save(value)
            self.assertEqual(self.desktop.config(), before)

    def test_default_https_port_matches_browser_origin_serialization(self):
        value = self.server()
        value['preferences']['connections'][0]['publicUrl'] = 'https://CODEX.example.com:443/'
        self.desktop.save(value)
        self.assertEqual(self.desktop.config()['origins'], ['https://codex.example.com'])

    def test_nas_requires_reachable_computer_address_and_generates_literal_config(self):
        value = self.value
        value['preferences']['connections'] = [{**access.DEFAULTS, 'id':'nas', 'name':'NAS', 'enabled':True, 'accessMode':'nas', 'publicUrl':'https://codex.example.com:8443', 'proxyUpstream':'http://192.168.2.30:8787'}]
        self.desktop.save(value)
        files = self.desktop.deployment({'id':'nas'})['files']
        self.assertIn('proxy_pass http://192.168.2.30:8787;', files['nginx.conf'])
        self.assertIn('Host $http_host', files['nginx.conf'])
        self.assertIn('127.0.0.1', files['compose.yaml'])
        for changes in [dict(lan=False), dict(proxyUpstream='http://127.0.0.1:8787'),
                        dict(proxyUpstream='http://192.168.2.30:80'), dict(proxyUpstream='http://x/${evil}:8787')]:
            invalid = copy.deepcopy(value)
            (invalid['preferences'] if 'lan' in changes else invalid['preferences']['connections'][0]).update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.desktop.save(invalid)

    def test_running_configuration_cannot_change_external_access(self):
        self.desktop.save(self.value)
        self.desktop.status = lambda: {'running': True, 'pid': os.getpid()}
        with self.assertRaisesRegex(ValueError, '先停止'):
            self.desktop.save(self.server())
        invalid = copy.deepcopy(self.value)
        invalid['origins'] = ['https://new.example.com']
        with self.assertRaisesRegex(ValueError, '先停止'):
            self.desktop.save(invalid)

    def test_probe_checks_current_instance_and_does_not_send_credentials(self):
        preferences = access.select_connection(self.server()['preferences'], {'id':'server'})
        for remote, success in [({'instanceId': 'same'}, True), ({'instanceId': 'another'}, False), ({}, False)]:
            with patch('bridge.access.read_auth', side_effect=[{'instanceId': 'same'}, remote]) as read:
                if success:
                    self.assertIn('已连到当前网关', access.check_entry(preferences)['message'])
                else:
                    with self.assertRaisesRegex(ValueError, '当前网关'):
                        access.check_entry(preferences)
                self.assertEqual(read.call_args_list[0].args, ('http://127.0.0.1:8787',))
        self.desktop.save(self.server())
        with self.assertRaisesRegex(ValueError, '先启动'):
            self.desktop.check_entry({'id':'server'})

    def test_fixed_notification_url_precedence_keeps_explicit_override(self):
        notifications = Notifications(None, self.directory, lambda: ['https://a.trycloudflare.com'], lambda: 'https://fixed.example.com')
        self.assertTrue(notifications.click_url({}, 'id', 'local').startswith('https://fixed.example.com/#'))
        self.assertTrue(notifications.click_url({'clickBase': 'https://override.example.com'}, 'id', 'local').startswith('https://override.example.com/#'))

    def test_old_tunnel_url_and_stale_ssh_status_are_not_shown(self):
        self.desktop.save(self.server())
        (self.directory/'外网地址.txt').write_text('https://stale.trycloudflare.com')
        write_json(self.directory/'ssh-status.json', {'pid': 101, 'message': 'old connection'})
        self.desktop.status = lambda: {'running': True, 'pid': 102}
        state = self.desktop.snapshot()
        self.assertFalse(any('trycloudflare' in u for u in state['urls']))
        self.assertEqual(state['externalStatus'], {})

    def test_parallel_entries_migrate_and_disable_independently(self):
        legacy = {'port':8787, 'lan':True, 'accessMode':'server', 'publicUrl':'https://old.example.com', 'sshTarget':'old-server'}
        write_json(self.directory/'desktop.json', legacy)
        self.assertEqual(self.desktop.preferences()['connections'][0]['id'], 'legacy-server')
        self.assertEqual(json.loads((self.directory/'desktop.json').read_text(encoding='utf-8')), legacy)
        value = self.server()
        value['preferences']['connections'] += [
            {**access.DEFAULTS,'id':'quick','name':'Temporary','accessMode':'quick','enabled':True},
            {**access.DEFAULTS,'id':'nas','name':'Home','accessMode':'nas','enabled':True,'publicUrl':'https://nas.example.com','proxyUpstream':'http://192.168.1.10:8787'}]
        self.desktop.save(value)
        self.assertIn('--lan', self.desktop.argv())
        self.assertIn('--tunnel', self.desktop.argv())
        self.assertEqual(self.desktop.config()['origins'], ['https://codex.example.com','https://nas.example.com'])
        value=self.desktop.snapshot()
        value['preferences']['connections'][0]['enabled']=False
        self.desktop.save(value)
        self.assertEqual(self.desktop.config()['publicUrl'],'https://nas.example.com')
        self.assertEqual(self.desktop.config()['origins'],['https://nas.example.com'])
        self.assertIn('--tunnel', self.desktop.argv())
        with self.assertRaises(ValueError): self.desktop.deployment({'id':'server'})
        self.assertIn('192.168.1.10:8787',self.desktop.deployment({'id':'nas'})['files']['nginx.conf'])

    def test_duplicate_forward_and_quick_connections_are_rejected(self):
        for kind in ['quick','server']:
            value=self.server();first=value['preferences']['connections'][0]
            first['accessMode']=kind
            value['preferences']['connections'].append({**first,'id':'other','publicUrl':'https://other.example.com'})
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.desktop.save(value)

    def test_latest_log_records_keep_traceback_order(self):
        (self.directory/'gateway.log').write_text('startup\n2026-10-01 10:00:00 INFO old\n2026-10-01 10:01:00 ERROR new\nTraceback:\n  call()\nValueError: bad\n',encoding='utf-8')
        text=self.desktop.logs()['text']
        self.assertLess(text.index('ERROR new'),text.index('INFO old'))
        self.assertIn('ERROR new\nTraceback:\n  call()\nValueError: bad',text)


class SSHTests(unittest.TestCase):
    def test_command_uses_private_forward_and_existing_identity(self):
        with patch('bridge.ssh_tunnel.shutil.which', return_value='/usr/bin/ssh'):
            args = command('my-server', 18787, 8787)
        self.assertEqual(args[-2:], ['127.0.0.1:18787:127.0.0.1:8787', 'my-server'])
        for value in ['StrictHostKeyChecking=yes', 'BatchMode=yes', 'ExitOnForwardFailure=yes',
                      'ControlPath=none', 'ForwardAgent=no', 'UpdateHostKeys=no']:
            self.assertIn(value, args)

    def test_success_close_and_failed_connection_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder, patch('bridge.ssh_tunnel.shutil.which', return_value='ssh'):
            tunnel = SSHTunnel('test', 18787, 8787, folder)
            seen = []
            ready = threading.Event()
            real_status = tunnel.status
            def status(state, message):
                seen.append(state)
                real_status(state, message)
                if state == 'retrying':
                    ready.set()
            tunnel.status = status
            class Process:
                stderr = io.StringIO('debug1: remote forward success for: listen 127.0.0.1:18787, connect 127.0.0.1:8787\n')
                def wait(self, **kwargs): return 255
                def poll(self): return 255
            with patch('bridge.ssh_tunnel.subprocess.Popen', return_value=Process()) as spawn:
                tunnel.start()
                self.assertTrue(ready.wait(2))
                tunnel.close()
                self.assertFalse(tunnel.thread.is_alive())
                self.assertEqual(spawn.call_count, 1)
            self.assertEqual(seen, ['connecting', 'connected', 'retrying', 'stopped'])
            self.assertEqual(json.loads((Path(folder)/'ssh-status.json').read_text(encoding='utf-8'))['state'], 'stopped')


class ConcurrentRuntimeTests(unittest.TestCase):
    def test_parallel_entries_serve_lan_while_quick_tunnel_waits_and_close_all(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            data = Path(directory)
            config = data/'config.json'
            config.write_text(json.dumps({'auth': {'mode': 'none'}, 'origins': []}), encoding='utf-8')
            rows = [
                {**access.DEFAULTS, 'id': 'a', 'enabled': True, 'accessMode': 'server', 'sshTarget': 'server-a', 'publicUrl': 'https://a.example.com'},
                {**access.DEFAULTS, 'id': 'b', 'enabled': True, 'accessMode': 'server', 'sshTarget': 'server-b', 'publicUrl': 'https://b.example.com'},
                {**access.DEFAULTS, 'id': 'quick', 'enabled': True, 'accessMode': 'quick'},
                {**access.DEFAULTS, 'id': 'off', 'enabled': False, 'accessMode': 'server'}]
            connecting, closed = threading.Event(), threading.Event()
            quick = MagicMock()
            def connect():
                connecting.set()
                if not closed.wait(3):
                    raise RuntimeError('LAN was blocked by the tunnel handshake')
                raise RuntimeError('closed')
            quick.start.side_effect = connect
            quick.close.side_effect = closed.set
            server = MagicMock()
            def serve(**kwargs):
                self.assertTrue(connecting.wait(2))
                self.assertFalse(closed.is_set())
            server.serve_forever.side_effect = serve
            tunnels = [MagicMock(), MagicMock()]
            with patch.object(run.sys, 'argv', ['run.py', '--config', str(config), '--lan']), \
                 patch.object(run.sys, 'stdout', MagicMock()), patch.object(run.sys, 'stderr', MagicMock()), \
                 patch.object(run, 'addresses', return_value=['127.0.0.1']), \
                 patch.object(run, 'Bridge'), patch.object(run, 'Notifications'), \
                 patch.object(run, 'GatewayControl'), patch.object(run.signal, 'signal'), \
                 patch.object(run, 'GatewayServer', return_value=server) as make_server, \
                 patch.object(run, 'SSHTunnel', side_effect=tunnels) as make_ssh, \
                 patch.object(run, 'QuickTunnel', return_value=quick):
                run.main(connections=rows)
            self.assertEqual([call.args[-1] for call in make_ssh.call_args_list], ['a', 'b'])
            for tunnel in tunnels:
                tunnel.start.assert_called_once()
                tunnel.close.assert_called_once()
            self.assertTrue(closed.is_set())
            self.assertIn('https://a.example.com', make_server.call_args.args[2]['origins'])
            self.assertIn('https://b.example.com', make_server.call_args.args[2]['origins'])
            self.assertFalse((data/'gateway.pid').exists())

    def test_quick_tunnel_concurrent_close_cleans_process_and_cannot_restart(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            data = Path(directory)
            executable = data/'cloudflared'
            executable.touch()
            tunnel = QuickTunnel(executable, 8787, data, MagicMock())
            spawning, release = threading.Event(), threading.Event()
            process = MagicMock(stdout=io.StringIO(''))
            process.poll.return_value = None
            def spawn(*args, **kwargs):
                spawning.set()
                release.wait(2)
                return process
            errors = []
            def start():
                try: tunnel.start()
                except RuntimeError as error: errors.append(str(error))
            with patch('bridge.tunnel.subprocess.Popen', side_effect=spawn):
                starter = threading.Thread(target=start)
                starter.start()
                self.assertTrue(spawning.wait(2))
                closer = threading.Thread(target=tunnel.close)
                closer.start()
                self.assertTrue(tunnel.closed.wait(2))
                release.set()
                starter.join(3); closer.join(3)
                self.assertFalse(starter.is_alive() or closer.is_alive())
                process.terminate.assert_called()
                with self.assertRaisesRegex(RuntimeError, '隧道已停止'): tunnel.start()
            self.assertTrue(errors)
            tunnel.on_origin.assert_not_called()

    def test_quick_tunnel_keeps_waiting_while_cloudflared_retries_api(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            data = Path(directory)
            executable = data/'cloudflared';executable.touch()
            api_ready = threading.Event()
            first_failure = threading.Event()
            class RetryStream:
                def __init__(self): self.lines = iter([
                    'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": EOF',
                    'https://late.trycloudflare.com',
                    'Registered tunnel connection',
                ])
                def __iter__(self):
                    yield next(self.lines)
                    first_failure.set()
                    api_ready.wait(3)
                    yield from self.lines
            process = MagicMock(stdout=RetryStream(), poll=lambda: None)
            process.terminate.side_effect = api_ready.set
            tunnel = QuickTunnel(executable, 8787, data, MagicMock())
            starter = threading.Thread(target=tunnel.start)
            try:
                with patch('bridge.tunnel.subprocess.Popen', return_value=process):
                    starter.start()
                    # A slow Quick Tunnel must remain explicitly connecting instead
                    # of turning into a false failure while cloudflared retries.
                    self.assertTrue(first_failure.wait(2))
                    self.assertFalse(tunnel.ready.is_set())
                    self.assertEqual(json.loads((data/'cloudflare-status.json').read_text(encoding='utf-8'))['state'], 'connecting')
                    api_ready.set();starter.join(3)
                    self.assertFalse(starter.is_alive())
                    self.assertEqual(tunnel.url, 'https://late.trycloudflare.com')
                    self.assertEqual(tunnel.on_origin.call_args.args, ('https://late.trycloudflare.com',))
            finally:
                tunnel.close();starter.join(3)
            self.assertFalse(starter.is_alive())
            process.terminate.assert_called_once()

    def test_quick_tunnel_creates_new_url_after_tunnel_not_found(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            data = Path(directory)
            executable = data/'cloudflared'
            executable.touch()
            old_gate, new_gate = threading.Event(), threading.Event()
            old_process = MagicMock(stdout=LineStream([
                'https://old.trycloudflare.com',
                'Registered tunnel connection',
                'ERR Connection terminated error="Unauthorized: Tunnel not found"',
            ], old_gate))
            old_process.poll.return_value = None
            old_process.terminate.side_effect = old_gate.set
            new_process = MagicMock(stdout=LineStream([
                'https://new.trycloudflare.com',
                'Registered tunnel connection',
            ], new_gate))
            new_process.poll.return_value = None
            new_process.terminate.side_effect = new_gate.set
            tunnel = QuickTunnel(executable, 8787, data, MagicMock())
            tunnel.RESTART_DELAY = 0

            replaced = threading.Event()
            tunnel.on_origin.side_effect = lambda url: replaced.set() if url == 'https://new.trycloudflare.com' else None
            real_is_set = tunnel.ready.is_set
            def ready_after_loss():
                # Force the reader to report loss before the supervisor checks
                # readiness, as can happen on a busy Windows runner.
                if tunnel.process is old_process:
                    tunnel.broken.wait(3)
                return real_is_set()

            with patch('bridge.tunnel.subprocess.Popen', side_effect=[old_process, new_process]), \
                 patch.object(tunnel.ready, 'is_set', side_effect=ready_after_loss):
                starter = threading.Thread(target=tunnel.start)
                try:
                    starter.start()
                    self.assertTrue(replaced.wait(5), 'Lost tunnel was not replaced promptly')
                    starter.join(3)
                    self.assertFalse(starter.is_alive())
                    self.assertEqual(tunnel.url, 'https://new.trycloudflare.com')
                    self.assertEqual(tunnel.on_origin.call_args_list[0].args, ('https://old.trycloudflare.com',))
                    self.assertEqual(tunnel.on_origin.call_args_list[-1].args, ('https://new.trycloudflare.com',))
                finally:
                    tunnel.close()
                    starter.join(3)
                old_process.terminate.assert_called_once()
                new_process.terminate.assert_called_once()
