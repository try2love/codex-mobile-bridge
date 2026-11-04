import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from bridge.clients.discovery import discover_clients
from bridge.platforms.linux import appimage, zcode_pipe
from test_zcode_linux import package


DESKTOP = b'[Desktop Entry]\nType=Application\nName=ZCode\nStartupWMClass=ZCode\nExec=AppRun --no-sandbox %U\nX-AppImage-Version=9.3.4.8\n'


def image(path):
    data = bytearray(512)
    data[:11] = b'\x7fELF\x02\x01\x01\0AI\x02'
    struct.pack_into('<Q', data, 40, 64)
    struct.pack_into('<HH', data, 58, 64, 1)
    struct.pack_into('<IIQQQQIIQQ', data, 64, 0, 1, 0, 0, 0, 128, 0, 0, 0, 0)
    data[128:132] = b'hsqs'
    struct.pack_into('<HH', data, 156, 4, 0)
    struct.pack_into('<Q', data, 168, 384)
    path.write_bytes(data);path.chmod(0o755)
    return path


@unittest.skipUnless(os.name == 'posix', 'Linux AppImage and POSIX parser fixtures')
class AppImageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = image(self.root/'ZCode with spaces.AppImage')
        self.proc = self.root/'proc';(self.proc/'self').mkdir(parents=True)
        self.mounts = []
        self.stub_proc = patch.object(appimage, 'PROC', self.proc);self.stub_proc.start();self.addCleanup(self.stub_proc.stop)
        (self.proc/'self/mountinfo').write_text('')

    def process(self, pid, executable, parent=1, fds=()):
        path = self.proc/str(pid);(path/'fd').mkdir(parents=True)
        (path/'exe').symlink_to(executable)
        fields = ['S',str(parent)]+['0']*17+[str(pid*100)]+['0']*4
        (path/'stat').write_text(str(pid)+' (fixture) '+' '.join(fields))
        for index,target in enumerate(fds):(path/'fd'/str(index)).symlink_to(target)
        return path

    def runtime(self, pid, keeper, token, *, name='@zcode/desktop'):
        folder = self.root/('mount '+str(pid));exe = package(folder, name=name)
        escaped = str(folder).replace(' ', r'\040')
        self.mounts.append('8 2 0:83 / '+escaped+' ro,nosuid,nodev - fuse.ZCode image ro,user_id='+str(os.getuid()))
        (self.proc/'self/mountinfo').write_text('\n'.join(self.mounts))
        self.process(keeper,self.app,fds=(str(self.app),'/dev/fuse',token))
        self.process(pid,exe,fds=(token,))
        return exe

    def test_cold_metadata_is_read_without_executing_image_or_desktop_exec(self):
        self.assertEqual(appimage.image_offset(self.app),128)
        entries=self.root/'applications';entries.mkdir()
        (entries/'zcode.desktop').write_text('[Desktop Entry]\nName=ZCode\nType=Application\nExec="'+str(self.app)+'" %U\n')
        with patch.object(appimage,'_parser',return_value='/usr/bin/unsquashfs'), \
                patch.object(appimage,'_read_desktop',return_value=DESKTOP) as read:
            row=discover_clients(platform='linux',home=self.root,env={'XDG_DATA_HOME':str(self.root),'XDG_DATA_DIRS':str(self.root/'none'),'PATH':''})['zcode']
            self.assertEqual(row['application'],str(self.app));self.assertEqual(row['executable'],str(self.app))
            self.assertTrue(row['coldStartVerified']);self.assertFalse(row['runtimeVerified'])
            self.assertEqual(row['version'],'9.3.4.8');read.assert_called_once()
            row2=zcode_pipe.installation(self.app);self.assertEqual(row2['version'],row['version']);read.assert_called_once()

    def test_kernel_pipe_proves_runtime_source_and_rejects_ambiguity(self):
        exe=self.runtime(11,12,'pipe:[101]')
        with patch.object(appimage,'_parser',return_value=None):
            value=zcode_pipe.installation(self.app)
            self.assertEqual(value['application'],str(self.app));self.assertEqual(value['executable'],str(exe))
            self.assertTrue(value['runtimeVerified']);self.assertFalse(value['coldStartVerified'])
            self.runtime(21,22,'pipe:[202]')
            self.assertIsNone(zcode_pipe.installation(self.app))
            self.assertEqual(zcode_pipe.installation(self.app,pid=11)['executable'],str(exe))
            self.assertIsNone(zcode_pipe.installation(self.app,pid=99))
        self.process(31,exe,parent=11,fds=('pipe:[101]',))
        self.assertEqual(sorted(r['pid'] for r in zcode_pipe.runtime_candidates(self.app)),[11,21])

    def test_runtime_needs_matching_source_fuse_mount_uid_and_brand(self):
        self.runtime(11,12,'pipe:[101]',name='not-zcode')
        with patch.object(appimage,'_parser',return_value=None):self.assertIsNone(zcode_pipe.installation(self.app))
        package(self.root/'mount 11')
        (self.proc/'12/fd/0').unlink();(self.proc/'12/fd/0').symlink_to(str(self.root/'another.AppImage'))
        self.assertEqual(zcode_pipe.runtime_candidates(self.app),[])
        (self.proc/'12/fd/0').unlink();(self.proc/'12/fd/0').symlink_to(str(self.app))
        with patch.object(appimage.os,'getuid',return_value=os.getuid()+1):self.assertEqual(zcode_pipe.runtime_candidates(self.app),[])
        (self.proc/'self/mountinfo').write_text('')
        self.assertEqual(zcode_pipe.runtime_candidates(self.app),[])

    def test_replaced_source_does_not_claim_the_old_fuse_runtime(self):
        self.runtime(11,12,'pipe:[101]')
        old=self.app.with_name('old.AppImage');self.app.rename(old);image(self.app)
        (self.proc/'12/exe').unlink();(self.proc/'12/exe').symlink_to(old)
        (self.proc/'12/fd/0').unlink();(self.proc/'12/fd/0').symlink_to(old)
        self.assertEqual(zcode_pipe.runtime_candidates(self.app),[])

    def test_bad_formats_brand_and_mid_read_change_are_not_accepted(self):
        original=self.app.read_bytes()
        for offset,replacement in ((8,b'XX\2'),(40,b'\xff'*8),(60,b'\xff\xff'),(128,b'nope')):
            data=bytearray(original);data[offset:offset+len(replacement)]=replacement;self.app.write_bytes(data)
            self.assertIsNone(appimage.image_offset(self.app))
        self.app.write_bytes(original)
        with patch.object(appimage,'_parser',return_value='/usr/bin/unsquashfs'), \
                patch.object(appimage,'_read_desktop',return_value=DESKTOP.replace(b'Name=ZCode',b'Name=Other')):
            self.assertIsNone(zcode_pipe.installation(self.app))
        self.app.write_bytes(original)
        def changed(*args):self.app.write_bytes(original+b'changed');return DESKTOP
        with patch.object(appimage,'_parser',return_value='/usr/bin/unsquashfs'),patch.object(appimage,'_read_desktop',side_effect=changed):
            self.assertIsNone(appimage.metadata(self.app,'zcode.desktop'))

    def test_owned_parser_is_size_and_time_bounded(self):
        parser=self.root/'parser';parser.write_text('#!'+sys.executable+'\nimport sys\nsys.stdout.buffer.write(b"x"*70000)\n');parser.chmod(0o700)
        with self.assertRaisesRegex(ValueError,'过大'):appimage._read_desktop(str(parser),self.app,128,'zcode.desktop')
        parser.write_text('#!'+sys.executable+'\nimport time\ntime.sleep(10)\n')
        with self.assertRaisesRegex(ValueError,'超时'):appimage._read_desktop(str(parser),self.app,128,'zcode.desktop',timeout=.1)
        with self.assertRaisesRegex(ValueError,'路径无效'):appimage._read_desktop(str(parser),self.app,128,'../private')

    def test_ordinary_launch_uses_durable_image_and_never_imports_desktop_flags(self):
        exe=self.root/'mount/zcode'
        info={'application':str(self.app),'executable':str(exe)}
        with patch.object(zcode_pipe,'installation',return_value=info), \
                patch.dict(os.environ,{'DISPLAY':':fixture','APPIMAGE':'/gateway.AppImage','APPDIR':'/gateway-mount',
                                      'ARGV0':'gateway','TARGET_APPIMAGE':'/another.AppImage'}),patch.object(zcode_pipe.desktop,'processes',return_value=[]) as processes, \
                patch.object(zcode_pipe.subprocess,'Popen') as launch:
            zcode_pipe.launch_ordinary(self.app,self.root)
            self.assertEqual(launch.call_args.args[0],[str(self.app)])
            predicate=processes.call_args.args[0]
            self.assertTrue(predicate(self.app));self.assertTrue(predicate(exe))
            self.assertTrue(all(name not in launch.call_args.kwargs['env'] for name in ('APPIMAGE','APPDIR','ARGV0','TARGET_APPIMAGE')))

    def test_runtime_owner_waits_for_verified_exec_but_rejects_reused_pid(self):
        first={'pid':11,'uid':os.getuid(),'birth':'100','started':'boot','image':str(self.app)}
        live={**first,'image':str(self.root/'mount/zcode')}
        info={'packageType':'appimage','runtimeVerified':True,'executable':live['image']}
        with patch.object(zcode_pipe,'identity',side_effect=[first,live]), \
                patch.object(zcode_pipe,'installation',side_effect=[None,info]),patch.object(zcode_pipe.time,'sleep'):
            self.assertEqual(zcode_pipe.runtime_owner(self.app,11),live)
        with patch.object(zcode_pipe,'identity',side_effect=[first,{**live,'birth':'200'}]), \
                patch.object(zcode_pipe,'installation',return_value=None),patch.object(zcode_pipe.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'身份已变化'):zcode_pipe.runtime_owner(self.app,11)

    def test_initialization_keeps_unverified_cold_app_running_and_accepts_new_mount_after_quit(self):
        from bridge.clients.manager import DesktopSessions
        config={'application':str(self.app),'dataDirectory':str(self.root),'privatePipe':True}
        adapter=Mock(config=config,home=self.root);adapter.connection_owner.return_value=None
        manager=object.__new__(DesktopSessions);manager.adapters={'zcode':adapter};manager.zcode_pipe=Mock();manager.config={}
        manager._stop_client=Mock();manager._descriptor=Mock()
        base={'id':'zcode','pipeSupported':True,'application':str(self.app),'executable':'/old/mount/zcode',
              'packageType':'appimage','installationIdentity':[1,2,3], 'coldStartVerified':False}
        running={'running':True,'mainPids':[11],'unknown':False}
        with patch('bridge.clients.lifecycle.inspect_client',return_value=running):
            with self.assertRaisesRegex(ValueError,'冷启动尚未核验'):
                manager._start_zcode_connection(base,initialize=True,acknowledge_unavailable=False)
        manager._stop_client.assert_not_called();manager.zcode_pipe.start.assert_not_called()
        base['coldStartVerified']=True;cold={**base,'executable':str(self.app)};new={**base,'executable':'/new/mount/zcode'}
        manager._descriptor.side_effect=[cold,new];owner={'pid':21};manager.zcode_pipe.start.return_value=owner
        with patch('bridge.clients.lifecycle.inspect_client',side_effect=[running,{'running':True,'mainPids':[21],'unknown':False}]),patch('sys.platform','linux'):
            manager._start_zcode_connection(base,initialize=True,acknowledge_unavailable=False)
        manager._stop_client.assert_called_once();manager.zcode_pipe.start.assert_called_once()
        adapter.start_private_application.assert_called_once_with(owner)


if __name__=='__main__':unittest.main()
