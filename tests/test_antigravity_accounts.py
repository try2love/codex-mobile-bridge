import json
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.clients.antigravity.accounts import AntigravityAccounts, TOKEN_FILES
from bridge.clients.antigravity.credentials import NativeCredentials
from bridge.platforms import antigravity as native


ROOT = Path(__file__).resolve().parents[1]


def token(email, secret='fixture-token'):
    return json.dumps({'token': {'access_token': secret, 'refresh_token': 'refresh-'+secret},
                       'auth_method': 'consumer', 'id_token_email': email, 'writer_id': 'native', 'future_field': {'keep': True}})


class AntigravityAccountTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root/'home'
        self.home.mkdir()
        self.addCleanup(mock.patch.stopall)
        mock.patch('pathlib.Path.home', return_value=self.home).start()
        self.stopped = mock.patch('bridge.clients.antigravity.accounts.require_stopped').start()
        self.secret = token('first@example.invalid')
        self.vault = mock.Mock()
        self.vault.read.side_effect = lambda: self.secret
        self.vault.write.side_effect = lambda value: setattr(self, 'secret', value)
        mock.patch('bridge.clients.antigravity.accounts.NativeCredentials', return_value=self.vault).start()
        self.native = {'accounts': [], 'activeIds': [], 'message': 'APP：原生服务未连接'}
        self.store = AntigravityAccounts(self.root/'saved', self.home, lambda: self.native)

    def file(self, name, value):
        path = self.home/'.gemini'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def test_public_never_opens_vault_or_invents_active_login(self):
        self.store.import_current('第一账号')
        self.vault.reset_mock()
        self.stopped.reset_mock()
        value = self.store.public()
        self.assertEqual(value['activeIds'], [])
        self.assertEqual(value['accounts'][0]['name'], '第一账号')
        self.assertNotIn('fixture-token', json.dumps(value))
        self.vault.read.assert_not_called()
        self.stopped.assert_not_called()
        self.assertTrue(value['canSwitch'])
        self.assertIn('APP、CLI、IDE', value['switchConfirmation'])

    def test_import_and_restore_preserve_raw_fields_and_missing_files(self):
        first = self.secret
        path = self.file(TOKEN_FILES[0], first+'\n')
        plugin = self.file('antigravity/plugin-secrets.json', 'unrelated')
        identifier = self.store.import_current('first')['accounts'][0]['id']
        self.secret = token('second@example.invalid', 'other')
        path.write_text(self.secret)
        extra = self.file(TOKEN_FILES[1], self.secret)
        result = self.store.restore(identifier)
        self.assertEqual(self.secret, first)
        self.assertEqual(path.read_text(), first+'\n')
        self.assertFalse(extra.exists())
        self.assertEqual(plugin.read_text(), 'unrelated')
        self.assertEqual(result['activeIds'], [])
        self.assertIn('尚未验证登录', result['message'])

    def test_running_clients_and_custom_home_block_before_credentials(self):
        self.stopped.side_effect = ValueError('请正常退出 APP、CLI、IDE')
        with self.assertRaisesRegex(ValueError, '正常退出'):
            self.store.import_current('first')
        self.vault.read.assert_not_called()
        other = AntigravityAccounts(self.root/'other', self.root/'custom', lambda: self.native)
        self.assertFalse(other.public()['canImport'])
        with self.assertRaisesRegex(ValueError, '默认'):
            other.import_current('first')
        self.vault.read.assert_not_called()

    def test_disagreeing_native_fallback_identities_are_not_saved(self):
        self.file(TOKEN_FILES[0], token('different@example.invalid'))
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.store.import_current('first')
        self.assertFalse((self.root/'saved/index.json').exists())

    def test_failed_restore_rolls_back_only_unchanged_written_values(self):
        path = self.file(TOKEN_FILES[0], self.secret)
        identifier = self.store.import_current('first')['accounts'][0]['id']
        before = token('second@example.invalid', 'second')
        self.secret = before
        path.write_text(before)
        def refuse(value):
            if value != before:
                raise OSError('vault refused')
            self.secret = value
        self.vault.write.side_effect = refuse
        with self.assertRaisesRegex(ValueError, '未完成'):
            self.store.restore(identifier)
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self.secret, before)

    def test_symlink_and_saved_id_traversal_are_rejected(self):
        target = self.root/'outside'
        target.mkdir()
        (self.home/'.gemini').symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.store.import_current('first')
        self.vault.read.assert_not_called()
        for identifier in ('../outside', '/tmp/account', '', None):
            with self.assertRaises(ValueError):
                self.store.restore(identifier)

    def test_native_verified_email_controls_current_identity(self):
        identifier = self.store.import_current('first')['accounts'][0]['id']
        self.native['accounts'] = [{'id': 'cli:current', 'email': 'first@example.invalid',
                                    'name': 'CLI · first', 'surface': 'cli', 'saved': False}]
        result = self.store.public()
        self.assertIn(identifier, result['activeIds'])
        self.assertEqual(len(result['accounts']), 1)
        self.assertEqual(result['accounts'][0]['connectedSurfaces'], ['cli'])

    def test_external_write_during_restore_is_retained_with_backup(self):
        path = self.file(TOKEN_FILES[0], self.secret)
        identifier = self.store.import_current('first')['accounts'][0]['id']
        before = token('second@example.invalid', 'second')
        external = token('third@example.invalid', 'third')
        self.secret = before
        path.write_text(before)
        def interfere(value):
            path.write_text(external)
            raise OSError('vault refused')
        self.vault.write.side_effect = interfere
        with self.assertRaisesRegex(ValueError, '未覆盖外部变化'):
            self.store.restore(identifier)
        self.assertEqual(path.read_text(), external)
        self.assertEqual(self.secret, before)
        self.assertEqual(len(list(self.store.directory.glob('rollback-*.json'))), 1)

    def test_unconfirmed_rollback_retains_recovery_copy(self):
        identifier = self.store.import_current('first')['accounts'][0]['id']
        before = token('second@example.invalid', 'second')
        self.secret = before
        def unreliable(value):
            if value != before:
                self.secret = value
                raise OSError('write acknowledgement lost')
            # Do not acknowledge a restoration solely because this call returns.
        self.vault.write.side_effect = unreliable
        with self.assertRaisesRegex(ValueError, '恢复备份已保留'):
            self.store.restore(identifier)
        self.assertEqual(len(list(self.store.directory.glob('rollback-*.json'))), 1)

    def test_wif_and_gcp_do_not_enter_default_consumer_snapshot(self):
        for change in ({'auth_method': 'gcp'}, {'wif_provider': 'fixture-provider'}, {'auth_method': None}):
            data = json.loads(token('first@example.invalid'))
            data.update(change)
            self.secret = json.dumps(data)
            with self.assertRaisesRegex(ValueError, '默认 Google 登录'):
                self.store.import_current('first')
        self.assertFalse((self.store.directory/'index.json').exists())

    def test_running_linux_executable_unlinked_by_updater_is_not_stopped(self):
        proc = self.root/'proc'
        process = proc/'123'
        process.mkdir(parents=True)
        (process/'exe').symlink_to('/opt/antigravity/agy (deleted)')
        (process/'stat').write_text('123 (agy) S 1 0')
        def path(value):
            return proc if value == '/proc' else Path(value)
        with mock.patch.object(native.sys, 'platform', 'linux'), mock.patch.object(native, 'Path', side_effect=path):
            self.assertEqual(native.login_processes(), [123])

    def test_linux_non_dumpable_unrelated_process_is_not_an_antigravity_writer(self):
        proc = self.root/'proc'
        process = proc/'123'
        process.mkdir(parents=True)
        (process/'comm').write_text('(sd-pam)\n')
        def path(value):
            return proc if value == '/proc' else Path(value)
        with mock.patch.object(native.sys, 'platform', 'linux'), mock.patch.object(native, 'Path', side_effect=path), \
                mock.patch.object(native.os, 'readlink', side_effect=PermissionError('not dumpable')):
            self.assertEqual(native.login_processes(), [])
            (process/'comm').write_text('agy\n')
            with self.assertRaisesRegex(ValueError, '无法核验'):
                native.login_processes()

    def test_macos_native_codec_accepts_current_legacy_and_plain_records(self):
        credentials = NativeCredentials.__new__(NativeCredentials)
        credentials.vault = mock.Mock()
        content = token('fixture@example.invalid').encode()
        with mock.patch('bridge.clients.antigravity.credentials.sys.platform', 'darwin'):
            for raw in (b'go-keyring-base64:'+base64.b64encode(content),
                        b'go-keyring-encoded:'+content.hex().encode(), content):
                credentials.vault.read.return_value = raw
                self.assertEqual(credentials.read(), content.decode())
            credentials.write(content.decode())
        credentials.vault.write.assert_called_once_with('gemini', 'antigravity', b'go-keyring-base64:'+base64.b64encode(content))

    def test_windows_and_linux_native_codec_preserves_utf8_bytes(self):
        credentials = NativeCredentials.__new__(NativeCredentials)
        credentials.vault = mock.Mock()
        content = '原生 UTF-8 fixture'
        for platform in ('win32', 'linux'):
            with mock.patch('bridge.clients.antigravity.credentials.sys.platform', platform):
                credentials.vault.read.return_value = content.encode()
                self.assertEqual(credentials.read(), content)
                credentials.write(content)
                credentials.vault.write.assert_called_with('gemini', 'antigravity', content.encode())


if __name__ == '__main__':
    unittest.main()
