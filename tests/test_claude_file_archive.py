import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.clients.claude import file_archive as archive
from bridge.clients.claude.cli_session import ClaudeCli, ResumePreflightBlocked
from bridge.clients.errors import BridgeUnavailable


@unittest.skipUnless(os.name == 'posix', 'Claude file archive requires verified POSIX ownership and fsync semantics')
class FileArchiveTests(unittest.TestCase):
    def setUp(self):
        domain = patch.object(archive, '_domain', return_value='synthetic-domain')
        domain.start();self.addCleanup(domain.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'config'
        self.project = self.directory/'projects'/'synthetic-project'
        self.project.mkdir(parents=True)
        self.native = str(uuid.uuid4())
        self.path = self.project/(self.native+'.jsonl')
        self.raw = (json.dumps({'type':'user','cwd':str(self.project),
                    'sessionId':self.native,'permissionMode':'default',
                    'message':{'role':'user','content':'Synthetic only'}})+'\r\n').encode()
        self.path.write_bytes(self.raw)
        self.sidecar = self.project/self.native/'tool-results'/'result.txt'
        self.sidecar.parent.mkdir(parents=True)
        self.sidecar.write_bytes(b'original\x00\r\n')
        self.shared = self.project/'memory'/'shared.md'
        self.shared.parent.mkdir()
        self.shared.write_bytes(b'shared')

    def archive(self):
        return archive.change(self.directory,self.path,self.native,True)

    def stored(self):
        return self.directory/'bridge-archives'/self.native/(self.native+'.jsonl')

    def test_roundtrip_raw_bytes_identity_and_private_snapshot(self):
        self.assertEqual(self.archive(),{'archived':True})
        self.assertFalse(self.path.exists())
        self.assertEqual(archive.archive_paths(self.directory),[self.stored()])
        self.assertEqual(self.sidecar.read_bytes(),b'original\x00\r\n')
        self.sidecar.unlink()  # Simulate native retention of orphaned sidecar.
        self.assertEqual(archive.change(self.directory,self.stored(),self.native,False),{'archived':False})
        self.assertEqual(self.path.read_bytes(),self.raw)
        self.assertEqual(self.sidecar.read_bytes(),b'original\x00\r\n')
        self.assertEqual(self.shared.read_bytes(),b'shared')
        self.assertEqual(archive.archive_paths(self.directory),[])

    def test_copied_bundle_cannot_restore_into_another_profile(self):
        import shutil
        self.archive()
        other=Path(self.temp.name)/'other-profile'
        (other/'projects'/self.project.name).mkdir(parents=True)
        target=other/'bridge-archives'/self.native
        shutil.copytree(self.stored().parent,target)
        with self.assertRaises(ValueError):archive.change(other,target/self.stored().name,self.native,False)
        self.assertFalse((other/'projects'/self.project.name/self.path.name).exists())

    def test_replaced_profile_inode_refused(self):
        import shutil
        self.archive();old=self.directory.with_name('old-config')
        self.directory.rename(old);shutil.copytree(old,self.directory)
        with self.assertRaises(ValueError):archive.state(self.directory,self.stored(),self.native)

    def test_foreign_host_domain_refused(self):
        self.archive()
        with patch.object(archive,'_domain',return_value='other-host-domain'):
            with self.assertRaises(ValueError):archive.state(self.directory,self.stored(),self.native)

    def test_restore_conflicting_original_never_overwrites(self):
        self.archive();self.path.write_bytes(b'new native session')
        with self.assertRaises(ValueError):archive.change(self.directory,self.stored(),self.native,False)
        self.assertEqual(self.path.read_bytes(),b'new native session')
        self.assertEqual(self.stored().read_bytes(),self.raw)

    def test_restore_conflicting_sidecar_never_overwrites(self):
        self.archive();self.sidecar.write_bytes(b'changed')
        with self.assertRaises(ValueError):archive.change(self.directory,self.stored(),self.native,False)
        self.assertEqual(self.sidecar.read_bytes(),b'changed')
        self.assertFalse(self.path.exists())

    def test_restored_transcript_can_grow_and_archive_again(self):
        self.archive();archive.change(self.directory,self.stored(),self.native,False)
        self.path.write_bytes(self.raw+b'\n')
        self.sidecar.write_bytes(b'new result')
        self.assertFalse(archive.state(self.directory,self.path,self.native)['archived'])
        self.archive();archive.change(self.directory,self.stored(),self.native,False)
        self.assertEqual(self.path.read_bytes(),self.raw+b'\n')
        self.assertEqual(self.sidecar.read_bytes(),b'new result')

    def test_shared_hardlink_refused(self):
        os.link(self.path,self.project/'other.jsonl')
        with self.assertRaises(ValueError):self.archive()
        self.assertEqual(self.path.read_bytes(),self.raw)

    def test_symlink_sidecar_refused(self):
        try:(self.sidecar.parent/'link').symlink_to(self.shared)
        except OSError as exc:self.skipTest('Native symlink creation unavailable: '+str(exc))
        with self.assertRaises(ValueError):self.archive()
        self.assertTrue(self.path.exists())

    def test_planted_manifest_temp_symlink_never_changes_target(self):
        self.archive()
        bundle=self.stored().parent
        target=Path(self.temp.name)/'unrelated-user-file';target.write_bytes(b'unchanged')
        planted=bundle/'manifest.tmp'
        try:planted.symlink_to(target)
        except OSError as exc:self.skipTest('Native symlink creation unavailable: '+str(exc))
        archive.change(self.directory,self.stored(),self.native,False)
        self.assertEqual(target.read_bytes(),b'unchanged')
        self.assertTrue(planted.is_symlink())
        self.assertEqual(self.path.read_bytes(),self.raw)
        self.assertEqual(list(bundle.glob('.manifest-*.tmp')),[])

    def test_manifest_failed_replace_cleans_private_temp(self):
        bundle=Path(self.temp.name)/'synthetic-bundle';bundle.mkdir()
        with patch.object(archive.os,'replace',side_effect=OSError('synthetic replace failure')):
            with self.assertRaises(OSError):archive._write(bundle/'manifest.json',{'synthetic':True})
        self.assertEqual(list(bundle.iterdir()),[])

    def test_tampered_backup_refused(self):
        self.archive()
        backup=self.stored().parent/'sidecars'/'0'/'tool-results'/'result.txt'
        backup.write_bytes(b'tampered')
        self.sidecar.unlink()
        with self.assertRaises(ValueError):archive.change(self.directory,self.stored(),self.native,False)
        self.assertFalse(self.sidecar.exists())

    def test_incomplete_archive_visible_without_breaking_other_rows(self):
        self.archive();manifest=self.stored().parent/'manifest.json'
        value=json.loads(manifest.read_text());value['phase']='preparing';manifest.write_text(json.dumps(value))
        other=self.project/(str(uuid.uuid4())+'.jsonl');other.write_bytes(self.raw)
        profile={'directory':self.directory,'origin':'synthetic','binary':'/synthetic/claude'}
        cli=ClaudeCli(Path(self.temp.name)/'uploads',profile_resolver=lambda:profile)
        listing=cli.listing()
        self.assertEqual(len(listing['sessions']),2)
        bad=next(row for row in listing['sessions'] if row.get('archiveIncomplete'))
        self.assertEqual(bad['title'],'Claude Code 归档待核对')
        self.assertFalse(bad['runtimeKnown']);self.assertFalse(bad['capabilities']['archive'])
        self.assertFalse(listing['complete'])

    def test_passive_active_and_archived_list_does_not_hash_transcripts(self):
        profile={'directory':self.directory,'origin':'synthetic','binary':'/synthetic/claude'}
        cli=ClaudeCli(Path(self.temp.name)/'uploads',profile_resolver=lambda:profile)
        with patch.object(archive,'_digest',side_effect=AssertionError('passive archive hash')):
            self.assertFalse(archive.state(self.directory,self.path,self.native)['archived'])
            self.assertEqual(len(cli.listing()['sessions']),1)
        self.archive()
        with patch.object(archive,'_digest',side_effect=AssertionError('passive archive hash')):
            self.assertEqual(archive.archive_paths(self.directory),[self.stored()])
            self.assertTrue(archive.state(self.directory,self.stored(),self.native)['archived'])
            self.assertTrue(cli.listing()['sessions'][0]['archived'])

    def test_tampered_primary_refused_at_explicit_restore(self):
        self.archive();self.stored().write_bytes(b'tampered transcript')
        self.assertTrue(archive.state(self.directory,self.stored(),self.native)['archived'])
        with self.assertRaises(ValueError):archive.state(self.directory,self.stored(),self.native,verify=True)
        with self.assertRaises(ValueError):archive.change(self.directory,self.stored(),self.native,False)
        self.assertFalse(self.path.exists())

    def test_interrupted_journal_no_auto_replay(self):
        self.archive();manifest=self.stored().parent/'manifest.json'
        value=json.loads(manifest.read_text());value['phase']='preparing';manifest.write_text(json.dumps(value))
        with self.assertRaises(ValueError):archive.state(self.directory,self.stored(),self.native)
        self.assertTrue(self.stored().exists())

    def test_failed_source_unlink_rolls_back_without_overwrite(self):
        original=Path.unlink
        def fail(path,*args,**kwargs):
            if path==self.path:raise OSError('synthetic unlink failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fail),self.assertRaises(OSError):self.archive()
        self.assertTrue(self.path.exists());self.assertFalse(self.stored().exists())
        self.assertFalse(archive.state(self.directory,self.path,self.native)['archived'])

    def test_unknown_runtime_disabled_without_launch(self):
        with patch.object(archive,'VERIFIED_PICKER_VERSIONS',{}),patch.object(archive.subprocess,'run') as run:
            self.assertFalse(archive.supported('/synthetic/claude',{}))
        run.assert_not_called()

    def test_foreign_live_writer_refused_before_file_mutation(self):
        from bridge.clients.errors import BridgeUnavailable
        profile={'directory':self.directory,'origin':'synthetic','binary':'/synthetic/claude'}
        cli=ClaudeCli(Path(self.temp.name)/'uploads',profile_resolver=lambda:profile)
        sid=cli.listing()['sessions'][0]['id']
        with patch.object(archive,'supported',return_value=True),patch.object(archive,'assert_no_cli'),patch('bridge.clients.claude.cli_session.assert_session_closed',side_effect=BridgeUnavailable('live writer')):
            with self.assertRaises(BridgeUnavailable):cli.call('archive',sid,{'archived':True})
        self.assertTrue(self.path.exists())
        self.assertFalse(self.stored().exists())


    def test_local_binary_update_before_mutation_refused(self):
        profile={'directory':self.directory,'origin':'synthetic','binary':'/synthetic/claude'}
        cli=ClaudeCli(Path(self.temp.name)/'uploads',profile_resolver=lambda:profile)
        sid=cli.listing()['sessions'][0]['id']
        with patch.object(archive,'supported',side_effect=[True,False]),patch.object(archive,'assert_no_cli'),patch('bridge.clients.claude.cli_session.assert_session_closed'):
            with self.assertRaisesRegex(BridgeUnavailable,'原生程序'):cli.call('archive',sid,{'archived':True})
        self.assertEqual(self.path.read_bytes(),self.raw)
        self.assertFalse(self.stored().exists())

    def test_local_cli_archive_routes_identity_and_refuses_resume(self):
        profile={'directory':self.directory,'origin':'synthetic','binary':'/synthetic/claude'}
        cli=ClaudeCli(Path(self.temp.name)/'uploads',profile_resolver=lambda:profile)
        listing=cli.listing();sid=listing['sessions'][0]['id']
        self.assertFalse(cli.call('archive-state',sid,{})['archiveSafe'])
        with patch.object(archive,'supported',return_value=True),patch.object(archive,'assert_no_cli'),patch('bridge.clients.claude.cli_session.assert_session_closed') as guard:
            self.assertTrue(cli.call('archive-state',sid,{})['archiveSafe'])
            cli.call('archive',sid,{'archived':True})
            self.assertGreaterEqual(guard.call_count,2)
            row=cli.listing()['sessions'][0]
            self.assertEqual(row['id'],sid);self.assertEqual(row['nativeId'],self.native)
            self.assertTrue(row['archived'])
            with self.assertRaises(ResumePreflightBlocked):cli.call('resume',sid,{})
            cli.call('archive',sid,{'archived':False})
        self.assertEqual(self.path.read_bytes(),self.raw)


class ProcessCensusTests(unittest.TestCase):
    def census(self, executable, script=None, uid=1000):
        output=SimpleNamespace(returncode=0,stdout=f'{uid} 4321 {executable}\n')
        values=[output]
        if script is not None:values.append(SimpleNamespace(returncode=0,stdout=script))
        return patch.object(archive.subprocess,'run',side_effect=values)

    def test_native_cli_refused(self):
        with patch.object(archive.sys,'platform','darwin'),patch.object(archive.os,'getuid',return_value=1000,create=True),self.census('/installed/claude'):
            with self.assertRaises(ValueError):archive.assert_no_cli('/selected/claude')

    def test_other_native_version_path_refused(self):
        for executable in ('/Users/synthetic/.local/share/claude/versions/2.1.307', '/opt/homebrew/Caskroom/claude-code/2.1.291/claude'):
            with self.subTest(executable=executable),patch.object(archive.sys,'platform','darwin'),patch.object(archive.os,'getuid',return_value=1000,create=True),self.census(executable):
                with self.assertRaises(ValueError):archive.assert_no_cli('/selected/claude')

    def test_unrelated_version_directory_allowed(self):
        with patch.object(archive.sys,'platform','darwin'),patch.object(archive.os,'getuid',return_value=1000,create=True),self.census('/Users/synthetic/.local/share/other-tool/versions/2.1.307'):
            archive.assert_no_cli('/selected/claude')

    def test_other_user_native_cli_not_owned(self):
        with patch.object(archive.sys,'platform','darwin'),patch.object(archive.os,'getuid',return_value=1000,create=True),self.census('/installed/claude',uid=1001):
            archive.assert_no_cli('/selected/claude')

    def test_unrelated_node_entry_script_allowed(self):
        with patch.object(archive.sys,'platform','darwin'),patch.object(archive.os,'getuid',return_value=1000,create=True),self.census('/usr/bin/node','node /fixture/dev-server.js synthetic-private-operand'):
            archive.assert_no_cli('/selected/claude')

    def test_known_node_claude_entry_script_refused(self):
        with patch.object(archive.sys,'platform','darwin'),patch.object(archive.os,'getuid',return_value=1000,create=True),self.census('/usr/bin/node','node /fixture/@anthropic-ai/claude-code/cli.js --resume synthetic'):
            with self.assertRaises(ValueError):archive.assert_no_cli('/selected/claude')

    def test_inaccessible_census_refused(self):
        with patch.object(archive.sys,'platform','darwin'),patch.object(archive.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='')):
            with self.assertRaises(ValueError):archive.assert_no_cli('/selected/claude')


class RuntimeProofTests(unittest.TestCase):
    def test_windows_native_archive_remains_disabled(self):
        with patch.object(archive.sys, 'platform', 'win32'), \
             patch.object(archive.subprocess, 'run') as run:
            self.assertFalse(archive.supported('/not-launched', {}))
            run.assert_not_called()
        archive._domain.cache_clear()
        try:
            with patch.object(archive.sys, 'platform', 'win32'), self.assertRaises(ValueError):
                archive._domain()
        finally:
            archive._domain.cache_clear()

    def test_exact_native_digest_and_version_both_required(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            binary=Path(temporary)/'synthetic-native';binary.write_bytes(b'synthetic program')
            digest=hashlib.sha256(binary.read_bytes()).hexdigest()
            proof={('darwin','fixture-version'):digest}
            with patch.object(archive.sys,'platform','darwin'),patch.object(archive,'VERIFIED_PICKER_VERSIONS',proof),patch.object(archive.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='fixture-version (Claude Code)')) as run:
                self.assertTrue(archive.supported(str(binary),{}))
                for _ in range(10):
                    self.assertTrue(archive.supported(str(binary),{}))
                run.assert_called_once()
                binary.write_bytes(b'changed unverified distribution')
                run.reset_mock()
                self.assertFalse(archive.supported(str(binary),{}))
                run.assert_not_called()

    def test_version_failures_are_not_cached_and_replacement_invalidates_success(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary)/'synthetic-native'
            binary.write_bytes(b'synthetic program')
            proof = {('darwin', 'fixture-version'): hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch.object(archive.sys, 'platform', 'darwin'), patch.object(archive, 'VERIFIED_PICKER_VERSIONS', proof), \
                    patch.object(archive.subprocess, 'run') as run:
                run.return_value = SimpleNamespace(returncode=1, stdout='')
                self.assertFalse(archive.supported(str(binary), {}))
                run.return_value = SimpleNamespace(returncode=0, stdout='fixture-version')
                self.assertTrue(archive.supported(str(binary), {}))
                replacement = binary.with_suffix('.new')
                replacement.write_bytes(binary.read_bytes())
                replacement.replace(binary)
                self.assertTrue(archive.supported(str(binary), {}))
                self.assertEqual(run.call_count, 3)

    def test_program_change_during_version_probe_is_not_cached(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary)/'synthetic-native'
            binary.write_bytes(b'synthetic program')
            proof = {('darwin', 'fixture-version'): hashlib.sha256(binary.read_bytes()).hexdigest()}
            def version(*args, **kwargs):
                binary.write_bytes(b'changed program')
                return SimpleNamespace(returncode=0, stdout='fixture-version')
            with patch.object(archive.sys, 'platform', 'darwin'), patch.object(archive, 'VERIFIED_PICKER_VERSIONS', proof), \
                    patch.object(archive.subprocess, 'run', side_effect=version):
                self.assertFalse(archive.supported(str(binary), {}))

    def test_binary_update_during_hash_disables_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary=Path(temporary)/'synthetic-native';binary.write_bytes(b'synthetic program')
            with patch.object(archive.sys,'platform','darwin'),patch.object(archive,'VERIFIED_PICKER_VERSIONS',{('darwin','fixture-version'):'synthetic'}),patch.object(archive,'_binary_hash',side_effect=ValueError('program changed')),patch.object(archive.subprocess,'run') as run:
                self.assertFalse(archive.supported(str(binary),{}))
                run.assert_not_called()

    def test_mac_proof_does_not_enable_linux(self):
        with patch.object(archive.sys,'platform','linux'),patch.object(archive,'VERIFIED_PICKER_VERSIONS',{('darwin','2.1.292'):'synthetic'}),patch.object(archive.subprocess,'run') as run:
            self.assertFalse(archive.supported('/not-launched',{}))
            run.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'Durable Linux archive fixture uses a POSIX filesystem')
class DurableLinuxIdentityTests(unittest.TestCase):
    def run_roundtrip(self, other_machine=False):
        import hashlib
        native=str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)/'config';project=directory/'projects'/'synthetic'
            project.mkdir(parents=True);original=project/(native+'.jsonl');original.write_bytes(b'synthetic bytes\r\n')
            before=hashlib.sha256(original.read_bytes()).hexdigest()
            machine=['a'*32];namespace=['pid:[111]']
            real_read=Path.read_text;real_link=os.readlink
            def read(path,*args,**kwargs):
                if str(path)=='/etc/machine-id':return machine[0]
                return real_read(path,*args,**kwargs)
            def link(path,*args,**kwargs):
                if str(path)=='/proc/self/ns/pid':return namespace[0]
                return real_link(path,*args,**kwargs)
            archive._domain.cache_clear()
            try:
                with patch.object(archive.sys,'platform','linux'),patch.object(Path,'read_text',read),patch.object(archive.os,'readlink',link):
                    archive.change(directory,original,native,True)
                    stored=directory/'bridge-archives'/native/(native+'.jsonl')
                    namespace[0]='pid:[222]'  # Normal distro restart, not a new host.
                    if other_machine:machine[0]='b'*32
                    archive._domain.cache_clear()  # New worker after restart.
                    if other_machine:
                        with self.assertRaises(ValueError):archive.change(directory,stored,native,False)
                        self.assertFalse(original.exists())
                    else:
                        archive.change(directory,stored,native,False)
                        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),before)
            finally:archive._domain.cache_clear()

    def test_namespace_restart_does_not_invalidate_durable_archive(self):
        self.run_roundtrip()

    def test_other_machine_cannot_restore_archive(self):
        self.run_roundtrip(other_machine=True)


class LinuxExecutableCensus(unittest.TestCase):
    def stat_text(self, start='123'):
        return '4321 (2.1.308) '+' '.join(['S']+['0']*18+[start])

    def metadata(self, executable='/home/synthetic/.local/share/claude/versions/2.1.308', namespace='pid:[100]'):
        def link(path):
            return executable if Path(path).name == 'exe' else namespace
        return patch.object(archive.os, 'readlink', side_effect=link)

    def test_full_executable_path_replaces_truncated_comm(self):
        result=SimpleNamespace(returncode=0,stdout='1000 4321 2.1.308\n')
        with patch.object(archive.sys,'platform','linux'),patch.object(archive.os,'getuid',return_value=1000,create=True), \
             patch.object(archive.os,'readlink',return_value='pid:[100]'), \
             patch.object(archive,'_linux_executable',return_value='/home/synthetic/.local/share/claude/versions/2.1.308') as read, \
             patch.object(archive.subprocess,'run',return_value=result):
            with self.assertRaises(ValueError):archive.assert_no_cli('/selected/claude')
            read.assert_called_once_with(4321,1000,'pid:[100]')

    def test_noncandidate_unreadable_process_does_not_expand_guard_scope(self):
        result=SimpleNamespace(returncode=0,stdout='1000 4321 session-helper\n')
        with patch.object(archive.sys,'platform','linux'),patch.object(archive.os,'getuid',return_value=1000,create=True), \
             patch.object(archive.os,'readlink',return_value='pid:[100]'), \
             patch.object(archive,'_linux_executable',side_effect=PermissionError()) as read, \
             patch.object(archive.subprocess,'run',return_value=result) as commands:
            archive.assert_no_cli('/selected/claude')
            read.assert_not_called();self.assertEqual(commands.call_count,1)

    def test_native_candidate_unreadable_is_refused_without_argument_reads(self):
        result=SimpleNamespace(returncode=0,stdout='1000 4321 2.1.308\n')
        with patch.object(archive.sys,'platform','linux'),patch.object(archive.os,'getuid',return_value=1000,create=True), \
             patch.object(archive.os,'readlink',return_value='pid:[100]'), \
             patch.object(archive,'_linux_executable',side_effect=ValueError('unknown executable')), \
             patch.object(archive.subprocess,'run',return_value=result) as commands:
            with self.assertRaisesRegex(ValueError,'unknown executable'):archive.assert_no_cli('/selected/claude')
            self.assertEqual(commands.call_count,1)

    def test_other_native_version_candidate_is_refused(self):
        result=SimpleNamespace(returncode=0,stdout='1000 4321 2.1.307\n')
        with patch.object(archive.sys,'platform','linux'),patch.object(archive.os,'getuid',return_value=1000,create=True), \
             patch.object(archive.os,'readlink',return_value='pid:[100]'), \
             patch.object(archive,'_linux_executable',return_value='/home/synthetic/.local/share/claude/versions/2.1.307'), \
             patch.object(archive.subprocess,'run',return_value=result) as commands:
            with self.assertRaises(ValueError):archive.assert_no_cli('/selected/2.1.308')
            self.assertEqual(commands.call_count,1)

    def test_selected_long_basename_is_a_candidate(self):
        binary='/selected/native-selected-binary-long'
        comm=Path(binary).name[:15];result=SimpleNamespace(returncode=0,stdout='1000 4321 '+comm+'\n')
        with patch.object(Path,'resolve',lambda path,**options:path), \
             patch.object(archive.sys,'platform','linux'),patch.object(archive.os,'getuid',return_value=1000,create=True), \
             patch.object(archive.os,'readlink',return_value='pid:[100]'), \
             patch.object(archive,'_linux_executable',return_value=binary) as read, \
             patch.object(archive.subprocess,'run',return_value=result):
            with self.assertRaises(ValueError):archive.assert_no_cli(binary)
            read.assert_called_once()

    def test_deleted_executable_retains_native_install_identity(self):
        with patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=1000)), \
             patch.object(Path,'read_text',return_value=self.stat_text()), \
             self.metadata('/home/synthetic/.local/share/claude/versions/2.1.308 (deleted)'):
            self.assertEqual(archive._linux_executable(4321,1000,'pid:[100]'), '/home/synthetic/.local/share/claude/versions/2.1.308')

    def test_pid_generation_change_fails_closed(self):
        with patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=1000)), \
             patch.object(Path,'read_text',side_effect=[self.stat_text(),self.stat_text('456')]),self.metadata():
            with self.assertRaises(ValueError):archive._linux_executable(4321,1000,'pid:[100]')

    def test_uid_and_namespace_changes_fail_closed(self):
        with patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=1001)):
            with self.assertRaises(ValueError):archive._linux_executable(4321,1000,'pid:[100]')
        with patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=1000)),self.metadata(namespace='pid:[200]'):
            with self.assertRaises(ValueError):archive._linux_executable(4321,1000,'pid:[100]')

    def test_definitely_exited_process_is_skipped(self):
        with patch.object(Path,'stat',side_effect=FileNotFoundError()):
            self.assertIsNone(archive._linux_executable(4321,1000,'pid:[100]'))

    def test_missing_exe_but_live_process_and_permissions_fail_closed(self):
        for error in (FileNotFoundError(),PermissionError()):
            with self.subTest(error=type(error).__name__), \
                 patch.object(Path,'stat',return_value=SimpleNamespace(st_uid=1000)), \
                 patch.object(Path,'read_text',return_value=self.stat_text()), \
                 patch.object(archive.os,'readlink',side_effect=lambda path: (_ for _ in ()).throw(error) if Path(path).name == 'exe' else 'pid:[100]'):
                with self.assertRaises(ValueError):archive._linux_executable(4321,1000,'pid:[100]')
