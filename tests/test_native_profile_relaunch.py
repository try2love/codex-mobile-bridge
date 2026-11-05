"""Explicit native restart recovery; no real Apps, accounts or signals."""
import errno
from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from bridge.clients.profile_process import require_owned
from bridge.clients.profiles import resolve_profile
from bridge.platforms.macos.desktop import verify_relaunch

ROOT = Path(__file__).resolve().parents[1]


class ProfileRelaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = resolve_profile('claude', self.root/'profile', platform='darwin')
        self.receipt = self.root/'native-launch.json'
        self.previous = {'pid': 10, 'identity': {'birth': [1, 1], 'started': [1, 0]},
                         'command': '/Claude --user-data-dir='+str(self.profile.roots['userData']),
                         'executable': '/Claude.app/Contents/MacOS/Claude', 'home': str(self.profile.home)}
        self.current = {**self.previous, 'pid': 20, 'identity': {'birth': [2, 2], 'started': [2, 0]}}
        self.receipt.write_text(json.dumps(self.previous))
        self.guard = Mock()
        self.descriptor = {'id': 'claude', 'nativeProfile': self.profile,
                           'profileLaunchReceipt': self.receipt, 'profileGuard': self.guard}
        self.state = {'running': True, 'unknown': False, 'mainPids': [20]}
        for target, value in [('bridge.clients.profile_process.sys', SimpleNamespace(platform='darwin')),
                              ('bridge.clients.profiles.sys', SimpleNamespace(platform='darwin')),
                              ('bridge.clients.profile_process._identity', Mock(return_value=self.current)),
                              ('bridge.clients.lifecycle.inspect_client', Mock(return_value=self.state)),
                              ('bridge.platforms.macos.desktop.verify_relaunch', Mock())]:
            p = patch(target, value)
            p.start(); self.addCleanup(p.stop)
            if target.endswith('verify_relaunch'):
                self.verify = value
            elif target.endswith('inspect_client'):
                self.inspect = value

    def stored(self):
        return json.loads(self.receipt.read_text())

    def test_explicit_reconnect_updates_only_verified_generation(self):
        require_owned(self.descriptor, self.state, allow_native_relaunch=True)
        self.assertEqual(self.stored(), self.current)
        self.assertEqual(self.guard.call_count, 2)
        self.verify.assert_called_once_with(Path(self.current['executable']), self.previous, self.current,
            {'CLAUDE_CONFIG_DIR': str(self.profile.roots['code'])})
        require_owned(self.descriptor, self.state)

    def test_passive_or_normal_lifecycle_never_adopts_new_pid(self):
        with self.assertRaisesRegex(ValueError, '身份已变化'):
            require_owned(self.descriptor, self.state)
        self.assertEqual(self.stored(), self.previous)
        self.verify.assert_not_called()

    def test_missing_receipt_does_not_adopt_even_on_explicit_reconnect(self):
        self.receipt.unlink()
        with self.assertRaisesRegex(ValueError, '不是由网关启动'):
            require_owned(self.descriptor, self.state, allow_native_relaunch=True)
        self.assertFalse(self.receipt.exists())
        self.verify.assert_not_called()

    def test_foreign_saved_directory_or_executable_is_not_rebound(self):
        for key, value in [('home', '/another'), ('executable', '/another/Claude'),
                           ('command', '/Claude --user-data-dir=/another')]:
            with self.subTest(key=key):
                saved = {**self.previous, key: value}
                self.receipt.write_text(json.dumps(saved))
                with self.assertRaisesRegex(ValueError, '身份无法确认'):
                    require_owned(self.descriptor, self.state, allow_native_relaunch=True)
                self.assertEqual(self.stored(), saved)
        self.verify.assert_not_called()

    def test_profile_guard_failure_never_rewrites_receipt(self):
        self.guard.side_effect = ValueError('directory replaced')
        with self.assertRaisesRegex(ValueError, 'directory replaced'):
            require_owned(self.descriptor, self.state, allow_native_relaunch=True)
        self.verify.assert_not_called()
        self.assertEqual(self.stored(), self.previous)

    def test_postverification_ambiguous_or_changed_process_is_not_adopted(self):
        for state in ({**self.state, 'mainPids': [20, 30]}, {**self.state, 'unknown': True}):
            with self.subTest(state=state):
                self.inspect.return_value = state
                with self.assertRaisesRegex(ValueError, '身份无法确认'):
                    require_owned(self.descriptor, self.state, allow_native_relaunch=True)
                self.assertEqual(self.stored(), self.previous)

    def test_concurrent_receipt_or_cancellation_does_not_get_overwritten(self):
        newer = {**self.current, 'pid': 30}
        self.verify.side_effect = lambda *args: self.receipt.write_text(json.dumps(newer))
        with self.assertRaisesRegex(ValueError, '身份无法确认'):
            require_owned(self.descriptor, self.state, allow_native_relaunch=True)
        self.assertEqual(self.stored(), newer)
        self.receipt.write_text(json.dumps(self.previous))
        cancel = threading.Event()
        self.verify.side_effect = lambda *args: cancel.set()
        with self.assertRaisesRegex(ValueError, '取消'):
            require_owned(self.descriptor, self.state, allow_native_relaunch=True, cancelled=cancel)
        self.assertEqual(self.stored(), self.previous)

    def test_unchanged_generation_needs_no_signature_or_environment_recovery(self):
        self.receipt.write_text(json.dumps(self.current))
        require_owned(self.descriptor, self.state, allow_native_relaunch=True)
        self.verify.assert_not_called()

    def test_other_platform_remains_strict(self):
        with patch('bridge.clients.profile_process.sys', SimpleNamespace(platform='win32')):
            with self.assertRaisesRegex(ValueError, '身份已变化'):
                require_owned(self.descriptor, self.state, allow_native_relaunch=True)
        self.verify.assert_not_called()


class MacRelaunchEvidenceTests(unittest.TestCase):
    def setUp(self):
        uid = patch('os.getuid', return_value=501, create=True)
        uid.start(); self.addCleanup(uid.stop)
        self.exe = Path('/Applications/Claude.app/Contents/MacOS/Claude').resolve()
        self.old = {'pid': 10}
        self.identity = {'pid': 20, 'uid': os.getuid(), 'ruid': os.getuid(), 'zombie': False,
                         'image': str(self.exe), 'birth': (2, 3), 'started': (4, 5)}
        self.current = {'pid': 20, 'identity': {'birth': [2, 3], 'started': [4, 5]}}
        self.env = {'CLAUDE_CONFIG_DIR': '/private/profile/claude-code-config'}
        self.native = Mock()
        self.native.identity.side_effect = [ProcessLookupError(errno.ESRCH, 'gone'), self.identity, self.identity]
        p = patch('bridge.platforms.macos.force_exit.Native', return_value=self.native)
        p.start(); self.addCleanup(p.stop)
        self.read = Mock(return_value=(self.env, ()))
        p = patch('bridge.platforms.macos.codex_daemon_env.read_process_environment', self.read)
        p.start(); self.addCleanup(p.stop)
        self.signature = Mock()
        p = patch('bridge.platforms.macos.desktop.subprocess.run', self.signature)
        p.start(); self.addCleanup(p.stop)

    def test_signed_same_user_named_environment_evidence(self):
        verify_relaunch(self.exe, self.old, self.current, self.env)
        self.assertEqual(self.read.call_args.args[-1], self.env)
        self.assertIn('-R=anchor apple generic', self.signature.call_args.args[0])

    def test_old_pid_alive_or_unreadable_is_not_evidence_of_exit(self):
        for result in (self.identity, PermissionError(errno.EPERM, 'denied')):
            with self.subTest(result=result):
                self.native.identity.side_effect = [result]
                with self.assertRaises(ValueError):
                    verify_relaunch(self.exe, self.old, self.current, self.env)
        self.read.assert_not_called()
        self.signature.assert_not_called()

    def test_foreign_user_image_or_birth_is_rejected(self):
        for changed in ({'uid': os.getuid()+1}, {'ruid': os.getuid()+1}, {'image': '/other'},
                        {'birth': (9, 9)}, {'zombie': True}):
            with self.subTest(changed=changed):
                self.native.identity.side_effect = [ProcessLookupError(errno.ESRCH, 'gone'),
                                                    {**self.identity, **changed}]
                with self.assertRaises(ValueError):
                    verify_relaunch(self.exe, self.old, self.current, self.env)
        self.read.assert_not_called()

    def test_default_or_missing_code_home_is_rejected(self):
        for environment in ({}, {'CLAUDE_CONFIG_DIR': '/default'}):
            with self.subTest(environment=environment):
                self.native.identity.side_effect = [ProcessLookupError(errno.ESRCH, 'gone'), self.identity]
                self.read.return_value = environment, ()
                with self.assertRaisesRegex(ValueError, '环境不一致'):
                    verify_relaunch(self.exe, self.old, self.current, self.env)
        self.signature.assert_not_called()

    def test_unreadable_process_environment_is_not_recoverable(self):
        self.read.side_effect = OSError('launch data unavailable')
        with self.assertRaisesRegex(ValueError, '身份无法确认'):
            verify_relaunch(self.exe, self.old, self.current, self.env)
        self.signature.assert_not_called()

    def test_signature_failure_or_identity_change_is_rejected(self):
        self.signature.side_effect = OSError('signature unavailable')
        with self.assertRaisesRegex(ValueError, '签名'):
            verify_relaunch(self.exe, self.old, self.current, self.env)
        self.signature.side_effect = None
        self.native.identity.side_effect = [ProcessLookupError(errno.ESRCH, 'gone'), self.identity,
                                            {**self.identity, 'birth': (9, 9)}]
        with self.assertRaisesRegex(ValueError, '身份无法确认'):
            verify_relaunch(self.exe, self.old, self.current, self.env)


class ExplicitEntryTests(unittest.TestCase):
    def test_user_connect_enters_recovery_but_requested_restart_does_not(self):
        from bridge.clients.manager import DesktopSessions
        manager = DesktopSessions.__new__(DesktopSessions)
        manager.gateway_running = True
        manager.client_lock = threading.RLock()
        manager._changing = lambda *args: nullcontext()
        adapter = Mock()
        adapter.status.return_value = {'connected': False}
        manager.adapters = {'claude': adapter}
        descriptor = {'id': 'claude', 'nativeProfile': object()}
        manager._descriptor = Mock(return_value=descriptor)
        manager.clients = Mock(return_value={})
        manager._stop_client = Mock()
        with patch('bridge.clients.manager.sys', SimpleNamespace(platform='darwin')), \
                patch('bridge.clients.lifecycle.launch_client', return_value={'running': True}) as launch, \
                patch('bridge.clients.lifecycle.inspect_client', return_value={}):
            manager.connect_claude()
            launch.assert_called_once_with(descriptor, allow_native_relaunch=True)
            launch.reset_mock()
            manager.connect_claude(restart=True)
            launch.assert_called_once_with(descriptor, allow_native_relaunch=False)

    def test_profile_running_app_defaults_to_strict_and_only_opt_in_recovers(self):
        from bridge.clients.lifecycle import profile_running_app
        descriptor = {'nativeProfile': object()}
        state = {'running': True, 'mainPids': [20], 'unknown': False}
        with patch('bridge.clients.lifecycle.inspect_client', return_value=state), \
                patch('bridge.clients.profile_process.require_owned') as require:
            self.assertEqual(profile_running_app(descriptor), 20)
            self.assertEqual(require.call_args_list[0].kwargs['allow_native_relaunch'], False)
            require.reset_mock()
            self.assertEqual(profile_running_app(descriptor, allow_native_relaunch=True), 20)
            self.assertEqual(require.call_args_list[0].kwargs['allow_native_relaunch'], True)
            self.assertEqual(require.call_args_list[-1].kwargs, {})


if __name__ == '__main__':
    unittest.main()
