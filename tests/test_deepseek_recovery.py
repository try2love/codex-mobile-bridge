"""Recovery tests use process snapshots only; no native process is signalled."""
import copy
import hashlib
import signal
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.integrations.deepseek_recovery import DeepSeekRecovery, _evidence, _quit, _quitting_snapshot, _snapshot


def snapshot(*, gui=True, hosts=(12,), unknown=()):
    main = [11] if gui else []
    pids = main+list(hosts)+list(unknown)
    return {'executable': '/Applications/Fixture.app/Contents/MacOS/Fixture', 'home': '/fixture/home',
            'state': {'running': bool(pids), 'pids': pids, 'mainPids': main, 'runtimePids': list(hosts),
                      'unknown': len(hosts) > 1 or bool(unknown)},
            'identities': {pid: {'start': 'same-start', 'parent': 11 if pid in hosts and gui else 1,
                                 'command': 'fixture-command-'+str(pid)} for pid in pids},
            'unrecognized': list(unknown)}


class RecoveryConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.recovery = DeepSeekRecovery()
        self.adapter = Mock()
        self.state = snapshot(hosts=(12, 13))
        self.evidence = {'busy': False, 'unknown': True, 'verifiedIdle': []}
        self.native = patch('bridge.integrations.deepseek_recovery._snapshot', side_effect=lambda _: copy.deepcopy(self.state)).start()
        self.probe = patch('bridge.integrations.deepseek_recovery._evidence', side_effect=lambda *a: dict(self.evidence)).start()
        self.quit = patch('bridge.integrations.deepseek_recovery._quit').start()
        self.addCleanup(patch.stopall)

    def preview(self, restart=True):
        return self.recovery.preview({}, self.adapter, restart)

    def confirm(self, preview, **kwargs):
        return self.recovery.confirm({}, self.adapter, {'token': preview['token'], 'confirmed': True, **kwargs})

    def test_unknown_legacy_hosts_require_explicit_acknowledgement(self):
        preview = self.preview()
        self.assertTrue(preview['canRecover'])
        self.assertEqual(preview['backgroundCount'], 2)
        self.assertTrue(preview['requiresUnknownConfirmation'])
        self.assertNotIn('identities', preview)
        self.assertNotIn('executable', preview)
        self.assertNotIn('home', preview)
        with self.assertRaisesRegex(ValueError, '明确确认'):
            self.confirm(preview)
        self.quit.assert_not_called()
        self.assertTrue(self.confirm(preview, acknowledgeUnknown=True))
        self.quit.assert_called_once()
        with self.assertRaisesRegex(ValueError, '已过期'):
            self.confirm(preview, acknowledgeUnknown=True)

    def test_busy_after_preview_always_refuses_even_with_unknown_acknowledged(self):
        preview = self.preview()
        self.evidence['busy'] = True
        with self.assertRaisesRegex(ValueError, '任务运行'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_not_called()

    def test_pid_reuse_tree_or_profile_change_invalidates_confirmation(self):
        for mutation in ('birth', 'new-process', 'home'):
            self.state = snapshot(hosts=(12, 13))
            preview = self.preview()
            if mutation == 'birth': self.state['identities'][12]['start'] = 'reused-pid'
            elif mutation == 'new-process': self.state['identities'][99] = {'start': 'new', 'parent': 1, 'command': 'new'}
            else: self.state['home'] = '/other/profile'
            with self.assertRaisesRegex(ValueError, '进程已变化'):
                self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_not_called()

    def test_idle_hosts_can_exit_without_unknown_ack_and_mode_is_bound(self):
        self.evidence = {'busy': False, 'unknown': False, 'verifiedIdle': [12, 13]}
        preview = self.preview(False)
        self.assertFalse(self.confirm(preview, restart=True))
        self.quit.assert_called_once()

    def test_new_unknown_state_requires_a_new_preview(self):
        self.evidence['unknown'] = False
        preview = self.preview()
        self.evidence['unknown'] = True
        with self.assertRaisesRegex(ValueError, '重新检查'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_not_called()

    def test_foreign_or_unrecognized_process_cannot_be_confirmed(self):
        self.state = snapshot(unknown=(19,))
        preview = self.preview()
        self.assertFalse(preview['canRecover'])
        with self.assertRaisesRegex(ValueError, '其他或无法识别'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_not_called()

    def test_unverified_connector_cannot_issue_recovery_token(self):
        self.adapter.discover_existing.side_effect = ValueError('已有 Harness 接入无法验证')
        with self.assertRaises(ValueError): self.preview()
        self.assertIsNone(self.recovery.pending)
        self.native.assert_not_called()


class RecoveryEvidenceTests(unittest.TestCase):
    def test_each_host_requires_complete_native_evidence(self):
        def query(adapter, port, action):
            if action == 'status': return {'connected': True}
            if port == 112: return {'bridgeRevision': 3, 'complete': True, 'sessions': []}
            raise ValueError('inactive context')
        with patch('bridge.integrations.deepseek_recovery._ports', side_effect=lambda pid: [100+pid]), \
                patch('bridge.integrations.deepseek_recovery._query', side_effect=query):
            result = _evidence(Mock(), snapshot(hosts=(12, 13)))
        self.assertFalse(result['busy'])
        self.assertTrue(result['unknown'])
        self.assertEqual(result['verifiedIdle'], [12])

    def test_legacy_list_can_prove_busy_but_not_idle(self):
        for status in ('idle', 'active'):
            def query(adapter, port, action):
                if action == 'status': return {'connected': True, 'bridgeRevision': 2}
                if action == 'lifecycle': raise ValueError('unsupported')
                return {'sessions': [{'status': status, 'runtimeKnown': True}]}
            with patch('bridge.integrations.deepseek_recovery._ports', return_value=[112]), \
                    patch('bridge.integrations.deepseek_recovery._query', side_effect=query):
                result = _evidence(Mock(), snapshot())
            self.assertEqual(result['busy'], status == 'active')
            self.assertTrue(result['unknown'])

    def test_process_snapshot_rejects_disappearing_identity(self):
        app = Mock(executable=Path('/fixture/app'), home=Path('/fixture/home'))
        with patch('bridge.integrations.deepseek_recovery.inspect_client', return_value=snapshot()['state']), \
                patch('bridge.integrations.deepseek_recovery._app', return_value=app), \
                patch('bridge.integrations.deepseek_recovery._identities', return_value={}), \
                patch('bridge.integrations.deepseek_recovery._commands', return_value={}):
            with self.assertRaisesRegex(ValueError, '进程已变化'):
                _snapshot({})


class RecoveryQuitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.before = snapshot(hosts=(12, 13))
        self.commands = {pid: 'fixture-'+str(pid) for pid in self.before['identities']}
        for pid, identity in self.before['identities'].items():
            identity['command'] = hashlib.sha256(self.commands[pid].encode()).hexdigest()
        self.app = Mock(executable=Path(self.before['executable']), home=Path(self.before['home']))

    def sample(self, states, identities, commands, *, gui_allowed=True):
        with patch('bridge.integrations.deepseek_recovery._app', return_value=self.app), \
                patch('bridge.integrations.deepseek_recovery.inspect_client', side_effect=states), \
                patch('bridge.integrations.deepseek_recovery._identities', side_effect=identities), \
                patch('bridge.integrations.deepseek_recovery._commands', side_effect=commands):
            return _quitting_snapshot({}, self.before, gui_allowed=gui_allowed)

    def test_several_confirmed_exits_can_be_resampled(self):
        orphans = snapshot(gui=False, hosts=(12, 13))
        last = snapshot(gui=False, hosts=(13,))
        result = self.sample([self.before['state'], orphans['state'], last['state']],
            [orphans['identities'], last['identities'], last['identities']],
            [{pid: self.commands[pid] for pid in (12, 13)}, {13: self.commands[13]}, {13: self.commands[13]}])
        self.assertEqual(result['state']['pids'], [13])
        self.assertEqual(result['identities'][13]['parent'], 1)

    def test_missing_identity_or_command_for_live_process_is_rejected(self):
        for field in ('identity', 'command', 'empty-command'):
            with self.subTest(field=field):
                identities = copy.deepcopy(self.before['identities'])
                commands = dict(self.commands)
                if field == 'identity': identities.pop(11)
                elif field == 'command': commands.pop(11)
                else: commands[11] = ''
                with self.assertRaisesRegex(ValueError, '进程已变化'):
                    self.sample([self.before['state'], self.before['state']], [identities], [commands])

    def test_new_pid_during_resample_is_never_ignored(self):
        orphans = snapshot(gui=False, hosts=(12, 13))
        for fresh in (snapshot(gui=False, hosts=(12, 13, 14)), snapshot(gui=False, hosts=(13, 14))):
            with self.subTest(pids=fresh['state']['pids']), self.assertRaisesRegex(ValueError, '进程已变化'):
                self.sample([self.before['state'], fresh['state']], [orphans['identities']],
                    [{pid: self.commands[pid] for pid in (12, 13)}])

    def test_observed_pid_reuse_or_command_change_is_rejected_before_retry(self):
        for field in ('start', 'command'):
            with self.subTest(field=field):
                orphans = snapshot(gui=False, hosts=(12, 13))
                commands = {pid: self.commands[pid] for pid in (12, 13)}
                if field == 'start': orphans['identities'][12]['start'] = 'reused-pid'
                else: commands[12] = 'unknown-process'
                with self.assertRaisesRegex(ValueError, '进程已变化'):
                    self.sample([self.before['state']], [orphans['identities']], [commands])

    def test_reappearing_gui_is_rejected_before_host_cleanup(self):
        orphans = snapshot(gui=False, hosts=(12, 13))
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            self.sample([orphans['state'], snapshot(hosts=(13,))['state']],
                [{13: orphans['identities'][13]}], [{13: self.commands[13]}], gui_allowed=False)
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            self.sample([self.before['state']], [], [], gui_allowed=False)

    def test_new_pid_in_initial_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            self.sample([snapshot(hosts=(12, 13, 14))['state']], [], [])


class RecoveryQuitTests(unittest.TestCase):
    def setUp(self):
        self.before = snapshot(hosts=(12, 13))
        self.orphans = snapshot(gui=False, hosts=(12, 13))
        self.app = Mock(executable=Path(self.before['executable']))

    def test_normal_exit_during_snapshot_can_shrink_approved_process_set(self):
        expected = copy.deepcopy(self.before)
        for pid, identity in expected['identities'].items():
            identity['command'] = hashlib.sha256(('fixture-'+str(pid)).encode()).hexdigest()
        alive, inventories = {12, 13}, []

        def inventory(_):
            state = snapshot(gui=False, hosts=tuple(sorted(alive)))['state']
            if not inventories: state = copy.deepcopy(expected['state'])
            inventories.append(state)
            return state

        def identities(pids):
            return {pid: {'start': 'same-start', 'parent': 1} for pid in pids if pid in alive}

        with patch('bridge.integrations.deepseek_recovery.sys.platform', 'darwin'), \
                patch('bridge.integrations.deepseek_recovery._same_processes', return_value=expected), \
                patch('bridge.integrations.deepseek_recovery.inspect_client', side_effect=inventory), \
                patch('bridge.integrations.deepseek_recovery._identities', side_effect=identities), \
                patch('bridge.integrations.deepseek_recovery._commands', side_effect=lambda app, pids: {
                    pid: 'fixture-'+str(pid) for pid in pids if pid in alive}), \
                patch('bridge.integrations.deepseek_recovery._app', return_value=Mock(
                    executable=Path(expected['executable']), home=Path(expected['home']))), \
                patch('bridge.integrations.deepseek_recovery.subprocess.run'), \
                patch('bridge.integrations.deepseek_recovery.os.kill', side_effect=lambda pid, sig: alive.remove(pid)) as kill:
            _quit({}, expected)
        self.assertEqual([call.args for call in kill.call_args_list], [(12, signal.SIGTERM), (13, signal.SIGTERM)])
        self.assertFalse(alive)

    def test_gui_quit_completes_before_background_sigterm(self):
        calls = []
        with patch('bridge.integrations.deepseek_recovery.sys.platform', 'darwin'), \
                patch('bridge.integrations.deepseek_recovery._same_processes', return_value=self.before), \
                patch('bridge.integrations.deepseek_recovery._quitting_snapshot', return_value=self.orphans), \
                patch('bridge.integrations.deepseek_recovery._app', return_value=self.app), \
                patch('bridge.integrations.deepseek_recovery.inspect_client', return_value={'running': False}), \
                patch('bridge.integrations.deepseek_recovery.subprocess.run', side_effect=lambda *a, **kw: calls.append('quit')), \
                patch('bridge.integrations.deepseek_recovery.os.kill', side_effect=lambda pid, sig: calls.append((pid, sig))):
            _quit({}, self.before)
        self.assertEqual(calls, ['quit', (12, signal.SIGTERM), (13, signal.SIGTERM)])

    def test_reopening_gui_or_new_host_before_cleanup_never_signals_old_hosts(self):
        for resumed in (snapshot(hosts=(12, 13)), snapshot(gui=False, hosts=(12, 13, 14))):
            with patch('bridge.integrations.deepseek_recovery.sys.platform', 'darwin'), \
                    patch('bridge.integrations.deepseek_recovery._same_processes', return_value=self.before), \
                    patch('bridge.integrations.deepseek_recovery._quitting_snapshot', side_effect=[self.orphans, resumed]), \
                    patch('bridge.integrations.deepseek_recovery._app', return_value=self.app), \
                    patch('bridge.integrations.deepseek_recovery.subprocess.run'), \
                    patch('bridge.integrations.deepseek_recovery.os.kill') as kill:
                with self.assertRaisesRegex(ValueError, '进程已变化'):
                    _quit({}, self.before)
                kill.assert_not_called()

    def test_gui_refusing_quit_never_terminates_host(self):
        with patch('bridge.integrations.deepseek_recovery.sys.platform', 'darwin'), \
                patch('bridge.integrations.deepseek_recovery._same_processes', return_value=self.before), \
                patch('bridge.integrations.deepseek_recovery._quitting_snapshot', return_value=self.before), \
                patch('bridge.integrations.deepseek_recovery._app', return_value=self.app), \
                patch('bridge.integrations.deepseek_recovery.subprocess.run'), \
                patch('bridge.integrations.deepseek_recovery.time.monotonic', side_effect=[0, 16]), \
                patch('bridge.integrations.deepseek_recovery.os.kill') as kill:
            with self.assertRaisesRegex(ValueError, '未终止后台任务'):
                _quit({}, self.before)
            kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
