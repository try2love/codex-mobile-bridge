import copy
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

from bridge import access, connection_secrets, server_connection
from bridge.desktop import Desktop
from bridge.address_notifications import entry_urls
from bridge.notifications import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
try:
    import paramiko
except ImportError:
    paramiko = None


def entry(**updates):
    return {**access.DEFAULTS, 'id': 'test-server', 'name': 'Test', 'enabled': True,
            'accessMode': 'server', 'publicUrl': 'https://codex.example.com',
            'sshAuth': 'password', 'sshHost': '127.0.0.1', 'sshUser': 'test', **updates}


class ConnectionSetupTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_start_cloudflare_does_not_require_ssh_password(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        value = desktop.snapshot()
        value['preferences'].update(codexHome=str(self.directory), lan=False,
                                    cloudflared=__import__('sys').executable,
                                    connections=[entry(accessMode='cloudflare')])
        desktop.save(value)
        with patch('bridge.desktop.socket.socket'), patch('bridge.desktop.subprocess.Popen') as spawn:
            spawn.return_value.pid = 123
            result = desktop.start({'connectionSecrets': {'test-server': {'tunnelToken': 'synthetic-token'}}})
        self.assertTrue(result['started'])
        sent = json.loads(spawn.return_value.stdin.write.call_args.args[0])
        self.assertEqual(sent, {'test-server': {'tunnelToken': 'synthetic-token'}})

    def test_validation_reports_exact_connection_and_notification_field(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        value = desktop.snapshot()
        value['preferences'].update(codexHome=str(self.directory), connections=[
            entry(id='good', accessMode='cloudflare'),
            entry(id='bad', accessMode='cloudflare', publicUrl='https://wrong.example/path')])
        with self.assertRaises(ValueError) as failure:
            desktop.save(value)
        self.assertEqual(failure.exception.validation, {'field': 'publicUrl', 'connectionId': 'bad'})
        value['preferences']['connections'] = []
        value['notifications']['barkServer'] = 'bad-url'
        with self.assertRaises(ValueError) as failure:
            desktop.save(value)
        self.assertEqual(failure.exception.validation['field'], 'bark-server')

    def test_saved_credentials_read_privately_without_poll_snapshot_leak(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        value = desktop.snapshot()
        value['preferences'].update(codexHome=str(self.directory), connections=[entry()])
        value['notifications'].update(token='fixture-ntfy', barkKey='fixture-bark', pushplusToken='fixture-push')
        desktop.save(value)
        vault = MagicMock()
        vault.get_password.return_value = None
        with patch.object(connection_secrets, 'backend', return_value=vault):
            desktop.connection_credentials({'id': 'test-server', 'secrets': {'password': 'fixture-secret'}})
            vault.get_password.return_value = vault.set_password.call_args.args[2]
            self.assertEqual(Desktop(self.directory).read_credentials({'id': 'test-server'}), {'password': 'fixture-secret'})
        self.assertEqual(desktop.read_credentials({})['token'], 'fixture-ntfy')
        self.assertNotIn('fixture-', json.dumps(desktop.snapshot()))
        with self.assertRaises(ValueError):
            desktop.read_credentials({'id': 'deleted'})

    def test_disabled_and_deleted_ssh_do_not_block_cloudflare(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        for rows in ([entry(enabled=False), entry(id='cf', accessMode='cloudflare')],
                     [entry(id='cf', accessMode='cloudflare')]):
            value = desktop.snapshot()
            value['preferences'].update(codexHome=str(self.directory), lan=False,
                                        cloudflared=__import__('sys').executable, connections=rows)
            desktop.save(value)
            with patch('bridge.desktop.socket.socket'), patch('bridge.desktop.subprocess.Popen'):
                self.assertTrue(desktop.start({'connectionSecrets': {'cf': {'tunnelToken': 'fixture-token'}}})['started'])

    def test_new_fields_roundtrip_and_secrets_not_serialized(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        value = desktop.snapshot()
        value['preferences'].update(codexHome=str(self.directory), connections=[entry(password='SECRET', sshPort=2222)])
        desktop.save(value)
        result = desktop.preferences()['connections'][0]
        self.assertEqual(result['sshPort'], 2222)
        self.assertEqual(result['sshAuth'], 'password')
        self.assertNotIn('password', result)
        self.assertNotIn('SECRET', (self.directory/'config.json').read_text())
        self.assertNotIn('SECRET', (self.directory/'desktop.json').read_text())

    def test_legacy_config_and_named_domain_validation(self):
        legacy = entry(sshAuth='config', sshTarget='old-alias')
        self.assertEqual(access.validate(legacy)['sshTarget'], 'old-alias')
        named = entry(accessMode='cloudflare')
        self.assertEqual(access.public_urls({'connections': [named]}), ['https://codex.example.com'])
        for changes in ({'sshPort': 0}, {'sshPort': True}, {'sshHost': 'x;whoami'},
                        {'sshUser': '-root'}, {'sshAuth': 'unknown'}, {'publicUrl': 'https://x.example/a'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                access.validate({**entry(), **changes})
        self.assertEqual(entry_urls({'connections': [named]}, [], external_status={}), [])
        self.assertEqual(entry_urls({'connections': [named]}, [], external_status={'test-server': {'state': 'connected'}}), ['https://codex.example.com'])

    def test_vault_no_plaintext_and_failure_does_not_fallback(self):
        vault = MagicMock()
        vault.get_password.return_value = None
        with patch.object(connection_secrets, 'backend', return_value=vault):
            connection_secrets.save(self.directory, 'one', {'password': 'very-secret'})
            call = vault.set_password.call_args.args
            self.assertEqual(json.loads(call[2]), {'password': 'very-secret'})
            self.assertNotEqual(connection_secrets.service(self.directory), connection_secrets.service(self.directory/'other'))
            vault.set_password.side_effect = RuntimeError('vault locked')
            with self.assertRaisesRegex(ValueError, '无法保存'):
                connection_secrets.save(self.directory, 'two', {'password': 'very-secret'})
        self.assertNotIn('very-secret', ''.join(p.read_text(encoding='utf-8') for p in self.directory.iterdir()))
        self.assertEqual(read_json(self.directory/'connection-vault.json', {}), {'one': True})
        with patch.object(connection_secrets, 'backend', side_effect=AssertionError('vault not needed')):
            self.assertEqual(connection_secrets.read(self.directory, 'unsaved-key'), {})

    def test_ssh_inspection_authenticates_without_remote_commands(self):
        for dns in ([('', '', '', '', ('203.0.113.10', 443))], OSError('DNS unavailable')):
            with self.subTest(dns=dns):
                client = MagicMock()
                with patch.object(server_connection, 'connect', return_value=client), patch.object(
                        server_connection.socket, 'getaddrinfo') as resolve:
                    if isinstance(dns, Exception):
                        resolve.side_effect = dns
                    else:
                        resolve.return_value = dns
                    result = server_connection.inspect(entry(), self.directory)
                self.assertIn('尚未验证', result['message'])
                self.assertEqual(result['dnsReady'], not isinstance(dns, Exception))
                self.assertEqual(client.mock_calls, [call.close()])
                self.assertNotIn('configured', result)

    def test_removed_deploy_action_rejected_before_ssh_connection(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        value = desktop.snapshot()
        value['preferences'].update(codexHome=str(self.directory), connections=[entry(sshSudo=True)])
        desktop.save(value)
        self.assertNotIn('sshSudo', desktop.preferences()['connections'][0])
        with patch.object(server_connection, 'connect') as connect:
            with self.assertRaisesRegex(ValueError, '手动完成'):
                desktop.server_setup({'id': 'test-server', 'action': 'deploy', 'sudoPassword': 'fixture'})
            connect.assert_not_called()

    def test_server_export_is_manual_reference_without_agent_deployment_order(self):
        files = access.deployment(entry(port=8787))
        self.assertIn('http://127.0.0.1:18787', files['部署说明.md'])
        self.assertIn('管理员权限的命令必须由用户自行执行', files['部署说明.md'])
        self.assertIn('Administrative commands must be executed by the user', files['DEPLOYMENT_EN.md'])
        self.assertNotIn('## 给部署 Agent', files['部署说明.md'])
        self.assertNotIn('## Instructions for deployment Agents', files['DEPLOYMENT_EN.md'])
        self.assertIn('检查 SSH 登录', files['部署说明.md'])

    def test_disabled_saved_ssh_can_be_checked_and_prompt_is_read_only(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        value = desktop.snapshot()
        value['preferences'].update(codexHome=str(self.directory), connections=[entry(enabled=False)])
        desktop.save(value)
        with patch.object(server_connection, 'inspect', return_value={'message': 'ok'}) as inspect:
            self.assertEqual(desktop.server_setup({'id': 'test-server', 'action': 'inspect'})['message'], 'ok')
            inspect.assert_called_once()
        for language in ('zh', 'en'):
            with patch.object(server_connection, 'connect') as connect:
                result = desktop.server_setup({'id': 'test-server', 'action': 'diagnostics', 'language': language})
                connect.assert_not_called()
            self.assertIn('127.0.0.1:18787', result['prompt'])
            self.assertIn('sudo', result['prompt'])
            self.assertNotIn('correct-secret', result['prompt'])
        with self.assertRaises(ValueError):
            access.select_connection(desktop.preferences(), {'id': 'test-server'})

    def test_saved_false_and_bundled_path_discovery(self):
        write_json(self.directory/'notifications.json', {'addressEnabled': False})
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as bundle:
            binary = Path(bundle)/'cloudflared'/('cloudflared.exe' if os.name == 'nt' else 'cloudflared')
            binary.parent.mkdir()
            binary.write_text('fixture')
            with patch('sys._MEIPASS', bundle, create=True):
                self.assertEqual(desktop.preferences()['cloudflared'], str(binary))
        self.assertFalse(desktop.snapshot()['notifications']['addressEnabled'])


@unittest.skipUnless(paramiko, 'Optional SSH build dependencies unavailable')
class SSHIntegrationTests(unittest.TestCase):
    def setUp(self):
        ConnectionSetupTests.setUp(self)
        self.key = paramiko.RSAKey.generate(2048)
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(10)
        self.listener.settimeout(.2)
        self.done = threading.Event()
        self.transports = []
        self.authenticated = threading.Event()
        self.forwarded = threading.Event()
        self.authentication_count = 0
        owner = self

        class Server(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                owner.authentication_count += 1
                if username == 'test' and password == 'correct-secret':
                    owner.authenticated.set()
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def check_auth_publickey(self, username, key):
                if username == 'test' and key == owner.key:
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return 'password,publickey'

            def check_port_forward_request(self, address, port):
                if address != '127.0.0.1':
                    return False
                owner.forwarded.set()
                return port

        def handle(sock):
            transport = paramiko.Transport(sock)
            self.transports.append(transport)
            try:
                transport.add_server_key(self.key)
                transport.start_server(server=Server())
                self.done.wait(10)
            except (EOFError, OSError, paramiko.SSHException):
                pass
            finally:
                transport.close()

        def accept():
            while not self.done.is_set():
                try:
                    sock, _ = self.listener.accept()
                    threading.Thread(target=handle, args=(sock,), daemon=True).start()
                except socket.timeout:
                    pass
                except OSError:
                    break
        self.thread = threading.Thread(target=accept, daemon=True)
        self.thread.start()
        self.row = entry(sshPort=self.listener.getsockname()[1])
        self.addCleanup(self.close_server)

    def close_server(self):
        self.done.set()
        self.listener.close()
        for transport in self.transports:
            transport.close()
        self.thread.join(timeout=2)

    def test_host_confirmation_password_auth_and_key_change(self):
        with self.assertRaisesRegex(ValueError, '主机指纹'):
            server_connection.connect(self.row, self.directory, {'password': 'correct-secret'})
        probe = server_connection.host_probe(self.row, self.directory)
        self.assertFalse(probe['trusted'])
        self.assertEqual(self.authentication_count, 0)
        with self.assertRaisesRegex(ValueError, '变化'):
            server_connection.trust(self.row, self.directory, 'SHA256:wrong')
        server_connection.trust(self.row, self.directory, probe['fingerprint'])
        client = server_connection.connect(self.row, self.directory, {'password': 'correct-secret'})
        self.assertTrue(client.get_transport().is_authenticated())
        client.close()
        self.key = paramiko.RSAKey.generate(2048)
        count = self.authentication_count
        with self.assertRaisesRegex(ValueError, '指纹'):
            server_connection.connect(self.row, self.directory, {'password': 'correct-secret'})
        self.assertEqual(self.authentication_count, count)

    def test_saved_password_signin_then_mixed_gateway_start(self):
        desktop = Desktop(self.directory)
        desktop.status = lambda: {'running': False}
        value = desktop.snapshot()
        value['preferences'].update(codexHome=str(self.directory), lan=False,
                                    cloudflared=__import__('sys').executable,
                                    connections=[self.row, entry(id='cf', accessMode='cloudflare', publicUrl='https://other.example.com')])
        desktop.save(value)
        probe = desktop.server_setup({'id': self.row['id'], 'action': 'host'})
        desktop.server_setup({'id': self.row['id'], 'action': 'trust', 'fingerprint': probe['fingerprint']})
        values = {}
        vault = MagicMock()
        vault.get_password.side_effect = lambda service, key: values.get(key)
        vault.set_password.side_effect = lambda service, key, value: values.update({key: value})
        with patch.object(connection_secrets, 'backend', return_value=vault):
            desktop.connection_credentials({'id': self.row['id'], 'secrets': {'password': 'correct-secret'}})
            desktop.connection_credentials({'id': 'cf', 'secrets': {'tunnelToken': 'fixture-token'}})
            # SSH authentication is real; only public DNS uses a fixture.
            resolve = socket.getaddrinfo
            with patch.object(server_connection.socket, 'getaddrinfo', side_effect=lambda host, *args, **kwargs: resolve(host, *args, **kwargs) if host == '127.0.0.1' else []):
                result = desktop.server_setup({'id': self.row['id'], 'action': 'inspect'})
            self.assertIn('SSH 登录通过', result['message'])
            self.assertTrue(self.authenticated.is_set())
            with patch('bridge.desktop.socket.socket'), patch('bridge.desktop.subprocess.Popen') as spawn:
                self.assertTrue(desktop.start()['started'])
            sent = json.loads(spawn.return_value.stdin.write.call_args.args[0])
            self.assertEqual(sent[self.row['id']]['password'], 'correct-secret')
            self.assertEqual(sent['cf']['tunnelToken'], 'fixture-token')

    def test_encrypted_private_key_authentication(self):
        probe = server_connection.host_probe(self.row, self.directory)
        server_connection.trust(self.row, self.directory, probe['fingerprint'])
        key = self.directory/'private-key'
        self.key.write_private_key_file(str(key), password='key-passphrase')
        row = {**self.row, 'sshAuth': 'key', 'sshKeyPath': str(key)}
        client = server_connection.connect(row, self.directory, {'passphrase': 'key-passphrase'})
        self.assertTrue(client.get_transport().is_authenticated())
        client.close()

    def test_loopback_reverse_forward_moves_real_bytes_and_closes(self):
        probe = server_connection.host_probe(self.row, self.directory)
        server_connection.trust(self.row, self.directory, probe['fingerprint'])
        echo = socket.socket()
        echo.bind(('127.0.0.1', 0))
        echo.listen(1)
        self.addCleanup(echo.close)
        def echo_once():
            conn, _ = echo.accept()
            with conn:
                conn.sendall(conn.recv(100))
        threading.Thread(target=echo_once, daemon=True).start()
        forward = server_connection.ManagedForward(self.row, echo.getsockname()[1], self.directory, {'password': 'correct-secret'})
        self.addCleanup(forward.close)
        forward.start()
        self.assertTrue(self.forwarded.wait(5))
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            if read_json(self.directory/'ssh-status-test-server.json', {}).get('state') == 'connected':
                break
            time.sleep(.01)
        else:
            self.fail('Client did not finish registering the forwarding handler')
        transport = next(t for t in reversed(self.transports) if t.is_authenticated())
        channel = transport.open_forwarded_tcpip_channel(('127.0.0.1', 9000), ('127.0.0.1', 18787))
        channel.settimeout(5)
        channel.sendall(b'hello-through-ssh')
        self.assertEqual(channel.recv(100), b'hello-through-ssh')
        channel.close()
        forward.close()
        self.assertFalse(forward.thread.is_alive())
        self.assertNotIn('correct-secret', ''.join(p.read_text(encoding='utf-8') for p in self.directory.glob('*.json')))

class NamedTunnelTests(unittest.TestCase):
    def test_token_stays_out_of_argv_and_status_and_stop_closes_process(self):
        from bridge.named_tunnel import NamedTunnel
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            tunnel = NamedTunnel('/fixture/cloudflared', entry(accessMode='cloudflare'), directory, 'secret-fixture-token')
            process = MagicMock()
            process.stdout = iter(['Registered tunnel connection\n', 'ERR secret-fixture-token\n'])
            # Use a closing iterable like real Popen stdout.
            class Output:
                def __iter__(self):
                    yield 'Registered tunnel connection\n'
                    yield 'ERR secret-fixture-token\n'
                    tunnel.stopped.set()
                def close(self):
                    pass
            process.stdout = Output()
            with patch('bridge.named_tunnel.subprocess.Popen', return_value=process) as spawn:
                tunnel._run()
            call = spawn.call_args
            self.assertNotIn('secret-fixture-token', ' '.join(call.args[0]))
            self.assertEqual(call.kwargs['env']['TUNNEL_TOKEN'], 'secret-fixture-token')
            self.assertNotIn('secret-fixture-token', ''.join(p.read_text(encoding='utf-8') for p in Path(directory).iterdir()))
            self.assertEqual(read_json(Path(directory)/'ssh-status-test-server.json', {})['state'], 'stopped')
