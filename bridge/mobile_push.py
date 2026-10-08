"""Opt-in native push. Private provider configuration never comes from phone requests."""
import hashlib
import re
import threading
import time
import uuid
from pathlib import Path

from .notifications import read_json, write_json


_APNS_AUTH = {}
_FCM_CREDENTIALS = {}


class InvalidPushToken(Exception):
    pass


class MobilePush:
    def __init__(self, directory, feed, sender=None):
        self.directory = Path(directory)
        self.feed = feed
        self.path = self.directory / 'mobile-push-devices.json'
        self.devices = read_json(self.path, {})
        self.activity_states = {}
        self.lock = threading.RLock()
        self.sender = sender or send_provider
        self.session_valid = lambda key: False

    def config(self):
        return read_json(self.directory / 'mobile-push.json', {})

    def status(self):
        config = self.config()
        return {'apns': bool(config.get('apns')), 'fcm': bool(config.get('fcm')),
                'configured': bool(config.get('apns') or config.get('fcm'))}

    def register(self, body, owner, origin):
        identifier = str(uuid.UUID(body.get('deviceId', '')))
        kind = body.get('kind')
        if kind not in ('apns', 'fcm', 'activity'):
            raise ValueError('不支持的推送类型')
        key = hashlib.sha256((owner + identifier + kind).encode()).hexdigest()
        with self.lock:
            if body.get('enabled') is False:
                self.devices.pop(key, None)
                write_json(self.path, self.devices)
                return {'registered': False}
            token = body.get('token', '')
            pattern = r'[0-9a-fA-F]{32,512}' if kind != 'fcm' else r'[A-Za-z0-9_:.-]{20,4096}'
            if not isinstance(token, str) or not re.fullmatch(pattern, token):
                raise ValueError('推送设备标识格式不正确')
            provider = 'fcm' if kind == 'fcm' else 'apns'
            if not self.status()[provider]:
                raise ValueError('电脑尚未配置系统推送服务')
            thread = host = ''
            if kind == 'activity':
                thread = str(uuid.UUID(body.get('thread', '')))
                host = body.get('host', 'local')
                if not isinstance(host, str) or len(host) > 256:
                    raise ValueError('主机标识无效')
            if key not in self.devices and len(self.devices) >= 100:
                raise ValueError('推送设备数量已达上限')
            # A token belongs to one login. Re-registration replaces a revoked/old session.
            for old in list(self.devices):
                if old != key and self.devices[old]['token'] == token:
                    del self.devices[old]
            previous = self.devices.get(key, {})
            feed = self.feed.read()
            if previous.get('streamId') != feed['streamId'] or (kind == 'activity' and previous.get('token') != token):
                previous = {}
            self.devices[key] = {**previous, 'owner': owner, 'token': token, 'kind': kind,
                'origin': origin, 'thread': thread, 'host': host, 'updated': time.time(),
                'cursor': previous.get('cursor', feed['cursor']), 'streamId': feed['streamId'], 'next': 0}
            write_json(self.path, self.devices)
        return {'registered': True}

    def update_activity(self, row, phase):
        with self.lock:
            key = (row['host'], row['id'])
            if any(d['kind'] == 'activity' and (d['host'], d['thread']) == key for d in self.devices.values()):
                self.activity_states[key] = {'phase': phase, 'createdAt': time.time()}

    def drain(self, enabled, allowed=lambda event: True):
        if not enabled:
            return
        config = self.config()
        with self.lock:
            snapshot = {k: dict(v) for k, v in self.devices.items()}
        for key, device in snapshot.items():
            remove = not self.session_valid(device['owner'])
            changed = remove
            if not remove and time.time() >= device.get('next', 0):
                feed = self.feed.read(device['cursor'])
                if device.get('streamId') != feed['streamId']:
                    device.update(cursor=feed['cursor'], streamId=feed['streamId'])
                    feed['events'] = []; changed = True
                for original in feed['events']:
                    event = {**original, 'streamId': feed['streamId']}
                    matches = device['kind'] != 'activity' or (event['threadId'], event['host']) == (device['thread'], device['host'])
                    if not matches or not allowed(event) or time.time()-event['createdAt'] > 3600:
                        device['cursor'] = event['sequence']; changed = True; continue
                    if not self.session_valid(device['owner']):
                        remove = changed = True; break
                    try:
                        self.sender(config, device, event)
                    except InvalidPushToken:
                        remove = changed = True; break
                    except Exception:
                        attempts = device.get('attempts', 0) + 1
                        device.update(attempts=attempts, next=time.time()+min(300, 5*2**min(attempts, 6)), error='推送失败，等待重试')
                        changed = True; break
                    device.update(cursor=event['sequence'], attempts=0, next=0, error='', lastAccepted=time.time())
                    changed = True
                    if device['kind'] == 'activity' and event['kind'] == 'completion':
                        remove = True; break
            if not remove and device['kind'] == 'activity' and time.time() >= device.get('next', 0):
                with self.lock:
                    state = self.activity_states.get((device['host'], device['thread']))
                if state and time.time()-state['createdAt'] < 30 and (state['phase'] != device.get('phase') or time.time()-device.get('stateSent', 0) > 120):
                    event = {**state, 'kind': 'state', 'id': key, 'sequence': device['cursor'], 'host': device['host'], 'threadId': device['thread']}
                    if self.session_valid(device['owner']) and allowed(event):
                        try:
                            self.sender(config, device, event)
                            device.update(phase=state['phase'], stateSent=time.time(), next=0, error='')
                        except InvalidPushToken:
                            remove = True
                        except Exception:
                            device.update(next=time.time()+60, error='实时活动更新失败，等待重试')
                        changed = True
            if changed:
                with self.lock:
                    current = self.devices.get(key)
                    if current and current.get('updated') == device.get('updated'):
                        if remove: self.devices.pop(key, None)
                        else: self.devices[key] = device
                        write_json(self.path, self.devices)


def payload(device, event):
    route = {'origin': device['origin'], 'thread': event['threadId'], 'host': event['host'],
             'sequence': str(event['sequence']), 'streamId': event.get('streamId', '')}
    # Push carries generic state only. Full inbox content stays behind gateway authentication.
    title = '本轮已结束' if event['kind'] == 'completion' else '有任务等待你确认'
    client = {'desktop:claude': 'Claude', 'desktop:deepseek': 'DSH'}.get(event['host'])
    heading = client + ' · Codex Bridge' if client else 'Codex Bridge'
    if device['kind'] == 'fcm':
        return {'message': {'token': device['token'], 'notification': {'title': heading, 'body': title},
                           'data': route, 'android': {'priority': 'high', 'ttl': '3600s', 'notification': {'channel_id': 'tasks', 'tag': 'bridge|'+device['origin']+'|'+str(event['sequence'])}}}}
    if device['kind'] == 'activity':
        now = int(time.time())
        return {'aps': {'timestamp': now, 'event': 'end' if event['kind'] == 'completion' else 'update',
                        'content-state': {'phase': event.get('phase', 'ended' if event['kind'] == 'completion' else 'waiting'),
                                          'updatedAt': now-978307200},
                        **({'dismissal-date': now+300} if event['kind'] == 'completion' else {'stale-date': now+300})}}
    return {'aps': {'alert': {'title': heading, 'body': title}, 'sound': 'default', 'thread-id': event['threadId']}, **route}


def send_provider(config, device, event):
    """Optional provider SDKs are loaded only on an explicitly configured source deployment."""
    import httpx
    body = payload(device, event)
    if device['kind'] == 'fcm':
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        cfg = config['fcm']
        credential_file = Path(cfg['credentialsFile'])
        cache_key = (str(credential_file), credential_file.stat().st_mtime_ns)
        credentials = _FCM_CREDENTIALS.get(cache_key)
        if credentials is None:
            credentials = service_account.Credentials.from_service_account_file(str(credential_file), scopes=['https://www.googleapis.com/auth/firebase.messaging'])
            _FCM_CREDENTIALS.clear(); _FCM_CREDENTIALS[cache_key] = credentials
        if not credentials.valid:
            credentials.refresh(Request())
        if not re.fullmatch(r'[a-z0-9-]+', cfg['projectId']):
            raise ValueError('Firebase project ID 无效')
        url = 'https://fcm.googleapis.com/v1/projects/'+cfg['projectId']+'/messages:send'
        headers = {'Authorization': 'Bearer '+credentials.token}
    else:
        import jwt
        cfg = config['apns']
        key_file = Path(cfg['keyFile'])
        cache_key = (cfg['teamId'], cfg['keyId'], str(key_file), key_file.stat().st_mtime_ns)
        cached = _APNS_AUTH.get(cache_key)
        if cached is None or time.time()-cached[0] > 3000:
            token = jwt.encode({'iss': cfg['teamId'], 'iat': int(time.time())}, key_file.read_text(), algorithm='ES256', headers={'kid': cfg['keyId']})
            _APNS_AUTH.clear(); _APNS_AUTH[cache_key] = (time.time(), token)
        else:
            token = cached[1]
        host = 'api.sandbox.push.apple.com' if cfg.get('sandbox', True) else 'api.push.apple.com'
        url = 'https://'+host+'/3/device/'+device['token']
        live = device['kind'] == 'activity'
        headers = {'authorization': 'bearer '+token, 'apns-topic': cfg['bundleId']+('.push-type.liveactivity' if live else ''),
                   'apns-push-type': 'liveactivity' if live else 'alert', 'apns-priority': '10',
                   'apns-expiration': str(int(time.time())+3600), 'apns-collapse-id': event['id'][:64]}
    with httpx.Client(http2=True, timeout=10, follow_redirects=False) as client:
        response = client.post(url, headers=headers, json=body)
    if response.status_code == 410:
        raise InvalidPushToken()
    if response.status_code in (400, 404):
        error = response.json()
        if error.get('reason') in ('BadDeviceToken', 'Unregistered') or any(item.get('errorCode') == 'UNREGISTERED' for item in error.get('error', {}).get('details', [])):
            raise InvalidPushToken()
    if not 200 <= response.status_code < 300:
        raise RuntimeError('系统推送服务拒绝请求')
