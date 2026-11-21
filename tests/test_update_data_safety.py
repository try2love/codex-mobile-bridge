"""Isolated version-transition and live-data preservation fixtures."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bridge.features.updates import gateway as updater


class UpdateDataSafetyTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / '.tmp'
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data, self.target, self.staged = (self.root / name for name in ('data', 'app', 'staged'))
        for path in (self.data, self.target, self.staged):
            path.mkdir()
        self.platform = patch.object(updater.sys, 'platform', 'darwin')
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.write(self.target / 'Contents/Resources/update-version.json', {'version': '2.2.2-preview.2'})
        self.write(self.data / 'config.json', {'auth': {'mode': 'password', 'username': 'fixture',
                                                     'salt': 'fixture-salt', 'hash': 'fixture-hash', 'iterations': 600000}})
        self.write(self.data / 'desktop.json', {'port': 8788})
        self.auth = {'identity': 'fixture-identity', 'cookieName': 'fixture-cookie', 'policy': {},
                     'sessions': {'fixture-trusted': {'expires': 0, 'trusted': True}}}
        self.write(self.data / 'auth-sessions.json', self.auth)
        self.account_id = 'a' * 32
        self.write(self.data / 'accounts/index.json', {'accounts': [{'id': self.account_id, 'kind': 'api'}],
                                                     'activeId': self.account_id})
        self.write(self.data / ('accounts/' + self.account_id + '/api.json'), {'key': 'fixture-private-key'})
        self.write(self.data / 'client-instances/entries.json', {'version': 1, 'entries': []})
        self.payload = {'target': str(self.target), 'version': '2.2.1',
                        'transition': {'intent': 'return-stable', 'from': '2.2.2-preview.2',
                                       'to': '2.2.1', 'dataFormat': updater.DATA_FORMAT}}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def backup(self):
        return updater.backup_configuration(self.data, self.payload)

    def test_exact_reviewed_return_creates_private_verified_backup_without_touching_live_data(self):
        opaque = self.data / 'desktop-sessions/claude-accounts/profiles/opaque/Local Storage/00001.ldb'
        opaque.parent.mkdir(parents=True)
        opaque.write_bytes(b'opaque native storage must remain untouched')
        tools = self.data / 'tools/run'
        tools.parent.mkdir()
        tools.write_text('not update data')
        before = {path.relative_to(self.data).as_posix(): path.read_bytes()
                  for path in self.data.rglob('*') if path.is_file()}
        result = self.backup()
        backup = Path(result['backup'])
        manifest = json.loads((backup / 'backup.json').read_text())
        self.assertEqual(result['files'], 6)
        self.assertEqual(manifest['transition'], self.payload['transition'])
        for name, item in manifest['files'].items():
            raw = (backup / name).read_bytes()
            self.assertEqual(raw, before[name])
            self.assertEqual(item, {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        for name, raw in before.items():
            self.assertEqual((self.data / name).read_bytes(), raw)
        self.assertFalse((backup / opaque.relative_to(self.data)).exists())
        self.assertFalse((backup / 'tools').exists())
        self.assertFalse((self.data / 'desktop-update.log').exists())
        if os.name != 'nt':
            for path in [backup, *backup.rglob('*')]:
                self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_backup_always_captures_current_accounts_and_pairing_without_restoring_an_older_copy(self):
        old = Path(self.backup()['backup'])
        self.write(self.data / ('accounts/' + self.account_id + '/api.json'), {'key': 'new-fixture-key'})
        self.auth['sessions']['new-trusted-device'] = {'expires': 0, 'trusted': True}
        self.write(self.data / 'auth-sessions.json', self.auth)
        current = Path(self.backup()['backup'])
        key_path = 'accounts/' + self.account_id + '/api.json'
        self.assertEqual(json.loads((old / key_path).read_text())['key'], 'fixture-private-key')
        self.assertEqual(json.loads((current / key_path).read_text())['key'], 'new-fixture-key')
        self.assertEqual(json.loads((self.data / key_path).read_text())['key'], 'new-fixture-key')
        self.assertEqual(json.loads((self.data / 'auth-sessions.json').read_text()), self.auth)

    def test_unknown_or_mismatched_transitions_are_rejected_before_a_backup(self):
        for change in ({'from': '2.2.3-preview.1'}, {'to': '2.2.0'}, {'intent': 'update'},
                       {'dataFormat': 'bridge-data-v2'}, {'from': '2.2.1'}):
            with self.subTest(change=change), patch.dict(self.payload['transition'], change):
                with self.assertRaises(ValueError):
                    self.backup()
        self.assertFalse((self.data / 'update-backups').exists())

    def test_unrecognised_local_format_is_rejected(self):
        self.write(self.target / 'Contents/Resources/update-version.json',
                   {'version': '2.2.2-preview.2', 'dataFormat': 'bridge-data-v2'})
        with self.assertRaisesRegex(ValueError, '资料格式'):
            self.backup()

    def test_return_stable_also_allows_a_newer_stable_but_never_preview_or_equal_targets(self):
        with patch.dict(self.payload, version='2.2.2'), patch.dict(self.payload['transition'], to='2.2.2'):
            self.assertEqual(updater.validate_transition(self.target, self.payload), self.payload['transition'])
        for version in ('2.2.2-preview.2', '2.2.2-preview.3'):
            with patch.dict(self.payload, version=version), patch.dict(self.payload['transition'], to=version):
                with self.assertRaises(ValueError):
                    updater.validate_transition(self.target, self.payload)

    def test_read_only_checks_reject_incompatible_registry_auth_accounts_and_unfinished_switch(self):
        cases = [('config.json', []), ('config.json', {'auth': {'mode': 'future'}}), ('desktop.json', []),
                 ('client-instances/entries.json', {'version': 2, 'entries': []}),
                 ('client-instances/entries.json', {'version': 1, 'entries': [{'id': 'future'}]}),
                 ('codex-entries/entries.json', {'version': 1, 'entries': [{'id': 'future'}]}),
                 ('auth-sessions.json', {'sessions': {}}),
                 ('accounts/index.json', {'accounts': [{'id': self.account_id, 'kind': 'future'}]}),
                 ('accounts/switch.json', {'phase': 'applying'})]
        for name, value in cases:
            path = self.data / name
            before = path.read_bytes() if path.exists() else None
            try:
                self.write(path, value)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.backup()
                self.assertEqual(json.loads(path.read_text()), value)
            finally:
                path.write_bytes(before) if before is not None else path.unlink()
        self.assertFalse((self.data / 'update-backups').exists())

    def test_malformed_config_does_not_expose_credentials_in_error(self):
        path = self.data / 'desktop.json'
        path.write_text('{fixture-private-key')
        with self.assertRaises(ValueError) as caught:
            self.backup()
        self.assertNotIn('fixture-private-key', str(caught.exception))

    def test_selected_configuration_symlinks_are_rejected_without_reading_outside_data(self):
        external = self.root / 'external.json'
        self.write(external, {'key': 'outside-private-key'})
        path = self.data / 'config.json'
        path.unlink()
        path.symlink_to(external)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.backup()
        self.assertFalse((self.data / 'update-backups').exists())

    def test_unreadable_legacy_settings_is_preserved_and_sidecar_is_backed_up(self):
        legacy = self.data/'desktop-sessions/settings.json'
        fallback = self.data/'desktop-sessions/settings.user.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"legacy": true}')
        fallback.write_text('{"enabled": {"codex": false}}')
        original = legacy.read_bytes()
        real_read = Path.read_bytes
        def deny_legacy(path):
            if path == legacy:
                raise PermissionError()
            return real_read(path)
        with patch.object(Path, 'read_bytes', autospec=True, side_effect=deny_legacy):
            result = self.backup()
        manifest = json.loads((Path(result['backup'])/'backup.json').read_text())
        self.assertNotIn('desktop-sessions/settings.json', manifest['files'])
        self.assertIn('desktop-sessions/settings.user.json', manifest['files'])
        self.assertEqual(json.loads((Path(result['backup'])/'desktop-sessions/settings.user.json').read_text()),
                         {'enabled': {'codex': False}})
        self.assertEqual(legacy.read_bytes(), original)
        self.assertTrue(legacy.exists())

    def test_return_stable_restores_sidecar_for_legacy_gateway_after_backing_it_up(self):
        legacy = self.data/'desktop-sessions/settings.json'
        fallback = self.data/'desktop-sessions/settings.user.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"legacy": true}')
        fallback.write_text('{"enabled": {"codex": false}, "discoveryRevision": 1}')
        original = legacy.read_bytes()
        recovered = fallback.read_bytes()
        real_read = Path.read_bytes

        def deny_legacy(path):
            if path == legacy:
                raise PermissionError()
            return real_read(path)

        with patch.object(Path, 'read_bytes', autospec=True, side_effect=deny_legacy):
            result = self.backup()
            updater.restore_legacy_client_settings(self.data, self.payload['transition'])
        backup = Path(result['backup'])
        manifest = json.loads((backup/'backup.json').read_text())
        self.assertNotIn('desktop-sessions/settings.json', manifest['files'])
        self.assertIn('desktop-sessions/settings.user.json', manifest['files'])
        self.assertEqual(json.loads(legacy.read_bytes()), json.loads(recovered))
        self.assertFalse(fallback.exists())
        self.assertNotEqual(legacy.read_bytes(), original)
        self.assertEqual(json.loads(legacy.read_text()), {'enabled': {'codex': False}, 'discoveryRevision': 1})
        legacy.write_text('{"enabled": {"codex": true}}')
        from bridge.clients.manager import settings_path, read_client_settings
        self.assertEqual(settings_path(legacy.parent), legacy)
        self.assertTrue(read_client_settings(settings_path(legacy.parent))['enabled']['codex'])

    def test_return_stable_blocks_when_unreadable_legacy_settings_have_no_sidecar(self):
        legacy = self.data/'desktop-sessions/settings.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"enabled": {"codex": true}}')
        real_read_bytes, real_open = Path.read_bytes, Path.open

        def deny_legacy_read(path):
            if path == legacy:
                raise PermissionError()
            return real_read_bytes(path)

        def deny_legacy_open(path, *args, **kwargs):
            if path == legacy and args and args[0] == 'rb':
                raise PermissionError()
            return real_open(path, *args, **kwargs)

        with patch.object(Path, 'read_bytes', autospec=True, side_effect=deny_legacy_read), \
                patch.object(Path, 'open', autospec=True, side_effect=deny_legacy_open):
            result = self.backup()
            with self.assertRaisesRegex(ValueError, '没有可供旧版使用的恢复副本'):
                updater.restore_legacy_client_settings(self.data, self.payload['transition'])
        update_backups = self.data/'update-backups'
        self.assertTrue((Path(result['backup'])/'backup.json').exists())
        self.assertTrue(legacy.exists())

    def test_empty_linked_account_directory_is_rejected_without_walking_it(self):
        outside = self.root / 'outside-accounts'
        outside.mkdir()
        (self.data / 'desktop-sessions').mkdir()
        (self.data / 'desktop-sessions/zcode-accounts').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.backup()
        self.assertFalse((self.data / 'update-backups').exists())

    def test_backup_survives_finished_app_transaction_cleanup(self):
        backup = Path(self.backup()['backup'])
        transaction = self.target.parent / '.cmb-update-completed'
        transaction.mkdir()
        self.write(transaction / 'plan.json', {'target': str(self.target)})
        self.write(transaction / 'result.json', {'state': 'updated'})
        updater.clean_transactions(self.target)
        self.assertFalse(transaction.exists())
        self.assertTrue((backup / 'backup.json').is_file())

    def test_linux_backup_uses_installed_resources_without_replacing_or_launching_the_app(self):
        with patch.object(updater.sys, 'platform', 'linux'):
            self.write(self.target / 'resources/update-version.json', {'version': '2.2.2-preview.2'})
            with patch.object(updater.subprocess, 'run') as run:
                result = self.backup()
                run.assert_not_called()
        self.assertTrue(Path(result['backup']).is_dir())

    def test_successful_and_failed_explicit_return_reuse_latest_live_data(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                app, stage = self.root / ('app-' + str(failure)), self.root / ('stage-' + str(failure))
                app.mkdir(); stage.mkdir()
                (app / 'version').write_text('preview')
                (stage / 'version').write_text('stable')
                self.write(app / 'Contents/Resources/update-version.json', {'version': '2.2.2-preview.2'})
                transaction = self.root / ('.cmb-update-' + str(failure))
                transaction.mkdir()
                plan = {**self.payload, 'target': str(app), 'staged': str(stage),
                        'backup': str(transaction / 'previous'), 'dataDir': str(self.data),
                        'parentPid': 123, 'token': 'fixture', 'runtime': {'running': False, 'instanceId': None}}
                self.write(transaction / 'plan.json', plan)
                before = (self.data / 'auth-sessions.json').read_bytes()
                class Desktop:
                    def status(self): return {'running': False, 'portOccupied': False, 'instanceId': None}
                class Child:
                    def poll(self): return None
                    def terminate(self): pass
                    def wait(self, timeout): pass
                def health(*args):
                    if failure:
                        raise RuntimeError('fixture startup failure')
                with patch.object(updater, 'preflight'), patch.object(updater, 'registry_version'):
                    result = updater.apply(transaction / 'plan.json', desktop=Desktop(), wait=lambda pid: None,
                                           launch=lambda *args, **kwargs: Child(), health=health)
                self.assertEqual(result['state'], 'failed' if failure else 'updated')
                self.assertEqual((app / 'version').read_text(), 'preview' if failure else 'stable')
                self.assertEqual((self.data / 'auth-sessions.json').read_bytes(), before)
                self.assertTrue(Path(result['configurationBackup']).is_dir())
                updater.clean_transactions(app)
                self.assertTrue(Path(result['configurationBackup']).is_dir())


if __name__ == '__main__':
    unittest.main()
