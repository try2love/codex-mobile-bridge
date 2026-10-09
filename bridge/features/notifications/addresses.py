"""Opt-in gateway entry delivery, independent of chat subscriptions."""
import http.client
import json
import ipaddress
import threading
import time
from pathlib import Path

from bridge.features.notifications.channels import channels, publish, publish_bark, publish_pushplus, read_json, settings, write_json


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


def entry_urls(preferences, hosts, quick_url='', external_status=None):
    """Enabled fixed entries, live tunnels, and selected LAN listeners, in click order."""
    urls = []
    for entry in preferences.get('connections', []):
        if not entry['enabled']:
            continue
        mode = entry['accessMode']
        if mode == 'nas' or (mode in ('server', 'cloudflare') and
                (external_status or {}).get(entry['id'], {}).get('state') == 'connected'):
            urls.append(entry['publicUrl'].rstrip('/'))
    if quick_url:
        urls.append(quick_url.rstrip('/'))
    if preferences.get('lan'):
        selected = preferences.get('lanAddresses')
        for host in hosts:
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
    body += '\n访问地址：\n' + '\n'.join(urls) + '\n局域网地址需在同一网络访问；固定域名需已完成部署。\n请使用原网关登录方式访问。'
    return title, body


def send_address(config, channel, urls, first=False, test=False):
    title, body = address_message(config, urls, first, test)
    {'ntfy': publish, 'bark': publish_bark, 'pushplus': publish_pushplus}[channel](config, title, body, urls[0])


class AddressNotifications:
    def __init__(self, data_dir, current_urls, instance_id):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir/'address-notifications.json'
        self.current_urls = current_urls
        self.instance_id = instance_id
        self.closed = threading.Event()
        self.worker = None
        self.state = read_json(self.path, {})
        if self.state.get('instanceId') != instance_id:
            self.state = {}

    def scan(self):
        config = settings(self.data_dir)
        if self.closed.is_set() or not config['addressEnabled']:
            return
        urls = self.current_urls()
        if not urls:
            return
        if self.state.get('urls') != urls:
            self.state = {'instanceId': self.instance_id, 'urls': urls,
                          'first': not bool(self.state.get('urls')), 'deliveries': {}}
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
                send_address(current, channel, urls, first=self.state['first'])
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
