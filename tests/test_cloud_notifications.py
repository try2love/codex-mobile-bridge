import base64
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from bridge.features.network.cloud_crypto import decrypt_preview, new_key, public_key
from bridge.features.notifications.cloud import CloudNotifications


class CloudNotificationsTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.now = 1_800_000_000
        self.config = {'origin': 'https://relay.example.test', 'account_id': str(uuid.uuid4()),
                       'gateway_id': str(uuid.uuid4())}
        self.desktop, self.mobile = new_key(), new_key()
        self.binding = {'id': str(uuid.uuid4()), 'status': 'active', 'push_enabled': True,
                        'mobile_public_key': base64.b64encode(public_key(new_key())).decode(),
                        'endpoint_revision': str(uuid.uuid4())}
        self.pin = {'gateway_id': self.config['gateway_id'], 'mobile_public_key': self.binding['mobile_public_key'],
                    'preview_public_key': base64.b64encode(public_key(self.mobile)).decode()}
        self.snapshot = {'streamId': 'local-test-stream', 'cursor': 1, 'enabled': True, 'events': [self.event(1)]}
        self.outbox = CloudNotifications(self.temp.name, clock=lambda: self.now)
        self.addCleanup(lambda: self.outbox.close())
        self.calls = []

    def event(self, sequence):
        return {'id': f'event-{sequence}', 'kind': 'completion', 'sequence': sequence, 'createdAt': self.now,
                'title': 'Test completion', 'body': 'A private task completed'}

    def drain(self, send=None, current=lambda: True):
        self.outbox.drain(self.config, [self.binding], lambda _: self.pin, self.desktop, self.snapshot,
                          send or (lambda path, value: self.calls.append((path, value))), current)

    def baseline(self):
        self.drain()
        self.assertEqual(self.calls, [])

    def advance(self, sequence):
        self.snapshot['cursor'] = sequence
        self.snapshot['events'].append(self.event(sequence))

    def restart(self):
        self.outbox.close()
        self.outbox = CloudNotifications(self.temp.name, clock=lambda: self.now)

    def row(self, notification):
        with closing(sqlite3.connect(self.outbox.path)) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM events WHERE notification=?', (notification,)).fetchone())

    def test_quiet_baseline_and_direct_push_share_durable_identity(self):
        self.baseline()
        self.advance(2)
        direct = self.outbox.decorate(self.config, self.binding['id'], self.pin, self.desktop, self.snapshot)
        item = direct['events'][-1]

        def send(path, value):
            self.assertEqual(path, '/v1/push/jobs')
            envelope = value['envelope']
            self.assertEqual(envelope['notification_id'], item['notification_id'])
            self.assertEqual(self.row(envelope['notification_id'])['attempts'], 1)
            self.assertEqual(self.row(envelope['notification_id'])['revision'], self.binding['endpoint_revision'])
            plaintext = decrypt_preview(self.config['origin'], self.mobile, public_key(self.desktop), envelope, self.now)
            self.assertLessEqual(len(plaintext), 1024)
            preview = json.loads(plaintext)
            self.assertEqual(preview['scope'], direct['notification_scope'])
            self.assertEqual(preview['event_id'], item['notification_event_id'])
            self.calls.append((path, value))

        self.drain(send)
        self.assertEqual(len(self.calls), 1)
        self.restart()
        again = self.outbox.decorate(self.config, self.binding['id'], self.pin, self.desktop, self.snapshot)
        self.assertEqual(again['events'][-1]['notification_id'], item['notification_id'])
        self.drain()
        self.assertEqual(len(self.calls), 1)

    def test_lost_push_result_retries_same_ciphertext_after_restart(self):
        self.baseline()
        self.advance(2)

        def lost(path, value):
            self.calls.append((path, value))
            raise TimeoutError('local test lost response')

        self.drain(lost)
        self.restart()
        self.drain(lost)
        self.assertEqual(len(self.calls), 1)
        self.now += 5
        self.drain()
        self.assertEqual(self.calls[0], self.calls[1])
        self.assertEqual(self.row(self.calls[0][1]['envelope']['notification_id'])['status'], 'accepted')

    def test_push_retries_are_bounded_and_attempts_are_durable(self):
        self.baseline()
        self.advance(2)

        def lost(path, value):
            self.calls.append((path, value))
            raise TimeoutError()

        for attempt in range(10):
            self.drain(lost)
            self.restart()
            self.now += 160
        self.assertEqual(len(self.calls), 6)
        self.assertTrue(all(call == self.calls[0] for call in self.calls))

    def test_lost_cancellation_preserves_tombstone_and_same_ids(self):
        self.baseline()
        self.advance(2)
        self.drain()
        notification = self.calls.pop()[1]['envelope']['notification_id']
        self.binding['push_enabled'] = False

        def lost(path, value):
            self.assertEqual(path, '/v1/push/cancel')
            row = self.row(notification)
            self.assertEqual(row['status'], 'cancelling')
            self.assertEqual(row['cancel_attempts'], 1)
            self.calls.append((path, value))
            raise TimeoutError()

        self.drain(lost)
        self.restart()
        self.drain()
        self.assertEqual(len(self.calls), 1)
        self.now += 5
        self.drain()
        self.assertEqual(self.calls[0], self.calls[1])
        self.assertEqual(self.row(notification)['status'], 'discarded')

    def test_cancellation_retries_stop_at_six_without_reviving_jobs(self):
        self.baseline()
        self.advance(2)
        self.drain()
        notification = self.calls.pop()[1]['envelope']['notification_id']
        self.snapshot['enabled'] = False

        def lost(path, value):
            self.calls.append((path, value))
            raise TimeoutError()

        for _ in range(10):
            self.drain(lost)
            self.restart()
            self.now += 160
        self.assertEqual(len(self.calls), 6)
        self.assertTrue(all(call[0] == '/v1/push/cancel' and call == self.calls[0] for call in self.calls))
        self.assertEqual(self.row(notification)['cancel_attempts'], 6)
        self.assertEqual(self.row(notification)['status'], 'cancelling')

    def test_endpoint_and_stream_changes_cancel_old_jobs_and_start_quietly(self):
        self.baseline()
        self.advance(2)
        self.drain()
        old = self.calls.pop()[1]['envelope']['notification_id']
        self.binding['endpoint_revision'] = str(uuid.uuid4())
        self.drain()
        self.assertEqual(self.calls, [('/v1/push/cancel', {'binding_id': self.binding['id'], 'notification_ids': [old]})])
        self.calls.clear()
        self.snapshot = {'streamId': 'replacement-stream', 'cursor': 3, 'enabled': True, 'events': [self.event(3)]}
        self.drain()
        self.assertEqual(self.calls, [])
        self.advance(4)
        self.drain()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], '/v1/push/jobs')

    def test_changed_account_generation_cannot_send_an_observed_batch(self):
        self.baseline()
        self.advance(2)
        self.drain(current=lambda: False)
        self.assertEqual(self.calls, [])

    def test_old_development_schema_is_not_upgraded_or_used_to_send(self):
        self.baseline()
        self.outbox.close()
        with closing(sqlite3.connect(self.outbox.path)) as db:
            db.execute('ALTER TABLE events DROP COLUMN cancel_attempts')
            db.execute('ALTER TABLE events DROP COLUMN cancel_next')
            db.commit()
        with self.assertRaises(sqlite3.OperationalError):
            self.drain()
        self.outbox.close()
        with closing(sqlite3.connect(self.outbox.path)) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(events)')}
            self.assertNotIn('cancel_attempts', columns)
            self.assertNotIn('cancel_next', columns)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
