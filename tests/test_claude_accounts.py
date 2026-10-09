import json
import base64
import hashlib
import importlib.util
import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.integrations.claude_accounts import ClaudeAccounts, CONFIG, _cookies, _decrypt_cookie, _NoRedirect, _web_json

ROOT = Path(__file__).resolve().parents[1]
MODULE = 'bridge.integrations.claude_accounts.'


class ClaudeAccountSnapshots(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'APPDATA': str(self.root), 'LOCALAPPDATA': str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.home = self.root/'Claude'
        self.home.mkdir()
        self.threep = self.root/'Claude-3p'
        self.accounts = ClaudeAccounts(self.root/'accounts', self.home)
        self.assertEqual(self.accounts.home, self.home)
        self.assertEqual(self.accounts.threep, self.threep)
        self.login('first')

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def login(self, identifier):
        self.write(self.home/CONFIG, {'deploymentMode': '1p', 'nativeSetting': identifier})
        self.write(self.home/'config.json', {'oauth:tokenCache': 'TOKEN-'+identifier, 'theme': identifier})
        with closing(sqlite3.connect(self.home/'Cookies')) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS cookies (host_key TEXT,name TEXT,value TEXT,encrypted_value BLOB,expires_utc INTEGER)')
            db.execute('DELETE FROM cookies')
            db.executemany('INSERT INTO cookies VALUES (?,?,?,?,?)', [
                ('.claude.ai', 'sessionKey', 'COOKIE-'+identifier, b'', 0),
                ('.claude.ai', 'lastActiveOrg', 'ORG-'+identifier, b'', 0),
                ('.unrelated.test', 'sessionKey', 'NEVER-SEND', b'', 0)])
        self.write(self.home/'Local Storage'/'state.json', {'login': identifier})
        self.write(self.home/'local-sessions'/'conversation.json', {'history': 'DO-NOT-COPY'})

    def save(self, name=''):
        result = self.accounts.import_current(name)
        return result['activeId']

    def gateway(self, secret='API-SECRET'):
        config_id = str(uuid.uuid4())
        self.write(self.home/CONFIG, {'deploymentMode': '3p', 'nativeSetting': 'preserve'})
        self.write(self.threep/CONFIG, {'deploymentMode': '3p', 'thirdPartySetting': 'preserve'})
        self.write(self.threep/'configLibrary'/'_meta.json', {'appliedId': config_id, 'entries': [{'id': config_id, 'name': 'Provider'}], 'nativeMeta': True})
        self.write(self.threep/'configLibrary'/(config_id+'.json'), {'inferenceProvider': 'gateway',
                   'inferenceGatewayBaseUrl': 'https://provider.test/v1?private=query',
                   'inferenceGatewayApiKey': secret, 'inferenceGatewayAuthScheme': 'bearer',
                   'inferenceModels': [{'name': 'actual-upstream-model'}]})
        return config_id

    def test_import_keeps_native_profile_and_excludes_conversations_and_cli(self):
        before = (self.home/'Cookies').read_bytes()
        identifier = self.save('Work')
        snapshot = self.root/'accounts'/'profiles'/identifier
        self.assertEqual((self.home/'Cookies').read_bytes(), before)
        self.assertFalse((snapshot/'local-sessions').exists())
        self.assertEqual(json.loads((snapshot/'auth.json').read_text(encoding='utf-8')), {'oauth:tokenCache': 'TOKEN-first'})
        self.assertFalse((snapshot/'config.json').exists())
        self.assertTrue(self.accounts.public()['accounts'][0]['active'])
        self.assertEqual(self.save(), identifier, 'saving the same login must update its existing slot')
        if os.name != 'nt':
            self.assertEqual((snapshot/'Cookies').stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.root/'accounts').stat().st_mode & 0o777, 0o700)

    def test_cookie_read_closes_database_before_profile_move(self):
        opened = []
        connect = sqlite3.connect

        def capture(*args, **kwargs):
            db = connect(*args, **kwargs)
            opened.append(db)
            return db

        try:
            with patch(MODULE+'sqlite3.connect', side_effect=capture):
                self.assertEqual(len(_cookies(self.home)), 2)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute('SELECT 1')
            moved = self.home.with_name('Claude-moved')
            self.home.rename(moved)
            moved.rename(self.home)
        finally:
            for db in opened:
                db.close()

    def test_manual_native_login_change_clears_saved_current_identity(self):
        first = self.save()
        self.login('second')
        self.assertEqual(self.accounts.public()['activeId'], 'current')
        self.assertFalse(self.accounts.public()['current']['saved'])
        second = self.save('Personal')
        self.assertNotEqual(first, second)
        self.assertEqual(self.accounts.public()['accounts'][0]['id'], second)

    def test_restore_is_reversible_and_preserves_current_native_settings_and_history(self):
        first = self.save('First')
        self.login('second')
        second = self.save('Second')
        self.write(self.home/'Session Storage'/'only-second', {'private': True})
        token = self.accounts.restore(first)
        self.assertEqual(self.accounts.public()['activeId'], first)
        self.assertFalse((self.home/'Session Storage').exists(), 'missing target auth store must remove the previous login store')
        self.assertEqual(json.loads((self.home/'config.json').read_text(encoding='utf-8')), {'oauth:tokenCache': 'TOKEN-first', 'theme': 'second'})
        self.assertEqual(json.loads((self.home/'local-sessions'/'conversation.json').read_text(encoding='utf-8')), {'history': 'DO-NOT-COPY'})
        self.accounts.rollback(token)
        self.assertEqual(self.accounts.public()['activeId'], second)
        self.assertTrue((self.home/'Session Storage'/'only-second').is_file())
        self.assertFalse(self.threep.exists() and (self.threep/CONFIG).exists(), 'rollback restores absent config files too')
        self.accounts.commit(token)
        self.assertFalse((self.root/'accounts'/'transactions'/token).exists())

    def test_partial_native_write_failure_rolls_back_before_returning_error(self):
        first = self.save()
        self.login('second')
        second = self.save()
        from bridge.integrations.claude_accounts import private_json
        failed = False
        def fail_once(path, value):
            nonlocal failed
            if Path(path) == self.home/CONFIG and not failed:
                failed = True
                raise OSError('PRIVATE-NATIVE-ERROR')
            return private_json(path, value)
        with patch(MODULE+'private_json', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, '已恢复原接入') as caught:
                self.accounts.restore(first)
        self.assertNotIn('PRIVATE-NATIVE-ERROR', str(caught.exception))
        self.assertEqual(self.accounts.public()['activeId'], second)

    def test_api_snapshot_uses_desktop_gateway_and_keeps_other_provider_entries(self):
        official = self.save('Official')
        config_id = self.gateway()
        api = self.save('Upstream')
        result = self.accounts.public()
        self.assertEqual(result['current']['kind'], 'api')
        self.assertEqual(result['current']['baseUrl'], 'https://provider.test')
        self.accounts.commit(self.accounts.restore(official))
        other_id = str(uuid.uuid4())
        library = self.threep/'configLibrary'
        self.write(library/(other_id+'.json'), {'untouched': True})
        self.write(library/'_meta.json', {'entries': [{'id': other_id, 'name': 'Other'}], 'nativeMeta': True})
        token = self.accounts.restore(api)
        meta = json.loads((library/'_meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['appliedId'], config_id)
        self.assertTrue(meta['nativeMeta'])
        self.assertEqual(len(meta['entries']), 2)
        self.assertEqual(json.loads((self.home/CONFIG).read_text(encoding='utf-8'))['deploymentMode'], '3p')
        self.assertEqual(json.loads((self.threep/CONFIG).read_text(encoding='utf-8'))['deploymentMode'], '3p')
        self.assertEqual(self.accounts.public()['activeId'], api)
        self.accounts.rollback(token)
        self.assertEqual(self.accounts.public()['activeId'], official)

    def test_public_data_never_contains_native_secret_or_profile_path(self):
        self.save('Official')
        self.gateway()
        self.save('Gateway')
        text = json.dumps(self.accounts.public())
        for private in ('COOKIE-', 'TOKEN-', 'API-SECRET', 'private=query', str(self.home), 'fingerprint'):
            self.assertNotIn(private, text)

    def test_invalid_ids_and_linked_profile_data_are_rejected(self):
        identifier = self.save()
        for action in (self.accounts.restore, self.accounts.details, self.accounts.rollback):
            with self.assertRaises(ValueError):
                action('../outside')
        outside = self.root/'outside'
        outside.write_text('untouched')
        try:
            (self.home/'Preferences').symlink_to(outside)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symbolic-link privilege unavailable')
            raise
        with self.assertRaises(ValueError):
            self.accounts.restore(identifier)
        self.assertEqual(outside.read_text(encoding='utf-8'), 'untouched')

    def test_stale_or_missing_login_is_not_imported(self):
        (self.home/'Cookies').unlink()
        with self.assertRaisesRegex(ValueError, '已登录'):
            self.save()
        self.assertEqual(self.accounts.public()['accounts'], [])

    def test_usage_queries_once_then_caches_and_manual_refresh_restarts_timer(self):
        identifier = self.save()
        data = {'five_hour': {'utilization': 25, 'resets_at': '2026-10-09T12:00:00Z'}, 'seven_day': {'utilization': 50}}
        with patch(MODULE+'_web_json', return_value=data) as fetch:
            first = self.accounts.details(identifier)
            again = self.accounts.details(identifier)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(first, again)
            usage = first['current']['usage']
            self.assertEqual(usage['status'], 'ready')
            self.assertIsInstance(usage['checkedAt'], (int, float))
            self.assertAlmostEqual(usage['checkedAt'], time.time(), delta=5)
            self.assertEqual(usage['limits'][0]['windows'][0]['resetsAt'], 1791547200)
            self.assertEqual(usage['limits'][0]['windows'][0]['remainingPercent'], 75)
            self.assertEqual(fetch.call_args.args[0], '/api/organizations/ORG-first/usage')
            self.assertNotIn('NEVER-SEND', json.dumps(fetch.call_args.args))
            self.accounts.details(identifier, refresh=True)
            self.accounts.details(identifier)
            self.assertEqual(fetch.call_count, 2)

    def test_expired_usage_cache_fetches_again_and_errors_are_cached_and_redacted(self):
        identifier = self.save()
        with patch(MODULE+'_web_json', side_effect=RuntimeError('PRIVATE-SERVER-BODY COOKIE-first')) as fetch:
            result = self.accounts.details(identifier)
            self.accounts.details(identifier)
            self.assertEqual(fetch.call_count, 1)
            usage = result['current']['usage']
            self.assertEqual(usage['status'], 'error')
            self.assertTrue(usage['checkedAt'])
            self.assertNotIn('PRIVATE', json.dumps(result))
            index = json.loads((self.root/'accounts'/'index.json').read_text(encoding='utf-8'))
            index['accounts'][0]['usage']['checkedAt'] = '2000-01-01T00:00:00+00:00'
            self.write(self.root/'accounts'/'index.json', index)
            self.accounts.details(identifier)
            self.assertEqual(fetch.call_count, 2)

    def test_api_has_no_subscription_quota_request(self):
        self.gateway()
        identifier = self.save()
        with patch(MODULE+'_web_json') as fetch:
            result = self.accounts.details(identifier, refresh=True)
        fetch.assert_not_called()
        self.assertEqual(result['current']['usage']['status'], 'unsupported')

    def test_current_alias_resolves_saved_account_and_detected_threep_profile(self):
        identifier = self.save()
        with patch(MODULE+'_web_json', return_value={'five_hour': {'utilization': 30}}):
            result = self.accounts.details('current')
        self.assertEqual(result['current']['id'], identifier)
        self.assertEqual(result['current']['usage']['status'], 'ready')
        self.gateway()
        api = self.save('Gateway')
        detected = ClaudeAccounts(self.root/'accounts', self.threep)
        self.assertEqual(detected.home, self.home)
        self.assertEqual(detected.public()['activeId'], api)

    def test_unknown_usage_payload_never_implies_full_remaining_quota(self):
        identifier = self.save()
        with patch(MODULE+'_web_json', return_value={'unrecognized': 'private'}):
            result = self.accounts.details(identifier)
        self.assertEqual(result['current']['usage']['status'], 'error')
        self.assertEqual(result['current']['usage']['limits'], [])

    def test_unsaved_current_can_query_quota_without_enrollment_or_a_profile_snapshot(self):
        self.assertEqual(self.accounts.public()['activeId'], 'current')
        with patch(MODULE+'_web_json', return_value={'five_hour': {'utilization': 10}}) as fetch:
            result = self.accounts.details('current')
            self.accounts.details('current')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result['current']['usage']['status'], 'ready')
        self.assertFalse(result['current']['saved'])
        self.assertFalse(result['current']['canSwitch'])
        self.assertFalse((self.root/'accounts'/'index.json').exists())
        self.assertFalse((self.root/'accounts'/'profiles').exists())
        with self.assertRaises(ValueError):
            self.accounts.restore('current')

    def test_unsaved_current_cache_is_bound_to_native_identity_across_manager_recreation(self):
        with patch(MODULE+'_web_json', return_value={'five_hour': {'utilization': 10}}) as fetch:
            self.accounts.details('current')
            recreated = ClaudeAccounts(self.root/'accounts', self.home)
            recreated.details('current')
            self.assertEqual(fetch.call_count, 1)
            self.login('second')
            self.assertNotIn('usage', recreated.public()['current'])
            recreated.details('current')
            self.assertEqual(fetch.call_count, 2)
        (self.home/'Cookies').unlink()
        result = self.accounts.details('current')
        self.assertIsNone(result['current'])
        self.assertTrue(result['notice'])

    def test_legacy_iso_cache_stays_fresh_but_public_timestamps_are_epoch_seconds(self):
        identifier = self.save()
        old_usage = {'status': 'ready', 'checkedAt': datetime.now(timezone.utc).isoformat(),
                     'limits': [{'name': '5h', 'windows': [{'remainingPercent': 80, 'resetsAt': '2026-10-09T12:00:00Z'}]}]}
        index = json.loads((self.root/'accounts'/'index.json').read_text(encoding='utf-8'))
        index['accounts'][0]['usage'] = old_usage
        self.write(self.root/'accounts'/'index.json', index)
        with patch(MODULE+'_web_json') as fetch:
            usage = self.accounts.details(identifier)['current']['usage']
        fetch.assert_not_called()
        self.assertIsInstance(usage['checkedAt'], (int, float))
        self.assertEqual(usage['limits'][0]['windows'][0]['resetsAt'], 1791547200)
        self.login('unsaved')
        _, fingerprint, _ = self.accounts._current()
        self.write(self.root/'accounts'/'current-usage.json', {'kind': 'official', 'fingerprint': fingerprint, 'usage': old_usage})
        with patch(MODULE+'_web_json') as fetch:
            usage = self.accounts.details('current')['current']['usage']
        fetch.assert_not_called()
        self.assertIsInstance(usage['checkedAt'], (int, float))
        self.assertEqual(usage['limits'][0]['windows'][0]['resetsAt'], 1791547200)

    def test_native_identity_change_during_query_never_displays_previous_accounts_balance(self):
        def query(*args):
            self.login('second')
            return {'five_hour': {'utilization': 10}}
        with patch(MODULE+'_web_json', side_effect=query):
            result = self.accounts.details('current')
        self.assertNotIn('usage', result['current'])

    def test_current_usage_limits_schema_preserves_scopes_and_skips_missing_values(self):
        data = {'limits': [
            {'kind': 'session', 'group': 'session', 'percent': 42.4, 'resets_at': '2026-09-01T12:00:00Z'},
            {'kind': 'weekly', 'percent': 18, 'resets_at': 1788264000},
            {'group': 'weekly', 'percent': '9', 'scope': {'model': {'display_name': 'Claude Sonnet'}}},
            {'group': 'weekly'}, {'group': 'weekly', 'percent': float('nan')},
        ]}
        with patch(MODULE+'_web_json', return_value=data):
            result = self.accounts.details('current')
        limits = result['current']['usage']['limits']
        self.assertEqual(len(limits), 3)
        self.assertEqual(limits[0]['windows'][0]['remainingPercent'], 57.6)
        self.assertEqual(limits[2]['name'], 'Claude Sonnet · 7d')
        self.assertEqual(limits[2]['windows'][0]['remainingPercent'], 91)

    def test_expired_login_never_sends_a_quota_request(self):
        identifier = self.save()
        with closing(sqlite3.connect(self.home/'Cookies')) as db, db:
            db.execute('UPDATE cookies SET expires_utc=1')
        with patch(MODULE+'_web_json') as fetch:
            result = self.accounts.details(identifier)
        fetch.assert_not_called()
        self.assertEqual(result['current']['usage']['status'], 'error')

    def test_quota_redirect_handler_never_forwards_native_cookies(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, 'moved', {}, 'https://elsewhere.test'))

    def test_quota_https_uses_bundled_ca_context_and_rejects_redirects(self):
        context, handler = object(), object()
        response = Mock()
        response.read.return_value = b'{"five_hour":{"utilization":25}}'
        with patch(MODULE+'client_context', return_value=context) as trust, \
                patch(MODULE+'request.HTTPSHandler', return_value=handler) as https, \
                patch(MODULE+'request.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value = response
            result = _web_json('/api/organizations/test/usage', {'sessionKey': 'fixture'}, 'test')
        trust.assert_called_once_with()
        https.assert_called_once_with(context=context)
        self.assertIsInstance(build.call_args.args[0], _NoRedirect)
        self.assertIs(build.call_args.args[1], handler)
        self.assertEqual(result['five_hour']['utilization'], 25)

    def test_quota_certificate_configuration_failure_does_not_fall_back_to_unverified_https(self):
        with patch(MODULE+'client_context', side_effect=OSError('PRIVATE-CA-PATH')), patch(MODULE+'request.build_opener') as build:
            with self.assertRaisesRegex(ValueError, '稍后重试') as caught:
                _web_json('/api/organizations/test/usage', {'sessionKey': 'fixture'}, 'test')
        build.assert_not_called()
        self.assertNotIn('PRIVATE', str(caught.exception))

    @unittest.skipUnless(importlib.util.find_spec('cryptography') and importlib.util.find_spec('keyring'), 'bundled desktop crypto dependencies required')
    def test_mac_cookie_decryption_is_in_memory_and_bound_to_the_claude_vault_entry(self):
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        password, host = 'fixture-vault-password', '.claude.ai'
        key = hashlib.pbkdf2_hmac('sha1', password.encode(), b'saltysalt', 1003, dklen=16)
        plain = hashlib.sha256(host.encode()).digest()+b'fixture-session'
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plain)+padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(b' '*16)).encryptor()
        encrypted = b'v10'+encryptor.update(padded)+encryptor.finalize()
        with patch(MODULE+'sys.platform', 'darwin'), patch('keyring.backends.macOS.Keyring') as vault:
            vault.return_value.get_password.side_effect = [None, password]
            self.assertEqual(_decrypt_cookie(host, encrypted, self.home), 'fixture-session')
            self.assertEqual(vault.return_value.get_password.call_args_list[0].args, ('Claude Safe Storage', 'Claude'))
            self.assertEqual(vault.return_value.get_password.call_args_list[1].args, ('Claude Safe Storage', 'Claude Key'))

    @unittest.skipUnless(importlib.util.find_spec('cryptography'), 'bundled desktop crypto dependencies required')
    def test_windows_cookie_envelope_uses_dpapi_key_and_authenticates_gcm_tag(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key, nonce, host = b'K'*32, b'N'*12, '.claude.ai'
        self.write(self.home/'Local State', {'os_crypt': {'encrypted_key': base64.b64encode(b'DPAPIwrapped-fixture-key').decode()}})
        plain = hashlib.sha256(host.encode()).digest()+b'fixture-windows-session'
        encrypted = b'v10'+nonce+AESGCM(key).encrypt(nonce, plain, None)
        with patch(MODULE+'sys.platform', 'win32'), patch(MODULE+'_unprotect', return_value=key) as native:
            self.assertEqual(_decrypt_cookie(host, encrypted, self.home), 'fixture-windows-session')
            native.assert_called_once_with(b'wrapped-fixture-key')
            with self.assertRaises(Exception):
                _decrypt_cookie(host, encrypted[:-1]+bytes([encrypted[-1]^1]), self.home)
            with self.assertRaisesRegex(ValueError, '暂不支持'):
                _decrypt_cookie(host, b'v20unsupported', self.home)


if __name__ == '__main__':
    unittest.main()
