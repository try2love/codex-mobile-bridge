"""Authenticated companion inbox. Delivery to an inbox is not OS push delivery."""
import hashlib
import threading
import time
import uuid
from pathlib import Path

from bridge.features.notifications.channels import read_json, write_json


class MobileEvents:
    LIMIT = 200
    PROVIDERS = ('codex', 'claude', 'deepseek')

    @staticmethod
    def provider(event):
        return {'desktop:claude': 'claude', 'desktop:deepseek': 'deepseek'}.get(event['host'], 'codex')

    def __init__(self, directory):
        self.path = Path(directory) / 'mobile-events.json'
        self.lock = threading.RLock()
        self.value = read_json(self.path, {'streamId': uuid.uuid4().hex, 'cursor': 0, 'events': []})
        if 'pending' not in self.value:
            self.value['pending'] = [self.metadata(event) for event in self.value['events']
                                     if event['sequence'] > self.value.get('seen', {}).get(self.provider(event), 0)]

    @staticmethod
    def metadata(event):
        # Keep unread counts independent of the bounded inbox body history.
        return {key: event[key] for key in ('sequence', 'host', 'threadId', 'kind')}

    def append(self, keys, row, title, body, completed):
        identity = hashlib.sha256('\n'.join(sorted(keys)).encode()).hexdigest()
        with self.lock:
            if any(event['id'] == identity for event in self.value['events']):
                return
            before = self.value
            cursor = before['cursor'] + 1
            event = {'id': identity, 'sequence': cursor, 'threadId': row['id'], 'host': row['host'],
                     'kind': 'completion' if completed else 'request', 'title': title, 'body': body,
                     'createdAt': time.time()}
            updated = {**before, 'cursor': cursor, 'events': (before['events'] + [event])[-self.LIMIT:],
                       'pending': before['pending'] + [self.metadata(event)]}
            write_json(self.path, updated)
            self.value = updated

    def acknowledge(self, provider, stream, through):
        if provider not in (*self.PROVIDERS, 'all') or type(through) is not int or through < 0:
            raise ValueError('通知已读参数无效')
        with self.lock:
            if stream != self.value['streamId'] or through > self.value['cursor']:
                raise ValueError('通知状态已变化，请刷新后重试')
            seen = dict(self.value.get('seen', {}))
            providers = self.PROVIDERS if provider == 'all' else (provider,)
            if all(through <= seen.get(item, 0) for item in providers):
                return
            for item in providers:
                seen[item] = max(through, seen.get(item, 0))
            updated = {**self.value, 'seen': seen, 'pending': [event for event in self.value['pending']
                       if event['sequence'] > seen.get(self.provider(event), 0)]}
            write_json(self.path, updated)
            self.value = updated

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
                event = {**item, 'provider': provider, 'unread': item['sequence'] > seen.get(provider, 0)}
                latest[provider] = {'provider': provider, 'kind': event['kind'], 'sequence': event['sequence']}
                if event['sequence'] > after:
                    events.append(event)
            return {'streamId': self.value['streamId'], 'cursor': self.value['cursor'],
                    'unread': counts, 'seen': dict(seen), 'summary': list(latest.values()), 'events': events}
