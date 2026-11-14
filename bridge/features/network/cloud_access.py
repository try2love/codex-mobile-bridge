"""Local access fences are separate from immutable, previously verified phone keys."""
from pathlib import Path

from bridge.features.notifications.channels import read_json, write_json


class WebAccess:
    """A desktop-local grant; never inferred from cloud login or phone trust."""
    def __init__(self, directory):
        self.path = Path(directory) / 'cloud-web-access.json'

    def state(self, key, gateway):
        try:
            values = read_json(self.path, {})
            if not isinstance(values, dict):
                raise ValueError('invalid Web access state')
            record = values.get(key + ':' + gateway, {'allowed': False, 'revision': 0})
            if (not isinstance(record, dict) or set(record) != {'allowed', 'revision'}
                    or type(record['allowed']) is not bool or type(record['revision']) is not int
                    or not 0 <= record['revision'] <= 2**53 - 1):
                raise ValueError('invalid Web access state')
            return {**record, 'error': ''}
        except (OSError, ValueError):
            return {'allowed': False, 'revision': 0, 'error': 'web_access_storage_error'}

    def save(self, key, gateway, allowed, revision):
        values = read_json(self.path, {})
        if not isinstance(values, dict):
            raise ValueError('invalid Web access state')
        values[key + ':' + gateway] = {'allowed': allowed, 'revision': revision}
        write_json(self.path, values)


class BindingAccess:
    def __init__(self, directory):
        self.path = Path(directory) / 'cloud-access.json'

    def _records(self):
        value = read_json(self.path, {})
        if not isinstance(value, dict):
            raise ValueError('invalid local cloud access state')
        return value

    def state(self, key, gateway, binding):
        record = self._records().get(key + ':' + gateway, {}).get(binding)
        if record is None:
            # Trust records cannot stand in for an explicit local access grant.
            return {'allowed': False, 'generation': 0, 'remote_revision': 0,
                    'remote_status': 'pending', 'sync_state': 'confirmed'}
        if (not isinstance(record, dict) or type(record.get('allowed')) is not bool
                or type(record.get('generation')) is not int or record['generation'] < 0
                or type(record.get('remote_revision')) is not int or record['remote_revision'] < 0
                or record.get('remote_status') not in ('pending', 'active', 'disabled', 'revoked')
                or record.get('sync_state') not in ('confirmed', 'opening', 'closing', 'unconfirmed', 'identity_changed')):
            raise ValueError('invalid local cloud access state')
        return dict(record)

    def save(self, key, gateway, binding, record):
        values = self._records()
        records = values.setdefault(key + ':' + gateway, {})
        if not isinstance(records, dict) or len(records) >= 200 and binding not in records:
            raise ValueError('local cloud device capacity reached')
        records[binding] = record
        write_json(self.path, values)

    def begin(self, key, gateway, binding, allowed, expected):
        record = self.state(key, gateway, binding)
        record.update(allowed=False, generation=record['generation'] + 1,
                      sync_state='opening' if allowed else 'closing', requested_allowed=allowed,
                      expected_revision=expected)
        self.save(key, gateway, binding, record)
        return record['generation']

    def finish(self, key, gateway, binding, generation, response=None):
        record = self.state(key, gateway, binding)
        if record['generation'] != generation:
            return False
        if response is None:
            record.update(allowed=False, sync_state='unconfirmed')
        else:
            record.update(allowed=record['requested_allowed'], remote_revision=response['access_revision'],
                          remote_status=response['status'], sync_state='confirmed')
        self.save(key, gateway, binding, record)
        return True

    def observe(self, key, gateway, binding, remote, identity_matches=True):
        record = self.state(key, gateway, binding)
        if not identity_matches:
            record.update(allowed=False, sync_state='identity_changed')
        elif remote['access_revision'] >= record['remote_revision']:
            record.update(remote_revision=remote['access_revision'], remote_status=remote['status'])
            if remote['status'] != 'active' or remote.get('device_revoked'):
                record['allowed'] = False
            if (record['sync_state'] == 'unconfirmed' and record.get('requested_allowed') is False
                    and remote['status'] == 'disabled' and remote['access_revision'] > record.get('expected_revision', 0)):
                record['sync_state'] = 'confirmed'
        self.save(key, gateway, binding, record)
        return record
