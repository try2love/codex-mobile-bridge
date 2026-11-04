"""Authenticated companion inbox. Delivery to an inbox is not OS push delivery."""
from bridge.features.notifications.providers import PROVIDERS, desktop_provider, desktop_client

import hashlib
import threading
import time
import uuid
from pathlib import Path

from bridge.features.notifications.channels import read_json, write_json


class MobileEvents:
    LIMIT = 200
    PROVIDERS = PROVIDERS

    @staticmethod
    def provider(event):
        return desktop_provider(event['host']) or 'codex'

    @staticmethod
    def client(event):
        return desktop_client(event['host']) or 'codex'

    def __init__(self, directory):
        self.path = Path(directory) / 'mobile-events.json'
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.generation = 0
        self.closed = False
        self.value = read_json(self.path, {'streamId': uuid.uuid4().hex, 'cursor': 0, 'events': []})
        if 'pending' not in self.value:
            self.value['pending'] = [self.metadata(event) for event in self.value['events']
                                     if event['sequence'] > self.value.get('seen', {}).get(self.client(event), 0)]

    @staticmethod
    def metadata(event):
        # Keep unread counts independent of the bounded inbox body history.
        return {key: event[key] for key in ('sequence', 'host', 'threadId', 'kind')}

    def version(self):
        with self.lock:
            return self.generation

    def changed(self):
        with self.condition:
            self.generation += 1
            self.condition.notify_all()

    def wait(self, version, timeout):
        with self.condition:
            self.condition.wait_for(lambda: self.closed or self.generation != version, timeout)

    def close(self):
        with self.condition:
            self.closed = True
            self.changed()

    def append(self, keys, row, title, body, completed):
        identity = hashlib.sha256('\n'.join(sorted(keys)).encode()).hexdigest()
        with self.lock:
            if any(event['id'] == identity for event in self.value['events']):
                return
            before = self.value
            cursor = before['cursor'] + 1
            event = {'id': identity, 'sequence': cursor, 'threadId': row['id'], 'host': row['host'],
                     'trace': keys[0][-64:][:16] if len(keys[0]) >= 64 else '',
                     'kind': 'attention' if completed == 'attention' else 'completion' if completed else 'request', 'title': title, 'body': body,
                     'createdAt': time.time()}
            updated = {**before, 'cursor': cursor, 'events': (before['events'] + [event])[-self.LIMIT:],
                       'pending': before['pending'] + [self.metadata(event)]}
            write_json(self.path, updated)
            self.value = updated
            self.changed()

    def acknowledge(self, provider, stream, through):
        if (provider not in (*self.PROVIDERS, 'all') and not desktop_client('desktop:' + str(provider))) or type(through) is not int or through < 0:
            raise ValueError('通知已读参数无效')
        with self.lock:
            if stream != self.value['streamId'] or through > self.value['cursor']:
                raise ValueError('通知状态已变化，请刷新后重试')
            seen = dict(self.value.get('seen', {}))
            known = set(self.PROVIDERS) | {self.client(event) for event in self.value['pending']} | set(seen)
            providers = known if provider == 'all' else (provider,) if provider in known else ()
            if all(through <= seen.get(item, 0) for item in providers):
                return
            for item in providers:
                seen[item] = max(through, seen.get(item, 0))
            updated = {**self.value, 'seen': seen, 'pending': [event for event in self.value['pending']
                       if event['sequence'] > seen.get(self.client(event), 0)]}
            write_json(self.path, updated)
            self.value = updated
            self.changed()

    def checkpoint(self):
        with self.lock:
            return {key: self.value[key] for key in ('streamId', 'cursor')}

    def read(self, after=0, allowed=None):
        if type(after) is not int or after < 0:
            raise ValueError('通知游标格式不正确')
        with self.lock:
            # A bounded snapshot accompanies incremental reads, including stream resets.
            # These are the most recent notifications, not a live task-status claim.
            latest = {}
            seen = self.value.get('seen', {})
            counts = dict.fromkeys(self.PROVIDERS, 0)
            for event in self.value['pending']:
                if allowed is None or allowed(event):
                    counts[self.provider(event)] += 1
            events = []
            for item in self.value['events']:
                if allowed is not None and not allowed(item):
                    continue
                provider = self.provider(item)
                event = {**item, 'provider': provider, 'clientId': self.client(item), 'unread': item['sequence'] > seen.get(self.client(item), 0)}
                latest[provider] = {'provider': provider, 'kind': event['kind'], 'sequence': event['sequence']}
                if event['sequence'] > after:
                    events.append(event)
            return {'streamId': self.value['streamId'], 'cursor': self.value['cursor'],
                    'unread': counts, 'seen': dict(seen), 'summary': list(latest.values()), 'events': events}
