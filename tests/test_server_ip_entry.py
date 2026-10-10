"""Public IP defaults retain HTTPS, loopback forwarding and managed origins."""
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.features.network import access, server_connection
from bridge.app.desktop import Desktop

ROOT = Path(__file__).resolve().parents[1]


def entry(**changes):
    return {**access.DEFAULTS, 'id': 'server-ip', 'name': '', 'enabled': True,
            'accessMode': 'server', 'publicUrl': '', 'sshAuth': 'password',
            'sshHost': '93.184.216.34', 'sshUser': 'example', 'port': 8787, **changes}


class ServerIPTests(unittest.TestCase):
    def test_blank_uses_public_ip_https_without_dns_or_connection(self):
        for host, url in [('93.184.216.34', 'https://93.184.216.34'),
                          ('2606:4700:4700::1111', 'https://[2606:4700:4700::1111]')]:
            with self.subTest(host=host), patch.object(server_connection.socket, 'getaddrinfo') as dns, \
                    patch.object(server_connection, 'connect') as connect:
                self.assertEqual(access.validate(entry(sshHost=host))['publicUrl'], url)
                dns.assert_not_called()
                connect.assert_not_called()

    def test_alias_uses_config_hostname_without_dns(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            home = Path(folder)
            (home/'.ssh').mkdir()
            (home/'.ssh/config').write_text('Host fixture\n  HostName 93.184.216.34\n  User example\n')
            with patch.object(server_connection.Path, 'home', return_value=home):
                self.assertEqual(access.validate(entry(sshAuth='config', sshTarget='fixture'))['publicUrl'],
                                 'https://93.184.216.34')

    def test_blank_does_not_guess_domains_or_expose_private_and_special_addresses(self):
        for host in ['server.example.com', '127.0.0.1', '192.168.1.8', '10.0.0.1', '100.64.0.1',
                     '0.0.0.0', '224.0.0.1', '169.254.1.2', 'fe80::1', 'ff02::1', '2001:db8::1']:
            with self.subTest(host=host), self.assertRaises(ValueError) as caught:
                access.validate_connections({'connections': [entry(sshHost=host)]})
            self.assertEqual(caught.exception.validation, {'field': 'publicUrl', 'connectionId': 'server-ip'})
        self.assertEqual(access.validate(entry(publicUrl='https://bridge.example.com', sshHost='10.0.0.1'))['publicUrl'],
                         'https://bridge.example.com')
        with self.assertRaises(ValueError):
            access.validate(entry(publicUrl='http://93.184.216.34'))

    def test_save_resolves_once_and_keeps_origins_pairing_and_disable_in_sync(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            desktop = Desktop(folder)
            desktop.status = lambda: {'running': False, 'pid': None}
            value = desktop.snapshot()
            value['preferences'].update(codexHome=folder, connections=[entry()])
            result = desktop.save(value)
            self.assertEqual(result['preferences']['connections'][0]['publicUrl'], 'https://93.184.216.34')
            self.assertIn('https://93.184.216.34/', result['urls'])
            self.assertEqual(desktop.config()['origins'], ['https://93.184.216.34'])
            self.assertEqual(desktop.config()['publicUrl'], 'https://93.184.216.34')
            result['preferences']['connections'][0]['enabled'] = False
            desktop.save(result)
            self.assertEqual(desktop.config()['origins'], [])
            self.assertEqual(desktop.config()['publicUrl'], '')

    def test_ip_export_requires_matching_certificate_and_preserves_loopback(self):
        for host in ['93.184.216.34', '2606:4700:4700::1111']:
            files = access.deployment(entry(sshHost=host))
            self.assertIn('tls /etc/caddy/tls/fullchain.pem /etc/caddy/tls/privkey.pem', files['Caddyfile'])
            self.assertIn('reverse_proxy http://127.0.0.1:18787', files['Caddyfile'])
            self.assertIn('./tls:/etc/caddy/tls:ro', files['compose.yaml'])
            self.assertIn('Subject Alternative Name', files['部署说明.md'])
            self.assertIn('Subject Alternative Name', files['DEPLOYMENT_EN.md'])
            self.assertNotIn('Caddy 自动申请', files['部署说明.md'])
            self.assertNotIn('PRIVATE KEY', ''.join(files.values()))
        files = access.deployment(entry(publicUrl='https://bridge.example.com'))
        self.assertNotIn('tls /etc/caddy/tls', files['Caddyfile'])
        self.assertNotIn('./tls:', files['compose.yaml'])
        self.assertIn('Caddy 自动申请', files['部署说明.md'])

    def test_ip_probe_rejects_tls_error_without_http_fallback(self):
        with patch.object(access.http.client, 'HTTPSConnection') as https, \
                patch.object(access.http.client, 'HTTPConnection') as http:
            https.return_value.request.side_effect = ssl.SSLCertVerificationError('IP certificate mismatch')
            with self.assertRaises(ssl.SSLCertVerificationError):
                access.read_auth('https://93.184.216.34')
            self.assertTrue(https.call_args.kwargs['context'].check_hostname)
            self.assertEqual(https.call_args.kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
            https.return_value.close.assert_called_once()
            http.assert_not_called()
