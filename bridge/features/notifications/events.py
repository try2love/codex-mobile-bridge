"""Authenticated companion inbox. Delivery to an inbox is not OS push delivery."""
import hashlib
import threading
import time
import uuid
from pathlib import Path

from bridge.features.notifications.channels import read_json, write_json


class MobileEvents:
    LIMIT = 200

    def __init__(self, directory):
        self.path = Path(directory) / 'mobile-events.json'
        self.lock = threading.RLock()
        self.value = read_json(self.path, {'streamId': uuid.uuid4().hex, 'cursor': 0, 'events': []})

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
            updated = {**before, 'cursor': cursor, 'events': (before['events'] + [event])[-self.LIMIT:]}
            write_json(self.path, updated)
            self.value = updated

    def read(self, after=0):
        if type(after) is not int or after < 0:
            raise ValueError('通知游标格式不正确')
        with self.lock:
            return {'streamId': self.value['streamId'], 'cursor': self.value['cursor'],
                    'events': [dict(event) for event in self.value['events'] if event['sequence'] > after]}
