"""Recovery tests use process snapshots only; no native process is signalled."""
import copy
import hashlib
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.deepseek.adapter import DeepSeek
from bridge.clients.deepseek.setup import MARKER
from bridge.clients.deepseek.recovery import DeepSeekRecovery, _evidence, _quit, _quitting_snapshot, _snapshot


def snapshot(*, gui=True, hosts=(12,), unknown=()):
    main = [11] if gui else []
    pids = main+list(hosts)+list(unknown)
    return {'executable': str(Path('/Applications/Fixture.app/Contents/MacOS/Fixture')),
            'home': str(Path('/fixture/home')),
            'state': {'running': bool(pids), 'pids': pids, 'mainPids': main, 'runtimePids': list(hosts),
                      'unknown': len(hosts) > 1 or bool(unknown)},
            'identities': {pid: {'start': 'same-start', 'parent': 11 if pid in hosts and gui else 1,
                                 'command': 'fixture-command-'+str(pid)} for pid in pids},
            'unrecognized': list(unknown)}


class RecoveryConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.recovery = DeepSeekRecovery()
        self.adapter = Mock()
        self.adapter.discover_existing.return_value = {'installed': True}
        self.state = snapshot(hosts=(12, 13))
        self.evidence = {'busy': False, 'unknown': True, 'verifiedIdle': []}
        self.native = patch('bridge.clients.deepseek.recovery._snapshot', side_effect=lambda _: copy.deepcopy(self.state)).start()
        self.probe = patch('bridge.clients.deepseek.recovery._evidence', side_effect=lambda *a: dict(self.evidence)).start()
        self.quit = patch('bridge.clients.deepseek.recovery._quit').start()
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

    def test_broken_connector_does_not_block_identity_preview_or_probe_untrusted_rpc(self):
        self.adapter.discover_existing.side_effect = ValueError('已有 Harness 接入无法验证')
        preview = self.preview(False)
        self.assertTrue(preview['unknown'])
        self.assertFalse(preview['canRecover'])
        self.assertEqual(preview['backgroundCount'], 2)
        self.assertIn('尚未提交退出', preview['message'])
        self.native.assert_called_once()
        self.probe.assert_not_called()
        with self.assertRaisesRegex(ValueError, '接入配置无法验证'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_not_called()

    def test_unavailable_lazy_adapter_keeps_identity_preview(self):
        preview = self.recovery.preview({}, None, False)
        self.assertTrue(preview['unknown'])
        self.assertFalse(preview['canRecover'])
        self.native.assert_called_once()
        self.probe.assert_not_called()
        self.quit.assert_not_called()

    def test_identity_failure_is_not_hidden_by_broken_connector(self):
        self.native.side_effect = ValueError('Harness 进程已变化')
        self.adapter.discover_existing.side_effect = ValueError('broken connector')
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            self.preview()
        self.adapter.discover_existing.assert_not_called()
        self.probe.assert_not_called()

    def test_missing_connector_is_unknown_not_idle_or_a_trusted_query(self):
        self.adapter.discover_existing.return_value = {'installed': False}
        preview = self.preview(False)
        self.assertTrue(preview['unknown'])
        self.assertFalse(preview['canRecover'])
        self.probe.assert_not_called()

    def test_repaired_connector_requires_new_preview(self):
        self.adapter.discover_existing.side_effect = ValueError('broken connector')
        preview = self.preview(False)
        self.adapter.discover_existing.side_effect = None
        self.adapter.discover_existing.return_value = {'installed': True}
        with self.assertRaisesRegex(ValueError, '接入配置无法验证'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_not_called()
        preview = self.preview(False)
        self.assertFalse(self.confirm(preview, acknowledgeUnknown=True))
        self.quit.assert_called_once()

    def test_connector_failure_after_preview_never_dispatches(self):
        preview = self.preview(False)
        self.adapter.discover_existing.side_effect = ValueError('broken connector')
        with self.assertRaisesRegex(ValueError, '接入配置无法验证'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_not_called()

    def test_unknown_native_result_cannot_reuse_confirmation(self):
        preview = self.preview(False)
        self.quit.side_effect = ValueError('native reply unknown')
        with self.assertRaisesRegex(ValueError, 'native reply unknown'):
            self.confirm(preview, acknowledgeUnknown=True)
        with self.assertRaisesRegex(ValueError, '已过期'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.quit.assert_called_once()


class BrokenConnectorFileTests(unittest.TestCase):
    def test_real_orphan_insert_keeps_identity_visible_and_configuration_unchanged(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as temp:
            root=Path(temp);home=root/'home';profile=home/'profiles/desktop';profile.mkdir(parents=True)
            source=root/'removed-build/desktop-sessions/deepseek'
            adapter=DeepSeek(source,home)
            file=profile/'cordis.patch.yml';file.write_text(MARKER+'\n'+adapter._line()+'\n')
            before=file.read_bytes()
            with patch('bridge.clients.deepseek.recovery._snapshot', return_value=snapshot()) as identities, \
                 patch('bridge.clients.deepseek.recovery._query', side_effect=AssertionError('untrusted RPC')) as query:
                preview=DeepSeekRecovery().preview({},adapter,False)
            self.assertTrue(preview['unknown'])
            self.assertFalse(preview['canRecover'])
            identities.assert_called_once()
            query.assert_not_called()
            self.assertEqual(file.read_bytes(),before)
            self.assertFalse(source.exists())


class RecoveryEvidenceTests(unittest.TestCase):
    def test_non_http_runtime_port_does_not_abort_other_port_evidence(self):
        from http.client import BadStatusLine
        def query(adapter, port, action):
            if port == 111:
                raise BadStatusLine('not an HTTP endpoint')
            if action == 'status': return {'connected': True}
            return {'bridgeRevision': 9, 'complete': True, 'sessions': []}
        with patch('bridge.clients.deepseek.recovery._ports', return_value=[111, 112]), \
                patch('bridge.clients.deepseek.recovery._query', side_effect=query):
            self.assertEqual(_evidence(Mock(), snapshot()), {'busy': False, 'unknown': False, 'verifiedIdle': [12]})

    def test_only_non_http_ports_remain_unknown(self):
        from http.client import BadStatusLine
        with patch('bridge.clients.deepseek.recovery._ports', return_value=[111]), \
                patch('bridge.clients.deepseek.recovery._query', side_effect=BadStatusLine('not HTTP')):
            self.assertEqual(_evidence(Mock(), snapshot()), {'busy': False, 'unknown': True, 'verifiedIdle': []})

    def test_each_host_requires_complete_native_evidence(self):
        def query(adapter, port, action):
            if action == 'status': return {'connected': True}
            if port == 112: return {'bridgeRevision': 3, 'complete': True, 'sessions': []}
            raise ValueError('inactive context')
        with patch('bridge.clients.deepseek.recovery._ports', side_effect=lambda pid: [100+pid]), \
                patch('bridge.clients.deepseek.recovery._query', side_effect=query):
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
            with patch('bridge.clients.deepseek.recovery._ports', return_value=[112]), \
                    patch('bridge.clients.deepseek.recovery._query', side_effect=query):
                result = _evidence(Mock(), snapshot())
            self.assertEqual(result['busy'], status == 'active')
            self.assertTrue(result['unknown'])

    def test_process_snapshot_rejects_disappearing_identity(self):
        app = Mock(executable=Path('/fixture/app'), home=Path('/fixture/home'))
        with patch('bridge.clients.deepseek.recovery.inspect_client', return_value=snapshot()['state']), \
                patch('bridge.clients.deepseek.recovery._app', return_value=app), \
                patch('bridge.clients.deepseek.recovery._identities', return_value={}), \
                patch('bridge.clients.deepseek.recovery._commands', return_value={}):
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
        with patch('bridge.clients.deepseek.recovery._app', return_value=self.app), \
                patch('bridge.clients.deepseek.recovery.inspect_client', side_effect=states), \
                patch('bridge.clients.deepseek.recovery._identities', side_effect=identities), \
                patch('bridge.clients.deepseek.recovery._commands', side_effect=commands):
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

    @patch('bridge.clients.deepseek.recovery.sys.platform', 'win32')
    def test_windows_recovery_uses_verified_native_quit_without_window_or_taskkill_fallback(self):
        adapter=Mock()
        with patch('bridge.clients.deepseek.recovery._same_processes', return_value=self.before), \
             patch('bridge.clients.lifecycle.stop_deepseek') as native, \
             patch('bridge.clients.deepseek.recovery.subprocess.run') as process, \
             patch('bridge.clients.deepseek.recovery.os.kill') as signal_process:
            _quit({}, self.before, adapter=adapter)
        native.assert_called_once_with({}, adapter, state=self.before['state'])
        process.assert_not_called()
        signal_process.assert_not_called()

    @patch('bridge.clients.deepseek.recovery.sys.platform', 'win32')
    def test_windows_missing_native_protocol_never_falls_back_or_forces(self):
        with patch('bridge.clients.deepseek.recovery._same_processes', return_value=self.before), \
             patch('bridge.clients.lifecycle.stop_deepseek', side_effect=ValueError('no verified native quit')) as native, \
             patch('bridge.clients.deepseek.recovery.subprocess.run') as process, \
             patch('bridge.clients.deepseek.recovery.os.kill') as signal_process:
            with self.assertRaisesRegex(ValueError, 'no verified native quit'):
                _quit({}, self.before, adapter=Mock())
        native.assert_called_once()
        process.assert_not_called()
        signal_process.assert_not_called()

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

        with patch('bridge.clients.deepseek.recovery.sys.platform', 'darwin'), \
                patch('bridge.clients.deepseek.recovery._same_processes', return_value=expected), \
                patch('bridge.clients.deepseek.recovery.inspect_client', side_effect=inventory), \
                patch('bridge.clients.deepseek.recovery._identities', side_effect=identities), \
                patch('bridge.clients.deepseek.recovery._commands', side_effect=lambda app, pids: {
                    pid: 'fixture-'+str(pid) for pid in pids if pid in alive}), \
                patch('bridge.clients.deepseek.recovery._app', return_value=Mock(
                    executable=Path(expected['executable']), home=Path(expected['home']))), \
                patch('bridge.clients.deepseek.recovery.subprocess.run'), \
                patch('bridge.clients.deepseek.recovery.os.kill', side_effect=lambda pid, sig: alive.remove(pid)) as kill:
            _quit({}, expected)
        self.assertEqual([call.args for call in kill.call_args_list], [(12, signal.SIGTERM), (13, signal.SIGTERM)])
        self.assertFalse(alive)

    def test_gui_quit_completes_before_background_sigterm(self):
        calls = []
        with patch('bridge.clients.deepseek.recovery.sys.platform', 'darwin'), \
                patch('bridge.clients.deepseek.recovery._same_processes', return_value=self.before), \
                patch('bridge.clients.deepseek.recovery._quitting_snapshot', return_value=self.orphans), \
                patch('bridge.clients.deepseek.recovery._app', return_value=self.app), \
                patch('bridge.clients.deepseek.recovery.inspect_client', return_value={'running': False}), \
                patch('bridge.clients.deepseek.recovery.subprocess.run', side_effect=lambda *a, **kw: calls.append('quit')), \
                patch('bridge.clients.deepseek.recovery.os.kill', side_effect=lambda pid, sig: calls.append((pid, sig))):
            _quit({}, self.before)
        self.assertEqual(calls, ['quit', (12, signal.SIGTERM), (13, signal.SIGTERM)])

    def test_reopening_gui_or_new_host_before_cleanup_never_signals_old_hosts(self):
        for resumed in (snapshot(hosts=(12, 13)), snapshot(gui=False, hosts=(12, 13, 14))):
            with patch('bridge.clients.deepseek.recovery.sys.platform', 'darwin'), \
                    patch('bridge.clients.deepseek.recovery._same_processes', return_value=self.before), \
                    patch('bridge.clients.deepseek.recovery._quitting_snapshot', side_effect=[self.orphans, resumed]), \
                    patch('bridge.clients.deepseek.recovery._app', return_value=self.app), \
                    patch('bridge.clients.deepseek.recovery.subprocess.run'), \
                    patch('bridge.clients.deepseek.recovery.os.kill') as kill:
                with self.assertRaisesRegex(ValueError, '进程已变化'):
                    _quit({}, self.before)
                kill.assert_not_called()

    def test_gui_refusing_quit_never_terminates_host(self):
        with patch('bridge.clients.deepseek.recovery.sys.platform', 'darwin'), \
                patch('bridge.clients.deepseek.recovery._same_processes', return_value=self.before), \
                patch('bridge.clients.deepseek.recovery._quitting_snapshot', return_value=self.before), \
                patch('bridge.clients.deepseek.recovery._app', return_value=self.app), \
                patch('bridge.clients.deepseek.recovery.subprocess.run'), \
                patch('bridge.clients.deepseek.recovery.time.monotonic', side_effect=[0, 16]), \
                patch('bridge.clients.deepseek.recovery.os.kill') as kill:
            with self.assertRaisesRegex(ValueError, '未终止后台任务'):
                _quit({}, self.before)
            kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
