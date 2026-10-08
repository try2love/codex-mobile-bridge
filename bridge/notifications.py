"""Opt-in phone notifications. Watching a chat never changes its execution owner."""
import hashlib
from .validation import FieldError, at_field
import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

from .model import pending_requests, ordered_turns
from .tls import client_context

DEFAULTS = {'enabled': False, 'server': 'https://ntfy.sh', 'topic': '', 'token': '',
            'barkEnabled': False, 'barkServer': 'https://api.day.app', 'barkKey': '',
            'pushplusEnabled': False, 'pushplusToken': '',
            'clickBase': '', 'includeTitle': False, 'addressEnabled': True, 'addressName': '', 'securityEnabled': True, 'mobileEnabled': False, 'mobileAppLinks': False}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


def valid_url(value, allow_path=False):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or (not allow_path and parsed.path not in ('', '/'))):
        raise ValueError('请输入完整的 HTTP/HTTPS 地址，不含账号、查询参数或片段')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('推送服务请使用 HTTPS；HTTP 仅用于本机测试')
    return value.rstrip('/')


def settings(data_dir):
    return {**DEFAULTS, **read_json(Path(data_dir)/'notifications.json', {})}


def save_settings(data_dir, value):
    prior = settings(data_dir)
    result = {**prior, **{k: value[k] for k in DEFAULTS if k in value}}
    result['server'] = at_field('ntfy-server', valid_url, str(result['server']), allow_path=True)
    result['barkServer'] = at_field('bark-server', valid_url, str(result['barkServer']), allow_path=True)
    for key in ('enabled', 'barkEnabled', 'pushplusEnabled', 'includeTitle', 'addressEnabled', 'securityEnabled', 'mobileEnabled', 'mobileAppLinks'):
        if not isinstance(result[key], bool):
            raise ValueError('通知开关格式不正确')
    if not isinstance(result['addressName'], str) or len(result['addressName']) > 80 or any(not c.isprintable() for c in result['addressName']):
        raise FieldError('网关名称应为不超过 80 字的单行文本', 'address-name')
    if not isinstance(result['topic'], str) or (result['topic'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', result['topic'])):
        raise FieldError('ntfy 主题只允许 1–64 位字母、数字、下划线和短横线', 'ntfy-topic')
    if result['enabled'] and not result['topic']:
        raise FieldError('开启通知前请填写 ntfy 主题', 'ntfy-topic')
    if result['clickBase']:
        # A link back to the existing LAN gateway may intentionally use HTTP.
        parsed = urlsplit(result['clickBase'])
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise FieldError('通知跳转地址格式不正确', 'click-base')
        result['clickBase'] = result['clickBase'].rstrip('/')
    if not isinstance(result['token'], str) or len(result['token']) > 2000 or '\n' in result['token'] or '\r' in result['token']:
        raise FieldError('ntfy Token 格式不正确', 'ntfy-token')
    # Blank fields from the UI preserve a token only for the same server.
    if not value.get('token'):
        result['token'] = '' if value.get('clearToken') or result['server'] != prior['server'] else prior['token']
    if not isinstance(result['barkKey'], str) or len(result['barkKey']) > 2000 or any(not c.isprintable() or c.isspace() or c in '/?#' for c in result['barkKey']):
        raise FieldError('Bark Device Key 格式不正确，请仅填写密钥，不要粘贴完整推送地址', 'bark-key')
    if not value.get('barkKey'):
        result['barkKey'] = '' if value.get('clearBarkKey') or result['barkServer'] != prior['barkServer'] else prior['barkKey']
    if result['barkEnabled'] and not result['barkKey']:
        raise FieldError('开启 Bark 前请填写 Device Key；更换服务地址后需重新填写', 'bark-key')
    if 'clearPushplusToken' in value and not isinstance(value['clearPushplusToken'], bool):
        raise ValueError('PushPlus 配置格式不正确')
    token = result['pushplusToken']
    if not isinstance(token, str) or len(token) > 2000 or any(not c.isprintable() or c.isspace() for c in token):
        raise FieldError('PushPlus Token 格式不正确', 'pushplus-token')
    if not value.get('pushplusToken'):
        result['pushplusToken'] = '' if value.get('clearPushplusToken') else prior['pushplusToken']
    if result['pushplusEnabled'] and not result['pushplusToken']:
        raise FieldError('开启 PushPlus 前请填写 Token', 'pushplus-token')
    write_json(Path(data_dir)/'notifications.json', result)
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward notification credentials on redirects.


def publish(config, title, body, click=''):
    if not config.get('topic'):
        raise ValueError('请先配置 ntfy 服务和主题')
    server = valid_url(config['server'], allow_path=True)
    payload = {'topic': config['topic'], 'title': title, 'message': body, 'tags': ['bell'], 'priority': 3}
    if click:
        payload['click'] = click
    headers = {'Content-Type': 'application/json'}
    if config.get('token'):
        headers['Authorization'] = 'Bearer ' + config['token']
    request = Request(server + '/', data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
    with build_opener(NoRedirect(), HTTPSHandler(context=client_context())).open(request, timeout=8) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError('ntfy 未接受通知')
        response.read(65536)


def publish_bark(config, title, body, click=''):
    if not config.get('barkKey'):
        raise ValueError('请先配置 Bark 服务和 Device Key')
    server = valid_url(config['barkServer'], allow_path=True)
    payload = {'device_key': config['barkKey'], 'title': title, 'body': body, 'group': 'Codex Mobile Bridge'}
    if click:
        payload['url'] = click
    request = Request(server + '/push', data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'})
    handlers = [NoRedirect(), HTTPSHandler(context=client_context())]
    # A local Bark gateway must not receive a private device key through a system proxy.
    if urlsplit(server).hostname in ('localhost', '127.0.0.1', '::1'):
        handlers.insert(0, ProxyHandler({}))
    try:
        with build_opener(*handlers).open(request, timeout=8) as response:
            result = json.loads(response.read(65536))
            if not 200 <= response.status < 300 or not isinstance(result, dict) or result.get('code') != 200:
                raise ValueError('Rejected')
    except Exception as error:
        # The server may echo the device key in its error response. Do not expose it.
        if isinstance(error, HTTPError):
            error.close()
        raise RuntimeError('Bark 未接受通知，请检查服务地址、Device Key 和网络') from None


def publish_pushplus(config, title, body, click=''):
    if not config.get('pushplusToken'):
        raise ValueError('请先配置 PushPlus Token')
    payload = {'token': config['pushplusToken'], 'title': title,
               'content': body + ('\n\n' + click if click else ''), 'template': 'txt'}
    request = Request('https://www.pushplus.plus/send',
                      data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect(), HTTPSHandler(context=client_context())).open(request, timeout=8) as response:
            result = json.loads(response.read(65536))
            if not 200 <= response.status < 300 or not isinstance(result, dict) or result.get('code') != 200:
                raise ValueError('Rejected')
    except Exception as error:
        if isinstance(error, HTTPError):
            error.close()
        raise RuntimeError('PushPlus 未接受通知，请检查 Token 和网络') from None


def destination(config, channel):
    if channel == 'ntfy':
        values = [config['server'].rstrip('/'), config['topic']]
    elif channel == 'bark':
        values = [config['barkServer'].rstrip('/'), config['barkKey']]
    elif channel == 'pushplus':
        values = [config.get('pushplusToken', '')]
    else:
        raise ValueError('未知通知通道')
    return channel + ':' + hashlib.sha256(json.dumps(values).encode()).hexdigest()


def channels(config):
    return {name: destination(config, name) for name, enabled in
            (('ntfy', config['enabled']), ('bark', config['barkEnabled']), ('pushplus', config.get('pushplusEnabled', False))) if enabled}


class Notifications:
    def __init__(self, bridge, data_dir, origins=lambda: [], public_url=lambda: '', *, desktop_sessions=None):
        self.bridge, self.data_dir, self.origins = bridge, Path(data_dir), origins
        self.public_url = public_url
        self.desktop_sessions = desktop_sessions
        self.desktop_poll_at = 0
        self.desktop_details = {}
        self.desktop_cursor = 0
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.ledger = read_json(self.data_dir/'notification-delivery.json', {})
        self.completions = read_json(self.data_dir/'notification-completions.json', {})
        # Bind legacy ntfy-only records to their existing destination once. A new
        # recipient must not inherit another recipient's delivery or retry state.
        config = settings(self.data_dir)
        if config['topic']:
            target = destination(config, 'ntfy')
            migrated = {target + ':' + k if re.fullmatch(r'[a-f0-9]{64}', k) else k: v for k, v in self.ledger.items()}
            if migrated != self.ledger:
                self.ledger = migrated
                write_json(self.data_dir/'notification-delivery.json', migrated)
            migrated = {json.dumps(json.loads(k) + [target]) if len(json.loads(k)) == 2 else k: v for k, v in self.completions.items()}
            if migrated != self.completions:
                self.completions = migrated
                write_json(self.data_dir/'notification-completions.json', migrated)
        self.candidates = {}
        self.discovery_at = 0
        self.attached = {}
        self.dormant = set()
        self.target_signature = None
        self.wakeup = getattr(bridge, 'notification_event', threading.Event())
        self.worker = None
        from .security_notifications import SecurityNotifications
        self.security = SecurityNotifications(self.data_dir)
        from .mobile_events import MobileEvents
        self.mobile = MobileEvents(self.data_dir)
        from .mobile_push import MobilePush
        self.mobile_push = MobilePush(self.data_dir, self.mobile)

    def _targets(self, config):
        result = channels(config)
        if config.get('mobileEnabled'):
            result['mobile'] = 'mobile:' + self.mobile.value['streamId']
        return result

    def start(self):
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        self.push_worker = threading.Thread(target=self._push_run, daemon=True)
        self.push_worker.start()

    def close(self):
        self.closed.set()
        self.wakeup.set()
        if self.worker:
            self.worker.join(timeout=2)
        for session in self.attached.values():
            session.watched = False

    def watches(self):
        return read_json(self.data_dir/'notification-watches.json', [])

    def policies(self):
        return read_json(self.data_dir/'notification-policies.json',
                         {'requests': True, 'completion': False, 'chats': {}})

    def defaults(self, value=None):
        with self.lock:
            policies = self.policies()
            if value is not None:
                if not isinstance(value, dict) or set(value) != {'requests', 'completion'} or any(type(v) is not bool for v in value.values()):
                    raise ValueError('通知开关格式不正确')
                self.candidates.update({key: {'host': key[0], 'id': key[1]} for key in self.dormant})
                self.dormant.clear()
                self.wakeup.set()
                policies.update(value)
                write_json(self.data_dir/'notification-policies.json', policies)
            return {key: policies[key] for key in ('requests', 'completion')}

    def _effective(self, row, policies=None, legacy_rows=None):
        policies = policies or self.policies()
        key = row['host'] + '|' + row['id']
        overrides = policies.get('chats', {}).get(key, {})
        # Existing explicit watches keep their old preferences on upgrade.
        legacy = next((r for r in (self.watches() if legacy_rows is None else legacy_rows) if r['host'] == row['host'] and r['id'] == row['id']), None)
        choices = {key: overrides.get(key, ('on' if legacy.get('notifyOnCompletion') else 'off') if key == 'completion' and legacy else 'on' if legacy else 'inherit') for key in ('requests', 'completion')}
        enabled = {key: policies[key] if choice == 'inherit' else choice == 'on' for key, choice in choices.items()}
        return {**row, 'notifyOnRequest': enabled['requests'], 'notifyOnCompletion': enabled['completion'], 'choices': choices}

    @staticmethod
    def desktop_provider(host):
        return {'desktop:claude': 'claude', 'desktop:deepseek': 'deepseek'}.get(host)

    def policy(self, thread_id, host, value=None):
        provider = self.desktop_provider(host)
        if provider:
            if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 512 or any(ord(c) < 32 or ord(c) == 127 for c in thread_id):
                raise ValueError('会话标识无效')
        else:
            uuid.UUID(thread_id)
        with self.lock:
            policies = self.policies()
            if value is not None:
                if not isinstance(value, dict) or set(value) != {'requests', 'completion'} or any(v not in ('inherit', 'on', 'off') for v in value.values()):
                    raise ValueError('聊天通知设置格式不正确')
                if provider:
                    if self.desktop_sessions is None:
                        raise ValueError('桌面会话接入不可用')
                    self.desktop_sessions.require_enabled(provider)
                    self.desktop_sessions.call(provider, 'detail', thread_id)
                elif self.bridge is not None:
                    self.bridge.for_host(host).store.get(thread_id)
                self.dormant.discard((host, thread_id))
                self.wakeup.set()
                policies.setdefault('chats', {})[host + '|' + thread_id] = value
                self._clear_completion(host, thread_id)
                write_json(self.data_dir/'notification-completions.json', self.completions)
                write_json(self.data_dir/'notification-policies.json', policies)
            row = self._effective({'id': thread_id, 'host': host}, policies)
            return {'available': bool(self._targets(settings(self.data_dir))), 'watching': row['notifyOnRequest'] or row['notifyOnCompletion'],
                    'notifyOnRequest': row['notifyOnRequest'], 'notifyOnCompletion': row['notifyOnCompletion'],
                    'requests': row['choices']['requests'], 'completion': row['choices']['completion']}

    def _selected(self):
        policies = self.policies()
        legacy_rows = self.watches()
        rows = {(r['host'], r['id']): r for r in legacy_rows}
        if policies['requests'] or policies['completion']:
            rows.update(self.candidates)
        for key in policies.get('chats', {}):
            if key.startswith(('desktop:claude|', 'desktop:deepseek|')):
                continue
            host, identifier = key.rsplit('|', 1)
            rows.setdefault((host, identifier), {'id': identifier, 'host': host})
        effective = [self._effective(row, policies, legacy_rows) for row in rows.values()]
        return [row for row in effective if row['notifyOnRequest'] or row['notifyOnCompletion']]

    def watch(self, thread_id, host, enabled=None, notify_on_completion=None, *, existing_only=False):
        uuid.UUID(thread_id)
        for value in (enabled, notify_on_completion):
            if value is not None and not isinstance(value, bool):
                raise ValueError('提醒开关格式不正确')
        with self.lock:
            config = settings(self.data_dir)
            targets = self._targets(config)
            rows = self.watches()
            prior = next((r for r in rows if r['id'] == thread_id and r['host'] == host), None)
            if existing_only and prior is None:
                raise ValueError('该会话的通知监控已移除，请刷新列表')
            selected = prior is not None if enabled is None else enabled
            completion = bool(prior and prior.get('notifyOnCompletion')) if notify_on_completion is None else notify_on_completion
            if enabled is False:
                completion = False
            if enabled is not None or notify_on_completion is not None:
                self.dormant.discard((host, thread_id))
                self.wakeup.set()
                if selected and not targets and not existing_only:
                    raise ValueError('请先配置并开启 PushPlus、Bark 或 ntfy 通知')
                if not selected and completion:
                    raise ValueError('请先开启此聊天提醒')
                rows = [r for r in rows if not (r['id'] == thread_id and r['host'] == host)]
                if selected:
                    if len(rows) >= 100:
                        raise ValueError('最多关注 100 个聊天')
                    session = self.attached.get((host, thread_id)) if existing_only else None
                    if not existing_only and self.bridge is not None:
                        session = self.bridge.for_host(host).session(thread_id, background=True)
                    row = {**(prior or {}), 'id': thread_id, 'host': host, 'notifyOnCompletion': completion}
                    if session is not None:
                        with session.condition:
                            state = session.state or {}
                            row.update({k: state[k] for k in ('title', 'cwd') if state.get(k)})
                    index = next((i for i, r in enumerate(self.watches()) if r['id'] == thread_id and r['host'] == host), len(rows))
                    rows.insert(index, row)
                if not selected or not completion:
                    self._clear_completion(host, thread_id)
                elif not prior or not prior.get('notifyOnCompletion'):
                    # Establish the boundary at opt-in, including an already running turn.
                    self._clear_completion(host, thread_id)
                    if session is not None:
                        with session.condition:
                            if session.connected:
                                for target in targets.values():
                                    key = self._completion_key(host, thread_id, target)
                                    self.completions[key] = self._completion_baseline(ordered_turns(session.state or {}))
                write_json(self.data_dir/'notification-completions.json', self.completions)
                write_json(self.data_dir/'notification-watches.json', rows)
                policies = self.policies()
                key = host + '|' + thread_id
                if not selected:
                    policies.setdefault('chats', {})[key] = {'requests': 'off', 'completion': 'off'}
                else:
                    policies.setdefault('chats', {}).pop(key, None)
                write_json(self.data_dir/'notification-policies.json', policies)
            return {'available': bool(targets), 'watching': selected, 'notifyOnCompletion': selected and completion}

    def control(self, value):
        if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not isinstance(value.get('host'), str):
            raise ValueError('通知监控操作格式不正确')
        action = value.get('action')
        if action == 'remove' and set(value) == {'action', 'id', 'host'}:
            # Removal is also available for a host/chat that is no longer reachable.
            return self.watch(value['id'], value['host'], False, existing_only=True)
        if action == 'update' and set(value) == {'action', 'id', 'host', 'notifyOnCompletion'} and isinstance(value['notifyOnCompletion'], bool):
            return self.watch(value['id'], value['host'], notify_on_completion=value['notifyOnCompletion'], existing_only=True)
        raise ValueError('通知监控操作格式不正确')

    def _clear_completion(self, host, thread_id):
        self.completions = {k: v for k, v in self.completions.items() if json.loads(k)[:2] != [host, thread_id]}

    @staticmethod
    def _completion_key(host, thread_id, target):
        return json.dumps([host, thread_id, target])

    @staticmethod
    def _completion_baseline(turns):
        return {'anchor': next((t['turnId'] for t in reversed(turns) if t.get('turnId')), None),
                'running': [t['turnId'] for t in turns if t.get('turnId') and t.get('status') == 'inProgress'],
                'pending': []}

    def _completion_pending(self, row, turns, target):
        key = self._completion_key(row['host'], row['id'], target)
        with self.lock:
            if not self._effective(row)['notifyOnCompletion']:
                return []
            previous = self.completions.get(key)
            current = self._completion_baseline(turns)
            current['title'] = row.get('title') or (previous or {}).get('title') or '聊天'
            if previous is not None:
                # Only turns after the last live boundary (or observed running) are new.
                # Older pages loaded into a snapshot must never become completion alerts.
                anchor = next((i for i, t in enumerate(turns) if t.get('turnId') == previous['anchor']), None)
                newer = turns if previous['anchor'] is None else turns[anchor + 1:] if anchor is not None else []
                eligible = set(previous['running']) | {t['turnId'] for t in newer if t.get('turnId')}
                pending = previous['pending'] + [t['turnId'] for t in turns if t.get('turnId') in eligible and t.get('status') == 'completed']
                current['pending'] = list(dict.fromkeys(pending))
                # A reconnect may temporarily supply only an older page or no turns.
                if previous['anchor'] is not None and anchor is None and not any(t.get('turnId') in previous['running'] or t.get('status') == 'inProgress' for t in turns):
                    current['anchor'] = previous['anchor']
                present = {t.get('turnId') for t in turns}
                current['running'] += [turn_id for turn_id in previous['running'] if turn_id not in present]
            current['pending'] = [turn_id for turn_id in current['pending'] if not self.ledger.get(self._delivery_key(row, turn_id, completion=True, target=target), {}).get('delivered')]
            if current != previous:
                self.completions[key] = current
                write_json(self.data_dir/'notification-completions.json', self.completions)
            return [self._delivery_key(row, turn_id, completion=True, target=target) for turn_id in current['pending']]

    @staticmethod
    def _delivery_key(row, event_id, completion=False, target=''):
        identity = [row['host'], row['id'], event_id]
        if completion:
            identity.append('completion')
        return (target + ':' if target else '') + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()

    def click_url(self, config, thread_id, host):
        thread_id = quote(thread_id, safe='').replace('~', '%7E')
        origins = sorted(self.origins())
        base = config.get('clickBase') or self.public_url() or next((o for o in origins if o.startswith('https://')), '')
        if not base:
            base = next((o for o in origins if urlsplit(o).hostname not in ('127.0.0.1', 'localhost')), '')
        if base and config.get('mobileAppLinks'):
            return 'codexbridge://open?origin=' + quote(base.rstrip('/'), safe='') + '&thread=' + thread_id + '&host=' + quote(host, safe='')
        return base.rstrip('/') + '/#' + thread_id + '~' + quote(host, safe='') if base else ''

    def _send_keys(self, config, row, title, channel, target, keys, completed):
        now = time.time()
        pending = [k for k in keys if not self.ledger.get(k, {}).get('delivered') and now >= self.ledger.get(k, {}).get('next', 0)]
        if not pending:
            return False
        provider = self.desktop_provider(row['host'])
        if provider and (self.desktop_sessions is None or not self.desktop_sessions.enabled(provider)):
            return False
        current = self._effective(row)
        if not current['notifyOnCompletion' if completed else 'notifyOnRequest']:
            return False
        provider = self.desktop_provider(row['host'])
        client = {'claude': 'Claude', 'deepseek': 'DSH'}.get(provider, 'Codex')
        heading = client + (' 运行已完成' if completed else ' 需要你的确认')
        body = f'有 {len(pending)} 次运行已完成，请打开聊天查看。' if completed else f'有 {len(pending)} 项请求等待处理，请打开聊天查看。'
        if config['includeTitle']:
            body = title[:120] + '\n' + body
        try:
            if channel == 'mobile':
                self.mobile.append(pending, row, heading, body, completed)
            else:
                sender = {'ntfy': publish, 'bark': publish_bark, 'pushplus': publish_pushplus}[channel]
                sender(config, heading, body, self.click_url(config, row['id'], row['host']))
            for key in pending:
                self.ledger[key] = {'delivered': True, 'time': now}
            self._status(channel, lastSent=now, error='')
        except Exception:
            for key in pending:
                attempts = self.ledger.get(key, {}).get('attempts', 0) + 1
                self.ledger[key] = {'delivered': False, 'attempts': attempts, 'next': now + min(300, 5 * 2 ** min(attempts, 6)), 'time': now}
            self._status(channel, error='发送失败，将在提醒仍开启时重试完成通知；待确认通知仅在请求仍待处理时重试。请检查服务地址、认证和网络。')
        return True

    def scan(self):
        config = settings(self.data_dir)
        targets = self._targets(config)
        signature = tuple(sorted(targets.items()))
        if signature != self.target_signature:
            self.candidates.update({key: {'host': key[0], 'id': key[1]} for key in self.dormant})
            self.dormant.clear()
            self.target_signature = signature
        if targets and hasattr(self.bridge, 'notification_updates'):
            for row in self.bridge.notification_updates():
                key = (row['host'], row['id'])
                self.candidates[key] = row
                self.dormant.discard(key)
        if targets and time.monotonic() >= self.discovery_at and hasattr(self.bridge, 'notification_candidates'):
            self.discovery_at = time.monotonic() + 30
            # Explicit watches also need to discover a new run after going idle.
            for row in self.bridge.notification_candidates():
                key = (row['host'], row['id'])
                self.candidates[key] = row
                self.dormant.discard(key)
        with self.lock:
            watches = [r for r in self._selected() if (r['host'], r['id']) not in self.dormant] if targets else []
            changed = False
            policies, legacy_rows = self.policies(), self.watches()
            for key, record in list(self.completions.items()):
                host, identifier, target = json.loads(key)
                row = {'host': host, 'id': identifier}
                provider = self.desktop_provider(host)
                if provider and (self.desktop_sessions is None or not self.desktop_sessions.enabled(provider)):
                    continue
                if target not in targets.values() or not self._effective(row, policies, legacy_rows)['notifyOnCompletion']:
                    del self.completions[key]
                    write_json(self.data_dir/'notification-completions.json', self.completions)
                    continue
                channel = next(c for c, t in targets.items() if t == target)
                keys = [self._delivery_key(row, turn, completion=True, target=target) for turn in record['pending']]
                changed = self._send_keys(config, row, record.get('title') or '聊天', channel, target, keys, True) or changed
        selected = {(r['host'], r['id']) for r in watches}
        for key in set(self.attached) - selected:
            session = self.attached[key]
            source = self.bridge.for_host(key[0])
            if hasattr(source, 'release_notification_session'):
                if not source.release_notification_session(session):
                    continue
            else:
                session.watched = False
            self.attached.pop(key, None)
        self._status(error='')
        for channel, target in targets.items():
            self._status(channel, target=target)
        for row in watches:
            if self.closed.is_set():
                break
            try:
                source = self.bridge.for_host(row['host'])
                session = source.notification_session(row['id']) if hasattr(source, 'notification_session') else source.session(row['id'], background=True)
                session.watched = True
                self.attached[(row['host'], row['id'])] = session
                with self.lock:
                    with session.condition:
                        metadata = {k: session.state[k] for k in ('title', 'cwd') if (session.state or {}).get(k)}
                        rows = self.watches()
                        legacy = next((r for r in rows if r['id'] == row['id'] and r['host'] == row['host']), None)
                        if legacy is not None and any(legacy.get(k) != v for k, v in metadata.items()):
                            legacy.update(metadata)
                            write_json(self.data_dir/'notification-watches.json', rows)
                        current = self._effective(row)
                        if not session.connected:
                            # Allow an asynchronous initial snapshot to arrive, then
                            # retire unanswered probes until a later discovery.
                            if hasattr(source, 'release_notification_session') and time.monotonic() >= session.notification_probe_until and source.release_notification_session(session):
                                key = (row['host'], row['id'])
                                self.dormant.add(key)
                                self.candidates.pop(key, None)
                                self.attached.pop(key, None)
                            continue  # Saved history is not a live pending approval.
                        requests = pending_requests(session.state)
                        live_running = session.state.get('threadRuntimeStatus', {}).get('type') == 'active' or any(t.get('status') == 'inProgress' for t in ordered_turns(session.state))
                        self.mobile_push.update_activity(row, 'waiting' if requests else 'running' if live_running else 'ready')
                        title = session.state.get('title') or '聊天'
                        turns = [{'turnId': t.get('turnId'), 'status': t.get('status')} for t in ordered_turns(session.state)]
                    completed_keys = {channel: self._completion_pending({**current, 'title': title}, turns, target) if current.get('notifyOnCompletion') else [] for channel, target in targets.items()}
                for channel, target in targets.items():
                    with self.lock:
                        for completed, keys in ((False, [self._delivery_key(row, r['id'], target=target) for r in requests]), (True, completed_keys[channel])):
                            changed = self._send_keys(config, row, title, channel, target, keys, completed) or changed
                if hasattr(source, 'release_notification_session'):
                    # Keep only live tasks and approvals attached. Completion retry
                    # records above are independent of the thread subscription.
                    with session.condition:
                        state = session.state or {}
                        runtime = state.get('threadRuntimeStatus', {}).get('type')
                        active = bool(pending_requests(state)) or runtime == 'active' or any(t.get('status') == 'inProgress' for t in ordered_turns(state))
                        if not active and source.release_notification_session(session):
                            key = (row['host'], row['id'])
                            self.dormant.add(key)
                            self.candidates.pop(key, None)
                            self.attached.pop(key, None)
            except Exception:
                self._status(error='部分关注聊天暂时无法连接，请检查电脑 App 或 SSH 连接。')
        changed = self._scan_desktops(config, targets) or changed
        if changed:
            if len(self.ledger) > 5000:
                self.ledger = dict(sorted(self.ledger.items(), key=lambda p: p[1].get('time', 0))[-4000:])
            write_json(self.data_dir/'notification-delivery.json', self.ledger)

    def _scan_desktops(self, config, targets):
        manager = self.desktop_sessions
        if not targets or manager is None or time.monotonic() < self.desktop_poll_at:
            return False
        self.desktop_poll_at = time.monotonic() + 10
        rows = manager.notification_rows()
        if not rows:
            return False
        # Rotate bounded history reads so one large client's history cannot block
        # permission notifications or continuously reread every idle transcript.
        offset = self.desktop_cursor % len(rows)
        rows = rows[offset:] + rows[:offset]
        self.desktop_cursor = (offset + 8) % len(rows)
        changed, reads = False, 0
        for row in rows:
            if self.closed.is_set():
                break
            if not manager.enabled(row['provider']):
                continue
            current = self._effective(row)
            if not (current['notifyOnRequest'] or current['notifyOnCompletion']):
                continue
            title = row.get('title') or '聊天'
            requests = [r for r in row.get('requests', []) if isinstance(r, dict) and r.get('id')]
            if requests or row.get('runtimeKnown') is True:
                self.mobile_push.update_activity(row, 'waiting' if requests else 'running' if row.get('status') == 'active' else 'ready')
            for channel, target in targets.items():
                keys = [self._delivery_key(row, r['id'], target=target) for r in requests]
                changed = self._send_keys(config, row, title, channel, target, keys, False) or changed
            if not current['notifyOnCompletion']:
                continue
            key = (row['host'], row['id'])
            signature = (row.get('updatedAt'), row.get('status'))
            cached = self.desktop_details.get(key)
            if cached is None or cached[0] != signature or row.get('status') == 'active':
                if reads >= 8:
                    continue
                reads += 1
                try:
                    detail = manager.call(row['provider'], 'detail', row['id'])
                    if detail.get('connected') is False or not isinstance(detail.get('turns'), list):
                        continue
                    cached = (signature, detail['turns'])
                    self.desktop_details[key] = cached
                except (OSError, ValueError):
                    continue
            for channel, target in targets.items():
                keys = self._completion_pending({**current, 'title': title}, cached[1], target)
                changed = self._send_keys(config, row, title, channel, target, keys, True) or changed
        return changed

    def _status(self, channel=None, **values):
        path = self.data_dir/'notification-status.json'
        prior = read_json(path, {})
        if channel:
            record = prior.get(channel, {})
            if 'target' in values and record.get('target') != values['target']:
                record = {}
            current = {**prior, channel: {**record, **values}}
        else:
            current = {**prior, **values}
        if current != prior:
            write_json(path, current)

    def _run(self):
        while not self.closed.is_set():
            try:
                self.security.scan()
                self.scan()
            except Exception:
                self._status(error='通知配置读取失败，请在电脑启动器重新保存配置。')
            self.wakeup.wait(3)
            self.wakeup.clear()
            # Coalesce a burst of stream patches rather than scan per token.
            self.closed.wait(1)

    def _push_allowed(self, event):
        provider = self.desktop_provider(event['host'])
        if provider and (self.desktop_sessions is None or not self.desktop_sessions.enabled(provider)):
            return False
        policy = self._effective({'id': event['threadId'], 'host': event['host']})
        if event['kind'] == 'state':
            return policy['notifyOnRequest'] or policy['notifyOnCompletion']
        return policy['notifyOnCompletion' if event['kind'] == 'completion' else 'notifyOnRequest']

    def _push_run(self):
        while not self.closed.is_set():
            try:
                self.mobile_push.drain(settings(self.data_dir).get('mobileEnabled', False), self._push_allowed)
            except Exception:
                self._status('nativePush', error='系统推送暂不可用，请检查平台配置')
            self.closed.wait(3)
