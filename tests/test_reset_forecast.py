import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from bridge.features.accounts.reset_forecast import ResetForecast, normalize, retry_after, SOURCE, URL, USER_AGENT
from bridge.features.accounts.monitor import AccountMonitor
from bridge.features.notifications.channels import save_settings

NOW = 1791971100


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def payload(**changes):
    return {'updated_at': iso(NOW), 'last_reset_at': iso(NOW-3600), 'official_signal': None,
            'probabilities': {'rounded_24h': 19, 'rounded_48h': 34}, 'confidence': 'low', **changes}


class ForecastTests(unittest.TestCase):
    def test_refresh_instance_id_is_stable_for_one_gateway_and_changes_after_restart(self):
        first, second = ResetForecast(), ResetForecast()
        self.assertEqual(first.status()['refresh']['instanceId'], first.status()['refresh']['instanceId'])
        self.assertNotEqual(first.status()['refresh']['instanceId'], second.status()['refresh']['instanceId'])
        self.assertEqual(first.status()['refresh']['id'], second.status()['refresh']['id'])

    def test_refresh_status_tracks_running_completion_unchanged_data_and_backoff(self):
        forecast = ResetForecast()
        entered, release = threading.Event(), threading.Event()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {}
        def read(size):
            entered.set()
            release.wait(2)
            return json.dumps(payload()).encode()
        response.read.side_effect = read
        opener = Mock()
        opener.open.return_value = response
        clock = [NOW]
        with patch('bridge.features.accounts.reset_forecast.build_opener', return_value=opener), patch(
                'bridge.features.accounts.reset_forecast.time.time', side_effect=lambda: clock[0]):
            forecast.request(refresh=True)
            self.assertTrue(entered.wait(2))
            running = forecast.status()['refresh']
            self.assertEqual(running['id'], 1)
            self.assertTrue(running['checking'])
            forecast.request(refresh=True)
            self.assertEqual(forecast.status()['refresh']['id'], 1)
            release.set()
            forecast.worker.join(2)
            first = forecast.status()
            self.assertFalse(first['refresh']['checking'])
            self.assertTrue(first['refresh']['changed'])
            self.assertEqual(first['refresh']['finishedAt'], NOW)
            self.assertEqual(first['refresh']['cooldownUntil'], NOW+60)
            clock[0] += 60
            forecast.request(refresh=True)
            forecast.worker.join(2)
            second = forecast.status()
            self.assertFalse(second['refresh']['changed'])
            self.assertEqual(second['updatedAt'], first['updatedAt'])
            self.assertEqual(second['checkedAt'], NOW+60)
            self.assertEqual(second['refresh']['id'], 2)
            clock[0] += 60
            opener.open.side_effect = HTTPError(URL, 429, 'limited', {'Retry-After': '3600'}, io.BytesIO())
            forecast.request(refresh=True)
            forecast.worker.join(2)
            failed = forecast.status()
            self.assertEqual(failed['state'], 'error')
            self.assertFalse(failed['refresh']['checking'])
            self.assertEqual(failed['refresh']['retryAt'], NOW+120+3600)
            self.assertEqual(failed['checkedAt'], NOW+60)
        forecast.close()

    def test_forecast_is_not_a_reset_and_missing_odds_stay_unknown(self):
        value = normalize(payload(last_reset_at=None, probabilities={'rounded_24h': True, 'rounded_48h': 101}), NOW)
        self.assertIsNone(value['lastResetAt'])
        self.assertEqual(value['probabilities'], {'h24': None, 'h48': None})
        self.assertEqual(value['source'], SOURCE)

    def test_signal_window_is_active_only_until_its_deadline_and_after_last_reset(self):
        signal = {'at': iso(NOW-60), 'window': {'end_at': iso(NOW+3600)}}
        value = normalize(payload(official_signal=signal), NOW)
        self.assertEqual(value['officialSignal'], {'at': NOW-60, 'endsAt': NOW+3600})
        self.assertIsNone(normalize(payload(official_signal=signal), NOW+3601)['officialSignal'])
        self.assertIsNone(normalize(payload(official_signal=signal, last_reset_at=iso(NOW)), NOW)['officialSignal'])

    def test_optional_signal_fields_can_disappear_without_inventing_an_announcement_time(self):
        value = normalize(payload(official_signal={'summary': 'Reset planned'}), NOW)
        self.assertEqual(value['officialSignal'], {'at': None, 'endsAt': None, 'summary': 'Reset planned'})

    def test_malformed_or_future_data_does_not_become_a_confirmed_reset(self):
        for value in ([], {}, payload(updated_at='invalid'), payload(updated_at=iso(NOW+600))):
            with self.assertRaises(ValueError):
                normalize(value, NOW)
        self.assertIsNone(normalize(payload(last_reset_at=iso(NOW+600)), NOW)['lastResetAt'])

    def test_announcement_reads_only_the_matching_original_post_and_reuses_cache(self):
        url = 'https://x.com/thsottiaux/status/123'
        def response(value):
            result = Mock()
            result.__enter__ = Mock(return_value=result)
            result.__exit__ = Mock(return_value=False)
            result.read.return_value = json.dumps(value).encode()
            result.headers = {}
            return result
        forecast = ResetForecast()
        opener = Mock()
        opener.open.side_effect = [response(payload(official_signal={'at': iso(NOW-60), 'url': url, 'summary': 'Source summary'})),
                                  response({'fetched_at': iso(NOW), 'stale': False, 'tweets': [
                                      {'id': '456', 'text': 'Unrelated post', 'localized_text': '无关译文', 'translation_status': 'translated'},
                                      {'id': '123', 'text': 'Original announcement', 'localized_text': '来源提供的译文', 'translation_status': 'translated'}]})]
        with patch('bridge.features.accounts.reset_forecast.build_opener', return_value=opener), patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
            forecast.request()
            forecast.worker.join(2)
            forecast.request()
            self.assertEqual(opener.open.call_count, 2)
            self.assertEqual(forecast.status()['officialSignal']['text'], 'Original announcement')
            self.assertEqual(forecast.status()['officialSignal']['translation'], {'locale': 'zh', 'text': '来源提供的译文'})
            self.assertEqual(forecast.status()['officialSignal']['url'], url)
            self.assertEqual(opener.open.call_args.args[0].full_url, SOURCE + 'api/feed?locale=zh')
            self.assertEqual(opener.open.call_args.args[0].get_header('User-agent'), USER_AGENT)
        forecast.close()

    def test_missing_or_unfinished_translation_preserves_original_post(self):
        for translated, status in ((None, None), ('', 'translated'), ('  ', 'translated'),
                                   ({'text': 'invalid'}, 'translated'), ('待翻译文本', 'pending'), ('失败文本', 'failed')):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps({'fetched_at': iso(NOW), 'stale': False, 'tweets': [
                {'id': '123', 'text': 'Original announcement', 'localized_text': translated, 'translation_status': status}]}).encode()
            opener = Mock()
            opener.open.return_value = response
            signal = {'url': 'https://x.com/thsottiaux/status/123'}
            with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
                ResetForecast()._read_announcement(opener, signal)
            self.assertEqual(signal['text'], 'Original announcement')
            self.assertNotIn('translation', signal)

    def test_missing_stale_or_failed_original_keeps_labelled_summary(self):
        for feed in ({'fetched_at': iso(NOW), 'stale': True, 'tweets': [{'id': '123', 'text': 'Stale'}]},
                     {'fetched_at': iso(NOW), 'stale': False, 'tweets': [{'id': '456', 'text': 'Unrelated'}]},
                     {'fetched_at': iso(NOW-10000), 'stale': False, 'tweets': [{'id': '123', 'text': 'Old'}]},
                     {'tweets': 'invalid'}):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps(feed).encode()
            opener = Mock()
            opener.open.return_value = response
            signal = {'url': 'https://x.com/thsottiaux/status/123', 'summary': 'Source summary'}
            with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
                ResetForecast()._read_announcement(opener, signal)
            self.assertNotIn('text', signal)
            self.assertEqual(signal['summary'], 'Source summary')
        forecast = ResetForecast()
        opener.open.side_effect = HTTPError(URL, 429, 'limited', {'Retry-After': '3600'}, io.BytesIO())
        with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
            forecast._read_announcement(opener, signal)
        self.assertEqual(forecast.retry_until, NOW+3600)
        self.assertEqual(signal['summary'], 'Source summary')

    def test_announcement_links_are_validated_before_display_or_matching(self):
        for url in ('javascript:alert(1)', 'https://evil.test/thsottiaux/status/123',
                    'https://x.com@evil.test/thsottiaux/status/123', 'https://x.com/other/status/123'):
            signal = normalize(payload(official_signal={'url': url, 'at': iso(NOW-60)}), NOW)['officialSignal']
            self.assertNotIn('url', signal)

    def test_shared_async_request_uses_fixed_source_identity_and_thirty_minute_cache(self):
        forecast = ResetForecast()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(payload()).encode()
        response.headers = {'x-published-checked-at': iso(NOW)}
        opener = Mock()
        opener.open.return_value = response
        with patch('bridge.features.accounts.reset_forecast.build_opener', return_value=opener), patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
            forecast.request()
            forecast.worker.join(2)
            for _ in range(5):
                forecast.request()
            opener.open.assert_called_once()
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, URL)
            self.assertEqual(request.get_header('User-agent'), USER_AGENT)
            self.assertIsNone(request.get_header('Authorization'))
            self.assertIsNone(request.get_header('Cookie'))
            self.assertFalse(forecast.status()['stale'])
        with patch('bridge.features.accounts.reset_forecast.build_opener', return_value=opener), patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+1799):
            forecast.request()
            opener.open.assert_called_once()
        with patch('bridge.features.accounts.reset_forecast.build_opener', return_value=opener), patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+1800):
            forecast.request()
            forecast.worker.join(2)
            self.assertEqual(opener.open.call_count, 2)
        forecast.close()

    def test_open_refresh_bypasses_cache_coalesces_reopens_and_restarts_background_interval(self):
        forecast = ResetForecast()
        with patch.object(forecast, '_read') as read:
            with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
                forecast.request()
                forecast.worker.join(2)
            with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+59):
                forecast.request(refresh=True)
                read.assert_called_once()
            with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+60):
                forecast.request(refresh=True)
                forecast.worker.join(2)
                self.assertEqual(read.call_count, 2)
                self.assertEqual(forecast.next_check, NOW+1860)
            with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+1800):
                forecast.request()
                self.assertEqual(read.call_count, 2)
        forecast.close()

    def test_failure_keeps_cached_data_but_marks_it_stale_and_honours_retry_after(self):
        forecast = ResetForecast()
        forecast.value = normalize(payload(), NOW)
        error = HTTPError(URL, 429, 'limited', {'Retry-After': '3600'}, io.BytesIO())
        with patch('bridge.features.accounts.reset_forecast.build_opener') as build, patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
            build.return_value.open.side_effect = error
            forecast._read()
            self.assertEqual(forecast.status()['state'], 'error')
            self.assertTrue(forecast.status()['stale'])
            self.assertEqual(forecast.status()['lastResetAt'], NOW-3600)
            self.assertEqual(forecast.next_check, NOW+3600)
            forecast.request()
            forecast.request(refresh=True)
            build.return_value.open.assert_called_once()

    def test_published_copy_age_and_closed_service(self):
        forecast = ResetForecast()
        forecast.value = normalize(payload(updated_at=iso(NOW-3600)), NOW)
        forecast.value['publishedAt'] = NOW
        with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW):
            self.assertFalse(forecast.status()['stale'])
        with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+1801):
            self.assertFalse(forecast.status()['stale'])
        with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+5401):
            self.assertTrue(forecast.status()['stale'])
        forecast.close()
        with patch('bridge.features.accounts.reset_forecast.build_opener') as build:
            forecast.request()
            build.assert_not_called()

    def test_retry_after_supports_seconds_dates_and_invalid_values(self):
        self.assertEqual(retry_after('10', NOW), 1800)
        self.assertEqual(retry_after('Wed, 14 Oct 2026 09:50:00 GMT', NOW), 1800)
        self.assertEqual(retry_after('invalid', NOW), 1800)



    def test_cached_signal_expires_at_its_deadline_without_another_network_read(self):
        forecast = ResetForecast()
        forecast.value = normalize(payload(official_signal={
            'at': iso(NOW-60), 'window': {'end_at': iso(NOW+60)}}), NOW)
        with patch('bridge.features.accounts.reset_forecast.time.time', return_value=NOW+60):
            self.assertIsNone(forecast.status()['officialSignal'])
        self.assertIsNotNone(forecast.value['officialSignal'])

    def test_connection_failures_allow_manual_and_automatic_retry_after_one_minute(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                forecast = ResetForecast()
                clock = [NOW]
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.headers = {}
                response.read.return_value = json.dumps(payload()).encode()
                with patch('bridge.features.accounts.reset_forecast.build_opener') as build, patch(
                        'bridge.features.accounts.reset_forecast.time.time', side_effect=lambda: clock[0]):
                    build.return_value.open.side_effect = [OSError('offline'), response]
                    forecast.request()
                    forecast.worker.join(2)
                    self.assertEqual(forecast.status()['state'], 'error')
                    self.assertEqual(forecast.status()['refresh']['retryAt'], 0)
                    clock[0] += 59
                    forecast.request(refresh=manual)
                    self.assertEqual(build.return_value.open.call_count, 1)
                    clock[0] += 1
                    forecast.request(refresh=manual)
                    forecast.worker.join(2)
                    self.assertEqual(build.return_value.open.call_count, 2)
                    self.assertEqual(forecast.status()['state'], 'ready')
                forecast.close()


class GlobalReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.manager = SimpleNamespace(root=root/'accounts', bridge=SimpleNamespace(data_dir=root),
                                       lock=threading.RLock(), info=Mock(), check_ready=Mock(), reset_forecast=Mock())
        self.monitor = AccountMonitor(self.manager)
        self.manager.reset_forecast.status.return_value = {'state': 'ready', 'stale': False, 'source': SOURCE,
                                                          'lastResetAt': NOW-3600, 'officialSignal': None}
        save_settings(root, {'barkEnabled': True, 'barkKey': 'fixture'})

    def tearDown(self):
        self.temp.cleanup()

    def test_slow_delivery_releases_manager_lock_and_rechecks_reenabled_watch_boundary(self):
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW):
            self.monitor.configure({'globalReset': True})
        self.manager.reset_forecast.status.return_value.update(lastResetAt=NOW+1, officialSignal={'at': NOW+1})
        entered, release = threading.Event(), threading.Event()
        def slow_send(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Fixture timeout')
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW+2), \
                patch('bridge.features.accounts.monitor.publish_bark', side_effect=slow_send) as send:
            worker = threading.Thread(target=self.monitor.scan_global_reset)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                acquired = self.manager.lock.acquire(blocking=False)
                if acquired:
                    self.manager.lock.release()
                self.assertTrue(acquired, 'Status lock is held across push I/O')
                with patch('bridge.features.accounts.monitor.time.time', return_value=NOW+3):
                    self.monitor.configure({'globalReset': False})
                    self.monitor.configure({'globalReset': True})
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            send.assert_called_once()

    def test_enabling_skips_history_and_delivers_new_events_once_across_restart(self):
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW), patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.monitor.configure({'globalReset': True})
            self.monitor.scan()
            send.assert_not_called()
            self.manager.reset_forecast.status.return_value['lastResetAt'] = NOW+1
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW+2), patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.monitor.scan()
            self.monitor.scan()
            AccountMonitor(self.manager).scan()
            send.assert_called_once()
            self.assertIn(SOURCE, send.call_args.args[2])
            self.manager.info.request.assert_not_called()
            self.manager.check_ready.assert_not_called()

    def test_signal_is_separate_from_confirmation_and_missing_timestamp_never_alerts(self):
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW), patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.monitor.configure({'globalResetSignal': True})
            value = self.manager.reset_forecast.status.return_value
            value['officialSignal'] = {'at': None, 'endsAt': None}
            self.monitor.scan()
            send.assert_not_called()
            value['officialSignal'] = {'at': NOW+1, 'endsAt': NOW+3600}
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW+2), patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.monitor.scan()
            self.monitor.scan()
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1], 'Codex 官方重置预告')

    def test_stale_data_and_disabled_notifications_are_silent(self):
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW):
            self.monitor.configure({'globalReset': True})
        self.manager.reset_forecast.status.return_value.update(lastResetAt=NOW+1, stale=True)
        with patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.monitor.scan()
            send.assert_not_called()
        self.monitor.configure({'globalReset': False})
        self.manager.reset_forecast.request.reset_mock()
        self.monitor.scan()
        self.manager.reset_forecast.request.assert_not_called()

    def test_combined_switch_delivers_both_event_types_and_disables_both(self):
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW):
            self.monitor.configure({'globalReset': True})
        self.assertTrue(self.monitor.preferences['globalResetSignal'])
        value = self.manager.reset_forecast.status.return_value
        value['officialSignal'] = {'at': NOW+1, 'endsAt': NOW+3600}
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW+3), patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.monitor.scan()
            value.update(officialSignal=None, lastResetAt=NOW+2)
            self.monitor.scan()
            self.assertEqual([call.args[1] for call in send.call_args_list], ['Codex 官方重置预告', 'Codex 全局已重置'])
        self.monitor.configure({'globalResetSignal': False})
        self.assertFalse(self.monitor.preferences['globalReset'])
        self.assertFalse(self.monitor.preferences['globalResetSignal'])
        restored = AccountMonitor(self.manager)
        self.assertFalse(restored.preferences['globalReset'])

    def test_reenable_establishes_a_new_boundary(self):
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW):
            self.monitor.configure({'globalReset': True})
        self.monitor.configure({'globalReset': False})
        with patch('bridge.features.accounts.monitor.time.time', return_value=NOW+100):
            self.monitor.configure({'globalReset': True})
        self.manager.reset_forecast.status.return_value['lastResetAt'] = NOW+50
        with patch('bridge.features.accounts.monitor.publish_bark') as send:
            self.monitor.scan()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
