"""Real worker scheduling: outbound latency must not hold the companion inbox."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.app.service import LiveSession
from bridge.clients.manager import DesktopSessions
from bridge.features.notifications.channels import Notifications, save_settings

ROOT = Path(__file__).resolve().parents[1]


class DeliveryIsolation(unittest.TestCase):
    def test_slow_external_channel_does_not_block_first_or_next_mobile_event(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            session = LiveSession('chat')
            session.connected = True
            session.state = {'requests': [{'id': 'one', 'method': 'item/commandExecution/requestApproval'}]}
            source = Mock(notification_event=threading.Event())
            source.notification_candidates.return_value = [{'id': 'chat', 'host': 'local'}]
            source.notification_updates.return_value = [{'id': 'chat', 'host': 'local'}]
            source.for_host.return_value = source
            source.notification_session.return_value = session
            source.release_notification_session.return_value = False
            save_settings(directory, {'mobileEnabled': True, 'enabled': True, 'topic': 'fixture'})
            manager = Notifications(source, directory)
            entered, release = threading.Event(), threading.Event()
            def blocked(*args):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('fixture not released')
            with patch('bridge.features.notifications.channels.publish', side_effect=blocked):
                manager.start()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(manager.mobile.checkpoint()['cursor'], 1,
                                     'first mobile event waited for outbound HTTP')
                    with session.condition:
                        session.state['requests'].append({'id': 'two', 'method': 'item/commandExecution/requestApproval'})
                    version = manager.mobile.version()
                    source.notification_event.set()
                    manager.mobile.wait(version, 2)
                    self.assertEqual(manager.mobile.checkpoint()['cursor'], 2,
                                     'later mobile event waited for outbound HTTP')
                finally:
                    release.set()
                    manager.close()

    def test_claude_cli_is_scanned_without_a_desktop_connection(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            manager = DesktopSessions(directory, gateway_running=True)
            try:
                adapter = manager.adapters['claude']
                manager.config['enabled'] = {'claude': True, 'deepseek': False}
                with patch.object(adapter, 'status', return_value={'connected': False, 'cliAvailable': True}), \
                     patch.object(adapter, 'call', return_value={'connected': True, 'sessions': [{'id': 'cli-chat', 'backend': 'cli'}]}) as call:
                    self.assertEqual([r['id'] for r in manager.notification_rows()], ['cli-chat'])
                    call.assert_called_once_with('list', None, {})
            finally:
                manager.close()

    def test_dsh_completion_survives_old_anchor_leaving_latest_history_page(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            manager = Notifications(Mock(), directory)
            try:
                manager.defaults({'requests': True, 'completion': True})
                row = {'id': 'dsh-chat', 'host': 'desktop:deepseek'}
                manager._completion_pending(row, [{'turnId': '1', 'status': 'completed', 'sequence': 10}], 'mobile')
                keys = manager._completion_pending(row, [{'turnId': '2', 'status': 'completed', 'sequence': 100}], 'mobile')
                self.assertEqual(len(keys), 1)
                # An older page is not a new completion.
                manager.ledger[keys[0]] = {'delivered': True}
                self.assertEqual(manager._completion_pending(row, [{'turnId': '0', 'status': 'completed', 'sequence': 1}], 'mobile'), [])
                self.assertEqual(manager._completion_pending(row, [{'turnId': '2', 'status': 'completed', 'sequence': 100}], 'mobile'), [])
            finally:
                manager.close()

    def test_queued_deliveries_recheck_destination_policy_and_closed_state(self):
        for change in ('destination', 'disabled', 'policy', 'closed'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
                source = Mock()
                source.notification_candidates.return_value = []
                source.notification_updates.return_value = []
                manager = Notifications(source, directory)
                config = save_settings(directory, {'enabled': True, 'topic': 'fixture'})
                row = {'id': 'chat', 'host': 'local'}
                target = manager._targets(config)['ntfy']
                manager._dispatch(config, row, 'private title', 'ntfy', target, ['test-key'], False)
                if change == 'destination':
                    save_settings(directory, {'topic': 'replacement'})
                elif change == 'disabled':
                    save_settings(directory, {'enabled': False})
                elif change == 'policy':
                    manager.defaults({'requests': False, 'completion': False})
                else:
                    manager.close()
                with patch('bridge.features.notifications.channels.publish') as send:
                    manager.deliver_pending()
                    send.assert_not_called()
                manager.close()

    def test_native_claude_and_dsh_events_reach_both_channels_through_manager(self):
        import asyncio
        import io
        import json
        from contextlib import ExitStack
        for provider in ('claude', 'deepseek'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory, ExitStack() as stack:
                desktop = DesktopSessions(directory, gateway_running=True)
                stack.callback(desktop.close)
                desktop.config['enabled'] = {'claude': provider == 'claude', 'deepseek': provider == 'deepseek'}
                adapter = desktop.adapters[provider]
                native = {'sessionId': 'native', 'turnRunning': False, 'lastActivityAt': 1, 'pendingToolPermissions': []}
                row = {'id': 'native', 'status': 'idle', 'updatedAt': 1, 'requests': []}
                records = []
                if provider == 'claude':
                    # Model identity must use the synthetic desktop profile too;
                    # this protocol fixture does not require an installed app.
                    home = Path(directory)/'claude-home'
                    home.mkdir()
                    desktop.config['discovered'] = {'claude': {
                        'installed': True, 'dataDirectory': str(home),
                        'dataDirectoryExplicit': True}}
                    stack.enter_context(patch.object(desktop._account_store('claude'), '_current',
                                                    return_value=('official', 'fixture-account', None)))
                    adapter.desktop = Mock(connected=True, capabilities={'code': ['getAll', 'mobileList', 'mobileDetail']})
                    async def native_call(kind, method, *args, **kwargs):
                        if method == 'mobileList':
                            return {'code': [dict(native)]}
                        if method == 'mobileDetail':
                            return {'session': dict(native), 'transcript': list(records)}
                        raise AssertionError(method)
                    adapter.desktop.call = native_call
                    stack.enter_context(patch.object(adapter.cli, 'listing', return_value={'connected': False, 'sessions': []}))
                    stack.enter_context(patch.object(adapter.remote_cli, 'listing', return_value={'connected': False, 'sessions': [], 'complete': True}))
                    stack.enter_context(patch.object(adapter, 'call', side_effect=lambda action, sid=None, body=None: asyncio.run(adapter.dispatch(action, sid, body or {}))))
                else:
                    home = Path(directory)/'dsh-home'
                    home.mkdir()
                    adapter.home = home
                    desktop.config['discovered'] = {'deepseek': {'installed': True, 'dataDirectory': str(home)}}
                    adapter.directory.mkdir(parents=True, exist_ok=True)
                    (adapter.directory/'connection.json').write_text('{"token":"fixture"}')
                    (adapter.directory/'endpoint.json').write_text('{"port":32123}')
                    class Opener:
                        def open(self, request, timeout):
                            action = json.loads(request.data)['action']
                            value = {'connected': True}
                            if action == 'list':
                                value['sessions'] = [dict(row)]
                            elif action == 'detail':
                                value.update(session=dict(row), records=list(records), history={'hasMore': True})
                            elif action != 'status':
                                raise AssertionError(action)
                            return io.BytesIO(json.dumps(value).encode())
                    stack.enter_context(patch('bridge.clients.deepseek.adapter.urllib.request.build_opener', return_value=Opener()))
                stack.enter_context(patch.object(desktop, 'scan', side_effect=AssertionError('notification fixture must not scan installed clients')))
                source = Mock()
                source.notification_candidates.return_value = []
                source.notification_updates.return_value = []
                manager = Notifications(source, directory, desktop_sessions=desktop)
                stack.callback(manager.close)
                save_settings(directory, {'enabled': True, 'topic': 'fixture', 'mobileEnabled': True})
                manager.defaults({'requests': True, 'completion': True})
                send = stack.enter_context(patch('bridge.features.notifications.channels.publish'))
                # Native empty history establishes a baseline, never a completion.
                manager.scan()
                self.assertEqual(manager.mobile.checkpoint()['cursor'], 0)
                native.update(lastActivityAt=2, pendingToolPermissions=[{'requestId': 'approval', 'toolName': 'Bash'}])
                row.update(updatedAt=2, requests=[{'id': 'approval'}])
                if provider == 'claude':
                    records.append({'type': 'result', 'uuid': 'result-1', 'subtype': 'success', 'is_error': False, 'num_turns': 1})
                else:
                    records.append({'event': {'seq': 100, 'type': 'turn/end', 'data': {'turn': 1, 'reason': {'kind': 'completed'}}}})
                manager.desktop_poll_at = 0
                manager.scan()
                events = manager.mobile.read()['events']
                self.assertEqual({e['kind'] for e in events}, {'request', 'completion'})
                self.assertEqual({e['host'] for e in events}, {'desktop:' + provider})
                self.assertEqual(send.call_count, 2)
                manager.desktop_poll_at = 0
                manager.scan()
                self.assertEqual(send.call_count, 2)
                self.assertEqual(manager.mobile.checkpoint()['cursor'], 2)
                source.for_host.assert_not_called()

    def test_desktop_approval_queue_waits_for_the_next_native_poll_before_removal(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            source, desktop = Mock(), Mock()
            source.notification_candidates.return_value = []
            source.notification_updates.return_value = []
            desktop.enabled.return_value = True
            row = {'id': 'chat', 'host': 'desktop:deepseek', 'provider': 'deepseek', 'requests': [{'id': 'request'}]}
            desktop.notification_rows.side_effect = lambda: [dict(row)]
            manager = Notifications(source, directory, desktop_sessions=desktop)
            try:
                save_settings(directory, {'enabled': True, 'topic': 'fixture'})
                manager.scan(deliver=False)
                manager.scan(deliver=False)  # Desktop scan is not due yet.
                with patch('bridge.features.notifications.channels.publish') as send:
                    manager.deliver_pending()
                    send.assert_called_once()
                row['requests'] = [{'id': 'next'}]
                manager.desktop_poll_at = 0
                manager.scan(deliver=False)
                row['requests'] = []
                manager.desktop_poll_at = 0
                manager.scan(deliver=False)
                with patch('bridge.features.notifications.channels.publish') as send:
                    manager.deliver_pending()
                    send.assert_not_called()
            finally:
                manager.close()
