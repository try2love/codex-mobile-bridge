"""Disconnected Claude controls use only mocked native apps and private fixtures."""
import copy
import json
import unittest
from unittest.mock import patch

import test_client_lifecycle as lifecycle


class ClaudeControls(unittest.TestCase):
    def setUp(self):
        fixture = lifecycle.ClientLifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.manager, self.claude = fixture, fixture.manager, fixture.claude
        self.claude.directory = fixture.root/'claude-control'
        self.claude.directory.mkdir()
        (self.claude.directory/'connection.json').write_text('{"token":"fixture"}')
        self.claude.discovery = {'installed': True, 'automaticConnection': 'native-console'}
        self.claude.status.return_value = {'connected': False, 'setupState': 'needs-screen-saver',
                                         'reason': '请先退出屏幕保护程序，再继续连接 Claude'}
        self.state = {'running': True, 'pids': [11], 'mainPids': [11], 'runtimePids': [], 'unknown': False}
        previous = fixture.inspect.side_effect
        fixture.inspect.side_effect = lambda descriptor: copy.deepcopy(self.state) if descriptor['id'] == 'claude' else previous(descriptor)
        self.snapshot = {'executable': str(fixture.root/'claude'), 'home': str(fixture.root),
                         'state': self.state, 'identities': {11: {'start': 'fixture-start', 'command': 'fixture-hash'}},
                         'unrecognized': []}
        native = patch('bridge.clients.deepseek.recovery._snapshot', side_effect=lambda _: copy.deepcopy(self.snapshot))
        native.start(); self.addCleanup(native.stop)
        platform = patch('sys.platform', 'darwin')
        platform.start(); self.addCleanup(platform.stop)

    def preview(self):
        return self.manager.control({'action': 'claude-quit-preview'})

    def confirm(self, preview, **extra):
        return self.manager.control({'action': 'claude-quit-confirm', 'token': preview['token'],
                                     'confirmed': True, **extra})

    def test_disconnected_toggle_stays_protected_and_keeps_enabled(self):
        with self.assertRaisesRegex(ValueError, '无法确认'):
            self.manager.toggle_client({'provider': 'claude', 'enabled': False})
        self.fixture.stop.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))

    def test_reconnect_reuses_existing_setup_without_start_stop_or_enabling(self):
        result = self.manager.reconnect_claude()
        self.claude.connect.assert_called_once_with(existing_only=True)
        self.fixture.stop.assert_not_called(); self.fixture.launch.assert_not_called()
        self.claude.cancel.assert_not_called()
        row = next(row for row in result['clients'] if row['id'] == 'claude')
        self.assertFalse(row['connected']); self.assertTrue(row['enabled'])
        self.assertEqual(row['setupStatus'], 'needs-screen-saver')
        self.assertEqual(row['reason'], self.claude.status()['reason'])
        self.assertTrue(row['reconnectSupported']); self.assertTrue(row['canReconnect'])

    def test_reconnect_rejects_disabled_stopped_ambiguous_and_unprepared_clients(self):
        self.manager.config['enabled']['claude'] = False
        with self.assertRaisesRegex(ValueError, '应用已关闭'): self.manager.reconnect_claude()
        self.manager.config['enabled']['claude'] = True
        self.state.update(running=False, pids=[], mainPids=[])
        with self.assertRaisesRegex(ValueError, '打开 Claude'): self.manager.reconnect_claude()
        self.state.update(running=True, pids=[11], mainPids=[11], unknown=True)
        with self.assertRaisesRegex(ValueError, '进程'): self.manager.reconnect_claude()
        self.state['unknown'] = False
        (self.claude.directory/'connection.json').unlink()
        with self.assertRaisesRegex(ValueError, '电脑端'): self.manager.reconnect_claude()
        self.claude.connect.assert_not_called(); self.fixture.launch.assert_not_called()

    def test_connected_reconnect_is_idempotent_even_when_tasks_are_busy(self):
        self.claude.status.return_value = {'connected': True}
        self.claude.call.side_effect = lambda *_: {'complete': True, 'sessions': [
            {'id': 'busy', 'status': 'active', 'runtimeKnown': True}], 'models': [{'id': 'fixture'}]}
        self.manager.reconnect_claude()
        self.claude.check_connection.assert_called_once_with()
        self.claude.connect.assert_not_called(); self.fixture.stop.assert_not_called()

    def test_heartbeat_without_rpc_cannot_report_a_healthy_reconnection(self):
        self.claude.status.return_value = {'connected': True}
        self.claude.check_connection.side_effect = ValueError('Claude 桌面连接暂未响应，请检查电脑端状态后重试；未重启客户端')
        with self.assertRaisesRegex(ValueError, '未响应'):
            self.manager.reconnect_claude()
        self.claude.connect.assert_not_called(); self.fixture.stop.assert_not_called()
        self.fixture.launch.assert_not_called()

    def test_gateway_stopped_cannot_accept_web_reconnect(self):
        self.manager.gateway_running = False
        with self.assertRaisesRegex(ValueError, '启动网关'): self.manager.reconnect_claude()
        self.claude.connect.assert_not_called()

    def test_disconnected_quit_requires_fresh_explicit_unknown_confirmation(self):
        preview = self.preview()
        self.assertTrue(preview['canQuit']); self.assertTrue(preview['requiresUnknownConfirmation'])
        self.fixture.stop.assert_not_called(); self.claude.cancel.assert_not_called()
        with self.assertRaisesRegex(ValueError, '可能中断任务'): self.confirm(preview)
        self.fixture.stop.assert_not_called()
        result = self.confirm(preview, acknowledgeUnknown=True)
        self.fixture.stop.assert_called_once()
        self.claude.cancel.assert_any_call(persist=False); self.claude.cancel.assert_any_call()
        self.assertFalse(self.manager.enabled('claude'))
        self.assertFalse(next(row for row in result['clients'] if row['id'] == 'claude')['enabled'])
        self.assertFalse(json.loads(self.manager.config_path.read_text())['enabled']['claude'])
        self.fixture.launch.assert_not_called(); self.claude.connect.assert_not_called()
        with self.assertRaisesRegex(ValueError, '过期'): self.confirm(preview, acknowledgeUnknown=True)

    def test_busy_tasks_still_block_explicit_quit_even_with_confirmation(self):
        preview = self.preview()
        self.claude.status.return_value = {'connected': True}
        self.claude.call.side_effect = lambda *_: {'complete': False, 'sessions': [
            {'id': 'running', 'status': 'active', 'runtimeKnown': True}]}
        with self.assertRaisesRegex(ValueError, '任务运行或等待'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.fixture.stop.assert_not_called(); self.claude.cancel.assert_not_called()

    def test_process_identity_change_or_expiry_cannot_use_old_quit_confirmation(self):
        preview = self.preview()
        self.snapshot['identities'][11]['start'] = 'replacement'
        with self.assertRaisesRegex(ValueError, '进程已变化'): self.confirm(preview, acknowledgeUnknown=True)
        preview = self.preview()
        with patch('bridge.clients.manager.time.monotonic', return_value=10**12):
            with self.assertRaisesRegex(ValueError, '过期'): self.confirm(preview, acknowledgeUnknown=True)
        self.fixture.stop.assert_not_called()

    def test_new_unknown_task_state_needs_a_new_warning_preview(self):
        self.claude.status.return_value = {'connected': True}
        preview = self.preview()
        self.assertFalse(preview['requiresUnknownConfirmation'])
        self.claude.status.return_value = {'connected': False}
        with self.assertRaisesRegex(ValueError, '重新检查并明确确认'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.fixture.stop.assert_not_called(); self.claude.cancel.assert_not_called()

    def test_process_changed_while_cancelling_monitor_is_never_quit(self):
        preview = self.preview()
        def cancel(**_): self.snapshot['identities'][11]['start'] = 'replacement'
        self.claude.cancel.side_effect = cancel
        with self.assertRaisesRegex(ValueError, '进程已变化'):
            self.confirm(preview, acknowledgeUnknown=True)
        self.fixture.stop.assert_not_called()
        self.assertTrue(self.manager.enabled('claude'))

    def test_failed_native_quit_does_not_disable_or_restart_claude(self):
        self.fixture.stop.side_effect = ValueError('native refused exit')
        with self.assertRaisesRegex(ValueError, 'native refused'):
            self.confirm(self.preview(), acknowledgeUnknown=True)
        self.assertTrue(self.manager.enabled('claude'))
        self.claude.cancel.assert_called_once_with(persist=False)
        self.claude.connect.assert_not_called(); self.fixture.launch.assert_not_called()

    def test_unknown_processes_and_linux_never_offer_override_quit(self):
        self.state['unknown'] = True
        self.assertFalse(self.preview()['canQuit'])
        with self.assertRaisesRegex(ValueError, '进程'): self.confirm(self.preview(), acknowledgeUnknown=True)
        self.state['unknown'] = False
        with patch('sys.platform', 'linux'):
            self.assertFalse(self.preview()['canQuit'])
            with self.assertRaisesRegex(ValueError, '电脑端退出'):
                self.confirm(self.preview(), acknowledgeUnknown=True)
        self.fixture.stop.assert_not_called(); self.claude.cancel.assert_not_called()


if __name__ == '__main__': unittest.main()
