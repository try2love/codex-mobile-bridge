import copy
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.deepseek.accounts import DeepSeekAccounts, GRANT_KEY, API_REF, ORIGIN, _document, _request, _balance


class DeepSeekAccountsTests(unittest.TestCase):
    def test_new_api_snapshot_can_be_edited_and_applied_without_secret_response(self):
        self.write()
        before = self.path.read_bytes()
        result = self.accounts.save_api({'name': 'API', 'apiKey': 'private-new-key'})
        identifier = next(row['id'] for row in result['accounts'] if row.get('saved'))
        self.assertEqual(self.path.read_bytes(), before)
        editor = self.accounts.edit(identifier)
        self.assertEqual(editor['api'], {'hasKey': True})
        self.assertNotIn('private-new-key', json.dumps([editor, result]))
        self.accounts.save_api({'id': identifier, 'name': 'Updated', 'apiKey': ''})
        token = self.accounts.restore(identifier)
        self.assertEqual(_document(self.path.read_bytes())['refs'][API_REF], 'private-new-key')
        self.assertIn(GRANT_KEY, _document(self.path.read_bytes())['records'])
        self.accounts.rollback(token)
        self.assertEqual(self.path.read_bytes(), before)

    def test_saved_account_rename_and_delete_preserve_current_native_login(self):
        self.write()
        identifier = self.identifier()
        before = self.path.read_bytes()
        result = self.accounts.rename(identifier, 'Renamed')
        self.assertEqual(next(row for row in result['accounts'] if row['id'] == identifier)['name'], 'Renamed')
        result = self.accounts.remove(identifier)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn(identifier, [row['id'] for row in result['accounts']])
        self.assertIn('current-official', result['activeIds'])
        self.assertFalse((self.directory/(identifier+'.json')).exists())

    def test_api_editor_does_not_silently_accept_an_unconfigured_custom_route(self):
        for value in ({'name': 'API', 'apiKey': 'key', 'baseUrl': 'https://custom.test'},
                      {'name': 'API', 'apiKey': ''}, {'name': 'API', 'apiKey': 'key\nheader'},
                      {'name': '', 'apiKey': 'key'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.accounts.save_api(value)
        self.assertFalse((self.directory/'index.json').exists())

    def test_failed_api_or_delete_index_save_restores_private_snapshot(self):
        result = self.accounts.save_api({'name': 'API', 'apiKey': 'private-key'})
        identifier = result['accounts'][0]['id']
        path = self.directory/(identifier+'.json')
        before, index = path.read_bytes(), copy.deepcopy(self.accounts.index)
        with patch.object(self.accounts, '_save', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.accounts.save_api({'id': identifier, 'name': 'Other', 'apiKey': 'other-key'})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(self.accounts.index, index)
            with self.assertRaises(OSError):
                self.accounts.remove(identifier)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.accounts.index, index)

    def setUp(self):
        root = Path(__file__).resolve().parents[1]/'.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'dsh'
        self.home.mkdir()
        self.path = self.home/'.credentials.yaml'
        self.directory = self.root/'accounts'
        self.accounts = DeepSeekAccounts(self.directory, self.home)
        self.env = patch.dict(os.environ, {API_REF: ''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, token='private-one', api='private-api', extra=True):
        value = {'version': 1, 'refs': {API_REF: api}, 'records': {
            GRANT_KEY: {'kind': 'grant', 'payload': {'version': 1, 'issuer': ORIGIN, 'token': token}},
            'deepseek-account-platform/device': {'kind': 'grant', 'payload': {'id': 'device-stays'}},
        }}
        if extra:
            value['refs']['OTHER_API_KEY'] = 'other-key-stays'
            value['records']['unrelated/default'] = {'kind': 'grant', 'payload': {'decimal': 1.25, 'array': [1, 2]}}
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)
        return value

    def identifier(self, name='First', kind=''):
        return self.accounts.import_current(name, kind)['activeId']

    def test_discovery_is_read_only_and_empty_home_is_supported(self):
        self.assertEqual(self.accounts.public()['accounts'], [])
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(ValueError, '未找到'):
            self.accounts.import_current()

    def test_import_deduplicates_private_snapshot_and_never_exposes_secrets(self):
        self.write()
        before = self.path.read_bytes()
        identifier = self.identifier()
        result = self.accounts.import_current('Renamed')
        self.assertEqual(result['activeId'], identifier)
        self.assertEqual(len([row for row in result['accounts'] if row['saved']]), 1)
        self.assertEqual(result['accounts'][0]['name'], 'Renamed')
        self.assertEqual(result['activeIds'], [identifier, 'current-api'])
        self.assertEqual(self.path.read_bytes(), before)
        for secret in ('private-one', 'private-api', 'other-key-stays', 'fingerprint', 'credential'):
            self.assertNotIn(secret, json.dumps(result))
        if os.name != 'nt':
            self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
            for file in self.directory.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        reloaded = DeepSeekAccounts(self.directory, self.home)
        self.assertEqual(reloaded.public()['activeId'], identifier)

    def test_restore_changes_only_one_route_and_rollback_restores_exact_bytes(self):
        first = self.write()
        identifier = self.identifier()
        second = self.write('private-two', 'api-two')
        second['records']['new/provider'] = {'kind': 'grant', 'payload': {'new': True}}
        before = json.dumps(second, indent=3).encode()
        self.path.write_bytes(before)
        original_public = self.accounts.public()
        self.assertEqual(original_public['activeIds'], ['current-official', 'current-api'])
        token = self.accounts.restore(identifier)
        after = json.loads(self.path.read_bytes())
        expected = copy.deepcopy(second)
        expected['records'][GRANT_KEY] = first['records'][GRANT_KEY]
        self.assertEqual(after, expected)
        self.assertEqual(self.accounts.public()['activeId'], identifier)
        self.accounts.rollback(token)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.accounts.public()['activeIds'], ['current-official', 'current-api'])
        self.assertFalse((self.directory/('rollback-'+token+'.json')).exists())

    def test_yaml_restore_preserves_unrelated_comments_and_scalar_bytes(self):
        self.write(extra=False)
        identifier = self.identifier()
        original = ('# saved by native app\nversion: 1\nrefs:\n  OTHER_API_KEY: "untouched"\n'
                    'records:\n  unrelated/default:\n    kind: grant\n    payload:\n      exact: 1.2300\n'
                    '  deepseek-account-platform/default:\n    kind: grant\n    payload:\n'
                    '      version: 1\n      token: private-two\n      issuer: https://platform.deepseek.com\n'
                    '  # Device comment\n  deepseek-account-platform/device:\n    kind: grant\n    payload:\n      id: same-device\n')
        self.path.write_text(original)
        token = self.accounts.restore(identifier)
        text = self.path.read_text()
        self.assertIn('      exact: 1.2300\n', text)
        self.assertIn('  # Device comment\n  deepseek-account-platform/device:', text)
        self.assertIn('  OTHER_API_KEY: "untouched"', text)
        self.assertEqual(_document(self.path.read_bytes())['records'][GRANT_KEY]['payload']['token'], 'private-one')
        self.accounts.rollback(token)
        self.assertEqual(self.path.read_text(), original)

    def test_routes_coexist_and_api_restore_does_not_remove_official_account(self):
        self.write()
        official = self.identifier('Official')
        api = self.identifier('API', 'api')
        result = self.accounts.public()
        self.assertCountEqual(result['activeIds'], [official, api])
        self.assertEqual(result['activeId'], api)
        self.write('different-official', 'different-api')
        token = self.accounts.restore(api)
        data = json.loads(self.path.read_bytes())
        self.assertEqual(data['records'][GRANT_KEY]['payload']['token'], 'different-official')
        self.assertEqual(data['refs'][API_REF], 'private-api')
        self.assertEqual(self.accounts.public()['activeIds'], ['current-official', api])
        self.accounts.commit(token)
        self.assertFalse((self.directory/('rollback-'+token+'.json')).exists())

    def test_restore_missing_document_and_rollback_removes_new_document(self):
        self.write()
        identifier = self.identifier()
        self.path.unlink()
        token = self.accounts.restore(identifier)
        self.assertTrue(self.path.is_file())
        self.accounts.rollback(token)
        self.assertFalse(self.path.exists())

    def test_writer_contention_and_external_changes_do_not_overwrite_credentials(self):
        self.write()
        identifier = self.identifier()
        self.write('second')
        before = self.path.read_bytes()
        lock = self.path.with_name('.credentials.yaml.lock')
        lock.write_text('12345\n')
        with self.assertRaisesRegex(ValueError, '正在写入'):
            self.accounts.restore(identifier)
        self.assertEqual(self.path.read_bytes(), before)
        lock.unlink()
        token = self.accounts.restore(identifier)
        self.write('external')
        external = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '外部修改'):
            self.accounts.rollback(token)
        self.assertEqual(self.path.read_bytes(), external)

    def test_invalid_or_nonprivate_native_credentials_fail_without_mutation(self):
        self.write()
        identifier = self.identifier()
        for text in ('version: 1\nrecords: !!js private-secret\n', '{"version":2}',
                     'version: 1\nrefs:\n  X: first\n  X: second\n'):
            self.path.write_text(text)
            with self.assertRaisesRegex(ValueError, '格式无法识别'):
                self.accounts.restore(identifier)
            self.assertEqual(self.path.read_text(), text)
        if os.name != 'nt':
            self.write()
            self.path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, '权限'):
                self.accounts.import_current()

    def test_symlink_is_rejected(self):
        self.write()
        identifier = self.identifier()
        target = self.home/'target'
        self.path.rename(target)
        try:
            self.path.symlink_to(target)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symbolic-link privilege unavailable')
            raise
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.accounts.restore(identifier)
        self.path.unlink()
        target.rename(self.path)

    def test_wrong_issuer_is_rejected(self):
        data = self.write()
        data['records'][GRANT_KEY]['payload']['issuer'] = 'https://untrusted.example'
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, '凭据无效'):
            self.accounts.import_current()

    def test_api_environment_override_is_not_claimed_as_stored_active_account(self):
        self.write()
        api = self.identifier('API', 'api')
        with patch.dict(os.environ, {API_REF: 'launch-environment'}):
            self.assertNotIn(api, self.accounts.public()['activeIds'])
            with self.assertRaisesRegex(ValueError, '启动环境'):
                self.accounts.restore(api)

    def test_quota_uses_precise_currency_amounts_caches_and_manual_refresh_resets_timer(self):
        self.write()
        identifier = self.identifier()
        summary = {'normal_wallets': [{'currency': 'CNY', 'balance': '10.10'}, {'currency': 'USD', 'balance': '3.00'}],
                   'bonus_wallets': [{'currency': 'CNY', 'balance': '0.20'}]}
        profile = {'email': 'owner@example.test', 'id': 'opaque-user', 'token': 'never-return-this'}
        with patch('bridge.clients.deepseek.accounts._request', side_effect=lambda path, token: summary if 'summary' in path else profile) as request, \
                patch('bridge.clients.deepseek.accounts.time.monotonic', return_value=100) as clock, \
                patch('bridge.clients.deepseek.accounts.time.time', return_value=1780000000.25):
            result = self.accounts.details(identifier)
            usage = result['accounts'][0]['usage']
            self.assertEqual(usage['status'], 'ready')
            self.assertEqual(usage['checkedAt'], 1780000000.25)
            self.assertEqual(usage['limits'], [])
            self.assertEqual(usage['balance']['wallets'][0], {'currency': 'CNY', 'remaining': '10.30', 'toppedUp': '10.10', 'granted': '0.20'})
            self.assertNotIn('never-return-this', json.dumps(result))
            self.assertEqual(request.call_count, 2)
            clock.return_value = 399
            self.accounts.details(identifier)
            self.assertEqual(request.call_count, 2)
            self.accounts.details(identifier, refresh=True)
            self.assertEqual(request.call_count, 4)
            clock.return_value = 500
            self.accounts.details(identifier)
            self.assertEqual(request.call_count, 4)
            clock.return_value = 700
            self.accounts.details(identifier)
            self.assertEqual(request.call_count, 6)

    def test_quota_failure_is_not_zero_balance_and_api_never_sends_key(self):
        self.write()
        official = self.identifier()
        with patch('bridge.clients.deepseek.accounts._request', side_effect=ValueError('Harness 余额查询失败，请稍后重试')):
            usage = self.accounts.details(official)['accounts'][0]['usage']
            self.assertEqual(usage['status'], 'error')
            self.assertNotIn('balance', usage)
        api = self.identifier('API', 'api')
        with patch('bridge.clients.deepseek.accounts._request') as request:
            result = self.accounts.details(api)
            row = next(item for item in result['accounts'] if item['id'] == api)
            self.assertEqual(row['usage']['status'], 'unsupported')
            request.assert_not_called()

    def test_profile_failure_retains_valid_balance_and_invalid_amount_fails(self):
        self.write()
        identifier = self.identifier()
        with patch('bridge.clients.deepseek.accounts._request', side_effect=[{'normal_wallets': [], 'bonus_wallets': []}, ValueError('unavailable')]):
            usage = self.accounts.details(identifier)['accounts'][0]['usage']
            self.assertEqual(usage['status'], 'ready')
        for bad in ('NaN', 'Infinity', '1e9999999'):
            with self.assertRaises(ValueError):
                _balance({'normal_wallets': [{'currency': 'CNY', 'balance': bad}], 'bonus_wallets': []})

    def test_network_errors_are_sanitized_and_only_fixed_paths_are_allowed(self):
        with self.assertRaisesRegex(ValueError, '不受支持'):
            _request('https://untrusted.example', 'private-token')
        error = urllib.error.HTTPError(ORIGIN+'/private-token', 401, 'private-token', {}, None)
        with patch('bridge.clients.deepseek.accounts.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(ValueError, '登录已失效') as context:
                _request('/api/v0/users/get_user_summary', 'private-token')
            self.assertNotIn('private-token', str(context.exception))
            request = opener.return_value.open.call_args[0][0]
            self.assertEqual(request.full_url, ORIGIN+'/api/v0/users/get_user_summary')
            self.assertEqual(request.get_header('X-dsh-auth-token'), 'private-token')

    def test_legacy_iso_cached_timestamp_is_exposed_as_epoch_without_refetch(self):
        self.write()
        summary = {'normal_wallets': [], 'bonus_wallets': []}
        with patch('bridge.clients.deepseek.accounts._request', side_effect=[summary, {}]) as request:
            self.accounts.details('current-official')
            for timestamp in ('2026-10-09T00:00:00+00:00', '2026-10-09T00:00:00Z'):
                self.accounts.cache['current-official'][1]['checkedAt'] = timestamp
                usage = self.accounts.details('current-official')['accounts'][0]['usage']
                self.assertEqual(usage['checkedAt'], datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp())
            self.assertEqual(request.call_count, 2)

    def test_account_https_uses_portable_trust_and_keeps_redirects_disabled(self):
        context, handler = object(), object()
        response = Mock()
        response.read.return_value = b'{"code":0,"data":{"biz_code":0,"biz_data":{}}}'
        with patch('bridge.clients.deepseek.accounts.client_context', return_value=context) as trust, \
                patch('bridge.clients.deepseek.accounts.urllib.request.HTTPSHandler', return_value=handler) as https, \
                patch('bridge.clients.deepseek.accounts.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value = response
            self.assertEqual(_request('/api/v0/users/get_user_summary', 'fixture-token'), {})
            trust.assert_called_once_with()
            https.assert_called_once_with(context=context)
            self.assertIs(opener.call_args.args[1], handler)
            redirect = opener.call_args.args[0]
            self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, 'https://untrusted.example'))

    def test_unsaved_native_account_has_read_only_row_and_quota_without_import(self):
        self.write()
        before = self.path.read_bytes()
        current = self.accounts.public()
        self.assertEqual(current['activeIds'], ['current-official', 'current-api'])
        self.assertTrue(all(row['saved'] is False for row in current['accounts']))
        summary = {'normal_wallets': [{'currency': 'CNY', 'balance': '4.00'}], 'bonus_wallets': []}
        with patch('bridge.clients.deepseek.accounts._request', side_effect=[summary, {'email': 'current@example.test'}]):
            row = self.accounts.details('current-official')['accounts'][0]
            self.assertEqual(row['usage']['balance']['wallets'][0]['remaining'], '4.00')
            self.assertEqual(row['email'], 'current@example.test')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.directory.exists())
        with self.assertRaisesRegex(ValueError, '尚未保存'):
            self.accounts.restore('current-official')

    def test_native_account_change_cannot_reuse_old_current_quota_or_identity(self):
        self.write('first-token')
        def query(path, token):
            if 'summary' in path:
                return {'normal_wallets': [{'currency': 'CNY', 'balance': '4.00' if token == 'first-token' else '9.00'}], 'bonus_wallets': []}
            return {'email': 'first@example.test' if token == 'first-token' else 'second@example.test'}
        with patch('bridge.clients.deepseek.accounts._request', side_effect=query) as request:
            row = self.accounts.details('current-official')['accounts'][0]
            self.assertEqual(row['email'], 'first@example.test')
            self.accounts.details('current-official')
            self.assertEqual(request.call_count, 2)
            self.write('second-token')
            new_current = self.accounts.public()['accounts'][0]
            self.assertNotIn('usage', new_current)
            self.assertNotIn('email', new_current)
            row = self.accounts.details('current-official')['accounts'][0]
            self.assertEqual(request.call_count, 4)
            self.assertEqual(row['email'], 'second@example.test')
            self.assertEqual(row['usage']['balance']['wallets'][0]['remaining'], '9.00')
        self.assertFalse(self.directory.exists())


if __name__ == '__main__':
    unittest.main()
