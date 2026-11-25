"""Opt-in gateway entry delivery, independent of chat subscriptions."""
import http.client
import json
import ipaddress
import logging
import math
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

from bridge.features.notifications.channels import channels, publish, publish_bark, publish_pushplus, publish_webhook, settings, write_json, webhook_channel_config


def gateway_ready(port, instance_id):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', '/api/health')
        response = connection.getresponse()
        value = json.loads(response.read(4096))
        return response.status == 200 and value.get('instanceId') == instance_id
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def ready_url(tunnel, port, instance_id):
    """Require both a registered live tunnel and our serving local gateway."""
    if not tunnel or not tunnel.ready.is_set() or any(event.is_set() for event in
            (tunnel.closed, tunnel.broken, tunnel.finished)):
        return ''
    url = tunnel.url
    if not gateway_ready(port, instance_id):
        return ''
    if tunnel.url != url or not tunnel.ready.is_set() or any(event.is_set() for event in
            (tunnel.closed, tunnel.broken, tunnel.finished)):
        return ''
    return url


def entry_urls(preferences, hosts, quick_url='', external_status=None, lan_addresses=None):
    """Enabled fixed entries, live tunnels, and selected LAN listeners, in click order."""
    urls = []
    for entry in preferences.get('connections', []):
        if not entry['enabled']:
            continue
        mode = entry['accessMode']
        if mode in ('nas', 'external') or (mode in ('server', 'cloudflare', 'tailscale') and
                (external_status or {}).get(entry['id'], {}).get('state') == 'connected'):
            urls.append(entry['publicUrl'].rstrip('/'))
    if quick_url:
        urls.append(quick_url.rstrip('/'))
    if preferences.get('lan'):
        # hosts is only a startup snapshot; a running monitor supplies the
        # live authority after DHCP or Wi-Fi changes.
        if lan_addresses is None:
            selected = set(preferences.get('lanAddresses') or [])
            live_hosts = hosts
        else:
            selected = None
            live_hosts = sorted(set(lan_addresses), key=ipaddress.ip_address)
        for host in live_hosts:
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                continue
            if address.is_loopback or address.is_unspecified or address.is_multicast:
                continue
            if selected is None or host in selected:
                urls.append(f'http://{host}:{preferences["port"]}')
    return list(dict.fromkeys(urls))


def address_message(config, urls, first=False, test=False):
    title = 'Codex Mobile Bridge · ' + ('入口通知测试' if test else '网关已启动' if first else '访问入口已更新')
    name = config.get('addressName') or 'Codex Mobile Bridge'
    body = name + ('已启动。' if first else '的当前访问入口如下。')
    body += '\n访问地址：\n' + '\n'.join(urls) + '\n局域网地址需在同一网络访问；固定域名需要先完成部署。\n请使用原网关登录方式访问。'
    return title, body


def send_address(config, channel, urls, first=False, test=False, event_id=''):
    title, body = address_message(config, urls, first, test)
    if channel == 'webhook' or channel.startswith('webhook:'):
        publish_webhook(webhook_channel_config(config, channel), title, body, urls[0], event='test' if test else 'gateway.started' if first else 'gateway.address_changed',
                        event_id=event_id or uuid.uuid4().hex,
                        host=config.get('addressName') or 'Codex Mobile Bridge', count=len(urls))
    else:
        {'ntfy': publish, 'bark': publish_bark, 'pushplus': publish_pushplus}[channel](config, title, body, urls[0])


def _valid_state(state):
    if not isinstance(state, dict):
        return False
    for field in ('instanceId', 'eventId'):
        if field in state and not isinstance(state[field], str):
            return False
    if 'urls' in state and (not isinstance(state['urls'], list)
            or not all(isinstance(url, str) for url in state['urls'])
            or not isinstance(state.get('first'), bool)):
        return False
    records = state.get('deliveries', {})
    if not isinstance(records, dict) or ('urls' in state and 'deliveries' not in state):
        return False
    for record in records.values():
        if not isinstance(record, dict) or not isinstance(record.get('delivered', False), bool):
            return False
        if type(record.get('attempts', 0)) is not int or record.get('attempts', 0) < 0:
            return False
        for field in ('next', 'time'):
            value = record.get(field, 0)
            if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
                return False
    return True


class AddressNotifications:
    def __init__(self, data_dir, current_urls, instance_id):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir/'address-notifications.json'
        self.current_urls = current_urls
        self.instance_id = instance_id
        self.closed = threading.Event()
        self.worker = None
        self.state_unavailable = False
        self.state = self._load_state()
        if self.state.get('instanceId') != instance_id:
            self.state = {}

    def _load_state(self):
        evidence = None
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError:
            self.state_unavailable = True
            logging.getLogger(__name__).warning('Address notifications disabled: cannot read %s', self.path)
            return {}
        try:
            state = json.loads(raw.decode('utf-8'))
            if _valid_state(state):
                return state
        except (ValueError, RecursionError):
            pass
        try:
            # Preserve exactly the bytes we read; never move a newer writer's file.
            with tempfile.NamedTemporaryFile(prefix=self.path.name + '.corrupt-', dir=self.data_dir, delete=False) as stream:
                evidence = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            self.state_unavailable = True
            if evidence is not None:
                try:
                    evidence.unlink(missing_ok=True)
                except OSError:
                    pass
            logging.getLogger(__name__).warning('Address notifications disabled: cannot preserve invalid state at %s', self.path)
            return {}
        logging.getLogger(__name__).warning('Invalid address notification state; preserved snapshot at %s', evidence)
        return {}

    def scan(self):
        if self.state_unavailable:
            return
        config = settings(self.data_dir)
        if self.closed.is_set() or not config['addressEnabled']:
            return
        urls = self.current_urls()
        if not urls:
            return
        if self.state.get('urls') != urls:
            self.state = {'instanceId': self.instance_id, 'urls': urls,
                          'first': not bool(self.state.get('urls')), 'eventId': uuid.uuid4().hex, 'deliveries': {}}
            write_json(self.path, self.state)
        elif not self.state.get('eventId'):
            self.state['eventId'] = uuid.uuid4().hex
            write_json(self.path, self.state)
        targets = channels(config)
        records = self.state['deliveries']
        # Only keep destinations currently enabled; do not retain old recipients.
        if set(records) - set(targets.values()):
            self.state['deliveries'] = records = {k: v for k, v in records.items() if k in targets.values()}
            write_json(self.path, self.state)
        for channel, target in targets.items():
            record = records.get(target, {})
            now = time.time()
            if record.get('delivered') or now < record.get('next', 0):
                continue
            # Recheck after another channel's network request, before each send.
            current = settings(self.data_dir)
            if self.closed.is_set() or not current['addressEnabled'] or self.current_urls() != urls:
                return
            if channels(current).get(channel) != target:
                continue
            try:
                send_address(current, channel, urls, first=self.state['first'], event_id=self.state['eventId'])
                records[target] = {'delivered': True, 'time': now, 'channel': channel}
            except Exception:
                attempts = record.get('attempts', 0) + 1
                records[target] = {'delivered': False, 'attempts': attempts, 'time': now,
                                   'next': now + min(300, 5 * 2 ** min(attempts, 6)), 'channel': channel,
                                   'error': '入口通知发送失败，将在入口仍有效且通知开启时重试。'}
            write_json(self.path, self.state)

    def start(self):
        self.worker = threading.Thread(target=self._run, name='address-notifications', daemon=True)
        self.worker.start()

    def _run(self):
        while not self.closed.is_set():
            try:
                self.scan()
            except Exception:
                # Keep the worker alive on a transient local read/write failure.
                pass
            self.closed.wait(3)

    def close(self):
        self.closed.set()
        if self.worker:
            self.worker.join(timeout=2)
