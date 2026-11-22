"""Ordinary Windows launch contracts; native Explorer acceptance is separate."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.clients.codex import desktop_runtime as runtime
from bridge.clients.codex import desktop_runtime_windows as windows
from bridge.platforms.windows import codex_launch

ROOT = Path(__file__).resolve().parents[1]


class WindowsOrdinaryLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home/'bridge-desktop-runtime/records'
        self.root.mkdir(parents=True)
        self.application = self.home/'App/ChatGPT.exe'
        self.application.parent.mkdir()
        self.application.write_bytes(b'app')
        self.cli = self.application.parent/'resources/codex.exe'
        self.cli.parent.mkdir();self.cli.write_bytes(b'native')
        raw = b'immutable helper'
        self.helper = self.root/'gui'/hashlib.sha256(raw).hexdigest()/'codex-launcher.exe'
        self.helper.parent.mkdir(parents=True);self.helper.write_bytes(raw)
        self.config = {'version':1, 'home':str(self.home), 'application':os.path.normcase(str(self.application)),
                       'cli':str(self.cli), 'launcher':str(Path(windows.sys.executable).resolve()),
                       'sandboxFamily':'OpenAI.Codex_fixture'}
        self.record = {**self.config, 'dispatcher':str(self.helper), 'cliRelative':'resources/codex.exe',
                       'applicationUserModelId':'OpenAI.Codex_fixture!App'}
        self.dispatcher = {'pid':4, 'parent':2, 'image':str(self.helper), 'created':4}
        self.parent = {'pid':2, 'parent':1, 'image':os.path.normcase(str(self.application)), 'created':2}

    def test_install_keeps_third_party_override_and_never_writes_configuration(self):
        with patch.object(codex_launch.local_service, 'directory', side_effect=lambda path,**kw:Path(path)), \
             patch.object(codex_launch, 'get_environment', return_value='/other/tool.exe'), \
             patch.object(codex_launch.local_service, 'write_record') as write, \
             patch.object(codex_launch, 'set_environment') as environment:
            with self.assertRaisesRegex(ValueError, '保留原配置'):
                codex_launch.install(self.root, self.config, self.helper, ['gateway.exe'])
            write.assert_not_called();environment.assert_not_called()

    def test_selected_installation_follows_registered_identity_without_a_version_path(self):
        with patch.object(codex_launch, 'application_user_model_id', return_value=self.record['applicationUserModelId']):
            self.assertTrue(codex_launch.selected_application(self.home/'Future/ChatGPT.exe', self.record))
        with patch.object(codex_launch, 'application_user_model_id', return_value='Other!App'):
            self.assertFalse(codex_launch.selected_application(self.home/'Other/ChatGPT.exe', self.record))
        with patch.object(codex_launch, 'application_user_model_id', return_value=''):
            self.assertTrue(codex_launch.selected_application(self.application, self.record))

    def invoke(self, *, environment=None, config=None, record=None):
        selected = config or self.config
        configuration = self.root/'settings.json'
        def read(path):
            return record or self.record if Path(path).name == 'ordinary-launch.json' else selected
        patches = [patch.object(windows, 'directory', return_value=self.root),
                   patch.object(windows, '_read', side_effect=read),
                   patch.object(windows, 'process_identity', side_effect=lambda pid:
                       self.dispatcher if pid in (os.getppid(),4) else self.parent),
                   patch.dict(os.environ, {'USERPROFILE':str(self.home),'CODEX_HOME':str(self.home),
                       **(environment or {})}, clear=True)]
        for item in patches:item.start();self.addCleanup(item.stop)
        return str(configuration)

    def test_other_profile_passes_through_without_consuming_stdin_or_starting_a_bridge(self):
        path = self.invoke(environment={'CODEX_HOME':str(self.home/'other')})
        with patch.object(windows.subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
             patch.object(windows.subprocess, 'call', return_value=17) as call, \
             patch.object(windows, 'run') as share:
            self.assertEqual(windows.ordinary_run(path, ['app-server','--new-feature']),17)
            self.assertEqual(call.call_args.args[0],[str(self.cli),'app-server','--new-feature'])
            share.assert_not_called()

    def test_selected_app_uses_the_verified_dispatcher_chain(self):
        path = self.invoke()
        with patch.object(runtime, 'desktop_owner', return_value=(self.parent,None)), \
             patch.object(windows, 'run', return_value=0) as share:
            self.assertEqual(windows.ordinary_run(path,['app-server']),0)
            share.assert_called_once_with(path,['app-server'],dispatcher=self.dispatcher)

    def test_changed_dispatcher_binary_cannot_create_native_writer(self):
        self.helper.write_bytes(b'replaced')
        path = self.invoke()
        with patch.object(windows, 'run') as share:
            with self.assertRaisesRegex(ValueError,'身份已变化'):
                windows.ordinary_run(path,['app-server'])
            share.assert_not_called()

    def test_downloaded_runtime_follows_the_same_native_cache_policy_without_a_hash_pin(self):
        local=self.home/'Local'
        old=local/'OpenAI/Codex/bin/previous/codex.exe';old.parent.mkdir(parents=True);old.write_bytes(b'old')
        new=local/'OpenAI/Codex/bin/additive-update/codex.exe';new.parent.mkdir(parents=True);new.write_bytes(b'new')
        os.utime(old,(100,100));os.utime(new,(200,200))
        config={**self.config,'cli':str(old)}
        record={**self.record,'cli':str(old),'cliRelative':None}
        path=self.invoke(config=config,record=record,environment={'LOCALAPPDATA':str(local)})
        from contextlib import nullcontext
        with patch.object(runtime,'desktop_owner',return_value=(self.parent,None)), \
             patch.object(runtime,'initialization_lock',return_value=nullcontext()), \
             patch.object(windows,'_write') as write,patch.object(windows,'run',return_value=0):
            windows.ordinary_run(path,['app-server'])
            self.assertEqual(json.loads(write.call_args.args[1])['cli'],str(new))
        from bridge.platforms.windows.discovery import cached_runtime_candidates
        self.assertEqual(cached_runtime_candidates(self.cli,env={'LOCALAPPDATA':str(local)}),[])

    def test_new_package_path_rebinds_same_identity_but_not_an_existing_writer(self):
        old = {**self.config,'application':str(self.home/'Old/ChatGPT.exe'),
               'cli':str(self.home/'Old/resources/codex.exe')}
        path = self.invoke(config=old)
        from contextlib import nullcontext
        with patch.object(codex_launch,'selected_application',return_value=True), \
             patch.object(runtime,'desktop_owner',return_value=(self.parent,None)), \
             patch.object(runtime,'initialization_lock',return_value=nullcontext()), \
             patch.object(windows,'_write') as write,patch.object(windows,'run',return_value=0):
            self.assertEqual(windows.ordinary_run(path,['app-server']),0)
            value=json.loads(write.call_args.args[1])
            self.assertEqual(value['application'],os.path.normcase(str(self.application)))
            self.assertEqual(value['cli'],str(self.cli))
            self.assertEqual(value['home'],self.config['home'])
            self.assertEqual(value['sandboxFamily'],self.config['sandboxFamily'])
            (self.root/'active.json').write_text('{}')
            with patch.object(windows,'require_inactive',side_effect=ValueError('writer alive')):
                write.reset_mock()
                with self.assertRaisesRegex(ValueError,'writer alive'):
                    windows.ordinary_run(path,['app-server'])
                write.assert_not_called()

    def test_record_verifies_all_dispatcher_generations_and_parent_edges(self):
        app={'pid':1,'parent':99,'image':'app'}
        dispatcher={'pid':2,'parent':1,'image':'launcher'}
        adapter={'pid':3,'parent':2,'image':'gateway'}
        native={'pid':4,'parent':3,'image':'native'}
        rows={r['pid']:r for r in (app,dispatcher,adapter,native)}
        record={'app':app,'dispatcher':dispatcher,'adapter':adapter,'runtime':native}
        with patch.object(runtime.sys,'platform','win32'), \
             patch.object(runtime,'process_identity',side_effect=lambda pid:dict(rows[pid])):
            runtime.verify_record(record)
            with self.assertRaisesRegex(ValueError,'ancestry'):
                runtime.verify_record({**record,'dispatcher':{**dispatcher,'parent':99}})
            rows[2]={**dispatcher,'created':'reused'}
            with self.assertRaisesRegex(ValueError,'generation'):
                runtime.verify_record(record)


if __name__ == '__main__':
    unittest.main()
