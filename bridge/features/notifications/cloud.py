"""Durable per-binding notification identities; this outbox never contains commands."""
import base64
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from bridge.features.network.cloud_crypto import decode_key, encrypt_preview, exact_origin


class CloudNotifications:
    LIMIT = 4096

    def __init__(self, directory, clock=time.time):
        self.path = Path(directory) / 'cloud-notifications.sqlite3'
        self.clock = clock
        self.lock = threading.RLock()
        self.database = None

    def _db(self):
        if self.database is None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            db = sqlite3.connect(self.path, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS bindings (
                    account TEXT NOT NULL, binding TEXT NOT NULL, stream TEXT NOT NULL,
                    cursor INTEGER NOT NULL, revision TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(account,binding));
                CREATE TABLE IF NOT EXISTS events (
                    account TEXT NOT NULL, binding TEXT NOT NULL, stream TEXT NOT NULL,
                    event TEXT NOT NULL, notification TEXT NOT NULL, expires INTEGER NOT NULL,
                    ciphertext TEXT NOT NULL, status TEXT NOT NULL, revision TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, next REAL NOT NULL DEFAULT 0,
                    cancel_attempts INTEGER NOT NULL DEFAULT 0, cancel_next REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(account,binding,stream,event), UNIQUE(binding,notification));
            ''')
            db.commit()
            self.path.chmod(0o600)
            self.database = db
        return self.database

    @staticmethod
    def _account(config):
        exact_origin(config['origin'])
        return config['origin'] + '\0' + str(uuid.UUID(config['account_id'])) + '\0' + str(uuid.UUID(config['gateway_id']))

    @staticmethod
    def route(config, binding, stream):
        return hashlib.sha256((config['origin'] + '\0' + config['gateway_id'] + '\0' + binding + '\0' + stream).encode()).hexdigest()

    def _preview(self, config, binding, stream, event, notification, expires, private, pin):
        envelope = {'v': 1, 'notification_id': notification, 'binding_id': binding, 'expires_at': expires}
        value = {'title': str(event.get('title') or 'Bridge')[:80], 'body': str(event.get('body') or '')[:180],
                 'gateway_id': config['gateway_id'], 'binding_id': binding, 'notification_id': notification,
                 'scope': self.route(config, binding, stream), 'event_id': 'cloud:' + notification}
        while len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) > 1024:
            field = 'body' if value['body'] else 'title'
            value[field] = value[field][:-1]
        plaintext = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        envelope['ciphertext'] = encrypt_preview(config['origin'], private, decode_key(pin['preview_public_key']), envelope, plaintext)
        return envelope

    def _observe(self, db, account, config, binding, pin, private, snapshot):
        now = int(self.clock())
        stream, cursor = snapshot['streamId'], snapshot['cursor']
        if not isinstance(stream, str) or not 0 < len(stream) <= 256 or type(cursor) is not int or cursor < 0:
            raise ValueError('invalid notification snapshot')
        prior = db.execute('SELECT * FROM bindings WHERE account=? AND binding=?', (account, binding)).fetchone()
        if prior is None or prior['stream'] != stream:
            db.execute("UPDATE events SET status='cancelling',cancel_attempts=0,cancel_next=0 WHERE account=? AND binding=? AND status IN ('pending','accepted')", (account, binding))
            db.execute('''INSERT INTO bindings(account,binding,stream,cursor) VALUES(?,?,?,?)
                          ON CONFLICT(account,binding) DO UPDATE SET stream=excluded.stream,cursor=excluded.cursor''',
                       (account, binding, stream, cursor))
            watermark = cursor  # A new binding/stream starts quietly at the current snapshot.
        else:
            watermark = prior['cursor']
        enabled = snapshot.get('enabled') is True
        for event in snapshot['events'][-200:]:
            created, sequence = event.get('createdAt'), event.get('sequence')
            if (not enabled or event.get('kind') not in ('request', 'completion') or type(sequence) is not int
                    or not isinstance(created, (int, float)) or not now - 3600 < created <= now + 1
                    or not isinstance(event.get('id'), str) or not 0 < len(event['id']) <= 256):
                continue
            if db.execute('SELECT 1 FROM events WHERE account=? AND binding=? AND stream=? AND event=?',
                          (account, binding, stream, event['id'])).fetchone():
                continue
            if db.execute('SELECT count(*) FROM events').fetchone()[0] >= self.LIMIT:
                raise ValueError('notification receipt capacity')
            notification, expires = str(uuid.uuid4()), min(now + 3600, int(created) + 3600)
            envelope = self._preview(config, binding, stream, event, notification, expires, private, pin)
            db.execute('INSERT INTO events(account,binding,stream,event,notification,expires,ciphertext,status) VALUES(?,?,?,?,?,?,?,?)',
                       (account, binding, stream, event['id'], notification, expires, envelope['ciphertext'],
                        'pending' if sequence > watermark else 'baseline'))
        db.execute('UPDATE bindings SET cursor=max(cursor,?) WHERE account=? AND binding=?', (cursor, account, binding))

    def decorate(self, config, binding, pin, private, snapshot):
        """Commit identities before exposing them on an authenticated direct channel."""
        binding = str(uuid.UUID(binding))
        account = self._account(config)
        with self.lock:
            db = self._db()
            with db:
                db.execute('DELETE FROM events WHERE expires<=?', (int(self.clock()),))
                self._observe(db, account, config, binding, pin, private, snapshot)
            items = {row['event']: row for row in db.execute('SELECT * FROM events WHERE account=? AND binding=? AND stream=?',
                                                           (account, binding, snapshot['streamId']))}
            scope = self.route(config, binding, snapshot['streamId'])
            events = []
            for event in snapshot['events']:
                item = dict(event)
                if item['id'] in items:
                    row = items[item['id']]
                    item.update(notification_id=row['notification'], notification_event_id='cloud:' + row['notification'],
                                notification_expires_at=row['expires'])
                events.append(item)
            return {**snapshot, 'notification_scope': scope, 'events': events}

    def disable(self, config, binding):
        """A later permission restore starts quietly instead of replaying this outbox."""
        account = self._account(config)
        with self.lock:
            db = self._db()
            with db:
                db.execute("UPDATE events SET status='discarded' WHERE account=? AND binding=? AND status IN ('pending','accepted','cancelling')", (account, binding))
                db.execute('UPDATE bindings SET enabled=0 WHERE account=? AND binding=?', (account, binding))

    def drain(self, config, bindings, pins, private, snapshot, send, current=lambda: True):
        """Only bounded, same-ID idempotent notification retries are permitted."""
        account = self._account(config)
        for binding in bindings[:200]:
            identifier = binding.get('id')
            pin = pins(identifier)
            if (not pin or pin.get('gateway_id') != config['gateway_id'] or binding.get('status') != 'active'
                    or binding.get('mobile_public_key') != pin['mobile_public_key']):
                continue
            enabled = snapshot.get('enabled') is True and binding.get('push_enabled') is True
            revision = binding.get('endpoint_revision') or ''
            if enabled:
                revision = str(uuid.UUID(revision))
            with self.lock:
                db = self._db()
                with db:
                    db.execute('DELETE FROM events WHERE expires<=?', (int(self.clock()),))
                    self._observe(db, account, config, identifier, pin, private, snapshot)
                    state = db.execute('SELECT * FROM bindings WHERE account=? AND binding=?', (account, identifier)).fetchone()
                    reset = (state['revision'] != revision or bool(state['enabled']) != enabled)
                    if reset:
                        # A changed endpoint/policy starts quietly, but old queued jobs still need cancellation.
                        db.execute("UPDATE events SET status='cancelling',cancel_attempts=0,cancel_next=0 WHERE account=? AND binding=? AND status IN ('pending','accepted')", (account, identifier))
                        db.execute('UPDATE bindings SET revision=?,enabled=? WHERE account=? AND binding=?', (revision, enabled, account, identifier))
                    allowed = {event['id'] for event in snapshot['events']}
                    cancelled = [row['notification'] for row in db.execute("SELECT stream,event,notification FROM events WHERE account=? AND binding=? AND status IN ('pending','accepted')", (account, identifier))
                                 if not enabled or row['stream'] != snapshot['streamId'] or row['event'] not in allowed]
                    for notification in cancelled:
                        db.execute("UPDATE events SET status='cancelling',cancel_attempts=0,cancel_next=0 WHERE account=? AND binding=? AND notification=?", (account, identifier, notification))
                    cancelling = list(db.execute("SELECT * FROM events WHERE account=? AND binding=? AND status='cancelling' AND cancel_attempts<6 AND cancel_next<=? ORDER BY expires LIMIT 100",
                                                 (account, identifier, self.clock())))
            if not current():
                return
            if cancelling:
                with self.lock, db:
                    for row in cancelling:
                        db.execute('UPDATE events SET cancel_attempts=cancel_attempts+1,cancel_next=? WHERE account=? AND binding=? AND notification=?',
                                   (self.clock() + 5 * (2 ** row['cancel_attempts']), account, identifier, row['notification']))
                try:
                    send('/v1/push/cancel', {'binding_id': identifier, 'notification_ids': [row['notification'] for row in cancelling]})
                except Exception:
                    pass  # Keep the durable tombstone; a lost response retries the same IDs.
                else:
                    with self.lock, db:
                        for row in cancelling:
                            db.execute("UPDATE events SET status='discarded' WHERE account=? AND binding=? AND notification=? AND status='cancelling'",
                                       (account, identifier, row['notification']))
            if not enabled:
                continue
            with self.lock:
                pending = list(db.execute("SELECT * FROM events WHERE account=? AND binding=? AND status='pending' AND attempts<6 AND next<=? ORDER BY expires LIMIT 10", (account, identifier, self.clock())))
            for row in pending:
                if not current():
                    return
                if not pins(identifier):
                    break
                # Persist the attempt/revision before the network call, even if its response is lost.
                with self.lock, db:
                    db.execute('UPDATE events SET attempts=attempts+1,revision=?,next=? WHERE account=? AND binding=? AND notification=?',
                               (revision, self.clock() + 5 * (2 ** row['attempts']), account, identifier, row['notification']))
                envelope = {'v': 1, 'binding_id': identifier, 'notification_id': row['notification'],
                            'expires_at': row['expires'], 'ciphertext': row['ciphertext']}
                try:
                    send('/v1/push/jobs', {'envelope': envelope, 'endpoint_revision': revision})
                except Exception:
                    continue
                with self.lock, db:
                    db.execute("UPDATE events SET status='accepted' WHERE account=? AND binding=? AND notification=? AND status='pending'", (account, identifier, row['notification']))

    def close(self):
        with self.lock:
            if self.database is not None:
                self.database.close()
                self.database = None
