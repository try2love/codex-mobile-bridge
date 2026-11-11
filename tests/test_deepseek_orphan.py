"""Repair only a confirmed missing insertion, preserving the user's patch bytes."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bridge.clients.deepseek.adapter import DeepSeek
from bridge.clients.deepseek.orphan import inspect
from bridge.clients.deepseek.recovery import DeepSeekRecovery
from bridge.clients.deepseek.setup import MARKER, verified_directory
from tests.test_deepseek_recovery import snapshot


class MissingConnector(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root/'dsh'
        self.patch = self.home/'profiles/desktop/cordis.patch.yml'
        self.patch.parent.mkdir(parents=True)
        self.old = DeepSeek(self.root/'old/desktop-sessions/deepseek', self.home)
        self.adapter = DeepSeek(self.root/'gateway/desktop-sessions/deepseek', self.home)
        self.prefix = '# user configuration\r\n- {"other": "keep"}\r\n'
        self.suffix = '# trailing comment\r\n'
        self.raw = (self.prefix+MARKER+'\r\n'+self.old._line()+'\r\n'+self.suffix).encode()
        self.patch.write_bytes(self.raw)
        self.state = snapshot(gui=False, hosts=())
        self.state['home'] = str(self.home)
        self.probe = patch('bridge.clients.deepseek.recovery._snapshot', side_effect=lambda _:copy.deepcopy(self.state)).start()
        self.addCleanup(patch.stopall)
        self.recovery = DeepSeekRecovery()

    def preview(self):
        return self.recovery.preview({}, self.adapter)

    def confirm(self, preview):
        return self.recovery.confirm({}, self.adapter, {'token':preview['token'], 'confirmed':True, 'confirmConfigurationRepair':True})

    def test_confirmed_stopped_repair_preserves_other_text_and_private_full_backup(self):
        with self.assertRaisesRegex(ValueError, '无法验证'):
            self.adapter.discover_existing()
        preview = self.preview()
        self.assertTrue(preview['canRecover'])
        self.assertTrue(preview['configurationRepair'])
        self.assertFalse(preview['requiresUnknownConfirmation'])
        self.assertEqual(self.patch.read_bytes(), self.raw)
        with self.assertRaisesRegex(ValueError, '明确确认备份'):
            self.recovery.confirm({}, self.adapter, {'token':preview['token'], 'confirmed':True})
        self.assertTrue(self.confirm(preview))
        expected = (self.prefix+MARKER+'\r\n'+self.adapter._line()+'\r\n'+self.suffix).encode()
        self.assertEqual(self.patch.read_bytes(), expected)
        backups = list(self.adapter.directory.glob('cordis.patch.before-repair-*.yml'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.raw)
        if os.name != 'nt':self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)
        self.assertEqual(verified_directory(json.loads(self.adapter._line()[2:]),self.home)[0],self.adapter.directory)
        self.assertFalse(self.old.directory.exists())
        with self.assertRaisesRegex(ValueError, '已过期'):self.confirm(preview)

    def test_live_process_or_new_process_prevents_any_patch_write(self):
        self.state = snapshot();self.state['home']=str(self.home)
        self.assertFalse(self.preview()['canRecover'])
        self.state=snapshot(gui=False,hosts=());self.state['home']=str(self.home)
        preview=self.preview()
        self.state=snapshot();self.state['home']=str(self.home)
        with self.assertRaisesRegex(ValueError,'进程已变化'):self.confirm(preview)
        self.assertEqual(self.patch.read_bytes(),self.raw)
        self.assertFalse(self.adapter.directory.exists())

    def test_changed_patch_requires_new_preview(self):
        preview=self.preview();changed=self.raw+b'# new edit\n';self.patch.write_bytes(changed)
        with self.assertRaisesRegex(ValueError,'配置已变化'):self.confirm(preview)
        self.assertEqual(self.patch.read_bytes(),changed)
        self.assertFalse(self.adapter.directory.exists())

    def test_ambiguous_or_extra_plugin_fields_are_not_adopted(self):
        entry=json.loads(self.old._line()[2:]);entry['insert'][0]['extra']='unknown'
        for raw in (self.raw+self.raw,(MARKER+'\n- '+json.dumps(entry)+'\n').encode()):
            self.patch.write_bytes(raw);self.assertFalse(self.preview()['canRecover']);self.assertEqual(self.patch.read_bytes(),raw)

    def test_existing_old_directory_or_unknown_destination_is_not_overwritten(self):
        self.old.directory.mkdir(parents=True)
        self.assertFalse(self.preview()['canRecover'])
        self.old.directory.rmdir()
        self.adapter.directory.mkdir(parents=True)
        marker=self.adapter.directory/'unrelated';marker.write_text('keep')
        self.assertFalse(self.preview()['canRecover'])
        self.assertEqual(marker.read_text(),'keep')
        self.assertEqual(self.patch.read_bytes(),self.raw)

    @unittest.skipIf(os.name=='nt','Windows junction guard is covered on target')
    def test_symlink_parent_is_not_treated_as_absent(self):
        target=self.root/'actual';target.mkdir();(self.root/'old').symlink_to(target,target_is_directory=True)
        self.assertFalse(self.preview()['canRecover'])
        self.assertEqual(self.patch.read_bytes(),self.raw)

    def test_replace_failure_keeps_backup_and_allows_new_explicit_preview(self):
        preview=self.preview()
        native_replace=os.replace
        def replace(src,dst):
            if Path(dst)==self.patch:raise OSError('fixture replace failed')
            return native_replace(src,dst)
        with patch('bridge.clients.deepseek.orphan.os.replace',side_effect=replace):
            with self.assertRaisesRegex(OSError,'fixture replace failed'):self.confirm(preview)
        self.assertEqual(self.patch.read_bytes(),self.raw)
        self.assertEqual(next(self.adapter.directory.glob('cordis.patch.before-repair-*.yml')).read_bytes(),self.raw)
        with self.assertRaisesRegex(ValueError,'已过期'):self.confirm(preview)
        credentials=(self.adapter.directory/'connection.json').read_bytes()
        self.assertTrue(self.confirm(self.preview()))
        self.assertEqual((self.adapter.directory/'connection.json').read_bytes(),credentials)

    def test_patch_changes_during_preparation_is_preserved(self):
        expected=inspect(self.adapter,self.home);calls=[]
        changed=self.raw+b'# user edit\n'
        def check():
            calls.append(1)
            if len(calls)==2:self.patch.write_bytes(changed)
        with self.assertRaisesRegex(ValueError,'恢复期间变化'):
            self.adapter.repair_missing_connector(expected,check)
        self.assertEqual(self.patch.read_bytes(),changed)
        self.assertEqual(next(self.adapter.directory.glob('cordis.patch.before-repair-*.yml')).read_bytes(),self.raw)

    def test_prepared_target_credentials_change_invalidates_the_preview(self):
        with patch('bridge.clients.deepseek.orphan.os.replace',side_effect=OSError('write blocked')):
            with self.assertRaises(OSError):self.confirm(self.preview())
        preview=self.preview()
        config=self.adapter.directory/'connection.json';value=json.loads(config.read_text())
        value['token']='a-new-owner-token';config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'配置已变化'):self.confirm(preview)
        self.assertEqual(self.patch.read_bytes(),self.raw)
        self.assertEqual(json.loads(config.read_text())['token'],'a-new-owner-token')
