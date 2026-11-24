"""Isolated version-transition and live-data preservation fixtures."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile

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

    def gateway_credentials(self):
        vault = self.data / '.credential-vault'
        vault.mkdir(mode=0o700)
        key = vault / 'key'
        key.write_bytes(b'k' * 32)
        cipher = vault / 'connections.enc'
        cipher.write_bytes(b'CMB1' + b'c' * 44)
        self.write(self.data / 'connection-vault.json', {'fixture-connection': 'gateway-v1'})
        (self.data / 'zcode-official-auth.enc').write_bytes(b'CMB1' + b'z' * 44)
        (self.data / 'zcode-official-auth.legacy.enc').write_bytes(b'v10-old-login')
        self.write(self.data / 'zcode-official.json', {'link': 'fixture'})
        return vault

    def test_gateway_credential_snapshot_includes_key_ciphertext_and_legacy_login(self):
        self.gateway_credentials()
        files = updater.configuration_snapshot(self.data)
        for name in ('.credential-vault/key', '.credential-vault/connections.enc',
                     'zcode-official-auth.enc', 'zcode-official-auth.legacy.enc', 'zcode-official.json'):
            self.assertEqual(files[name], (self.data / name).read_bytes())
        self.assertFalse(any(name.endswith('.lock') for name in files))

    def test_gateway_credentials_reject_legacy_downgrade_before_backup_or_mutation(self):
        self.gateway_credentials()
        before = {p.relative_to(self.data): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, '凭据'):
            self.backup()
        self.assertFalse((self.data / 'update-backups').exists())
        self.assertEqual(before, {p.relative_to(self.data): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})

    def test_gateway_credentials_reject_old_preview_switch_before_staging(self):
        self.gateway_credentials()
        self.write(self.target / 'Contents/Resources/update-version.json',
                   {'version': '2.2.2-preview.2', 'dataFormat': updater.DATA_FORMAT})
        self.payload.update(version='2.2.2-preview.3')
        self.payload['transition'].update(intent='switch-version', to='2.2.2-preview.3')
        app, worker, _ = updater.locations(self.target)
        for path in (app, worker):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture executable')
        self.payload['archive'] = str(self.root / 'not-opened.zip')
        with self.assertRaisesRegex(ValueError, '凭据'):
            updater.prepare(self.data, self.payload)
        self.assertFalse((self.data / 'update-backups').exists())
        self.assertEqual(app.read_bytes(), b'fixture executable')

    def test_legacy_keychain_records_remain_eligible_for_reviewed_return(self):
        self.write(self.data / 'connection-vault.json', {'fixture-connection': True})
        (self.data / 'zcode-official-auth.enc').write_bytes(b'v10-old-login')
        result = self.backup()
        self.assertTrue((Path(result['backup']) / 'zcode-official-auth.enc').exists())

    def test_upgrade_backs_up_gateway_credentials_with_private_permissions(self):
        self.gateway_credentials()
        self.payload.update(version='2.2.2-preview.4')
        self.payload['transition'].update(intent='update', to='2.2.2-preview.4')
        backup = Path(self.backup()['backup'])
        for name in ('.credential-vault/key', '.credential-vault/connections.enc', 'zcode-official-auth.enc'):
            self.assertEqual((backup / name).read_bytes(), (self.data / name).read_bytes())
            if os.name != 'nt':
                self.assertEqual((backup / name).stat().st_mode & 0o777, 0o600)

    def test_gateway_credential_snapshot_rejects_symlinked_vault(self):
        vault = self.root / 'external-vault'
        vault.mkdir()
        (vault / 'key').write_bytes(b'k' * 32)
        (self.data / '.credential-vault').symlink_to(vault, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            updater.configuration_snapshot(self.data)

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

    def test_manual_legacy_installer_restores_current_sidecar_after_durable_backup(self):
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                legacy = self.data/'desktop-sessions/settings.json'
                fallback = self.data/'desktop-sessions/settings.user.json'
                self.write(legacy, {'enabled': {'codex': True}, 'old': True})
                self.write(fallback, {'enabled': {'codex': False}, 'latest': True})
                expected = fallback.read_bytes()
                self.payload['manualInstall'] = True
                real_read = Path.read_bytes

                def read(path):
                    if unreadable and path == legacy:
                        raise PermissionError()
                    return real_read(path)

                with patch.object(Path, 'read_bytes', autospec=True, side_effect=read):
                    result = self.backup()
                backup = Path(result['backup'])
                self.assertEqual((backup/'desktop-sessions/settings.user.json').read_bytes(), expected)
                self.assertEqual(json.loads(legacy.read_bytes()), json.loads(expected))
                self.assertFalse(fallback.exists())
                manifest = json.loads((backup/'backup.json').read_text())
                self.assertEqual('desktop-sessions/settings.json' in manifest['files'], not unreadable)

    def test_manual_legacy_restore_failure_keeps_complete_private_backup(self):
        fallback = self.data/'desktop-sessions/settings.user.json'
        self.write(fallback, {'enabled': {'codex': False}})
        expected = fallback.read_bytes()
        self.payload['manualInstall'] = True
        with patch.object(updater, 'restore_legacy_client_settings', side_effect=ValueError('fixture restore failure')):
            with self.assertRaisesRegex(ValueError, 'fixture restore failure'):
                self.backup()
        backups = list((self.data/'update-backups').iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0]/'backup.json').is_file())
        self.assertEqual((backups[0]/'desktop-sessions/settings.user.json').read_bytes(), expected)
        self.assertEqual(fallback.read_bytes(), expected)
        if os.name != 'nt':
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
            self.assertEqual((backups[0]/'desktop-sessions/settings.user.json').stat().st_mode & 0o777, 0o600)

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

    def select_switch(self, source, target, source_format=updater.DATA_FORMAT):
        metadata = {'version': source, 'platform': 'darwin', 'arch': 'arm64'}
        if source_format is not None:
            metadata['dataFormat'] = source_format
        self.write(self.target / 'Contents/Resources/update-version.json', metadata)
        self.payload = {'target': str(self.target), 'version': target, 'platform': 'darwin', 'arch': 'arm64',
                        'parentPid': 123, 'token': 'fixture',
                        'transition': {'intent': 'switch-version', 'from': source, 'to': target,
                                       'dataFormat': updater.DATA_FORMAT}}

    def switch_archive(self, metadata=None, identity=None):
        old_app, old_worker, _ = updater.locations(self.target)
        for path in (old_app, old_worker):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic old executable')
        target = self.payload['version']
        metadata = ({'version': target, 'platform': 'darwin', 'arch': 'arm64', 'dataFormat': updater.DATA_FORMAT}
                    if metadata is None else metadata)
        identity = ({'CFBundleIdentifier': updater.APP_ID, 'CFBundleShortVersionString': target}
                    if identity is None else identity)
        archive = self.root / 'selected-version.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            prefix = updater.APP + '.app/'
            bundle.writestr(prefix + 'Contents/MacOS/' + updater.APP, b'synthetic selected executable')
            bundle.writestr(prefix + 'Contents/Resources/gateway/codex-mobile-gateway', b'synthetic runtime')
            bundle.writestr(prefix + 'Contents/Resources/update-version.json', json.dumps(metadata))
            bundle.writestr(prefix + 'Contents/Info.plist', plistlib.dumps(identity))
        self.payload.update(archive=str(archive), sha256=hashlib.sha256(archive.read_bytes()).hexdigest())

    def prepare_switch(self):
        with patch.object(updater, 'preflight'), patch.object(updater.subprocess, 'run') as run, \
                patch('bridge.app.desktop.Desktop.status', return_value={'running': False, 'portOccupied': False}):
            result = updater.prepare(self.data, self.payload)
        return result, run

    def test_switch_version_accepts_all_directions_and_same_version_with_exact_source_format(self):
        for source, target in (('2.2.2', '2.2.2-preview.3'), ('2.2.2-preview.3', '2.2.2'),
                               ('2.2.2-preview.3', '2.2.2-preview.1'), ('2.2.2-preview.3', '2.2.2-preview.3'),
                               ('2.2.1', '2.2.2-preview.3')):
            with self.subTest(source=source, target=target):
                self.select_switch(source, target, None if source == '2.2.1' else updater.DATA_FORMAT)
                self.assertEqual(updater.validate_transition(self.target, self.payload), self.payload['transition'])
                backup = Path(self.backup()['backup'])
                self.assertEqual(json.loads((backup / 'backup.json').read_text())['transition'], self.payload['transition'])

    def test_switch_version_rejects_illegal_identity_format_and_extra_transition_fields(self):
        self.select_switch('2.2.2-preview.3', '2.2.2-preview.1')
        before = updater.configuration_snapshot(self.data)
        for change in ({'from': '2.2.2-preview.2'}, {'to': '2.2.2-preview.2'}, {'to': 'bad'},
                       {'intent': 'unknown'}, {'dataFormat': 'bridge-data-v2'}, {'extra': True}):
            with self.subTest(change=change), patch.dict(self.payload['transition'], change):
                with self.assertRaises(ValueError):
                    self.backup()
        for source_format in (None, 'bridge-data-v2'):
            self.select_switch('2.2.2-preview.3', '2.2.2-preview.1', source_format)
            with self.subTest(source_format=source_format), self.assertRaisesRegex(ValueError, '资料格式'):
                self.backup()
        for source, target in (('bad', '2.2.2'), ('2.2.2-preview.3', 'bad')):
            self.select_switch(source, target)
            with self.subTest(source=source, target=target), self.assertRaisesRegex(ValueError, '版本格式'):
                self.backup()
        self.assertEqual(updater.configuration_snapshot(self.data), before)
        self.assertFalse((self.data / 'update-backups').exists())

    def test_manual_historical_target_gets_a_limited_source_backup_without_target_compatibility_claim(self):
        self.select_switch('2.2.2-preview.3', '1.4.0')
        self.payload['manualInstall'] = True
        before = updater.configuration_snapshot(self.data)
        result = self.backup()
        backup = Path(result['backup'])
        manifest = json.loads((backup / 'backup.json').read_text())
        self.assertEqual(manifest['transition'], self.payload['transition'])
        self.assertEqual(set(manifest['files']), set(before))
        self.assertEqual(updater.configuration_snapshot(self.data), before)
        self.assertFalse((self.data / 'desktop-update.log').exists())

    def test_switch_prepare_rejects_unknown_target_formats_before_touching_live_data(self):
        for target, target_format in (('2.2.2-preview.1', None), ('1.4.0', None),
                                      ('2.2.2-preview.1', 'bridge-data-v2'), ('2.2.1', 'bridge-data-v2')):
            with self.subTest(target=target, target_format=target_format):
                self.select_switch('2.2.2-preview.3', target)
                metadata = {'version': target, 'platform': 'darwin', 'arch': 'arm64'}
                if target_format is not None:
                    metadata['dataFormat'] = target_format
                self.switch_archive(metadata)
                self.payload['manualInstall'] = True  # A manual UI choice must never bypass automatic preparation checks.
                before = updater.configuration_snapshot(self.data)
                with self.assertRaisesRegex(ValueError, '版本或平台'):
                    self.prepare_switch()
                self.assertEqual(updater.configuration_snapshot(self.data), before)
                self.assertEqual(list(self.root.glob('.cmb-update-*')), [])
                self.assertFalse((self.data / 'update-backups').exists())

    def test_switch_prepare_checks_signed_bundle_identity_digest_platform_and_arch(self):
        self.select_switch('2.2.2-preview.3', '2.2.2-preview.1')
        for field, value in (('version', '2.2.2-preview.2'), ('platform', 'win32'), ('arch', 'x64')):
            metadata = {'version': self.payload['version'], 'platform': 'darwin', 'arch': 'arm64',
                        'dataFormat': updater.DATA_FORMAT, field: value}
            self.switch_archive(metadata)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '版本或平台'):
                self.prepare_switch()
        self.switch_archive(identity={'CFBundleIdentifier': 'wrong.identity', 'CFBundleShortVersionString': self.payload['version']})
        with self.assertRaisesRegex(ValueError, '应用标识或版本'):
            self.prepare_switch()
        self.switch_archive()
        with patch.object(updater.subprocess, 'run', side_effect=RuntimeError('signature rejected')), \
                patch.object(updater, 'preflight'), self.assertRaisesRegex(RuntimeError, 'signature rejected'):
            updater.prepare(self.data, self.payload)
        with patch.dict(self.payload, sha256='0' * 64), self.assertRaisesRegex(ValueError, '校验失败'):
            self.prepare_switch()
        self.assertEqual(list(self.root.glob('.cmb-update-*')), [])
        self.assertFalse((self.data / 'update-backups').exists())

    def test_switch_prepare_accepts_reviewed_legacy_221_without_format_and_declared_v1_targets(self):
        for target in ('2.2.1', '2.2.2-preview.1', '2.2.2-preview.3'):
            with self.subTest(target=target):
                self.select_switch('2.2.2-preview.3', target)
                metadata = {'version': target, 'platform': 'darwin', 'arch': 'arm64'}
                if target != '2.2.1':
                    metadata['dataFormat'] = updater.DATA_FORMAT
                self.switch_archive(metadata)
                result, run = self.prepare_switch()
                plan_file = Path(result['plan'])
                self.assertEqual(json.loads(plan_file.read_text())['transition'], self.payload['transition'])
                self.assertEqual(run.call_args_list[0].args[0][:4], ['/usr/bin/codesign', '--verify', '--deep', '--strict'])
                self.assertEqual(run.call_args_list[1].args[0][-1], '--help')
                self.assertFalse((self.data / 'update-backups').exists())
                self.write(plan_file.parent / 'result.json', {'state': 'updated'})
                updater.clean_transactions(self.target)

    def test_switch_version_preserves_account_operation_and_live_task_guards(self):
        self.select_switch('2.2.2-preview.3', '2.2.2-preview.3')
        for name, value in (('accounts/switch.json', {'phase': 'applying'}), ('accounts/rollback.json', {})):
            path = self.data / name
            self.write(path, value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.backup()
            path.unlink()
        self.switch_archive()
        with patch.object(updater, 'preflight', side_effect=ValueError('active task')) as check, \
                patch.object(updater.hashlib, 'sha256') as digest, self.assertRaisesRegex(ValueError, 'active task'):
            updater.prepare(self.data, self.payload)
        check.assert_called_once_with(self.target)
        digest.assert_not_called()
        self.assertEqual(list(self.root.glob('.cmb-update-*')), [])
        self.assertFalse((self.data / 'update-backups').exists())

    def test_switch_restores_sidecar_only_for_legacy_221_after_backup(self):
        legacy = self.data / 'desktop-sessions/settings.json'
        fallback = self.data / 'desktop-sessions/settings.user.json'
        for intent, target in (('switch-version', '2.2.1'), ('switch-version', '2.2.2-preview.1'),
                                ('switch-version', '2.2.2'), ('return-stable', '2.2.2')):
            with self.subTest(intent=intent, target=target):
                self.select_switch('2.2.2-preview.3', target)
                self.payload['transition']['intent'] = intent
                self.write(legacy, {'legacy': True})
                self.write(fallback, {'enabled': {'codex': False}})
                before = legacy.read_bytes(), fallback.read_bytes()
                backup = Path(self.backup()['backup'])
                restored = updater.restore_legacy_client_settings(self.data, self.payload['transition'])
                self.assertEqual((backup / 'desktop-sessions/settings.json').read_bytes(), before[0])
                self.assertEqual((backup / 'desktop-sessions/settings.user.json').read_bytes(), before[1])
                self.assertEqual(restored, target == '2.2.1')
                if target == '2.2.1':
                    self.assertEqual(json.loads(legacy.read_bytes()), {'enabled': {'codex': False}})
                    self.assertFalse(fallback.exists())
                else:
                    self.assertEqual((legacy.read_bytes(), fallback.read_bytes()), before)

    def test_switch_transactions_roundtrip_stable_preview_and_reinstall_using_latest_live_data(self):
        class Desktop:
            def status(self): return {'running': False, 'portOccupied': False, 'instanceId': None}
        class Child:
            def poll(self): return None
        backups = []
        for source, target in (('2.2.2', '2.2.2-preview.3'), ('2.2.2-preview.3', '2.2.2'),
                               ('2.2.2', '2.2.2-preview.3'), ('2.2.2-preview.3', '2.2.2-preview.1'),
                               ('2.2.2-preview.1', '2.2.2-preview.1')):
            with self.subTest(source=source, target=target):
                self.select_switch(source, target)
                self.switch_archive()
                prepared, _ = self.prepare_switch()
                # Changes made after prepare must be captured at the final handoff.
                self.write(self.data / ('accounts/' + self.account_id + '/api.json'), {'key': 'current-' + target})
                current = updater.configuration_snapshot(self.data)
                launches = []
                def launch(app, *args, **kwargs):
                    launches.append(json.loads((app / 'Contents/Resources/update-version.json').read_text())['version'])
                    return Child()
                with patch.object(updater, 'preflight'), patch.object(updater, 'registry_version'):
                    result = updater.apply(prepared['plan'], desktop=Desktop(), wait=lambda pid: None,
                                           launch=launch, health=lambda *args: None)
                self.assertEqual(result['state'], 'updated')
                self.assertEqual(launches, [target])
                self.assertEqual(json.loads((self.target / 'Contents/Resources/update-version.json').read_text())['version'], target)
                backup = Path(result['configurationBackup'])
                backups.append(backup)
                for name, raw in current.items():
                    self.assertEqual((self.data / name).read_bytes(), raw)
                    self.assertEqual((backup / name).read_bytes(), raw)
                updater.clean_transactions(self.target)
        self.assertEqual(len(set(backups)), 5)
        self.assertTrue(all((backup / 'backup.json').is_file() for backup in backups))

    def test_switch_failed_health_restores_app_and_foreign_gateway_blocks_before_backup_or_stop(self):
        class Child:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout): pass
        class Desktop:
            def status(self): return {'running': False, 'portOccupied': False, 'instanceId': 'foreign'}
            def stop(self): raise AssertionError('A foreign gateway must not be stopped')
        for foreign in (False, True):
            with self.subTest(foreign=foreign):
                self.select_switch('2.2.2-preview.3', '2.2.2-preview.1')
                self.switch_archive()
                prepared, _ = self.prepare_switch()
                plan_file = Path(prepared['plan'])
                if foreign:
                    plan = json.loads(plan_file.read_text())
                    plan['runtime'] = {'running': True, 'instanceId': 'original'}
                    self.write(plan_file, plan)
                current = updater.configuration_snapshot(self.data)
                launches = []
                def launch(app, *args, **kwargs):
                    launches.append(json.loads((app / 'Contents/Resources/update-version.json').read_text())['version'])
                    return Child()
                def health(*args):
                    raise RuntimeError('synthetic startup failure')
                with patch.object(updater, 'preflight'), patch.object(updater, 'registry_version'):
                    result = updater.apply(plan_file, desktop=Desktop(), wait=lambda pid: None,
                                           launch=launch, health=health)
                self.assertEqual(result['state'], 'failed')
                self.assertTrue(result['recovered'])
                self.assertEqual(json.loads((self.target / 'Contents/Resources/update-version.json').read_text())['version'], '2.2.2-preview.3')
                self.assertEqual(launches, ['2.2.2-preview.3'] if foreign else ['2.2.2-preview.1', '2.2.2-preview.3'])
                self.assertEqual('configurationBackup' in result, not foreign)
                for name, raw in current.items():
                    self.assertEqual((self.data / name).read_bytes(), raw)
                updater.clean_transactions(self.target)


if __name__ == '__main__':
    unittest.main()
