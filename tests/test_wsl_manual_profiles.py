"""Manual WSL registration uses actual handlers and private metadata, no CLI/model runs."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    import pwd
except ModuleNotFoundError:
    pwd = None

from bridge.clients.claude.wsl import WslClaudeEntries, entry_id as claude_id
from bridge.clients.manager import DesktopSessions
from bridge.features.endpoints.registry import Entries, entry_id
from bridge.platforms.windows import wsl

ROOT = Path(__file__).resolve().parents[1]


class ManualWslProfiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(self.manager.close)
        self.codex = Entries(self.root)
        self.codex.wsl = wsl
        self.manager.instances.registry.codex = self.codex
        self.claude = self.manager.claude_wsl = WslClaudeEntries(self.manager.directory)
        self.home = '/home/alice/isolated profile'
        self.request = {'action': 'client-profiles', 'operation': 'save', 'kind': 'wsl-cli',
                        'distribution': 'Ubuntu-24.04', 'user': 'alice', 'home': self.home,
                        'label': 'Private WSL'}

    @contextlib.contextmanager
    def target(self, family, *, info=None):
        if family == 'codex':
            value = info or {'user': 'alice', 'homes': [{'home': self.home, 'daemon': None}]}
            with patch.object(wsl, 'probe', return_value=value) as probe:
                yield probe
        else:
            value = info or {'user': 'alice', 'profiles': [{'configDir': self.home}]}
            with patch.object(wsl, 'read', return_value=value) as probe:
                yield probe

    def test_real_control_registers_both_families_without_constructing_an_execution_owner(self):
        with patch('bridge.clients.instances.sys.platform', 'win32'), \
             patch.object(wsl, 'running_distributions', return_value=['Ubuntu-24.04']), \
             patch.object(wsl, 'ensure_daemon') as start:
            caps = self.manager.control({'action': 'client-profiles', 'operation': 'list'})['capabilities']
            self.assertEqual({r['family'] for r in caps if r.get('wslSupported')}, {'codex', 'claude'})
            for family, factory in (('codex', entry_id), ('claude', claude_id)):
                with self.subTest(family=family), self.target(family) as probe:
                    value = self.manager.control({**self.request, 'family': family})
                    self.assertEqual(value['entry'], factory('Ubuntu-24.04', 'alice', self.home))
                    self.assertNotIn('instance', value)  # No automatic account/runtime activation.
                    if family == 'codex':
                        probe.assert_called_once_with('Ubuntu-24.04', 'alice', home=self.home)
                    else:
                        self.assertEqual(probe.call_args.args[::2], ('Ubuntu-24.04', 'alice'))
                        self.assertIn('selected_directory', probe.call_args.args[1])
            start.assert_not_called()
        self.assertFalse(self.codex.bridges)
        self.assertFalse(self.manager.instances.children)
        self.assertEqual(Entries(self.root).entries[0]['home'], self.home)
        self.assertEqual(WslClaudeEntries(self.manager.directory).entries[0]['configDir'], self.home)

    def test_duplicate_manual_save_preserves_registered_disabled_state_and_bytes(self):
        with patch('bridge.clients.instances.sys.platform', 'win32'), \
             patch.object(wsl, 'running_distributions', return_value=['Ubuntu-24.04']):
            for family, registry in (('codex', self.codex), ('claude', self.claude)):
                with self.subTest(family=family), self.target(family):
                    identifier = self.manager.control({**self.request, 'family': family})['entry']
                    registry.configure(identifier, False)
                    before = registry.path.read_bytes()
                    with self.assertRaisesRegex(ValueError, '已登记'):
                        self.manager.control({**self.request, 'family': family, 'label': 'Do not replace'})
                    self.assertEqual(registry.path.read_bytes(), before)
                    self.assertFalse(registry.entries[0]['enabled'])
                    self.assertEqual(registry.entries[0]['label'], 'Private WSL')

    def test_non_windows_does_not_offer_or_accept_wsl_registration(self):
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), patch('bridge.clients.instances.sys.platform', platform), \
                 patch.object(wsl, 'read') as read, patch.object(wsl, 'probe') as probe:
                caps = self.manager.control({'action': 'client-profiles', 'operation': 'list'})['capabilities']
                self.assertFalse(any(row.get('wslSupported') for row in caps))
                for family in ('codex', 'claude'):
                    with self.assertRaisesRegex(ValueError, 'WSL 客户端保存请求无效'):
                        self.manager.control({**self.request, 'family': family})
                read.assert_not_called(); probe.assert_not_called()

    def test_stopped_distribution_never_executes_python_or_registers(self):
        with patch('bridge.clients.instances.sys.platform', 'win32'), \
             patch.object(wsl, 'running_distributions', return_value=[]), \
             patch.object(wsl.subprocess, 'run') as process:
            for family in ('codex', 'claude'):
                with self.subTest(family=family), self.assertRaisesRegex(OSError, '已停止或不可用'):
                    self.manager.control({**self.request, 'family': family})
            process.assert_not_called()
        self.assertFalse(self.codex.entries); self.assertFalse(self.claude.entries)

    def test_windows_and_relative_paths_are_rejected_before_wsl_invocation(self):
        for family, registry in (('codex', self.codex), ('claude', self.claude)):
            for home in ('C:\\Users\\alice\\.codex', '\\\\wsl$\\Ubuntu\\home\\alice',
                         '~/.codex', '.codex', '//server/config', '/home/alice/\nconfig'):
                with self.subTest(family=family, home=home), patch.object(wsl, 'read') as read:
                    with self.assertRaisesRegex(ValueError, 'Linux 绝对路径'):
                        registry.save_wsl('Ubuntu-24.04', 'alice', home, 'Private')
                    read.assert_not_called()

    def test_target_response_must_match_requested_linux_user_and_single_home(self):
        for family in ('codex', 'claude'):
            for info in ({'user': 'other', 'homes': [{'home': self.home}], 'profiles': [{'configDir': self.home}]},
                         {'user': 'alice', 'homes': [], 'profiles': []}):
                with self.subTest(family=family, info=info), self.target(family, info=info):
                    registry = self.codex if family == 'codex' else self.claude
                    with self.assertRaises(ValueError):
                        registry.save_wsl('Ubuntu-24.04', 'alice', self.home, 'Private')
        self.assertFalse(self.codex.entries); self.assertFalse(self.claude.entries)

    def test_manual_codex_reuses_windows_shared_home_conflict_guard(self):
        self.codex.desktop_home = self.root
        info = {'user': 'alice', 'homes': [{'home': self.home, 'windowsHome': str(self.root)}]}
        with self.target('codex', info=info), patch.object(wsl, 'running_distributions', return_value=['Ubuntu-24.04']):
            with self.assertRaisesRegex(ValueError, '共用同一'):
                self.codex.save_wsl('Ubuntu-24.04', 'alice', self.home, 'Shared')
        self.assertFalse(self.codex.entries); self.assertFalse(self.codex.candidates)
        self.assertFalse(self.codex.path.exists())

    def test_local_kind_and_other_families_cannot_accept_wsl_fields(self):
        with patch('bridge.clients.instances.sys.platform', 'win32'), patch.object(wsl, 'read') as read:
            for value in ({**self.request, 'family': 'codex', 'kind': 'codex-local'},
                          {**self.request, 'family': 'deepseek'}):
                with self.assertRaises(ValueError):self.manager.control(value)
            read.assert_not_called()

    def test_failed_atomic_write_never_publishes_or_enables_a_manual_entry(self):
        from bridge.app.lifecycle import private_json
        for family, registry in (('codex', self.codex), ('claude', self.claude)):
            with self.subTest(family=family), self.target(family):
                registry.path.parent.mkdir(parents=True, exist_ok=True)
                old_home = '/home/alice/old profile'
                factory = entry_id if family == 'codex' else claude_id
                old = {'id': factory('Ubuntu-24.04', 'alice', old_home), 'distribution': 'Ubuntu-24.04',
                       'user': 'alice', 'home' if family == 'codex' else 'configDir': old_home,
                       'label': 'Old disabled profile', 'enabled': False}
                registry.entries = [old]
                private_json(registry.path, {'version': 1, 'entries': registry.entries})
                before = registry.path.read_bytes()
                temporary = registry.path.with_suffix('.tmp')
                temporary.write_bytes(b'owned existing temporary file')
                with self.assertRaises(FileExistsError):
                    registry.save_wsl('Ubuntu-24.04', 'alice', self.home, 'Private')
                self.assertEqual(registry.entries, [old]); self.assertFalse(registry.candidates)
                self.assertFalse(old['enabled'])
                self.assertEqual(registry.path.read_bytes(), before)
                self.assertEqual(temporary.read_bytes(), b'owned existing temporary file')
                temporary.unlink()
                identifier = registry.save_wsl('Ubuntu-24.04', 'alice', self.home, 'Private')
                self.assertEqual(registry.entries[-1]['id'], identifier)
                self.assertEqual(registry.entries[0], old)

    def test_atomic_replace_with_lost_ack_is_verified_without_a_second_write(self):
        from bridge.app.lifecycle import private_json
        for family, registry, module in (('codex', self.codex, 'bridge.features.endpoints.registry'),
                                          ('claude', self.claude, 'bridge.clients.claude.wsl')):
            def committed_then_error(path, value):
                private_json(path, value)
                raise OSError('synthetic acknowledgement loss after atomic replace')
            with self.subTest(family=family), self.target(family), \
                 patch(module+'.private_json', side_effect=committed_then_error) as write:
                identifier = registry.save_wsl('Ubuntu-24.04', 'alice', self.home, 'Private')
                write.assert_called_once()
                self.assertEqual(registry.entries[0]['id'], identifier)
                self.assertEqual(json.loads(registry.path.read_text())['entries'], registry.entries)


@unittest.skipUnless(pwd is not None, 'Target Linux metadata proof requires a POSIX host')
class LinuxDirectoryProof(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root/'non-default profile'
        self.directory.mkdir()
        self.user = pwd.getpwuid(os.getuid()).pw_name

    def run_source(self, directory, user=None):
        scope = {}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(wsl.configuration_source(user or self.user, str(directory)), scope)
        self.assertEqual(output.getvalue(), '')  # No credential or other contents emitted.
        return scope['selected_directory']

    def test_canonical_same_uid_non_default_home_is_accepted_without_home_subtree_guessing(self):
        alias = self.root/'alias'; alias.symlink_to(self.directory, target_is_directory=True)
        self.assertEqual(self.run_source(alias), self.directory)

    def test_other_linux_user_or_directory_owner_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'user mismatch'):
            self.run_source(self.directory, 'other-user')
        uid = os.getuid()
        with patch('os.getuid', return_value=uid+1), \
             patch('pwd.getpwuid', return_value=SimpleNamespace(pw_name=self.user)):
            with self.assertRaisesRegex(ValueError, 'not owned'):
                self.run_source(self.directory)

    def test_non_directory_and_unreadable_configuration_are_rejected(self):
        file = self.root/'file'; file.write_text('not a configuration directory')
        with self.assertRaisesRegex(ValueError, 'not owned'):
            self.run_source(file)
        with patch('os.access', return_value=False), self.assertRaisesRegex(ValueError, 'not owned'):
            self.run_source(self.directory)

    def test_user_or_path_tokens_cannot_rewrite_generated_source(self):
        directory = self.root/"REQUESTED_USER ' REQUESTED_DIRECTORY"; directory.mkdir()
        self.assertEqual(self.run_source(directory), directory)

    def test_selected_codex_probe_reads_only_its_directory_metadata(self):
        config = self.directory/'config.toml'
        config.write_text('api_key="PRIVATE_SENTINEL_MUST_NOT_BE_READ"')
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path == config:
                raise AssertionError('Manual registration read configuration contents')
            return original_open(path, *args, **kwargs)
        def selected_read(distribution, source, user):
            self.assertNotIn('/proc', source)
            self.assertNotIn('environ', source)
            self.assertNotIn('cmdline', source)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(Path, 'open', guarded_open), \
                 patch('subprocess.run', return_value=Mock(returncode=0, stdout='C:\\private\\profile')) as process:
                exec(source, {})
            process.assert_called_once_with(['wslpath', '-w', str(self.directory)],
                                           capture_output=True, text=True, timeout=3)
            value = json.loads(output.getvalue())
            self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())
            return value
        with patch.object(wsl, 'read', side_effect=selected_read):
            value = wsl.probe('Ubuntu-24.04', self.user, home=str(self.directory))
        self.assertEqual(value['user'], self.user)
        self.assertEqual(value['homes'], [{'home': str(self.directory), 'daemon': None,
                                          'windowsHome': 'C:\\private\\profile'}])
